"""The Alpaca MCP tools that place, change, cancel or close anything are blocked everywhere.

bot/alpaca_mcp_tools.json is the tool list of the pinned server version. When the
version changes, regenerate it; these tests then flag any new write tool that isn't blocked.
"""

import json
import re
from pathlib import Path

from bot.alpaca_tools import BLOCKED_TOOLS, MCP_SERVER_NAME, MCP_SERVER_PACKAGE, READ_TOOLS, SERVER_TOOLS, mcp_name

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "bot/alpaca_mcp_tools.json").read_text())
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


def test_server_tools_loaded_from_pinned_list():
    assert SERVER_TOOLS == set(FIXTURE["tools"]) and len(SERVER_TOOLS) == 72


def test_read_tools_are_exactly_the_approved_seven():
    assert READ_TOOLS == {
        "get_news", "get_corporate_action_announcements", "get_account_info",
        "get_all_positions", "get_orders", "get_stock_snapshot", "get_clock",
    }


def test_read_tools_exist_and_are_not_blocked():
    assert READ_TOOLS <= SERVER_TOOLS
    assert not READ_TOOLS & BLOCKED_TOOLS


def test_no_read_tool_has_write_verb():
    assert not [t for t in READ_TOOLS if WRITE_VERB.match(t)]


def test_mcp_name():
    assert mcp_name("get_news") == "mcp__alpaca__get_news"
