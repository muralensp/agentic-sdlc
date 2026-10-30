"""`adlc new`: a minimal, ready-to-use repository for a stack (FR-29, FR-40).

`scaffold` returns every file as text, so the plan can be shown before anything is written;
`create_repo` writes them into an empty folder and makes the first commit on `main`. The remote and
branch protection need a person, so the repo carries the `protect-main` wizard: this module never
creates a remote and never runs it (FR-36).
"""

import re
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from adlc.commands import CommandError
from adlc.init import _IGNORE, missing_scripts
from adlc.review import GATES
from adlc.tasks import CODE_PATTERN

STACKS = ("python", "dotnet")
CHECK = "CI passed"  # the one job name the wizard requires and every CI workflow here provides
_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_EXECUTABLE = ".sh"
_SLN_GUIDS = ("{3F4C2A10-5B7D-4E8A-9C61-0A1B2C3D4E01}", "{3F4C2A10-5B7D-4E8A-9C61-0A1B2C3D4E02}")
_ADLC_IGNORE = _IGNORE.replace("!config.yaml\n", "!config.yaml\n!wizards/\n!wizards/**\n")


class ScaffoldError(CommandError):
    """The name, stack or code cannot be used."""


@dataclass(frozen=True)
class Created:
    path: Path
    files: tuple[str, ...]


def default_code(name: str) -> str:
    """Up to four capital letters or digits from the name, for the board's keys."""
    letters = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:4]
    return letters if CODE_PATTERN.match(letters) else "APP"


def _pascal(name: str) -> str:
    return "".join(word.capitalize() for word in name.split("-"))


def _fill(text: str, **values: str) -> str:
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", value)
    return text


# --- files shared by every stack ----------------------------------------------------------------

_GITATTRIBUTES = """\
# Keep line endings as LF everywhere so checks behave the same on Windows worktrees.
* text=auto eol=lf

*.sh text eol=lf
*.png binary
*.jpg binary
"""

_BOARD = """\
# Work Items Log

Task board for @@NAME@@, in the ADLC table format. One row per work item, added before its branch.
Each item has a brief in `docs/work-items/`. Add items with `adlc add`.

Context code: `@@CODE@@`.

| Key | Context | Title | Status | Linked PRD Chapter / Day | Notes |
|---|---|---|---|---|---|
"""

_CLAUDE = """\
# CLAUDE.md

Always-loaded context for **@@NAME@@**. Keep it short (maximum 150 lines).

## Working agreements

- Work one item at a time, from the board (`docs/WORK-ITEMS.md`). Briefs are in `docs/work-items/`.
- Tests first: show a new test fail before writing the code.
- Nothing is done until the checks below have run with real exit codes and the output was read.
- Never commit to `main`. Use a `<type>/<KEY>-<slug>` branch and a pull request.
- Conventional Commits: `type(scope): subject`.

## Commands

```bash
@@COMMANDS@@
```

## Where things are

- Terms: `GLOSSARY.md`. Decisions: `adr/`. Review checklists: `docs/quality-gates/`.
- ADLC settings: `.adlc/config.yaml`. Run `adlc status` to see the board.
"""

_AGENTS = """\
# AGENTS.md

The rules for working in **@@NAME@@** are in `CLAUDE.md`; read it first. They apply to every
agent, whichever tool runs it.
"""

_GLOSSARY = """\
# Glossary

The project's own terms, one entry each. Add an entry when a new concept appears; do not redefine
one silently.

| Term | Meaning |
|---|---|
| **Work item** | One row of the board, named by a key such as `@@CODE@@-001`. |
"""

_ADR_TEMPLATE = """\
# ADR-NNNN: Title

Status: Proposed | Accepted | Superseded by ADR-NNNN

## Context

What forces are at play, and what needs deciding.

## Decision

What was decided, in one or two sentences.

## Consequences

What becomes easier, what becomes harder, and what follows from it.
"""

_ADR_STACK = """\
# ADR-0001: Use @@STACK@@ for @@NAME@@

Status: Accepted

## Context

@@NAME@@ needed a language and toolchain before the first work item.

## Decision

Use @@STACK@@. The ADLC `@@STACK@@` profile supplies the Definition of Done commands, and
`.adlc/config.yaml` extends it.

## Consequences

Checks, protected areas and test locations follow that profile. Changing stack later means a new
ADR and a different profile.
"""

_PR_TEMPLATE = """\
## Summary

What changed and why.

## Impacted modules

Which parts of the code this touches.

## Tests

What ran, and with what result.

## Risks

What could go wrong, and how it would be noticed.
"""


def _gate_file(gate) -> tuple[str, str]:
    path = f"docs/quality-gates/gate-{gate.number}-{gate.name.lower()}.md"
    who = "Run by the ADLC Reviewer." if gate.run else "Not run by ADLC."
    items = "\n".join(f"- [ ] {item.strip()}" for item in gate.checks.split(";") if item.strip())
    return path, f"# Gate {gate.number}: {gate.name}\n\n{who}\n\n{items}\n"


