# claude-drift

Replay your own Claude Code sessions against a new model and see what actually changed.

When a new Claude model ships, its release notes say things like "behaves differently in ways
you may notice without changing any code". `drift` measures that on *your* work: it takes the
sessions already sitting in `~/.claude/projects`, replays each sampled turn with the new model,
and reports which next-action changes are real and which are noise.

- No config. No API key. Uses your existing `claude` login.
- Teacher-forced single-step replay: the new model sees exactly the context the old one saw and
  proposes one next action. No tools run.
- Noise-aware: the old model is replayed too, so you see a noise band, not just a number.

## Install

Not on PyPI yet. Run it straight from GitHub with uv:

```bash
uvx --from git+https://github.com/mandu5/claude-drift drift scan
```

or clone and `uv sync` (see Development). `uvx claude-drift` / `pip install claude-drift` will
work once the first release is published.

## Use

```bash
drift scan                                   # what is replayable on this machine (no model calls)
drift replay --from opus-5 --to sonnet-5     # replay 30 sampled turns with both models
drift report                                 # re-render the last run (no model calls)
drift resume                                 # finish the replays a cut-off run left pending
```

A 60-turn run with the noise pass sent about 25M input tokens on the author's machine; 30 turns
is the default so one run fits inside a subscription window. Use `drift resume` if a run is cut off.

Example output: see [docs/examples/first-report.md](docs/examples/first-report.md).

### Options for `drift replay`

| option | default | what it does |
|---|---|---|
| `--turns N` | 30 | How many recorded turns to sample and replay. |
| `--per-session N` | 3 | Max cuts sampled from one session. |
| `--workers N` | 4 | Replays in flight at once. Sessions are batched so one worker owns a session. |
| `--self-replays N` | 1 | Old-model replays per cut. Above 1 the report also gives old-vs-old self-agreement, which separates the model's own sampling instability from replay-vs-interactive mismatch. Multiplies cost. |
| `--no-noise` | off | Skip the old-model replay. Faster and half the cost, but no noise band and no verdict. |
| `--project PATH` | all | Only replay sessions whose working directory is under `PATH`. Also on `drift scan`. |
| `--seed N` | 0 | Seed for sampling and for the bootstrap, so a run is reproducible. |
| `--timeout SECONDS` | 180 | Per-replay wall clock. A replay that exceeds it is killed and counted as failed. |
| `-y`, `--yes` | off | Skip the cost confirmation prompt. |

### What is stored

Each run writes a directory under `~/.claude-drift/runs/` holding the sampled prompts, the raw
tool inputs of both the recorded and the proposed actions, and the rendered report. That is your
own session content sitting on your own disk; delete the directory to remove it.

If a replay is interrupted (Ctrl-C or SIGTERM) in-flight replays finish first so temporary
session copies are always removed; the run is marked `interrupted`.

## How it works

1. **ingest** — finds human prompts in your session logs that were followed by a tool call.
2. **sample** — picks up to 30 turns, stratified by tool, at most 3 per session, seeded.
3. **replay** — writes a truncated copy of the session next to the original under a temporary id
   and runs `claude -p --resume <id> --fork-session --model <new> --max-turns 1`. The primary
   guarantee that nothing runs is the stop itself: the stream is read only up to the first
   `tool_use` block and the process is killed there, before any tool can execute. As a second
   layer, a PreToolUse hook that blocks every tool is passed via `--settings`. That hook was
   never exercised in testing, because the stop always happens first. `--settings` merges rather
   than replaces, so your own hooks and plugins still load during a replay. The temporary file
   is deleted afterwards, and the original session file is never written to.
4. **classify** — normalises each action to `tool/target` (e.g. `Bash/local-read`, `Read/local-read`,
   `Agent/delegate`, `Bash/remote`).
5. **stats** — agreement with the recorded action for the new model and for the old model; a
   bootstrap 95% band of old-vs-old agreement is the noise floor. The bootstrap resamples
   sessions, not turns, because turns from one session are not independent. Transitions whose
   new-minus-old count has a bootstrap interval excluding zero are reported as REAL, with the
   interval Bonferroni-corrected over the number of transitions tested. Transitions are
   enumerated from what the new model did differently, so a cut where only the old model drifted
   away from the record is not listed in v1.
6. **report** — text or markdown.

## Cost

Each replayed turn sends the session prefix again (typically 30k–100k tokens, mostly cached).
Default settings replay 30 turns twice. `drift replay` prints an estimate and asks before starting.

## Limits

- Only turns after a human-typed prompt are replayed; turns after tool results are skipped in v1.
- Only Claude models, because only Claude Code sessions are read.
- Results describe *next-action* drift, not end-to-end task outcomes.
- Replays inherit your local plugins and hooks, so reports are not directly comparable across
  machines.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy
```

The live probe that shells out to a real `claude` process is opt-in:

```bash
DRIFT_LIVE=1 uv run pytest tests/test_replay_live.py -s
```

### Releasing

```bash
git tag v0.1.0 && git push --tags
```

A `v*` tag runs `.github/workflows/publish.yml`, which builds the wheel and uploads it to the
PyPI project `claude-drift` through trusted publishing from the `pypi` environment. No token is
stored in the repository.

## License

MIT
