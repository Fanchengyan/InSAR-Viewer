# Repository Guidelines

## Code Style Guidelines

### Language and Naming

- Write all code and comments in English
- Use descriptive English names for variables, functions, and classes
- Follow PEP 8 and PEP 257 standards

## Code Conventions

- **Python 3.9+ and 3.11+ typing** required
- **`from __future__ import annotations`** in every file
- **Type hints**: Use Python 3.11+ syntax (`str | None`, `dict[str, int]`). Use `Literal` for fixed value sets. Do not using Union，Optional.
- **Docstrings**: Use NumPy-style docstrings for all public modules, classes, functions, and methods. Include Parameters, Returns, Raises, and Examples where applicable, and use Sphinx reStructuredText markup such as :func:..., :class:..., and directives like .. note::, .. tip::, and .. warning:: when needed.
- **Logging**: Use `from .logging import setup_logger; logger = setup_logger(__name__)` inside `insar_viewer` modules — log before raising exceptions errors
- **Paths**: Use `pathlib.Path` internally; convert to `str` only when passing to ISCE2/ISCE3 APIs
- **Type-checking imports**: Put heavy/circular imports inside `if TYPE_CHECKING:` blocks
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)
- **Linter**: ruff only (no black, no flake8). Line length 88. Ruff excludes `tests/`, `docs/`, `examples/` directories.

## Coding Style & Naming Conventions

Use Python3.9+, 4-space indentation, Python 3.11+ type hints, and NumPy-style docstrings. Write code, comments, and log messages in English. Prefer descriptive `snake_case` for functions and variables, `PascalCase` for Qt/QGIS widget classes, and `UPPER_CASE` for constants. Use `Literal`, `TypedDict`, `Protocol`, and `Self` where they clarify the API. Format and lint with `ruff`; use `uv` for Python tooling.

## Testing Guidelines

There is no dedicated automated test suite in this repository today. At minimum:

- run `ruff` and `py_compile`;
- exercise the changed workflow in QGIS;
- verify realtime raster workflows when touching rendering or widget code.

When adding tests later, place them in a top-level `tests/` package and name files `test_<module>.py`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative prefixes such as `fix:`, `refactor:`, `update`, and `remove:`. Follow that style and keep subjects concise, for example `fix: preserve feature-cache model fallback`.

PRs should include a clear summary, affected QGIS workflows, manual test steps, and screenshots or GIFs for UI changes. Link related issues when relevant.
