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

from .dependency_path import is_plugin_managed_path
from .logging import setup_logger

logger = setup_logger(__name__)


def _candidate_proj_directories_from_sys_executable() -> list[Path]:
    """Return PROJ directories inferred from the active Python executable.

    Returns
    -------
    list[Path]
        Candidate directories ordered by preference.
    """

    executable_path = Path(sys.executable).expanduser().resolve()
    candidate_directories: list[Path] = []

    executable_candidates = [executable_path]
    for launcher_name in ("qgis", "QGIS"):
        launcher_path = executable_path.parent / launcher_name
        if launcher_path.exists():
            executable_candidates.append(launcher_path.resolve())

    for candidate_executable in executable_candidates:
        macos_directory = candidate_executable.parent
        contents_directory = macos_directory.parent
        if (
            macos_directory.name == "MacOS"
            and contents_directory.name == "Contents"
            and contents_directory.parent.suffix == ".app"
        ):
            qgis_bundle_proj_directory = (
                contents_directory / "Resources" / "qgis" / "proj"
            )
            if qgis_bundle_proj_directory not in candidate_directories:
                candidate_directories.append(qgis_bundle_proj_directory)

        for relative_path in (
            Path("../share/proj"),
            Path("../share/qgis/proj"),
            Path("share/proj"),
        ):
            sibling_candidate = (candidate_executable.parent / relative_path).resolve()
            if sibling_candidate not in candidate_directories:
                candidate_directories.append(sibling_candidate)

    return candidate_directories


def _candidate_proj_directories_from_prefix(prefix_path: Path) -> list[Path]:
    """Return PROJ directories discovered inside the active prefix.

    Parameters
    ----------
    prefix_path : Path
        Active Python prefix.

    Returns
    -------
    list[Path]
        Candidate directories ordered by preference.
    """

    candidate_directories: list[Path] = []
    bundled_patterns = (
        "lib/python*/site-packages/rasterio/proj_data",
        "Lib/site-packages/rasterio/proj_data",
        "lib/python*/site-packages/pyogrio/proj_data",
        "Lib/site-packages/pyogrio/proj_data",
        "lib/python*/site-packages/pyproj/proj_dir/share/proj",
        "Lib/site-packages/pyproj/proj_dir/share/proj",
    )
    for pattern in bundled_patterns:
        for matched_path in sorted(prefix_path.glob(pattern)):
            if matched_path not in candidate_directories:
                candidate_directories.append(matched_path)

    prefix_share_proj = prefix_path / "share" / "proj"
    if prefix_share_proj not in candidate_directories:
        candidate_directories.append(prefix_share_proj)
    return candidate_directories


def _candidate_proj_directories_from_module_origin(
    module_origin: Path | None,
) -> list[Path]:
    """Return candidate PROJ directories derived from a module location.

    Parameters
    ----------
    module_origin : Path | None
        Origin path of a resolved dependency module.

    Returns
    -------
    list[Path]
        Candidate directories ordered by preference.
    """

    if module_origin is None:
        return []

    candidate_directories: list[Path] = []
    module_base_directory = module_origin.parent
    local_candidates = (
        module_base_directory / "proj_data",
        module_base_directory / "proj_dir" / "share" / "proj",
        module_base_directory.parent / "pyproj" / "proj_dir" / "share" / "proj",
        module_base_directory.parent / "rasterio" / "proj_data",
        module_base_directory.parent / "pyogrio" / "proj_data",
    )
    for candidate_directory in local_candidates:
        if candidate_directory not in candidate_directories:
            candidate_directories.append(candidate_directory)
    return candidate_directories


