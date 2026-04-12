"""PyQtGraph time-series plotting widgets used by the plugin UI."""

from __future__ import annotations

import math
from typing import Any, Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from .logging import setup_logger
from .models import SampleSeries

logger = setup_logger(__name__)

pg: Any = None


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
            "background-color: rgba(255, 255, 255, 220);"
            "border: 1px solid #888;"
            "padding: 4px;"
            "border-radius: 3px;"
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
        plot_widget.showGrid(x=True, y=True, alpha=0.3)
        plot_widget.setLabel("left", "Value")
        plot_widget.setLabel("bottom", "Time")
        plot_widget.setTitle(self._title)
        plot_widget.addLegend()
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

        if hasattr(pos, "x"):
            x_pos = pos.x()
            y_pos = pos.y()
        else:
            return

        closest_info: dict[str, Any] | None = None
        min_distance = float("inf")

        for series_item in self._series_data:
            x_values = series_item.get("x", [])
            y_values = series_item.get("y", [])
            point_data = series_item.get("point_data", [])

            for i in range(len(x_values)):
                distance = abs(x_values[i] - x_pos) + abs(y_values[i] - y_pos)
                if distance < min_distance and distance < 0.5:
                    min_distance = distance
                    if i < len(point_data):
                        closest_info = point_data[i]

        if closest_info is not None:
            self._show_hover_label(closest_info)
        else:
            self._hover_label.hide()

    def _on_scatter_hovered(self, scatter_item: Any, spot: Any) -> None:
        """Handle scatter plot item hover event.

        Parameters
        ----------
        scatter_item : pg.ScatterPlotItem
            The scatter plot item that triggered the event.
        :param spot: The spot that is hovered.
        """

        if spot is None:
            self._hover_label.hide()
            return

        point_info = spot.data()
        if point_info is not None:
            self._show_hover_label(point_info)
        else:
            self._hover_label.hide()

    def _show_hover_label(self, closest_info: dict[str, Any]) -> None:
        """Show the hover label with point information.

        Parameters
        ----------
        closest_info : dict[str, Any]
            Information about the hovered point.
        """

        text = (
            f"Point: {closest_info.get('label', '')}\n"
            f"Time: {closest_info.get('time', '')}\n"
            f"Value: {closest_info.get('value', 0):.6g}\n"
            f"Requested Lon: {closest_info.get('requested_lon', 0):.6f}\n"
            f"Requested Lat: {closest_info.get('requested_lat', 0):.6f}\n"
            f"Matched Lon: {closest_info.get('matched_lon', 0):.6f}\n"
            f"Matched Lat: {closest_info.get('matched_lat', 0):.6f}"
        )
        self._hover_label.setText(text)
        self._hover_label.adjustSize()

        cursor_pos = self.mapFromGlobal(self.cursor().pos())
        self._hover_label.move(cursor_pos.x() + 15, cursor_pos.y() + 15)
        self._hover_label.show()

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

            point_data = []
            for i, idx in enumerate(visible_indices):
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

            spots = []
            for i in range(len(x_values)):
                spots.append(
                    pg.ScatterPlotItem.Spot(
                        x=x_values[i],
                        y=y_values[i],
                        size=marker_size,
                        pen=color,
                        brush=color,
                        data=point_data[i],
                    )
                )

            scatter = pg.ScatterPlotItem(spots=spots, pxMode=True)
            scatter.sigHovered.connect(self._on_scatter_hovered)
            self._plot_widget.addItem(scatter)

            self._plot_widget.plotItem.addItem(
                pg.PlotDataItem(
                    x_values,
                    y_values,
                    pen=pg.mkPen(color, width=2),
                    name=series.point.label,
                )
            )

        tick_positions = self._tick_positions(visible_indices)
        tick_labels = [time_labels[i] for i in tick_positions]
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
