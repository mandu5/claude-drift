# model-drift report  claude-opus-5 -> sonnet

sessions replayed: 6   turns sampled: 47   errors: 13   skipped: 0
next-action agreement: 15%  (noise band 9%-32%)
old-vs-old agreement: 19%
verdict: no detectable drift (candidate agreement inside noise band)
agreement by level: tool 38% / target 15% / full 15%

## REAL changes (outside noise band, Bonferroni-corrected over 23 transitions, alpha=0.05/23)

(none)

## NOISE (within band, ignore)

| # | transition | candidate turns | noise turns | delta CI (alpha=0.05/k) | flag |
|---|---|---|---|---|---|
| 1 | Bash/local-write -> text | 8 | 8 | 0 [0, 0] | !! |
| 2 | Bash/local-write -> Bash/local-read | 5 | 4 | 1 [-4, 6] | !! |
| 3 | Bash/other -> text | 3 | 3 | 0 [0, 0] |  |
| 4 | Bash/local-read -> Bash/local-write | 2 | 2 | 0 [0, 0] |  |
| 5 | Bash/local-read -> text | 2 | 2 | 0 [0, 0] |  |
| 6 | Bash/other -> Read/local-read | 2 | 2 | 0 [0, 0] |  |
| 7 | mcp__claude-in-chrome__browser_batch/remote -> Bash/other | 2 | 2 | 0 [0, 0] |  |
| 8 | Bash/local-read -> Agent/delegate | 1 | 0 | 1 [0, 5] |  |
| 9 | Bash/local-read -> Bash/other | 1 | 1 | 0 [0, 0] |  |
| 10 | Bash/local-read -> ToolSearch/other | 1 | 0 | 1 [0, 5] |  |

---

Real run on the author's machine: `drift replay --from opus-5 --to sonnet --turns 60 --per-session 12 --workers 4`,
60 sampled turns from 6 sessions in `<project>` working directories, 120 replays over 21 minutes,
Claude Code 2.1.261. 18 replays failed with "You've hit your session limit" when the subscription
window ran out near the end, so 13 turns lacked a noise-pass result and were dropped; 47 turns count.
Successful replays sent 25M cache-creation and 2.1M cache-read input tokens in total.

Reading it: the old model agrees with its own recorded next action only 19% of the time (noise band
9%-32%), and the candidate sits inside that band at 15%, so there is no detectable drift at the
`tool/target` level. Every one of the 23 transitions stays under the Bonferroni-corrected threshold.
