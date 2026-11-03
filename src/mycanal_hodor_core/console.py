import logging
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from collections.abc import Callable, Iterable
import re
import sys
import structlog

class MultilineConsoleRenderer:
    """Render multiline string fields inline with normal structlog formatting."""

    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
    _MARKER = "__STRUCTLOG_MULTILINE_MARKER__"

    def __init__(self, *, colors: bool = True) -> None:
        self._renderer = structlog.dev.ConsoleRenderer(colors=colors, sort_keys=False)

    @classmethod
    def _strip_ansi(cls, text: str) -> str:
        return cls._ANSI_RE.sub("", text)

    def __call__(
        self,
        logger,
        method_name: str,
        event_dict: dict,
    ) -> str:
        event_dict = event_dict.copy()

        multiline = {
            key: value
            for key, value in event_dict.items()
            if isinstance(value, str) and "\n" in value
        }

        if not multiline:
            return self._renderer(logger, method_name, event_dict)

        # Give ConsoleRenderer ordinary strings containing unique markers.
        markers: dict[str, str] = {}

        for index, key in enumerate(multiline):
            marker = f"{self._MARKER}{index}__"
            markers[key] = marker
            event_dict[key] = marker

        # ConsoleRenderer renders EVERYTHING:
        # timestamp, level, event, board=, colors, alignment...
        rendered = self._renderer(
            logger,
            method_name,
            event_dict,
        )

        for key, value in multiline.items():
            marker = markers[key]

            # Find the marker in the ANSI-rendered string.
            marker_pos = rendered.find(marker)

            if marker_pos < 0:
                continue

            # Determine its visible terminal column.
            before_marker = rendered[:marker_pos]
            visible_before = self._strip_ansi(before_marker)

            line_before_marker = visible_before.rsplit("\n", 1)[-1]
            value_column = len(line_before_marker)

            lines = value.splitlines()

            if not lines:
                lines = [""]

            if method_name == 'debug':
                # Diagnostic blocks must not align to a potentially huge context line.
                replacement = '\n' + '\n'.join('  ' + line for line in lines)
                rendered = (rendered[:marker_pos] + replacement
                            + rendered[marker_pos + len(marker):])
                continue

            replacement = lines[0]

            if len(lines) > 1:
                indent = " " * value_column

                replacement += "".join(
                    "\n" + indent + line
                    for line in lines[1:]
                )

            # Replace ONLY the marker itself.
            #
            # ANSI sequences surrounding it remain untouched, so the normal
            # ConsoleRenderer styling of board= is preserved.
            rendered = (
                rendered[:marker_pos]
                + replacement
                + rendered[marker_pos + len(marker):]
            )

        return rendered


def configure_console(*, level: int = logging.DEBUG, colors: bool = True,
                      stream=None, hide_component: bool = True,
                      log_directory: str | Path | None = None,
                      secrets: Callable[[], Iterable[str]] | None = None) -> Path | None:
    """Configure console output and an optional exclusive, progressively flushed JSONL file.

    The secret supplier is evaluated for every event so token rotations are covered.
    Applications opt into file output; importing Core never installs handlers.
    """
    from .diagnostics import redact

    def sanitize(logger, method_name, event_dict):
        return redact(event_dict, tuple(secrets()) if secrets is not None else ())

    def console_fields(logger, method_name, event_dict):
        if hide_component:
            event_dict.pop("component", None)
        # Keep the existing UTC console clock while JSONL retains the full ISO date.
        event_dict["timestamp"] = event_dict["timestamp"][11:19]
        single_line = {}
        multiline = {}
        for key, value in event_dict.items():
            if isinstance(value, str) and "\n" in value:
                multiline[key] = value
            else:
                single_line[key] = value
        return {**single_line, **multiline}

    shared = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.ExceptionRenderer(),
        sanitize,
    ]
    console = logging.StreamHandler(stream if stream is not None else sys.stdout)
    console.setFormatter(structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            console_fields,
            MultilineConsoleRenderer(colors=colors),
        ],
    ))
    handlers = [console]
    path = None
    if log_directory is not None:
        directory = Path(log_directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        # Exclusive creation prevents collisions and gives potentially personal logs
        # owner-only permissions, independently of the process umask.
        descriptor, filename = tempfile.mkstemp(
            prefix=f"crawl-{stamp}-", suffix=".jsonl", dir=directory)
        os.close(descriptor)
        path = Path(filename)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(ensure_ascii=False, allow_nan=False),
            ],
        ))
        handlers.append(file_handler)

    logger = logging.getLogger("mycanal")
    logger.setLevel(level)
    logger.propagate = False
    for previous in logger.handlers[:]:
        logger.removeHandler(previous)
        previous.close()
    for handler in handlers:
        logger.addHandler(handler)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=lambda *args: logger,
        cache_logger_on_first_use=True,
    )
    return path
