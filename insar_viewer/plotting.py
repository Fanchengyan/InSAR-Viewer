"""PyQtGraph time-series plotting widgets used by the plugin UI."""

from __future__ import annotations

import math
from typing import Any, TypedDict

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from .logging import setup_logger
from .models import SampleSeries

logger = setup_logger(__name__)

pg: Any = None


class HoverPointInfo(TypedDict):
    """Metadata shown inside the time-series hover tooltip."""

    label: str
    time: str
    value: float
    requested_lon: float
    requested_lat: float
    matched_lon: float
    matched_lat: float


def _import_pyqtgraph() -> type[Any]:
    """Import PyQtGraph PlotWidget lazily.

    Returns
    -------
    type[Any]
        PlotWidget class.

    Raises
    ------
    RuntimeError
        Raised when PyQtGraph is unavailable.
    """

    global pg
    if pg is None:
        try:
            import pyqtgraph as pg_module

            pg = pg_module
        except ImportError as exc:
            logger.error("pyqtgraph is unavailable: %s", exc)
            raise RuntimeError("pyqtgraph is required for plotting.") from exc
    return pg.PlotWidget


class HoverLabel(QLabel):
    """Label that displays hover information for a scatter point."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the hover label.

        Parameters
        ----------
        parent : QWidget | None, optional
            Parent widget.
        """

        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setTextFormat(Qt.PlainText)
        self.setStyleSheet(
            "background-color: rgba(255, 255, 255, 235);"
            "border: 1px solid #6b7280;"
            "color: #111827;"
            "padding: 6px 8px;"
            "border-radius: 4px;"
        )


