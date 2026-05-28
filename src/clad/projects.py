"""Project root resolution."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

#: Allowed characters in a tag: ASCII alphanumerics, underscore, hyphen, dot.
#: Max length 64. Rejects shell metacharacters, control chars, unicode RTL etc.
_TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def resolve_project_root(cwd: Path | None = None) -> Path:
    """Resolve project root using `git rev-parse --show-toplevel`, fall back to CWD."""
    start = Path(cwd) if cwd else Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            top = result.stdout.strip()
            if top:
                return Path(top).resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return start.resolve()


def project_hash(project_root: Path) -> str:
    """Stable, filesystem-safe id for a project root (first 10 hex of sha1)."""
    return hashlib.sha1(str(project_root).encode("utf-8")).hexdigest()[:10]


def session_key(project_root: Path, tag: str) -> str:
    """Build the channel/session key: '<projhash>-<tag>'.

    Tag must match ``[A-Za-z0-9._-]{1,64}`` — anything else (path separators,
    control chars, shell metacharacters, unicode bidi overrides) is rejected.
    """
    if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
        raise ValueError(
            f"invalid tag {tag!r}: must match [A-Za-z0-9._-] and be 1-64 chars"
        )
    return f"{project_hash(project_root)}-{tag}"


def tmux_session_name(project_root: Path) -> str:
    """The tmux session that holds all panes for this project."""
    return f"clad-{project_hash(project_root)}"
