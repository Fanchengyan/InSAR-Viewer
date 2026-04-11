"""Plugin controller for InSAR Viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QAction

from .dock_widget import InSARViewerDockWidget
from .logging import setup_logger

logger = setup_logger(__name__)


class InSARViewerPlugin:
    """QGIS plugin controller for InSAR Viewer."""

    def __init__(self, iface: object) -> None:
        """Store the QGIS interface and initialize plugin state.

        Parameters
        ----------
        iface : object
            Active QGIS interface instance.
        """

        self.iface = iface
        self.action: QAction | None = None
        self.dock_widget: InSARViewerDockWidget | None = None

    def initGui(self) -> None:
        """Create QGIS actions and menu entries."""

        self.action = QAction("InSAR Viewer", self.iface.mainWindow())
        icon_path = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "insar_viewer_icon.svg"
        )
        self.action.setIcon(QIcon(str(icon_path)))
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&InSAR Viewer", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self) -> None:
        """Remove QGIS actions and dock widget."""

        if self.dock_widget is not None:
            self.dock_widget.clear_runtime_artifacts()
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None

        if self.action is not None:
            self.iface.removePluginMenu("&InSAR Viewer", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

    def run(self) -> None:
        """Show the main dock widget."""

        if self.dock_widget is None:
            self.dock_widget = InSARViewerDockWidget(
                iface=self.iface,
                parent=self.iface.mainWindow(),
            )
            self.dock_widget.closed.connect(self._on_dock_closed)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)

        self.dock_widget.show()
        self.dock_widget.raise_()

    def _on_dock_closed(self) -> None:
        """Reset internal state after the dock widget is closed."""

        if self.dock_widget is None:
            return
        self.dock_widget.clear_runtime_artifacts()
        self.iface.removeDockWidget(self.dock_widget)
        self.dock_widget.deleteLater()
        self.dock_widget = None