class TimeSeriesPlotWidget(QWidget):
    """Reusable PyQtGraph time-series widget with hover, pan, and zoom support."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        """Create the plot widget.

        Parameters
        ----------
        title : str
            Plot title.
        parent : QWidget | None, optional
            Parent widget.
        """

        super().__init__(parent)
        self._title = title
        self._layout = QVBoxLayout(self)
        self._fallback_label = QLabel(
            "Install pyqtgraph to enable plotting.",
            self,
        )
        self._layout.addWidget(self._fallback_label)
        self._plot_widget: Any = None
        self._hover_label: HoverLabel | None = None
        self._time_labels: list[str] = []
        self._series_data: list[dict[str, Any]] = []
        self._setup_canvas()

    def _setup_canvas(self) -> None:
        """Initialize the PyQtGraph canvas when available."""

        try:
            plot_widget_class = _import_pyqtgraph()
        except RuntimeError:
            return

        self._layout.removeWidget(self._fallback_label)
        self._fallback_label.deleteLater()

        plot_widget = plot_widget_class(self)
        plot_widget.setBackground("w")
        plot_widget.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )
        plot_widget.showGrid(x=True, y=True, alpha=0.3)
        plot_widget.setLabel("left", "Value")
        plot_widget.setLabel("bottom", "Time")
        plot_widget.setTitle(self._title)
        plot_widget.addLegend()
        plot_widget.setMouseEnabled(x=True, y=True)
        self._plot_widget = plot_widget
        self._layout.addWidget(plot_widget)

        self._hover_label = HoverLabel(self)
        self._hover_label.hide()

        plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def _on_mouse_moved(self, pos: Any) -> None:
        """Handle mouse movement for hover display.

        Parameters
        ----------
        pos : QPointF
            Mouse position in scene coordinates.
        """

        if self._plot_widget is None or self._hover_label is None:
            return

        if not hasattr(pos, "x") or not hasattr(pos, "y"):
            return

        closest_info = self._find_hover_point_info(QPointF(pos.x(), pos.y()))

        if closest_info is not None:
            self._show_hover_label(closest_info)
        else:
            self._hover_label.hide()

    def _find_hover_point_info(
        self,
        scene_position: QPointF,
    ) -> HoverPointInfo | None:
        """Return the hovered point nearest to the cursor.

        Parameters
        ----------
        scene_position : QPointF
            Cursor position in scene coordinates.

        Returns
        -------
        HoverPointInfo | None
            Point metadata when the cursor is close enough to a plotted marker.
        """

        if self._plot_widget is None:
            return None

        scene_rect = self._plot_widget.sceneBoundingRect()
        if not scene_rect.contains(scene_position):
            return None

        view_box = self._plot_widget.plotItem.vb
        cursor_view_position = view_box.mapSceneToView(scene_position)
        max_distance_pixels = 12.0
        tolerance_view_position = view_box.mapSceneToView(
            QPointF(
                scene_position.x() + max_distance_pixels,
                scene_position.y() + max_distance_pixels,
            )
        )
        x_tolerance = max(
            abs(tolerance_view_position.x() - cursor_view_position.x()),
            1e-9,
        )
        y_tolerance = max(
            abs(tolerance_view_position.y() - cursor_view_position.y()),
            1e-9,
        )

        closest_info: HoverPointInfo | None = None
        min_distance = float("inf")
        for series_item in self._series_data:
            x_values = series_item.get("x", [])
            y_values = series_item.get("y", [])
            point_data = series_item.get("point_data", [])

            for index, (x_value, y_value) in enumerate(zip(x_values, y_values)):
                if not math.isfinite(y_value) or index >= len(point_data):
                    continue

                normalized_distance = math.hypot(
                    (x_value - cursor_view_position.x()) / x_tolerance,
                    (y_value - cursor_view_position.y()) / y_tolerance,
                )
                if normalized_distance <= 1.0 and normalized_distance < min_distance:
                    min_distance = normalized_distance
                    closest_info = point_data[index]
        return closest_info

    def _show_hover_label(self, closest_info: HoverPointInfo) -> None:
        """Show the hover label with point information.

        Parameters
        ----------
        closest_info : HoverPointInfo
            Information about the hovered point.
        """

        time_val = closest_info.get("time", "")
        if "T" in time_val:
            time_val = time_val.split("T")[0]
        elif " " in time_val:
            time_val = time_val.split(" ")[0]
        time_val = time_val[:10] if len(time_val) >= 10 else time_val

        lon = closest_info.get("matched_lon", 0)
        lat = closest_info.get("matched_lat", 0)
        text = (
            f"Point: {closest_info.get('label', '')}\n"
            f"x: {time_val}\n"
            f"y: {closest_info.get('value', 0):.6g}\n"
            f"Location: ({lon:.4f}, {lat:.4f})"
        )
        self._hover_label.setText(text)
        self._hover_label.adjustSize()

        cursor_global_pos = self.cursor().pos()
        plot_top_left = self.mapToGlobal(self.rect().topLeft())
        plot_bottom_right = self.mapToGlobal(self.rect().bottomRight())

        min_x = plot_top_left.x() + 8
        min_y = plot_top_left.y() + 8
        max_x = max(
            min_x,
            plot_bottom_right.x() - self._hover_label.width() - 8,
        )
        max_y = max(
            min_y,
            plot_bottom_right.y() - self._hover_label.height() - 8,
        )
        label_position = QPoint(
            min(max(cursor_global_pos.x() + 12, min_x), max_x),
            min(max(cursor_global_pos.y() + 12, min_y), max_y),
        )
        self._hover_label.move(label_position)
        self._hover_label.show()
        self._hover_label.raise_()

    def leaveEvent(self, event: QEvent) -> None:
        """Hide the hover label when the cursor leaves the plot widget.

        Parameters
        ----------
        event : QEvent
            Qt leave event.
        """

        if self._hover_label is not None:
            self._hover_label.hide()
        super().leaveEvent(event)

    def draw_time_series(
        self,
        time_labels: list[str],
        sampled_series: list[SampleSeries],
        color_lookup: dict[str, str],
        marker_size_lookup: dict[str, float] | None = None,
        start_index: int = 0,
        end_index: int | None = None,
        empty_message: str = "Add one or more points from the map canvas.",
    ) -> None:
        """Render time-series lines for sampled points.

        Parameters
        ----------
        time_labels : list[str]
            Time step labels.
        sampled_series : list[SampleSeries]
            Sampled data series.
        color_lookup : dict[str, str]
            Color lookup by point label.
        marker_size_lookup : dict[str, float] | None, optional
            Marker size lookup by point label.
        start_index : int, optional
            Start index for visible range.
        end_index : int | None, optional
            End index for visible range.
        empty_message : str, optional
            Message to display when no data.
        """

        if self._plot_widget is None:
            return

        self._time_labels = time_labels
        self._series_data = []
        if self._hover_label is not None:
            self._hover_label.hide()
        self._plot_widget.clear()

        bounded_end_index = len(time_labels) - 1 if end_index is None else end_index
        bounded_end_index = max(start_index, bounded_end_index)
        visible_indices = list(range(start_index, bounded_end_index + 1))

        if not sampled_series:
            self._plot_widget.setTitle(f"{self._title}\n{empty_message}")
            return

        self._plot_widget.setTitle(self._title)

        for series in sampled_series:
            y_values = [series.values[index] for index in visible_indices]
            x_values = list(visible_indices)

            color_hex = color_lookup.get(series.point.label, "#1f77b4")
            color = self._parse_color(color_hex)

            marker_size = (
                marker_size_lookup.get(series.point.label, 8.0)
                if marker_size_lookup is not None
                else 8.0
            )

            point_data: list[HoverPointInfo] = []
            for idx in visible_indices:
                point_data.append(
                    {
                        "label": series.point.label,
                        "time": time_labels[idx] if idx < len(time_labels) else "",
                        "value": series.values[idx],
                        "requested_lon": series.point.longitude,
                        "requested_lat": series.point.latitude,
                        "matched_lon": series.matched_longitude,
                        "matched_lat": series.matched_latitude,
                    }
                )

            self._series_data.append(
                {"x": x_values, "y": y_values, "point_data": point_data}
            )

            scatter = pg.ScatterPlotItem(
                x=x_values,
                y=y_values,
                size=marker_size,
                pen=color,
                brush=color,
                pxMode=True,
            )
            self._plot_widget.addItem(scatter)

            self._plot_widget.plotItem.addItem(
                pg.PlotDataItem(
                    x_values,
                    y_values,
                    antialias=True,
                    pen=pg.mkPen(color, width=2),
                    name=series.point.label,
                )
            )

        tick_positions = self._tick_positions(visible_indices)
        tick_labels = [self._format_time_label(time_labels[i]) for i in tick_positions]
        ax = self._plot_widget.getAxis("bottom")
        ax.setTicks([list(zip(tick_positions, tick_labels))])

    def _parse_color(self, color_hex: str) -> tuple[int, int, int, int]:
        """Parse hex color string to RGBA tuple.

        Parameters
        ----------
        color_hex : str
            Hex color string (e.g., "#d73027").

        Returns
        -------
        tuple[int, int, int, int]
            RGBA color tuple.
        """

        if color_hex.startswith("#"):
            color_hex = color_hex[1:]

        r = int(color_hex[0:2], 16)
        g = int(color_hex[2:4], 16)
        b = int(color_hex[4:6], 16)
        return (r, g, b, 255)

    def _format_time_label(self, label: str) -> str:
        """Format time label to show only date (YYYY-MM-DD).

        Parameters
        ----------
        label : str
            Original time label.

        Returns
        -------
        str
            Formatted label with only date.
        """

        if "T" in label:
            return label.split("T")[0]
        if " " in label:
            return label.split(" ")[0]
        return label[:10] if len(label) >= 10 else label

    def _tick_positions(
        self,
        visible_indices: list[int],
        max_ticks: int = 8,
    ) -> list[int]:
        """Return readable x-axis tick positions for a time-series plot.

        Parameters
        ----------
        visible_indices : list[int]
            Indices currently visible in the plot.
        max_ticks : int, optional
            Maximum number of tick labels to display.

        Returns
        -------
        list[int]
            Selected tick positions.
        """

        if len(visible_indices) <= max_ticks:
            return visible_indices

        step = max(1, math.ceil(len(visible_indices) / max_ticks))
        tick_positions = visible_indices[::step]
        if tick_positions[-1] != visible_indices[-1]:
            tick_positions.append(visible_indices[-1])
        return tick_positions
