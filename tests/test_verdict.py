"""Unit, preset, and seeded-defect tests for the standalone `verdict` system."""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verdict.checker import check_entry
from verdict.cli import main as cli_main
from verdict.differ import diff_entries, scan_workspace_since
from verdict.markdown import render_verdict_markdown
from verdict.schema import load_ledger, load_task


def _make_fixture_workspace(tmp_dir: Path, preset: str = "systems") -> tuple[Path, dict]:
    ws = tmp_dir / "repo"
    (ws / "docs" / "notebook").mkdir(parents=True)
    (ws / "verdicts").mkdir(parents=True)

    (ws / "docs" / "notebook" / "2026-10-08-sim.md").write_text("# Sim\n", encoding="utf-8")
    (ws / "docs" / "notebook" / "2026-10-16-new.md").write_text("# New\n", encoding="utf-8")
    (ws / "ROADMAP.md").write_text("# Roadmap\n", encoding="utf-8")

    cli_main(["init", str(ws / "verdicts"), "--name", "TestProj", "--preset", preset, "--with-findings"])
    (ws / "reviews" / "findings.md").write_text(
        "# Review findings\n\n"
        "## Summary\n\n"
        "| ID | Area | Finding | Type | Severity | Reviewer | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| F1 | Stats | Multiple testing uncorrected | risk | high | AI desk review | fixed, unchecked |\n"
        "| F2 | Security | Threat model open | risk | high | Seat 6 | open |\n\n"
        "## F1. Multiple testing\n\n"
        "- **Proposed response:** To be confirmed by external statistician.\n",
        encoding="utf-8",
    )

    task_file = ws / "verdicts" / "task.json"
    valid_entry = {
        "id": "2026-10-10",
        "date": "2026-10-10",
        "baseline": False,
        "previous_id": "2026-10-01",
        "verdicts": [
            {
                "question_id": "technical_lever",
                "status": "Go with conditions",
                "summary": "Predicted to use 18% less capacity against B0; saves nothing without scale-down.",
                "evidence": ["E2"],
                "review_status": "self-assessed",
                "review_refs": [],
            }
        ],
        "changes": {
            "disconfirming": [
                {
                    "text": "Edge against B1 disappeared when B1 prefers newer instances.",
                    "evidence": ["E2"],
                    "repo_paths": ["docs/notebook/2026-10-08-sim.md"],
                }
            ],
            "confirming": [
                {
                    "text": "Zero SLO violations in trace replay.",
                    "evidence": ["E2"],
                    "repo_paths": ["docs/notebook/2026-10-08-sim.md"],
                }
            ],
        },
        "killed_or_failed": [
            {
                "item": "Lookahead planner",
                "outcome": "dropped",
                "evidence": ["E2"],
                "rule_and_finding": "Under 1 percentage point over reactive timer.",
            }
        ],
        "scorecard": [
            {
                "dimension_id": "impact_vs_b0",
                "score": "Promising",
                "evidence": ["E2"],
                "change": "Same",
                "reasoning": "Predicted 18% below B0 where idle nodes remain allocated.",
            },
            {
                "dimension_id": "impact_vs_b1",
                "score": "Weak",
                "evidence": ["E2"],
                "change": "Weaker",
                "reasoning": "Within 1–2% of tuned B1.",
            },
        ],
        "outside_review": {
            "claims_checked_by_outside_human": 0,
            "verified_finding_ids": [],
            "open_finding_ids": ["F2"],
            "max_finding_number_seen": 1,
            "summary": "No outside human review yet.",
            "notes": [],
        },
        "strengths": [
            {
                "text": "Pre-stated rules killed our own feature openly.",
                "evidence": ["E2"],
                "structural": True,
            }
        ],
        "options": ["Live controller", "Partner pilot"],
        "competitors": [
            {
                "name": "IncumbentCo",
                "does": "Autoscaling",
                "target": "Cloud",
                "lever": "Reactive",
                "measured": "No",
                "source": "incumbent.example/docs",
                "checked": "2026-10-08",
            }
        ],
        "risks": [
            {
                "risk": "No pilot access",
                "kill_criterion": "No partner agrees by kill date",
                "decision_date": "2026-10-25",
                "kill_date": "2027-01-31",
            }
        ],
        "next_tests": [{"title": "Partner call", "detail": "Ask for exported trace."}],
        "contradictions": [{"target": "ROADMAP.md", "proposal": "Gate milestone on pilot access."}],
        "sources": [
            {
                "label": "Sim notebook",
                "date": "2026-10-08",
                "repo_paths": ["docs/notebook/2026-10-08-sim.md"],
            }
        ],
    }
    return task_file, valid_entry


