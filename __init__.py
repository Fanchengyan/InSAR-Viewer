"""QGIS plugin entry point for InSAR Viewer."""

from __future__ import annotations

from qgis.gui import QgisInterface

from .insar_viewer.plugin import InSARViewerPlugin


def classFactory(iface: QgisInterface) -> InSARViewerPlugin:
    """Create the QGIS plugin instance.

    Parameters
    ----------
    iface : QgisInterface
        Active QGIS interface instance.

    Returns
    -------
    InSARViewerPlugin
        Configured plugin controller.
    """

    return InSARViewerPlugin(iface)
