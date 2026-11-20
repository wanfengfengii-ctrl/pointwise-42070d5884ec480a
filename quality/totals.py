from collections import Counter
from dataclasses import dataclass

from quality.classification import ReadingClass


@dataclass(frozen=True)
class CoverageTotals:
    anomalies: int
    within_limit: int
    missing: int

    @property
    def total(self):
        return self.anomalies + self.within_limit + self.missing


def count_coverage(classified):
    counts = Counter(reading.category for reading in classified)
    return CoverageTotals(
        counts[ReadingClass.ANOMALY], counts[ReadingClass.WITHIN_LIMIT], counts[ReadingClass.MISSING]
    )
