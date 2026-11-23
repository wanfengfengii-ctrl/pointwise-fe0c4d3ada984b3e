import sqlite3
from dataclasses import dataclass

from handover.ledger import HandoverLedger
from recipes.revisions import require_name
from review.reconcile import order_issued_captures, select_shift_peak


@dataclass(frozen=True, slots=True)
class ShiftSummary:
    shift: str
    capture_ids: tuple[int, ...]
    peak: int | None
    peak_unit: str | None
    peak_capture_id: int | None


def shift(db: sqlite3.Connection, name: str) -> ShiftSummary:
    require_name(name, 'shift')
    ledger = HandoverLedger(db)
    ordered = order_issued_captures(ledger.list_issued_for_shift(name))
    capture_ids = tuple(record.capture_id for record in ordered)
    winner = select_shift_peak(ordered)
    if winner is None:
        return ShiftSummary(name, capture_ids, None, None, None)
    return ShiftSummary(name, capture_ids, winner.result.peak, winner.result.unit.symbol, winner.capture_id)
