from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select

from src.core.metrics import get_metrics, metrics
from src.core.traffic_classification import classify_traffic_class
from src.db import async_session
from src.db.models import ApprovalRequestRecord, Alert, InteractionLog

router = APIRouter(tags=["monitoring"])


def _classify_traffic(row) -> tuple[str, str]:
    """Lightweight traffic classifier: service-like vs casual/unknown."""
    extra_metadata = getattr(row, "extra_metadata", None) or {}
    traffic_class = getattr(row, "traffic_class", None) or extra_metadata.get("traffic_class")
    if traffic_class in {"service_like", "casual_unknown"}:
        return traffic_class, "stored_traffic_class"

    traffic_class = classify_traffic_class(
        intent=getattr(row, "intent", None),
        input_text=getattr(row, "input_text", None),
        output_text=getattr(row, "output_text", None),
        extra_metadata=extra_metadata,
    )
    return traffic_class, "heuristic_fallback"


def _summarize_interactions(interactions: list) -> dict:
    total_interactions = len(interactions)
    kb_hit_count = sum(1 for row in interactions if (row.kb_hit_count or 0) > 0)
    approval_required_count = sum(1 for row in interactions if row.approval_required)
    needs_review_count = sum(1 for row in interactions if row.outcome_status == "needs_review")
    skipped_count = sum(1 for row in interactions if row.outcome_status == "skipped")
    completed_count = sum(1 for row in interactions if row.outcome_status == "completed")
    clarification_count = sum(1 for row in interactions if row.outcome_status == "needs_clarification")
    confidences = [row.confidence_score for row in interactions if row.confidence_score is not None]
    latencies = [row.processing_latency_ms for row in interactions if row.processing_latency_ms is not None]
    intents = Counter((row.intent or "unknown") for row in interactions)
    top_intents = intents.most_common(5)

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0
    kb_hit_rate = round(kb_hit_count / total_interactions * 100, 1) if total_interactions else 0
    approval_required_rate = round(approval_required_count / total_interactions * 100, 1) if total_interactions else 0
    skip_rate = round(skipped_count / total_interactions * 100, 1) if total_interactions else 0
    auto_send_rate = round(completed_count / total_interactions * 100, 1) if total_interactions else 0
    review_rate = round(needs_review_count / total_interactions * 100, 1) if total_interactions else 0
    clarify_rate = round(clarification_count / total_interactions * 100, 1) if total_interactions else 0

    return {
        "total_interactions": total_interactions,
        "kb_hits": kb_hit_count,
        "auto_sent": completed_count,
        "need_manual_review": approval_required_count,
        "skipped": skipped_count,
        "needs_review": needs_review_count,
        "clarifications": clarification_count,
        "kb_hit_rate": kb_hit_rate,
        "approval_required_rate": approval_required_rate,
        "skip_rate": skip_rate,
        "auto_send_rate": auto_send_rate,
        "needs_review_rate": review_rate,
        "clarification_rate": clarify_rate,
        "avg_confidence": round(avg_confidence * 100, 1),
        "avg_latency_ms": round(avg_latency_ms, 1),
        "avg_latency_sec": round(avg_latency_ms / 1000, 2) if avg_latency_ms else 0,
        "high_confidence_count": sum(1 for c in confidences if c >= 0.9),
        "low_confidence_count": sum(1 for c in confidences if c < 0.5),
        "top_intents": [{"intent": intent, "count": count} for intent, count in top_intents],
    }


