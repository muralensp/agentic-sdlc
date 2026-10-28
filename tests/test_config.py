"""The config and profile loader (ADLC-006)."""

import dataclasses
from pathlib import Path

import pytest
import yaml

from adlc.config import ConfigError, load_config
from adlc.methods import Registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".adlc").mkdir()
    return tmp_path


def write(repo: Path, data, name: str = ".adlc/config.yaml") -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return path


def problems(repo: Path) -> list[str]:
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    return caught.value.problems


# --- defaults ---------------------------------------------------------------------------------


def test_no_config_file_gives_the_prd_defaults(repo):
    settings = load_config(repo)
    assert settings.source is None
    limits = settings.limits
    assert (limits.max_iterations, limits.max_review_rounds) == (5, 2)
    assert (limits.max_diff_lines, limits.max_diff_lines_hard) == (400, 800)
    assert (limits.max_task_minutes, limits.max_tasks_per_window) == (30, 3)
    assert limits.max_cost_usd is None
    assert settings.merge.auto_merge is False
    assert settings.git.base_branch == "main"
    assert settings.board.path == "docs/WORK-ITEMS.md"
    assert settings.dod == {}


def test_default_protected_areas_follow_fr_41(repo):
    protected = load_config(repo).protected
    for expected in (".github/**", ".adlc/**", "**/migrations/**", "**/.env"):
        assert expected in protected


def test_an_empty_config_file_is_the_same_as_none(repo):
    write(repo, "")
    settings = load_config(repo)
    assert settings.limits.max_iterations == 5 and settings.source == repo / ".adlc/config.yaml"


def test_this_repos_own_config_loads():
    settings = load_config(ROOT)
    assert settings.merge.auto_merge is False
    assert isinstance(settings.methods, Registry) and settings.methods.version == "1.3.1"


# --- settings object --------------------------------------------------------------------------


def test_settings_are_frozen(repo):
    settings = load_config(repo)
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.limits.max_iterations = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.merge = None
    with pytest.raises(TypeError):
        settings.dod["x"] = "y"


# --- overrides and merging --------------------------------------------------------------------


def test_a_repo_can_override_one_limit_and_keep_the_rest(repo):
    write(repo, {"limits": {"max_iterations": 4}})
    limits = load_config(repo).limits
    assert limits.max_iterations == 4 and limits.max_review_rounds == 2


def profile(repo: Path, **body) -> None:
    write(repo, body, "profiles/stack.yaml")


def test_extends_a_local_profile_and_override_a_single_dod_command(repo):
    profile(repo, dod={"lint": "make lint", "test": "make test", "scan": "make scan"})
    write(repo, {"extends": "profiles/stack.yaml", "dod": {"test": "make test-fast"}})
    assert load_config(repo).dod == {
        "lint": "make lint",
        "test": "make test-fast",
        "scan": "make scan",
    }


def test_null_removes_a_dod_command(repo):
    profile(repo, dod={"lint": "make lint", "smoke": "make smoke"})
    write(repo, {"extends": "profiles/stack.yaml", "dod": {"smoke": None}})
    assert load_config(repo).dod == {"lint": "make lint"}


def test_protected_replaces_and_protected_extra_appends(repo):
    profile(repo, protected=["a/**", "b/**"])
    write(repo, {"extends": "profiles/stack.yaml", "protected_extra": ["c/**", "a/**"]})
    assert load_config(repo).protected == ("a/**", "b/**", "c/**")
    write(repo, {"extends": "profiles/stack.yaml", "protected": ["only/**"]})
    assert load_config(repo).protected == ("only/**",)


def test_the_builtin_generic_profile_can_be_named(repo):
    write(repo, {"extends": "generic"})
    assert load_config(repo).limits.max_iterations == 5


def test_profiles_can_extend_profiles_up_to_three_deep(repo):
    write(repo, {"extends": "profiles/a.yaml"})
    write(repo, {"extends": "profiles/b.yaml", "limits": {"max_iterations": 7}}, "profiles/a.yaml")
    write(
        repo, {"extends": "profiles/c.yaml", "limits": {"max_task_minutes": 10}}, "profiles/b.yaml"
    )
    write(repo, {"extends": "generic", "limits": {"max_review_rounds": 3}}, "profiles/c.yaml")
    limits = load_config(repo).limits
    assert (limits.max_iterations, limits.max_task_minutes, limits.max_review_rounds) == (7, 10, 3)


def test_a_fourth_level_is_refused(repo):
    write(repo, {"extends": "profiles/a.yaml"})
    for name, parent in (("a", "b"), ("b", "c"), ("c", "d")):
        write(repo, {"extends": f"profiles/{parent}.yaml"}, f"profiles/{name}.yaml")
    write(repo, {}, "profiles/d.yaml")
    assert any("too deep" in p for p in problems(repo))


def test_a_profile_loop_is_refused(repo):
    write(repo, {"extends": "profiles/a.yaml"})
    write(repo, {"extends": "profiles/b.yaml"}, "profiles/a.yaml")
    write(repo, {"extends": "profiles/a.yaml"}, "profiles/b.yaml")
    assert any("loop" in p for p in problems(repo))


