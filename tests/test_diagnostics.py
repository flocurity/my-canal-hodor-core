import io
import json
import logging
from unittest.mock import Mock
import pytest
import structlog
from mycanal_hodor_core import http
from mycanal_hodor_core.console import configure_console
from mycanal_hodor_core.logging import get_logger
from mycanal_hodor_core.diagnostics import debug_failure, redact


@pytest.fixture
def diagnostic_output(monkeypatch):
    previous = structlog.get_config()
    def setup(level):
        output = io.StringIO()
        configure_console(level=level, colors=False, stream=output)
        monkeypatch.setattr(http, 'log', get_logger('diagnostic_test'))
        return output
    yield setup
    structlog.configure(**previous)


@pytest.mark.parametrize('level', [logging.INFO, logging.DEBUG])
def test_http_body_is_debug_only_and_sanitized(diagnostic_output, monkeypatch, level):
    output = diagnostic_output(level)
    token = 'b' * 32
    secret = 'FAKE_AUTHENTICATION_MAMMOUTH'
    cookie = 'FAKE_COOKIE_GPTOU'
    bearer = 'FAKE_BEARER_NOT_VALID'
    url = 'https://hodor.canalplus.pro/api/v2/mycanal/detail/' + token + '/mammouth.json'
    response = Mock(status_code=400, headers={'Set-Cookie': 'session=' + cookie},
        text=json.dumps({'message': 'Descriptor needs a valid enum', 'tokenPass': secret,
                         'echo': secret + ' ' + cookie + ' ' + bearer, 'URLPage': url}))
    with http.CanalClient() as client:
        monkeypatch.setattr(client.session, 'get', Mock(return_value=response))
        with pytest.raises(http.HodorError, match='HTTP 400') as error:
            client.get_json(url, headers={'tokenPass': secret, 'Cookie': 'session=' + cookie,
                                         'Authorization': 'Bearer ' + bearer}, resource='detail')
    rendered = output.getvalue()
    assert str(error.value) == 'HTTP 400'
    assert 'hodor_error' in rendered
    assert ('Descriptor needs a valid enum' in rendered) == (level == logging.DEBUG)
    assert ('api_http_debug' in rendered) == (level == logging.DEBUG)
    assert all(s not in rendered for s in (secret, cookie, bearer, token))


@pytest.mark.parametrize('suppress', [False, True])
def test_non_http_exception_chain_and_traceback_are_sanitized(diagnostic_output, suppress):
    output = diagnostic_output(logging.DEBUG)
    secret = 'FAKE_SECRET_NOT_VALID'
    try:
        try:
            raise ValueError('Cannot parse payload ' + secret)
        except ValueError as cause:
            if suppress:
                raise RuntimeError('Operation failed') from None
            raise RuntimeError('Operation failed') from cause
    except RuntimeError as error:
        debug_failure(get_logger(), 'operation_debug', error, (secret,), content_id='mammouth')
    rendered = output.getvalue()
    assert 'Traceback' in rendered and 'ValueError' in rendered and 'RuntimeError' in rendered
    assert 'Cannot parse payload' in rendered and 'mammouth' in rendered
    assert secret not in rendered


def test_successful_http_has_no_failure_diagnostics(diagnostic_output, monkeypatch):
    output = diagnostic_output(logging.DEBUG)
    response = Mock(status_code=200, headers={}, text='{}')
    response.json.return_value = {'detail': {}}
    with http.CanalClient() as client:
        monkeypatch.setattr(client.session, 'get', Mock(return_value=response))
        assert client.fetch('https://hodor.canalplus.pro/api/v2/mycanal/detail/fake/mammouth.json') == {'detail': {}}
    assert 'http_attempt_timing' in output.getvalue()
    assert 'api_transport_debug' not in output.getvalue()
    assert 'api_http_debug' not in output.getvalue()
    assert 'api_structure_debug' not in output.getvalue()


