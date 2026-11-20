from contextlib import contextmanager


class UnknownRecord(LookupError):
    """The requested business identity has no committed record."""


class Conflict(ValueError):
    """The request conflicts with a committed identity or old image."""


class IntegrityViolation(RuntimeError):
    """A stored or derived result cannot be reconciled with its inputs."""


def encode_mm(value):
    return None if value is None else format(value, 'x')


def decode_mm(value):
    return None if value is None else int(value, 16)


@contextmanager
def transaction(db, *, write=False):
    if db.in_transaction:
        raise RuntimeError('warehouse operations require a transaction-free connection')
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS stations (
        station TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS day_policies (
        station TEXT NOT NULL REFERENCES stations(station),
        day TEXT NOT NULL,
        limit_mm TEXT NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY (station, day)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY NOT NULL,
        station TEXT NOT NULL REFERENCES stations(station),
        day TEXT NOT NULL,
        slot_count INTEGER NOT NULL CHECK (slot_count >= 0)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS raw_slots (
        batch_id TEXT NOT NULL REFERENCES batches(batch_id),
        position INTEGER NOT NULL CHECK (position >= 0),
        observation_key TEXT NOT NULL,
        reading TEXT,
        PRIMARY KEY (batch_id, position)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS batch_receipts (
        batch_id TEXT PRIMARY KEY NOT NULL REFERENCES batches(batch_id),
        accepted_count INTEGER NOT NULL CHECK (accepted_count >= 0)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS current_facts (
        station TEXT NOT NULL,
        day TEXT NOT NULL,
        observation_key TEXT NOT NULL,
        reading TEXT,
        batch_id TEXT NOT NULL,
        slot INTEGER NOT NULL,
        correction_id TEXT,
        PRIMARY KEY (station, day, observation_key),
        FOREIGN KEY (batch_id, slot) REFERENCES raw_slots(batch_id, position)
    )''',
    '''CREATE TABLE IF NOT EXISTS corrections (
        correction_id TEXT PRIMARY KEY NOT NULL,
        station TEXT NOT NULL,
        day TEXT NOT NULL,
        observation_key TEXT NOT NULL,
        old_reading TEXT,
        batch_id TEXT NOT NULL,
        slot INTEGER NOT NULL,
        old_correction_id TEXT,
        action TEXT NOT NULL CHECK (action IN ('replace', 'withdraw')),
        reason TEXT NOT NULL,
        replacement TEXT,
        CHECK (action = 'replace' OR replacement IS NULL)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS reports (
        report_id TEXT PRIMARY KEY NOT NULL,
        station TEXT NOT NULL,
        day TEXT NOT NULL,
        limit_mm TEXT NOT NULL,
        policy_source TEXT NOT NULL,
        anomaly_count INTEGER NOT NULL CHECK (anomaly_count >= 0),
        within_count INTEGER NOT NULL CHECK (within_count >= 0),
        missing_count INTEGER NOT NULL CHECK (missing_count >= 0),
        FOREIGN KEY (station, day) REFERENCES day_policies(station, day)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS report_rows (
        report_id TEXT NOT NULL REFERENCES reports(report_id),
        position INTEGER NOT NULL CHECK (position >= 0),
        observation_key TEXT NOT NULL,
        reading TEXT NOT NULL,
        excess TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        slot INTEGER NOT NULL,
        correction_id TEXT,
        PRIMARY KEY (report_id, position),
        UNIQUE (report_id, observation_key),
        FOREIGN KEY (batch_id, slot) REFERENCES raw_slots(batch_id, position)
    ) WITHOUT ROWID''',
    '''CREATE TABLE IF NOT EXISTS report_receipts (
        report_id TEXT PRIMARY KEY NOT NULL REFERENCES reports(report_id),
        issued_count INTEGER NOT NULL CHECK (issued_count >= 0)
    ) WITHOUT ROWID''',
)
