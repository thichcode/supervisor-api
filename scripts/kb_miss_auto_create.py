"""
KB Miss Auto-Create Worker

Automatically creates Knowledge Base entries from queries that failed to find KB matches.

How it works:
1. Scans interaction_logs for records with kb_hit_count = 0 (misses)
2. Groups misses by topic similarity
3. For high-frequency misses, generates a draft KB entry using LLM
4. Saves draft as a new KB document for review

Run daily via cron: python scripts/kb_miss_auto_create.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class KBMissAutoCreator:
    """Analyze KB search misses and auto-create draft KB entries."""
    
    def __init__(self, session_factory, llm=None):
        self.session_factory = session_factory
        self.llm = llm
        self.min_miss_count = 3  # Minimum times a similar query must miss to trigger auto-create
        self.lookback_days = 7   # Look back this many days for misses
    
    async def run(self, dry_run: bool = False) -> dict[str, int]:
        """Run the KB miss auto-create process.
        
        Args:
            dry_run: If True, only analyze, don't create entries
            
        Returns:
            Dict with stats about what was created
        """
        stats = {
            "total_misses_analyzed": 0,
            "topics_found": 0,
            "draft_created": 0,
            "errors": 0,
            "min_miss_count": self.min_miss_count,
        }
        
        async with self.session_factory() as session:
            # Step 1: Get KB misses from interaction_logs
            misses = await self._get_kb_misses(session)
            stats["total_misses_analyzed"] = len(misses)
            
            if not misses:
                logger.info("no_kb_misses_found")
                return stats
            
            # Step 2: Cluster misses by topic similarity
            topics = self._cluster_misses(misses)
            stats["topics_found"] = len(topics)
            
            # Step 3: For high-frequency topics, create draft KB entries
            for topic_key, topic_data in topics.items():
                if topic_data["count"] < self.min_miss_count:
                    continue
                
                if dry_run:
                    logger.info(
                        "dry_run_would_create",
                        topic=topic_key,
                        count=topic_data["count"],
                        sample_query=topic_data["sample_query"][:100],
                    )
                    stats["draft_created"] += 1
                    continue
                
                try:
                    await self._create_draft_entry(
                        session=session,
                        topic_key=topic_key,
                        topic_data=topic_data,
                    )
                    stats["draft_created"] += 1
                except Exception as e:
                    logger.error("draft_creation_failed", topic=topic_key, error=str(e))
                    stats["errors"] += 1
            
            if not dry_run:
                await session.commit()
        
        logger.info(
            "kb_miss_auto_create_complete",
            total_analyzed=stats["total_misses_analyzed"],
            topics_found=stats["topics_found"],
            draft_created=stats["draft_created"],
            errors=stats["errors"],
        )
        return stats
    
    async def _get_kb_misses(self, session) -> list[dict[str, Any]]:
        """Get KB search misses from interaction_logs."""
        from sqlalchemy import text
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        
        query = text("""
            SELECT 
                request_id,
                input_text,
                output_text,
                confidence_score,
                created_at,
                traffic_class,
                kb_sources
            FROM interaction_logs 
            WHERE (kb_hit_count IS NULL OR kb_hit_count = 0)
              AND created_at >= :cutoff
              AND input_text IS NOT NULL
              AND LENGTH(input_text) > 10
              AND traffic_class = 'service_like'
            ORDER BY created_at DESC
            LIMIT 500
        """)
        
        result = await session.execute(query, {"cutoff": cutoff})
        rows = result.fetchall()
        
        misses = []
        for row in rows:
            misses.append({
                "request_id": row[0],
                "query": row[1],
                "answer": row[2] or "",
                "confidence": row[3],
                "created_at": row[4],
                "traffic_class": row[5],
                "kb_sources": row[6],
            })
        
        return misses
    
    def _cluster_misses(self, misses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Cluster similar queries into topics using simple keyword overlap."""
        topics: dict[str, dict] = {}
        
        for miss in misses:
            query = (miss.get("query") or "").strip().lower()
            if not query:
                continue
            
            # Extract key terms (skip common words)
            words = re.findall(r'\w+', query)
            stopwords = {
                "the", "a", "an", "is", "are", "was", "were", "be", "been",
                "have", "has", "had", "do", "does", "did", "will", "would",
                "can", "could", "may", "might", "shall", "should",
                "to", "of", "in", "for", "on", "with", "at", "by", "from",
                "this", "that", "these", "those", "it", "its",
                "i", "you", "we", "they", "he", "she",
                "my", "your", "our", "their", "his", "her",
                "not", "no", "nor", "but", "or", "and", "so", "if",
                "và", "của", "cho", "với", "một", "những", "các",
                "được", "có", "trong", "không", "là", "bạn", "tôi",
                "này", "đó", "như", "khi", "vì", "nên", "lại",
                "giúp", "mình", "ơi", "ạ", "nhé", "ạ",
            }
            key_terms = [w for w in words if w not in stopwords and len(w) > 2]
            
            if not key_terms:
                continue
            
            # Use first 3 key terms as topic key
            topic_key = " ".join(key_terms[:3])
            
            if topic_key not in topics:
                topics[topic_key] = {
                    "count": 0,
                    "queries": [],
                    "sample_query": query,
                    "first_seen": miss["created_at"],
                    "last_seen": miss["created_at"],
                    "has_answer": False,
                }
            
            topic = topics[topic_key]
            topic["count"] += 1
            if len(topic["queries"]) < 5:
                topic["queries"].append(query)
            if miss.get("answer"):
                topic["has_answer"] = True
            if miss.get("created_at") and miss["created_at"] > topic["last_seen"]:
                topic["last_seen"] = miss["created_at"]
        
        return topics
    
    async def _create_draft_entry(
        self,
        session,
        topic_key: str,
        topic_data: dict[str, Any],
    ) -> None:
        """Create a draft KB entry for a missed topic.
        
        Uses LLM to generate: title, content, category, tags
        Falls back to template-based generation if LLM unavailable.
        """
        from src.db.models import KnowledgeDocument
        from datetime import datetime, timezone
        
        title = self._generate_title(topic_key, topic_data)
        content = await self._generate_content(topic_key, topic_data)
        category = self._infer_category(topic_key, topic_data)
        tags = self._infer_tags(topic_key, topic_data)
        
        # Check if similar entry already exists to avoid duplicates
        from sqlalchemy import select
        existing = await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.title.ilike(f"%{topic_key[:50]}%"),
                KnowledgeDocument.is_active.is_(True),
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            logger.info("duplicate_skipped", topic=topic_key[:50])
            return
        
        entry = KnowledgeDocument(
            document_id=f"miss-{topic_key[:30].replace(' ', '-')}-{int(datetime.now(timezone.utc).timestamp())}",
            title=title[:300] if title else f"Auto: {topic_key[:50]}",
            content=content,
            category=category or "general",
            tags=tags,
            document_type="draft",
            extra_metadata={
                "source": "kb_miss_auto_create",
                "miss_count": topic_data["count"],
                "sample_queries": topic_data["queries"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "draft",
            },
        )
        session.add(entry)
        
        logger.info(
            "kb_draft_created_from_miss",
            title=entry.title[:50],
            category=category,
            miss_count=topic_data["count"],
        )
    
    def _generate_title(self, topic_key: str, topic_data: dict[str, Any]) -> str:
        """Generate a title from the topic."""
        words = topic_key.split()
        if len(words) <= 3:
            return f"Hướng dẫn: {topic_key.title()}"
        return f"Hướng dẫn xử lý vấn đề về {' '.join(words[:3]).title()}"
    
    async def _generate_content(self, topic_key: str, topic_data: dict[str, Any]) -> str:
        """Generate content for the KB entry.
        
        Uses LLM if available, otherwise template-based.
        """
        if self.llm and topic_data.get("sample_query"):
            prompt = f"""Bạn là chuyên gia IT support. Tạo nội dung Knowledge Base từ các câu hỏi sau:

Các câu hỏi của người dùng:
{chr(10).join(f'- {q}' for q in topic_data['queries'][:3])}

Yêu cầu:
1. Viết câu trả lời chi tiết, có cấu trúc (bước 1, bước 2...)
2. Trả lời bằng tiếng Việt
3. Dài khoảng 200-500 từ
4. Kèm các bước troubleshooting cụ thể
5. Đề xuất thông tin cần thu thập thêm nếu chưa đủ context

Format:
## Tên vấn đề
### Mô tả
...
### Các bước xử lý
1. ...
2. ...
### Thông tin cần thu thập
- ..."""

            try:
                response = await self.llm.complete(
                    system_prompt="Bạn là chuyên gia IT support tạo nội dung KB.",
                    user_message=prompt,
                    temperature=0.3,
                )
                if response and response.content:
                    return response.content[:2000]
            except Exception as e:
                logger.warning("llm_content_generation_failed", error=str(e))
        
        # Template fallback
        return (
            f"## {topic_key.title()}\n\n"
            f"### Mô tả\n"
            f"Vấn đề liên quan đến {topic_key}.\n\n"
            f"### Các bước xử lý\n"
            f"1. Kiểm tra thông tin chi tiết từ người dùng\n"
            f"2. Xác định nguyên nhân dựa trên các triệu chứng\n"
            f"3. Áp dụng giải pháp phù hợp\n"
            f"4. Xác nhận với người dùng vấn đề đã được giải quyết\n\n"
            f"### Thông tin cần thu thập\n"
            f"- Mô tả chi tiết vấn đề\n"
            f"- Hệ thống/dịch vụ liên quan\n"
            f"- Mã lỗi (nếu có)\n"
            f"- Các bước đã thực hiện\n"
        )
    
    def _infer_category(self, topic_key: str, topic_data: dict[str, Any]) -> str:
        """Infer KB category from topic content."""
        topic_lower = topic_key.lower()
        category_map = [
            ("network", ["vpn", "network", "mạng", "wifi", "internet", "kết nối", "remote"]),
            ("software", ["software", "phần mềm", "app", "application", "cài đặt", "install"]),
            ("hardware", ["hardware", "phần cứng", "máy in", "laptop", "màn hình"]),
            ("email", ["email", "mail", "outlook", "thư", "gmail"]),
            ("account", ["account", "tài khoản", "password", "mật khẩu", "login", "đăng nhập"]),
            ("security", ["security", "bảo mật", "virus", "firewall", "license"]),
            ("database", ["database", "sql", "db", "cơ sở dữ liệu"]),
            ("printer", ["printer", "máy in", "print"]),
        ]
        
        for category, keywords in category_map:
            if any(kw in topic_lower for kw in keywords):
                return category
        
        return "general"
    
    def _infer_tags(self, topic_key: str, topic_data: dict[str, Any]) -> list[str]:
        """Infer tags from topic content."""
        words = re.findall(r'\w+', topic_key.lower())
        stopwords = {"the", "a", "an", "cho", "của", "với", "một", "các", "được", "không", "là"}
        
        tags = [w for w in words if w not in stopwords and len(w) > 2]
        
        # Add context tags
        if "lỗi" in topic_key.lower() or "error" in topic_key.lower():
            tags.append("error")
        if "hướng dẫn" in topic_key.lower() or "guide" in topic_key.lower():
            tags.append("guide")
        
        return tags[:5]


async def main():
    """Run the KB miss auto-create worker."""
    import argparse
    
    parser = argparse.ArgumentParser(description="KB Miss Auto-Create Worker")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, don't create entries")
    parser.add_argument("--min-misses", type=int, default=3, help="Minimum misses to trigger auto-create")
    parser.add_argument("--lookback-days", type=int, default=7, help="Days to look back for misses")
    args = parser.parse_args()
    
    from src.db import async_session
    from src.config import get_settings
    
    settings = get_settings()
    
    # Initialize LLM if configured
    llm = None
    if settings.primary_llm_model:
        try:
            from src.llm import MultiProviderLLMClient
            llm = MultiProviderLLMClient()
            logger.info("llm_initialized_for_kb_creation", model=settings.primary_llm_model)
        except Exception as e:
            logger.warning("llm_init_failed", error=str(e))
    
    creator = KBMissAutoCreator(
        session_factory=async_session,
        llm=llm,
    )
    creator.min_miss_count = args.min_misses
    creator.lookback_days = args.lookback_days
    
    stats = await creator.run(dry_run=args.dry_run)
    
    print(f"\nKB Miss Auto-Create Results:")
    print(f"  Total misses analyzed: {stats['total_misses_analyzed']}")
    print(f"  Topics found:          {stats['topics_found']}")
    print(f"  Draft entries created: {stats['draft_created']}")
    print(f"  Errors:                {stats['errors']}")
    
    if stats["draft_created"] > 0:
        print(f"\n✅ Created {stats['draft_created']} draft KB entries from missed searches.")
        print(f"   Review and publish them in KB admin panel.")
    elif stats["topics_found"] < stats["min_miss_count"]:
        print(f"\n📊 Not enough misses to trigger auto-create (need {args.min_misses}+ per topic).")


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["KBMissAutoCreator", "main"]