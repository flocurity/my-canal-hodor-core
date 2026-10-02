from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import pytest
import requests

from mycanal_hodor_core.http import CanalClient, HodorError, MAX_ATTEMPTS, retry_after_seconds

EXPECTED_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:156.0) '
    'Gecko/20100101 Firefox/156.0'
)


def http_response(status=200, payload=None, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=payload))


@pytest.fixture
def client(monkeypatch):
    session = Mock(headers={})
    monkeypatch.setattr('mycanal_hodor_core.http.requests.Session', Mock(return_value=session))
    monkeypatch.setattr('mycanal_hodor_core.http.random.uniform', lambda *args: 0.1)
    sleep = Mock()
    monkeypatch.setattr('mycanal_hodor_core.http.time.sleep', sleep)
    with CanalClient() as api:
        yield api, session, sleep
    session.close.assert_called_once()


def test_success_and_pacing(client, item, fixture_data, monkeypatch):
    api, session, sleep = client
    payload = fixture_data('detail_movie.json')
    session.get.return_value = http_response(payload=payload)
    assert api.fetch(item.detail_url) == payload
    monkeypatch.setattr('mycanal_hodor_core.http.random.uniform', lambda *args: 0.25)
    sleep.assert_not_called()
    api.fetch(item.detail_url)
    sleep.assert_called_once_with(.25)
    session.get.assert_called_with(item.detail_url, timeout=10, allow_redirects=False)
    assert session.headers['User-Agent'] == EXPECTED_USER_AGENT
    assert session.headers['Accept-Encoding'] == 'deflate, gzip'

def test_default_pacing_jitter_range(client, item, fixture_data, monkeypatch):
    api, session, sleep = client
    payload = fixture_data('detail_movie.json')
    session.get.return_value = http_response(payload=payload)
    uniform = Mock(return_value=0.25)
    monkeypatch.setattr('mycanal_hodor_core.http.random.uniform', uniform)
    api.fetch(item.detail_url)
    api.fetch(item.detail_url)
    uniform.assert_called_once()
    minimum, maximum = uniform.call_args.args
    assert minimum == pytest.approx(api.delay)
    assert maximum == pytest.approx(api.delay + 0.15)

    sleep.assert_called_once_with(0.25)


def test_prepared_request_has_both_required_headers(item):
    # A real Session verifies that both values override Requests' defaults.
    # Preparing a request does not send it or require network access.
    with CanalClient() as api:
        request = api.session.prepare_request(requests.Request('GET', item.detail_url))

    assert request.headers['User-Agent'] == EXPECTED_USER_AGENT
    assert request.headers['Accept-Encoding'] == 'deflate, gzip'


@pytest.mark.parametrize('status', [429, 502, 503, 504])
def test_retry_backoff(client, item, status):
    api, session, sleep = client
    session.get.side_effect = [http_response(status), http_response(status),
                               http_response(payload={'detail': {}})]
    assert api.fetch(item.detail_url) == {'detail': {}}
    assert [call.args[0] for call in sleep.call_args_list] == [1.1, 2.1]


def test_retry_after(client, item):
    api, session, sleep = client
    session.get.side_effect = [http_response(429, headers={'Retry-After': '12'}),
                               http_response(payload={'detail': {}})]
    api.fetch(item.detail_url)
    sleep.assert_called_once_with(12)


def test_retry_after_http_date():
    deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
    delay = retry_after_seconds(format_datetime(deadline, usegmt=True))
    assert 58 <= delay <= 60
    assert retry_after_seconds('Wed, 21 Oct 2015 07:28:00 GMT') == 0


@pytest.mark.parametrize('header', [None, 'invalid', '-1', 'inf', 'nan'])
def test_bad_retry_after(header):
    assert retry_after_seconds(header) is None


@pytest.mark.parametrize('error', [requests.Timeout, requests.ConnectionError])
def test_transient_network_retry(client, item, error):
    api, session, sleep = client
    session.get.side_effect = [error(), http_response(payload={'detail': {}})]
    api.fetch(item.detail_url)
    assert session.get.call_count == 2
    sleep.assert_called_once_with(1.1)


@pytest.mark.parametrize('status', [400, 401, 403, 404, 302])
def test_nonretryable_status(client, item, status):
    api, session, sleep = client
    session.get.return_value = http_response(status)
    with pytest.raises(HodorError, match=f'HTTP {status}'):
        api.fetch(item.detail_url)
    assert session.get.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize('failure', [http_response(429), requests.Timeout(), requests.ConnectionError()])
def test_maximum_attempts(client, item, failure):
    api, session, sleep = client
    session.get.side_effect = [failure] * MAX_ATTEMPTS
    with pytest.raises(HodorError, match='exhausted 4 attempts'):
        api.fetch(item.detail_url)
    assert session.get.call_count == MAX_ATTEMPTS
    assert [call.args[0] for call in sleep.call_args_list] == [1.1, 2.1, 4.1]


def test_final_retry_after_is_respected_before_next_item(client, item):
    api, session, sleep = client
    session.get.side_effect = [http_response(429, headers={'Retry-After': '20'})] * 4 + [http_response(payload={'detail': {}})]
    with pytest.raises(HodorError):
        api.fetch(item.detail_url)
    api.fetch(item.detail_url)
    assert [call.args[0] for call in sleep.call_args_list] == [20, 20, 20, 20]


