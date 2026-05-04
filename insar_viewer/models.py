"""Data models used by InSAR Viewer."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import PointShape


@dataclass(frozen=True)
class DimensionSelection:
    """Selected stack and spatial dimensions for a raster variable.

    Attributes
    ----------
    time : str
        Name of the stack dimension. This may represent time or a generic band
        axis depending on the dataset metadata.
    latitude : str
        Name of the latitude dimension.
    longitude : str
        Name of the longitude dimension.
    """

    time: str
    latitude: str
    longitude: str


@dataclass(frozen=True)
class DatasetInspection:
    """Inspection metadata for a source dataset.

    Attributes
    ----------
    variable_names : list[str]
        Available variable names that are potential InSAR cubes.
    variable_dimensions : dict[str, tuple[str, ...]]
        Dimension names per variable.
    suggested_variable : str | None
        Automatically suggested variable when a clear candidate exists.
    suggested_dimensions : DimensionSelection | None
        Automatically detected dimensions for the suggested variable.
    """

    variable_names: list[str]
    variable_dimensions: dict[str, tuple[str, ...]]
    suggested_variable: str | None
    suggested_dimensions: DimensionSelection | None


@dataclass(frozen=True)
class SamplePoint:
    """A named geographic sampling point.

    Attributes
    ----------
    label : str
        Human-readable label shown in the UI.
    longitude : float
        Longitude in degrees.
    latitude : float
        Latitude in degrees.
    """

    label: str
    longitude: float
    latitude: float


@dataclass(frozen=True)
class SampleSeries:
    """Time-series sampled for a point.

    Attributes
    ----------
    point : SamplePoint
        Requested point.
    matched_longitude : float
        Nearest grid-cell longitude used for sampling.
    matched_latitude : float
        Nearest grid-cell latitude used for sampling.
    values : list[float]
        Sampled values for each time step.
    """

    point: SamplePoint
    matched_longitude: float
    matched_latitude: float
    values: list[float]


@dataclass(frozen=True)
class PointStyle:
    """Visual style for a point set shown in the UI and on the canvas.

    Attributes
    ----------
    color_hex : str
        Hex color string such as ``#d73027``.
    size : int
        Marker size in pixels.
    shape : PointShape
        Marker shape name.
    """

    color_hex: str
    size: int
    shape: PointShape


@dataclass(frozen=True)
class LiveProbePlotResult:
    """Background sampling result for the live-probe plot.

    Attributes
    ----------
    probe_point : SamplePoint
        Live probe point used to create the sampling request.
    series_points : list[SamplePoint]
        Series points included in the request, excluding the live probe.
    reference_points : list[SamplePoint]
        Reference points used for baseline adjustment.
    sampled_series : list[SampleSeries]
        Sampled series returned by the dataset.
    """

    probe_point: SamplePoint
    series_points: list[SamplePoint]
    reference_points: list[SamplePoint]
    sampled_series: list[SampleSeries]
