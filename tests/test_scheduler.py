import plistlib
import unittest
from pathlib import Path

from omnius.scheduler import (
    build_omnius_run_command,
    build_scheduler_environment,
    render_cron_block,
    render_launchd_plist,
    replace_managed_cron_block,
    translate_cron_to_launchd,
)


class CronSchedulerTests(unittest.TestCase):
    def test_build_scheduler_environment_includes_workspace_timezone_scheduled_marker_and_path(self) -> None:
        env = build_scheduler_environment(
            workspace_home=Path("/tmp/.omnius"),
            timezone="America/Los_Angeles",
            path_env="/usr/local/bin:/usr/bin:/bin",
        )

        self.assertEqual(
            env,
            {
                "OMNIUS_HOME": "/tmp/.omnius",
                "OMNIUS_SCHEDULED": "1",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "TZ": "America/Los_Angeles",
            },
        )

    def test_render_cron_block_uses_python_module_command(self) -> None:
        block = render_cron_block("0 21 * * 0-4", build_omnius_run_command("/tmp/python3"))
        self.assertIn("# BEGIN OMNIUS", block)
        self.assertIn("/tmp/python3 -m omnius run", block)
        self.assertIn("0 21 * * 0-4", block)

    def test_render_cron_block_injects_scheduler_environment_for_command(self) -> None:
        block = render_cron_block(
            "0 21 * * 0-4",
            build_omnius_run_command("/tmp/python3"),
            workspace_home=Path("/tmp/.omnius"),
            timezone="America/Los_Angeles",
            path_env="/usr/local/bin:/usr/bin:/bin",
        )

        self.assertIn("cd /tmp/.omnius && env ", block)
        self.assertIn("OMNIUS_HOME=/tmp/.omnius", block)
        self.assertIn("OMNIUS_SCHEDULED=1", block)
        self.assertIn("TZ=America/Los_Angeles", block)
        self.assertIn("PATH=/usr/local/bin:/usr/bin:/bin", block)
        self.assertIn(">> /tmp/.omnius/logs/omnius-cron.log 2>&1", block)

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
                timezone="America/Los_Angeles",
                path_env="/usr/local/bin:/usr/bin:/bin",
            )
        )
        self.assertEqual(payload["Label"], "dev.omnius.pipeline")
        self.assertEqual(payload["ProgramArguments"], command)
        self.assertEqual(payload["StandardOutPath"], "/tmp/.omnius/logs/omnius-launchd.log")
        self.assertEqual(
            payload["EnvironmentVariables"],
            {
                "OMNIUS_HOME": "/tmp/.omnius",
                "OMNIUS_SCHEDULED": "1",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "TZ": "America/Los_Angeles",
            },
        )
