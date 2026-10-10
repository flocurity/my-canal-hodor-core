"""Conservative, sequential Hodor transport shared by reports and acquisition."""

import json
from collections.abc import Mapping
import math
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import requests

from .logging import get_logger
from .diagnostics import debug_failure, debug_enabled, response_body

log = get_logger(__name__)
DEFAULT_DELAY = 0.18
TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 4
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:156.0) Gecko/20100101 Firefox/156.0'
RETRY_STATUSES = {429, 502, 503, 504}


class HodorError(Exception):
    """Technical failure, independent of application report statuses."""
    def __init__(self, message: str, kind: str = 'http', status_code: int | None = None, policy_error: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.policy_error = policy_error


def hodor_policy_error(body: object) -> str | None:
    """Recognize explicit server diagnostics without retaining response contents."""
    if not isinstance(body, str) or len(body) > 8192:
        return None
    message = 'tokenPass header forbidden for this request'
    def recognized(value: object) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() == message.casefold()
        if isinstance(value, list):
            return any(recognized(v) for v in value)
        if isinstance(value, dict):
            if any(recognized(value[k]) for k in ('message', 'error', 'errors', 'text') if k in value):
                return True
            tracking = value.get('tracking')
            layer = tracking.get('dataLayer') if isinstance(tracking, dict) else None
            return isinstance(layer, dict) and recognized(layer.get('error_message'))
        return False
    if recognized(body):
        return 'token_pass_forbidden'
    try:
        return 'token_pass_forbidden' if recognized(json.loads(body)) else None
    except (ValueError, RecursionError):
        return None


def validate_api_url(url: str, resource: str | None = None) -> None:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == 'https'
            and parsed.hostname == 'hodor.canalplus.pro'
            and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and parsed.path.startswith('/api/v2/mycanal/')
            and (resource is None or parsed.path.startswith(f'/api/v2/mycanal/{resource}/'))
            and not any(part in ('.', '..') for part in unquote(parsed.path).split('/'))
            and not any(key.casefold() in ('tokenpass', 'xx-profile-id')
                        for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
            and not parsed.fragment
            and not any(char.isspace() or char == '\\' for char in url)
        )
    except ValueError:
        valid = False
    if not valid:
        raise HodorError(f'Expected a public HTTPS hodor.canalplus.pro /{resource}/ URL')


def validate_detail_url(url: str) -> None:
    validate_api_url(url, 'detail')


def build_detail_url(source_url: str, supports_detail_v5: bool = False) -> str:
    if not supports_detail_v5:
        return source_url
    # Validate before rebuilding so parsing cannot hide an invalid source URL.
    validate_detail_url(source_url)
    parsed = urlsplit(source_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    existing = [value for key, value in query if key == 'featureToggles']
    toggles = [toggle for value in existing for toggle in value.split(',') if toggle]
    if len(existing) == 1 and 'detailV5' in toggles:
        return source_url
    if 'detailV5' not in toggles:
        toggles.append('detailV5')
    # Preserve toggles explicitly supplied in the source, not the descriptor enum.
    query = [(key, value) for key, value in query if key != 'featureToggles']
    query.append(('featureToggles', ','.join(dict.fromkeys(toggles))))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
        return None
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class CanalClient:
    def __init__(self, delay: float = DEFAULT_DELAY, *,
                 diagnostic_secrets: tuple[str, ...] = ()) -> None:
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('Delay must be finite and non-negative')
        self.delay = delay
        self.diagnostic_secrets = tuple(diagnostic_secrets)
        self.session = requests.Session()
        self.session.headers['User-Agent'] = USER_AGENT
        self.session.headers['Accept-Encoding'] = 'deflate, gzip'
        self._has_requested = False
        self._cooldown = 0.0

    def __enter__(self) -> 'CanalClient':
        return self

    def __exit__(self, *args: object) -> None:
        self.session.close()

    def fetch(self, url: str, content_id: str = '') -> dict:
        return self._fetch(url, content_id, 'detail')

    def fetch_episodes(self, url: str, content_id: str = '') -> dict:
        return self._fetch(url, content_id, 'episodes')

    def _fetch(self, url: str, content_id: str, resource: str) -> dict:
        payload = self.get_json(url, content_id=content_id, resource=resource)
        if not isinstance(payload.get(resource), dict):
            debug_failure(log, 'api_structure_debug', secrets=self._diagnostic_secrets(), url=url, content_id=content_id,
                          expected=resource + ' object', payload=payload)
            raise HodorError(f'Missing or invalid {resource} object', 'parsing')
        return payload

    def _diagnostic_secrets(self, headers: dict[str, str] | None = None,
                            response: requests.Response | None = None) -> tuple[str, ...]:
        secret_names = {'tokenpass', 'authorization', 'proxy-authorization',
                        'cookie', 'set-cookie'}
        authentication = getattr(self, 'authentication', None)
        values = list(self.diagnostic_secrets)
        if authentication is not None:
            values.extend(authentication.secrets)
        for mapping in (self.session.headers, headers or {},
                        getattr(response, 'headers', {}) if response is not None else {}):
            if isinstance(mapping, Mapping):
                for key, value in mapping.items():
                    if key.casefold() in secret_names and isinstance(value, str):
                        values.append(value)
                        if key.casefold() in ('cookie', 'set-cookie'):
                            values.extend(part.partition('=')[2].strip() for part in value.split(';') if '=' in part)
                        elif key.casefold() == 'authorization':
                            values.append(value.split(' ', 1)[-1])
        cookies = self.session.cookies.get_dict()
        if isinstance(cookies, Mapping):
            values.extend(v for v in cookies.values() if isinstance(v, str))
        return tuple(value for value in values if value)

    def _response_diagnostic(self, event: str, url: str, response: requests.Response,
                             headers: dict[str, str] | None, content_id: str = '',
                             error: BaseException | None = None, **context: object) -> None:
        if not debug_enabled(log):
            return
        try:
            secrets = self._diagnostic_secrets(headers, response)
            body = response_body(response.text, secrets)
        except Exception:
            debug_failure(log, event, diagnostic_unavailable=True,
                          content_id=content_id, status_code=response.status_code)
            return
        debug_failure(log, event, error, secrets, url=url, content_id=content_id,
                      status_code=response.status_code, response_body=body,
                      response_headers=dict(response.headers) if isinstance(response.headers, Mapping) else {},
                      **context)

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """Read decompressed bytes; application-specific URL policies remain with callers."""
        response = self._request(url, '', None, headers)
        try:
            return response.content
        finally:
            response.close()

    def get_json(self, url: str, headers: dict[str, str] | None = None,
                 content_id: str = '', resource: str | None = None) -> dict:
        response = self._request(url, content_id, resource, headers)
        try:
            try:
                payload = response.json()
            except ValueError as exc:
                self._response_diagnostic('api_parsing_debug', url, response, headers, content_id, exc)
                raise HodorError('Response is not valid JSON', 'parsing') from None
            if not isinstance(payload, dict):
                self._response_diagnostic('api_parsing_debug', url, response, headers, content_id,
                                          expected='JSON object')
                raise HodorError('Expected a JSON object', 'parsing')
            return payload
        finally:
            response.close()

    def _notify_request_failure(self, reason: str, attempt: int) -> None:
        observer = getattr(self, 'failure_observer', None)
        if callable(observer):
            try:
                observer(reason, attempt)
            except Exception:
                pass  # Observability must not change transport behavior.

    def _request(self, url: str, content_id: str, resource: str | None,
                 headers: dict[str, str] | None = None) -> requests.Response:
        validate_api_url(url, resource)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self._has_requested:
                time.sleep(max(random.uniform(self.delay, self.delay + 0.15), self._cooldown))
            self._cooldown = 0.0
            self._has_requested = True
            try:
                # Redirects must not turn a validated public URL into another target.
                options = {'headers': headers} if headers is not None else {}
                started = time.monotonic()
                outcome = 'failed'
                status = None
                try:
                    response = self.session.get(
                        url, timeout=TIMEOUT_SECONDS, allow_redirects=False, **options,
                    )
                    status = response.status_code
                    outcome = 'response'
                finally:
                    # Measure the actual attempt, excluding pacing and retry backoff.
                    log.debug('http_attempt_timing', content_id=content_id,
                              resource=resource, attempt=attempt, outcome=outcome,
                              status_code=status,
                              duration_s=round(time.monotonic() - started, 6))
            except (requests.Timeout, requests.ConnectionError) as exc:
                reason = type(exc).__name__
                status_code = None
                retry_after = None
                debug_failure(log, 'api_transport_debug', exc, self._diagnostic_secrets(headers),
                              url=url, content_id=content_id, attempt=attempt)
            except requests.RequestException as exc:
                debug_failure(log, 'api_transport_debug', exc, self._diagnostic_secrets(headers),
                              url=url, content_id=content_id, attempt=attempt)
                self._notify_request_failure(type(exc).__name__, attempt)
                raise HodorError(type(exc).__name__) from None
            else:
                status_code = response.status_code
                if status_code == 200:
                    return response
                try:
                    if status_code not in RETRY_STATUSES:
                        code = hodor_policy_error(response.text) if status_code == 400 else None
                        log.warning('hodor_error', content_id=content_id,
                                    status_code=status_code, policy_error=code)
                        self._response_diagnostic('api_http_debug', url, response, headers, content_id,
                                                  resource=resource, attempt=attempt, policy_error=code)
                        self._notify_request_failure(f'HTTP {status_code}', attempt)
                        raise HodorError(f'HTTP {status_code}', status_code=status_code, policy_error=code)
                    reason = f'HTTP {status_code}'
                    retry_after = retry_after_seconds(response.headers.get('Retry-After'))
                    self._response_diagnostic('api_http_debug', url, response, headers, content_id,
                                              resource=resource, attempt=attempt)
                finally:
                    response.close()

            self._notify_request_failure(reason, attempt)
            if retry_after is not None:
                self._cooldown = retry_after
            else:
                self._cooldown = 2 ** (attempt - 1) + random.uniform(0, 0.25)
            if attempt == MAX_ATTEMPTS:
                # Preserve cooldown across items, including the final 429 attempt.
                raise HodorError(f'{reason}; exhausted {MAX_ATTEMPTS} attempts', status_code=status_code)
            log.warning('api_retry', content_id=content_id, status_code=status_code,
                        attempt=attempt, delay=max(self.delay, self._cooldown), reason=reason)
        raise AssertionError('Unreachable retry state')
