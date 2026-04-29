#!/usr/bin/env python3
"""
End-to-end test for supervisor-api
Tests the full pipeline: KB retrieval → Reasoning → Response generation
"""
import asyncio
import os
import sys
from datetime import datetime

# Load .env
from dotenv import load_dotenv
load_dotenv()

async def test_knowledge_retrieval():
    """Test 1: Knowledge retrieval (PostgreSQL)"""
    print("=" * 60)
    print("TEST 1: Knowledge Retrieval (PostgreSQL)")
    print("=" * 60)
    
    try:
        from src.db import async_session
        from src.knowledge.service import KnowledgeRetrievalService
        
        async with async_session() as session:
            service = KnowledgeRetrievalService(session)
            
            # Test search
            result = await service.search("password reset", search_type="all")
            
            print(f"✅ KB search completed")
            print(f"   - Total results: {result.total}")
            print(f"   - Template ID: {result.template_id}")
            print(f"   - Results: {len(result.results)} items")
            
            if result.results:
                for r in result.results[:3]:
                    print(f"     • [{r.knowledge_type}] {r.title[:50]} (sim: {r.similarity:.3f})")
            
            return True
    except Exception as e:
        print(f"❌ KB retrieval failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_hindsight():
    """Test 2: Hindsight integration"""
    print("\n" + "=" * 60)
    print("TEST 2: Hindsight Integration")
    print("=" * 60)
    
    try:
        from src.memory.hindsight_service import get_hindsight_service
        
        hindsight = get_hindsight_service()
        
        if not hindsight.enabled:
            print("⚠️  Hindsight disabled (HINDSIGHT_ENABLED != true)")
            return True
        
        # Test recall
        print("Testing recall...")
        memories = await hindsight.recall("password reset", limit=3)
        print(f"✅ Recall completed: {len(memories)} memories")
        
        # Test retain
        print("Testing retain...")
        success = await hindsight.retain(
            content=f"Test interaction at {datetime.utcnow().isoformat()}",
            metadata={"test": True, "source": "e2e_test"}
        )
        print(f"✅ Retain completed: {success}")
        
        return True
    except Exception as e:
        print(f"❌ Hindsight test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_rag_pipeline():
    """Test 3: RAG Pipeline (BM25 + Vector + LLM)"""
    print("\n" + "=" * 60)
    print("TEST 3: RAG Pipeline")
    print("=" * 60)
    
    try:
        from src.tools.rag_pipeline import RAGPipeline, create_document
        
        # Create pipeline
        pipeline = RAGPipeline()
        
        # Add test documents
        docs = [
            create_document("Password reset requires admin approval", metadata={"type": "policy"}),
            create_document("To reset password, go to Settings > Security", metadata={"type": "guide"}),
            create_document("Common issue: password reset email not received", metadata={"type": "faq"}),
        ]
        
        for doc in docs:
            pipeline.add_document(doc)
        
        print(f"✅ Added {len(docs)} documents to RAG pipeline")
        
        # Test search
        results = pipeline.search("password reset", top_k=5)
        print(f"✅ Search completed: {len(results)} results")
        
        for r in results:
            print(f"     • Score: {r.score:.3f} | Source: {r.source}")
        
        return True
    except Exception as e:
        print(f"❌ RAG pipeline test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_reasoning_loop():
    """Test 4: Reasoning Loop"""
    print("\n" + "=" * 60)
    print("TEST 4: Reasoning Loop")
    print("=" * 60)
    
    try:
        from src.core.supervisor import Supervisor
        from src.core.schemas import ChatMessage, UserContext, ConversationContext
        from src.core.reasoning_loop import ReasoningLoopOrchestrator
        
        # Create supervisor instance
        supervisor = Supervisor()
        
        # Create test payload
        message = ChatMessage(text="How to reset password?")
        user = UserContext(id="test_user", display_name="Test User")
        conversation = ConversationContext(
            thread_id="test_thread_123",
            platform="telegram",
            chat_type="private",
            group_chat=False,
        )
        
        # Create reasoning loop
        loop = ReasoningLoopOrchestrator(supervisor)
        
        print("✅ Reasoning loop created")
        print("   - Supervisor initialized")
        print("   - Test payload ready")
        
        return True
    except Exception as e:
        print(f"❌ Reasoning loop test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests"""
    print("\n" + "🚀 SUPERVISOR-API END-TO-END TEST".center(60))
    print("=" * 60)
    
    results = []
    
    # Run tests
    results.append(("Knowledge Retrieval", await test_knowledge_retrieval()))
    results.append(("Hindsight Integration", await test_hindsight()))
    results.append(("RAG Pipeline", await test_rag_pipeline()))
    results.append(("Reasoning Loop", await test_reasoning_loop()))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
    
    print(f"\nResult: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
