import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
import pytest
from mycanal_hodor_core.playlist import acquire_playlist_pages, PlaylistError

TOKEN = 'a'*32


def page(more=False, cursor=None):
    return json.dumps({'contents': [], 'paging': {'hasNextPage': more, 'idEnd': cursor}}).encode()


def test_complete_pagination_goes_beyond_expiry_specific_five_page_limit():
    fetch = Mock(side_effect=[page(True, str(i)) for i in range(6)] + [page()])
    pages = acquire_playlist_pages(fetch, TOKEN)
    assert len(pages) == 7
    assert parse_qs(urlsplit(fetch.call_args_list[1].args[0]).query)['after'] == ['0']


@pytest.mark.parametrize('responses', [
    [page(True, 'repeat'), page(True, 'repeat')],
    [page(True, None)],
    [b'{"contents":[],"paging":{}}'],
    [b'{"contents":[],"contents":[],"paging":{"hasNextPage":false}}'],
])
def test_ambiguous_or_looping_pagination_never_returns_partial_success(responses):
    with pytest.raises(PlaylistError):
        acquire_playlist_pages(Mock(side_effect=responses), TOKEN)


def test_safety_limit_fails_instead_of_claiming_completeness():
    with pytest.raises(PlaylistError, match='Incomplete'):
        acquire_playlist_pages(Mock(return_value=page(True, 'next')), TOKEN, max_pages=1)
