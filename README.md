# clad

> **tmux + Claude Code 채널 기반 영구 워커 CLI**
> `claude -p` 한 번 호출의 일회성 한계를 넘어, **태그별로 살아있는 Claude 세션**을 두고 컨텍스트를 누적시키며 작업합니다.

```sh
clad "auth 흐름 검토해줘"             # default 태그
clad "이제 그거 테스트 추가해줘" -t auth  # 같은 세션 — Claude가 직전 대화 기억
clad list                            # 활성 세션 표시
clad attach auth                     # tmux pane에 직접 진입
clad close auth                      # 종료
```

같은 `(프로젝트, 태그)` 조합은 **하나의 tmux pane에서 살아 있는 Claude 프로세스**에 매핑됩니다. 새로 호출할 때마다 클로드를 다시 띄우지 않고, 기존 세션에 프롬프트만 흘려보냅니다.

---

## 빠른 시작

요구사항: Python 3.10+, `tmux`, Claude Code(`claude`가 PATH에 있어야 함).

### 자동 부트스트랩 (권장)

```sh
git clone <repo> clad && cd clad
.claude/skills/init/init.sh
```

`init.sh`가 알아서:
1. `python@3.11` / `tmux` / `pipx` brew로 설치
2. `claude` PATH 확인 (없으면 안내 링크)
3. `.venv/` 생성 + dev deps 설치
4. `pipx install -e .` → `~/.local/bin/clad`
5. `pytest -q` 통과 확인
6. `clad doctor` exit 0 확인
7. **실제 Claude 라운드트립 smoke 테스트** (`Reply: PONG` → 90초 내 응답 확인)

`/init` 슬래시 명령으로도 호출됩니다 (Claude Code 내부에서).

### 수동 설치

```sh
brew install python@3.11 tmux pipx
pipx ensurepath               # 새 셸로 PATH 갱신
pipx install -e . --python /opt/homebrew/opt/python@3.11/bin/python3.11
clad doctor                   # 환경 진단
clad "안녕"                    # 첫 호출 — 콜드스타트 ~10초
```

---

## 아키텍처 (3-프로세스)

```
┌───────────────┐  HTTP   ┌─────────────────────┐  MCP stdio  ┌────────────────────┐
│ clad CLI      │ ──────► │ clad-bridge daemon  │ ◄─────────► │ claude (tmux pane) │
│ (단기, 호출당)  │ ◄────── │ (사용자별 영구)       │             │  (프로젝트, 태그)당   │
└───────────────┘   SSE   └─────────────────────┘             └────────────────────┘
                                  ▲
                                  │ tmux send-keys (제어용)
```

| 프로세스 | 역할 | 수명 |
|---|---|---|
| `clad` CLI | 인자 파싱, 브리지에 HTTP+SSE로 요청 | 호출 1회 |
| `clad-bridge` 데몬 | HTTP+SSE 서버 + MCP 서버 + tmux 매핑 + 상태 파일 소유. 첫 CLI 호출 시 자동 기동 | 사용자당 영구 |
| `claude` in pane | `--mcp-config`로 clad의 MCP 도구 로드 → 프롬프트 풀, 토큰 푸시 | `(프로젝트, 태그)` 당 |

세션 키: `sha1(project_root)[:10] + "-" + tag` — 같은 태그라도 다른 프로젝트면 다른 세션입니다.

### 채널 작동 원리

브리지는 Claude에게 노출하는 3개의 MCP 도구를 제공합니다:

| 도구 | 동작 |
|---|---|
| `clad_get_prompt()` | 큐에서 다음 프롬프트를 long-poll (최대 30초) |
| `clad_emit_token(text)` | 응답 토큰을 SSE 구독자(CLI)에게 푸시 |
| `clad_emit_done(summary?)` | 응답 완료 신호 |

콜드스타트 시 Claude pane에 한 번 보내는 부트스트랩 지시:
> *"You are a worker inside the clad CLI. Loop forever: call `clad_get_prompt`; when you receive a prompt, complete it, emit results via `clad_emit_token` incrementally and `clad_emit_done` when finished, then loop."*

이후 모든 사용자 프롬프트는 큐에 적재되고 Claude가 풀해서 응답합니다.

---

## 명령어 레퍼런스

### 개요

