"""Integration test: verify MCP SessionManager is started before tool calls.

Requires mcp extra. Skips gracefully when absent.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

pytest.importorskip("mcp.server")


class TestMcpLifespan:
    """Verify the session_manager.run() lifespan is entered when the MCP
    server is served via uvicorn (or driven directly)."""

    def test_task_group_initialized_after_lifespan(self) -> None:
        """Enter the lifespan, assert the SessionManager task-group is alive,
        then call ``initialize`` to start an MCP session and finally call
        ``session_status`` — no ``RuntimeError`` at any point."""
        from mcp.server import MCPServer

        server = MCPServer(name="test", version="0.0.1")
        inner = server.streamable_http_app(json_response=True)

        from voxium.agent_http import AgentBearerMiddleware

        token = "test-token"
        app = AgentBearerMiddleware(inner, token=token)

        async def _run() -> None:
            import json

            session_mgr = server.session_manager
            session_mgr._task_group = None  # reset / ensure fresh
            assert session_mgr._task_group is None

            # ---- Lifespan protocol (mimics uvicorn) ----
            startup_complete = False
            msg_queue: list[dict[str, Any]] = [{"type": "lifespan.startup"}]

            shutdown_event = anyio.Event()

            async def lifespan_receive() -> dict[str, Any]:
                if msg_queue:
                    return msg_queue.pop(0)
                await shutdown_event.wait()
                return {"type": "lifespan.shutdown"}

            async def lifespan_send(msg: dict[str, Any]) -> None:
                nonlocal startup_complete
                if msg["type"] == "lifespan.startup.complete":
                    startup_complete = True

            scope_lifespan = {
                "type": "lifespan",
                "asgi": {"version": "3.0", "spec_version": "2.0"},
                "state": {},
            }

            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    app.__call__,
                    scope_lifespan,
                    lifespan_receive,
                    lifespan_send,
                )

                await anyio.sleep(0.2)
                assert startup_complete, "Lifespan did not startup"
                assert (
                    session_mgr._task_group is not None
                ), "Task group not initialized after lifespan entry"

                # ---- Helper: send a JSON-RPC request on /mcp ----
                session_id: str | None = None
                responses: list[dict[str, Any]] = []

                async def http_send(msg: dict[str, Any]) -> None:
                    responses.append(msg)

                async def mcp_call(
                    method: str, params: dict[str, Any] | None = None
                ) -> dict[str, Any]:
                    nonlocal session_id
                    payload = {
                        "jsonrpc": "2.0",
                        "id": method,  # simple unique id
                        "method": method,
                    }
                    if params is not None:
                        payload["params"] = params
                    body = json.dumps(payload).encode()
                    rcv_iter: list[dict[str, Any]] = [
                        {"type": "http.request", "body": body},
                        {"type": "http.request", "body": b""},
                    ]

                    hdrs: list[tuple[bytes, bytes]] = [
                        (b"content-type", b"application/json"),
                        (b"accept", b"application/json"),
                        (b"authorization", f"Bearer {token}".encode()),
                        (b"host", b"localhost:12345"),
                    ]
                    if session_id is not None:
                        hdrs.append((b"mcp-session-id", session_id.encode()))

                    async def http_receive() -> dict[str, Any]:
                        return rcv_iter.pop(0)

                    responses.clear()
                    await app(
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/mcp",
                            "query_string": b"",
                            "root_path": "",
                            "headers": hdrs,
                        },
                        http_receive,
                        http_send,
                    )

                    resp_body = b""
                    for msg in responses:
                        if msg.get("type") == "http.response.body":
                            resp_body = msg.get("body", b"")
                            break

                    # Capture session ID from initialize response headers
                    if method == "initialize":
                        for msg in responses:
                            hdrs_list = msg.get("headers", []) or []
                            for k, v in hdrs_list:
                                if k == b"mcp-session-id":
                                    session_id = v.decode()
                                    break

                    return json.loads(resp_body.decode())

                # 1. Initialize handshake (creates session)
                init_result = await mcp_call(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1.0"},
                    },
                )
                assert init_result.get("jsonrpc") == "2.0", init_result
                assert (
                    "error" not in init_result
                ), f"Initialize failed: {init_result.get('error')}"

                # 2. Call session_status (the tool)
                tool_result = await mcp_call(
                    "tools/call",
                    {"name": "session_status", "arguments": {}},
                )
                assert tool_result.get("jsonrpc") == "2.0", tool_result
                assert (
                    "error" not in tool_result
                ), f"Tool call failed: {tool_result.get('error')}"
                assert "result" in tool_result, "Tool call had no result"

                # Signal lifespan to shut down (inside the task group scope)
                shutdown_event.set()

        anyio.run(_run)
