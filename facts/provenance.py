from dataclasses import dataclass


@dataclass(frozen=True)
class FactSource:
    batch_id: str
    slot: int
    correction_id: str | None = None
    label_id: str | None = None


def source_from_slot(batch_id, slot):
    return FactSource(batch_id, slot.position)


def source_after_replacement(source, correction_id):
    return FactSource(source.batch_id, source.slot, correction_id, source.label_id)


def source_after_relabel(source, label_id):
    return FactSource(source.batch_id, source.slot, source.correction_id, label_id)
