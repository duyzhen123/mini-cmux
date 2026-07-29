# Vendor-neutral workers and reliable hook adapters

This document defines how any local worker can participate in a mini-cmux
project without depending on Pi, Claude, Codex, or another specific coding
agent.

It is intended for teams integrating:

- An approved internal coding-agent CLI
- A wrapper around an approved internal model API
- A deterministic build, test, review, or deployment script
- A long-running service operator
- A human-operated shell
- A future agent product with a native hook or extension system

## 1. Architecture boundary

mini-cmux is not an agent implementation:

```text
Ghostty = terminal presentation
tmux = persistent PTY and process runtime
mini-cmux = identity, routing, events, attention, and orchestration
worker = the process that performs the assigned work
```

A worker is required for autonomous work. Without an agent or model-backed
worker, mini-cmux can still coordinate deterministic scripts and human shells,
but it does not provide planning or coding intelligence.

Do not build a terminal UI or a tmux replacement as part of a worker adapter.
The adapter boundary is only:

```text
assignment in
artifacts produced
status out
```

## 2. Worker capability levels

Classify a proposed worker before integrating it:

| Level | Worker | Can receive natural-language tasks? | Typical use |
| --- | --- | --- | --- |
| 0 | Human shell | Only through the human | Investigation and intervention |
| 1 | Deterministic command | No | Tests, builds, lint, deploy, policy checks |
| 2 | Interactive approved agent CLI | Yes | Planning, implementation, review |
| 3 | Internal model worker | Yes | Company-controlled agent behavior |

mini-cmux can host all four levels in the same project.

Examples:

```text
planner       → internal model worker
backend       → approved coding-agent CLI
unit-tests    → deterministic test wrapper
security      → deterministic scanner plus human review
operator      → human shell
observer      → log-following process
```

This is a heterogeneous team. Members do not need the same executable or
implementation language.

## 3. Minimum worker contract

Every managed worker needs:

| Contract field | Requirement |
| --- | --- |
| Stable address | A unique mini-cmux agent name |
| Launch command | An executable command accepted by `agent create` |
| Working directory | The project directory or a declared subdirectory |
| Assignment transport | Terminal input for interactive workers, launch arguments for one-shot jobs |
| Artifact contract | Declared files, directories, or reports it owns |
| Status contract | Hooks preferred; exact markers are the fallback |
| Failure behavior | Explicit failure hook and meaningful process exit |
| Restart behavior | Safe to rerun, or clearly documented as non-idempotent |

An interactive worker must continue reading from its terminal after startup:

```text
start process
wait for assignment
perform assignment
report state
wait for another assignment
```

A one-shot worker may exit after one job:

```text
start process
perform fixed job
report result
exit
```

The built-in `workflow` requires long-running planner, implementer, and
verifier processes because it dispatches assignments after the panes have
already been created.

## 4. Identity available to adapters

mini-cmux injects these environment variables into every managed pane:

```text
MINI_CMUX_PROJECT_ID
MINI_CMUX_AGENT_ID
MINI_CMUX_AGENT_NAME
MINI_CMUX_AGENT_ROLE
```

An adapter running inside the pane should use this inherited identity. It
should not search by pane index, window title, command name, or current
directory.

Inside a managed pane:

```bash
mini-cmux hook working --message "Worker accepted the assignment"
```

Outside a managed pane, the caller must identify the target:

```bash
mini-cmux hook working \
  --project shop-api \
  --agent backend \
  --message "Worker accepted the assignment"
```

## 5. Runtime lifecycle versus task lifecycle

Do not collapse every agent event into “done.”

Runtime lifecycle describes the process:

```text
started → working → idle → process exited
```

Task lifecycle describes the assignment:

```text
assigned
   ├── plan_ready
   ├── completed
   ├── waiting_for_input
   └── failed
```

Review lifecycle describes a decision:

```text
review_green | review_red
```

A native agent “stop,” “turn end,” or “settled” event normally means the
runtime is idle. It does not prove that the project assignment is complete.

Recommended normalization:

| Native signal | mini-cmux hook | Meaning |
| --- | --- | --- |
| Session/process started | `started` | Worker exists |
| Agent begins a turn | `working` | Worker is active |
| Agent settles with no follow-up | `idle` | Runtime is awaiting input |
| Planner explicitly publishes plan | `plan_ready` | Planning barrier satisfied |
| Worker explicitly finishes assignment | `completed` | Task barrier satisfied |
| Worker needs a decision | `waiting` | Human/controller attention required |
| Worker cannot finish | `failed` | Task failed |
| Reviewer accepts result | `review_green` | Review barrier satisfied |
| Reviewer rejects result | `review_red` | Repair is required |
| Process terminates | automatic process exit | Reliability backstop |

## 6. Reliable hook invocation

The basic call is:

