import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SetupScriptTests(unittest.TestCase):
    def test_setup_uses_compatible_python_when_python3_is_too_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            calls = tmp_path / "calls.txt"
            fake_bin.mkdir()

            self._write_executable(
                fake_bin / "python3",
                (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"-c\" ]; then\n"
                    "  exit 1\n"
                    "fi\n"
                    "echo \"python3 $*\" >> \"$OMNIUS_TEST_CALLS\"\n"
                    "exit 90\n"
                ),
            )
            self._write_executable(
                fake_bin / "python3.11",
                (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"-c\" ]; then\n"
                    "  exit 0\n"
                    "fi\n"
                    "echo \"python3.11 $*\" >> \"$OMNIUS_TEST_CALLS\"\n"
                    "exit 42\n"
                ),
            )

            env = os.environ.copy()
            env["OMNIUS_TEST_CALLS"] = str(calls)
            env["PATH"] = f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"
            result = subprocess.run(
                [str(ROOT / "omnius_setup.sh")],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=env,
            )

            self.assertEqual(result.returncode, 42, result.stderr)
            self.assertEqual(calls.read_text(encoding="utf-8"), f"python3.11 -m omnius.bootstrap {ROOT}\n")

    def _write_executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
