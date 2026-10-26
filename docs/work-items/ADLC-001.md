---
key: ADLC-001
type: feature
milestone: M0
priority: 1
depends_on: []
adr: none
requires_approval: false
---
# ADLC-001: Repository scaffold: git init, pyproject, pytest, ruff, CI, CLAUDE.md, .gitattributes, GLOSSARY.md

**Description:** Turn this folder into a proper repo for the ADLC Python package, using the same conventions ADLC will later enforce on targets.

**Linked PRD:** Sec 9, 12 (M0); FR-37

**Acceptance criteria:**
- `git init` done; `main` is the base branch; first commit uses Conventional Commits
- `pyproject.toml` with a `src/adlc` package, `adlc --version` works, pytest and ruff configured
- A GitHub Actions workflow runs ruff and pytest on pull requests
- `CLAUDE.md` (under 150 lines), `.gitattributes` (LF line endings) and `GLOSSARY.md` exist
