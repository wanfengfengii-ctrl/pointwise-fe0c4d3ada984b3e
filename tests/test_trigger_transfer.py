import sqlite3
from dataclasses import replace

from bench.session import move, prepare, start, trigger
from bench.slots import SlotsFull
from bench.writer import RegionReady, RegionSource
from capture.intake import deliver
from capture.resume import status
from handover.publish import publish
from review.provenance import verify_provenance
from review.readout import get
from review.shift import shift
from tests.support import (BenchCase, DEFAULT_KEY, deferred_commit_failure, receipt_failure,
                           recording, row_count)


class TestTriggerTransfer(BenchCase):
    def prepared(self, values=(2, 4), multiplier=10):
        session = self.session()
        number, carrier = prepare(session, values, multiplier)
        self.addCleanup(session.writer.unguard, carrier)
        return session, number, carrier

    def trigger_case(self, session, number, carrier, suffix=(-9,)):
        event = trigger(session, number, carrier, suffix)
        self.assertEqual(deliver(session, event).state, 'held')
        return event

    def test_prepare_writes_prefix_only(self):
        session, number, first = self.prepared()
        self.assertEqual((number, first.capture_id, first.slot), (41, 41, 'A'))
        with self.assertRaises(ValueError):
            session.writer.write_waveform(first, (2, 4))
        with self.assertRaises(ValueError):
            session.writer.write_range(first, 10)
        self.assertEqual(publish(self.db, 41).state, 'pending')
        self.assertIsNone(get(self.db, 41))

    def test_move_and_reuse_original_slot(self):
        session, number, first = self.prepared()
        last = move(session, number, first, 'B')
        self.assertEqual((last.session_id, last.capture_id, last.slot), (first.session_id, 41, 'B'))
        self.assertEqual(session.slots.occupied, (last,))
        successor = session.begin_capture()
        self.assertEqual((successor.capture_id, successor.slot), (42, 'A'))
        self.assertNotEqual(successor.instance, first.instance)

    def test_target_busy_keeps_original_right(self):
        session, number, first = self.prepared()
        other = session.begin_capture()
        with self.assertRaises(SlotsFull):
            move(session, number, first, other.slot)
        self.assertEqual(set(session.slots.occupied), {first, other})
        self.trigger_case(session, number, first)
        self.assertEqual(publish(self.db, number).issued.result.scaled, (20, 40, -90))

    def test_interrupted_move_keeps_original_right(self):
        session, number, first = self.prepared()
        other = session.begin_capture()
        wave = session.writer.write_waveform(other, (1,))
        self.assertEqual(deliver(session, wave).state, 'pending')
        self.assertEqual(deliver(session, session.writer.write_range(other, 10)).state, 'held')
        self.assertEqual(session.slots.occupied, (first,))
        def stop():
            raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            move(session, number, first, other.slot, before_commit=stop)
        self.assertEqual(session.slots.occupied, (first,))
        event = self.trigger_case(session, number, first)
        self.assertEqual(event.source.lease, first)
        self.assertEqual(publish(self.db, number).issued.result.evidence.trigger_segments,
                         (((2, 4), first), ((-9,), first)))

    def test_move_commit_failure_keeps_original_right(self):
        session, number, first = self.prepared()
        self.db.execute("CREATE TEMP TRIGGER stop_move BEFORE UPDATE ON main.capture_index "
                        "BEGIN SELECT RAISE(ABORT, 'move unavailable'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'move unavailable'):
            move(session, number, first, 'B')
        self.assertEqual(session.slots.occupied, (first,))
        self.assertEqual(session.index.get_capture(number).lease, first)
        self.trigger_case(session, number, first)
        self.assertEqual(publish(self.db, number).issued.result.scaled, (20, 40, -90))
        self.db.execute('DROP TRIGGER stop_move')

    def test_reject_stale_relabelled_and_untriggered_feedback(self):
        session, number, first = self.prepared()
        last = move(session, number, first, 'B')
        successor = session.begin_capture()
        wave = session.writer.write_waveform(successor, (2, 4))
        with self.assertRaises(ValueError):
            trigger(session, number, first, (-9,))
        relabelled = replace(wave, source=RegionSource(replace(successor, capture_id=number), 'waveform'))
        with self.assertRaises(ValueError):
            deliver(session, relabelled)
        with self.assertRaises(ValueError):
            deliver(session, RegionReady(RegionSource(last, 'waveform'), (-9,)))
        self.assertEqual(publish(self.db, number).state, 'pending')

    def test_single_move_and_trigger_replay(self):
        session, number, first = self.prepared()
        last = move(session, number, first, 'B')
        with self.assertRaises(ValueError):
            move(session, number, last, 'A')
        event = trigger(session, number, last, (-9,))
        with self.assertRaises(ValueError):
            trigger(session, number, last, (-9,))
        with self.assertRaises(ValueError):
            move(session, number, last, 'A')
        self.assertEqual(deliver(session, event).state, 'held')
        self.assertEqual(deliver(session, event).state, 'held')
        with self.assertRaises(ValueError):
            deliver(session, replace(event, value=(9,)))
        record = publish(self.db, number).issued
        self.assertEqual(record.result.scaled, (20, 40, -90))
        self.assertEqual(record.result.peak, 90)
        self.assertEqual(record.result.evidence.trigger_segments,
                         (((2, 4), first), ((-9,), last)))

    def test_invalid_arguments_do_not_consume_number(self):
        session = self.session()
        for values in ([], (True,), (1, 2.5), [1, 'x']):
            with self.subTest(values=values), self.assertRaises(ValueError):
                prepare(session, values, 10)
        for multiplier in (0, 11, True, '10'):
            with self.subTest(multiplier=multiplier), self.assertRaises(ValueError):
                prepare(session, (2, 4), multiplier)
        number, first = prepare(session, (2, 4), 10)
        self.assertEqual(number, 41)

    def test_publish_pending_until_suffix_and_resume_failure(self):
        session, number, first = self.prepared()
        last = move(session, number, first, 'B')
        event = trigger(session, number, last, (-9,))
        self.assertEqual(publish(self.db, number).state, 'pending')
        self.assertIsNone(get(self.db, number))
        deliver(session, event)
        receipt_failure(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            publish(self.db, number)
        self.assertEqual(row_count(self.db, 'issued_results'), 0)
        self.assertEqual(row_count(self.db, 'first_receipts'), 0)
        self.assertIsNone(get(self.db, number))
        self.db.execute('DROP TRIGGER stop_receipt')
        self.reopen(session)
        self.assertEqual(publish(self.db, number).issued.result.scaled, (20, 40, -90))

    def test_offline_two_segments_and_first_receipt(self):
        session, number, first = self.prepared()
        last = move(session, number, first, 'B')
        self.trigger_case(session, number, last)
        self.complete(session, (3, 5), 100)
        issued = publish(self.db, number).issued
        moved = publish(self.db, 42).issued
        self.reopen(session)
        fixed = get(self.db, number)
        self.assertEqual(fixed, issued)
        self.assertEqual(fixed.receipt, publish(self.db, number).issued.receipt)
        evidence = verify_provenance(fixed, number)
        self.assertEqual(evidence.trigger_segments,
                         (((2, 4), first), ((-9,), last)))
        self.assertEqual((evidence.waveform, evidence.multiplier), ((2, 4, -9), 10))
        self.assertEqual((fixed.result.scaled, fixed.result.peak), ((20, 40, -90), 90))
        report = shift(self.db, 'day')
        self.assertEqual((report.capture_ids, report.peak, report.peak_capture_id),
                         ((41, 42), 500, 42))
        self.assertEqual(get(self.db, 42), moved)
