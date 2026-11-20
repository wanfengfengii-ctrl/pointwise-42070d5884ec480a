from dataclasses import dataclass

from delivery.repository import (
    insert_frozen_report, load_frozen_slice, load_report_header, load_report_receipt,
)
from delivery.snapshot import FrozenReport, freeze_report
from delivery.snapshot import IssueReceipt as IssueReceipt
from limits.validation import require_text, validate_station_day
from quality.evaluate import evaluate_snapshot, load_quality_inputs
from quality.verification import verify_issue_result
from storage import Conflict, IntegrityViolation, transaction


@dataclass(frozen=True)
class IssuedReport:
    report: FrozenReport
    receipt: IssueReceipt


def _store_verified_report(db, report_id, policy, facts, candidate):
    verify_issue_result(policy, facts, candidate)
    report = freeze_report(report_id, candidate)
    receipt = IssueReceipt(report_id, policy.station, policy.day, report.header.total_rows)
    insert_frozen_report(db, report, receipt)
    return IssuedReport(report, receipt)


def _replay_report(db, header, station, day):
    if (header.policy.station, header.policy.day) != (station, day):
        raise Conflict('report id is already bound to another station-day')
    receipt = load_report_receipt(db, header.report_id)
    if receipt is None:
        raise IntegrityViolation(f'missing issue receipt for {header.report_id!r}')
    rows = load_frozen_slice(db, header.report_id, 0, header.total_rows)
    return IssuedReport(FrozenReport(header, rows), receipt)


def publish(db, station, day, report_id):
    require_text(report_id, 'report id')
    validate_station_day(station, day)
    with transaction(db, write=True):
        existing = load_report_header(db, report_id)
        if existing is not None:
            return _replay_report(db, existing, station, day)
        policy, facts = load_quality_inputs(db, station, day)
        candidate = evaluate_snapshot(policy, facts)
        return _store_verified_report(db, report_id, policy, facts, candidate)
