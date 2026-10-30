"""Portable HTML dashboard and Power BI-ready CSV exports.

Power BI Desktop is not available in every environment (and cannot be
scripted headlessly), so the same SQL views are rendered into a self-contained
HTML page for an immediate demo while the CSVs remain available for Power BI.
"""

from __future__ import annotations

import html
import json
import logging
import math
from datetime import UTC, datetime

import pandas as pd

from .config import Settings
from .storage import (
    build_run_summary,
    get_monthly_spend,
    get_rejection_summary,
    get_rejected,
    get_run_summary,
    get_vendor_spend,
    get_invoices,
)

logger = logging.getLogger(__name__)

PALETTE = ("#0F766E", "#2563EB", "#7C3AED", "#B45309", "#BE185D", "#15803D", "#0891B2", "#4F46E5")


def _money(value: float | int | None, *, cents: bool = False) -> str:
    if value is None:
        return "-"
    amount = float(value) / 100.0 if cents else float(value)
    return f"${amount:,.2f}"


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _short_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return str(value)


def _month_label(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m").strftime("%b")
    except (TypeError, ValueError):
        return str(value)


def _svg_trend(monthly: list[dict]) -> str:
    if not monthly:
        return '<div class="empty">No monthly data</div>'
    width, height = 760, 380
    left, right, top, bottom = 58, 18, 26, 46
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = [float(row["spend"] or 0) for row in monthly]
    max_value = max(values)
    if max_value <= 0:
        max_value = 1.0
    nice_max = math.ceil(max_value / 5000.0) * 5000.0 if max_value > 5000 else math.ceil(max_value / 500.0) * 500.0
    if nice_max < max_value:
        nice_max = max_value * 1.1

    if len(monthly) == 1:
        xs = [left + plot_w / 2]
    else:
        xs = [left + i * plot_w / (len(monthly) - 1) for i in range(len(monthly))]
    ys = [top + plot_h - (value / nice_max) * plot_h for value in values]

    grid = []
    for fraction in (0.0, 0.5, 1.0):
        y = top + plot_h - fraction * plot_h
        label = nice_max * fraction
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'class="grid"/>'
            f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="axis">'
            f'${label:,.0f}</text>'
        )

    area_path = " ".join(
        [
            f"M {xs[0]:.1f} {top + plot_h:.1f}",
            *[f"L {x:.1f} {y:.1f}" for x, y in zip(xs, ys)],
            f"L {xs[-1]:.1f} {top + plot_h:.1f}",
            "Z",
        ]
    )
    line_path = " ".join(
        [f"M {xs[0]:.1f} {ys[0]:.1f}", *[f"L {x:.1f} {y:.1f}" for x, y in zip(xs[1:], ys[1:])]]
    )
    points = []
    for index, (x, y, row, value) in enumerate(zip(xs, ys, monthly, values)):
        points.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="dot">'
            f"<title>{_esc(row['month'])}: {_money(value)}</title></circle>"
        )
        if index % 2 == 0 or len(monthly) <= 8:
            points.append(
                f'<text x="{x:.1f}" y="{top + plot_h + 26}" text-anchor="middle" '
                f'class="axis">{_esc(_month_label(str(row["month"])))}</text>'
            )

    return f"""
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="Monthly invoice spend">
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0F766E" stop-opacity="0.24"/>
          <stop offset="100%" stop-color="#0F766E" stop-opacity="0.01"/>
        </linearGradient>
      </defs>
      {''.join(grid)}
      <path d="{area_path}" fill="url(#trendFill)"/>
      <path d="{line_path}" fill="none" stroke="#0F766E" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
      {''.join(points)}
    </svg>
    """


def _vendor_bars(vendors: list[dict]) -> str:
    if not vendors:
        return '<div class="empty">No vendor data</div>'
    max_spend = max(float(row["spend"] or 0) for row in vendors) or 1
    rows = []
    for index, row in enumerate(vendors):
        spend = float(row["spend"] or 0)
        percent = max(2.0, 100.0 * spend / max_spend)
        color = PALETTE[index % len(PALETTE)]
        rows.append(
            f"""
            <div class="vendor-row">
              <div class="vendor-name" title="{_esc(row['vendor'])}">{_esc(row['vendor'])}</div>
              <div class="vendor-track"><div class="vendor-bar" style="width:{percent:.1f}%;background:{color}"></div></div>
              <div class="vendor-value">{_money(spend)}<span>{int(row['invoice_count'])} inv.</span></div>
            </div>
            """
        )
    return "".join(rows)


