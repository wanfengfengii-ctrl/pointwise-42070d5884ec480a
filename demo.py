from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from batches.intake import ingest
from delivery.issue import publish
from delivery.pages import read_page
from delivery.source_lookup import read_source
from facts.corrections import CorrectionRequest, correct_fact
from facts.old_image import OldImage
from facts.repository import load_current_fact, load_day_facts
from limits.day_policy import register
from quality.evaluate import preview
from warehouse import initialize, open_warehouse


def apply_correction(db, station, day, instruction):
    old = OldImage.from_fact(load_current_fact(db, station, day, instruction['key']))
    request = CorrectionRequest(
        instruction['id'], old, instruction['action'], instruction['reason'], instruction.get('reading')
    )
    return correct_fact(db, request)


def require_equal(actual, expected, label):
    if actual != expected:
        raise RuntimeError(f'{label}: expected {expected!r}, got {actual!r}')


def run_day(sample, path):
    station = sample['station']
    day = sample['day']
    report_id = sample['report_id']
    morning = sample['morning']
    evening = sample['evening']
    with closing(open_warehouse(path)) as db:
        initialize(db)
        register(db, station, day, sample['limit'], sample['policy_source'])
        early_receipt = ingest(db, station, day, morning['batch_id'], morning['rows'])
        ingest(db, station, day, evening['batch_id'], evening['rows'])
        replay = ingest(db, station, day, morning['batch_id'], morning['rows'])
        require_equal(replay, early_receipt, 'intake replay')
        require_equal(len(load_day_facts(db, station, day)), 5, 'two-shift fact count')
        for instruction in sample['before_issue']:
            apply_correction(db, station, day, instruction)
        result = preview(db, station, day)
        issued = publish(db, station, day, report_id)
        apply_correction(db, station, day, sample['after_issue'])
    with closing(open_warehouse(path)) as db:
        pages = [read_page(db, report_id, start, 1) for start in (0, 1)]
        evidence = read_source(db, report_id, 0)
        current = load_current_fact(db, station, day, 'A')
        require_equal(publish(db, station, day, report_id), issued, 'issue replay after reopen')
    actual = {
        'rows': [[row.key, row.reading, row.excess] for row in result.rows],
        'coverage': asdict(result.totals),
    }
    downstream = {
        'pages': [[row.position, row.key, row.reading, row.excess] for page in pages for row in page.rows],
        'page_totals': [page.total_rows for page in pages],
        'report_coverage': asdict(issued.report.header.totals),
        'frozen_origin': [evidence.row.source.batch_id, evidence.original.position, evidence.original.reading,
                          evidence.correction_id, evidence.correction_reason],
        'current_a': current.reading,
    }
    return actual, downstream


def main():
    fixture = Path(__file__).with_name('examples') / 'bay_day.json'
    sample = json.loads(fixture.read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='tide-demo-') as directory:
        actual, downstream = run_day(sample, Path(directory) / 'station.sqlite3')
    expected = {
        'rows': [['A', 15, 5], ['C', 15, 5]],
        'coverage': {'anomalies': 2, 'within_limit': 1, 'missing': 1},
    }
    downstream_expected = {
        'pages': [[0, 'A', 15, 5], [1, 'C', 15, 5]],
        'page_totals': [2, 2],
        'report_coverage': {'anomalies': 2, 'within_limit': 1, 'missing': 1},
        'frozen_origin': ['EARLY', 2, 12, 'COR-A', 'Gauge slip A-17'],
        'current_a': 9,
    }
    require_equal(actual, expected, 'quality preview')
    require_equal(downstream, downstream_expected, 'reopened handoff')
    evidence = {
        'case_id': 'v_fixed', 'input': sample, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream,
        'code_paths': ['batches/intake.py', 'facts/corrections.py', 'quality/evaluate.py',
                       'delivery/issue.py', 'delivery/pages.py', 'delivery/source_lookup.py'],
        'oracle': 'Hand calculation: 15 minus 10 is 5 for A and C; B meets the limit and M is missing. '
                  'A sorts before C. EARLY slot 2 holds 12 and R1 uses COR-A before COR-L changes A to 9.',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
