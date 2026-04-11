"""
BM25/TF-IDF Search for Knowledge Base
Enhanced knowledge retrieval with probabilistic ranking
"""

import math
import re
from typing import List, Dict, Optional, Tuple
from collections import Counter
import structlog

logger = structlog.get_logger()


class BM25Search:
    """
    BM25 (Best Matching 25) ranking function for information retrieval.
    More sophisticated than simple TF-IDF, considers document length normalization.
    """
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """
        Args:
            k1: Term frequency saturation parameter (typical: 1.5-2.0)
            b: Length normalization parameter (typical: 0.75)
        """
        self.k1 = k1
        self.b = b
        self.documents: Dict[str, Dict] = {}  # id -> {text, title, metadata}
        self.doc_lengths: List[int] = []
        self.avg_doc_length: float = 0
        self.doc_count: int = 0
        self.term_doc_freq: Dict[str, int] = {}  # term -> num docs containing term
        self.idf_cache: Dict[str, float] = {}
    
    def add_document(self, doc_id: str, text: str, title: str = "", metadata: dict = None) -> None:
        """Add a document to the index."""
        # Tokenize
        tokens = self._tokenize(text)
        
        self.documents[doc_id] = {
            "text": text,
            "title": title,
            "tokens": tokens,
            "metadata": metadata or {}
        }
        
        # Update term document frequency
        unique_terms = set(tokens)
        for term in unique_terms:
            self.term_doc_freq[term] = self.term_doc_freq.get(term, 0) + 1
        
        self._recalculate_stats()
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into terms."""
        # Convert to lowercase and split
        text = text.lower()
        # Remove special characters but keep Vietnamese characters
        text = re.sub(r'[^\w\s]', ' ', text)
        tokens = text.split()
        # Remove very short tokens
        tokens = [t for t in tokens if len(t) > 1]
        return tokens
    
    def _recalculate_stats(self) -> None:
        """Recalculate document statistics."""
        self.doc_count = len(self.documents)
        self.doc_lengths = [len(doc["tokens"]) for doc in self.documents.values()]
        self.avg_doc_length = sum(self.doc_lengths) / self.doc_count if self.doc_count > 0 else 1
    
    def _get_idf(self, term: str) -> float:
        """Calculate IDF for a term."""
        if term in self.idf_cache:
            return self.idf_cache[term]
        
        df = self.term_doc_freq.get(term, 0)
        if df == 0:
            idf = 0
        else:
            # Standard IDF formula with smoothing
            idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)
        
        self.idf_cache[term] = idf
        return idf
    
    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        Search documents using BM25 ranking.
        
        Args:
            query: Search query
            top_k: Number of top results to return
            
        Returns:
            List of ranked documents with scores
        """
        if not self.documents:
            return []
        
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        
        scores = []
        
        for doc_id, doc in self.documents.items():
            score = self._calculate_bm25_score(query_tokens, doc)
            if score > 0:
                scores.append({
                    "doc_id": doc_id,
                    "score": score,
                    "title": doc["title"],
                    "text": doc["text"],
                    "metadata": doc["metadata"]
                })
        
        # Sort by score descending
        scores.sort(key=lambda x: x["score"], reverse=True)
        
        return scores[:top_k]
    
    def _calculate_bm25_score(self, query_tokens: List[str], doc: Dict) -> float:
        """Calculate BM25 score for a document."""
        doc_tokens = doc["tokens"]
        doc_len = len(doc_tokens)
        
        if doc_len == 0:
            return 0
        
        # Term frequency in document
        tf = Counter(doc_tokens)
        
        score = 0.0
        
        for term in query_tokens:
            term_tf = tf.get(term, 0)
            if term_tf == 0:
                continue
            
            idf = self._get_idf(term)
            
            # BM25 formula
            numerator = term_tf * (self.k1 + 1)
            denominator = term_tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_length)
            
            score += idf * (numerator / denominator)
        
        return score
    
    def get_stats(self) -> Dict:
        """Get index statistics."""
        return {
            "doc_count": self.doc_count,
            "avg_doc_length": self.avg_doc_length,
            "unique_terms": len(self.term_doc_freq)
        }


