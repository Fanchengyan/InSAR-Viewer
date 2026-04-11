"""Matplotlib time-series plotting widgets used by the plugin UI."""

from __future__ import annotations

import math
from typing import Any

from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from .logging import setup_logger
from .models import SampleSeries

logger = setup_logger(__name__)


def _import_matplotlib() -> tuple[type[QWidget], Any]:
    """Import matplotlib Qt canvas classes lazily.

    Returns
    -------
    tuple[type[QWidget], Any]
        FigureCanvasQTAgg and Figure.

    Raises
    ------
    RuntimeError
        Raised when matplotlib is unavailable.
    """

    try:
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
    except ImportError as exc:
        logger.error("matplotlib is required for plotting: %s", exc)
        raise RuntimeError("matplotlib is required for plotting.") from exc
    return FigureCanvasQTAgg, Figure


class TimeSeriesPlotWidget(QWidget):
    """Reusable matplotlib time-series widget with pan and zoom support."""

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
        self._fallback_label = QLabel("Install matplotlib to enable plotting.", self)
        self._layout.addWidget(self._fallback_label)
        self._canvas: QWidget | None = None
        self._axes: Any | None = None
        self._figure: Any | None = None
        self._toolbar: QWidget | None = None
        self._setup_canvas()

    def _setup_canvas(self) -> None:
        """Initialize the matplotlib canvas when available."""

        try:
            figure_canvas_class, figure_class = _import_matplotlib()
            from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
        except RuntimeError:
            return

        self._layout.removeWidget(self._fallback_label)
        self._fallback_label.deleteLater()
        self._figure = figure_class(figsize=(3.8, 3.2))
        self._axes = self._figure.add_subplot(111)
        self._axes.set_title(self._title)
        canvas = figure_canvas_class(self._figure)
        self._canvas = canvas
        self._toolbar = NavigationToolbar2QT(canvas, self)
        self._layout.addWidget(self._toolbar)
        self._layout.addWidget(canvas)

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
        """Render time-series lines for sampled points."""

        if self._axes is None or self._figure is None or self._canvas is None:
            return

        if not time_labels:
            sampled_series = []

        bounded_end_index = len(time_labels) - 1 if end_index is None else end_index
        bounded_end_index = max(start_index, bounded_end_index)
        visible_indices = (
            list(range(start_index, bounded_end_index + 1)) if time_labels else []
        )

        self._figure.clear()
        axes = self._figure.add_subplot(111)
        axes.set_title(self._title)
        axes.set_xlabel("Time")
        axes.set_ylabel("Value")
        if not sampled_series:
            axes.text(
                0.5,
                0.5,
                empty_message,
                ha="center",
                va="center",
                transform=axes.transAxes,
            )

        visible_time_labels = [time_labels[index] for index in visible_indices]
        date_axis_values = self._date_axis_values(visible_time_labels)
        x_values: list[int] | list[float]
        if date_axis_values is not None:
            x_values = date_axis_values
        else:
            x_values = visible_indices

        for series in sampled_series:
            axes.plot(
                x_values,
                [series.values[index] for index in visible_indices],
                marker="o",
                linewidth=1.5,
                markersize=(
                    marker_size_lookup.get(series.point.label, 4.0)
                    if marker_size_lookup is not None
                    else 4.0
                ),
                label=series.point.label,
                color=color_lookup.get(series.point.label),
            )
        if date_axis_values is not None:
            self._configure_date_axis(axes)
        else:
            tick_positions = self._tick_positions(visible_indices)
            axes.set_xticks(tick_positions)
            axes.set_xticklabels(
                [time_labels[index] for index in tick_positions],
                rotation=45,
                ha="right",
            )
        axes.grid(True, which="major", color="#c7c7c7", alpha=0.6, linewidth=0.7)
        if sampled_series:
            axes.legend(loc="best")
        self._figure.tight_layout()
        self._canvas.draw()

    def _configure_date_axis(self, axes: Any) -> None:
        """Configure a datetime x-axis with yearly major ticks and monthly minors."""

        from matplotlib import dates as mdates
        from matplotlib.ticker import NullFormatter

        axes.xaxis_date()
        axes.xaxis.set_major_locator(mdates.YearLocator())
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        axes.xaxis.set_minor_locator(mdates.MonthLocator())
        axes.xaxis.set_minor_formatter(NullFormatter())
        axes.fmt_xdata = mdates.DateFormatter("%Y-%m-%d")
        axes.format_coord = self._build_date_coordinate_formatter(axes)
        axes.tick_params(axis="x", which="major", labelsize=9, pad=14)
        axes.tick_params(axis="x", which="minor", length=3)

    def _build_date_coordinate_formatter(self, axes: Any) -> Any:
        """Return a coordinate formatter that renders the x-value as a date.

        Parameters
        ----------
        axes : Any
            Matplotlib axes instance for the current plot.

        Returns
        -------
        Any
            Callable used by Matplotlib to format hover coordinates.
        """

        from matplotlib import dates as mdates

        def _format_coordinates(x_value: float, y_value: float) -> str:
            """Format plot coordinates for the Matplotlib status display.

            Parameters
            ----------
            x_value : float
                X-axis value encoded as a Matplotlib date number.
            y_value : float
                Y-axis value of the plotted series.

            Returns
            -------
            str
                Coordinate text with a calendar date and numeric y-value.
            """

            if not axes.get_navigate():
                return ""
            date_label = mdates.num2date(x_value).strftime("%Y-%m-%d")
            return f"x={date_label}, y={y_value:0.6g}"

        return _format_coordinates

    def _date_axis_values(self, time_labels: list[str]) -> list[float] | None:
        """Convert time-label strings into Matplotlib date numbers when possible.

        Parameters
        ----------
        time_labels : list[str]
            Visible time labels.

        Returns
        -------
        list[float] | None
            Parsed date values for Matplotlib, or ``None`` when parsing fails.
        """

        if not time_labels:
            return None

        try:
            from matplotlib import dates as mdates

            return [mdates.datestr2num(label) for label in time_labels]
        except Exception:
            return None

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
        if not visible_indices:
            return []

        step = max(1, math.ceil(len(visible_indices) / max_ticks))
        tick_positions = visible_indices[::step]
        if tick_positions[-1] != visible_indices[-1]:
            tick_positions.append(visible_indices[-1])
        return tick_positions
