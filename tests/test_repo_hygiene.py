"""Guards that keep secrets out of git and the hard rules in CLAUDE.md."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_gitignore_covers_secrets_and_runtime():
    lines = set((ROOT / ".gitignore").read_text().split())
    assert {".env", "logs/", "KILL", ".venv/"} <= lines


def test_env_file_is_not_tracked():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert ".env" not in tracked


def test_env_example_has_no_values():
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, _, value = line.partition("=")
        assert value in ("", "0"), f"{key} has a value in .env.example"


def test_claude_md_states_hard_rules():
    text = (ROOT / "CLAUDE.md").read_text()
    for phrase in ["paper", "KILL", "executor.py", "validator.py", "limits.yaml", "risk/SKILL.md", ".env"]:
        assert phrase in text
