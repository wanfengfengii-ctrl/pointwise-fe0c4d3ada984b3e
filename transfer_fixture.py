import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from bench.session import start
from bench.slots import SlotsFull
from bench.writer import RegionReady, RegionSource
from capture.intake import deliver
from handover.publish import publish
from recipes.catalog import ProcedureCatalog
from review.readout import get
from review.shift import shift
from tests.support import deferred_commit_failure


RECORDING = {"shift": "day", "channel": "drive-end", "steps": []}
PREFIX = (2, 4)
SUFFIX = (-9,)
MULTIPLIER = 10


@contextmanager
def recording_at(path):
    declarations = Path(__file__).resolve().with_name("samples") / "procedures.json"
    with closing(sqlite3.connect(path)) as db:
        ProcedureCatalog(db).seed_defaults(json.loads(declarations.read_text(encoding="utf-8")))
        with start(db, ("motor-vibration", "1"), RECORDING) as session:
            yield db, session


def rejects(error_type, call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except error_type:
        return
    raise AssertionError(f"{call.__name__} should raise {error_type.__name__}")


def complete_ordinary(session, lease, wave):
    assert deliver(session, wave).state == "pending"
    scale = session.writer.write_range(lease, MULTIPLIER)
    assert deliver(session, scale).state == "held"


def baseline():
    with recording_at(":memory:") as (db, session):
        first = session.begin_capture()
        front = session.writer.write_waveform(first, PREFIX)
        second = session.begin_capture()
        back = session.writer.write_waveform(second, SUFFIX)
        assert (first.capture_id, first.slot, second.capture_id, second.slot) == (41, "A", 42, "B")
        assert publish(db, 41).state == "pending"
        complete_ordinary(session, first, front)
        complete_ordinary(session, second, back)
        actual = [publish(db, number).issued.result.scaled for number in (41, 42)]
        assert actual == [(20, 40), (-90,)]
        print(json.dumps({"ordinary_capture_ids": [41, 42], "scaled": actual}))


def assert_trigger_record(record, first, last):
    assert record is not None
    assert record.capture_id == record.receipt.capture_id == first.capture_id
    result = record.result
    assert (result.scaled, result.peak, result.unit.symbol) == ((20, 40, -90), 90, "mm/s")
    assert (result.evidence.waveform, result.evidence.multiplier) == (PREFIX + SUFFIX, MULTIPLIER)
    assert result.evidence.trigger_segments == ((PREFIX, first), (SUFFIX, last))


def moved_case(path):
    from bench.session import move, prepare, trigger

    with recording_at(path) as (db, session):
        number, first = prepare(session, PREFIX, MULTIPLIER)
        assert (number, first.capture_id, first.slot) == (41, 41, "A")
        rejects(ValueError, session.writer.write_waveform, first, PREFIX)
        rejects(ValueError, session.writer.write_range, first, MULTIPLIER)
        last = move(session, number, first, "B")
        assert (last.session_id, last.capture_id, last.slot) == (first.session_id, number, "B")
        assert session.slots.occupied == (last,)
        successor = session.begin_capture()
        assert (successor.capture_id, successor.slot) == (42, "A")
        assert successor.instance != first.instance
        wave = session.writer.write_waveform(successor, PREFIX)
        rejects(ValueError, trigger, session, number, first, SUFFIX)
        relabelled = replace(wave, source=RegionSource(replace(successor, capture_id=number), "waveform"))
        rejects(ValueError, deliver, session, relabelled)
        rejects(ValueError, deliver, session, RegionReady(RegionSource(last, "waveform"), SUFFIX))
        event = trigger(session, number, last, SUFFIX)
        assert event.source == RegionSource(last, "waveform") and event.value == SUFFIX
        assert publish(db, number).state == "pending" and get(db, number) is None
        assert deliver(session, event).state == "held"
        assert deliver(session, event).state == "held"
        rejects(ValueError, deliver, session, replace(event, value=(9,)))
        assert session.slots.occupied == (successor,)
        deferred_commit_failure(db)
        rejects(sqlite3.IntegrityError, publish, db, number)
        assert get(db, number) is None and shift(db, "day").capture_ids == ()
        db.execute("DROP TRIGGER stop_at_commit")
        issued = publish(db, number).issued
        assert_trigger_record(issued, first, last)
        assert publish(db, number).issued == issued
        complete_ordinary(session, successor, wave)
        ordinary = publish(db, successor.capture_id).issued
        assert ordinary.result.evidence.waveform_source == wave.source

    with closing(sqlite3.connect(path)) as db:
        fixed = get(db, number)
        assert fixed == issued and get(db, successor.capture_id) == ordinary
        assert_trigger_record(fixed, first, last)
        report = shift(db, "day")
        assert (report.capture_ids, report.peak, report.peak_capture_id) == ((41, 42), 90, 41)
        return {
            "capture_id": fixed.capture_id,
            "segments": [[list(raw), lease.as_evidence()]
                         for raw, lease in fixed.result.evidence.trigger_segments],
            "scaled": fixed.result.scaled,
            "peak": fixed.result.peak,
            "unit": fixed.result.unit.symbol,
            "receipt_id": fixed.receipt.receipt_id,
        }


class MoveInterrupted(RuntimeError):
    pass


def stop_move():
    raise MoveInterrupted("transfer interrupted before commit")


def interrupted_case(path):
    from bench.session import move, prepare, trigger

    with recording_at(path) as (db, session):
        number, first = prepare(session, PREFIX, MULTIPLIER)
        other = session.begin_capture()
        rejects(SlotsFull, move, session, number, first, other.slot)
        assert set(session.slots.occupied) == {first, other}
        complete_ordinary(session, other, session.writer.write_waveform(other, (1,)))
        rejects(MoveInterrupted, move, session, number, first, other.slot, before_commit=stop_move)
        assert session.slots.occupied == (first,)
        assert publish(db, number).state == "pending"
        event = trigger(session, number, first, SUFFIX)
        rejects(ValueError, move, session, number, first, other.slot)
        assert deliver(session, event).state == "held"
        assert_trigger_record(publish(db, number).issued, first, first)


def main():
    parser = argparse.ArgumentParser(description="Replay the vibration bench slot-transfer scenario")
    parser.add_argument("command", choices=("baseline", "verify", "demo"))
    args = parser.parse_args()
    if args.command == "baseline":
        baseline()
        return 0
    if args.command == "verify":
        from check_project import main as original_checks

        if status := original_checks():
            return status
    with TemporaryDirectory(prefix="trigger-transfer-") as directory:
        if args.command == "verify":
            interrupted_case(Path(directory) / "interrupted.sqlite")
        print(json.dumps(moved_case(Path(directory) / "moved.sqlite"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
