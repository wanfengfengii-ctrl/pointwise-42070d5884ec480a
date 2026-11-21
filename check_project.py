"""Run this project's full unittest suite from the repository root."""
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from batches.intake import ingest
from delivery.issue import publish
from delivery.pages import read_page
from delivery.source_lookup import read_source
from facts.corrections import CorrectionRequest, correct_fact
from facts.labels import relabel_facts
from facts.old_image import OldImage
from facts.repository import load_current_fact
from limits.day_policy import register
from storage import Conflict
from warehouse import initialize, open_warehouse


def verify_reopened_label_chain():
    """End-to-end: two reports with the same settled key/reading, distinct label trails."""
    with TemporaryDirectory(prefix='tide-verify-') as directory:
        path = Path(directory) / 'verify.sqlite3'
        station, day = 'WB01', '2026-10-08'
        with closing(open_warehouse(path)) as db:
            initialize(db)
            register(db, station, day, 10, 'LIMIT-8')
            ingest(db, station, day, 'EARLY', [('A', 19), ('B', 14), ('D', 10)])

            def pair(*pairs):
                return [(OldImage.from_fact(load_current_fact(db, station, day, old)), new) for old, new in pairs]

            publish(db, station, day, 'R0')
            relabel_facts(db, 'TAG-SWAP', '校对双槽标签', pair(('A', 'B'), ('B', 'A')))
            publish(db, station, day, 'R1')
            correct_fact(db, CorrectionRequest(
                'VALUE-1', OldImage.from_fact(load_current_fact(db, station, day, 'B')),
                'replace', '复查仪表单', 21,
            ))
            publish(db, station, day, 'R2')
            try:
                relabel_facts(db, 'TAG-OCCUPIED', '待复核标签', pair(('B', 'D')))
            except Conflict:
                pass
            else:
                raise RuntimeError('label request occupied an outside key')
            relabel_facts(db, 'TAG-OUT', '改挂临时标签', pair(('B', 'C')))
            publish(db, station, day, 'R3')
            relabel_facts(db, 'TAG-BACK', '恢复核定标签', pair(('C', 'B')))
            publish(db, station, day, 'R4')
        with closing(open_warehouse(path)) as db:
            for report_id in ('R2', 'R4'):
                page = read_page(db, report_id, 0, 1)
                evidence = read_source(db, report_id, 0)
                expected = ('B', 21)
                if (page.rows[0].key, page.rows[0].reading) != expected:
                    raise RuntimeError(f'{report_id} reopened row mismatch')
                if (evidence.original.key, evidence.original.position) != ('A', 0):
                    raise RuntimeError(f'{report_id} did not reach EARLY slot 0')
                if evidence.correction_id != 'VALUE-1':
                    raise RuntimeError(f'{report_id} lost its reading correction')
            r2_labels = tuple(change[0] for change in read_source(db, 'R2', 0).label_changes)
            r4_labels = tuple(change[0] for change in read_source(db, 'R4', 0).label_changes)
            if r2_labels != ('TAG-SWAP',):
                raise RuntimeError(f'R2 label trail {r2_labels!r}')
            if r4_labels != ('TAG-SWAP', 'TAG-OUT', 'TAG-BACK'):
                raise RuntimeError(f'R4 label trail {r4_labels!r}')


def main():
    try:
        verify_reopened_label_chain()
    except Exception as exc:  # noqa: BLE001 - the run list must fail closed
        print(f'VERIFY FAILURE {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