```
clad [OPTIONS] COMMAND [ARGS]...
```

| 명령 | 용도 |
|---|---|
| `prompt` | `(프로젝트, 태그)` Claude pane에 프롬프트 전송 |
| `list` | 활성 세션 목록 |
| `close` | 태그 종료 또는 `--all` |
| `attach` | tmux pane에 직접 진입 |
| `logs` | 채널 히스토리 JSONL 덤프 |
| `doctor` | 설치 상태 진단 |
| `config` | `~/.clad/config.yaml` get/set/list |

> **암묵적 `prompt` shortcut**: 첫 인자가 알려진 서브커맨드명이 아니고 `-`로 시작하지 않으면 자동으로 `prompt` 서브커맨드로 디스패치됩니다. 즉 `clad "안녕" -t test` ≡ `clad prompt "안녕" -t test`.

---

### `clad prompt PROMPT_TEXT`

| 옵션 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `-t`, `--tag TEXT` | string | `default` | 프로젝트당 세션 태그. 정규식 `[A-Za-z0-9._-]{1,64}`. |
| `--detach` | flag | off | 보내고 즉시 반환. 스트리밍 안 함. |
| `--keepalive` | flag | off | idle 자동종료 면제. **Sticky** — 한 번 설정되면 세션 종료 전까지 유지. |
| `-h`, `--help` | — | — | 도움말 |

> **`-a`/`--attach`는 제거됐습니다.** iTerm2의 `tmux -CC` 컨트롤 모드가 셸을 캡티브 UI로 가둬버려서 클린하게 반환되지 않기 때문. 실행 중인 세션을 보려면 별도 셸에서 `clad attach <태그>`를 쓰세요.

#### 유효 조합 4가지

| 형태 | 동작 |
|---|---|
| `clad "p"` | `default` 태그, `done`까지 스트리밍. |
| `clad "p" -t T` | 태그 `T`, 스트리밍. |
| `clad "p" -t T --detach` | 보내고 즉시 exit (콜드스타트 후 < 500ms). |
| `clad "p" -t T --keepalive` | 스트리밍. 이 세션 idle 면제됨. |
| `clad "p" -t T --detach --keepalive` | 보내고 exit. 세션은 sticky-keepalive. |

#### 예시

```sh
# 기본: 토큰 스트리밍
clad "utils.py를 pathlib로 리팩터링해줘"

# 던져놓고 다른 일 하기
clad "1000줄 에세이 써줘" -t essay --detach
clad logs essay --tail 30        # 나중에 결과 확인

# idle 자동종료 면제
clad "QA 에이전트" -t qa --keepalive

# 야간 배치 패턴
clad "분기 PR 전수 감사" -t audit --detach --keepalive
```

#### Exit 코드

| 코드 | 원인 |
|---|---|
| `0` | `done` 이벤트로 정상 종료, 또는 `--detach` 성공 |
| `1` | 브리지 에러 (콜드스타트 실패, HTTP 에러, 스트림 에러) |
| `2` | 잘못된 인자 (빈 태그, 정규식 위반, unknown option) |
| `130` | 스트리밍 중 `Ctrl+C`. **세션은 살아있음** — 데몬과 Claude pane은 그대로 |

---

### `clad list [--all]`

| 옵션 | 설명 |
|---|---|
| `--all` | 모든 프로젝트의 세션 표시 (기본은 현재 프로젝트만) |

컬럼:

| 컬럼 | 의미 |
|---|---|
| `TAG` | 세션 태그 |
| `PROJECT` | 프로젝트 루트 (30자 초과시 왼쪽 ellipsis) |
| `PANE` | tmux pane id (`%12`) |
| `UPTIME` | 세션 생성 후 경과 시간 |
| `IDLE` | 마지막 활동(프롬프트/토큰/done) 후 경과 시간 |
| `KA` | keepalive 표시 (`★`) |
| `LAST_PROMPT` | 마지막 프롬프트 처음 40자 |

```sh
clad list           # 이 프로젝트만
clad list --all     # 브리지가 아는 모든 세션
```

---

### `clad close [TAG] [--all]`

| 인자 | 설명 |
|---|---|
| `TAG` | 종료할 태그 (또는 `--all` 사용) |
| `--all` | 현재 프로젝트의 모든 세션 종료 |

