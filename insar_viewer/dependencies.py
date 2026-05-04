"""Dependency checking and background installation helpers."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from qgis.core import QgsTask

from .dependency_path import (
    is_plugin_managed_path,
    register_plugin_managed_dependency_path,
)
from .logging import setup_logger
from .runtime_environment import (
    configure_native_data_paths,
    is_module_origin_in_active_prefix,
    is_module_origin_in_qgis_runtime,
    is_module_origin_supported,
    module_spec_origin_path,
    prefer_active_prefix_imports,
)

configure_native_data_paths()
logger = setup_logger(__name__)


@dataclass(frozen=True)
class DependencySpec:
    """Runtime dependency metadata shown in the plugin UI.

    Attributes
    ----------
    package_name : str
        Package distribution name.
    import_name : str
        Python module name used for import checks.
    pip_spec : str
        Package specifier passed to pip.
    required : bool
        Whether missing dependency blocks core functionality.
    """

    package_name: str
    import_name: str
    pip_spec: str
    required: bool = True


@dataclass(frozen=True)
class DependencyStatus:
    """Installed status for one dependency.

    Attributes
    ----------
    dependency : DependencySpec
        Dependency metadata.
    installed : bool
        Whether the import target can be resolved.
    version : str | None
        Installed distribution version when available.
    source : str
        Dependency source shown in the UI.
    origin : Path | None
        Resolved module origin path when available.
    error_message : str | None
        Import error text when the dependency is present but unusable.
    """

    dependency: DependencySpec
    installed: bool
    version: str | None
    source: str
    origin: Path | None
    error_message: str | None = None


REQUIRED_DEPENDENCIES: tuple[DependencySpec, ...] = (
    DependencySpec(
        package_name="numpy",
        import_name="numpy",
        pip_spec="numpy>=1.26.0,<2.0.0",
    ),
    DependencySpec(
        package_name="xarray",
        import_name="xarray",
        pip_spec="xarray>=2024.1.0,<2025.0.0",
    ),
    DependencySpec(
        package_name="dask",
        import_name="dask",
        pip_spec="dask>=2024.1.0,<2025.0.0",
    ),
    DependencySpec(
        package_name="rioxarray",
        import_name="rioxarray",
        pip_spec="rioxarray>=0.15.0,<0.18.0",
    ),
    DependencySpec(
        package_name="rasterio",
        import_name="rasterio",
        pip_spec="rasterio>=1.4.0,<1.5.0",
    ),
    DependencySpec(
        package_name="pyproj",
        import_name="pyproj",
        pip_spec="pyproj>=3.6.1",
    ),
    DependencySpec(
        package_name="cftime",
        import_name="cftime",
        pip_spec="cftime>=1.6.4",
    ),
    DependencySpec(
        package_name="pandas",
        import_name="pandas",
        pip_spec="pandas>=2.2.0,<3.0.0",
    ),
    DependencySpec(
        package_name="openpyxl",
        import_name="openpyxl",
        pip_spec="openpyxl>=3.1.0",
    ),
    DependencySpec(
        package_name="pyqtgraph",
        import_name="pyqtgraph",
        pip_spec="pyqtgraph>=0.13.0",
        required=False,
    ),
)

RUNTIME_CONSTRAINED_DISTRIBUTIONS: dict[str, str] = {
    "numpy": "numpy",
    "pandas": "pandas",
    "packaging": "packaging",
    "python-dateutil": "dateutil",
    "pytz": "pytz",
    "pyyaml": "yaml",
    "click": "click",
    "certifi": "certifi",
    "pyproj": "pyproj",
}
RUNTIME_CONSTRAINT_EXCLUDED_DISTRIBUTIONS: set[str] = {
    "rasterio",
}


def dependency_statuses() -> list[DependencyStatus]:
    """Return the installed status of all plugin runtime dependencies.

    Returns
    -------
    list[DependencyStatus]
        Status rows suitable for display in the dependency UI.
    """

    statuses: list[DependencyStatus] = []
    register_plugin_managed_dependency_path()
    with prefer_active_prefix_imports(
        ("pyproj", "rasterio", "rioxarray", "xarray", "cftime")
    ):
        for dependency in REQUIRED_DEPENDENCIES:
            module_origin, import_error = _probe_dependency_import(dependency)
            installed = module_origin is not None and import_error is None
            version = (
                _distribution_version(dependency.package_name) if installed else None
            )
            statuses.append(
                DependencyStatus(
                    dependency=dependency,
                    installed=installed,
                    version=version,
                    source=_dependency_source(module_origin) if installed else "-",
                    origin=module_origin,
                    error_message=import_error,
                )
            )
    return statuses


def missing_dependencies() -> list[DependencySpec]:
    """Return dependencies whose import targets cannot be resolved.

    Returns
    -------
    list[DependencySpec]
        Missing dependency specs.
    """

    return [
        status.dependency for status in dependency_statuses() if not status.installed
    ]


def dependency_install_specs(dependencies: list[DependencySpec]) -> list[str]:
    """Return pip requirements adapted to the active QGIS numpy version.

    Parameters
    ----------
    dependencies : list[DependencySpec]
        Dependencies selected for installation.

    Returns
    -------
    list[str]
        Pip requirement strings.
    """

    numpy_major_version = active_qgis_numpy_major_version()
    return [
        _dependency_install_spec(dependency, numpy_major_version)
        for dependency in dependencies
    ]


def runtime_resolver_constraints() -> list[str]:
    """Return resolver constraints derived from the active QGIS runtime.

    Returns
    -------
    list[str]
        Additional pip constraints that keep plugin-managed dependencies
        compatible with packages already loaded from QGIS.
    """

    constraints = active_prefix_distribution_constraints()
    constraints.extend(_numpy_compatibility_constraints())
    return constraints


def active_prefix_distribution_constraints() -> list[str]:
    """Return exact constraints for distributions provided by QGIS.

    Returns
    -------
    list[str]
        Requirements such as ``numpy==1.26.4`` for packages provided by QGIS.
    """

    constraints: dict[str, str] = {}
    for distribution_name, module_name in RUNTIME_CONSTRAINED_DISTRIBUTIONS.items():
        distribution_version = _active_runtime_distribution_version(
            distribution_name,
            module_name,
        )
        if distribution_version is None:
            continue
        constraints[distribution_name.casefold()] = (
            f"{distribution_name}=={distribution_version}"
        )

    for distribution in importlib.metadata.distributions():
        distribution_name = distribution.metadata.get("Name")
        distribution_version = distribution.version
        if not distribution_name or not distribution_version:
            continue
        normalized_name = distribution_name.casefold()
        if normalized_name in RUNTIME_CONSTRAINT_EXCLUDED_DISTRIBUTIONS:
            continue
        distribution_path = _distribution_root_path(distribution)
        if distribution_path is None:
            continue
        if not is_module_origin_in_active_prefix(distribution_path):
            continue
        constraints[normalized_name] = f"{distribution_name}=={distribution_version}"
    return sorted(constraints.values(), key=str.casefold)


def active_qgis_numpy_major_version() -> int | None:
    """Return the major version of numpy installed in the active QGIS prefix.

    Returns
    -------
    int | None
        QGIS-provided numpy major version, or ``None`` when it cannot be
        resolved.
    """

    numpy_version = _active_runtime_distribution_version("numpy", "numpy")
    if numpy_version is None:
        return None
    version_head = numpy_version.split(".", maxsplit=1)[0]
    try:
        return int(version_head)
    except ValueError:
        logger.warning("Could not parse active QGIS numpy version: %s", numpy_version)
        return None


def _dependency_install_spec(
    dependency: DependencySpec,
    numpy_major_version: int | None,
) -> str:
    """Return a numpy-aware pip spec for one dependency.

    Parameters
    ----------
    dependency : DependencySpec
        Dependency selected for installation.
    numpy_major_version : int | None
        Major version of numpy from the active QGIS prefix.

    Returns
    -------
    str
        Pip requirement string.
    """

    if numpy_major_version is None or numpy_major_version < 2:
        return dependency.pip_spec

    relaxed_specs = {
        "xarray": "xarray>=2024.1.0,<2026.0.0",
        "dask": "dask>=2024.1.0,<2026.0.0",
        "pandas": "pandas>=2.2.0",
        "rasterio": "rasterio>=1.4.0",
    }
    return relaxed_specs.get(dependency.package_name, dependency.pip_spec)


def _numpy_compatibility_constraints() -> list[str]:
    """Return package constraints required by the QGIS numpy major version.

    Returns
    -------
    list[str]
        Constraints for packages whose newer releases can assume newer numpy
        APIs than QGIS provides.
    """

    numpy_major_version = active_qgis_numpy_major_version()
    if numpy_major_version is None or numpy_major_version < 2:
        return [
            "xarray<2025.0.0",
            "dask<2025.0.0",
            "pandas<3.0.0",
            "rasterio<1.5.0",
        ]
    return [
        "xarray<2026.0.0",
        "dask<2026.0.0",
    ]


def _distribution_version(package_name: str) -> str | None:
    """Return an installed distribution version when available.

    Parameters
    ----------
    package_name : str
        Distribution name to query.

    Returns
    -------
    str | None
        Installed version, or ``None`` when the distribution is unavailable.
    """

    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _active_runtime_distribution_version(
    package_name: str,
    module_name: str,
) -> str | None:
    """Return a distribution version from QGIS or another non-plugin path.

    Parameters
    ----------
    package_name : str
        Distribution name to query.
    module_name : str
        Importable module name used to verify the runtime origin.

    Returns
    -------
    str | None
        Installed version outside plugin-managed dependencies, or ``None`` when
        unavailable.
    """

    module_origin = module_spec_origin_path(module_name)
    if module_origin is not None and not is_plugin_managed_path(module_origin):
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            logger.warning(
                "Could not import runtime module %s for constraints: %s",
                module_name,
                exc,
            )
        else:
            module_version = getattr(module, "__version__", None)
            if isinstance(module_version, str) and module_version:
                return module_version

    normalized_package_name = package_name.casefold()
    for distribution in importlib.metadata.distributions():
        distribution_name = distribution.metadata.get("Name")
        if distribution_name is None:
            continue
        if distribution_name.casefold() != normalized_package_name:
            continue
        distribution_path = _distribution_root_path(distribution)
        if distribution_path is None:
            continue
        if is_plugin_managed_path(distribution_path):
            continue
        if not (
            is_module_origin_in_active_prefix(distribution_path)
            or module_origin is not None
        ):
            continue
        return distribution.version
    return None


def _probe_dependency_import(
    dependency: DependencySpec,
) -> tuple[Path | None, str | None]:
    """Import a dependency and return its origin plus any import error.

    Parameters
    ----------
    dependency : DependencySpec
        Dependency to import.

    Returns
    -------
    tuple[Path | None, str | None]
        Module origin and import error text. The error is ``None`` when the
        dependency can be imported from a supported location.
    """

    module_origin = module_spec_origin_path(dependency.import_name)
    if module_origin is None:
        return None, None
    try:
        importlib.import_module(dependency.import_name)
    except ImportError as exc:
        logger.warning(
            "Dependency %s was found at %s but could not be imported: %s",
            dependency.import_name,
            module_origin,
            exc,
        )
        return module_origin, str(exc)

    module_origin = module_spec_origin_path(dependency.import_name)
    if dependency.import_name in {"pyproj", "rasterio"} and not (
        is_module_origin_supported(module_origin)
    ):
        error_message = (
            f"{dependency.import_name} resolved outside QGIS and InSAR Viewer "
            f"managed paths: {module_origin}"
        )
        logger.warning(error_message)
        return module_origin, error_message
    return module_origin, None


def _dependency_source(module_origin: Path | None) -> str:
    """Return a short dependency source label for a module origin.

    Parameters
    ----------
    module_origin : Path | None
        Resolved module origin path.

    Returns
    -------
    str
        Human-readable dependency source label.
    """

    if is_module_origin_in_qgis_runtime(module_origin):
        return "QGIS runtime"
    if is_plugin_managed_path(module_origin):
        return "InSAR Viewer managed"
    return "External"


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


def _iter_python_interpreter_candidates() -> list[Path]:
    """Return candidate Python executables for the active QGIS environment.

    Returns
    -------
    list[Path]
        Ordered executable candidates, with duplicates removed.
    """

    prefix_path = Path(sys.prefix).expanduser().resolve()
    executable_path = Path(sys.executable).expanduser().resolve()
    candidate_paths: list[Path] = []

    for sibling_name in (
        f"python{sys.version_info.major}.{sys.version_info.minor}",
        f"python{sys.version_info.major}",
        "python3",
        "python",
    ):
        candidate_paths.append(executable_path.parent / sibling_name)

    for relative_path in (
        "bin/python",
        "bin/python3",
        "bin/python3.11",
        "python",
        "python3",
        "python3.11",
    ):
        candidate_paths.append(prefix_path / relative_path)

    app_contents_path = _qgis_app_contents_path(prefix_path, executable_path)
    if app_contents_path is not None:
        for relative_path in (
            "MacOS/python",
            "MacOS/python3",
            "MacOS/python3.11",
            "MacOS/python3.12",
        ):
            candidate_paths.append(app_contents_path / relative_path)

    for raw_path in sys.path:
        if not raw_path:
            continue
        try:
            resolved_path = Path(raw_path).expanduser().resolve()
        except OSError:
            continue
        if not _is_relative_to(resolved_path, prefix_path):
            continue

        for parent_path in (resolved_path, *resolved_path.parents):
            if parent_path == prefix_path.parent:
                break
            if parent_path.name not in {"bin", "Scripts"}:
                continue
            for child_path in sorted(parent_path.glob("python*")):
                candidate_paths.append(child_path)

    unique_candidates: list[Path] = []
    seen_paths: set[Path] = set()
    for candidate_path in candidate_paths:
        try:
            resolved_candidate = candidate_path.expanduser().resolve()
        except OSError:
            continue
        if resolved_candidate in seen_paths:
            continue
        seen_paths.add(resolved_candidate)
        unique_candidates.append(resolved_candidate)
    return unique_candidates


def _qgis_app_contents_path(
    prefix_path: Path,
    executable_path: Path,
) -> Path | None:
    """Return the QGIS macOS app bundle ``Contents`` path when available.

    Parameters
    ----------
    prefix_path : Path
        Resolved ``sys.prefix`` path.
    executable_path : Path
        Resolved ``sys.executable`` path.

    Returns
    -------
    Path | None
        ``.app/Contents`` path for the active QGIS bundle, or ``None`` when the
        runtime is not using the standard macOS app layout.
    """

    for candidate_path in (executable_path.parent, prefix_path, *prefix_path.parents):
        if candidate_path.name != "Contents":
            continue
        if candidate_path.parent.suffix != ".app":
            continue
        return candidate_path
    return None


def resolve_python_interpreter() -> Path:
    """Resolve the Python interpreter for the active QGIS runtime environment.

    Returns
    -------
    Path
        Resolved Python interpreter path that belongs to ``sys.prefix``.

    Raises
    ------
    RuntimeError
        Raised when no suitable Python interpreter can be found.
    """

    prefix_path = Path(sys.prefix).expanduser().resolve()
    executable_path = Path(sys.executable).expanduser().resolve()
    if (
        executable_path.is_file()
        and executable_path.name.startswith("python")
        and _is_relative_to(executable_path, prefix_path)
    ):
        return executable_path

    for candidate_path in _iter_python_interpreter_candidates():
        if not candidate_path.is_file():
            continue
        if not os.access(candidate_path, os.X_OK):
            continue
        if not candidate_path.name.startswith("python"):
            continue
        if not _belongs_to_qgis_runtime(candidate_path, prefix_path, executable_path):
            continue
        return candidate_path

    message = (
        "Could not resolve a Python interpreter from the current QGIS runtime. "
        f"sys.executable={sys.executable!r}, sys.prefix={sys.prefix!r}"
    )
    logger.error(message)
    raise RuntimeError(message)


def _belongs_to_qgis_runtime(
    candidate_path: Path,
    prefix_path: Path,
    executable_path: Path,
) -> bool:
    """Return whether a Python candidate belongs to the active QGIS runtime.

    Parameters
    ----------
    candidate_path : Path
        Resolved Python executable candidate.
    prefix_path : Path
        Resolved ``sys.prefix`` path.
    executable_path : Path
        Resolved ``sys.executable`` path.

    Returns
    -------
    bool
        ``True`` when the candidate is part of the current QGIS runtime layout.
    """

    if _is_relative_to(candidate_path, prefix_path):
        return True

    app_contents_path = _qgis_app_contents_path(prefix_path, executable_path)
    if app_contents_path is not None and _is_relative_to(
        candidate_path, app_contents_path
    ):
        return True

    return False


def python_executable_from_environment() -> Path:
    """Return the resolved Python executable for the active QGIS environment.

    Returns
    -------
    Path
        Resolved Python interpreter path for dependency installation.

    Raises
    ------
    RuntimeError
        Raised when no suitable Python interpreter can be found.
    """

    return resolve_python_interpreter()


def build_dependency_install_environment(
    python_executable: Path,
) -> dict[str, str]:
    """Build subprocess environment variables for dependency installation.

    Parameters
    ----------
    python_executable : Path
        Resolved Python executable used to run ``pip``.

    Returns
    -------
    dict[str, str]
        Environment variables for the dependency installation subprocess.
    """

    environment = os.environ.copy()
    prefix_path = Path(sys.prefix).expanduser().resolve()
    executable_path = Path(sys.executable).expanduser().resolve()
    app_contents_path = _qgis_app_contents_path(prefix_path, executable_path)

    if app_contents_path is not None and _is_relative_to(
        python_executable, app_contents_path
    ):
        frameworks_path = app_contents_path / "Frameworks"
        if frameworks_path.is_dir():
            environment["PYTHONHOME"] = str(frameworks_path)

    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def build_runtime_constraints_file() -> Path:
    """Create a pip constraints file from packages already in the QGIS runtime.

    Returns
    -------
    Path
        Temporary constraints file path. The caller is responsible for deleting
        the file after pip exits.
    """

    constraints_file = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        prefix="insar-viewer-pip-constraints-",
        suffix=".txt",
    )
    with constraints_file:
        for requirement in runtime_resolver_constraints():
            constraints_file.write(f"{requirement}\n")
    return Path(constraints_file.name)


def prune_runtime_provided_target_packages(target_path: Path) -> list[str]:
    """Remove target packages that are already provided by QGIS.

    Parameters
    ----------
    target_path : Path
        Plugin-managed pip target directory.

    Returns
    -------
    list[str]
        Distribution names removed from the target directory.
    """

    runtime_package_names = _runtime_provided_package_names()
    if not target_path.exists() or not runtime_package_names:
        return []

    removed_names: list[str] = []
    for distribution in importlib.metadata.distributions(path=[str(target_path)]):
        distribution_name = distribution.metadata.get("Name")
        if distribution_name is None:
            continue
        if distribution_name.casefold() not in runtime_package_names:
            continue
        _remove_target_distribution(distribution, target_path)
        removed_names.append(distribution_name)
    return sorted(removed_names, key=str.casefold)


def _runtime_provided_package_names() -> set[str]:
    """Return normalized package names exactly provided by QGIS.

    Returns
    -------
    set[str]
        Package names from exact runtime constraints.
    """

    package_names: set[str] = set()
    for constraint in active_prefix_distribution_constraints():
        if "==" not in constraint:
            continue
        package_name = constraint.split("==", maxsplit=1)[0].strip().casefold()
        if package_name:
            package_names.add(package_name)
    return package_names


def _remove_target_distribution(
    distribution: importlib.metadata.Distribution,
    target_path: Path,
) -> None:
    """Remove files belonging to one target distribution.

    Parameters
    ----------
    distribution : importlib.metadata.Distribution
        Distribution installed in the plugin-managed target directory.
    target_path : Path
        Plugin-managed pip target directory.
    """

    distribution_files = distribution.files
    if distribution_files is None:
        logger.warning("Cannot remove distribution without RECORD: %s", distribution)
        return

    resolved_target_path = target_path.resolve()
    parent_paths: set[Path] = set()
    for distribution_file in distribution_files:
        file_path = Path(distribution.locate_file(distribution_file))
        try:
            file_path.relative_to(resolved_target_path)
        except ValueError:
            logger.warning("Ignoring dependency file outside target: %s", file_path)
            continue
        parent_paths.update(file_path.parents)
        if file_path.is_dir():
            shutil.rmtree(file_path, ignore_errors=True)
            continue
        try:
            file_path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Failed to remove dependency file %s: %s", file_path, exc)

    for parent_path in sorted(
        parent_paths,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if parent_path == resolved_target_path:
            continue
        try:
            parent_path.relative_to(resolved_target_path)
        except ValueError:
            continue
        try:
            parent_path.rmdir()
        except OSError:
            continue


def _distribution_root_path(
    distribution: importlib.metadata.Distribution,
) -> Path | None:
    """Return the normalized root path for an installed distribution.

    Parameters
    ----------
    distribution : importlib.metadata.Distribution
        Distribution metadata object.

    Returns
    -------
    Path | None
        Distribution root path when available.
    """

    try:
        distribution_path = distribution.locate_file("")
    except Exception as exc:
        logger.warning("Failed to locate distribution %s: %s", distribution, exc)
        return None
    try:
        return Path(distribution_path).expanduser().resolve()
    except OSError:
        logger.warning("Failed to normalize distribution path: %s", distribution_path)
        return None


class DependencyInstallTask(QgsTask):
    """Install missing Python dependencies in a QGIS background task."""

    logMessage = pyqtSignal(str)
    installFinished = pyqtSignal(bool, str)

    def __init__(self, dependencies: list[DependencySpec]) -> None:
        """Initialize the install task.

        Parameters
        ----------
        dependencies : list[DependencySpec]
            Dependencies to install with pip.
        """

        super().__init__("Install InSAR Viewer dependencies", QgsTask.CanCancel)
        self.dependencies = dependencies
        self.error_message = ""

    def run(self) -> bool:
        """Run pip in a subprocess without blocking the QGIS UI thread.

        Returns
        -------
        bool
            ``True`` when pip exits successfully, otherwise ``False``.
        """

        if not self.dependencies:
            self.logMessage.emit("All dependencies are already installed.")
            return True

        try:
            python_executable = python_executable_from_environment()
        except RuntimeError as exc:
            logger.error("Failed to resolve Python interpreter: %s", exc)
            self.error_message = str(exc)
            self.logMessage.emit(self.error_message)
            return False

        target_path = register_plugin_managed_dependency_path(create=True)
        constraints_file = build_runtime_constraints_file()
        command = [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--target",
            str(target_path),
            "--constraint",
            str(constraints_file),
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "--upgrade",
            "--upgrade-strategy",
            "only-if-needed",
            *dependency_install_specs(self.dependencies),
        ]
        environment = build_dependency_install_environment(python_executable)
        self.logMessage.emit(f"Installing into: {target_path}")
        numpy_major_version = active_qgis_numpy_major_version()
        if numpy_major_version is not None:
            self.logMessage.emit(
                f"Detected QGIS numpy major version: {numpy_major_version}"
            )
        self._log_runtime_constraints()
        self.logMessage.emit(
            "Running: " + " ".join(shlex.quote(argument) for argument in command)
        )
        try:
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            logger.error("Failed to start dependency installation: %s", exc)
            self.error_message = str(exc)
            self.logMessage.emit(f"Failed to start pip: {exc}")
            constraints_file.unlink(missing_ok=True)
            return False

        try:
            if process.stdout is not None:
                for line in process.stdout:
                    if self.isCanceled():
                        process.terminate()
                        self.error_message = "Installation was canceled."
                        self.logMessage.emit(self.error_message)
                        return False
                    self.logMessage.emit(line.rstrip())

            return_code = process.wait()
        finally:
            constraints_file.unlink(missing_ok=True)

        if return_code != 0:
            self.error_message = f"pip exited with status {return_code}."
            logger.error(self.error_message)
            self.logMessage.emit(self.error_message)
            return False
        removed_names = prune_runtime_provided_target_packages(target_path)
        if removed_names:
            self.logMessage.emit(
                "Removed QGIS-provided packages from managed target: "
                + ", ".join(removed_names)
            )
        register_plugin_managed_dependency_path()
        self.logMessage.emit("Dependency installation completed.")
        return True

    def _log_runtime_constraints(self) -> None:
        """Log key resolver constraints used for dependency installation."""

        key_prefixes = ("numpy", "xarray", "dask", "pandas", "rasterio")
        key_constraints = [
            constraint
            for constraint in runtime_resolver_constraints()
            if constraint.startswith(key_prefixes)
        ]
        if not key_constraints:
            return
        self.logMessage.emit(
            "Runtime constraints: " + ", ".join(sorted(key_constraints))
        )

    def finished(self, result: bool) -> None:
        """Emit task completion information on the main thread."""

        message = self.error_message if self.error_message else "Installation complete."
        self.installFinished.emit(result, message)
