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


@pytest.mark.parametrize('problem', [None, 'cursor', 'season', 'resource', 'host', 'missing'])
def test_explicit_episode_continuation(problem):
    from mycanal_hodor_core.episodes import episode_continuation
    from mycanal_hodor_core.http import HodorError
    url = 'https://hodor.canalplus.pro/api/v2/mycanal/episodes/' + 'a'*32 + '/mammouth?seasonID=gptou&after=opaque'
    paging = {'iterationType': 'id', 'hasNextPage': True, 'idEnd': 'opaque', 'URLPage': url}
    if problem == 'cursor':
        paging['idEnd'] = 'different'
    elif problem == 'season':
        paging['URLPage'] = url.replace('seasonID=gptou', 'seasonID=other')
    elif problem == 'resource':
        paging['URLPage'] = url.replace('/episodes/', '/detail/')
    elif problem == 'host':
        paging['URLPage'] = url.replace('hodor.canalplus.pro', 'example.com')
    elif problem == 'missing':
        del paging['URLPage']
    if problem:
        with pytest.raises((ValueError, HodorError)):
            episode_continuation(paging, 'gptou', 'mammouth')
    else:
        assert episode_continuation(paging, 'gptou', 'mammouth') == url
    assert episode_continuation({'hasNextPage': False}, 'gptou', 'mammouth') is None


@pytest.mark.parametrize('number,valid', [(0, True), (1, True), (-1, False),
                                         (1.0, False), ('0', False), (True, False), (None, False)])
def test_season_numbers_preserve_zero_and_reject_invalid_values(number, valid):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': number}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': 'episode_gptou', 'seasonNumber': number,
                                          'episodeNumber': 7, 'durationLabel': '57 min'}]}}
    if valid:
        catalog, _ = parse_catalog(payload, 'season_mammouth')
        assert catalog.season.number == number
        assert catalog.episodes[0].number == 7
    else:
        with pytest.raises(ValueError, match='Invalid season selector entry'):
            parse_catalog(payload, 'season_mammouth')


def test_zero_season_navigation_is_not_missing():
    from mycanal_hodor_core.episodes import extract_navigation
    payload = {'actionLayout': {'primaryActions': [{'onClick': {
        'URLEpisodesList': 'synthetic', 'seasonNumber': 0, 'episodeNumber': 1}}]}}
    assert extract_navigation(payload)[2:] == (0, 1)


@pytest.mark.parametrize('context_number,coordinate,accepted', [
    (0, {}, True), (1, {}, True),
    (0, {'seasonNumber': 0}, True), (3, {'seasonNumber': 3}, True),
    (0, {'seasonNumber': 1}, False), (1, {'seasonNumber': 0}, False),
    (0, {'seasonNumber': None}, False), (1, {'seasonNumber': '1'}, False),
    (1, {'seasonNumber': True}, False), (0, {'seasonNumber': False}, False),
    (1, {'seasonNumber': -1}, False), (1, {'seasonNumber': 1.0}, False),
])
def test_episode_season_omission_uses_validated_catalog(context_number, coordinate, accepted):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': context_number}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': 'episode_gptou', 'episodeNumber': 177,
                                          'durationLabel': '57 min', **coordinate}]}}
    if accepted:
        catalog, _ = parse_catalog(payload, 'season_mammouth')
        assert catalog.season.content_id == 'season_mammouth'
        assert catalog.season.number == context_number
        assert catalog.episodes[0].number == 177
        assert payload['episodes']['contents'][0].get('seasonNumber') == coordinate.get('seasonNumber')
        assert ('seasonNumber' in payload['episodes']['contents'][0]) == ('seasonNumber' in coordinate)
    else:
        with pytest.raises(ValueError, match='Missing or inconsistent episode coordinates'):
            parse_catalog(payload, 'season_mammouth')


@pytest.mark.parametrize('episode_coordinate', [{}, {'episodeNumber': None},
    {'episodeNumber': -1}, {'episodeNumber': True},
    {'episodeNumber': '177'}, {'episodeNumber': 177.0}])
def test_omitted_season_does_not_relax_episode_number(episode_coordinate):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': 0}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': 'episode_gptou', **episode_coordinate}]}}
    with pytest.raises(ValueError, match='Missing or inconsistent episode coordinates'):
        parse_catalog(payload, 'season_mammouth')


@pytest.mark.parametrize('coordinate,accepted', [
    ({'episodeNumber': 7}, True), ({}, True),
    ({'episodeNumber': None}, False), ({'episodeNumber': '7'}, False),
    ({'episodeNumber': True}, False), ({'episodeNumber': 0}, True),
    ({'episodeNumber': -1}, False), ({'episodeNumber': 7.0}, False),
])
def test_synthetic_episode_number_only_for_absent_key(coordinate, accepted):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': 2}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': '26219525_50006', 'durationLabel': '57 min', **coordinate}]}}
    if not accepted:
        with pytest.raises(ValueError, match='Missing or inconsistent episode coordinates'):
            parse_catalog(payload, 'season_mammouth')
    else:
        first, _ = parse_catalog(payload, 'season_mammouth')
        second, _ = parse_catalog(payload, 'season_mammouth')
        assert first == second
        assert first.episodes[0].number == coordinate.get('episodeNumber', 2621952550006)
        assert ('episodeNumber' in payload['episodes']['contents'][0]) == ('episodeNumber' in coordinate)


