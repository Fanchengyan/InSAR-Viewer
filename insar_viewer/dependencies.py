"""Dependency checking and background installation helpers."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shlex
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
    """

    dependency: DependencySpec
    installed: bool
    version: str | None
    source: str
    origin: Path | None


REQUIRED_DEPENDENCIES: tuple[DependencySpec, ...] = (
    DependencySpec(
        package_name="numpy",
        import_name="numpy",
        pip_spec="numpy>=1.26.0,<2.0.0",
    ),
    DependencySpec(
        package_name="xarray",
        import_name="xarray",
        pip_spec="xarray>=2024.1.0,<2026.0.0",
    ),
    DependencySpec(
        package_name="dask",
        import_name="dask",
        pip_spec="dask[array]>=2024.1.0,<2026.0.0",
    ),
    DependencySpec(
        package_name="rioxarray",
        import_name="rioxarray",
        pip_spec="rioxarray>=0.15.0,<0.18.0",
    ),
    DependencySpec(
        package_name="rasterio",
        import_name="rasterio",
        pip_spec="rasterio>=1.4.0",
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
        pip_spec="pandas>=2.2.0",
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
            module_origin = module_spec_origin_path(dependency.import_name)
            installed = module_origin is not None
            if installed and dependency.import_name in {"pyproj", "rasterio"}:
                installed = is_module_origin_supported(module_origin)
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

    if is_module_origin_in_active_prefix(module_origin):
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

    constraints: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        distribution_name = distribution.metadata.get("Name")
        distribution_version = distribution.version
        if not distribution_name or not distribution_version:
            continue
        distribution_path = _distribution_root_path(distribution)
        if distribution_path is None:
            continue
        if not is_module_origin_in_active_prefix(distribution_path):
            continue
        normalized_name = distribution_name.casefold()
        constraints[normalized_name] = f"{distribution_name}=={distribution_version}"

    constraints_file = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        prefix="insar-viewer-pip-constraints-",
        suffix=".txt",
    )
    with constraints_file:
        for requirement in sorted(constraints.values(), key=str.casefold):
            constraints_file.write(f"{requirement}\n")
    return Path(constraints_file.name)


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
            "--upgrade-strategy",
            "only-if-needed",
            *[dependency.pip_spec for dependency in self.dependencies],
        ]
        environment = build_dependency_install_environment(python_executable)
        self.logMessage.emit(f"Installing into: {target_path}")
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
        register_plugin_managed_dependency_path()
        self.logMessage.emit("Dependency installation completed.")
        return True

    def finished(self, result: bool) -> None:
        """Emit task completion information on the main thread."""

        message = self.error_message if self.error_message else "Installation complete."
        self.installFinished.emit(result, message)
