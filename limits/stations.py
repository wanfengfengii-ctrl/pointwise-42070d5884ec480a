from dataclasses import dataclass

from limits.repository import insert_station_if_absent, load_station
from limits.validation import require_text
from storage import UnknownRecord


@dataclass(frozen=True)
class Station:
    station: str
    name: str


def require_station(db, station):
    require_text(station, 'station')
    row = load_station(db, station)
    if row is None:
        raise UnknownRecord(f'unknown station {station!r}')
    return Station(*row)


def seed_stations(db, stations):
    for station in stations:
        require_text(station.station, 'station')
        require_text(station.name, 'station name')
        insert_station_if_absent(db, station.station, station.name)