def _vendor_table(vendors: list[dict]) -> str:
    if not vendors:
        return '<tr><td colspan="4" class="empty">No vendor data</td></tr>'
    rows = []
    total = sum(float(row["spend"] or 0) for row in vendors) or 1
    for row in vendors:
        spend = float(row["spend"] or 0)
        rows.append(
            f"""
            <tr>
              <td><span class="vendor-dot"></span>{_esc(row['vendor'])}</td>
              <td class="num">{int(row['invoice_count'])}</td>
              <td class="num">{_money(spend)}</td>
              <td class="num muted">{100 * spend / total:.1f}%</td>
            </tr>
            """
        )
    return "".join(rows)


def _rejected_cards(rejected: list[dict]) -> str:
    if not rejected:
        return '<div class="empty success">No documents rejected in this run.</div>'
    cards = []
    for row in rejected:
        try:
            codes = json.loads(row.get("reason_codes_json") or "[]")
        except ValueError:
            codes = [row.get("reason_codes_json")]
        try:
            reasons = json.loads(row.get("reasons_json") or "[]")
        except ValueError:
            reasons = [row.get("reasons_json")]
        code_tags = "".join(f'<span class="tag warn">{_esc(code)}</span>' for code in codes)
        reason_text = " ".join(str(reason) for reason in reasons)
        cards.append(
            f"""
            <article class="reject-card">
              <div class="reject-head">
                <span class="reject-id">{_esc(row['document_id'])}</span>
                <span class="tag neutral">{_esc(row['stage'])}</span>
              </div>
              <div class="reject-file">{_esc(row['source_file'])}</div>
              <div class="tag-row">{code_tags}</div>
              <p>{_esc(reason_text)}</p>
            </article>
            """
        )
    return "".join(cards)


