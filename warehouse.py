import sqlite3

from limits.stations import Station, seed_stations
from storage import SCHEMA, transaction


DEMO_STATION = Station('WB01', '湾口站')


def open_warehouse(path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
    except BaseException:
        db.close()
        raise
    return db


def initialize(db):
    with transaction(db, write=True):
        for statement in SCHEMA:
            db.execute(statement)
        seed_stations(db, (DEMO_STATION,))
