# Operating agent teams with mini-cmux

This guide explains how to define, launch, coordinate, observe, recover, and
tear down a local coding-agent team using Ghostty, tmux, and mini-cmux.

mini-cmux is deliberately a control plane, not a model provider or autonomous
multi-agent framework:

```text
Ghostty = visible terminal application
tmux = persistent sessions, windows, panes, PTYs, and processes
mini-cmux = stable identity, routing, status, events, attention, and workflow
project files = durable work products and handoff artifacts
```

The examples use the installed `mini-cmux` command. From a source checkout,
replace it with `./bin/mini-cmux`, or install the command with:

```bash
python3 -m pip install -e .
```

## 1. Mental model

One mini-cmux project corresponds to one named tmux session. Each agent is one
process in one managed tmux pane:

```text
Ghostty tab
└── tmux session: mini-shop-api
    ├── window: main
    │   ├── planner pane
    │   ├── backend pane
    │   └── frontend pane
    ├── window: review
    │   ├── verifier pane
    │   └── security pane
    └── window: logs
        └── observer pane
```

mini-cmux records a stable UUID for every agent. A pane may move from
`main.0` to `main.2`, but the logical name and UUID remain authoritative.

There are two distinct communication planes:

| Plane | Purpose | Mechanism |
| --- | --- | --- |
| Control plane | Prompts, lifecycle, readiness, failures, attention | `agent send`, keys, markers, hooks, events |
| Data plane | Plans, code, tests, review reports, generated artifacts | Shared project directory and version control |

Agents do not secretly exchange messages. A human or controller routes control
messages, while files carry durable work between agents.

## 2. What defines a team

A team definition is currently an operating contract plus a set of
`agent create` commands. There is no YAML manifest parser in the current
version.

Define these fields for every worker:

| Field | Meaning | Example |
| --- | --- | --- |
| Project | Isolation and shared working directory | `shop-api` |
| Name | Stable address used by commands | `backend` |
| Role | Logical responsibility and tmux placement hint | `implementer` |
| Command | Long-running interactive agent process | `pi` |
| Inputs | Files or messages the worker may consume | `PLAN.md` |
| Outputs | Files the worker owns | `src/api.py` |
| Ready signal | Structured terminal marker or hook | `AGENT_STATUS=DONE` |
| Failure signal | Explicit failure or process exit | `AGENT_STATUS=FAILED` |
| Downstream consumer | Who should receive the handoff | `verifier` |

A useful team contract can live in `TEAM.md`:

```markdown
# Team

## planner

- Reads: GOAL.md and repository state
- Writes: PLAN.md
- Completion: AGENT_STATUS=PLAN_READY
- Must not modify source code

## backend

- Reads: GOAL.md and PLAN.md
- Writes: backend-owned source and tests
- Completion: AGENT_STATUS=DONE
- Human decision: AGENT_STATUS=WAITING_FOR_INPUT

## verifier

- Reads: GOAL.md, PLAN.md, tests, and current diff
- Writes: REVIEW.md when useful
- Completion: REVIEW_STATUS=GREEN or REVIEW_STATUS=RED
- Must not silently repair the implementation
```

Good team definitions establish file ownership. Two agents editing the same
file concurrently will conflict even though their terminal panes are isolated.

## 3. Readiness and installation

Run diagnostics before creating a fleet:

```bash
mini-cmux doctor
mini-cmux capabilities
```

Create or choose the working directory and put the objective in a durable
file:

```bash
mkdir -p ~/projects/shop-api
cd ~/projects/shop-api

cat > GOAL.md <<'EOF'
Implement the requested API, add tests, and keep existing behavior compatible.
EOF
```

The goal file is preferable to embedding a long specification in every
terminal prompt. Agents can reread it, reviewers can compare the result to it,
and the file remains available after terminal output scrolls away.

## 4. Create a project and launch a fixed team

Create the project session:

```bash
mini-cmux project create shop-api --cwd ~/projects/shop-api
```

Launch three long-running interactive agents:

```bash
mini-cmux agent create shop-api planner \
  --role planner \
  --command pi

mini-cmux agent create shop-api implementer \
  --role implementer \
  --command pi

mini-cmux agent create shop-api verifier \
  --role verifier \
  --command pi
```

