import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from handover.ledger import HandoverLedger, IssuedRecord
from review.provenance import verify_provenance


def get(db: sqlite3.Connection, number: int) -> IssuedRecord | None:
    issued = HandoverLedger(db).get_issued(number)
    if issued is not None:
        verify_provenance(issued, number)
    return issued


def _demo() -> None:
    from bench.session import start
    from bench.slots import SlotLease
    from bench.writer import RegionReady
    from capture.intake import deliver
    from capture.resume import status
    from handover.publish import publish
    from recipes.catalog import ProcedureCatalog
    from review.shift import shift

    samples = Path(__file__).resolve().parents[1] / 'samples'
    defaults = json.loads((samples / 'procedures.json').read_text(encoding='utf-8'))
    recording = json.loads((samples / 'day.json').read_text(encoding='utf-8'))
    leases = []
    with TemporaryDirectory(prefix='vibration-bench-') as directory:
        path = Path(directory) / 'bench.sqlite'
        with closing(sqlite3.connect(path)) as db:
            ProcedureCatalog(db).seed_defaults(defaults)
            HandoverLedger(db)
            with start(db, ('motor-vibration', '1'), recording) as session:
                db.execute('''CREATE TEMP TRIGGER pause_receipt BEFORE INSERT ON main.first_receipts
                    BEGIN SELECT RAISE(ABORT, 'demo receipt temporarily unavailable'); END''')
                for _ in session.plan.steps:
                    item = session.record_step()
                    if isinstance(item, SlotLease):
                        leases.append(item)
                    if not isinstance(item, RegionReady):
                        continue
                    state = deliver(session, item)
                    if item.source.lease.capture_id == 41 and state.held is not None:
                        assert state.held.returned
                        try:
                            publish(db, 41)
                        except sqlite3.IntegrityError as error:
                            if str(error) != 'demo receipt temporarily unavailable':
                                raise
                        else:
                            raise AssertionError('the first handover was expected to roll back')
                        assert get(db, 41) is None and shift(db, 'day').capture_ids == ()
                        db.execute('DROP TRIGGER pause_receipt')
            assert [(lease.capture_id, lease.slot) for lease in leases] == [(41, 'A'), (42, 'B'), (43, 'A')]
            assert leases[0].instance != leases[2].instance
        with closing(sqlite3.connect(path)) as db:
            old = status(db, leases[0].session_id, 41).held
            assert old is not None and old.returned and old.capture.waveform == (2, 4)
            assert publish(db, 42).state == 'pending'
            assert publish(db, 41).state == 'issued' and publish(db, 43).state == 'issued'
            expected = [[20, 40], [300, 500]]
            actual = []
            for lease, waveform, multiplier in ((leases[0], (2, 4), 10), (leases[2], (3, 5), 100)):
                archive = get(db, lease.capture_id)
                assert archive is not None
                evidence = verify_provenance(archive, lease.capture_id)
                assert (evidence.waveform, evidence.multiplier) == (waveform, multiplier)
                assert evidence.waveform_source.lease == evidence.range_source.lease == lease
                assert publish(db, lease.capture_id).issued == archive
                actual.append(list(archive.result.scaled))
            summary = shift(db, 'day')
            downstream = {'capture_ids': list(summary.capture_ids), 'peak': summary.peak, 'unit': summary.peak_unit}
            downstream_expected = {'capture_ids': [41, 43], 'peak': 500, 'unit': 'mm/s'}
            assert actual == expected and downstream == downstream_expected
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': 'v6.reuse_then_reopen',
                'input': {'recording': recording, 'first_handover': 'receipt transaction abort'},
                'expected': expected,
                'actual': actual,
                'downstream_expected': downstream_expected,
                'downstream_actual': downstream,
                'code_paths': ['bench/session.py', 'capture/intake.py', 'capture/custody.py',
                               'handover/publish.py', 'handover/ledger.py', 'review/readout.py', 'review/shift.py'],
                'oracle': 'Manual products 2*10, 4*10, 3*100, 5*100. Only 41 and 43 signed. 500 is the largest signed absolute reading.',
            }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description='Replay the vibration bench handover example')
    parser.add_argument('command', choices=['demo'])
    parser.parse_args()
    _demo()


if __name__ == '__main__':
    main()
