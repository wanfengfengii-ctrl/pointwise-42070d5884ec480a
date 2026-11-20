from dataclasses import dataclass

from facts.repository import load_day_facts
from limits.day_policy import DayPolicy, require_day_policy
from quality.classification import ReadingClass, classify_reading
from quality.ranking import AnomalyRow, rank_anomalies
from quality.totals import CoverageTotals, count_coverage
from storage import transaction


@dataclass(frozen=True)
class QualityResult:
    policy: DayPolicy
    rows: tuple[AnomalyRow, ...]
    totals: CoverageTotals


def load_quality_inputs(db, station, day):
    policy = require_day_policy(db, station, day)
    return policy, load_day_facts(db, station, day)


def evaluate_snapshot(policy, facts):
    classified = []
    anomalies = []
    for fact in facts:
        result = classify_reading(fact.reading, policy.limit)
        classified.append(result)
        if result.category is ReadingClass.ANOMALY:
            anomalies.append(AnomalyRow(fact.key, fact.reading, result.excess, fact.source))
    return QualityResult(policy, rank_anomalies(anomalies), count_coverage(classified))


def preview(db, station, day):
    with transaction(db):
        policy, facts = load_quality_inputs(db, station, day)
        return evaluate_snapshot(policy, facts)
