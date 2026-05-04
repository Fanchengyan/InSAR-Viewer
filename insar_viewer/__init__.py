"""InSAR Viewer plugin package."""

from __future__ import annotations

from .dependency_path import register_plugin_managed_dependency_path

register_plugin_managed_dependency_path()

__all__ = ["__version__"]

__version__ = "0.1.0"
