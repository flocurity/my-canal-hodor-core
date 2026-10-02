"""Lazy structured logger access without process-wide configuration."""
import structlog


def get_logger(name: str | None = None):
    return structlog.get_logger(**({'component': name} if name is not None else {}))
