from dataclasses import dataclass

from facts.old_image import OldImage
from facts.provenance import FactSource
from storage import IntegrityViolation, decode_mm, encode_mm


@dataclass(frozen=True)
class LabelRequestRecord:
    request_id: str
    station: str
    day: str
    reason: str
    items: tuple[tuple[OldImage, str], ...]


def insert_label_request(db, request_id, station, day, reason, items):
    db.execute(
        '''INSERT INTO label_requests (request_id, station, day, reason, item_count)
           VALUES (?, ?, ?, ?, ?)''',
        (request_id, station, day, reason, len(items)),
    )
    db.executemany(
        '''INSERT INTO label_changes (request_id, position, old_key, old_reading, old_batch_id,
                                      old_slot, old_correction_id, old_label_id, new_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            (request_id, position, old.key, encode_mm(old.reading), old.source.batch_id,
             old.source.slot, old.source.correction_id, old.source.label_id, new_key)
            for position, (old, new_key) in enumerate(items)
        ),
    )


def load_label_request(db, request_id):
    header = db.execute(
        'SELECT station, day, reason, item_count FROM label_requests WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    if header is None:
        return None
    station, day, reason, item_count = header
    rows = db.execute(
        '''SELECT old_key, old_reading, old_batch_id, old_slot, old_correction_id, old_label_id, new_key
           FROM label_changes WHERE request_id = ? ORDER BY position''',
        (request_id,),
    ).fetchall()
    if len(rows) != item_count:
        raise IntegrityViolation(f'label request {request_id!r} has incomplete changes')
    items = []
    for old_key, reading, batch_id, slot, correction_id, label_id, new_key in rows:
        old = OldImage(
            station, day, old_key, decode_mm(reading),
            FactSource(batch_id, slot, correction_id, label_id),
        )
        items.append((old, new_key))
    return LabelRequestRecord(request_id, station, day, reason, tuple(items))


def label_scope(db, label_id):
    row = db.execute(
        'SELECT station, day FROM label_requests WHERE request_id = ?', (label_id,)
    ).fetchone()
    return None if row is None else tuple(row)


def label_reason(db, label_id):
    row = db.execute('SELECT reason FROM label_requests WHERE request_id = ?', (label_id,)).fetchone()
    return None if row is None else row[0]


def locate_label_change(db, station, day, label_id, new_key):
    """Return ``(old_key, old_reading, pre_image)`` for a frozen key, or ``None``."""
    row = db.execute(
        '''SELECT old_key, old_reading, old_batch_id, old_slot, old_correction_id, old_label_id
           FROM label_changes WHERE request_id = ? AND new_key = ?''',
        (label_id, new_key),
    ).fetchone()
    if row is None:
        return None
    old_key, reading, batch_id, slot, correction_id, previous_label = row
    return (
        old_key, decode_mm(reading),
        OldImage(station, day, old_key, decode_mm(reading),
                 FactSource(batch_id, slot, correction_id, previous_label)),
    )
