# Supervisor KB Draft Systemd Install

This directory contains a daily systemd worker that:

1. reads service-like KB misses from `interaction_logs`
2. finds the top miss patterns
3. performs web search via the built-in DuckDuckGo helper
4. asks the LLM to write a draft KB in Vietnamese
5. stores the draft in `knowledge_candidates`
6. notifies the configured Telegram approval chat(s) for approve/revise

## Files

- `supervisor-kb-draft.service`
- `supervisor-kb-draft.timer`

## Assumptions

- repo is deployed at `/opt/supervisor-api`
- environment file is available at `/etc/supervisor-api/supervisor.env`
- `telegram_bot_token` and `telegram_approval_chat_ids` are configured if you want Telegram notifications

## Install

```bash
sudo cp deploy/systemd/supervisor-kb-draft.service /etc/systemd/system/
sudo cp deploy/systemd/supervisor-kb-draft.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now supervisor-kb-draft.timer
sudo systemctl list-timers | grep supervisor-kb-draft
```

## Manual run

```bash
cd /opt/supervisor-api
python3 scripts/daily_kb_draft_worker.py --days 30 --top-n 5 --min-count 2 --json
```

## Notes

- If you want to suppress Telegram notifications for a manual run, add `--no-send-telegram`.
- The worker is intentionally conservative: it only drafts from `traffic_class = service_like` and `kb_hit_count = 0` rows.
- If you later want interactive approve/revise buttons, add a small Telegram callback flow on top of the generated `knowledge_candidates` rows.
