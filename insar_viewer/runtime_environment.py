"""Runtime environment helpers for QGIS dependency validation."""

from __future__ import annotations

import importlib
import importlib.util
import os
import site
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .logging import setup_logger

logger = setup_logger(__name__)


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


def _normalize_path(path_string: str) -> Path | None:
    """Return a normalized path when a path-like string is provided.

    Parameters
    ----------
    path_string : str
        Input path string taken from import metadata.

    Returns
    -------
    Path | None
        Normalized path, or ``None`` when the input is empty.
    """

    if not path_string or path_string in {"built-in", "frozen"}:
        return None
    try:
        return Path(path_string).expanduser().resolve()
    except OSError:
        logger.warning("Failed to normalize path entry: %s", path_string)
        return None


def _user_site_paths() -> list[Path]:
    """Return normalized user-site package directories.

    Returns
    -------
    list[Path]
        User-site package paths currently known to the active interpreter.
    """

    try:
        raw_user_site_paths = site.getusersitepackages()
    except Exception as exc:
        logger.warning("Failed to query Python user site-packages paths: %s", exc)
        return []

    if isinstance(raw_user_site_paths, str):
        candidate_paths = [raw_user_site_paths]
    else:
        candidate_paths = list(raw_user_site_paths)

    normalized_paths: list[Path] = []
    for candidate_path in candidate_paths:
        normalized_path = _normalize_path(candidate_path)
        if normalized_path is None or normalized_path in normalized_paths:
            continue
        normalized_paths.append(normalized_path)
    return normalized_paths


def configure_native_data_paths() -> None:
    """Populate GDAL and PROJ data directories from the active QGIS prefix.

    Notes
    -----
    This function only fills missing environment variables. Existing values are
    preserved so external QGIS launch scripts can still override them.
    """

    prefix_path = Path(sys.prefix).expanduser().resolve()
    environment_defaults = {
        "PROJ_LIB": prefix_path / "share" / "proj",
        "GDAL_DATA": prefix_path / "share" / "gdal",
    }

    for environment_name, directory_path in environment_defaults.items():
        if os.environ.get(environment_name):
            continue
        if not directory_path.exists():
            logger.warning(
                "Expected runtime data directory for %s does not exist: %s",
                environment_name,
                directory_path,
            )
            continue
        os.environ[environment_name] = str(directory_path)
        logger.info("Configured %s=%s", environment_name, directory_path)


def _module_name_matches_prefixes(
    module_name: str,
    module_prefixes: tuple[str, ...],
) -> bool:
    """Return whether a module belongs to one of the given prefixes.

    Parameters
    ----------
    module_name : str
        Loaded module name.
    module_prefixes : tuple[str, ...]
        Module prefixes that should be matched.

    Returns
    -------
    bool
        ``True`` when the module belongs to one of the given prefixes.
    """

    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in module_prefixes
    )


def _active_prefix_site_package_entries(original_sys_path: list[str]) -> list[str]:
    """Return ``sys.path`` entries that belong to the active prefix site-packages.

    Parameters
    ----------
    original_sys_path : list[str]
        Current Python import path list.

    Returns
    -------
    list[str]
        Ordered site-packages paths inside ``sys.prefix``.
    """

    prefix_path = Path(sys.prefix).expanduser().resolve()
    prefix_entries: list[str] = []
    for path_entry in original_sys_path:
        normalized_path = _normalize_path(path_entry)
        if normalized_path is None:
            continue
        if "site-packages" not in normalized_path.parts:
            continue
        if not _is_relative_to(normalized_path, prefix_path):
            continue
        if path_entry in prefix_entries:
            continue
        prefix_entries.append(path_entry)
    return prefix_entries


def _prioritized_sys_path(
    original_sys_path: list[str],
    prefix_entries: list[str],
) -> list[str]:
    """Return ``sys.path`` with active-prefix site-packages moved earlier.

    Parameters
    ----------
    original_sys_path : list[str]
        Current Python import path list.
    prefix_entries : list[str]
        Active-prefix site-packages entries to prioritize.

    Returns
    -------
    list[str]
        Reordered import path list.
    """

    if not prefix_entries:
        return list(original_sys_path)

    remaining_entries = [
        path_entry
        for path_entry in original_sys_path
        if path_entry not in prefix_entries
    ]
    user_site_paths = _user_site_paths()
    prioritized_entries: list[str] = []
    inserted_prefix_entries = False

    for path_entry in remaining_entries:
        normalized_path = _normalize_path(path_entry)
        if (
            not inserted_prefix_entries
            and normalized_path is not None
            and any(
                _is_relative_to(normalized_path, user_site_path)
                for user_site_path in user_site_paths
            )
        ):
            prioritized_entries.extend(prefix_entries)
            inserted_prefix_entries = True
        prioritized_entries.append(path_entry)

    if not inserted_prefix_entries:
        prioritized_entries.extend(prefix_entries)

    return prioritized_entries


