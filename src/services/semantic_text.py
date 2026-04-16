from __future__ import annotations

from functools import lru_cache
import hashlib
import math
import re
from typing import Optional

import structlog

logger = structlog.get_logger()


class SemanticTextEncoder:
    """Encode text into semantic vectors with an optional transformer backend.

    When ``sentence_transformers`` is unavailable, the encoder falls back to a
    deterministic hashed feature vector that mixes tokens, stems, character
    n-grams, and token n-grams. The fallback is intentionally stable so it can
    be used in tests and offline deployments.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", dimension: int = 384):
        self.model_name = model_name
        self.dimension = dimension
        self._model: Optional[object] = None
        self._transformers_available: Optional[bool] = None
        self._cache: dict[str, list[float]] = {}

    def encode(self, text: str) -> list[float]:
        normalized = self._normalize_text(text)
        if not normalized:
            return [0.0] * self.dimension

        cached = self._cache.get(normalized)
        if cached is not None:
            return list(cached)

        model = self._load_model()
        if model is not None:
            vector = self._encode_with_transformer(model, normalized)
        else:
            vector = self._encode_with_fallback(normalized)

        self._cache[normalized] = vector
        return list(vector)

    def similarity(self, text_a: str, text_b: str) -> float:
        vector_a = self.encode(text_a)
        vector_b = self.encode(text_b)
        return cosine_similarity(vector_a, vector_b)

    def _load_model(self) -> Optional[object]:
        if self._transformers_available is False:
            return None
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            self._transformers_available = False
            return None

        try:
            self._model = SentenceTransformer(self.model_name)
            self._transformers_available = True
            logger.info("semantic_encoder_loaded", model=self.model_name)
            return self._model
        except Exception as exc:
            logger.warning("semantic_encoder_load_failed", model=self.model_name, error=str(exc))
            self._transformers_available = False
            return None

    def _encode_with_transformer(self, model: object, text: str) -> list[float]:
        encoded = model.encode([text], show_progress_bar=False)
        vector = encoded[0].tolist() if hasattr(encoded[0], "tolist") else list(encoded[0])
        return self._normalize_vector([float(value) for value in vector])

    def _encode_with_fallback(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = self._tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            self._add_feature(vector, f"tok:{token}", 1.0)
            stem = self._stem(token)
            if stem != token:
                self._add_feature(vector, f"stem:{stem}", 0.7)

        for first, second in zip(tokens, tokens[1:]):
            self._add_feature(vector, f"bigram:{first} {second}", 1.15)

        compact = text.replace(" ", "")
        for ngram in self._character_ngrams(compact, 3, 5):
            self._add_feature(vector, f"char:{ngram}", 0.3)

        return self._normalize_vector(vector)

    def _add_feature(self, vector: list[float], feature: str, weight: float) -> None:
        index = self._stable_hash(feature) % self.dimension
        sign = -1.0 if self._stable_hash(f"sign:{feature}") % 2 else 1.0
        vector[index] += sign * weight

    def _normalize_vector(self, vector: list[float]) -> list[float]:
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [value / magnitude for value in vector]

    def _normalize_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        normalized = re.sub(r"[^\w\s\-]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def _tokenize(self, text: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9à-ỹ_]+", text.lower())
        return [self._stem(token) for token in tokens if len(token) > 1]

    def _stem(self, token: str) -> str:
        for suffix in ("ing", "edly", "edly", "ed", "es", "s"):
            if len(token) > 4 and token.endswith(suffix):
                return token[: -len(suffix)]
        return token

    def _character_ngrams(self, text: str, min_size: int, max_size: int) -> list[str]:
        if len(text) < min_size:
            return []
        ngrams: list[str] = []
        for size in range(min_size, max_size + 1):
            if len(text) < size:
                continue
            for index in range(len(text) - size + 1):
                ngrams.append(text[index : index + size])
        return ngrams

    @lru_cache(maxsize=4096)
    def _stable_hash(self, value: str) -> int:
        digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False)


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    dot_product = sum(left * right for left, right in zip(vector_a, vector_b))
    magnitude_a = math.sqrt(sum(value * value for value in vector_a))
    magnitude_b = math.sqrt(sum(value * value for value in vector_b))
    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0
    return dot_product / (magnitude_a * magnitude_b)