async def _load_dashboard_snapshot(days: int = 7) -> dict:
    """Collect efficiency metrics for the dashboard and boss report."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "window_days": days,
        "overview": {},
        "performance": {},
        "ai_quality": {},
        "user_satisfaction": {},
        "approvals": {},
        "efficiency": {},
        "boss_summary": [],
        "recommendations": [],
        "top_intents": [],
        "traffic_breakdown": {},
        "raw_overview": {},
        "service_overview": {},
    }

    try:
        async with async_session() as session:
            interaction_result = await session.execute(
                select(InteractionLog).where(InteractionLog.created_at >= cutoff)
            )
            approval_result = await session.execute(
                select(ApprovalRequestRecord).where(ApprovalRequestRecord.created_at >= cutoff)
            )
            interactions = interaction_result.scalars().all()
            approvals = approval_result.scalars().all()

        raw_overview = _summarize_interactions(interactions)
        service_interactions = []
        casual_interactions = []
        service_signal_reasons = Counter()
        for row in interactions:
            traffic_class, traffic_reason = _classify_traffic(row)
            if traffic_class == "service_like":
                service_interactions.append(row)
                service_signal_reasons[traffic_reason] += 1
            else:
                casual_interactions.append(row)

        service_overview = _summarize_interactions(service_interactions)
        service_total = service_overview["total_interactions"]
        raw_total = raw_overview["total_interactions"]
        casual_total = len(casual_interactions)
        service_like_rate = round(service_total / raw_total * 100, 1) if raw_total else 0
        casual_unknown_rate = round(casual_total / raw_total * 100, 1) if raw_total else 0

        pending_count = sum(1 for a in approvals if getattr(a, "status", "") == "pending")
        approved_count = sum(1 for a in approvals if getattr(a, "status", "") == "approved")
        rejected_count = sum(1 for a in approvals if getattr(a, "status", "") == "rejected")
        approval_confidences = [a.confidence for a in approvals if getattr(a, "confidence", None) is not None]
        votes_agree = sum(1 for a in approvals if getattr(a, "vote", None) == "agree")
        votes_change = sum(1 for a in approvals if getattr(a, "vote", None) == "change")
        votes_skip = sum(1 for a in approvals if getattr(a, "vote", None) == "skip")
        total_voted = votes_agree + votes_change + votes_skip

        approve_rate = round(approved_count / (approved_count + rejected_count) * 100, 1) if (approved_count + rejected_count) else 0
        satisfaction_rate = round(votes_agree / total_voted * 100, 1) if total_voted else 0
        approval_avg_conf = round(sum(approval_confidences) / len(approval_confidences) * 100, 1) if approval_confidences else 0

        recommendations = []
        if raw_total and service_total == 0:
            recommendations.append("Không có service-like traffic trong window: report đang chỉ tính traffic IT service thật.")
        if service_overview["kb_hit_rate"] < 40 and service_total > 0:
            recommendations.append("KB hit rate thấp: nên bổ sung / chỉnh KB và truy vấn search.")
        if service_overview["approval_required_rate"] > 30:
            recommendations.append("Approve rate cao: xem lại calibration confidence hoặc chất lượng QA.")
        if service_overview["avg_latency_ms"] and service_overview["avg_latency_ms"] > 5000:
            recommendations.append("Latency cao: cân nhắc tối ưu model hoặc giảm số bước subagent.")
        if service_overview["clarifications"]:
            recommendations.append(f"Có {service_overview['clarifications']} lượt cần làm rõ: nên cải thiện clarification flow.")
        if raw_total and casual_total > service_total:
            recommendations.append(
                f"Traffic casual/unknown chiếm {casual_unknown_rate}%: cân nhắc lọc chat đời thường khỏi boss report."
            )
        if not recommendations:
            recommendations.append("Hiệu quả đang ổn, tiếp tục theo dõi theo tuần.")

        boss_summary = [
            f"Trong {days} ngày gần nhất có {raw_total} interaction(s) raw, trong đó {service_total} service-like và {casual_total} casual/unknown.",
            f"Service-like rate: {service_like_rate}% | Casual/unknown rate: {casual_unknown_rate}%.",
            f"KB hit rate (service-like): {service_overview['kb_hit_rate']}% | Auto-send: {service_overview['auto_send_rate']}% | Skip: {service_overview['skip_rate']}% | Needs review: {service_overview['needs_review_rate']}%.",
            f"Average confidence (service-like): {service_overview['avg_confidence']}% | Average latency: {service_overview['avg_latency_ms']} ms.",
            f"Approval queue: pending={pending_count}, approved={approved_count}, rejected={rejected_count}, approve_rate={approve_rate}%.",
        ]

        snapshot.update(
            {
                "overview": {
                    **service_overview,
                },
                "raw_overview": raw_overview,
                "service_overview": service_overview,
                "traffic_breakdown": {
                    "raw_total": raw_total,
                    "service_like": service_total,
                    "casual_unknown": casual_total,
                    "service_like_rate": service_like_rate,
                    "casual_unknown_rate": casual_unknown_rate,
                    "service_signal_reasons": dict(service_signal_reasons),
                },
                "approvals": {
                    "pending": pending_count,
                    "approved": approved_count,
                    "rejected": rejected_count,
                    "approve_rate": approve_rate,
                    "average_confidence": approval_avg_conf,
                },
                "ai_quality": {
                    "avg_confidence": service_overview["avg_confidence"],
                    "high_confidence_count": service_overview["high_confidence_count"],
                    "low_confidence_count": service_overview["low_confidence_count"],
                    "auto_send_count": service_overview["auto_sent"],
                    "approval_needed_count": service_overview["need_manual_review"],
                    "needs_review_count": service_overview["needs_review"],
                },
                "user_satisfaction": {
                    "total_votes": total_voted,
                    "agree": votes_agree,
                    "change": votes_change,
                    "skip": votes_skip,
                    "satisfaction_rate": satisfaction_rate,
                },
                "performance": {
                    "total_interactions": service_overview["total_interactions"],
                    "avg_processing_time_ms": service_overview["avg_latency_ms"],
                    "avg_processing_time_sec": service_overview["avg_latency_sec"],
                },
                "efficiency": {
                    "kb_hit_rate": service_overview["kb_hit_rate"],
                    "approval_required_rate": service_overview["approval_required_rate"],
                    "skip_rate": service_overview["skip_rate"],
                    "auto_send_rate": service_overview["auto_send_rate"],
                    "needs_review_rate": service_overview["needs_review_rate"],
                    "clarification_rate": service_overview["clarification_rate"],
                    "avg_confidence": service_overview["avg_confidence"],
                    "avg_latency_ms": service_overview["avg_latency_ms"],
                },
                "top_intents": service_overview["top_intents"],
                "boss_summary": boss_summary,
                "recommendations": recommendations,
            }
        )
    except Exception as e:
        snapshot["error"] = str(e)

    return snapshot


@router.get("/metrics/dashboard")
async def dashboard_metrics(days: int = Query(default=7, ge=1, le=90)):
    return await _load_dashboard_snapshot(days=days)


@router.get("/metrics/dashboard/boss-report")
async def boss_report(days: int = Query(default=7, ge=1, le=90)):
    snapshot = await _load_dashboard_snapshot(days=days)
    lines = [
        "Supervisor boss report",
        f"Window: last {snapshot.get('window_days', days)} day(s)",
        "",
    ]
    lines.extend(snapshot.get("boss_summary", []))
    lines.append("")
    lines.append("Key recommendations:")
    for item in snapshot.get("recommendations", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Top intents:")
    for item in snapshot.get("top_intents", [])[:5]:
        lines.append(f"- {item.get('intent')}: {item.get('count')}")
    if snapshot.get("error"):
        lines.append("")
        lines.append(f"Error: {snapshot['error']}")
    return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8")


@router.get("/metrics/dashboard/html")
async def dashboard_html(days: int = Query(default=7, ge=1, le=90)):
    snapshot = await _load_dashboard_snapshot(days=days)
    overview = snapshot.get("overview", {})
    approvals = snapshot.get("approvals", {})
    ai_quality = snapshot.get("ai_quality", {})
    user_satisfaction = snapshot.get("user_satisfaction", {})
    efficiency = snapshot.get("efficiency", {})
    boss_summary = snapshot.get("boss_summary", [])
    recommendations = snapshot.get("recommendations", [])
    top_intents = snapshot.get("top_intents", [])

    pending = approvals.get("pending", 0)
    approved = approvals.get("approved", 0)
    rejected = approvals.get("rejected", 0)
    avg_conf = ai_quality.get("avg_confidence", 0)
    auto_send = overview.get("auto_sent", 0)
    total_interactions = overview.get("total_interactions", 0)
    sat_rate = user_satisfaction.get("satisfaction_rate", 0)
    total_votes = user_satisfaction.get("total_votes", 0)
    votes_agree = user_satisfaction.get("agree", 0)
    votes_change = user_satisfaction.get("change", 0)
    votes_skip = user_satisfaction.get("skip", 0)
    approve_rate = approvals.get("approve_rate", 0)
    kb_hit_rate = efficiency.get("kb_hit_rate", 0)
    approval_required_rate = efficiency.get("approval_required_rate", 0)
    skip_rate = efficiency.get("skip_rate", 0)
    review_rate = efficiency.get("needs_review_rate", 0)
    avg_latency_ms = efficiency.get("avg_latency_ms", 0)

    intent_rows = "".join(
        f'<div class="metric-row"><span class="label">{item.get("intent")}</span><span class="val">{item.get("count")}</span></div>'
        for item in top_intents
    ) or '<div class="metric-row"><span class="label">N/A</span><span class="val">0</span></div>'

    summary_html = "".join(f"<li>{line}</li>" for line in boss_summary) or "<li>Không có dữ liệu.</li>"
    recommendation_html = "".join(f"<li>{line}</li>" for line in recommendations) or "<li>Không có khuyến nghị.</li>"

    html = f"""
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Supervisor Analytics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{ text-align: center; margin-bottom: 24px; color: #fff; }}
        header h1 {{ font-size: 2.5rem; margin-bottom: 10px; text-shadow: 0 0 20px rgba(0,255,255,0.3); }}
        header p {{ color: #aaa; font-size: 1rem; }}
        .summary-panel {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12); border-radius: 18px; padding: 18px 22px; color: #fff; margin-bottom: 22px; }}
        .summary-panel h2 {{ font-size: 1.1rem; margin-bottom: 10px; color: #00d4ff; }}
        .summary-panel ul {{ margin-left: 18px; color: #ddd; line-height: 1.7; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 18px; margin-bottom: 22px; }}
        .stat-card {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 20px; padding: 22px; border: 1px solid rgba(255,255,255,0.1); transition: transform 0.3s, box-shadow 0.3s; }}
        .stat-card:hover {{ transform: translateY(-5px); box-shadow: 0 20px 40px rgba(0,0,0,0.3); }}
        .stat-card h3 {{ color: #aaa; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
        .stat-card .value {{ font-size: 2.2rem; font-weight: bold; }}
        .stat-card .sub {{ color: #888; font-size: 0.82rem; margin-top: 5px; }}
        .stat-card.green .value {{ color: #00ff88; }}
        .stat-card.yellow .value {{ color: #ffd700; }}
        .stat-card.red .value {{ color: #ff6b6b; }}
        .stat-card.blue .value {{ color: #00d4ff; }}
        .stat-card.purple .value {{ color: #a855f7; }}
        .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 20px; }}
        .chart-card {{ background: rgba(255,255,255,0.05); backdrop-filter: blur(10px); border-radius: 20px; padding: 22px; border: 1px solid rgba(255,255,255,0.1); }}
        .chart-card h3 {{ color: #fff; font-size: 1.05rem; margin-bottom: 18px; padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        .metric-row {{ display: flex; justify-content: space-between; padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        .metric-row:last-child {{ border-bottom: none; }}
        .metric-row .label {{ color: #aaa; }}
        .metric-row .val {{ color: #fff; font-weight: bold; }}
        .satisfaction-bar {{ display: flex; height: 38px; border-radius: 18px; overflow: hidden; margin: 14px 0; }}
        .satisfaction-bar .agree {{ background: #00ff88; display: flex; align-items: center; justify-content: center; color: #000; font-weight: bold; }}
        .satisfaction-bar .change {{ background: #ffd700; display: flex; align-items: center; justify-content: center; color: #000; font-weight: bold; }}
        .satisfaction-bar .skip {{ background: #666; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: bold; }}
        .confidence-bar {{ height: 28px; background: rgba(255,255,255,0.1); border-radius: 14px; overflow: hidden; position: relative; margin: 14px 0; }}
        .confidence-bar .fill {{ height: 100%; background: linear-gradient(90deg, #ff6b6b, #ffd700, #00ff88); border-radius: 14px; }}
        .confidence-bar .marker {{ position: absolute; left: 90%; top: 0; height: 100%; width: 2px; background: #fff; }}
        .tag-list {{ list-style: none; color: #ddd; line-height: 1.7; }}
        .tag-list li {{ margin-bottom: 6px; }}
        @media (max-width: 768px) {{ .charts-grid {{ grid-template-columns: 1fr; }} .stat-card .value {{ font-size: 1.9rem; }} }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Supervisor Analytics Dashboard</h1>
            <p>Hiệu quả vận hành | Cập nhật: {datetime.now().strftime('%H:%M:%S %d/%m/%Y')} | Window: {snapshot.get('window_days', days)} ngày</p>
        </header>

        <div class="summary-panel">
            <h2>📣 Executive Summary</h2>
            <ul class="tag-list">
                {summary_html}
            </ul>
        </div>

        <div class="stats-grid">
            <div class="stat-card green"><h3>📨 Total Interactions</h3><div class="value">{total_interactions}</div><div class="sub">Tổng requests trong window</div></div>
            <div class="stat-card blue"><h3>✅ Auto Send Rate</h3><div class="value">{overview.get('auto_send_rate', 0)}%</div><div class="sub">Đã gửi tự động</div></div>
            <div class="stat-card purple"><h3>🤖 Avg Confidence</h3><div class="value">{avg_conf}%</div><div class="sub">Confidence trung bình</div></div>
            <div class="stat-card yellow"><h3>🔎 KB Hit Rate</h3><div class="value">{kb_hit_rate}%</div><div class="sub">Có evidence KB</div></div>
            <div class="stat-card red"><h3>📝 Approval Rate</h3><div class="value">{approval_required_rate}%</div><div class="sub">Cần duyệt trước khi gửi</div></div>
            <div class="stat-card blue"><h3>⚡ Avg Latency</h3><div class="value">{avg_latency_ms:.0f}ms</div><div class="sub">Thời gian xử lý trung bình</div></div>
        </div>

        <div class="charts-grid">
            <div class="chart-card"><h3>📋 Approval Status</h3><canvas id="approvalChart"></canvas></div>
            <div class="chart-card">
                <h3>📈 Efficiency Metrics</h3>
                <div class="metric-row"><span class="label">KB Hit Rate</span><span class="val">{kb_hit_rate}%</span></div>
                <div class="metric-row"><span class="label">Approval Required</span><span class="val">{approval_required_rate}%</span></div>
                <div class="metric-row"><span class="label">Auto Send</span><span class="val">{overview.get('auto_send_rate', 0)}%</span></div>
                <div class="metric-row"><span class="label">Skip Rate</span><span class="val">{skip_rate}%</span></div>
                <div class="metric-row"><span class="label">Needs Review Rate</span><span class="val">{review_rate}%</span></div>
                <div class="confidence-bar"><div class="fill" style="width: {avg_conf}%"></div><div class="marker"></div></div>
                <p style="color:#888;font-size:0.8rem">Marker = 90% threshold</p>
            </div>
            <div class="chart-card">
                <h3>👤 User Satisfaction</h3>
                <div class="satisfaction-bar">
                    <div class="agree" style="width: {votes_agree / max(total_votes,1) * 100}%">{votes_agree}</div>
                    <div class="change" style="width: {votes_change / max(total_votes,1) * 100}%">{votes_change}</div>
                    <div class="skip" style="width: {votes_skip / max(total_votes,1) * 100}%">{votes_skip}</div>
                </div>
                <div class="metric-row"><span class="label">Agree</span><span class="val">{votes_agree}</span></div>
                <div class="metric-row"><span class="label">Change Requested</span><span class="val">{votes_change}</span></div>
                <div class="metric-row"><span class="label">Skip</span><span class="val">{votes_skip}</span></div>
                <div class="metric-row"><span class="label">Satisfaction Rate</span><span class="val" style="color:#00ff88">{sat_rate}%</span></div>
            </div>
            <div class="chart-card">
                <h3>🏷️ Top Intents</h3>
                {intent_rows}
            </div>
            <div class="chart-card">
                <h3>💼 Boss Recommendations</h3>
                <ul class="tag-list">{recommendation_html}</ul>
            </div>
        </div>
    </div>
    <script>
        const ctx = document.getElementById('approvalChart').getContext('2d');
        new Chart(ctx, {{
            type: 'doughnut',
            data: {{ labels: ['Pending', 'Approved', 'Rejected'], datasets: [{{ data: [{pending}, {approved}, {rejected}], backgroundColor: ['#ffd700', '#00ff88', '#ff6b6b'], borderWidth: 0 }}] }},
            options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#fff' }} }} }} }}
        }});
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html, media_type="text/html")


@router.post("/alerts")
async def create_alert(alert_type: str, severity: str, title: str, message: str, metadata: dict = None):
    alert_id = f"alert-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    async with async_session() as session:
        alert = Alert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            metadata=metadata or {},
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)

    metrics.record_counter("alerts_created", 1, {"type": alert_type, "severity": severity})
    return {"status": "created", "alert_id": alert_id}


@router.get("/alerts")
async def list_alerts(severity: str = None, status: str = None, limit: int = 50):
    async with async_session() as session:
        query = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
        if severity:
            query = query.where(Alert.severity == severity)
        if status:
            query = query.where(Alert.status == status)

        result = await session.execute(query)
        alerts = result.scalars().all()
        return {
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": a.title,
                    "message": a.message,
                    "status": a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in alerts
            ],
            "total": len(alerts),
        }


@router.put("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, acknowledged_by: str):
    async with async_session() as session:
        result = await session.execute(select(Alert).where(Alert.alert_id == alert_id))
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert.status = "acknowledged"
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.now()
        await session.commit()

    return {"status": "acknowledged", "alert_id": alert_id}


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    async with async_session() as session:
        result = await session.execute(select(Alert).where(Alert.alert_id == alert_id))
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        await session.delete(alert)
        await session.commit()

    return {"status": "deleted", "alert_id": alert_id}


@router.get("/metrics")
async def metrics_endpoint():
    return get_metrics()
