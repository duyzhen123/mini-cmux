# mini-cmux

`mini-cmux` is a thin, local control plane for coding agents running in tmux.
It assumes Ghostty and tmux are already installed and approved.

It does not render terminals, create PTYs, or replace tmux:

```text
Ghostty = visible macOS terminal UI
tmux    = persistent session/window/pane/process runtime
mini-cmux = identity, lifecycle, status, events, notifications, and workflow
```

## What is implemented

- Project names mapped to isolated tmux sessions.
- Stable UUID-backed agent names mapped to current tmux panes.
- Pane metadata repair using tmux user options, not pane IDs alone.
- Agent creation, listing, text paste, special keys, bounded capture, focus,
  stop, and cleanup.
- Atomic JSON registry and append-only JSONL event history.
- Explicit `AGENT_STATUS` and `REVIEW_STATUS` marker parsing.
- Incremental per-agent tmux output pipes, with bounded screen-capture fallback.
- Process-exit and missing-session detection.
- Attention/unread state, acknowledged when an agent is focused.
- Attention queue commands to list, acknowledge, and jump to the latest unread.
- Monotonic event sequences, cursor files, filtering, and gap detection.
- A generic hook command usable by any shell-based coding agent.
- Restart of a dead or stopped worker using its stored launch command.
- Best-effort native macOS notifications through `osascript`.
- Foreground event watcher and `wait` command.
- Planner → implementer → verifier workflow with one configurable RED repair
  loop.
- Manual Ghostty fallback: attach a Ghostty tab to the printed tmux command.
- Readiness diagnostics and a machine-readable capability report.

There are no third-party Python runtime dependencies.

## Run from this checkout

```bash
./bin/mini-cmux --help
```

Or install the console command:

```bash
python3 -m pip install -e .
mini-cmux --help
```

State defaults to:

```text
~/.local/state/mini-cmux/registry.json
~/.local/state/mini-cmux/events.jsonl
```

Set `MINI_CMUX_HOME` to use a different state directory.

## Basic workflow

Both compact and explicit agent-create forms are supported:

```bash
mini-cmux project create calculator --cwd ~/projects/calculator

mini-cmux agent create calculator planner \
  --role planner \
  --command pi

mini-cmux agent create \
  --project calculator \
  --name implementer \
  --role implementer \
  --command pi

mini-cmux agent send planner \
  "Read GOAL.md and write PLAN.md. End with AGENT_STATUS=PLAN_READY." \
  --project calculator

# Text paste intentionally does not press Enter.
mini-cmux agent key planner enter --project calculator

mini-cmux wait planner --status plan_ready --project calculator
mini-cmux agent read planner --lines 100 --project calculator
mini-cmux agent focus planner --project calculator
```

`--role` defaults to the agent name in the compact form. Planner and
implementer roles are placed in a `main` tmux window; verifier/reviewer roles
are placed in `review`; log/monitor roles are placed in `logs`. Additional
agents split the matching window using tmux's tiled layout.

## Commands

```text
mini-cmux project create NAME [--cwd PATH]
mini-cmux project list
mini-cmux project attach NAME [--print-command]
mini-cmux project close NAME

mini-cmux agent create PROJECT NAME --command COMMAND [--role ROLE]
mini-cmux agent create --project PROJECT --name NAME --role ROLE --command COMMAND
mini-cmux agent list [--project PROJECT]
mini-cmux agent send NAME TEXT [--project PROJECT]
mini-cmux agent key NAME {enter,escape,ctrl-c} [--project PROJECT]
mini-cmux agent read NAME [--lines 100] [--project PROJECT]
mini-cmux agent focus NAME [--project PROJECT]
mini-cmux agent stop NAME [--project PROJECT]
mini-cmux agent restart NAME [--project PROJECT]

mini-cmux status [--project PROJECT]
mini-cmux events [--follow] [--cursor-file PATH] [--after-seq N]
                 [--type TYPE] [--unread] [--project PROJECT] [--agent AGENT]
mini-cmux hook STATUS [--message TEXT] [--session-id ID]
mini-cmux notify --title TITLE [--body BODY] [--project PROJECT] [--agent AGENT]
mini-cmux attention list [--project PROJECT] [--agent AGENT]
mini-cmux attention ack (--id ID | --agent AGENT | --all) [--project PROJECT]
mini-cmux attention jump [--project PROJECT]
mini-cmux wait AGENT --status STATUS [--project PROJECT] [--timeout SECONDS]
mini-cmux watch [--interval SECONDS] [--once]
mini-cmux repair
mini-cmux doctor
mini-cmux capabilities
mini-cmux cleanup --project PROJECT
```

Use the global `--json` option before the command for machine-readable output:

```bash
mini-cmux --json status --project calculator
```

Agent names only need `--project` when the same name exists in multiple
projects. Ambiguous operations fail safely and list the matching projects.

## Status markers and notifications

Supported explicit markers:

```text
AGENT_STATUS=READY
AGENT_STATUS=WORKING
AGENT_STATUS=WAITING_FOR_INPUT
AGENT_STATUS=PLAN_READY
AGENT_STATUS=DONE
AGENT_STATUS=FAILED
REVIEW_STATUS=GREEN
REVIEW_STATUS=RED
```

Explicit markers are the primary source of truth. Process exit is the second
source. mini-cmux does not treat prompt patterns or idle-time guesses as
completion.

