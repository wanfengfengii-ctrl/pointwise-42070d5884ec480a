from dataclasses import dataclass

from batches.identity import BatchIdentity


@dataclass(frozen=True)
class IntakeReceipt:
    station: str
    day: str
    batch_id: str
    accepted_count: int

    @property
    def slot_references(self):
        return tuple((self.batch_id, position) for position in range(self.accepted_count))


def make_intake_receipt(batch: BatchIdentity):
    return IntakeReceipt(batch.station, batch.day, batch.batch_id, len(batch.slots))
