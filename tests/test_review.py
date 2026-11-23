from capture.intake import deliver
from handover.publish import publish
from review.readout import get
from review.shift import shift
from tests.support import BenchCase


class TestReview(BenchCase):
    def test_offline_read_after_slot_reuse(self):
        session = self.session()
        first, old_wave, old_scale = self.complete(session)
        original = publish(self.db, first.capture_id).issued
        second = session.begin_capture()
        third, new_wave, new_scale = self.complete(session, (3, 5), 100)
        self.assertEqual((first.slot, second.slot, third.slot), ('A', 'B', 'A'))
        self.assertNotEqual(first.instance, third.instance)
        self.assertTrue(deliver(session, old_wave).held.returned)
        self.assertEqual(publish(self.db, 41).issued, original)
        third_record = publish(self.db, third.capture_id).issued
        self.reopen(session)
        old = get(self.db, 41)
        new = get(self.db, 43)
        self.assertEqual(old, original)
        self.assertEqual(new, third_record)
        self.assertEqual((old.result.scaled, new.result.scaled), ((20, 40), (300, 500)))
        self.assertEqual((old.result.evidence.waveform, old.result.evidence.multiplier), ((2, 4), 10))
        self.assertEqual((new.result.evidence.waveform, new.result.evidence.multiplier), ((3, 5), 100))
        self.assertEqual(old.result.evidence.waveform_source, old_wave.source)
        self.assertEqual(old.result.evidence.range_source, old_scale.source)
        self.assertEqual(new.result.evidence.waveform_source, new_wave.source)
        self.assertEqual(new.result.evidence.range_source, new_scale.source)
        self.assertNotEqual(old.receipt, new.receipt)

    def test_shift_completed_only(self):
        session = self.session()
        complete, _, _ = self.complete(session, (-7, 3), 10)
        pending = session.begin_capture()
        deliver(session, session.writer.write_waveform(pending, (100,)))
        self.assertEqual(publish(self.db, complete.capture_id).state, 'issued')
        summary = shift(self.db, 'day')
        self.assertEqual(summary.capture_ids, (41,))
        self.assertEqual((summary.peak, summary.peak_unit, summary.peak_capture_id), (70, 'mm/s', 41))
        self.assertIsNone(get(self.db, pending.capture_id))
