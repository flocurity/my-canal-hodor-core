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


def test_jsonl_preserves_fields_and_console_rendering_with_live_secrets(tmp_path):
    import io
    import json
    import logging
    from datetime import datetime
    import structlog
    from mycanal_hodor_core.console import configure_console, MultilineConsoleRenderer

    previous = structlog.get_config().copy()
    stream = io.StringIO()
    secrets = ['SYNTHETIC_OLD']
    try:
        path = configure_console(colors=True, stream=stream, log_directory=tmp_path,
                                 secrets=lambda: secrets)
        get_logger('consumer').info('sample', count=2, details='first\nsecond',
                                   nested={'echo': 'SYNTHETIC_OLD', 'tokenPass': 'hidden'})
        # FileHandler flushes each event, without waiting for shutdown.
        row = json.loads(path.read_text())
        assert row['component'] == 'consumer'
        assert row['nested'] == {'echo': '[REDACTED]'}
        assert row['count'] == 2
        assert row['details'] == 'first\nsecond'
        assert datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).tzinfo
        assert '_record' not in row and '_from_structlog' not in row
        console_row = {k: v for k, v in row.items() if k != 'component'}
        console_row['timestamp'] = row['timestamp'][11:19]
        # Exactly the previous renderer and console field ordering, including ANSI.
        details = console_row.pop('details')
        console_row['details'] = details
        assert stream.getvalue() == MultilineConsoleRenderer(colors=True)(
            None, 'info', console_row) + '\n'
        secrets.append('SYNTHETIC_NEW')
        get_logger().warning('rotation', echo='SYNTHETIC_OLD SYNTHETIC_NEW')
        for output in (stream.getvalue(), path.read_text()):
            assert 'SYNTHETIC_OLD' not in output
            assert 'SYNTHETIC_NEW' not in output
        assert len(path.read_text().splitlines()) == 2
    finally:
        for handler in logging.getLogger('mycanal').handlers[:]:
            logging.getLogger('mycanal').removeHandler(handler)
            handler.close()
        structlog.configure(**previous)


def test_reconfiguration_creates_distinct_files_without_duplicate_events(tmp_path):
    import io
    import json
    import logging
    import structlog
    from mycanal_hodor_core.console import configure_console

    previous = structlog.get_config().copy()
    stream = io.StringIO()
    try:
        first = configure_console(colors=False, stream=stream, log_directory=tmp_path)
        get_logger().info('first')
        second = configure_console(colors=False, stream=stream, log_directory=tmp_path)
        assert first != second
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            get_logger().warning('interrupted')
        assert [json.loads(s)['event'] for s in first.read_text().splitlines()] == ['first']
        assert [json.loads(s)['event'] for s in second.read_text().splitlines()] == ['interrupted']
        assert stream.getvalue().count('interrupted') == 1
        assert len(logging.getLogger('mycanal').handlers) == 2
    finally:
        for handler in logging.getLogger('mycanal').handlers[:]:
            logging.getLogger('mycanal').removeHandler(handler)
            handler.close()
        structlog.configure(**previous)


def test_console_only_configuration_does_not_create_files(tmp_path, monkeypatch):
    import io
    import logging
    import structlog
    from mycanal_hodor_core.console import configure_console

    monkeypatch.chdir(tmp_path)
    previous = structlog.get_config().copy()
    try:
        assert configure_console(colors=False, stream=io.StringIO()) is None
        get_logger().info('console_only')
        assert list(tmp_path.iterdir()) == []
    finally:
        for handler in logging.getLogger('mycanal').handlers[:]:
            logging.getLogger('mycanal').removeHandler(handler)
            handler.close()
        structlog.configure(**previous)
