from contextlib import closing, contextmanager
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from batches.intake import ingest
from delivery.issue import publish
from delivery.pages import read_page
from delivery.source_lookup import read_source
from facts.corrections import CorrectionRequest, correct_fact
from facts.labels import relabel_facts
from facts.old_image import OldImage
from facts.repository import load_current_fact, load_day_facts
from limits.day_policy import register
from quality.evaluate import preview
from storage import Conflict
from warehouse import initialize, open_warehouse


def apply_correction(db, station, day, instruction):
    old = OldImage.from_fact(load_current_fact(db, station, day, instruction['key']))
    request = CorrectionRequest(
        instruction['id'], old, instruction.get('action', 'replace'), instruction['reason'],
        instruction.get('reading'),
    )
    return correct_fact(db, request)


def apply_label_change(db, station, day, instruction):
    changes = tuple(
        (OldImage.from_fact(load_current_fact(db, station, day, old_key)), new_key)
        for old_key, new_key in instruction['pairs']
    )
    return relabel_facts(db, instruction['id'], instruction['reason'], changes)


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


def run_handoff(sample, path):
    station = sample['station']
    day = sample['day']
    with closing(open_warehouse(path)) as db:
        initialize(db)
        register(db, station, day, sample['limit'], sample['policy_source'])
        ingest(db, station, day, sample['batch_id'], sample['rows'])
        publish(db, station, day, 'R0')
        apply_label_change(db, station, day, sample['swap'])
        publish(db, station, day, 'R1')
        apply_correction(db, station, day, sample['reading_fix'])
        publish(db, station, day, 'R2')
        with self_contained_conflict() as flags:
            apply_label_change(db, station, day, sample['occupied'])
        occupied_rejected = flags[0]
        apply_label_change(db, station, day, sample['move_out'])
        publish(db, station, day, 'R3')
        apply_label_change(db, station, day, sample['move_back'])
        publish(db, station, day, 'R4')
        correct_fact(db, CorrectionRequest(
            'WITHDRAW-B', OldImage.from_fact(load_current_fact(db, station, day, 'B')),
            'withdraw', '夜间探杆受阻',
        ))
        reintake = sample['reintake']
        ingest(db, station, day, reintake['batch_id'], reintake['rows'])
        publish(db, station, day, 'R5')
    with closing(open_warehouse(path)) as db:
        history = {}
        for report_id in ('R0', 'R1', 'R2', 'R3', 'R4', 'R5'):
            evidence = read_source(db, report_id, 0)
            history[report_id] = {
                'key': evidence.row.key,
                'reading': evidence.row.reading,
                'batch': evidence.row.source.batch_id,
                'original': [evidence.original.key, evidence.original.position, evidence.original.reading],
                'labels': [list(change) for change in evidence.label_changes],
                'correction': [evidence.correction_id, evidence.correction_reason],
            }
        r2_page = read_page(db, 'R2', 0, 1)
        r4_page = read_page(db, 'R4', 0, 1)
    return {
        'history': history,
        'r2': [r2_page.rows[0].key, r2_page.rows[0].reading],
        'r4': [r4_page.rows[0].key, r4_page.rows[0].reading],
        'occupied_rejected': occupied_rejected,
    }


@contextmanager
def self_contained_conflict():
    flags = [False]
    try:
        yield flags
    except Conflict:
        flags[0] = True


def main():
    fixture_dir = Path(__file__).with_name('examples')
    sample = json.loads((fixture_dir / 'bay_day.json').read_text(encoding='utf-8'))
    handoff = json.loads((fixture_dir / 'label_review.json').read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='tide-demo-') as directory:
        actual, downstream = run_day(sample, Path(directory) / 'station.sqlite3')
        handoff_result = run_handoff(handoff, Path(directory) / 'handoff.sqlite3')
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

    history = handoff_result['history']
    history_expected = {
        'R0': ('A', 19, None, []),
        'R1': ('B', 19, None, ['TAG-SWAP']),
        'R2': ('B', 21, 'VALUE-1', ['TAG-SWAP']),
        'R3': ('C', 21, 'VALUE-1', ['TAG-SWAP', 'TAG-OUT']),
        'R4': ('B', 21, 'VALUE-1', ['TAG-SWAP', 'TAG-OUT', 'TAG-BACK']),
        'R5': ('B', 22, None, []),
    }
    for report_id, (key, reading, correction, labels) in history_expected.items():
        entry = history[report_id]
        require_equal((entry['key'], entry['reading'], entry['correction'][0],
                       [change[0] for change in entry['labels']]),
                      (key, reading, correction, labels), f'{report_id} label history')
        expected_origin = 'B' if report_id == 'R5' else 'A'
        require_equal(entry['original'][0], expected_origin, f'{report_id} original label')
    require_equal(handoff_result['r2'], handoff_result['r4'], 'reopened identical row')
    require_equal(handoff_result['r2'], ['B', 21], 'reopened row content')
    require_equal(history['R2']['labels'], [['TAG-SWAP', '校对双槽标签']], 'R2 label trail')
    require_equal(history['R4']['labels'],
                  [['TAG-SWAP', '校对双槽标签'], ['TAG-OUT', '改挂临时标签'], ['TAG-BACK', '恢复核定标签']],
                  'R4 label trail')
    require_equal((history['R5']['batch'], history['R5']['original']), ('NEXT', ['B', 0, 22]),
                  'R5 fresh original slot')
    require_equal(handoff_result['occupied_rejected'], True, 'occupied target refusal')

    evidence = {
        'case_id': 'v_fixed', 'input': sample, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream,
        'code_paths': ['batches/intake.py', 'facts/corrections.py', 'quality/evaluate.py',
                       'delivery/issue.py', 'delivery/pages.py', 'delivery/source_lookup.py'],
        'oracle': 'Hand calculation: 15 minus 10 is 5 for A and C; B meets the limit and M is missing. '
                  'A sorts before C. EARLY slot 2 holds 12 and R1 uses COR-A before COR-L changes A to 9.',
    }
    label_evidence = {
        'case_id': 'v_label_review', 'input': handoff,
        'expected': {report_id: list(values) for report_id, values in history_expected.items()},
        'actual': {report_id: [entry['key'], entry['reading'], entry['correction'][0],
                               [change[0] for change in entry['labels']]]
                   for report_id, entry in history.items()},
        'reopened': {'r2': handoff_result['r2'], 'r4': handoff_result['r4'],
                     'r2_labels': [change[0] for change in history['R2']['labels']],
                     'r4_labels': [change[0] for change in history['R4']['labels']]},
        'code_paths': ['facts/labels.py', 'facts/label_repository.py', 'delivery/source_lookup.py'],
        'oracle': 'R2 and R4 share key B and reading 21 after reopen but reach EARLY slot 0 through '
                  'different label histories; the occupied D target is refused with Conflict.',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    print('POINTWISE_SCENARIO ' + json.dumps(label_evidence, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
