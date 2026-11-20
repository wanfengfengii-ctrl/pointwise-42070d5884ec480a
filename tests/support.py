from contextlib import contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from batches.intake import ingest
from batches.repository import load_batch, load_batch_receipt
from facts.corrections import CorrectionRequest
from facts.old_image import OldImage
from facts.repository import load_current_fact, load_day_facts
from limits.day_policy import register
from warehouse import initialize, open_warehouse


STATION = 'WB01'
DAY = '2026-10-08'
PREVIOUS_DAY = '2026-10-07'


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix='tide-regression-'))
        self.path = Path(directory) / 'station.sqlite3'
        self.db = open_warehouse(self.path)
        self.addCleanup(self.close_db)
        initialize(self.db)

    def close_db(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def reopen(self):
        self.close_db()
        self.db = open_warehouse(self.path)

    def register(self, limit=10, source='P-17', *, station=STATION, day=DAY):
        return register(self.db, station, day, limit, source)

    def ingest(self, batch_id, rows, *, station=STATION, day=DAY):
        return ingest(self.db, station, day, batch_id, rows)

    def fact(self, key, *, station=STATION, day=DAY):
        return load_current_fact(self.db, station, day, key)

    def facts(self, *, station=STATION, day=DAY):
        return load_day_facts(self.db, station, day)

    def correction(self, correction_id, key, action, reason, reading=None):
        return CorrectionRequest(correction_id, OldImage.from_fact(self.fact(key)), action, reason, reading)

    def assert_no_batch(self, batch_id):
        self.assertIsNone(load_batch(self.db, batch_id))
        self.assertIsNone(load_batch_receipt(self.db, batch_id))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM raw_slots WHERE batch_id = ?', (batch_id,)).fetchone(), (0,))


@contextmanager
def deny_commit(db):
    """Make SQLite reject COMMIT while allowing the real statements and rollback."""
    attempts = []
    statements = []

    def authorize(action, operation, spare, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and operation == 'COMMIT':
            attempts.append(operation)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_trace_callback(statements.append)
    db.set_authorizer(authorize)
    try:
        yield attempts, statements
    finally:
        db.set_authorizer(None)
        db.set_trace_callback(None)
