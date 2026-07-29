#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
mini_cmux="$repo_root/bin/mini-cmux"
project="${1:-team-demo}"

if [[ ! -x "$mini_cmux" ]]; then
  echo "mini-cmux launcher is not executable: $mini_cmux" >&2
  exit 1
fi

echo "Creating demo project: $project"
"$mini_cmux" project create "$project" --cwd "$script_dir"

"$mini_cmux" agent create "$project" planner \
  --role planner \
  --command "python3 demo_agent.py planner"

"$mini_cmux" agent create "$project" implementer \
  --role implementer \
  --command "python3 demo_agent.py implementer"

"$mini_cmux" agent create "$project" verifier \
  --role verifier \
  --command "python3 demo_agent.py verifier"

echo "Running planner -> implementer -> verifier orchestration"
"$mini_cmux" workflow \
  --project "$project" \
  --planner planner \
  --implementer implementer \
  --verifier verifier \
  --goal-file GOAL.md \
  --plan-file PLAN.md \
  --max-repairs 1 \
  --timeout 30

echo
"$mini_cmux" status --project "$project"
echo
echo "Demo complete. Inspect with:"
echo "  $mini_cmux events --project $project"
echo "  $mini_cmux project attach $project"
echo "Tear down with:"
echo "  $mini_cmux cleanup --project $project"
