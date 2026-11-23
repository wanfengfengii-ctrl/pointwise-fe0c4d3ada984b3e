import json
import sqlite3
from dataclasses import dataclass

from bench.session import RunIndex
from bench.slots import SlotLease, capture_number
from capture.completion import CompletedCapture, require_waveform, verify_capture_sources
from recipes.limits import require_allowed_multiplier


@dataclass(frozen=True, slots=True)
class HeldCapture:
    capture: CompletedCapture
    returned: bool


class CustodyStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('''CREATE TABLE IF NOT EXISTS held_captures (
            capture_id INTEGER PRIMARY KEY REFERENCES capture_index(capture_id),
            evidence TEXT NOT NULL,
            returned INTEGER NOT NULL DEFAULT 0 CHECK (returned IN (0, 1))
        )''')

    def get(self, number: int) -> HeldCapture | None:
        row = self.db.execute('SELECT evidence, returned FROM held_captures WHERE capture_id = ?',
                              (capture_number(number),)).fetchone()
        if row is None:
            return None
        return HeldCapture(CompletedCapture.from_evidence(json.loads(row[0])), bool(row[1]))

    def commit(self, complete: CompletedCapture) -> HeldCapture:
        index = RunIndex(self.db)
        number = complete.lease.capture_id
        verify_capture_sources(complete.lease, complete.waveform, complete.waveform_source,
                               complete.range_source, complete.trigger_segments)
        require_waveform(complete.waveform)
        require_allowed_multiplier(complete.procedure.limits, complete.multiplier)
        document = json.dumps(complete.as_evidence(), ensure_ascii=False)
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            registration = index.get_capture(number)
            if registration is None or (
                registration.lease, registration.shift, registration.procedure
            ) != (complete.lease, complete.shift, complete.procedure):
                raise ValueError('complete evidence does not match the capture registration')
            existing = self.get(number)
            if existing is None:
                self.db.execute('INSERT INTO held_captures (capture_id, evidence) VALUES (?, ?)',
                                (number, document))
                existing = HeldCapture(complete, False)
            elif existing.capture != complete:
                raise ValueError('conflicting durable capture evidence')
            self.db.commit()
        return existing

    def mark_returned(self, confirmation: SlotLease) -> HeldCapture:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            held = self.get(confirmation.capture_id)
            if held is None or held.capture.lease != confirmation:
                raise ValueError('slot confirmation does not match durable custody')
            if not held.returned:
                self.db.execute('UPDATE held_captures SET returned = 1 WHERE capture_id = ?',
                                (confirmation.capture_id,))
            self.db.commit()
        return HeldCapture(held.capture, True)
