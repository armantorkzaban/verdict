"""Generate a self-contained, zero-network interactive HTML report across verdict runs."""

from __future__ import annotations

import datetime as dt
import html
from typing import Any

from .checker import check_ledger
from .differ import scan_workspace_since
from .schema import TaskConfig, normalize_verdict_kind, parse_date, score_to_number


def _esc(val: Any) -> str:
    return html.escape(str(val)) if val is not None else ""


def _highest_entry_evidence(entry: dict[str, Any], task: TaskConfig) -> str:
    codes: list[str] = []
    for v in entry.get("verdicts", []):
        codes.extend(v.get("evidence", []))
    for s in entry.get("scorecard", []):
        codes.extend(s.get("evidence", []))
    return task.max_evidence_code(codes) or list(task.evidence_levels.keys())[0]


def _render_evidence_svg(entries: list[dict[str, Any]], task: TaskConfig) -> str:
    levels = sorted(task.evidence_levels.items(), key=lambda kv: kv[1])
    max_rank = max(r for _, r in levels) if levels else 5
    w, h = 680, 190
    pad_l, pad_r, pad_t, pad_b = 52, 36, 22, 36
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b

    def y_for(rank: float) -> float:
        return pad_t + plot_h / 2 if max_rank == 0 else pad_t + plot_h * (1.0 - rank / max_rank)

    def x_for(i: int, n: int) -> float:
        return pad_l + plot_w / 2 if n <= 1 else pad_l + plot_w * (i / (n - 1))

    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Evidence progression across verdicts" '
        f'style="width:100%;max-width:{w}px;height:auto;display:block">'
    ]
    for code, rank in levels:
        yy = y_for(rank)
        is_causal = code == task.causal_floor
        stroke = "var(--amp)" if is_causal else "var(--rule)"
        dash = 'stroke-dasharray="4 3"' if is_causal else ""
        parts.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{w - pad_r}" y2="{yy:.1f}" stroke="{stroke}" stroke-width="1" {dash}/>'
        )
        parts.append(
            f'<text x="{pad_l - 10}" y="{yy + 4:.1f}" text-anchor="end" '
            f'fill="{"var(--amp)" if is_causal else "var(--micro)"}" font-family="var(--f-mono)" font-size="11">{_esc(code)}</text>'
        )
        if is_causal:
            parts.append(
                f'<text x="{w - pad_r}" y="{yy - 5:.1f}" text-anchor="end" fill="var(--amp)" '
                f'font-family="var(--f-mono)" font-size="10">causal floor ({_esc(code)})</text>'
            )

    if entries:
        pts = [
            (
                x_for(i, len(entries)),
                y_for(task.evidence_levels.get(_highest_entry_evidence(e, task), 0)),
                e.get("date", e.get("id", "")),
                _highest_entry_evidence(e, task),
            )
            for i, e in enumerate(entries)
        ]
        if len(pts) > 1:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pts)
            parts.append(f'<polyline fill="none" stroke="var(--off)" stroke-width="2.2" points="{poly}"/>')
        for x, y, lbl, best in pts:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="var(--off)" stroke="var(--bg)" stroke-width="1.5"/>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{h - 10}" text-anchor="middle" fill="var(--fg-dim)" '
                f'font-family="var(--f-mono)" font-size="11">{_esc(lbl)}</text>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle" fill="var(--off)" '
                f'font-family="var(--f-mono)" font-size="10.5" font-weight="600">{_esc(best)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def build_html_report(task: TaskConfig, entries: list[dict[str, Any]], as_of: dt.date | None = None) -> str:
    ref_date = as_of or dt.date.today()
    latest = entries[-1] if entries else None
    issues = check_ledger(entries, task, as_of=ref_date)
    ws_scan = scan_workspace_since(latest, task, as_of=ref_date)

    best_ev = _highest_entry_evidence(latest, task) if latest else "—"
    outside_checked = int(latest.get("outside_review", {}).get("claims_checked_by_outside_human", 0)) if latest else 0
    all_killed = [
        {**k, "verdict_date": e.get("date", e.get("id", ""))}
        for e in entries
        for k in e.get("killed_or_failed", [])
    ]
    err_count = sum(1 for i in issues if i.severity == "ERROR")
    warn_count = sum(1 for i in issues if i.severity == "WARN")

    q_ids = list(task.questions.keys()) or list(
        dict.fromkeys(v["question_id"] for e in entries for v in e.get("verdicts", []))
    )
    traj_header = "".join(f'<th><span class="mono">{_esc(e.get("date", e["id"]))}</span></th>' for e in entries)
    traj_rows: list[str] = []
    for qid in q_ids:
        q_lbl = task.questions.get(qid, {}).get("label", qid)
        cells = [f"<td class='q-lbl'>{_esc(q_lbl)}</td>"]
        for e in entries:
            found = next((v for v in e.get("verdicts", []) if v["question_id"] == qid), None)
            if not found:
                cells.append("<td class='dim mono'>—</td>")
            else:
                kind = normalize_verdict_kind(found["status"])
                ev_txt = ", ".join(found.get("evidence", []))
                note = found.get("headline") or (
                    found["summary"][:110] + ("…" if len(found["summary"]) > 110 else "")
                )
                cells.append(
                    f"<td><span class='pill pill-{kind}'>{_esc(found['status'])}</span> "
                    f"<span class='ev'>{_esc(ev_txt)}</span><div class='cell-note'>{_esc(note)}</div></td>"
                )
        traj_rows.append("<tr>" + "".join(cells) + "</tr>")

    dim_ids = list(task.dimensions.keys()) or list(
        dict.fromkeys(s["dimension_id"] for e in entries for s in e.get("scorecard", []))
    )
    score_rows: list[str] = []
    for did in dim_ids:
        dmeta = task.dimensions.get(did, {})
        dlbl = dmeta.get("label", did)
        dkind = dmeta.get("kind", "mixed").replace("_", "-")
        cells = [f"<td><strong>{_esc(dlbl)}</strong><div class='micro mono'>{_esc(dkind)}</div></td>"]
        for e in entries:
            srow = next((s for s in e.get("scorecard", []) if s["dimension_id"] == did), None)
            if not srow:
                cells.append("<td class='dim mono'>—</td>")
            else:
                pct = max(12, min(100, int(score_to_number(srow["score"], task.score_scale) / 4.0 * 100)))
                bar_cls = "bar-hi" if pct >= 70 else ("bar-mid" if pct >= 48 else "bar-lo")
                ev_txt = ", ".join(srow.get("evidence", []))
                chg = f" · <em>{_esc(srow['change'])}</em>" if srow.get("change") else ""
                cells.append(
                    f"<td><div class='s-top'><span>{_esc(srow['score'])}</span><span class='ev'>{_esc(ev_txt)}</span></div>"
                    f"<div class='bar-track'><div class='bar-fill {bar_cls}' style='width:{pct}%'></div></div>"
                    f"<div class='cell-note'>{_esc(srow['reasoning'])}{chg}</div></td>"
                )
        score_rows.append("<tr>" + "".join(cells) + "</tr>")

    if all_killed:
        k_items = [
            f"<tr><td class='mono'>{_esc(k.get('verdict_date'))}</td><td><strong>{_esc(k.get('item'))}</strong></td>"
            f"<td><span class='pill pill-stop'>{_esc(k.get('outcome', 'dropped'))}</span> "
            f"<span class='ev'>{_esc(', '.join(k.get('evidence', [])))}</span></td>"
            f"<td>{_esc(k.get('rule_and_finding', ''))}</td></tr>"
            for k in all_killed
        ]
        killed_html = (
            "<table><thead><tr><th>Verdict</th><th>Feature / check / dataset</th><th>Outcome</th>"
            "<th>Rule &amp; evidence</th></tr></thead><tbody>" + "".join(k_items) + "</tbody></table>"
        )
    else:
        killed_html = "<p class='dim'>None recorded yet.</p>"

    risk_rows: list[str] = []
    if latest:
        for r in latest.get("risks", []):
            badges: list[str] = []
            for lbl, dval in (("decision", parse_date(r.get("decision_date"))), ("kill", parse_date(r.get("kill_date")))):
                if dval:
                    days_left = (dval - ref_date).days
                    if days_left < 0:
                        badges.append(f"<span class='pill pill-stop'>{lbl} {dval}: {-days_left}d overdue</span>")
                    elif days_left <= task.due_soon_days:
                        badges.append(f"<span class='pill pill-conditions'>{lbl} {dval}: in {days_left}d</span>")
                    else:
                        badges.append(f"<span class='ev'>{lbl} {dval} ({days_left}d)</span>")
            if not badges and r.get("date_note"):
                badges.append(f"<span class='ev'>{_esc(r['date_note'])}</span>")
            risk_rows.append(
                f"<tr><td><strong>{_esc(r['risk'])}</strong></td><td>{_esc(r['kill_criterion'])}</td><td>{' '.join(badges)}</td></tr>"
            )

    alert_items: list[str] = []
    for iss in issues:
        cls = "pill-stop" if iss.severity == "ERROR" else "pill-conditions"
        alert_items.append(
            f"<li><span class='pill {cls}'>{_esc(iss.severity)}</span> <span class='mono'>{_esc(iss.code)}</span> — {_esc(iss.message)}</li>"
        )
    for c in ws_scan.get("commits_since", [])[:8]:
        alert_items.append(
            f"<li><span class='ev'>git</span> <span class='mono'>{_esc(c['commit'])}</span> ({_esc(c['date'])}) {_esc(c['subject'])}</li>"
        )
    for p in ws_scan.get("uncited_result_files", []):
        alert_items.append(
            f"<li><span class='pill pill-conditions'>NEW RESULT</span> <span class='mono'>{_esc(p)}</span> not yet cited in latest verdict</li>"
        )
    fsum = ws_scan.get("findings") or {}
    if fsum.get("new_since_last_verdict"):
        alert_items.append(
            f"<li><span class='pill pill-conditions'>NEW FINDINGS</span> {len(fsum['new_since_last_verdict'])} added since latest verdict "
            f"(total: {fsum['total']}, outside-human confirmed: {fsum['human_confirmed_count']})</li>"
        )

    tab_buttons, tab_panels = [], []
    for idx, e in enumerate(reversed(entries)):
        active_cls = "active" if idx == 0 else ""
        eid = e["id"]
        tab_buttons.append(
            f'<button type="button" class="tab-btn {active_cls}" data-tab="panel-{_esc(eid)}" onclick="selectRun(\'{_esc(eid)}\')">{_esc(e.get("date", eid))}</button>'
        )
        dis_li = "".join(
            f"<li>{_esc(x['text'])} <span class='ev'>{_esc(', '.join(x.get('evidence', [])))}</span></li>"
            for x in e.get("changes", {}).get("disconfirming", [])
        )
        con_li = "".join(
            f"<li>{_esc(x['text'])} <span class='ev'>{_esc(', '.join(x.get('evidence', [])))}</span></li>"
            for x in e.get("changes", {}).get("confirming", [])
        )
        str_li = "".join(
            f"<li>{_esc(s['text'])} <span class='ev'>{_esc(', '.join(s.get('evidence', [])))}</span>"
            f"{' <span class=ev>structural</span>' if s.get('structural') else ''}</li>"
            for s in e.get("strengths", [])
        )
        tests_li = "".join(
            f"<li><strong>{_esc(t['title'])}</strong>: {_esc(t['detail'])}"
            f"{' <span class=pill>' + _esc(t['outcome']) + '</span>' if t.get('outcome') else ''}</li>"
            for t in e.get("next_tests", [])
        )
        comp_rows = []
        for comp in e.get("competitors", []):
            cdt = parse_date(comp.get("checked"))
            stale = cdt and (ref_date - cdt).days > task.max_source_age_days and (e is latest)
            c_badge = (
                f"<span class='pill pill-conditions'>{_esc(comp.get('checked'))} (stale)</span>"
                if stale
                else f"<span class='mono dim'>{_esc(comp.get('checked') or comp.get('source'))}</span>"
            )
            comp_rows.append(
                f"<tr><td><strong>{_esc(comp['name'])}</strong></td><td>{_esc(comp.get('does', '—'))}</td>"
                f"<td>{_esc(comp.get('target', '—'))}</td><td>{_esc(comp.get('lever', '—'))}</td>"
                f"<td>{_esc(comp.get('measured', '—'))}</td><td>{_esc(comp.get('source', ''))} · {c_badge}</td></tr>"
            )
        contra_li = "".join(
            f"<li><strong>{_esc(c['target'])}:</strong> {_esc(c['proposal'])}</li>" for c in e.get("contradictions", [])
        )
        addenda_html = "".join(
            f"<div class='callout'><strong>Addendum ({_esc(ad.get('date', ''))}): {_esc(ad.get('title', ''))}</strong>"
            f"<ul>{''.join('<li>' + _esc(pt) + '</li>' for pt in ad.get('points', []))}</ul></div>"
            for ad in e.get("addenda", [])
        )
        dis_fallback = "<li class='dim'>Baseline entry (none earlier).</li>"
        con_fallback = "<li class='dim'>Baseline entry.</li>"
        tab_panels.append(
            f"<div id='panel-{_esc(eid)}' class='tab-panel {active_cls}'>"
            f"<p class='preamble'>{_esc(e.get('preamble', ''))}</p>"
            f"<div class='two-col'><div><h3>Disconfirming changes</h3><ul>{dis_li or dis_fallback}</ul></div>"
            f"<div><h3>Confirming changes</h3><ul>{con_li or con_fallback}</ul></div></div>"
            f"<div class='two-col' style='margin-top:20px'>"
            f"<div><h3>Where we are stronger ({_esc(task.strength_floor)}+ or structural)</h3><ul>{str_li}</ul>"
            f"<p class='cell-note' style='margin-top:8px'><strong>Options (unbuilt / unproven):</strong> {_esc('; '.join(e.get('options', [])))}</p></div>"
            f"<div><h3>Next cheapest tests</h3><ol>{tests_li}</ol>"
            f"{('<h3>Proposed roadmap / plan edits</h3><ul>' + contra_li + '</ul>') if contra_li else ''}</div></div>"
            f"{addenda_html}"
            f"<h3 style='margin-top:22px'>Competitor matrix ({_esc(e.get('date', eid))})</h3>"
            f"<div class='table-wrap'><table><thead><tr><th>Competitor</th><th>Does</th><th>Segment</th>"
            f"<th>Core lever</th><th>Measured</th><th>Source &amp; check date</th></tr></thead>"
            f"<tbody>{''.join(comp_rows)}</tbody></table></div></div>"
        )

    ladder_legend = " · ".join(
        f"<span class='mono'><strong>{_esc(k)}</strong></span> {_esc(task.evidence_descriptions[k])}"
        for k in sorted(task.evidence_levels, key=lambda c: task.evidence_levels[c])
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(task.project)} — Verdict Ledger &amp; Trajectory</title>
<style>
:root {{
  --bg:#F5F5F1; --bg-2:#ECECE6; --bg-3:#E1E1D9;
  --fg:#14171B; --fg-dim:#454B54; --micro:#5C636D;
  --amp:#A44A00; --amp-wash:rgba(164,74,0,.10);
  --off:#0D6B62; --off-wash:rgba(13,107,98,.11);
  --stop:#8F2D2D; --stop-wash:rgba(143,45,45,.11);
  --rule:rgba(20,23,27,.15); --rule-soft:rgba(20,23,27,.07);
  --f-serif:"Source Serif 4",Charter,"Iowan Old Style",Georgia,serif;
  --f-sans:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}}
