from dataclasses import dataclass

from facts.provenance import FactSource, source_after_replacement
from storage import IntegrityViolation, decode_mm, encode_mm


@dataclass(frozen=True)
class CurrentFact:
    station: str
    day: str
    key: str
    reading: int | None
    source: FactSource


@dataclass(frozen=True)
class CorrectionRecord:
    correction_id: str
    before: CurrentFact
    action: str
    reason: str
    replacement: int | None

    @property
    def after(self):
        if self.action == 'withdraw':
            return None
        before = self.before
        source = source_after_replacement(before.source, self.correction_id)
        return CurrentFact(before.station, before.day, before.key, self.replacement, source)


def _current_fact(row):
    station, day, key, reading, batch_id, slot, correction_id = row
    return CurrentFact(station, day, key, decode_mm(reading), FactSource(batch_id, slot, correction_id))


def load_current_fact(db, station, day, key):
    row = db.execute(
        '''SELECT station, day, observation_key, reading, batch_id, slot, correction_id
           FROM current_facts WHERE station = ? AND day = ? AND observation_key = ?''',
        (station, day, key),
    ).fetchone()
    return None if row is None else _current_fact(row)


def load_day_facts(db, station, day):
    rows = db.execute(
        '''SELECT station, day, observation_key, reading, batch_id, slot, correction_id
           FROM current_facts WHERE station = ? AND day = ? ORDER BY rowid''',
        (station, day),
    )
    return tuple(_current_fact(row) for row in rows)


def insert_current_facts(db, facts):
    db.executemany(
        '''INSERT INTO current_facts (station, day, observation_key, reading, batch_id, slot, correction_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (
            (fact.station, fact.day, fact.key, encode_mm(fact.reading),
             fact.source.batch_id, fact.source.slot, fact.source.correction_id)
            for fact in facts
        ),
    )


def replace_current_fact(db, fact):
    cursor = db.execute(
        '''UPDATE current_facts SET reading = ?, batch_id = ?, slot = ?, correction_id = ?
           WHERE station = ? AND day = ? AND observation_key = ?''',
        (encode_mm(fact.reading), fact.source.batch_id, fact.source.slot,
         fact.source.correction_id, fact.station, fact.day, fact.key),
    )
    if cursor.rowcount != 1:
        raise IntegrityViolation('replacement lost its current fact')


def remove_current_fact(db, station, day, key):
    cursor = db.execute(
        'DELETE FROM current_facts WHERE station = ? AND day = ? AND observation_key = ?',
        (station, day, key),
    )
    if cursor.rowcount != 1:
        raise IntegrityViolation('withdrawal lost its current fact')


def load_correction(db, correction_id):
    row = db.execute(
        '''SELECT station, day, observation_key, old_reading, batch_id, slot,
                  old_correction_id, action, reason, replacement
           FROM corrections WHERE correction_id = ?''',
        (correction_id,),
    ).fetchone()
    if row is None:
        return None
    station, day, key, reading, batch_id, slot, previous_id, action, reason, replacement = row
    source = FactSource(batch_id, slot, previous_id)
    before = CurrentFact(station, day, key, decode_mm(reading), source)
    return CorrectionRecord(correction_id, before, action, reason, decode_mm(replacement))


def insert_correction(db, record):
    before = record.before
    source = before.source
    # The immutable request row also encodes the first result. Its new source
    # uses the same original slot and binds this correction id.
    db.execute(
        '''INSERT INTO corrections (correction_id, station, day, observation_key,
                  old_reading, batch_id, slot, old_correction_id, action, reason, replacement)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (record.correction_id, before.station, before.day, before.key, encode_mm(before.reading),
         source.batch_id, source.slot, source.correction_id, record.action, record.reason,
         encode_mm(record.replacement)),
    )
