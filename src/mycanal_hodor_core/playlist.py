"""Shared, complete playlist pagination; observations never infer removals."""
import json
from collections.abc import Callable, Iterable
from urllib.parse import urlencode
from .logging import get_logger

log = get_logger(__name__)


class PlaylistError(ValueError):
    """A safe playlist contract error, without authenticated destinations."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlaylistError('Unexpected duplicate JSON field')
        result[key] = value
    return result


def acquire_playlist_pages(fetch_page: Callable[[str], bytes], token: str, *,
                           query_parameters: Iterable[tuple[str, str]] = (),
                           max_pages: int = 1000, max_contents: int | None = None) -> list[bytes]:
    pages, cursors = [], set()
    cursor = None
    total = 0
    preserved_query = [(key, value) for key, value in query_parameters
                       if key.casefold() not in ('get', 'maxcontentremaining', 'after')]
    for page_number in range(1, max_pages+1):
        query = list(preserved_query)
        query.extend([('get', '100'), ('maxContentRemaining', '500')])
        if cursor is not None:
            query.append(('after', cursor))
        url = (f'https://hodor.canalplus.pro/api/v2/mycanal/me/{token}/lists/playlist?'
               + urlencode(query))
        raw = fetch_page(url)
        try:
            payload = json.loads(raw, object_pairs_hook=_object)
        except (ValueError, UnicodeError, RecursionError):
            raise PlaylistError('Response is not valid JSON') from None
        if not isinstance(payload, dict) or not isinstance(payload.get('contents'), list):
            raise PlaylistError('Unexpected playlist response')
        paging = payload.get('paging')
        if not isinstance(paging, dict) or type(paging.get('hasNextPage')) is not bool:
            raise PlaylistError('Unexpected playlist paging')
        count = len(payload['contents'])
        total += count
        if count > 100 or max_contents is not None and total > max_contents:
            raise PlaylistError('Unexpected playlist size')
        pages.append(raw)
        log.info('playlist_page_acquired', page=page_number, contents=count)
        if not paging['hasNextPage']:
            log.info('playlist_acquisition_complete', pages=page_number, contents=total)
            return pages
        cursor = paging.get('idEnd')
        if not isinstance(cursor, str) or not cursor.strip() or any(ord(c)<32 for c in cursor):
            raise PlaylistError('Missing or invalid pagination cursor')
        if cursor in cursors:
            raise PlaylistError('Pagination cursor loop')
        cursors.add(cursor)
    raise PlaylistError('Incomplete acquisition: page limit exceeded')