@media (prefers-color-scheme:dark) {{
  :root {{
    --bg:#0F1215; --bg-2:#161A1F; --bg-3:#1E242A;
    --fg:#E7EAED; --fg-dim:#A7AEB7; --micro:#8A929D;
    --amp:#F2A050; --amp-wash:rgba(242,160,80,.13);
    --off:#5FC6B8; --off-wash:rgba(95,198,184,.13);
    --stop:#E87C7C; --stop-wash:rgba(232,124,124,.14);
    --rule:rgba(231,234,237,.14); --rule-soft:rgba(231,234,237,.07);
  }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font-family:var(--f-sans); font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1160px; margin:0 auto; padding:32px clamp(16px,4vw,40px) 72px; }}
.mono {{ font-family:var(--f-mono); }} .dim {{ color:var(--fg-dim); }} .micro {{ font-size:12px; color:var(--micro); }}
h1,h2,h3 {{ font-family:var(--f-serif); font-weight:600; margin:0; }}
h1 {{ font-size:clamp(28px,4.2vw,44px); letter-spacing:-.02em; margin-top:6px; }}
h2 {{ font-size:22px; margin:38px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--rule); }}
h3 {{ font-size:16.5px; margin:0 0 8px; }}
.kicker {{ font-family:var(--f-mono); font-size:12px; color:var(--micro); text-transform:uppercase; letter-spacing:.05em; }}
.facts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1px; background:var(--rule); border:1px solid var(--rule); margin:24px 0 18px; }}
.fact {{ background:var(--bg); padding:14px 16px; }}
.fact .k {{ font-family:var(--f-mono); font-size:11.5px; color:var(--micro); }}
.fact .v {{ font-family:var(--f-serif); font-size:26px; font-weight:600; margin-top:2px; }}
.fact .s {{ font-size:12.5px; color:var(--fg-dim); margin-top:2px; }}
.ladder-box {{ display:grid; grid-template-columns:minmax(0,1.2fr) minmax(0,1fr); gap:24px; align-items:center; background:var(--bg-2); border:1px solid var(--rule); padding:18px 20px; border-radius:3px; }}
@media (max-width:820px) {{ .ladder-box, .two-col {{ grid-template-columns:1fr; }} }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13.8px; }}
th, td {{ text-align:left; vertical-align:top; padding:10px 12px; border-bottom:1px solid var(--rule-soft); }}
th {{ font-family:var(--f-mono); font-size:12px; font-weight:500; color:var(--micro); border-bottom:1px solid var(--rule); background:var(--bg-2); }}
.q-lbl {{ font-weight:600; white-space:nowrap; }}
.pill {{ display:inline-block; font-family:var(--f-mono); font-size:11.5px; font-weight:500; padding:2px 7px; border-radius:2px; border:1px solid transparent; }}
.pill-go {{ background:var(--off-wash); color:var(--off); border-color:color-mix(in srgb,var(--off) 35%,transparent); }}
.pill-conditions, .pill-hold, .pill-pivot {{ background:var(--amp-wash); color:var(--amp); border-color:color-mix(in srgb,var(--amp) 35%,transparent); }}
.pill-stop {{ background:var(--stop-wash); color:var(--stop); border-color:color-mix(in srgb,var(--stop) 35%,transparent); }}
.ev {{ display:inline-block; font-family:var(--f-mono); font-size:11px; color:var(--fg-dim); background:var(--bg-3); padding:1px 6px; border-radius:2px; margin-left:3px; }}
.cell-note {{ font-size:12.6px; color:var(--fg-dim); margin-top:5px; line-height:1.45; }}
.s-top {{ display:flex; justify-content:space-between; gap:8px; align-items:baseline; font-weight:500; }}
.bar-track {{ height:4px; background:var(--bg-3); border-radius:2px; margin-top:5px; overflow:hidden; }}
.bar-fill {{ height:100%; }} .bar-hi {{ background:var(--off); }} .bar-mid {{ background:var(--amp); }} .bar-lo {{ background:var(--stop); }}
ul, ol {{ margin:6px 0 0; padding-left:20px; }} li {{ margin-bottom:6px; font-size:13.8px; }}
.tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
.tab-btn {{ font-family:var(--f-mono); font-size:12.5px; padding:6px 12px; cursor:pointer; background:var(--bg-2); color:var(--fg-dim); border:1px solid var(--rule); border-radius:2px; }}
.tab-btn.active {{ background:var(--fg); color:var(--bg); border-color:var(--fg); }}
.tab-panel {{ display:none; background:var(--bg-2); border:1px solid var(--rule); padding:20px; border-radius:3px; }}
.tab-panel.active {{ display:block; }}
.preamble {{ font-size:14px; color:var(--fg-dim); margin:0 0 16px; padding-bottom:12px; border-bottom:1px solid var(--rule-soft); }}
.callout {{ margin-top:16px; padding:12px 14px; background:var(--bg); border-left:3px solid var(--amp); font-size:13.5px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">Verdict Report · as of {_esc(ref_date)} · {len(entries)} recorded run(s)</div>
  <h1>{_esc(task.project)}</h1>
  <p class="dim" style="max-width:74ch;margin-top:8px">{_esc(task.summary)}</p>
  <div class="facts">
    <div class="fact"><div class="k">Strongest evidence</div><div class="v" style="color:var(--off)">{_esc(best_ev)}</div><div class="s">Causal floor: {_esc(task.causal_floor)} (below {_esc(task.causal_floor)}: predicted only)</div></div>
    <div class="fact"><div class="k">Outside human review</div><div class="v" style="color:{'var(--off)' if outside_checked > 0 else 'var(--amp)'}">{outside_checked} claims</div><div class="s">{'Independent check recorded' if outside_checked > 0 else 'All claims self-assessed / E0 AI desk review'}</div></div>
    <div class="fact"><div class="k">Killed / failed by rule</div><div class="v">{len(all_killed)}</div><div class="s">Features, checks or datasets dropped openly</div></div>
    <div class="fact"><div class="k">Check &amp; horizon status</div><div class="v" style="color:{'var(--stop)' if err_count else ('var(--amp)' if warn_count else 'var(--off)')}">{err_count} err · {warn_count} warn</div><div class="s">Last verdict: {_esc(ws_scan.get('latest_verdict_date') or 'none')} ({ws_scan.get('days_since_last_verdict', '—')}d ago)</div></div>
  </div>
  <div class="ladder-box">
    <div>{_render_evidence_svg(entries, task)}</div>
    <div style="font-size:13px;color:var(--fg-dim);line-height:1.6"><strong style="color:var(--fg)">Evidence ladder &amp; confidence ceiling</strong><br>{ladder_legend}</div>
  </div>
  <h2>1. Recon &amp; horizon since latest verdict ({_esc(ws_scan.get('latest_verdict_date') or '—')} → {_esc(ref_date)})</h2>
  <ul>{''.join(alert_items) if alert_items else '<li class="dim">No lints, new commits, or overdue dates.</li>'}</ul>
  <h2>2. Verdict trajectory</h2>
  <div class="table-wrap"><table><thead><tr><th>Track</th>{traj_header}</tr></thead><tbody>{''.join(traj_rows)}</tbody></table></div>
  <h2>3. Scorecard evolution (structural vs. feature-dependent)</h2>
  <div class="table-wrap"><table><thead><tr><th>Dimension</th>{traj_header}</tr></thead><tbody>{''.join(score_rows)}</tbody></table></div>
  <h2>4. Active risks &amp; kill criteria</h2>
  <div class="table-wrap"><table><thead><tr><th>Risk</th><th>Kill criterion</th><th>Horizon (as of {_esc(ref_date)})</th></tr></thead><tbody>{''.join(risk_rows) or '<tr><td colspan="3" class="dim">None</td></tr>'}</tbody></table></div>
  <h2>5. Killed features &amp; negative results</h2>
  <div class="table-wrap">{killed_html}</div>
  <h2>6. Run inspector</h2>
  <div class="tabs">{''.join(tab_buttons)}</div>
  {''.join(tab_panels)}
</div>
<script>
function selectRun(id) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === 'panel-' + id));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + id));
}}
</script>
</body>
</html>
"""

