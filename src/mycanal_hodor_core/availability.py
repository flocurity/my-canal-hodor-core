"""Read canonical availability representations without report presentation."""
import math
import re
from datetime import date, datetime, timezone, tzinfo

_LABEL_DATE = re.compile(r"Dispo\.\s+jusqu['’]au\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)


def timestamp_datetime(value: object, tz: tzinfo = timezone.utc) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if math.isfinite(value):
            return datetime.fromtimestamp(value / 1000, tz)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def parse_availability_label(label: str) -> date | None:
    match = _LABEL_DATE.search(label)
    if match:
        try:
            return datetime.strptime(match.group(1), '%d/%m/%Y').date()
        except ValueError:
            pass
    return None


def extract_raw_availability(payload: object) -> tuple[int | float | None, str]:
    """Keep the selected timestamp, or a dated API label when no timestamp exists."""
    if not isinstance(payload, dict) or not isinstance(payload.get('detail'), dict):
        raise ValueError('Missing or invalid detail object')
    timestamp = payload['detail'].get('availabilityEndDate')
    if timestamp_datetime(timestamp) is not None:
        return timestamp, ''
    info = payload['detail'].get('informations')
    if not isinstance(info, dict):
        return None, ''
    availability = info.get('contentAvailability')
    if not isinstance(availability, dict):
        return None, ''
    options = availability.get('availabilities')
    if not isinstance(options, dict):
        return None, ''

    ordered = [options.get('download'), options.get('stream')]
    ordered.extend(value for key, value in options.items() if key not in ('download', 'stream'))
    for option in ordered:
        if isinstance(option, dict):
            timestamp = option.get('availabilityEndDate')
            if timestamp_datetime(timestamp) is not None:
                return timestamp, ''

    # Exact timestamps take precedence over the less precise display labels.
    for option in [options.get('stream'), *ordered]:
        if not isinstance(option, dict) or not isinstance(option.get('label'), str):
            continue
        match = _LABEL_DATE.search(option['label'])
        if match:
            try:
                datetime.strptime(match.group(1), '%d/%m/%Y')
                return None, match.group(0)
            except ValueError:
                continue
    return None, ''
