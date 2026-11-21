from dataclasses import dataclass

from batches.repository import load_batch_scope, load_raw_slot
from batches.rows import RawSlot
from delivery.repository import load_frozen_row, require_report_header
from delivery.snapshot import FrozenReportRow
from facts.label_repository import label_reason, label_scope, locate_label_change
from facts.provenance import FactSource
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
    label_changes: tuple[tuple[str, str], ...] = ()


def _check_stages(db, station, day, stages, original, batch_id, slot):
    """Reconcile every label stage with the reading correction it adopts.

    A reading correction keeps the label identity in force when it was granted,
    so its first result must appear verbatim as one stage of the frozen row.
    """
    for key, reading, correction_id, _ in stages:
        if correction_id is None and reading != original.reading:
            raise IntegrityViolation('frozen reading disagrees with the original slot')
    for correction_id in {stage[2] for stage in stages if stage[2] is not None}:
        record = load_correction(db, correction_id)
        if record is None or record.after is None:
            raise IntegrityViolation('frozen row adopts an unknown reading correction')
        if any(stage[1] != record.after.reading for stage in stages if stage[2] == correction_id):
            raise IntegrityViolation('frozen reading disagrees with its adopted correction')
        matches = [
            stage for stage in stages
            if stage[2] == correction_id
            and record.after == CurrentFact(
                station, day, stage[0], stage[1],
                FactSource(batch_id, slot, correction_id, stage[3]),
            )
        ]
        if not matches:
            raise IntegrityViolation('reading correction is bound to another label identity')


def read_source(db, report_id, position):
    require_text(report_id, 'report id')
    if type(position) is not int or position < 0:
        raise ValueError('source position must be a nonnegative integer')
    with transaction(db):
        header = require_report_header(db, report_id)
        if position >= header.total_rows:
            raise IndexError('source position is outside the report')
        row = load_frozen_row(db, report_id, position)
        station, day = header.policy.station, header.policy.day
        source = row.source
        scope = load_batch_scope(db, source.batch_id)
        original = load_raw_slot(db, source.batch_id, source.slot)
        if scope != (station, day) or original is None:
            raise IntegrityViolation('frozen report row has no matching original slot')

        stages = [(row.key, row.reading, source.correction_id, source.label_id)]
        label_changes = []
        seen = set()
        key, reading, correction_id, label_id = stages[0]
        while label_id is not None:
            if label_id in seen:
                raise IntegrityViolation('label history contains a cycle')
            seen.add(label_id)
            located = locate_label_change(db, station, day, label_id, key)
            reason = label_reason(db, label_id)
            if located is None or reason is None or label_scope(db, label_id) != (station, day):
                raise IntegrityViolation('frozen row adopts a misbound label change')
            old_key, old_reading, previous = located
            if (previous.key, previous.reading) != (old_key, old_reading):
                raise IntegrityViolation('label change carries inconsistent old images')
            if (previous.source.batch_id, previous.source.slot) != (source.batch_id, original.position):
                raise IntegrityViolation('label change is bound to another original slot')
            label_changes.append((label_id, reason))
            key, reading = old_key, old_reading
            correction_id, label_id = previous.source.correction_id, previous.source.label_id
            stages.append((key, reading, correction_id, label_id))

        if key != original.key:
            raise IntegrityViolation('label history does not reach the original label')

        _check_stages(db, station, day, stages, original, source.batch_id, source.slot)

        if source.correction_id is None:
            correction_reason = None
        else:
            record = load_correction(db, source.correction_id)
            if record is None:
                raise IntegrityViolation('frozen report row has no matching correction')
            correction_reason = record.reason

        label_changes.reverse()
        return SourceEvidence(
            report_id, row, original, source.correction_id, correction_reason, tuple(label_changes)
        )
