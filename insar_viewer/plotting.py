"""PyQtGraph time-series plotting widgets used by the plugin UI."""

from __future__ import annotations

import math
from datetime import datetime
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
    x_position: float
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
        self._hover_vertical_line: Any = None
        self._hover_horizontal_line: Any = None
        self._hover_x_value_label: Any = None
        self._hover_y_value_label: Any = None
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
        self._create_hover_guides()

        plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def _create_hover_guides(self) -> None:
        """Create hidden crosshair guides used for point hover feedback."""

        if self._plot_widget is None:
            return

        guide_pen = pg.mkPen("#ff0000", width=1)
        self._hover_vertical_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=guide_pen,
        )
        self._hover_horizontal_line = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=guide_pen,
        )
        text_border = pg.mkPen("#4b5563", width=1)
        text_fill = pg.mkBrush(255, 255, 255, 220)
        self._hover_x_value_label = pg.TextItem(
            anchor=(0.5, 1.0),
            color="#111827",
            border=text_border,
            fill=text_fill,
        )
        self._hover_y_value_label = pg.TextItem(
            anchor=(0.0, 0.5),
            color="#111827",
            border=text_border,
            fill=text_fill,
        )
        self._plot_widget.addItem(self._hover_vertical_line, ignoreBounds=True)
        self._plot_widget.addItem(self._hover_horizontal_line, ignoreBounds=True)
        self._plot_widget.addItem(self._hover_x_value_label, ignoreBounds=True)
        self._plot_widget.addItem(self._hover_y_value_label, ignoreBounds=True)
        self._hide_hover_guides()

    def _hide_hover_guides(self) -> None:
        """Hide the hover crosshair guides."""

        if self._hover_vertical_line is not None:
            self._hover_vertical_line.hide()
        if self._hover_horizontal_line is not None:
            self._hover_horizontal_line.hide()
        if self._hover_x_value_label is not None:
            self._hover_x_value_label.hide()
        if self._hover_y_value_label is not None:
            self._hover_y_value_label.hide()

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

        scene_position = QPointF(pos.x(), pos.y())
        if not self._plot_widget.sceneBoundingRect().contains(scene_position):
            self._hover_label.hide()
            self._hide_hover_guides()
            return

        selected_info = self._find_selected_point_info(scene_position)
        if selected_info is None:
            self._hover_label.hide()
            self._hide_hover_guides()
            return

        self._show_hover_guides(scene_position, selected_info)
        closest_info = self._find_hover_point_info(scene_position, selected_info)

        if closest_info is not None:
            self._show_hover_label(closest_info)
        else:
            self._hover_label.hide()

    def _find_selected_point_info(
        self,
        scene_position: QPointF,
    ) -> HoverPointInfo | None:
        """Return the point selected by the cursor x-position.

        Parameters
        ----------
        scene_position : QPointF
            Cursor position in scene coordinates.

        Returns
        -------
        HoverPointInfo | None
            Point metadata for the series sample nearest to the cursor x-position.
        """

        if self._plot_widget is None:
            return None

        scene_rect = self._plot_widget.sceneBoundingRect()
        if not scene_rect.contains(scene_position):
            return None

        view_box = self._plot_widget.plotItem.vb
        cursor_view_position = view_box.mapSceneToView(scene_position)

        closest_info: HoverPointInfo | None = None
        min_x_distance = float("inf")
        min_y_distance = float("inf")
        for series_item in self._series_data:
            x_values = series_item.get("x", [])
            y_values = series_item.get("y", [])
            point_data = series_item.get("point_data", [])

            for index, (x_value, y_value) in enumerate(zip(x_values, y_values)):
                if not math.isfinite(y_value) or index >= len(point_data):
                    continue

                x_distance = abs(x_value - cursor_view_position.x())
                y_distance = abs(y_value - cursor_view_position.y())
                if x_distance < min_x_distance or (
                    math.isclose(x_distance, min_x_distance)
                    and y_distance < min_y_distance
                ):
                    min_x_distance = x_distance
                    min_y_distance = y_distance
                    closest_info = point_data[index]
        return closest_info

    def _find_hover_point_info(
        self,
        scene_position: QPointF,
        selected_info: HoverPointInfo,
    ) -> HoverPointInfo | None:
        """Return hover metadata only when the cursor is near the selected point.

        Parameters
        ----------
        scene_position : QPointF
            Cursor position in scene coordinates.
        selected_info : HoverPointInfo
            Point selected by cursor x-position.

        Returns
        -------
        HoverPointInfo | None
            Selected point metadata when the cursor is close enough to that point.
        """

        if self._plot_widget is None:
            return None

        view_box = self._plot_widget.plotItem.vb
        point_scene_position = view_box.mapViewToScene(
            QPointF(selected_info["x_position"], selected_info["value"])
        )
        pixel_distance = math.hypot(
            scene_position.x() - point_scene_position.x(),
            scene_position.y() - point_scene_position.y(),
        )
        return selected_info if pixel_distance <= 12.0 else None

    def _show_hover_guides(
        self,
        scene_position: QPointF,
        selected_info: HoverPointInfo,
    ) -> None:
        """Show crosshair guides and axis value labels for the selected point."""

        if (
            self._plot_widget is None
            or self._hover_vertical_line is None
            or self._hover_horizontal_line is None
            or self._hover_x_value_label is None
            or self._hover_y_value_label is None
        ):
            return

        x_axis_position = float(selected_info["x_position"])
        y_axis_position = float(selected_info["value"])
        self._hover_vertical_line.setPos(x_axis_position)
        self._hover_horizontal_line.setPos(y_axis_position)
        self._hover_vertical_line.show()
        self._hover_horizontal_line.show()

        x_range, y_range = self._plot_widget.plotItem.vb.viewRange()
        x_offset, y_offset = self._view_offset_for_pixels(6.0, 6.0)

        x_label_text = self._format_x_value_label(selected_info)
        y_label_text = f"Y : {selected_info['value']:.6g}"
        self._hover_x_value_label.setText(x_label_text)
        self._hover_x_value_label.setPos(x_axis_position, y_range[0] + y_offset)
        self._hover_x_value_label.show()

        self._hover_y_value_label.setText(y_label_text)
        self._hover_y_value_label.setPos(x_range[0] + x_offset, y_axis_position)
        self._hover_y_value_label.show()

    def _view_offset_for_pixels(
        self,
        x_pixels: float,
        y_pixels: float,
    ) -> tuple[float, float]:
        """Convert pixel offsets to view-coordinate offsets.

        Parameters
        ----------
        x_pixels : float
            Horizontal offset in scene pixels.
        y_pixels : float
            Vertical offset in scene pixels.

        Returns
        -------
        tuple[float, float]
            Horizontal and vertical offsets in view coordinates.
        """

        if self._plot_widget is None:
            return (0.0, 0.0)

        view_box = self._plot_widget.plotItem.vb
        scene_rect = view_box.sceneBoundingRect()
        top_left_view = view_box.mapSceneToView(scene_rect.topLeft())
        offset_view = view_box.mapSceneToView(
            QPointF(scene_rect.left() + x_pixels, scene_rect.top() + y_pixels)
        )
        return (
            abs(offset_view.x() - top_left_view.x()),
            abs(offset_view.y() - top_left_view.y()),
        )

    def _format_x_value_label(self, point_info: HoverPointInfo) -> str:
        """Format the x-axis value label for the selected point.

        Parameters
        ----------
        point_info : HoverPointInfo
            Selected point metadata.

        Returns
        -------
        str
            Formatted x-axis value label.
        """

        time_label = self._format_time_label(point_info["time"])
        if time_label:
            return f"X : {time_label}"
        return f"X : {point_info['x_position']:.6g}"

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
        self._hide_hover_guides()
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
        self._hide_hover_guides()
        self._plot_widget.clear()
        self._create_hover_guides()

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
                        "x_position": idx,
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
        minor_tick_positions = self._month_tick_positions(visible_indices, time_labels)
        ax = self._plot_widget.getAxis("bottom")
        ax.setTicks(
            [
                list(zip(tick_positions, tick_labels)),
                [(position, "") for position in minor_tick_positions],
            ]
        )

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

    def _month_tick_positions(
        self,
        visible_indices: list[int],
        time_labels: list[str],
    ) -> list[int]:
        """Return month boundary positions for x-axis minor ticks.

        Parameters
        ----------
        visible_indices : list[int]
            Indices currently visible in the plot.
        time_labels : list[str]
            Time step labels for the full time series.

        Returns
        -------
        list[int]
            Positions where a new month starts within the visible range.
        """

        month_tick_positions: list[int] = []
        previous_month_key: tuple[int, int] | None = None

        for index in visible_indices:
            if index >= len(time_labels):
                continue

            parsed_date = self._parse_time_label_to_datetime(time_labels[index])
            if parsed_date is None:
                continue

            month_key = (parsed_date.year, parsed_date.month)
            if month_key != previous_month_key:
                month_tick_positions.append(index)
                previous_month_key = month_key

        return month_tick_positions

    def _parse_time_label_to_datetime(self, label: str) -> datetime | None:
        """Parse a time label into a datetime when possible.

        Parameters
        ----------
        label : str
            Original time label.

        Returns
        -------
        datetime | None
            Parsed datetime or ``None`` when the label format is unsupported.
        """

        normalized_label = label.strip()
        if not normalized_label:
            return None

        if normalized_label.endswith("Z"):
            normalized_label = normalized_label[:-1] + "+00:00"

        iso_candidate = normalized_label.replace(" ", "T", 1)
        try:
            return datetime.fromisoformat(iso_candidate)
        except ValueError:
            pass

        date_prefix = self._format_time_label(normalized_label)
        try:
            return datetime.strptime(date_prefix, "%Y-%m-%d")
        except ValueError:
            logger.debug("Unsupported time label for month ticks: %s", label)
            return None
