#!/usr/bin/env python3
"""Deterministic interactive workers for the mini-cmux team demo."""

from __future__ import annotations

import sys
from pathlib import Path


EXPECTED = "mini-cmux team handoff complete\n"


def planner() -> None:
    Path("PLAN.md").write_text(
        "# Demo plan\n\n"
        "1. Read the required output from GOAL.md.\n"
        "2. Write the exact line to demo_output.txt.\n"
        "3. Verify the file content byte-for-byte.\n",
        encoding="utf-8",
    )
    print("Planner wrote PLAN.md", flush=True)
    print("AGENT_STATUS=PLAN_READY", flush=True)


def implementer() -> None:
    if not Path("PLAN.md").exists():
        print("PLAN.md is missing", flush=True)
        print("AGENT_STATUS=FAILED", flush=True)
        return
    Path("demo_output.txt").write_text(EXPECTED, encoding="utf-8")
    print("Implementer wrote demo_output.txt", flush=True)
    print("AGENT_STATUS=DONE", flush=True)


def verifier() -> None:
    try:
        actual = Path("demo_output.txt").read_text(encoding="utf-8")
    except OSError as exc:
        print("Verifier could not read demo_output.txt: {}".format(exc), flush=True)
        print("REVIEW_STATUS=RED", flush=True)
        return
    if actual == EXPECTED:
        print("Verifier confirmed the exact expected output", flush=True)
        print("REVIEW_STATUS=GREEN", flush=True)
    else:
        print("Expected {!r}, received {!r}".format(EXPECTED, actual), flush=True)
        print("REVIEW_STATUS=RED", flush=True)


HANDLERS = {
    "planner": planner,
    "implementer": implementer,
    "verifier": verifier,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in HANDLERS:
        print("usage: demo_agent.py planner|implementer|verifier", file=sys.stderr)
        return 2
    role = sys.argv[1]
    print("{} demo worker ready".format(role), flush=True)
    for prompt in sys.stdin:
        if not prompt.strip():
            continue
        print("{} received an assignment".format(role), flush=True)
        HANDLERS[role]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
