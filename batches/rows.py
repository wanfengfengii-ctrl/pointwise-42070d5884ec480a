from collections.abc import Sequence
from dataclasses import dataclass

from limits.validation import require_text


@dataclass(frozen=True)
class RawSlot:
    position: int
    key: str
    reading: int | None


def validate_reading(reading):
    if reading is not None and type(reading) is not int:
        raise ValueError('reading must be integer millimetres or None')


def normalize_rows(rows):
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ValueError('batch rows must be an ordered sequence')
    slots = []
    for position, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f'slot {position} must contain a key and a reading')
        key, reading = row
        require_text(key, f'key at slot {position}')
        validate_reading(reading)
        slots.append(RawSlot(position, key, reading))
    return tuple(slots)
