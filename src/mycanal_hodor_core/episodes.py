"""Canonical episode catalogs and API-provided season navigation."""
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .diagnostics import debug_failure
from .logging import get_logger
from .http import validate_api_url, HodorError
from .availability import timestamp_datetime

log = get_logger(__name__)

def positive_number(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def season_number(value: object) -> int | None:
    """Hodor exposes S0 as a real season, distinct from missing coordinates."""
    return value if type(value) is int and value >= 0 else None


def identifier(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r'[\w-]+', value, re.ASCII) else ''


def parse_duration_label(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'\s*(\d+)\s*min\s*', value)
    if match:
        return positive_number(int(match[1]))
    match = re.fullmatch(r'\s*(\d+)\s*h\s*([0-5]\d)\s*(?:min)?\s*', value)
    return positive_number(int(match[1]) * 60 + int(match[2])) if match else None


@dataclass(frozen=True)
class Season:
    content_id: str
    number: int


@dataclass(frozen=True)
class Episode:
    content_id: str
    number: int
    duration_minutes: int | None
    availability_end_date: int | float | None
    title: str | None = None


@dataclass(frozen=True)
class SeasonCatalog:
    season: Season
    seasons: tuple[Season, ...]
    episodes: tuple[Episode, ...]

def validate_catalog(season: Season, seasons: tuple[Season, ...], episodes: list[Episode]) -> None:
    if (not seasons or season not in seasons
            or len({s.content_id for s in seasons}) != len(seasons)
            or len({s.number for s in seasons}) != len(seasons)):
        raise ValueError('Missing or ambiguous season selector')
    ids = [e.content_id for e in episodes if e.content_id]
    if len(set(ids)) != len(ids) or len({e.number for e in episodes}) != len(episodes):
        raise ValueError('Duplicate episode identities or numbers')


def episode_continuation(paging: object, season_id: str, brand_id: str) -> str | None:
    """Validate the observed id pagination; never construct a continuation."""
    if isinstance(paging, dict) and paging.get('hasNextPage') is False:
        return None
    if (not isinstance(paging, dict) or paging.get('hasNextPage') is not True
            or paging.get('iterationType') != 'id'
            or not isinstance(paging.get('URLPage'), str)):
        raise ValueError('Incomplete pagination: no verified continuation URL')
    url = paging['URLPage']
    validate_api_url(url, 'episodes')
    if urlsplit(url).path.rsplit('/', 1)[-1] != brand_id or url_season(url) != season_id:
        raise ValueError('Inconsistent episodes continuation target')
    cursors = [v for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True) if k == 'after']
    cursor = paging.get('idEnd')
    if (not isinstance(cursor, str) or not cursor or cursors != [cursor]):
        raise ValueError('Inconsistent episodes continuation cursor')
    return url


