from facts.provenance import source_from_slot
from facts.repository import CurrentFact, insert_current_facts, load_current_fact
from storage import Conflict


def admit_facts(db, station, day, batch_id, slots):
    """Admit validated, persisted original slots in the caller's write transaction."""
    if not db.in_transaction:
        raise RuntimeError('fact admission requires a caller transaction')
    seen = set()
    facts = []
    for slot in slots:
        if slot.key in seen:
            raise Conflict(f'duplicate observation {slot.key!r} in original batch')
        if load_current_fact(db, station, day, slot.key) is not None:
            raise Conflict(f'current observation {slot.key!r} already exists for the station-day')
        seen.add(slot.key)
        facts.append(CurrentFact(station, day, slot.key, slot.reading, source_from_slot(batch_id, slot)))
    insert_current_facts(db, facts)
