"""B3: over stdio, the MCP server writes only protocol messages to stdout.

A log line on stdout breaks the stdio transport (unclecode/crawl4ai#1968). The
server runs as a real subprocess; a tool call fails (nothing listens on the API
URL) so the error and HTTP-client logging paths run too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_network  # loopback only: a closed local port


def _messages() -> str:
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "assemble_context",
            "arguments": {"query": "q", "urls": ["https://docs.example.com/a"]}}},
    ]  # fmt: skip
    return "".join(json.dumps(c) + "\n" for c in calls)


def test_stdout_carries_only_json_rpc(tmp_path: Path) -> None:
    env = {**os.environ, "API_URL": "http://127.0.0.1:9", "API_KEY": "test-key"}
    stderr = (tmp_path / "stderr.txt").open("w")  # an unread pipe would fill up
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, our own module
        [sys.executable, "-m", "app.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        env=env,
    )
    watchdog = threading.Timer(120, proc.kill)  # never hang the suite
    watchdog.start()
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(_messages())
    proc.stdin.flush()
    lines = []
    while True:  # keep stdin open until the tool call has answered
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if '"id":3' in line.replace(" ", ""):
            break
    proc.stdin.close()
    proc.wait(timeout=30)
    watchdog.cancel()
    stderr.close()
    lines += [line for line in proc.stdout.read().splitlines() if line.strip()]

    replies = [json.loads(line) for line in lines]  # any non-JSON line fails here
    assert all(r.get("jsonrpc") == "2.0" for r in replies)
    by_id = {r["id"]: r for r in replies if "id" in r}
    assert set(by_id) == {1, 2, 3}
    names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert "assemble_context" in names
    assert by_id[3]["result"]["isError"] is True  # the failure is a tool error
