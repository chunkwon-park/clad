# CLAUDE.md

이 파일은 Claude Code가 `clad` 리포에서 작업할 때 따라야 할 규칙·관례·진입점을 모은 운영 가이드입니다. 깊은 설계 컨트랙트(HTTP/MCP 시그니처, 모듈 책임)는 [`AGENTS.md`](AGENTS.md)에, 사용자 문서는 [`README.md`](README.md)에 있습니다. 중복하지 말고 참조하세요.

## 한 줄 요약

`clad`는 tmux + Claude Code 채널 기반 영구 워커 CLI. 3-프로세스 구조 (CLI / `clad-bridge` 데몬 / Claude in tmux pane). 사용자별 단일 데몬이 세션 라이프사이클·SSE 팬아웃·MCP 어댑터를 전부 소유합니다.

## 자주 쓰는 명령

```sh
# 테스트 (반드시 이것으로 — pytest 직접 호출 금지)
.venv/bin/python -m pytest tests/ -q

# 린트
ruff check

# 데몬 전경 실행 (디버깅 / 코드 변경 즉시 반영)
.venv/bin/python -m clad.bridge --foreground

# 데몬 강제 재기동 (코드 수정 반영)
kill $(cat ~/.clad/bridge.pid)   # 다음 clad 호출이 자동 재기동
```

## 코드 맵 (핫 패스)

| 파일 | 책임 |
|---|---|
| [`src/clad/cli.py`](src/clad/cli.py) | Click 진입점, 서브커맨드, 프롬프트 shortcut |
| [`src/clad/bridge_client.py`](src/clad/bridge_client.py) | CLI → 데몬 HTTP 클라이언트, `ensure_bridge_running()` |
| [`src/clad/bridge/__main__.py`](src/clad/bridge/__main__.py) | 데몬 부트스트랩 (double-fork, port 바인딩, signal) |
| [`src/clad/bridge/server.py`](src/clad/bridge/server.py) | `Bridge` 상태 객체 + aiohttp 라우트 (public + internal MCP) |
| [`src/clad/bridge/session_manager.py`](src/clad/bridge/session_manager.py) | Cold-start / close 시퀀스, bootstrap instruction |
| [`src/clad/bridge/mcp_server.py`](src/clad/bridge/mcp_server.py) | stdio MCP 서버 — `clad_get_prompt`/`clad_emit_token`/`clad_emit_done` |
| [`src/clad/bridge/idle_watcher.py`](src/clad/bridge/idle_watcher.py) | idle 자동종료 + config hot-reload |
| [`src/clad/claude_launch.py`](src/clad/claude_launch.py) | tmux 안에서 Claude 띄우기, trust 다이얼로그 처리 |
| [`src/clad/state.py`](src/clad/state.py) | atomic JSON + fcntl flock, `SessionRecord` |
| [`src/clad/tmux.py`](src/clad/tmux.py) | 모든 tmux subprocess 래퍼 |

## 코드 작성 규칙

- **Python 3.9+ 호환**: 모든 모듈 상단 `from __future__ import annotations`. `match` 금지(3.10+). 어노테이션에서 `X | None`은 OK.
- **`shell=True` 금지**. 모든 subprocess는 argv 리스트.
- **`tmux send-keys`로 들어가는 문자열**의 경로 인자는 `shlex.quote`로 감쌀 것 (AC-N4).
- **HTTP 바인드는 `127.0.0.1` 전용**. 외부 노출 금지.
- **파일 모드 `0o600`** 강제 대상: `state.json`, `state.lock`, `~/.clad/mcp/<key>/.mcp.json`. atomic write 사용.
- **태그 검증**: 정규식 `[A-Za-z0-9._-]{1,64}`. 위반 시 `ValueError`. `projects.session_key`가 단일 진입점.
- **세션 키 파생**: 항상 `projects.session_key(project, tag)` 사용. 직접 sha1 만들지 말 것.
- **동시 cold-start 직렬화**: `Bridge.creation_locks[key]` 사용. 직접 새 lock 만들지 말 것.
- **새 SSE 이벤트 타입 추가** 시 `event_buffers` ring(maxlen=1000)에 적합한지 확인. `done`/`auto_closed`는 스트림 종료 트리거임을 잊지 말 것.

## 검증 게이트

- 변경 후 항상 `.venv/bin/python -m pytest tests/ -q` 통과 확인.
- 데몬 변경은 `--foreground`로 띄워 stdout 로그 보면서 직접 manual 호출(`curl http://127.0.0.1:$(cat ~/.clad/bridge.port)/healthz`).
- README의 §`clad-bridge` 데몬 → "직접 디버깅" 레시피에 curl 예시 정리되어 있음.
- 외부 의존: `tmux`, `claude` CLI가 PATH에 있어야 함. CI/샌드박스에서 `CLAD_HOME=/tmp/...`로 격리 가능.

## 알려진 함정

- **데몬은 코드를 메모리에 들고 있음**. `src/clad/bridge/*.py` 수정 후 즉시 반영하려면 `kill $(cat ~/.clad/bridge.pid)`. Config(`~/.clad/config.yaml`)만 mtime hot-reload됨.
- **MCP 도구 이름·시그니처는 변경 금지**. Claude pane 안의 부트스트랩 지시(`session_manager.BOOTSTRAP_INSTRUCTION`)와 `mcp_server.list_tools()`가 함께 묶여 있음.
- **Claude Code UI 마커**(`_TRUST_MARKER`, `_READY_MARKERS` in `claude_launch.py`)는 Claude Code 2.1.153 캡처 기준. 메이저 버전이 바뀌면 깨질 수 있음.
- **`--detach`도 cold-start는 못 건너뜀**. `POST /sessions`가 동기적으로 Claude ready 마커를 기다림.
- **인증 없음**. 같은 UID의 어떤 프로세스든 `127.0.0.1:<port>` 호출 가능. v1은 단일사용자 워크스테이션 전용.

## 환경 변수

| 변수 | 효과 |
|---|---|
| `CLAD_HOME` | `~/.clad` 오버라이드. 테스트/샌드박싱에 권장. |
| `CLAD_LOG_LEVEL` | `DEBUG`/`INFO`(기본)/`WARNING`. |
| `CLAD_BRIDGE_URL` | MCP 사이드카가 사용. `~/.clad/mcp/<key>/.mcp.json`에 자동 주입됨 — 사람이 손댈 일 없음. |

## 추가 자료

- [`README.md`](README.md) — 사용자 가이드 + 데몬 상세 설명 + 디버깅 레시피
- [`AGENTS.md`](AGENTS.md) — HTTP/MCP 컨트랙트, 모듈 시그니처, 보안 ACs
- [`docs/commands.md`](docs/commands.md) — 영문 풀 레퍼런스
- [`.omc/plans/clad-cli-v1.md`](.omc/plans/clad-cli-v1.md) — 원본 설계 + AC + 검증 절차
