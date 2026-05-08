import plistlib
import unittest
from pathlib import Path

from omnius.scheduler import (
    build_omnius_run_command,
    render_cron_block,
    render_launchd_plist,
    replace_managed_cron_block,
    translate_cron_to_launchd,
)


class CronSchedulerTests(unittest.TestCase):
    def test_render_cron_block_uses_python_module_command(self) -> None:
        block = render_cron_block("0 21 * * 0-4", build_omnius_run_command("/tmp/python3"))
        self.assertIn("# BEGIN OMNIUS", block)
        self.assertIn("/tmp/python3 -m omnius run", block)
        self.assertIn("0 21 * * 0-4", block)

    def test_replace_managed_cron_block_preserves_unmanaged_lines(self) -> None:
        updated = replace_managed_cron_block(
            "# existing\nMAILTO=user@example.com\n",
            "0 21 * * 0-4",
            build_omnius_run_command("/tmp/python3"),
        )
        self.assertIn("MAILTO=user@example.com", updated)
        self.assertEqual(updated.count("# BEGIN OMNIUS"), 1)
        self.assertEqual(updated.count("# END OMNIUS"), 1)


class LaunchdSchedulerTests(unittest.TestCase):
    def test_translate_supported_cron_to_launchd_intervals(self) -> None:
        intervals = translate_cron_to_launchd("0 21 * * 0-4")
        self.assertEqual(len(intervals), 5)
        self.assertEqual(intervals[0], {"Weekday": 0, "Hour": 21, "Minute": 0})
        self.assertEqual(intervals[-1], {"Weekday": 4, "Hour": 21, "Minute": 0})

    def test_translate_launchd_rejects_unsupported_cron(self) -> None:
        with self.assertRaisesRegex(ValueError, "launchd"):
            translate_cron_to_launchd("*/15 21 * * 0-4")

    def test_render_launchd_plist_contains_expected_program_arguments(self) -> None:
        command = build_omnius_run_command("/tmp/python3")
        payload = plistlib.loads(
            render_launchd_plist(
                schedule="0 21 * * 0-4",
                command=command,
                home=Path("/tmp/.omnius"),
            )
        )
        self.assertEqual(payload["Label"], "dev.omnius.pipeline")
        self.assertEqual(payload["ProgramArguments"], command)
        self.assertEqual(payload["StandardOutPath"], "/tmp/.omnius/logs/omnius-launchd.log")
