#!/usr/bin/env python3
"""Prove the tests can actually fail.

A green test suite means little if it would stay green with a bug in the code. This script
copies the project to a temporary folder, injects one known bug at a time (a wrong
threshold, a leaked token, ...) and checks that the test suite goes red each time.

    python scripts/mutation_check.py

Exit code 0 = every injected bug was caught.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (what we break, file, exact text to replace, replacement)
MUTATIONS = [
    (
        "SLA boundary: '>' becomes '>='",
        "leadwatch/checks.py",
        "elif age > cfg.sla_hours:",
        "elif age >= cfg.sla_hours:",
    ),
    (
        "conversion measured on leads too new to convert",
        "leadwatch/metrics.py",
        "rate_cur = in_window(leads, now - week - mature, now - mature)",
        "rate_cur = in_window(leads, now - week, now)",
    ),
    (
        "Telegram error keeps the exception chain (token could leak)",
        "leadwatch/telegram.py",
        ") from None\n    except urllib.error.URLError",
        ")\n    except urllib.error.URLError",
    ),
    (
        "luck check removed: every weak source gets blamed",
        "leadwatch/metrics.py",
        "if lost >= 1.0 and chance < cfg.luck_threshold:",
        "if lost >= 1.0:",
    ),
    (
        "digest printed to CI logs even when sent (lead names exposed)",
        "leadwatch/cli.py",
        "        if args.show:\n            print()\n            print(text)",
        "        print(text)",
    ),
    (
        "slash dates read day-first instead of month-first",
        "leadwatch/loader.py",
        '    "%m/%d/%Y %H:%M:%S",\n    "%m/%d/%Y %H:%M",\n    "%m/%d/%Y",\n'
        '    "%d/%m/%Y %H:%M:%S",\n    "%d/%m/%Y %H:%M",\n    "%d/%m/%Y",',
        '    "%d/%m/%Y %H:%M:%S",\n    "%d/%m/%Y %H:%M",\n    "%d/%m/%Y",\n'
        '    "%m/%d/%Y %H:%M:%S",\n    "%m/%d/%Y %H:%M",\n    "%m/%d/%Y",',
    ),
    (
        "duplicate phones compared with country code (+1 415... vs 415...)",
        "leadwatch/checks.py",
        'return digits[-10:] if len(digits) >= 7 else ""',
        'return digits if len(digits) >= 7 else ""',
    ),
    (
        "overdue-call grace period removed",
        "leadwatch/checks.py",
        "if late > cfg.call_grace_hours:",
        "if late > 0:",
    ),
]


def run_suite(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover"],
        cwd=project, capture_output=True, text=True, timeout=300,
    )


def main() -> int:
    caught = 0
    problems = 0
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "project"
        for name, rel, old, new in MUTATIONS:
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".env", "*.pyc"))
            target = work / rel
            source = target.read_text(encoding="utf-8")
            if old not in source:
                print(f"SKIPPED  {name}: the code it targets has changed, update this script")
                problems += 1
                continue
            target.write_text(source.replace(old, new, 1), encoding="utf-8")
            result = run_suite(work)
            failed = result.returncode != 0
            first = next((l for l in result.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))), "")
            print(f"{'CAUGHT ' if failed else 'MISSED!'}  {name}")
            if failed:
                caught += 1
                print(f"           by {first.split(' (')[0]}")
            else:
                problems += 1
    print(f"\n{caught} of {len(MUTATIONS)} injected bugs were caught by the test suite.")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
