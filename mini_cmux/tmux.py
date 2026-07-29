"""Small, argument-safe adapter around the tmux CLI."""

from __future__ import annotations

import os
import shlex
import subprocess
import uuid
from typing import Any, Dict, List, Optional

from .errors import TmuxError


PANE_FORMAT = "\t".join(
    [
        "#{pane_id}",
        "#{session_name}",
        "#{window_name}",
        "#{window_index}",
        "#{pane_index}",
        "#{pane_dead}",
        "#{pane_dead_status}",
        "#{pane_pid}",
        "#{pane_title}",
        "#{@mini_cmux_agent_id}",
    ]
)


class Tmux:
    def __init__(
        self,
        binary: Optional[str] = None,
        socket_name: Optional[str] = None,
        socket_path: Optional[str] = None,
    ) -> None:
        self.binary = binary or os.environ.get("MINI_CMUX_TMUX_BIN", "tmux")
        self.socket_name = socket_name or os.environ.get(
            "MINI_CMUX_TMUX_SOCKET_NAME"
        )
        self.socket_path = socket_path or os.environ.get(
            "MINI_CMUX_TMUX_SOCKET_PATH"
        )
        if self.socket_name and self.socket_path:
            raise TmuxError(
                "Set only one of MINI_CMUX_TMUX_SOCKET_NAME and "
                "MINI_CMUX_TMUX_SOCKET_PATH"
            )

    @property
    def base_command(self) -> List[str]:
        command = [self.binary]
        if self.socket_path:
            command.extend(["-S", self.socket_path])
        elif self.socket_name:
            command.extend(["-L", self.socket_name])
        return command

    def run(
        self,
        *arguments: str,
        input_text: Optional[str] = None,
        check: bool = True,
        interactive: bool = False,
    ) -> subprocess.CompletedProcess:
        kwargs: Dict[str, Any] = {"text": True, "check": False}
        if input_text is not None:
            kwargs["input"] = input_text
        if not interactive:
            kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE})
        try:
            result = subprocess.run(self.base_command + list(arguments), **kwargs)
        except FileNotFoundError:
            raise TmuxError(
                "tmux is not installed or MINI_CMUX_TMUX_BIN is invalid"
            )
        if check and result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            raise TmuxError(
                "tmux {} failed: {}".format(" ".join(arguments[:2]), detail)
            )
        return result

    def session_exists(self, session: str) -> bool:
        return self.run("has-session", "-t", session, check=False).returncode == 0

    def version(self) -> str:
        return self.run("-V").stdout.strip()

    def create_session(self, session: str, cwd: str, project_id: str) -> str:
        result = self.run(
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            session,
            "-n",
            "shell",
            "-c",
            cwd,
        )
        pane = result.stdout.strip()
        self.run("set-option", "-t", session, "@mini_cmux_project_id", project_id)
        self.run("set-option", "-w", "-t", "{}:shell".format(session), "remain-on-exit", "on")
        self.run(
            "set-option", "-w", "-t", "{}:shell".format(session), "automatic-rename", "off"
        )
        return pane

    def kill_session(self, session: str, missing_ok: bool = False) -> None:
        result = self.run("kill-session", "-t", session, check=False)
        if result.returncode and not missing_ok:
            raise TmuxError((result.stderr or "Cannot close tmux session").strip())

    def window_names(self, session: str) -> List[str]:
        result = self.run("list-windows", "-t", session, "-F", "#{window_name}")
        return [line for line in result.stdout.splitlines() if line]

    @staticmethod
    def window_for_role(role: str) -> str:
        normalized = role.lower().replace("_", "-")
        if normalized in {"verifier", "reviewer", "review", "tester", "qa"}:
            return "review"
        if normalized in {"log", "logs", "observer", "monitor"}:
            return "logs"
        return "main"

    def create_agent_pane(
        self,
        session: str,
        project_id: str,
        agent_id: str,
        name: str,
        role: str,
        cwd: str,
        command: str,
        output_log: Optional[str] = None,
    ) -> str:
        window = self.window_for_role(role)
        pane_command = self._agent_command(
            project_id, agent_id, name, role, command
        )
        common = ["-d", "-P", "-F", "#{pane_id}", "-c", cwd]
        if window in self.window_names(session):
            result = self.run(
                "split-window",
                *common,
                "-t",
                "{}:{}".format(session, window),
                pane_command,
            )
        else:
            result = self.run(
                "new-window",
                *common,
                "-t",
                session,
                "-n",
                window,
                pane_command,
            )
        pane = result.stdout.strip()
        self._configure_agent_pane(
            pane, project_id, agent_id, name, output_log=output_log
        )
        return pane

    @staticmethod
    def _agent_command(
        project_id: str,
        agent_id: str,
        name: str,
        role: str,
        command: str,
    ) -> str:
        shell = os.environ.get("SHELL") or "/bin/sh"
        return shlex.join(
            [
                "env",
                "MINI_CMUX_PROJECT_ID={}".format(project_id),
                "MINI_CMUX_AGENT_ID={}".format(agent_id),
                "MINI_CMUX_AGENT_NAME={}".format(name),
                "MINI_CMUX_AGENT_ROLE={}".format(role),
                shell,
                "-lc",
                command,
            ]
        )

    def _configure_agent_pane(
        self,
        pane: str,
        project_id: str,
        agent_id: str,
        name: str,
        output_log: Optional[str] = None,
    ) -> None:
        title = "mini-cmux:{}:{}".format(name, agent_id[:8])
        self.run("set-option", "-p", "-t", pane, "@mini_cmux_agent_id", agent_id)
        self.run("set-option", "-p", "-t", pane, "@mini_cmux_project_id", project_id)
        self.run("set-option", "-p", "-t", pane, "@mini_cmux_agent_name", name)
        self.run("select-pane", "-t", pane, "-T", title)
        self.run("set-option", "-w", "-t", pane, "remain-on-exit", "on")
        self.run("set-option", "-w", "-t", pane, "automatic-rename", "off")
        if output_log:
            pipe_command = (
                "while IFS= read -r line; do "
                "case \"$line\" in "
                "AGENT_STATUS=*|REVIEW_STATUS=*) "
                "printf '%%s\\n' \"$line\" ;; "
                "esac; done >> {}"
            ).format(shlex.quote(output_log))
            self.run("pipe-pane", "-O", "-t", pane, pipe_command)
        self.run("select-layout", "-t", pane, "tiled", check=False)

    def restart_agent_pane(
        self,
        pane: str,
        project_id: str,
        agent_id: str,
        name: str,
        role: str,
        cwd: str,
        command: str,
        output_log: Optional[str] = None,
    ) -> None:
        pane_command = self._agent_command(
            project_id, agent_id, name, role, command
        )
        self.run(
            "respawn-pane",
            "-k",
            "-t",
            pane,
            "-c",
            cwd,
            pane_command,
        )
        self._configure_agent_pane(
            pane, project_id, agent_id, name, output_log=output_log
        )

    def list_panes(self, session: str) -> List[Dict[str, Any]]:
        result = self.run("list-panes", "-s", "-t", session, "-F", PANE_FORMAT)
        panes = []
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 10:
                continue
            pane_id, session_name, window_name, window_index, pane_index = fields[:5]
            dead, dead_status, pid, title, agent_id = fields[5:]
            panes.append(
                {
                    "pane_id": pane_id,
                    "session": session_name,
                    "window": window_name,
                    "window_index": int(window_index),
                    "pane_index": int(pane_index),
                    "dead": dead == "1",
                    "dead_status": int(dead_status) if dead_status else None,
                    "pid": int(pid) if pid else None,
                    "title": title,
                    "agent_id": agent_id,
                    "target": "{}:{}.{}".format(
                        session_name, window_index, pane_index
                    ),
                }
            )
        return panes

    def capture(self, pane: str, lines: int) -> str:
        bounded = max(1, min(lines, 2000))
        result = self.run(
            "capture-pane",
            "-p",
            "-J",
            "-t",
            pane,
            "-S",
            "-{}".format(bounded),
        )
        return result.stdout

    def send_text(self, pane: str, text: str) -> None:
        buffer_name = "mini-cmux-{}".format(uuid.uuid4().hex)
        self.run("load-buffer", "-b", buffer_name, "-", input_text=text)
        try:
            self.run("paste-buffer", "-d", "-b", buffer_name, "-t", pane)
        finally:
            self.run("delete-buffer", "-b", buffer_name, check=False)

    def send_key(self, pane: str, key: str) -> None:
        keys = {
            "enter": "Enter",
            "escape": "Escape",
            "ctrl-c": "C-c",
        }
        try:
            tmux_key = keys[key.lower()]
        except KeyError:
            raise TmuxError("Unsupported key {!r}".format(key))
        self.run("send-keys", "-t", pane, tmux_key)

    def select_pane(self, pane: str) -> None:
        self.run("select-window", "-t", pane)
        self.run("select-pane", "-t", pane)

    def kill_pane(self, pane: str, missing_ok: bool = False) -> None:
        result = self.run("kill-pane", "-t", pane, check=False)
        if result.returncode and not missing_ok:
            raise TmuxError((result.stderr or "Cannot close tmux pane").strip())

    def attach(self, session: str) -> int:
        return self.run("attach-session", "-t", session, interactive=True).returncode

    def switch_client(self, session: str) -> None:
        self.run("switch-client", "-t", session)
