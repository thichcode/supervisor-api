from src.core.thread_targeting import GroupChatTargetResolver, TargetType


def test_resolver_targets_thuong_from_history_and_current_message():
    resolver = GroupChatTargetResolver()
    decision = resolver.resolve(
        current_text="Thuong ơi xem giúp em",
        history_texts=["A: ping Thuong nhé"],
        group_chat=True,
    )

    assert decision.target == TargetType.THUONG
    assert decision.confidence >= 0.7


def test_resolver_targets_workflow_bot_for_approval_context():
    resolver = GroupChatTargetResolver()
    decision = resolver.resolve(
        current_text="cái này xử lý sao?",
        history_texts=["workflow bot đang approve ticket"],
        group_chat=True,
    )

    assert decision.target == TargetType.WORKFLOW_BOT
    assert decision.confidence >= 0.7


def test_resolver_ignores_non_group_chat():
    resolver = GroupChatTargetResolver()
    decision = resolver.resolve(
        current_text="hello",
        history_texts=["Thuong"],
        group_chat=False,
    )

    assert decision.target == TargetType.IGNORE
    assert decision.confidence == 0.0


def test_resolver_marks_unclear_when_context_is_weak_but_present():
    resolver = GroupChatTargetResolver(min_confidence=0.2)
    decision = resolver.resolve(
        current_text="cái này sao nhỉ?",
        history_texts=["some previous message about a request"],
        group_chat=True,
    )

    assert decision.target in {TargetType.UNCLEAR, TargetType.IGNORE, TargetType.WORKFLOW_BOT}
