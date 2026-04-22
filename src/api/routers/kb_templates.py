"""KB Template Analytics Router.

Exposes template detection metrics from Prometheus registry.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from prometheus_client import REGISTRY

router = APIRouter(prefix="/metrics/kb-templates", tags=["monitoring", "kb-templates"])

# Predefined template IDs and labels (synced with kb_templates.py)
ALL_TEMPLATE_DEFINITIONS: Dict[str, str] = {
    "password_reset": "Password Reset",
    "vpn_access": "VPN Access",
    "sharepoint_onedrive": "SharePoint/OneDrive",
    "outlook_mail": "Outlook/Mail",
    "backup_restore": "Backup/Restore",
    "excel_csv": "Excel/CSV",
    "jira_confluence": "Jira/Confluence",
    "policy_request": "Policy Request",
}


def _parse_kb_template_metrics() -> dict:
    """Parse KB_TEMPLATES counter from Prometheus registry."""
    template_counts: Counter = Counter()
    total_detected = 0
    total_not_detected = 0

    try:
        for metric_family in REGISTRY.collect():
            if metric_family.name != "supervisor_kb_templates_total":
                continue
            for sample in metric_family.samples:
                # sample: (name, labels, value)
                labels = sample[1]
                value = sample[2]
                template_id = labels.get("template_id", "unknown")
                outcome = labels.get("outcome", "unknown")

                if outcome == "detected":
                    total_detected += value
                elif outcome == "not_detected":
                    total_not_detected += value

                key = f"{template_id}|{labels.get('search_type', 'all')}"
                template_counts[key] = value
    except Exception:
        pass

    return {
        "template_counts": dict(template_counts),
        "total_detected": total_detected,
        "total_not_detected": total_not_detected,
        "all_template_ids": ALL_TEMPLATE_DEFINITIONS,
    }


def _summarize_templates(metrics_data: dict) -> dict:
    """Build summary from template metrics."""
    template_counts = metrics_data.get("template_counts", {})
    all_templates = metrics_data.get("all_template_ids", {})
    total_detected = metrics_data.get("total_detected", 0)
    total_not_detected = metrics_data.get("total_not_detected", 0)

    # Aggregate by template_id
    by_template: dict = {}
    for key, count in template_counts.items():
        parts = key.split("|")
        template_id = parts[0]
        search_type = parts[1] if len(parts) > 1 else "all"

        if template_id not in by_template:
            by_template[template_id] = {
                "template_id": template_id,
                "label": all_templates.get(template_id, template_id),
                "total_count": 0,
                "by_search_type": Counter(),
            }
        by_template[template_id]["total_count"] += count
        by_template[template_id]["by_search_type"][search_type] += count

    # Sort by count
    sorted_templates = sorted(
        by_template.values(),
        key=lambda x: x["total_count"],
        reverse=True
    )

    # Convert Counter to dict for JSON
    for t in sorted_templates:
        t["by_search_type"] = dict(t["by_search_type"])

    total = total_detected + total_not_detected
    detection_rate = round(total_detected / total * 100, 1) if total else 0

    return {
        "total_searches": total,
        "total_detected": total_detected,
        "total_not_detected": total_not_detected,
        "detection_rate": detection_rate,
        "templates": sorted_templates,
    }


@router.get("")
async def kb_templates_summary(days: int = Query(default=7, ge=1, le=90)):
    """JSON endpoint for KB template analytics."""
    raw = _parse_kb_template_metrics()
    summary = _summarize_templates(raw)

    return {
        "timestamp": datetime.now().isoformat(),
        "window_days": days,
        "summary": summary,
        "raw_metrics": raw,
    }


@router.get("/report")
async def kb_templates_report(days: int = Query(default=7, ge=1, le=90)):
    """Plain-text report for KB template analytics."""
    raw = _parse_kb_template_metrics()
    summary = _summarize_templates(raw)

    lines = [
        "KB Template Analytics Report",
        f"Window: last {days} day(s)",
        "",
        f"Total KB searches: {summary['total_searches']}",
        f"Template detected: {summary['total_detected']} ({summary['detection_rate']}%)",
        f"Template not detected: {summary['total_not_detected']}",
        "",
        "Template breakdown:",
    ]

    if summary["templates"]:
        for t in summary["templates"]:
            label = t["label"]
            count = t["total_count"]
            by_type = ", ".join(
                f"{st}={c}" for st, c in sorted(t["by_search_type"].items())
            )
            lines.append(f"  - {label} ({t['template_id']}): {count} [{by_type}]")
    else:
        lines.append("  (No template data yet)")

    # Gaps analysis
    all_defined = raw.get("all_template_ids", {})
    detected_ids = {t["template_id"] for t in summary["templates"] if t["template_id"] != "none"}
    missing = set(all_defined.keys()) - detected_ids

    lines.append("")
    lines.append("Coverage gaps (defined but never detected):")
    if missing:
        for tid in sorted(missing):
            label = all_defined.get(tid, tid)
            lines.append(f"  - {label} ({tid})")
    else:
        lines.append("  (All templates have been detected at least once)")

    return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8")


@router.get("/html")
async def kb_templates_html(days: int = Query(default=7, ge=1, le=90)):
    """HTML dashboard for KB template analytics."""
    raw = _parse_kb_template_metrics()
    summary = _summarize_templates(raw)

    template_rows = ""
    if summary["templates"]:
        colors = ["#00d4ff", "#a855f7", "#00ff88", "#ffd700", "#ff6b6b", "#f97316", "#06b6d4", "#8b5cf6"]
        for i, t in enumerate(summary["templates"][:10]):
            color = colors[i % len(colors)]
            pct = round(t["total_count"] / max(summary["total_searches"], 1) * 100, 1)
            by_type_html = "<br>".join(
                f"  {st}: {c}" for st, c in sorted(t["by_search_type"].items())
            )
            template_rows += f"""
            <tr>
                <td style="color:{color};font-weight:bold">{t['label']}</td>
                <td><code>{t['template_id']}</code></td>
                <td><strong>{t['total_count']}</strong></td>
                <td>{pct}%</td>
                <td style="font-size:0.8rem;color:#aaa">{by_type_html}</td>
            </tr>
            """
    else:
        template_rows = '<tr><td colspan="5" style="color:#888;text-align:center">No template data yet</td></tr>'

    # Coverage gaps
    all_defined = raw.get("all_template_ids", {})
    detected_ids = {t["template_id"] for t in summary["templates"] if t["template_id"] != "none"}
    missing = set(all_defined.keys()) - detected_ids
    gaps_html = ""
    if missing:
        for tid in sorted(missing):
            label = all_defined.get(tid, tid)
            gaps_html += f'<span style="background:#333;padding:4px 8px;border-radius:8px;margin:4px;display:inline-block">{label}</span>'
    else:
        gaps_html = '<span style="color:#00ff88">All templates have been detected</span>'

    detection_rate = summary["detection_rate"]
    rate_color = "#00ff88" if detection_rate >= 50 else "#ffd700" if detection_rate >= 25 else "#ff6b6b"

    html = f"""
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KB Template Analytics</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #1a1a2e, #16213e); min-height: 100vh; padding: 20px; color: #fff; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        header {{ text-align: center; margin-bottom: 28px; }}
        header h1 {{ font-size: 2.2rem; margin-bottom: 8px; color: #00d4ff; }}
        header p {{ color: #aaa; }}
        .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }}
        .card {{ background: rgba(255,255,255,0.08); border-radius: 16px; padding: 20px; border: 1px solid rgba(255,255,255,0.1); }}
        .card h3 {{ color: #aaa; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
        .card .value {{ font-size: 2.2rem; font-weight: bold; color: #00d4ff; }}
        .card.green .value {{ color: #00ff88; }}
        .card.red .value {{ color: #ff6b6b; }}
        .card.yellow .value {{ color: #ffd700; }}
        table {{ width: 100%; border-collapse: collapse; background: rgba(255,255,255,0.05); border-radius: 16px; overflow: hidden; margin-bottom: 28px; }}
        th {{ background: rgba(0,212,255,0.15); padding: 14px 12px; text-align: left; color: #00d4ff; font-size: 0.85rem; text-transform: uppercase; }}
        td {{ padding: 12px; border-top: 1px solid rgba(255,255,255,0.05); }}
        tr:hover {{ background: rgba(255,255,255,0.03); }}
        .section {{ background: rgba(255,255,255,0.05); border-radius: 16px; padding: 22px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }}
        .section h2 {{ color: #fff; margin-bottom: 14px; font-size: 1.1rem; }}
        .bar {{ height: 24px; background: rgba(255,255,255,0.1); border-radius: 12px; overflow: hidden; margin: 8px 0; }}
        .bar-fill {{ height: 100%; border-radius: 12px; transition: width 0.5s ease; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📋 KB Template Analytics</h1>
            <p>Template detection breakdown | Updated: {datetime.now().strftime('%H:%M:%S %d/%m/%Y')} | Window: {days} days</p>
        </header>

        <div class="summary-cards">
            <div class="card blue"><h3>Total Searches</h3><div class="value">{summary['total_searches']}</div></div>
            <div class="card green"><h3>Detected</h3><div class="value">{summary['total_detected']}</div></div>
            <div class="card red"><h3>Not Detected</h3><div class="value">{summary['total_not_detected']}</div></div>
            <div class="card yellow"><h3>Detection Rate</h3><div class="value" style="color:{rate_color}">{detection_rate}%</div></div>
        </div>

        <div class="section">
            <h2>Template Detection Rate</h2>
            <div class="bar">
                <div class="bar-fill" style="width:{detection_rate}%;background:linear-gradient(90deg,#ff6b6b,#ffd700,#00ff88)"></div>
            </div>
        </div>

        <div class="section">
            <h2>Top Templates ({min(len(summary['templates']), 10)} shown)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Template</th>
                        <th>ID</th>
                        <th>Count</th>
                        <th>Share</th>
                        <th>By Search Type</th>
                    </tr>
                </thead>
                <tbody>
                    {template_rows}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>Coverage Gaps</h2>
            <p style="color:#888;margin-bottom:12px">Templates defined but never detected in this window:</p>
            {gaps_html}
        </div>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html, media_type="text/html")
