#!/usr/bin/env python3
"""Validate YAML frontmatter used by agent rules and skills."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "ERROR: PyYAML is required; run "
        "`python -m pip install -r requirements-agent-harness.txt`"
    ) from exc


class FrontmatterError(ValueError):
    """Raised when a rule or skill has invalid frontmatter."""


class UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise FrontmatterError(f"duplicate frontmatter key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(path: Path, message: str) -> FrontmatterError:
    return FrontmatterError(f"{path}: {message}")


def load_frontmatter(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise _fail(path, "frontmatter must start on the first line")

    try:
        end = next(
            index for index in range(1, len(lines)) if lines[index].strip() == "---"
        )
    except StopIteration as exc:
        raise _fail(path, "frontmatter closing delimiter is missing") from exc

    raw = "\n".join(lines[1:end])
    try:
        data = yaml.load(raw, Loader=UniqueKeySafeLoader)
    except (yaml.YAMLError, FrontmatterError) as exc:
        raise _fail(path, f"invalid YAML frontmatter: {exc}") from exc

    if not isinstance(data, dict):
        raise _fail(path, "frontmatter must be a YAML mapping")
    if any(not isinstance(key, str) for key in data):
        raise _fail(path, "frontmatter keys must be strings")
    return data


def _require_string(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(path, f"{key} must be a non-empty string")
    return value


def validate_skill(data: dict[str, Any], path: Path) -> None:
    name = _require_string(data, "name", path)
    _require_string(data, "description", path)
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise _fail(path, "name must be a lowercase kebab-case string")


def validate_claude_rule(data: dict[str, Any], path: Path) -> None:
    paths = data.get("paths")
    if not isinstance(paths, list) or not paths:
        raise _fail(path, "paths must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in paths):
        raise _fail(path, "every paths item must be a non-empty string")


def validate_cursor_rule(data: dict[str, Any], path: Path) -> None:
    _require_string(data, "description", path)
    _require_string(data, "globs", path)
    if data.get("alwaysApply") is not False:
        raise _fail(path, "alwaysApply must be the YAML boolean false")


def infer_kind(path: Path) -> str:
    posix = path.as_posix()
    if posix.startswith(".claude/rules/"):
        return "claude-rule"
    if posix.startswith(".cursor/rules/"):
        return "cursor-rule"
    if path.name == "SKILL.md":
        return "skill"
    raise _fail(path, "cannot infer frontmatter kind from path")


def validate_path(path: Path) -> None:
    data = load_frontmatter(path)
    kind = infer_kind(path)
    validators = {
        "skill": validate_skill,
        "claude-rule": validate_claude_rule,
        "cursor-rule": validate_cursor_rule,
    }
    validators[kind](data, path)


def run_self_test() -> None:
    cases = {
        "valid-skill/SKILL.md": (
            '---\nname: valid-skill\ndescription: "valid description"\n---\n',
            True,
        ),
        "bad-list/SKILL.md": (
            "---\nname: [invalid\ndescription: test\n---\n",
            False,
        ),
        "bad-colon/SKILL.md": (
            "---\nname: invalid\ndescription: foo: bar\n---\n",
            False,
        ),
        "bad-type/SKILL.md": (
            "---\nname: invalid\ndescription: [not, a, string]\n---\n",
            False,
        ),
    }
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for relative, (content, should_pass) in cases.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            passed = True
            try:
                validate_path(path)
            except FrontmatterError:
                passed = False
            if passed != should_pass:
                raise FrontmatterError(f"self-test failed for {relative}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            run_self_test()
        for path in args.paths:
            validate_path(path)
    except (OSError, FrontmatterError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
