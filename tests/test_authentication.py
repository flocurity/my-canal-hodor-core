from unittest.mock import Mock
import pytest
from mycanal_hodor_core.bootstrap import BootstrapError
from mycanal_hodor_core import authentication as auth

PROFILE_PATH = auth.profile_path


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, 'profile_path', lambda application=auth.SERVICE: tmp_path / application / 'profile.json')

def token_response(token='FAKE_MAMMOTH_TOKEN'):
    return {'response': {'errorCode': 0, 'passToken': token,
        'userData': {'isAuthenticated': True, 'collectUserData': True,
                     'macroEligibility': 'synthetic', 'microEligibility': 'synthetic'}}}


def test_full_bootstrap_profile_zero_and_explicit_selection(monkeypatch):
    obj = auth.PassIdAuth('FAKE_PASS_ID')
    post = Mock(return_value=token_response())
    get = Mock(side_effect=[{'URLAuthenticate': auth.BASE + '/authenticate.json/webapp/6.0'},
        {'contents': [{'type': 'profile', 'profileId': 0, 'displayName': 'Default', 'isKidsProfile': False},
                      {'type': 'profile', 'profileId': 42, 'displayName': 'Mammoth', 'isKidsProfile': False},
                      {'type': 'button'}]},
        {'token': 'a' * 32, 'settings': {'userStatus': 'abonne'}}])
    monkeypatch.setattr(obj, '_json', post)
    monkeypatch.setattr(obj, '_get', get)
    monkeypatch.setattr(auth, 'choose_profile', lambda profiles, path, **kwargs: profiles[1])
    result = obj.bootstrap()
    assert result.profile_id == '42'
    assert result.token_pass == 'FAKE_MAMMOTH_TOKEN'
    assert get.call_args_list[0].args[1]['xx-profile-id'] == '0'
    assert get.call_args_list[1].args[1]['xx-profile-id'] == '0'
    assert 'deviceId' not in get.call_args_list[0].args[0]
    assert 'userId' not in get.call_args_list[0].args[0]
    assert get.call_args_list[2].args[1]['xx-profile-id'] == '42'
    assert post.call_args.kwargs['data']['passId'] == 'FAKE_PASS_ID'


def test_renew_is_atomic_and_preserves_profile(monkeypatch):
    obj = auth.PassIdAuth('FAKE_PASS_ID')
    monkeypatch.setattr(obj, '_json', Mock(side_effect=[token_response('FIRST'), token_response('SECOND'), {'response': {'errorCode': 9}}]))
    obj.renew(); obj.headers['xx-profile-id'] = '42'
    obj.ensure(); assert obj.headers['tokenPass'] == 'FIRST'
    obj.ensure(force=True); assert obj.headers == {'tokenPass': 'SECOND', 'xx-profile-id': '42'}
    with pytest.raises(BootstrapError): obj.ensure(force=True)
    assert obj.headers['tokenPass'] == 'SECOND'
    assert 'FIRST' in obj.secrets and 'SECOND' in obj.secrets


@pytest.mark.parametrize('result', [{'response': {'errorCode': 0, 'passToken': ''}},
    {'response': {'errorCode': 0, 'passToken': 'FAKE', 'userData': {'isAuthenticated': False}}}])
def test_invalid_authentication_rejected(monkeypatch, result):
    obj = auth.PassIdAuth('FAKE_PASS_ID')
    monkeypatch.setattr(obj, '_json', lambda *a, **k: result)
    with pytest.raises(BootstrapError):obj.renew()
    assert not obj.headers


def test_no_insecure_keyring_fallback(monkeypatch):
    monkeypatch.setattr(auth.keyring, 'get_keyring', lambda: Mock())
    with pytest.raises(BootstrapError, match='secure OS'):auth.secure_backend()


def test_keyring_set_replace_delete_masked(monkeypatch):
    backend = Mock()
    monkeypatch.setattr(auth, 'secure_backend', lambda: backend)
    monkeypatch.setattr(auth.getpass, 'getpass', lambda prompt: 'FAKE_PASS_ID')
    auth.vault('set');auth.vault('set');auth.vault('delete')
    assert backend.set_password.call_count == 2
    backend.delete_password.assert_called_once_with(auth.SERVICE, auth.ACCOUNT)


def test_profile_memory_contains_only_id(tmp_path, monkeypatch):
    path = tmp_path/'profile.json'
    monkeypatch.setattr(auth, 'profile_path', lambda: path)
    monkeypatch.setattr('builtins.input', Mock(side_effect=['2', '']))
    profiles = [{'profileId': 0, 'displayName': 'Default'}, {'profileId': 42, 'displayName': 'Mammoth', 'profileToken': 'FAKE_SECRET'}]
    assert auth.choose_profile(profiles)['profileId'] == 42
    assert not path.exists()
    auth.remember_profile(path, '42')
    assert path.read_text() == '{"profileId": 42}'
    assert auth.choose_profile(profiles)['profileId'] == 42


def test_auth_errors_do_not_expose_credentials(monkeypatch):
    session = Mock();session.__enter__ = Mock(return_value=session);session.__exit__ = Mock(return_value=False)
    session.request.side_effect = RuntimeError('FAKE_PASS_ID FAKE_SECRET')
    monkeypatch.setattr(auth.requests, 'Session', lambda: session)
    with pytest.raises(BootstrapError) as error:auth.PassIdAuth('FAKE_PASS_ID').renew()
    assert 'FAKE' not in str(error.value)
    assert error.value.__context__ is None


