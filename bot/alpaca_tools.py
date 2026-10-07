"""Alpaca MCP server pin and the tools no Claude session may ever call.

BLOCKED_TOOLS is mirrored as deny rules in .claude/settings.json (Claude Code) and will be
passed as disallowed tools to the bot's Agent SDK session. Tests keep them in sync.
"""

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
