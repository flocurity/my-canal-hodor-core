"""Ephemeral passId authentication; only the OS vault stores credentials."""
import getpass
import json
import os
import re
import sys
import time
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import keyring
from keyring.backend import KeyringBackend
import requests

from mycanal_hodor_core.bootstrap import BootstrapError, HodorRuntimeContext
from mycanal_hodor_core.http import HodorError, validate_api_url

SERVICE = 'mycanal-catalog'
ACCOUNT = 'passId'
BASE = 'https://hodor.canalplus.pro/api/v2/mycanal'
CREATE = 'https://pass-api-v2.canal-plus.com/provider/services/cpfra-fr/public/createToken'
# Conservative proactive rotation; Hodor does not advertise token lifetime.
RENEW_AFTER = 300
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
           'Origin': 'https://www.canalplus.com', 'Referer': 'https://www.canalplus.com/'}


def secure_backend() -> KeyringBackend:
    backend = keyring.get_keyring()
    # Select only a supported native backend, even inside a chainer.
    allowed = ('keyring.backends.macOS', 'keyring.backends.Windows',
               'keyring.backends.SecretService', 'keyring.backends.kwallet',
               'keyring.backends.libsecret')
    candidates = backend.backends if type(backend).__module__ == 'keyring.backends.chainer' else [backend]
    for candidate in candidates:
        if type(candidate).__module__ in allowed and candidate.priority > 0:
            return candidate
    raise BootstrapError('No supported secure OS keyring backend is available')


def vault(action: str) -> str | None:
    if action not in ('get', 'set', 'delete'):
        raise BootstrapError('Invalid keyring operation')
    try:
        backend = secure_backend()
        if action == 'set':
            value = getpass.getpass('passId (hidden): ')
            if not value or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise BootstrapError('Invalid passId')
            backend.set_password(SERVICE, ACCOUNT, value)
            return None
        if action == 'delete':
            if backend.get_password(SERVICE, ACCOUNT) is not None:
                backend.delete_password(SERVICE, ACCOUNT)
            return None
        return backend.get_password(SERVICE, ACCOUNT)
    except BootstrapError:
        raise
    except Exception:
        pass
    # Raise outside the handler so suppressed underlying errors cannot leak secrets
    # through diagnostic traversal of __context__.
    raise BootstrapError('Secure keyring operation failed') from None


def profile_path(application: str = SERVICE) -> Path:
    if sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming'))
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library/Application Support'
    else:
        root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return root / application / 'profile.json'