@pytest.mark.parametrize('payload', [[], {}, {'detail': None}])
def test_bad_structure(client, item, payload):
    api, session, sleep = client
    session.get.return_value = http_response(payload=payload)
    with pytest.raises(HodorError) as error:
        api.fetch(item.detail_url)
    assert error.value.kind == 'parsing'
    assert session.get.call_count == 1


def test_invalid_json(client, item):
    api, session, sleep = client
    session.get.return_value = http_response()
    session.get.return_value.json.side_effect = ValueError('bad json')
    with pytest.raises(HodorError) as error:
        api.fetch(item.detail_url)
    assert error.value.kind == 'parsing'


@pytest.mark.parametrize('url', ['http://hodor.canalplus.pro/api/v2/mycanal/detail/x',
                                'https://localhost/api/v2/mycanal/detail/x',
                                'https://hodor.canalplus.pro.evil.test/api/v2/mycanal/detail/x',
                                'https://user:pass@hodor.canalplus.pro/api/v2/mycanal/detail/x',
                                'https://hodor.canalplus.pro/private', 'file:///tmp/a',
                                'https://[bad'])
def test_reject_unsafe_url_before_request(client, url):
    api, session, sleep = client
    with pytest.raises(HodorError):
        api.fetch(url)
    session.get.assert_not_called()


@pytest.mark.parametrize('delay', [-1, float('inf'), float('nan')])
def test_invalid_delay(delay):
    with pytest.raises(ValueError):
        CanalClient(delay)


@pytest.mark.parametrize('suffix, expected', [
    ('', ['detailV5']),
    ('&featureToggles=', ['detailV5']),
    ('&featureToggles=detailV5', ['detailV5']),
    ('&featureToggles=registerProspect', ['registerProspect,detailV5']),
    ('&featureToggles=detailV5%2CregisterProspect', ['detailV5,registerProspect']),
    ('&featureToggles=detailV5&featureToggles=registerProspect',
     ['detailV5,registerProspect']),
])
def test_detail_v5_request_query(client, item, suffix, expected):
    from urllib.parse import parse_qs, urlsplit
    from mycanal_hodor_core.http import build_detail_url

    api, session, sleep = client
    source = item.detail_url + '&label=a%26b&blank=&tag=one&tag=two' + suffix
    request_url = build_detail_url(source, True)
    session.get.return_value = http_response(payload={'detail': {}})
    api.fetch(request_url, item.content_id)
    sent_url = session.get.call_args.args[0]
    query = parse_qs(urlsplit(sent_url).query, keep_blank_values=True)
    assert query['featureToggles'] == expected
    assert query['detailType'] == ['detailPage']
    assert query['objectType'] == ['unit']
    assert query['label'] == ['a&b']
    assert query['blank'] == ['']
    assert query['tag'] == ['one', 'two']
    prepared = requests.Request('GET', sent_url).prepare()
    assert parse_qs(urlsplit(prepared.url).query)['featureToggles'] == expected


@pytest.mark.parametrize('suffix', ['', '&featureToggles=mgm', '&blank=&x=a%20b'])
def test_no_declaration_leaves_url_unchanged(item, suffix):
    from mycanal_hodor_core.http import build_detail_url
    source = item.detail_url + suffix
    assert build_detail_url(source, False) == source


def test_already_declared_detail_v5_leaves_url_unchanged(item):
    from mycanal_hodor_core.http import build_detail_url
    source = item.detail_url + '&featureToggles=detailV5%2Cmgm&x=a%20b'
    assert build_detail_url(source, True) == source


def test_detail_v5_builder_rejects_invalid_source_before_rebuilding():
    from mycanal_hodor_core.http import build_detail_url
    with pytest.raises(HodorError):
        build_detail_url('https://hodor.canalplus.pro/api/v2/mycanal/detail/a\n.json', True)


def test_episodes_reuses_http_retry_and_does_not_add_detail_toggle(client, fixture_data):
    api, session, sleep = client
    url = ('https://hodor.canalplus.pro/api/v2/mycanal/episodes/' + 'a' * 32
           + '/squirtle_brand?seasonID=squirtle_s3')
    payload = fixture_data('episodes_series.json')
    session.get.side_effect = [http_response(503), http_response(payload=payload)]
    assert api.fetch_episodes(url) == payload
    assert all(call.args[0] == url for call in session.get.call_args_list)
    sleep.assert_called_once_with(1.1)


@pytest.mark.parametrize('url', [
    'https://hodor.canalplus.pro/api/v2/mycanal/me/context',
    'https://evil.test/api/v2/mycanal/episodes/context',
    'https://hodor.canalplus.pro/api/v2/mycanal/episodes/../me/context',
    'https://hodor.canalplus.pro/api/v2/mycanal/episodes/%2e%2e/me/context',
    'https://hodor.canalplus.pro/api/v2/mycanal/episodes/context?tokenPass=fake',
    'https://hodor.canalplus.pro/api/v2/mycanal/episodes/context?xx-profile-id=fake',
    'https://hodor.canalplus.pro/api/v2/mycanal/episodes/context?XX-PROFILE-ID=fake',
    'https://hodor.canalplus.pro/api/v2/mycanal/episodes/context?xx-profile-id=',
])
def test_episodes_rejects_private_or_unsafe_endpoints(client, url):
    api, session, sleep = client
    with pytest.raises(HodorError):
        api.fetch_episodes(url)
    session.get.assert_not_called()
