"""Map interaction helpers for point capture."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QCursor
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)
from qgis.gui import QgsMapToolEmitPoint

from .logging import setup_logger

if TYPE_CHECKING:
    from qgis.core import QgsPointXY
    from qgis.gui import QgsMapCanvas

logger = setup_logger(__name__)


class InSARPointMapTool(QgsMapToolEmitPoint):
    """Capture map clicks and emit WGS84 coordinates."""

    pointCaptured = pyqtSignal(float, float)

    def __init__(self, canvas: QgsMapCanvas) -> None:
        """Initialize the map tool.

        Parameters
        ----------
        canvas : QgsMapCanvas
            Active QGIS map canvas.
        """

        super().__init__(canvas)
        self.setCursor(QCursor(Qt.CrossCursor))

    def canvasReleaseEvent(self, event: object) -> None:
        """Handle a mouse-release event on the map canvas."""

        point = self.toMapCoordinates(event.pos())
        longitude, latitude = self._to_wgs84(point)
        self.pointCaptured.emit(longitude, latitude)

    def _to_wgs84(self, point: QgsPointXY) -> tuple[float, float]:
        """Transform a canvas point into WGS84 longitude/latitude."""

        canvas_crs = self.canvas().mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if canvas_crs == target_crs:
            return float(point.x()), float(point.y())

        transform = QgsCoordinateTransform(
            canvas_crs,
            target_crs,
            QgsProject.instance(),
        )
        transformed_point = transform.transform(point)
        return float(transformed_point.x()), float(transformed_point.y())
