import pytest
from mycanal_hodor_core.detail import extract_subgenre, extract_duration

@pytest.mark.parametrize('tracking', [
    None, [], {}, {'dataLayer': None}, {'dataLayer': []}, {'dataLayer': {}},
    *[{'dataLayer': {'subgenre': value}} for value in (None, 42, False, [], {}, '', ' \t')],
])
def test_unusable_subgenre(tracking):
    assert extract_subgenre({'tracking': tracking}) == ''


def test_subgenre_preserved_verbatim():
    value = '  Science-FICTION / mystère  '
    assert extract_subgenre({'tracking': {'dataLayer': {'subgenre': value}}}) == value


@pytest.mark.parametrize('value', ['Série Science-fiction', '  Série Science-fiction \t'])
@pytest.mark.parametrize('tracking', [{}, {'dataLayer': {'subgenre': 'Serie Science-fiction'}}])
def test_detail_subgenre_wins_and_is_preserved(value, tracking):
    assert extract_subgenre({
        'detail': {'subgenre': value}, 'tracking': tracking,
    }) == value


@pytest.mark.parametrize('detail', [
    None, [], {},
    *[{'subgenre': value} for value in (None, 42, False, [], {}, '', ' \t\n')],
])
def test_unusable_detail_subgenre_falls_back(detail):
    value = '  Serie Science-fiction  '
    assert extract_subgenre({
        'detail': detail, 'tracking': {'dataLayer': {'subgenre': value}},
    }) == value


@pytest.mark.parametrize('value', [None, 42, False, [], {}, '', ' \t\n'])
def test_unusable_subgenre_in_both_locations(value):
    assert extract_subgenre({
        'detail': {'subgenre': value},
        'tracking': {'dataLayer': {'subgenre': value}},
    }) == ''


def test_subgenre_missing_in_both_locations():
    assert extract_subgenre({}) == ''


