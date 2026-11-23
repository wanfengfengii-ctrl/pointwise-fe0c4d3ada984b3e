from collections.abc import Iterable

from handover.ledger import IssuedRecord


def order_issued_captures(records: Iterable[IssuedRecord]) -> tuple[IssuedRecord, ...]:
    return tuple(sorted(records, key=lambda record: record.capture_id))


def select_shift_peak(ordered: tuple[IssuedRecord, ...]) -> IssuedRecord | None:
    # max preserves the first candidate when two physical magnitudes are equal.
    return max(ordered, key=lambda record: record.result.unit.comparison_value(record.result.peak),
               default=None)