종료 절차:
1. SSE `auto_closed` 이벤트 발행 (`reason: "user"`)
2. `tmux send-keys '/exit' Enter` → Claude 정상 종료
3. 3초 대기
4. `tmux kill-pane` → 강제 정리
5. `state.json`에서 레코드 삭제

```sh
clad close auth
clad close --all       # 이 프로젝트의 모든 세션
```

| Exit 코드 | 원인 |
|---|---|
| `0` | 종료 성공 (없는 태그도 no-op 성공) |
| `1` | 브리지 에러 |
| `2` | `TAG`도 없고 `--all`도 없음 |

---

### `clad attach TAG [--cc/--no-cc]`

태그의 tmux pane에 진입. `os.execvp`로 CLI 프로세스가 tmux로 교체됩니다.

| 옵션 | 설명 |
|---|---|
| `--cc` / `--no-cc` | tmux 컨트롤 모드 강제 on/off. 미지정시 `tmux_attach_mode` config 따름 |

#### 터미널별 기본 동작

| 감지 | 기본 모드 | 결과 |
|---|---|---|
| iTerm2 (`TERM_PROGRAM=iTerm.app` 또는 `LC_TERMINAL=iTerm2`) | `cc` | **새 iTerm2 창**이 tmux pane으로 열림 |
| WezTerm (`TERM_PROGRAM=WezTerm`) | `cc` | 동일 |
| 그 외 | `plain` | 현재 터미널이 tmux UI로 전환. `Ctrl+B D`로 detach |

```sh
clad attach auth                # 자동 감지
clad attach auth --no-cc        # 강제 in-place (iTerm2에서도)
clad attach auth --cc           # 강제 control mode
```

> ⚠ pane에 직접 타이핑한 입력은 **`clad logs`에 기록되지 않습니다** (채널 우회). 로그가 중요하면 `clad "..."`로 보내세요.

---

### `clad logs TAG [--tail N]`

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--tail INTEGER` | `200` | 마지막 N줄만 표시 |

#### 로그 포맷

`~/.clad/logs/sessions/<key>.jsonl` — 한 줄당 하나의 JSON 객체:

| `type` | 페이로드 | 출처 |
|---|---|---|
| `prompt_received` | `{prompt, ts}` | 브리지 `POST /sessions/{key}/prompt` |
| `prompt_delivered` | `{prompt, ts}` | Claude가 `clad_get_prompt`로 실제 가져감 |
| `token` | `{data, ts}` | Claude의 `clad_emit_token` |
| `done` | `{data:{summary}, ts}` | Claude의 `clad_emit_done` |

```sh
clad logs auth                  # 최근 200개
clad logs auth --tail 20
clad logs auth --tail 9999 | grep '"type": "token"' | wc -l
```

---

### `clad doctor [--prune]`

| 옵션 | 설명 |
|---|---|
| `--prune` | 진단 후 죽은 pane을 참조하는 stale 세션 레코드 삭제 |

체크 항목:
1. `tmux` PATH
2. `claude` PATH
3. 브리지 데몬 (없으면 자동 기동 후 `/healthz`)
4. `~/.clad/` 디렉터리
5. 감지된 터미널, 컨트롤 모드 가용성, 활성 attach 모드
6. `idle_timeout_minutes` / `idle_check_interval_seconds`

```
clad doctor
  ✔ tmux on PATH  /opt/homebrew/bin/tmux
  ✔ claude on PATH  /opt/homebrew/bin/claude
  ✔ bridge running  pid=83024 port=60920
  ✔ state dir  /Users/me/.clad
  ℹ terminal=iTerm.app, control-mode=available, attach-mode=cc (control mode — new window)
  ℹ idle_timeout_minutes=10, idle_check_interval_seconds=30
