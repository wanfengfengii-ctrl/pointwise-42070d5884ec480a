from dataclasses import replace
import sqlite3

from batches.repository import load_batch, load_batch_receipt, load_raw_slot
from batches.rows import RawSlot
from facts.corrections import CorrectionRequest, correct_fact
from facts.old_image import OldImage
from facts.provenance import FactSource
from facts.repository import load_correction
from quality.evaluate import preview
from quality.totals import CoverageTotals
from storage import Conflict
from tests.support import DAY, STATION, WarehouseCase, deny_commit


class CorrectionTests(WarehouseCase):
    def test_stale_provenance(self):
        self.register()
        self.ingest('EARLY', [('A', 15)])
        stale = self.correction('STALE', 'A', 'withdraw', 'older evidence')
        accepted = correct_fact(self.db, self.correction('C1', 'A', 'replace', 'new gauge slip', 15))
        before_rejection = preview(self.db, STATION, DAY)
        self.assertEqual(accepted.after.source, FactSource('EARLY', 0, 'C1'))
        with self.assertRaises(Conflict):
            correct_fact(self.db, stale)
        self.assertIsNone(load_correction(self.db, 'STALE'))
        self.assertEqual(self.fact('A'), accepted.after)
        self.assertEqual(preview(self.db, STATION, DAY), before_rejection)
        self.assertEqual(before_rejection.totals, CoverageTotals(1, 0, 0))

    def test_withdraw_keeps_source(self):
        self.register()
        rows = [('A', 15), ('B', 10)]
        original = self.ingest('EARLY', rows)
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(1, 1, 0))
        request = self.correction('W1', 'A', 'withdraw', 'probe obstructed')
        receipt = correct_fact(self.db, request)
        self.assertIsNone(receipt.after)
        self.reopen()
        self.assertIsNone(self.fact('A'))
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(0, 1, 0))
        self.assertEqual(load_batch(self.db, 'EARLY').slots, (RawSlot(0, 'A', 15), RawSlot(1, 'B', 10)))
        self.assertEqual(load_raw_slot(self.db, 'EARLY', 0), RawSlot(0, 'A', 15))
        self.assertEqual(load_batch_receipt(self.db, 'EARLY'), original)
        self.assertEqual(self.ingest('EARLY', rows), original)
        self.assertIsNone(self.fact('A'))
        self.ingest('RETAKE', [('A', 15)])
        self.assertEqual(self.fact('A').source, FactSource('RETAKE', 0))
        self.assertEqual(correct_fact(self.db, request), receipt)
        self.assertEqual(self.fact('A').source, FactSource('RETAKE', 0))
        self.assertEqual(load_raw_slot(self.db, 'EARLY', 0), RawSlot(0, 'A', 15))

    def test_correction_replay_preserves_later_update(self):
        self.register()
        self.ingest('EARLY', [('A', 15)])
        first_request = self.correction('C1', 'A', 'replace', 'reading recovered', 16)
        first = correct_fact(self.db, first_request)
        later = correct_fact(self.db, self.correction('C2', 'A', 'replace', 'latest audit', 9))
        self.reopen()
        self.assertEqual(correct_fact(self.db, first_request), first)
        self.assertEqual(first.after.reading, 16)
        self.assertEqual(first.after.source, FactSource('EARLY', 0, 'C1'))
        altered = replace(first_request, reason='different evidence')
        with self.assertRaises(Conflict):
            correct_fact(self.db, altered)
        self.assertEqual(self.fact('A'), later.after)
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(0, 1, 0))

    def test_old_image_and_replacement_validation(self):
        self.register()
        self.ingest('EARLY', [('A', 0), ('M', None)])
        original = self.fact('A')
        old = OldImage.from_fact(original)
        invalid = (
            CorrectionRequest('BAD', replace(old, reading=False), 'withdraw', 'evidence'),
            CorrectionRequest('BAD', replace(old, source=FactSource('EARLY', False)), 'withdraw', 'evidence'),
            CorrectionRequest('BAD', old, 'withdraw', 'evidence', 2),
            CorrectionRequest('BAD', old, 'replace', 'evidence', 0.0),
            CorrectionRequest('BAD', old, 'replace', '', None),
            CorrectionRequest('BAD', old, 'restore', 'evidence'),
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                correct_fact(self.db, request)
        self.assertIsNone(load_correction(self.db, 'BAD'))
        self.assertEqual(self.fact('A'), original)
        with self.assertRaises(Conflict):
            correct_fact(self.db, CorrectionRequest('STALE', replace(old, reading=None), 'withdraw', 'wrong value'))
        to_missing = correct_fact(self.db, self.correction('C1', 'A', 'replace', 'gauge unreadable', None))
        self.assertIsNone(to_missing.after.reading)
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(0, 0, 2))
        recovered = correct_fact(self.db, self.correction('C2', 'A', 'replace', 'manual reading', 18))
        self.assertEqual(recovered.after.source, FactSource('EARLY', 0, 'C2'))
        self.assertEqual(preview(self.db, STATION, DAY).rows[0].excess, 8)

    def test_failed_correction_rolls_back_fact_and_receipt(self):
        self.register()
        self.ingest('EARLY', [('A', 15)])
        original = self.fact('A')
        request = self.correction('C1', 'A', 'replace', 'audited low reading', 9)
        with deny_commit(self.db) as (attempts, statements):
            with self.assertRaises(sqlite3.DatabaseError):
                correct_fact(self.db, request)
        self.assertEqual(attempts, ['COMMIT'])
        self.assertTrue(any(sql.lstrip().startswith('INSERT INTO corrections') for sql in statements))
        self.reopen()
        self.assertEqual(self.fact('A'), original)
        self.assertIsNone(load_correction(self.db, 'C1'))
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(1, 0, 0))
        self.assertEqual(correct_fact(self.db, request).after.reading, 9)