def _purge_modules_outside_prefix(module_prefixes: tuple[str, ...]) -> None:
    """Remove targeted modules imported from outside the active prefix.

    Parameters
    ----------
    module_prefixes : tuple[str, ...]
        Module prefixes that should be re-imported from ``sys.prefix``.
    """

    removed_module_names: list[str] = []
    for module_name, module_object in list(sys.modules.items()):
        if not _module_name_matches_prefixes(module_name, module_prefixes):
            continue
        module_origin = module_origin_path(module_name, module_object)
        if is_module_origin_in_active_prefix(module_origin):
            continue
        sys.modules.pop(module_name, None)
        removed_module_names.append(module_name)

    if removed_module_names:
        logger.warning(
            "Removed modules imported outside the active prefix: %s",
            ", ".join(sorted(removed_module_names)),
        )


@contextmanager
def prefer_active_prefix_imports(module_prefixes: tuple[str, ...]) -> Iterator[None]:
    """Temporarily prioritize ``sys.prefix`` site-packages for selected imports.

    Parameters
    ----------
    module_prefixes : tuple[str, ...]
        Module prefixes that should be re-imported from ``sys.prefix``.

    Yields
    ------
    None
        Control returns to the caller while ``sys.path`` is temporarily
        reordered.
    """

    configure_native_data_paths()
    original_sys_path = list(sys.path)
    prefix_entries = _active_prefix_site_package_entries(original_sys_path)
    prioritized_sys_path = _prioritized_sys_path(original_sys_path, prefix_entries)
    _purge_modules_outside_prefix(module_prefixes)

    sys.path[:] = prioritized_sys_path
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path[:] = original_sys_path
        importlib.invalidate_caches()


def module_origin_path(module_name: str, module_object: Any) -> Path | None:
    """Return the normalized origin path for a loaded module.

    Parameters
    ----------
    module_name : str
        Imported module name.
    module_object : Any
        Imported module object.

    Returns
    -------
    Path | None
        Module origin path when available.
    """

    module_file = getattr(module_object, "__file__", None)
    if isinstance(module_file, str):
        return _normalize_path(module_file)

    module_spec = getattr(module_object, "__spec__", None)
    module_origin = getattr(module_spec, "origin", None)
    if isinstance(module_origin, str):
        return _normalize_path(module_origin)

    logger.debug("Module %s has no import origin path.", module_name)
    return None


def module_spec_origin_path(module_name: str) -> Path | None:
    """Return the import-spec origin path for a module name.

    Parameters
    ----------
    module_name : str
        Module name to inspect.

    Returns
    -------
    Path | None
        Import-spec origin path when available.
    """

    module_spec = importlib.util.find_spec(module_name)
    if module_spec is None or module_spec.origin is None:
        return None
    return _normalize_path(module_spec.origin)


def is_module_origin_in_active_prefix(module_origin: Path | None) -> bool:
    """Return whether a module origin belongs to the active QGIS prefix.

    Parameters
    ----------
    module_origin : Path | None
        Module origin path.

    Returns
    -------
    bool
        ``True`` when the origin is inside ``sys.prefix``.
    """

    if module_origin is None:
        return False
    prefix_path = Path(sys.prefix).expanduser().resolve()
    return _is_relative_to(module_origin, prefix_path)


def ensure_module_origin_in_active_prefix(
    module_name: str,
    module_object: Any,
) -> None:
    """Raise when a native dependency comes from outside the QGIS prefix.

    Parameters
    ----------
    module_name : str
        Imported module name.
    module_object : Any
        Imported module object.

    Raises
    ------
    RuntimeError
        Raised when the imported module originates outside ``sys.prefix``.
    """

    module_origin = module_origin_path(module_name, module_object)
    if is_module_origin_in_active_prefix(module_origin):
        return

    logger.error(
        "Detected incompatible runtime dependency for %s at %s. Active prefix: %s",
        module_name,
        module_origin,
        sys.prefix,
    )
    raise RuntimeError(
        f"Dependency '{module_name}' was imported from {module_origin}, which is "
        f"outside the active QGIS Python environment ({sys.prefix}). Install the "
        "plugin dependencies into the QGIS environment instead of the Python "
        "user site directory."
    )
