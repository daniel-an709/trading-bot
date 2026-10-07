"""Keep the skills Claude reasons with in step with the limits the validator enforces.

risk/SKILL.md and strategy/SKILL.md each carry one config block:

    <!-- config: limits.yaml -->
    - `max_position_pct`: 5 — ...
    <!-- /config -->

The block must list every key of its YAML file with the same value. Outside the
block those two skills contain no digits at all, so a changed limit can't survive
as a stale number somewhere in the prose.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude/skills"

BLOCK = re.compile(r"<!-- config: (\S+) -->\n(.*?)\n<!-- /config -->", re.DOTALL)
ENTRY = re.compile(r"^- `([\w.]+)`: (.+?)(?: — .*)?$")


def skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    return yaml.safe_load(text.split("---\n", 2)[1])


def flatten(data: dict, prefix: str = "") -> dict[str, object]:
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def parse_block(text: str) -> tuple[str, dict[str, str], str]:
    blocks = BLOCK.findall(text)
    assert len(blocks) == 1, "expected exactly one <!-- config: ... --> block"
    source, body = blocks[0]
    entries = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        m = ENTRY.match(line)
        assert m, f"unparseable config line: {line!r}"
        assert m.group(1) not in entries, f"duplicate key in config block: {m.group(1)}"
        entries[m.group(1)] = m.group(2).strip()
    outside = BLOCK.sub("", text)
    return source, entries, outside


def same(block_value: str, yaml_value) -> bool:
    if isinstance(yaml_value, list):
        return sorted(t.strip() for t in block_value.split(",")) == sorted(yaml_value)
    if isinstance(yaml_value, (int, float)) and not isinstance(yaml_value, bool):
        try:
            return float(block_value.rstrip("%")) == float(yaml_value)
        except ValueError:
            return False
    return block_value == str(yaml_value)


@pytest.mark.parametrize("name", ["strategy", "risk", "journal"])
def test_skill_frontmatter(name):
    meta = frontmatter(skill_text(name))
    assert meta["name"] == name
    assert len(meta["description"]) >= 100, "description must say specifically when to use the skill"


@pytest.mark.parametrize("skill,config", [("risk", "limits.yaml"), ("strategy", "strategy.yaml")])
def test_config_block_matches_yaml(skill, config):
    source, entries, _ = parse_block(skill_text(skill))
    assert source == config
    expected = flatten(yaml.safe_load((ROOT / "config" / config).read_text()))
    assert set(entries) == set(expected), (
        f"{skill}/SKILL.md block keys differ from config/{config}: "
        f"missing {sorted(set(expected) - set(entries))}, unknown {sorted(set(entries) - set(expected))}"
    )
    stale = {k: (entries[k], expected[k]) for k in expected if not same(entries[k], expected[k])}
    assert not stale, f"{skill}/SKILL.md is out of date with config/{config} (skill, yaml): {stale}"


@pytest.mark.parametrize("skill", ["risk", "strategy"])
def test_no_numbers_outside_config_block(skill):
    _, _, outside = parse_block(skill_text(skill))
    body = outside.split("---\n", 2)[2]
    list_marker = re.compile(r"^\s*\d+\.\s")  # "1. " numbering is not a config value
    stale = [line for line in body.splitlines() if re.search(r"\d", list_marker.sub("", line))]
    assert not stale, f"{skill}/SKILL.md has numbers outside its config block (refer to keys instead): {stale}"


def test_duplicate_block_key_rejected():
    text = "<!-- config: limits.yaml -->\n- `max_position_pct`: 5\n- `max_position_pct`: 3\n<!-- /config -->"
    with pytest.raises(AssertionError, match="duplicate"):
        parse_block(text)


def test_list_values_compared_exactly():
    assert not same("SPY, SPY, QQQ", ["SPY", "QQQ"])
    assert same("QQQ, SPY", ["SPY", "QQQ"])


def test_non_numeric_values_compared_as_text():
    assert same("1Day", "1Day") and not same("1Day", "1Hour")


@pytest.mark.parametrize("skill", ["risk", "strategy"])
def test_no_numbers_in_description(skill):
    description = frontmatter(skill_text(skill))["description"]
    assert not re.search(r"\d", description), f"{skill} description contains numbers: {description!r}"


def test_risk_skill_states_validator_limit_band():
    # The band is hard-coded in bot/validator.py; the skill describes it in words.
    from decimal import Decimal

    from bot.validator import LIMIT_BAND_HIGH, LIMIT_BAND_LOW

    assert (LIMIT_BAND_LOW, LIMIT_BAND_HIGH) == (Decimal("0.90"), Decimal("1.01")), (
        "validator limit band changed: update the wording in risk/SKILL.md, then this test"
    )
    text = skill_text("risk")
    assert "more than one percent above the latest price" in text
    assert "more than ten percent below it" in text


def test_journal_examples_pass_the_real_parser():
    from bot.schema import parse_proposal

    blocks = re.findall(r"```json\n(.*?)\n```", skill_text("journal"), re.DOTALL)
    assert len(blocks) >= 3
    for block in blocks:
        parse_proposal(block)