def _config(stack: str, code: str, paths: set[str]) -> str:
    data: dict = {"extends": stack, "board": {"code": code}}
    gone = missing_scripts(stack, lambda script: script in paths)
    if gone:
        data["dod"] = gone
    if stack == "dotnet":
        data["merge"] = {"required_checks": [CHECK]}  # the profile also asks for a CodeQL job
    return "# Created by `adlc new`; edit freely.\n" + yaml.safe_dump(data, sort_keys=False)


# --- python -------------------------------------------------------------------------------------

_PY_PROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "@@NAME@@"
version = "0.1.0"
description = "@@NAME@@"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "ruff>=0.6",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
"""

_PY_INIT = '''\
"""@@NAME@@."""


def greet(name: str) -> str:
    """A greeting; replace it with the first real behaviour."""
    return f"Hello, {name}!"
'''

_PY_TEST = """\
from @@PKG@@ import greet


def test_greet_names_the_person():
    assert greet("Ada") == "Hello, Ada!"
"""

_PY_CI = """\
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  ci:
    name: CI passed
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install
        run: python -m pip install -e ".[dev]"
      - name: Lint
        run: ruff check .
      - name: Format check
        run: ruff format --check .
      - name: Tests
        run: pytest --cov --cov-fail-under=80
"""

_PY_IGNORE = """\
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
.coverage
dist/
build/
"""

_PY_COMMANDS = """\
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest --cov --cov-fail-under=80"""


def _python(name: str) -> tuple[dict[str, str], str]:
    pkg = name.replace("-", "_")
    files = {
        "pyproject.toml": _fill(_PY_PROJECT, NAME=name),
        f"src/{pkg}/__init__.py": _fill(_PY_INIT, NAME=name),
        "tests/test_smoke.py": _fill(_PY_TEST, PKG=pkg),
        ".github/workflows/ci.yml": _PY_CI,
        ".gitignore": _PY_IGNORE,
    }
    return files, _PY_COMMANDS


# --- dotnet -------------------------------------------------------------------------------------

_SLN = """\
Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio Version 17
VisualStudioVersion = 17.0.31903.59
MinimumVisualStudioVersion = 10.0.40219.1
@@PROJECTS@@Global
\tGlobalSection(SolutionConfigurationPlatforms) = preSolution
\t\tDebug|Any CPU = Debug|Any CPU
\t\tRelease|Any CPU = Release|Any CPU
\tEndGlobalSection
\tGlobalSection(ProjectConfigurationPlatforms) = postSolution
\t\t@@G1@@.Debug|Any CPU.ActiveCfg = Debug|Any CPU
\t\t@@G1@@.Debug|Any CPU.Build.0 = Debug|Any CPU
\t\t@@G1@@.Release|Any CPU.ActiveCfg = Release|Any CPU
\t\t@@G1@@.Release|Any CPU.Build.0 = Release|Any CPU
\t\t@@G2@@.Debug|Any CPU.ActiveCfg = Debug|Any CPU
\t\t@@G2@@.Debug|Any CPU.Build.0 = Debug|Any CPU
\t\t@@G2@@.Release|Any CPU.ActiveCfg = Release|Any CPU
\t\t@@G2@@.Release|Any CPU.Build.0 = Release|Any CPU
\tEndGlobalSection
\tGlobalSection(SolutionProperties) = preSolution
\t\tHideSolutionNode = FALSE
\tEndGlobalSection
EndGlobal
"""

_CS_PROJECT = """\
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
  </PropertyGroup>

</Project>
"""

_CS_TEST_PROJECT = """\
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <IsPackable>false</IsPackable>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
    <PackageReference Include="coverlet.collector" Version="6.0.2" />
  </ItemGroup>

  <ItemGroup>
    <ProjectReference Include="..\\..\\src\\@@P@@\\@@P@@.csproj" />
  </ItemGroup>

</Project>
"""

_CS_CODE = """\
namespace @@P@@;

public static class Greeter
{
    public static string Greet(string name) => $"Hello, {name}!";
}
"""

_CS_TEST = """\
using Xunit;

namespace @@P@@.Tests;

public class GreeterTests
{
    [Fact]
    public void Greet_names_the_person() => Assert.Equal("Hello, Ada!", Greeter.Greet("Ada"));
}
"""

_EDITORCONFIG = """\
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 4
"""

_LINT_SH = """\
#!/usr/bin/env bash
set -euo pipefail
dotnet format --verify-no-changes
"""

_TEST_SH = """\
#!/usr/bin/env bash
set -euo pipefail
dotnet test --collect:"XPlat Code Coverage" --results-directory ./TestResults
"""

_CS_CI = """\
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  ci:
    name: CI passed
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-dotnet@v4
        with:
          dotnet-version: "8.0.x"
      - name: Build
        run: dotnet build
      - name: Lint
        run: bash build/scripts/lint.sh
      - name: Tests
        run: bash build/scripts/test.sh
