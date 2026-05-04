import tempfile
import textwrap
import unittest
from pathlib import Path

from omnius.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_reads_minimal_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_text(
                textwrap.dedent(
                    """
                    [global]
                    timezone = "America/Los_Angeles"
                    pipeline_cron = "0 21 * * 0-4"
                    pipeline_budget_minutes = 540
                    default_task_budget_minutes = 120
                    max_consecutive_failures = 3
                    notification_backend = "none"

                    [runner]
                    default = "codex"

                    [runners.codex]
                    enabled = true

                    [capabilities]
                    brainstorm = "auto"
                    review_diff = "auto"
                    autonomous_testing = "auto"
                    second_opinion = "auto"

                    [[repos]]
                    slug = "example"
                    path = "~/code/example"
                    branch = "main"
                    role = "author"
                    labels = ["omnius"]
                    """
                ).strip()
            )

            config = load_config(home / "omnius.toml")

            self.assertEqual(config.runner.default, "codex")
            self.assertEqual(config.repos[0].slug, "example")

    def test_load_config_rejects_unknown_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_text(
                (
                    "[global]\n"
                    "timezone = 'America/Los_Angeles'\n"
                    "pipeline_cron = '0 21 * * 0-4'\n"
                    "pipeline_budget_minutes = 540\n"
                    "default_task_budget_minutes = 120\n"
                    "max_consecutive_failures = 3\n"
                    "notification_backend = 'none'\n"
                    "[runner]\n"
                    "default = 'bogus'\n"
                    "[runners.bogus]\n"
                    "enabled = true\n"
                    "[capabilities]\n"
                    "brainstorm = 'auto'\n"
                    "review_diff = 'auto'\n"
                    "autonomous_testing = 'auto'\n"
                    "second_opinion = 'auto'\n"
                )
            )

            with self.assertRaises(ConfigError):
                load_config(home / "omnius.toml")

    def test_load_config_accepts_claude_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_text(
                textwrap.dedent(
                    """
                    [global]
                    timezone = "America/Los_Angeles"
                    pipeline_cron = "0 21 * * 0-4"
                    pipeline_budget_minutes = 540
                    default_task_budget_minutes = 120
                    max_consecutive_failures = 3
                    notification_backend = "none"

                    [runner]
                    default = "claude"

                    [runners.claude]
                    enabled = true

                    [capabilities]
                    brainstorm = "auto"
                    review_diff = "auto"
                    autonomous_testing = "auto"
                    second_opinion = "auto"
                    """
                ).strip()
            )

            config = load_config(home / "omnius.toml")

            self.assertEqual(config.runner.default, "claude")

    def test_load_config_wraps_missing_required_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_text(
                textwrap.dedent(
                    """
                    [global]
                    timezone = "America/Los_Angeles"
                    pipeline_cron = "0 21 * * 0-4"
                    pipeline_budget_minutes = 540
                    default_task_budget_minutes = 120
                    max_consecutive_failures = 3
                    notification_backend = "none"

                    [capabilities]
                    brainstorm = "auto"
                    review_diff = "auto"
                    autonomous_testing = "auto"
                    second_opinion = "auto"
                    """
                ).strip()
            )

            with self.assertRaisesRegex(ConfigError, r"^Invalid config structure in "):
                load_config(home / "omnius.toml")

    def test_load_config_wraps_wrong_value_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_text(
                textwrap.dedent(
                    """
                    [global]
                    timezone = "America/Los_Angeles"
                    pipeline_cron = "0 21 * * 0-4"
                    pipeline_budget_minutes = "540"
                    default_task_budget_minutes = 120
                    max_consecutive_failures = 3
                    notification_backend = "none"

                    [runner]
                    default = "codex"

                    [capabilities]
                    brainstorm = "auto"
                    review_diff = "auto"
                    autonomous_testing = "auto"
                    second_opinion = "auto"

                    [[repos]]
                    slug = "example"
                    path = "~/code/example"
                    branch = "main"
                    role = "author"
                    labels = "omnius"
                    """
                ).strip()
            )

            with self.assertRaisesRegex(ConfigError, r"^Invalid config structure in "):
                load_config(home / "omnius.toml")

    def test_load_config_wraps_missing_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            with self.assertRaisesRegex(ConfigError, r"^Failed to load config from "):
                load_config(home / "omnius.toml")

    def test_load_config_wraps_malformed_toml_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_text("[global\ntimezone = 'America/Los_Angeles'\n")

            with self.assertRaisesRegex(ConfigError, r"^Failed to load config from "):
                load_config(home / "omnius.toml")

    def test_load_config_wraps_utf8_decode_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "omnius.toml").write_bytes(b"\x80")

            with self.assertRaisesRegex(ConfigError, r"^Failed to load config from "):
                load_config(home / "omnius.toml")
