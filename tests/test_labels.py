from dataclasses import replace
from pathlib import Path
import sqlite3

from batches.repository import load_raw_slot
from delivery.issue import publish
from delivery.pages import read_page
from delivery.source_lookup import read_source
from facts.corrections import CorrectionRequest, correct_fact, relabel_facts as public_relabel
from facts.label_repository import load_label_request
from facts.labels import relabel_facts
from facts.old_image import OldImage
from facts.provenance import FactSource
from facts.repository import load_current_fact
from quality.evaluate import preview
from quality.totals import CoverageTotals
from storage import Conflict, IntegrityViolation
from tests.support import DAY, PREVIOUS_DAY, STATION, WarehouseCase, deny_commit


class PublicEntryPointTests(WarehouseCase):
    def test_relabel_is_exported_from_corrections(self):
        self.register()
        self.ingest('EARLY', [('A', 15), ('B', 12)])
        self.assertIs(public_relabel, relabel_facts)
        receipt = public_relabel(self.db, 'TAG', 'swap', [
            (OldImage.from_fact(self.fact('A')), 'B'), (OldImage.from_fact(self.fact('B')), 'A')])
        self.assertEqual([fact.key for fact in receipt.after], ['B', 'A'])


class LegacyDatabaseTests(WarehouseCase):
    def test_initialize_upgrades_old_warehouse_in_place(self):
        from tempfile import TemporaryDirectory
        from warehouse import initialize, open_warehouse
        from storage import encode_mm

        with TemporaryDirectory(prefix='tide-legacy-') as directory:
            path = Path(directory) / 'old.sqlite3'
            db = open_warehouse(path)
            db.execute('PRAGMA foreign_keys = ON')
            db.executescript('''
                CREATE TABLE stations (station TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL) WITHOUT ROWID;
                CREATE TABLE day_policies (station TEXT NOT NULL, day TEXT NOT NULL, limit_mm TEXT NOT NULL,
                    source TEXT NOT NULL, PRIMARY KEY (station, day)) WITHOUT ROWID;
                CREATE TABLE batches (batch_id TEXT PRIMARY KEY NOT NULL, station TEXT NOT NULL, day TEXT NOT NULL,
                    slot_count INTEGER NOT NULL) WITHOUT ROWID;
                CREATE TABLE raw_slots (batch_id TEXT NOT NULL, position INTEGER NOT NULL, observation_key TEXT NOT NULL,
                    reading TEXT, PRIMARY KEY (batch_id, position)) WITHOUT ROWID;
                CREATE TABLE batch_receipts (batch_id TEXT PRIMARY KEY NOT NULL, accepted_count INTEGER NOT NULL) WITHOUT ROWID;
                CREATE TABLE current_facts (station TEXT NOT NULL, day TEXT NOT NULL, observation_key TEXT NOT NULL,
                    reading TEXT, batch_id TEXT NOT NULL, slot INTEGER NOT NULL, correction_id TEXT,
                    PRIMARY KEY (station, day, observation_key));
                CREATE TABLE corrections (correction_id TEXT PRIMARY KEY NOT NULL, station TEXT NOT NULL, day TEXT NOT NULL,
                    observation_key TEXT NOT NULL, old_reading TEXT, batch_id TEXT NOT NULL, slot INTEGER NOT NULL,
                    old_correction_id TEXT, action TEXT NOT NULL, reason TEXT NOT NULL, replacement TEXT) WITHOUT ROWID;
                CREATE TABLE reports (report_id TEXT PRIMARY KEY NOT NULL, station TEXT NOT NULL, day TEXT NOT NULL,
                    limit_mm TEXT NOT NULL, policy_source TEXT NOT NULL, anomaly_count INTEGER NOT NULL,
                    within_count INTEGER NOT NULL, missing_count INTEGER NOT NULL) WITHOUT ROWID;
                CREATE TABLE report_rows (report_id TEXT NOT NULL, position INTEGER NOT NULL, observation_key TEXT NOT NULL,
                    reading TEXT NOT NULL, excess TEXT NOT NULL, batch_id TEXT NOT NULL, slot INTEGER NOT NULL,
                    correction_id TEXT, PRIMARY KEY (report_id, position)) WITHOUT ROWID;
                CREATE TABLE report_receipts (report_id TEXT PRIMARY KEY NOT NULL, issued_count INTEGER NOT NULL) WITHOUT ROWID;
            ''')
            db.execute("INSERT INTO stations VALUES ('WB01', '湾口站')")
            db.execute("INSERT INTO day_policies VALUES ('WB01', ?, ?, 'P-17')", (DAY, encode_mm(10)))
            db.execute("INSERT INTO batches VALUES ('EARLY', 'WB01', ?, 1)", (DAY,))
            db.execute("INSERT INTO raw_slots VALUES ('EARLY', 0, 'A', ?)", (encode_mm(15),))
            db.execute("INSERT INTO batch_receipts VALUES ('EARLY', 1)")
            db.execute("INSERT INTO current_facts VALUES ('WB01', ?, 'A', ?, 'EARLY', 0, NULL)", (DAY, encode_mm(15)))
            db.execute("INSERT INTO reports VALUES ('OLD', 'WB01', ?, ?, 'P-17', 1, 0, 0)", (DAY, encode_mm(10)))
            db.execute("INSERT INTO report_rows VALUES ('OLD', 0, 'A', ?, ?, 'EARLY', 0, NULL)",
                       (encode_mm(15), encode_mm(5)))
            db.execute("INSERT INTO report_receipts VALUES ('OLD', 1)")
            initialize(db)
            initialize(db)
            self.assertEqual(preview(db, STATION, DAY).totals, CoverageTotals(1, 0, 0))
            self.assertEqual(read_source(db, 'OLD', 0).label_changes, ())
            self.assertEqual(read_page(db, 'OLD', 0, 1).rows[0].reading, 15)
            relabel_facts(db, 'NEW-LABEL', 'upgrade', [
                (OldImage.from_fact(load_current_fact(db, STATION, DAY, 'A')), 'A2')])
            publish(db, STATION, DAY, 'NEW')
            self.assertEqual(read_source(db, 'NEW', 0).label_changes[0][0], 'NEW-LABEL')
            self.assertEqual(read_source(db, 'OLD', 0).label_changes, ())
            db.close()


