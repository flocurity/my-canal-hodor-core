from unittest.mock import Mock
import pytest
from structlog.testing import capture_logs
from mycanal_hodor_core.bootstrap import BootstrapError, parse_curl, read_curl

TOKEN = 'a' * 32
AUTH = 'FAKE_AUTH_NOT_VALID'
PROFILE = 'FAKE_PROFILE_NOT_VALID'
URL = f'https://hodor.canalplus.pro/api/v2/mycanal/me/{TOKEN}/lists/playlist'
CURL = f"curl '{URL}' -H 'tokenPass: {AUTH}' -H 'xx-profile-id: {PROFILE}' --compressed"


def test_parser_header_case_order_multiline_and_ignored_headers():
    command = (f"curl --compressed '{URL}?get=20' " + chr(92) + chr(10)
               + f" -H 'XX-PROFILE-ID: {PROFILE}' -H 'Cookie: ignored' "
               + f"--header='TOKENPASS: {AUTH}' -X GET -A 'ignored'")
    context = parse_curl(command)
    assert (context.hodor_token, context.token_pass, context.profile_id) == (TOKEN, AUTH, PROFILE)
    assert AUTH not in repr(context)
    assert PROFILE not in repr(context)


def test_read_multiline_paste(monkeypatch):
    long_token = 'FAKE_AUTH_' + 'x' * 2400
    pasted = (' ' + chr(92) + chr(10)).join([
        f"curl '{URL}'",
        "-H 'User-Agent: Mozilla/5.0 Firefox/156.0'",
        f"-H 'tokenPass: {long_token}'",
        "-H 'Accept: application/json'",
        f"-H 'xx-profile-id: {PROFILE}'",
        "--compressed",
    ]) + chr(10)
    assert len(pasted) > 2048
    assert len(long_token) > 1800
    prompt = Mock(return_value=pasted)
    monkeypatch.setattr('prompt_toolkit.prompt', prompt)
    received = read_curl()
    assert received == pasted
    prompt.assert_called_once_with('> ', multiline=True)
    context = parse_curl(received)
    assert context.token_pass == long_token
    assert context.profile_id == PROFILE
    assert context.hodor_token == TOKEN


@pytest.mark.parametrize('command, reason', [
    (f"curl '{URL}' -H 'xx-profile-id: {PROFILE}'", 'Missing tokenPass'),
    (f"curl '{URL}' -H 'tokenPass: {AUTH}'", 'Missing xx-profile-id'),
    (CURL.replace(TOKEN, 'short'), 'path token'),
    (CURL.replace('hodor.canalplus.pro', 'evil.invalid'), 'Hodor API URL'),
    (CURL + " -X POST", 'GET'),
    (CURL + " -H 'tokenPass: duplicate'", 'duplicate'),
    ("curl 'unterminated", 'quoting'),
])
def test_invalid_input_safe_errors(command, reason):
    with capture_logs() as logs, pytest.raises(BootstrapError, match=reason) as error:
        parse_curl(command)
    assert not logs
    assert all(secret not in str(error.value) for secret in (TOKEN, AUTH, PROFILE))


def test_shell_syntax_never_executes(tmp_path):
    marker = tmp_path / 'EXECUTED'
    malicious = CURL + f' $(touch "{marker}") ; touch "{marker}" | cat > "{marker}"'
    with pytest.raises(BootstrapError):
        parse_curl(malicious)
    parse_curl(CURL + f" -H 'X-Ignored: $(touch {marker}); | >'")
    assert not marker.exists()


@pytest.mark.parametrize('header', ['tokenPass', 'xx-profile-id'])
def test_credentials_cannot_be_query_parameters(header):
    with pytest.raises(BootstrapError):
        parse_curl(CURL.replace(URL, URL + '?' + header + '=FAKE_NOT_VALID'))


def test_generic_bootstrap_is_not_playlist_acquisition():
    context = parse_curl(CURL.replace('/me/', '/page/').replace('/lists/playlist', '/9999.json'))
    assert context.headers == {'tokenPass': AUTH, 'xx-profile-id': PROFILE}
    assert context.api_base == 'https://hodor.canalplus.pro/api/v2/mycanal'
    assert context.request_url.endswith('/9999.json')
