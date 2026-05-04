"""Plugin-managed dependency path helpers for InSAR Viewer."""

from __future__ import annotations

import hashlib
import importlib
import platform
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import TypedDict

from .logging import setup_logger

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DEPENDENCY_ROOT = PLUGIN_ROOT / ".deps"
PLUGIN_RUNTIME_DEPENDENCIES_ROOT = PLUGIN_DEPENDENCY_ROOT / "runtimes"
LEGACY_PLUGIN_MANAGED_SITE_PACKAGES = PLUGIN_DEPENDENCY_ROOT / "python"

logger = setup_logger(__name__)


class DependencyPathStats(TypedDict):
    """Summary information for a plugin-managed dependency path.

    Attributes
    ----------
    path : Path
        Dependency directory path.
    exists : bool
        Whether the directory exists.
    file_count : int
        Number of files contained in the directory.
    size_bytes : int
        Total size of files contained in the directory.
    """

    path: Path
    exists: bool
    file_count: int
    size_bytes: int


def get_plugin_managed_site_packages(*, create: bool = False) -> Path:
    """Return the dependency directory for the active QGIS Python runtime.

    Parameters
    ----------
    create : bool, optional
        Whether to create the dependency directory before returning it.

    Returns
    -------
    Path
        Runtime-specific directory used as the pip ``--target`` location.
    """

    dependency_path = (
        PLUGIN_RUNTIME_DEPENDENCIES_ROOT / _runtime_dependency_key() / "python"
    )
    if create:
        dependency_path.mkdir(parents=True, exist_ok=True)
    return dependency_path


def register_plugin_managed_dependency_path(*, create: bool = False) -> Path:
    """Expose plugin-managed dependencies without overriding QGIS packages.

    Parameters
    ----------
    create : bool, optional
        Whether to create the dependency directory before registering it.

    Returns
    -------
    Path
        Registered plugin-managed dependency directory.
    """

    dependency_path = get_plugin_managed_site_packages(create=create)
    dependency_path_text = str(dependency_path)
    if dependency_path_text in sys.path:
        importlib.invalidate_caches()
        return dependency_path

    insert_index = _dependency_path_insert_index()
    sys.path.insert(insert_index, dependency_path_text)
    importlib.invalidate_caches()
    return dependency_path


def is_plugin_managed_path(path: Path | None) -> bool:
    """Return whether a path is inside an InSAR Viewer dependency directory.

    Parameters
    ----------
    path : Path | None
        Candidate module or distribution path.

    Returns
    -------
    bool
        ``True`` when the path belongs to the active or legacy plugin-managed
        dependency directories.
    """

    if path is None:
        return False
    dependency_roots = [
        get_plugin_managed_site_packages(),
        LEGACY_PLUGIN_MANAGED_SITE_PACKAGES,
    ]
    return any(_is_relative_to(path, root) for root in dependency_roots)


