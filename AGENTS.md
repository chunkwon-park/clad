# clad — Agent Implementation Contract

You are implementing one slice of the `clad` CLI. This file is the **shared contract**
all parallel agents must respect. Do not invent new public APIs that other modules
depend on; if you need one, add it to the slot defined here.

Authoritative plan: `.omc/plans/clad-cli-v1.md`. Architecture is in §3. ACs in §2.

## Python compatibility

- Target Python **3.9+** (the test machine only has 3.9). Use:
  - `from __future__ import annotations` at the top of every module
  - `Optional[X]` / `Union[X, Y]` *for non-annotation runtime use* (rare). In annotations,
    `X | None` is fine because of the `__future__` import.
- No `match` statements (3.10+). Use `if/elif`.
- `dataclasses`, `asyncio`, `aiohttp` are available.

## Foundation modules (already written — do not modify)

| Module | Purpose | Key exports |
|---|---|---|
| `clad.paths` | `~/.clad/*` layout | `ensure_layout()`, `state_file()`, `bridge_pid_file()`, `bridge_port_file()`, `bridge_log()`, `session_log(key)`, `mcp_config_path(key)`, `config_file()`, `mcp_dir()`, `logs_dir()`, `state_dir()` |
| `clad.logger` | Rich logging | `setup(level=None, file_log=False)`, `get(name='clad')` |
| `clad.projects` | Project root + key | `resolve_project_root(cwd=None) -> Path`, `project_hash(root)`, `session_key(root, tag) -> str`, `tmux_session_name(root) -> str` |
| `clad.state` | Atomic JSON state | `transaction()` ctx mgr, `read()`, `load()`, `save(state)`. Dataclasses `State`, `SessionRecord`. Fields on `SessionRecord`: `key, project, tag, pane_id, tmux_session, mcp_config_path, created_at, last_prompt_at, last_activity_at, keepalive, channel_id, stale, last_prompt`. `touch(now=None)` updates `last_activity_at`. |
| `clad.config` | YAML config | `Config` dataclass (props `idle_timeout_s`, `idle_check_interval_s`, `idle_timeout_minutes`, `idle_check_interval_seconds`, `permissions_mode`, `tmux_attach_mode`). Funcs `load()`, `reload_if_changed(cfg)`, `get(key)`, `set_value(key, value)`, `ensure_file()`, `all_keys()`. |
| `clad.terminal` | tmux -CC detection | `terminal_supports_control_mode(env=None)`, `resolve_attach_mode(cli_flag, config_value, env=None) -> 'cc'\|'plain'`, `build_attach_argv(mode, session, pane_id)`, `detected_terminal(env=None)`. |

## Bridge HTTP contract

All endpoints under `http://127.0.0.1:<port>`:

| Method | Path | Body / Query | Response |
|---|---|---|---|
| `POST` | `/sessions/{key}/prompt` | `{"prompt": str}` | `{"accepted": true, "event_id": int}` |
| `GET` | `/sessions/{key}/stream` | `?last_event_id=<int>` (optional) | SSE stream of `{"id":N,"event":"message","data":{"type":"token\|done\|error\|auto_closed","data":...,"ts":<float>}}` |
| `GET` | `/sessions/{key}` | — | `SessionRecord.to_dict()` or 404 |
| `DELETE` | `/sessions/{key}` | `?reason=<str>` optional | `{"closed": true}` |
| `POST` | `/sessions` | `{"project": str, "tag": str, "keepalive": bool, "workdir": str?}` | `{"key": str, "created": bool, "tmux_session": str, "pane_id": str}` (synchronous cold-start; may take up to 60s) |
| `GET` | `/sessions` | `?all=true` optional | `{"sessions": [SessionRecord.to_dict(), ...]}` |
| `GET` | `/healthz` | — | `{"ok": true, "pid": int, "port": int}` |

## MCP tools (bridge-side, called by Claude in the pane)

The MCP server is invoked as `python -m clad.bridge.mcp <session_key>` with env
`CLAD_BRIDGE_URL=http://127.0.0.1:<port>`. It exposes:

| Tool | Args | Returns |
|---|---|---|
| `clad_get_prompt` | (none) | `{"prompt": str \| null}` — blocking poll (up to 30s) for the next prompt |
| `clad_emit_token` | `{"text": str}` | `{"ok": true}` |
| `clad_emit_done` | `{"summary": str?}` | `{"ok": true}` |

Each call POSTs to `CLAD_BRIDGE_URL` on internal paths:
- `GET  /internal/mcp/{key}/next-prompt` → `{"prompt": str \| null}` (long-poll up to 30s)
- `POST /internal/mcp/{key}/token` `{"text": ...}` → ack
- `POST /internal/mcp/{key}/done` `{"summary": ...}` → ack

These internal paths also call `session.touch()` and append to the per-session log file.

## SSE event shapes

```json
{"type":"token","data":"hello","ts":1717000000.123}
{"type":"done","data":{"summary":"..."},"ts":...}
{"type":"error","data":{"message":"..."},"ts":...}
{"type":"auto_closed","data":{"reason":"idle 10m"},"ts":...}
```

Each event also gets an `id` (monotonic int) in the SSE framing for `last_event_id` resume.
Bridge keeps a ring buffer (last 1000 per session).

## tmux module contract

Module `clad.tmux`:

