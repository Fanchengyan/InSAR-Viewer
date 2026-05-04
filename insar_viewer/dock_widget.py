"""Primary dock widget for the InSAR Viewer plugin."""

from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING, Literal

import numpy as np
from PyQt5 import uic
from PyQt5.QtCore import QObject, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (
    QColorDialog,
    QDockWidget,
    QFileDialog,
    QHeaderView,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsApplication,
    QgsColorRamp,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayerTreeGroup,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsStyle,
)
from qgis.gui import QgsFileWidget

try:
    from qgis.gui import QgsColorRampButton
except ImportError:  # pragma: no cover - depends on QGIS runtime version
    QgsColorRampButton = None

from .canvas_items import PointOverlayManager
from .constants import (
    DEFAULT_DATE_PROBE_COLOR,
    DEFAULT_DATE_PROBE_SHAPE,
    DEFAULT_DATE_PROBE_SIZE,
    DEFAULT_REFERENCE_COLOR,
    DEFAULT_REFERENCE_SHAPE,
    DEFAULT_REFERENCE_SIZE,
    DEFAULT_RENDER_MODE,
    DEFAULT_SERIES_COLOR,
    DEFAULT_SERIES_SHAPE,
    DEFAULT_SERIES_SIZE,
    POINT_SHAPE_NAMES,
    SERIES_POINT_PALETTE,
)
from .data_loader import InSARDataset, suggest_dimensions
from .dependencies import (
    DependencyInstallTask,
    dependency_statuses,
    missing_dependencies,
)
from .dependency_path import (
    clear_all_plugin_managed_site_packages,
    clear_current_plugin_managed_site_packages,
    get_plugin_managed_dependency_stats,
    get_plugin_managed_site_packages,
)
from .exporters import export_array_geotiff, export_sampled_series_table
from .logging import setup_logger
from .map_tools import InSARPointMapTool
from .models import (
    DatasetInspection,
    DimensionSelection,
    LiveProbePlotResult,
    PointStyle,
    SamplePoint,
    SampleSeries,
)
from .plotting import TimeSeriesPlotWidget
from .rendering import apply_color_ramp_renderer, list_qgis_color_ramps
from .tasks import DatasetInspectionTask, DatasetLoadTask, LiveProbePlotTask

if TYPE_CHECKING:
    from qgis.gui import QgisInterface

logger = setup_logger(__name__)

OPACITY_SLIDER_SCALE = 10
DEFAULT_LAYER_OPACITY_PERCENT = 100.0
DEFAULT_PREVIEW_LAYER_INSERT_INDEX = 0


class PreviewLayerTreePosition:
    """Persist the preview-layer position inside the QGIS layer tree.

    Attributes
    ----------
    group_path : tuple[str, ...]
        Hierarchical layer-group names from the root to the preview layer parent.
    index : int
        Zero-based insertion index inside the parent group.
    """

    def __init__(self, group_path: tuple[str, ...], index: int) -> None:
        """Initialize the stored layer-tree position.

        Parameters
        ----------
        group_path : tuple[str, ...]
            Hierarchical layer-group names from the root to the parent group.
        index : int
            Zero-based insertion index inside the parent group.
        """

        self.group_path = group_path
        self.index = index


