# Second-machine smoke test request

The last red pre-launch gate: someone other than me runs `drift scan` on their own machine
without hitting an error. Send one of these, then paste what comes back into the checklist.

What I need back, in every case:

- the full output of the command, or the full error if it fails
- OS and chip (e.g. macOS 15 / M2, Ubuntu 24.04 / x86)
- `python3 --version`
- whether they have ever used Claude Code on that machine (if not, the count will be 0 and
  that is still a useful result — it should say 0, not crash)

---

## Korean, for a DM

안녕하세요! 사이드로 만든 CLI 하나를 오픈소스로 공개하려는데, 제 맥북에서만 돌려봐서
남의 환경에서 깨지는지 확인이 필요합니다. 1분이면 됩니다.

터미널에 이거 한 줄만 실행해주세요:

```
uvx claude-drift scan
```

(`uv`가 없으면: `pipx run claude-drift scan` 또는 `pip install claude-drift && drift scan`.
Python 3.11 이상 필요합니다.)

**부담 없는 이유:**
- 모델을 호출하지 않습니다. 토큰도, API 키도, 로그인도 안 씁니다. 돈 안 나갑니다.
- 로컬 `~/.claude/projects` 폴더를 읽기만 하고, 아무것도 쓰거나 보내지 않습니다.
- 출력은 개수 통계뿐입니다 (세션 몇 개, 모델별/도구별 건수). 대화 내용은 안 찍힙니다.

**보내주실 것:** 출력 전체를 그대로 복붙 (에러면 에러 전체), 그리고 OS/칩이랑
`python3 --version` 결과요. Claude Code를 안 써보셨어도 괜찮습니다 — 그 경우 0으로
나와야 정상이고, 대신 죽어버리면 그게 제가 찾는 버그입니다.

출력에 경로나 프로젝트 개수가 신경 쓰이시면 그 줄은 지우고 나머지만 주셔도 됩니다.
감사합니다!

---

## English, for a DM or a dev channel

Quick favour — takes about a minute, costs nothing.

I'm about to open-source a small CLI and it has only ever run on my machine. I need one
person to confirm it doesn't blow up on a different setup.

```
uvx claude-drift scan
```

(No `uv`? `pipx run claude-drift scan`, or `pip install claude-drift && drift scan`.
Needs Python 3.11+.)

Why it's a safe thing to run: it makes **no model calls** — no tokens, no API key, no login.
It only reads the local `~/.claude/projects` folder, writes nothing, sends nothing. The output
is counts only (how many sessions, broken down by recorded model and tool), never the contents
of your conversations.

What I need back: the whole output pasted as-is, or the whole error if it fails, plus your
OS/chip and `python3 --version`. If you've never used Claude Code on that machine, that's still
a useful run — it should report 0 and exit cleanly, and if it crashes instead, that's the bug
I'm looking for.

Feel free to strip the path line if you'd rather not share it.

---

## What a healthy run looks like

```
projects root: ~/.claude/projects
sessions with replayable cuts: 12
replayable cuts: 337

by recorded model:
  claude-opus-5                  289
  claude-sonnet-5                 48
...
```

Anything else — a traceback, a `command not found`, a hang, an empty projects root on a machine
that does use Claude Code — is a finding. File it as an issue the same day.