class VerdictCoreTests(unittest.TestCase):
    def test_all_presets_initialize_valid_tasks(self) -> None:
        for preset in ("general", "systems", "ml", "saas", "research"):
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / preset
                self.assertEqual(cli_main(["init", str(target), "--preset", preset, "--name", f"Proj-{preset}"]), 0)
                task = load_task(str(target / "task.json"))
                self.assertEqual(task.project, f"Proj-{preset}")
                self.assertIn("E0", task.evidence_levels)

    def test_valid_entry_passes_cleanly_on_verdict_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            task = load_task(str(task_file))
            self.assertEqual(check_entry(entry, task, as_of=dt.date(2026, 10, 10)), [])

    def test_seeded_defect_causal_claim_below_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            task = load_task(str(task_file))
            bad = copy.deepcopy(entry)
            bad["verdicts"][0]["summary"] = "Our proxy reduces tail latency by 24% in production."
            codes = {i.code for i in check_entry(bad, task, as_of=dt.date(2026, 10, 10)) if i.severity == "ERROR"}
            self.assertIn("CAUSAL_BELOW_FLOOR", codes)

    def test_seeded_defect_strength_below_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            task = load_task(str(task_file))
            bad = copy.deepcopy(entry)
            bad["strengths"].append({"text": "Unbuilt adaptive scheduler.", "evidence": ["E0"], "structural": False})
            codes = {i.code for i in check_entry(bad, task, as_of=dt.date(2026, 10, 10)) if i.severity == "ERROR"}
            self.assertIn("STRENGTH_BELOW_FLOOR", codes)

    def test_seeded_defect_ai_desk_review_marked_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            task = load_task(str(task_file))
            bad = copy.deepcopy(entry)
            bad["verdicts"][0]["review_status"] = "reviewed"
            bad["verdicts"][0]["review_refs"] = ["F1"]
            codes = {i.code for i in check_entry(bad, task, as_of=dt.date(2026, 10, 10)) if i.severity == "ERROR"}
            self.assertIn("AI_REVIEW_MARKED_HUMAN", codes)

    def test_seeded_defect_missing_disconfirming_and_broken_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            task = load_task(str(task_file))
            bad = copy.deepcopy(entry)
            bad["changes"]["disconfirming"] = []
            bad["sources"][0]["repo_paths"] = ["docs/notebook/missing.md"]
            codes = {i.code for i in check_entry(bad, task, as_of=dt.date(2026, 10, 10)) if i.severity == "ERROR"}
            self.assertIn("MISSING_DISCONFIRMING", codes)
            self.assertIn("BROKEN_REPO_PATH", codes)

    def test_seeded_defect_horizon_overdue_and_stale_competitor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            task = load_task(str(task_file))
            warns = {
                i.code
                for i in check_entry(entry, task, as_of=dt.date(2026, 11, 15), strict_dates=False)
                if i.severity == "WARN"
            }
            self.assertIn("OVERDUE_DECISION_DATE", warns)
            self.assertIn("STALE_COMPETITOR_CHECK", warns)

    def test_end_to_end_record_diff_render_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_file, entry = _make_fixture_workspace(Path(tmp))
            e1 = copy.deepcopy(entry)
            e1["id"] = "2026-10-08"
            e1["date"] = "2026-10-08"
            e1["baseline"] = True
            e1["previous_id"] = None
            e1["verdicts"][0]["status"] = "Go"
            e1["verdicts"][0]["evidence"] = ["E1"]

            p1, p2 = Path(tmp) / "e1.json", Path(tmp) / "e2.json"
            p1.write_text(json.dumps(e1), encoding="utf-8")
            p2.write_text(json.dumps(entry), encoding="utf-8")

            self.assertEqual(cli_main(["record", str(p1), "--task", str(task_file), "--write-md"]), 0)
            self.assertEqual(cli_main(["record", str(p2), "--task", str(task_file), "--write-md"]), 0)

            task = load_task(str(task_file))
            entries = load_ledger(task.ledger_path)
            self.assertEqual(len(entries), 2)

            d = diff_entries(entries[0], entries[1], task)
            self.assertEqual(d["verdict_shifts"][0]["to_status"], "Go with conditions")

            ws_scan = scan_workspace_since(entries[-1], task, as_of=dt.date(2026, 10, 30))
            self.assertIn("docs/notebook/2026-10-16-new.md", ws_scan["uncited_result_files"])
            self.assertEqual(ws_scan["findings"]["human_confirmed_count"], 0)

            md = render_verdict_markdown(entries[-1], task)
            self.assertIn("## 1. The verdict", md)

            html_out = Path(tmp) / "report.html"
            self.assertEqual(
                cli_main(["report", "--task", str(task_file), "--out", str(html_out), "--as-of", "2026-10-30"]),
                0,
            )
            self.assertIn("Lookahead planner", html_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

