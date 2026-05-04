"""Dataset loading and preprocessing for InSAR Viewer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .constants import (
    LATITUDE_DIMENSION_ALIASES,
    LONGITUDE_DIMENSION_ALIASES,
    SUPPORTED_DATA_SUFFIXES,
    TIME_DIMENSION_ALIASES,
)
from .logging import setup_logger
from .models import DatasetInspection, DimensionSelection, SamplePoint, SampleSeries
from .runtime_environment import (
    configure_native_data_paths,
    ensure_module_origin_in_active_prefix,
    prefer_active_prefix_imports,
)

if TYPE_CHECKING:
    import xarray as xr

configure_native_data_paths()
logger = setup_logger(__name__)


def _import_xarray() -> Any:
    """Import xarray lazily.

    Returns
    -------
    Any
        Imported :mod:`xarray` module.

    Raises
    ------
    RuntimeError
        Raised when xarray is unavailable.
    """

    try:
        with prefer_active_prefix_imports(("xarray", "cftime")):
            import xarray as xr
    except ImportError as exc:
        logger.error("xarray is required to load InSAR datasets: %s", exc)
        raise RuntimeError("xarray is required to load InSAR datasets.") from exc
    return xr


def _import_rioxarray() -> Any:
    """Import rioxarray lazily.

    Returns
    -------
    Any
        Imported :mod:`rioxarray` module.

    Raises
    ------
    RuntimeError
        Raised when rioxarray is unavailable.
    """

    try:
        with prefer_active_prefix_imports(
            ("pyproj", "rasterio", "rioxarray", "xarray", "cftime")
        ):
            import pyproj
            import rasterio
            import rioxarray
    except ImportError as exc:
        logger.error("rioxarray is required to load raster datasets: %s", exc)
        raise RuntimeError(
            "rioxarray or one of its dependencies could not be imported: "
            f"{exc}. Open the Dependencies tab, refresh the status, and reinstall "
            "items marked as missing or broken."
        ) from exc
    ensure_module_origin_in_active_prefix("pyproj", pyproj)
    ensure_module_origin_in_active_prefix("rasterio", rasterio)
    return rioxarray


def _open_source_dataset(
    dataset_path: Path,
    variable_name: str | None = None,
) -> Any:
    """Open an input dataset with :func:`rioxarray.open_rasterio`.

    Parameters
    ----------
    dataset_path : Path
        Source dataset path.
    variable_name : str | None, optional
        Optional NetCDF/HDF variable name to open.

    Returns
    -------
    Any
        Opened xarray dataset or data array backed by lazy chunks.

    Raises
    ------
    FileNotFoundError
        Raised when the file does not exist.
    RuntimeError
        Raised when the dataset format cannot be opened.
    """

    if not dataset_path.exists():
        logger.error("Dataset file does not exist: %s", dataset_path)
        raise FileNotFoundError(f"Dataset file does not exist: {dataset_path}")

    suffix = dataset_path.suffix.lower()
    if suffix not in SUPPORTED_DATA_SUFFIXES:
        logger.warning(
            "Unsupported suffix %s detected, trying rioxarray.open_rasterio.",
            suffix,
        )

    rioxarray = _import_rioxarray()
    open_kwargs: dict[str, Any] = {
        "chunks": {},
        "masked": True,
    }
    if variable_name:
        open_kwargs["variable"] = variable_name
    try:
        return rioxarray.open_rasterio(dataset_path, **open_kwargs)
    except Exception as exc:
        if _should_retry_without_time_decoding(exc):
            logger.warning(
                "Failed to decode temporal metadata for %s with rioxarray: %s. "
                "Retrying with decode_times=False.",
                dataset_path,
                exc,
            )
            retry_kwargs = dict(open_kwargs)
            retry_kwargs["decode_times"] = False
            try:
                return rioxarray.open_rasterio(dataset_path, **retry_kwargs)
            except Exception as retry_exc:
                logger.error(
                    "Failed to open dataset %s with rioxarray.open_rasterio after "
                    "disabling time decoding: %s",
                    dataset_path,
                    retry_exc,
                )
                raise RuntimeError(
                    "Unable to open the dataset with rioxarray.open_rasterio after "
                    f"retrying with decode_times=False: {retry_exc}"
                ) from retry_exc
        logger.error(
            "Failed to open dataset %s with rioxarray.open_rasterio: %s",
            dataset_path,
            exc,
        )
        raise RuntimeError(
            "Unable to open the dataset with rioxarray.open_rasterio: "
            f"{exc}. Ensure the required runtime dependencies are installed in "
            "QGIS or the InSAR Viewer managed dependency directory."
        ) from exc


def _should_retry_without_time_decoding(error: Exception) -> bool:
    """Return whether opening should be retried with ``decode_times=False``.

    Parameters
    ----------
    error : Exception
        Exception raised while opening the dataset with rioxarray.

    Returns
    -------
    bool
        ``True`` when the failure indicates CF time decoding is unsupported in
        the active environment.
    """

    error_text = str(error)
    lowered_error_text = error_text.casefold()
    return (
        "unable to decode time units" in lowered_error_text
        or "cftime package is required" in lowered_error_text
        or "non-standard calendars" in lowered_error_text
    )


def _detect_dimension_name(
    available_dimensions: tuple[str, ...],
    aliases: tuple[str, ...],
) -> str | None:
    """Detect a likely dimension name from aliases.

    Parameters
    ----------
    available_dimensions : tuple[str, ...]
        Dimensions exposed by a variable.
    aliases : tuple[str, ...]
        Common aliases for a semantic role.

    Returns
    -------
    str | None
        Matched dimension name or ``None`` when no alias matches.
    """

    alias_lookup = {alias.casefold(): alias for alias in aliases}
    for dimension_name in available_dimensions:
        normalized_name = dimension_name.casefold()
        if normalized_name in alias_lookup:
            return dimension_name
    return None


def suggest_dimensions(
    variable_dimensions: tuple[str, ...],
) -> DimensionSelection | None:
    """Suggest dimension mapping for a variable.

    Parameters
    ----------
    variable_dimensions : tuple[str, ...]
        Variable dimensions.

    Returns
    -------
    DimensionSelection | None
        Suggested dimensions or ``None`` when auto-detection is incomplete.
    """

    time_name = _detect_dimension_name(variable_dimensions, TIME_DIMENSION_ALIASES)
    latitude_name = _detect_dimension_name(
        variable_dimensions,
        LATITUDE_DIMENSION_ALIASES,
    )
    longitude_name = _detect_dimension_name(
        variable_dimensions,
        LONGITUDE_DIMENSION_ALIASES,
    )
    if not latitude_name or not longitude_name:
        return None
    if not time_name:
        remaining_dimensions = [
            dimension_name
            for dimension_name in variable_dimensions
            if dimension_name not in {latitude_name, longitude_name}
        ]
        if len(remaining_dimensions) != 1:
            return None
        time_name = remaining_dimensions[0]
    return DimensionSelection(
        time=time_name,
        latitude=latitude_name,
        longitude=longitude_name,
    )


def _build_default_axis_coordinate(size: int) -> np.ndarray:
    """Return a 1-based default coordinate for a stack dimension.

    Parameters
    ----------
    size : int
        Axis length.

    Returns
    -------
    np.ndarray
        Generated coordinate values.
    """

    return np.arange(1, size + 1, dtype=int)


def _coordinate_values_are_temporal(coordinate_values: np.ndarray) -> bool:
    """Return whether a coordinate array looks temporal.

    Parameters
    ----------
    coordinate_values : np.ndarray
        Coordinate values for the stack dimension.

    Returns
    -------
    bool
        ``True`` when the values appear to describe dates or datetimes.
    """

    flattened_values = np.asarray(coordinate_values).reshape(-1)
    if flattened_values.size == 0:
        return False

    sample_size = min(5, flattened_values.size)
    temporal_matches = 0
    for value in flattened_values[:sample_size]:
        normalized_value = str(value).strip()
        if not normalized_value:
            continue
        if (
            "T" in normalized_value
            or "-" in normalized_value
            or "/" in normalized_value
        ):
            temporal_matches += 1
            continue
        if len(normalized_value) >= 8 and normalized_value[:8].isdigit():
            temporal_matches += 1

    return temporal_matches > 0


def inspect_dataset(dataset_path: Path) -> DatasetInspection:
    """Inspect an input dataset and discover variables and dimensions.

    Parameters
    ----------
    dataset_path : Path
        Dataset location.

    Returns
    -------
    DatasetInspection
        Inspection summary used to populate the UI.

    Raises
    ------
    RuntimeError
        Raised when no suitable numeric variables are found.
    """

    source = _open_source_dataset(dataset_path)

    if hasattr(source, "data_vars"):
        candidate_variables = {
            name: tuple(variable.dims)
            for name, variable in source.data_vars.items()
            if len(variable.dims) >= 3 and np.issubdtype(variable.dtype, np.number)
        }
    else:
        data_array = source
        candidate_name = data_array.name or "displacement"
        candidate_variables = {
            candidate_name: tuple(data_array.dims),
        }

    if not candidate_variables:
        logger.error("No numeric 3D variables found in dataset %s.", dataset_path)
        raise RuntimeError(
            "No numeric variable with at least three dimensions was found."
        )

    suggested_variable: str | None = None
    suggested_dimensions: DimensionSelection | None = None
    for variable_name, variable_dimensions in candidate_variables.items():
        detected_dimensions = suggest_dimensions(variable_dimensions)
        if detected_dimensions is not None:
            suggested_variable = variable_name
            suggested_dimensions = detected_dimensions
            break

    if suggested_variable is None:
        suggested_variable = next(iter(candidate_variables.keys()))

    return DatasetInspection(
        variable_names=list(candidate_variables.keys()),
        variable_dimensions=candidate_variables,
        suggested_variable=suggested_variable,
        suggested_dimensions=suggested_dimensions,
    )


@dataclass
class InSARDataset:
    """Normalized InSAR dataset ready for visualization and export.

    Attributes
    ----------
    source_path : Path
        Original dataset path.
    variable_name : str
        Selected variable name.
    dimensions : DimensionSelection
        Active dimension mapping.
    data_array : xr.DataArray
        Normalized data array with dimensions ``time``, ``latitude``, and ``longitude``.
    crs_wkt : str | None
        CRS WKT or authority string discovered from ``rio.crs`` when available.
    is_geographic : bool
        Whether the detected CRS is geographic.
    is_temporal : bool
        Whether the stack dimension appears to represent time.
    """

    source_path: Path
    variable_name: str
    dimensions: DimensionSelection
    data_array: xr.DataArray
    crs_wkt: str | None
    is_geographic: bool
    is_temporal: bool

    @classmethod
    def load(
        cls,
        dataset_path: Path,
        variable_name: str,
        dimensions: DimensionSelection,
    ) -> InSARDataset:
        """Load and normalize an InSAR variable.

        Parameters
        ----------
        dataset_path : Path
            Dataset location.
        variable_name : str
            Selected data variable.
        dimensions : DimensionSelection
            Dimension mapping chosen by the user or auto-detection.

        Returns
        -------
        InSARDataset
            Normalized dataset object.

        Raises
        ------
        RuntimeError
            Raised when the variable is missing or has unsupported coordinates.
        """

        try:
            source = _open_source_dataset(dataset_path, variable_name=variable_name)
        except RuntimeError:
            source = _open_source_dataset(dataset_path)

        if hasattr(source, "data_vars"):
            if variable_name not in source.data_vars:
                logger.error(
                    "Variable %s not found in dataset %s.",
                    variable_name,
                    dataset_path,
                )
                raise RuntimeError(f"Variable '{variable_name}' was not found.")
            data_array = source[variable_name]
        else:
            data_array = source

        expected_dimensions = {
            dimensions.time,
            dimensions.latitude,
            dimensions.longitude,
        }
        if not expected_dimensions.issubset(set(data_array.dims)):
            logger.error(
                "Dimension selection %s does not match variable dims %s.",
                dimensions,
                data_array.dims,
            )
            raise RuntimeError(
                "Selected dimensions do not match the variable dimensions."
            )

        latitude_coordinate = data_array.coords.get(dimensions.latitude)
        longitude_coordinate = data_array.coords.get(dimensions.longitude)
        time_coordinate = data_array.coords.get(dimensions.time)
        if latitude_coordinate is None or longitude_coordinate is None:
            logger.error(
                "Variable %s is missing latitude/longitude coordinates.", variable_name
            )
            raise RuntimeError("Latitude and longitude coordinates are required.")
        if latitude_coordinate.ndim != 1 or longitude_coordinate.ndim != 1:
            logger.error(
                "Only 1D latitude/longitude coordinates are supported, got %s and %s.",
                latitude_coordinate.ndim,
                longitude_coordinate.ndim,
            )
            raise RuntimeError(
                "Only 1D latitude and longitude coordinates are currently supported."
            )

        if time_coordinate is None:
            stack_size = int(data_array.sizes[dimensions.time])
            data_array = data_array.assign_coords(
                {dimensions.time: _build_default_axis_coordinate(stack_size)}
            )
            time_coordinate = data_array.coords.get(dimensions.time)
        is_temporal = (
            False
            if time_coordinate is None
            else _coordinate_values_are_temporal(np.asarray(time_coordinate.values))
        )

        renamed = data_array.rename(
            {
                dimensions.time: "time",
                dimensions.latitude: "latitude",
                dimensions.longitude: "longitude",
            }
        )
        normalized = renamed.transpose("time", "latitude", "longitude")
        crs_wkt, is_geographic = cls._detect_crs_metadata(normalized)
        return cls(
            source_path=dataset_path,
            variable_name=variable_name,
            dimensions=dimensions,
            data_array=normalized,
            crs_wkt=crs_wkt,
            is_geographic=is_geographic,
            is_temporal=is_temporal,
        )

    @staticmethod
    def _detect_crs_metadata(data_array: xr.DataArray) -> tuple[str | None, bool]:
        """Detect CRS metadata without invoking external CRS parsers.

        Parameters
        ----------
        data_array : xr.DataArray
            Loaded data array.

        Returns
        -------
        tuple[str | None, bool]
            CRS string and whether it is geographic. If no CRS is found, the
            method returns ``None`` for the CRS when the source metadata does not
            expose CRS information.
        """

        crs_wkt = _extract_crs_string_from_data_array(data_array)
        if crs_wkt is not None:
            return crs_wkt, _is_geographic_crs_string(crs_wkt)

        logger.warning("No CRS metadata was found in the loaded dataset.")
        return None, False

    @property
    def shape(self) -> tuple[int, int, int]:
        """Return the normalized data shape."""

        time_size = int(self.data_array.sizes["time"])
        latitude_size = int(self.data_array.sizes["latitude"])
        longitude_size = int(self.data_array.sizes["longitude"])
        return (time_size, latitude_size, longitude_size)

    @property
    def longitudes(self) -> np.ndarray:
        """Return longitude coordinates."""

        return np.asarray(self.data_array.coords["longitude"].values, dtype=float)

    @property
    def latitudes(self) -> np.ndarray:
        """Return latitude coordinates."""

        return np.asarray(self.data_array.coords["latitude"].values, dtype=float)

    def time_labels(self) -> list[str]:
        """Return labels for all stack positions."""

        labels: list[str] = []
        for index, value in enumerate(self.data_array.coords["time"].values, start=1):
            if self.is_temporal:
                labels.append(str(value))
                continue
            normalized_value = str(value).strip()
            if not normalized_value:
                labels.append(f"Band {index}")
                continue
            labels.append(f"Band {normalized_value}")
        return labels

    def _extract_point_series(
        self,
        point: SamplePoint,
    ) -> tuple[float, float, xr.DataArray]:
        """Sample the nearest grid-cell time-series for a point.

        Parameters
        ----------
        point : SamplePoint
            Sampling point.

        Returns
        -------
        tuple[float, float, xr.DataArray]
            Matched longitude, matched latitude, and the extracted time-series.
        """

        selected_series = self.data_array.sel(
            latitude=point.latitude,
            longitude=point.longitude,
            method="nearest",
        )
        matched_latitude = float(selected_series.coords["latitude"].item())
        matched_longitude = float(selected_series.coords["longitude"].item())
        return matched_longitude, matched_latitude, selected_series

    def _reference_series(
        self,
        reference_points: list[SamplePoint],
    ) -> xr.DataArray | None:
        """Compute the mean reference series from selected points.

        Parameters
        ----------
        reference_points : list[SamplePoint]
            Reference points used for baseline adjustment.

        Returns
        -------
        xr.DataArray | None
            Mean reference time-series or ``None`` when no reference points exist.
        """

        if not reference_points:
            return None

        xr = _import_xarray()
        sampled_series: list[xr.DataArray] = []
        for point in reference_points:
            _, _, series = self._extract_point_series(point)
            sampled_series.append(series)

        stacked_series = xr.concat(sampled_series, dim="reference_point")
        return stacked_series.mean(dim="reference_point")

    def adjusted_data(self, reference_points: list[SamplePoint]) -> xr.DataArray:
        """Return data adjusted against reference points.

        Parameters
        ----------
        reference_points : list[SamplePoint]
            Reference points used to compute the baseline.

        Returns
        -------
        xr.DataArray
            Original data when no reference points are provided, otherwise
            reference-adjusted data.
        """

        reference_series = self._reference_series(reference_points)
        if reference_series is None:
            return self.data_array
        return self.data_array - reference_series

    def spatial_slice(
        self,
        time_index: int,
        reference_points: list[SamplePoint],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return a 2D slice for visualization or export.

        Parameters
        ----------
        time_index : int
            Selected time-step index.
        reference_points : list[SamplePoint]
            Reference points used for adjustment.

        Returns
        -------
        tuple[np.ndarray, np.ndarray, np.ndarray]
            Longitudes, latitudes, and 2D values sorted for north-up display.
        """

        adjusted = self.adjusted_data(reference_points).isel(time=time_index)
        sorted_slice = adjusted.sortby("latitude", ascending=False).sortby("longitude")
        values = np.asarray(sorted_slice.values, dtype=float)
        longitudes = np.asarray(sorted_slice.coords["longitude"].values, dtype=float)
        latitudes = np.asarray(sorted_slice.coords["latitude"].values, dtype=float)
        return longitudes, latitudes, values

    def date_difference_slice(
        self,
        compare_time_index: int,
        reference_time_index: int | None,
        reference_points: list[SamplePoint],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return a spatial slice for date-based comparison rendering.

        Parameters
        ----------
        compare_time_index : int
            Index of the compare date.
        reference_time_index : int | None
            Index of the reference date. When ``None``, the compare date is used
            directly.
        reference_points : list[SamplePoint]
            Reference points used for baseline adjustment.

        Returns
        -------
        tuple[np.ndarray, np.ndarray, np.ndarray]
            Longitudes, latitudes, and rendered values.
        """

        adjusted = self.adjusted_data(reference_points)
        compare_slice = adjusted.isel(time=compare_time_index)
        if reference_time_index is not None:
            compare_slice = compare_slice - adjusted.isel(time=reference_time_index)
        sorted_slice = compare_slice.sortby("latitude", ascending=False).sortby(
            "longitude"
        )
        values = np.asarray(sorted_slice.values, dtype=float)
        longitudes = np.asarray(sorted_slice.coords["longitude"].values, dtype=float)
        latitudes = np.asarray(sorted_slice.coords["latitude"].values, dtype=float)
        return longitudes, latitudes, values

    def slice_value_range(
        self,
        compare_time_index: int,
        reference_time_index: int | None,
        reference_points: list[SamplePoint],
    ) -> tuple[float, float]:
        """Return min/max values for a date-difference slice."""

        _, _, values = self.date_difference_slice(
            compare_time_index=compare_time_index,
            reference_time_index=reference_time_index,
            reference_points=reference_points,
        )
        finite_values = values[np.isfinite(values)]
        if finite_values.size == 0:
            return (0.0, 0.0)
        return (float(np.min(finite_values)), float(np.max(finite_values)))

    def sample_series(
        self,
        points: list[SamplePoint],
        reference_points: list[SamplePoint],
    ) -> list[SampleSeries]:
        """Sample time-series for multiple points.

        Parameters
        ----------
        points : list[SamplePoint]
            Points to sample.
        reference_points : list[SamplePoint]
            Reference points used for baseline adjustment.

        Returns
        -------
        list[SampleSeries]
            Sampled time-series results.
        """

        adjusted = self.adjusted_data(reference_points)
        sampled_series: list[SampleSeries] = []
        for point in points:
            selected_series = adjusted.sel(
                latitude=point.latitude,
                longitude=point.longitude,
                method="nearest",
            )
            matched_latitude = float(selected_series.coords["latitude"].item())
            matched_longitude = float(selected_series.coords["longitude"].item())
            values = np.asarray(selected_series.values, dtype=float).tolist()
            sampled_series.append(
                SampleSeries(
                    point=point,
                    matched_longitude=matched_longitude,
                    matched_latitude=matched_latitude,
                    values=values,
                )
            )
        return sampled_series


def _extract_crs_string_from_data_array(data_array: Any) -> str | None:
    """Extract a CRS string from xarray metadata when available.

    Parameters
    ----------
    data_array : Any
        Data array returned by the loader.

    Returns
    -------
    str | None
        CRS string from known metadata locations, or ``None`` when absent.
    """

    spatial_ref = data_array.coords.get("spatial_ref")
    if spatial_ref is not None:
        spatial_ref_attributes = getattr(spatial_ref, "attrs", {})
        for attribute_name in ("crs_wkt", "spatial_ref"):
            attribute_value = spatial_ref_attributes.get(attribute_name)
            if isinstance(attribute_value, str) and attribute_value.strip():
                return attribute_value.strip()

    for attribute_name in ("crs_wkt", "spatial_ref", "crs"):
        attribute_value = data_array.attrs.get(attribute_name)
        if isinstance(attribute_value, str) and attribute_value.strip():
            return attribute_value.strip()

    return None


def _is_geographic_crs_string(crs_string: str) -> bool:
    """Return whether a CRS string appears to describe a geographic CRS.

    Parameters
    ----------
    crs_string : str
        CRS authority string or WKT text.

    Returns
    -------
    bool
        ``True`` when the CRS string looks geographic.
    """

    normalized_crs = crs_string.casefold()
    geographic_tokens = (
        "epsg:4326",
        'authority["epsg","4326"]',
        "geogcs[",
        "geodcrs[",
        "latitude_longitude",
    )
    return any(token in normalized_crs for token in geographic_tokens)
