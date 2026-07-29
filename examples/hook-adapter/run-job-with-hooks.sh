#!/usr/bin/env bash
set -uo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: run-job-with-hooks.sh COMMAND [ARG ...]" >&2
  exit 64
fi

mini_cmux_bin="${MINI_CMUX_BIN:-mini-cmux}"
adapter_source="${MINI_CMUX_HOOK_SOURCE:-shell-job}"
run_id="${MINI_CMUX_AGENT_ID:-unmanaged}:$$:$(date +%s)"

report() {
  local status="$1"
  local transition="$2"
  local message="$3"
  local attempt=1
  while (( attempt <= 3 )); do
    if "$mini_cmux_bin" hook "$status" \
      --source "$adapter_source" \
      --event-id "$run_id:$transition" \
      --message "$message"; then
      return 0
    fi
    sleep "$attempt"
    ((attempt += 1))
  done
  echo "failed to report mini-cmux hook: $status" >&2
  return 1
}

if ! report working working "Deterministic job started"; then
  exit 70
fi

"$@"
job_status=$?

if [[ $job_status -eq 0 ]]; then
  if ! report completed completed "Deterministic job completed"; then
    exit 70
  fi
else
  report failed failed "Deterministic job failed with status $job_status" || true
fi

exit "$job_status"
