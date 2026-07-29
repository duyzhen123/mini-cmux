# Demo team

## planner

- Reads `GOAL.md`.
- Writes `PLAN.md`.
- Reports `AGENT_STATUS=PLAN_READY`.

## implementer

- Reads `GOAL.md` and `PLAN.md`.
- Writes `demo_output.txt`.
- Reports `AGENT_STATUS=DONE`.

## verifier

- Reads the goal, plan, and output.
- Reports `REVIEW_STATUS=GREEN` only when the output is exact.
- Otherwise explains the mismatch and reports `REVIEW_STATUS=RED`.