class InSARViewerDockWidget(QDockWidget):
    """Dock widget that orchestrates the InSAR workflow."""

    closed = pyqtSignal()

    def __init__(
        self,
        iface: QgisInterface,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the dock widget.

        Parameters
        ----------
        iface : QgisInterface
            Active QGIS interface.
        parent : QWidget | None, optional
            Parent widget.
        """

        super().__init__(parent)
        self.iface = iface
        self.setMinimumSize(360, 520)
        ui_path = (
            Path(__file__).resolve().parents[1] / "ui" / "insar_viewer_dock_widget.ui"
        )
        uic.loadUi(str(ui_path), self)
        self._bind_ui_widgets()
        self.colorRampButton: QgsColorRampButton | None = None
        self._install_color_ramp_button_if_available()

        self.dataset_path: Path | None = None
        self.dataset_inspection: DatasetInspection | None = None
        self.dataset: InSARDataset | None = None
        self.preview_layer_id: str | None = None
        self.preview_layer_tree_position = PreviewLayerTreePosition(
            group_path=(),
            index=DEFAULT_PREVIEW_LAYER_INSERT_INDEX,
        )
        self.preview_raster_path = Path(gettempdir()) / "insar_viewer_preview.tif"
        self.current_capture_mode: str | None = None
        self.reference_points: list[SamplePoint] = []
        self.series_points: list[SamplePoint] = []
        self.series_point_styles: dict[str, PointStyle] = {}
        self.live_probe_point: SamplePoint | None = None
        self.live_probe_plot_task: LiveProbePlotTask | None = None
        self.pending_live_probe_point: SamplePoint | None = None
        self.dataset_inspection_task: DatasetInspectionTask | None = None
        self.dataset_load_task: DatasetLoadTask | None = None
        self._current_dataset_load_request: (
            tuple[
                Path,
                str,
                DimensionSelection,
            ]
            | None
        ) = None
        self.dependency_install_task: DependencyInstallTask | None = None
        self._is_updating_ui = False

        self.reference_style = PointStyle(
            color_hex=DEFAULT_REFERENCE_COLOR,
            size=DEFAULT_REFERENCE_SIZE,
            shape=DEFAULT_REFERENCE_SHAPE,
        )
        self.series_style = PointStyle(
            color_hex=DEFAULT_SERIES_COLOR,
            size=DEFAULT_SERIES_SIZE,
            shape=DEFAULT_SERIES_SHAPE,
        )
        self.live_probe_style = PointStyle(
            color_hex=DEFAULT_DATE_PROBE_COLOR,
            size=DEFAULT_DATE_PROBE_SIZE,
            shape=DEFAULT_DATE_PROBE_SHAPE,
        )

        self.point_map_tool = InSARPointMapTool(self.iface.mapCanvas())
        self.point_map_tool.pointCaptured.connect(self._handle_captured_point)
        self.iface.mapCanvas().xyCoordinates.connect(self._handle_canvas_hover)
        self.overlay_manager = PointOverlayManager(self.iface.mapCanvas())

        self.point_plot_widget = TimeSeriesPlotWidget("Point View Time Series", self)
        QVBoxLayout(self.pointPlotPlaceholder).addWidget(self.point_plot_widget)

        self._configure_static_controls()
        self._connect_signals()
        QgsProject.instance().layersAdded.connect(self._refresh_raster_layer_combo)
        QgsProject.instance().layersRemoved.connect(self._refresh_raster_layer_combo)
        self._set_selection_controls_enabled(False)
        self._refresh_raster_layer_combo()
        self._refresh_dependency_statuses()
        self._refresh_point_lists()
        self._refresh_canvas_markers()
        self._refresh_point_plot()

    def _bind_ui_widgets(self) -> None:
        """Bind Qt Designer widgets to stable attributes used by the controller.

        Raises
        ------
        RuntimeError
            Raised when a required widget object name is missing from the UI file.
        """

        required_widget_names = (
            "addReferencePointButton",
            "addSeriesPointButton",
            "autoValueRangeCheckBox",
            "clearAllDependenciesButton",
            "clearCurrentDependenciesButton",
            "clearReferencePointsButton",
            "clearSeriesPointsButton",
            "colorRampComboBox",
            "compareDateComboBox",
            "datasetPathFileWidget",
            "dateRenderGroupBox",
            "dependenciesInfoLabel",
            "dependenciesTableWidget",
            "dependencyInstallLogTextEdit",
            "displayDateSlider",
            "displayDateSliderLabel",
            "exportPointSeriesCsvButton",
            "installDependenciesButton",
            "jumpToFirstDateButton",
            "jumpToLastDateButton",
            "latitudeDimensionComboBox",
            "liveRenderCheckBox",
            "liveSeriesProbeCheckBox",
            "loadLayerButton",
            "loadSourceButton",
            "longitudeDimensionComboBox",
            "maxValueDoubleSpinBox",
            "minValueDoubleSpinBox",
            "opacityDoubleSpinBox",
            "opacitySlider",
            "openDependenciesFolderButton",
            "pointPlotEndComboBox",
            "pointPlotPlaceholder",
            "pointPlotStartComboBox",
            "rasterLayerComboBox",
            "referenceColorButton",
            "referenceDateLabel",
            "referenceDateComboBox",
            "referencePointsListWidget",
            "referenceShapeComboBox",
            "referenceSizeSpinBox",
            "refreshDependenciesButton",
            "removeReferencePointButton",
            "removeSeriesPointButton",
            "renderDateViewButton",
            "renderModeComboBox",
            "seriesColorButton",
            "seriesPointsListWidget",
            "seriesShapeComboBox",
            "seriesSizeSpinBox",
            "stepToNextDateButton",
            "stepToPreviousDateButton",
            "compareDateLabel",
            "timeDimensionComboBox",
            "variableComboBox",
        )
        missing_names: list[str] = []
        for widget_name in required_widget_names:
            widget = self.findChild(QObject, widget_name)
            if widget is None:
                missing_names.append(widget_name)
                continue
            setattr(self, widget_name, widget)

        if missing_names:
            logger.error(
                "Required UI widgets are missing: %s",
                ", ".join(missing_names),
            )
            raise RuntimeError(
                "The UI file is missing required widgets: " + ", ".join(missing_names)
            )

        self.selectionGroupBox = self._find_widget_with_fallback(
            primary_name="selectionGroupBox",
            fallback_name="horizontalGroupBox",
        )

    def _find_widget_with_fallback(
        self,
        primary_name: str,
        fallback_name: str,
    ) -> QObject:
        """Find a widget by primary name, falling back to an alternate name."""

        widget = self.findChild(QObject, primary_name)
        if widget is not None:
            return widget
        widget = self.findChild(QObject, fallback_name)
        if widget is None:
            logger.error(
                "Required UI widget is missing: %s or %s",
                primary_name,
                fallback_name,
            )
            raise RuntimeError(
                f"The UI file is missing required widget: {primary_name}"
            )
        return widget

    def _configure_static_controls(self) -> None:
        """Populate controls with static options."""

        self.selectionGroupBox.setEnabled(False)
        self.datasetPathFileWidget.setStorageMode(QgsFileWidget.GetFile)
        self.datasetPathFileWidget.setFilter(
            "Raster data (*.nc *.nc4 *.cdf *.h5 *.hdf5 *.he5 *.tif *.tiff);;"
            "All files (*)"
        )
        self._set_stack_axis_ui_labels(is_temporal=True)

        self.referenceShapeComboBox.addItems(POINT_SHAPE_NAMES)
        self.seriesShapeComboBox.addItems(POINT_SHAPE_NAMES)
        self.referenceShapeComboBox.setCurrentText(self.reference_style.shape)
        self.seriesShapeComboBox.setCurrentText(self.series_style.shape)
        self.referenceSizeSpinBox.setValue(self.reference_style.size)
        self.seriesSizeSpinBox.setValue(self.series_style.size)
        self._set_color_button_style(
            self.referenceColorButton,
            self.reference_style.color_hex,
        )
        self._set_color_button_style(
            self.seriesColorButton,
            self.series_style.color_hex,
        )

        self.renderModeComboBox.addItem("Continuous", "continuous")
        self.renderModeComboBox.addItem("Segmented", "segmented")
        render_mode_index = self.renderModeComboBox.findData(DEFAULT_RENDER_MODE)
        self.renderModeComboBox.setCurrentIndex(render_mode_index)

        preferred_ramps = ("Viridis", "Turbo", "Spectral", "Magma")
        if self.colorRampButton is not None:
            self._set_preferred_color_ramp(preferred_ramps)
        else:
            self.colorRampComboBox.addItems(list_qgis_color_ramps())
            for ramp_name in preferred_ramps:
                ramp_index = self.colorRampComboBox.findText(ramp_name)
                if ramp_index >= 0:
                    self.colorRampComboBox.setCurrentIndex(ramp_index)
                    break

        for combo_box in (
            self.referenceDateComboBox,
            self.compareDateComboBox,
            self.pointPlotStartComboBox,
            self.pointPlotEndComboBox,
        ):
            combo_box.clear()
        self.displayDateSlider.setMinimum(0)
        self.displayDateSlider.setMaximum(0)
        self.displayDateSlider.setValue(0)
        self.opacitySlider.setValue(
            int(DEFAULT_LAYER_OPACITY_PERCENT * OPACITY_SLIDER_SCALE)
        )
        self.opacityDoubleSpinBox.setValue(DEFAULT_LAYER_OPACITY_PERCENT)
        self.dependenciesTableWidget.setColumnCount(4)
        self.dependenciesTableWidget.setHorizontalHeaderLabels(
            ["Package", "Status", "Version", "Source"]
        )
        self.dependenciesTableWidget.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.dependenciesTableWidget.horizontalHeader().setStretchLastSection(True)
        points_selection_group = self.findChild(QWidget, "horizontalGroupBox_2")
        date_export_group = self.findChild(QWidget, "horizontalGroupBox_3")
        if points_selection_group is not None:
            points_selection_group.setMaximumHeight(245)
            points_selection_group.setSizePolicy(
                QSizePolicy.Preferred,
                QSizePolicy.Maximum,
            )
        if date_export_group is not None:
            date_export_group.setMaximumHeight(95)
            date_export_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.pointPlotPlaceholder.setMinimumHeight(300)
        self.pointPlotPlaceholder.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Expanding,
        )

    def _connect_signals(self) -> None:
        """Connect UI signals to handlers."""

        self.loadSourceButton.clicked.connect(self._load_source_dataset)
        self.loadLayerButton.clicked.connect(self._load_selected_raster_layer)
        self.variableComboBox.currentTextChanged.connect(self._handle_variable_change)
        self.timeDimensionComboBox.currentTextChanged.connect(
            self._apply_dataset_selection
        )
        self.latitudeDimensionComboBox.currentTextChanged.connect(
            self._apply_dataset_selection
        )
        self.longitudeDimensionComboBox.currentTextChanged.connect(
            self._apply_dataset_selection
        )

        self.referenceDateComboBox.currentIndexChanged.connect(
            self._handle_date_control_change
        )
        self.compareDateComboBox.currentIndexChanged.connect(
            self._handle_date_control_change
        )
        self.displayDateSlider.valueChanged.connect(
            self._handle_display_date_slider_change
        )
        self.jumpToFirstDateButton.clicked.connect(
            lambda: self._set_display_date_slider_position("first")
        )
        self.stepToPreviousDateButton.clicked.connect(
            lambda: self._step_display_date_slider(-1)
        )
        self.stepToNextDateButton.clicked.connect(
            lambda: self._step_display_date_slider(1)
        )
        self.jumpToLastDateButton.clicked.connect(
            lambda: self._set_display_date_slider_position("last")
        )
        if self.colorRampButton is not None:
            self.colorRampButton.colorRampChanged.connect(
                self._handle_render_style_change
            )
        else:
            self.colorRampComboBox.currentTextChanged.connect(
                self._handle_render_style_change
            )
        self.renderModeComboBox.currentIndexChanged.connect(
            self._handle_render_style_change
        )
        self.minValueDoubleSpinBox.valueChanged.connect(
            self._handle_render_range_change
        )
        self.maxValueDoubleSpinBox.valueChanged.connect(
            self._handle_render_range_change
        )
        self.autoValueRangeCheckBox.toggled.connect(
            self._handle_auto_value_range_toggle
        )
        self.opacitySlider.valueChanged.connect(self._handle_opacity_slider_change)
        self.opacityDoubleSpinBox.valueChanged.connect(
            self._handle_opacity_spin_box_change
        )
        self.renderDateViewButton.clicked.connect(self._render_date_view)
        self.liveRenderCheckBox.toggled.connect(self._handle_live_render_toggle)

        self.addReferencePointButton.clicked.connect(
            lambda: self._activate_capture_mode("reference")
        )
        self.removeReferencePointButton.clicked.connect(
            lambda: self._remove_last_point("reference")
        )
        self.clearReferencePointsButton.clicked.connect(
            lambda: self._clear_points("reference")
        )
        self.addSeriesPointButton.clicked.connect(
            lambda: self._activate_capture_mode("series")
        )
        self.removeSeriesPointButton.clicked.connect(
            lambda: self._remove_last_point("series")
        )
        self.clearSeriesPointsButton.clicked.connect(
            lambda: self._clear_points("series")
        )
        self.referenceShapeComboBox.currentTextChanged.connect(
            lambda: self._update_point_style("reference")
        )
        self.seriesShapeComboBox.currentTextChanged.connect(
            lambda: self._update_point_style("series")
        )
        self.referenceSizeSpinBox.valueChanged.connect(
            lambda: self._update_point_style("reference")
        )
        self.seriesSizeSpinBox.valueChanged.connect(
            lambda: self._update_point_style("series")
        )
        self.referenceColorButton.clicked.connect(
            lambda: self._choose_point_color("reference")
        )
        self.seriesColorButton.clicked.connect(
            lambda: self._choose_point_color("series")
        )
        self.pointPlotStartComboBox.currentIndexChanged.connect(
            self._refresh_point_plot
        )
        self.pointPlotEndComboBox.currentIndexChanged.connect(self._refresh_point_plot)
        self.liveSeriesProbeCheckBox.toggled.connect(self._handle_live_probe_toggle)
        self.exportPointSeriesCsvButton.clicked.connect(self._export_series_points)
        self.refreshDependenciesButton.clicked.connect(
            self._refresh_dependency_statuses
        )
        self.installDependenciesButton.clicked.connect(
            self._install_missing_dependencies
        )
        self.openDependenciesFolderButton.clicked.connect(
            self._open_plugin_dependency_folder
        )
        self.clearCurrentDependenciesButton.clicked.connect(
            self._clear_current_plugin_dependencies
        )
        self.clearAllDependenciesButton.clicked.connect(
            self._clear_all_plugin_dependencies
        )

    def _set_stack_axis_ui_labels(self, is_temporal: bool) -> None:
        """Update labels that describe the active stack axis.

        Parameters
        ----------
        is_temporal : bool
            Whether the active stack axis represents time.
        """

        axis_noun = "Date" if is_temporal else "Band"
        self.dateRenderGroupBox.setTitle(f"{axis_noun} Render")
        self.referenceDateLabel.setText(f"Reference {axis_noun}")
        self.compareDateLabel.setText(f"Display {axis_noun}")
        self.displayDateSliderLabel.setText(f"{axis_noun} Slider")

    def _refresh_raster_layer_combo(self, *args: object) -> None:
        """Refresh the raster-layer selector from the current QGIS project."""

        selected_layer_id = self.rasterLayerComboBox.currentData()
        raster_layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsRasterLayer)
            and layer.isValid()
            and layer.id() != self.preview_layer_id
            and Path(layer.source().split("|", maxsplit=1)[0].strip())
            != self.preview_raster_path
        ]
        raster_layers.sort(key=lambda layer: layer.name().casefold())

        self._is_updating_ui = True
        try:
            self.rasterLayerComboBox.clear()
            self.rasterLayerComboBox.addItem("", None)
            for raster_layer in raster_layers:
                self.rasterLayerComboBox.addItem(
                    raster_layer.name(),
                    raster_layer.id(),
                )
            if selected_layer_id is not None:
                selected_index = self.rasterLayerComboBox.findData(selected_layer_id)
                if selected_index >= 0:
                    self.rasterLayerComboBox.setCurrentIndex(selected_index)
        finally:
            self._is_updating_ui = False

    def _selected_raster_layer(self) -> QgsRasterLayer | None:
        """Return the raster layer selected in the layer combo-box."""

        layer_id = self.rasterLayerComboBox.currentData()
        if not isinstance(layer_id, str) or not layer_id:
            return None
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
            return None
        return layer

    def _extract_raster_layer_source_path(
        self,
        raster_layer: QgsRasterLayer,
    ) -> Path | None:
        """Extract a filesystem path from a QGIS raster-layer source.

        Parameters
        ----------
        raster_layer : QgsRasterLayer
            Raster layer chosen in the UI.

        Returns
        -------
        Path | None
            Parsed filesystem path when one can be derived.
        """

        source_text = raster_layer.source().strip()
        if not source_text:
            return None

        cleaned_source = source_text.split("|", maxsplit=1)[0].strip()
        if (
            cleaned_source.startswith(("NETCDF:", "HDF5:", "HDF4:"))
            and '"' in cleaned_source
        ):
            source_parts = cleaned_source.split('"')
            if len(source_parts) >= 3:
                cleaned_source = source_parts[1].strip()
        if not cleaned_source:
            return None
        return Path(cleaned_source)

    def _start_dataset_inspection(self, dataset_path: Path) -> None:
        """Reset runtime state and inspect a dataset path.

        Parameters
        ----------
        dataset_path : Path
            Dataset path to inspect and prepare for loading.
        """

        self._cancel_dataset_inspection_task()
        self._cancel_dataset_load_task()
        self.dataset_path = dataset_path
        self.dataset_inspection = None
        self.dataset = None
        self.remove_preview_layer()
        self._cancel_live_probe_task()
        self.live_probe_point = None
        self.pending_live_probe_point = None
        self.overlay_manager.clear_role("live_probe")
        self._refresh_point_plot()
        self._set_selection_controls_enabled(False)
        self.loadSourceButton.setEnabled(False)
        self.loadLayerButton.setEnabled(False)

        task = DatasetInspectionTask(self.dataset_path)
        task.inspectionFinished.connect(self._handle_dataset_inspection_finished)
        self.dataset_inspection_task = task
        QgsApplication.taskManager().addTask(task)

    def _set_selection_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable dataset-selection controls."""

        self.selectionGroupBox.setEnabled(enabled)

    def _refresh_dependency_statuses(self) -> None:
        """Refresh the dependency status table."""

        statuses = dependency_statuses()
        self.dependenciesTableWidget.setRowCount(len(statuses))
        for row_index, status in enumerate(statuses):
            dependency = status.dependency
            status_text = "Installed" if status.installed else "Missing"
            if status.error_message:
                status_text = "Broken"
            elif not dependency.required and not status.installed:
                status_text = "Missing (Optional)"

            row_items = (
                QTableWidgetItem(dependency.package_name),
                QTableWidgetItem(status_text),
                QTableWidgetItem(status.version or "-"),
                QTableWidgetItem(status.source),
            )
            status_color = QColor("#1a9850") if status.installed else QColor("#d73027")
            for column_index, item in enumerate(row_items):
                item.setForeground(status_color)
                if status.error_message:
                    item.setToolTip(status.error_message)
                self.dependenciesTableWidget.setItem(row_index, column_index, item)

        missing_count = sum(1 for status in statuses if not status.installed)
        self.installDependenciesButton.setEnabled(
            missing_count > 0 and self.dependency_install_task is None
        )
        dependency_stats = get_plugin_managed_dependency_stats(
            get_plugin_managed_site_packages()
        )
        stats_text = (
            f"Managed directory: {dependency_stats['file_count']} files, "
            f"{dependency_stats['size_bytes'] / (1024 * 1024):.1f} MB."
        )
        self.dependenciesInfoLabel.setText(
            f"Dependencies: {len(statuses) - missing_count} installed, "
            f"{missing_count} missing. Missing packages install into the hidden "
            f"plugin dependency directory for this QGIS runtime. {stats_text}"
        )

    def _install_missing_dependencies(self) -> None:
        """Install missing dependencies in the QGIS background task manager."""

        if self.dependency_install_task is not None:
            self._append_dependency_log("Dependency installation is already running.")
            return

        dependencies = missing_dependencies()
        if not dependencies:
            self._refresh_dependency_statuses()
            self._append_dependency_log("All dependencies are already installed.")
            return

        self.dependencyInstallLogTextEdit.clear()
        self._append_dependency_log("Starting background dependency installation...")
        self.installDependenciesButton.setEnabled(False)
        self.refreshDependenciesButton.setEnabled(False)
        task = DependencyInstallTask(dependencies)
        task.logMessage.connect(self._append_dependency_log)
        task.installFinished.connect(self._handle_dependency_install_finished)
        self.dependency_install_task = task
        QgsApplication.taskManager().addTask(task)

    def _open_plugin_dependency_folder(self) -> None:
        """Open the plugin-managed dependency directory for this runtime."""

        dependency_path = get_plugin_managed_site_packages(create=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(dependency_path))):
            logger.error("Failed to open dependency directory: %s", dependency_path)
            self._append_dependency_log(
                f"Failed to open dependency directory: {dependency_path}"
            )

    def _clear_current_plugin_dependencies(self) -> None:
        """Clear plugin-managed dependencies for the active QGIS runtime."""

        dependency_path = get_plugin_managed_site_packages()
        if dependency_path.exists() and not self._confirm_dependency_clear(
            "Clear Current Runtime Dependencies",
            "Clear dependencies installed for this QGIS runtime?",
        ):
            return
        removed_count = clear_current_plugin_managed_site_packages()
        self._append_dependency_log(
            f"Cleared {removed_count} files from current runtime dependencies."
        )
        self._refresh_dependency_statuses()

    def _clear_all_plugin_dependencies(self) -> None:
        """Clear all plugin-managed dependency directories."""

        if not self._confirm_dependency_clear(
            "Clear All Managed Dependencies",
            "Clear dependencies installed by InSAR Viewer for all QGIS runtimes?",
        ):
            return
        removed_count = clear_all_plugin_managed_site_packages()
        self._append_dependency_log(
            f"Cleared {removed_count} files from all managed dependency directories."
        )
        self._refresh_dependency_statuses()

    def _confirm_dependency_clear(self, title: str, message: str) -> bool:
        """Return whether the user confirmed dependency deletion.

        Parameters
        ----------
        title : str
            Dialog title.
        message : str
            Dialog message.

        Returns
        -------
        bool
            ``True`` when deletion was confirmed.
        """

        reply = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _append_dependency_log(self, message: str) -> None:
        """Append a line to the dependency installation log."""

        if not message:
            return
        self.dependencyInstallLogTextEdit.append(message)

    def _handle_dependency_install_finished(
        self,
        success: bool,
        message: str,
    ) -> None:
        """Handle completion of the background dependency installation task."""

        self._append_dependency_log(message)
        self.dependency_install_task = None
        self.refreshDependenciesButton.setEnabled(True)
        self._refresh_dependency_statuses()
        if success:
            self.iface.messageBar().pushSuccess(
                "InSAR Viewer",
                "Dependency installation completed. Restart QGIS if newly installed "
                "compiled packages are not immediately available.",
            )
            return
        self.iface.messageBar().pushWarning(
            "InSAR Viewer",
            "Dependency installation failed. Review the Dependencies tab log.",
        )

    def _load_source_dataset(self) -> None:
        """Inspect the chosen dataset and populate variable controls."""

        dataset_text = self.datasetPathFileWidget.filePath().strip()
        if not dataset_text:
            self._show_error_message("Missing Dataset", "Select a dataset path first.")
            return
        self._start_dataset_inspection(Path(dataset_text))

    def _load_selected_raster_layer(self) -> None:
        """Inspect the dataset referenced by the selected raster layer."""

        raster_layer = self._selected_raster_layer()
        if raster_layer is None:
            self._show_error_message(
                "Missing Raster Layer",
                "Select a raster layer from the list first.",
            )
            return

        dataset_path = self._extract_raster_layer_source_path(raster_layer)
        if dataset_path is None or not dataset_path.exists():
            logger.error(
                "Failed to resolve a readable dataset path for raster layer %s: %s",
                raster_layer.name(),
                raster_layer.source(),
            )
            self._show_error_message(
                "Invalid Raster Layer Source",
                "The selected raster layer does not reference a readable local file.",
            )
            return

        self.datasetPathFileWidget.setFilePath(str(dataset_path))
        self._start_dataset_inspection(dataset_path)

    def _handle_dataset_inspection_finished(
        self,
        success: bool,
        dataset_path: object,
        inspection: object,
        error_message: str,
    ) -> None:
        """Handle completion of the background dataset inspection task."""

        self.dataset_inspection_task = None
        self.loadSourceButton.setEnabled(True)
        self.loadLayerButton.setEnabled(True)

        if not isinstance(dataset_path, Path):
            logger.error(
                "Dataset inspection returned an invalid path: %r",
                dataset_path,
            )
            return
        if self.dataset_path != dataset_path:
            return
        if not success:
            if error_message and "canceled" not in error_message.casefold():
                self._show_error_message("Dataset Inspection Failed", error_message)
            return
        if not isinstance(inspection, DatasetInspection):
            logger.error(
                "Dataset inspection returned an invalid payload for %s.",
                dataset_path,
            )
            return

        self.dataset_inspection = inspection
        self._is_updating_ui = True
        self.variableComboBox.clear()
        self.variableComboBox.addItems(self.dataset_inspection.variable_names)
        self._set_selection_controls_enabled(True)
        if self.dataset_inspection.suggested_variable is not None:
            suggested_index = self.variableComboBox.findText(
                self.dataset_inspection.suggested_variable
            )
            if suggested_index >= 0:
                self.variableComboBox.setCurrentIndex(suggested_index)
        self._is_updating_ui = False

        self._update_dimension_controls()
        self._apply_dataset_selection()

    def _handle_variable_change(self) -> None:
        """Update dimension controls when the selected variable changes."""

        if self._is_updating_ui:
            return
        self._update_dimension_controls()
        self._apply_dataset_selection()

    def _update_dimension_controls(self) -> None:
        """Populate the dimension selectors for the active variable."""

        if self.dataset_inspection is None:
            return

        variable_name = self.variableComboBox.currentText()
        dimension_names = self.dataset_inspection.variable_dimensions.get(
            variable_name,
            (),
        )
        detected_dimensions = suggest_dimensions(dimension_names)

        self._is_updating_ui = True
        self._populate_dimension_combo(self.timeDimensionComboBox, dimension_names)
        self._populate_dimension_combo(self.latitudeDimensionComboBox, dimension_names)
        self._populate_dimension_combo(self.longitudeDimensionComboBox, dimension_names)
        if detected_dimensions is not None:
            self._set_combo_value(self.timeDimensionComboBox, detected_dimensions.time)
            self._set_combo_value(
                self.latitudeDimensionComboBox,
                detected_dimensions.latitude,
            )
            self._set_combo_value(
                self.longitudeDimensionComboBox,
                detected_dimensions.longitude,
            )
        else:
            self.timeDimensionComboBox.setCurrentIndex(-1)
            self.latitudeDimensionComboBox.setCurrentIndex(-1)
            self.longitudeDimensionComboBox.setCurrentIndex(-1)
        self._is_updating_ui = False

    def _populate_dimension_combo(
        self,
        combo_box: object,
        dimension_names: tuple[str, ...],
    ) -> None:
        """Populate a dimension combo-box with available names."""

        previous_value = combo_box.currentText()
        combo_box.clear()
        combo_box.addItems(dimension_names)
        if previous_value:
            index = combo_box.findText(previous_value)
            if index >= 0:
                combo_box.setCurrentIndex(index)

    def _set_combo_value(self, combo_box: object, value: str) -> None:
        """Set a combo-box value when it exists."""

        index = combo_box.findText(value)
        if index >= 0:
            combo_box.setCurrentIndex(index)

    def _apply_dataset_selection(self) -> None:
        """Load the active variable using the current dimension mapping."""

        if self._is_updating_ui or self.dataset_path is None:
            return

        variable_name = self.variableComboBox.currentText().strip()
        time_name = self.timeDimensionComboBox.currentText().strip()
        latitude_name = self.latitudeDimensionComboBox.currentText().strip()
        longitude_name = self.longitudeDimensionComboBox.currentText().strip()
        if not all((variable_name, time_name, latitude_name, longitude_name)):
            return

        dimensions = DimensionSelection(
            time=time_name,
            latitude=latitude_name,
            longitude=longitude_name,
        )
        request = (self.dataset_path, variable_name, dimensions)
        if request == self._current_dataset_load_request:
            return

        self._current_dataset_load_request = request
        self._load_dataset_sync(
            dataset_path=self.dataset_path,
            variable_name=variable_name,
            dimensions=dimensions,
        )

    def _load_dataset_sync(
        self,
        dataset_path: Path,
        variable_name: str,
        dimensions: DimensionSelection,
    ) -> None:
        """Load the dataset synchronously in the main process."""

        error_message = ""
        try:
            dataset = InSARDataset.load(
                dataset_path,
                variable_name=variable_name,
                dimensions=dimensions,
            )
        except Exception as exc:
            logger.error(
                "Dataset load failed for %s (%s, %s): %s",
                dataset_path,
                variable_name,
                dimensions,
                exc,
            )
            error_message = str(exc)
            dataset = None

        self._handle_dataset_load_finished(
            success=dataset is not None,
            dataset_path=dataset_path,
            variable_name=variable_name,
            dimensions=dimensions,
            dataset=dataset,
            error_message=error_message,
        )

    def _handle_dataset_load_finished(
        self,
        success: bool,
        dataset_path: object,
        variable_name: str,
        dimensions: object,
        dataset: object,
        error_message: str,
    ) -> None:
        """Handle completion of the background dataset load task."""

        request = (
            dataset_path,
            variable_name,
            dimensions,
        )
        if request == self._current_dataset_load_request:
            self._current_dataset_load_request = None

        if not isinstance(dataset_path, Path) or not isinstance(
            dimensions, DimensionSelection
        ):
            logger.error(
                "Dataset load returned invalid request metadata: %r",
                request,
            )
            return

        current_request = self._current_dataset_request()
        if current_request != request:
            return

        if not success:
            if error_message and "canceled" not in error_message.casefold():
                self._show_error_message("Dataset Load Failed", error_message)
            return
        if not isinstance(dataset, InSARDataset):
            logger.error(
                "Dataset load returned an invalid payload for %s.",
                dataset_path,
            )
            return

        self.dataset = dataset
        self._set_stack_axis_ui_labels(is_temporal=self.dataset.is_temporal)
        self._populate_time_controls()
        self.remove_preview_layer()
        self._refresh_value_range_from_data()
        self._cancel_live_probe_task()
        self.live_probe_point = None
        self.pending_live_probe_point = None
        self.overlay_manager.clear_role("live_probe")
        self._refresh_point_plot()
        self.iface.messageBar().pushSuccess(
            "InSAR Viewer",
            f"Loaded {self.dataset.source_path.name}",
        )
        if self.dataset.crs_wkt is None:
            self.iface.messageBar().pushWarning(
                "InSAR Viewer",
                "The loaded dataset does not expose CRS metadata through rio.crs. "
                "Map sampling and export will use EPSG:4326 as a fallback.",
            )

    def _populate_time_controls(self) -> None:
        """Populate stack-axis controls from the current dataset."""

        if self.dataset is None:
            return

        time_labels = self.dataset.time_labels()
        combo_groups = (
            self.compareDateComboBox,
            self.pointPlotStartComboBox,
            self.pointPlotEndComboBox,
        )
        self._is_updating_ui = True
        self.referenceDateComboBox.clear()
        self.referenceDateComboBox.addItem("None", None)
        if self.dataset.is_temporal:
            for index, label in enumerate(time_labels):
                self.referenceDateComboBox.addItem(label, index)
        for combo_box in combo_groups:
            combo_box.clear()
            for index, label in enumerate(time_labels):
                combo_box.addItem(label, index)
        last_index = max(0, len(time_labels) - 1)
        self.compareDateComboBox.setCurrentIndex(0)
        self.referenceDateComboBox.setCurrentIndex(0)
        self.referenceDateComboBox.setEnabled(self.dataset.is_temporal)
        self.referenceDateLabel.setEnabled(self.dataset.is_temporal)
        self.displayDateSlider.setMinimum(0)
        self.displayDateSlider.setMaximum(last_index)
        self.displayDateSlider.setValue(0)
        self.pointPlotStartComboBox.setCurrentIndex(0)
        self.pointPlotEndComboBox.setCurrentIndex(last_index)
        self._is_updating_ui = False

    def _current_compare_index(self) -> int | None:
        """Return the current compare-date index."""

        if self.compareDateComboBox.count() == 0:
            return None
        return int(self.compareDateComboBox.currentData())

    def _current_reference_index(self) -> int | None:
        """Return the current reference-date index or ``None``."""

        if (
            self.dataset is None
            or not self.dataset.is_temporal
            or self.referenceDateComboBox.count() == 0
        ):
            return None
        data = self.referenceDateComboBox.currentData()
        if data is None:
            return None
        return int(data)

    def _combo_range_indices(
        self,
        start_combo_box: object,
        end_combo_box: object,
    ) -> tuple[int, int]:
        """Return a valid inclusive time-index range from two combo-boxes."""

        if self.dataset is None:
            return (0, 0)
        if start_combo_box.count() == 0 or end_combo_box.count() == 0:
            return (0, 0)

        start_data = start_combo_box.currentData()
        end_data = end_combo_box.currentData()
        start_index = (
            int(start_data)
            if start_data is not None
            else max(start_combo_box.currentIndex(), 0)
        )
        end_index = (
            int(end_data)
            if end_data is not None
            else max(end_combo_box.currentIndex(), 0)
        )
        if start_index > end_index:
            end_index = start_index
            end_combo_box.setCurrentIndex(start_combo_box.currentIndex())
        return (start_index, end_index)

    def _handle_date_control_change(self) -> None:
        """Refresh date-dependent UI state after date controls change."""

        if self._is_updating_ui or self.dataset is None:
            return
        self._sync_display_slider_to_combo()
        self._refresh_value_range_from_data()
        if not self.autoValueRangeCheckBox.isChecked():
            self._refresh_or_mark_date_render()

    def _handle_display_date_slider_change(self, display_index: int) -> None:
        """Synchronize the display-date combo-box from the time slider."""

        if self._is_updating_ui or self.compareDateComboBox.count() == 0:
            return
        bounded_index = max(0, min(display_index, self.compareDateComboBox.count() - 1))
        self.compareDateComboBox.setCurrentIndex(bounded_index)

    def _step_display_date_slider(self, step_offset: int) -> None:
        """Move the display-date slider by a relative number of steps.

        Parameters
        ----------
        step_offset : int
            Relative slider offset. Negative values move backward and positive
            values move forward.
        """

        slider_minimum = self.displayDateSlider.minimum()
        slider_maximum = self.displayDateSlider.maximum()
        next_value = self.displayDateSlider.value() + step_offset
        bounded_value = max(slider_minimum, min(next_value, slider_maximum))
        self.displayDateSlider.setValue(bounded_value)

    def _set_display_date_slider_position(
        self,
        position: Literal["first", "last"],
    ) -> None:
        """Jump the display-date slider to the requested endpoint.

        Parameters
        ----------
        position : str
            Target endpoint. Accepted values are ``"first"`` and ``"last"``.

        Raises
        ------
        ValueError
            Raised when the target endpoint is unsupported.
        """

        if position == "first":
            self.displayDateSlider.setValue(self.displayDateSlider.minimum())
            return
        if position == "last":
            self.displayDateSlider.setValue(self.displayDateSlider.maximum())
            return
        logger.error("Unsupported display-date slider position: %s", position)
        raise ValueError(f"Unsupported display-date slider position: {position}")

    def _sync_display_slider_to_combo(self) -> None:
        """Synchronize the time slider with the selected display date."""

        compare_index = self._current_compare_index()
        if compare_index is None:
            return
        self.displayDateSlider.blockSignals(True)
        try:
            self.displayDateSlider.setValue(compare_index)
        finally:
            self.displayDateSlider.blockSignals(False)

    def _handle_render_style_change(self, *_args: object) -> None:
        """Handle color-ramp and render-mode changes."""

        if self._is_updating_ui:
            return
        self._refresh_or_mark_date_render()

    def _handle_opacity_slider_change(self, slider_value: int) -> None:
        """Synchronize the opacity spin box from the slider value."""

        if self._is_updating_ui:
            return
        opacity_percent = slider_value / OPACITY_SLIDER_SCALE
        self._is_updating_ui = True
        try:
            self.opacityDoubleSpinBox.setValue(opacity_percent)
        finally:
            self._is_updating_ui = False
        self._apply_or_mark_preview_opacity()

    def _handle_opacity_spin_box_change(self, opacity_percent: float) -> None:
        """Synchronize the opacity slider from the spin-box value."""

        if self._is_updating_ui:
            return
        slider_value = int(round(opacity_percent * OPACITY_SLIDER_SCALE))
        self._is_updating_ui = True
        try:
            self.opacitySlider.setValue(slider_value)
        finally:
            self._is_updating_ui = False
        self._apply_or_mark_preview_opacity()

    def _current_preview_opacity(self) -> float:
        """Return the configured preview opacity as a normalized fraction."""

        return float(self.opacityDoubleSpinBox.value()) / 100.0

    def _apply_or_mark_preview_opacity(self) -> None:
        """Apply opacity to the preview layer or mark the render as pending."""

        preview_layer = self._preview_layer()
        if preview_layer is not None:
            self._apply_preview_renderer()
            return
        self._refresh_or_mark_date_render()

    def _handle_live_render_toggle(self, checked: bool) -> None:
        """Render immediately when live rendering is enabled."""

        if checked:
            self._render_date_view()

    def _handle_reference_points_change(self) -> None:
        """Refresh outputs affected by reference-point updates."""

        self._refresh_point_lists()
        self._refresh_canvas_markers()
        self._refresh_value_range_from_data()
        if not self.autoValueRangeCheckBox.isChecked():
            self._refresh_or_mark_date_render()
        self._refresh_point_plot()

    def _refresh_or_mark_date_render(self) -> None:
        """Render immediately when live render is enabled, otherwise mark pending."""

        if self.liveRenderCheckBox.isChecked():
            self._render_date_view()
            return
        self._mark_date_view_pending()

    def _render_date_view(self) -> None:
        """Render the Date Band Render raster on the QGIS canvas."""

        if self.dataset is None:
            return

        compare_index = self._current_compare_index()
        if compare_index is None:
            return

        reference_index = self._current_reference_index()
        try:
            longitudes, latitudes, values = self.dataset.date_difference_slice(
                compare_time_index=compare_index,
                reference_time_index=reference_index,
                reference_points=self.reference_points,
            )
        except Exception as exc:
            logger.error("Date Band Render refresh failed: %s", exc)
            self._show_error_message("Date Band Render Failed", str(exc))
            return

        self._update_preview_layer(
            longitudes=longitudes,
            latitudes=latitudes,
            values=values,
            minimum_value=float(self.minValueDoubleSpinBox.value()),
            maximum_value=float(self.maxValueDoubleSpinBox.value()),
        )

    def _refresh_value_range_from_data(self) -> None:
        """Refresh the render range from the current slice when auto mode is enabled."""

        if self.dataset is None or not self.autoValueRangeCheckBox.isChecked():
            return
        compare_index = self._current_compare_index()
        if compare_index is None:
            return
        minimum_value, maximum_value = self.dataset.slice_value_range(
            compare_time_index=compare_index,
            reference_time_index=self._current_reference_index(),
            reference_points=self.reference_points,
        )
        self._is_updating_ui = True
        self.minValueDoubleSpinBox.setValue(minimum_value)
        self.maxValueDoubleSpinBox.setValue(maximum_value)
        self._is_updating_ui = False
        self._refresh_or_mark_date_render()

    def _handle_auto_value_range_toggle(self, checked: bool) -> None:
        """Refresh the value range when auto mode is enabled."""

        if self._is_updating_ui:
            return
        if checked:
            self._refresh_value_range_from_data()

    def _handle_render_range_change(self) -> None:
        """Keep the render range valid without auto-rendering the raster."""

        if self._is_updating_ui:
            return
        if self.minValueDoubleSpinBox.value() > self.maxValueDoubleSpinBox.value():
            self._is_updating_ui = True
            self.maxValueDoubleSpinBox.setValue(self.minValueDoubleSpinBox.value())
            self._is_updating_ui = False
        self._refresh_or_mark_date_render()

    def _update_preview_layer(
        self,
        longitudes: object,
        latitudes: object,
        values: object,
        minimum_value: float,
        maximum_value: float,
    ) -> None:
        """Write the current date-view slice to a temporary raster layer."""

        project = QgsProject.instance()
        previous_preview_position = self._preview_layer_tree_position()
        if previous_preview_position is not None:
            self.preview_layer_tree_position = previous_preview_position
        self.remove_preview_layer()

        clipped_values = self._clip_values_to_range(
            values=values,
            minimum_value=minimum_value,
            maximum_value=maximum_value,
        )

        try:
            export_array_geotiff(
                destination_path=self.preview_raster_path,
                longitudes=longitudes,
                latitudes=latitudes,
                values=clipped_values,
                crs_wkt=self._dataset_crs_wkt(),
            )
        except Exception as exc:
            logger.error("Failed to write preview raster: %s", exc)
            self._show_error_message("Preview Raster Failed", str(exc))
            return

        compare_label = self.compareDateComboBox.currentText()
        layer_prefix = (
            "InSAR Date Band"
            if self.dataset is None or self.dataset.is_temporal
            else "InSAR Raster Band"
        )
        layer_name = f"{layer_prefix} | {compare_label}"
        preview_layer = QgsRasterLayer(str(self.preview_raster_path), layer_name)
        if not preview_layer.isValid():
            logger.error(
                "Generated preview layer is invalid: %s",
                self.preview_raster_path,
            )
            self._show_error_message(
                "Preview Layer Failed",
                "The temporary preview raster could not be loaded in QGIS.",
            )
            return
        project.addMapLayer(preview_layer, False)
        self._insert_preview_layer_into_tree(preview_layer)
        self.preview_layer_id = preview_layer.id()
        self._apply_preview_renderer()

    def _install_color_ramp_button_if_available(self) -> None:
        """Replace the plain ramp combo box with the native QGIS ramp picker."""

        if QgsColorRampButton is None:
            return
        parent_widget = self.colorRampComboBox.parentWidget()
        if parent_widget is None:
            logger.warning(
                "Color ramp combo box has no parent widget. Falling back to text list."
            )
            return
        parent_layout = parent_widget.layout()
        if parent_layout is None:
            logger.warning(
                "Color ramp combo box parent has no layout. Falling back to text list."
            )
            return

        color_ramp_button = QgsColorRampButton(parent_widget)
        color_ramp_button.setObjectName("colorRampButton")
        color_ramp_button.setSizePolicy(self.colorRampComboBox.sizePolicy())
        if hasattr(color_ramp_button, "setShowNull"):
            color_ramp_button.setShowNull(False)
        if hasattr(color_ramp_button, "setDialogTitle"):
            color_ramp_button.setDialogTitle("Select Color Ramp")

        parent_layout.replaceWidget(self.colorRampComboBox, color_ramp_button)
        self.colorRampComboBox.hide()
        self.colorRampComboBox.deleteLater()
        self.colorRampButton = color_ramp_button

    def _set_preferred_color_ramp(self, preferred_ramps: tuple[str, ...]) -> None:
        """Set the first available preferred color ramp on the native picker."""

        if self.colorRampButton is None:
            return
        style = QgsStyle.defaultStyle()
        for ramp_name in preferred_ramps:
            color_ramp = style.colorRamp(ramp_name)
            if color_ramp is not None:
                self.colorRampButton.setColorRamp(color_ramp)
                return

    def _current_color_ramp(self) -> QgsColorRamp | None:
        """Return the currently selected QGIS color ramp instance."""

        if self.colorRampButton is not None:
            return self.colorRampButton.colorRamp()
        ramp_name = self.colorRampComboBox.currentText().strip()
        if not ramp_name:
            return None
        return QgsStyle.defaultStyle().colorRamp(ramp_name)

    def _current_color_ramp_name(self) -> str | None:
        """Return the currently selected color-ramp name when available."""

        if self.colorRampButton is not None:
            return None
        ramp_name = self.colorRampComboBox.currentText().strip()
        return ramp_name or None

    def _apply_preview_renderer(self) -> None:
        """Apply the chosen color ramp and value range to the preview layer."""

        preview_layer = self._preview_layer()
        if preview_layer is None:
            return

        try:
            apply_color_ramp_renderer(
                layer=preview_layer,
                ramp_name=self._current_color_ramp_name(),
                minimum_value=float(self.minValueDoubleSpinBox.value()),
                maximum_value=float(self.maxValueDoubleSpinBox.value()),
                render_mode=str(self.renderModeComboBox.currentData()),
                opacity=self._current_preview_opacity(),
                color_ramp=self._current_color_ramp(),
            )
        except Exception as exc:
            logger.error("Failed to apply raster renderer: %s", exc)
            self._show_error_message("Renderer Failed", str(exc))

    def _clip_values_to_range(
        self,
        values: object,
        minimum_value: float,
        maximum_value: float,
    ) -> np.ndarray:
        """Clip finite raster values to the configured render range."""

        clipped_values = np.asarray(values, dtype=float).copy()
        if maximum_value < minimum_value:
            minimum_value, maximum_value = maximum_value, minimum_value
        finite_mask = np.isfinite(clipped_values)
        clipped_values[finite_mask] = np.clip(
            clipped_values[finite_mask],
            minimum_value,
            maximum_value,
        )
        return clipped_values

    def _mark_date_view_pending(self) -> None:
        """Update the status text after date-view settings change."""

        if self.dataset is None:
            return
        self.remove_preview_layer()

    def _current_dataset_request(
        self,
    ) -> tuple[Path, str, DimensionSelection] | None:
        """Return the current dataset-load request derived from the UI state."""

        if self.dataset_path is None:
            return None

        variable_name = self.variableComboBox.currentText().strip()
        time_name = self.timeDimensionComboBox.currentText().strip()
        latitude_name = self.latitudeDimensionComboBox.currentText().strip()
        longitude_name = self.longitudeDimensionComboBox.currentText().strip()
        if not all((variable_name, time_name, latitude_name, longitude_name)):
            return None

        return (
            self.dataset_path,
            variable_name,
            DimensionSelection(
                time=time_name,
                latitude=latitude_name,
                longitude=longitude_name,
            ),
        )

    def _activate_capture_mode(self, capture_mode: str) -> None:
        """Activate a point-capture mode on the QGIS canvas."""

        self.current_capture_mode = capture_mode
        self.iface.mapCanvas().setMapTool(self.point_map_tool)
        self.iface.messageBar().pushInfo(
            "InSAR Viewer",
            f"Click on the canvas to add a {capture_mode.replace('_', ' ')}.",
        )

    def _handle_captured_point(self, longitude: float, latitude: float) -> None:
        """Store a point captured from the QGIS map canvas."""

        if self.current_capture_mode is None:
            return

        sample_x, sample_y = self._transform_wgs84_to_dataset_crs(longitude, latitude)

        if self.current_capture_mode == "reference":
            point = SamplePoint(
                label=f"REF-{len(self.reference_points) + 1}",
                longitude=sample_x,
                latitude=sample_y,
            )
            self.reference_points.append(point)
            self._handle_reference_points_change()
        elif self.current_capture_mode == "series":
            point = SamplePoint(
                label=f"SER-{len(self.series_points) + 1}",
                longitude=sample_x,
                latitude=sample_y,
            )
            self.series_points.append(point)
            self.series_point_styles[point.label] = self._next_series_point_style()
            self._advance_series_color()
            self._refresh_point_lists()
            self._refresh_canvas_markers()
            self._refresh_point_plot()

        self.reset_map_tool()

    def _refresh_point_lists(self) -> None:
        """Refresh the reference and series point list widgets."""

        self.referencePointsListWidget.clear()
        for point in self.reference_points:
            self.referencePointsListWidget.addItem(
                self._create_point_list_item(point, self.reference_style.color_hex)
            )

        self.seriesPointsListWidget.clear()
        for point in self.series_points:
            point_style = self.series_point_styles.get(point.label, self.series_style)
            self.seriesPointsListWidget.addItem(
                self._create_point_list_item(point, point_style.color_hex)
            )

    def _create_point_list_item(
        self,
        point: SamplePoint,
        color_hex: str,
    ) -> QListWidgetItem:
        """Create a list item for a point with matching color."""

        item = QListWidgetItem(
            f"{point.label} | lon={point.longitude:.5f}, lat={point.latitude:.5f}"
        )
        item.setForeground(QColor(color_hex))
        return item

    def _refresh_canvas_markers(self) -> None:
        """Refresh every point marker drawn on the QGIS canvas."""

        self.overlay_manager.set_points(
            role="reference",
            points=self.reference_points,
            style=self.reference_style,
            source_crs_wkt=self._dataset_crs_wkt(),
        )
        self.overlay_manager.set_points(
            role="series",
            points=self.series_points,
            style=self.series_point_styles,
            source_crs_wkt=self._dataset_crs_wkt(),
        )
        self.overlay_manager.set_single_point(
            role="live_probe",
            point=(
                self.live_probe_point
                if self.liveSeriesProbeCheckBox.isChecked()
                else None
            ),
            style=self.live_probe_style,
            source_crs_wkt=self._dataset_crs_wkt(),
        )

    def _remove_last_point(self, point_role: str) -> None:
        """Remove the most recently added point for a role."""

        if point_role == "reference":
            if not self.reference_points:
                return
            self.reference_points.pop()
            self._handle_reference_points_change()
            return

        if point_role == "series":
            if not self.series_points:
                return
            removed_point = self.series_points.pop()
            self.series_point_styles.pop(removed_point.label, None)
            self._sync_next_series_color()
            self._refresh_point_lists()
            self._refresh_canvas_markers()
            self._refresh_point_plot()

    def _clear_points(self, point_role: str) -> None:
        """Clear all points for a role."""

        if point_role == "reference":
            self.reference_points.clear()
            self._handle_reference_points_change()
            return

        if point_role == "series":
            self.series_points.clear()
            self.series_point_styles.clear()
            self._sync_next_series_color()
            self._refresh_point_lists()
            self._refresh_canvas_markers()
            self._refresh_point_plot()

    def _update_point_style(self, point_role: str) -> None:
        """Update marker style state from the UI controls."""

        if point_role == "reference":
            self.reference_style = PointStyle(
                color_hex=self.reference_style.color_hex,
                size=int(self.referenceSizeSpinBox.value()),
                shape=str(self.referenceShapeComboBox.currentText()),
            )
        else:
            self.series_style = PointStyle(
                color_hex=self.series_style.color_hex,
                size=int(self.seriesSizeSpinBox.value()),
                shape=str(self.seriesShapeComboBox.currentText()),
            )
            self.live_probe_style = PointStyle(
                color_hex=self.live_probe_style.color_hex,
                size=max(4, int(self.seriesSizeSpinBox.value())),
                shape=str(self.seriesShapeComboBox.currentText()),
            )
            self.series_point_styles = {
                label: PointStyle(
                    color_hex=style.color_hex,
                    size=self.series_style.size,
                    shape=self.series_style.shape,
                )
                for label, style in self.series_point_styles.items()
            }

        self._refresh_point_lists()
        self._refresh_canvas_markers()
        self._refresh_point_plot()

    def _choose_point_color(self, point_role: str) -> None:
        """Open a color dialog and update the selected point style."""

        initial_color = (
            self.reference_style.color_hex
            if point_role == "reference"
            else self.series_style.color_hex
        )
        color = QColorDialog.getColor(QColor(initial_color), self, "Choose Point Color")
        if not color.isValid():
            return
        if point_role == "reference":
            self.reference_style = PointStyle(
                color_hex=color.name(),
                size=self.reference_style.size,
                shape=self.reference_style.shape,
            )
            self._set_color_button_style(self.referenceColorButton, color.name())
        else:
            self.series_style = PointStyle(
                color_hex=color.name(),
                size=self.series_style.size,
                shape=self.series_style.shape,
            )
            self._set_color_button_style(self.seriesColorButton, color.name())

        self._refresh_point_lists()
        self._refresh_canvas_markers()
        self._refresh_point_plot()

    def _next_series_point_style(self) -> PointStyle:
        """Return the style assigned to the next series point."""

        return PointStyle(
            color_hex=self.series_style.color_hex,
            size=self.series_style.size,
            shape=self.series_style.shape,
        )

    def _advance_series_color(self) -> None:
        """Advance the queued series-point color to the next palette value."""

        palette = SERIES_POINT_PALETTE or (DEFAULT_SERIES_COLOR,)
        try:
            current_index = palette.index(self.series_style.color_hex)
            next_color = palette[(current_index + 1) % len(palette)]
        except ValueError:
            next_color = palette[0]
        self.series_style = PointStyle(
            color_hex=next_color,
            size=self.series_style.size,
            shape=self.series_style.shape,
        )
        self._set_color_button_style(self.seriesColorButton, next_color)

    def _sync_next_series_color(self) -> None:
        """Reset the queued series-point color after list edits."""

        if not self.series_points:
            next_color = DEFAULT_SERIES_COLOR
        else:
            last_style = self.series_point_styles.get(self.series_points[-1].label)
            last_color = (
                last_style.color_hex if last_style is not None else DEFAULT_SERIES_COLOR
            )
            palette = SERIES_POINT_PALETTE or (DEFAULT_SERIES_COLOR,)
            try:
                next_color = palette[(palette.index(last_color) + 1) % len(palette)]
            except ValueError:
                next_color = palette[0]
        self.series_style = PointStyle(
            color_hex=next_color,
            size=self.series_style.size,
            shape=self.series_style.shape,
        )
        self._set_color_button_style(self.seriesColorButton, next_color)

    def _set_color_button_style(self, button: object, color_hex: str) -> None:
        """Apply a simple swatch style to a color button."""

        button.setStyleSheet(
            "QPushButton {"
            f"background-color: {color_hex};"
            "color: white;"
            "font-weight: 600;"
            "}"
        )
        button.setText(color_hex.upper())

    def _refresh_point_plot(self) -> None:
        """Refresh the Point View time-series plot."""

        if self.dataset is None:
            self.point_plot_widget.draw_time_series(
                time_labels=[],
                sampled_series=[],
                color_lookup={},
                empty_message="Load a dataset and add one or more series points.",
            )
            return

        start_index, end_index = self._combo_range_indices(
            self.pointPlotStartComboBox,
            self.pointPlotEndComboBox,
        )
        plot_points = list(self.series_points)
        if (
            self.liveSeriesProbeCheckBox.isChecked()
            and self.live_probe_point is not None
        ):
            plot_points.append(self.live_probe_point)
        sampled_series = self.dataset.sample_series(
            points=plot_points,
            reference_points=self.reference_points,
        )
        self._draw_point_plot(sampled_series)

    def _draw_point_plot(self, sampled_series: list[SampleSeries]) -> None:
        """Draw the point plot using pre-sampled series data."""

        if self.dataset is None:
            return

        start_index, end_index = self._combo_range_indices(
            self.pointPlotStartComboBox,
            self.pointPlotEndComboBox,
        )
        color_lookup: dict[str, str] = {}
        marker_size_lookup: dict[str, float] = {}
        for series in sampled_series:
            if series.point.label == "LIVE":
                point_style = self.live_probe_style
            else:
                point_style = self.series_point_styles.get(
                    series.point.label,
                    self.series_style,
                )
            color_lookup[series.point.label] = point_style.color_hex
            marker_size_lookup[series.point.label] = max(3.0, point_style.size * 0.45)
        self.point_plot_widget.draw_time_series(
            time_labels=self.dataset.time_labels(),
            sampled_series=sampled_series,
            color_lookup=color_lookup,
            marker_size_lookup=marker_size_lookup,
            start_index=start_index,
            end_index=end_index,
            empty_message="Add one or more series points to plot their time series.",
        )

    def _handle_live_probe_toggle(self, checked: bool) -> None:
        """Enable or disable the live series probe."""

        if not checked:
            self._cancel_live_probe_task()
            self.live_probe_point = None
            self.pending_live_probe_point = None
            self.overlay_manager.clear_role("live_probe")
            self._refresh_canvas_markers()
            self._refresh_point_plot()
            return

        self.pending_live_probe_point = None
        self._refresh_canvas_markers()

    def _handle_canvas_hover(self, canvas_point: QgsPointXY) -> None:
        """Update the live series probe from the current canvas position."""

        if not self.liveSeriesProbeCheckBox.isChecked() or self.dataset is None:
            return

        sample_x, sample_y = self._transform_canvas_to_dataset_crs(canvas_point)
        self.live_probe_point = SamplePoint(
            label="LIVE",
            longitude=sample_x,
            latitude=sample_y,
        )
        self.pending_live_probe_point = self.live_probe_point
        self._refresh_canvas_markers()
        self._schedule_live_probe_plot_update()

    def _schedule_live_probe_plot_update(self) -> None:
        """Schedule background sampling for the latest live probe point."""

        if (
            not self.liveSeriesProbeCheckBox.isChecked()
            or self.dataset is None
            or self.pending_live_probe_point is None
        ):
            return
        if self.live_probe_plot_task is not None:
            return

        probe_point = self.pending_live_probe_point
        self.pending_live_probe_point = None
        task = LiveProbePlotTask(
            dataset=self.dataset,
            probe_point=probe_point,
            series_points=list(self.series_points),
            reference_points=list(self.reference_points),
        )
        task.samplingFinished.connect(self._handle_live_probe_plot_finished)
        self.live_probe_plot_task = task
        QgsApplication.taskManager().addTask(task)

    def _handle_live_probe_plot_finished(
        self,
        success: bool,
        plot_result: object,
        error_message: str,
    ) -> None:
        """Handle completion of the live-probe background sampling task."""

        self.live_probe_plot_task = None
        if not success and error_message:
            logger.warning("Live probe plot update failed: %s", error_message)

        if self.pending_live_probe_point is not None:
            self._schedule_live_probe_plot_update()

        if not success or not isinstance(plot_result, LiveProbePlotResult):
            return
        if not self._is_live_probe_plot_result_current(plot_result):
            return
        self._draw_point_plot(plot_result.sampled_series)

    def _is_live_probe_plot_result_current(
        self,
        plot_result: LiveProbePlotResult,
    ) -> bool:
        """Return whether a background live-probe result still matches the UI."""

        if not self.liveSeriesProbeCheckBox.isChecked():
            return False
        if self.dataset is None or self.live_probe_point is None:
            return False
        if self.live_probe_point != plot_result.probe_point:
            return False
        if list(self.series_points) != plot_result.series_points:
            return False
        return list(self.reference_points) == plot_result.reference_points

    def _cancel_live_probe_task(self) -> None:
        """Cancel the active live-probe sampling task when present."""

        if self.live_probe_plot_task is None:
            return
        self.live_probe_plot_task.cancel()
        self.live_probe_plot_task = None

    def _export_series_points(self) -> None:
        """Export Point View series points to CSV or XLSX."""

        if self.dataset is None:
            self._show_error_message("No Dataset", "Load a dataset before exporting.")
            return
        if not self.series_points:
            self._show_error_message(
                "No Series Points",
                "Add one or more series points before exporting.",
            )
            return

        destination_path = self._choose_series_export_path()
        if destination_path is None:
            return

        start_index, end_index = self._combo_range_indices(
            self.pointPlotStartComboBox,
            self.pointPlotEndComboBox,
        )
        sampled_series = self.dataset.sample_series(
            points=self.series_points,
            reference_points=self.reference_points,
        )
        try:
            export_sampled_series_table(
                destination_path=destination_path,
                time_labels=self.dataset.time_labels(),
                sampled_series=sampled_series,
                start_index=start_index,
                end_index=end_index,
            )
        except Exception as exc:
            logger.error("Point View export failed: %s", exc)
            self._show_error_message("Series Export Failed", str(exc))
            return

        self.iface.messageBar().pushSuccess(
            "InSAR Viewer",
            f"Series exported to {destination_path}",
        )

    def _choose_series_export_path(self) -> Path | None:
        """Prompt for a CSV or XLSX export path."""

        destination_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Time Series",
            "",
            "CSV (*.csv);;Excel Workbook (*.xlsx)",
        )
        if not destination_path:
            return None
        path = Path(destination_path)
        if "xlsx" in selected_filter.lower():
            return path if path.suffix.lower() == ".xlsx" else path.with_suffix(".xlsx")
        return path if path.suffix.lower() == ".csv" else path.with_suffix(".csv")

    def _dataset_crs_wkt(self) -> str:
        """Return the active dataset CRS string."""

        if self.dataset is None or self.dataset.crs_wkt is None:
            return "EPSG:4326"
        return self.dataset.crs_wkt

    def _preview_layer(self) -> object | None:
        """Return the current preview layer when it still exists in QGIS."""

        if self.preview_layer_id is None:
            return None
        return QgsProject.instance().mapLayer(self.preview_layer_id)

    def _preview_layer_tree_position(self) -> PreviewLayerTreePosition | None:
        """Return the current preview-layer position in the QGIS layer tree."""

        if self.preview_layer_id is None:
            return None

        layer_tree_layer = (
            QgsProject.instance().layerTreeRoot().findLayer(self.preview_layer_id)
        )
        if layer_tree_layer is None:
            return None

        parent_group = layer_tree_layer.parent()
        if not isinstance(parent_group, QgsLayerTreeGroup):
            return None

        group_path: list[str] = []
        current_group: QgsLayerTreeGroup | None = parent_group
        root_group = QgsProject.instance().layerTreeRoot()
        while current_group is not None and current_group is not root_group:
            group_path.append(current_group.name())
            parent_node = current_group.parent()
            current_group = (
                parent_node if isinstance(parent_node, QgsLayerTreeGroup) else None
            )

        return PreviewLayerTreePosition(
            group_path=tuple(reversed(group_path)),
            index=parent_group.children().index(layer_tree_layer),
        )

    def _insert_preview_layer_into_tree(self, preview_layer: QgsRasterLayer) -> None:
        """Insert the preview layer back into its stored layer-tree position."""

        root_group = QgsProject.instance().layerTreeRoot()
        target_group = root_group
        for group_name in self.preview_layer_tree_position.group_path:
            next_group = target_group.findGroup(group_name)
            if next_group is None:
                break
            target_group = next_group

        target_index = min(
            self.preview_layer_tree_position.index,
            len(target_group.children()),
        )
        target_group.insertLayer(target_index, preview_layer)

    def _dataset_crs_label(self) -> str:
        """Return a short label for the active dataset CRS."""

        if self.dataset is None:
            return "EPSG:4326"
        if self.dataset.crs_wkt is None:
            return "Unknown CRS (fallback EPSG:4326)"
        dataset_crs = QgsCoordinateReferenceSystem()
        if dataset_crs.createFromString(self._dataset_crs_wkt()):
            auth_id = dataset_crs.authid()
            if auth_id:
                return auth_id
        return self._dataset_crs_wkt()

    def _transform_wgs84_to_dataset_crs(
        self,
        longitude: float,
        latitude: float,
    ) -> tuple[float, float]:
        """Transform a captured WGS84 point into the active dataset CRS."""

        source_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        target_crs = QgsCoordinateReferenceSystem()
        if not target_crs.createFromString(self._dataset_crs_wkt()):
            logger.warning(
                "Failed to parse dataset CRS. Falling back to WGS84 sampling: %s",
                self._dataset_crs_wkt(),
            )
            return longitude, latitude
        if source_crs == target_crs:
            return longitude, latitude

        transform = QgsCoordinateTransform(
            source_crs,
            target_crs,
            QgsProject.instance(),
        )
        transformed_point = transform.transform(QgsPointXY(longitude, latitude))
        return float(transformed_point.x()), float(transformed_point.y())

    def _transform_canvas_to_dataset_crs(
        self,
        canvas_point: QgsPointXY,
    ) -> tuple[float, float]:
        """Transform a canvas coordinate into the active dataset CRS."""

        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem()
        if not target_crs.createFromString(self._dataset_crs_wkt()):
            logger.warning(
                "Failed to parse dataset CRS. Falling back to canvas sampling: %s",
                self._dataset_crs_wkt(),
            )
            return float(canvas_point.x()), float(canvas_point.y())
        if canvas_crs == target_crs:
            return float(canvas_point.x()), float(canvas_point.y())

        transform = QgsCoordinateTransform(
            canvas_crs,
            target_crs,
            QgsProject.instance(),
        )
        transformed_point = transform.transform(canvas_point)
        return float(transformed_point.x()), float(transformed_point.y())

    def clear_runtime_artifacts(self) -> None:
        """Clear transient layers, map tools, and canvas overlays."""

        self.reset_map_tool()
        self.remove_preview_layer()
        self._cancel_dataset_inspection_task()
        self._cancel_dataset_load_task()
        self._cancel_live_probe_task()
        self.live_probe_point = None
        self.pending_live_probe_point = None
        self.overlay_manager.clear_all()

    def _cancel_dataset_inspection_task(self) -> None:
        """Cancel the active dataset inspection task when present."""

        if self.dataset_inspection_task is None:
            return
        self.dataset_inspection_task.cancel()
        self.dataset_inspection_task = None

    def _cancel_dataset_load_task(self) -> None:
        """Cancel the active dataset load task when present."""

        if self.dataset_load_task is None:
            return
        self.dataset_load_task.cancel()
        self.dataset_load_task = None
        self._current_dataset_load_request = None

    def _show_error_message(self, title: str, message: str) -> None:
        """Show an error dialog."""

        QMessageBox.critical(self, title, message)

    def reset_map_tool(self) -> None:
        """Reset the canvas to the default map tool."""

        self.current_capture_mode = None
        self.iface.mapCanvas().unsetMapTool(self.point_map_tool)

    def remove_preview_layer(self) -> None:
        """Remove the preview raster layer from the QGIS project."""

        if self.preview_layer_id is None:
            return
        project = QgsProject.instance()
        preview_layer = project.mapLayer(self.preview_layer_id)
        if preview_layer is not None:
            project.removeMapLayer(self.preview_layer_id)
        self.preview_layer_id = None

    def closeEvent(self, event: object) -> None:
        """Handle dock-widget close events."""

        self.clear_runtime_artifacts()
        self.closed.emit()
        super().closeEvent(event)
