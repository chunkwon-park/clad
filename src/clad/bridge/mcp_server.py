"""MCP stdio server for the clad bridge.

This module is invoked as ``python -m clad.bridge.mcp <session_key>`` with the
environment variable ``CLAD_BRIDGE_URL=http://127.0.0.1:<port>``.

It exposes three tools to Claude running in a tmux pane:
  - clad_get_prompt   — long-poll for the next prompt (up to 30 s)
  - clad_emit_token   — stream a token back through the bridge
  - clad_emit_done    — signal completion

Channels reference: per plan §Risk R-1, the exact MCP tool surface for channels
may need adjustment after reading https://code.claude.com/docs/ko/channels-reference.
This implementation provides the three tools (clad_get_prompt, clad_emit_token,
clad_emit_done) over stdio MCP using the official ``mcp`` PyPI package.
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types


def _key() -> str:
    if len(sys.argv) < 2:
        raise RuntimeError("Usage: python -m clad.bridge.mcp <session_key>")
    return sys.argv[1]


def _base_url() -> str:
    url = os.environ.get("CLAD_BRIDGE_URL", "")
    if not url:
        raise RuntimeError("CLAD_BRIDGE_URL environment variable is not set")
    return url.rstrip("/")


server = Server("clad-bridge")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="clad_get_prompt",
            description=(
                "Poll the clad bridge for the next prompt to process. "
                "Blocks up to 30 s. Returns the prompt text, or an empty string "
                "if no prompt arrived. Call this in a loop."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="clad_emit_token",
            description="Emit a streaming token back to the clad CLI.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The token text to stream.",
                    }
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="clad_emit_done",
            description="Signal that the current prompt response is complete.",
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Optional summary of the completed response.",
                    }
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    key = _key()
    base = _base_url()

    async with httpx.AsyncClient(timeout=35.0) as client:
        if name == "clad_get_prompt":
            resp = await client.get(
                f"{base}/internal/mcp/{key}/next-prompt",
                timeout=35.0,
            )
            resp.raise_for_status()
            data = resp.json()
            prompt = data.get("prompt") or ""
            return [types.TextContent(type="text", text=prompt)]

        elif name == "clad_emit_token":
            text = arguments.get("text", "")
            resp = await client.post(
                f"{base}/internal/mcp/{key}/token",
                json={"text": text},
            )
            resp.raise_for_status()
            return [types.TextContent(type="text", text="ok")]

        elif name == "clad_emit_done":
            summary = arguments.get("summary", None)
            payload: dict = {}
            if summary is not None:
                payload["summary"] = summary
            resp = await client.post(
                f"{base}/internal/mcp/{key}/done",
                json=payload,
            )
            resp.raise_for_status()
            return [types.TextContent(type="text", text="ok")]

        else:
            raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main_cli() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_cli()
