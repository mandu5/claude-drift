# Show HN draft

## Title candidates (pick one)

1. Show HN: Claude-drift – my agent matches its own session log 35% of the time
2. Show HN: My coding agent reproduces itself 80% of the time, its own log 35%
3. Show HN: Claude-drift – find the real model drift in your own agent logs

## Body

`claude-drift` replays the Claude Code sessions already on your disk against a new model and
tells you what actually changed. For each sampled turn it feeds the model exactly the context
the old model saw, lets it propose one next action, and stops before any tool can run. Then it
compares that action to what you actually did.

The reason I built it: the naive number is a trap. On my own sessions the candidate model picked
a different next action than the record 75% of the time, which reads like a huge behaviour
change. But when I replayed the *old* model against the same turns, it disagreed with its own
recorded action 65% of the time. Replaying the old model against itself five times, it disagreed
with itself only 20% of the time. So most of that gap is the replay setup, not the model:
`claude -p` does not reproduce the interactive session's thinking budget or plugin state.

That is why every run ships a same-model control. Transitions only get labelled REAL when a
bootstrap interval over sessions, Bonferroni-corrected, excludes zero. On the run in the README,
Sonnet sits inside the noise band: no detectable drift over 20 turns.

No config, no API key; it uses your existing `claude` login.

```
uvx claude-drift scan
```

## Prepared first comment (limitations)

Author here. What this does not do, stated plainly:

- **Single step only.** It measures the next action after a human prompt, not whether the task
  eventually succeeded. Turns that follow a tool result are skipped in v1.
- **The `-p` regime is not your session.** Replays run headless, so the thinking budget and the
  plugin state differ from the interactive run that produced the log. That is exactly why
  old-vs-record agreement is low, and why the self-replay control exists — but it also means
  old-vs-record is a lower bound, not a clean measurement.
- **Your hooks and plugins load during a replay.** `--settings` merges rather than replaces, so
  numbers are not directly comparable across machines.
- **It costs real tokens.** The README run was 120 replays (135 attempts after retries), about
  39M input tokens — almost all of it cache writes and reads — and 59 minutes of replay time,
  roughly 15-20 minutes of wall clock at 4 workers. It also exhausted my Max session window
  partway through, so the run you see finished only because `drift resume` picked it up after
  the window reset. `--self-replays 5` is what makes it that expensive; the default is 1, and
  the default 30 turns with `--self-replays 1` is about a tenth of the cost.
- **Claude only**, because it reads Claude Code session logs. The method is not Claude-specific,
  the log parser is.
- **Small samples.** The bootstrap resamples sessions, not turns, so bands over 5-6 sessions are
  wide. Wide bands are honest, but they do mean small drifts will not clear the threshold.
