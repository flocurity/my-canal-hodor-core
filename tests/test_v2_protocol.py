from unittest.mock import Mock

import pytest
import requests
from mycanal_hodor_core import authentication as auth
from mycanal_hodor_core.http import CanalClient, HodorError


def test_explicit_profile_does_not_prompt_or_change_preference(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    path.write_text('{"profileId": 7}')
    obj = auth.PassIdAuth('SYNTHETIC', profile_file=path)
    monkeypatch.setattr(obj, '_json', lambda *a, **kw: {'response': {'errorCode': 0,
        'passToken': 'NEW_SYNTHETIC', 'userData': {'isAuthenticated': True}}})
    monkeypatch.setattr(obj, '_get', Mock(side_effect=[
        {'URLAuthenticate': auth.BASE + '/authenticate.json/webapp/6.0'},
        {'contents': [{'type': 'profile', 'profileId': 42, 'displayName': 'Test', 'isKidsProfile': False}]},
        {'token': 'a' * 32, 'settings': {'userStatus': 'abonne'}}]))
    monkeypatch.setattr(auth, 'choose_profile', Mock(side_effect=AssertionError('No prompt')))
    assert obj.bootstrap(profile_id='42').profile_id == '42'
    assert path.read_text() == '{"profileId": 7}'


@pytest.mark.parametrize('favorite,method', [(True, 'PUT'), (False, 'DELETE')])
def test_playlist_mutation_exact_contract(favorite, method):
    with CanalClient() as client:
        response = Mock(status_code=204)
        client.session.request = Mock(return_value=response)
        client.set_playlist('a'*32, '123_50001', favorite, {'tokenPass': 'SYNTHETIC', 'xx-profile-id': '42'})
        args, kwargs = client.session.request.call_args
        assert args[0] == method
        assert kwargs['json'] == {'contentIds': ['123_50001']}
        assert kwargs['allow_redirects'] is False
        response.close.assert_called_once()


def test_ambiguous_mutation_never_retried_or_exposes_secrets():
    with CanalClient() as client:
        client.session.request = Mock(side_effect=requests.Timeout('SENSITIVE_URL'))
        with pytest.raises(HodorError) as caught:
            client.set_playlist('a'*32, '123_50001', False, {'tokenPass': 'SYNTHETIC', 'xx-profile-id': '42'})
        assert 'SENSITIVE' not in str(caught.value)
        assert client.session.request.call_count == 1


@pytest.mark.parametrize('cid', ['../evil', '123', '123_456?x=1'])
def test_mutation_rejects_non_content_ids(cid):
    with CanalClient() as client:
        client.session.request = Mock()
        with pytest.raises(HodorError):
            client.set_playlist('a'*32, cid, True, {'tokenPass': 'SYNTHETIC', 'xx-profile-id': '42'})
        client.session.request.assert_not_called()
