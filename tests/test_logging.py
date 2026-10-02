import subprocess
import sys
from structlog.testing import capture_logs
from mycanal_hodor_core.logging import get_logger


def test_import_does_not_configure_process_logging():
    code = "import structlog; before=structlog.get_config().copy(); import mycanal_hodor_core.console, mycanal_hodor_core.timing, mycanal_hodor_core.http; assert before==structlog.get_config(); assert not structlog.is_configured()"
    subprocess.run([sys.executable, '-c', code], check=True)


def test_application_selected_processors_receive_core_fields():
    with capture_logs() as logs:
        get_logger('consumer').info('test_event', count=2)
    assert logs[0]['component'] == 'consumer'
    assert logs[0]['count'] == 2


def test_console_renderer_and_explicit_configuration():
    import io
    import structlog
    from mycanal_hodor_core.console import configure_console
    previous = structlog.get_config().copy()
    stream = io.StringIO()
    try:
        configure_console(colors=False, stream=stream)
        get_logger('example').info('multiline', details='line one\nline two')
        assert 'line one' in stream.getvalue()
        assert 'line two' in stream.getvalue()
    finally:
        structlog.configure(**previous)
