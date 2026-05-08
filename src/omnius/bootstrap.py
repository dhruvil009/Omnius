from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import sysconfig


def run_bootstrap_install(
    *,
    repo_root: Path,
    python_bin: str,
    argv: list[str] | None = None,
    runner=subprocess.run,
) -> int:
    install_command = [python_bin, "-m", "pip", "install", "--user", str(repo_root)]
    runner(
        install_command,
        check=True,
        text=True,
    )
    install_env = os.environ.copy()
    install_env.pop("PYTHONPATH", None)
    runner(
        [python_bin, "-m", "omnius", "install", *(argv or [])],
        check=True,
        text=True,
        env=install_env,
    )
    _print_post_install_notes(python_bin=python_bin)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if sys.version_info < (3, 11):
        print("Omnius requires Python 3.11 or newer", file=sys.stderr)
        return 1
    if not args:
        print("Usage: python -m omnius.bootstrap <repo-root> [install args...]", file=sys.stderr)
        return 1
    repo_root = Path(args[0]).expanduser().resolve()
    return run_bootstrap_install(
        repo_root=repo_root,
        python_bin=sys.executable,
        argv=args[1:],
    )

def _print_post_install_notes(*, python_bin: str) -> None:
    scripts_dir = Path(sysconfig.get_path("scripts", scheme=sysconfig.get_preferred_scheme("user")))
    omnius_command = scripts_dir / ("omnius.exe" if os.name == "nt" else "omnius")
    current_path_entries = os.environ.get("PATH", "").split(os.pathsep)
    on_path = any(Path(entry).resolve() == scripts_dir.resolve() for entry in current_path_entries if entry)
    print("Bootstrap complete.")
    if on_path and omnius_command.exists():
        print("Next command: omnius doctor")
        return
    if omnius_command.exists():
        print(f"Installed command path: {omnius_command}")
    print(f"If the command is not on PATH yet, use: {python_bin} -m omnius doctor")


if __name__ == "__main__":
    raise SystemExit(main())