```python
def has_session(name: str) -> bool: ...
def ensure_project_session(project_root: Path) -> str:
    """Return session name; create with a single 'clad' window if missing."""
def spawn_pane(session: str, tag: str, workdir: Path) -> str:
    """Return pane_id like '%12'. Sets pane title to tag."""
def send_keys(pane_id: str, text: str, enter: bool = True) -> None: ...
def send_literal(pane_id: str, text: str) -> None:
    """Send text literally (no shell expansion) — uses tmux send-keys -l."""
def capture_pane(pane_id: str, lines: int = 200) -> str: ...
def wait_for_pane_content(pane_id: str, pattern: str, timeout_s: float) -> bool: ...
def kill_pane(pane_id: str) -> None: ...
def list_panes(session: str) -> list[dict]:
    """Return [{pane_id, title}, ...]."""
def pane_exists(pane_id: str) -> bool: ...
```

All shell args MUST flow through subprocess argv lists (no shell=True) so `shlex.quote`
is unnecessary in module code — but when constructing **strings sent into tmux send-keys**
(e.g. the `claude ...` command line), use `shlex.quote` for each path argument (AC-N4).

## claude_launch module contract

Module `clad.claude_launch`:

```python
def build_claude_argv(mcp_config_path: Path, permissions_mode: str = "skip") -> list[str]:
    """Return ['claude', '--mcp-config', <path>, '--dangerously-load-development-channels',
       'server:clad-bridge', ('--dangerously-skip-permissions' if skip else ...)]."""
def launch_claude(pane_id: str, workdir: Path, mcp_config_path: Path,
                  permissions_mode: str = "skip") -> None:
    """cd to workdir, exec claude with the right flags. Uses tmux send-keys."""
def handle_init_prompts(pane_id: str, timeout_s: float = 45.0) -> bool:
    """Walk through trust/confirm prompts until a `>` ready state is seen.
       Return True on success, False on timeout."""
```

## session_manager (bridge-side) contract

Module `clad.bridge.session_manager`:

```python
async def create_or_get(key: str, project: Path, tag: str,
                        keepalive: bool, workdir: Path | None,
                        cfg: Config) -> SessionRecord:
    """Cold-start: create pane, write per-session .mcp.json, launch Claude,
       wait for init prompts, send bootstrap loop instruction, persist state."""

async def close_session(key: str, reason: str = "user") -> None:
    """Send /exit to pane, wait 3s, kill pane, remove state entry."""

BOOTSTRAP_INSTRUCTION = "You are a worker inside the clad CLI. Loop forever: call \
clad_get_prompt; when you receive a prompt, complete it, emit results via \
clad_emit_token incrementally and clad_emit_done when finished, then loop. Begin now."
```

Serialize per-key creation with `asyncio.Lock` (R-4).

## idle_watcher contract

Module `clad.bridge.idle_watcher`:

```python
async def watch_idle(bridge, stop_event: asyncio.Event) -> None:
    """Every cfg.idle_check_interval_s, scan sessions; close any non-keepalive
       session idle for >= cfg.idle_timeout_s. Emits 'auto_closed' SSE event."""
```

`bridge` here is the `Bridge` object that the HTTP server exposes (see below).

## Bridge core (server.py shape)

`Bridge` class holds:
- `sessions: dict[str, SessionRecord]` (in-memory mirror of state file)
- `event_buffers: dict[str, RingBuffer]` (per-key SSE ring of last 1000 events)
- `pending_prompts: dict[str, asyncio.Queue]` (one queue per key; MCP `clad_get_prompt` blocks on .get())
- `creation_locks: dict[str, asyncio.Lock]`
- `cfg: Config`, `log: logging.Logger`
- `port: int`, `pid: int`

Helpers:
- `bridge.publish(key, event_type, data)` → appends to ring buffer + wakes SSE subscribers
- `bridge.enqueue_prompt(key, prompt)` → puts into `pending_prompts[key]`, touches activity
- `bridge.touch(key)` → updates `last_activity_at`

## bridge_client (CLI-side)

```python
def ensure_bridge_running() -> tuple[int, int]:
    """Return (pid, port). Spawn `python -m clad.bridge` detached if not running.
       Wait up to 8s for /healthz. Raise BridgeError on failure."""
def post_prompt(key, prompt: str) -> dict: ...
def create_session(project, tag, keepalive, workdir) -> dict: ...
def get_session(key) -> dict | None: ...
def delete_session(key, reason='user') -> None: ...
def list_sessions(all_projects: bool) -> list[dict]: ...
def stream(key, last_event_id: int = 0): ...  # generator yielding SSE event dicts
```

## CLI shape

Entry point `clad = clad.cli:main`. Click sub-commands:

- (default) `clad PROMPT [-t TAG] [--detach] [--keepalive] [-a/--attach] [--cc/--no-cc]`
- `clad list [--all]`
- `clad close TAG [--all]`
- `clad attach TAG [--cc/--no-cc]`
- `clad logs TAG [--tail N]`
- `clad doctor [--prune]`
- `clad config get KEY` / `clad config set KEY VALUE`

If invoked as just `clad "some prompt"`, Click should treat that as the default
"prompt" subcommand. Implementation tip: use `click.group(invoke_without_command=True)`
plus `pass_context`, with a `prompt` sub-command, and if `ctx.invoked_subcommand is None`
delegate to the prompt path with the first positional arg.

## Security (AC-N4)

- Never `shell=True`.
- All subprocess calls use argv lists.
- The `claude` command string passed to tmux `send-keys` quotes each path component with `shlex.quote`.
- Bridge binds `127.0.0.1` only.
