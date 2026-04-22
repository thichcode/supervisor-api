import pytest
from unittest.mock import AsyncMock

from src.services.interaction_service import InteractionService
from src.core.traffic_classification import classify_traffic_class


class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    def __init__(self):
        self.added = None
        self.execute = AsyncMock(return_value=_FakeResult())
        self.flush = AsyncMock()

    def add(self, obj):
        self.added = obj


@pytest.mark.asyncio
async def test_interaction_service_sets_traffic_class_from_service_keywords():
    session = _FakeSession()
    service = InteractionService(session)  # type: ignore[arg-type]

    log = await service.log_interaction(
        request_id="req-1",
        thread_id="thread-1",
        user_id="user-1",
        input_text="Anh check giúp em ticket VPN đang lỗi nhé",
        output_text="Để mình kiểm tra",
        intent="unknown",
        confidence_score=0.44,
        extra_metadata={"platform": "ms_teams"},
    )

    assert session.added is log
    assert log.traffic_class == "service_like"
    assert log.extra_metadata["traffic_class"] == "service_like"
    assert classify_traffic_class(intent="unknown", input_text="Anh check giúp em ticket VPN đang lỗi nhé") == "service_like"


@pytest.mark.asyncio
async def test_interaction_service_respects_explicit_traffic_class():
    session = _FakeSession()
    service = InteractionService(session)  # type: ignore[arg-type]

    log = await service.log_interaction(
        request_id="req-2",
        thread_id="thread-1",
        user_id="user-1",
        input_text="1-2 tháng nữa kỉ niệm lại về thôi bạn",
        output_text="",
        intent="unknown",
        confidence_score=0.45,
        extra_metadata={"traffic_class": "casual_unknown"},
    )

    assert log.traffic_class == "casual_unknown"
    assert log.extra_metadata["traffic_class"] == "casual_unknown"
