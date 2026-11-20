from dataclasses import dataclass

from delivery.repository import load_frozen_slice, require_report_header
from delivery.snapshot import FrozenReportRow
from limits.validation import require_text
from storage import transaction


@dataclass(frozen=True)
class ReportPage:
    report_id: str
    start: int
    total_rows: int
    rows: tuple[FrozenReportRow, ...]


def read_page(db, report_id, start, length):
    require_text(report_id, 'report id')
    if type(start) is not int or start < 0 or type(length) is not int or length < 0:
        raise ValueError('page start and length must be nonnegative integers')
    with transaction(db):
        header = require_report_header(db, report_id)
        end = min(start + length, header.total_rows)
        rows = load_frozen_slice(db, report_id, start, end)
        return ReportPage(report_id, start, header.total_rows, rows)
