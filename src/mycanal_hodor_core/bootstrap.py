"""Parse authenticated browser GET requests as inert, ephemeral runtime context.

The parser validates the Hodor origin; consumers enforce their endpoint policy.
Interactive input is optional and never configures logging or stores history.
"""
import re
import shlex
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit
from .http import HodorError, validate_api_url


class BootstrapError(ValueError):
    """A safe, non-sensitive browser bootstrap failure."""


@dataclass(frozen=True, repr=False)
class HodorRuntimeContext:
    hodor_token: str
    token_pass: str
    profile_id: str
    query_parameters: tuple[tuple[str, str], ...] = ()
    request_url: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {"tokenPass": self.token_pass, "xx-profile-id": self.profile_id}

    @property
    def api_base(self) -> str:
        return "https://hodor.canalplus.pro/api/v2/mycanal"


def parse_curl(command: str) -> HodorRuntimeContext:
    """Parse a restricted GET cURL grammar as data, never as shell code."""
    try:
        args = shlex.split(command.replace('\r\n', '\n').replace('\\\n', ''), posix=True)
    except ValueError:
        raise BootstrapError('Invalid Copy-as-cURL quoting') from None
    if not args or args.pop(0) != 'curl':
        raise BootstrapError('Expected a Firefox Copy-as-cURL request')
    url = None
    headers = {}
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg in ('--compressed', '--globoff'):
            continue
        option, separator, inline = arg.partition('=')
        if option in ('-H', '--header', '-X', '--request', '--url',
                      '-A', '--user-agent', '-b', '--cookie'):
            if separator:
                value = inline
            else:
                if index == len(args):
                    raise BootstrapError('Incomplete Copy-as-cURL option')
                value = args[index]
                index += 1
            if option in ('-H', '--header'):
                name, colon, value = value.partition(':')
                if not colon:
                    raise BootstrapError('Invalid copied header')
                name, value = name.strip().lower(), value.strip()
                if name in ('tokenpass', 'xx-profile-id'):
                    if name in headers or not value or any(
                        ord(char) < 33 or ord(char) > 126 for char in value
                    ):
                        raise BootstrapError('Invalid or duplicate required header')
                    headers[name] = value
            elif option in ('-X', '--request') and value != 'GET':
                raise BootstrapError('Expected a GET Hodor request')
            elif option == '--url':
                if url is not None:
                    raise BootstrapError('Expected exactly one request URL')
                url = value
            continue
        if arg.startswith('https://') and url is None:
            url = arg
        else:
            raise BootstrapError('Unrecognized Copy-as-cURL option or shell syntax')
    try:
        validate_api_url(url or '')
        match = re.fullmatch(r'/api/v2/mycanal/[^/]+/([a-fA-F0-9]{32})/[^\s]+',
                             urlsplit(url).path)
    except (HodorError, ValueError):
        match = None
    if match is None:
        raise BootstrapError('Expected a Hodor API URL and a valid path token')
    if 'tokenpass' not in headers:
        raise BootstrapError('Missing tokenPass header')
    if 'xx-profile-id' not in headers:
        raise BootstrapError('Missing xx-profile-id header')
    query = tuple(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    if any(key.casefold() in ('tokenpass', 'xx-profile-id') for key, _ in query):
        raise BootstrapError('Authentication context must use headers')
    return HodorRuntimeContext(match[1], headers['tokenpass'], headers['xx-profile-id'], query, url)


def read_curl(instruction: str = 'Paste Firefox "Copy as cURL" from myCANAL > Mes Vidéos.') -> str:
    print(instruction)
    print('Sensitive request: do not share or save it. Press Esc, then Enter to submit.')
    import prompt_toolkit

    # No history/completer: credentials remain transient input.
    return prompt_toolkit.prompt('> ', multiline=True)


