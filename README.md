# InSAR Viewer

InSAR Viewer is a QGIS plugin for loading NetCDF and HDF5 InSAR cubes,
detecting common time/latitude/longitude dimension names, adjusting the data
with reference points, rendering date-based spatial differences on the QGIS
canvas, and exporting GeoTIFF, CSV, and XLSX outputs.

## Implemented workflow

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