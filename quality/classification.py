from dataclasses import dataclass
from enum import Enum


class ReadingClass(Enum):
    ANOMALY = 'anomaly'
    WITHIN_LIMIT = 'within_limit'
    MISSING = 'missing'


@dataclass(frozen=True)
class ClassifiedReading:
    category: ReadingClass
    excess: int | None = None


def classify_reading(reading, limit):
    if reading is None:
        return ClassifiedReading(ReadingClass.MISSING)
    if reading <= limit:
        return ClassifiedReading(ReadingClass.WITHIN_LIMIT)
    return ClassifiedReading(ReadingClass.ANOMALY, reading - limit)
