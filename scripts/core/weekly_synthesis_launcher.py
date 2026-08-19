#!/usr/bin/env python3
"""Stdlib-only launchd shim for the weekly synthesis.

The plist's ProgramArguments[0] must stay /opt/homebrew/bin/python3 - that is
the interpreter holding the TCC grant for ~/Documents - but weekly_synthesis.py
needs pandas, which lives in the repo venv. Same split the Matter sync uses
for --rebuild-index: TCC-granted parent, venv child. This shim is the parent.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "core" / "weekly_synthesis.py"

candidates = []
override = os.environ.get("MATTER_INDEX_PYTHON")
if override:
    candidates.append(Path(override).expanduser())
candidates += [REPO_ROOT / ".venv" / "bin" / "python", REPO_ROOT / "venv" / "bin" / "python"]

interpreter = next((c for c in candidates if c.exists()), None)
if interpreter is None:
    print("weekly-synthesis launcher: no venv interpreter found", file=sys.stderr)
    sys.exit(2)

sys.exit(subprocess.run([str(interpreter), str(SCRIPT)] + sys.argv[1:]).returncode)
