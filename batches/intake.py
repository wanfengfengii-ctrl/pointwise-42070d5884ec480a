from batches.identity import identify_batch, require_same_batch
from batches.receipts import make_intake_receipt
from batches.repository import insert_batch, insert_batch_receipt, load_batch, load_batch_receipt
from batches.rows import normalize_rows
from facts.admission import admit_facts
from limits.day_policy import require_day_policy
from storage import IntegrityViolation, transaction


def ingest(db, station, day, batch_id, rows):
    submitted = identify_batch(station, day, batch_id, normalize_rows(rows))
    with transaction(db, write=True):
        require_day_policy(db, station, day)
        existing = load_batch(db, batch_id)
        if existing is not None:
            require_same_batch(existing, submitted)
            receipt = load_batch_receipt(db, batch_id)
            if receipt is None:
                raise IntegrityViolation(f'missing intake receipt for {batch_id!r}')
            return receipt
        receipt = make_intake_receipt(submitted)
        insert_batch(db, submitted)
        insert_batch_receipt(db, receipt)
        admit_facts(db, station, day, batch_id, submitted.slots)
        return receipt
