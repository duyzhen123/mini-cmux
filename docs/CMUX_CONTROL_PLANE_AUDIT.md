# cmux control-plane audit

This audit compares mini-cmux with the public cmux repository at commit
[`664c6e1`](https://github.com/manaflow-ai/cmux/commit/664c6e1fb7e6e84c862d890b207207cc6c442261),
reviewed on 2026-07-28.

The comparison is intentionally behavioral. No cmux source code is copied.

## Fixed architecture boundary

```text
Ghostty = existing display, keyboard, scrollback, and macOS window/tab layer
tmux = existing session, window, pane, PTY, and process layer
mini-cmux = Python identity, lifecycle, events, attention, and workflow layer
```

mini-cmux will not implement Swift/AppKit UI, terminal rendering, a sidebar,
browser embedding, or a replacement control-socket server.

## Capability map

| cmux behavior | Importance | mini-cmux implementation | Status |
| --- | --- | --- | --- |
| Persistent logical workspace | Critical | Named tmux session plus project registry | Ready |
| Splits and process lifecycle | Critical | tmux windows/panes and `remain-on-exit` | Ready |
| Stable agent addressing | Critical | UUID stored in registry and tmux pane user options | Ready |
| Literal text send | Critical | Isolated tmux buffer plus `paste-buffer`; Enter is separate | Ready |
| Special keys | Critical | Fixed allow-list for Enter, Escape, and Ctrl-C | Ready |
| Bounded screen capture | Critical | `capture-pane`, capped at 2,000 lines | Ready |
| Reliable incremental status capture | Critical | Per-agent marker-only `pipe-pane` log with capture fallback | Ready |
| Structured agent lifecycle | Critical | Explicit markers and `mini-cmux hook STATUS` | Ready |
| Process-exit detection | Critical | tmux `pane_dead` and exit status | Ready |
| Attention/unread queue | Critical | Durable events, list/ack/jump commands | Ready |
| Native notification | Critical | Best-effort macOS notification with focus command | Ready |
| Reconnectable events | High | Monotonic durable sequence, cursor file, filters, gap warning | Ready |
| Agent restart | High | Re-run stored command in the same stable pane or recreate pane | Ready |
| Recovery after Ghostty closes | Critical | tmux survives; `project attach` reconnects | Ready |
| Registry reconciliation | Critical | Repairs pane indexes from stable tmux metadata | Ready |
| Planner/worker/reviewer routing | High | Structured workflow with one configurable RED repair loop | Ready |
| Environment/readiness report | High | `doctor` and `capabilities` | Ready |
| Ghostty tab creation/focus | Optional | Manual attach command; no unstable tab ID dependency | Manual |
| Laptop reboot process survival | Optional | Requires tmux/server persistence outside mini-cmux | External |
| Native sidebar and notification rings | UI-only | Text status and attention queue instead | Excluded |
| Embedded/scriptable browser | UI-only | None | Excluded |
| Swift Unix socket server | Not needed | Direct local CLI, locked JSON state, and tmux adapter | Excluded |
| Remote workers and cloud VMs | Out of scope | None | Excluded |
| Mobile UI, Feed cards, approvals | Out of scope | None | Excluded |
| Agent hibernation | Later optimization | None | Postponed |
| Vendor-specific hook installers | Optional | Generic hook protocol works with any shell agent | Postponed |

## Reliability decisions adopted

The cmux documentation emphasizes several principles that also apply without a
custom UI:

1. Bind an agent by a durable controller-owned identifier, not by title or
   current pane index.
2. Prefer explicit hook/marker state over prompt and idle heuristics.
3. Use owned process exit as the secondary source of truth.
4. Treat live event delivery as a convenience and durable state as
   authoritative.
5. Keep text send separate from Enter and special keys.
6. Never let an attention-navigation command silently target an ambiguous
   agent name.

mini-cmux implements those principles with tmux pane user options, an atomic
registry, incremental pane logs, process-exit reconciliation, and monotonic
event sequences.

## Intentional differences

cmux owns its app UI and can visually ring a pane, light a sidebar tab, and
focus an AppKit window. mini-cmux owns none of those surfaces. Its equivalent
is:

```text
macOS banner
    + durable unread event
    + mini-cmux attention list
    + mini-cmux attention jump
    + tmux pane selection/attach
```

cmux streams events over a Unix socket. mini-cmux does not need a second local
server: `events --follow` polls the locked authoritative registry, and
`--cursor-file` provides reconnectable consumption across CLI restarts.

## Source documents

- [cmux README](https://github.com/manaflow-ai/cmux/blob/main/README.md)
- [cmux CLI contract](https://github.com/manaflow-ai/cmux/blob/main/docs/cli-contract.md)
- [cmux events](https://github.com/manaflow-ai/cmux/blob/main/docs/events.md)
- [cmux notifications](https://github.com/manaflow-ai/cmux/blob/main/docs/notifications.md)
- [cmux agent hooks](https://github.com/manaflow-ai/cmux/blob/main/docs/agent-hooks.md)
- [cmux agent session tracking](https://github.com/manaflow-ai/cmux/blob/main/docs/agent-session-tracking-spec.md)
