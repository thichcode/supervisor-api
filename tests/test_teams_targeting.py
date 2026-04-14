from src.core.teams_targeting import TeamsTargetResolver, TargetType, extract_teams_signal


def test_teams_explicit_mention_to_thuong_wins():
    resolver = TeamsTargetResolver()
    signal = extract_teams_signal({
        "conversation_type": "groupChat",
        "mention_targets": ["Thuong"],
    })

    decision = resolver.resolve(
        current_text="@Thuong xem giúp em",
        signal=signal,
        history_texts=["older message"],
    )

    assert decision.target == TargetType.THUONG
    assert decision.confidence == 1.0


def test_teams_reply_target_inherits_workflow_bot():
    resolver = TeamsTargetResolver()
    signal = extract_teams_signal({
        "conversation_type": "channel",
        "reply_target": "workflow_bot",
    })

    decision = resolver.resolve(
        current_text="cái này xử lý sao?",
        signal=signal,
        history_texts=["approval ticket is pending"],
    )

    assert decision.target == TargetType.WORKFLOW_BOT
    assert decision.confidence >= 0.9


def test_teams_personal_chat_defaults_to_thuong():
    resolver = TeamsTargetResolver()
    signal = extract_teams_signal({"conversation_type": "personal"})

    decision = resolver.resolve(
        current_text="xin chào",
        signal=signal,
        history_texts=[],
    )

    assert decision.target == TargetType.THUONG
    assert decision.confidence >= 0.7


def test_teams_sender_bot_is_ignored():
    resolver = TeamsTargetResolver()
    signal = extract_teams_signal({"sender_is_bot": True})

    decision = resolver.resolve(
        current_text="bot message",
        signal=signal,
        history_texts=[],
    )

    assert decision.target == TargetType.IGNORE
    assert decision.confidence == 0.0