def remembered_profile(path: Path) -> str | None:
    """Read a preference without changing it on missing or malformed input."""
    try:
        value = json.loads(path.read_text()).get('profileId')
    except (OSError, ValueError, AttributeError):
        return None
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def remember_profile(path: Path, profile_id: str) -> None:
    """Persist only a selected identifier, after successful authentication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, prefix='.profile-',
                                         delete=False) as stream:
            temporary = Path(stream.name)
            value = int(profile_id) if profile_id.isascii() and profile_id.isdigit() else profile_id
            json.dump({'profileId': value}, stream)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def choose_profile(profiles: list[dict], path: Path | None = None, *,
                   invalidate_preference: bool = True) -> dict:
    path = path if path is not None else profile_path()
    remembered = remembered_profile(path)
    default = next((i for i, p in enumerate(profiles, 1)
                    if str(p['profileId']) == remembered), None)
    # Callers supply a fully validated API list. Network/auth failures must never
    # reach this point and therefore cannot invalidate the saved preference.
    if remembered is not None and default is None and invalidate_preference:
        path.unlink(missing_ok=True)
    for i, profile in enumerate(profiles, 1):
        name = ''.join(c for c in profile.get('displayName', 'Profile') if c.isprintable())
        print(f'{i}. {name}')
    while True:
        answer = input(f'Choose profile{f" [{default}]" if default else ""}: ').strip()
        if not answer and default:
            selected = default
        elif answer.isascii() and answer.isdigit():
            selected = int(answer)
        else:
            continue
        if 1 <= selected <= len(profiles):
            return profiles[selected - 1]


def recover_authentication(error: HodorError, authentication: 'PassIdAuth | None',
                           headers: dict[str, str],
                           renew: Callable[[], object] | None = None) -> bool:
    """Recover one authenticated failure; the caller retries once, outside a loop.

    Applications can coordinate renewal with their own validation policy. This
    primitive never interprets editorial policies or commits personal state.
    """
    if (error.status_code not in (401, 403) or authentication is None
            or not any(k.casefold() == 'tokenpass' for k in headers)):
        return False
    if renew is None:
        authentication.ensure(force=True)
    else:
        renew()
    current_names = {name.casefold() for name in authentication.headers}
    for name in list(headers):
        if name.casefold() in current_names:
            del headers[name]
    headers.update(authentication.headers)
    return True


class PassIdAuth:
    def __init__(self, pass_id: str, *, profile_file: Path | None = None) -> None:
        if not isinstance(pass_id, str) or not pass_id or any(ord(c) < 33 or ord(c) > 126 for c in pass_id):
            raise BootstrapError('Invalid passId')
        self.profile_file = profile_file
        self.pass_id = pass_id
        self.headers: dict[str, str] = {}
        self.secrets: list[str] = [pass_id]
        self.renewed_at: float | None = None
        self.rotation_observer: Callable[[], None] | None = None

    def _json(self, method: str, url: str, **options) -> dict:
        # Authentication never shares crawler cookies or logs raw bodies/errors.
        try:
            with requests.Session() as session:
                response = session.request(method, url, headers=HEADERS,
                                           timeout=30, allow_redirects=False, **options)
                try:
                    if response.status_code != 200:
                        raise BootstrapError(f'Authentication HTTP {response.status_code}')
                    result = response.json()
                finally:
                    response.close()
            if not isinstance(result, dict):
                raise BootstrapError('Invalid authentication response')
            return result
        except BootstrapError:
            raise
        except Exception:
            pass
        raise BootstrapError('Authentication request failed') from None

    def _get(self, url: str, headers: dict | None = None) -> dict:
        try:
            validate_api_url(url)
        except HodorError:
            raise BootstrapError('Invalid authentication navigation') from None
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'hodor.canalplus.pro' or parsed.port is not None or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith('/api/v2/mycanal/'):
            raise BootstrapError('Invalid authentication navigation')
        # Merge here rather than passing duplicate headers to requests.
        return self._get_with_headers(url, {**HEADERS, **(headers or {})})

    def _get_with_headers(self, url: str, headers: dict) -> dict:
        try:
            with requests.Session() as session:
                response = session.get(url, headers=headers, timeout=30, allow_redirects=False)
                try:
                    if response.status_code != 200:
                        raise BootstrapError(f'Authentication HTTP {response.status_code}')
                    result = response.json()
                finally:
                    response.close()
            if not isinstance(result, dict):
                raise BootstrapError('Invalid authentication response')
            return result
        except BootstrapError:
            raise
        except Exception:
            pass
        raise BootstrapError('Authentication request failed') from None

    def renew(self) -> None:
        result = self._json('POST', CREATE, data={'portailId': 'vbdTj7eb6aM.',
            'media': 'web', 'vect': 'INTERNET', 'passIdType': 'pass',
            'noCache': 'false', 'passId': self.pass_id}).get('response', {})
        if not isinstance(result, dict) or result.get('errorCode') != 0:
            raise BootstrapError('passId rejected; register a fresh passId')
        token = result.get('passToken')
        user = result.get('userData')
        if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token) or not isinstance(user, dict) or user.get('isAuthenticated') is not True:
            raise BootstrapError('Personal authentication was not confirmed')
        renewing = self.renewed_at is not None
        self.secrets.append(token)
        self.headers['tokenPass'] = token
        self.user = user
        self.renewed_at = time.monotonic()
        if renewing and self.rotation_observer is not None:
            self.rotation_observer()

    def ensure(self, force: bool = False) -> None:
        if force or self.renewed_at is None or time.monotonic() - self.renewed_at >= RENEW_AFTER:
            self.renew()

    def bootstrap(self) -> HodorRuntimeContext:
        self.renew()
        initial = {**self.headers, 'xx-profile-id': '0'}
        init = self._get(BASE + '/me/cpfra/init/webapp/6.0?' + urlencode({
            'allowedProfiles': 'kids,adult', 'refreshProfiles': 'false'}), initial)
        response = self._get(BASE + '/me/Profiles?' + urlencode({
            'displayTemplate': 'profilesSelection', 'allowedProfiles': 'kids,adult',
            'profilesTemplateVersion': '2'}), initial)
        contents = response.get('contents')
        if not isinstance(contents, list):
            raise BootstrapError('Invalid profile list')
        profiles = [p for p in contents if isinstance(p, dict) and p.get('type') == 'profile']
        if not profiles or any(type(p.get('profileId')) is not int or p['profileId'] < 0 or type(p.get('isKidsProfile')) is not bool or not isinstance(p.get('displayName'), str) for p in profiles) or len({p['profileId'] for p in profiles}) != len(profiles):
            raise BootstrapError('Invalid profile list')
        path = self.profile_file if self.profile_file is not None else profile_path()
        # Delay destructive preference changes until final authentication succeeds.
        # An absent remembered profile still has no default during selection.
        invalid_preference = remembered_profile(path) not in {str(p['profileId']) for p in profiles}
        profile = choose_profile(profiles, path, invalidate_preference=False)
        pid = str(profile['profileId'])
        self.headers['xx-profile-id'] = pid
        url = init.get('URLAuthenticate')
        if not isinstance(url, str) or urlsplit(url).path != '/api/v2/mycanal/authenticate.json/webapp/6.0':
            raise BootstrapError('Missing authentication navigation')
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query))
        query.update({'isAuthenticated': '1', 'isKidsProfile': '1' if profile['isKidsProfile'] else '0',
            'collectUserData': '1' if self.user.get('collectUserData') is True else '0',
            'macros': self.user.get('macroEligibility', ''), 'micros': self.user.get('microEligibility', ''),
            'offerLocation': self.user.get('offerLinguisticRegion', ''),
            'offerZone': self.user.get('currentOfferZone', self.user.get('offerZone', '')), 'language': 'fr'})
        result = self._get(urlunsplit(parsed._replace(query=urlencode(query))), self.headers)
        token = result.get('token')
        if not isinstance(token, str) or not re.fullmatch('[a-fA-F0-9]{32}', token) or not isinstance(result.get('settings'), dict) or result['settings'].get('userStatus') == 'prospect':
            raise BootstrapError('Invalid authenticated Hodor context')
        self.secrets.append(token)
        if invalid_preference:
            path.unlink(missing_ok=True)
        remember_profile(path, pid)
        return HodorRuntimeContext(token, self.headers['tokenPass'], pid)
