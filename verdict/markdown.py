"""Render a structured verdict entry into clean, plain Markdown (YYYY-MM-DD.md)."""

from __future__ import annotations

from typing import Any

from .schema import TaskConfig


def render_verdict_markdown(entry: dict[str, Any], task: TaskConfig) -> str:
    lines: list[str] = []
    title = entry.get("title") or f"Verdict, {entry['date']}"
    lines.append(f"# {title}")
    lines.append("")
    if entry.get("preamble"):
        lines.append(entry["preamble"].strip())
        lines.append("")

    lines.append("## 1. The verdict")
    lines.append("")
    lines.append("| Question | Verdict | Evidence |")
    lines.append("|---|---|---|")
    for v in entry.get("verdicts", []):
        q_label = v.get("label") or task.questions.get(v["question_id"], {}).get("label", v["question_id"])
        ev_str = v.get("evidence_note") or ", ".join(v.get("evidence", []))
        verdict_cell = f"{v['status']} {v['summary']}".strip() if v["status"].endswith(".") else f"{v['status']}. {v['summary']}".strip()
        lines.append(f"| {q_label} | {verdict_cell} | {ev_str} |")
    lines.append("")

    prev_ref = entry.get("previous_id")
    lines.append(f"## 2. What changed since {prev_ref}" if prev_ref else "## 2. What changed")
    lines.append("")
    changes = entry.get("changes", {})
    if entry.get("baseline") and changes.get("note"):
        lines.append(changes["note"])
        lines.append("")
    else:
        if changes.get("disconfirming"):
            lines.append("**Disconfirming first:**")
            lines.append("")
            for item in changes["disconfirming"]:
                lines.append(f"- {item['text']}")
            lines.append("")
        elif changes.get("no_disconfirming_reason"):
            lines.append(f"**Disconfirming first:** None ({changes['no_disconfirming_reason']}).")
            lines.append("")

        if changes.get("confirming"):
            lines.append("**Confirming:**")
            lines.append("")
            for item in changes["confirming"]:
                lines.append(f"- {item['text']}")
            lines.append("")

        if changes.get("decisions"):
            lines.append(f"**Decisions since:** {changes['decisions']}")
            lines.append("")

    lines.append("## 3. Scorecard")
    lines.append("")
    has_change_col = any(r.get("change") for r in entry.get("scorecard", []))
    if has_change_col:
        lines.append("| Dimension | Score | Evidence | Change | Reasoning |")
        lines.append("|---|---|---|---|---|")
        for r in entry.get("scorecard", []):
            d_label = r.get("label") or task.dimensions.get(r["dimension_id"], {}).get("label", r["dimension_id"])
            ev_str = r.get("evidence_note") or ", ".join(r.get("evidence", []))
            lines.append(f"| {d_label} | {r['score']} | {ev_str} | {r.get('change', '—')} | {r['reasoning']} |")
    else:
        lines.append("| Dimension | Score | Evidence | Reasoning |")
        lines.append("|---|---|---|---|")
        for r in entry.get("scorecard", []):
            d_label = r.get("label") or task.dimensions.get(r["dimension_id"], {}).get("label", r["dimension_id"])
            ev_str = r.get("evidence_note") or ", ".join(r.get("evidence", []))
            lines.append(f"| {d_label} | {r['score']} | {ev_str} | {r['reasoning']} |")
    lines.append("")

    outside = entry.get("outside_review", {})
    lines.append("## 4. Outside review")
    lines.append("")
    checked_n = outside.get("claims_checked_by_outside_human", 0)
    lines.append(
        f"- **Claims checked by a person outside the project: {checked_n if checked_n else 'none'}.** {outside.get('summary', '')}".rstrip()
    )
    for note in outside.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## 5. Where we are stronger")
    lines.append("")
    lines.append(f"Only {task.strength_floor} or better, or a structural fact:")
    lines.append("")
    for s in entry.get("strengths", []):
        lines.append(f"- {s['text']}")
    lines.append("")
    opts = entry.get("options", [])
    if opts:
        lines.append("Options, not strengths: " + "; ".join(opts) + ".")
        lines.append("")

    lines.append("## 6. Competitor matrix")
    lines.append("")
    lines.append("| Competitor | What they do | Segment / deployment | Core lever | Measured evidence | Source, date |")
    lines.append("|---|---|---|---|---|---|")
    for c in entry.get("competitors", []):
        src_col = (
            f"{c['source']}, checked {c['checked']}"
            if c.get("checked") and c["checked"] not in c["source"]
            else c["source"]
        )
        lines.append(
            f"| {c['name']} | {c.get('does', '—')} | {c.get('target', '—')} | "
            f"{c.get('lever', '—')} | {c.get('measured', '—')} | {src_col} |"
        )
    lines.append("")

    lines.append("## 7. Top risks")
    lines.append("")
    lines.append("| Risk | Kill criterion | Date |")
    lines.append("|---|---|---|")
    for r in entry.get("risks", []):
        date_col = r.get("date_note") or r.get("kill_date") or r.get("trigger", "—")
        lines.append(f"| {r['risk']} | {r['kill_criterion']} | {date_col} |")
    if entry.get("internal_risk_note"):
        lines.append("")
        lines.append(entry["internal_risk_note"])
    lines.append("")

    lines.append("## 8. The next cheapest test")
    lines.append("")
    for idx, t in enumerate(entry.get("next_tests", []), start=1):
        lines.append(f"{idx}. **{t['title']}**: {t['detail']}")
    lines.append("")

    contradictions = entry.get("contradictions", [])
    if contradictions:
        lines.append("## 9. Where this contradicts the roadmap or plan")
        lines.append("")
        lines.append("Proposed, not applied:")
        lines.append("")
        for c in contradictions:
            lines.append(f"- **{c['target']}:** {c['proposal']}")
        lines.append("")

    sec_num = 10 if contradictions else 9
    lines.append(f"## {sec_num}. Sources")
    lines.append("")
    for src in entry.get("sources", []):
        lbl = f"[{src['label']}]({src['url']})" if src.get("url") else src["label"]
        suffix = f" ({src['date']})" if src.get("date") else (f" ({src['note']})" if src.get("note") else "")
        lines.append(f"- {lbl}{suffix}")
    lines.append("")

    return "\n".join(lines)