The command must remain able to receive prompts through its terminal. A
one-shot process that immediately exits cannot participate in later handoffs;
restart it or use a long-running agent command.

Inspect the topology:

```bash
mini-cmux agent list --project shop-api
mini-cmux status --project shop-api
```

Open the visible workspace in Ghostty:

```bash
mini-cmux project attach shop-api
```

If automatic attachment is unavailable, print and manually run the command:

```bash
mini-cmux project attach shop-api --print-command
```

## 5. Communication contract

### Controller to agent

Text and Enter are intentionally separate operations:

```bash
mini-cmux agent send planner \
  "Read GOAL.md and write PLAN.md. End with AGENT_STATUS=PLAN_READY." \
  --project shop-api

mini-cmux agent key planner enter --project shop-api
```

This prevents a controller from accidentally executing incomplete text.
Supported special keys are `enter`, `escape`, and `ctrl-c`.

Read recent visible output:

```bash
mini-cmux agent read planner --project shop-api --lines 100
```

Pane capture is for diagnosis and short review feedback. Plans, reports, and
code should be saved as files rather than recovered from terminal history.

### Agent to controller

An agent should print one of these exact markers:

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

Structured markers are the primary source of truth. Process exit is the
secondary source. Prompt shapes and idle-time guesses are not treated as
completion.

An agent wrapper can report status without relying on terminal parsing:

```bash
mini-cmux hook working --message "Running integration tests"
mini-cmux hook waiting --message "Need a database migration decision"
mini-cmux hook completed --message "Implementation and tests finished"
mini-cmux hook review_green --message "Review passed"
```

Managed panes inherit these environment variables, so hooks know their own
identity:

```text
MINI_CMUX_PROJECT_ID
MINI_CMUX_AGENT_ID
MINI_CMUX_AGENT_NAME
MINI_CMUX_AGENT_ROLE
```

Outside a managed pane, qualify the target:

```bash
mini-cmux hook completed \
  --project shop-api \
  --agent implementer \
  --message "Implementation finished"
```

### Agent to agent

The reliable pattern is a controller-mediated handoff:

```text
planner writes PLAN.md
planner reports PLAN_READY
controller observes PLAN_READY
controller tells implementer to read PLAN.md
implementer changes code and tests
implementer reports DONE
controller tells verifier to inspect the diff
```

Do not make agent B scrape agent A's terminal directly. Route a short control
message and let agent B read the durable artifact.

## 6. Built-in planner → implementer → verifier orchestration

The built-in workflow is the easiest way to initiate a standard team:

```bash
mini-cmux workflow \
  --project shop-api \
  --planner planner \
  --implementer implementer \
  --verifier verifier \
  --goal-file GOAL.md \
  --plan-file PLAN.md \
  --max-repairs 1 \
  --timeout 1800
```

The controller performs this sequence:

```text
planner
  ├── receives GOAL.md → PLAN.md assignment
  └── reports PLAN_READY
         │
         ▼
implementer
  ├── reads GOAL.md and PLAN.md
  ├── changes code and runs tests
  └── reports DONE
         │
         ▼
verifier
  ├── reviews the diff and tests
  └── reports GREEN or RED
         │
         ├── GREEN → workflow completes
         └── RED → captured feedback goes to implementer
                    → one repair by default
                    → verifier runs again
```

`--max-repairs 0` makes the first RED terminal. Increase the number carefully;
an unlimited repair loop can waste time without resolving a missing human
decision.

The built-in workflow is sequential. It does not dispatch the implementer
until the planner is ready, and it does not dispatch the verifier until the
implementation reports completion.

## 7. Manual orchestration

Manual control is useful when the team shape or handoff logic does not match
the built-in workflow:

```bash
mini-cmux agent send planner \
  "Create PLAN.md and finish with AGENT_STATUS=PLAN_READY." \
  --project shop-api
mini-cmux agent key planner enter --project shop-api

mini-cmux wait planner \
  --project shop-api \
  --status plan_ready \
  --timeout 900

mini-cmux agent send implementer \
  "PLAN.md is ready. Implement your assigned section and finish with AGENT_STATUS=DONE." \
  --project shop-api
mini-cmux agent key implementer enter --project shop-api

mini-cmux wait implementer \
  --project shop-api \
  --status completed \
  --timeout 1800
```

