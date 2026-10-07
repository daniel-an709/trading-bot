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
        entries[m.group(1)] = m.group(2).strip()
    outside = BLOCK.sub("", text)
    return source, entries, outside


def same(block_value: str, yaml_value) -> bool:
    if isinstance(yaml_value, list):
        return {t.strip() for t in block_value.split(",")} == set(yaml_value)
    return float(block_value.rstrip("%")) == float(yaml_value)


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