```bash
mini-cmux hook completed \
  --source company-agent \
  --event-id session-123:task-456:completed:1 \
  --session-id session-123 \
  --message "Implementation and tests finished"
```

`--source` identifies the adapter. Use a stable, short value:

```text
company-agent
internal-test-runner
security-review
deployment-worker
```

`--event-id` is an adapter-owned idempotency key. A retry using the same
source, agent, and event ID returns the original event instead of creating a
duplicate notification or lifecycle transition.

Good event IDs include the native session, task, transition, and a monotonic
counter:

```text
session-123:task-456:working:1
session-123:task-456:waiting:2
session-123:task-456:completed:3
```

Do not generate a new ID for each retry. A new ID means a new logical event.

Idempotency receipts are bounded like the registry event tail. They protect
normal delivery retries while the referenced event remains in the retained
2,000-event snapshot. Long-term audit consumers should also deduplicate using
the mini-cmux event `id` and `seq`.

## 7. Delivery guarantees and retry policy

The current local CLI provides:

- Atomic registry mutation under a file lock
- Monotonic event sequence assignment
- Append-only JSONL audit logging
- Bounded idempotency receipts
- Notification only for the first accepted delivery

An adapter should:

1. Construct the event ID once.
2. Invoke the hook using argument-vector execution, not an interpolated shell
   command.
3. Set a short timeout around delivery.
4. Retry temporary failures with the same event ID.
5. Keep the worker’s real process exit code.
6. Log a delivery failure locally.
7. Never report completion before artifacts have been flushed to disk.

Suggested local retry schedule:

```text
attempt 1 → immediately
attempt 2 → after 1 second
attempt 3 → after 2 seconds
```

Three attempts are generally sufficient for a local locked JSON registry.
Adapters requiring stronger guarantees should spool undelivered events to a
local file and replay them with the same event IDs.

## 8. Safe command execution

Native adapters should execute mini-cmux directly:

```text
argv[0] = /approved/path/mini-cmux
argv[1] = hook
argv[2] = completed
...
```

Do not construct:

```text
shell("mini-cmux hook completed --message '" + modelText + "'")
```

Model text, file paths, and messages may contain quotes, dollar signs,
backticks, or shell operators. Pass each value as one argument.

The example shell wrapper uses `"$@"` for the wrapped job and quotes every hook
argument. Native implementations should use their language’s equivalent of
`execve`, `subprocess.run([...])`, `execFile`, or `ProcessBuilder`.

## 9. What hook payloads may contain

Recommended:

- Short status summary
- Native session ID
- Internal task ID
- Adapter name and version
- Test or review outcome
- A relative artifact path

Do not include:

- Full user prompts
- Model transcripts
- API keys or authorization headers
- Invite URLs or capability tokens
- Environment dumps
- Secret file contents
- Unbounded terminal output

The durable event log is operational metadata, not a transcript store.

## 10. Adapter strategies

### Native extension or native hook

Use this when the worker exposes lifecycle events.

```text
native session start → hook started
native agent start → hook working
native agent settled → hook idle
native permission request → hook waiting
explicit task result → completed/failed/review result
```

This is the preferred mature integration because lifecycle reporting is
deterministic and does not depend on the model remembering instructions.

Different agent products expose different native APIs. Normalize at the
mini-cmux boundary rather than exposing vendor event names to workflow code.

### Process wrapper

Use this for a worker without native hooks:

```text
wrapper starts
wrapper reports working
wrapper launches worker
worker exits
wrapper reports completed or failed
wrapper preserves exit status
```

This works well for deterministic one-shot jobs. It is insufficient for
semantic completion of a long-running interactive agent because a successful
turn does not necessarily terminate the process.

See:

```text
examples/hook-adapter/run-job-with-hooks.sh
```

### Explicit worker status API

For an internal model worker, expose a typed operation:

```json
{
  "status": "completed",
  "message": "Implementation and tests finished",
  "event_id": "session-123:task-456:completed:3"
}
```

The worker runtime validates the values and calls mini-cmux. Do not allow
arbitrary event types or arbitrary shell commands.

### Exact terminal markers

For a worker that cannot call hooks:

```text
AGENT_STATUS=PLAN_READY
AGENT_STATUS=DONE
AGENT_STATUS=WAITING_FOR_INPUT
AGENT_STATUS=FAILED
REVIEW_STATUS=GREEN
REVIEW_STATUS=RED
```

The marker must occupy its own terminal line. mini-cmux rejects status text
embedded inside instructions, prose, or echoed prompts.

Markers are a compatibility mechanism. Prefer native hooks for production
adapters.

## 11. Building an internal model worker

If the company has an approved model API but no approved agent CLI, the missing
component is a headless worker—not a mini terminal application.

Minimum architecture:

```text
company-agent
├── reads assignments from terminal input
├── calls approved internal model API
├── maintains bounded conversation state
├── exposes approved file and shell tools
├── enforces permission policy
├── supports cancellation and timeouts
├── records tool and approval audit data
└── reports typed mini-cmux hooks
```

