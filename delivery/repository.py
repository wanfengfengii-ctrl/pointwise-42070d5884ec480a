from delivery.snapshot import FrozenReportRow, IssueReceipt, ReportHeader
from facts.provenance import FactSource
from limits.day_policy import DayPolicy
from quality.totals import CoverageTotals
from storage import IntegrityViolation, UnknownRecord, decode_mm, encode_mm


def load_report_header(db, report_id):
    row = db.execute(
        '''SELECT station, day, limit_mm, policy_source, anomaly_count, within_count, missing_count
           FROM reports WHERE report_id = ?''',
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    station, day, limit, source, anomalies, within, missing = row
    return ReportHeader(
        report_id, DayPolicy(station, day, decode_mm(limit), source), CoverageTotals(anomalies, within, missing)
    )


def require_report_header(db, report_id):
    header = load_report_header(db, report_id)
    if header is None:
        raise UnknownRecord(f'unknown report {report_id!r}')
    return header


def load_report_receipt(db, report_id):
    row = db.execute(
        '''SELECT h.station, h.day, h.anomaly_count, r.issued_count
           FROM report_receipts AS r JOIN reports AS h ON h.report_id = r.report_id
           WHERE r.report_id = ?''',
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    station, day, anomaly_count, issued_count = row
    if anomaly_count != issued_count:
        raise IntegrityViolation(f'issue receipt disagrees with report {report_id!r}')
    return IssueReceipt(report_id, station, day, issued_count)


def insert_frozen_report(db, report, receipt):
    header = report.header
    policy = header.policy
    totals = header.totals
    db.execute(
        '''INSERT INTO reports (report_id, station, day, limit_mm, policy_source,
                                anomaly_count, within_count, missing_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (header.report_id, policy.station, policy.day, encode_mm(policy.limit), policy.source,
         totals.anomalies, totals.within_limit, totals.missing),
    )
    db.executemany(
        '''INSERT INTO report_rows (report_id, position, observation_key, reading, excess,
                                    batch_id, slot, correction_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            (header.report_id, row.position, row.key, encode_mm(row.reading), encode_mm(row.excess),
             row.source.batch_id, row.source.slot, row.source.correction_id)
            for row in report.rows
        ),
    )
    db.execute(
        'INSERT INTO report_receipts (report_id, issued_count) VALUES (?, ?)',
        (receipt.report_id, receipt.total_rows),
    )


def _frozen_row(row):
    position, key, reading, excess, batch_id, slot, correction_id = row
    return FrozenReportRow(position, key, decode_mm(reading), decode_mm(excess), FactSource(batch_id, slot, correction_id))


def load_frozen_slice(db, report_id, start, end):
    if start >= end:
        return ()
    result = db.execute(
        '''SELECT position, observation_key, reading, excess, batch_id, slot, correction_id
           FROM report_rows WHERE report_id = ? AND position >= ? AND position < ? ORDER BY position''',
        (report_id, start, end),
    )
    rows = tuple(_frozen_row(row) for row in result)
    if len(rows) != end - start or any(row.position != index for index, row in enumerate(rows, start)):
        raise IntegrityViolation(f'fixed report {report_id!r} has incomplete row positions')
    return rows


def load_frozen_row(db, report_id, position):
    return load_frozen_slice(db, report_id, position, position + 1)[0]
