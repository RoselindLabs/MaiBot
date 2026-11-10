"""Prepare an explicitly old config COPY; never start a host or modify the input."""

from copy import deepcopy
from pathlib import Path

import argparse
import re
import tomllib
import tomlkit


def prepare_focus_groups(data):
    """Translate old explicit global sharing, leaving new/disabled/scoped settings intact."""
    result = deepcopy(data)
    version = result.get("inner", {}).get("version")
    if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("An explicit valid source config version is required")
    if tuple(map(int, version.split("."))) > (8, 15, 68):
        return result
    experimental = result.get("experimental")
    if not isinstance(experimental, dict):
        raise ValueError("Old experimental settings must be explicit")
    enabled = experimental.get("focus_mode")
    if type(enabled) is not bool:
        raise ValueError("Old focus_mode must be an explicit boolean")
    if not enabled:
        return result
    groups = experimental.get("focus_groups")
    if not isinstance(groups, list):
        raise ValueError("Old enabled Focus requires an explicit focus_groups list")
    if groups:
        return result
    private = experimental.get("focus_on_private")
    if type(private) is not bool:
        raise ValueError("Old global Focus requires an explicit private participation choice")
    targets = [{"platform": "*", "item_id": "*", "rule_type": "group"}]
    if private:
        targets.append({"platform": "*", "item_id": "*", "rule_type": "private"})
    experimental["focus_groups"] = [{"targets": targets}]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise ValueError("Input and output must be different copies")
    prepared = prepare_focus_groups(tomllib.loads(args.input.read_text(encoding="utf-8")))
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        args.output.chmod(0o600)
        output.write(tomlkit.dumps(prepared))
    print("Prepared Focus copy; input unchanged. Host migration and other config review remain required.")


if __name__ == "__main__":
    main()
