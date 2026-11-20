from dataclasses import dataclass

from facts.provenance import FactSource


@dataclass(frozen=True)
class AnomalyRow:
    key: str
    reading: int
    excess: int
    source: FactSource


def rank_anomalies(rows):
    return tuple(sorted(rows, key=lambda row: (-row.excess, row.key)))