```

---

### `clad config get|set|list`

| 키 | 타입 | 기본값 | 효과 |
|---|---|---|---|
| `idle_timeout_minutes` | int | `10` | 이 시간 이상 idle하면 자동종료 (keepalive 제외) |
| `idle_check_interval_seconds` | int | `30` | idle watcher 스캔 주기 |
| `permissions_mode` | `skip` / `prompt` | `skip` | `skip`이면 Claude에 `--dangerously-skip-permissions` 전달 |
| `tmux_attach_mode` | `auto` / `cc` / `plain` | `auto` | `clad attach` 기본 모드 |

```sh
clad config list
clad config get idle_timeout_minutes
clad config set idle_timeout_minutes 30
clad config set tmux_attach_mode plain        # iTerm2에서도 in-place attach 강제
```

브리지 데몬은 파일 mtime 변경을 감지해 **즉시 hot-reload** — 재시작 불필요.

---

## 환경 변수

| 변수 | 효과 |
|---|---|
| `CLAD_HOME` | `~/.clad` 디렉터리 오버라이드 (테스트/샌드박싱용). 모든 상태/로그/MCP 설정이 이 경로 아래로 이동 |
| `CLAD_LOG_LEVEL` | 로그 레벨. 기본 `INFO`. `DEBUG`, `WARNING` 가능 |

```sh
CLAD_HOME=/tmp/sandbox clad doctor
CLAD_LOG_LEVEL=DEBUG clad list
```

---

## 파일 구조 (`~/.clad/`)

```
~/.clad/
├── state.json              # 세션 레코드 (0o600, atomic write)
├── state.lock              # fcntl flock (0o600, O_NOFOLLOW)
├── config.yaml             # 설정 키들
├── bridge.pid              # 데몬 PID
├── bridge.port             # 데몬 포트 (127.0.0.1만)
├── logs/
│   ├── bridge.log          # 데몬 로그
│   └── sessions/
│       └── <key>.jsonl     # 세션별 채널 히스토리
└── mcp/
    └── <key>/
        └── .mcp.json       # Claude가 로드하는 MCP 설정 (0o600)
