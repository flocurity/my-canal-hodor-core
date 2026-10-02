from urllib.parse import parse_qs, urlsplit
import pytest
from mycanal_hodor_core.episodes import parse_duration_label, extract_navigation

@pytest.mark.parametrize('label, expected', [
    ('57 min', 57), ('1h02', 62), ('1h31', 91), (' 1 h 02 min ', 62),
    (None, None), ('unknown', None), ('1h99', None), (True, None), ('0 min', None),
])
def test_duration_labels(label, expected):
    assert parse_duration_label(label) == expected


@pytest.mark.parametrize('name', ['detail_show.json', 'detail_season.json'])
def test_current_detail_navigation_structure(name, fixture_data):
    from mycanal_hodor_core.availability import extract_raw_availability
    _navigation = extract_navigation
    from mycanal_hodor_core.detail import extract_subgenre

    payload = fixture_data(name)
    assert 'informations' not in payload['detail']
    assert extract_raw_availability(payload) == (None, '')
    assert extract_subgenre(payload) == 'Série Science-fiction'
    url, episode_id, season_number, episode_number = _navigation(payload)
    assert episode_id == 'fiction_episode_50002'
    assert (season_number, episode_number) == (1, 1)
    assert parse_qs(urlsplit(url).query)['seasonID'] == ['fiction_season_50002']
    assert payload['actionLayout']['primaryActions'][0]['onClick']['episodesList']['URLPage'] == url
    # A known playlist point can also obtain navigation through the current tab.
    payload['actionLayout']['primaryActions'] = []
    assert _navigation(payload) == (url, '', None, None)


def test_catalog_parsing_is_raw_and_complete(fixture_data):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = fixture_data('episodes_series.json')
    season_number = payload['episodes']['contents'][0]['seasonNumber']
    season_id = next(s['contentID'] for s in payload['selector'] if s['seasonNumber'] == season_number)
    catalog, urls = parse_catalog(payload, season_id)
    assert catalog.episodes
    assert catalog.season.content_id == season_id
    assert all(type(e.duration_minutes) is int for e in catalog.episodes)
    payload['episodes']['paging']['hasNextPage'] = True
    with pytest.raises(ValueError, match='pagination'):
        parse_catalog(payload, season_id)