def _resolve_proj_data_directory(prefix_path: Path) -> Path | None:
    """Return a compatible PROJ data directory for the active runtime.

    Parameters
    ----------
    prefix_path : Path
        Active Python prefix.

    Returns
    -------
    Path | None
        Directory that contains ``proj.db`` or ``None`` when nothing suitable
        can be found.
    """

    candidate_directories: list[Path] = []
    for candidate_directory in _candidate_proj_directories_from_sys_executable():
        if candidate_directory not in candidate_directories:
            candidate_directories.append(candidate_directory)

    for candidate_directory in _candidate_proj_directories_from_prefix(prefix_path):
        if candidate_directory not in candidate_directories:
            candidate_directories.append(candidate_directory)

    for module_name in ("rasterio", "pyogrio", "pyproj"):
        module_origin = module_spec_origin_path(module_name)
        for candidate_directory in _candidate_proj_directories_from_module_origin(
            module_origin
        ):
            if candidate_directory not in candidate_directories:
                candidate_directories.append(candidate_directory)

    for candidate_directory in candidate_directories:
        if not candidate_directory.exists():
            continue
        if not (candidate_directory / "proj.db").exists():
            logger.warning(
                "Ignoring PROJ directory without proj.db: %s",
                candidate_directory,
            )
            continue
        return candidate_directory
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
    ``PROJ_LIB`` and ``PROJ_DATA`` are aligned with the runtime's compatible
    PROJ database directory to avoid mixing one PROJ library with another
    installation's ``proj.db``. ``GDAL_DATA`` is still filled only when
    missing.
    """

    prefix_path = Path(sys.prefix).expanduser().resolve()
    proj_data_directory = _resolve_proj_data_directory(prefix_path)
    if proj_data_directory is None:
        logger.warning(
            "No compatible PROJ data directory was found under the active prefix: %s",
            prefix_path,
        )
    else:
        for environment_name in ("PROJ_LIB", "PROJ_DATA"):
            current_proj_path = os.environ.get(environment_name)
            resolved_current_proj_path = (
                _normalize_path(current_proj_path) if current_proj_path else None
            )
            if resolved_current_proj_path == proj_data_directory:
                continue
            if current_proj_path:
                logger.warning(
                    "Overriding incompatible %s=%s with %s",
                    environment_name,
                    current_proj_path,
                    proj_data_directory,
                )
            else:
                logger.info("Configured %s=%s", environment_name, proj_data_directory)
            os.environ[environment_name] = str(proj_data_directory)

    gdal_data_directory = prefix_path / "share" / "gdal"
    if not os.environ.get("GDAL_DATA"):
        if not gdal_data_directory.exists():
            logger.warning(
                "Expected runtime data directory for GDAL_DATA does not exist: %s",
                gdal_data_directory,
            )
            return
        os.environ["GDAL_DATA"] = str(gdal_data_directory)
        logger.info("Configured GDAL_DATA=%s", gdal_data_directory)


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
    """Remove targeted modules imported from outside supported dependency paths.

    Parameters
    ----------
    module_prefixes : tuple[str, ...]
        Module prefixes that should be re-imported from QGIS or plugin paths.
    """

    removed_module_names: list[str] = []
    for module_name, module_object in list(sys.modules.items()):
        if not _module_name_matches_prefixes(module_name, module_prefixes):
            continue
        module_origin = module_origin_path(module_name, module_object)
        if is_module_origin_supported(module_origin):
            continue
        sys.modules.pop(module_name, None)
        removed_module_names.append(module_name)

    if removed_module_names:
        logger.warning(
            "Removed modules imported outside supported dependency paths: %s",
            ", ".join(sorted(removed_module_names)),
        )


@contextmanager
def prefer_active_prefix_imports(module_prefixes: tuple[str, ...]) -> Iterator[None]:
    """Temporarily prioritize QGIS packages for selected imports.

    Parameters
    ----------
    module_prefixes : tuple[str, ...]
        Module prefixes that should be re-imported from QGIS first, then plugin
        managed dependencies.

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


def is_module_origin_supported(module_origin: Path | None) -> bool:
    """Return whether a module origin belongs to a supported dependency path.

    Parameters
    ----------
    module_origin : Path | None
        Module origin path.

    Returns
    -------
    bool
        ``True`` when the origin is inside the active QGIS prefix or the
        runtime-specific plugin-managed dependency directory.
    """

    return is_module_origin_in_active_prefix(module_origin) or is_plugin_managed_path(
        module_origin
    )


def ensure_module_origin_in_active_prefix(
    module_name: str,
    module_object: Any,
) -> None:
    """Raise when a native dependency comes from an unsupported path.

    Parameters
    ----------
    module_name : str
        Imported module name.
    module_object : Any
        Imported module object.

    Raises
    ------
    RuntimeError
        Raised when the imported module originates outside QGIS and plugin
        managed dependency paths.
    """

    module_origin = module_origin_path(module_name, module_object)
    if is_module_origin_supported(module_origin):
        return

    logger.error(
        "Detected incompatible runtime dependency for %s at %s. Active prefix: %s",
        module_name,
        module_origin,
        sys.prefix,
    )
    raise RuntimeError(
        f"Dependency '{module_name}' was imported from {module_origin}, which is "
        f"outside the active QGIS Python environment ({sys.prefix}) and the "
        "InSAR Viewer managed dependency directory. Clear external user-site "
        "packages or reinstall the plugin dependencies."
    )
