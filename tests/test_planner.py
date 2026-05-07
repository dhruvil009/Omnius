import json
import importlib.resources
import unittest
from unittest.mock import patch

from omnius.planner import (
    build_manifest_tasks,
    build_planner_prompt,
    choose_planner_response,
    load_manifest_schema,
    load_planner_prompt_template,
    parse_planner_response,
    validate_manifest,
)
from omnius.tasks import LocalTaskEntry, RecurringTaskEntry


class PlannerTests(unittest.TestCase):
    def test_packaged_planner_resources_use_importlib_resources(self) -> None:
        with patch("importlib.resources.files", wraps=importlib.resources.files) as mock_files:
            load_planner_prompt_template()

        self.assertGreaterEqual(mock_files.call_count, 1)

    def test_packaged_planner_resources_load(self) -> None:
        template = load_planner_prompt_template()
        schema = load_manifest_schema()

        self.assertIn("Omnius planner", template)
        self.assertEqual(schema["type"], "object")
        self.assertIn("run_date", schema["required"])

    def test_build_planner_prompt_includes_labeled_sections(self) -> None:
        prompt = build_planner_prompt(
            template="TEMPLATE",
            run_date="2026-05-03",
            journal_dir="/tmp/journal",
            repos_table="example -> /tmp/example",
            local_tasks="LOCAL",
            recurring_tasks="<none>",
            github_issues="<none>",
            pr_review_comments="<none>",
            pending_approval="<none>",
        )

        self.assertIn("RUN_DATE\n2026-05-03", prompt)
        self.assertIn("LOCAL_TASKS\nLOCAL", prompt)
        self.assertIn("RECURRING_TASKS\n<none>", prompt)
        self.assertIn("GITHUB_ISSUES\n<none>", prompt)
        self.assertIn("PR_REVIEW_COMMENTS\n<none>", prompt)
        self.assertIn("PENDING_APPROVAL\n<none>", prompt)

    def test_parse_planner_response_returns_manifest_dict(self) -> None:
        manifest = parse_planner_response(
            json.dumps(
                {
                    "run_date": "2026-05-03",
                    "journal_dir": "/tmp/journal",
                    "summary": "0 tasks",
                    "tasks": [],
                    "skipped": [],
                    "notes": "stub",
                }
            )
        )

        self.assertEqual(manifest["summary"], "0 tasks")
        self.assertEqual(manifest["tasks"], [])

    def test_choose_planner_response_prefers_valid_planner_manifest(self) -> None:
        planner_response = json.dumps(
            {
                "run_date": "2026-05-06",
                "journal_dir": "/tmp/journal",
                "summary": "planner manifest",
                "tasks": [],
                "skipped": [],
                "notes": "from planner",
            }
        )

        chosen = choose_planner_response(
            planner_output=planner_response,
            fallback_manifest_response='{"summary":"fallback"}\n',
        )

        self.assertEqual(chosen, planner_response)

    def test_choose_planner_response_falls_back_when_planner_output_is_invalid(self) -> None:
        fallback_manifest = '{"run_date":"2026-05-06","journal_dir":"/tmp/journal","summary":"fallback","tasks":[],"skipped":[],"notes":"stub"}\n'

        chosen = choose_planner_response(
            planner_output="not valid json",
            fallback_manifest_response=fallback_manifest,
        )

        self.assertEqual(chosen, fallback_manifest)

    def test_validate_manifest_accepts_minimal_milestone_one_contract(self) -> None:
        manifest = {
            "run_date": "2026-05-03",
            "journal_dir": "/tmp/journal",
            "summary": "0 tasks",
            "tasks": [],
            "skipped": [],
            "notes": "stub",
        }

        validate_manifest(manifest)

    def test_validate_manifest_accepts_typed_implementation_task(self) -> None:
        manifest = {
            "run_date": "2026-05-05",
            "journal_dir": "/tmp/journal",
            "summary": "1 task planned",
            "tasks": [
                {
                    "id": "O00001",
                    "title": "Add sample",
                    "type": "implementation",
                    "repo_slug": "example",
                    "source_ref": "tasks/O00001_add_sample.md",
                    "filename": "O00001_add_sample.md",
                    "max_time_minutes": 120,
                    "complexity": "small",
                }
            ],
            "skipped": [],
            "notes": "stub",
        }

        validate_manifest(manifest)

    def test_validate_manifest_rejects_missing_or_invalid_required_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary"):
            validate_manifest(
                {
                    "run_date": "2026-05-03",
                    "journal_dir": "/tmp/journal",
                    "tasks": [],
                    "skipped": [],
                    "notes": "stub",
                }
            )

    def test_build_manifest_tasks_combines_local_and_due_recurring_entries(self) -> None:
        manifest_tasks = build_manifest_tasks(
            local_entries=[
                LocalTaskEntry(
                    task_id="O00001",
                    filename="O00001_add_sample.md",
                    body="---\ntitle: Add sample\nrepo: example\n---\nBody\n",
                )
            ],
            recurring_entries=[
                RecurringTaskEntry(
                    task_id="R00001",
                    filename="R00001_daily_cleanup.md",
                    title="Daily cleanup",
                    repo_slug="example",
                    schedule="daily",
                    task_type="maintenance",
                    complexity="medium",
                    max_time_minutes=45,
                    retry_on_failure="next_run",
                    only_if_last_succeeded=False,
                    body="---\ntitle: Daily cleanup\nrepo: example\nschedule: daily\n---\nRecurring body\n",
                )
            ],
            default_task_budget_minutes=120,
        )

        self.assertEqual(
            manifest_tasks,
            [
                {
                    "id": "O00001",
                    "title": "Add sample",
                    "type": "implementation",
                    "repo_slug": "example",
                    "source_ref": "tasks/O00001_add_sample.md",
                    "filename": "O00001_add_sample.md",
                    "max_time_minutes": 120,
                    "complexity": "small",
                },
                {
                    "id": "R00001",
                    "title": "Daily cleanup",
                    "type": "maintenance",
                    "repo_slug": "example",
                    "source_ref": "tasks/recurring/R00001_daily_cleanup.md",
                    "filename": "R00001_daily_cleanup.md",
                    "max_time_minutes": 45,
                    "complexity": "medium",
                },
            ],
        )

        with self.assertRaisesRegex(ValueError, "max_time_minutes"):
            validate_manifest(
                {
                    "run_date": "2026-05-05",
                    "journal_dir": "/tmp/journal",
                    "summary": "1 task planned",
                    "tasks": [
                        {
                            "id": "O00001",
                            "title": "Add sample",
                            "type": "implementation",
                            "repo_slug": "example",
                            "source_ref": "tasks/O00001_add_sample.md",
                            "filename": "O00001_add_sample.md",
                            "max_time_minutes": "120",
                            "complexity": "small",
                        }
                    ],
                    "skipped": [],
                    "notes": "stub",
                }
            )

        with self.assertRaisesRegex(ValueError, "tasks"):
            validate_manifest(
                {
                    "run_date": "2026-05-03",
                    "journal_dir": "/tmp/journal",
                    "summary": "0 tasks",
                    "tasks": {},
                    "skipped": [],
                    "notes": "stub",
                }
            )
