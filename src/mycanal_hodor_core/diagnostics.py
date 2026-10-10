"""Shared secret redaction and debug-only failure diagnostics."""
import json
import logging
import math
import re
import traceback
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_KEYS = {'passid', 'tokenpass', 'xxprofileid', 'authorization', 'proxyauthorization',
                'cookie', 'setcookie', 'credentials', 'password', 'accesstoken', 'refreshtoken', 'profileid', 'userid', 'subscriberid', 'sig', 'expires', 'exp', 'policy', 'keypairid'}
_TOKEN_PATH = re.compile(r'(/api/v2/mycanal/[^/\s?]+/)[^/\s?]+(?=/)')


def _secret_key(key: str) -> bool:
    name = re.sub(r'[^a-z0-9]', '', key.casefold())
    return name in _SECRET_KEYS or 'token' in name or 'signature' in name


def redact(value: object, secrets: tuple[str, ...] = ()) -> object:
    if isinstance(value, dict):
        if isinstance(value.get('name'), str) and _secret_key(value['name']):
            return {'name': '[REDACTED HEADER]'}
        return {str(k): redact(v, secrets) for k, v in value.items() if not _secret_key(str(k))}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        text = value
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[REDACTED]')
        # Text error envelopes and tracebacks can contain header assignments.
        text = re.sub(r'(?im)(\b(?:pass[_-]?id|tokenPass|xx-profile-id|authorization|proxy-authorization|cookie|set-cookie|password|access[_-]?token|refresh[_-]?token)\s*[:=]\s*)[^\r\n]+', r'\1[REDACTED]', text)
        text = _TOKEN_PATH.sub(r'\1[REDACTED]', text)
        if text.startswith(('https://', 'http://')):
            try:
                parsed = urlsplit(text)
                query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                         if not _secret_key(k)]
                text = urlunsplit(parsed._replace(query=urlencode(query), fragment=''))
                if parsed.username or parsed.password:
                    return '[REDACTED URL]'
            except ValueError:
                return '[REDACTED URL]'
        return text
    if isinstance(value, float) and not math.isfinite(value):
        return '[NONFINITE NUMBER]'
    return value



def debug_enabled(log) -> bool:
    check = getattr(log, 'is_enabled_for', None)
    return check(logging.DEBUG) if callable(check) else True


def debug_failure(log, event: str, error: BaseException | None = None,
                  secrets: tuple[str, ...] = (), **context: object) -> None:
    """Sanitize exception chains without locals; diagnostics must not mask failures."""
    try:
        if not debug_enabled(log):
            return
        if error is not None:
            context['exception_type'] = type(error).__name__
            traces = []
            current = error
            seen = set()
            # Public wrappers intentionally suppress causes; DEBUG may still diagnose them.
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                traces.append(''.join(traceback.TracebackException.from_exception(
                    current, capture_locals=False).format(chain=False)))
                current = current.__cause__ or current.__context__
            context['traceback'] = '\nUnderlying exception:\n'.join(traces)
        sanitized = redact(context, secrets)
        # Structured diagnostic context is easier to inspect as a readable block.
        for key, value in sanitized.items():
            if isinstance(value, (dict, list)):
                sanitized[key] = json.dumps(value, ensure_ascii=False, indent=2)
        log.debug(event, **sanitized)
    except Exception:
        # A renderer/sanitation failure must not replace the original operation error.
        return


def response_body(text: object, secrets: tuple[str, ...]) -> object:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return redact(text, secrets)
    return json.dumps(redact(value, secrets), ensure_ascii=False, indent=2)