@pytest.mark.parametrize('content_id', ['episode_gptou', '', None, True, '12/34', '-123', '1 23', '0_0'])
def test_unconvertible_episode_identity_has_no_synthetic_fallback(content_id):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': 2}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': content_id}]}}
    with pytest.raises(ValueError, match='Missing or inconsistent episode coordinates'):
        parse_catalog(payload, 'season_mammouth')


def test_mixed_catalog_preserves_uniqueness_for_synthetic_numbers():
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': 2}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': '26219525_50006', 'durationLabel': '57 min'},
                                         {'contentID': '26008498_50006', 'durationLabel': '57 min'},
                                         {'contentID': 'episode_gptou', 'episodeNumber': 3, 'durationLabel': '57 min'}]}}
    catalog, _ = parse_catalog(payload, 'season_mammouth')
    assert [episode.number for episode in catalog.episodes] == [2621952550006, 2600849850006, 3]
    payload['episodes']['contents'][2]['episodeNumber'] = 2621952550006
    repeated_number, _ = parse_catalog(payload, 'season_mammouth')
    assert [e.number for e in repeated_number.episodes] == [2621952550006, 2600849850006, 2621952550006]
    payload['episodes']['contents'][2]['contentID'] = '26219525_50006'
    with pytest.raises(ValueError, match='Duplicate episode identities'):
        parse_catalog(payload, 'season_mammouth')


@pytest.mark.parametrize('title,expected', [('Mammouth', 'Mammouth'), (None, None), (42, None)])
def test_episode_title_is_optional_raw_editorial_data(title, expected):
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID': 'season_mammouth', 'seasonNumber': 1}],
               'episodes': {'paging': {'hasNextPage': False, 'hasPreviousPage': False},
                            'contents': [{'contentID': '123_45', 'title': title, 'durationLabel': '57 min'}]}}
    catalog, _ = parse_catalog(payload, 'season_mammouth')
    assert catalog.episodes[0].title == expected


@pytest.mark.parametrize('duplicate_identity', [False, True])
def test_editorial_number_duplicates_preserve_identity_and_order(duplicate_identity):
    from mycanal_hodor_core.episodes import Episode, Season, validate_catalog
    season = Season('season_mammouth', 0)
    episodes = [Episode('unit_a', 173, 27, None),
                Episode('unit_a' if duplicate_identity else 'unit_b', 173, 30, None)]
    if duplicate_identity:
        with pytest.raises(ValueError, match='Duplicate episode identities'):
            validate_catalog(season, (season,), episodes)
    else:
        validate_catalog(season, (season,), episodes)
        assert [e.content_id for e in episodes] == ['unit_a', 'unit_b']
        assert [e.number for e in episodes] == [173, 173]


def test_zero_episode_and_sequence_are_explicit_raw_coordinates():
    from mycanal_hodor_core.episodes import parse_catalog
    payload = {'selector': [{'contentID':'season_mammouth', 'seasonNumber':1}],
               'episodes': {'paging': {'hasNextPage':False,'hasPreviousPage':False},
                            'contents': [{'contentID':f'unit_{index}', 'episodeNumber':n,
                                          'seasonNumber':1,'title':'Pilot'} for index, n in enumerate((0,1,2,0))]}}
    catalog, _ = parse_catalog(payload, 'season_mammouth')
    assert [e.number for e in catalog.episodes] == [0,1,2,0]
    # Identity uniqueness still applies, even to episode zero.
    payload['episodes']['contents'][-1]['contentID'] = 'unit_other'
    catalog, _ = parse_catalog(payload, 'season_mammouth')
    assert [e.number for e in catalog.episodes] == [0,1,2,0]


def test_long_single_season_catalog_with_repeated_editorial_numbers():
    """Editorial year labels do not create seasons or episode identities."""
    from mycanal_hodor_core.episodes import parse_catalog, validate_catalog
    entries = [{'contentID': f'{900000 + index}_50001',
                'episodeNumber': index % 73 + 1, 'seasonNumber': 0,
                'title': f'Mammouth editorial year {index // 73}, unit {index % 73 + 1}',
                'durationLabel': '20 min'} for index in range(584)]
    payload = {'selector': [{'contentID': 'season_editorial_mammoth', 'seasonNumber': 0}],
               'episodes': {'contents': entries,
                            'paging': {'hasNextPage': False, 'hasPreviousPage': False}}}
    catalog, _ = parse_catalog(payload, 'season_editorial_mammoth')
    assert len(catalog.episodes) == 584
    assert len({e.content_id for e in catalog.episodes}) == 584
    assert [e.content_id for e in catalog.episodes] == [e['contentID'] for e in entries]
    assert [e.number for e in catalog.episodes] == [e['episodeNumber'] for e in entries]
    assert catalog.episodes[0].number == catalog.episodes[73].number
    assert catalog.episodes[0].title != catalog.episodes[73].title
    # An overlapping page must still be rejected after assembly.
    with pytest.raises(ValueError, match='Duplicate episode identities'):
        validate_catalog(catalog.season, catalog.seasons,
                         list(catalog.episodes) + [catalog.episodes[0]])
