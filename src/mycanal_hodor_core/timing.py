"""Opt-in timing for synchronous functions using the project logger."""

import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from .logging import get_logger

log = get_logger(__name__)
P = ParamSpec('P')
R = TypeVar('R')


def timeit() -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Log inclusive elapsed seconds at debug level, including failed calls."""
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def timed(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                duration = time.perf_counter() - started
                try:
                    log.debug('function_timing', function=function.__qualname__,
                              duration_s=round(duration, 6))
                except Exception:
                    # Optional diagnostics must not replace a result or exception
                    # if the logging output itself fails (for example a closed pipe).
                    pass
        return timed
    return decorate
