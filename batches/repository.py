from batches.identity import BatchIdentity
from batches.receipts import IntakeReceipt
from batches.rows import RawSlot
from storage import IntegrityViolation, decode_mm, encode_mm


def load_batch_scope(db, batch_id):
    return db.execute(
        'SELECT station, day FROM batches WHERE batch_id = ?', (batch_id,)
    ).fetchone()


def _slot(row):
    position, key, reading = row
    return RawSlot(position, key, decode_mm(reading))


def load_batch(db, batch_id):
    header = db.execute(
        'SELECT station, day, slot_count FROM batches WHERE batch_id = ?', (batch_id,)
    ).fetchone()
    if header is None:
        return None
    station, day, slot_count = header
    rows = db.execute(
        'SELECT position, observation_key, reading FROM raw_slots '
        'WHERE batch_id = ? ORDER BY position',
        (batch_id,),
    )
    slots = tuple(_slot(row) for row in rows)
    if len(slots) != slot_count or any(slot.position != index for index, slot in enumerate(slots)):
        raise IntegrityViolation(f'original slots incomplete for batch {batch_id!r}')
    return BatchIdentity(station, day, batch_id, slots)


def insert_batch(db, batch):
    db.execute(
        'INSERT INTO batches (batch_id, station, day, slot_count) VALUES (?, ?, ?, ?)',
        (batch.batch_id, batch.station, batch.day, len(batch.slots)),
    )
    db.executemany(
        'INSERT INTO raw_slots (batch_id, position, observation_key, reading) VALUES (?, ?, ?, ?)',
        ((batch.batch_id, slot.position, slot.key, encode_mm(slot.reading)) for slot in batch.slots),
    )


def load_raw_slot(db, batch_id, position):
    row = db.execute(
        'SELECT position, observation_key, reading FROM raw_slots WHERE batch_id = ? AND position = ?',
        (batch_id, position),
    ).fetchone()
    return None if row is None else _slot(row)


def load_batch_receipt(db, batch_id):
    row = db.execute(
        '''SELECT b.station, b.day, b.slot_count, r.accepted_count
           FROM batch_receipts AS r JOIN batches AS b ON b.batch_id = r.batch_id
           WHERE r.batch_id = ?''',
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    station, day, slot_count, accepted_count = row
    if accepted_count != slot_count:
        raise IntegrityViolation(f'intake receipt disagrees with batch {batch_id!r}')
    return IntakeReceipt(station, day, batch_id, accepted_count)


def insert_batch_receipt(db, receipt):
    db.execute(
        'INSERT INTO batch_receipts (batch_id, accepted_count) VALUES (?, ?)',
        (receipt.batch_id, receipt.accepted_count),
    )
