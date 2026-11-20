from quality.totals import CoverageTotals
from storage import IntegrityViolation


def verify_issue_result(policy, facts, candidate):
    if candidate.policy != policy:
        raise IntegrityViolation('candidate uses another station-day policy')
    by_key = {fact.key: fact for fact in facts}
    if len(by_key) != len(facts) or any(
        (fact.station, fact.day) != (policy.station, policy.day) for fact in facts
    ):
        raise IntegrityViolation('fact snapshot must contain unique keys from one station-day')

    expected_keys = {
        fact.key for fact in facts if fact.reading is not None and fact.reading > policy.limit
    }
    missing = sum(fact.reading is None for fact in facts)
    expected_totals = CoverageTotals(len(expected_keys), len(facts) - len(expected_keys) - missing, missing)
    if candidate.totals != expected_totals:
        raise IntegrityViolation('candidate coverage disagrees with the current fact snapshot')

    unseen = set(expected_keys)
    prior_rank = None
    for row in candidate.rows:
        if row.key not in unseen:
            raise IntegrityViolation(f'unexpected or duplicate anomaly {row.key!r}')
        fact = by_key[row.key]
        if (
            type(row.reading) is not int or row.reading != fact.reading
            or type(row.excess) is not int or row.excess != fact.reading - policy.limit
            or row.source != fact.source
        ):
            raise IntegrityViolation(f'anomaly disagrees with fact {row.key!r}')
        rank = (-row.excess, row.key)
        if prior_rank is not None and rank <= prior_rank:
            raise IntegrityViolation('candidate anomaly order is incorrect')
        prior_rank = rank
        unseen.remove(row.key)
    if unseen:
        raise IntegrityViolation('candidate omitted anomalies')
