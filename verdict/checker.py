"""Deterministic epistemic, provenance, citation, and horizon checker for verdicts."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import FindingRecord, TaskConfig, parse_date, parse_findings_md


@dataclass
class Issue:
    severity: str  # "ERROR" | "WARN"
    code: str
    entry_id: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "entry_id": self.entry_id,
            "location": self.location,
            "message": self.message,
        }


REQUIRED_TOP_FIELDS = (
    "id",
    "date",
    "verdicts",
    "scorecard",
    "outside_review",
    "strengths",
    "options",
    "competitors",
    "risks",
    "next_tests",
    "sources",
)

HYPOTHETICAL_GUARDS = (
    r"\bwould\s+(?:have\s+)?(?:save|saved|reduce|reduced|cut|improve|improved|increase|increased)\b",
    r"\bcould\s+(?:have\s+)?(?:save|saved|reduce|reduced|cut|improve|improved|increase|increased)\b",
    r"\bcan\s+(?:save|reduce|cut|improve|accelerate|increase)\b",
    r"\bto\s+(?:save|reduce|cut|improve|accelerate|increase)\b",
    r"\bin\s+published\s+work\b",
)


def _git_commit_exists(workspace_root: Path, rev: str) -> bool:
    if not (workspace_root / ".git").exists():
        return True
    res = subprocess.run(
        ["git", "-C", str(workspace_root), "cat-file", "-e", f"{rev}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


def _find_forbidden_causal_verb(text: str, task: TaskConfig) -> str | None:
    scrubbed = re.sub(r'["\'][^"\']{1,24}["\']', " ", text)
    low = scrubbed.lower()
    for phrase in task.allowed_causal_phrases:
        low = low.replace(phrase.lower(), " ")
    for pat in HYPOTHETICAL_GUARDS:
        if re.search(pat, low):
            low = re.sub(pat, " ", low)
    if "published work" in text.lower():
        return None
    for verb in task.forbidden_below_causal:
        if re.search(rf"\b{re.escape(verb.lower())}\b", low):
            return verb
    return None


def _validate_evidence_list(
    codes: Any,
    task: TaskConfig,
    entry_id: str,
    location: str,
    issues: list[Issue],
) -> list[str]:
    if not isinstance(codes, list) or not codes:
        issues.append(
            Issue(
                "ERROR",
                "MISSING_EVIDENCE",
                entry_id,
                location,
                "Must carry a non-empty list of evidence level codes (e.g. ['E2']).",
            )
        )
        return []
    valid: list[str] = []
    for c in codes:
        if c not in task.evidence_levels:
            issues.append(
                Issue(
                    "ERROR",
                    "UNKNOWN_EVIDENCE",
                    entry_id,
                    location,
                    f"Unknown evidence level '{c}'. Expected one of {list(task.evidence_levels)}.",
                )
            )
        else:
            valid.append(c)
    return valid


def _validate_review_provenance(
    review_status: str,
    review_refs: list[str],
    findings: dict[str, FindingRecord],
    entry_id: str,
    location: str,
    issues: list[Issue],
) -> None:
    if review_status not in ("self-assessed", "reviewed"):
        issues.append(
            Issue(
                "ERROR",
                "INVALID_REVIEW_STATUS",
                entry_id,
                location,
                f"review_status must be 'self-assessed' or 'reviewed', got '{review_status}'.",
            )
        )
        return
    if review_status == "reviewed":
        if not review_refs:
            issues.append(
                Issue(
                    "ERROR",
                    "UNVERIFIED_OUTSIDE_REVIEW",
                    entry_id,
                    location,
                    "Claim marked 'reviewed' must cite at least one finding ID or named outside reviewer in review_refs.",
                )
            )
            return
        for ref in review_refs:
            if re.match(r"^F\d+$", ref):
                if findings and ref not in findings:
                    issues.append(
                        Issue(
                            "ERROR",
                            "MISSING_FINDING_REF",
                            entry_id,
                            location,
                            f"Referenced finding '{ref}' does not exist in findings file.",
                        )
                    )
                elif findings and findings[ref].provenance == "ai_desk":
                    issues.append(
                        Issue(
                            "ERROR",
                            "AI_REVIEW_MARKED_HUMAN",
                            entry_id,
                            location,
                            f"Finding '{ref}' is an AI desk review (E0), not an outside human review; keep review_status as 'self-assessed'.",
                        )
                    )


def check_entry(
    entry: dict[str, Any],
    task: TaskConfig,
    as_of: dt.date | None = None,
    strict_dates: bool = False,
    check_horizon: bool = True,
) -> list[Issue]:
    issues: list[Issue] = []
    entry_id = str(entry.get("id", "<missing-id>"))
    entry_date = parse_date(entry.get("date"))
    ref_date = as_of or entry_date or dt.date.today()
    horizon_sev = "ERROR" if strict_dates else "WARN"

    for field_name in REQUIRED_TOP_FIELDS:
        if field_name not in entry:
            issues.append(
                Issue(
                    "ERROR",
                    "MISSING_FIELD",
                    entry_id,
                    field_name,
                    f"Required top-level field '{field_name}' is missing.",
                )
            )

    if not entry_date:
        issues.append(
            Issue("ERROR", "INVALID_DATE", entry_id, "date", f"Invalid ISO date: {entry.get('date')!r}.")
        )

    findings_rel = task.watched_paths.get("reviews")
    findings: dict[str, FindingRecord] = {}
    if findings_rel:
        findings = parse_findings_md(task.workspace_root / findings_rel)

    verdicts = entry.get("verdicts", [])
    if not isinstance(verdicts, list) or not verdicts:
        issues.append(
            Issue("ERROR", "EMPTY_VERDICTS", entry_id, "verdicts", "Must contain at least one verdict track.")
        )
    seen_q: set[str] = set()
    for idx, v in enumerate(verdicts):
        loc = f"verdicts[{idx}]"
        qid = v.get("question_id", "")
        if task.questions and qid not in task.questions:
            issues.append(
                Issue(
                    "ERROR",
                    "UNKNOWN_QUESTION",
                    entry_id,
                    loc,
                    f"Unknown question_id '{qid}'. Configured: {list(task.questions)}.",
                )
            )
        if qid in seen_q:
            issues.append(Issue("ERROR", "DUPLICATE_QUESTION", entry_id, loc, f"Duplicate question_id '{qid}'."))
        seen_q.add(qid)

        ev = _validate_evidence_list(v.get("evidence"), task, entry_id, f"{loc}({qid})", issues)
        summary = str(v.get("summary", ""))
        if not summary.strip():
            issues.append(Issue("ERROR", "EMPTY_SUMMARY", entry_id, f"{loc}({qid})", "Verdict summary cannot be empty."))

        max_ev = task.max_evidence_code(ev)
        if max_ev and task.evidence_levels[max_ev] < task.causal_floor_rank and not v.get("external_claim"):
            bad_verb = _find_forbidden_causal_verb(summary, task)
            if bad_verb:
                issues.append(
                    Issue(
                        "ERROR",
                        "CAUSAL_BELOW_FLOOR",
                        entry_id,
                        f"{loc}({qid})",
                        f"Summary uses causal verb '{bad_verb}' at evidence {max_ev} (below causal floor {task.causal_floor}). State as predicted/simulated.",
                    )
                )

        _validate_review_provenance(
            v.get("review_status", "self-assessed"),
            v.get("review_refs", []),
            findings,
            entry_id,
            f"{loc}({qid})",
            issues,
        )

    is_baseline = bool(entry.get("baseline", False))
    changes = entry.get("changes", {})
    if not is_baseline:
        disconfirming = changes.get("disconfirming", [])
        if not disconfirming and not changes.get("no_disconfirming_reason"):
            issues.append(
                Issue(
                    "ERROR",
                    "MISSING_DISCONFIRMING",
                    entry_id,
                    "changes.disconfirming",
                    "Non-baseline verdicts must list disconfirming evidence first (or explicitly set no_disconfirming_reason).",
                )
            )
        for kind in ("disconfirming", "confirming"):
            for idx, item in enumerate(changes.get(kind, [])):
                loc = f"changes.{kind}[{idx}]"
                ev = _validate_evidence_list(item.get("evidence"), task, entry_id, loc, issues)
                max_ev = task.max_evidence_code(ev)
                text = str(item.get("text", ""))
                if max_ev and task.evidence_levels[max_ev] < task.causal_floor_rank and not item.get("external_claim"):
                    bad_verb = _find_forbidden_causal_verb(text, task)
                    if bad_verb:
                        issues.append(
                            Issue(
                                "ERROR",
                                "CAUSAL_BELOW_FLOOR",
                                entry_id,
                                loc,
                                f"Change item uses causal verb '{bad_verb}' at {max_ev} (below {task.causal_floor}).",
                            )
                        )
                for p in item.get("repo_paths", []):
                    if not (task.workspace_root / p).exists():
                        issues.append(
                            Issue("ERROR", "BROKEN_REPO_PATH", entry_id, loc, f"Cited repo path does not exist: {p}")
                        )
                for c in item.get("commits", []):
                    if not _git_commit_exists(task.workspace_root, c):
                        issues.append(
                            Issue("ERROR", "BROKEN_COMMIT", entry_id, loc, f"Cited git commit not found: {c}")
                        )

    scorecard = entry.get("scorecard", [])
    seen_dim: set[str] = set()
    for idx, row in enumerate(scorecard):
        loc = f"scorecard[{idx}]"
        did = row.get("dimension_id", "")
        if task.dimensions and did not in task.dimensions:
            issues.append(
                Issue(
                    "ERROR",
                    "UNKNOWN_DIMENSION",
                    entry_id,
                    loc,
                    f"Unknown dimension_id '{did}'. Configured: {list(task.dimensions)}.",
                )
            )
        if did in seen_dim:
            issues.append(Issue("ERROR", "DUPLICATE_DIMENSION", entry_id, loc, f"Duplicate dimension_id '{did}'."))
        seen_dim.add(did)
        _validate_evidence_list(row.get("evidence"), task, entry_id, f"{loc}({did})", issues)
        if not str(row.get("score", "")).strip() or not str(row.get("reasoning", "")).strip():
            issues.append(
                Issue("ERROR", "INCOMPLETE_SCORECARD_ROW", entry_id, f"{loc}({did})", "Score and reasoning are required.")
            )

    outside = entry.get("outside_review", {})
    checked_count = int(outside.get("claims_checked_by_outside_human", 0))
    verified_refs = outside.get("verified_finding_ids", [])
    if checked_count > 0 and not verified_refs:
        issues.append(
            Issue(
                "ERROR",
                "UNVERIFIED_OUTSIDE_REVIEW",
                entry_id,
                "outside_review",
                f"claims_checked_by_outside_human={checked_count}, but verified_finding_ids is empty.",
            )
        )
    for fid in verified_refs:
        if findings and fid not in findings:
            issues.append(
                Issue("ERROR", "MISSING_FINDING_REF", entry_id, "outside_review", f"Finding '{fid}' not in findings file.")
            )
        elif findings and findings[fid].provenance == "ai_desk":
            issues.append(
                Issue(
                    "ERROR",
                    "AI_REVIEW_MARKED_HUMAN",
                    entry_id,
                    "outside_review",
                    f"Finding '{fid}' in verified_finding_ids is an AI desk review (E0), not a human check.",
                )
            )
    for fid in outside.get("open_finding_ids", []):
        if findings and fid not in findings:
            issues.append(
                Issue("ERROR", "MISSING_FINDING_REF", entry_id, "outside_review", f"Open finding '{fid}' not in findings file.")
            )

    for idx, s in enumerate(entry.get("strengths", [])):
        loc = f"strengths[{idx}]"
        is_structural = bool(s.get("structural", False))
        ev = _validate_evidence_list(s.get("evidence"), task, entry_id, loc, issues)
        max_ev = task.max_evidence_code(ev)
        rank = task.evidence_levels[max_ev] if max_ev else -1
        if not is_structural and rank < task.strength_floor_rank:
            issues.append(
                Issue(
                    "ERROR",
                    "STRENGTH_BELOW_FLOOR",
                    entry_id,
                    loc,
                    f"Strength backed only by {max_ev} (below strength_floor {task.strength_floor}) and not structural. Move to 'options'.",
                )
            )
        if max_ev and rank < task.causal_floor_rank and not s.get("external_claim"):
            bad_verb = _find_forbidden_causal_verb(str(s.get("text", "")), task)
            if bad_verb:
                issues.append(
                    Issue(
                        "ERROR",
                        "CAUSAL_BELOW_FLOOR",
                        entry_id,
                        loc,
                        f"Strength uses causal verb '{bad_verb}' at {max_ev} (below {task.causal_floor}).",
                    )
                )

    for idx, comp in enumerate(entry.get("competitors", [])):
        loc = f"competitors[{idx}]({comp.get('name', '?')})"
        if not comp.get("name") or not comp.get("source"):
            issues.append(
                Issue("ERROR", "INCOMPLETE_COMPETITOR", entry_id, loc, "Competitor must state name and source.")
            )
        checked_dt = parse_date(comp.get("checked"))
        if check_horizon and checked_dt:
            age = (ref_date - checked_dt).days
            if age > task.max_source_age_days:
                issues.append(
                    Issue(
                        horizon_sev,
                        "STALE_COMPETITOR_CHECK",
                        entry_id,
                        loc,
                        f"Competitor '{comp.get('name')}' last checked {comp.get('checked')} ({age}d > {task.max_source_age_days}d as of {ref_date}).",
                    )
                )

    risks = entry.get("risks", [])
    if not risks:
        issues.append(Issue("ERROR", "EMPTY_RISKS", entry_id, "risks", "Must list top risks with kill criteria."))
    for idx, r in enumerate(risks):
        loc = f"risks[{idx}]"
        if not str(r.get("risk", "")).strip() or not str(r.get("kill_criterion", "")).strip():
            issues.append(
                Issue("ERROR", "INCOMPLETE_KILL_CRITERION", entry_id, loc, "Risk must specify 'risk' and 'kill_criterion'.")
            )
        if not r.get("kill_date") and not r.get("trigger"):
            issues.append(
                Issue(
                    "ERROR",
                    "MISSING_KILL_DATE",
                    entry_id,
                    loc,
                    "Risk must specify an ISO 'kill_date' or explicit 'trigger'.",
                )
            )
        if check_horizon:
            for dfield, code_over, code_soon in (
                ("decision_date", "OVERDUE_DECISION_DATE", "DUE_SOON_DECISION"),
                ("kill_date", "OVERDUE_KILL_DATE", "DUE_SOON_KILL"),
            ):
                dval = parse_date(r.get(dfield))
                if not dval:
                    continue
                delta = (dval - ref_date).days
                if delta < 0:
                    issues.append(
                        Issue(
                            horizon_sev,
                            code_over,
                            entry_id,
                            loc,
                            f"{dfield} {dval} is {-delta}d OVERDUE as of {ref_date}: {r.get('risk')}",
                        )
                    )
                elif delta <= task.due_soon_days:
                    issues.append(
                        Issue(
                            "WARN",
                            code_soon,
                            entry_id,
                            loc,
                            f"{dfield} {dval} is due in {delta}d (as of {ref_date}): {r.get('risk')}",
                        )
                    )

    if not entry.get("next_tests"):
        issues.append(
            Issue("ERROR", "EMPTY_NEXT_TESTS", entry_id, "next_tests", "Must propose at least one next cheapest test.")
        )

    for idx, src in enumerate(entry.get("sources", [])):
        loc = f"sources[{idx}]"
        if not src.get("label"):
            issues.append(Issue("ERROR", "INCOMPLETE_SOURCE", entry_id, loc, "Source requires a 'label'."))
        if not src.get("date") and not src.get("note"):
            issues.append(
                Issue("ERROR", "UNDATED_SOURCE", entry_id, loc, "Source must carry a 'date' (or 'known, not re-checked' note).")
            )
        for p in src.get("repo_paths", []):
            if not (task.workspace_root / p).exists():
                issues.append(
                    Issue("ERROR", "BROKEN_REPO_PATH", entry_id, loc, f"Source repo_path does not exist: {p}")
                )
        if src.get("commit") and not _git_commit_exists(task.workspace_root, str(src["commit"])):
            issues.append(
                Issue("ERROR", "BROKEN_COMMIT", entry_id, loc, f"Source git commit not found: {src['commit']}")
            )

    return issues


def check_ledger(
    entries: list[dict[str, Any]],
    task: TaskConfig,
    entry_filter: str | None = None,
    as_of: dt.date | None = None,
    strict_dates: bool = False,
) -> list[Issue]:
    if not entries:
        return [Issue("WARN", "EMPTY_LEDGER", "-", str(task.ledger_path), "Ledger has no verdict entries yet.")]

    selected = entries
    if entry_filter and entry_filter != "all":
        if entry_filter == "latest":
            selected = [entries[-1]]
        else:
            selected = [e for e in entries if e.get("id") == entry_filter or e.get("date") == entry_filter]
            if not selected:
                return [Issue("ERROR", "ENTRY_NOT_FOUND", entry_filter, "ledger", f"No entry matching '{entry_filter}'.")]

    all_issues: list[Issue] = []
    latest_id = entries[-1].get("id")
    for e in selected:
        is_latest = e.get("id") == latest_id
        all_issues.extend(
            check_entry(
                e,
                task,
                as_of=as_of if is_latest else parse_date(e.get("date")),
                strict_dates=strict_dates if is_latest else False,
                check_horizon=is_latest,
            )
        )
    return all_issues

