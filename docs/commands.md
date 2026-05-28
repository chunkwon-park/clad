# `clad` Command Reference

A complete inventory of every `clad` sub-command, every flag combination, every
exit code, and every environment / config variable. Every example below was
verified against `clad --version 0.1.0` running on Python 3.11 / claude 2.1.153
on macOS.

> **Quick mental model.** `clad` is a thin client that talks HTTP+SSE to a
> per-user bridge daemon. The bridge owns one `tmux pane` per
> `(project_root, tag)` running an interactive `claude`. Every CLI invocation is
> one of: **send a prompt to the pane** (default), or **manage that pane**
> (list/close/attach/logs/doctor/config).

---

## Synopsis

```
clad [OPTIONS] COMMAND [ARGS]...
```

Top-level options:

| Option | Description |
|---|---|
| `--version` | Print version (`clad, version 0.1.0`) and exit. |
| `-h`, `--help` | Show help for the group or the named subcommand. |

Top-level commands:

| Command | Purpose |
|---|---|
| `prompt` | Send a prompt to the (project, tag) Claude pane. |
| `list` | List active sessions for the current project (or all). |
| `close` | Close one tag or `--all` in the current project. |
| `attach` | tmux-attach to the pane for a tag. |
| `logs` | Dump captured channel history for a tag. |
| `doctor` | Diagnose installation + report active attach mode. |
| `config` | Get/set/list keys in `~/.clad/config.yaml`. |

### The implicit `prompt` shortcut

If the first positional argument does **not** match a known sub-command name
(`prompt`, `list`, `close`, `attach`, `logs`, `doctor`, `config`) and does **not**
start with `-`, `clad` treats it as a prompt and prepends the `prompt`
subcommand. These two are equivalent:

```sh
clad "hello there" -t auth
clad prompt "hello there" -t auth
```

This is implemented in `cli.py::_inject_prompt_prefix`. It's only convenient
sugar — anywhere this doc shows `clad prompt "..."`, you can drop the word
`prompt` and use the implicit form.

---

## `clad prompt PROMPT_TEXT [OPTIONS]`

Send `PROMPT_TEXT` to the Claude pane bound to `(current_project, --tag)`.
Cold-starts the pane on first call; reuses it on subsequent calls so Claude
keeps context.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `-t`, `--tag TEXT` | string | `default` | Per-project session tag. Must match `[A-Za-z0-9._-]{1,64}`. |
| `--detach` | flag | off | Send and return immediately; do not stream tokens back. |
| `--keepalive` | flag | off | Exempt this session from the idle auto-close timer. **Sticky** — once set on a session it stays set until the session is closed; subsequent `clad "..." -t X` calls without `--keepalive` do not unset it. |
| `-h`, `--help` | — | — | Show help. |

> **No `-a`/`--attach` on the prompt path.** Use `clad attach <tag>` from a
> separate shell to inspect a running session. The send-then-exec-attach flow
> was removed because iTerm2's `tmux -CC` control mode opens a captive UI
> ("`** tmux mode started **`") that doesn't return cleanly to the shell.

### Every valid combination

| Form | Behavior |
|---|---|
| `clad "p"` | Tag `default`, stream tokens until `done`. |
| `clad "p" -t T` | Tag `T`, stream. |
| `clad "p" -t T --detach` | Send, exit < 500 ms (after cold-start). No stream. |
| `clad "p" -t T --keepalive` | Stream. Session is now exempt from idle close. |
| `clad "p" -t T --detach --keepalive` | Send, exit. Session is sticky-keepalive. |

### Examples

```sh
# Stream tokens (most common)
clad "Refactor utils.py to use pathlib"

# Send and don't wait — let Claude keep working
clad "Write 1000-word essay" -t essay --detach
clad logs essay --tail 30   # check later

# Never auto-close this one
clad "Long-running QA agent" -t qa --keepalive

# Watch a running session interactively (from another terminal)
clad attach essay --no-cc
```

### Exit codes

| Code | Cause |
|---|---|
| 0 | Stream finished cleanly with a `done` event (or `--detach` succeeded). |
| 1 | Bridge error (cold-start failed, HTTP error, stream error). |
| 2 | Invalid arguments — empty tag, bad regex, unknown option. |
| 130 | `Ctrl+C` during streaming. **The session is left running** — the daemon and Claude pane survive. |