def image(db, *keys, station=STATION, day=DAY):
    return [(OldImage.from_fact(load_current_fact(db, station, day, key)), key) for key in keys]


class SwapCycleTests(WarehouseCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.ingest('EARLY', [('A', 19), ('B', 14), ('D', 10)])

    def pair(self, *pairs):
        return [(OldImage.from_fact(self.fact(old)), new) for old, new in pairs]

    def test_swap_keeps_readings_and_slots(self):
        receipt = relabel_facts(self.db, 'TAG-SWAP', '校对双槽标签', self.pair(('A', 'B'), ('B', 'A')))
        self.assertEqual([fact.key for fact in receipt.after], ['B', 'A'])
        self.assertEqual([fact.reading for fact in receipt.after], [19, 14])
        self.assertEqual(receipt.after[0].source, FactSource('EARLY', 0, None, 'TAG-SWAP'))
        self.assertEqual(receipt.after[1].source, FactSource('EARLY', 1, None, 'TAG-SWAP'))
        self.assertEqual(self.fact('B').reading, 19)
        self.assertEqual(self.fact('A').reading, 14)
        self.assertEqual(load_raw_slot(self.db, 'EARLY', 0).key, 'A')

    def test_closed_cycle(self):
        self.ingest('EXTRA', [('C', 17)])
        receipt = relabel_facts(self.db, 'CYCLE', '环形改挂', self.pair(('A', 'B'), ('B', 'C'), ('C', 'A')))
        self.assertEqual([fact.key for fact in receipt.after], ['B', 'C', 'A'])
        self.assertEqual({fact.key: fact.reading for fact in self.facts()},
                         {'A': 17, 'B': 19, 'C': 14, 'D': 10})

    def test_target_occupied_outside_rejected(self):
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'BAD', '占用', self.pair(('B', 'D')))
        self.assertIsNone(load_label_request(self.db, 'BAD'))
        self.assertEqual({fact.key: fact.reading for fact in self.facts()}, {'A': 19, 'B': 14, 'D': 10})

    def test_shape_errors(self):
        good = image(self.db, 'A')
        with self.assertRaises(ValueError):
            relabel_facts(self.db, 'EMPTY', 'empty', [])
        with self.assertRaises(ValueError):
            relabel_facts(self.db, 'SELF', 'same', [(good[0][0], 'A')])
        with self.assertRaises(ValueError):
            relabel_facts(self.db, 'DUP-OUT', 'dup', [(good[0][0], 'X'), (OldImage.from_fact(self.fact('B')), 'X')])
        with self.assertRaises(ValueError):
            relabel_facts(self.db, 'DUP-IN', 'dup', [(good[0][0], 'X'), (good[0][0], 'Y')])
        self.register(day=PREVIOUS_DAY)
        self.ingest('OLD', [('A', 12)], day=PREVIOUS_DAY)
        with self.assertRaises(ValueError):
            relabel_facts(self.db, 'MIXED', 'mixed', [
                (OldImage.from_fact(self.fact('A')), 'X'),
                (OldImage.from_fact(self.fact('A', day=PREVIOUS_DAY)), 'Y'),
            ])
        for request_id in ('', ' ', None, 5):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                relabel_facts(self.db, request_id, 'reason', self.pair(('A', 'X')))
        with self.assertRaises(ValueError):
            relabel_facts(self.db, 'X', ' ', self.pair(('A', 'X')))
        self.assertEqual(self.fact('A').reading, 19)

    def test_replays(self):
        request = self.pair(('A', 'B'), ('B', 'A'))
        first = relabel_facts(self.db, 'TAG-SWAP', 'reason one', request)
        self.assertEqual(relabel_facts(self.db, 'TAG-SWAP', 'reason one', request), first)
        self.reopen()
        self.assertEqual(relabel_facts(self.db, 'TAG-SWAP', 'reason one', request), first)
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'TAG-SWAP', 'reason two', request)
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'TAG-SWAP', 'reason one', self.pair(('A', 'B')))

    def test_stale_identity_rejected(self):
        stale = self.pair(('A', 'B'), ('B', 'A'))
        relabel_facts(self.db, 'TAG-SWAP', 'first', stale)
        relabel_facts(self.db, 'TAG-OUT', 'second', self.pair(('B', 'C')))
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'TOO-LATE', 'stale', stale)
        self.assertIsNone(load_label_request(self.db, 'TOO-LATE'))
        self.assertEqual(self.fact('C').reading, 19)

    def test_old_image_without_label_still_usable(self):
        old = OldImage(STATION, DAY, 'A', 19, FactSource('EARLY', 0))
        relabel_facts(self.db, 'L1', 'relabel', [(old, 'Z')])
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'L2', 'again', [(old, 'Y')])

    def test_replay_does_not_revert_later_changes(self):
        request = self.pair(('A', 'B'), ('B', 'A'))
        first = relabel_facts(self.db, 'TAG-SWAP', 'first', request)
        later = relabel_facts(self.db, 'TAG-OUT', 'later', self.pair(('B', 'C')))
        self.reopen()
        self.assertEqual(relabel_facts(self.db, 'TAG-SWAP', 'first', request), first)
        self.assertEqual(self.fact('C').reading, later.after[0].reading)
        self.assertEqual(self.fact('C').reading, 19)
        self.assertIsNone(self.fact('B'))

    def test_replay_survives_withdrawal_of_first_result(self):
        request = self.pair(('A', 'B'), ('B', 'A'))
        first = relabel_facts(self.db, 'TAG-SWAP', 'swap', request)
        correct_fact(self.db, CorrectionRequest(
            'W-B', OldImage.from_fact(self.fact('B')), 'withdraw', 'withdraw after swap'))
        self.reopen()
        self.assertEqual(relabel_facts(self.db, 'TAG-SWAP', 'swap', request), first)
        self.assertIsNone(self.fact('B'))
        self.assertEqual(self.fact('A').reading, 14)

    def test_one_stale_item_leaves_whole_request_unapplied(self):
        stale = self.pair(('A', 'B'), ('B', 'A'))
        self.ingest('OTHER', [('E', 18)])
        self.assertEqual(self.fact('E').reading, 18)
        wrong = replace(stale[0][0], reading=20)
        bad = [(OldImage.from_fact(self.fact('E')), 'F'), (wrong, 'B')]
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'PARTIAL', 'partial', bad)
        self.reopen()
        self.assertIsNone(load_label_request(self.db, 'PARTIAL'))
        self.assertEqual({fact.key: fact.reading for fact in self.facts()},
                         {'A': 19, 'B': 14, 'D': 10, 'E': 18})

    def test_return_to_same_key_and_reading_still_rejects_stale(self):
        relabel_facts(self.db, 'L1', 'out', self.pair(('A', 'Z')))
        stale = [(OldImage.from_fact(self.fact('Z')), 'A')]
        relabel_facts(self.db, 'L2', 'back', stale)
        self.assertEqual((self.fact('A').reading, self.fact('A').source.label_id), (19, 'L2'))
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'L3', 'stale', stale)
        self.assertIsNone(load_label_request(self.db, 'L3'))

    def test_settled_key_drives_equal_excess_order(self):
        self.ingest('EXTRA', [('C', 17)])
        self.assertEqual([row.key for row in publish(self.db, STATION, DAY, 'BEFORE').report.rows],
                         ['A', 'C', 'B'])
        # Swap the labels: key A now carries C's 17 and key C carries A's 19.
        relabel_facts(self.db, 'TAG-SWAP', 'swap equal rows', self.pair(('A', 'C'), ('C', 'A')))
        self.assertEqual([(row.key, row.reading) for row in preview(self.db, STATION, DAY).rows],
                         [('C', 19), ('A', 17), ('B', 14)])
        issued = publish(self.db, STATION, DAY, 'AFTER')
        self.assertEqual([(row.key, row.reading, row.source.label_id) for row in issued.report.rows],
                         [('C', 19, 'TAG-SWAP'), ('A', 17, 'TAG-SWAP'), ('B', 14, None)])
        self.assertEqual([(row.position, row.key) for row in read_page(self.db, 'AFTER', 0, 3).rows],
                         [(0, 'C'), (1, 'A'), (2, 'B')])
        self.assertEqual([(row.position, row.key) for row in read_page(self.db, 'BEFORE', 0, 3).rows],
                         [(0, 'A'), (1, 'C'), (2, 'B')])
        self.assertIsNone(read_page(self.db, 'BEFORE', 0, 1).rows[0].source.label_id)

    def test_rollback(self):
        original = {fact.key: fact for fact in self.facts()}
        request = self.pair(('A', 'B'), ('B', 'A'))
        with deny_commit(self.db) as (attempts, statements):
            with self.assertRaises(sqlite3.DatabaseError):
                relabel_facts(self.db, 'TAG-SWAP', 'rollback', request)
        self.assertEqual(attempts, ['COMMIT'])
        self.assertTrue(any(sql.lstrip().startswith('INSERT INTO label_requests') for sql in statements))
        self.reopen()
        self.assertIsNone(load_label_request(self.db, 'TAG-SWAP'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM label_changes').fetchone(), (0,))
        self.assertEqual({fact.key: fact for fact in self.facts()}, original)
        self.assertEqual(relabel_facts(self.db, 'TAG-SWAP', 'rollback', request).request_id, 'TAG-SWAP')
        self.assertEqual(self.fact('B').reading, 19)


class HandoffTests(WarehouseCase):
    def setUp(self):
        super().setUp()
        self.register(10, 'LIMIT-8')
        self.ingest('EARLY', [('A', 19), ('B', 14), ('D', 10)])

    def pair(self, *pairs):
        return [(OldImage.from_fact(self.fact(old)), new) for old, new in pairs]

    def test_handoff_table(self):
        self.assertEqual([(row.key, row.reading) for row in publish(self.db, STATION, DAY, 'R0').report.rows],
                         [('A', 19), ('B', 14)])
        relabel_facts(self.db, 'TAG-SWAP', '校对双槽标签', self.pair(('A', 'B'), ('B', 'A')))
        self.assertEqual([(row.key, row.reading) for row in publish(self.db, STATION, DAY, 'R1').report.rows],
                         [('B', 19), ('A', 14)])
        correct_fact(self.db, CorrectionRequest(
            'VALUE-1', OldImage.from_fact(self.fact('B')), 'replace', '复查仪表单', 21))
        publish(self.db, STATION, DAY, 'R2')
        relabel_facts(self.db, 'TAG-OUT', '改挂临时标签', self.pair(('B', 'C')))
        publish(self.db, STATION, DAY, 'R3')
        relabel_facts(self.db, 'TAG-BACK', '恢复核定标签', self.pair(('C', 'B')))
        publish(self.db, STATION, DAY, 'R4')
        with self.assertRaises(Conflict):
            relabel_facts(self.db, 'TAG-OCCUPIED', '待复核标签', self.pair(('B', 'D')))

        self.reopen()
        expectations = {
            'R0': ('A', 19, None, ()),
            'R1': ('B', 19, None, ('TAG-SWAP',)),
            'R2': ('B', 21, 'VALUE-1', ('TAG-SWAP',)),
            'R3': ('C', 21, 'VALUE-1', ('TAG-SWAP', 'TAG-OUT')),
            'R4': ('B', 21, 'VALUE-1', ('TAG-SWAP', 'TAG-OUT', 'TAG-BACK')),
        }
        for report_id, (key, reading, correction, labels) in expectations.items():
            with self.subTest(report=report_id):
                evidence = read_source(self.db, report_id, 0)
                self.assertEqual(evidence.row.key, key)
                self.assertEqual(evidence.row.reading, reading)
                self.assertEqual(evidence.original.key, 'A')
                self.assertEqual(evidence.correction_id, correction)
                self.assertEqual(tuple(change[0] for change in evidence.label_changes), labels)
        r1_second = read_source(self.db, 'R1', 1)
        self.assertEqual((r1_second.row.key, r1_second.row.reading, r1_second.original.key,
                          r1_second.original.reading, tuple(c[0] for c in r1_second.label_changes)),
                         ('A', 14, 'B', 14, ('TAG-SWAP',)))
        self.assertEqual(read_page(self.db, 'R2', 0, 1).rows[0].reading, 21)
        self.assertEqual(read_page(self.db, 'R4', 0, 1).rows[0].key, 'B')
        for report_id in ('R2', 'R4'):
            self.assertEqual(publish(self.db, STATION, DAY, report_id).report.rows[0].reading, 21)

    def test_reintake_after_withdrawal(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        publish(self.db, STATION, DAY, 'R1')
        correct_fact(self.db, CorrectionRequest(
            'W-B', OldImage.from_fact(self.fact('B')), 'withdraw', 'probe blocked'))
        self.assertIsNone(self.fact('B'))
        self.ingest('NEXT', [('B', 22)])
        self.assertEqual(self.fact('B').source, FactSource('NEXT', 0))
        self.assertEqual(self.fact('B').reading, 22)
        publish(self.db, STATION, DAY, 'R5')
        evidence = read_source(self.db, 'R5', 0)
        self.assertEqual((evidence.original.key, evidence.original.reading,
                          evidence.row.reading, evidence.label_changes), ('B', 22, 22, ()))
        old = read_source(self.db, 'R1', 0)
        self.assertEqual((old.row.key, old.original.key, tuple(c[0] for c in old.label_changes)),
                         ('B', 'A', ('TAG-SWAP',)))

    def test_reading_correction_wrong_label_context_rejected(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        correct_fact(self.db, CorrectionRequest(
            'VALUE-1', OldImage.from_fact(self.fact('B')), 'replace', 'fix', 21))
        publish(self.db, STATION, DAY, 'R2')
        # Rebind the granted correction to a label identity it never saw.
        self.db.execute("UPDATE corrections SET old_label_id = 'TAG-FORGED' WHERE correction_id = 'VALUE-1'")
        with self.assertRaises(IntegrityViolation):
            read_source(self.db, 'R2', 0)

    def test_reading_correction_survives_later_relabel(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        correct_fact(self.db, CorrectionRequest(
            'VALUE-1', OldImage.from_fact(self.fact('B')), 'replace', 'fix', 21))
        publish(self.db, STATION, DAY, 'R2')
        relabel_facts(self.db, 'TAG-OUT', 'out', self.pair(('B', 'C')))
        self.reopen()
        evidence = read_source(self.db, 'R2', 0)
        self.assertEqual((evidence.correction_id, evidence.correction_reason), ('VALUE-1', 'fix'))
        self.assertEqual(tuple(c[0] for c in evidence.label_changes), ('TAG-SWAP',))
        self.assertEqual(read_page(self.db, 'R2', 0, 1).rows[0].key, 'B')

    def test_reading_correction_replay_after_relabel(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        request = CorrectionRequest('VALUE-1', OldImage.from_fact(self.fact('B')), 'replace', 'fix', 21)
        first = correct_fact(self.db, request)
        publish(self.db, STATION, DAY, 'R2')
        relabel_facts(self.db, 'TAG-OUT', 'out', self.pair(('B', 'C')))
        self.reopen()
        self.assertEqual(correct_fact(self.db, request), first)
        self.assertEqual(self.fact('C').reading, 21)
        with self.assertRaises(Conflict):
            correct_fact(self.db, replace(request, reason='other evidence'))

    def test_source_survives_withdrawal(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        publish(self.db, STATION, DAY, 'R1')
        before = read_source(self.db, 'R1', 0)
        correct_fact(self.db, CorrectionRequest(
            'W-B', OldImage.from_fact(self.fact('B')), 'withdraw', 'withdraw'))
        self.reopen()
        self.assertEqual(read_source(self.db, 'R1', 0), before)

    def test_tampered_evidence_rejected(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        publish(self.db, STATION, DAY, 'R1')
        self.db.execute("UPDATE label_changes SET old_slot = 2 WHERE request_id = 'TAG-SWAP' AND new_key = 'B'")
        with self.assertRaises(IntegrityViolation):
            read_source(self.db, 'R1', 0)

    def test_label_history_misbound_to_other_fact_rejected(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        publish(self.db, STATION, DAY, 'R1')
        # Bind the swap hop to another committed fact's original label.
        self.db.execute("UPDATE label_changes SET old_key = 'D' WHERE request_id = 'TAG-SWAP' AND new_key = 'B'")
        with self.assertRaises(IntegrityViolation):
            read_source(self.db, 'R1', 0)

    def test_missing_label_history_rejected(self):
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        publish(self.db, STATION, DAY, 'R1')
        self.db.execute("DELETE FROM label_changes WHERE request_id = 'TAG-SWAP'")
        with self.assertRaises(IntegrityViolation):
            read_source(self.db, 'R1', 0)

    def test_old_report_after_reopen(self):
        self.ingest('NEXT', [('E', 20)])
        publish(self.db, STATION, DAY, 'OLD')
        self.reopen()
        relabel_facts(self.db, 'TAG-SWAP', 'swap', self.pair(('A', 'B'), ('B', 'A')))
        self.assertEqual([(row.key, row.reading) for row in read_page(self.db, 'OLD', 0, 5).rows],
                         [('E', 20), ('A', 19), ('B', 14)])
        self.assertEqual(read_source(self.db, 'OLD', 1).label_changes, ())
