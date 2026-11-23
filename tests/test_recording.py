from dataclasses import replace

from bench.session import RunIndex, start
from capture.intake import deliver
from capture.resume import status
from tests.support import BenchCase, recording, row_count


class TestRecording(BenchCase):
    def test_reordered_regions(self):
        session = self.session()
        lease = session.begin_capture()
        self.assertEqual((lease.capture_id, lease.slot), (41, 'A'))
        scale = session.writer.write_range(lease, 10)
        first = deliver(session, scale)
        self.assertEqual(first.state, 'pending')
        self.assertIsNone(first.held)
        self.assertEqual(session.slots.occupied, (lease,))
        wave = session.writer.write_waveform(lease, (2, 4))
        self.assertEqual(status(self.db, lease.session_id, 41).state, 'pending')
        second = deliver(session, wave)
        self.assertEqual(second.state, 'held')
        self.assertTrue(second.held.returned)
        self.assertEqual(second.held.capture.waveform, (2, 4))
        self.assertEqual(second.held.capture.multiplier, 10)
        self.assertEqual(second.held.capture.waveform_source, wave.source)
        self.assertEqual(second.held.capture.range_source, scale.source)
        self.assertEqual(session.slots.occupied, ())

    def test_unknown_recipe(self):
        RunIndex(self.db)
        with self.assertRaises(KeyError):
            start(self.db, ('motor-vibration', 'absent'), recording())
        self.assertEqual(row_count(self.db, 'bench_sessions'), 0)
        self.assertEqual(row_count(self.db, 'capture_index'), 0)
        session = self.session()
        first = session.begin_capture()
        self.assertEqual((first.capture_id, first.slot), (41, 'A'))

    def test_conflicting_retransmission(self):
        session = self.session()
        first = session.begin_capture()
        wave = session.writer.write_waveform(first, (-2, 4))
        self.assertEqual(deliver(session, wave).state, 'pending')
        conflict = replace(wave, value=(-2, 9))
        with self.assertRaises(ValueError):
            deliver(session, conflict)
        self.assertEqual(session.pending.for_capture(41), {'waveform': wave})
        scale = session.writer.write_range(first, 10)
        self.assertEqual(deliver(session, scale).held.capture.waveform, (-2, 4))
        with self.assertRaises(ValueError):
            deliver(session, conflict)
        self.assertEqual(status(self.db, first.session_id, 41).held.capture.waveform_source, wave.source)
