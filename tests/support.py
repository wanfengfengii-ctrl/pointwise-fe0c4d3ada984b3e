import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bench.session import start
from capture.intake import deliver
from handover.ledger import HandoverLedger
from recipes.catalog import ProcedureCatalog
from recipes.revisions import build_revision


SAMPLES = Path(__file__).resolve().parents[1] / 'samples'
DEFAULT_KEY = ('motor-vibration', '1')


def defaults() -> list[dict]:
    return json.loads((SAMPLES / 'procedures.json').read_text(encoding='utf-8'))


def recording(shift='day', channel='drive-end', steps=None) -> dict:
    return {'shift': shift, 'channel': channel, 'steps': [] if steps is None else steps}


def register_micro(db, revision='2'):
    return ProcedureCatalog(db).register(build_revision(
        name='motor-vibration', revision=revision, channel='drive-end',
        sampling_rate=2000, multipliers=(1, 10), unit='µm/s',
    ))


def row_count(db, relation):
    return db.execute(f'SELECT count(*) FROM {relation}').fetchone()[0]


def receipt_failure(db):
    HandoverLedger(db)
    db.execute('''CREATE TEMP TRIGGER stop_receipt BEFORE INSERT ON main.first_receipts
        BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END''')


def deferred_commit_failure(db):
    HandoverLedger(db)
    db.execute('CREATE TEMP TABLE permitted_receipts (id INTEGER PRIMARY KEY)')
    db.execute('''CREATE TEMP TABLE pending_receipt_checks (
        id INTEGER NOT NULL REFERENCES permitted_receipts(id) DEFERRABLE INITIALLY DEFERRED
    )''')
    db.execute('''CREATE TEMP TRIGGER stop_at_commit AFTER INSERT ON main.first_receipts
        BEGIN INSERT INTO pending_receipt_checks VALUES (NEW.receipt_id); END''')


class BenchCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='bench-tests-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'bench.sqlite'
        self.db = self.connect()
        ProcedureCatalog(self.db).seed_defaults(defaults())

    def connect(self):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        return db

    def session(self, shift='day', key=DEFAULT_KEY, *, source=None):
        session = start(self.db, key, recording(shift) if source is None else source)
        self.addCleanup(session.close)
        return session

    def complete(self, session, values=(2, 4), multiplier=10):
        lease = session.begin_capture()
        wave = session.writer.write_waveform(lease, values)
        scale = session.writer.write_range(lease, multiplier)
        self.assertEqual(deliver(session, wave).state, 'pending')
        state = deliver(session, scale)
        self.assertEqual(state.state, 'held')
        self.assertTrue(state.held.returned)
        return lease, wave, scale

    def reopen(self, session):
        session.close()
        self.db.close()
        self.db = self.connect()
