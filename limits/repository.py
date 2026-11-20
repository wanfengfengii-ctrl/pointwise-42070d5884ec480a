from storage import decode_mm, encode_mm


def load_station(db, station):
    return db.execute(
        'SELECT station, name FROM stations WHERE station = ?', (station,)
    ).fetchone()


def insert_station_if_absent(db, station, name):
    db.execute(
        'INSERT INTO stations (station, name) VALUES (?, ?) ON CONFLICT(station) DO NOTHING',
        (station, name),
    )


def load_day_policy(db, station, day):
    row = db.execute(
        'SELECT limit_mm, source FROM day_policies WHERE station = ? AND day = ?',
        (station, day),
    ).fetchone()
    if row is None:
        return None
    limit, source = row
    return decode_mm(limit), source


def insert_day_policy(db, station, day, limit, source):
    db.execute(
        'INSERT INTO day_policies (station, day, limit_mm, source) VALUES (?, ?, ?, ?)',
        (station, day, encode_mm(limit), source),
    )
