"""Unit tests for MCP client parsing and error classification."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_client import RobinhoodMCPClient, MCPAuthError, MCPFatalError


def test_parse_plain_json():
    c = RobinhoodMCPClient()
    body = json.dumps({"jsonrpc": "2.0", "id": "1", "result": {"tools": []}})
    assert c._parse_response(body) == {"tools": []}


def test_parse_sse():
    c = RobinhoodMCPClient()
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":{"ok":true}}\n'
    assert c._parse_response(body) == {"ok": True}


def test_parse_sse_skips_done():
    c = RobinhoodMCPClient()
    body = 'data: {"jsonrpc":"2.0","id":"1","result":{"v":1}}\ndata: [DONE]\n'
    assert c._parse_response(body) == {"v": 1}


def test_unwrap_error():
    c = RobinhoodMCPClient()
    try:
        c._unwrap({"error": {"code": -32601, "message": "Method not found"}})
        assert False
    except MCPFatalError as e:
        assert "Method not found" in str(e)


def test_unwrap_auth_error():
    c = RobinhoodMCPClient()
    try:
        c._unwrap({"error": {"code": 401, "message": "Unauthorized"}})
        assert False
    except MCPAuthError:
        pass


def test_empty_response():
    c = RobinhoodMCPClient()
    try:
        c._parse_response("   ")
        assert False
    except MCPFatalError:
        pass


def test_invalid_json():
    c = RobinhoodMCPClient()
    try:
        c._parse_response("not json at all")
        assert False
    except MCPFatalError:
        pass


def test_headers_include_auth():
    c = RobinhoodMCPClient(access_token="tok123")
    h = c._headers()
    assert h["Authorization"] == "Bearer tok123"
    assert "text/event-stream" in h["Accept"]


def test_headers_session_id():
    c = RobinhoodMCPClient()
    c.session_id = "sess-abc"
    assert c._headers()["Mcp-Session-Id"] == "sess-abc"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