def test_missing_profiles_say_where_they_looked(repo):
    write(repo, {"extends": "nonesuch"})
    (message,) = problems(repo)
    assert "nonesuch" in message and "generic" in message  # lists what is available
    write(repo, {"extends": "profiles/gone.yaml"})
    assert "profiles" in problems(repo)[0] and "gone.yaml" in problems(repo)[0]


def test_a_profile_path_cannot_leave_the_repo(repo, tmp_path):
    outside = tmp_path.parent / "outside.yaml"
    outside.write_text("limits: {max_iterations: 9}\n")
    write(repo, {"extends": "../outside.yaml"})
    assert any("inside the repository" in p for p in problems(repo))


# --- errors -----------------------------------------------------------------------------------


def test_unknown_keys_name_the_path_and_suggest_the_closest(repo):
    write(repo, {"limits": {"max_iteration": 5}})
    (message,) = problems(repo)
    assert message.startswith("limits.max_iteration: unknown key")
    assert "did you mean 'max_iterations'" in message


def test_unknown_top_level_key(repo):
    write(repo, {"limit": {}})
    assert problems(repo)[0].startswith("limit: unknown key")


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({"limits": {"max_iterations": "five"}}, "limits.max_iterations"),
        ({"limits": {"max_iterations": True}}, "limits.max_iterations"),
        ({"limits": {"max_iterations": 0}}, "limits.max_iterations"),
        ({"limits": {"max_iterations": 21}}, "limits.max_iterations"),
        ({"limits": {"max_review_rounds": 6}}, "limits.max_review_rounds"),
        ({"limits": {"max_task_minutes": 241}}, "limits.max_task_minutes"),
        ({"limits": {"max_tasks_per_window": 0}}, "limits.max_tasks_per_window"),
        ({"limits": {"max_cost_usd": 0}}, "limits.max_cost_usd"),
        ({"limits": {"max_cost_usd": "free"}}, "limits.max_cost_usd"),
        ({"limits": 5}, "limits"),
        ({"merge": {"auto_merge": "yes"}}, "merge.auto_merge"),
        ({"merge": {"method": "fast-forward"}}, "merge.method"),
        ({"dod": {"lint": 5}}, "dod.lint"),
        ({"dod": []}, "dod"),
        ({"protected": "a/**"}, "protected"),
        ({"models": {"coder": 5}}, "models.coder"),
        ({"git": {"base_branch": ""}}, "git.base_branch"),
        ({"board": {"path": None}}, "board.path"),
    ],
)
def test_bad_types_and_bounds_name_the_key_path(repo, data, path):
    write(repo, data)
    assert any(p.startswith(f"{path}:") for p in problems(repo)), problems(repo)


def test_every_problem_is_reported_at_once(repo):
    write(repo, {"limits": {"max_iterations": "x", "bogus": 1}, "merge": {"auto_merge": 3}})
    assert len(problems(repo)) == 3


def test_unreadable_yaml_names_the_file(repo):
    write(repo, "limits: [unclosed")
    assert ".adlc" in problems(repo)[0] and "YAML" in problems(repo)[0]


def test_config_must_be_a_mapping(repo):
    write(repo, "- just\n- a list\n")
    assert "mapping" in problems(repo)[0]


def test_the_error_text_lists_each_problem_on_its_own_line(repo):
    write(repo, {"limit": 1, "merge": {"auto_merge": 3}})
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    assert str(caught.value).count("\n") == 1


# --- cross-field rules ------------------------------------------------------------------------


def test_soft_diff_limit_cannot_exceed_the_hard_one(repo):
    write(repo, {"limits": {"max_diff_lines": 900}})
    assert any("max_diff_lines" in p and "hard" in p for p in problems(repo))


def test_reviewer_must_differ_from_coder(repo):
    write(repo, {"models": {"coder": "sonnet", "reviewer": "Sonnet"}})
    assert any("models.reviewer" in p and "differ" in p for p in problems(repo))


def test_different_models_or_only_one_set_is_fine(repo):
    write(repo, {"models": {"coder": "sonnet", "reviewer": "opus"}})
    assert load_config(repo).models.reviewer == "opus"
    write(repo, {"models": {"coder": "sonnet"}})
    assert load_config(repo).models.reviewer is None


# --- auto_merge and profiles ------------------------------------------------------------------


def test_a_profile_cannot_enable_auto_merge(repo):
    profile(repo, merge={"auto_merge": True})
    write(repo, {"extends": "profiles/stack.yaml"})
    (message,) = problems(repo)
    assert "auto_merge" in message and "profile" in message


def test_the_repo_config_can_enable_auto_merge(repo):
    write(repo, {"merge": {"auto_merge": True}})
    assert load_config(repo).merge.auto_merge is True


# --- methods block ----------------------------------------------------------------------------


def test_the_methods_block_is_validated_by_the_methods_module(repo):
    write(repo, {"methods": {"stages": {"implement": {"sha256": "nothex"}}}})
    assert problems(repo)[0].startswith("methods:")


def test_a_valid_methods_block_becomes_a_registry(repo):
    write(repo, {"methods": {"version": "9.9.9", "stages": {"implement": {"skill": "my-tdd"}}}})
    methods = load_config(repo).methods
    assert methods.version == "9.9.9" and methods["implement"].skill == "my-tdd"
