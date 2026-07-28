"""Command-line interface for mini-cmux."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__
from .controller import Controller
from .errors import MiniCmuxError
from .workflow import WorkflowRunner


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _table(headers: List[str], rows: Iterable[Iterable[Any]]) -> None:
    rendered = [["" if item is None else str(item) for item in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in rendered:
        widths = [
            max(width, len(row[index]) if index < len(row) else 0)
            for index, width in enumerate(widths)
        ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rendered:
        print(
            "  ".join(
                (row[index] if index < len(row) else "").ljust(widths[index])
                for index in range(len(headers))
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-cmux",
        description="Control named coding-agent processes in persistent tmux sessions.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="root_command", required=True)

    project = commands.add_parser("project", help="manage project sessions")
    projects = project.add_subparsers(dest="project_command", required=True)
    create_project = projects.add_parser("create", help="create a tmux-backed project")
    create_project.add_argument("name")
    create_project.add_argument("--cwd", default=".")
    projects.add_parser("list", help="list projects")
    attach = projects.add_parser("attach", help="attach to a project session")
    attach.add_argument("name")
    attach.add_argument(
        "--print-command",
        action="store_true",
        help="print the attach command instead of executing it",
    )
    close = projects.add_parser("close", help="close a managed project session")
    close.add_argument("name")

    agent = commands.add_parser("agent", help="manage named agent panes")
    agents = agent.add_subparsers(dest="agent_command", required=True)
    create_agent = agents.add_parser("create", help="launch an agent")
    create_agent.add_argument("project_arg", nargs="?")
    create_agent.add_argument("name_arg", nargs="?")
    create_agent.add_argument("--project", dest="project_option")
    create_agent.add_argument("--name", dest="name_option")
    create_agent.add_argument(
        "--role", help="logical role (defaults to the agent name)"
    )
    create_agent.add_argument("--command", required=True)
    create_agent.add_argument("--cwd")
    list_agents = agents.add_parser("list", help="list agents")
    list_agents.add_argument("--project")
    send = agents.add_parser("send", help="paste text without pressing Enter")
    send.add_argument("name")
    send.add_argument("text")
    send.add_argument("--project")
    key = agents.add_parser("key", help="send a supported special key")
    key.add_argument("name")
    key.add_argument("key", choices=["enter", "escape", "ctrl-c"])
    key.add_argument("--project")
    read = agents.add_parser("read", help="capture bounded recent pane output")
    read.add_argument("name")
    read.add_argument("--lines", type=int, default=100)
    read.add_argument("--project")
    focus = agents.add_parser("focus", help="select and attach to an agent pane")
    focus.add_argument("name")
    focus.add_argument("--project")
    focus.add_argument(
        "--print-command",
        action="store_true",
        help="select the pane and print the attach command",
    )
    stop = agents.add_parser("stop", help="close one managed agent pane")
    stop.add_argument("name")
    stop.add_argument("--project")
    restart = agents.add_parser("restart", help="restart an agent's saved command")
    restart.add_argument("name")
    restart.add_argument("--project")

    status = commands.add_parser("status", help="reconcile and show current state")
    status.add_argument("--project")

    events = commands.add_parser("events", help="show structured attention events")
    events.add_argument("--project")
    events.add_argument("--agent")
    events.add_argument("--follow", action="store_true")
    events.add_argument("--interval", type=float, default=1.0)
    events.add_argument("--after-seq", type=int)
    events.add_argument("--cursor-file")
    events.add_argument("--type", action="append", dest="event_types")
    events.add_argument("--unread", action="store_true")
    events.add_argument("--limit", type=int)

    wait = commands.add_parser("wait", help="wait for an agent status")
    wait.add_argument("name")
    wait.add_argument("--status", required=True)
    wait.add_argument("--project")
    wait.add_argument("--timeout", type=float)
    wait.add_argument("--interval", type=float, default=0.5)

    cleanup = commands.add_parser("cleanup", help="close one managed project safely")
    cleanup.add_argument("--project", required=True)

    watch = commands.add_parser(
        "watch", help="watch tmux output and emit events/notifications"
    )
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--once", action="store_true")

    commands.add_parser("repair", help="reconcile stable IDs with current tmux panes")

    hook = commands.add_parser(
        "hook", help="record a structured lifecycle event from an agent pane"
    )
    hook.add_argument("status")
    hook.add_argument("--agent")
    hook.add_argument("--project")
    hook.add_argument("--message")
    hook.add_argument("--session-id")

    notify = commands.add_parser(
        "notify", help="record attention and send a native notification"
    )
    notify.add_argument("--title", required=True)
    notify.add_argument("--body", default="")
    notify.add_argument("--agent")
    notify.add_argument("--project")

    attention = commands.add_parser(
        "attention", help="list, acknowledge, or jump to unread attention"
    )
    attention_commands = attention.add_subparsers(
        dest="attention_command", required=True
    )
    attention_list = attention_commands.add_parser("list")
    attention_list.add_argument("--project")
    attention_list.add_argument("--agent")
    attention_ack = attention_commands.add_parser("ack")
    attention_ack.add_argument("--id")
    attention_ack.add_argument("--agent")
    attention_ack.add_argument("--project")
    attention_ack.add_argument("--all", action="store_true")
    attention_jump = attention_commands.add_parser("jump")
    attention_jump.add_argument("--project")
    attention_jump.add_argument("--print-command", action="store_true")

    commands.add_parser("capabilities", help="print supported control-plane features")
    commands.add_parser("doctor", help="check tmux, Ghostty, and registry readiness")

    workflow = commands.add_parser(
        "workflow", help="run the planner/implementer/verifier workflow"
    )
    workflow.add_argument("--project", required=True)
    workflow.add_argument("--planner", default="planner")
    workflow.add_argument("--implementer", default="implementer")
    workflow.add_argument("--verifier", default="verifier")
    workflow.add_argument("--goal-file", default="GOAL.md")
    workflow.add_argument("--plan-file", default="PLAN.md")
    workflow.add_argument("--max-repairs", type=int, default=1)
    workflow.add_argument("--timeout", type=float)
    return parser


def _display_project(project: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        _print_json(project)
    else:
        print(
            "{}\t{}\t{}\t{}".format(
                project["name"],
                project["status"],
                project["tmux_session"],
                project["cwd"],
            )
        )


def _display_agents(agents: List[Dict[str, Any]], as_json: bool) -> None:
    if as_json:
        _print_json(agents)
        return
    _table(
        ["PROJECT ID", "AGENT", "ROLE", "STATUS", "TARGET", "ATTENTION"],
        [
            [
                agent["project_id"][:8],
                agent["name"],
                agent["role"],
                agent["status"],
                agent.get("tmux_target"),
                "yes" if agent.get("attention_required") else "",
            ]
            for agent in agents
        ],
    )


def _display_events(events: List[Dict[str, Any]], as_json: bool) -> None:
    if as_json:
        for event in events:
            print(json.dumps(event, sort_keys=True))
        return
    for event in events:
        print(
            "{:<6}  {}  {:<24}  {}/{}  {}{}".format(
                event.get("seq", "-"),
                event["timestamp"],
                event["type"],
                event["project_name"],
                event.get("agent_name") or "-",
                event["message"],
                " [unread]" if event["read_state"] == "unread" else "",
            )
        )


def _read_cursor(path_value: Optional[str]) -> Optional[int]:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise MiniCmuxError("Cannot read event cursor {}: {}".format(path, exc))


def _write_cursor(path_value: Optional[str], events: List[Dict[str, Any]]) -> None:
    if not path_value or not events:
        return
    path = Path(path_value).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            "{}\n".format(max(event.get("seq", 0) for event in events)),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        raise MiniCmuxError("Cannot update event cursor {}: {}".format(path, exc))


def _interactive_attach(
    controller: Controller,
    project: Dict[str, Any],
    print_command: bool,
) -> int:
    command = controller.attach_command(project)
    if print_command or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(command)
        return 0
    return controller.tmux.attach(project["tmux_session"])


def execute(args: argparse.Namespace, controller: Controller) -> int:
    if args.root_command == "project":
        if args.project_command == "create":
            _display_project(
                controller.create_project(args.name, args.cwd), args.json
            )
        elif args.project_command == "list":
            projects = controller.list_projects()
            if args.json:
                _print_json(projects)
            else:
                _table(
                    ["PROJECT", "STATUS", "TMUX SESSION", "CWD"],
                    [
                        [
                            project["name"],
                            project["status"],
                            project["tmux_session"],
                            project["cwd"],
                        ]
                        for project in projects
                    ],
                )
        elif args.project_command == "attach":
            project = controller.attach_project(args.name)
            return _interactive_attach(controller, project, args.print_command)
        elif args.project_command == "close":
            _display_project(controller.close_project(args.name), args.json)
        return 0

    if args.root_command == "agent":
        if args.agent_command == "create":
            project_name = args.project_option or args.project_arg
            agent_name = args.name_option or args.name_arg
            if not project_name or not agent_name:
                raise MiniCmuxError(
                    "agent create requires PROJECT and NAME, either positionally "
                    "or with --project/--name"
                )
            agent = controller.create_agent(
                project_name,
                agent_name,
                args.role or agent_name,
                args.command,
                args.cwd,
            )
            if args.json:
                _print_json(agent)
            else:
                print(
                    "{}\t{}\t{}\t{}".format(
                        agent["name"],
                        agent["status"],
                        agent["tmux_target"],
                        agent["id"],
                    )
                )
        elif args.agent_command == "list":
            _display_agents(controller.list_agents(args.project), args.json)
        elif args.agent_command == "send":
            agent = controller.send_text(args.name, args.text, args.project)
            if args.json:
                _print_json(agent)
        elif args.agent_command == "key":
            agent = controller.send_key(args.name, args.key, args.project)
            if args.json:
                _print_json(agent)
        elif args.agent_command == "read":
            output = controller.read_agent(args.name, args.lines, args.project)
            if args.json:
                _print_json({"agent": args.name, "output": output})
            else:
                sys.stdout.write(output)
        elif args.agent_command == "focus":
            result = controller.focus_agent(args.name, args.project)
            project = result["project"]
            if os.environ.get("TMUX") and not args.print_command:
                controller.tmux.switch_client(project["tmux_session"])
            else:
                return _interactive_attach(
                    controller, project, args.print_command
                )
        elif args.agent_command == "stop":
            agent = controller.stop_agent(args.name, args.project)
            if args.json:
                _print_json(agent)
            else:
                print("{}\t{}".format(agent["name"], agent["status"]))
        elif args.agent_command == "restart":
            agent = controller.restart_agent(args.name, args.project)
            if args.json:
                _print_json(agent)
            else:
                print(
                    "{}\t{}\t{}".format(
                        agent["name"], agent["status"], agent["tmux_target"]
                    )
                )
        return 0

    if args.root_command == "status":
        state = controller.status(args.project)
        if args.json:
            _print_json(state)
        else:
            projects = list(state["projects"].values())
            agents = list(state["agents"].values())
            _table(
                ["PROJECT", "STATUS", "SESSION"],
                [
                    [item["name"], item["status"], item["tmux_session"]]
                    for item in projects
                ],
            )
            if agents:
                print()
                _display_agents(agents, False)
        return 0

    if args.root_command == "events":
        cursor = _read_cursor(args.cursor_file)
        after_seq = max(
            value
            for value in [args.after_seq, cursor, 0]
            if value is not None
        )
        cursor_info = controller.event_cursor_info(after_seq)
        if cursor_info["gap"]:
            print(
                "mini-cmux: event cursor gap; refresh with `mini-cmux status`",
                file=sys.stderr,
            )
        initial = controller.events(
            args.project,
            args.agent,
            after_seq=after_seq,
            event_types=args.event_types,
            unread_only=args.unread,
            limit=args.limit,
        )
        _display_events(initial, args.json)
        _write_cursor(args.cursor_file, initial)
        if initial:
            after_seq = max(event.get("seq", 0) for event in initial)
        if not args.follow:
            return 0
        while True:
            time.sleep(max(0.1, args.interval))
            fresh = controller.events(
                args.project,
                args.agent,
                after_seq=after_seq,
                event_types=args.event_types,
                unread_only=args.unread,
            )
            _display_events(fresh, args.json)
            _write_cursor(args.cursor_file, fresh)
            if fresh:
                after_seq = max(event.get("seq", 0) for event in fresh)

    if args.root_command == "wait":
        agent = controller.wait(
            args.name,
            args.status.lower(),
            args.project,
            args.timeout,
            args.interval,
        )
        if args.json:
            _print_json(agent)
        else:
            print("{}\t{}".format(agent["name"], agent["status"]))
        return 0

    if args.root_command == "cleanup":
        _display_project(controller.cleanup(args.project), args.json)
        return 0

    if args.root_command == "watch":
        controller.watch(args.interval, args.once)
        return 0

    if args.root_command == "repair":
        events = controller.reconcile()
        if args.json:
            _print_json({"events": events})
        else:
            print("Reconciled registry with tmux ({} new events).".format(len(events)))
        return 0

    if args.root_command == "hook":
        result = controller.record_hook(
            args.status,
            message=args.message,
            agent_name=args.agent,
            project_name=args.project,
            session_id=args.session_id,
        )
        if args.json:
            _print_json(result)
        else:
            print(
                "{}\t{}\tseq={}".format(
                    result["agent"]["name"],
                    result["agent"]["status"],
                    result["event"]["seq"],
                )
            )
        return 0

    if args.root_command == "notify":
        event = controller.notify(
            args.title,
            args.body,
            agent_name=args.agent,
            project_name=args.project,
        )
        if args.json:
            _print_json(event)
        else:
            print("{}\tseq={}".format(event["type"], event["seq"]))
        return 0

    if args.root_command == "attention":
        if args.attention_command == "list":
            _display_events(
                controller.attention_events(args.project, args.agent), args.json
            )
            return 0
        if args.attention_command == "ack":
            changed = controller.acknowledge_events(
                event_id=args.id,
                agent_name=args.agent,
                project_name=args.project,
                acknowledge_all=args.all,
            )
            if args.json:
                _print_json({"acknowledged": changed})
            else:
                print("Acknowledged {} event(s).".format(changed))
            return 0
        if args.attention_command == "jump":
            event = controller.latest_attention(args.project)
            if event is None:
                raise MiniCmuxError("No unread attention events")
            if event.get("agent_id"):
                result = controller.focus_agent(
                    event["agent_id"], event["project_id"]
                )
                project = result["project"]
            else:
                project = controller.get_project(event["project_id"])
                controller.acknowledge_events(event_id=event["id"])
            if os.environ.get("TMUX") and not args.print_command:
                controller.tmux.switch_client(project["tmux_session"])
                return 0
            return _interactive_attach(controller, project, args.print_command)

    if args.root_command == "capabilities":
        _print_json(controller.capabilities())
        return 0

    if args.root_command == "doctor":
        report = controller.doctor()
        if args.json:
            _print_json(report)
        else:
            for check in report["checks"]:
                print(
                    "{:<8}  {:<8}  {}".format(
                        check["name"], check["status"], check["detail"]
                    )
                )
        return 0 if report["ok"] else 1

    if args.root_command == "workflow":
        result = WorkflowRunner(controller).run(
            project=args.project,
            planner=args.planner,
            implementer=args.implementer,
            verifier=args.verifier,
            goal_file=args.goal_file,
            plan_file=args.plan_file,
            max_repairs=args.max_repairs,
            timeout=args.timeout,
        )
        if args.json:
            _print_json(result)
        else:
            print(
                "{}\t{}\trepair_rounds={}".format(
                    result["project"], result["status"], result["repair_rounds"]
                )
            )
        return 0

    raise MiniCmuxError("No command selected")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return execute(args, Controller())
    except KeyboardInterrupt:
        return 130
    except MiniCmuxError as exc:
        print("mini-cmux: {}".format(exc), file=sys.stderr)
        return 1
