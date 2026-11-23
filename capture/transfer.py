"""Pre-trigger slot transfer for a capture that already holds a prefix."""

import json
import sqlite3
from dataclasses import dataclass

from bench.slots import SlotLease, SlotsFull, capture_number
from bench.writer import RegionReady, RegionSource
from capture.completion import CompletedCapture, require_waveform, verify_trigger_segments
from recipes.limits import require_allowed_multiplier


@dataclass(frozen=True, slots=True)
class TransferState:
    number: int
    session_id: int
    prefix: tuple[int, ...]
    multiplier: int
    prefix_lease: SlotLease
    trigger_lease: SlotLease | None
    triggered: bool


class TransferStore:
    """Durable custody of the prefix plus the carrier that may trigger it."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('''CREATE TABLE IF NOT EXISTS trigger_transfers (
            capture_id INTEGER PRIMARY KEY REFERENCES capture_index(capture_id),
            session_id INTEGER NOT NULL,
            prefix TEXT NOT NULL,
            multiplier INTEGER NOT NULL,
            prefix_slot TEXT NOT NULL,
            prefix_instance INTEGER NOT NULL,
            trigger_slot TEXT,
            trigger_instance INTEGER,
            triggered INTEGER NOT NULL DEFAULT 0 CHECK (triggered IN (0, 1))
        )''')

    def get(self, number: int) -> TransferState | None:
        row = self.db.execute('''SELECT session_id, prefix, multiplier, prefix_slot,
                prefix_instance, trigger_slot, trigger_instance, triggered
            FROM trigger_transfers WHERE capture_id = ?''', (capture_number(number),)).fetchone()
        if row is None:
            return None
        (session_id, prefix, multiplier, prefix_slot, prefix_instance,
         trigger_slot, trigger_instance, triggered) = row
        prefix_lease = SlotLease(session_id, number, prefix_slot, prefix_instance)
        trigger_lease = None
        if trigger_slot is not None:
            trigger_lease = SlotLease(session_id, number, trigger_slot, trigger_instance)
        return TransferState(number, session_id, tuple(json.loads(prefix)), multiplier,
                             prefix_lease, trigger_lease, bool(triggered))

    def create(self, number: int, session_id: int, prefix: tuple[int, ...],
               multiplier: int, prefix_lease: SlotLease) -> TransferState:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('''INSERT INTO trigger_transfers
                (capture_id, session_id, prefix, multiplier, prefix_slot, prefix_instance)
                VALUES (?, ?, ?, ?, ?, ?)''',
                (number, session_id, json.dumps(list(prefix)), multiplier,
                 prefix_lease.slot, prefix_lease.instance))
            self.db.commit()
        state = self.get(number)
        if state is None:
            raise ValueError('prefix custody was not recorded')
        return state

    def mark_moved(self, number: int, target: SlotLease, before_commit) -> None:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            row = self.db.execute('''SELECT trigger_slot, triggered FROM trigger_transfers
                WHERE capture_id = ?''', (capture_number(number),)).fetchone()
            if row is None:
                raise ValueError('capture is not prepared for a transfer')
            if row[0] is not None or row[1]:
                raise ValueError('capture was already moved or triggered')
            self.db.execute('''UPDATE trigger_transfers
                SET trigger_slot = ?, trigger_instance = ? WHERE capture_id = ?''',
                (target.slot, target.instance, number))
            updated = self.db.execute('''UPDATE capture_index SET slot = ?, instance = ?
                WHERE capture_id = ? AND session_id = ?''',
                (target.slot, target.instance, number, target.session_id)).rowcount
            if updated != 1:
                raise ValueError('capture registration no longer matches the transfer')
            if before_commit is not None:
                before_commit()
            self.db.commit()

    def mark_triggered(self, number: int) -> None:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('UPDATE trigger_transfers SET triggered = 1 WHERE capture_id = ?',
                            (capture_number(number),))
            self.db.commit()

    def delete(self, number: int) -> None:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('DELETE FROM trigger_transfers WHERE capture_id = ?',
                            (capture_number(number),))
            self.db.commit()


def _segment(values: object, field: str) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f'{field} must contain one or more integer samples')
    if any(type(value) is not int for value in values):
        raise ValueError(f'{field} must contain integer samples only')
    return tuple(values)


def prepare(session, prefix, multiplier):
    """Allocate an ordinary capture number on a fresh slot and write the prefix."""
    session.require_open()
    values = _segment(prefix, 'prefix')
    require_allowed_multiplier(session.procedure.limits, multiplier)
    lease = session.begin_capture()
    try:
        session.writer.write_waveform(lease, values)
        session.writer.write_range(lease, multiplier)
        TransferStore(session.db).create(lease.capture_id, session.session_id, values,
                                         multiplier, lease)
    except BaseException:
        session.slots.release(lease)
        raise
    session.writer.guard(lease)
    session.prepared.add(lease.capture_id)
    return lease.capture_id, lease


def move(session, number, expected, target, *, before_commit=None):
    """Hand the prefix custody over and move the trigger right to another slot."""
    session.require_open()
    number = capture_number(number)
    if not isinstance(expected, SlotLease):
        raise ValueError('expected carrier must be a slot lease')
    if not isinstance(target, str) or target not in session.slots.names:
        raise ValueError('target must name the other device slot')
    store = TransferStore(session.db)
    state = store.get(number)
    if state is None:
        raise ValueError('capture is not prepared for a transfer')
    if state.session_id != session.session_id:
        raise ValueError('transfer belongs to a different recording session')
    if state.trigger_lease is not None or state.triggered:
        raise ValueError('capture was already moved or triggered')
    if expected != state.prefix_lease:
        raise ValueError('expected carrier does not match the prepared prefix')
    session.slots.require_active(expected)
    registration = session.index.get_capture(number)
    if registration is None or registration.lease != expected:
        raise ValueError('capture registration does not match the expected carrier')
    if target == expected.slot:
        raise ValueError('target must be the other device slot')
    if target in {lease.slot for lease in session.slots.occupied}:
        raise SlotsFull(f'device slot {target} is occupied')
    new_lease = SlotLease(expected.session_id, number, target,
                          session.slots.next_instance(target))
    store.mark_moved(number, new_lease, before_commit)
    session.slots.activate_moved(expected, new_lease)
    session.writer.rebind(expected, new_lease)
    return new_lease


def trigger(session, number, carrier, suffix):
    """Write the suffix once on the carrier that currently owns the trigger right."""
    session.require_open()
    number = capture_number(number)
    if not isinstance(carrier, SlotLease):
        raise ValueError('carrier must be a slot lease')
    values = _segment(suffix, 'suffix')
    store = TransferStore(session.db)
    state = store.get(number)
    if state is None or state.session_id != session.session_id:
        raise ValueError('capture is not prepared for a trigger')
    current = state.trigger_lease if state.trigger_lease is not None else state.prefix_lease
    if carrier != current:
        raise ValueError('carrier is stale or does not own the trigger right')
    if state.triggered:
        raise ValueError('capture was already triggered')
    registration = session.index.get_capture(number)
    if registration is None or registration.lease != carrier:
        raise ValueError('carrier does not match the capture registration')
    event = session.writer.write_trigger(carrier, values)
    store.mark_triggered(number)
    return event


def complete_transfer(session, registration, state: TransferState, event: RegionReady):
    """Assemble a triggered capture from the secured prefix and the actual suffix write."""
    actual = session.writer.verify_trigger(event)
    lease = state.trigger_lease if state.trigger_lease is not None else state.prefix_lease
    if lease != actual.source.lease:
        raise ValueError('trigger feedback does not belong to the current carrier')
    segments = ((state.prefix, state.prefix_lease), (actual.value, actual.source.lease))
    complete = CompletedCapture(
        lease=lease,
        shift=registration.shift,
        procedure=registration.procedure,
        waveform=state.prefix + actual.value,
        multiplier=state.multiplier,
        waveform_source=RegionSource(state.prefix_lease, 'waveform'),
        range_source=RegionSource(state.prefix_lease, 'range'),
        trigger_segments=segments,
    )
    verify_trigger_segments(lease, complete.waveform, complete.waveform_source,
                            complete.range_source, complete.trigger_segments)
    require_waveform(complete.waveform)
    require_allowed_multiplier(complete.procedure.limits, complete.multiplier)
    return complete
