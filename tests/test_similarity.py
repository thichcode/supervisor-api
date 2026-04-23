"""Tests for KB similarity scoring."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.knowledge.service import KnowledgeRetrievalService


class MockSession(MagicMock):
    async def execute(self, *args, **kwargs):
        return MagicMock(scalars=MagicMock(all=MagicMock(return_value=[])))

    async def commit(self):
        pass

    async def refresh(self, *args, **kwargs):
        pass


def _make_service():
    session = MockSession()
    return KnowledgeRetrievalService(session=session)


class TestCalculateTextSimilarity:
    def test_single_word_query_exact_match(self):
        """Single word matching a large document should NOT score 1.0."""
        svc = _make_service()
        # Query: "sharepoint" (1 word), Document: 50 words with sharepoint
        doc = "This document covers sharepoint access, onedrive sync, and file permissions for enterprise users"
        score = svc._calculate_text_similarity("sharepoint", doc)
        # With Jaccard: 1 intersection / 50 union ≈ 0.02
        assert score < 1.0, f"Score {score} should be < 1.0 for single-word match in large doc"

    def test_single_word_query_no_match(self):
        """Single word NOT in document should return floor, not 0."""
        svc = _make_service()
        doc = "This document is about VPN and network access only"
        score = svc._calculate_text_similarity("sharepoint", doc)
        assert score == 0.4  # floor when no intersection

    def test_multi_word_query_partial_match(self):
        """Multi-word query with 1 shared word should score lower."""
        svc = _make_service()
        doc = "VPN access requires sharepoint credentials and two-factor authentication"
        score = svc._calculate_text_similarity("vpn sharepoint password", doc)
        # intersection = {vpn, sharepoint} → 2 words
        # union = {vpn, sharepoint, password, access, requires, credentials, and, two, factor, authentication} → 10+
        # jaccard = 2/10 = 0.2, no len penalty (3 words > 2)
        assert 0.1 < score < 0.5, f"Score {score} should be between 0.1 and 0.5"

    def test_full_match_both_directions(self):
        """When query and doc have the same words, should approach 1.0."""
        svc = _make_service()
        doc = "vpn access password"
        score = svc._calculate_text_similarity("vpn access password", doc)
        # union = {vpn, access, password} → 3
        # jaccard = 3/3 = 1.0
        assert score == 1.0, f"Score {score} should be 1.0 for full match"

    def test_short_query_penalty(self):
        """Short query (1 word) should get 0.6x penalty."""
        svc = _make_service()
        doc = "vpn password sharepoint onedrive"
        score = svc._calculate_text_similarity("vpn", doc)
        # intersection = {vpn} = 1, union = {vpn, password, sharepoint, onedrive} = 4
        # jaccard = 1/4 = 0.25, len_penalty = 0.6 (1 word)
        # result = 0.25 * 0.6 = 0.15
        assert score == pytest.approx(0.15, abs=0.02)

    def test_empty_query_returns_floor(self):
        """Empty query returns floor."""
        svc = _make_service()
        score = svc._calculate_text_similarity("", "some text")
        assert score == 0.4

    def test_empty_text_returns_floor(self):
        """Empty text returns floor."""
        svc = _make_service()
        score = svc._calculate_text_similarity("sharepoint", "")
        assert score == 0.4