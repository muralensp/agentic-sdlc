"""The method registry: which skill each lifecycle stage uses, pinned by content hash.

Skills are third-party instructions, so `adlc doctor` verifies that each one is the exact version
the person approved (FR-33) and reports which stages would use the fallback prompt (FR-34).
"""

import hashlib
import os
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

PLUGIN = "mattpocock-skills"

# stage -> (skill, required). PRD 7.3. Required stages cannot run without a skill or a fallback.
DEFAULT_STAGES: dict[str, tuple[str, bool]] = {
    "frame": ("grilling", True),
    "name": ("domain-modeling", False),
    "learn": ("research", False),
    "try": ("prototype", False),
    "design": ("codebase-design", False),
    "implement": ("tdd", True),
    "diagnose": ("diagnosing-bugs", False),
    "review": ("code-review", True),
    "deliver": ("pr", False),
    "human_step": ("wizard", False),
    "author_context": ("writing-for-agents", False),
    "resume": ("handoff", False),
}

_SKILL_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MethodsError(Exception):
    """The methods configuration is unusable."""


@dataclass(frozen=True)
class Method:
    stage: str
    skill: str
    sha256: str | None
    source: str  # "plugin" or "project"
    required: bool


class Registry(dict[str, Method]):
    """Stage -> Method, plus where the plugin's skills are expected to live."""

    plugin: str = PLUGIN
    version: str | None = None
    skills_root: str | None = None


@dataclass(frozen=True)
class Result:
    stage: str
    skill: str
    status: str  # available | missing | hash mismatch | unpinned | ambiguous
    required: bool
    fallback: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        if self.status == "available":
            return True
        return self.status == "missing" and (self.fallback or not self.required)


def claude_home() -> Path:
    return Path(os.environ.get("ADLC_CLAUDE_HOME") or Path.home() / ".claude")


def dir_hash(folder: Path) -> str:
    """SHA-256 over every file's relative path and content; line endings do not matter."""
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        data = path.read_bytes()
        try:
            data = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        except UnicodeDecodeError:
            pass
        name = path.relative_to(folder).as_posix()
        digest.update(f"{name}\0{hashlib.sha256(data).hexdigest()}\n".encode())
    return digest.hexdigest()


def load_registry(config: Path | None) -> Registry:
    """Defaults from PRD 7.3, overridden by the `methods:` block of config.yaml if present."""
    block: dict = {}
    if config is not None and Path(config).is_file():
        try:
            data = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise MethodsError(f"cannot read config {config}: {exc}") from exc
        block = data.get("methods") or {}
    return build_registry(block)


def build_registry(block: object) -> Registry:
    """Build the registry from an already-parsed `methods:` block (defaults fill the gaps)."""
    if not isinstance(block, dict):
        raise MethodsError("config: `methods` must be a mapping")
    registry = Registry()
    registry.plugin = str(block.get("plugin") or PLUGIN)
    registry.version = str(block["version"]) if block.get("version") else None
    registry.skills_root = block.get("skills_root")
    stages = block.get("stages") or {}
    if not isinstance(stages, dict):
        raise MethodsError("config: `methods.stages` must be a mapping")
    for stage, (skill, required) in DEFAULT_STAGES.items():
        registry[stage] = Method(stage, skill, None, "plugin", required)
    for stage, entry in stages.items():
        registry[stage] = _method(stage, entry, registry.get(stage))
    return registry


def _method(stage: str, entry: object, default: Method | None) -> Method:
    if not isinstance(entry, dict):
        raise MethodsError(f"config: method {stage!r} must be a mapping")
    skill = entry.get("skill", default.skill if default else None)
    if not isinstance(skill, str) or not _SKILL_NAME.match(skill):
        raise MethodsError(f"config: method {stage!r} needs a plain skill name")
    sha = entry.get("sha256", default.sha256 if default else None)
    if sha is not None and not (isinstance(sha, str) and _SHA256.match(sha)):
        raise MethodsError(f"config: method {stage!r} sha256 must be 64 lowercase hex digits")
    source = entry.get("source", "plugin")
    if source not in ("plugin", "project"):
        raise MethodsError(f"config: method {stage!r} source must be plugin or project")
    required = entry.get("required", default.required if default else False)
    if not isinstance(required, bool):
        raise MethodsError(f"config: method {stage!r} required must be true or false")
    return Method(stage, skill, sha, source, required)


def _plugin_roots(registry: Registry, skills_root: Path | None) -> tuple[list[Path], str]:
    """Where plugin skills may be, plus a note explaining an empty result."""
    explicit = skills_root or registry.skills_root
    if explicit:
        return [Path(explicit).expanduser()], ""
    cache = claude_home() / "plugins" / "cache"
    installed = {p.parent.name: p for p in sorted(cache.glob(f"*/{registry.plugin}/*/skills"))}
    if registry.version is None:
        return list(installed.values()), f"{registry.plugin} is not installed"
    if registry.version in installed:
        return [installed[registry.version]], ""
    found = ", ".join(sorted(installed)) or "none"
    return [], f"pinned {registry.plugin} {registry.version} not installed (installed: {found})"


def _find(method: Method, repo: Path, roots: list[Path]) -> list[Path]:
    if method.source == "project":
        candidate = repo / ".claude" / "skills" / method.skill
        return [candidate] if (candidate / "SKILL.md").is_file() else []
    return [
        folder
        for root in roots
        for folder in sorted(root.rglob(method.skill))
        if (folder / "SKILL.md").is_file()
    ]


