# claude-drift 설계 문서

작성일: 2026-09-05
상태: 승인됨 (설계 단계), 구현 계획 작성 전

## 1. 한 줄 요약

새 Claude 모델이 나왔을 때, 사용자가 이미 갖고 있는 Claude Code 세션 로그를 그 모델로 재생해
"내 실제 작업에서 행동이 어디가 어떻게 달라지는지"를 노이즈와 분리해 보여주는 오픈소스 CLI.

## 2. 목표와 비목표

### 목표

- 설정 파일 없이 `~/.claude/projects` 아래 세션 로그만으로 동작한다.
- API 키 없이 Claude Code 구독만으로 동작한다. `claude` CLI 바이너리를 자식 프로세스로 호출한다.
- 도구를 실행하지 않는다. 기록된 턴 직전까지의 맥락을 주고 "다음 행동 하나"만 받는다 (teacher-forced single-step replay).
- 옛 모델 대 옛 모델 재생으로 노이즈 밴드를 측정하고, 밴드 밖의 변화만 "진짜 변화"로 보고한다.
- 리포트는 재생 결과 파일에서만 생성한다. 재생 없이 리포트를 다시 뽑을 수 있다.

### 비목표 (첫 릴리즈)

- 작업 전체 재실행 (접근 B). 비용과 비결정성 때문에 제외.
- Agent SDK 또는 Anthropic API 직접 호출. 2026-02 정책상 구독 토큰을 서드파티에서 쓸 수 없다.
- 웹 UI, SaaS, 원격 저장.
- Claude 이외 모델. Claude Code 세션 로그는 Claude 모델만 담고 있다.
- CLAUDE.md 규칙별 준수 판정. 두 번째 릴리즈 후보.

## 3. 사용자 시나리오

1. 새 모델 출시 당일, 사용자가 `drift scan`으로 재생 가능한 세션과 턴 수를 확인한다.
2. `drift replay --from opus-5 --to fable-5.1 --turns 60`으로 재생한다. 기본값으로 노이즈 밴드용 옛 모델 재생도 함께 돈다.
3. `drift report`로 아래 형식의 리포트를 본다.

```
model-drift report  opus-5 -> fable-5.1

sessions replayed: 30   turns sampled: 60
next-action agreement: 81%  (noise band 76-88%)

REAL changes (outside noise band)
  1. uses Read instead of Bash cat        +23 turns
  2. asks AskUserQuestion before Write     +11 turns
  3. skips CLAUDE.md 'run make check'      -7 turns  !!

NOISE (within band, ignore)
  - wording of final summaries
  - order of parallel tool calls
```

## 4. CLI

| 명령 | 역할 | 모델 호출 |
|---|---|---|
| `drift scan [--project PATH]` | 세션 목록, 모델 분포, 재생 가능 컷 수 출력 | 없음 |
| `drift replay --from A --to B [--turns N] [--workers W] [--no-noise] [--project PATH]` | 샘플 컷을 A(노이즈용)와 B로 재생, 결과 저장 | 있음 |
| `drift report [--run ID] [--format text\|md]` | 저장된 결과로 리포트 생성 | 없음 |

- `--from`은 기록된 모델이다. 기록 모델이 다른 세션은 제외한다.
- `--turns` 기본 60, `--workers` 기본 4.
- 배포: `uvx claude-drift`. 패키지명 `claude-drift`, 진입점 `drift`.

## 5. 아키텍처

여섯 모듈이 파이프라인을 이룬다. 각 모듈은 입력과 출력이 파일 또는 데이터클래스이고 독립적으로 테스트한다.

```
ingest -> sample -> replay -> classify -> stats -> report
```

### 5.1 ingest

- 입력: `~/.claude/projects/<slug>/<session>.jsonl` (최상위 파일만). `subagents/` 하위와 `isSidechain: true` 항목은 제외.
- 출력: `CutPoint` 목록. 각 항목은 세션 경로, 컷 인덱스(해당 사용자 메시지의 줄 번호), 사용자 메시지 본문, 기록된 다음 행동(`RecordedAction`), 기록 모델, cwd.
- 컷 포인트 조건: `type == "user"`이고 `message.content`가 문자열(사람이 친 텍스트)이며, 그 뒤 다른 사용자 메시지가 나오기 전에 `tool_use`를 포함한 assistant 메시지가 있을 것.
- tool_result 뒤에 오는 턴은 첫 릴리즈에서 제외한다. `-p`로 넘길 때 tool_result를 텍스트로 바꾸면 맥락이 왜곡된다.
- cwd가 존재하지 않는 세션은 제외한다. 재생 시 같은 cwd에서 실행해야 하기 때문.