Managed panes also inherit `MINI_CMUX_PROJECT_ID`, `MINI_CMUX_AGENT_ID`,
`MINI_CMUX_AGENT_NAME`, and `MINI_CMUX_AGENT_ROLE`. Any agent or hook running in
that pane can report state without an ambiguous name lookup:

```bash
mini-cmux hook working --message "Running tests"
mini-cmux hook waiting --message "Approval required"
mini-cmux hook completed --message "Implementation finished"
mini-cmux hook review_green --session-id native-session-123
```

Supported hook statuses are `started`, `running`, `working`, `ready`, `idle`,
`waiting`, `waiting_for_input`, `done`, `completed`, `failed`, `green`,
`review_green`, `red`, and `review_red`.

Every important event is written to the registry and JSONL event log. The
agent's attention flag remains set until `agent focus` acknowledges its
events. On macOS, important events also trigger a best-effort notification
whose body includes the corresponding focus command.

For immediate unattended notifications, keep the lightweight watcher running
in any shell:

```bash
mini-cmux watch
```

`events --follow` also reconciles status continuously while streaming events.
Set `MINI_CMUX_DISABLE_NOTIFICATIONS=1` to disable native notifications.

For a reconnectable consumer, persist the last successfully processed sequence:

```bash
mini-cmux --json events \
  --cursor-file ~/.cache/mini-cmux/events.seq \
  --type agent_completed \
  --type agent_waiting_for_input \
  --follow
```

Events are kept in the atomic registry and appended to a JSONL audit log. Each
event has a monotonic `seq` and persistent `stream_id`. The JSONL file rotates
at 16 MiB; the registry retains the latest 2,000 events. A cursor older than the
retained snapshot produces an explicit gap warning and should be followed by a
fresh `mini-cmux status`.

Unread attention can be handled without a custom sidebar:

```bash
mini-cmux attention list
mini-cmux attention jump
mini-cmux attention ack --agent implementer --project calculator
```

`attention jump` selects the related tmux pane, attaches or switches the tmux
client, and acknowledges that agent's pending attention.

## Controller-worker workflow

Create three long-running interactive agent panes, then run:

```bash
mini-cmux workflow \
  --project calculator \
  --planner planner \
  --implementer implementer \
  --verifier verifier \
  --goal-file GOAL.md \
  --plan-file PLAN.md \
  --max-repairs 1
```

The controller:

1. asks the planner to write the plan and waits for `PLAN_READY`;
2. asks the implementer to implement/test and waits for `DONE`;
3. asks the verifier to review and waits for `GREEN` or `RED`;
4. routes captured RED feedback to the implementer once by default;
5. finishes only after GREEN.

Use `--timeout` to bound each wait.

## Ghostty integration

Ghostty is deliberately not part of the durable identity model. A Ghostty tab
is simply attached to the project's named tmux session:

```bash
mini-cmux project attach calculator
```

When stdout is not interactive, or when `--print-command` is passed, the
command prints the exact `tmux attach-session` command instead. This is the
documented fallback for Ghostty builds without a stable external tab API.

Closing Ghostty does not kill the tmux session. Open a new Ghostty tab and run
the attach command to return to it.

## Recovery and safety

Each managed pane stores its stable agent UUID in the tmux
`@mini_cmux_agent_id` pane option. `mini-cmux repair` reconciles registry
targets after tmux pane indices change. Missing sessions or panes become
`session_lost` events instead of being silently recreated.

`agent stop` kills only the selected managed pane. `project close` and
`cleanup --project` kill only that project's named tmux session; unrelated
tmux sessions are never enumerated or modified.

`agent send` uses an isolated tmux buffer and `paste-buffer`, so arbitrary text
is pasted literally. It never appends Enter. Special keys use a fixed
allow-list.

Pane capture is bounded to 2,000 lines. Each managed agent also has an
incremental `tmux pipe-pane` marker log so repeated markers and markers between
watcher polls are not lost. The pipe stores only lines containing
`AGENT_STATUS` or `REVIEW_STATUS`, not the full terminal transcript. Screen
capture remains the fallback for output emitted before the pipe is attached.

A dead or stopped worker can be relaunched from its saved command:

```bash
mini-cmux agent restart implementer --project calculator
```

Run the readiness checks before first use:

```bash
mini-cmux doctor
mini-cmux capabilities
```

The detailed comparison with upstream cmux is in
[`docs/CMUX_CONTROL_PLANE_AUDIT.md`](docs/CMUX_CONTROL_PLANE_AUDIT.md).

For team design, orchestration, communication, notifications, recovery,
dynamic workers, teardown, and a runnable demonstration, see
[`docs/AGENT_TEAMS_GUIDE.md`](docs/AGENT_TEAMS_GUIDE.md).

Run the deterministic three-agent demo without a model or API key:

```bash
./examples/team-demo/run-demo.sh
./bin/mini-cmux cleanup --project team-demo
```

## Environment controls

```text
MINI_CMUX_HOME                 state directory
MINI_CMUX_TMUX_BIN             tmux executable
MINI_CMUX_TMUX_SOCKET_NAME     optional isolated tmux -L socket name
MINI_CMUX_TMUX_SOCKET_PATH     optional isolated tmux -S socket path
MINI_CMUX_DISABLE_NOTIFICATIONS=1
```

The socket options are primarily useful for tests and isolated environments.
Set only one of them.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite includes unit tests with an in-memory tmux adapter and a real
integration test against an isolated temporary tmux socket. It never connects
to the user's default tmux server.
