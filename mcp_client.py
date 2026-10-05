"""
MCP client for Robinhood Agentic Trading — hardened.

- Retries with exponential backoff on transient failures
- Robust SSE + plain JSON response parsing
- Session management per MCP spec
- Timeouts on every request
- Detailed error classification (auth vs. transient vs. fatal)
"""
import json
import sys
import time
import uuid
import urllib.request
import urllib.error

MCP_ENDPOINT = "https://agent.robinhood.com/mcp/trading"
PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    """Base MCP error."""


class MCPAuthError(MCPError):
    """401/403 — token invalid or expired. Re-authenticate."""


class MCPTransientError(MCPError):
    """5xx, timeouts — safe to retry."""


class MCPFatalError(MCPError):
    """4xx (non-auth), protocol errors — do not retry."""


class RobinhoodMCPClient:
    def __init__(self, endpoint=MCP_ENDPOINT, access_token=None,
                 timeout=30, max_retries=3):
        self.endpoint = endpoint
        self.access_token = access_token
        self.timeout = timeout
        self.max_retries = max_retries
        self.session_id = None
        self.server_info = None
        self._tools_cache = None
        self._tools_cache_time = 0

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "robinhood-agent/0.2.0",
        }
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _rpc_once(self, method, params):
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode(),
            headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                return self._parse_response(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:500]
            if e.code in (401, 403):
                raise MCPAuthError(
                    f"Auth failed (HTTP {e.code}). Token expired or invalid. "
                    f"Re-authenticate. Body: {body}"
                )
            if 500 <= e.code < 600:
                raise MCPTransientError(f"Server error HTTP {e.code}: {body}")
            raise MCPFatalError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPTransientError(f"Network error: {e}")

    def _rpc(self, method, params=None):
        """RPC with retry on transient failures."""
        last = None
        for attempt in range(self.max_retries):
            try:
                return self._rpc_once(method, params)
            except MCPTransientError as e:
                last = e
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
            except MCPError:
                raise
        raise last

    def _parse_response(self, body):
        body = body.strip()
        if not body:
            raise MCPFatalError("Empty response from server")
        # SSE: extract data lines
        if "data:" in body:
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        try:
                            parsed = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if "result" in parsed or "error" in parsed:
                            return self._unwrap(parsed)
            raise MCPFatalError("SSE stream contained no result")
        try:
            return self._unwrap(json.loads(body))
        except json.JSONDecodeError:
            raise MCPFatalError(f"Invalid JSON response: {body[:200]}")

    def _unwrap(self, parsed):
        if "error" in parsed:
            err = parsed["error"]
            code = err.get("code", 0)
            msg = err.get("message", "unknown")
            # JSON-RPC standard auth-ish codes
            if code in (-32001, 401, 403):
                raise MCPAuthError(f"MCP auth error: {msg}")
            raise MCPFatalError(f"MCP error {code}: {msg}")
        return parsed.get("result")

    def initialize(self):
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "robinhood-agent", "version": "0.2.0"},
        })
        # Verify protocol compatibility
        server_version = (result or {}).get("protocolVersion", "")
        if server_version and server_version != PROTOCOL_VERSION:
            print(
                f"Warning: server protocol version {server_version} "
                f"differs from client {PROTOCOL_VERSION}. "
                "Some calls may fail.",
                file=sys.stderr,
            )
        self.server_info = (result or {}).get("serverInfo", {})
        # Per spec, send initialized notification (no response expected)
        try:
            self._rpc_once("notifications/initialized", {})
        except MCPError:
            pass  # notification; ignore errors
        return result

    def list_tools(self, use_cache=True):
        # Cache tool list for 5 minutes — it rarely changes
        if use_cache and self._tools_cache and time.time() - self._tools_cache_time < 300:
            return self._tools_cache
        result = self._rpc("tools/list", {})
        tools = result.get("tools", [])
        self._tools_cache = tools
        self._tools_cache_time = time.time()
        return tools

    def call_tool(self, name, arguments=None):
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return result

    def find_tool(self, *keywords):
        """Find first tool matching all keywords (case-insensitive)."""
        for tool in self.list_tools():
            name = tool.get("name", "").lower()
            if all(k.lower() in name for k in keywords):
                return tool["name"]
        return None
