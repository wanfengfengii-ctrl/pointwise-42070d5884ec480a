from dataclasses import dataclass

from limits.repository import insert_day_policy, load_day_policy
from limits.stations import require_station
from limits.validation import validate_limit, validate_policy_source, validate_station_day
from storage import Conflict, UnknownRecord, transaction


@dataclass(frozen=True)
class DayPolicy:
    station: str
    day: str
    limit: int
    source: str


def require_day_policy(db, station, day):
    validate_station_day(station, day)
    row = load_day_policy(db, station, day)
    if row is None:
        raise UnknownRecord(f'no policy for {station!r} on {day}')
    return DayPolicy(station, day, *row)


def register(db, station, day, limit, source):
    validate_station_day(station, day)
    validate_limit(limit)
    validate_policy_source(source)
    with transaction(db, write=True):
        require_station(db, station)
        existing = load_day_policy(db, station, day)
        if existing is not None:
            policy = DayPolicy(station, day, *existing)
            if policy.limit != limit:
                raise Conflict('station-day limit is already frozen')
            return policy
        insert_day_policy(db, station, day, limit, source)
        return DayPolicy(station, day, limit, source)
