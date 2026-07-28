"""Structured status marker recognition."""

from __future__ import annotations

import re
from typing import Dict, List, Optional


MARKER = re.compile(
    r"(?<![A-Z0-9_])(AGENT_STATUS|REVIEW_STATUS)=([A-Z][A-Z0-9_]*)"
)


def last_marker(output: str) -> Optional[Dict[str, str]]:
    markers = all_markers(output)
    return markers[-1] if markers else None


def all_markers(output: str) -> List[Dict[str, str]]:
    markers = []
    for match in MARKER.finditer(output):
        markers.append(
            {
                "kind": match.group(1),
                "value": match.group(2),
                "raw": "{}={}".format(match.group(1), match.group(2)),
            }
        )
    return markers


def interpret_marker(marker: Dict[str, str]) -> Dict[str, str]:
    value = marker["value"]
    if marker["kind"] == "REVIEW_STATUS":
        if value == "GREEN":
            return {"status": "review_green", "event": "review_green"}
        if value == "RED":
            return {"status": "review_red", "event": "review_red"}
    mapping = {
        "READY": ("ready", "agent_idle"),
        "IDLE": ("idle", "agent_idle"),
        "WORKING": ("working", "agent_working"),
        "WAITING_FOR_INPUT": (
            "waiting_for_input",
            "agent_waiting_for_input",
        ),
        "DONE": ("completed", "agent_completed"),
        "COMPLETED": ("completed", "agent_completed"),
        "FAILED": ("failed", "agent_failed"),
        "PLAN_READY": ("plan_ready", "agent_completed"),
    }
    status, event = mapping.get(value, (value.lower(), ""))
    return {"status": status, "event": event}
