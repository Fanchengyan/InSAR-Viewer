"""Background QGIS tasks used by InSAR Viewer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import pyqtSignal
from qgis.core import QgsTask

from .data_loader import InSARDataset, inspect_dataset
from .logging import setup_logger
from .models import (
    DatasetInspection,
    DimensionSelection,
    LiveProbePlotResult,
    SamplePoint,
    SampleSeries,
)

if TYPE_CHECKING:
    pass

logger = setup_logger(__name__)


class DatasetInspectionTask(QgsTask):
    """Inspect a source dataset in a QGIS background task."""

    inspectionFinished = pyqtSignal(bool, object, object, str)

    def __init__(self, dataset_path: Path) -> None:
        """Initialize the dataset inspection task.

        Parameters
        ----------
        dataset_path : Path
            Source dataset path to inspect.
        """

        super().__init__("Inspect InSAR dataset", QgsTask.CanCancel)
        self.dataset_path = dataset_path
        self.inspection: DatasetInspection | None = None
        self.error_message = ""

    def run(self) -> bool:
        """Inspect the dataset without blocking the UI thread.

        Returns
        -------
        bool
            ``True`` when inspection succeeds, otherwise ``False``.
        """

        if self.isCanceled():
            self.error_message = "Dataset inspection was canceled."
            return False

        try:
            self.inspection = inspect_dataset(self.dataset_path)
        except Exception as exc:
            logger.error(
                "Dataset inspection failed for %s in background task: %s",
                self.dataset_path,
                exc,
            )
            self.error_message = str(exc)
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:
        """Emit inspection results on the main thread."""

        self.inspectionFinished.emit(
            result,
            self.dataset_path,
            self.inspection,
            self.error_message,
        )


class DatasetLoadTask(QgsTask):
    """Load the selected InSAR variable in a QGIS background task."""

    datasetLoaded = pyqtSignal(bool, object, str, object, object, str)

    def __init__(
        self,
        dataset_path: Path,
        variable_name: str,
        dimensions: DimensionSelection,
    ) -> None:
        """Initialize the dataset load task.

        Parameters
        ----------
        dataset_path : Path
            Source dataset path.
        variable_name : str
            Selected variable name.
        dimensions : DimensionSelection
            User-selected dimension mapping.
        """

        super().__init__("Load InSAR dataset", QgsTask.CanCancel)
        self.dataset_path = dataset_path
        self.variable_name = variable_name
        self.dimensions = dimensions
        self.dataset: InSARDataset | None = None
        self.error_message = ""

    def run(self) -> bool:
        """Load the dataset without blocking the UI thread.

        Returns
        -------
        bool
            ``True`` when loading succeeds, otherwise ``False``.
        """

        if self.isCanceled():
            self.error_message = "Dataset loading was canceled."
            return False

        try:
            self.dataset = InSARDataset.load(
                self.dataset_path,
                variable_name=self.variable_name,
                dimensions=self.dimensions,
            )
        except Exception as exc:
            logger.error(
                "Dataset load failed for %s (%s, %s) in background task: %s",
                self.dataset_path,
                self.variable_name,
                self.dimensions,
                exc,
            )
            self.error_message = str(exc)
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:
        """Emit dataset load results on the main thread."""

        self.datasetLoaded.emit(
            result,
            self.dataset_path,
            self.variable_name,
            self.dimensions,
            self.dataset,
            self.error_message,
        )


class LiveProbePlotTask(QgsTask):
    """Sample the live-probe time series in a QGIS background task."""

    samplingFinished = pyqtSignal(bool, object, str)

    def __init__(
        self,
        dataset: InSARDataset,
        probe_point: SamplePoint,
        series_points: list[SamplePoint],
        reference_points: list[SamplePoint],
    ) -> None:
        """Initialize the live-probe sampling task.

        Parameters
        ----------
        dataset : InSARDataset
            Dataset used for sampling.
        probe_point : SamplePoint
            Live probe point under the mouse cursor.
        series_points : list[SamplePoint]
            Stored series points already shown in the plot.
        reference_points : list[SamplePoint]
            Reference points used for baseline adjustment.
        """

        super().__init__("Sample live probe time series", QgsTask.CanCancel)
        self.dataset = dataset
        self.probe_point = probe_point
        self.series_points = list(series_points)
        self.reference_points = list(reference_points)
        self.sampled_series: list[SampleSeries] = []
        self.error_message = ""

    def run(self) -> bool:
        """Run background sampling without blocking the UI thread.

        Returns
        -------
        bool
            ``True`` when sampling succeeds, otherwise ``False``.
        """

        if self.isCanceled():
            self.error_message = "Live probe sampling was canceled."
            return False

        try:
            self.sampled_series = self.dataset.sample_series(
                points=[*self.series_points, self.probe_point],
                reference_points=self.reference_points,
            )
        except Exception as exc:
            logger.error("Live probe background sampling failed: %s", exc)
            self.error_message = str(exc)
            return False
        return not self.isCanceled()

    def finished(self, result: bool) -> None:
        """Emit the sampling result on the main thread."""

        plot_result = LiveProbePlotResult(
            probe_point=self.probe_point,
            series_points=list(self.series_points),
            reference_points=list(self.reference_points),
            sampled_series=list(self.sampled_series),
        )
        self.samplingFinished.emit(result, plot_result, self.error_message)
