import sqlite3

from limits.stations import Station, seed_stations
from storage import SCHEMA, transaction


DEMO_STATION = Station('WB01', '湾口站')

# Columns added after the first release. Old databases gain them on initialize.
ADDED_COLUMNS = (
    ('current_facts', 'label_id', 'TEXT'),
    ('corrections', 'old_label_id', 'TEXT'),
    ('report_rows', 'label_id', 'TEXT'),
)


def open_warehouse(path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
    except BaseException:
        db.close()
        raise
    return db


def _migrate_columns(db):
    for table, column, kind in ADDED_COLUMNS:
        existing = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        if column not in existing:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')


def initialize(db):
    with transaction(db, write=True):
        for statement in SCHEMA:
            db.execute(statement)
        _migrate_columns(db)
        seed_stations(db, (DEMO_STATION,))
