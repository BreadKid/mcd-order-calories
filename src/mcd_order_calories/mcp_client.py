"""Minimal Streamable HTTP MCP client.

Only the Python standard library is used, so the project runs on any
Python >= 3.9 without a build step or third-party packages. The client
implements just enough of the Model Context Protocol to drive the
McDonald's China MCP server (https://mcp.mcd.cn):

    initialize -> notifications/initialized -> tools/list -> tools/call

Reference: https://modelcontextprotocol.io/specification
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "mcd-party-planner"
CLIENT_VERSION = "0.1.0"
DEFAULT_TIMEOUT = 60.0

_ACCEPT = "application/json, text/event-stream"


class McpError(RuntimeError):
    """A JSON-RPC error, an HTTP failure, or a malformed server response."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class McpTransportError(McpError):
    """The server could not be reached or spoke something other than MCP."""


@dataclass
class ToolResult:
    """One ``tools/call`` result."""

    content: list[Mapping[str, Any]]
    is_error: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Concatenated text blocks, which is how every MCP tool reports data."""
        return "\n".join(
            str(block.get("text", ""))
            for block in self.content
            if block.get("type") == "text"
        )

    def json(self) -> Any:
        """Parse the text payload as JSON.

        MCP servers are free to return prose; this raises ``McpError`` when the
        payload is not JSON so callers can fall back to :attr:`text`.
        """
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise McpError(f"tool result is not JSON: {self.text[:200]!r}") from exc


class McpHttpClient:
    """A synchronous Streamable HTTP MCP client for one server."""

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not url:
            raise ValueError("url is required")
        self.url = url
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        self._tools: list[Mapping[str, Any]] | None = None
        self._instructions: str | None = None
        self._server_info: Mapping[str, Any] | None = None
        self._initialized = False

        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": _ACCEPT,
            "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        if headers:
            self._headers.update(dict(headers))

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def __enter__(self) -> "McpHttpClient":
        self.initialize()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Drop the session.

        The specification makes session teardown optional (HTTP DELETE); a
        server that does not implement it is still correct, so failures here
        are deliberately ignored.
        """
        if not self._session_id:
            return
        session_id, self._session_id = self._session_id, None
        self._initialized = False
        request = urllib.request.Request(self.url, method="DELETE")
        for key, value in self._headers.items():
            request.add_header(key, value)
        request.add_header("Mcp-Session-Id", session_id)
        try:
            urllib.request.urlopen(request, timeout=self.timeout).close()
        except Exception:  # noqa: BLE001 - teardown is best effort
            pass

    def initialize(self) -> Mapping[str, Any]:
        """Perform the MCP handshake and cache the server's instructions.

        Idempotent: a second call returns the cached ``serverInfo``.
        """
        if self._initialized and self._server_info is not None:
            return self._server_info
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        server_info = result.get("serverInfo", {})
        self._instructions = result.get("instructions")
        # Notifications must not be answered; a 202 with an empty body is the
        # normal reply and an error is not fatal for stateless servers.
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            pass
        self._server_info = server_info if isinstance(server_info, Mapping) else {}
        self._initialized = True
        return self._server_info

    @property
    def server_info(self) -> Mapping[str, Any]:
        """The server's ``serverInfo``, performing the handshake if needed."""
        return self.initialize()

    @property
    def server_instructions(self) -> str | None:
        return self._instructions

    # ------------------------------------------------------------------ #
    # tools
    # ------------------------------------------------------------------ #

    def list_tools(self, *, refresh: bool = False) -> list[Mapping[str, Any]]:
        """Return the server's tool definitions, cached after the first call."""
        if self._tools is None or refresh:
            result = self._request("tools/list", {})
            self._tools = list(result.get("tools", []))
        return self._tools

    def tool_names(self) -> list[str]:
        return [str(tool.get("name", "")) for tool in self.list_tools()]

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """Invoke one tool by its wire name (for example ``query-meals``)."""
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        blocks = result.get("content")
        if not isinstance(blocks, list):
            raise McpTransportError(
                f"tools/call for {name!r} returned no content list: {result!r}"
            )
        return ToolResult(
            content=[b for b in blocks if isinstance(b, Mapping)],
            is_error=bool(result.get("isError", False)),
            raw=result,
        )

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #

    def _request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        # Any request other than the handshake itself implies a live session,
        # so a caller that never used the context manager still works.
        if method != "initialize" and not self._initialized:
            self.initialize()
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params)}
        body = self._post(payload)
        if not isinstance(body, Mapping):
            raise McpTransportError(f"{method}: expected a JSON object, got {type(body).__name__}")
        if "error" in body:
            error = body["error"] or {}
            raise McpError(
                f"{method} failed: {error.get('message', 'unknown error')}",
                code=error.get("code"),
                data=error.get("data"),
            )
        result = body.get("result")
        if not isinstance(result, Mapping):
            raise McpTransportError(f"{method}: response carried no result object")
        return result

    def _post(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        for key, value in self._headers.items():
            request.add_header(key, value)
        if self._session_id:
            request.add_header("Mcp-Session-Id", self._session_id)
            request.add_header("MCP-Protocol-Version", PROTOCOL_VERSION)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                if response.status == 202:
                    return None
                content_type = (response.headers.get("Content-Type") or "").lower()
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code == 401 or exc.code == 403:
                raise McpTransportError(
                    f"HTTP {exc.code} from {self.url}; check MCD_MCP_TOKEN is set and valid. "
                    f"Body: {detail}"
                ) from exc
            raise McpTransportError(f"HTTP {exc.code} from {self.url}: {detail}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise McpTransportError(f"cannot reach {self.url}: {exc}") from exc

        if not raw.strip():
            return None
        if "text/event-stream" in content_type:
            return self._first_sse_message(raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpTransportError(f"server returned non-JSON body: {raw[:200]!r}") from exc
        if not isinstance(parsed, Mapping):
            raise McpTransportError(f"server returned a JSON {type(parsed).__name__}, expected object")
        return parsed

    @staticmethod
    def _first_sse_message(raw: str) -> Mapping[str, Any]:
        """Return the first JSON-RPC message in an SSE stream.

        A Streamable HTTP server may answer a request with ``text/event-stream``
        even though exactly one response is expected. Server-sent pings and
        unrelated notifications are skipped.
        """
        data_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue
            if line.strip() == "" and data_lines:
                message = McpHttpClient._decode_sse_data(data_lines)
                data_lines = []
                if message is not None:
                    return message
        if data_lines:
            message = McpHttpClient._decode_sse_data(data_lines)
            if message is not None:
                return message
        raise McpTransportError("SSE response contained no JSON-RPC message")

    @staticmethod
    def _decode_sse_data(data_lines: list[str]) -> Mapping[str, Any] | None:
        chunk = "\n".join(data_lines)
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, Mapping) and "id" in parsed:
            return parsed
        return None
