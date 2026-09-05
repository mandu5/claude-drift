# model-drift report  claude-opus-5 -> sonnet

sessions replayed: 5   turns sampled: 12   errors: 0   skipped: 0
next-action agreement: 17%  (noise band 8%-50%)

## REAL changes (outside noise band, Bonferroni-corrected over 10 transitions)

(none)

## NOISE (within band, ignore)

| # | transition | candidate turns | noise turns | delta 95% CI | flag |
|---|---|---|---|---|---|
| 1 | Bash/local-read -> text | 1 | 1 | 0 [0, 0] |  |
| 2 | Bash/local-write -> Bash/local-read | 1 | 0 | 1 [0, 4] | !! |
| 3 | Bash/local-write -> text | 1 | 1 | 0 [0, 0] | !! |
| 4 | Bash/remote -> Bash/local-read | 1 | 0 | 1 [0, 4] |  |
| 5 | Read/local-read -> Bash/local-read | 1 | 0 | 1 [0, 4] |  |
| 6 | RemoteTrigger/other -> ToolSearch/other | 1 | 0 | 1 [0, 5] |  |
| 7 | SendMessage/other -> Bash/local-write | 1 | 1 | 0 [0, 0] |  |
| 8 | Skill/skill -> Bash/local-write | 1 | 1 | 0 [0, 0] |  |
| 9 | ToolSearch/other -> Bash/other | 1 | 0 | 1 [0, 5] |  |
| 10 | mcp__claude-in-chrome__browser_batch/remote -> Bash/other | 1 | 1 | 0 [0, 0] |  |

---

Real run on the author's machine, 12 sampled turns from 5 sessions in `<project>` working
directories, 24 replays, no errors. At this sample size the noise band is wide (8%-50%) and
nothing clears the Bonferroni-corrected threshold, so every transition lands in NOISE. That is the
expected and honest result for 12 turns; run the default 60 turns for a report with power.
