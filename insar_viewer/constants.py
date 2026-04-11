"""Shared constants and type aliases for InSAR Viewer."""

from __future__ import annotations

from typing import Literal

DimensionRole = Literal["time", "latitude", "longitude"]
ExportFormat = Literal["csv", "xlsx", "geotiff"]
CaptureMode = Literal["reference", "series", "date_probe"]
PointShape = Literal["circle", "square", "cross", "x"]
RenderMode = Literal["continuous", "segmented"]

SUPPORTED_DATA_SUFFIXES: tuple[str, ...] = (
    ".nc",
    ".nc4",
    ".cdf",
    ".h5",
    ".hdf5",
    ".he5",
)

TIME_DIMENSION_ALIASES: tuple[str, ...] = (
    "band",
    "bands",
    "date",
    "dates",
    "time",
    "times",
    "t",
    "epoch",
    "acquisition_date",
)
LATITUDE_DIMENSION_ALIASES: tuple[str, ...] = (
    "lat",
    "latitude",
    "y",
    "ylat",
)
LONGITUDE_DIMENSION_ALIASES: tuple[str, ...] = (
    "lon",
    "lng",
    "long",
    "longitude",
    "x",
    "xlon",
)

POINT_SHAPE_NAMES: tuple[str, ...] = ("circle", "square", "cross", "x")
DEFAULT_REFERENCE_COLOR = "#d73027"
DEFAULT_SERIES_COLOR = "#2c7fb8"
DEFAULT_DATE_PROBE_COLOR = "#1a9850"
SERIES_POINT_PALETTE: tuple[str, ...] = (
    "#2c7fb8",
    "#d95f0e",
    "#1b9e77",
    "#7570b3",
    "#e7298a",
    "#66a61e",
    "#e6ab02",
    "#a6761d",
    "#666666",
)
DEFAULT_REFERENCE_SHAPE: PointShape = "circle"
DEFAULT_SERIES_SHAPE: PointShape = "square"
DEFAULT_DATE_PROBE_SHAPE: PointShape = "cross"
DEFAULT_REFERENCE_SIZE = 12
DEFAULT_SERIES_SIZE = 7
DEFAULT_DATE_PROBE_SIZE = 8
DEFAULT_RENDER_MODE: RenderMode = "continuous"
