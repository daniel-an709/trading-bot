"""Hard-coded safety checks: paper endpoint only, and the kill switch."""

from collections.abc import Mapping
from pathlib import Path

PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


def kill_switch_reason(project_root: Path, environ: Mapping[str, str]) -> str | None:
    """Return why the bot must halt, or None. Any KILL_SWITCH value except empty or 0 halts."""
    if (project_root / "KILL").exists():
        return "KILL file present"
    value = environ.get("KILL_SWITCH", "").strip()
    if value not in ("", "0"):
        return f"KILL_SWITCH={value}"
    return None


def is_paper_endpoint(url: str) -> bool:
    if not isinstance(url, str):
        return False
    return url.removesuffix("/") == PAPER_ENDPOINT
