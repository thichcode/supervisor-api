"""
RAG Pipeline - Hybrid search combining BM25 + Vector (semantic) search
Supports multiple vector stores: ChromaDB, Qdrant, Pinecone
"""

import hashlib
import json
import re
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import structlog

logger = structlog.get_logger()

# Try importing vector store libraries
CHROMADB_AVAILABLE = False
QDRANT_AVAILABLE = False
PINECONE_AVAILABLE = False

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    pass

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
    QDRANT_AVAILABLE = True
except ImportError:
    pass

try:
    import pinecone
    PINECONE_AVAILABLE = True
except ImportError:
    pass

# Optional: sentence transformers for embeddings
SENTENCE_TRANSFORMERS_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    pass


@dataclass
class Document:
    """Document for RAG indexing"""
    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def text(self) -> str:
        return self.content


@dataclass
class SearchResult:
    """Search result with score"""
    document: Document
    score: float
    rerank_score: Optional[float] = None
    source: str = "hybrid"  # "bm25", "vector", "hybrid"


class RAGConfig:
    """RAG configuration"""
    def __init__(
        self,
        vector_store: str = "chroma",  # chroma, qdrant, pinecone
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        vector_dimension: int = 384,
        collection_name: str = "supervisor_knowledge",
        # ChromaDB config
        chroma_persist_dir: str = "./data/chroma",
        # Qdrant config
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        qdrant_collection: str = "supervisor_knowledge",
        # Pinecone config
        pinecone_api_key: str = "",
        pinecone_environment: str = "us-west1",
        pinecone_index: str = "supervisor-knowledge",
        # Search config
        bm25_weight: float = 0.3,
        vector_weight: float = 0.5,
        rerank_weight: float = 0.2,
        top_k: int = 10,
    ):
        self.vector_store = vector_store
        self.embedding_model = embedding_model
        self.vector_dimension = vector_dimension
        self.collection_name = collection_name
        self.chroma_persist_dir = chroma_persist_dir
        self.qdrant_host = qdrant_host
        self.qdrant_port = qdrant_port
        self.qdrant_collection = qdrant_collection
        self.pinecone_api_key = pinecone_api_key
        self.pinecone_environment = pinecone_environment
        self.pinecone_index = pinecone_index
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        self.rerank_weight = rerank_weight
        self.top_k = top_k


class BM25Indexer:
    """BM25 search implementation"""
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: Dict[str, Document] = {}
        self.doc_lengths: Dict[str, int] = {}
        self.avg_doc_length = 0
        self.term_doc_freq: Dict[str, int] = {}
        self.N = 0  # Total documents
    
    def add_document(self, doc: Document):
        """Add document to index"""
        self.documents[doc.id] = doc
        self.N += 1
        
        # Tokenize
        tokens = self._tokenize(doc.content)
        self.doc_lengths[doc.id] = len(tokens)
        
        # Update term frequencies
        for token in tokens:
            self.term_doc_freq[token] = self.term_doc_freq.get(token, 0) + 1
        
        # Update avg length
        total_len = sum(self.doc_lengths.values())
        self.avg_doc_length = total_len / self.N if self.N > 0 else 0
    
    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenization"""
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        tokens = text.split()
        return [t for t in tokens if len(t) > 1]
    
    def _calculate_idf(self, term: str) -> float:
        """Calculate IDF for term"""
        df = self.term_doc_freq.get(term, 0)
        if df == 0:
            return 0
        return max(0, self.k1 + 1) / (self.k1 * ((df / self.N) + 1))
    
    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Search using BM25"""
        query_tokens = self._tokenize(query)
        
        scores = {}
        for doc_id, doc in self.documents.items():
            doc_tokens = self._tokenize(doc.content)
            doc_len = self.doc_lengths.get(doc_id, 0)
            
            score = 0
            for term in query_tokens:
                tf = doc_tokens.count(term)
                if tf > 0:
                    idf = self._calculate_idf(term)
                    # BM25 formula
                    term_score = idf * (tf * (self.k1 + 1)) / (
                        tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_length)
                    )
                    score += term_score
            
            if score > 0:
                scores[doc_id] = score
        
        # Sort by score
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_scores[:top_k]


