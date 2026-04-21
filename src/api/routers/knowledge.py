
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from src.db import async_session
from src.knowledge.schemas import (
    BulkImportRequest,
    DocumentCreate,
    FAQCreate,
    GuideCreate,
    KnowledgeSearchRequest,
    PolicyCreate,
)
from src.llm import llm_client

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/stats")
async def get_knowledge_stats():
    """Get knowledge base statistics."""
    from src.knowledge import KnowledgeRetrievalService

    async with async_session() as session:
        kb_service = KnowledgeRetrievalService(session)
        return await kb_service.get_knowledge_stats()


@router.post("/search")
async def search_knowledge(request: KnowledgeSearchRequest):
    """Search knowledge base (policies, FAQs, guides, documents)."""
    from src.knowledge import KnowledgeRetrievalService

    async with async_session() as session:
        kb_service = KnowledgeRetrievalService(session, None)
        return await kb_service.search(
            query=request.query,
            search_type=request.search_type,
            category=request.category,
            tags=request.tags,
            limit=request.limit,
            offset=request.offset,
        )


@router.post("/policies")
async def create_policy(policy: PolicyCreate):
    from src.db.models import KnowledgePolicy

    async with async_session() as session:
        kb_policy = KnowledgePolicy(
            policy_id=policy.policy_id,
            title=policy.title,
            content=policy.content,
            category=policy.category,
            tags=policy.tags,
            version=policy.version,
        )
        session.add(kb_policy)
        await session.commit()
        await session.refresh(kb_policy)
        return {"status": "created", "policy_id": kb_policy.policy_id}


@router.get("/policies")
async def list_policies(category: str = None, limit: int = 20):
    from src.knowledge import KnowledgeBaseRepository

    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        policies = await repo.search_policies(category=category, limit=limit)
        return {
            "policies": [
                {
                    "policy_id": p.policy_id,
                    "title": p.title,
                    "content": p.content,
                    "category": p.category,
                    "tags": p.tags,
                    "version": p.version,
                }
                for p in policies
            ],
            "total": len(policies),
        }


@router.post("/faqs")
async def create_faq(faq: FAQCreate):
    from src.db.models import KnowledgeFAQ

    async with async_session() as session:
        kb_faq = KnowledgeFAQ(
            question_id=faq.question_id,
            question=faq.question,
            answer=faq.answer,
            category=faq.category,
            tags=faq.tags,
            keywords=faq.keywords,
        )
        session.add(kb_faq)
        await session.commit()
        await session.refresh(kb_faq)
        return {"status": "created", "question_id": kb_faq.question_id}


@router.get("/faqs")
async def list_faqs(category: str = None, limit: int = 20):
    from src.knowledge import KnowledgeBaseRepository

    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        faqs = await repo.search_faqs(category=category, limit=limit)
        return {
            "faqs": [
                {
                    "question_id": f.question_id,
                    "question": f.question,
                    "answer": f.answer,
                    "category": f.category,
                    "tags": f.tags,
                    "usage_count": f.usage_count,
                }
                for f in faqs
            ],
            "total": len(faqs),
        }


@router.post("/guides")
async def create_guide(guide: GuideCreate):
    from src.db.models import KnowledgeGuide

    async with async_session() as session:
        kb_guide = KnowledgeGuide(
            guide_id=guide.guide_id,
            title=guide.title,
            content=guide.content,
            guide_type=guide.guide_type,
            category=guide.category,
            tags=guide.tags,
            steps=guide.steps,
        )
        session.add(kb_guide)
        await session.commit()
        await session.refresh(kb_guide)
        return {"status": "created", "guide_id": kb_guide.guide_id}


@router.get("/guides")
async def list_guides(guide_type: str = None, category: str = None, limit: int = 20):
    from src.knowledge import KnowledgeBaseRepository

    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        guides = await repo.search_guides(guide_type=guide_type, category=category, limit=limit)
        return {
            "guides": [
                {
                    "guide_id": g.guide_id,
                    "title": g.title,
                    "content": g.content,
                    "guide_type": g.guide_type,
                    "category": g.category,
                    "tags": g.tags,
                    "steps_count": len(g.steps or []),
                }
                for g in guides
            ],
            "total": len(guides),
        }


@router.get("/policies/{policy_id}")
async def get_policy(policy_id: str):
    from src.db.models import KnowledgePolicy

    async with async_session() as session:
        result = await session.execute(select(KnowledgePolicy).where(KnowledgePolicy.policy_id == policy_id))
        policy = result.scalar_one_or_none()
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        return {
            "policy_id": policy.policy_id,
            "title": policy.title,
            "content": policy.content,
            "category": policy.category,
            "tags": policy.tags,
            "version": policy.version,
            "created_at": policy.created_at.isoformat() if policy.created_at else None,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
        }


