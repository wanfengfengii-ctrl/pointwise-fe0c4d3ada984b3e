import json
import sqlite3
from dataclasses import dataclass

from bench.slots import capture_number
from handover.receipts import FirstReceipt, create_first_receipt
from recipes.revisions import require_name
from reduction.result import ReducedResult


@dataclass(frozen=True, slots=True)
class IssuedRecord:
    result: ReducedResult
    receipt: FirstReceipt

    @property
    def capture_id(self) -> int:
        return self.result.evidence.lease.capture_id

    @property
    def source_shift(self) -> str:
        return self.result.evidence.shift


class HandoverLedger:
    _issued_rows = '''SELECT i.capture_id, i.source_shift, i.archive, r.receipt_id
        FROM issued_results AS i JOIN first_receipts AS r USING (capture_id)'''

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('''CREATE TABLE IF NOT EXISTS issued_results (
            capture_id INTEGER PRIMARY KEY,
            source_shift TEXT NOT NULL,
            archive TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS first_receipts (
            receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            capture_id INTEGER NOT NULL UNIQUE REFERENCES issued_results(capture_id)
        )''')
        db.execute('''CREATE INDEX IF NOT EXISTS issued_by_source_shift
            ON issued_results(source_shift, capture_id)''')

    def commit_issued(self, result: ReducedResult) -> IssuedRecord:
        number = capture_number(result.evidence.lease.capture_id)
        shift = require_name(result.evidence.shift, 'source shift')
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            issued = self.get_issued(number)
            if issued is None:
                archive = json.dumps(result.as_archive(), ensure_ascii=False)
                self.db.execute('INSERT INTO issued_results VALUES (?, ?, ?)', (number, shift, archive))
                cursor = self.db.execute('INSERT INTO first_receipts (capture_id) VALUES (?)', (number,))
                issued = IssuedRecord(result, create_first_receipt(cursor.lastrowid, number))
            self.db.commit()
        return issued

    def get_issued(self, number: int) -> IssuedRecord | None:
        row = self.db.execute(self._issued_rows + ' WHERE i.capture_id = ?',
                              (capture_number(number),)).fetchone()
        return None if row is None else self._decode(row)

    def list_issued_for_shift(self, shift: str) -> tuple[IssuedRecord, ...]:
        rows = self.db.execute(self._issued_rows + ' WHERE i.source_shift = ?',
                               (require_name(shift, 'shift'),))
        return tuple(map(self._decode, rows))

    @staticmethod
    def _decode(row: tuple) -> IssuedRecord:
        number, shift, archive, receipt_id = row
        result = ReducedResult.from_archive(json.loads(archive))
        if result.evidence.lease.capture_id != number or result.evidence.shift != shift:
            raise ValueError('archive identity conflicts with the handover ledger')
        return IssuedRecord(result, create_first_receipt(receipt_id, number))
