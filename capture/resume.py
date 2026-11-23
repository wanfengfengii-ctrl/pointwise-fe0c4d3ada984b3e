import sqlite3
from dataclasses import dataclass
from typing import Literal

from bench.session import BenchSession, CaptureRegistration, RunIndex
from bench.slots import capture_number
from capture.custody import CustodyStore, HeldCapture


@dataclass(frozen=True, slots=True)
class CaptureStatus:
    state: Literal['unknown', 'pending', 'held']
    registration: CaptureRegistration | None = None
    held: HeldCapture | None = None


def status(db: sqlite3.Connection, session_id: int, number: int) -> CaptureStatus:
    return status_by_number(db, number, session_id=capture_number(session_id))


def status_by_number(db: sqlite3.Connection, number: int, *, session_id: int | None = None) -> CaptureStatus:
    registration = RunIndex(db).get_capture(number)
    if registration is None or (session_id is not None and registration.lease.session_id != session_id):
        return CaptureStatus('unknown')
    held = CustodyStore(db).get(number)
    if held is None:
        return CaptureStatus('pending', registration)
    if (held.capture.lease, held.capture.shift, held.capture.procedure) != (
        registration.lease, registration.shift, registration.procedure
    ):
        raise ValueError('durable capture does not match its registration')
    return CaptureStatus('held', registration, held)


def confirm_return(session: BenchSession, number: int) -> HeldCapture:
    store = CustodyStore(session.db)
    held = store.get(number)
    if held is None or held.capture.lease.session_id != session.session_id:
        raise ValueError('no durable custody for an instance of this session')
    if held.returned:
        return held
    session.require_open()
    confirmation = session.slots.release(held.capture.lease)
    return store.mark_returned(confirmation)
