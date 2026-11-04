"""Historical interactive behavior and side-effect-free explicit profile choice."""
from unittest.mock import Mock

import pytest

from mycanal_hodor_core import authentication as auth
from mycanal_hodor_core.bootstrap import BootstrapError

PROFILES = {'contents': [
    {'type': 'profile', 'profileId': 0, 'displayName': 'Default', 'isKidsProfile': False},
    {'type': 'profile', 'profileId': 42, 'displayName': 'Selected', 'isKidsProfile': False},
]}


def preference(path, state):
    if state == 'missing':
        return None
    contents = {'valid': b'{"profileId":42}', 'invalid': b'{"profileId":99}',
                'malformed': b'not JSON'}[state]
    path.write_bytes(contents)
    return contents


def configured(path, monkeypatch, failure=None):
    obj = auth.PassIdAuth('SYNTHETIC_PASS', profile_file=path)
    obj.headers.update({'tokenPass': 'OLD_SYNTHETIC_TOKEN', 'xx-profile-id': '11'})
    obj.renewed_at = 1.0
    post = Mock(return_value={'response': {'errorCode': 0,
        'passToken': 'NEW_SYNTHETIC_TOKEN', 'userData': {'isAuthenticated': True}}})
    if failure == 'renew':
        post.side_effect = BootstrapError('synthetic token failure')
    monkeypatch.setattr(obj, '_json', post)
    stages = []
    initial = {'URLAuthenticate': auth.BASE + '/authenticate.json/webapp/6.0'}

    def get(url, headers):
        stage = ('init', 'profiles', 'authenticate')[len(stages)]
        stages.append((stage, dict(headers)))
        if stage == failure:
            raise BootstrapError('synthetic failure')
        if stage == 'init':
            return {} if failure == 'missing_navigation' else initial
        if stage == 'profiles':
            return {'contents': []} if failure == 'invalid_profiles' else PROFILES
        return {'token': 'invalid' if failure == 'invalid_authenticate' else 'a'*32,
                'settings': {'userStatus': 'abonne'}}

    monkeypatch.setattr(obj, '_get', get)
    return obj, stages


@pytest.mark.parametrize('state', ['valid', 'invalid', 'malformed', 'missing'])
def test_historical_no_argument_still_prompts_and_saves_only_after_success(tmp_path, monkeypatch, state):
    path = tmp_path / 'profile.json'
    before = preference(path, state)
    obj, stages = configured(path, monkeypatch)
    prompts = []
    original_choose = auth.choose_profile

    def answer(prompt):
        prompts.append(prompt)
        assert path.read_bytes() == before if before is not None else not path.exists()
        return '' if state == 'valid' else '2'

    monkeypatch.setattr('builtins.input', answer)
    choose = Mock(wraps=original_choose)
    monkeypatch.setattr(auth, 'choose_profile', choose)
    remember = Mock(wraps=auth.remember_profile)
    monkeypatch.setattr(auth, 'remember_profile', remember)
    result = obj.bootstrap()
    assert result.profile_id == '42'
    assert prompts == ['Choose profile [2]: ' if state == 'valid' else 'Choose profile: ']
    choose.assert_called_once_with(PROFILES['contents'], path, invalidate_preference=False)
    remember.assert_called_once_with(path, '42')
    assert auth.remembered_profile(path) == '42'
    assert stages[0][1]['xx-profile-id'] == stages[1][1]['xx-profile-id'] == '0'
    assert stages[2][1] == obj.headers == {'tokenPass': 'NEW_SYNTHETIC_TOKEN', 'xx-profile-id': '42'}


@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('failure', ['renew', 'init', 'profiles', 'invalid_profiles',
                                   'missing_navigation', 'authenticate', 'invalid_authenticate'])
def test_failures_preserve_historical_header_side_effects_and_preference(tmp_path, monkeypatch, explicit, failure):
    path = tmp_path / 'profile.json'
    before = preference(path, 'invalid')
    stat = path.stat()
    obj, stages = configured(path, monkeypatch, failure)
    choose = Mock(wraps=auth.choose_profile)
    remember = Mock(wraps=auth.remember_profile)
    monkeypatch.setattr(auth, 'choose_profile', choose)
    monkeypatch.setattr(auth, 'remember_profile', remember)
    prompt = Mock(return_value='2', side_effect=AssertionError('No explicit prompt') if explicit else None)
    monkeypatch.setattr('builtins.input', prompt)
    with pytest.raises(BootstrapError):
        obj.bootstrap(profile_id='42') if explicit else obj.bootstrap()
    assert path.read_bytes() == before
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns)
    remember.assert_not_called()
    reached_selection = failure in ('missing_navigation', 'authenticate', 'invalid_authenticate')
    assert obj.headers == {
        'tokenPass': 'OLD_SYNTHETIC_TOKEN' if failure == 'renew' else 'NEW_SYNTHETIC_TOKEN',
        'xx-profile-id': '42' if reached_selection else '11',
    }
    if explicit:
        choose.assert_not_called()
        prompt.assert_not_called()
    elif reached_selection:
        choose.assert_called_once_with(PROFILES['contents'], path, invalidate_preference=False)
    else:
        choose.assert_not_called()


@pytest.mark.parametrize('state', ['valid', 'invalid', 'malformed', 'missing'])
def test_explicit_success_never_creates_deletes_or_rewrites_preference(tmp_path, monkeypatch, state):
    path = tmp_path / 'profile.json'
    before = preference(path, state)
    stat = path.stat() if path.exists() else None
    obj, stages = configured(path, monkeypatch)
    choose = Mock(side_effect=AssertionError('No interactive choice'))
    remember = Mock(side_effect=AssertionError('No preference write'))
    monkeypatch.setattr(auth, 'choose_profile', choose)
    monkeypatch.setattr(auth, 'remember_profile', remember)
    assert obj.bootstrap(profile_id='42').profile_id == '42'
    choose.assert_not_called()
    remember.assert_not_called()
    if before is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == before
        assert (path.stat().st_ino, path.stat().st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns)
    assert stages[-1][1]['xx-profile-id'] == '42'


def test_unavailable_explicit_profile_does_not_change_selected_header_or_preference(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    before = preference(path, 'valid')
    obj, stages = configured(path, monkeypatch)
    monkeypatch.setattr(auth, 'choose_profile', Mock(side_effect=AssertionError('No prompt')))
    with pytest.raises(BootstrapError, match='unavailable'):
        obj.bootstrap(profile_id='999')
    assert len(stages) == 2
    assert obj.headers == {'tokenPass': 'NEW_SYNTHETIC_TOKEN', 'xx-profile-id': '11'}
    assert path.read_bytes() == before


def test_historical_cancel_keeps_new_token_previous_profile_and_invalid_preference(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    before = preference(path, 'invalid')
    obj, _ = configured(path, monkeypatch)
    monkeypatch.setattr('builtins.input', Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        obj.bootstrap()
    assert obj.headers == {'tokenPass': 'NEW_SYNTHETIC_TOKEN', 'xx-profile-id': '11'}
    assert path.read_bytes() == before
