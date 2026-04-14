import hashlib
import json
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from src.db import async_session
from src.knowledge.schemas import BatchFileRequest, BatchFileResponse, FileProcessRequest, FileProcessResponse
from src.llm import llm_client

router = APIRouter(prefix="/knowledge/file", tags=["knowledge-files"])


async def _resolve_file_path(request: FileProcessRequest) -> str:
    file_path = request.file_path
    if request.file_url and not Path(request.file_path).exists():
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(request.file_url)
                response.raise_for_status()
                temp_path = Path("/tmp") / f"upload_{int(time.time())}"
                temp_path.write_bytes(response.content)
                file_path = str(temp_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")

    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    return file_path


async def _classify_content(content: str, default_type: str, default_tags: list[str]):
    knowledge_type = default_type
    tags = default_tags.copy()

    if not content:
        return knowledge_type, tags

    try:
        if llm_client and llm_client.is_initialized:
            classification_prompt = f"""Phân loại tài liệu sau và trả về JSON:
{{
    \"type\": \"policy|faq|guide|document\",
    \"category\": \"một từ mô tả category\",
    \"tags\": [\"tag1\", \"tag2\", \"tag3\"]
}}

Nội dung:
{content[:3000]}
"""
            response = await llm_client.complete(
                "Bạn là trợ lý phân loại tài liệu. Phân tích và trả về JSON.",
                classification_prompt,
            )
            match = re.search(r'\{[^{}]*"type"[^{}]*"category"[^{}]*"tags"[^{}]*\}', response.content, re.DOTALL)
            if match:
                data = json.loads(match.group())
                knowledge_type = data.get("type", knowledge_type)
                tags = data.get("tags", tags)
    except Exception:
        pass

    return knowledge_type, tags


@router.post("/process", response_model=FileProcessResponse)
async def process_file(request: FileProcessRequest):
    from src.tools.file_processor import get_file_processor

    start_time = time.time()
    errors = []
    processor = get_file_processor()
    if not processor:
        raise HTTPException(status_code=503, detail="File processor not available. Set ENABLE_FILE_PROCESSOR=true")

    file_path = await _resolve_file_path(request)
    file_size = Path(file_path).stat().st_size

    try:
        file_content = processor.process_file(file_path)
        extracted_content = file_content.content if file_content and file_content.content else ""
        if not extracted_content:
            errors.append("No content extracted from file")

        if len(extracted_content) > 50000:
            extracted_content = extracted_content[:50000]
            errors.append("Content truncated to 50K characters")

        knowledge_type = request.knowledge_type
        suggested_tags = request.tags.copy()
        if request.auto_classify and extracted_content:
            try:
                knowledge_type, suggested_tags = await _classify_content(
                    extracted_content,
                    request.knowledge_type,
                    request.tags,
                )
            except Exception as e:
                errors.append(f"Auto-classification failed: {str(e)}")

        extracted_fields = {}
        if request.extract_metadata and file_content:
            extracted_fields = {
                "filename": file_content.filename,
                "content_type": file_content.content_type,
                "metadata": file_content.metadata or {},
            }

        return FileProcessResponse(
            status="success",
            file_name=Path(file_path).name,
            file_size=file_size,
            extracted_content=extracted_content,
            knowledge_type=knowledge_type,
            category=request.category,
            suggested_tags=suggested_tags,
            extracted_fields=extracted_fields,
            chunks_count=max(1, len(extracted_content) // 1000),
            embeddings_generated=False,
            processing_time_ms=int((time.time() - start_time) * 1000),
            errors=errors,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@router.post("/import")
async def import_file_to_knowledge(request: FileProcessRequest):
    from src.db.models import KnowledgeDocument, KnowledgeFAQ, KnowledgeGuide, KnowledgePolicy
    from src.tools.file_processor import get_file_processor

    start_time = time.time()
    processor = get_file_processor()
    if not processor:
        raise HTTPException(status_code=503, detail="File processor not available. Set ENABLE_FILE_PROCESSOR=true")

    file_path = await _resolve_file_path(request)
    file_content = processor.process_file(file_path)
    if not file_content or not file_content.content:
        raise HTTPException(status_code=400, detail="No content extracted")

    content = file_content.content[:50000]
    knowledge_type = request.knowledge_type
    tags = request.tags.copy()
    if request.auto_classify:
        knowledge_type, tags = await _classify_content(content, knowledge_type, tags)

    file_id = hashlib.md5(f"{Path(file_path).name}{time.time()}".encode()).hexdigest()[:12]
    file_name = Path(file_path).name

    async with async_session() as session:
        try:
            if knowledge_type == "policy":
                kb = KnowledgePolicy(
                    policy_id=f"policy_{file_id}",
                    title=file_name,
                    content=content,
                    category=request.category,
                    tags=tags,
                    version="1.0",
                )
            elif knowledge_type == "faq":
                kb = KnowledgeFAQ(
                    question_id=f"faq_{file_id}",
                    question=file_name,
                    answer=content,
                    category=request.category,
                    tags=tags,
                )
            elif knowledge_type == "guide":
                kb = KnowledgeGuide(
                    guide_id=f"guide_{file_id}",
                    title=file_name,
                    content=content,
                    guide_type="document",
                    category=request.category,
                    tags=tags,
                )
            else:
                kb = KnowledgeDocument(
                    document_id=f"doc_{file_id}",
                    title=file_name,
                    content=content,
                    document_type=Path(file_path).suffix,
                    category=request.category,
                    tags=tags,
                    file_url=request.file_url or file_path,
                )

            session.add(kb)
            await session.commit()
            return {
                "status": "imported",
                "file_name": file_name,
                "knowledge_type": knowledge_type,
                "knowledge_id": kb.policy_id if hasattr(kb, "policy_id") else (kb.question_id if hasattr(kb, "question_id") else (kb.guide_id if hasattr(kb, "guide_id") else kb.document_id)),
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@router.post("/batch")
async def batch_process_files(request: BatchFileRequest):
    results = []
    successful = 0
    failed = 0

    for file_req in request.files:
        try:
            from src.tools.file_processor import get_file_processor

            processor = get_file_processor()
            if not processor:
                results.append({"file": file_req.file_path, "status": "error", "error": "File processor not available"})
                failed += 1
                continue

            file_content = processor.process_file(file_req.file_path)
            content_length = file_content.content_length if file_content else 0

            if request.import_to_knowledge_base:
                result = await import_file_to_knowledge(file_req)
                results.append({"file": file_req.file_path, "status": "imported", "knowledge_id": result.get("knowledge_id")})
            else:
                results.append({"file": file_req.file_path, "status": "processed", "content_length": content_length})

            successful += 1
        except Exception as e:
            results.append({"file": file_req.file_path, "status": "error", "error": str(e)})
            failed += 1

    return BatchFileResponse(
        status="completed",
        total_files=len(request.files),
        successful=successful,
        failed=failed,
        results=results,
    )


@router.get("/formats")
async def get_supported_formats():
    return {
        "formats": [
            {"extension": ".pdf", "name": "PDF", "ocr_support": True},
            {"extension": ".xlsx", "name": "Excel", "ocr_support": False},
            {"extension": ".xls", "name": "Excel (Legacy)", "ocr_support": False},
            {"extension": ".csv", "name": "CSV", "ocr_support": False},
            {"extension": ".json", "name": "JSON", "ocr_support": False},
            {"extension": ".txt", "name": "Text", "ocr_support": False},
            {"extension": ".md", "name": "Markdown", "ocr_support": False},
            {"extension": ".docx", "name": "Word", "ocr_support": False},
            {"extension": ".jpg", "name": "JPEG Image", "ocr_support": True},
            {"extension": ".png", "name": "PNG Image", "ocr_support": True},
            {"extension": ".tiff", "name": "TIFF Image", "ocr_support": True},
        ],
        "max_file_size_mb": 50,
        "ocr_languages": ["eng", "vie", "chi_sim", "jpn", "kor"],
    }