"""

_CS_IGNORE = """\
bin/
obj/
TestResults/
.vs/
"""

_CS_COMMANDS = """\
dotnet build
bash build/scripts/lint.sh
bash build/scripts/test.sh"""


_SLN_PROJECT = (
    'Project("{{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}}") = "{n}", "{path}", "{guid}"\nEndProject\n'
)


def _projects(p: str) -> str:
    library = _SLN_PROJECT.format(n=p, path=f"src\\{p}\\{p}.csproj", guid=_SLN_GUIDS[0])
    tests = f"tests\\{p}.Tests\\{p}.Tests.csproj"
    return library + _SLN_PROJECT.format(n=f"{p}.Tests", path=tests, guid=_SLN_GUIDS[1])


def _dotnet(name: str) -> tuple[dict[str, str], str]:
    p = _pascal(name)
    files = {
        f"{p}.sln": _fill(_SLN, PROJECTS=_projects(p), G1=_SLN_GUIDS[0], G2=_SLN_GUIDS[1]),
        f"src/{p}/{p}.csproj": _CS_PROJECT,
        f"src/{p}/Greeter.cs": _fill(_CS_CODE, P=p),
        f"tests/{p}.Tests/{p}.Tests.csproj": _fill(_CS_TEST_PROJECT, P=p),
        f"tests/{p}.Tests/GreeterTests.cs": _fill(_CS_TEST, P=p),
        "build/scripts/lint.sh": _LINT_SH,
        "build/scripts/test.sh": _TEST_SH,
        ".editorconfig": _EDITORCONFIG,
        ".github/workflows/ci.yml": _CS_CI,
        ".gitignore": _CS_IGNORE,
    }
    return files, _CS_COMMANDS


_BUILDERS = {"python": _python, "dotnet": _dotnet}


# --- putting it together ------------------------------------------------------------------------


def scaffold(name: str, stack: str, code: str) -> dict[str, str]:
    """Every file of a new repository, as {path: text}."""
    if not _NAME.match(name):
        raise ScaffoldError(
            f"name {name!r} must be lower case words joined by - (it names the folder and package)"
        )
    if stack not in STACKS:
        raise ScaffoldError(f"stack {stack!r} must be one of: {', '.join(STACKS)}")
    if not CODE_PATTERN.match(code):
        raise ScaffoldError(
            f"code {code!r} must be capital letters and digits, starting with a letter"
        )
    files, commands = _BUILDERS[stack](name)
    values = {"NAME": name, "CODE": code, "STACK": stack, "COMMANDS": commands}
    files.update(
        {
            ".gitattributes": _GITATTRIBUTES,
            "docs/WORK-ITEMS.md": _fill(_BOARD, **values),
            "docs/work-items/.gitkeep": "",
            "CLAUDE.md": _fill(_CLAUDE, **values),
            "AGENTS.md": _fill(_AGENTS, **values),
            "GLOSSARY.md": _fill(_GLOSSARY, **values),
            "adr/TEMPLATE.md": _ADR_TEMPLATE,
            f"adr/0001-stack-{stack}.md": _fill(_ADR_STACK, **values),
            ".github/pull_request_template.md": _PR_TEMPLATE,
            ".adlc/.gitignore": _ADLC_IGNORE,
            ".adlc/wizards/protect-main.sh": (
                resources.files("adlc") / "templates" / "protect-main.sh"
            ).read_text(encoding="utf-8"),
        }
    )
    files.update(dict(_gate_file(gate) for gate in GATES))
    files[".adlc/config.yaml"] = _config(stack, code, set(files))
    return dict(sorted(files.items()))


def render_scaffold(name: str, stack: str, paths: list[str]) -> str:
    """The plan shown for a dry run."""
    rows = [f"New {stack} repository '{name}' would contain {len(paths)} files:", ""]
    rows += [f"  {path}" for path in paths]
    rows += [
        "",
        "This is a dry run: nothing is written. Run again with --yes to create the folder and "
        "its first commit.",
    ]
    return "\n".join(rows) + "\n"


def _git(cwd: Path, *args: str) -> None:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise CommandError(f"git {' '.join(args[:2])} failed: {(done.stderr or '').strip()}")


def create_repo(dest: Path, name: str, stack: str, code: str) -> Created:
    """Write the scaffold into `dest` (new or empty) and commit it on `main`. No remote."""
    dest = Path(dest)
    files = scaffold(name, stack, code)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CommandError(f"{dest} is not empty; choose a new folder")
    for path, text in files.items():
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "add", "-A")
    scripts = [p for p in files if p.endswith(_EXECUTABLE)]
    _git(dest, "update-index", "--chmod=+x", *scripts)
    _git(dest, "commit", "-q", "-m", f"chore: scaffold {name} with adlc new")
    return Created(dest, tuple(files))
