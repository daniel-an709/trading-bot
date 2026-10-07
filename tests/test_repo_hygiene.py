"""Guards that keep secrets out of git and the hard rules in CLAUDE.md."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize("path", [
    ".env", ".env.local", ".env.paper", "config/.env", ".mcp.json",
    "logs/runs.jsonl", "KILL", ".venv/bin/python",
])
def test_sensitive_paths_are_ignored(path):
    assert git("check-ignore", "-q", "--no-index", path).returncode == 0, f"{path} is not git-ignored"


def test_env_example_is_not_ignored():
    assert git("check-ignore", "-q", "--no-index", ".env.example").returncode == 1


def test_no_secret_files_tracked():
    tracked = [p for p in git("ls-files", "-z").stdout.split("\0") if p]
    names = [Path(p).name for p in tracked]
    bad = [n for n in names if (n.startswith(".env") and n != ".env.example") or n == ".mcp.json"]
    assert bad == []


def test_env_example_lists_names_only():
    for line in (ROOT / ".env.example").read_text().splitlines():
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=", line), f"unexpected line in .env.example: {line!r}"


def test_claude_md_hard_rules_section():
    text = (ROOT / "CLAUDE.md").read_text()
    section = text.split("## Hard rules", 1)[1].split("\n## ", 1)[0]
    for phrase in [
        "https://paper-api.alpaca.markets", "`KILL` file", "KILL_SWITCH",
        "bot/executor.py", "bot/validator.py", "config/limits.yaml", ".env", ".mcp.json",
    ]:
        assert phrase in section, f"{phrase!r} missing from CLAUDE.md hard rules"


def test_claude_md_consistency_section():
    text = (ROOT / "CLAUDE.md").read_text()
    section = text.split("## Consistency", 1)[1].split("\n## ", 1)[0]
    assert "config/limits.yaml" in section and "risk/SKILL.md" in section


def test_constraints_pin_every_requirement():
    def pins(name: str) -> dict[str, str]:
        out = {}
        for line in (ROOT / name).read_text().splitlines():
            if "==" in line:
                pkg, ver = line.split("==")
                out[pkg.strip().lower().replace("_", "-")] = ver.strip()
        return out

    reqs, constraints = pins("requirements.txt"), pins("constraints.txt")
    for pkg, ver in reqs.items():
        assert constraints.get(pkg) == ver, f"{pkg} not pinned to {ver} in constraints.txt"
