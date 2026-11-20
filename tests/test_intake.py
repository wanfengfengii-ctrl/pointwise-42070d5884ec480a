import sqlite3

from batches.repository import load_batch, load_batch_receipt, load_raw_slot
from batches.rows import RawSlot
from facts.provenance import FactSource
from limits.day_policy import require_day_policy
from limits.repository import insert_station_if_absent
from limits.stations import Station, require_station
from storage import Conflict, UnknownRecord, transaction
from tests.support import DAY, PREVIOUS_DAY, STATION, WarehouseCase, deny_commit
from warehouse import initialize


class IntakeTests(WarehouseCase):
    def test_two_batches_and_replay(self):
        policy = self.register()
        early = [('C', 15), ('M', None), ('A', 12)]
        late = [('B', 10), ('D', 13)]
        first = self.ingest('EARLY', early)
        second = self.ingest('LATE', late)
        self.assertEqual(self.ingest('EARLY', tuple(early)), first)
        self.assertEqual(first.accepted_count, 3)
        self.assertEqual(first.slot_references, (('EARLY', 0), ('EARLY', 1), ('EARLY', 2)))
        self.assertEqual(second.slot_references, (('LATE', 0), ('LATE', 1)))
        self.assertEqual({fact.key: fact.reading for fact in self.facts()}, dict(early + late))
        self.assertEqual(require_day_policy(self.db, STATION, DAY), policy)
        self.assertEqual(self.fact('A').source, FactSource('EARLY', 2))
        self.assertEqual(tuple(load_raw_slot(self.db, 'EARLY', i) for i in range(3)),
                         (RawSlot(0, 'C', 15), RawSlot(1, 'M', None), RawSlot(2, 'A', 12)))
        self.assertEqual(load_batch_receipt(self.db, 'LATE'), second)

    def test_unregistered_day(self):
        self.register(day=PREVIOUS_DAY)
        rows = [('D', 13)]
        with self.assertRaises(UnknownRecord):
            self.ingest('LATE', rows)
        self.assert_no_batch('LATE')
        self.assertEqual(self.facts(), ())
        self.assertEqual(self.facts(day=PREVIOUS_DAY), ())
        self.register(12)
        receipt = self.ingest('LATE', rows)
        self.assertEqual((receipt.day, receipt.accepted_count), (DAY, 1))
        self.assertEqual(self.fact('D').source, FactSource('LATE', 0))

    def test_batch_key_collision(self):
        self.register()
        first = self.ingest('EARLY', [('A', 12)])
        with self.assertRaises(Conflict):
            self.ingest('LATE', [('D', 13), ('A', 20)])
        self.reopen()
        self.assert_no_batch('LATE')
        self.assertIsNone(self.fact('D'))
        self.assertEqual(self.fact('A').reading, 12)
        self.assertEqual(self.ingest('EARLY', [('A', 12)]), first)
        receipt = self.ingest('LATE', [('D', 13), ('E', 20)])
        self.assertEqual(receipt.accepted_count, 2)
        self.assertEqual({fact.key for fact in self.facts()}, {'A', 'D', 'E'})

    def test_policy_replay(self):
        original = self.register()
        self.assertEqual(self.register(10, 'P-18'), original)
        with self.assertRaises(Conflict):
            self.register(11)
        for invalid in (True, '10', 10.0, None):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                self.register(invalid)
        with self.assertRaises(ValueError):
            self.register(source=' ')
        with self.assertRaises(ValueError):
            self.register(day='2026-02-30')
        with self.assertRaises(UnknownRecord):
            self.register(station='UNKNOWN')
        self.ingest('EARLY', [('A', 12)])
        self.ingest('LATE', [('D', 13)])
        self.assertEqual(require_day_policy(self.db, STATION, DAY), original)
        self.assertEqual(original.source, 'P-17')

    def test_reordered_replay(self):
        self.register()
        rows = [('C', 15), ('M', None), ('A', 12)]
        original = self.ingest('EARLY', rows)
        with self.assertRaises(Conflict):
            self.ingest('EARLY', [rows[2], rows[1], rows[0]])
        self.register(day=PREVIOUS_DAY)
        with self.assertRaises(Conflict):
            self.ingest('EARLY', rows, day=PREVIOUS_DAY)
        self.assertEqual(self.ingest('EARLY', rows), original)
        self.assertEqual(load_batch(self.db, 'EARLY').slots,
                         (RawSlot(0, 'C', 15), RawSlot(1, 'M', None), RawSlot(2, 'A', 12)))
        self.assertEqual(self.fact('A').source, FactSource('EARLY', 2))
        self.assertEqual(self.facts(day=PREVIOUS_DAY), ())

    def test_invalid_row(self):
        self.register()
        for invalid in (('B', True), ('B', 10.0), ('B', '10'), ('', 10), ('B',), 'B1'):
            with self.subTest(row=invalid):
                with self.assertRaises(ValueError):
                    self.ingest('BAD', [('A', 12), invalid])
                self.assert_no_batch('BAD')
                self.assertEqual(self.facts(), ())
        self.assertEqual(self.ingest('BAD', [('A', 12), ('B', 0)]).accepted_count, 2)
        self.assertEqual(self.fact('B').reading, 0)

    def test_duplicate_in_batch(self):
        self.register()
        with self.assertRaises(Conflict):
            self.ingest('DUP', [('A', None), ('A', 12)])
        self.assert_no_batch('DUP')
        self.assertEqual(self.facts(), ())

    def test_initialization_preserves_station_and_warehouse(self):
        with transaction(self.db, write=True):
            self.db.execute('UPDATE stations SET name = ? WHERE station = ?', ('湾口新站名', STATION))
            insert_station_if_absent(self.db, 'LOCAL', '自建站')
        policy = self.register()
        self.register(-4, 'LOCAL-1', station='LOCAL')
        original = self.ingest('EARLY', [('M', None), ('A', 12)])
        local = self.ingest('LOCAL-BATCH', [('A', -3)], station='LOCAL')
        initialize(self.db)
        self.reopen()
        initialize(self.db)
        self.assertEqual(require_station(self.db, STATION), Station(STATION, '湾口新站名'))
        self.assertEqual(require_station(self.db, 'LOCAL'), Station('LOCAL', '自建站'))
        self.assertEqual(require_day_policy(self.db, STATION, DAY), policy)
        self.assertEqual(require_day_policy(self.db, 'LOCAL', DAY).limit, -4)
        self.assertEqual(self.fact('A').reading, 12)
        self.assertEqual(self.fact('A', station='LOCAL').reading, -3)
        self.assertEqual(self.fact('M').source, FactSource('EARLY', 0))
        self.assertEqual(load_raw_slot(self.db, 'EARLY', 0), RawSlot(0, 'M', None))
        self.assertEqual(self.ingest('EARLY', [('M', None), ('A', 12)]), original)
        self.assertEqual(load_batch_receipt(self.db, 'LOCAL-BATCH'), local)

    def test_failed_intake_commit_keeps_original_number_available(self):
        self.register()
        rows = [('A', 12), ('M', None)]
        with deny_commit(self.db) as (attempts, statements):
            with self.assertRaises(sqlite3.DatabaseError):
                self.ingest('EARLY', rows)
        self.assertEqual(attempts, ['COMMIT'])
        self.assertTrue(any(sql.lstrip().startswith('INSERT INTO current_facts') for sql in statements))
        self.reopen()
        self.assert_no_batch('EARLY')
        self.assertEqual(self.facts(), ())
        self.assertEqual(self.ingest('EARLY', rows).accepted_count, 2)
