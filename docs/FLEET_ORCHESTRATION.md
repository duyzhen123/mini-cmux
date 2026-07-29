# Maintainable fleet orchestration

This document defines the recommended architecture for coordinating arbitrary
worker teams with mini-cmux. It is guidance and a roadmap. Commands marked
**target contract** are not implemented yet.

The design starts from one decision:

> Models may propose work, but deterministic code owns assignment, routing,
> retries, and completion.

That keeps the system understandable when workers are implemented by different
agent products, deterministic scripts, or humans.

## 1. System boundary

```text
Ghostty                 visible terminal UI
tmux                    persistent process and pane runtime
mini-cmux               worker identity, transport, events, and attention
deterministic scheduler task state, allocation, barriers, retries, and policy
worker adapter          vendor-specific input and lifecycle normalization
worker                  performs one assigned task and produces artifacts
```

The scheduler may live inside mini-cmux or in a small external Python program.
It does not need to be a daemon at first. It does need to be the single
authority that decides orchestration state.

An LLM is useful for producing a plan or dependency graph. It should not drive
the execution loop directly:

```text
human goal
   ↓
planner worker produces PLAN.md or task graph
   ↓
deterministic scheduler validates and executes the graph
   ↓
workers produce artifacts and task-correlated events
   ↓
scheduler advances barriers or asks a human
```

## 2. Why an “orchestrator agent” spawns its own subagents

This failure is structural:

1. Native subagents are visible as a one-call tool inside the model's current
   conversation.
2. Existing tmux workers are external processes. Unless a current roster and a
   delegation command are included in the assignment, the model cannot see or
   address them.
3. A roster sent only at startup eventually disappears through context
   compaction.
4. Manual delegation currently takes several operations, while a native
   subagent often takes one.
5. The word “agent” describes both native conversation-local subagents and
   registered mini-cmux processes, encouraging the model to conflate them.

Repeatedly prompting “do not spawn subagents” does not repair these mechanics.
The maintainable solution is to remove fleet authority from the model:

- Call registered processes **workers** in orchestration prompts.
- Put a compact live roster in every assignment.
- Provide one deterministic assignment operation.
- Accept task progress only through the scheduler's task state machine.
- Give workers an explicit way to report `blocked` or request a capability.

## 3. Non-negotiable invariants

### One scheduling authority

Only the scheduler may:

- create an assignment;
- choose or create a worker;
- increment an attempt;
- retry, cancel, or reassign work;
- declare a dependency barrier satisfied;
- mark the overall run complete.

A worker may report facts. It does not mutate another worker or advance the
workflow directly.

### Task attempts, not worker status, are the durable unit

The minimum identity for an assignment is:

```text
(task_id, attempt)
```

The minimum durable assignment record is:

```text
task_id
attempt
required_capabilities
assigned_worker_id
lease_expires_at
status
input_artifacts
output_artifacts
```

`attempt` is a monotonically increasing epoch. A delayed completion from
attempt 1 must never complete attempt 2, even if both attempts used the same
worker.

Worker status such as `idle` or `working` is operational information. It is
not proof that a particular task completed.

### Every accepted transition is correlated

A task event must carry:

```text
project_id
task_id
attempt
worker_id
event_id
event_type
```

The scheduler accepts it only when:

1. the project and task exist;
2. the attempt is current;
3. the worker holds the current assignment;
4. the transition is valid from the current task state;
5. the event ID has not already been applied;
6. required output artifacts exist before a success transition.

Old, duplicate, unattributed, or invalid events are recorded for diagnosis but
do not advance the task.

mini-cmux pane identity is useful routing evidence, not authentication. A
child process may inherit the parent pane's environment variables. If strict
process or tool isolation is required, enforce it in the approved worker
harness, sandbox, credentials, and filesystem permissions.

### Artifacts are the data contract

Plans, patches, test reports, and reviews belong in files or version control.
Terminal output is an observation and debugging surface.

Do not make scheduler decisions by scraping rendered pane output. Marker
parsing remains a compatibility adapter, not the preferred fleet protocol.

### Blocking is explicit

`blocked` is different from failure, timeout, or runtime idle. A blocked event
must include a reason such as:

```text
human_decision
missing_capability
missing_input
policy_denied
external_dependency
```

The scheduler may route the request, notify a human, or end the branch. It
must not hide a blocked task behind repeated retries.

## 4. Small state machine

Keep the task model deliberately small:

```text
queued
  ↓
assigned(attempt=N, lease)
  ↓
running
  ├── completed
  ├── failed
  ├── blocked
  └── lease_expired → queued(attempt=N+1) or failed
```