The same commands can be placed in an internal shell or Python controller.
mini-cmux deliberately exposes primitives instead of requiring one workflow
engine.

## 8. Dynamic teams and parallel work

An arbitrary team can be created by adding named workers:

```bash
mini-cmux agent create shop-api backend \
  --role implementer \
  --command pi

mini-cmux agent create shop-api frontend \
  --role implementer \
  --command pi

mini-cmux agent create shop-api tests \
  --role tester \
  --command pi

mini-cmux agent create shop-api security \
  --role reviewer \
  --command pi

mini-cmux agent create shop-api observer \
  --role logs \
  --command "exec bash"
```

Role placement is intentionally small:

- `verifier`, `reviewer`, `review`, `tester`, or `qa` go to the `review`
  tmux window.
- `log`, `logs`, `observer`, or `monitor` go to `logs`.
- All other roles go to `main`.

Parallel work is safe when ownership is disjoint:

```bash
mini-cmux agent send backend \
  "Own server/ and server tests only. Read PLAN.md; finish with AGENT_STATUS=DONE." \
  --project shop-api
mini-cmux agent key backend enter --project shop-api

mini-cmux agent send frontend \
  "Own web/ and web tests only. Read PLAN.md; finish with AGENT_STATUS=DONE." \
  --project shop-api
mini-cmux agent key frontend enter --project shop-api
```

Wait for both barriers before starting integration review:

```bash
mini-cmux wait backend --project shop-api --status completed --timeout 1800
mini-cmux wait frontend --project shop-api --status completed --timeout 1800

mini-cmux agent send tests \
  "Backend and frontend report done. Run integration tests and finish with REVIEW_STATUS=GREEN or REVIEW_STATUS=RED." \
  --project shop-api
mini-cmux agent key tests enter --project shop-api
```

Recommended dynamic-team rules:

1. Give every worker a unique stable name.
2. State file and directory ownership in every assignment.
3. Use files for substantial outputs and markers for readiness.
4. Establish barriers before integration or review.
5. Route RED feedback to the worker that owns the affected files.
6. Escalate ambiguous decisions to a human with `WAITING_FOR_INPUT`.
7. Avoid allowing multiple agents to commit, rebase, or rewrite shared history
   concurrently unless the project has an explicit Git coordination policy.

## 9. Events, notifications, and attention

Keep a foreground watcher in a separate Ghostty tab when agents are
unattended:

```bash
mini-cmux watch --interval 1
```

The watcher reconciles pane output and process state, records events, and sends
best-effort macOS notifications for important events.

Inspect the durable event stream:

```bash
mini-cmux events --project shop-api
mini-cmux events --project shop-api --follow
```

Filter for controller-relevant signals:

```bash
mini-cmux --json events \
  --project shop-api \
  --type agent_completed \
  --type agent_waiting_for_input \
  --type agent_failed \
  --type review_green \
  --type review_red \
  --cursor-file ~/.cache/mini-cmux/shop-api.seq \
  --follow
```

Each event has a persistent stream ID and monotonic sequence number. A cursor
file lets an external controller resume after it restarts without treating
old events as new.

Important events stay unread until acknowledged:

```bash
mini-cmux attention list --project shop-api
mini-cmux attention jump --project shop-api
mini-cmux attention ack --agent backend --project shop-api
mini-cmux attention ack --all --project shop-api
```

`attention jump` selects the newest relevant tmux pane and acknowledges that
agent's pending attention.

Agents can also create an explicit human notification:

```bash
mini-cmux notify \
  --project shop-api \
  --agent backend \
  --title "Migration decision required" \
  --body "Choose whether to preserve the legacy column"
```

## 10. Human-input and failure protocol

When an agent cannot safely choose:

```text
1. Write a concise question and the available evidence.
2. Print AGENT_STATUS=WAITING_FOR_INPUT.
3. Stop changing state until the controller or human responds.
```

