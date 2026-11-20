from dataclasses import dataclass

from facts.provenance import FactSource
from limits.day_policy import DayPolicy
from quality.evaluate import QualityResult
from quality.totals import CoverageTotals


@dataclass(frozen=True)
class FrozenReportRow:
    position: int
    key: str
    reading: int
    excess: int
    source: FactSource


@dataclass(frozen=True)
class ReportHeader:
    report_id: str
    policy: DayPolicy
    totals: CoverageTotals

    @property
    def total_rows(self):
        return self.totals.anomalies


@dataclass(frozen=True)
class FrozenReport:
    header: ReportHeader
    rows: tuple[FrozenReportRow, ...]


@dataclass(frozen=True)
class IssueReceipt:
    report_id: str
    station: str
    day: str
    total_rows: int


def freeze_report(report_id, result: QualityResult):
    header = ReportHeader(report_id, result.policy, result.totals)
    rows = tuple(
        FrozenReportRow(position, row.key, row.reading, row.excess, row.source)
        for position, row in enumerate(result.rows)
    )
    return FrozenReport(header, rows)