@router.put("/policies/{policy_id}")
async def update_policy(policy_id: str, policy: PolicyCreate):
    from src.db.models import KnowledgePolicy

    async with async_session() as session:
        result = await session.execute(select(KnowledgePolicy).where(KnowledgePolicy.policy_id == policy_id))
        kb_policy = result.scalar_one_or_none()
        if not kb_policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        kb_policy.title = policy.title
        kb_policy.content = policy.content
        kb_policy.category = policy.category
        kb_policy.tags = policy.tags
        kb_policy.version = policy.version

        await session.commit()
        await session.refresh(kb_policy)
        return {"status": "updated", "policy_id": kb_policy.policy_id}


@router.delete("/policies/{policy_id}")
async def delete_policy(policy_id: str):
    from src.db.models import KnowledgePolicy

    async with async_session() as session:
        result = await session.execute(select(KnowledgePolicy).where(KnowledgePolicy.policy_id == policy_id))
        kb_policy = result.scalar_one_or_none()
        if not kb_policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        await session.delete(kb_policy)
        await session.commit()
        return {"status": "deleted", "policy_id": policy_id}


@router.get("/faqs/{question_id}")
async def get_faq(question_id: str):
    from src.db.models import KnowledgeFAQ

    async with async_session() as session:
        result = await session.execute(select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == question_id))
        faq = result.scalar_one_or_none()
        if not faq:
            raise HTTPException(status_code=404, detail="FAQ not found")
        return {
            "question_id": faq.question_id,
            "question": faq.question,
            "answer": faq.answer,
            "category": faq.category,
            "tags": faq.tags,
            "keywords": faq.keywords,
            "usage_count": faq.usage_count,
        }


@router.put("/faqs/{question_id}")
async def update_faq(question_id: str, faq: FAQCreate):
    from src.db.models import KnowledgeFAQ

    async with async_session() as session:
        result = await session.execute(select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == question_id))
        kb_faq = result.scalar_one_or_none()
        if not kb_faq:
            raise HTTPException(status_code=404, detail="FAQ not found")

        kb_faq.question = faq.question
        kb_faq.answer = faq.answer
        kb_faq.category = faq.category
        kb_faq.tags = faq.tags
        kb_faq.keywords = faq.keywords

        await session.commit()
        await session.refresh(kb_faq)
        return {"status": "updated", "question_id": kb_faq.question_id}


@router.delete("/faqs/{question_id}")
async def delete_faq(question_id: str):
    from src.db.models import KnowledgeFAQ

    async with async_session() as session:
        result = await session.execute(select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == question_id))
        kb_faq = result.scalar_one_or_none()
        if not kb_faq:
            raise HTTPException(status_code=404, detail="FAQ not found")

        await session.delete(kb_faq)
        await session.commit()
        return {"status": "deleted", "question_id": question_id}


@router.get("/guides/{guide_id}")
async def get_guide(guide_id: str):
    from src.db.models import KnowledgeGuide

    async with async_session() as session:
        result = await session.execute(select(KnowledgeGuide).where(KnowledgeGuide.guide_id == guide_id))
        guide = result.scalar_one_or_none()
        if not guide:
            raise HTTPException(status_code=404, detail="Guide not found")
        return {
            "guide_id": guide.guide_id,
            "title": guide.title,
            "content": guide.content,
            "guide_type": guide.guide_type,
            "category": guide.category,
            "tags": guide.tags,
            "steps": guide.steps,
        }


@router.put("/guides/{guide_id}")
async def update_guide(guide_id: str, guide: GuideCreate):
    from src.db.models import KnowledgeGuide

    async with async_session() as session:
        result = await session.execute(select(KnowledgeGuide).where(KnowledgeGuide.guide_id == guide_id))
        kb_guide = result.scalar_one_or_none()
        if not kb_guide:
            raise HTTPException(status_code=404, detail="Guide not found")

        kb_guide.title = guide.title
        kb_guide.content = guide.content
        kb_guide.guide_type = guide.guide_type
        kb_guide.category = guide.category
        kb_guide.tags = guide.tags
        kb_guide.steps = guide.steps

        await session.commit()
        await session.refresh(kb_guide)
        return {"status": "updated", "guide_id": kb_guide.guide_id}


@router.delete("/guides/{guide_id}")
async def delete_guide(guide_id: str):
    from src.db.models import KnowledgeGuide

    async with async_session() as session:
        result = await session.execute(select(KnowledgeGuide).where(KnowledgeGuide.guide_id == guide_id))
        kb_guide = result.scalar_one_or_none()
        if not kb_guide:
            raise HTTPException(status_code=404, detail="Guide not found")

        await session.delete(kb_guide)
        await session.commit()
        return {"status": "deleted", "guide_id": guide_id}


