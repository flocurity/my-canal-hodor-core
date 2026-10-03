import logging
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
                      stream=None, hide_component: bool = True) -> None:
    """Configure application logging"""

    timestamper = structlog.processors.TimeStamper(
        fmt="%H:%M:%S",
    )

    def hide_console_fields(
        logger,
        method_name: str,
        event_dict: dict,
    ) -> dict:
        if hide_component:
            event_dict.pop("component", None)
        return event_dict

    def order_console_fields(
        logger,
        method_name: str,
        event_dict: dict,
    ) -> dict:
        """Put multiline fields last so they don't interrupt structured fields."""

        single_line = {}
        multiline = {}

        for key, value in event_dict.items():
            if isinstance(value, str) and "\n" in value:
                multiline[key] = value
            else:
                single_line[key] = value

        return {
            **single_line,
            **multiline,
        }

    structlog.configure(
        processors=[
            # Add the log level to the structured event.
            structlog.processors.add_log_level,

            # Add a timestamp.
            timestamper,

            # Support positional arguments:
            # log.info("Hello %s", name)
            structlog.stdlib.PositionalArgumentsFormatter(),

            # Render exceptions nicely.
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.ExceptionRenderer(),

            # custom rules to hide and sort fields
            hide_console_fields,
            order_console_fields,

            # Pretty development console output.
            #structlog.dev.ConsoleRenderer(
            MultilineConsoleRenderer(
                colors=colors,
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream if stream is not None else sys.stdout),
        cache_logger_on_first_use=True,
    )



