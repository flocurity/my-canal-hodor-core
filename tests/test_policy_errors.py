import json
from unittest.mock import Mock
import pytest
from structlog.testing import capture_logs
from mycanal_hodor_core.http import CanalClient, HodorError, hodor_policy_error

MESSAGE = 'tokenPass header forbidden for this request'


@pytest.mark.parametrize('body, expected', [
    (MESSAGE, 'token_pass_forbidden'),
    (json.dumps({'errors': [{'message': MESSAGE}]}), 'token_pass_forbidden'),
    ('Bad request', None),
    (json.dumps({'diagnostic': MESSAGE}), None),
    ('tokenPass required', None),
])
def test_only_explicit_observed_policy_error(body, expected):
    assert hodor_policy_error(body) == expected


def test_error_code_without_sensitive_logs_and_response_closed(monkeypatch):
    url = 'https://hodor.canalplus.pro/api/v2/mycanal/detail/' + 'a' * 32 + '/mammouth.json'
    response = Mock(status_code=400, text=json.dumps({'message': MESSAGE, 'echo': 'FAKE_SECRET_NOT_VALID'}))
    with CanalClient() as client:
        monkeypatch.setattr(client.session, 'get', Mock(return_value=response))
        with capture_logs() as logs, pytest.raises(HodorError) as error:
            client.get_json(url, headers={'tokenPass': 'FAKE_SECRET_NOT_VALID'}, resource='detail')
        assert error.value.policy_error == 'token_pass_forbidden'
        assert error.value.status_code == 400
        assert 'FAKE_SECRET_NOT_VALID' not in str(logs)
        assert 'a' * 32 not in str(logs)
        assert 'FAKE_SECRET_NOT_VALID' not in str(error.value)
        response.close.assert_called_once()


OBSERVED_ERROR = {'currentPage': {'displayTemplate': 'error', 'displayName': 'Page indisponible', 'BOName': 'Page indisponible', 'BOLayoutName': 'Erreur 400'}, 'title': 'Page indisponible', 'text': 'tokenPass header forbidden for this request', 'code': 400, 'tracking': {'dataLayer': {'error_message': 'tokenPass header forbidden for this request'}}}


@pytest.mark.parametrize('location', ['both', 'text', 'tracking'])
def test_observed_hodor_error_envelope(location):
    from copy import deepcopy
    payload = deepcopy(OBSERVED_ERROR)
    if location == 'text':
        del payload['tracking']
    elif location == 'tracking':
        del payload['text']
    assert hodor_policy_error(json.dumps(payload)) == 'token_pass_forbidden'
