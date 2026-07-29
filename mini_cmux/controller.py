"""Project, agent, status, event, and recovery orchestration."""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import MiniCmuxError, TmuxError
from .markers import all_markers, interpret_marker
from .notifications import Notifier
from .registry import Registry, utc_now
from .tmux import Tmux


TERMINAL_STATUSES = {
    "completed",
    "failed",
    "review_green",
    "review_red",
    "stopped",
    "lost",
}
IMPORTANT_EVENTS = {
    "agent_waiting_for_input",
    "agent_completed",
    "agent_failed",
    "process_exited",
    "review_green",
    "review_red",
    "timeout",
    "session_lost",
    "notification",
}
EVENT_CATEGORIES = {
    "agent_started": "agent",
    "agent_working": "agent",
    "agent_idle": "agent",
    "agent_waiting_for_input": "attention",
    "agent_completed": "attention",
    "agent_failed": "attention",
    "process_exited": "lifecycle",
    "review_green": "attention",
    "review_red": "attention",
    "timeout": "attention",
    "session_lost": "lifecycle",
    "notification": "notification",
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return slug or "project"


class Controller:
    def __init__(
        self,
        registry: Optional[Registry] = None,
        tmux: Optional[Tmux] = None,
        notifier: Optional[Notifier] = None,
    ) -> None:
        self.registry = registry or Registry()
        self.tmux = tmux or Tmux()
        self.notifier = notifier or Notifier()

    @staticmethod
    def _project_by_name(state: Dict[str, Any], name: str) -> Dict[str, Any]:
        for project in state["projects"].values():
            if project["name"] == name or project["id"] == name:
                return project
        raise MiniCmuxError("Unknown project {!r}".format(name))

    @staticmethod
    def _agent_candidates(
        state: Dict[str, Any], name: str, project_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        project_id = None
        if project_name:
            project_id = Controller._project_by_name(state, project_name)["id"]
        return [
            agent
            for agent in state["agents"].values()
            if (agent["name"] == name or agent["id"] == name)
            and (project_id is None or agent["project_id"] == project_id)
        ]

    @classmethod
    def _agent_by_name(
        cls, state: Dict[str, Any], name: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        candidates = cls._agent_candidates(state, name, project_name)
        if not candidates:
            qualifier = " in project {!r}".format(project_name) if project_name else ""
            raise MiniCmuxError("Unknown agent {!r}{}".format(name, qualifier))
        if len(candidates) > 1:
            projects = sorted(
                state["projects"][agent["project_id"]]["name"] for agent in candidates
            )
            raise MiniCmuxError(
                "Agent {!r} is ambiguous; pass --project (matches: {})".format(
                    name, ", ".join(projects)
                )
            )
        return candidates[0]

    def _event_in_state(
        self,
        state: Dict[str, Any],
        event_type: str,
        message: str,
        project: Dict[str, Any],
        agent: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sequence = state["next_event_seq"]
        state["next_event_seq"] += 1
        event = {
            "id": str(uuid.uuid4()),
            "seq": sequence,
            "stream_id": state["stream_id"],
            "timestamp": utc_now(),
            "project_id": project["id"],
            "project_name": project["name"],
            "agent_id": agent["id"] if agent else None,
            "agent_name": agent["name"] if agent else None,
            "role": agent["role"] if agent else None,
            "tmux_target": agent.get("tmux_target") if agent else None,
            "type": event_type,
            "category": EVENT_CATEGORIES.get(event_type, "agent"),
            "message": message,
            "payload": payload or {},
            "read_state": "unread",
            "acknowledged_at": None,
        }
        self.registry.append_event_unlocked(state, event)
        if agent and event_type in IMPORTANT_EVENTS:
            agent["attention_required"] = True
        return event

    def _notify_events(self, events: Iterable[Dict[str, Any]]) -> None:
        titles = {
            "agent_waiting_for_input": "Agent needs input",
            "agent_completed": "Agent completed",
            "agent_failed": "Agent failed",
            "process_exited": "Agent process exited",
            "review_green": "Review passed",
            "review_red": "Review failed",
            "timeout": "Agent wait timed out",
            "session_lost": "Agent session lost",
            "notification": "mini-cmux",
        }
        for event in events:
            if event["type"] not in IMPORTANT_EVENTS:
                continue
            agent_name = event.get("agent_name")
            action = (
                "mini-cmux agent focus {} --project {}".format(
                    shlex.quote(agent_name), shlex.quote(event["project_name"])
                )
                if agent_name
                else "mini-cmux project attach {}".format(
                    shlex.quote(event["project_name"])
                )
            )
            body = "{} / {}: {}".format(
                event["project_name"], agent_name or "project", event["message"]
            )
            title = event.get("payload", {}).get("title") or titles.get(
                event["type"], "mini-cmux"
            )
            self.notifier.send(title, body, action)

    def create_project(self, name: str, cwd: str) -> Dict[str, Any]:
        path = Path(cwd).expanduser().resolve()
        if not path.is_dir():
            raise MiniCmuxError("Project cwd is not a directory: {}".format(path))
        state = self.registry.read()
        if any(project["name"] == name for project in state["projects"].values()):
            raise MiniCmuxError("Project {!r} already exists".format(name))
        project_id = str(uuid.uuid4())
        base_session = "mini-{}".format(_slug(name))
        session = base_session
        suffix = 2
        while self.tmux.session_exists(session):
            session = "{}-{}".format(base_session, suffix)
            suffix += 1
        self.tmux.create_session(session, str(path), project_id)
        now = utc_now()
        project = {
            "id": project_id,
            "name": name,
            "cwd": str(path),
            "tmux_session": session,
            "ghostty": {
                "automation": "manual",
                "tab_id": None,
                "attach_command": "tmux attach-session -t {}".format(
                    shlex.quote(session)
                ),
            },
            "status": "active",
            "session_lost_reported": False,
            "created_at": now,
            "updated_at": now,
        }

        def add(state: Dict[str, Any]) -> None:
            state["projects"][project_id] = project

        self.registry.mutate(add)
        return project

    def list_projects(self) -> List[Dict[str, Any]]:
        self.reconcile()
        state = self.registry.read()
        return sorted(state["projects"].values(), key=lambda item: item["created_at"])

    def get_project(self, name: str) -> Dict[str, Any]:
        state = self.registry.read()
        return self._project_by_name(state, name)

    def close_project(self, name: str) -> Dict[str, Any]:
        state = self.registry.read()
        project = self._project_by_name(state, name)
        self.tmux.kill_session(project["tmux_session"], missing_ok=True)

        def close(current: Dict[str, Any]) -> Dict[str, Any]:
            item = current["projects"][project["id"]]
            item["status"] = "closed"
            item["updated_at"] = utc_now()
            for agent in current["agents"].values():
                if agent["project_id"] == item["id"] and agent["status"] != "stopped":
                    agent["status"] = "stopped"
                    agent["updated_at"] = utc_now()
            return dict(item)

        return self.registry.mutate(close)

    def create_agent(
        self,
        project_name: str,
        name: str,
        role: str,
        command: str,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.registry.read()
        project = self._project_by_name(state, project_name)
        if project["status"] != "active":
            raise MiniCmuxError("Project {!r} is not active".format(project_name))
        if not self.tmux.session_exists(project["tmux_session"]):
            raise MiniCmuxError(
                "tmux session {} is missing; recreate the project".format(
                    project["tmux_session"]
                )
            )
        if any(
            item["project_id"] == project["id"]
            and item["name"] == name
            and item["status"] != "stopped"
            for item in state["agents"].values()
        ):
            raise MiniCmuxError(
                "Agent {!r} already exists in project {!r}".format(name, project_name)
            )
        agent_cwd = Path(cwd or project["cwd"]).expanduser().resolve()
        if not agent_cwd.is_dir():
            raise MiniCmuxError("Agent cwd is not a directory: {}".format(agent_cwd))
        agent_id = str(uuid.uuid4())
        output_directory = self.registry.home / "output"
        output_directory.mkdir(parents=True, exist_ok=True)
        output_log = output_directory / "{}.log".format(agent_id)
        pane = self.tmux.create_agent_pane(
            project["tmux_session"],
            project["id"],
            agent_id,
            name,
            role,
            str(agent_cwd),
            command,
            str(output_log),
        )
        pane_info = next(
            (
                item
                for item in self.tmux.list_panes(project["tmux_session"])
                if item["pane_id"] == pane
            ),
            None,
        )
        now = utc_now()
        agent = {
            "id": agent_id,
            "project_id": project["id"],
            "project_name": project["name"],
            "name": name,
            "role": role,
            "tmux_session": project["tmux_session"],
            "tmux_window": pane_info["window"] if pane_info else None,
            "tmux_pane": pane,
            "tmux_target": pane_info["target"] if pane_info else pane,
            "command": command,
            "cwd": str(agent_cwd),
            "pid": pane_info["pid"] if pane_info else None,
            "status": "running",
            "last_marker": None,
            "marker_counts": {},
            "output_log": str(output_log),
            "output_offset": 0,
            "last_activity_at": now,
            "last_exit_status": None,
            "exit_reported": False,
            "session_lost_reported": False,
            "attention_required": False,
            "created_at": now,
            "updated_at": now,
        }
        events: List[Dict[str, Any]] = []

        def add(current: Dict[str, Any]) -> None:
            current["agents"][agent_id] = agent
            current_project = current["projects"][project["id"]]
            events.append(
                self._event_in_state(
                    current,
                    "agent_started",
                    "{} started as {}".format(name, role),
                    current_project,
                    agent,
                )
            )

        self.registry.mutate(add)
        self._notify_events(events)
        return agent

    def list_agents(self, project_name: Optional[str] = None) -> List[Dict[str, Any]]:
        self.reconcile()
        state = self.registry.read()
        project_id = (
            self._project_by_name(state, project_name)["id"] if project_name else None
        )
        agents = [
            agent
            for agent in state["agents"].values()
            if project_id is None or agent["project_id"] == project_id
        ]
        return sorted(agents, key=lambda item: item["created_at"])

    def _resolve_agent(
        self, name: str, project_name: Optional[str] = None, reconcile: bool = True
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if reconcile:
            self.reconcile()
        state = self.registry.read()
        agent = self._agent_by_name(state, name, project_name)
        project = state["projects"][agent["project_id"]]
        return agent, project

    @staticmethod
    def _require_live(agent: Dict[str, Any]) -> None:
        if agent["status"] in {"stopped", "lost"}:
            raise MiniCmuxError(
                "Agent {!r} is {}".format(agent["name"], agent["status"])
            )

    def send_text(
        self, name: str, text: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        agent, _ = self._resolve_agent(name, project_name)
        self._require_live(agent)
        self.tmux.send_text(agent["tmux_pane"], text)
        self._touch_agent(agent["id"])
        return agent

    def send_key(
        self, name: str, key: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        agent, _ = self._resolve_agent(name, project_name)
        self._require_live(agent)
        self.tmux.send_key(agent["tmux_pane"], key)
        self._touch_agent(agent["id"])
        return agent

    def dispatch(
        self, name: str, text: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send one prompt plus Enter and mark an interactive agent as working."""
        agent, _ = self._resolve_agent(name, project_name)
        self._require_live(agent)
        self.tmux.send_text(agent["tmux_pane"], text)
        self.tmux.send_key(agent["tmux_pane"], "enter")

        def mark_working(state: Dict[str, Any]) -> Dict[str, Any]:
            current = state["agents"][agent["id"]]
            current["status"] = "working"
            current["attention_required"] = False
            current["last_activity_at"] = utc_now()
            current["updated_at"] = utc_now()
            return dict(current)

        return self.registry.mutate(mark_working)

    def _touch_agent(self, agent_id: str) -> None:
        def touch(state: Dict[str, Any]) -> None:
            if agent_id in state["agents"]:
                state["agents"][agent_id]["last_activity_at"] = utc_now()
                state["agents"][agent_id]["updated_at"] = utc_now()

        self.registry.mutate(touch)

    def read_agent(
        self, name: str, lines: int, project_name: Optional[str] = None
    ) -> str:
        if lines < 1 or lines > 2000:
            raise MiniCmuxError("--lines must be between 1 and 2000")
        agent, _ = self._resolve_agent(name, project_name)
        if agent["status"] == "lost":
            raise MiniCmuxError("Agent {!r} has no live tmux pane".format(name))
        return self.tmux.capture(agent["tmux_pane"], lines)

    def focus_agent(
        self, name: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        agent, project = self._resolve_agent(name, project_name)
        self._require_live(agent)
        self.tmux.select_pane(agent["tmux_pane"])
        self.acknowledge_agent(agent["id"])
        return {"agent": agent, "project": project}

    def acknowledge_agent(self, agent_id: str) -> None:
        now = utc_now()

        def acknowledge(state: Dict[str, Any]) -> None:
            agent = state["agents"].get(agent_id)
            if not agent:
                return
            agent["attention_required"] = False
            for event in state["events"]:
                if event.get("agent_id") == agent_id and not event["acknowledged_at"]:
                    event["acknowledged_at"] = now
                    event["read_state"] = "read"

        self.registry.mutate(acknowledge)

    def stop_agent(
        self, name: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        agent, _ = self._resolve_agent(name, project_name, reconcile=False)
        self.tmux.kill_pane(agent["tmux_pane"], missing_ok=True)

        def stop(state: Dict[str, Any]) -> Dict[str, Any]:
            item = state["agents"][agent["id"]]
            item["status"] = "stopped"
            item["attention_required"] = False
            item["updated_at"] = utc_now()
            return dict(item)

        return self.registry.mutate(stop)

    def restart_agent(
        self, name: str, project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        agent, project = self._resolve_agent(name, project_name, reconcile=False)
        if not self.tmux.session_exists(project["tmux_session"]):
            raise MiniCmuxError(
                "tmux session {} is missing; recreate the project first".format(
                    project["tmux_session"]
                )
            )
        panes = self.tmux.list_panes(project["tmux_session"])
        pane = next(
            (
                item
                for item in panes
                if item.get("agent_id") == agent["id"]
                or item["pane_id"] == agent.get("tmux_pane")
            ),
            None,
        )
        output_log = agent.get("output_log")
        if pane:
            self.tmux.restart_agent_pane(
                pane["pane_id"],
                project["id"],
                agent["id"],
                agent["name"],
                agent["role"],
                agent["cwd"],
                agent["command"],
                output_log,
            )
            pane_id = pane["pane_id"]
        else:
            pane_id = self.tmux.create_agent_pane(
                project["tmux_session"],
                project["id"],
                agent["id"],
                agent["name"],
                agent["role"],
                agent["cwd"],
                agent["command"],
                output_log,
            )
        pane_info = next(
            (
                item
                for item in self.tmux.list_panes(project["tmux_session"])
                if item["pane_id"] == pane_id
            ),
            None,
        )
        emitted: List[Dict[str, Any]] = []

        def restart(state: Dict[str, Any]) -> Dict[str, Any]:
            current = state["agents"][agent["id"]]
            current["tmux_pane"] = pane_id
            current["tmux_window"] = (
                pane_info["window"] if pane_info else current.get("tmux_window")
            )
            current["tmux_target"] = (
                pane_info["target"] if pane_info else pane_id
            )
            current["pid"] = pane_info["pid"] if pane_info else None
            current["status"] = "running"
            current["last_exit_status"] = None
            current["exit_reported"] = False
            current["session_lost_reported"] = False
            current["attention_required"] = False
            current["updated_at"] = utc_now()
            emitted.append(
                self._event_in_state(
                    state,
                    "agent_started",
                    "{} restarted".format(current["name"]),
                    state["projects"][current["project_id"]],
                    current,
                    payload={"restart": True},
                )
            )
            return dict(current)

        restarted = self.registry.mutate(restart)
        self._notify_events(emitted)
        return restarted

    @staticmethod
    def _read_incremental_output(agent: Dict[str, Any]) -> Tuple[str, int]:
        output_log = agent.get("output_log")
        old_offset = int(agent.get("output_offset") or 0)
        if not output_log:
            return "", old_offset
        path = Path(output_log)
        try:
            size = path.stat().st_size
            offset = old_offset if old_offset <= size else 0
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(4 * 1024 * 1024)
                new_offset = handle.tell()
        except OSError:
            return "", old_offset
        return data.decode("utf-8", errors="replace"), new_offset

    def reconcile(self) -> List[Dict[str, Any]]:
        """Repair tmux targets and turn markers/process state into events."""
        state = self.registry.read()
        observations: Dict[str, Dict[str, Any]] = {}
        missing_projects = set()
        present_projects = set()
        for project in state["projects"].values():
            if project["status"] not in {"active", "lost"}:
                continue
            session = project["tmux_session"]
            if not self.tmux.session_exists(session):
                missing_projects.add(project["id"])
                continue
            try:
                panes = self.tmux.list_panes(session)
            except TmuxError:
                missing_projects.add(project["id"])
                continue
            present_projects.add(project["id"])
            by_agent = {
                pane["agent_id"]: pane for pane in panes if pane.get("agent_id")
            }
            for agent in state["agents"].values():
                if (
                    agent["project_id"] != project["id"]
                    or agent["status"] == "stopped"
                ):
                    continue
                pane = by_agent.get(agent["id"])
                if pane is None:
                    pane = next(
                        (
                            item
                            for item in panes
                            if item["pane_id"] == agent.get("tmux_pane")
                            or item["title"].endswith(agent["id"][:8])
                        ),
                        None,
                    )
                if pane is None:
                    observations[agent["id"]] = {"missing": True}
                    continue
                capture = ""
                try:
                    capture = self.tmux.capture(pane["pane_id"], 250)
                except TmuxError:
                    pass
                incremental_output, new_output_offset = (
                    self._read_incremental_output(agent)
                )
                observations[agent["id"]] = {
                    "missing": False,
                    "pane": pane,
                    "capture_markers": all_markers(capture),
                    "incremental_markers": all_markers(incremental_output),
                    "output_offset": new_output_offset,
                    "had_incremental_output": bool(incremental_output),
                }

        emitted: List[Dict[str, Any]] = []

        def apply(current: Dict[str, Any]) -> None:
            now = utc_now()
            for project_id in present_projects:
                project = current["projects"].get(project_id)
                if project and project["status"] == "lost":
                    project["status"] = "active"
                    project["session_lost_reported"] = False
                    project["updated_at"] = now
            for project_id in missing_projects:
                project = current["projects"].get(project_id)
                if not project or project["status"] != "active":
                    continue
                project["status"] = "lost"
                project["updated_at"] = now
                affected_agents = [
                    agent
                    for agent in current["agents"].values()
                    if agent["project_id"] == project_id
                    and agent["status"] != "stopped"
                ]
                if not project.get("session_lost_reported"):
                    if not affected_agents:
                        emitted.append(
                            self._event_in_state(
                                current,
                                "session_lost",
                                "tmux session {} is missing".format(
                                    project["tmux_session"]
                                ),
                                project,
                            )
                        )
                    project["session_lost_reported"] = True
                for agent in affected_agents:
                    agent["status"] = "lost"
                    agent["updated_at"] = now
                    if not agent.get("session_lost_reported"):
                        emitted.append(
                            self._event_in_state(
                                current,
                                "session_lost",
                                "tmux session {} is missing".format(
                                    project["tmux_session"]
                                ),
                                project,
                                agent,
                            )
                        )
                        agent["session_lost_reported"] = True

            for agent_id, observation in observations.items():
                agent = current["agents"].get(agent_id)
                if not agent or agent["status"] == "stopped":
                    continue
                project = current["projects"][agent["project_id"]]
                if observation["missing"]:
                    agent["status"] = "lost"
                    agent["updated_at"] = now
                    if not agent.get("session_lost_reported"):
                        emitted.append(
                            self._event_in_state(
                                current,
                                "session_lost",
                                "managed tmux pane is missing",
                                project,
                                agent,
                            )
                        )
                        agent["session_lost_reported"] = True
                    continue
                pane = observation["pane"]
                if project["status"] == "lost":
                    project["status"] = "active"
                    project["session_lost_reported"] = False
                    project["updated_at"] = now
                if agent["status"] == "lost":
                    agent["status"] = "running"
                agent["tmux_pane"] = pane["pane_id"]
                agent["tmux_window"] = pane["window"]
                agent["tmux_target"] = pane["target"]
                agent["pid"] = pane["pid"]
                agent["session_lost_reported"] = False
                agent["output_offset"] = observation.get(
                    "output_offset", agent.get("output_offset", 0)
                )
                marker_occurrences: Dict[str, int] = {}
                previous_counts = agent.get("marker_counts", {})
                capture_new_markers = []
                for marker in observation.get("capture_markers", []):
                    raw = marker["raw"]
                    marker_occurrences[raw] = marker_occurrences.get(raw, 0) + 1
                    if marker_occurrences[raw] > previous_counts.get(raw, 0):
                        capture_new_markers.append(marker)
                updated_counts = dict(previous_counts)
                for raw, count in marker_occurrences.items():
                    updated_counts[raw] = max(count, previous_counts.get(raw, 0))
                agent["marker_counts"] = updated_counts
                incremental_markers = observation.get("incremental_markers", [])
                new_markers = (
                    incremental_markers
                    if observation.get("had_incremental_output")
                    else capture_new_markers
                )
                for marker in new_markers:
                    interpreted = interpret_marker(marker)
                    agent["last_marker"] = marker["raw"]
                    agent["status"] = interpreted["status"]
                    agent["last_activity_at"] = now
                    agent["updated_at"] = now
                    if interpreted["event"]:
                        emitted.append(
                            self._event_in_state(
                                current,
                                interpreted["event"],
                                "{} reported {}".format(
                                    agent["name"], marker["raw"]
                                ),
                                project,
                                agent,
                            )
                        )
                if pane["dead"] and not agent.get("exit_reported"):
                    exit_status = pane["dead_status"]
                    agent["last_exit_status"] = exit_status
                    agent["exit_reported"] = True
                    if agent["status"] not in TERMINAL_STATUSES:
                        agent["status"] = "completed" if exit_status == 0 else "failed"
                    agent["updated_at"] = now
                    emitted.append(
                        self._event_in_state(
                            current,
                            "process_exited",
                            "{} exited with status {}".format(
                                agent["name"],
                                exit_status if exit_status is not None else "unknown",
                            ),
                            project,
                            agent,
                        )
                    )

        if missing_projects or present_projects or observations:
            self.registry.mutate(apply)
        self._notify_events(emitted)
        return emitted

    def status(self, project_name: Optional[str] = None) -> Dict[str, Any]:
        self.reconcile()
        state = self.registry.read()
        if not project_name:
            return state
        project = self._project_by_name(state, project_name)
        return {
            "version": state["version"],
            "projects": {project["id"]: project},
            "agents": {
                agent_id: agent
                for agent_id, agent in state["agents"].items()
                if agent["project_id"] == project["id"]
            },
            "events": [
                event
                for event in state["events"]
                if event["project_id"] == project["id"]
            ],
        }

    def events(
        self,
        project_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        after_seq: Optional[int] = None,
        event_types: Optional[Iterable[str]] = None,
        unread_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        self.reconcile()
        state = self.registry.read()
        project_id = (
            self._project_by_name(state, project_name)["id"] if project_name else None
        )
        agent_id = (
            self._agent_by_name(state, agent_name, project_name)["id"]
            if agent_name
            else None
        )
        accepted_types = set(event_types or [])
        events = [
            event
            for event in state["events"]
            if (project_id is None or event["project_id"] == project_id)
            and (agent_id is None or event["agent_id"] == agent_id)
            and (after_seq is None or event.get("seq", 0) > after_seq)
            and (not accepted_types or event["type"] in accepted_types)
            and (not unread_only or event["read_state"] == "unread")
        ]
        if limit is not None:
            events = events[-max(0, limit) :]
        return events

    def event_cursor_info(self, after_seq: int) -> Dict[str, Any]:
        state = self.registry.read()
        sequences = [
            event.get("seq", 0) for event in state["events"] if event.get("seq")
        ]
        oldest = min(sequences) if sequences else None
        latest = max(sequences) if sequences else 0
        return {
            "stream_id": state["stream_id"],
            "requested_after_seq": after_seq,
            "oldest_seq": oldest,
            "latest_seq": latest,
            "next_seq": state["next_event_seq"],
            "gap": bool(
                after_seq
                and oldest is not None
                and after_seq < oldest - 1
            ),
        }

    def record_hook(
        self,
        status: str,
        message: Optional[str] = None,
        agent_name: Optional[str] = None,
        project_name: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "generic",
        source_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        identifier = agent_name or os.environ.get("MINI_CMUX_AGENT_ID")
        project_identifier = project_name or os.environ.get("MINI_CMUX_PROJECT_ID")
        if not identifier:
            raise MiniCmuxError(
                "hook requires --agent outside a mini-cmux managed pane"
            )
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", source):
            raise MiniCmuxError(
                "hook --source must contain 1-64 letters, digits, '.', '_', or '-'"
            )
        if source_event_id is not None and (
            not source_event_id
            or len(source_event_id) > 256
            or any(character in source_event_id for character in "\r\n")
        ):
            raise MiniCmuxError(
                "hook --event-id must be 1-256 characters without newlines"
            )
        state = self.registry.read()
        agent = self._agent_by_name(state, identifier, project_identifier)
        project = state["projects"][agent["project_id"]]
        normalized = status.lower().replace("-", "_")
        aliases = {
            "done": "completed",
            "waiting": "waiting_for_input",
            "needs_input": "waiting_for_input",
            "green": "review_green",
            "red": "review_red",
        }
        normalized = aliases.get(normalized, normalized)
        event_mapping = {
            "started": ("running", "agent_started"),
            "running": ("running", "agent_started"),
            "working": ("working", "agent_working"),
            "ready": ("ready", "agent_idle"),
            "plan_ready": ("plan_ready", "agent_completed"),
            "idle": ("idle", "agent_idle"),
            "waiting_for_input": (
                "waiting_for_input",
                "agent_waiting_for_input",
            ),
            "completed": ("completed", "agent_completed"),
            "failed": ("failed", "agent_failed"),
            "review_green": ("review_green", "review_green"),
            "review_red": ("review_red", "review_red"),
        }
        if normalized not in event_mapping:
            raise MiniCmuxError(
                "Unsupported hook status {!r}; expected one of {}".format(
                    status, ", ".join(sorted(event_mapping))
                )
            )
        agent_status, event_type = event_mapping[normalized]
        emitted: List[Dict[str, Any]] = []
        duplicate = False

        def record(current: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal duplicate
            current_agent = current["agents"][agent["id"]]
            current_project = current["projects"][project["id"]]
            receipt_key = (
                "{}:{}:{}".format(source, current_agent["id"], source_event_id)
                if source_event_id is not None
                else None
            )
            if receipt_key:
                existing_event_id = current["hook_receipts"].get(receipt_key)
                existing_event = next(
                    (
                        item
                        for item in current["events"]
                        if item["id"] == existing_event_id
                    ),
                    None,
                )
                if existing_event is not None:
                    duplicate = True
                    emitted.append(existing_event)
                    return dict(current_agent)
                current["hook_receipts"].pop(receipt_key, None)
            current_agent["status"] = agent_status
            current_agent["last_activity_at"] = utc_now()
            current_agent["updated_at"] = utc_now()
            if session_id:
                current_agent["native_session_id"] = session_id
            hook_message = message or "{} reported {}".format(
                current_agent["name"], normalized
            )
            emitted.append(
                self._event_in_state(
                    current,
                    event_type,
                    hook_message,
                    current_project,
                    current_agent,
                    payload={
                        "source": source,
                        "source_event_id": source_event_id,
                        "status": normalized,
                        "session_id": session_id,
                    },
                )
            )
            if receipt_key:
                receipts = current["hook_receipts"]
                receipts[receipt_key] = emitted[-1]["id"]
                for stale_key in list(receipts)[:-2000]:
                    receipts.pop(stale_key, None)
            return dict(current_agent)

        updated = self.registry.mutate(record)
        if not duplicate:
            self._notify_events(emitted)
        return {
            "agent": updated,
            "event": emitted[0],
            "duplicate": duplicate,
        }

    def notify(
        self,
        title: str,
        body: str,
        agent_name: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        identifier = agent_name or os.environ.get("MINI_CMUX_AGENT_ID")
        project_identifier = project_name or os.environ.get("MINI_CMUX_PROJECT_ID")
        state = self.registry.read()
        agent = (
            self._agent_by_name(state, identifier, project_identifier)
            if identifier
            else None
        )
        project = (
            state["projects"][agent["project_id"]]
            if agent
            else self._project_by_name(state, project_identifier)
            if project_identifier
            else None
        )
        if project is None:
            raise MiniCmuxError(
                "notify requires --project or a mini-cmux managed pane"
            )
        emitted: List[Dict[str, Any]] = []

        def record(current: Dict[str, Any]) -> None:
            current_project = current["projects"][project["id"]]
            current_agent = current["agents"].get(agent["id"]) if agent else None
            emitted.append(
                self._event_in_state(
                    current,
                    "notification",
                    body,
                    current_project,
                    current_agent,
                    payload={"title": title, "body": body, "source": "manual"},
                )
            )

        self.registry.mutate(record)
        self._notify_events(emitted)
        return emitted[0]

    def attention_events(
        self,
        project_name: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return [
            event
            for event in self.events(
                project_name=project_name,
                agent_name=agent_name,
                unread_only=True,
            )
            if event["type"] in IMPORTANT_EVENTS
        ]

    def acknowledge_events(
        self,
        event_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        project_name: Optional[str] = None,
        acknowledge_all: bool = False,
    ) -> int:
        if not any([event_id, agent_name, acknowledge_all]):
            raise MiniCmuxError("ack requires --id, --agent, or --all")
        state = self.registry.read()
        project_id = (
            self._project_by_name(state, project_name)["id"] if project_name else None
        )
        agent_id = (
            self._agent_by_name(state, agent_name, project_name)["id"]
            if agent_name
            else None
        )
        now = utc_now()

        def acknowledge(current: Dict[str, Any]) -> int:
            changed = 0
            for event in current["events"]:
                matches = event_id is not None and event["id"] == event_id
                if event_id is None and event["type"] in IMPORTANT_EVENTS:
                    matches = (
                        (agent_id is not None and event.get("agent_id") == agent_id)
                        or (
                            acknowledge_all
                            and (
                                project_id is None
                                or event["project_id"] == project_id
                            )
                        )
                    )
                if matches and not event.get("acknowledged_at"):
                    event["acknowledged_at"] = now
                    event["read_state"] = "read"
                    changed += 1
            for current_agent in current["agents"].values():
                current_agent["attention_required"] = any(
                    event["read_state"] == "unread"
                    and event.get("agent_id") == current_agent["id"]
                    and event["type"] in IMPORTANT_EVENTS
                    for event in current["events"]
                )
            return changed

        return self.registry.mutate(acknowledge)

    def latest_attention(
        self, project_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        events = self.attention_events(project_name=project_name)
        return max(events, key=lambda event: event.get("seq", 0)) if events else None

    def capabilities(self) -> Dict[str, Any]:
        return {
            "protocol_version": 1,
            "presentation": "ghostty-manual",
            "runtime": "tmux",
            "registry": "atomic-json",
            "features": {
                "stable_agent_ids": True,
                "send_text": True,
                "send_keys": ["enter", "escape", "ctrl-c"],
                "bounded_capture": True,
                "incremental_output_pipe": True,
                "structured_hooks": True,
                "hook_idempotency": True,
                "vendor_neutral_workers": True,
                "event_sequence": True,
                "event_cursor": True,
                "attention_queue": True,
                "native_notifications": platform.system() == "Darwin",
                "agent_restart": True,
                "workflow": ["planner", "implementer", "verifier"],
                "custom_terminal_ui": False,
                "browser": False,
                "remote_workers": False,
            },
        }

    def doctor(self) -> Dict[str, Any]:
        checks = []
        try:
            tmux_version = self.tmux.version()
            checks.append(
                {"name": "tmux", "status": "ok", "detail": tmux_version}
            )
        except TmuxError as exc:
            checks.append(
                {"name": "tmux", "status": "error", "detail": str(exc)}
            )
        ghostty_cli = shutil.which("ghostty")
        ghostty_candidates = [
            "/Applications/Ghostty.app",
            str(Path.home() / "Applications/Ghostty.app"),
            ghostty_cli
            if ghostty_cli and "/cmux.app/" not in ghostty_cli.lower()
            else None,
        ]
        ghostty = next(
            (candidate for candidate in ghostty_candidates if candidate and Path(candidate).exists()),
            None,
        )
        checks.append(
            {
                "name": "ghostty",
                "status": "ok" if ghostty else "warning",
                "detail": ghostty
                or (
                    "only cmux-bundled ghostty CLI detected; "
                    "standalone Ghostty.app not found"
                    if ghostty_cli
                    else "not detected; manual display layer still supported"
                ),
            }
        )
        try:
            self.registry.home.mkdir(parents=True, exist_ok=True)
            writable = os.access(str(self.registry.home), os.W_OK)
        except OSError:
            writable = False
        checks.append(
            {
                "name": "registry",
                "status": "ok" if writable else "error",
                "detail": str(self.registry.home),
            }
        )
        state = self.registry.read()
        checks.append(
            {
                "name": "state",
                "status": "ok",
                "detail": "{} projects, {} agents, {} unread".format(
                    len(state["projects"]),
                    len(state["agents"]),
                    sum(
                        event["read_state"] == "unread"
                        for event in state["events"]
                    ),
                ),
            }
        )
        return {
            "ok": not any(check["status"] == "error" for check in checks),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "checks": checks,
        }

    def wait(
        self,
        name: str,
        expected_status: str,
        project_name: Optional[str] = None,
        timeout: Optional[float] = None,
        interval: float = 0.5,
    ) -> Dict[str, Any]:
        aliases = {
            "done": "completed",
            "green": "review_green",
            "red": "review_red",
            "waiting": "waiting_for_input",
        }
        expected_status = aliases.get(expected_status.lower(), expected_status.lower())
        started = time.monotonic()
        while True:
            agent, project = self._resolve_agent(name, project_name)
            if agent["status"] == expected_status:
                return agent
            if (
                agent["status"] in TERMINAL_STATUSES
                and expected_status != agent["status"]
            ):
                raise MiniCmuxError(
                    "Agent {} reached {} while waiting for {}".format(
                        name, agent["status"], expected_status
                    )
                )
            if timeout is not None and time.monotonic() - started >= timeout:
                emitted: List[Dict[str, Any]] = []

                def record(state: Dict[str, Any]) -> None:
                    current_agent = state["agents"][agent["id"]]
                    current_project = state["projects"][project["id"]]
                    emitted.append(
                        self._event_in_state(
                            state,
                            "timeout",
                            "Timed out waiting for {}".format(expected_status),
                            current_project,
                            current_agent,
                        )
                    )

                self.registry.mutate(record)
                self._notify_events(emitted)
                raise MiniCmuxError(
                    "Timed out waiting for {} to reach {}".format(
                        name, expected_status
                    )
                )
            time.sleep(max(0.1, interval))

    def wait_for_any(
        self,
        name: str,
        expected_statuses: Iterable[str],
        project_name: Optional[str] = None,
        timeout: Optional[float] = None,
        interval: float = 0.5,
    ) -> Dict[str, Any]:
        aliases = {
            "done": "completed",
            "green": "review_green",
            "red": "review_red",
            "waiting": "waiting_for_input",
        }
        expected = {
            aliases.get(status.lower(), status.lower()) for status in expected_statuses
        }
        started = time.monotonic()
        while True:
            agent, _ = self._resolve_agent(name, project_name)
            if agent["status"] in expected:
                return agent
            if agent["status"] in TERMINAL_STATUSES:
                raise MiniCmuxError(
                    "Agent {} reached {} while waiting for {}".format(
                        name, agent["status"], ", ".join(sorted(expected))
                    )
                )
            if timeout is not None and time.monotonic() - started >= timeout:
                raise MiniCmuxError(
                    "Timed out waiting for {} to reach one of {}".format(
                        name, ", ".join(sorted(expected))
                    )
                )
            time.sleep(max(0.1, interval))

    def cleanup(self, project_name: str) -> Dict[str, Any]:
        return self.close_project(project_name)

    def attach_project(self, project_name: str) -> Dict[str, Any]:
        project = self.get_project(project_name)
        if not self.tmux.session_exists(project["tmux_session"]):
            raise MiniCmuxError(
                "tmux session {} is missing".format(project["tmux_session"])
            )
        return project

    def attach_command(self, project: Dict[str, Any]) -> str:
        command = self.tmux.base_command + [
            "attach-session",
            "-t",
            project["tmux_session"],
        ]
        return shlex.join(command)

    def watch(self, interval: float = 1.0, once: bool = False) -> None:
        while True:
            self.reconcile()
            if once:
                return
            time.sleep(max(0.1, interval))