```

`<key>` = `sha1(project_root)[:10] + "-" + tag`

---

## 동작 디테일

### Cold-start 지연

새로운 `(프로젝트, 태그)`에 대한 첫 호출은 Claude가 완전히 준비될 때까지 블록됩니다. 보통 5~10초. 상한 60초.

`--detach`도 콜드스타트는 **건너뛰지 못합니다** — 프롬프트 큐 적재까지는 빠르지만, `POST /sessions` HTTP 호출 자체가 Claude 준비 완료를 기다립니다.

### Ctrl+C 의미

스트리밍 중 `Ctrl+C`:
- SSE 구독만 끊고 exit 130
- 데몬·Claude pane **죽지 않음**
- 응답은 백그라운드에서 계속됨 — `clad logs <tag>`로 누적 토큰 확인 가능

진짜 세션을 죽이려면 `clad close <tag>`.

### Idle 자동종료

브리지의 idle watcher가 `idle_check_interval_seconds`마다 스캔. 다음 조건 **모두 충족**시 종료:

1. `keepalive == False`
2. `time.time() - last_activity_at >= idle_timeout_minutes * 60`

"활동" = 인바운드 프롬프트 또는 아웃바운드 `clad_emit_token` 또는 `clad_emit_done`. 토큰이 흐르는 동안에는 타이머가 계속 리셋되므로 응답 중간에 죽는 일은 없습니다.

자동종료 시 SSE `auto_closed` 이벤트 (`reason: "idle <N>m"`)가 발행되고 JSONL에 기록됨.

### 서브커맨드명 vs 프롬프트

서브커맨드명 그 자체를 프롬프트로 보내려면 명시적 `prompt` 사용:

```sh
clad list             # → clad list 서브커맨드
clad "list"           # → clad list 서브커맨드 (따옴표 무의미)
clad prompt list      # → "list"라는 프롬프트를 default 태그로
```

---

## 보안

`clad`는 **개발자 1인, 1대 머신, 단일 UNIX 유저** 기준으로 설계됐습니다.

### v1 보장

- 모든 subprocess는 argv 리스트 (no `shell=True`)
- `send-keys`에 들어가는 경로는 `shlex.quote`
- 태그 입력은 `[A-Za-z0-9._-]{1,64}` 정규식 검증
- 브리지 데몬은 `127.0.0.1`만 바인드
- 상태 파일은 atomic write + mode `0o600`
- 세션별 `.mcp.json`은 `0o600`, state lock은 `O_NOFOLLOW`
- Trust 다이얼로그는 `"do you trust"` + `"files in this folder"` 동시 매칭일 때만 자동 수락

### v1이 보장하지 **않는** 것 (멀티유저 환경 전에 fix 필요)

- **브리지 인증 없음**: 같은 UID로 동작하는 어떤 프로세스든 `127.0.0.1:<포트>`로 브리지를 조작 가능. v1.1에서 `~/.clad/bridge.token` (mode `0o600`) 기반 인증 토큰 추가 예정.
- **PID 재활용 레이스**: 옛 브리지 PID가 다른 프로세스에게 재할당되면 false-positive "alive". 회피: `clad doctor --prune`.
- **로그 회전 없음**: `~/.clad/logs/sessions/*.jsonl`이 무한 증가. keepalive 세션에서 주의. 로그에 프롬프트 평문 포함됨 — 공유 백업 시 민감 정보로 취급.

---

## 자주 쓰는 레시피

```sh
# 처음 설정
.claude/skills/init/init.sh

# auth 코드 작업을 영구 세션으로
clad "auth 엔드포인트 찾아줘" -t auth
clad "이제 그것에 대한 테스트 추가" -t auth
clad attach auth --no-cc          # 직접 들여다보기

# 오래 걸리는 작업, 안 막힘
clad "5000줄 감사 보고서 생성" -t audit --detach --keepalive
clad logs audit --tail 100
clad close audit

# 환경 진단
clad doctor
clad config list

# 디버깅 세션 동안 idle을 길게
clad config set idle_timeout_minutes 60

# iTerm2에서도 in-place attach 강제 (글로벌)
clad config set tmux_attach_mode plain

# 샌드박스 상태 디렉터리로 전체 분리
CLAD_HOME=/tmp/sandbox clad doctor
```

---

## 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| `clad doctor` 가 `bridge: not running` | 데몬 기동 실패. 직접 확인: `python -m clad.bridge --foreground` 또는 `~/.clad/logs/bridge.log` |
| 코드 수정했는데 반영 안 됨 | 브리지 데몬이 옛 코드 보유 중. `kill $(cat ~/.clad/bridge.pid)` → 다음 `clad` 호출 시 자동 재기동 |
| `Ctrl+C` 후 세션 살아있음 | **의도된 동작.** `clad close <tag>`로 종료 |
| iTerm2 `clad attach` 가 새 창 + 캡티브 UI | tmux `-CC` 컨트롤 모드. `clad attach <tag> --no-cc` 또는 `clad config set tmux_attach_mode plain` |
| 재부팅 후 stale 세션 | `clad doctor --prune` |
| 채널 프로토콜 에러 (도구 없음 등) | Claude Code 버전 확인 — `claude --version` 출력의 메이저 버전이 변경됐을 수 있음. `claude_launch.py::build_claude_argv` 참조 |

---

## 개발

```sh
# 테스트
.venv/bin/python -m pytest -q

# 데몬 전경 실행 (디버깅)
.venv/bin/python -m clad.bridge --foreground

# 데몬 HTTP 직접 호출
PORT=$(cat ~/.clad/bridge.port)
curl http://127.0.0.1:$PORT/healthz
curl http://127.0.0.1:$PORT/sessions?all=true
curl -X DELETE http://127.0.0.1:$PORT/sessions/<key>?reason=manual
```

전체 HTTP 컨트랙트는 [AGENTS.md](AGENTS.md) 참조.

추가 자료:
- [`docs/commands.md`](docs/commands.md) — 영문 풀 레퍼런스
- [`.omc/plans/clad-cli-v1.md`](.omc/plans/clad-cli-v1.md) — 원본 설계 계획 + AC + 검증 절차
- [`.claude/skills/init/SKILL.md`](.claude/skills/init/SKILL.md) — `/init` 스킬 사용법

---

## v1 미지원 (의도)

- 인덱스/pane id로 attach (태그만)
- 프로젝트 간 태그 공유 (모든 태그는 한 프로젝트 루트에 스코프)
- 멀티유저 브리지
- stdin 파이프로 프롬프트 입력 → 회피: `clad "$(cat prompt.txt)" -t mytag`
- 세션 로그 회전

향후 로드맵은 [`.omc/plans/clad-cli-v1.md`](.omc/plans/clad-cli-v1.md) §7 "Open Questions".

---

## 라이선스

MIT
