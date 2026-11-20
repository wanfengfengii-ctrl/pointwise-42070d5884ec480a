from dataclasses import asdict
import json

from delivery.issue import publish
from delivery.pages import read_page
from facts.corrections import correct_fact
from quality.evaluate import preview
from quality.totals import CoverageTotals
from storage import UnknownRecord
from tests.support import DAY, STATION, WarehouseCase


class QualityTests(WarehouseCase):
    def test_threshold_and_missing(self):
        self.register()
        rows = [('LIMIT', 10), ('WAVE', 12), ('GAP', None)]
        self.ingest('EARLY', rows)
        correct_fact(self.db, self.correction('C1', 'WAVE', 'replace', 'corrected gauge reading', 15))
        result = preview(self.db, STATION, DAY)
        expected = {'rows': [['WAVE', 15, 5]], 'coverage': {'anomalies': 1, 'within_limit': 1, 'missing': 1}}
        actual = {'rows': [[row.key, row.reading, row.excess] for row in result.rows],
                  'coverage': asdict(result.totals)}
        self.assertEqual(actual, expected)
        self.assertEqual(result.totals.total, 3)
        self.assertEqual(result.rows[0].source.correction_id, 'C1')
        published = publish(self.db, STATION, DAY, 'R')
        page = read_page(self.db, 'R', 0, 2)
        downstream = {'rows': [[row.position, row.key, row.reading, row.excess] for row in page.rows],
                      'total_rows': page.total_rows, 'coverage': asdict(published.report.header.totals)}
        downstream_expected = {'rows': [[0, 'WAVE', 15, 5]], 'total_rows': 1,
                               'coverage': {'anomalies': 1, 'within_limit': 1, 'missing': 1}}
        self.assertEqual(downstream, downstream_expected)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v_exact', 'input': {'limit': 10, 'rows': rows, 'replacement': ['WAVE', 15]},
            'expected': expected, 'actual': actual, 'downstream_expected': downstream_expected,
            'downstream_actual': downstream,
            'code_paths': ['facts/corrections.py', 'quality/classification.py', 'quality/evaluate.py',
                           'delivery/issue.py', 'delivery/pages.py'],
            'oracle': '15 minus 10 gives 5; 10 meets the limit and GAP has no reading.',
        }, sort_keys=True))

    def test_integer_precision(self):
        self.register(9007199254740992)
        self.ingest('EARLY', [('A', 9007199254740995)])
        self.reopen()
        result = preview(self.db, STATION, DAY)
        self.assertEqual(result.policy.limit, 9007199254740992)
        self.assertEqual(self.fact('A').reading, 9007199254740995)
        self.assertEqual((result.rows[0].reading, result.rows[0].excess), (9007199254740995, 3))
        self.assertIs(type(result.rows[0].excess), int)
        self.assertEqual(result.totals, CoverageTotals(1, 0, 0))

    def test_excess_beyond_sqlite_integer_range_survives_publication(self):
        limit = -(1 << 65)
        reading = (1 << 65) + 3
        self.register(limit)
        self.ingest('EARLY', [('A', reading), ('B', limit), ('M', None)])
        publish(self.db, STATION, DAY, 'BIG')
        self.reopen()
        page = read_page(self.db, 'BIG', 0, 1)
        self.assertEqual((page.rows[0].reading, page.rows[0].excess, page.total_rows),
                         (36893488147419103235, 73786976294838206467, 1))
        self.assertEqual(preview(self.db, STATION, DAY).totals, CoverageTotals(1, 1, 1))

    def test_empty_day(self):
        policy = self.register()
        result = preview(self.db, STATION, DAY)
        self.assertEqual(result.policy, policy)
        self.assertEqual(result.rows, ())
        self.assertEqual(result.totals, CoverageTotals(0, 0, 0))
        issued = publish(self.db, STATION, DAY, 'EMPTY')
        self.assertEqual(issued.receipt.total_rows, 0)
        self.assertEqual(read_page(self.db, 'EMPTY', 0, 1).rows, ())

    def test_unknown_day(self):
        with self.assertRaises(UnknownRecord):
            preview(self.db, STATION, DAY)
        with self.assertRaises(UnknownRecord):
            publish(self.db, STATION, DAY, 'R')
        self.register()
        self.assertEqual(publish(self.db, STATION, DAY, 'R').receipt.total_rows, 0)
