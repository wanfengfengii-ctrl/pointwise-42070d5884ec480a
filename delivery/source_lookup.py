from dataclasses import dataclass

from batches.repository import load_batch_scope, load_raw_slot
from batches.rows import RawSlot
from delivery.repository import load_frozen_row, require_report_header
from delivery.snapshot import FrozenReportRow
from facts.repository import CurrentFact, load_correction
from limits.validation import require_text
from storage import IntegrityViolation, transaction


@dataclass(frozen=True)
class SourceEvidence:
    report_id: str
    row: FrozenReportRow
    original: RawSlot
    correction_id: str | None
    correction_reason: str | None


def read_source(db, report_id, position):
    require_text(report_id, 'report id')
    if type(position) is not int or position < 0:
        raise ValueError('source position must be a nonnegative integer')
    with transaction(db):
        header = require_report_header(db, report_id)
        if position >= header.total_rows:
            raise IndexError('source position is outside the report')
        row = load_frozen_row(db, report_id, position)
        source = row.source
        policy = header.policy
        scope = load_batch_scope(db, source.batch_id)
        original = load_raw_slot(db, source.batch_id, source.slot)
        if scope != (policy.station, policy.day) or original is None or original.key != row.key:
            raise IntegrityViolation('frozen report row has no matching original slot')
        if source.correction_id is None:
            if original.reading != row.reading:
                raise IntegrityViolation('frozen reading disagrees with original slot')
            return SourceEvidence(report_id, row, original, None, None)
        record = load_correction(db, source.correction_id)
        expected = CurrentFact(policy.station, policy.day, row.key, row.reading, source)
        if record is None or record.after != expected:
            raise IntegrityViolation('frozen report row has no matching correction')
        return SourceEvidence(report_id, row, original, record.correction_id, record.reason)