def _invoice_rows(invoices: list[dict]) -> str:
    if not invoices:
        return '<tr><td colspan="5" class="empty">No invoices</td></tr>'
    rows = []
    for row in sorted(invoices, key=lambda item: str(item["invoice_date"]), reverse=True)[:10]:
        rows.append(
            f"""
            <tr>
              <td class="mono">{_esc(row['invoice_number'])}</td>
              <td>{_esc(row['vendor'])}</td>
              <td>{_esc(_short_date(str(row['invoice_date'])))}</td>
              <td class="num">{_esc(row['currency'])}</td>
              <td class="num strong">{_money(row['total_amount'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def _rejection_bars(rejections: list[dict]) -> str:
    if not rejections:
        return ""
    max_count = max(int(row["document_count"] or 0) for row in rejections) or 1
    rows = []
    for row in rejections:
        count = int(row["document_count"] or 0)
        rows.append(
            f"""
            <div class="reason-row">
              <div class="reason-label">{_esc(row['reason_code']).replace('_', ' ')}</div>
              <div class="reason-track"><div class="reason-bar" style="width:{100 * count / max_count:.0f}%"></div></div>
              <div class="reason-count">{count}</div>
            </div>
            """
        )
    return "".join(rows)


def export_csvs(settings: Settings) -> list[str]:
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    datasets = {
        "fact_invoices.csv": get_invoices(settings),
        "spend_by_vendor.csv": get_vendor_spend(settings),
        "monthly_spend.csv": get_monthly_spend(settings),
        "rejection_summary.csv": get_rejection_summary(settings),
        "rejected_documents.csv": get_rejected(settings),
    }
    paths: list[str] = []
    for filename, rows in datasets.items():
        path = settings.exports_dir / filename
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
        paths.append(str(path))
    return paths


def build_dashboard(settings: Settings, *, evaluation: dict | None = None) -> str:
    summary = get_run_summary(settings)
    if summary is None:
        raise RuntimeError("No run summary available. Store a validation run first.")
    vendors = get_vendor_spend(settings)
    monthly = get_monthly_spend(settings)
    rejected = get_rejected(settings)
    rejection_summary = get_rejection_summary(settings)
    invoices = get_invoices(settings)
    build_run_summary(settings)
    export_csvs(settings)

    if evaluation is None and settings.evaluation_path.exists():
        try:
            evaluation = json.loads(settings.evaluation_path.read_text(encoding="utf-8"))
        except ValueError:
            evaluation = None
    evaluation = evaluation or {}
    extraction = evaluation.get("extraction", {})
    validation = evaluation.get("validation", {})
    ocr = evaluation.get("ocr", {})
    timing = evaluation.get("timing", {})

    accepted = int(summary["accepted"])
    rejected_count = int(summary["rejected"])
    seen = int(summary["documents_seen"])
    straight_through = 100.0 * accepted / seen if seen else 0.0
    total_seconds = float(timing.get("ocr_total_seconds", 0)) + float(
        timing.get("llm_total_seconds", 0)
    )
    field_accuracy = extraction.get("average_field_accuracy_pct")
    exact_match = extraction.get("document_exact_match_pct")
    raw_field_accuracy = extraction.get("raw_model_average_field_accuracy_pct")
    reconciled_documents = int(extraction.get("reconciled_documents", 0) or 0)
    raw_quality_note = (
        f" &middot; raw LLM {float(raw_field_accuracy):.1f}%"
        if raw_field_accuracy is not None
        else ""
    )
    vendor_count = len(vendors)
    vendor_total = sum(float(row["spend"] or 0) for row in vendors) or 1.0
    top_vendor_share = (
        100.0 * float(vendors[0]["spend"] or 0) / vendor_total if vendors else 0.0
    )
    run_time = f"{total_seconds / 60:.1f} min" if total_seconds else "-"
    generated = datetime.now(UTC).strftime("%b %d, %Y %H:%M UTC")

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>LedgerLens | Invoice operations</title>
<style>
  :root {{
    --ink: #102033;
    --muted: #64748B;
    --line: #E5EBF1;
    --panel: #FFFFFF;
    --canvas: #F3F6F8;
    --teal: #0F766E;
    --amber: #B45309;
    --red: #B42318;
    --shadow: 0 12px 32px rgba(16,32,51,.06);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; color: var(--ink); background: var(--canvas);
    font-family: "Segoe UI", Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  a {{ color: inherit; }}
  .shell {{ max-width: 1380px; margin: 0 auto; padding: 28px 34px 44px; }}
  .topbar {{ display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 26px; }}
  .brand {{ display: flex; align-items: center; gap: 13px; }}
  .brand-mark {{
    width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center;
    color: #fff; background: linear-gradient(145deg, #0F766E, #0B4F4A);
    box-shadow: 0 7px 18px rgba(15,118,110,.28); font-weight: 800; font-size: 18px;
    letter-spacing: -.06em;
  }}
  .brand h1 {{ font-size: 21px; letter-spacing: -.03em; margin: 0; line-height: 1.1; }}
  .brand p {{ margin: 3px 0 0; color: var(--muted); font-size: 12.5px; }}
  .run-meta {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }}
  .pill {{ display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 7px 11px; font-size: 11.5px; color: #334155; box-shadow: 0 3px 10px rgba(16,32,51,.03); }}
  .pulse {{ width: 7px; height: 7px; border-radius: 50%; background: #22C55E; box-shadow: 0 0 0 3px #DCFCE7; }}
  .hero {{
    position: relative; overflow: hidden; color: #F8FAFC; border-radius: 22px; padding: 28px 30px;
    background: linear-gradient(115deg, #102033 0%, #173D44 48%, #0F766E 100%);
    box-shadow: 0 20px 40px rgba(16,32,51,.16); margin-bottom: 18px;
  }}
  .hero:after {{
    content: ""; position: absolute; width: 430px; height: 430px; border-radius: 50%;
    right: -130px; top: -250px; border: 1px solid rgba(255,255,255,.18);
    box-shadow: 0 0 0 55px rgba(255,255,255,.035), 0 0 0 110px rgba(255,255,255,.025);
  }}
  .hero-grid {{ position: relative; z-index: 1; display: flex; justify-content: space-between; gap: 30px; align-items: flex-end; flex-wrap: wrap; }}
  .eyebrow {{ text-transform: uppercase; letter-spacing: .14em; font-size: 10.5px; font-weight: 700; color: #99F6E4; margin-bottom: 9px; }}
  .hero h2 {{ font-size: 32px; line-height: 1.1; letter-spacing: -.035em; margin: 0 0 8px; max-width: 600px; }}
  .hero p {{ margin: 0; color: #C7D7DC; font-size: 13.5px; max-width: 620px; line-height: 1.55; }}
  .hero-stat {{ text-align: right; min-width: 180px; }}
  .hero-stat .big {{ font-size: 38px; font-weight: 750; letter-spacing: -.05em; line-height: 1; }}
  .hero-stat .small {{ color: #C7D7DC; font-size: 12px; margin-top: 7px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 13px; margin-bottom: 18px; }}
  .kpi {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 17px 18px 15px; box-shadow: var(--shadow); }}
  .kpi .label {{ font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: .075em; display: flex; justify-content: space-between; }}
  .kpi .value {{ margin-top: 11px; font-size: 25px; line-height: 1; font-weight: 750; letter-spacing: -.04em; }}
  .kpi .sub {{ margin-top: 8px; color: var(--muted); font-size: 11.5px; }}
  .kpi .sub b {{ color: var(--teal); font-weight: 650; }}
  .kpi.warn .value {{ color: var(--amber); }}
  .grid-main {{ display: grid; grid-template-columns: 1.55fr 1fr; gap: 18px; margin-bottom: 18px; }}
  .grid-lower {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; margin-bottom: 18px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 21px 22px; box-shadow: var(--shadow); }}
  .card-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 16px; }}
  .card h3 {{ margin: 0; font-size: 15px; letter-spacing: -.015em; }}
  .card .hint {{ color: var(--muted); font-size: 11.5px; }}
  .trend svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
  .trend .grid {{ stroke: #E8EEF3; stroke-width: 1; }}
  .trend .axis {{ fill: #7A8B9C; font-size: 10.5px; font-family: inherit; }}
  .trend .dot {{ fill: #fff; stroke: #0F766E; stroke-width: 3; cursor: default; }}
  .vendor-row {{ display: grid; grid-template-columns: 155px 1fr 92px; align-items: center; gap: 12px; padding: 9px 0; }}
  .vendor-name {{ font-size: 12.2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .vendor-track {{ height: 9px; border-radius: 999px; background: #F0F4F7; overflow: hidden; }}
  .vendor-bar {{ height: 100%; border-radius: 999px; }}
  .vendor-value {{ text-align: right; font-size: 12.5px; font-weight: 700; }}
  .vendor-value span {{ display: block; font-size: 10px; font-weight: 500; color: var(--muted); margin-top: 2px; }}
  .reject-card {{ border: 1px solid #F1E0D8; background: #FFFCFA; border-radius: 13px; padding: 13px 14px; margin-bottom: 10px; }}
  .reject-card:last-child {{ margin-bottom: 0; }}
  .reject-head {{ display: flex; justify-content: space-between; align-items: center; }}
  .reject-id {{ font-family: "Cascadia Code", Consolas, monospace; font-size: 11.5px; font-weight: 700; }}
  .reject-file {{ color: var(--muted); font-size: 10.5px; margin: 4px 0 8px; }}
  .reject-card p {{ margin: 7px 0 0; font-size: 11.5px; color: #6B4E42; line-height: 1.45; }}
  .tag-row {{ display: flex; flex-wrap: wrap; gap: 5px; }}
  .tag {{ border-radius: 5px; padding: 3px 6px; font-size: 9.5px; font-weight: 650; letter-spacing: .01em; }}
  .tag.warn {{ background: #FEF3E2; color: #92400E; }}
  .tag.neutral {{ background: #F1F5F9; color: #475569; }}
  .reason-row {{ display: grid; grid-template-columns: 160px 1fr 22px; align-items: center; gap: 11px; margin: 10px 0; }}
  .reason-label {{ font-size: 11.5px; color: #475569; text-transform: capitalize; }}
  .reason-track {{ height: 7px; background: #F1F5F9; border-radius: 999px; overflow: hidden; }}
  .reason-bar {{ height: 100%; background: #D97706; border-radius: 999px; }}
  .reason-count {{ font-size: 11.5px; font-weight: 700; text-align: right; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.3px; }}
  th {{ color: var(--muted); text-transform: uppercase; letter-spacing: .07em; font-size: 9.8px; text-align: left; padding: 0 9px 10px; border-bottom: 1px solid var(--line); }}
  td {{ padding: 10px 9px; border-bottom: 1px solid #F1F5F8; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: 0; }}
  td.num, th.num {{ text-align: right; }}
  .mono {{ font-family: "Cascadia Code", Consolas, monospace; font-size: 11px; }}
  .strong {{ font-weight: 700; }}
  .muted {{ color: var(--muted); }}
  .vendor-dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--teal); margin-right: 8px; vertical-align: 1px; }}
  .quality {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .quality-item {{ background: #F8FAFC; border: 1px solid #EDF2F6; border-radius: 12px; padding: 13px 14px; }}
  .quality-item .q-label {{ font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; font-weight: 700; }}
  .quality-item .q-value {{ font-size: 22px; letter-spacing: -.035em; font-weight: 750; margin-top: 5px; }}
  .quality-item .q-sub {{ font-size: 10.5px; color: var(--muted); margin-top: 3px; }}
  .empty {{ color: var(--muted); font-size: 12px; padding: 15px 0; }}
  .empty.success {{ color: var(--teal); }}
  .footer {{ display: flex; justify-content: space-between; gap: 18px; color: #8191A2; font-size: 10.5px; padding: 4px 2px; flex-wrap: wrap; }}
  .footer b {{ color: #526477; font-weight: 650; }}
  .source-note {{ margin-top: 12px; padding: 11px 13px; border-radius: 10px; background: #F0FDFA; border: 1px solid #CCFBF1; color: #115E59; font-size: 11px; line-height: 1.5; }}
  @media (max-width: 1050px) {{
    .kpis {{ grid-template-columns: repeat(3, 1fr); }}
    .grid-main, .grid-lower {{ grid-template-columns: 1fr; }}
  }}
  @media (max-width: 700px) {{
    .shell {{ padding: 18px 16px 30px; }}
    .topbar {{ align-items: flex-start; flex-direction: column; }}
    .run-meta {{ justify-content: flex-start; }}
    .hero {{ padding: 23px 21px; }}
    .hero h2 {{ font-size: 26px; }}
    .hero-stat {{ text-align: left; }}
    .kpis {{ grid-template-columns: repeat(2, 1fr); }}
    .kpi:last-child {{ grid-column: span 2; }}
    .vendor-row {{ grid-template-columns: 112px 1fr 80px; }}
    .reason-row {{ grid-template-columns: 120px 1fr 20px; }}
  }}
</style>
</head>
<body>
<div class="shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">L</div>
      <div>
        <h1>LedgerLens</h1>
        <p>Local-first invoice operations</p>
      </div>
    </div>
    <div class="run-meta">
      <span class="pill"><span class="pulse"></span> Pipeline complete</span>
      <span class="pill">Model&nbsp; <b>{_esc(summary['model'])}</b></span>
      <span class="pill">Generated&nbsp; {_esc(generated)}</span>
    </div>
  </header>

  <section class="hero">
    <div class="hero-grid">
      <div>
        <div class="eyebrow">Accounts payable overview</div>
        <h2>Clean spend data from messy documents.</h2>
        <p>OCR, a local Ollama model and deterministic validation turn invoice PDFs into an auditable SQL ledger. This snapshot covers the latest pipeline run.</p>
      </div>
      <div class="hero-stat">
        <div class="big">{straight_through:.1f}%</div>
        <div class="small">straight-through processing<br>{accepted} of {seen} documents loaded</div>
      </div>
    </div>
  </section>

  <section class="kpis" aria-label="Key metrics">
    <div class="kpi">
      <div class="label">Total spend <span class="muted">USD</span></div>
      <div class="value">{_money(summary['total_spend'])}</div>
      <div class="sub"><b>{accepted}</b> validated invoices</div>
    </div>
    <div class="kpi">
      <div class="label">Documents</div>
      <div class="value">{seen}</div>
      <div class="sub">{accepted} accepted · {rejected_count} rejected</div>
    </div>
    <div class="kpi warn">
      <div class="label">Rejected</div>
      <div class="value">{rejected_count}</div>
      <div class="sub">{100 * rejected_count / seen if seen else 0:.1f}% of document batch</div>
    </div>
    <div class="kpi">
      <div class="label">OCR confidence</div>
      <div class="value">{float(summary['avg_ocr_confidence']):.1f}%</div>
      <div class="sub">Tesseract word average</div>
    </div>
    <div class="kpi">
      <div class="label">Quality</div>
      <div class="value">{f'{float(field_accuracy):.1f}%' if field_accuracy is not None else '-'}</div>
      <div class="sub">{f'{float(exact_match):.0f}%' if exact_match is not None else '-'} exact docs{raw_quality_note}</div>
    </div>
  </section>

  <main>
    <section class="grid-main">
      <article class="card">
        <div class="card-head">
          <h3>Monthly spend trend</h3>
          <span class="hint">Validated invoices · 2026</span>
        </div>
        <div class="trend">{_svg_trend(monthly)}</div>
      </article>
      <article class="card">
        <div class="card-head">
          <h3>Spend by vendor</h3>
          <span class="hint">Top vendors</span>
        </div>
        {_vendor_bars(vendors)}
        <div class="source-note">Power BI connects to the same SQL views: <b>vw_spend_by_vendor</b>, <b>vw_monthly_spend</b> and <b>vw_rejected_documents</b>.</div>
      </article>
    </section>

    <section class="grid-lower">
      <article class="card">
        <div class="card-head">
          <h3>Vendor ledger</h3>
          <span class="hint">Latest run · {_esc(summary['run_id'][-6:])}</span>
        </div>
        <table>
          <thead><tr><th>Vendor</th><th class="num">Invoices</th><th class="num">Spend</th><th class="num">Share</th></tr></thead>
          <tbody>{_vendor_table(vendors)}</tbody>
        </table>
        <div class="source-note" style="margin-top:8px">{vendor_count} vendors &middot; {_money(vendor_total)} validated spend &middot; top vendor {top_vendor_share:.1f}%.</div>
      </article>
      <article class="card">
        <div class="card-head">
          <h3>Exceptions</h3>
          <span class="hint">{rejected_count} need review</span>
        </div>
        {_rejected_cards(rejected)}
        {f'<div style="margin-top:14px">{_rejection_bars(rejection_summary)}</div>' if rejection_summary else ''}
      </article>
    </section>

    <section class="grid-lower">
      <article class="card">
        <div class="card-head">
          <h3>Recent accepted invoices</h3>
          <span class="hint">Audit trail in SQLite</span>
        </div>
        <table>
          <thead><tr><th>Invoice</th><th>Vendor</th><th>Date</th><th class="num">Ccy</th><th class="num">Amount</th></tr></thead>
          <tbody>{_invoice_rows(invoices)}</tbody>
        </table>
      </article>
      <article class="card">
        <div class="card-head">
          <h3>Pipeline health</h3>
          <span class="hint">Latest run</span>
        </div>
        <div class="quality">
          <div class="quality-item">
            <div class="q-label">Field accuracy</div>
            <div class="q-value">{f'{float(field_accuracy):.1f}%' if field_accuracy is not None else '-'}</div>
            <div class="q-sub">{extraction.get('fields_correct', '-')}/{extraction.get('fields_compared', '-')} fields &middot; raw {f'{float(raw_field_accuracy):.1f}%' if raw_field_accuracy is not None else '-'}</div>
          </div>
          <div class="quality-item">
            <div class="q-label">Reject recall</div>
            <div class="q-value">{f"{float(validation.get('catch_rate_pct', 0)):.0f}%" if validation else '-'}</div>
            <div class="q-sub">{validation.get('expected_rejected_detected', '-')} / {validation.get('expected_rejected_total', '-')} bad docs caught</div>
          </div>
          <div class="quality-item">
            <div class="q-label">Run time</div>
            <div class="q-value">{run_time}</div>
            <div class="q-sub">OCR + local LLM extraction</div>
          </div>
          <div class="quality-item">
            <div class="q-label">Synthetic source</div>
            <div class="q-value">25 PDFs</div>
            <div class="q-sub">Fake vendor and invoice data</div>
          </div>
        </div>
        {f'<div class="source-note" style="margin-top:14px"><b>TOTAL DUE reconciliation:</b> the printed footer total replaced the LLM amount on {reconciled_documents} document(s). Raw LLM field accuracy was {float(raw_field_accuracy):.1f}% before reconciliation.</div>' if reconciled_documents and raw_field_accuracy is not None else ''}
        <div class="source-note" style="margin-top:14px"><b>Audit note:</b> rejected rows never enter the invoice ledger. Every document is traceable to its OCR text and raw model response.</div>
      </article>
    </section>
  </main>

  <footer class="footer">
    <span><b>LedgerLens</b> · Tesseract OCR · Ollama · LangChain · Pydantic · SQLite · Dagster</span>
    <span>Dashboard generated from SQL · <b>Power BI-ready exports</b> in data/exports</span>
  </footer>
</div>
</body>
</html>
"""
    settings.dashboard_path.write_text(page, encoding="utf-8")
    logger.info("[dashboard] wrote %s", settings.dashboard_path)
    return str(settings.dashboard_path)


def dashboard_summary(settings: Settings) -> dict:
    return {
        "run": get_run_summary(settings),
        "vendors": get_vendor_spend(settings),
        "monthly": get_monthly_spend(settings),
        "rejections": get_rejected(settings),
        "invoices": get_invoices(settings),
    }
