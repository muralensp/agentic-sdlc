"""Load `.adlc/config.yaml`, resolve the profile it extends, and validate the result.

Layers, lowest first: the built-in `generic` profile, the profile chain named by `extends:`,
then the repo's own config. Mappings merge key by key, lists replace, and `null` in `dod`
removes a command.
Everything is checked at once and reported with its key path (PRD FR-39, FR-20, FR-22).
"""

import copy
import difflib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType

import yaml

from adlc.methods import MethodsError, Registry, build_registry

MAX_PROFILE_DEPTH = 3
REQUIRED_DOD = ("lint", "test")  # commands the orchestrator cannot do without
_BUILTIN_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class ConfigError(Exception):
    """The configuration is invalid. `problems` holds one message per problem."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Board:
    path: str
    briefs: str
    code: str | None


@dataclass(frozen=True)
class Git:
    base_branch: str
    branch_prefixes: tuple[str, ...]
    commit_convention: str


@dataclass(frozen=True)
class Limits:
    max_iterations: int
    max_review_rounds: int
    max_diff_lines: int
    max_diff_lines_hard: int
    max_task_minutes: int
    max_cost_usd: float | None
    max_tasks_per_window: int


@dataclass(frozen=True)
class Models:
    coder: str | None
    tester: str | None
    reviewer: str | None
    pr_writer: str | None


@dataclass(frozen=True)
class Merge:
    auto_merge: bool
    required_checks: tuple[str, ...]
    method: str
    trust_after: int = 3  # hand-approved merges before `adlc trust` may work


@dataclass(frozen=True)
class Tests:
    paths: tuple[str, ...]  # globs of test files; empty until a repo or profile says (ADLC-015)
    red: str | None  # command for one test file, with {file}; None runs the `test` command
    failure_markers: tuple[str, ...]  # text a real test failure prints (not a build error)


@dataclass(frozen=True)
class Guardrails:
    coder_paths: tuple[str, ...]  # allow-list for the Coder; empty: anything not protected
    allowed_commands: tuple[str, ...]  # if set, the only shell commands an agent may run
    forbidden_commands: tuple[str, ...]  # never run by an agent


@dataclass(frozen=True)
class Scrub:
    pan: bool  # remove card numbers (digits that pass the Luhn check)
    patterns: tuple[str, ...]  # extra regular expressions for personal data


@dataclass(frozen=True)
class Settings:
    board: Board
    git: Git
    limits: Limits
    models: Models
    dod: Mapping[str, str]
    protected: tuple[str, ...]
    merge: Merge
    methods: Registry
    tests: Tests
    source: Path | None  # the repo config file, or None when there was none
    guardrails: Guardrails = Guardrails((), (), ())
    scrub: Scrub = Scrub(True, ())


# --- schema -------------------------------------------------------------------------------------
# A leaf is a tuple (kind, ...); a dict is a mapping of known keys.

SCHEMA: dict = {
    "extends": ("str",),
    "board": {"path": ("str",), "briefs": ("str",), "code": ("str?",)},
    "git": {
        "base_branch": ("str",),
        "branch_prefixes": ("strs",),
        "commit_convention": ("str",),
    },
    "limits": {
        "max_iterations": ("int", 1, 20),
        "max_review_rounds": ("int", 1, 5),
        "max_diff_lines": ("int", 1, 100_000),
        "max_diff_lines_hard": ("int", 1, 100_000),
        "max_task_minutes": ("int", 1, 240),
        "max_cost_usd": ("positive?",),
        "max_tasks_per_window": ("int", 1, 1000),
    },
    "models": {k: ("str?",) for k in ("coder", "tester", "reviewer", "pr_writer")},
    "dod": ("commands",),
    "protected": ("strs",),
    "protected_extra": ("strs",),
    "merge": {
        "auto_merge": ("bool",),
        "required_checks": ("strs",),
        "method": ("choice", "squash", "rebase", "merge"),
        "trust_after": ("int", 1, 100),
    },
    "guardrails": {
        "coder_paths": ("strs",),
        "allowed_commands": ("strs",),
        "forbidden_commands": ("strs",),
    },
    "scrub": {"pan": ("bool",), "patterns": ("strs",)},
    "methods": ("any",),
    "tests": {"paths": ("strs",), "red": ("str?",), "failure_markers": ("strs",)},
}


def _leaf(spec: tuple, value: object, path: str, errors: list[str]) -> object:
    kind = spec[0]
    text = isinstance(value, str) and value.strip() != ""
    if kind == "any":
        return value
    if kind == "str" and not text:
        errors.append(f"{path}: expected text, got {value!r}")
    elif kind == "str?" and value is not None and not text:
        errors.append(f"{path}: expected text or null, got {value!r}")
    elif kind == "bool" and not isinstance(value, bool):
        errors.append(f"{path}: expected true or false, got {value!r}")
    elif kind == "int":
        _, low, high = spec
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append(f"{path}: expected a whole number from {low} to {high}, got {value!r}")
    elif kind == "positive?":
        number = isinstance(value, int | float) and not isinstance(value, bool)
        if value is not None and not (number and value > 0):
            errors.append(f"{path}: expected a number above 0 or null, got {value!r}")
    elif kind == "choice" and value not in spec[1:]:
        errors.append(f"{path}: expected one of {', '.join(spec[1:])}, got {value!r}")
    elif kind == "strs":
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            errors.append(f"{path}: expected a list of text values, got {value!r}")
    elif kind == "commands":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected a mapping of name to command, got {value!r}")
        else:
            for name, command in value.items():
                if not (isinstance(command, str) and command.strip()):
                    errors.append(f"{path}.{name}: expected a command, got {command!r}")
    return value


def _check(spec: object, value: object, path: str, errors: list[str]) -> None:
    if not isinstance(spec, dict):
        _leaf(spec, value, path, errors)
        return
    if not isinstance(value, dict):
        errors.append(f"{path}: expected a mapping of settings, got {value!r}")
        return
    for key, item in value.items():
        where = f"{path}.{key}" if path else str(key)
        if key not in spec:
            near = difflib.get_close_matches(str(key), list(spec), n=1)
            hint = f" (did you mean '{near[0]}'?)" if near else ""
            errors.append(f"{where}: unknown key{hint}")
        else:
            _check(spec[key], item, where, errors)


# --- layers -------------------------------------------------------------------------------------


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _parse(text: str, label: str, errors: list[str]) -> dict | None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        errors.append(f"{label}: not valid YAML ({str(exc).splitlines()[0] if str(exc) else exc})")
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        errors.append(f"{label}: must be a mapping of settings")
        return None
    return data


def _builtin_names() -> list[str]:
    folder = resources.files("adlc") / "profiles"
    return sorted(
        p.name.removesuffix(".yaml") for p in folder.iterdir() if p.name.endswith(".yaml")
    )


def _read_profile(ref: object, repo: Path, errors: list[str]) -> tuple[dict, str] | None:
    if not isinstance(ref, str) or not ref.strip():
        errors.append(f"extends: expected a profile name or path, got {ref!r}")
        return None
    if _BUILTIN_NAME.match(ref):
        file = resources.files("adlc") / "profiles" / f"{ref}.yaml"
        if not file.is_file():
            errors.append(
                f"extends: no built-in profile {ref!r} (available: {', '.join(_builtin_names())})"
            )
            return None
        data = _parse(file.read_text(encoding="utf-8"), f"profile {ref}", errors)
        return (data, f"profile {ref}") if data is not None else None
    path = (repo / ref).resolve()
    if not path.is_relative_to(repo.resolve()):
        errors.append(f"extends: {ref!r} must be inside the repository")
        return None
    if not path.is_file():
        errors.append(f"extends: profile file {ref!r} not found (looked for {path})")
        return None
    data = _parse(path.read_text(encoding="utf-8"), f"profile {ref}", errors)
    return (data, f"profile {ref}") if data is not None else None


def _resolve(ref: str, repo: Path, trail: list[str], errors: list[str]) -> dict:
    """The merged settings of profile `ref` and everything it extends."""
    if ref in trail:
        errors.append(f"extends: profile loop {' -> '.join([*trail, ref])}")
        return {}
    if len(trail) >= MAX_PROFILE_DEPTH and ref != "generic":  # generic is the root, not a level
        errors.append(f"extends: profiles are nested too deep (more than {MAX_PROFILE_DEPTH})")
        return {}
    found = _read_profile(ref, repo, errors)
    if found is None:
        return {}
    data, label = found
    merge = data.get("merge")
    if isinstance(merge, dict) and merge.get("auto_merge") is True:
        errors.append(
            f"merge.auto_merge: {label} cannot enable auto-merge; set it in the repo config"
        )
    parent = data.get("extends")
    base = _resolve(parent, repo, [*trail, ref], errors) if parent else {}
    return _merge(base, {k: v for k, v in data.items() if k != "extends"})


def missing_dod(settings: Settings) -> tuple[str, ...]:
    """The required Definition of Done commands the settings do not define."""
    return tuple(name for name in REQUIRED_DOD if name not in settings.dod)


def load_config(repo: str | Path) -> Settings:
    """Read the repo config over its profiles; raise ConfigError listing every problem."""
    repo = Path(repo)
    errors: list[str] = []
    merged = _resolve("generic", repo, [], errors)

    source = repo / ".adlc" / "config.yaml"
    own: dict = {}
    if source.is_file():
        own = (
            _parse(source.read_text(encoding="utf-8"), str(source.relative_to(repo)), errors) or {}
        )
    else:
        source = None
    if own.get("extends") is not None:
        merged = _merge(merged, _resolve(own["extends"], repo, [], errors))
    merged = _merge(merged, {k: v for k, v in own.items() if k != "extends"})

    if isinstance(merged.get("dod"), dict):
        merged["dod"] = {k: v for k, v in merged["dod"].items() if v is not None}
    _check(SCHEMA, merged, "", errors)

    limits, models = merged.get("limits"), merged.get("models")
    limits = limits if isinstance(limits, dict) else {}
    models = models if isinstance(models, dict) else {}
    soft, hard = limits.get("max_diff_lines"), limits.get("max_diff_lines_hard")
    if isinstance(soft, int) and isinstance(hard, int) and soft > hard:
        errors.append(
            f"limits.max_diff_lines: {soft} is above the hard ceiling max_diff_lines_hard ({hard})"
        )
    coder, reviewer = models.get("coder"), models.get("reviewer")
    if isinstance(coder, str) and isinstance(reviewer, str) and coder.lower() == reviewer.lower():
        errors.append(
            "models.reviewer: must differ from models.coder (the Reviewer uses a different model)"
        )
    scrub_block = merged.get("scrub")
    for pattern in scrub_block.get("patterns", []) if isinstance(scrub_block, dict) else []:
        try:
            re.compile(pattern)
        except (re.error, TypeError) as exc:
            errors.append(f"scrub.patterns: {pattern!r} is not a valid regular expression ({exc})")
    registry = None
    try:
        registry = build_registry(merged.get("methods") or {})
    except MethodsError as exc:
        errors.append(f"methods: {exc}")
    if errors:
        raise ConfigError(errors)

    protected = list(dict.fromkeys([*merged["protected"], *merged["protected_extra"]]))
    return Settings(
        board=Board(**merged["board"]),
        git=Git(
            merged["git"]["base_branch"],
            tuple(merged["git"]["branch_prefixes"]),
            merged["git"]["commit_convention"],
        ),
        limits=Limits(**merged["limits"]),
        models=Models(**merged["models"]),
        dod=MappingProxyType(dict(merged["dod"])),
        protected=tuple(protected),
        merge=Merge(
            merged["merge"]["auto_merge"],
            tuple(merged["merge"]["required_checks"]),
            merged["merge"]["method"],
            merged["merge"]["trust_after"],
        ),
        methods=registry,
        tests=Tests(
            tuple(merged["tests"]["paths"]),
            merged["tests"]["red"],
            tuple(merged["tests"]["failure_markers"]),
        ),
        source=source,
        guardrails=Guardrails(
            tuple(merged["guardrails"]["coder_paths"]),
            tuple(merged["guardrails"]["allowed_commands"]),
            tuple(merged["guardrails"]["forbidden_commands"]),
        ),
        scrub=Scrub(merged["scrub"]["pan"], tuple(merged["scrub"]["patterns"])),
    )
