import sqlite3
from dataclasses import dataclass
from typing import Literal

from handover.eligibility import check_publish_eligibility
from handover.ledger import HandoverLedger, IssuedRecord
from reduction.result import reduce_capture


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    state: Literal['unknown', 'pending', 'issued']
    issued: IssuedRecord | None = None


def publish(db: sqlite3.Connection, number: int) -> PublishOutcome:
    ledger = HandoverLedger(db)
    issued = ledger.get_issued(number)
    if issued is not None:
        return PublishOutcome('issued', issued)
    eligibility = check_publish_eligibility(db, number)
    if eligibility.inputs is None:
        return PublishOutcome('unknown' if eligibility.state == 'unknown' else 'pending')
    return PublishOutcome('issued', ledger.commit_issued(reduce_capture(eligibility.inputs)))
