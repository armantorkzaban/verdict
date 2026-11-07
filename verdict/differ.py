"""Longitudinal verdict comparison and workspace reconnaissance since last verdict."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Any

from .checker import check_entry
from .schema import TaskConfig, parse_date, parse_findings_md, score_to_number


def diff_entries(prev: dict[str, Any], curr: dict[str, Any], task: TaskConfig) -> dict[str, Any]:
    prev_v = {v["question_id"]: v for v in prev.get("verdicts", [])}
    curr_v = {v["question_id"]: v for v in curr.get("verdicts", [])}

    verdict_shifts: list[dict[str, Any]] = []
    for qid in dict.fromkeys([*prev_v.keys(), *curr_v.keys()]):
        q_label = task.questions.get(qid, {}).get("label", qid)
        pv, cv = prev_v.get(qid), curr_v.get(qid)
        if pv is None and cv is not None:
            verdict_shifts.append(
                {
                    "question_id": qid,
                    "label": q_label,
                    "change": "added",
                    "from_status": None,
                    "to_status": cv["status"],
                    "from_evidence": [],
                    "to_evidence": cv.get("evidence", []),
                }
            )
        elif pv is not None and cv is None:
            verdict_shifts.append(
                {
                    "question_id": qid,
                    "label": q_label,
                    "change": "removed",
                    "from_status": pv["status"],
                    "to_status": None,
                    "from_evidence": pv.get("evidence", []),
                    "to_evidence": [],
                }
            )
        elif pv and cv:
            if pv["status"].strip().lower() != cv["status"].strip().lower() or pv.get("evidence", []) != cv.get("evidence", []):
                verdict_shifts.append(
                    {
                        "question_id": qid,
                        "label": q_label,
                        "change": "moved",
                        "from_status": pv["status"],
                        "to_status": cv["status"],
                        "from_evidence": pv.get("evidence", []),
                        "to_evidence": cv.get("evidence", []),
                    }
                )

    prev_s = {s["dimension_id"]: s for s in prev.get("scorecard", [])}
    curr_s = {s["dimension_id"]: s for s in curr.get("scorecard", [])}
    score_shifts: list[dict[str, Any]] = []
    for did in dict.fromkeys([*prev_s.keys(), *curr_s.keys()]):
        d_label = task.dimensions.get(did, {}).get("label", did)
        ps, cs = prev_s.get(did), curr_s.get(did)
        if ps and cs:
            delta_num = round(
                score_to_number(cs["score"], task.score_scale) - score_to_number(ps["score"], task.score_scale),
                2,
            )
            if ps["score"] != cs["score"] or ps.get("evidence") != cs.get("evidence") or cs.get("change"):
                score_shifts.append(
                    {
                        "dimension_id": did,
                        "label": d_label,
                        "from_score": ps["score"],
                        "to_score": cs["score"],
                        "delta": delta_num,
                        "stated_change": cs.get("change", ""),
                        "from_evidence": ps.get("evidence", []),
                        "to_evidence": cs.get("evidence", []),
                    }
                )

    return {
        "from_id": prev.get("id"),
        "from_date": prev.get("date"),
        "to_id": curr.get("id"),
        "to_date": curr.get("date"),
        "verdict_shifts": verdict_shifts,
        "score_shifts": score_shifts,
        "killed_or_failed": curr.get("killed_or_failed", []),
    }


def _git_commits_since(workspace_root: Path, base_commit: str | None, since_date: str | None) -> list[dict[str, str]]:
    if not (workspace_root / ".git").exists():
        return []
    cmd = ["git", "-C", str(workspace_root), "log", "--pretty=format:%h|%cs|%s"]
    if base_commit:
        chk = subprocess.run(
            ["git", "-C", str(workspace_root), "cat-file", "-e", f"{base_commit}^{{commit}}"],
            capture_output=True,
            check=False,
        )
        if chk.returncode == 0:
            cmd.append(f"{base_commit}..HEAD")
        elif since_date:
            cmd.append(f"--since={since_date}")
    elif since_date:
        cmd.append(f"--since={since_date}")
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            out.append({"commit": parts[0], "date": parts[1], "subject": parts[2]})
    return out


def _git_changed_files_since(workspace_root: Path, base_commit: str | None) -> list[str]:
    if not base_commit or not (workspace_root / ".git").exists():
        return []
    res = subprocess.run(
        ["git", "-C", str(workspace_root), "diff", "--name-only", f"{base_commit}..HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def scan_workspace_since(
    latest_entry: dict[str, Any] | None,
    task: TaskConfig,
    as_of: dt.date | None = None,
) -> dict[str, Any]:
    ref_date = as_of or dt.date.today()
    base_commit = latest_entry.get("head_commit") if latest_entry else None
    since_date = latest_entry.get("date") if latest_entry else None

    commits = _git_commits_since(task.workspace_root, base_commit, since_date)
    changed_git_files = _git_changed_files_since(task.workspace_root, base_commit)

    cited_paths: set[str] = set()
    if latest_entry:
        for src in latest_entry.get("sources", []):
            cited_paths.update(src.get("repo_paths", []))
        for kind in ("disconfirming", "confirming"):
            for ch in latest_entry.get("changes", {}).get(kind, []):
                cited_paths.update(ch.get("repo_paths", []))

    uncited_results: list[str] = []
    for rel in task.watched_paths.get("results", []):
        target = task.workspace_root / rel
        if target.is_dir():
            for md in sorted(target.rglob("*.md")):
                if md.name.lower() == "readme.md":
                    continue
                rel_str = str(md.relative_to(task.workspace_root))
                m_date = parse_date(md.name[:10])
                if rel_str not in cited_paths and (
                    rel_str in changed_git_files
                    or (m_date and since_date and str(m_date) >= since_date)
                ):
                    uncited_results.append(rel_str)

    findings_summary: dict[str, Any] = {}
    reviews_rel = task.watched_paths.get("reviews")
    if reviews_rel and (task.workspace_root / reviews_rel).exists():
        records = parse_findings_md(task.workspace_root / reviews_rel)
        known_max = int(latest_entry.get("outside_review", {}).get("max_finding_number_seen", 0)) if latest_entry else 0
        new_findings, open_or_partial, human_confirmed = [], [], []
        for fid, rec in records.items():
            fnum = int(fid[1:]) if fid[1:].isdigit() else 0
            if known_max and fnum > known_max:
                new_findings.append(f"{fid} ({rec.severity}, {rec.status}): {rec.title}")
            if any(tok in rec.status.lower() for tok in ("open", "in part")):
                open_or_partial.append(f"{fid} [{rec.status}, {rec.severity}]: {rec.title}")
            if rec.provenance == "human":
                human_confirmed.append(fid)
        findings_summary = {
            "total": len(records),
            "human_confirmed_count": len(human_confirmed),
            "human_confirmed_ids": human_confirmed,
            "new_since_last_verdict": new_findings,
            "open_or_in_part": open_or_partial,
        }

    horizon_issues = []
    if latest_entry:
        for iss in check_entry(latest_entry, task, as_of=ref_date, check_horizon=True):
            if iss.code in (
                "OVERDUE_DECISION_DATE",
                "OVERDUE_KILL_DATE",
                "DUE_SOON_DECISION",
                "DUE_SOON_KILL",
                "STALE_COMPETITOR_CHECK",
            ):
                horizon_issues.append(iss.to_dict())

    return {
        "as_of": str(ref_date),
        "latest_verdict_id": latest_entry.get("id") if latest_entry else None,
        "latest_verdict_date": since_date,
        "latest_head_commit": base_commit,
        "days_since_last_verdict": (
            (ref_date - parse_date(since_date)).days if since_date and parse_date(since_date) else None
        ),
        "commits_since": commits,
        "changed_files_since": changed_git_files,
        "uncited_result_files": uncited_results,
        "findings": findings_summary,
        "horizon_alerts": horizon_issues,
    }


def format_diff_text(task: TaskConfig, entry_diff: dict[str, Any] | None, ws_scan: dict[str, Any]) -> str:
    lines: list[str] = [f"== {task.project}: verdict diff (as of {ws_scan['as_of']}) =="]
    if ws_scan["latest_verdict_id"]:
        lines.append(
            f"Latest verdict: {ws_scan['latest_verdict_id']} ({ws_scan['latest_verdict_date']}, "
            f"{ws_scan['days_since_last_verdict']}d ago, base commit {ws_scan['latest_head_commit'] or 'unset'})"
        )
    else:
        lines.append("Latest verdict: none recorded yet")

    if entry_diff:
        lines.append(f"\n-- Movement: {entry_diff['from_id']} -> {entry_diff['to_id']} --")
        if entry_diff["verdict_shifts"]:
            for s in entry_diff["verdict_shifts"]:
                if s["change"] == "added":
                    lines.append(f"  + [{s['label']}] added: {s['to_status']} ({', '.join(s['to_evidence'])})")
                elif s["change"] == "moved":
                    lines.append(
                        f"  ~ [{s['label']}] {s['from_status']} ({','.join(s['from_evidence'])}) "
                        f"-> {s['to_status']} ({','.join(s['to_evidence'])})"
                    )
        else:
            lines.append("  No top-line verdict status changes.")

        if entry_diff["score_shifts"]:
            lines.append("  Scorecard shifts:")
            for sc in entry_diff["score_shifts"]:
                chg = f" [{sc['stated_change']}]" if sc["stated_change"] else ""
                lines.append(
                    f"    - {sc['label']}: {sc['from_score']} ({','.join(sc['from_evidence'])}) "
                    f"-> {sc['to_score']} ({','.join(sc['to_evidence'])}){chg}"
                )

    lines.append("\n-- Workspace changes since latest verdict --")
    if ws_scan["commits_since"]:
        lines.append(
            f"  Git commits since {ws_scan['latest_head_commit'] or ws_scan['latest_verdict_date']} ({len(ws_scan['commits_since'])}):"
        )
        for c in ws_scan["commits_since"]:
            lines.append(f"    {c['commit']} ({c['date']}) {c['subject']}")
    else:
        lines.append("  Git commits since base: 0")

    if ws_scan["uncited_result_files"]:
        lines.append("  New/updated result notes not yet cited in latest verdict:")
        for p in ws_scan["uncited_result_files"]:
            lines.append(f"    - {p}")

    fsum = ws_scan.get("findings") or {}
    if fsum:
        lines.append(
            f"\n-- Review status ({fsum['total']} total, {fsum['human_confirmed_count']} outside-human confirmed) --"
        )
        if fsum["new_since_last_verdict"]:
            lines.append(f"  New findings added since latest verdict ({len(fsum['new_since_last_verdict'])}):")
            for nf in fsum["new_since_last_verdict"]:
                lines.append(f"    * {nf}")
        if fsum["open_or_in_part"]:
            lines.append(f"  Currently open / in-part findings ({len(fsum['open_or_in_part'])}):")
            for of in fsum["open_or_in_part"]:
                lines.append(f"    - {of}")

    lines.append("\n-- Horizon & freshness alerts --")
    if ws_scan["horizon_alerts"]:
        for al in ws_scan["horizon_alerts"]:
            lines.append(f"  [{al['severity']}] {al['code']}: {al['message']}")
    else:
        lines.append("  None.")

    return "\n".join(lines) + "\n"