### What runs in the background

A successful `clad prompt "..."` causes the bridge to:

1. Spawn or reuse `tmux session = clad-<sha1(project)[:10]>`.
2. Split a pane in the `clad` window for this tag.
3. Write `~/.clad/mcp/<key>/.mcp.json` (mode `0o600`).
4. Run `claude --mcp-config <.mcp.json> --dangerously-skip-permissions` in the pane.
5. Auto-accept the "Yes, I trust this folder" dialog.
6. Submit the bootstrap loop instruction ("call `clad_get_prompt`, emit
   results via `clad_emit_token`, signal `clad_emit_done`, then loop").
7. Enqueue the user's prompt; Claude polls via the MCP `clad_get_prompt` tool
   and streams tokens back through `clad_emit_token`.

---

## `clad list [OPTIONS]`

Render a table of active sessions.

| Option | Type | Default | Description |
|---|---|---|---|
| `--all` | flag | off | List sessions across **every** project, not just the current one. |
| `-h`, `--help` | — | — | Show help. |

Columns:

| Column | Source |
|---|---|
| `TAG` | The tag string the session was created with. |
| `PROJECT` | Project root (truncated from the left if > 30 chars). |
| `PANE` | tmux pane id like `%12`. |
| `UPTIME` | Time since session creation (`Ns` / `NmSSs` / `NhMMm`). |
| `IDLE` | Time since last activity (prompt-in, token-out, done). |
| `KA` | `★` if `keepalive` is sticky, blank otherwise. |
| `LAST_PROMPT` | First 40 chars of the most recent prompt, ellipsis if longer. |

### Examples

```sh
clad list                # this project only
clad list --all          # everything the bridge knows about
```

### Exit codes

| Code | Cause |
|---|---|
| 0 | Always, unless bridge unreachable. |
| 1 | Bridge connection / HTTP error. |

---

## `clad close [TAG] [OPTIONS]`

Send `/exit` to the Claude pane, wait 3 s, then `tmux kill-pane`. Removes the
session from `~/.clad/state.json`.

| Option | Type | Description |
|---|---|---|
| (positional) `TAG` | string | Tag of the session to close. Optional iff `--all` is given. |
| `--all` | flag | Close every session in the **current project**. Does not touch other projects (use `clad list --all` then loop with `clad close <tag>` if you need to). |
| `-h`, `--help` | — | Show help. |

### Argument rules

- `clad close` (no args) → **exit 2**: `usage: clad close <tag>   or   clad close --all`
- `clad close <tag>` → close that one.
- `clad close --all` → close all in the current project.
- `clad close <tag> --all` → both args present; loops over current-project sessions, ignores the positional. *(Acceptable but redundant.)*

### Examples

```sh
clad close auth
clad close --all      # only this project
```

### What happens

For each session to close:
1. Bridge publishes an SSE `auto_closed` event with `{reason: "user"}` so any
   `clad logs` consumer still subscribed sees the cause.
2. `tmux send-keys -t <pane> '/exit' Enter` → Claude's clean shutdown.
3. `await asyncio.sleep(3)` → give Claude time to write final logs.
4. `tmux kill-pane -t <pane>` → forced pane teardown.
5. State entry removed from `~/.clad/state.json`.

### Exit codes

| Code | Cause |
|---|---|
| 0 | Close succeeded (or the tag had no session — still a no-op success). |
| 1 | Bridge error. |
| 2 | Missing TAG and `--all` not given. |

---

## `clad attach TAG [OPTIONS]`

Replace the current process with `tmux attach` (or `tmux -CC attach`) on the
session's pane. `os.execvp` — the CLI does not return.

| Option | Type | Description |
|---|---|---|
| (positional) `TAG` | required | Tag to attach to. Resolved against current project. |
| `--cc` / `--no-cc` | tristate | Force control mode on/off. Without either, falls back to `tmux_attach_mode` config (default `auto` → iTerm2/WezTerm get `-CC`). |
| `-h`, `--help` | — | Show help. |

### Examples

```sh
clad attach auth                # auto-detect mode
clad attach auth --cc           # force -CC (new iTerm2 window)
clad attach auth --no-cc        # force plain attach (in-place)
```

### Behavior by terminal

| Detected | Default mode | What happens |
|---|---|---|
| iTerm2 (`TERM_PROGRAM=iTerm.app` or `LC_TERMINAL=iTerm2`) | `cc` | Opens a **new iTerm2 window** showing the tmux pane. Native-iTerm scrolling/copy. |
| WezTerm (`TERM_PROGRAM=WezTerm`) | `cc` | Same as iTerm2. |
| Anything else | `plain` | Current terminal switches to tmux UI. `Ctrl+B D` to detach. |

### Exit codes

| Code | Cause |
|---|---|
| (no return) | Successful `exec` — process becomes `tmux`. |
| 1 | Tag not found in current project, or bridge error. |

### Caveat

Anything typed directly into the pane after attach **bypasses the channel**
and is not captured in `clad logs`. Prompts sent via `clad "..."` from another
terminal are still logged. See `docs/commands.md` → Gotchas.

---

## `clad logs TAG [OPTIONS]`

Dump the JSONL channel log for a tag.

| Option | Type | Default | Description |
|---|---|---|---|
| (positional) `TAG` | required | — | Tag whose log to read. |
| `--tail INTEGER` | int | `200` | Show the last N lines only. Use `--tail 999999` for "all". |
| `-h`, `--help` | — | — | Show help. |

### Log format

`~/.clad/logs/sessions/<key>.jsonl` — one JSON object per line. Event types:

| `type` | Payload | Emitted by |
|---|---|---|
| `prompt_received` | `{prompt, ts}` | Bridge HTTP `POST /sessions/{key}/prompt`. |
| `prompt_delivered` | `{prompt, ts}` | MCP tool `clad_get_prompt` (Claude actually pulled it). |
| `token` | `{data, ts}` | MCP tool `clad_emit_token`. One per chunk Claude streams. |
| `done` | `{data:{summary}, ts}` | MCP tool `clad_emit_done`. |

### Examples

```sh
clad logs auth                  # last 200 entries
clad logs auth --tail 20        # last 20
clad logs auth --tail 9999 | grep '"type": "token"' | wc -l
```

### Exit codes

| Code | Cause |
|---|---|
| 0 | Always (prints `(no log yet)` if the file is missing). |

---

## `clad doctor [OPTIONS]`

Diagnose the install.

| Option | Description |
|---|---|
| `--prune` | After diagnostics, remove state entries whose tmux pane no longer exists. |
| `-h`, `--help` | Show help. |

### What it checks

1. `tmux` on PATH (`shutil.which`).
2. `claude` on PATH.
3. Bridge daemon — auto-spawn if missing, then check `/healthz`.
4. State directory exists (`~/.clad/`).
5. Reports detected terminal, control-mode availability, active attach mode.
6. Reports current `idle_timeout_minutes` and `idle_check_interval_seconds`.

### Sample output

```
clad doctor
  ✔ tmux on PATH  /opt/homebrew/bin/tmux
  ✔ claude on PATH  /opt/homebrew/bin/claude
  ✔ bridge running  pid=83024 port=60920
  ✔ state dir  /Users/<you>/.clad
  ℹ terminal=iTerm.app, control-mode=available, attach-mode=cc (control mode — new window)
  ℹ idle_timeout_minutes=10, idle_check_interval_seconds=30
```

The `attach-mode` line tells you what `clad attach` will do without any flags;
flip it with `clad config set tmux_attach_mode plain|cc|auto` or override per
call with `--cc`/`--no-cc`.

### Exit codes

| Code | Cause |
|---|---|
| 0 | Every ✔ is true. |
| 1 | Any ✘ — typically tmux/claude missing or bridge can't start. |

---

## `clad config`

Read or write `~/.clad/config.yaml`. The file is auto-created with defaults on
first `clad` invocation.

Three sub-commands: `get`, `set`, `list`.

### Keys

| Key | Type | Default | Effect |
|---|---|---|---|
| `idle_timeout_minutes` | int | `10` | Sessions idle ≥ this long are auto-closed (unless `keepalive`). |
| `idle_check_interval_seconds` | int | `30` | How often the idle watcher scans sessions. |
| `permissions_mode` | enum (`skip` / `prompt`) | `skip` | If `skip`, Claude is launched with `--dangerously-skip-permissions`. If `prompt`, you'll see permission prompts inside the pane. |
| `tmux_attach_mode` | enum (`auto` / `cc` / `plain`) | `auto` | Default for `clad attach` and `clad "p" -a`. `auto` = iTerm2/WezTerm get `-CC`, others plain. |

### `clad config get KEY`

```sh
clad config get idle_timeout_minutes
# 10
```

Exit 2 + `unknown key 'X'. Known: idle_timeout_minutes, ...` if `KEY` is not in
the schema.

### `clad config set KEY VALUE`

```sh
clad config set idle_timeout_minutes 30
clad config set tmux_attach_mode plain
clad config set permissions_mode prompt
```

Bridge daemon hot-reloads config when the file mtime changes — no restart
needed. `VALUE` is coerced for int keys; everything else is stored as-is.

Exit 2 if `KEY` is unknown or `VALUE` can't be coerced.

### `clad config list`

Prints every known key with its current value:

```
idle_check_interval_seconds = 30
idle_timeout_minutes = 10
permissions_mode = skip
tmux_attach_mode = auto
```

Exit 0 always (file is auto-created if missing).

---

## Environment variables

| Variable | Effect |
|---|---|
| `CLAD_HOME` | Override the `~/.clad` directory (used by tests and for sandboxing). All state, logs, mcp configs, and the bridge pid/port file move under this directory. |
| `CLAD_LOG_LEVEL` | Logging level. Default `INFO`. Useful values: `DEBUG`, `WARNING`. Applies to both CLI and bridge daemon. |

Example:

```sh
CLAD_HOME=/tmp/clad-sandbox clad doctor    # everything goes under /tmp/clad-sandbox
CLAD_LOG_LEVEL=DEBUG clad list             # verbose CLI logging (tmux subprocess calls etc)
```

---

## File layout (`~/.clad/`)

```
~/.clad/
├── state.json              # session records (0o600, atomic writes)
├── state.lock              # fcntl flock (0o600, O_NOFOLLOW)
├── config.yaml             # the config keys above
├── bridge.pid              # daemon PID
├── bridge.port             # daemon TCP port (127.0.0.1 only)
├── logs/
│   ├── bridge.log          # rotating; INFO+ from the daemon
│   └── sessions/
│       └── <key>.jsonl     # one file per (project, tag) — see `clad logs`
└── mcp/
    └── <key>/
        └── .mcp.json       # MCP config Claude loads (0o600)
```

`<key>` is `sha1(project_root)[:10] + "-" + tag` (10 hex + dash + tag).

---

## Behaviors that aren't an explicit flag

### Sub-command name vs. prompt

Type a sub-command name that's also a plausible prompt? It dispatches as the
sub-command:

```sh
clad list           # → `clad list` (the sub-command), NOT the prompt "list"
clad "list"         # also → the sub-command (quoting doesn't help)
clad prompt list    # → the prompt "list" sent to the default tag
```

If you genuinely want to send a prompt that happens to match a sub-command
name, use the explicit `prompt` form.

### Cold-start latency

The first `clad "p" -t T` call against a new (project, tag) blocks until
Claude is fully ready. Typical: 5–10 s on a warm Claude install. Cap: 60 s
(driven by aiohttp request timeout for `POST /sessions`, currently 90 s).

`--detach` does **not** skip the cold-start. The prompt is enqueued
immediately, but the HTTP call still waits for Claude to be ready.

### Attaching to a running session

`clad prompt` no longer accepts an attach flag. To watch a session live:

```sh
clad attach <tag>           # auto-detect (iTerm2/WezTerm → -CC, else plain)
clad attach <tag> --no-cc   # force in-place plain tmux attach
clad attach <tag> --cc      # force tmux -CC control mode (new iTerm2 window)
```

The iTerm2 `-CC` path puts the **terminal** into a tmux control-mode UI
(`** tmux mode started **`) rather than the tmux pane itself; if your terminal
doesn't render it the way you expect, use `--no-cc` or
`clad config set tmux_attach_mode plain`.

### Ctrl+C semantics

`Ctrl+C` during streaming:
- Cancels the SSE subscription.
- CLI exits 130 with a helpful hint.
- **Does not** kill the bridge.
- **Does not** kill the Claude pane.
- The current Claude response continues in the background; tokens accumulate
  in the session log; you can resume tailing them via
  `clad logs <tag> --tail 9999`.

To actually stop a session, use `clad close <tag>`.

### Idle auto-close

The bridge runs an idle watcher every `idle_check_interval_seconds`. A session
is closed if **all** of these are true:

1. `keepalive` is false.
2. `time.time() - last_activity_at >= idle_timeout_minutes * 60`.

"Activity" = inbound prompt **or** outbound `clad_emit_token` **or**
`clad_emit_done`. A multi-minute Claude response that streams tokens will
keep the timer reset for the whole response.

When auto-closed:
- An SSE `auto_closed` event with `{reason: "idle <N>m"}` is appended to the
  ring buffer (and to the JSONL log) so late subscribers see why.
- The same `/exit` → wait 3 s → `kill_pane` sequence as `clad close` runs.

---

## Quick recipes

```sh
# First-run setup (or after a fresh checkout)
.claude/skills/init/init.sh

# Iterating on auth code with persistent Claude memory
clad "find the auth endpoint" -t auth
clad "now add a test for it" -t auth
clad attach auth     # take over the pane interactively

# Long-running task, don't block — and watch from another terminal
clad "Generate a 5000-line audit report" -t audit --detach --keepalive
clad attach audit --no-cc        # in-place watch from another shell
# … later
clad logs audit --tail 100
clad close audit

# Quick environment check
clad doctor
clad config list

# Hot-tune idle for a debugging session
clad config set idle_timeout_minutes 60
# (bridge picks it up on next idle scan, no restart)

# Force in-place attach even on iTerm2
clad attach auth --no-cc
# or globally:
clad config set tmux_attach_mode plain

# Move all clad state to a sandbox directory
CLAD_HOME=/tmp/sandbox clad doctor
CLAD_HOME=/tmp/sandbox clad "test prompt" -t sb
```

---

## Limits & validation rules

- **Tag** must match `^[A-Za-z0-9._-]{1,64}$`. Empty, slashes, control chars,
  unicode bidi overrides, and >64 chars all reject with exit 2.
- **PROMPT_TEXT** has no length cap from `clad`'s side. tmux `send-keys`
  pastes via the X-equivalent buffer; very long prompts (>10k chars) may
  noticeably lag.
- **Number of concurrent sessions** is bounded only by your tmux/process
  limits. Each adds one Claude process + one pane.
- **SSE ring buffer** caches the last 1000 events per session (in-memory in
  the bridge). Replay via `?last_event_id=N`.
- **Per-session JSONL log** is **not rotated** in v1. Long-running keepalive
  sessions can grow large.

---

## Bridge-daemon details (advanced)

You usually don't talk to the bridge directly, but if you need to:

```sh
# Start it in the foreground (for debugging)
.venv/bin/python -m clad.bridge --foreground

# Find its port
cat ~/.clad/bridge.port

# Hit endpoints directly
curl http://127.0.0.1:$(cat ~/.clad/bridge.port)/healthz
curl http://127.0.0.1:$(cat ~/.clad/bridge.port)/sessions?all=true
curl -X DELETE http://127.0.0.1:$(cat ~/.clad/bridge.port)/sessions/<key>?reason=manual

# Kill it (next clad call respawns automatically)
kill $(cat ~/.clad/bridge.pid)
```

Full HTTP contract is in `AGENTS.md` (Bridge HTTP contract section).

---

## What's intentionally not supported (v1)

- **Attaching by pane index or pane id** — only by tag.
- **Cross-project tag namespace** — every tag is scoped to one project root.
- **Multi-user / multi-UID bridge** — bridge binds 127.0.0.1 only, no auth
  token. Same-host users sharing one bridge would step on each other.
- **Programmatic prompts via stdin pipe to `clad`** — prompts must be CLI args.
  (Wrap in `clad "$(cat prompt.txt)" -t mytag` if you need that.)
- **Rotating session logs** — see Limits.

See `.omc/plans/clad-cli-v1.md` §7 "Open Questions" for the v1.1 roadmap.
