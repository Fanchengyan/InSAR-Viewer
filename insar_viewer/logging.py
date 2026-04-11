"""Logging helpers for InSAR Viewer."""

from __future__ import annotations

import logging


def setup_logger(name: str) -> logging.Logger:
    """Return a consistently configured plugin logger.

    Parameters
    ----------
    name : str
        Logger name, typically ``__name__``.

    Returns
    -------
    logging.Logger
        Logger configured with a basic stream handler when no handlers exist.
    """

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
