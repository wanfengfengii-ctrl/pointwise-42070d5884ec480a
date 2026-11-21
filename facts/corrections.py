from dataclasses import dataclass

from batches.rows import validate_reading
from facts.labels import LabelReceipt, relabel_facts
from facts.old_image import OldImage, require_matching_old_image, validate_old_image
from facts.repository import (
    CorrectionRecord, CurrentFact, insert_correction, load_correction, load_current_fact,
    remove_current_fact, replace_current_fact,
)
from limits.validation import require_text
from storage import Conflict, transaction


@dataclass(frozen=True)
class CorrectionRequest:
    correction_id: str
    old: OldImage
    action: str
    reason: str
    reading: int | None = None


@dataclass(frozen=True)
class CorrectionReceipt:
    correction_id: str
    old: OldImage
    action: str
    reason: str
    after: CurrentFact | None


def _validate_request(request):
    if not isinstance(request, CorrectionRequest):
        raise ValueError('a correction request is required')
    require_text(request.correction_id, 'correction id')
    require_text(request.reason, 'correction reason')
    validate_old_image(request.old)
    if request.action not in ('replace', 'withdraw'):
        raise ValueError('action must be replace or withdraw')
    validate_reading(request.reading)
    if request.action == 'withdraw' and request.reading is not None:
        raise ValueError('withdrawal does not take a replacement reading')


def _receipt(record):
    return CorrectionReceipt(
        record.correction_id, OldImage.from_fact(record.before), record.action, record.reason, record.after
    )


def correct_fact(db, request):
    _validate_request(request)
    old = request.old
    with transaction(db, write=True):
        existing = load_correction(db, request.correction_id)
        if existing is not None:
            original = CorrectionRequest(
                existing.correction_id, OldImage.from_fact(existing.before), existing.action,
                existing.reason, existing.replacement,
            )
            if original != request:
                raise Conflict('correction id is already bound to another request')
            return _receipt(existing)

        current = load_current_fact(db, old.station, old.day, old.key)
        require_matching_old_image(current, old)
        record = CorrectionRecord(request.correction_id, current, request.action, request.reason, request.reading)
        receipt = _receipt(record)
        if receipt.after is None:
            remove_current_fact(db, current.station, current.day, current.key)
        else:
            replace_current_fact(db, receipt.after)
        insert_correction(db, record)
        return receipt
