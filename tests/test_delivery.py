from dataclasses import replace
import sqlite3

from delivery.issue import IssueReceipt, _store_verified_report, publish
from delivery.pages import read_page
from delivery.repository import load_report_header, load_report_receipt
from delivery.source_lookup import read_source
from facts.corrections import correct_fact
from facts.provenance import FactSource
from quality.evaluate import evaluate_snapshot, load_quality_inputs, preview
from quality.totals import CoverageTotals
from storage import Conflict, IntegrityViolation, UnknownRecord, transaction
from tests.support import DAY, PREVIOUS_DAY, STATION, WarehouseCase, deny_commit


class DeliveryTests(WarehouseCase):
    def test_fixed_order_after_reopen(self):
        policy = self.register()
        self.ingest('EARLY', [('B', 15), ('M', None), ('A', 15), ('N', 10)])
        self.assertEqual([fact.key for fact in self.facts()], ['B', 'M', 'A', 'N'])
        issued = publish(self.db, STATION, DAY, 'R1')
        correct_fact(self.db, self.correction('DOWN', 'A', 'replace', 'new audit', 8))
        self.reopen()
        first = read_page(self.db, 'R1', 0, 1)
        second = read_page(self.db, 'R1', 1, 1)
        self.assertEqual([first.total_rows, second.total_rows], [2, 2])
        self.assertEqual([(row.position, row.key, row.reading, row.excess) for row in first.rows + second.rows],
                         [(0, 'A', 15, 5), (1, 'B', 15, 5)])
        self.assertEqual(first.rows[0].source, FactSource('EARLY', 2))
        self.assertEqual(second.rows[0].source, FactSource('EARLY', 0))
        self.assertEqual(issued.report.header.policy, policy)
        self.assertEqual(issued.report.header.totals, CoverageTotals(2, 1, 1))
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(1, 2, 1))
        self.assertEqual(publish(self.db, STATION, DAY, 'R1'), issued)
        self.register(day=PREVIOUS_DAY)
        with self.assertRaises(Conflict):
            publish(self.db, STATION, PREVIOUS_DAY, 'R1')
        self.assertEqual(read_page(self.db, 'R1', 0, 1), first)

    def test_fresh_issue(self):
        self.register()
        self.ingest('EARLY', [('A', 12), ('B', 10), ('M', None)])
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(1, 1, 1))
        correct_fact(self.db, self.correction('A-DOWN', 'A', 'replace', 'lowered', 9))
        correct_fact(self.db, self.correction('B-UP', 'B', 'replace', 'raised', 18))
        correct_fact(self.db, self.correction('M-UP', 'M', 'replace', 'recovered', 14))
        signed = publish(self.db, STATION, DAY, 'R1')
        self.assertEqual([(row.key, row.excess) for row in signed.report.rows], [('B', 8), ('M', 4)])
        self.assertEqual(signed.report.header.totals, CoverageTotals(2, 1, 0))
        correct_fact(self.db, self.correction('B-DOWN', 'B', 'replace', 'new audit', 11))
        self.assertEqual([row.key for row in preview(self.db, STATION, DAY).rows], ['M', 'B'])
        self.assertEqual([row.key for row in read_page(self.db, 'R1', 0, 2).rows], ['B', 'M'])

    def test_failed_issue_retry(self):
        self.register()
        self.ingest('EARLY', [('A', 19), ('B', 12)])
        with deny_commit(self.db) as (attempts, statements):
            with self.assertRaises(sqlite3.DatabaseError):
                publish(self.db, STATION, DAY, 'R1')
        self.assertEqual(attempts, ['COMMIT'])
        for table in ('reports', 'report_rows', 'report_receipts'):
            self.assertTrue(any(sql.lstrip().startswith(f'INSERT INTO {table} (') for sql in statements), table)
        self.assertFalse(self.db.in_transaction)
        self.reopen()
        self.assertIsNone(load_report_header(self.db, 'R1'))
        self.assertIsNone(load_report_receipt(self.db, 'R1'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM report_rows').fetchone(), (0,))
        with self.assertRaises(UnknownRecord):
            read_page(self.db, 'R1', 0, 2)
        result = publish(self.db, STATION, DAY, 'R1')
        self.assertEqual(result.receipt, IssueReceipt('R1', STATION, DAY, 2))
        self.reopen()
        self.assertEqual(publish(self.db, STATION, DAY, 'R1'), result)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reports').fetchone(), (1,))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM report_receipts').fetchone(), (1,))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM report_rows').fetchone(), (2,))
        self.assertEqual([(row.key, row.excess) for row in read_page(self.db, 'R1', 0, 2).rows], [('A', 9), ('B', 2)])

    def test_verification_failure_leaves_number_available(self):
        self.register()
        self.ingest('EARLY', [('A', 17), ('B', 14), ('M', None)])
        policy, facts = load_quality_inputs(self.db, STATION, DAY)
        candidate = evaluate_snapshot(policy, facts)
        first, second = candidate.rows
        malformed = {
            'difference': replace(candidate, rows=(replace(first, excess=8), second)),
            'source': replace(candidate, rows=(replace(first, source=second.source), second)),
            'missing': replace(candidate, rows=(first,)),
            'order': replace(candidate, rows=(second, first)),
            'coverage': replace(candidate, totals=CoverageTotals(2, 1, 0)),
            'duplicate': replace(candidate, rows=(first, first)),
        }
        for defect, result in malformed.items():
            with self.subTest(case=defect):
                with self.assertRaises(IntegrityViolation), transaction(self.db, write=True):
                    _store_verified_report(self.db, 'R1', policy, facts, result)
                self.assertIsNone(load_report_header(self.db, 'R1'))
                self.assertIsNone(load_report_receipt(self.db, 'R1'))
        self.assertEqual(publish(self.db, STATION, DAY, 'R1').receipt.total_rows, 2)
        self.assertEqual([(row.key, row.excess) for row in read_page(self.db, 'R1', 0, 2).rows], [('A', 7), ('B', 4)])

    def test_absolute_slice(self):
        self.register()
        self.ingest('EARLY', [('A', 16), ('B', 19), ('C', 13), ('D', 17)])
        publish(self.db, STATION, DAY, 'R1')
        page = read_page(self.db, 'R1', 1, 2)
        self.assertEqual([(row.position, row.key) for row in page.rows], [(1, 'D'), (2, 'A')])
        self.assertEqual(page.total_rows, 4)
        for start, length in ((0, 0), (4, 1), (20, 2), (1 << 70, 4)):
            with self.subTest(start=start, length=length):
                empty = read_page(self.db, 'R1', start, length)
                self.assertEqual((empty.start, empty.total_rows, empty.rows), (start, 4, ()))
        self.assertEqual(len(read_page(self.db, 'R1', 1, 1 << 70).rows), 3)

    def test_page_errors(self):
        with self.assertRaises(UnknownRecord):
            read_page(self.db, 'MISSING', 0, 0)
        self.register()
        publish(self.db, STATION, DAY, 'R1')
        for start, length in ((-1, 0), (0, -1), (False, 1), (0, True), (0, 1.0), ('0', 1)):
            with self.subTest(start=start, length=length), self.assertRaises(ValueError):
                read_page(self.db, 'R1', start, length)

    def test_frozen_source(self):
        self.register()
        self.ingest('EARLY', [('M', None), ('A', 12), ('B', 11)])
        correct_fact(self.db, self.correction('C1', 'A', 'replace', 'first gauge evidence', 15))
        publish(self.db, STATION, DAY, 'R1')
        correct_fact(self.db, self.correction('C2', 'A', 'replace', 'later gauge evidence', 15))
        self.reopen()
        frozen = read_source(self.db, 'R1', 0)
        page = read_page(self.db, 'R1', 0, 1)
        self.assertEqual(frozen.row, page.rows[0])
        self.assertEqual((frozen.row.position, frozen.original.position, frozen.original.key, frozen.original.reading),
                         (0, 1, 'A', 12))
        self.assertEqual((frozen.row.reading, frozen.correction_id, frozen.correction_reason),
                         (15, 'C1', 'first gauge evidence'))
        self.assertEqual(self.fact('A').source, FactSource('EARLY', 1, 'C2'))
        second = read_source(self.db, 'R1', 1)
        self.assertEqual((second.original.key, second.original.reading, second.correction_reason), ('B', 11, None))
        correct_fact(self.db, self.correction('W', 'A', 'withdraw', 'removed from current ledger'))
        self.assertEqual(read_source(self.db, 'R1', 0), frozen)

    def test_source_errors(self):
        with self.assertRaises(UnknownRecord):
            read_source(self.db, 'MISSING', 0)
        self.register()
        self.ingest('EARLY', [('A', 12), ('B', 15)])
        correct_fact(self.db, self.correction('C1', 'A', 'replace', 'gauge audit', 17))
        publish(self.db, STATION, DAY, 'R1')
        with self.assertRaises(IndexError):
            read_source(self.db, 'R1', 2)
        with self.assertRaises(ValueError):
            read_source(self.db, 'R1', -1)
        with self.assertRaises(ValueError):
            read_source(self.db, 'R1', True)
        # Damage only this isolated fixture to exercise the real lookup errors.
        self.db.execute('DELETE FROM corrections WHERE correction_id = ?', ('C1',))
        with self.assertRaises(IntegrityViolation):
            read_source(self.db, 'R1', 0)
        self.db.execute('PRAGMA foreign_keys = OFF')
        self.db.execute('DELETE FROM raw_slots WHERE batch_id = ? AND position = ?', ('EARLY', 1))
        self.db.execute('PRAGMA foreign_keys = ON')
        with self.assertRaises(IntegrityViolation):
            read_source(self.db, 'R1', 1)