def get_plugin_managed_dependency_stats(path: Path) -> DependencyPathStats:
    """Return size and file-count information for a dependency directory.

    Parameters
    ----------
    path : Path
        Dependency directory to inspect.

    Returns
    -------
    DependencyPathStats
        Summary information for the dependency directory.
    """

    exists = path.exists()
    file_count = 0
    size_bytes = 0
    if exists:
        for child_path in path.rglob("*"):
            if not child_path.is_file():
                continue
            file_count += 1
            try:
                size_bytes += child_path.stat().st_size
            except OSError as exc:
                logger.warning(
                    "Could not stat dependency file %s while computing size: %s",
                    child_path,
                    exc,
                )
    return {
        "path": path,
        "exists": exists,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def iter_plugin_managed_runtime_site_packages() -> list[Path]:
    """Return existing plugin-managed dependency directories for all runtimes.

    Returns
    -------
    list[Path]
        Existing runtime-specific dependency directories.
    """

    if not PLUGIN_RUNTIME_DEPENDENCIES_ROOT.exists():
        return []
    dependency_paths: list[Path] = []
    for runtime_path in sorted(PLUGIN_RUNTIME_DEPENDENCIES_ROOT.iterdir()):
        site_packages_path = runtime_path / "python"
        if site_packages_path.exists():
            dependency_paths.append(site_packages_path)
    return dependency_paths


def iter_all_plugin_managed_site_packages(*, include_legacy: bool = True) -> list[Path]:
    """Return all plugin-managed dependency directories.

    Parameters
    ----------
    include_legacy : bool, optional
        Whether to include the old shared ``.deps/python`` directory.

    Returns
    -------
    list[Path]
        Existing plugin-managed dependency directories.
    """

    dependency_paths = iter_plugin_managed_runtime_site_packages()
    if include_legacy and LEGACY_PLUGIN_MANAGED_SITE_PACKAGES.exists():
        dependency_paths.append(LEGACY_PLUGIN_MANAGED_SITE_PACKAGES)
    return dependency_paths


def clear_current_plugin_managed_site_packages() -> int:
    """Delete dependencies installed for the active runtime.

    Returns
    -------
    int
        Number of files removed.
    """

    dependency_path = get_plugin_managed_site_packages()
    return _remove_dependency_path(dependency_path)


def clear_all_plugin_managed_site_packages() -> int:
    """Delete all plugin-managed dependencies, including legacy installs.

    Returns
    -------
    int
        Number of files removed.
    """

    removed_count = 0
    for dependency_path in iter_all_plugin_managed_site_packages(include_legacy=True):
        removed_count += _remove_dependency_path(dependency_path)
    _remove_empty_directory(PLUGIN_RUNTIME_DEPENDENCIES_ROOT)
    _remove_empty_directory(PLUGIN_DEPENDENCY_ROOT)
    return removed_count


def _dependency_path_insert_index() -> int:
    """Return a sys.path index after active-prefix site-package entries.

    Returns
    -------
    int
        Index where plugin-managed dependencies should be inserted.
    """

    prefix_path = Path(sys.prefix).expanduser().resolve()
    insert_index = 0
    for index, path_entry in enumerate(sys.path):
        normalized_path = _normalize_path(path_entry)
        if normalized_path is None:
            continue
        if "site-packages" not in normalized_path.parts:
            continue
        if _is_relative_to(normalized_path, prefix_path):
            insert_index = index + 1
    return insert_index


def _runtime_dependency_key() -> str:
    """Return a stable dependency directory key for the active runtime.

    Returns
    -------
    str
        Runtime-specific dependency directory name.
    """

    python_tag = f"py{sys.version_info.major}{sys.version_info.minor}"
    platform_tag = _safe_path_part(sysconfig.get_platform())
    architecture_tag = _safe_path_part(platform.machine() or "unknown")
    identity_parts = [
        sys.prefix,
        getattr(sys, "base_prefix", ""),
        sys.executable,
        python_tag,
        platform_tag,
        architecture_tag,
    ]
    runtime_hash = hashlib.sha256("\0".join(identity_parts).encode("utf-8"))
    return (
        f"{python_tag}-{platform_tag}-{architecture_tag}-"
        f"{runtime_hash.hexdigest()[:12]}"
    )


def _safe_path_part(value: str) -> str:
    """Return a filesystem-safe path component.

    Parameters
    ----------
    value : str
        Raw path component text.

    Returns
    -------
    str
        Sanitized path component text.
    """

    sanitized = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in value
    ).strip("-._")
    return sanitized or "unknown"


def _remove_dependency_path(path: Path) -> int:
    """Remove a dependency path and return the number of files deleted.

    Parameters
    ----------
    path : Path
        Dependency directory to delete.

    Returns
    -------
    int
        Number of files removed.
    """

    stats = get_plugin_managed_dependency_stats(path)
    if not stats["exists"]:
        return 0
    shutil.rmtree(path)
    return stats["file_count"]


def _remove_empty_directory(path: Path) -> None:
    """Remove a directory when it exists and is empty.

    Parameters
    ----------
    path : Path
        Directory to remove.
    """

    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return


def _normalize_path(path_string: str) -> Path | None:
    """Return a normalized filesystem path from a sys.path entry.

    Parameters
    ----------
    path_string : str
        Raw path string.

    Returns
    -------
    Path | None
        Resolved path, or ``None`` when the value is not path-like.
    """

    if not path_string or path_string in {"built-in", "frozen"}:
        return None
    try:
        return Path(path_string).expanduser().resolve()
    except OSError:
        logger.warning("Failed to normalize path entry: %s", path_string)
        return None


def _is_relative_to(path: Path, other_path: Path) -> bool:
    """Return whether ``path`` is relative to ``other_path``.

    Parameters
    ----------
    path : Path
        Candidate path to evaluate.
    other_path : Path
        Expected ancestor path.

    Returns
    -------
    bool
        ``True`` when ``path`` is inside ``other_path``.
    """

    try:
        path.relative_to(other_path)
    except ValueError:
        return False
    return True