The human can inspect and reply:

```bash
mini-cmux attention jump --project shop-api
mini-cmux agent read backend --project shop-api --lines 200
mini-cmux agent send backend \
  "Preserve the legacy column and add a deprecation note." \
  --project shop-api
mini-cmux agent key backend enter --project shop-api
```

If a process exits, mini-cmux records its exit code. A clean process exit is a
backstop, not a substitute for an explicit completion marker.

Inspect and restart a worker under the same logical identity:

```bash
mini-cmux agent read backend --project shop-api --lines 200
mini-cmux agent restart backend --project shop-api
```

Interrupt a stuck worker:

```bash
mini-cmux agent key backend ctrl-c --project shop-api
```

Stop only that pane:

```bash
mini-cmux agent stop backend --project shop-api
```

## 11. Recovery after Ghostty or pane changes

Closing Ghostty does not kill the tmux server:

```bash
mini-cmux project attach shop-api
```

If pane indexes changed, reconcile using stable mini-cmux metadata:

```bash
mini-cmux repair
mini-cmux status --project shop-api
```

Missing panes or sessions become `session_lost` events. mini-cmux does not
silently target a similarly named unrelated pane.

A complete laptop reboot normally ends the tmux server unless an external tmux
persistence mechanism is configured. Ghostty and mini-cmux do not change that
tmux behavior.

## 12. Teardown

Stop a temporary worker while preserving the rest of the project:

```bash
mini-cmux agent stop security --project shop-api
```

Close the whole managed project when the work is complete:

```bash
mini-cmux cleanup --project shop-api
```

Equivalent project command:

```bash
mini-cmux project close shop-api
```

Cleanup kills only the tmux session recorded for that project. It does not
enumerate or terminate unrelated tmux sessions, other Ghostty tabs, or external
processes.

Project and event history remain in the local registry for audit and recovery
diagnosis. Cleanup is runtime teardown, not destructive deletion of source
files or event history.

## 13. Runnable local demo

The repository includes a deterministic demo that does not require a coding
agent or API key:

```bash
./examples/team-demo/run-demo.sh
```

It creates:

```text
project: team-demo
├── planner → writes PLAN.md → PLAN_READY
├── implementer → writes demo_output.txt → DONE
└── verifier → checks the artifact → GREEN
```

The script runs the built-in workflow and leaves the tmux project available
for inspection:

```bash
./bin/mini-cmux status --project team-demo
./bin/mini-cmux events --project team-demo
./bin/mini-cmux project attach team-demo
```

Tear it down:

```bash
./bin/mini-cmux cleanup --project team-demo
```

Run the demo with a different project name:

```bash
./examples/team-demo/run-demo.sh my-demo
./bin/mini-cmux cleanup --project my-demo
```

## 14. Production operating checklist

Before dispatch:

- Confirm `mini-cmux doctor` is healthy.
- Store the objective in `GOAL.md`.
- Define role responsibilities, file ownership, and output markers.
- Use unique agent names within the project.
- Start a watcher for unattended work.
- Set timeouts around waits and workflows.

During execution:

- Treat markers and hooks as lifecycle truth.
- Treat files and Git diffs as work-product truth.
- Inspect unread attention rather than scraping every pane.
- Pause and route human decisions explicitly.
- Keep parallel write ownership disjoint.

Before completion:

- Require verifier GREEN.
- Inspect the final diff and test results.
- Acknowledge or resolve remaining attention events.
- Stop temporary agents or clean up the project session.
- Preserve source, plan, and review artifacts according to project policy.

## 15. Current boundaries

mini-cmux currently does not provide:

- A Swift or custom terminal UI
- Automatic Ghostty tab creation or tab-ID management
- A team manifest parser
- Peer-to-peer agent messaging
- An always-running daemon
- Arbitrary dependency-graph scheduling
- Git worktree creation or merge coordination
- Remote workers or multi-user collaboration
- Direct model-provider integration

For the standard three-role lifecycle, use `mini-cmux workflow`. For dynamic
teams, use the stable agent commands, explicit status contract, shared files,
and an external shell or Python controller to implement the desired routing.
