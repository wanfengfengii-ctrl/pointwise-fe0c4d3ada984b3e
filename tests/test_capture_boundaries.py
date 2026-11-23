import sqlite3
from dataclasses import replace

from bench.session import BenchBusy, RunIndex, start
from bench.slots import SlotsFull
from bench.writer import RegionReady, RegionSource
from capture.custody import CustodyStore
from capture.intake import deliver
from capture.resume import confirm_return, status
from handover.publish import publish
from recipes.catalog import ProcedureCatalog
from recipes.revisions import build_revision
from review.readout import get
from tests.support import BenchCase, DEFAULT_KEY, defaults, recording, register_micro, row_count


class TestCaptureBoundaries(BenchCase):
    def test_replay(self):
        session = self.session()
        lease = session.begin_capture()
        wave = session.writer.write_waveform(lease, (2, 4))
        self.assertEqual(deliver(session, wave), deliver(session, wave))
        self.assertEqual(session.pending.for_capture(41), {'waveform': wave})
        self.assertEqual(row_count(self.db, 'held_captures'), 0)
        scale = session.writer.write_range(lease, 10)
        held = deliver(session, scale)
        self.assertEqual(deliver(session, scale), held)
        self.assertEqual(deliver(session, wave), held)
        self.assertEqual(row_count(self.db, 'held_captures'), 1)
        self.assertEqual(session.pending.for_capture(41), {})
        self.assertEqual(row_count(self.db, 'capture_index'), 1)

    def test_by_number(self):
        session = self.session()
        self.assertEqual(status(self.db, session.session_id, 41).state, 'unknown')
        lease = session.begin_capture()
        self.assertEqual(status(self.db, session.session_id, 41).registration.lease, lease)
        self.assertEqual(status(self.db, session.session_id, 41).state, 'pending')
        self.assertEqual(status(self.db, session.session_id + 1, 41).state, 'unknown')
        self.assertEqual(status(self.db, session.session_id, 42).state, 'unknown')
        self.assertEqual(publish(self.db, 42).state, 'unknown')

    def test_release_after_custody(self):
        session = self.session()
        first = session.begin_capture()
        second = session.begin_capture()
        wave = session.writer.write_waveform(first, (2, 4))
        scale = session.writer.write_range(first, 10)
        deliver(session, wave)
        self.db.execute('''CREATE TEMP TRIGGER stop_custody BEFORE INSERT ON main.held_captures
            BEGIN SELECT RAISE(ABORT, 'custody unavailable'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'custody unavailable'):
            deliver(session, scale)
        self.assertEqual(status(self.db, first.session_id, 41).state, 'pending')
        self.assertEqual(session.pending.for_capture(41), {'waveform': wave, 'range': scale})
        self.assertEqual(session.slots.occupied, (first, second))
        with self.assertRaises(SlotsFull):
            session.begin_capture()
        self.assertEqual(row_count(self.db, 'capture_index'), 2)
        self.db.execute('DROP TRIGGER stop_custody')
        self.assertTrue(deliver(session, scale).held.returned)
        third = session.begin_capture()
        self.assertEqual((third.capture_id, third.slot), (43, 'A'))

    def test_reuse_original(self):
        session = self.session()
        first, wave, scale = self.complete(session)
        session.begin_capture()
        third, new_wave, new_scale = self.complete(session, (3, 5), 100)
        self.assertEqual(first.slot, third.slot)
        self.reopen(session)
        old = status(self.db, first.session_id, 41).held.capture
        new = status(self.db, first.session_id, 43).held.capture
        self.assertEqual((old.waveform, old.multiplier), ((2, 4), 10))
        self.assertEqual((new.waveform, new.multiplier), ((3, 5), 100))
        self.assertEqual((old.waveform_source, old.range_source), (wave.source, scale.source))
        self.assertEqual((new.waveform_source, new.range_source), (new_wave.source, new_scale.source))

    def test_invalid_recording(self):
        RunIndex(self.db)
        invalid = [
            recording(steps=[{'op': 'range', 'label': 'unallocated', 'multiplier': 10}]),
            recording(steps=[{'op': 'begin', 'label': 'x'},
                             {'op': 'deliver', 'label': 'x', 'region': 'waveform'}]),
            recording(channel='non-drive-end'),
            recording(shift=' '),
        ]
        for sample in invalid:
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                start(self.db, DEFAULT_KEY, sample)
        self.assertEqual(row_count(self.db, 'bench_sessions'), 0)
        self.assertEqual(row_count(self.db, 'capture_index'), 0)
        self.assertEqual(self.session().begin_capture().slot, 'A')

    def test_initialization_replay(self):
        custom = register_micro(self.db)
        session = self.session()
        self.complete(session)
        issued = publish(self.db, 41).issued
        self.reopen(session)
        catalog = ProcedureCatalog(self.db)
        changed_default = dict(defaults()[0], sampling_rate=3000)
        extra_default = dict(defaults()[0], revision='3')
        self.assertEqual(catalog.seed_defaults([changed_default, extra_default]), 1)
        self.assertEqual(catalog.seed_defaults(defaults()), 0)
        self.assertEqual(catalog.get(DEFAULT_KEY).limits.sampling_rate, 1000)
        self.assertEqual(catalog.get(custom.key), custom)
        self.assertEqual(catalog.get(('motor-vibration', '3')).limits.sampling_rate, 1000)
        self.assertEqual(get(self.db, 41), issued)
        self.assertEqual(row_count(self.db, 'capture_index'), 1)
        with self.assertRaises(KeyError):
            catalog.get(('motor-vibration', 'missing'))
        with self.assertRaises(ValueError):
            catalog.register(build_revision(**changed_default))
        self.assertEqual(catalog.get(DEFAULT_KEY).limits.sampling_rate, 1000)

    def test_foreign_region(self):
        RunIndex(self.db, first_capture=39)
        session = self.session()
        old, _, old_range = self.complete(session, (8,), 100)
        session.begin_capture()
        target = session.begin_capture()
        self.assertEqual((old.capture_id, old.slot, target.capture_id, target.slot), (39, 'A', 41, 'A'))
        wave = session.writer.write_waveform(target, (2, 4))
        self.assertEqual(deliver(session, wave).state, 'pending')
        self.assertEqual(deliver(session, old_range).registration.lease, old)
        forged = replace(old_range, source=RegionSource(target, 'range'))
        with self.assertRaisesRegex(ValueError, 'actual completed device write'):
            deliver(session, forged)
        self.assertEqual(status(self.db, target.session_id, 41).state, 'pending')
        self.assertEqual(publish(self.db, 41).state, 'pending')
        self.assertIsNone(get(self.db, 41))
        self.assertEqual(row_count(self.db, 'first_receipts'), 0)
        scale = session.writer.write_range(target, 10)
        self.assertEqual(deliver(session, scale).state, 'held')
        fixed = publish(self.db, 41).issued
        self.assertEqual(fixed.result.scaled, (20, 40))
        self.assertEqual(fixed.result.evidence.range_source, scale.source)

    def test_empty_waveform_and_invalid_readings(self):
        for values, multiplier in (([], 10), ([1, True], 10), ([1, 2.5], 10), ([1], 0), ([1], 11), ([1], True)):
            with self.subTest(values=values, multiplier=multiplier):
                with self.session() as session:
                    lease = session.begin_capture()
                    wave = session.writer.write_waveform(lease, values)
                    scale = session.writer.write_range(lease, multiplier)
                    valid_waveform = bool(values) and all(type(value) is int for value in values)
                    good, bad = (wave, scale) if valid_waveform else (scale, wave)
                    self.assertEqual(deliver(session, good).state, 'pending')
                    with self.assertRaises(ValueError):
                        deliver(session, bad)
                    self.assertEqual(publish(self.db, lease.capture_id).state, 'pending')
                    self.assertIsNone(get(self.db, lease.capture_id))
                    self.assertEqual(row_count(self.db, 'first_receipts'), 0)

    def test_return_confirmation_after_reuse(self):
        session = self.session()
        first = session.begin_capture()
        deliver(session, session.writer.write_waveform(first, (2, 4)))
        scale = session.writer.write_range(first, 10)
        CustodyStore(self.db)
        self.db.execute('''CREATE TEMP TRIGGER drop_confirmation BEFORE UPDATE OF returned ON main.held_captures
            BEGIN SELECT RAISE(ABORT, 'confirmation unavailable'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'confirmation unavailable'):
            deliver(session, scale)
        self.assertFalse(status(self.db, first.session_id, 41).held.returned)
        self.assertEqual(session.slots.occupied, ())
        second = session.begin_capture()
        third = session.begin_capture()
        self.assertEqual((second.slot, third.slot), ('B', 'A'))
        self.db.execute('DROP TRIGGER drop_confirmation')
        self.assertTrue(confirm_return(session, 41).returned)
        self.assertTrue(deliver(session, scale).held.returned)
        self.assertCountEqual(session.slots.occupied, (second, third))
        with self.assertRaises(ValueError):
            session.writer.write_range(first, 10)
        self.assertEqual(status(self.db, first.session_id, 43).state, 'pending')

    def test_registration_rollback_preserves_slot_and_number(self):
        session = self.session()
        self.db.execute('''CREATE TEMP TRIGGER stop_registration BEFORE INSERT ON main.capture_index
            BEGIN SELECT RAISE(ABORT, 'capture index unavailable'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'capture index unavailable'):
            session.begin_capture()
        self.assertEqual(session.slots.occupied, ())
        self.assertEqual(status(self.db, session.session_id, 41).state, 'unknown')
        self.db.execute('DROP TRIGGER stop_registration')
        first = session.begin_capture()
        self.assertEqual((first.capture_id, first.slot), (41, 'A'))

    def test_device_write_and_delivery_are_separate(self):
        source = recording(steps=[
            {'op': 'begin', 'label': 'm'},
            {'op': 'range', 'label': 'm', 'multiplier': 10},
            {'op': 'waveform', 'label': 'm', 'values': [2, 4]},
            {'op': 'deliver', 'label': 'm', 'region': 'range'},
            {'op': 'deliver', 'label': 'm', 'region': 'waveform'},
        ])
        session = self.session(source=source)
        lease = session.record_step()
        self.assertIsNone(session.record_step())
        self.assertIsNone(session.record_step())
        self.assertEqual(publish(self.db, lease.capture_id).state, 'pending')
        event = session.record_step()
        self.assertIsInstance(event, RegionReady)
        self.assertEqual(deliver(session, event).state, 'pending')
        self.assertEqual(deliver(session, session.record_step()).state, 'held')
        with self.assertRaises(StopIteration):
            session.record_step()

    def test_single_live_session(self):
        with self.session() as session:
            other_db = self.connect()
            with self.assertRaises(BenchBusy):
                start(other_db, DEFAULT_KEY, recording())
            first = session.begin_capture()
        with start(other_db, DEFAULT_KEY, recording()) as later:
            self.assertNotEqual(later.session_id, first.session_id)
            self.assertEqual(later.begin_capture().capture_id, 42)
