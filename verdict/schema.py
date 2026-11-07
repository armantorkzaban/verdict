"""Task configuration, structured verdict ledger loading, and review findings parsing."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def parse_date(value: str | None) -> dt.date | None:
    """Parse an ISO YYYY-MM-DD date string if valid, else return None."""
    if not value:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})$", value.strip())
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def normalize_verdict_kind(status: str) -> str:
    low = status.strip().lower().rstrip(".")
    if low.startswith("go with condition") or "planned outcome" in low:
        return "conditions"
    if low.startswith("go"):
        return "go"
    if low.startswith("not yet") or low.startswith("hold") or low.startswith("unchanged"):
        return "hold"
    if low.startswith("pivot"):
        return "pivot"
    if low.startswith("stop"):
        return "stop"
    return "other"


def score_to_number(score: str, custom_scale: dict[str, float] | None = None) -> float:
    """Map qualitative scorecard labels onto a [1.0, 4.0] numeric bar scale."""
    low = score.strip().lower().rstrip(".")
    if custom_scale and low in custom_scale:
        return float(custom_scale[low])
    if low == "strong":
        return 4.0
    if low.startswith("strong"):
        return 3.5
    if low == "promising":
        return 3.0
    if low.startswith("promising") and ("thin" in low or "contested" in low):
        return 2.4
    if "promising" in low and "weak" in low:
        return 2.2
    if low.startswith("promising") or "promising" in low or "first" in low or "supported" in low:
        return 2.6
    if low.startswith("thin"):
        return 2.2
    if low == "unproven":
        return 1.5
    if "unproven" in low and "weak" in low:
        return 1.2
    if "unproven" in low:
        return 1.5
    if low.startswith("weak") and "scale" in low:
        return 1.2
    if low.startswith("weak"):
        return 1.0
    return 2.0


@dataclass
class TaskConfig:
    path: Path
    raw: dict[str, Any]
    project: str
    summary: str
    workspace_root: Path
    ledger_path: Path
    verdicts_dir: Path
    evidence_levels: dict[str, int]
    evidence_descriptions: dict[str, str]
    causal_floor: str
    strength_floor: str
    forbidden_below_causal: list[str]
    allowed_causal_phrases: list[str]
    questions: dict[str, dict[str, Any]]
    dimensions: dict[str, dict[str, Any]]
    baselines: list[dict[str, Any]]
    watched_paths: dict[str, Any]
    literature: dict[str, Any]
    score_scale: dict[str, float]
    max_source_age_days: int
    due_soon_days: int

    @property
    def causal_floor_rank(self) -> int:
        return self.evidence_levels[self.causal_floor]

    @property
    def strength_floor_rank(self) -> int:
        return self.evidence_levels[self.strength_floor]

    def evidence_rank(self, code: str) -> int | None:
        return self.evidence_levels.get(code.strip())

    def max_evidence_code(self, codes: list[str]) -> str | None:
        valid = [c for c in codes if c in self.evidence_levels]
        if not valid:
            return None
        return max(valid, key=lambda c: self.evidence_levels[c])


def resolve_task_path(task_arg: str | None = None, cwd: Path | None = None) -> Path:
    """Locate task.json from explicit path or standard workspace locations."""
    base = (cwd or Path.cwd()).resolve()
    if task_arg:
        p = Path(task_arg).expanduser()
        if not p.is_absolute():
            p = (base / p).resolve()
        if p.is_dir():
            p = p / "task.json"
        if not p.exists():
            raise FileNotFoundError(f"Task file not found: {p}")
        return p

    candidates = [
        base / "task.json",
        base / "verdicts" / "task.json",
        base / "private" / "verdicts" / "task.json",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError(
        "No task.json found. Pass --task <path/to/task.json> or run 'verdict init <dir>'."
    )


def load_task(task_arg: str | None = None, cwd: Path | None = None) -> TaskConfig:
    path = resolve_task_path(task_arg, cwd=cwd)
    raw = json.loads(path.read_text(encoding="utf-8"))
    task_dir = path.parent

    ws_rel = raw.get("workspace_root", ".")
    workspace_root = (task_dir / ws_rel).resolve()
    verdicts_dir = (task_dir / raw.get("verdicts_dir", ".")).resolve()
    ledger_path = (task_dir / raw.get("ledger_file", "ledger.jsonl")).resolve()

    ladder_list = raw.get("evidence_ladder", [])
    if not ladder_list:
        raise ValueError(f"{path}: 'evidence_ladder' must define at least one level")
    evidence_levels: dict[str, int] = {}
    evidence_descriptions: dict[str, str] = {}
    for idx, item in enumerate(ladder_list):
        code = item["id"]
        evidence_levels[code] = int(item.get("rank", idx))
        evidence_descriptions[code] = item.get("description", "")

    rules = raw.get("rules", {})
    causal_floor = rules.get("causal_floor", list(evidence_levels.keys())[-1])
    strength_floor = rules.get("strength_floor", list(evidence_levels.keys())[0])
    if causal_floor not in evidence_levels:
        raise ValueError(f"{path}: causal_floor '{causal_floor}' not in evidence_ladder")
    if strength_floor not in evidence_levels:
        raise ValueError(f"{path}: strength_floor '{strength_floor}' not in evidence_ladder")

    questions = {q["id"]: q for q in raw.get("questions", [])}
    dimensions = {d["id"]: d for d in raw.get("dimensions", [])}
    score_scale = {k.lower(): float(v) for k, v in raw.get("score_scale", {}).items()}

    return TaskConfig(
        path=path,
        raw=raw,
        project=raw.get("project", task_dir.name),
        summary=raw.get("summary", ""),
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        verdicts_dir=verdicts_dir,
        evidence_levels=evidence_levels,
        evidence_descriptions=evidence_descriptions,
        causal_floor=causal_floor,
        strength_floor=strength_floor,
        forbidden_below_causal=rules.get(
            "forbidden_causal_verbs",
            ["saves", "saved", "reduces", "reduced", "cuts", "delivers"],
        ),
        allowed_causal_phrases=rules.get(
            "allowed_causal_phrases",
            [
                "saves nothing",
                "saves no ",
                "never \"saves\"",
                "never 'saves'",
                "never saves",
                "would have saved",
                "measured savings",
                "verified savings",
            ],
        ),
        questions=questions,
        dimensions=dimensions,
        baselines=raw.get("baselines", []),
        watched_paths=raw.get("watched_paths", {}),
        literature=raw.get("literature", {}),
        score_scale=score_scale,
        max_source_age_days=int(rules.get("max_source_age_days", 30)),
        due_soon_days=int(rules.get("due_soon_days", 14)),
    )


def load_ledger(ledger_path: Path) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for lineno, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{ledger_path}:{lineno}: invalid JSON: {exc}") from exc
        entries.append(obj)
    return entries


def save_ledger_entry(ledger_path: Path, entry: dict[str, Any]) -> str:
    """Upsert by entry['id'] in chronological order, preserving existing records."""
    entries = load_ledger(ledger_path)
    entry_id = entry["id"]
    replaced = False
    for idx, existing in enumerate(entries):
        if existing.get("id") == entry_id:
            entries[idx] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    entries.sort(key=lambda e: (e.get("date", ""), e.get("id", "")))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    ledger_path.write_text(content, encoding="utf-8")
    return "updated" if replaced else "appended"


@dataclass
class FindingRecord:
    fid: str
    area: str
    title: str
    ftype: str
    severity: str
    confirms: str
    status: str
    provenance: str = "unknown"  # "ai_desk" | "human" | "unknown"


def parse_findings_md(findings_path: Path) -> dict[str, FindingRecord]:
    """Parse any Markdown review-findings summary table (5 to 7 columns) and reviewer provenance."""
    if not findings_path.exists():
        return {}
    text = findings_path.read_text(encoding="utf-8")
    records: dict[str, FindingRecord] = {}

    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or not s.endswith("|"):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 4 or not re.match(r"^F\d+$", cols[0]):
            continue
        fid = cols[0]
        if len(cols) >= 7:
            area, title, ftype, sev, confirms, status = cols[1:7]
        elif len(cols) == 6:
            area, title, sev, confirms, status = cols[1:6]
            ftype = "finding"
        elif len(cols) == 5:
            area, title, sev, status = cols[1:5]
            ftype, confirms = "finding", ""
        else:
            area, title, status = "", cols[1], cols[-1]
            ftype, sev, confirms = "finding", "medium", ""

        prov = "unknown"
        if any(tok in confirms.lower() for tok in ("ai", "claude", "gpt", "llm", "desk")):
            prov = "ai_desk"
        records[fid] = FindingRecord(
            fid=fid,
            area=area,
            title=title,
            ftype=ftype,
            severity=sev,
            confirms=confirms,
            status=status,
            provenance=prov,
        )

    low = text.lower()
    if "all fifteen come from a desk pre-review" in low and "not yet confirmed by any human" in low:
        for i in range(1, 16):
            key = f"F{i}"
            if key in records:
                records[key].provenance = "ai_desk"

    for m in re.finditer(
        r"f(\d+)[–-]f(\d+)\s+come from a [^\n]*desk review[^\n]*not (?:yet )?confirmed by any human",
        low,
    ):
        for i in range(int(m.group(1)), int(m.group(2)) + 1):
            key = f"F{i}"
            if key in records:
                records[key].provenance = "ai_desk"

    section_splits = re.split(r"(?m)^##\s+(F\d+)\b", text)
    for idx in range(1, len(section_splits) - 1, 2):
        fid = section_splits[idx].strip()
        body = section_splits[idx + 1]
        if fid not in records:
            continue
        if re.search(
            r"(?im)^-\s*\*\*(?:confirmed|reviewed)\b[^*]*\bby\s+(?!ai\b|claude\b|gpt\b|llm\b|none\b)[a-z]",
            body,
        ):
            records[fid].provenance = "human"
        elif records[fid].provenance == "unknown":
            if "unchecked" in records[fid].status.lower() or "desk review" in body.lower():
                records[fid].provenance = "ai_desk"

    return records

