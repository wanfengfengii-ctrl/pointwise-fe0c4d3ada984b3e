import sqlite3
from dataclasses import dataclass
from typing import Literal

from capture.resume import status_by_number
from reduction.inputs import ReductionInput, build_reduction_input


@dataclass(frozen=True, slots=True)
class PublishEligibility:
    state: Literal['unknown', 'pending', 'eligible']
    inputs: ReductionInput | None = None


def check_publish_eligibility(db: sqlite3.Connection, number: int) -> PublishEligibility:
    state = status_by_number(db, number)
    if state.held is None:
        return PublishEligibility('unknown' if state.state == 'unknown' else 'pending')
    return PublishEligibility('eligible', build_reduction_input(number, state.held))
