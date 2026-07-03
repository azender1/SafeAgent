"""
SafeAgent MCP Server

Exposes safeagent_claim and safeagent_settle as MCP tools over stdio,
compatible with Claude Desktop, Cursor, and any MCP client.

Usage:
    python -m safeagent_exec_guard.mcp_server
    # or
    python safeagent_exec_guard/mcp_server.py
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

SAFEAGENT_BASE_URL = "https://safeagent-production.up.railway.app"

app = Server("safeagent")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="safeagent_claim",
            description=(
                "Claim a SafeAgent execution slot before performing an action. "
                "Returns PROCEED (safe to execute), SKIP (duplicate — skip execution), "
                "or PENDING (another instance is in-flight)."
                "This uses SafeAgent's free test endpoint (limited to 10 calls per IP) — for production use with unlimited claims, see the paid /claim endpoint documented in the SafeAgent README."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "Deterministic, globally-unique ID for this action attempt.",
                    },
                    "action": {
                        "type": "string",
                        "description": "Human-readable action name (e.g. 'send_payment').",
                    },
                },
                "required": ["request_id", "action"],
            },
        ),
        Tool(
            name="safeagent_settle",
            description=(
                "Settle a previously claimed SafeAgent request with its final result. "
                "Call this after the action completes (or fails) to close the execution slot."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The same request_id used in safeagent_claim.",
                    },
                    "result": {
                        "type": "object",
                        "description": "Arbitrary JSON object containing the action outcome.",
                    },
                },
                "required": ["request_id", "result"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        if name == "safeagent_claim":
            request_id = arguments["request_id"]
            action = arguments["action"]
            response = await client.post(
                f"{SAFEAGENT_BASE_URL}/claim/test",
                json={"request_id": request_id, "action": action},
            )
            response.raise_for_status()
            return [TextContent(type="text", text=response.text)]

        if name == "safeagent_settle":
            request_id = arguments["request_id"]
            result = arguments["result"]
            response = await client.post(
                f"{SAFEAGENT_BASE_URL}/settle/{request_id}",
                json=result,
            )
            response.raise_for_status()
            return [TextContent(type="text", text=response.text)]

        raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
