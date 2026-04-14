from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from src.core.metrics import get_metrics, metrics
from src.db import async_session

router = APIRouter(tags=["monitoring"])


@router.get("/metrics/dashboard")
async def dashboard_metrics():
    from src.core.approval import approval_service

    stats = {
        "timestamp": datetime.now().isoformat(),
        "overview": {},
        "performance": {},
        "ai_quality": {},
        "user_satisfaction": {},
        "approvals": {},
    }

    try:
        all_approvals = await approval_service.get_all_approvals()
        pending_count = sum(1 for a in all_approvals if a.status == "pending")
        approved_count = sum(1 for a in all_approvals if a.status == "approved")
        rejected_count = sum(1 for a in all_approvals if a.status == "rejected")
        confidences = [a.confidence for a in all_approvals if a.confidence]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        auto_send = sum(1 for a in all_approvals if a.confidence >= 0.9)
        need_approval = sum(1 for a in all_approvals if a.confidence < 0.9)
        votes_agree = sum(1 for a in all_approvals if a.vote == "agree")
        votes_change = sum(1 for a in all_approvals if a.vote == "change")
        votes_skip = sum(1 for a in all_approvals if a.vote == "skip")
        total_voted = votes_agree + votes_change + votes_skip

        stats["overview"] = {
            "total_approvals": len(all_approvals),
            "auto_sent": auto_send,
            "need_manual_review": need_approval,
            "auto_send_rate": round(auto_send / len(all_approvals) * 100, 1) if all_approvals else 0,
        }
        stats["approvals"] = {
            "pending": pending_count,
            "approved": approved_count,
            "rejected": rejected_count,
            "approve_rate": round(approved_count / (approved_count + rejected_count) * 100, 1) if (approved_count + rejected_count) > 0 else 0,
        }
        stats["ai_quality"] = {
            "avg_confidence": round(avg_confidence * 100, 1),
            "high_confidence_count": sum(1 for c in confidences if c >= 0.9),
            "low_confidence_count": sum(1 for c in confidences if c < 0.9),
            "auto_send_count": auto_send,
            "approval_needed_count": need_approval,
        }
        stats["user_satisfaction"] = {
            "total_votes": total_voted,
            "agree": votes_agree,
            "change": votes_change,
            "skip": votes_skip,
            "satisfaction_rate": round(votes_agree / total_voted * 100, 1) if total_voted > 0 else 0,
        }
        stats["performance"] = {
            "total_approvals": len(all_approvals),
            "avg_processing_time_sec": "N/A",
        }
    except Exception as e:
        stats["error"] = str(e)

    return stats


