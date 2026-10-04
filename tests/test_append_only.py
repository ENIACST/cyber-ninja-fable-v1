import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "check_append_only.py"
DOCTRINE = "docs/CYBER_NINJA_v5.2_HYBRID.md"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}


def sh(repo, *args):
    return subprocess.run(args, cwd=repo, env=ENV, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "docs/proposals").mkdir(parents=True)
    (tmp_path / DOCTRINE).write_text("§1 rule\n§2 rule\n")
    (tmp_path / "docs/proposals/CN-CP-001.md").write_text("PROPOSED\n")
    sh(tmp_path, "git", "init", "-q")
    sh(tmp_path, "git", "add", "-A")
    sh(tmp_path, "git", "commit", "-qm", "base")
    return tmp_path


def check(repo, base="HEAD"):
    return sh(repo, sys.executable, str(SCRIPT), base)


def test_unchanged_and_appended_pass(repo):
    assert check(repo).returncode == 0
    with open(repo / DOCTRINE, "a") as f:
        f.write("GOVERNANCE EVENT\n")
    (repo / "docs/proposals/CN-CP-002.md").write_text("new proposal\n")
    assert check(repo).returncode == 0


def test_edit_inside_doctrine_fails(repo):
    (repo / DOCTRINE).write_text("§1 rule (improved)\n§2 rule\nappended\n")
    r = check(repo)
    assert r.returncode == 1 and "existing text changed" in r.stdout


def test_deleted_doctrine_fails(repo):
    (repo / DOCTRINE).unlink()
    assert "deleted" in check(repo).stdout


def test_edited_or_deleted_proposal_fails(repo):
    (repo / "docs/proposals/CN-CP-001.md").write_text("APPROVED\n")
    assert "changed after being committed" in check(repo).stdout
    (repo / "docs/proposals/CN-CP-001.md").unlink()
    assert "deleted" in check(repo).stdout


def test_unknown_base_fails(repo):
    r = check(repo, "0123456789abcdef0123456789abcdef01234567")
    assert r.returncode == 1 and "not found" in r.stdout