def test_absent_profile_has_no_default_and_removes_preference(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    auth.remember_profile(path, '99')
    prompts = []

    def answer(prompt):
        assert not path.exists()
        prompts.append(prompt)
        return '1'

    monkeypatch.setattr('builtins.input', answer)
    selected = auth.choose_profile([{'profileId': 42, 'displayName': 'Flo'}], path)
    assert selected['profileId'] == 42
    assert prompts == ['Choose profile: ']
    assert not path.exists()


@pytest.mark.parametrize('failure_stage', ['token', 'profiles', 'authenticate'])
def test_failed_bootstrap_preserves_preference(tmp_path, monkeypatch, failure_stage):
    path = tmp_path / 'profile.json'
    auth.remember_profile(path, '99')
    before = path.read_bytes()
    obj = auth.PassIdAuth('FAKE_PASS_ID', profile_file=path)
    monkeypatch.setattr(obj, '_json', Mock(return_value=token_response()))
    profiles = {'contents': [{'type': 'profile', 'profileId': 42,
                             'displayName': 'Flo', 'isKidsProfile': False}]}
    responses = [{'URLAuthenticate': auth.BASE + '/authenticate.json/webapp/6.0'},
                 profiles, BootstrapError('Authentication HTTP 403')]
    if failure_stage == 'token':
        obj._json.side_effect = BootstrapError('Authentication request failed')
    elif failure_stage == 'profiles':
        responses[1] = BootstrapError('Authentication request failed')
    monkeypatch.setattr(obj, '_get', Mock(side_effect=responses))
    monkeypatch.setattr('builtins.input', lambda _: '1')
    with pytest.raises(BootstrapError):
        obj.bootstrap()
    assert path.read_bytes() == before


def test_profile_saved_only_after_successful_authentication(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    auth.remember_profile(path, '99')
    obj = auth.PassIdAuth('FAKE_PASS_ID', profile_file=path)
    monkeypatch.setattr(obj, '_json', Mock(return_value=token_response()))
    calls = []

    def get(url, headers):
        calls.append(url)
        assert auth.remembered_profile(path) == '99'
        if len(calls) == 1:
            return {'URLAuthenticate': auth.BASE + '/authenticate.json/webapp/6.0'}
        if len(calls) == 2:
            return {'contents': [{'type': 'profile', 'profileId': 42,
                                  'displayName': 'Flo', 'isKidsProfile': False}]}
        return {'token': 'a' * 32, 'settings': {'userStatus': 'abonne'}}

    monkeypatch.setattr(obj, '_get', get)
    monkeypatch.setattr('builtins.input', lambda _: '1')
    assert obj.bootstrap().profile_id == '42'
    assert auth.remembered_profile(path) == '42'


def test_application_profile_preferences_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, 'profile_path', PROFILE_PATH)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setattr(auth.sys, 'platform', 'linux')
    catalog = auth.profile_path('mycanal-catalog')
    expiry = auth.profile_path('mycanal-expiry-tracker')
    auth.remember_profile(catalog, '42')
    auth.remember_profile(expiry, '77')
    assert auth.remembered_profile(catalog) == '42'
    assert auth.remembered_profile(expiry) == '77'


@pytest.mark.parametrize('status', [401, 403])
def test_shared_recovery_refreshes_current_headers(status):
    from mycanal_hodor_core.http import HodorError
    authentication = Mock(headers={'tokenPass': 'NEW', 'xx-profile-id': '42'})
    headers = {'tokenPass': 'OLD'}
    assert auth.recover_authentication(HodorError('HTTP', status_code=status),
                                       authentication, headers)
    authentication.ensure.assert_called_once_with(force=True)
    assert headers == authentication.headers


def test_shared_recovery_respects_application_coordination():
    from mycanal_hodor_core.http import HodorError
    authentication = Mock(headers={'tokenPass': 'NEW'})
    coordinator = Mock()
    assert auth.recover_authentication(HodorError('HTTP', status_code=401),
                                       authentication, {'tokenPass': 'OLD'}, coordinator)
    coordinator.assert_called_once_with()
    authentication.ensure.assert_not_called()


@pytest.mark.parametrize('status,headers,has_auth', [
    (400, {'tokenPass': 'OLD'}, True),
    (401, {}, True),
    (403, {'tokenPass': 'OLD'}, False),
])
def test_shared_recovery_does_not_change_anonymous_or_unrelated_requests(status, headers, has_auth):
    from mycanal_hodor_core.http import HodorError
    authentication = Mock() if has_auth else None
    assert not auth.recover_authentication(HodorError('HTTP', status_code=status),
                                          authentication, headers)
    if authentication is not None:
        authentication.ensure.assert_not_called()


def test_keyring_failure_has_no_sensitive_exception_context(monkeypatch):
    backend = Mock()
    backend.set_password.side_effect = RuntimeError('FAKE_PASS_ID')
    monkeypatch.setattr(auth, 'secure_backend', lambda: backend)
    monkeypatch.setattr(auth.getpass, 'getpass', lambda _: 'FAKE_PASS_ID')
    with pytest.raises(BootstrapError) as error:
        auth.vault('set')
    assert error.value.__context__ is None


def test_proactive_rotation_uses_monotonic_age(monkeypatch):
    obj = auth.PassIdAuth('FAKE_PASS_ID')
    monkeypatch.setattr(obj, '_json', Mock(side_effect=[token_response('FIRST'), token_response('SECOND')]))
    clock = Mock(return_value=10)
    monkeypatch.setattr(auth.time, 'monotonic', clock)
    obj.renew()
    clock.return_value = 10 + auth.RENEW_AFTER
    obj.ensure()
    assert obj.headers['tokenPass'] == 'SECOND'




