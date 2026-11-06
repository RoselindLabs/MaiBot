"""Admission and public-source policy, not a fixed total test-count threshold."""

from pathlib import Path

import hashlib
import ast
import io
import json
import re
import subprocess
import tokenize
import tomllib
import pytest

import neko_pytest_guard as gate


ROOT = Path(__file__).resolve().parents[2]


def test_all_registered_source_bytes_match():
    manifest = json.loads((ROOT / "neko-patches.json").read_text(encoding="utf-8"))
    assert len(manifest["sources"]) == 32
    for name, digest in manifest["sources"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def _python_maintenance_markers(source):
    """Read comments and actual docstrings, never runtime strings or prompts."""
    texts = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                texts.append(doc)
    return [line.split("NEKO:", 1)[1].strip() for text in texts for line in text.splitlines() if "NEKO:" in line]


def _validate_maintenance_markers(source, ledger, *, required=False):
    markers = _python_maintenance_markers(source)
    assert markers or not required, "Missing maintenance marker"
    for marker in markers:
        match = re.fullmatch(r"(N\d{2}) ([a-z0-9]+(?:-[a-z0-9]+)*) — (\S.*)", marker)
        assert match, f"Malformed maintenance marker: {marker}"
        patch_id, slug, _reason = match.groups()
        assert ledger.get(patch_id) == slug, f"Unknown ID or mismatched slug: {marker}"
    return markers


def test_maintained_python_markers_match_upgrade_ledger():
    # The existing human upgrade ledger owns IDs/slugs; do not maintain a second registry.
    rows = re.findall(r"^\| (N\d{2}) ([a-z0-9-]+) \|", (ROOT / "NEKO_FORK.md").read_text(encoding="utf-8"), re.M)
    ledger = dict(rows)
    assert rows and len(ledger) == len(rows), "Missing or duplicate ledger IDs"
    manifest = json.loads((ROOT / "neko-patches.json").read_text(encoding="utf-8"))
    maintained = {name for name in manifest["sources"] if name.endswith(".py")}
    # uv.lock has hashes, not Python comments. Dashboard has its separate artifact contract.
    # Scan all product Python so a marker in an unregistered/new file cannot hide an unknown ID.
    for path in (ROOT / "src").rglob("*.py"):
        name = path.relative_to(ROOT).as_posix()
        try:
            _validate_maintenance_markers(path.read_text(encoding="utf-8-sig"), ledger, required=name in maintained)
        except AssertionError as exc:
            raise AssertionError(f"{name}: {exc}") from exc


def test_marker_parser_ignores_runtime_strings_and_accepts_multiple_ids():
    source = '''"""NEKO: N01 identity-echo — module contract"""
value = "NEKO: N99 invented — runtime text is not a marker"
# NEKO: N15 bounded-steering — preserve cancellation
def helper():
    """NEKO: N15 bounded-steering — function contract"""
    return "NEKO: N99 invented — also ordinary text"
'''
    assert (
        len(
            _validate_maintenance_markers(
                source,
                {"N01": "identity-echo", "N15": "bounded-steering"},
                required=True,
            )
        )
        == 3
    )


@pytest.mark.parametrize(
    "source,reason",
    [
        ('value = "NEKO: N01 identity-echo — string only"', "Missing"),
        ("# NEKO: N99 invented — unknown", "Unknown"),
        ("# NEKO: N01 wrong-slug — mismatch", "Unknown"),
        ("# NEKO: N01 — missing slug", "Malformed"),
        ("# NEKO: N01 identity-echo — ", "Malformed"),
        ('def helper():\n    """NEKO: N99 invented — unknown docstring"""', "Unknown"),
    ],
)
def test_marker_policy_rejects_missing_unknown_or_malformed_markers(source, reason):
    with pytest.raises(AssertionError, match=reason):
        _validate_maintenance_markers(source, {"N01": "identity-echo"}, required=True)


def test_targeted_runtime_upgrade_keeps_reviewed_locked_versions():
    manifest = json.loads((ROOT / "neko-patches.json").read_text(encoding="utf-8"))
    # v1.3.17 stopped publishing a lock; keep the last official stable lock as
    # the independently reviewable baseline, not a fresh unconstrained resolve.
    baseline = manifest["lock_baseline_commit"]
    assert baseline == "fe88ec6b6053e40eaf430cb5ba7ed7e805add2cb"
    subprocess.run(["git", "merge-base", "--is-ancestor", baseline, manifest["upstream_commit"]], cwd=ROOT, check=True)
    assert not subprocess.check_output(["git", "ls-tree", manifest["upstream_commit"], "uv.lock"], cwd=ROOT).strip()
    official = tomllib.loads(
        subprocess.check_output(
            ["git", "show", baseline + ":uv.lock"],
            cwd=ROOT,
        ).decode("utf-8")
    )
    current = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    before = {package["name"]: package["version"] for package in official["package"]}
    after = {package["name"]: package["version"] for package in current["package"]}
    reviewed = {"maibot": "1.3.19", "maibot-dashboard": "1.9.13", "maibot-plugin-sdk": "2.20.0"}
    assert after == {**before, **reviewed}
    official_packages = {package["name"]: package for package in official["package"]}
    for package in current["package"]:
        if package["name"] not in reviewed:
            assert package == official_packages[package["name"]]
    assert manifest["runtime_packages"] == reviewed
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    upstream_project = tomllib.loads(
        subprocess.check_output(["git", "show", manifest["upstream_commit"] + ":pyproject.toml"], cwd=ROOT).decode(
            "utf-8"
        )
    )
    assert project["project"]["dependencies"] == upstream_project["project"]["dependencies"]
    assert "maibot-plugin-sdk>=2.20.0" in project["project"]["dependencies"]
    assert "maibot-plugin-sdk>=2.20.0" in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()


def test_only_the_readonly_fork_workflow_is_active():
    files = list((ROOT / ".github/workflows").iterdir())
    assert {path.name for path in files} == {"neko-ci.yml"}
    text = files[0].read_text(encoding="utf-8")
    assert "contents: read" in text and "pull_request_target" not in text
    assert "id-token:" not in text and "secrets." not in text and "write" not in text
    assert "github.repository == 'RoselindLabs/MaiBot'" in text
    assert len(list((ROOT / ".github/upstream-workflows").glob("*.yml"))) == 16


def test_gate_rejects_empty_missing_failed_or_skipped_groups():
    complete = {name + "::test_synthetic" for name in gate.REQUIRED}
    assert gate.contracts_complete(complete, complete, [])
    assert not gate.contracts_complete(set(), set(), [])
    assert not gate.contracts_complete(complete, set(), [])
    assert not gate.contracts_complete(complete, complete, [next(iter(complete))])
    assert not gate.contracts_complete(complete, complete - {next(iter(complete))}, [])


@pytest.mark.parametrize("address", [("127.0.0.1", 7890), ("127.0.0.1", 3001), ("example.invalid", 443)])
def test_network_gate_blocks_model_proxy_adapter_and_external_connections(address):
    with pytest.raises(RuntimeError, match="External network disabled"):
        gate._network_guard("socket.connect", (None, address))


def test_upstream_history_license_and_application_diff_stay_narrow():
    assert subprocess.check_output(["git", "config", "--get", "core.autocrlf"], cwd=ROOT).strip() == b"false"
    manifest = json.loads((ROOT / "neko-patches.json").read_text(encoding="utf-8"))
    base = manifest["upstream_commit"]
    subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=ROOT, check=True)
    official_license = subprocess.check_output(["git", "show", base + ":LICENSE"], cwd=ROOT)
    assert (ROOT / "LICENSE").read_bytes() == official_license
    names = subprocess.check_output(["git", "diff", "--name-only", base, "--", "src"], cwd=ROOT).decode().splitlines()
    assert set(names) == set(manifest["sources"]) - {"uv.lock"}
