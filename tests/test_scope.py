"""Which changed paths the rules forbid (ADLC-009; ADLC-027 builds the full guard on this)."""

import pytest

from adlc.scope import out_of_scope

PROTECTED = [".github/**", "**/pyproject.toml", "*.sln", "docs/BRANCH-PROTECTION.md", "**/.env.*"]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".github/CODEOWNERS",
        "pyproject.toml",
        "a/b/pyproject.toml",
        "App.sln",
        "docs/BRANCH-PROTECTION.md",
        "svc/.env.local",
    ],
)
def test_protected_paths_are_flagged(path):
    assert out_of_scope([path], PROTECTED) == [path]


@pytest.mark.parametrize(
    "path", ["src/app.py", "docs/other.md", "src/App.sln", "github/x", ".githubx/y", "x/.env"]
)
def test_other_paths_are_fine(path):
    assert out_of_scope([path], PROTECTED) == []


def test_matching_ignores_case_slashes_and_a_leading_dot_slash():
    assert out_of_scope([".GitHub/workflows/ci.yml"], PROTECTED) == [".GitHub/workflows/ci.yml"]
    backslashed = ".github" + chr(92) + "workflows" + chr(92) + "ci.yml"
    assert out_of_scope([backslashed], PROTECTED) == [backslashed]
    assert out_of_scope(["./pyproject.toml"], PROTECTED) == ["./pyproject.toml"]


@pytest.mark.parametrize("path", ["../outside.py", "src/../../x.py", "/etc/passwd", "C:/x/y.py"])
def test_paths_leaving_the_repository_are_flagged(path):
    assert out_of_scope([path], []) == [path]


def test_an_allow_list_limits_edits_to_matching_paths():
    allowed = ["src/**", "tests/**"]
    got = out_of_scope(["src/a.py", "tests/t.py", "README.md", ".github/x.yml"], PROTECTED, allowed)
    assert got == ["README.md", ".github/x.yml"]


def test_protected_wins_over_allowed():
    assert out_of_scope(["src/pyproject.toml"], PROTECTED, ["src/**"]) == ["src/pyproject.toml"]


def test_no_allow_list_means_anywhere_except_protected():
    assert out_of_scope(["anything/at/all.txt"], []) == []


def test_order_is_kept_and_nothing_flagged_twice():
    paths = ["b/pyproject.toml", "ok.py", "a/pyproject.toml", "b/pyproject.toml"]
    assert out_of_scope(paths, PROTECTED) == ["b/pyproject.toml", "a/pyproject.toml"]


def test_glob_characters_other_than_star_are_literal():
    assert out_of_scope(["a+b.txt", "axb.txt"], ["a+b.txt"]) == ["a+b.txt"]
    assert out_of_scope(["f1.txt", "f12.txt"], ["f?.txt"]) == ["f1.txt"]