def test_plain_text_sensitive_headers_are_redacted():
    text = 'Parsing failed\nAuthorization: Bearer FAKE_AUTH\nCookie: session=FAKE_COOKIE\ntokenPass=FAKE_TOKEN'
    cleaned = redact(text)
    assert 'Parsing failed' in cleaned
    assert all(s not in cleaned for s in ('FAKE_AUTH', 'FAKE_COOKIE', 'FAKE_TOKEN'))


def test_debug_rendering_long_context_has_bounded_traceback_indent(diagnostic_output):
    output = diagnostic_output(logging.DEBUG)
    try:
        raise ValueError('Unsupported descriptor')
    except ValueError as error:
        debug_failure(get_logger(), 'catalog_item_debug', error,
                      source_id='mammouth', long_context='x' * 4000,
                      parameters=[{'id': 'featureToggles', 'enum': ['gptou'] * 100}])
    lines = output.getvalue().splitlines()
    traceback_start = next(i for i, line in enumerate(lines) if 'Traceback (most recent call last)' in line)
    assert lines[traceback_start] == '  Traceback (most recent call last):'
    assert lines[traceback_start - 1].endswith('traceback=')
    assert all(len(line) - len(line.lstrip(' ')) < 40 for line in lines[traceback_start:])
    assert 'Unsupported descriptor' in output.getvalue()
    assert 'featureToggles' in output.getvalue()


@pytest.mark.parametrize('level', [logging.INFO, logging.DEBUG])
@pytest.mark.parametrize('failure,field,message', [
    ('paging', 'paging', 'Incomplete pagination: no verified continuation URL'),
    ('selector', 'selector_entry', 'Invalid season selector entry'),
    ('coordinates', 'episode_entry', 'Missing or inconsistent episode coordinates'),
])
def test_catalog_rejection_fragment_debug_only_and_sanitized(
        diagnostic_output, monkeypatch, level, failure, field, message):
    from mycanal_hodor_core import episodes
    output = diagnostic_output(level)
    monkeypatch.setattr(episodes, 'log', get_logger('catalog_parser_test'))
    secret = 'FAKE_AUTH_MAMMOUTH'
    token = 'd' * 32
    fragment = {'diagnostic_marker': 'selection_gptou', 'tokenPass': secret,
                'Cookie': 'FAKE_COOKIE', 'Authorization': 'FAKE_AUTHORIZATION',
                'echo': secret,
                'URLPage': 'https://hodor.canalplus.pro/api/v2/mycanal/episodes/' + token + '/brand'}
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': 3}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': 'episode_gptou', 'seasonNumber': 3,
                                          'episodeNumber': 1, 'durationLabel': '57 min'}]},
               'detail': {'irrelevant': 'DO_NOT_LOG_WHOLE_PAYLOAD'}}
    if failure == 'paging':
        payload['episodes']['paging'].update(fragment, hasNextPage=True)
    elif failure == 'selector':
        payload['selector'][0].update(fragment, seasonNumber='three')
    else:
        payload['episodes']['contents'][0].update(fragment, seasonNumber=9)
    with pytest.raises(ValueError) as error:
        episodes.parse_catalog(payload, 'season_mammouth', diagnostic_secrets=(secret,))
    assert str(error.value) == message
    rendered = output.getvalue()
    assert ('selection_gptou' in rendered) == (level == logging.DEBUG)
    assert (field in rendered) == (level == logging.DEBUG)
    assert ('series_catalog_debug' in rendered) == (level == logging.DEBUG)
    assert all(value not in rendered for value in (
        secret, token, 'FAKE_COOKIE', 'FAKE_AUTHORIZATION', 'DO_NOT_LOG_WHOLE_PAYLOAD'))


def test_parser_diagnostic_failure_preserves_rejection(monkeypatch):
    from mycanal_hodor_core import episodes
    logger = Mock()
    logger.debug.side_effect = RuntimeError('Broken logger')
    monkeypatch.setattr(episodes, 'log', logger)
    with pytest.raises(ValueError, match='Incomplete pagination: no verified continuation URL'):
        episodes.parse_catalog({'episodes': {'contents': [], 'paging': {}}}, 'season_mammouth')