### 5.2 sample

- 세션당 최대 k개 컷(기본 3), 전체 `--turns`개.
- 층화 기준: 기록된 도구 이름. 각 도구가 비율에 맞게 들어가되 최소 1개는 보장한다.
- 시드 고정 (`--seed`, 기본 0). 같은 입력이면 같은 샘플.
- 같은 세션의 컷은 인덱스 오름차순으로 묶어 같은 워커에 배정한다. 프롬프트 캐시가 앞부분을 재사용하도록 하기 위함.

### 5.3 replay

한 컷의 재생 절차:

1. 원본 세션의 `[:cut]` 줄을 임시 세션 ID로 복사한다. 각 줄의 `sessionId`를 임시 ID로 바꾼다. 원본 세션과 같은 프로젝트 디렉토리에 쓴다.
2. 원본 cwd에서 다음을 실행한다.
   ```
   claude -p "<사용자 메시지>" --resume <임시ID> --fork-session --model <대상> \
     --max-turns 1 --no-session-persistence --output-format stream-json --verbose \
     --settings <도구 차단 설정 JSON> < /dev/null
   ```
3. stream-json에서 첫 `tool_use`가 포함된 assistant 메시지를 받으면 프로세스를 종료한다. `result`에서 usage를 읽는다.
4. finally에서 임시 세션 파일을 삭제한다. 프로세스 종료로 fork된 세션이 남지 않는지 확인한다.

제약과 결정:

- `--bare`는 쓰지 않는다. 로그인 정보를 못 읽는다 (2026-09-05 탐색에서 확인).
- 훅과 플러그인 로딩은 `--settings`로 주입한 설정에서 끈다. 도구 실행은 같은 설정의 PreToolUse 훅이 전부 거부한다. `--disallowedTools`는 쓰지 않는다. 모델의 선택지를 바꾸기 때문.
- `--max-turns 1`이어도 `num_turns`가 2로 끝날 수 있다. 종료 조건은 turn 수가 아니라 첫 tool_use 수신이다.
- 실패한 컷(타임아웃, 로그인 오류, 빈 응답)은 결과에 `error` 필드로 남기고 통계에서 제외한다. 실패율이 20%를 넘으면 replay가 경고와 함께 종료한다.
- 타임아웃 기본 180초. 탐색에서 한 턴 47초였다.
- 비용 추정치를 시작 전에 출력한다. 컷당 약 10만 입력 토큰 기준.

### 5.4 classify

`RecordedAction`과 재생 응답을 같은 `ActionSignature`로 정규화한다.

```
ActionSignature(
  tool: str,             # Bash, Read, Write, Edit, Agent, AskUserQuestion, Skill, ...
  target: str,           # local-read | local-write | remote | delegate | ask-user | skill | other
  path_scope: str|None,  # 파일 경로가 있으면 repo 상대 경로의 첫 디렉토리
  asks_first: bool,      # 도구 전에 AskUserQuestion이 있었는가
  text_only: bool,       # 도구 없이 텍스트로 끝났는가
)
```

- Bash 명령의 target은 규칙 기반이다. `curl|wget|gh api|pip install|npm install` 등은 remote, `cat|head|grep|ls|git log|git status`는 local-read, 리다이렉션이나 `rm|mv|git commit|sed -i`는 local-write.
- 규칙은 `classify/rules.py` 한 파일에 있고 표로 테스트한다.
- 일치 판정은 계층적이다. `tool` 일치, `(tool, target)` 일치, 전체 일치 셋을 모두 기록한다. 리포트는 `(tool, target)` 기준을 쓴다.

### 5.5 stats

- 각 컷에 대해 세 값이 있다. 기록 행동 R, 옛 모델 재생 A, 새 모델 재생 B.
- 노이즈 바닥: `agree(R, A)`의 비율. 부트스트랩(1000회, 시드 고정) 95% 구간이 노이즈 밴드.
- 새 모델 일치율: `agree(R, B)`.
- 패턴별 변화: `(tool, target)` 전이 `R -> B`를 세고, 같은 전이의 `R -> A` 빈도와 비교한다. 차이가 그 전이의 부트스트랩 구간 밖이면 REAL, 안이면 NOISE.
- `--no-noise`이면 A를 돌리지 않고 밴드 없이 일치율만 출력한다. 리포트에 "noise band not measured"를 명시한다.