`completed`, `failed`, and `cancelled` are terminal. `blocked` is a stable
paused state that requires an explicit scheduler or human decision.

Do not infer `completed` from:

- a quiet prompt;
- runtime idle;
- a clean process exit;
- a pane title;
- the same worker having completed an earlier assignment.

Process exit while a task is active is an uncertain failure unless a valid
task completion event was already accepted.

## 5. Assignment envelope

Every worker dispatch should contain a compact machine-readable envelope plus
a human-readable instruction. A target envelope is:

```json
{
  "project_id": "shop-api",
  "task_id": "task-0042",
  "attempt": 2,
  "worker_id": "worker-backend",
  "required_capabilities": ["python", "tests"],
  "input_artifacts": ["GOAL.md", "PLAN.md"],
  "output_artifacts": [".mini-cmux/tasks/task-0042/result.md"],
  "lease_expires_at": "2026-07-29T03:30:00Z"
}
```

The prompt should also include a fresh roster:

```text
FLEET (live, project=shop-api)
  backend   [python, tests]       busy:task-0042
  verifier  [diff-review]         idle
  security  [security-review]     idle

YOUR ASSIGNMENT
  task_id=task-0042 attempt=2
  read: GOAL.md, PLAN.md
  write: .mini-cmux/tasks/task-0042/result.md

LEGAL OUTCOMES
  complete this task
  report blocked with a reason
  request a missing capability from the scheduler
```

The roster is refreshed at each dispatch or scheduler wake-up. It is advisory
context; the registry remains authoritative.

Do not include credentials, secret environment values, or unnecessary pane
scrollback in the envelope.

## 6. Communication model

Use three distinct channels:

| Channel | Carries | Mechanism |
| --- | --- | --- |
| Assignment | Task identity, attempt, inputs, ownership | Scheduler to worker |
| Artifact | Plan, code, review, test result | Files and version control |
| Event | Working, blocked, completed, failed | Correlated hook to scheduler |

A planner does not tell an implementer that it is ready. It writes its plan
and reports completion for its own task. The scheduler validates the artifact,
satisfies the dependency, and assigns the implementer.

A reviewer does not send feedback directly to an implementer. It writes a
review artifact and reports RED. The scheduler creates a new repair task or a
new attempt and routes that artifact to the owner.

This topology gives one auditable route:

```text
worker A → artifact + event → scheduler → assignment → worker B
```

Direct peer messaging can be added later for performance, but it must not be
required for correctness.

## 7. Dynamic teams and cross-vendor workers

A worker should declare capabilities when it is registered:

```text
worker_id
role
capabilities[]
command
env_profile
allowed_paths[]
max_concurrency
```

When a worker needs help, it requests a capability:

```json
{
  "task_id": "task-0042",
  "attempt": 2,
  "request": "independent_code_review",
  "reason": "Authentication changes require independent review"
}
```

The scheduler then:

1. reuses an idle registered worker with the capability;
2. allocates an approved worker command if policy and budget allow;
3. otherwise marks the task blocked and notifies a human.

The narrative should therefore be:

> The implementation worker requests independent review; the scheduler assigns
> an approved Claude, Codex, internal, deterministic, or human reviewer.

It should not be:

> Pi spawns a Claude agent.

Provider choice belongs in scheduler policy, not in worker prompts. Core
orchestration code should never import a provider SDK.

## 8. Native subagents: what can and cannot be enforced

There are two separate concerns:

1. **Fleet correctness:** Can untracked work advance the task graph?
2. **Execution policy:** Is a worker allowed to use its runtime's native
   subagents internally?

Task correlation solves the first concern. It does not prove how the registered
worker computed its artifact.

If native subagents are allowed inside one worker boundary, treat them as an
implementation detail. The scheduler still sees one worker, one assignment,
and one accountable result.

If native subagents are prohibited, enforce that through a runtime or harness
that can actually restrict tools and subprocesses. Pane-output patterns such as
`Task(...)` or spawn messages are useful alerts, but they are not a security
control and must not be the source of task truth.

When a selected runtime cannot provide the required restriction, document the
worker profile as unsuitable for strict-isolation tasks.

## 9. What is safe with the current release

The current release already provides:

- stable project and worker identity;
- tmux routing and bounded capture;
- lifecycle markers and idempotent hook delivery;
- event cursors, attention, and notifications;
- process/session reconciliation;
- a deterministic fixed planner → implementer → verifier workflow.

It does **not** yet provide:

- task records;
- task-correlated hook validation;
- attempts or leases;
- capability declarations and allocation;
- a general dependency scheduler;
- enforcement of worker process or tool policy.

