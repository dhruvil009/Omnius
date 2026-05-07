import tempfile
import unittest
from pathlib import Path

from omnius.costs import SessionCostRecord, update_aggregate_cost_ledger, write_session_cost_record
from omnius.runners.base import UsageStats


class CostLedgerTests(unittest.TestCase):
    def test_write_session_cost_record_renders_markdown_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            costs_dir = Path(tmp)
            record_path = write_session_cost_record(
                costs_dir=costs_dir,
                session=SessionCostRecord(
                    file_stem="2026-05-06_2100_O00001",
                    session_name="worker O00001",
                    started_at="2026-05-06T21:00:00-07:00",
                    ended_at="2026-05-06T21:07:00-07:00",
                    status="SUCCESS",
                    task_id="O00001",
                    task_type="implementation",
                    complexity="small",
                    usage=UsageStats(
                        cost_usd=0.47,
                        input_tokens=1200,
                        output_tokens=300,
                        cache_read_tokens=40,
                        turns=7,
                        model="fake-model",
                    ),
                ),
            )

            rendered = record_path.read_text(encoding="utf-8")

        self.assertEqual(record_path.name, "2026-05-06_2100_O00001.md")
        self.assertIn("# Omnius Session Cost", rendered)
        self.assertIn("| Cost | $0.47 |", rendered)
        self.assertIn("| Turns | 7 |", rendered)
        self.assertIn("worker O00001", rendered)

    def test_update_aggregate_cost_ledger_appends_run_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            costs_dir = Path(tmp)
            ledger_path = update_aggregate_cost_ledger(
                costs_dir=costs_dir,
                run_date="2026-05-06",
                total_tasks=5,
                success_count=3,
                total_cost_usd=0.47,
                notes="circuit breaker tripped",
            )

            rendered = ledger_path.read_text(encoding="utf-8")

        self.assertEqual(ledger_path.name, "omnius_cost.md")
        self.assertIn("# Omnius Cost Ledger", rendered)
        self.assertIn("_Running total since 2026-05-06: **$0.47**_", rendered)
        self.assertIn("## May 2026", rendered)
        self.assertIn("| 2026-05-06 |   5   |   3     | $0.47   | circuit breaker tripped |", rendered)