@router.post("/bulk-import")
async def bulk_import_knowledge(request: BulkImportRequest):
    from src.db.models import KnowledgeDocument, KnowledgeFAQ, KnowledgeGuide, KnowledgePolicy

    imported = {"policies": 0, "faqs": 0, "guides": 0, "documents": 0}
    errors = []

    async with async_session() as session:
        for policy in request.policies:
            try:
                session.add(KnowledgePolicy(
                    policy_id=policy.policy_id,
                    title=policy.title,
                    content=policy.content,
                    category=policy.category,
                    tags=policy.tags,
                    version=policy.version,
                ))
                imported["policies"] += 1
            except Exception as e:
                errors.append({"type": "policy", "id": policy.policy_id, "error": str(e)})

        for faq in request.faqs:
            try:
                session.add(KnowledgeFAQ(
                    question_id=faq.question_id,
                    question=faq.question,
                    answer=faq.answer,
                    category=faq.category,
                    tags=faq.tags,
                    keywords=faq.keywords,
                ))
                imported["faqs"] += 1
            except Exception as e:
                errors.append({"type": "faq", "id": faq.question_id, "error": str(e)})

        for guide in request.guides:
            try:
                session.add(KnowledgeGuide(
                    guide_id=guide.guide_id,
                    title=guide.title,
                    content=guide.content,
                    guide_type=guide.guide_type,
                    category=guide.category,
                    tags=guide.tags,
                    steps=guide.steps,
                ))
                imported["guides"] += 1
            except Exception as e:
                errors.append({"type": "guide", "id": guide.guide_id, "error": str(e)})

        for doc in request.documents:
            try:
                session.add(KnowledgeDocument(
                    document_id=doc.document_id,
                    title=doc.title,
                    content=doc.content,
                    document_type=doc.document_type,
                    category=doc.category,
                    tags=doc.tags,
                    file_url=doc.file_url,
                ))
                imported["documents"] += 1
            except Exception as e:
                errors.append({"type": "document", "id": doc.document_id, "error": str(e)})

        await session.commit()

    return {"status": "completed", "imported": imported, "errors": errors}


@router.post("/search/enhanced")
async def search_knowledge_enhanced(request: KnowledgeSearchRequest):
    from src.knowledge import KnowledgeRetrievalService

    async with async_session() as session:
        if llm_client and llm_client.is_initialized:
            kb_service = KnowledgeRetrievalService(session, llm_client)
            return await kb_service.search_with_llm_enhancement(
                query=request.query,
                search_type=request.search_type or "all",
                category=request.category,
                tags=request.tags,
                limit=request.limit,
            )

        kb_service = KnowledgeRetrievalService(session, None)
        return await kb_service.search(
            query=request.query,
            search_type=request.search_type,
            category=request.category,
            tags=request.tags,
            limit=request.limit,
            offset=request.offset,
        )


@router.post("/documents")
async def create_document(document: DocumentCreate):
    from src.db.models import KnowledgeDocument

    async with async_session() as session:
        kb_doc = KnowledgeDocument(
            document_id=document.document_id,
            title=document.title,
            content=document.content,
            document_type=document.document_type,
            category=document.category,
            tags=document.tags,
            file_url=document.file_url,
        )
        session.add(kb_doc)
        await session.commit()
        await session.refresh(kb_doc)
        return {"status": "created", "document_id": kb_doc.document_id}


@router.get("/documents")
async def list_documents(document_type: str = None, category: str = None, limit: int = 20):
    from src.knowledge import KnowledgeBaseRepository

    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        docs = await repo.search_documents(document_type=document_type, category=category, limit=limit)
        return {
            "documents": [
                {
                    "document_id": d.document_id,
                    "title": d.title,
                    "content": d.content,
                    "document_type": d.document_type,
                    "category": d.category,
                    "tags": d.tags,
                    "file_url": d.file_url,
                }
                for d in docs
            ],
            "total": len(docs),
        }


@router.get("/documents/{document_id}")
async def get_document(document_id: str):
    from src.db.models import KnowledgeDocument

    async with async_session() as session:
        result = await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.document_id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "document_id": doc.document_id,
            "title": doc.title,
            "content": doc.content,
            "document_type": doc.document_type,
            "category": doc.category,
            "tags": doc.tags,
            "file_url": doc.file_url,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }


@router.put("/documents/{document_id}")
async def update_document(document_id: str, document: DocumentCreate):
    from src.db.models import KnowledgeDocument

    async with async_session() as session:
        result = await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.document_id == document_id))
        kb_doc = result.scalar_one_or_none()
        if not kb_doc:
            raise HTTPException(status_code=404, detail="Document not found")

        kb_doc.title = document.title
        kb_doc.content = document.content
        kb_doc.document_type = document.document_type
        kb_doc.category = document.category
        kb_doc.tags = document.tags
        kb_doc.file_url = document.file_url

        await session.commit()
        await session.refresh(kb_doc)
        return {"status": "updated", "document_id": kb_doc.document_id}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    from src.db.models import KnowledgeDocument

    async with async_session() as session:
        result = await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.document_id == document_id))
        kb_doc = result.scalar_one_or_none()
        if not kb_doc:
            raise HTTPException(status_code=404, detail="Document not found")

        await session.delete(kb_doc)
        await session.commit()
        return {"status": "deleted", "document_id": document_id}
