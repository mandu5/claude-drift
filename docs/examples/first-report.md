# model-drift report  claude-opus-5 -> sonnet

sessions replayed: 5   turns sampled: 20   errors: 0   skipped: 0
next-action agreement: 25%  (noise band 14%-58%)
old-vs-record agreement: 35%
noise column = one old-model draw per turn (first successful attempt)
old-vs-old self-agreement (k=5 measured): 80%  (band 63%-88%)
interpretation: the old model mostly reproduces itself; the gap to the record is replay-vs-interactive mismatch, not model instability
verdict: no detectable drift (candidate agreement inside noise band)
agreement by level: tool 30% / target 25% / full 25%

## REAL changes (outside noise band, Bonferroni-corrected over 14 transitions, alpha=0.05/14)

(none)

## NOISE (within band, ignore)

| # | transition | candidate turns | noise turns | delta CI (alpha=0.05/k) | flag |
|---|---|---|---|---|---|
| 1 | Bash/local-read -> text | 2 | 2 | 0 [0, 0] |  |
| 2 | Bash/local-read -> Read/local-read | 1 | 0 | 1 [0, 4] |  |
| 3 | Bash/local-write -> text | 1 | 1 | 0 [0, 0] | !! |
| 4 | Bash/other -> text | 1 | 1 | 0 [0, 0] |  |
| 5 | Bash/remote -> Bash/local-read | 1 | 1 | 0 [0, 0] |  |
| 6 | Read/local-read -> Bash/local-read | 1 | 0 | 1 [0, 4] |  |
| 7 | RemoteTrigger/other -> ToolSearch/other | 1 | 0 | 1 [0, 4] |  |
| 8 | SendMessage/other -> Bash/local-read | 1 | 1 | 0 [0, 0] |  |
| 9 | Skill/skill -> Bash/local-read | 1 | 1 | 0 [0, 0] |  |
| 10 | ToolSearch/other -> Bash/other | 1 | 1 | 0 [0, 0] |  |

---

Real run on the author's machine: `drift replay --from opus-5 --to sonnet --turns 20 --per-session 8
--self-replays 5 --workers 4`, 20 sampled turns from 5 sessions in `<project>` working directories,
120 replays (20 candidate, 100 old-model), Claude Code 2.1.261. The first pass hit "You've hit your
session limit" with 15 replays outstanding; those were completed with `drift resume` once the
subscription window reset, so the run finished with 0 errors and no turn was dropped. The 120
successful replays sent 27.8M cache-creation and 11.3M cache-read input tokens in total (19.1M and
9.7M of that before the resume), and took about 20 minutes of wall clock at 4 workers, excluding the
wait for the window.

Reading it: the old model reproduces its own next action across five draws 80% of the time (band
63%-88%), but agrees with the action it actually recorded in the interactive session only 35% of the
time. That 45-point gap is the replay regime, not model instability — `claude -p` does not reproduce
the interactive session's thinking budget or the plugin state at recording time. The candidate sits
at 25% against a noise band of 14%-58%, which is inside the band, so there is no detectable drift at
the `tool/target` level, and none of the 14 transitions clears the Bonferroni-corrected threshold.
The band is wide because it resamples the 5 sessions, not the 20 turns.
