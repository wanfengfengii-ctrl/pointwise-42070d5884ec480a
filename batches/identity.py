from dataclasses import dataclass

from batches.rows import RawSlot
from limits.validation import require_text
from storage import Conflict


@dataclass(frozen=True)
class BatchIdentity:
    station: str
    day: str
    batch_id: str
    slots: tuple[RawSlot, ...]


def identify_batch(station, day, batch_id, slots):
    require_text(batch_id, 'batch id')
    return BatchIdentity(station, day, batch_id, tuple(slots))


def require_same_batch(existing, submitted):
    if existing != submitted:
        raise Conflict(f'batch id {submitted.batch_id!r} is bound to another original')
