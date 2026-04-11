"""Background QGIS tasks used by InSAR Viewer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import pyqtSignal
from qgis.core import QgsTask

from .logging import setup_logger
from .models import LiveProbePlotResult, SamplePoint, SampleSeries

if TYPE_CHECKING:
    from .data_loader import InSARDataset

logger = setup_logger(__name__)


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