### 5.6 report

- 텍스트와 마크다운 두 형식. 내용은 같다.
- 3장 리포트 형식을 따른다. REAL 변화는 영향 턴 수 내림차순, 상위 10개.
- CLAUDE.md 관련 전이는 첫 릴리즈에서 특별 취급하지 않는다. `!!` 표시는 `local-write`나 `delegate`가 사라지는 전이에 붙인다.

## 6. 저장 형식

```
~/.claude-drift/runs/<YYYYMMDD-HHMMSS>/
  manifest.json      # from, to, turns, seed, workers, claude version, 시작·종료 시각
  cuts.jsonl         # CutPoint 목록 (샘플 결과)
  replays.jsonl      # 컷마다 {cut_id, model, signature, raw_tool_use, usage, error, duration_ms}
  report.md          # drift report가 마지막으로 생성한 리포트
```

원본 세션 로그는 절대 수정하지 않는다. 임시 세션 파일은 재생 직후 삭제한다.

## 7. 테스트 전략

- 단위: ingest, sample, classify, stats는 고정 픽스처(익명화한 세션 JSONL 3개)로 테스트한다.
- replay는 `claude`를 호출하는 함수를 주입 가능하게 만들고, 테스트는 가짜 프로세스가 미리 준비한 stream-json을 돌려준다.
- 구성 타당성 테스트 (RGI-ARC 교훈): 같은 컷에 다른 가짜 응답을 주입하면 리포트의 REAL 목록이 바뀌어야 한다. 바뀌지 않으면 실패.
- 실제 `claude` 호출 스모크 테스트는 `DRIFT_LIVE=1`일 때만 돈다. CI에서는 꺼져 있다.
- 임시 세션 파일이 예외 상황에서도 삭제되는지 테스트한다.

## 8. 스택

- Python 3.11 이상. 의존성은 `click` 하나. 통계는 표준 라이브러리 `random`과 `statistics`로 한다.
- `pyproject.toml`, `uv` 기반. `ruff`, `mypy --strict`, `pytest`.
- 레포: `~/Documents/GitHub/claude-drift`, GitHub `mandu5/claude-drift`, MIT.
- README는 영어. 데모 GIF와 실제 리포트 하나를 포함한다.

## 9. 일정 (6주)

| 주 | 산출물 |
|---|---|
| 1~2 | ingest, sample, replay, 임시 세션 안전 처리, 단위 테스트 |
| 3 | classify 규칙, stats 노이즈 밴드, 구성 타당성 테스트 |
| 4 | report, README, 내 세션 30개로 첫 실제 리포트 |
| 5 | `uvx` 배포, GitHub Actions, 데모 GIF |
| 6 | 다음 모델 출시 대기. 출시 당일 리포트 공개가 런칭 |

## 10. 확인된 사실 (2026-09-05 탐색)

- Claude Code 2.1.261에서 잘린 세션 파일을 `--resume --fork-session --model sonnet`으로 이어 붙이면 모델이 앞부분 맥락을 인식하고 다음 행동을 낸다.
- 한 턴 비용: 캐시 생성 81k, 캐시 읽기 19k, 출력 3.7k 토큰, 47초. 구독 인증으로 과금 없음.
- 같은 도구(Bash)를 골라도 의도가 달랐다(원격 API 조회 대 로컬 grep). 도구 이름만으로는 비교가 부족하다. 5.4의 target 분류가 필요한 근거.
- `--bare`는 로그인 실패를 일으킨다.

## 11. 열린 질문

- `--settings`로 주입한 설정이 사용자 전역 훅과 플러그인을 실제로 덮어쓰는지 확인하지 않았다. 안 되면 `PreToolUse` 거부 훅만으로 도구 실행을 막고, 다른 훅은 감수한다. 1주차 첫 작업.

- 여러 컷을 같은 세션에서 재생할 때 프롬프트 캐시가 실제로 재사용되는지는 아직 측정하지 않았다. 1주차에 측정한다.
- 세션 로그 형식은 Claude Code 버전에 따라 바뀔 수 있다. `version` 필드를 읽어 지원 범위를 명시하고, 파싱 실패는 세션 단위로 건너뛴다.
