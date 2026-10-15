"""Hermetic tests for scripts/open-drift-issue.sh.

The script shells out to `gh`; tests substitute a fake `gh` on PATH that
records its argv and returns canned output (no network, no repo access).
"""

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "open-drift-issue.sh"

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    log = os.environ["FAKE_GH_LOG"]
    with open(log, "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    if sys.argv[1:3] == ["issue", "list"]:
        # emulate `--jq 'length'`: print just the count
        print(os.environ.get("FAKE_OPEN_COUNT", "0"))
    elif sys.argv[1:3] == ["label", "create"]:
        if os.environ.get("FAKE_LABEL_FAIL") == "1":
            print("label already exists", file=sys.stderr)
            sys.exit(1)
    elif sys.argv[1:3] == ["issue", "create"]:
        print("https://github.com/x/y/issues/1")
    else:
        sys.exit(99)
    """)


@pytest.fixture()
def gh_env(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gh-calls.jsonl"
    env = {
        "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
        "FAKE_GH_LOG": str(log),
        "FAKE_OPEN_COUNT": "0",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return tmp_path, log


def run_script(tmp_path, extra_env=None):
    out = tmp_path / "drift.txt"
    out.write_text("Membership: DRIFT\n- PSKY\n+ SKYD\n")
    env = dict(os.environ)
    env.update(extra_env or {})
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(out)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc


def read_calls(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_skips_when_issue_already_open(gh_env, monkeypatch):
    tmp_path, log = gh_env
    monkeypatch.setenv("FAKE_OPEN_COUNT", "1")
    proc = run_script(tmp_path)
    assert proc.returncode == 0
    calls = read_calls(log)
    assert [c for c in calls if c[:2] == ["issue", "list"]]
    assert not [c for c in calls if c[:2] == ["issue", "create"]], "must not duplicate"


def test_creates_issue_when_none_open(gh_env):
    tmp_path, log = gh_env
    proc = run_script(tmp_path)
    assert proc.returncode == 0
    calls = read_calls(log)
    assert [c for c in calls if c[:2] == ["label", "create"]]
    creates = [c for c in calls if c[:2] == ["issue", "create"]]
    assert len(creates) == 1
    args = creates[0]
    title = args[args.index("--title") + 1]
    body = args[args.index("--body") + 1]
    label = args[args.index("--label") + 1]
    assert title == "S&P 500 universe drift detected"
    assert label == "universe-drift"
    assert "Membership: DRIFT" in body
    assert "- PSKY" in body


def test_still_creates_when_label_already_exists(gh_env, monkeypatch):
    tmp_path, log = gh_env
    monkeypatch.setenv("FAKE_LABEL_FAIL", "1")
    proc = run_script(tmp_path)
    assert proc.returncode == 0
    calls = read_calls(log)
    assert [c for c in calls if c[:2] == ["issue", "create"]]


def test_long_output_truncated_to_4k(gh_env, tmp_path, monkeypatch):
    bindir, log = gh_env
    out = tmp_path / "big.txt"
    out.write_text("x" * 10000)
    env = dict(os.environ)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(out)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0
    body = [c for c in read_calls(log) if c[:2] == ["issue", "create"]][0]
    body_text = body[body.index("--body") + 1]
    excerpt = body_text.split("```")[1]
    assert len(excerpt.strip()) <= 4000