This is a separate security-sensitive project. It requires:

- Model authentication and secret storage
- Tool-call schema validation
- Workspace path restrictions
- Command allow/approval policy
- Context and token management
- Retry and rate-limit behavior
- Human approval handling
- Session persistence
- Audit and incident response

Do not describe mini-cmux itself as providing these capabilities.

## 12. Heterogeneous dynamic team example

Assume the company provides `company-agent`, a test wrapper, and normal shell
access:

```bash
mini-cmux project create shop-api --cwd ~/projects/shop-api

mini-cmux agent create shop-api planner \
  --role planner \
  --command "company-agent --profile planner"

mini-cmux agent create shop-api backend \
  --role implementer \
  --command "company-agent --profile backend"

mini-cmux agent create shop-api tests \
  --role tester \
  --command "exec bash"

mini-cmux agent create shop-api security \
  --role reviewer \
  --command "exec bash"

mini-cmux agent create shop-api observer \
  --role logs \
  --command "exec bash"
```

The team contains different member types:

| Member | Type | Assignment |
| --- | --- | --- |
| planner | Internal agent | Produce `PLAN.md` |
| backend | Internal agent | Implement backend-owned files |
| tests | Deterministic/human shell | Run integration test command |
| security | Scanner/human shell | Review security-sensitive diff |
| observer | Shell | Follow logs and events |

Controller routing:

```text
planner plan_ready
       │
       ▼
backend completed
       │
       ├──────────────┐
       ▼              ▼
tests review result   security review result
       │              │
       └──────┬───────┘
              ▼
     GREEN + GREEN → complete
     any RED → route repair to owning worker
```

mini-cmux provides stable addressing and wait/event primitives. An external
shell or Python controller implements arbitrary dependency graphs.

## 13. Controller rules for arbitrary teams

A controller should maintain a routing table:

| Incoming event | Preconditions | Action |
| --- | --- | --- |
| `agent_completed` from planner | `PLAN.md` exists | Dispatch implementers |
| `agent_completed` from implementer | All implementation barriers complete | Dispatch reviewers |
| `agent_waiting_for_input` | None | Notify human and pause that branch |
| `agent_failed` | Retry budget available | Restart or reroute |
| `review_red` | Artifact identifies owner | Send repair to owner |
| `review_green` | All required reviewers GREEN | Complete project |
| `process_exited` | No terminal task event | Treat as uncertain and inspect |
| `session_lost` | None | Stop routing and repair state |

Persist the event cursor only after the downstream action succeeds:

```bash
mini-cmux --json events \
  --project shop-api \
  --cursor-file ~/.cache/mini-cmux/shop-api-controller.seq \
  --follow
```

This gives the controller at-least-once event observation. Downstream actions
must therefore be idempotent or protected by the event ID.

## 14. Hook maturity checklist

An adapter is ready for internal production when:

- It uses stable mini-cmux agent identity.
- It distinguishes runtime idle from task completion.
- It supplies a stable `--source`.
- It supplies one `--event-id` per logical transition.
- Retries reuse the same event ID.
- It passes arguments without shell interpolation.
- It keeps messages short and sanitized.
- It reports waiting-for-input explicitly.
- It reports completion only after artifacts are durable.
- It preserves the worker’s true process exit status.
- It has a bounded timeout and retry policy.
- It has tests for duplicate delivery.
- It has tests for crash and controller-unavailable behavior.
- It documents installation and removal.
- `mini-cmux doctor` and adapter-specific diagnostics can verify it.

## 15. Recommended adoption path

Phase 1 — validate orchestration without a model:

```bash
./examples/team-demo/run-demo.sh
```

Phase 2 — integrate deterministic jobs:

```text
build
test
lint
security scan
deployment verification
```

Phase 3 — integrate one approved interactive worker:

```text
native hook or wrapper
stable session identity
working/idle/waiting/task-result mapping
```

Phase 4 — add dynamic routing:

```text
event cursor
dependency barriers
repair ownership
human attention
retry budgets
```

Phase 5 — add more worker types behind the same contract.

Workflow code should never need to know whether a member is implemented by an
internal model, an external approved CLI, Python, Go, TypeScript, a shell
script, or a human.

## 16. Industry design references

The adapter pattern is intentionally compatible with established native-hook
approaches:

- [Claude Code hooks](https://code.claude.com/docs/en/hooks) receive structured
  input and run deterministic command, HTTP, or tool handlers.
- [Pi extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)
  subscribe to native session, agent, turn, and tool lifecycle events.
- [cmux agent integrations](https://github.com/manaflow-ai/cmux/blob/main/docs/agent-hooks.md)
  normalize multiple vendor-specific hook and extension systems.

These are design references, not required mini-cmux dependencies.