The built-in workflow calls `Controller.dispatch()`, which sends the prompt,
presses Enter, and resets the worker state to `working`. That avoids the
simplest stale-status reuse in that path. It does not correlate a later event
to a task attempt. A delayed event from old work can still be mistaken for
current work.

Manual `agent send` + `agent key` + `wait` is weaker because sending does not
reset task state. Reusing a worker that already reports the awaited status may
make `wait` return before the new assignment finishes.

Until task attempts ship:

1. Prefer the built-in fixed workflow for the included three-stage lifecycle.
2. Use one dedicated worker per concurrent assignment.
3. Do not let an LLM act as the execution scheduler.
4. Put unique assignment IDs in prompts, artifact paths, and hook event IDs.
5. Validate the expected artifact after every wait.
6. Bound every wait and repair loop.
7. Treat multi-round reuse and late hook delivery as best-effort, not
   production-grade task synchronization.

## 10. Target CLI contract

The smallest useful future interface is:

```text
mini-cmux agent create ... --capability python --capability tests
mini-cmux agent dispatch WORKER --task TASK_ID --file ASSIGNMENT.md

mini-cmux task create --project PROJECT --id TASK_ID \
  --requires python --input GOAL.md --output result.md
mini-cmux task assign TASK_ID --worker WORKER
mini-cmux task wait TASK_ID --status completed
mini-cmux task block TASK_ID --reason missing_capability

mini-cmux hook completed --task TASK_ID --attempt N \
  --source ADAPTER --event-id IDEMPOTENCY_KEY
```

This is a contract sketch, not current command documentation.

Machine-readable JSON CLI output should remain the automation boundary. Add an
MCP server only if a real consumer needs it; it should be a thin adapter over
the same controller methods, never a second scheduler.

## 11. Implementation order

Optimize for correctness gained per unit of code:

1. Add a versioned task table to the existing locked JSON registry.
2. Add `task_id`, monotonic `attempt`, assignment owner, lease expiry, and
   terminal status.
3. Add one task dispatch command that persists the assignment before sending a
   prompt and records dispatch failure explicitly.
4. Require task-correlated hooks for task completion; keep old agent hooks for
   lifecycle compatibility.
5. Make waits target a task attempt, not a worker status.
6. Add `blocked` and capability requests.
7. Add worker capability declarations and deterministic pool allocation.
8. Inject the live roster and legal delegation actions into every dispatch.
9. Add renewable leases or heartbeats only if long-running assignments need
   safe lease extension.
10. Add a daemon only when foreground `watch` no longer meets notification
    latency or availability requirements.

Do not start with:

- a custom UI;
- Swift;
- provider-specific core logic;
- a generic autonomous planner;
- peer-to-peer messaging;
- MCP;
- distributed workers;
- a database.

The existing locked JSON registry is sufficient while one local scheduler owns
mutations and the event volume remains bounded. Move to SQLite only after
measured contention, query, or migration requirements justify it.

## 12. Security and isolation

For heterogeneous workers, store policy with the worker profile:

```text
env_profile
allowed_paths
read_only_paths
credential_profile
network_policy
allowed_commands or tools
```

The controller should pass only the selected profile at worker creation.
Examples:

- a reviewer can read the source and write only its review artifact;
- a test worker receives no model credentials;
- a deployment worker receives deployment credentials but cannot edit source;
- provider credentials are not shared between unrelated workers.

These controls require OS, container, sandbox, or worker-harness enforcement.
A JSON registry declaration alone is documentation, not isolation.

## 13. Acceptance tests for mature orchestration

Before calling the task layer reliable, demonstrate:

- a duplicate event does not create a duplicate transition;
- a late event from attempt 1 cannot complete attempt 2;
- a completion from the wrong worker is rejected;
- completion without the required artifact is rejected;
- a scheduler restart resumes from durable task and event state;
- a worker crash becomes an explicit failed or expired assignment;
- a missing capability becomes `blocked`, not an infinite retry;
- a RED review creates a bounded, attributable repair attempt;
- two independent tasks can run without sharing write ownership;
- cleanup removes only managed tmux resources;
- native-subagent detection, when enabled, is labeled advisory.

## 14. Decision summary

For a maintainable local system:

```text
LLM planner       proposes the graph
deterministic code executes the graph
registered worker performs one assignment
task_id + attempt identifies the assignment
artifacts carry results
correlated hooks carry state
tmux carries bytes and processes
Ghostty shows the runtime
```

This makes the problematic state—an orchestrator silently creating its own
team and advancing the official workflow—unrepresentable. The scheduler can
only advance durable tasks through validated transitions from registered
assignments.