def fallback_text(repo: Path, skill: str) -> tuple[str, str] | None:
    """The fallback prompt for `skill` and where it came from ("repo" or "adlc"), or None.

    A repo's own `.adlc/methods/<skill>.md` wins over the one packaged with ADLC.
    """
    own = repo / ".adlc" / "methods" / f"{skill}.md"
    if own.is_file():
        return own.read_bytes().decode("utf-8"), "repo"
    packaged = resources.files("adlc") / "fallbacks" / f"{skill}.md"
    if _SKILL_NAME.match(skill) and packaged.is_file():
        return packaged.read_text(encoding="utf-8"), "adlc"
    return None


def _check_method(
    method: Method, repo: Path, roots: list[Path], note: str
) -> tuple[Result, Path | None]:
    fallback = fallback_text(repo, method.skill) is not None
    found = _find(method, repo, roots)
    if not found:
        detail = note if method.source == "plugin" else "no project skill of that name"
        return Result(
            method.stage, method.skill, "missing", method.required, fallback, detail
        ), None
    if len(found) > 1:
        where = ", ".join(str(p) for p in found)
        return Result(
            method.stage, method.skill, "ambiguous", method.required, fallback, where
        ), None
    actual = dir_hash(found[0])
    if method.sha256 is None:
        status, detail = "unpinned", f"sha256 is {actual}"
    elif method.sha256 != actual:
        status, detail = "hash mismatch", f"pinned {method.sha256[:12]}, found {actual[:12]}"
    else:
        status, detail = "available", ""
    return Result(method.stage, method.skill, status, method.required, fallback, detail), found[0]


def check(registry: Registry, repo: Path, skills_root: Path | None = None) -> list[Result]:
    roots, note = _plugin_roots(registry, skills_root)
    return [_check_method(m, repo, roots, note)[0] for m in registry.values()]


def inspect(
    registry: Registry, repo: Path, stage: str, skills_root: Path | None = None
) -> tuple[Result, Path | None]:
    """The check result for one stage, and the skill folder when exactly one was found."""
    roots, note = _plugin_roots(registry, skills_root)
    return _check_method(registry[stage], repo, roots, note)


# --- what an agent always loads, and what a skill says (FR-33, FR-37) -------------------------

INDEX_FILES = ("CLAUDE.md", "AGENTS.md")
_SCRIPT_SUFFIXES = {".sh", ".bash", ".ps1", ".bat", ".cmd", ".py", ".js", ".rb", ".pl", ".exe"}
_OVERRIDE = re.compile(
    r"(?i)\b(ignore|disregard|forget)\s+(all|any|your|the|previous|prior|above)\b"
    r"[^.\n]{0,30}\b(instructions?|rules?|guidelines?)\b"
)
_PIPE_TO_SHELL = re.compile(r"(?i)\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b")
_TOOL_GRANT = re.compile(r"(?im)^\s*allowed[-_]tools\s*:")


def _line_count(path: Path) -> int:
    return len(path.read_bytes().decode("utf-8", errors="replace").splitlines())


def index_warnings(repo: Path, max_lines: int) -> list[str]:
    """Always-loaded files over `max_lines`: CLAUDE.md, AGENTS.md and project skills (FR-37)."""
    repo = Path(repo)
    files = [repo / name for name in INDEX_FILES]
    files += sorted((repo / ".claude" / "skills").glob("*/SKILL.md"))
    found = []
    for path in files:
        if path.is_file() and (count := _line_count(path)) > max_lines:
            found.append(
                f"{path.relative_to(repo).as_posix()} has {count} lines, over the limit of "
                f"{max_lines}; keep always-loaded files short (writing-for-agents)"
            )
    return found


def skill_warnings(folder: Path) -> list[str]:
    """Things in a skill a person should read before pinning it. Advice, not a verdict."""
    folder = Path(folder)
    found = []
    skill = folder / "SKILL.md"
    text = skill.read_bytes().decode("utf-8", errors="replace") if skill.is_file() else ""
    if _OVERRIDE.search(text):
        found.append("SKILL.md tells the agent to ignore or disregard its rules")
    if _PIPE_TO_SHELL.search(text):
        found.append("SKILL.md pipes a download into a shell")
    if _TOOL_GRANT.search(text):
        found.append("SKILL.md asks for tool permissions (allowed-tools); ADLC ignores them")
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in _SCRIPT_SUFFIXES:
            found.append(f"ships a script: {path.relative_to(folder).as_posix()}")
    return found


def supply_chain_warnings(
    registry: Registry, repo: Path, skills_root: Path | None = None
) -> list[str]:
    """`skill_warnings` for every skill the registry finds, labelled with its stage."""
    found = []
    for stage in registry:
        _, folder = inspect(registry, repo, stage, skills_root)
        if folder is not None:
            found += [
                f"skill {registry[stage].skill} ({stage}): {w}" for w in skill_warnings(folder)
            ]
    return found


def pins_yaml(registry: Registry, repo: Path, skills_root: Path | None = None) -> str:
    """A `methods:` block with the current hashes, for a person to review and commit."""
    roots, _ = _plugin_roots(registry, skills_root)
    stages: dict[str, dict] = {}
    for method in registry.values():
        found = _find(method, repo, roots)
        if len(found) == 1:
            entry: dict = {"skill": method.skill, "sha256": dir_hash(found[0])}
            if method.source == "project":
                entry["source"] = "project"
            stages[method.stage] = entry
    block: dict = {"plugin": registry.plugin}
    if registry.version:
        block["version"] = registry.version
    block["stages"] = stages
    return yaml.safe_dump({"methods": block}, sort_keys=False)