class TFIDFSearch:
    """
    TF-IDF (Term Frequency-Inverse Document Frequency) search.
    Simpler than BM25, good for smaller datasets.
    """
    
    def __init__(self):
        self.documents: Dict[str, Dict] = {}
        self.term_doc_freq: Dict[str, int] = {}
        self.doc_term_freqs: Dict[str, Dict[str, int]] = {}  # doc_id -> term -> freq
        self.doc_count: int = 0
        self.idf_cache: Dict[str, float] = {}
    
    def add_document(self, doc_id: str, text: str, title: str = "", metadata: dict = None) -> None:
        """Add a document to the index."""
        tokens = self._tokenize(text)
        
        self.documents[doc_id] = {
            "text": text,
            "title": title,
            "tokens": tokens,
            "metadata": metadata or {}
        }
        
        # Term frequency for this document
        tf = Counter(tokens)
        self.doc_term_freqs[doc_id] = dict(tf)
        
        # Update document frequency
        for term in tf.keys():
            self.term_doc_freq[term] = self.term_doc_freq.get(term, 0) + 1
        
        self.doc_count = len(self.documents)
        self.idf_cache.clear()  # Invalidate cache
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text."""
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        tokens = text.split()
        return [t for t in tokens if len(t) > 1]
    
    def _get_idf(self, term: str) -> float:
        """Calculate IDF."""
        if term in self.idf_cache:
            return self.idf_cache[term]
        
        df = self.term_doc_freq.get(term, 0)
        if df == 0:
            idf = 0
        else:
            idf = math.log(self.doc_count / df)
        
        self.idf_cache[term] = idf
        return idf
    
    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """Search using TF-IDF scoring."""
        if not self.documents:
            return []
        
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        
        scores = []
        
        for doc_id, doc in self.documents.items():
            score = self._calculate_tfidf_score(query_tokens, doc_id)
            if score > 0:
                scores.append({
                    "doc_id": doc_id,
                    "score": score,
                    "title": doc["title"],
                    "text": doc["text"],
                    "metadata": doc["metadata"]
                })
        
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]
    
    def _calculate_tfidf_score(self, query_tokens: List[str], doc_id: str) -> float:
        """Calculate TF-IDF score."""
        doc_tf = self.doc_term_freqs.get(doc_id, {})
        
        score = 0.0
        for term in query_tokens:
            tf = doc_tf.get(term, 0)
            if tf == 0:
                continue
            
            idf = self._get_idf(term)
            # TF-IDF: (1 + log(tf)) * idf
            tf_score = (1 + math.log(tf)) * idf
            score += tf_score
        
        return score
    
    def get_stats(self) -> Dict:
        """Get index statistics."""
        return {
            "doc_count": self.doc_count,
            "unique_terms": len(self.term_doc_freq)
        }


class HybridSearch:
    """
    Combines BM25 and TF-IDF with learned weights.
    Uses ensemble method for better results.
    """
    
    def __init__(self, bm25_weight: float = 0.7, tfidf_weight: float = 0.3):
        self.bm25 = BM25Search()
        self.tfidf = TFIDFSearch()
        self.bm25_weight = bm25_weight
        self.tfidf_weight = tfidf_weight
    
    def add_document(self, doc_id: str, text: str, title: str = "", metadata: dict = None) -> None:
        """Add document to both indexes."""
        self.bm25.add_document(doc_id, text, title, metadata)
        self.tfidf.add_document(doc_id, text, title, metadata)
    
    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """Search using weighted ensemble."""
        bm25_results = {r["doc_id"]: r["score"] for r in self.bm25.search(query, top_k * 2)}
        tfidf_results = {r["doc_id"]: r["score"] for r in self.tfidf.search(query, top_k * 2)}
        
        # Normalize scores
        max_bm25 = max(bm25_results.values()) if bm25_results else 1
        max_tfidf = max(tfidf_results.values()) if tfidf_results else 1
        
        # Combine scores
        combined = {}
        all_doc_ids = set(bm25_results.keys()) | set(tfidf_results.keys())
        
        for doc_id in all_doc_ids:
            bm25_norm = bm25_results.get(doc_id, 0) / max_bm25
            tfidf_norm = tfidf_results.get(doc_id, 0) / max_tfidf
            
            combined[doc_id] = (
                self.bm25_weight * bm25_norm + 
                self.tfidf_weight * tfidf_norm
            )
        
        # Get document info
        docs = self.bm25.documents
        results = [
            {
                "doc_id": doc_id,
                "score": score,
                "title": docs.get(doc_id, {}).get("title", ""),
                "text": docs.get(doc_id, {}).get("text", ""),
                "metadata": docs.get(doc_id, {}).get("metadata", {}),
                "bm25_score": bm25_results.get(doc_id, 0),
                "tfidf_score": tfidf_results.get(doc_id, 0)
            }
            for doc_id, score in combined.items()
        ]
        
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


# Factory function
def create_search_engine(engine_type: str = "hybrid") -> HybridSearch:
    """
    Create a search engine instance.
    
    Args:
        engine_type: "bm25", "tfidf", or "hybrid"
        
    Returns:
        Search engine instance
    """
    if engine_type == "bm25":
        return BM25Search()
    elif engine_type == "tfidf":
        return TFIDFSearch()
    else:
        return HybridSearch()


# Example usage and testing
if __name__ == "__main__":
    # Create hybrid search engine
    search = HybridSearch()
    
    # Add sample documents (policies)
    documents = [
        ("policy_001", "Chính sách nghỉ phép năm 2024. Nhân viên được nghỉ 12 ngày phép/năm. Ngày nghỉ được tính theo công ty.", "Chính sách nghỉ phép"),
        ("policy_002", "Quy định giờ làm việc từ thứ 2 đến thứ 6. Sáng từ 8h đến 12h, chiều từ 13h đến 17h30.", "Giờ làm việc"),
        ("policy_003", "Chính sách bảo hiểm xã hội. Công ty đóng BHXH 17% lương. Nhân viên đóng 8%.", "Bảo hiểm xã hội"),
        ("policy_004", "Quy định về laptop công ty. Nhân viên được cấp laptop theo yêu cầu công việc. Bảo hành 3 năm.", "Laptop công ty"),
        ("policy_005", "Chính sách đào tạo. Nhân viên được tham gia các khóa đào tạo nội bộ và bên ngoài. Ngân sách 10 triệu/năm.", "Đào tạo"),
    ]
    
    for doc_id, text, title in documents:
        search.add_document(doc_id, text, title)
    
    # Search test
    print("=== BM25/TF-IDF Search Test ===\n")
    
    test_queries = [
        "nghỉ phép",
        "bảo hiểm",
        "laptop",
        "giờ làm việc",
        "đào tạo nhân viên"
    ]
    
    for query in test_queries:
        print(f"Query: '{query}'")
        results = search.search(query, top_k=3)
        for i, r in enumerate(results, 1):
            print(f"  {i}. [{r['score']:.3f}] {r['title']}")
        print()