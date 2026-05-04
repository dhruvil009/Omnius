import json
import unittest

from omnius.planner import (
    build_planner_prompt,
    load_manifest_schema,
    load_planner_prompt_template,
    parse_planner_response,
    validate_manifest,
)


class PlannerTests(unittest.TestCase):
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
