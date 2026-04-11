"""Canvas marker helpers for InSAR Viewer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)
from qgis.gui import QgsVertexMarker

from .logging import setup_logger
from .models import PointStyle, SamplePoint

if TYPE_CHECKING:
    from qgis.gui import QgsMapCanvas

logger = setup_logger(__name__)


class PointOverlayManager:
    """Manage point markers shown on the QGIS map canvas."""

    def __init__(self, canvas: QgsMapCanvas) -> None:
        """Initialize the overlay manager.

        Parameters
        ----------
        canvas : QgsMapCanvas
            Active QGIS map canvas.
        """

        self.canvas = canvas
        self._markers: dict[str, list[QgsVertexMarker]] = {
            "reference": [],
            "series": [],
            "live_probe": [],
        }

    def set_points(
        self,
        role: str,
        points: list[SamplePoint],
        style: PointStyle | dict[str, PointStyle],
        source_crs_wkt: str | None,
    ) -> None:
        """Replace markers for a role with a fresh set.

        Parameters
        ----------
        role : str
            Marker role identifier.
        points : list[SamplePoint]
            Points to show on the canvas.
        style : PointStyle | dict[str, PointStyle]
            Shared style for every point, or per-point styles keyed by label.
        source_crs_wkt : str | None
            CRS for the stored point coordinates.
        """

        self.clear_role(role)
        markers: list[QgsVertexMarker] = []
        for point in points:
            marker_style = self._style_for_point(point, style)
            marker = QgsVertexMarker(self.canvas)
            marker.setCenter(self._to_canvas_point_xy(point, source_crs_wkt))
            marker.setIconSize(marker_style.size)
            marker.setPenWidth(max(1, marker_style.size // 5))
            marker.setColor(QColor(marker_style.color_hex))
            marker.setFillColor(QColor(marker_style.color_hex))
            marker.setIconType(self._icon_type(marker_style.shape))
            markers.append(marker)
        self._markers[role] = markers

    def set_single_point(
        self,
        role: str,
        point: SamplePoint | None,
        style: PointStyle,
        source_crs_wkt: str | None,
    ) -> None:
        """Replace a single marker role, or clear it when no point exists."""

        points = [point] if point is not None else []
        self.set_points(role, points, style, source_crs_wkt)

    def _style_for_point(
        self,
        point: SamplePoint,
        style: PointStyle | dict[str, PointStyle],
    ) -> PointStyle:
        """Return the marker style for a point."""

        if isinstance(style, PointStyle):
            return style
        point_style = style.get(point.label)
        if point_style is None:
            logger.warning(
                "Missing point style for %s. Using fallback marker.",
                point.label,
            )
            return PointStyle(color_hex="#2c7fb8", size=7, shape="circle")
        return point_style

    def clear_role(self, role: str) -> None:
        """Remove all markers for a specific role."""

        scene = self.canvas.scene()
        for marker in self._markers.get(role, []):
            scene.removeItem(marker)
        self._markers[role] = []

    def clear_all(self) -> None:
        """Remove every managed marker."""

        for role in tuple(self._markers.keys()):
            self.clear_role(role)

    def _icon_type(self, shape_name: str) -> int:
        """Map a shape name to the matching QGIS vertex icon."""

        icon_lookup = {
            "circle": QgsVertexMarker.ICON_CIRCLE,
            "square": QgsVertexMarker.ICON_BOX,
            "cross": QgsVertexMarker.ICON_CROSS,
            "x": QgsVertexMarker.ICON_X,
        }
        return icon_lookup.get(shape_name, QgsVertexMarker.ICON_CIRCLE)

    def _to_canvas_point_xy(
        self,
        point: SamplePoint,
        source_crs_wkt: str | None,
    ) -> object:
        """Transform a point from the data CRS into the canvas CRS."""

        from qgis.core import QgsPointXY

        map_point = QgsPointXY(point.longitude, point.latitude)
        if not source_crs_wkt:
            return map_point

        source_crs = QgsCoordinateReferenceSystem()
        if not source_crs.createFromString(source_crs_wkt):
            logger.warning(
                "Failed to parse source CRS for canvas markers: %s",
                source_crs_wkt,
            )
            return map_point

        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if source_crs == canvas_crs:
            return map_point

        transform = QgsCoordinateTransform(
            source_crs,
            canvas_crs,
            QgsProject.instance(),
        )
        try:
            return transform.transform(map_point)
        except Exception as exc:
            logger.warning("Failed to transform canvas marker point: %s", exc)
            return map_point
