"""The Alpaca MCP tools that place, change, cancel or close anything are blocked everywhere.

tests/fixtures/alpaca_mcp_tools.json is the tool list of the pinned server version. When the
version changes, regenerate it; these tests then flag any new write tool that isn't blocked.
"""

import json
import re
from pathlib import Path

from bot.alpaca_tools import BLOCKED_TOOLS, MCP_SERVER_NAME, MCP_SERVER_PACKAGE

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests/fixtures/alpaca_mcp_tools.json").read_text())
WRITE_VERB = re.compile(r"^(place|replace|cancel|close|exercise|do_not_exercise|update|create|delete|add|remove|patch|post)_")


def test_fixture_matches_pinned_server():
    assert FIXTURE["server"] == MCP_SERVER_PACKAGE


def test_every_write_tool_is_blocked():
    writes = {t for t in FIXTURE["tools"] if WRITE_VERB.match(t)}
    assert writes, "fixture has no write tools - wrong fixture?"
    assert writes <= BLOCKED_TOOLS, f"write tools not blocked: {sorted(writes - BLOCKED_TOOLS)}"


def test_blocked_tools_exist_in_server():
    assert BLOCKED_TOOLS <= set(FIXTURE["tools"]), f"unknown names: {sorted(BLOCKED_TOOLS - set(FIXTURE['tools']))}"


def test_claude_code_settings_deny_every_blocked_tool():
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    deny = set(settings["permissions"]["deny"])
    missing = {f"mcp__{MCP_SERVER_NAME}__{t}" for t in BLOCKED_TOOLS} - deny
    assert not missing, f".claude/settings.json does not deny: {sorted(missing)}"


def test_claude_code_settings_hold_no_secrets():
    text = (ROOT / ".claude/settings.json").read_text()
    assert "ALPACA_API_KEY" not in text and "ALPACA_SECRET_KEY" not in text and "ANTHROPIC_API_KEY" not in text
