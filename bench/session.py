import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bench.recording import RecordingPlan, parse_recording
from bench.slots import SlotLease, SlotPool, capture_number
from bench.writer import DeviceWriter, RegionReady
from recipes.catalog import ProcedureCatalog
from recipes.revisions import ProcedureRevision

if TYPE_CHECKING:
    from capture.intake import PendingRegions


class BenchBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CaptureRegistration:
    lease: SlotLease
    shift: str
    procedure: ProcedureRevision


class RunIndex:
    def __init__(self, db: sqlite3.Connection, *, first_capture: int = 41) -> None:
        self.db = db
        capture_number(first_capture)
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('''CREATE TABLE IF NOT EXISTS bench_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift TEXT NOT NULL,
            declaration TEXT NOT NULL,
            is_open INTEGER NOT NULL CHECK (is_open IN (0, 1))
        )''')
        db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_live_bench_session
            ON bench_sessions(is_open) WHERE is_open = 1''')
        db.execute('''CREATE TABLE IF NOT EXISTS capture_index (
            capture_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES bench_sessions(session_id),
            slot TEXT NOT NULL CHECK (slot IN ('A', 'B')),
            instance INTEGER NOT NULL CHECK (instance > 0),
            UNIQUE (session_id, slot, instance)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS capture_number_sequence (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            last_number INTEGER NOT NULL
        )''')
        if db.execute('SELECT 1 FROM capture_number_sequence').fetchone() is None:
            db.execute('BEGIN IMMEDIATE')
            with db:
                db.execute('INSERT OR IGNORE INTO capture_number_sequence VALUES (1, ?)',
                           (first_capture - 1,))
                db.commit()

    def open_session(self, shift: str, procedure: ProcedureRevision) -> int:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            if self.db.execute('SELECT 1 FROM bench_sessions WHERE is_open = 1').fetchone():
                raise BenchBusy('the bench already has an open session')
            cursor = self.db.execute('''INSERT INTO bench_sessions (shift, declaration, is_open)
                VALUES (?, ?, 1)''', (shift, json.dumps(procedure.as_evidence(), ensure_ascii=False)))
            session_id = cursor.lastrowid
            self.db.commit()
        return session_id

    def close_session(self, session_id: int) -> None:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('UPDATE bench_sessions SET is_open = 0 WHERE session_id = ?', (session_id,))
            self.db.commit()

    def register_capture(self, session_id: int, slots: SlotPool) -> SlotLease:
        lease = None
        self.db.execute('BEGIN IMMEDIATE')
        try:
            with self.db:
                active = self.db.execute('''SELECT 1 FROM bench_sessions
                    WHERE session_id = ? AND is_open = 1''', (session_id,)).fetchone()
                if active is None:
                    raise ValueError('bench session is not open')
                last_number = self.db.execute(
                    'SELECT last_number FROM capture_number_sequence WHERE singleton = 1'
                ).fetchone()[0]
                lease = slots.reserve(session_id, last_number + 1)
                self.db.execute('INSERT INTO capture_index VALUES (?, ?, ?, ?)',
                                (lease.capture_id, lease.session_id, lease.slot, lease.instance))
                self.db.execute('UPDATE capture_number_sequence SET last_number = ? WHERE singleton = 1',
                                (lease.capture_id,))
                self.db.commit()
        except BaseException:
            if lease is not None:
                slots.cancel_reservation(lease)
            raise
        return lease

    def get_capture(self, number: int) -> CaptureRegistration | None:
        row = self.db.execute('''SELECT c.session_id, c.slot, c.instance, s.shift, s.declaration
            FROM capture_index AS c JOIN bench_sessions AS s USING (session_id)
            WHERE c.capture_id = ?''', (capture_number(number),)).fetchone()
        if row is None:
            return None
        session_id, slot, instance, shift, declaration = row
        return CaptureRegistration(SlotLease(session_id, number, slot, instance), shift,
                                   ProcedureRevision.from_evidence(json.loads(declaration)))


class BenchSession:
    def __init__(self, index: RunIndex, session_id: int, procedure: ProcedureRevision,
                 plan: RecordingPlan, pending: 'PendingRegions') -> None:
        self.index = index
        self.session_id = session_id
        self.procedure = procedure
        self.plan = plan
        self.pending = pending
        self.slots = SlotPool()
        self.writer = DeviceWriter(self.slots)
        self._closed = False
        self._position = 0
        self._labels: dict[str, SlotLease] = {}
        self._notifications: dict[tuple[str, str], RegionReady] = {}

    @property
    def db(self) -> sqlite3.Connection:
        return self.index.db

    def require_open(self) -> None:
        if self._closed:
            raise ValueError('bench session is closed')

    def begin_capture(self) -> SlotLease:
        self.require_open()
        lease = self.index.register_capture(self.session_id, self.slots)
        self.writer.activate(lease)
        return lease

    def record_step(self) -> SlotLease | RegionReady | None:
        self.require_open()
        if self._position == len(self.plan.steps):
            raise StopIteration
        step = self.plan.steps[self._position]
        result = None
        if step.operation == 'begin':
            result = self.begin_capture()
            self._labels[step.label] = result
        elif step.operation == 'deliver':
            result = self._notifications[step.label, step.region]
        else:
            lease = self._labels[step.label]
            write = self.writer.write_waveform if step.region == 'waveform' else self.writer.write_range
            self._notifications[step.label, step.region] = write(lease, step.value)
        self._position += 1
        return result

    def close(self) -> None:
        if not self._closed:
            self.index.close_session(self.session_id)
            self.writer.close()
            self.pending.clear()
            self._notifications.clear()
            self._labels.clear()
            self._closed = True

    def __enter__(self) -> 'BenchSession':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def start(db: sqlite3.Connection, procedure_key: tuple[str, str], recording: dict) -> BenchSession:
    from capture.intake import PendingRegions

    procedure = ProcedureCatalog(db).get(procedure_key)
    plan = parse_recording(recording)
    if plan.channel != procedure.channel:
        raise ValueError('recording channel does not match the procedure')
    index = RunIndex(db)
    session_id = index.open_session(plan.shift, procedure)
    return BenchSession(index, session_id, procedure, plan, PendingRegions())
