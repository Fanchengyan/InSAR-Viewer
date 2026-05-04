"""QGIS raster rendering helpers for InSAR Viewer."""

from __future__ import annotations

from typing import Literal

import numpy as np
from qgis.core import (
    QgsColorRamp,
    QgsColorRampShader,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
)

from .logging import setup_logger

logger = setup_logger(__name__)

RenderMode = Literal["continuous", "segmented"]


def list_qgis_color_ramps() -> list[str]:
    """Return available QGIS color-ramp names."""

    style = QgsStyle.defaultStyle()
    ramp_names = sorted(style.colorRampNames())
    if ramp_names:
        return ramp_names
    return ["Spectral"]


def apply_color_ramp_renderer(
    layer: object,
    ramp_name: str | None,
    minimum_value: float,
    maximum_value: float,
    render_mode: RenderMode,
    opacity: float = 1.0,
    color_ramp: QgsColorRamp | None = None,
) -> None:
    """Apply a pseudo-color renderer to a raster layer.

    Parameters
    ----------
    layer : object
        Raster layer instance.
    ramp_name : str | None
        QGIS color-ramp name. Used when ``color_ramp`` is not provided.
    minimum_value : float
        Lower render bound.
    maximum_value : float
        Upper render bound.
    render_mode : RenderMode
        Continuous or segmented ramp mode.
    opacity : float, optional
        Layer opacity in the range [0.0, 1.0].
    color_ramp : QgsColorRamp | None, optional
        Explicit QGIS color-ramp instance. When provided, it takes precedence over
        ``ramp_name``.
    """

    if maximum_value <= minimum_value:
        maximum_value = minimum_value + 1e-9
    opacity = min(max(opacity, 0.0), 1.0)

    style = QgsStyle.defaultStyle()
    resolved_color_ramp = color_ramp
    if resolved_color_ramp is None and ramp_name:
        resolved_color_ramp = style.colorRamp(ramp_name)
    if resolved_color_ramp is None:
        available_names = list_qgis_color_ramps()
        fallback_name = available_names[0]
        logger.warning(
            "Color ramp %s was not found. Falling back to %s.",
            ramp_name,
            fallback_name,
        )
        resolved_color_ramp = style.colorRamp(fallback_name)
    if resolved_color_ramp is None:
        logger.error("No QGIS color ramp is available for raster rendering.")
        raise RuntimeError("No QGIS color ramp is available.")

    fractions = np.linspace(0.0, 1.0, 9).tolist()
    items = [
        QgsColorRampShader.ColorRampItem(
            minimum_value + fraction * (maximum_value - minimum_value),
            resolved_color_ramp.color(fraction),
            f"{minimum_value + fraction * (maximum_value - minimum_value):.3f}",
        )
        for fraction in fractions
    ]

    shader_function = QgsColorRampShader()
    shader_function.setMinimumValue(minimum_value)
    shader_function.setMaximumValue(maximum_value)
    if hasattr(shader_function, "setClip"):
        shader_function.setClip(True)
    if render_mode == "continuous":
        shader_function.setColorRampType(QgsColorRampShader.Interpolated)
    else:
        shader_function.setColorRampType(QgsColorRampShader.Discrete)
    shader_function.setColorRampItemList(items)
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader_function)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, raster_shader)
    if hasattr(renderer, "setClassificationMin"):
        renderer.setClassificationMin(minimum_value)
    if hasattr(renderer, "setClassificationMax"):
        renderer.setClassificationMax(maximum_value)
    if hasattr(renderer, "setOpacity"):
        renderer.setOpacity(opacity)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
