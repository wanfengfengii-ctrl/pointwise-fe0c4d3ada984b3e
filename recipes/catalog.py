import json
import sqlite3
from collections.abc import Iterable

from recipes.revisions import ProcedureRevision, build_revision, require_name


class ProcedureCatalog:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute('''CREATE TABLE IF NOT EXISTS procedure_revisions (
            name TEXT NOT NULL,
            revision TEXT NOT NULL,
            declaration TEXT NOT NULL,
            PRIMARY KEY (name, revision)
        )''')

    def get(self, key: tuple[str, str]) -> ProcedureRevision:
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError('procedure key must be a (name, revision) pair')
        name, revision = key
        require_name(name, 'procedure')
        require_name(revision, 'revision')
        row = self.db.execute(
            'SELECT declaration FROM procedure_revisions WHERE name = ? AND revision = ?', key
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return ProcedureRevision.from_evidence(json.loads(row[0]))

    def register(self, revision: ProcedureRevision) -> ProcedureRevision:
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('''INSERT INTO procedure_revisions VALUES (?, ?, ?)
                ON CONFLICT (name, revision) DO NOTHING''', self._row(revision))
            saved = self.get(revision.key)
            if saved != revision:
                raise ValueError('procedure revision already exists with a different declaration')
            self.db.commit()
        return saved

    def seed_defaults(self, declarations: Iterable[dict]) -> int:
        revisions = [build_revision(**fields) for fields in declarations]
        if len({revision.key for revision in revisions}) != len(revisions):
            raise ValueError('duplicate default procedure key')
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            changed = self.db.executemany('''INSERT INTO procedure_revisions VALUES (?, ?, ?)
                ON CONFLICT (name, revision) DO NOTHING''', map(self._row, revisions)).rowcount
            self.db.commit()
        return changed

    @staticmethod
    def _row(revision: ProcedureRevision) -> tuple[str, str, str]:
        return (*revision.key, json.dumps(revision.as_evidence(), ensure_ascii=False))
