"""
MCP client for Robinhood Agentic Trading.
Speaks JSON-RPC 2.0 over HTTP with SSE streaming, per MCP spec (2024-11-05+).
Endpoint: https://agent.robinhood.com/mcp/trading
"""
import json
import uuid
import urllib.request
import urllib.error

MCP_ENDPOINT = "https://agent.robinhood.com/mcp/trading"
PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


class RobinhoodMCPClient:
    def __init__(self, endpoint=MCP_ENDPOINT, access_token=None):
        self.endpoint = endpoint
        self.access_token = access_token
        self.session_id = None

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _rpc(self, method, params=None):
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Capture session id if server provides one
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                body = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise MCPError(f"HTTP {e.code}: {e.read().decode()[:500]}")
        # Handle SSE-wrapped or plain JSON responses
        return self._parse_response(body)

    def _parse_response(self, body):
        body = body.strip()
        # SSE format: lines starting with "data: "
        if body.startswith("data:") or "\ndata:" in body:
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        parsed = json.loads(data)
                        if "result" in parsed or "error" in parsed:
                            return self._unwrap(parsed)
            raise MCPError("No result in SSE stream")
        parsed = json.loads(body)
        return self._unwrap(parsed)

    def _unwrap(self, parsed):
        if "error" in parsed:
            err = parsed["error"]
            raise MCPError(f"MCP error {err.get('code')}: {err.get('message')}")
        return parsed.get("result")

    def initialize(self):
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "robinhood-agent", "version": "0.1.0"},
        })
        # MCP requires notifications/initialized after handshake
        self._rpc("notifications/initialized", {})
        return result

    def list_tools(self):
        result = self._rpc("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name, arguments=None):
        result = self._rpc("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        return result
