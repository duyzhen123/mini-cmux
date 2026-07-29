# Deterministic hook adapter example

`run-job-with-hooks.sh` wraps a one-shot command and reports reliable mini-cmux
task status.

Run it only inside a mini-cmux-managed pane:

```bash
mini-cmux agent create demo tests \
  --role tester \
  --command "/path/to/run-job-with-hooks.sh python3 -m unittest"
```

The wrapper:

1. creates one run ID;
2. reports `working`;
3. runs the command using its original argument vector;
4. reports `completed` or `failed`;
5. retries hook delivery with the same event ID; and
6. preserves the wrapped command's exit status.

Set `MINI_CMUX_BIN` to an absolute mini-cmux launcher when the command is not
installed on `PATH`:

```bash
MINI_CMUX_BIN=/path/to/mini-cmux \
  ./run-job-with-hooks.sh python3 -m unittest
```

This wrapper is for deterministic one-shot jobs. Do not treat the exit of one
interactive model turn as semantic task completion.