@router.get("/metrics/dashboard/html")
async def dashboard_html():
    from src.core.approval import approval_service

    try:
        all_approvals = await approval_service.get_all_approvals()
        pending = sum(1 for a in all_approvals if a.status == "pending")
        approved = sum(1 for a in all_approvals if a.status == "approved")
        rejected = sum(1 for a in all_approvals if a.status == "rejected")
        confidences = [a.confidence for a in all_approvals if a.confidence]
        avg_conf = sum(confidences) / len(confidences) * 100 if confidences else 0
        auto_send = sum(1 for a in all_approvals if a.confidence >= 0.9)
        votes_agree = sum(1 for a in all_approvals if a.vote == "agree")
        votes_change = sum(1 for a in all_approvals if a.vote == "change")
        votes_skip = sum(1 for a in all_approvals if a.vote == "skip")
        total_votes = votes_agree + votes_change + votes_skip
        sat_rate = round(votes_agree / total_votes * 100, 1) if total_votes > 0 else 0
        approve_rate = round(approved / (approved + rejected) * 100, 1) if (approved + rejected) > 0 else 0
    except Exception:
        pending = approved = rejected = 0
        avg_conf = auto_send = 0
        votes_agree = votes_change = votes_skip = total_votes = sat_rate = approve_rate = 0

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
        header {{ text-align: center; margin-bottom: 30px; color: #fff; }}
        header h1 {{ font-size: 2.5rem; margin-bottom: 10px; text-shadow: 0 0 20px rgba(0,255,255,0.3); }}
        header p {{ color: #aaa; font-size: 1rem; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .stat-card {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 20px; padding: 25px; border: 1px solid rgba(255,255,255,0.1); transition: transform 0.3s, box-shadow 0.3s; }}
        .stat-card:hover {{ transform: translateY(-5px); box-shadow: 0 20px 40px rgba(0,0,0,0.3); }}
        .stat-card h3 {{ color: #aaa; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
        .stat-card .value {{ font-size: 2.5rem; font-weight: bold; }}
        .stat-card .sub {{ color: #888; font-size: 0.85rem; margin-top: 5px; }}
        .stat-card.green .value {{ color: #00ff88; }}
        .stat-card.yellow .value {{ color: #ffd700; }}
        .stat-card.red .value {{ color: #ff6b6b; }}
        .stat-card.blue .value {{ color: #00d4ff; }}
        .stat-card.purple .value {{ color: #a855f7; }}
        .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 25px; }}
        .chart-card {{ background: rgba(255,255,255,0.05); backdrop-filter: blur(10px); border-radius: 20px; padding: 25px; border: 1px solid rgba(255,255,255,0.1); }}
        .chart-card h3 {{ color: #fff; font-size: 1.1rem; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        .satisfaction-bar {{ display: flex; height: 40px; border-radius: 20px; overflow: hidden; margin: 15px 0; }}
        .satisfaction-bar .agree {{ background: #00ff88; display: flex; align-items: center; justify-content: center; color: #000; font-weight: bold; }}
        .satisfaction-bar .change {{ background: #ffd700; display: flex; align-items: center; justify-content: center; color: #000; font-weight: bold; }}
        .satisfaction-bar .skip {{ background: #666; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: bold; }}
        .confidence-bar {{ height: 30px; background: rgba(255,255,255,0.1); border-radius: 15px; overflow: hidden; position: relative; margin: 15px 0; }}
        .confidence-bar .fill {{ height: 100%; background: linear-gradient(90deg, #ff6b6b, #ffd700, #00ff88); border-radius: 15px; transition: width 1s; }}
        .confidence-bar .marker {{ position: absolute; left: 90%; top: 0; height: 100%; width: 2px; background: #fff; }}
        .metric-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        .metric-row:last-child {{ border-bottom: none; }}
        .metric-row .label {{ color: #aaa; }}
        .metric-row .val {{ color: #fff; font-weight: bold; }}
        @media (max-width: 768px) {{ .charts-grid {{ grid-template-columns: 1fr; }} .stat-card .value {{ font-size: 2rem; }} }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Supervisor Analytics Dashboard</h1>
            <p>Cập nhật: {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}</p>
        </header>
        <div class="stats-grid">
            <div class="stat-card green"><h3>📨 Tổng Requests</h3><div class="value">{len(all_approvals) if 'all_approvals' in dir() else 0}</div><div class="sub">Tất cả approval requests</div></div>
            <div class="stat-card blue"><h3>✅ Auto Send</h3><div class="value">{round(auto_send / len(all_approvals) * 100, 1) if 'all_approvals' in dir() and all_approvals else 0}%</div><div class="sub">Gửi tự động (confidence ≥ 90%)</div></div>
            <div class="stat-card purple"><h3>🤖 AI Confidence</h3><div class="value">{round(avg_conf, 1)}%</div><div class="sub">Trung bình</div></div>
            <div class="stat-card yellow"><h3>⭐ User Satisfaction</h3><div class="value">{sat_rate}%</div><div class="sub">{total_votes} votes</div></div>
        </div>
        <div class="charts-grid">
            <div class="chart-card"><h3>📋 Approval Status</h3><canvas id="approvalChart"></canvas></div>
            <div class="chart-card">
                <h3>📈 AI Quality Metrics</h3>
                <div class="metric-row"><span class="label">High Confidence (≥90%)</span><span class="val" style="color:#00ff88">{sum(1 for c in confidences if c >= 0.9) if 'confidences' in dir() else 0}</span></div>
                <div class="metric-row"><span class="label">Low Confidence (<90%)</span><span class="val" style="color:#ff6b6b">{sum(1 for c in confidences if c < 0.9) if 'confidences' in dir() else 0}</span></div>
                <div class="metric-row"><span class="label">Approve Rate</span><span class="val" style="color:#00d4ff">{approve_rate}%</span></div>
                <div class="metric-row"><span class="label">Reject Rate</span><span class="val" style="color:#ff6b6b">{100 - approve_rate}%</span></div>
                <div class="confidence-bar"><div class="fill" style="width: {avg_conf}%"></div><div class="marker"></div></div>
                <p style="color:#888;font-size:0.8rem">Vertical line = 90% threshold</p>
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
    from src.db.models import Alert

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
    from src.db.models import Alert

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
    from src.db.models import Alert

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
    from src.db.models import Alert

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
