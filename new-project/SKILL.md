---
name: new-project
description: Scaffold a new Python project to the user's standards with Poetry, pyproject.toml, pre-commit, GitHub Actions CI, src layout, and tests directory.
---

Scaffold a new Python project named `$ARGUMENTS[0]` in the current working directory.

## Project structure

Create the following structure:

```
$ARGUMENTS[0]/
  src/
    $ARGUMENTS[0]/     (replace hyphens with underscores for the package name)
      __init__.py
      py.typed
  tests/
    __init__.py
    conftest.py
  .github/
    workflows/
      ci.yml
  pyproject.toml
  .pre-commit-config.yaml
  .gitignore
  AGENTS.md
  CLAUDE.md
  README.md
```

## File contents

### pyproject.toml
- Use Poetry (`[tool.poetry]` format)
- Target Python >=3.11,<3.13
- Include dev dependencies: pytest, pytest-cov, pytest-mock, ruff, mypy, pre-commit, loguru
- Configure ruff in `[tool.ruff]`: line-length = 99, target-version = "py311", select = ["E", "F", "I", "N", "W", "UP", "B", "A", "SIM", "ANN"], ignore ANN101/ANN102
- Configure pytest: testpaths = ["tests"], addopts = "--cov=src --cov-report=term-missing"
- Configure mypy: python_version = "3.11", strict = true

### .pre-commit-config.yaml
- ruff (lint + format)
- mypy
- Check for large files, trailing whitespace, end-of-file-fixer

### .github/workflows/ci.yml
- Run on push to main and on PRs
- Matrix: Python 3.10, 3.11, 3.12
- Steps: checkout, setup-python, install poetry, install deps, ruff check, ruff format --check, pytest

### .gitignore
- Standard Python gitignore (dist, __pycache__, .venv, *.egg-info, .mypy_cache, .pytest_cache, .ruff_cache, .coverage, *.pyc)

### AGENTS.md
- Brief project description using `$ARGUMENTS` (the full argument string including description if provided).
- Record project-specific setup, verification commands, and conventions only; do not duplicate personal global preferences.

### CLAUDE.md
- Create a compatibility shim containing exactly `@AGENTS.md` followed by a newline.

### __init__.py (src package)
- Set `__version__ = "0.1.0"`

### conftest.py
- Empty with a docstring: """Shared test fixtures."""

### README.md
- Project name as H1
- One-line description from `$ARGUMENTS` if provided
- Sections: Installation (poetry install), Development (pre-commit, ruff, pytest), License (leave as TBD)

## After scaffolding

1. `cd` into the project directory
2. Run `git init`
3. Run `poetry install`
4. Run `pre-commit install`
5. Run `git add -A && git commit -m "feat: initial project scaffold"`
6. Report what was created
