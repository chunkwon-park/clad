"""Entry-point alias so ``python -m clad.bridge.mcp <key>`` works."""
from __future__ import annotations

from .mcp_server import main_cli

if __name__ == "__main__":
    main_cli()
