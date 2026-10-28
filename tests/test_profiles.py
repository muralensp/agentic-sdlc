"""The stack profiles shipped with ADLC: dotnet and python (ADLC-008)."""

import os
import re
from pathlib import Path

import pytest
import yaml

from adlc.config import REQUIRED_DOD, load_config, missing_dod

BOSS = os.environ.get("ADLC_BOSS_ROOT")

DOTNET_DOD = {
    "build": "dotnet build",
    "lint": "bash build/scripts/lint.sh",
    "test": "bash build/scripts/test.sh",
    "integration": "bash build/scripts/integration-test.sh",
    "dependency_scan": "bash build/scripts/dependency-scan.sh",
    "secret_scan": "bash build/scripts/secret-scan.sh",
    "smoke": "bash build/scripts/smoke-test.sh",
    "e2e_smoke": "bash build/scripts/e2e-smoke-test.sh",
    "contract": "bash build/scripts/contract-test.sh",
}
PYTHON_DOD = {
    "install": "python -m pip install -e .[dev]",
    "lint": "ruff check .",
    "format": "ruff format --check .",
    "test": "pytest --cov --cov-fail-under=80",
    "dependency_scan": "pip-audit",
    "secret_scan": "gitleaks detect",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".adlc").mkdir()
    return tmp_path


def use(repo: Path, profile: str, extra: dict | None = None):
    (repo / ".adlc" / "config.yaml").write_text(
        yaml.safe_dump({"extends": profile, **(extra or {})})
    )
    return load_config(repo)


# --- dotnet -----------------------------------------------------------------------------------


def test_dotnet_dod_is_the_boss_command_set_in_run_order(repo):
    dod = use(repo, "dotnet").dod
    assert dict(dod) == DOTNET_DOD
    assert list(dod) == list(DOTNET_DOD)


def test_dotnet_protects_the_boss_areas_and_keeps_the_generic_ones(repo):
    protected = use(repo, "dotnet").protected
    for glob in (".github/**", ".adlc/**", "**/migrations/**"):  # from generic
        assert glob in protected
    for glob in (
        "build/azure-pipelines.yml",
        "**/Dockerfile*",
        "docker-compose*.yml",
        "**/appsettings*.json",
        "**/.gitattributes",
        "*.sln",
        "**/*.csproj",
        "src/Gateway/**",
        "src/Api/Program.cs",
        "**/TestApiFactory.cs",
        "CLAUDE.md",
        "adr/**",
        "docs/quality-gates/**",
        "docs/BRANCH-PROTECTION.md",
    ):
        assert glob in protected, glob
    assert len(protected) == len(set(protected))


def test_dotnet_requires_both_boss_checks_and_squash(repo):
    merge = use(repo, "dotnet").merge
    assert merge.required_checks == ("CI passed", "Analyze (csharp)")
    assert merge.method == "squash" and merge.auto_merge is False


def test_dotnet_keeps_the_generic_branch_rules(repo):
    git = use(repo, "dotnet").git
    assert git.base_branch == "main" and "feature" in git.branch_prefixes


# --- python -----------------------------------------------------------------------------------


def test_python_dod_follows_prd_13_2(repo):
    dod = use(repo, "python").dod
    assert dict(dod) == PYTHON_DOD
    assert "mypy" not in " ".join(dod.values())  # only when a repo configures it


def test_python_adds_no_protected_areas_and_requires_ci_passed(repo):
    settings = use(repo, "python")
    generic = tuple(yaml.safe_load(Path(_profile("generic")).read_text())["protected"])
    assert settings.protected == generic
    assert settings.merge.required_checks == ("CI passed",)
    assert settings.merge.auto_merge is False


# --- both -------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["dotnet", "python"])
def test_each_profile_has_the_commands_the_orchestrator_needs(repo, profile):
    assert missing_dod(use(repo, profile)) == ()


@pytest.mark.parametrize("profile", ["dotnet", "python"])
def test_a_profile_cannot_turn_on_auto_merge(profile):
    data = yaml.safe_load(Path(_profile(profile)).read_text())
    assert data.get("merge", {}).get("auto_merge") is not True


@pytest.mark.parametrize("profile", ["dotnet", "python"])
def test_a_repo_can_override_or_drop_one_command(repo, profile):
    settings = use(repo, profile, {"dod": {"lint": "make lint", "secret_scan": None}})
    assert settings.dod["lint"] == "make lint" and "secret_scan" not in settings.dod
    assert "test" in settings.dod


@pytest.mark.parametrize("profile", ["dotnet", "python"])
def test_each_profile_extends_generic_and_has_only_known_keys(profile):
    data = yaml.safe_load(Path(_profile(profile)).read_text())
    assert data["extends"] == "generic"


def _profile(name: str) -> str:
    from importlib import resources

    return str(resources.files("adlc") / "profiles" / f"{name}.yaml")


# --- missing_dod ------------------------------------------------------------------------------


def test_required_commands_are_lint_and_test():
    assert REQUIRED_DOD == ("lint", "test")


def test_an_incomplete_profile_is_reported(repo):
    (repo / "profiles").mkdir()
    (repo / "profiles" / "half.yaml").write_text(yaml.safe_dump({"dod": {"lint": "make lint"}}))
    assert missing_dod(use(repo, "profiles/half.yaml")) == ("test",)


def test_no_profile_at_all_reports_everything_missing(repo):
    assert missing_dod(load_config(repo)) == ("lint", "test")


# --- drift against BOSS NextGen (local only, read-only) -----------------------------------------


@pytest.mark.skipif(not BOSS, reason="set ADLC_BOSS_ROOT to BOSS NextGen's repository folder")
def test_every_script_the_dotnet_profile_names_exists_in_boss(repo):
    scripts = [
        match
        for command in use(repo, "dotnet").dod.values()
        for match in re.findall(r"build/scripts/[\w.-]+\.sh", command)
    ]
    assert len(scripts) == 8
    for script in scripts:
        assert (Path(BOSS) / script).is_file(), script
