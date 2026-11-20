from datetime import date


def require_text(value, field):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{field} must be nonblank text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{field} must be valid Unicode') from exc
    return value


def validate_station_day(station, day):
    require_text(station, 'station')
    require_text(day, 'day')
    try:
        parsed = date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError('day must use YYYY-MM-DD') from exc
    if parsed.isoformat() != day:
        raise ValueError('day must use YYYY-MM-DD')


def validate_limit(limit):
    if type(limit) is not int:
        raise ValueError('limit must be an integer number of millimetres')


def validate_policy_source(source):
    require_text(source, 'policy source')
