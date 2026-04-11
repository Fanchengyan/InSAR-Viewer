"""Export helpers for InSAR Viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .data_loader import InSARDataset
from .logging import setup_logger
from .models import SamplePoint, SampleSeries

logger = setup_logger(__name__)


def _import_pandas() -> Any:
    """Import pandas lazily."""

    try:
        import pandas as pd
    except ImportError as exc:
        logger.error("pandas is required for tabular export: %s", exc)
        raise RuntimeError("pandas is required for CSV and XLSX export.") from exc
    return pd


def _import_rasterio() -> Any:
    """Import rasterio lazily."""

    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError as exc:
        logger.error("rasterio is required for GeoTIFF export: %s", exc)
        raise RuntimeError("rasterio is required for GeoTIFF export.") from exc
    return rasterio, from_bounds


def export_sampled_series_table(
    destination_path: Path,
    time_labels: list[str],
    sampled_series: list[SampleSeries],
    start_index: int = 0,
    end_index: int | None = None,
) -> None:
    """Export sampled time-series to CSV or XLSX.

    Parameters
    ----------
    destination_path : Path
        Output table path.
    time_labels : list[str]
        Time labels aligned with the series values.
    sampled_series : list[SampleSeries]
        Time-series values to export.
    start_index : int, optional
        Inclusive start index for export.
    end_index : int | None, optional
        Inclusive end index for export. ``None`` exports through the last time step.

    Raises
    ------
    RuntimeError
        Raised when no sample series are available or the format is unsupported.
    """

    if not sampled_series:
        logger.error("No sampled series were provided for export.")
        raise RuntimeError("Add at least one point before exporting.")

    pd = _import_pandas()
    bounded_end_index = len(time_labels) - 1 if end_index is None else end_index
    rows: list[dict[str, float | str]] = []
    for point_series in sampled_series:
        for index in range(start_index, bounded_end_index + 1):
            rows.append(
                {
                    "point_label": point_series.point.label,
                    "requested_longitude": point_series.point.longitude,
                    "requested_latitude": point_series.point.latitude,
                    "matched_longitude": point_series.matched_longitude,
                    "matched_latitude": point_series.matched_latitude,
                    "time": time_labels[index],
                    "value": point_series.values[index],
                }
            )

    frame = pd.DataFrame(rows)
    suffix = destination_path.suffix.lower()
    if suffix == ".csv":
        frame.to_csv(destination_path, index=False)
        return
    if suffix == ".xlsx":
        frame.to_excel(destination_path, index=False)
        return

    logger.error("Unsupported tabular export format: %s", destination_path)
    raise RuntimeError("Only .csv and .xlsx exports are supported.")


def export_array_geotiff(
    destination_path: Path,
    longitudes: np.ndarray,
    latitudes: np.ndarray,
    values: np.ndarray,
    crs_wkt: str | None = "EPSG:4326",
) -> None:
    """Export a 2D longitude/latitude array to GeoTIFF.

    Parameters
    ----------
    destination_path : Path
        Output GeoTIFF path.
    longitudes : np.ndarray
        X coordinates.
    latitudes : np.ndarray
        Y coordinates.
    values : np.ndarray
        Raster cell values.
    crs_wkt : str | None, optional
        Output CRS string.
    """

    rasterio, from_bounds = _import_rasterio()
    if longitudes.size < 2 or latitudes.size < 2:
        logger.error(
            (
                "GeoTIFF export requires at least a 2x2 grid, got %s longitudes "
                "and %s latitudes."
            ),
            longitudes.size,
            latitudes.size,
        )
        raise RuntimeError("GeoTIFF export requires at least a 2x2 spatial grid.")

    west = float(np.min(longitudes))
    east = float(np.max(longitudes))
    south = float(np.min(latitudes))
    north = float(np.max(latitudes))
    raster_values = np.asarray(values, dtype=np.float32)
    transform = from_bounds(
        west=west,
        south=south,
        east=east,
        north=north,
        width=raster_values.shape[1],
        height=raster_values.shape[0],
    )

    with rasterio.open(
        destination_path,
        mode="w",
        driver="GTiff",
        height=raster_values.shape[0],
        width=raster_values.shape[1],
        count=1,
        dtype=raster_values.dtype,
        crs=crs_wkt,
        transform=transform,
        nodata=np.nan,
    ) as output_raster:
        output_raster.write(raster_values, 1)


def export_spatial_slice_geotiff(
    dataset: InSARDataset,
    destination_path: Path,
    time_index: int,
    reference_points: list[SamplePoint],
) -> None:
    """Export the current time-step slice to GeoTIFF."""

    longitudes, latitudes, values = dataset.spatial_slice(time_index, reference_points)
    export_array_geotiff(
        destination_path,
        longitudes,
        latitudes,
        values,
        crs_wkt=dataset.crs_wkt,
    )