class EmbeddingModel:
    """Embedding model wrapper"""
    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
    
    def load(self):
        """Lazy load the model"""
        if self._model is None and SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.info("Loading embedding model", model=self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model
    
    def encode(self, texts: List[str]) -> List[List[float]]:
        """Encode texts to embeddings"""
        model = self.load()
        if model is None:
            # Fallback: random embeddings
            import random
            dim = 384
            return [[random.random() for _ in range(dim)] for _ in texts]
        
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()
    
    def encode_single(self, text: str) -> List[float]:
        """Encode single text"""
        return self.encode([text])[0]


class RAGPipeline:
    """
    Hybrid RAG pipeline combining BM25 + Vector search
    """
    
    def __init__(self, config: Optional[RAGConfig] = None):
        self.config = config or RAGConfig()
        self.bm25_indexer = BM25Indexer()
        self.embedding_model = EmbeddingModel(self.config.embedding_model)
        self._vector_store = None
        self._initialized = False
    
    def initialize(self):
        """Initialize the vector store"""
        if self._initialized:
            return
        
        if self.config.vector_store == "chroma":
            self._init_chroma()
        elif self.config.vector_store == "qdrant":
            self._init_qdrant()
        elif self.config.vector_store == "pinecone":
            self._init_pinecone()
        
        self._initialized = True
        logger.info("RAG pipeline initialized", 
                   vector_store=self.config.vector_store,
                   embedding_model=self.config.embedding_model)
    
    def _init_chroma(self):
        """Initialize ChromaDB"""
        if not CHROMADB_AVAILABLE:
            logger.warning("ChromaDB not available, using in-memory fallback")
            return
        
        self._vector_store = chromadb.PersistentClient(
            path=self.config.chroma_persist_dir
        )
        
        # Create or get collection
        try:
            self._collection = self._vector_store.get_collection(
                name=self.config.collection_name
            )
        except Exception:
            self._collection = self._vector_store.create_collection(
                name=self.config.collection_name,
                metadata={"dimension": self.config.vector_dimension}
            )
    
    def _init_qdrant(self):
        """Initialize Qdrant"""
        if not QDRANT_AVAILABLE:
            logger.warning("Qdrant not available")
            return
        
        self._qdrant_client = QdrantClient(
            host=self.config.qdrant_host,
            port=self.config.qdrant_port
        )
    
    def _init_pinecone(self):
        """Initialize Pinecone"""
        if not PINECONE_AVAILABLE:
            logger.warning("Pinecone not available")
            return
        
        pinecone.init(
            api_key=self.config.pinecone_api_key,
            environment=self.config.pinecone_environment
        )
        
        # Create index if not exists
        if self.config.pinecone_index not in pinecone.list_indexes():
            pinecone.create_index(
                self.config.pinecone_index,
                dimension=self.config.vector_dimension,
                metric="cosine"
            )
        
        self._pinecone_index = pinecone.Index(self.config.pinecone_index)
    
    def add_document(self, doc: Document):
        """Add document to RAG pipeline"""
        self.initialize()
        
        # Add to BM25
        self.bm25_indexer.add_document(doc)
        
        # Generate embedding
        if doc.embedding is None:
            doc.embedding = self.embedding_model.encode_single(doc.content)
        
        # Add to vector store
        if self.config.vector_store == "chroma" and CHROMADB_AVAILABLE:
            self._collection.add(
                ids=[doc.id],
                embeddings=[doc.embedding],
                documents=[doc.content],
                metadatas=[doc.metadata]
            )
        elif self.config.vector_store == "qdrant" and QDRANT_AVAILABLE:
            self._qdrant_client.upsert(
                collection_name=self.config.qdrant_collection,
                points=[{
                    "id": doc.id,
                    "vector": doc.embedding,
                    "payload": {
                        "content": doc.content,
                        **doc.metadata
                    }
                }]
            )
        elif self.config.vector_store == "pinecone" and PINECONE_AVAILABLE:
            self._pinecone_index.upsert(vectors=[{
                "id": doc.id,
                "values": doc.embedding,
                "metadata": doc.metadata
            }])
    
    def add_documents(self, documents: List[Document]):
        """Add multiple documents"""
        for doc in documents:
            self.add_document(doc)
    
    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """
        Hybrid search combining BM25 + Vector
        """
        self.initialize()
        top_k = top_k or self.config.top_k
        
        results: Dict[str, SearchResult] = {}
        
        # 1. BM25 Search
        bm25_results = self.bm25_indexer.search(query, top_k * 2)
        for doc_id, bm25_score in bm25_results:
            doc = self.bm25_indexer.documents[doc_id]
            results[doc_id] = SearchResult(
                document=doc,
                score=bm25_score * self.config.bm25_weight,
                source="bm25"
            )
        
        # 2. Vector Search
        query_embedding = self.embedding_model.encode_single(query)
        
        if self.config.vector_store == "chroma" and CHROMADB_AVAILABLE:
            vector_results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k * 2,
                where=filter_metadata
            )
            
            if vector_results and vector_results['ids']:
                for i, doc_id in enumerate(vector_results['ids'][0]):
                    if doc_id in results:
                        # Update with vector score
                        vector_score = vector_results['distances'][0][i]
                        results[doc_id].score += (1 - vector_score) * self.config.vector_weight
                        results[doc_id].source = "hybrid"
                    else:
                        doc = Document(
                            id=doc_id,
                            content=vector_results['documents'][0][i],
                            metadata=vector_results['metadatas'][0][i] if vector_results.get('metadatas') else {}
                        )
                        results[doc_id] = SearchResult(
                            document=doc,
                            score=(1 - vector_score) * self.config.vector_weight,
                            source="vector"
                        )
        
        # 3. Combine and sort
        sorted_results = sorted(
            results.values(),
            key=lambda x: x.score,
            reverse=True
        )
        
        return sorted_results[:top_k]
    
    def search_vector_only(self, query: str, top_k: int = 10) -> List[SearchResult]:
        """Vector-only search (semantic)"""
        query_embedding = self.embedding_model.encode_single(query)
        
        if self.config.vector_store == "chroma" and CHROMADB_AVAILABLE:
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k
            )
            
            search_results = []
            for i, doc_id in enumerate(results['ids'][0]):
                doc = Document(
                    id=doc_id,
                    content=results['documents'][0][i],
                    metadata=results['metadatas'][0][i] if results.get('metadatas') else {}
                )
                search_results.append(SearchResult(
                    document=doc,
                    score=1 - results['distances'][0][i],
                    source="vector"
                ))
            
            return search_results
        
        return []
    
    def search_bm25_only(self, query: str, top_k: int = 10) -> List[SearchResult]:
        """BM25-only search (keyword)"""
        bm25_results = self.bm25_indexer.search(query, top_k)
        
        return [
            SearchResult(
                document=self.bm25_indexer.documents[doc_id],
                score=score,
                source="bm25"
            )
            for doc_id, score in bm25_results
        ]
    
    def delete_document(self, doc_id: str):
        """Delete document from index"""
        if self.config.vector_store == "chroma" and CHROMADB_AVAILABLE:
            self._collection.delete(ids=[doc_id])
        
        # Remove from BM25 (rebuild)
        if doc_id in self.bm25_indexer.documents:
            del self.bm25_indexer.documents[doc_id]
    
    def clear(self):
        """Clear all documents"""
        if self.config.vector_store == "chroma" and CHROMADB_AVAILABLE:
            self._vector_store.delete_collection(self.config.collection_name)
            self._collection = self._vector_store.create_collection(
                name=self.config.collection_name,
                metadata={"dimension": self.config.vector_dimension}
            )
        
        self.bm25_indexer = BM25Indexer()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics"""
        return {
            "vector_store": self.config.vector_store,
            "embedding_model": self.config.embedding_model,
            "bm25_documents": len(self.bm25_indexer.documents),
            "config": {
                "bm25_weight": self.config.bm25_weight,
                "vector_weight": self.config.vector_weight,
                "top_k": self.config.top_k,
            }
        }


# Convenience functions
def create_document(
    content: str,
    doc_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Document:
    """Create a document with auto-generated ID"""
    if doc_id is None:
        doc_id = hashlib.md5(content.encode()).hexdigest()[:16]
    
    return Document(
        id=doc_id,
        content=content,
        metadata=metadata or {}
    )


# Global RAG pipeline instance
_rag_pipeline: Optional[RAGPipeline] = None


def get_rag_pipeline(config: Optional[RAGConfig] = None) -> RAGPipeline:
    """Get or create global RAG pipeline"""
    global _rag_pipeline
    if _rag_pipeline is None:
        _rag_pipeline = RAGPipeline(config)
        _rag_pipeline.initialize()
    return _rag_pipeline