def parse_catalog(payload: dict, season_id: str,
                  fallback_selector: list | None = None, *,
                  diagnostic_secrets: tuple[str, ...] = (),
                  continuation_brand_id: str | None = None,
                  allow_previous_page: bool = False) -> tuple[SeasonCatalog, dict[str, str]]:
    block = payload.get('episodes')
    if not isinstance(block, dict) or not isinstance(block.get('contents'), list):
        raise ValueError('Missing episodes contents')
    paging = block.get('paging')
    try:
        if continuation_brand_id is not None:
            episode_continuation(paging, season_id, continuation_brand_id)
        elif not isinstance(paging, dict) or paging.get('hasNextPage') is not False:
            raise ValueError('Incomplete pagination: no verified continuation URL')
        if (paging.get('hasPreviousPage') is not False
                and not (allow_previous_page and paging.get('hasPreviousPage') is True)):
            raise ValueError('Catalog start page is incomplete or unknown')
    except (ValueError, HodorError) as error:
        debug_failure(log, 'series_catalog_debug', error, diagnostic_secrets,
                      season_id=season_id, paging=paging)
        raise
    count = paging.get('nbContents')
    if type(count) is int and count != len(block['contents']):
        raise ValueError('Paging count disagrees with returned episode count')
    selector = payload.get('selector', fallback_selector)
    if not isinstance(selector, list):
        raise ValueError('Missing season selector')
    seasons, urls = [], {}
    for selector_index, value in enumerate(selector):
        if (not isinstance(value, dict) or not identifier(value.get('contentID'))
                or season_number(value.get('seasonNumber')) is None):
            error = ValueError('Invalid season selector entry')
            debug_failure(log, 'series_catalog_debug', error, diagnostic_secrets,
                          season_id=season_id, selector_index=selector_index,
                          selector_entry=value)
            raise error
        seasons.append(Season(value['contentID'], value['seasonNumber']))
        click = value.get('onClick')
        if isinstance(click, dict) and isinstance(click.get('URLPage'), str):
            urls[value['contentID']] = click['URLPage']
    current = [season for season in seasons if season.content_id == season_id]
    if len(current) != 1:
        raise ValueError('Requested season absent from selector')
    season = current[0]
    episodes = []
    # An omitted season coordinate inherits the validated catalog membership.
    # Present coordinates must still agree; null is not an omission.
    for episode_index, value in enumerate(block['contents']):
        number = None
        if isinstance(value, dict):
            if 'episodeNumber' in value:
                number = positive_number(value['episodeNumber'])
            else:
                # Technical identity only: never derive a viewing order from this number.
                content_id = identifier(value.get('contentID'))
                digits = content_id.replace('_', '')
                if digits and re.fullmatch(r'[0-9]+', digits):
                    try:
                        number = positive_number(int(digits))
                    except ValueError:
                        # Excessively long integers are untrusted input too.
                        pass
        if (not isinstance(value, dict) or number is None
                or ('seasonNumber' in value
                    and season_number(value['seasonNumber']) != season.number)):
            error = ValueError('Missing or inconsistent episode coordinates')
            debug_failure(log, 'series_catalog_debug', error, diagnostic_secrets,
                          season_id=season_id, expected_season_number=season.number,
                          episode_index=episode_index, episode_entry=value)
            raise error
        minutes = parse_duration_label(value.get('durationLabel'))
        if minutes is None:
            log.warning('series_duration_unknown', season_id=season_id,
                        episode_number=number)
        timestamp = value.get('availabilityEndDate')
        if timestamp_datetime(timestamp) is None:
            timestamp = None
        episodes.append(Episode(identifier(value.get('contentID')), number,
                                minutes, timestamp,
                                value.get('title') if isinstance(value.get('title'), str) else None))
    validate_catalog(season, tuple(seasons), episodes)
    return SeasonCatalog(season, tuple(seasons), tuple(episodes)), urls


def _tracking_number(value: object, name: str) -> int | None:
    """Tracking may be nested under an action or its onClick object."""
    found = set()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            number = season_number(node.get(name)) if name == 'seasonNumber' else positive_number(node.get(name))
            if number is not None:
                found.add(number)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return next(iter(found)) if len(found) == 1 else None


def extract_navigation(payload: dict) -> tuple[str, str, int | None, int | None]:
    layout = payload.get('actionLayout')
    actions = layout.get('primaryActions') if isinstance(layout, dict) else None
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            click = action.get('onClick')
            if not isinstance(click, dict):
                continue
            url = click.get('URLEpisodesList')
            if isinstance(url, str):
                return (url, identifier(click.get('contentID')),
                        _tracking_number(action, 'seasonNumber'),
                        _tracking_number(action, 'episodeNumber'))
    # Tabs can provide catalog navigation, but cannot invent a resume episode.
    tabs = payload.get('tabs')
    if isinstance(tabs, list):
        for tab in tabs:
            url = tab.get('URLPage') if isinstance(tab, dict) else None
            if isinstance(url, str) and urlsplit(url).path.startswith('/api/v2/mycanal/episodes/'):
                return url, '', None, None
    raise ValueError('Missing episodes navigation')


def url_season(url: str) -> str:
    validate_api_url(url, 'episodes')
    values = [value for key, value in parse_qsl(urlsplit(url).query) if key == 'seasonID']
    return identifier(values[0]) if len(values) == 1 else ''


def for_season(url: str, season_id: str) -> str:
    # Reuse the API endpoint and its observed seasonID parameter. This is needed
    # when detail suggests an earlier episode/season than the current playlist.
    current_season = url_season(url)
    if current_season == season_id:
        return url
    if not current_season or not identifier(season_id):
        raise ValueError('Missing usable seasonID in episodes navigation')
    parsed = urlsplit(url)
    query = [(key, season_id if key == 'seasonID' else value)
             for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    return urlunsplit(parsed._replace(query=urlencode(query)))


def legacy_selector(payload: dict | None) -> list | None:
    if payload is None:
        return None
    detail = payload.get('detail')
    parent = payload.get('parentShow')
    for source in (detail, parent):
        values = source.get('seasons') if isinstance(source, dict) else None
        if isinstance(values, list) and values:
            # Legacy URLs target detail pages, not catalogs. Keep only structured
            # season identities; reuse the existing episodes endpoint if needed.
            return [{'contentID': value.get('contentID'),
                     'seasonNumber': value.get('seasonNumber')}
                    if isinstance(value, dict) else {} for value in values]
    return None


