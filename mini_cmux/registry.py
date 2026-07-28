"""Atomic JSON state and append-only event storage."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar

from .errors import MiniCmuxError

T = TypeVar("T")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "stream_id": str(uuid.uuid4()),
        "next_event_seq": 1,
        "projects": {},
        "agents": {},
        "events": [],
    }


class Registry:
    """Serialize registry mutations with a filesystem lock and atomic replace."""

    def __init__(self, home: Optional[Path] = None) -> None:
        configured = os.environ.get("MINI_CMUX_HOME")
        self.home = Path(home or configured or Path.home() / ".local/state/mini-cmux")
        self.path = self.home / "registry.json"
        self.events_path = self.home / "events.jsonl"
        self.lock_path = self.home / "registry.lock"

    def _ensure_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self, exclusive: bool) -> Iterator[None]:
        self._ensure_home()
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> Dict[str, Any]:
        if not self.path.exists():
            return default_state()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise MiniCmuxError("Cannot read registry {}: {}".format(self.path, exc))
        if state.get("version") != 1:
            raise MiniCmuxError(
                "Unsupported registry version {!r}".format(state.get("version"))
            )
        state.setdefault("projects", {})
        state.setdefault("agents", {})
        state.setdefault("events", [])
        state.setdefault("stream_id", str(uuid.uuid4()))
        next_sequence = 1
        for event in state["events"]:
            if not event.get("seq"):
                event["seq"] = next_sequence
            next_sequence = max(next_sequence, event["seq"] + 1)
            event.setdefault("stream_id", state["stream_id"])
            event.setdefault("category", "agent")
            event.setdefault("payload", {})
        state["next_event_seq"] = max(
            int(state.get("next_event_seq") or 1), next_sequence
        )
        return state

    def read(self) -> Dict[str, Any]:
        with self._lock(exclusive=False):
            return copy.deepcopy(self._load_unlocked())

    def _write_unlocked(self, state: Dict[str, Any]) -> None:
        self._ensure_home()
        descriptor, temporary = tempfile.mkstemp(
            prefix="registry.", suffix=".tmp", dir=str(self.home)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def mutate(self, callback: Callable[[Dict[str, Any]], T]) -> T:
        with self._lock(exclusive=True):
            state = self._load_unlocked()
            result = callback(state)
            self._write_unlocked(state)
            return result

    def append_event_unlocked(
        self, state: Dict[str, Any], event: Dict[str, Any]
    ) -> None:
        """Append while called from a mutate callback holding the registry lock."""
        state["events"].append(event)
        state["events"] = state["events"][-2000:]
        if self.events_path.exists() and self.events_path.stat().st_size >= 16 * 1024 * 1024:
            rotated = self.events_path.with_name(self.events_path.name + ".1")
            if rotated.exists():
                rotated.unlink()
            os.replace(self.events_path, rotated)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True))
            handle.write("\n")
            handle.flush()
