"""
Supervisor Fact Store - Structured memory with algebraic reasoning

Unlike simple key-value stores, fact_store supports:
- Entity-based queries (all facts about X)
- Relationship queries (what connects A and B?)
- Compositional queries (facts about A AND B)
- Trust scoring (helpful/unhelpful feedback)
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()


@dataclass
class Fact:
    """A structured fact"""
    id: int
    content: str
    category: str  # user_pref, project, tool, general
    entities: List[str]  # Entities mentioned in this fact
    tags: List[str]
    trust: float = 0.5  # 0-1 trust score
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class FactStore:
    """
    Structured memory with entity relationships
    Thread-safe: open connection per operation (sqlite3 cannot share connections across threads)
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            self.db_path = Path.home() / ".supervisor" / "fact_store.db"
        else:
            self.db_path = db_path
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Do NOT keep connection open - sqlite3 is not thread safe
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Facts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                entities TEXT NOT NULL,  -- JSON array
                tags TEXT NOT NULL,      -- JSON array
                trust REAL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Entity index for fast lookup
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entities ON facts(entities)
        """)
        
        # Category index
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_category ON facts(category)
        """)
        
        conn.commit()
        conn.close()
    
    def add(
        self,
        content: str,
        category: str = "general",
        entities: Optional[List[str]] = None,
        tags: Optional[List[str]] = None
    ) -> int:
        """Add a new fact"""
        entities = entities or []
        tags = tags or []
        now = datetime.now(timezone.utc).isoformat()
        
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO facts (content, category, entities, tags, trust, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0.5, ?, ?)
        """, (content, category, json.dumps(entities), json.dumps(tags), now, now))
        
        last_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return last_id
    
    def search(self, query: str, limit: int = 10, min_trust: float = 0.3) -> List[Fact]:
        """Keyword search"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM facts
            WHERE content LIKE ? AND trust >= ?
            ORDER BY trust DESC
            LIMIT ?
        """, (f"%{query}%", min_trust, limit))
        
        rows = [self._row_to_fact(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def probe(self, entity: str, limit: int = 10, min_trust: float = 0.3) -> List[Fact]:
        """Get ALL facts about an entity"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM facts
            WHERE entities LIKE ? AND trust >= ?
            ORDER BY trust DESC
            LIMIT ?
        """, (f'%"{entity}"%', min_trust, limit))
        
        rows = [self._row_to_fact(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def related(self, entity: str, limit: int = 10) -> List[Fact]:
        """Find facts connected to an entity (share relationships)"""
        # Find entities that co-occur with this entity
        cursor = self._conn.cursor()
        
        # Get all facts mentioning this entity
        cursor.execute("""
            SELECT entities FROM facts
            WHERE entities LIKE ?
        """, (f'%"{entity}"%',))
        
        # Collect related entities
        related: Set[str] = set()
        for row in cursor.fetchall():
            fact_entities = json.loads(row["entities"])
            related.update(fact_entities)
        
        related.discard(entity)
        
        # Get facts about related entities
        if not related:
            return []
        
        # Simple approach: query each related entity
        results = []
        for rel_entity in list(related)[:5]:
            facts = self.probe(rel_entity, limit=5)
            results.extend(facts)
        
        # Deduplicate and sort by trust
        seen = set()
        unique = []
        for f in results:
            if f.id not in seen:
                seen.add(f.id)
                unique.append(f)
        
        return unique[:limit]
    
    def reason(self, entities: List[str], limit: int = 10) -> List[Fact]:
        """Compositional: facts connected to MULTIPLE entities"""
        if not entities:
            return []
        
        cursor = self._conn.cursor()
        
        # Find facts that mention ALL entities
        # This is a simple implementation - could be more efficient
        results = []
        
        # Get facts for each entity
        entity_facts = {}
        for entity in entities:
            facts = self.probe(entity, limit=100)
            entity_facts[entity] = set(f.id for f in facts)
        
        # Find intersection
        if not entity_facts:
            return []
        
        common_ids = set.intersection(*entity_facts.values())
        
        # Get full facts
        for fid in common_ids:
            cursor.execute("SELECT * FROM facts WHERE id = ?", (fid,))
            row = cursor.fetchone()
            if row:
                results.append(self._row_to_fact(row))
        
        return results[:limit]
    
    def contradict(self, query: str, limit: int = 5) -> List[tuple]:
        """Find potentially contradicting facts"""
        # Simple approach: find facts with opposite keywords
        opposites = {
            "good": ["bad", "poor", "wrong"],
            "fast": ["slow", "fast"],
            "yes": ["no"],
            "true": ["false"],
        }
        
        opposites_lower = {k.lower(): [w.lower() for w in v] for k, v in opposites.items()}
        
        results = []
        
        for pos, negs in opposites_lower.items():
            if pos.lower() in query.lower():
                for neg in negs:
                    facts = self.search(neg, limit=limit)
                    results.extend([(pos, f) for f in facts])
        
        return results[:limit]
    
    def update(self, fact_id: int, trust_delta: float):
        """Update trust score"""
        cursor = self._conn.cursor()
        
        cursor.execute("""
            UPDATE facts 
            SET trust = MAX(0, MIN(1, trust + ?)), updated_at = ?
            WHERE id = ?
        """, (trust_delta, datetime.now(timezone.utc).isoformat(), fact_id))
        
        self._conn.commit()
    
    def remove(self, fact_id: int):
        """Delete a fact"""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._conn.commit()
    
    def list(self, category: Optional[str] = None, limit: int = 50) -> List[Fact]:
        """List all facts"""
        cursor = self._conn.cursor()
        
        if category:
            cursor.execute("""
                SELECT * FROM facts WHERE category = ? 
                ORDER BY trust DESC LIMIT ?
            """, (category, limit))
        else:
            cursor.execute("""
                SELECT * FROM facts ORDER BY trust DESC LIMIT ?
            """, (limit,))
        
        return [self._row_to_fact(row) for row in cursor.fetchall()]
    
    def _row_to_fact(self, row) -> Fact:
        """Convert row to Fact"""
        return Fact(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            entities=json.loads(row["entities"]),
            tags=json.loads(row["tags"]),
            trust=row["trust"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"])
        )
    
    def close(self):
        """Close connection"""
        if self._conn:
            self._conn.close()


# ============ Global Instance ============

_fact_store: Optional[FactStore] = None


def get_fact_store() -> FactStore:
    """Get global fact store"""
    global _fact_store
    if _fact_store is None:
        _fact_store = FactStore()
    return _fact_store


__all__ = ["Fact", "FactStore", "get_fact_store"]