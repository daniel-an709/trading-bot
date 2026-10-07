"""Alpaca MCP server pin and the tools no Claude session may ever call.

BLOCKED_TOOLS is mirrored as deny rules in .claude/settings.json (Claude Code) and passed as
disallowed tools to the bot's Agent SDK session. READ_TOOLS is the only Alpaca access the bot's
Claude session gets (approved by the user 2026-10-07). Tests keep them in sync.
"""

import json
from pathlib import Path

MCP_SERVER_NAME = "alpaca"
MCP_SERVER_PACKAGE = "alpaca-mcp-server==2.3.2"

BLOCKED_TOOLS = frozenset({
    # place orders
    "place_stock_order",
    "place_crypto_order",
    "place_option_order",
    # change or cancel orders
    "replace_order_by_id",
    "cancel_order_by_id",
    "cancel_all_orders",
    # close positions
    "close_position",
    "close_all_positions",
    # options
    "exercise_options_position",
    "do_not_exercise_options_position",
    # account settings, short locates, watchlists
    "update_account_config",
    "create_locate",
    "create_watchlist",
    "update_watchlist_by_id",
    "delete_watchlist_by_id",
    "add_asset_to_watchlist_by_id",
    "remove_asset_from_watchlist_by_id",
})

# Read-only tools the bot's Claude session may call.
READ_TOOLS = frozenset({
    "get_news",
    "get_corporate_action_announcements",
    "get_account_info",
    "get_all_positions",
    "get_orders",
    "get_stock_snapshot",
    "get_clock",
})

# Every tool the pinned server version exposes (regenerate when MCP_SERVER_PACKAGE changes).
SERVER_TOOLS = frozenset(json.loads(Path(__file__).with_name("alpaca_mcp_tools.json").read_text())["tools"])


def mcp_name(tool: str) -> str:
    return f"mcp__{MCP_SERVER_NAME}__{tool}"
