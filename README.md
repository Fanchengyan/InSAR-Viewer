## InSAR Viewer

InSAR Viewer is a QGIS plugin for loading NetCDF and HDF5 InSAR cubes,
detecting common time/latitude/longitude dimension names, adjusting the data
with reference points, rendering date-based spatial differences on the QGIS
canvas, and exporting GeoTIFF, CSV, and XLSX outputs.

### UI Structure

- `ui/insar_viewer_dock_widget.ui`: Qt Designer UI definition for the dock widget.
- `assets/insar_viewer_icon.svg`: plugin icon used by QGIS metadata and toolbar.

### Implemented workflow

1. In the `Data` tab, use `Browse` to choose a file path and `Load` to inspect
   the source dataset.
2. Select `Variable`, `Time`, `Latitude`, and `Longitude` dimensions. The plugin
   auto-loads the dataset when a valid mapping is available.
3. In `Date Band Render`, choose an optional reference date and a display date, then
   adjust the QGIS canvas rendering with any QGIS color ramp, value range, and
   continuous or segmented display mode.
4. In `Point Time Series`, add reference points and series points, customize each point
   type with its own color, size, and shape, and inspect the sampled series with
   pan and zoom support.
5. Export sampled point series from `Point Time Series`.

### Development

Use `uv` for Python tooling and `ruff` for linting:

```bash
uv run ruff check .
uv run python -m compileall __init__.py insar_viewer
```

### Notes

- The plugin currently supports only 1D latitude and longitude coordinates.
- QGIS usually expects the plugin directory name to be a valid Python package
  name. If this folder is loaded directly by QGIS, renaming it from
  `InSAR-Viewer` to `insar_viewer` may be necessary.
