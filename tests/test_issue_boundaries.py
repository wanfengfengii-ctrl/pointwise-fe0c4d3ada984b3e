import sqlite3

from capture.intake import deliver
from capture.resume import status
from handover.publish import publish
from review.provenance import project_issued_evidence, verify_provenance
from review.readout import get
from review.shift import shift
from tests.support import BenchCase, deferred_commit_failure, receipt_failure, register_micro, row_count


class TestIssueBoundaries(BenchCase):
    def test_frozen_revision(self):
        session = self.session()
        register_micro(self.db)
        self.complete(session, (-2, 4), 10)
        record = publish(self.db, 41).issued
        self.assertEqual(record.result.evidence.procedure.key, ('motor-vibration', '1'))
        self.assertEqual(record.result.evidence.procedure.limits.sampling_rate, 1000)
        self.assertEqual(record.result.evidence.multiplier, 10)
        self.assertEqual((record.result.scaled, record.result.unit.symbol), ((-20, 40), 'mm/s'))

    def test_negative_peak(self):
        session = self.session()
        self.complete(session, (-9, 2, -9, 0), 10)
        result = publish(self.db, 41).issued.result
        self.assertEqual(result.scaled, (-90, 20, -90, 0))
        self.assertEqual(result.peak, 90)
        self.assertEqual(shift(self.db, 'day').peak, 90)

    def test_atomic_rollback(self):
        session = self.session()
        lease, _, _ = self.complete(session)
        receipt_failure(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt unavailable'):
            publish(self.db, 41)
        self.assert_clean_issue_rollback(lease)
        self.db.execute('DROP TRIGGER stop_receipt')
        deferred_commit_failure(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            publish(self.db, 41)
        self.assert_clean_issue_rollback(lease)
        self.assertEqual(row_count(self.db, 'pending_receipt_checks'), 0)
        self.db.execute('DROP TRIGGER stop_at_commit')
        self.assertEqual(publish(self.db, 41).state, 'issued')
        self.assertEqual(row_count(self.db, 'issued_results'), 1)
        self.assertEqual(row_count(self.db, 'first_receipts'), 1)

    def assert_clean_issue_rollback(self, lease):
        self.assertFalse(self.db.in_transaction)
        self.assertIsNone(get(self.db, 41))
        self.assertEqual(shift(self.db, 'day').capture_ids, ())
        self.assertEqual(row_count(self.db, 'issued_results'), 0)
        self.assertEqual(row_count(self.db, 'first_receipts'), 0)
        self.assertTrue(status(self.db, lease.session_id, 41).held.returned)
        with self.connect() as observer:
            self.assertEqual(row_count(observer, 'issued_results'), 0)
            self.assertEqual(row_count(observer, 'first_receipts'), 0)

    def test_reuse_then_reopen(self):
        session = self.session()
        first, wave, scale = self.complete(session)
        receipt_failure(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt unavailable'):
            publish(self.db, 41)
        self.assert_clean_issue_rollback(first)
        middle = session.begin_capture()
        deliver(session, session.writer.write_waveform(middle, (7, 8)))
        third, new_wave, new_scale = self.complete(session, (3, 5), 100)
        self.assertEqual([first.slot, middle.slot, third.slot], ['A', 'B', 'A'])
        self.reopen(session)
        self.assertIsNone(get(self.db, 41))
        self.assertEqual(publish(self.db, 42).state, 'pending')
        self.assertEqual(publish(self.db, 41).state, 'issued')
        self.assertEqual(publish(self.db, 43).state, 'issued')
        for number, raw, multiplier, scaled, source_wave, source_range in (
            (41, (2, 4), 10, (20, 40), wave.source, scale.source),
            (43, (3, 5), 100, (300, 500), new_wave.source, new_scale.source),
        ):
            with self.subTest(number=number):
                archive = get(self.db, number)
                evidence = verify_provenance(archive, number)
                self.assertEqual((evidence.waveform, evidence.multiplier, archive.result.scaled),
                                 (raw, multiplier, scaled))
                self.assertEqual((evidence.waveform_source, evidence.range_source), (source_wave, source_range))
                self.assertEqual(archive.receipt, publish(self.db, number).issued.receipt)
        report = shift(self.db, 'day')
        self.assertEqual((report.capture_ids, report.peak, report.peak_unit), ((41, 43), 500, 'mm/s'))

    def test_first_receipt(self):
        session = self.session()
        self.complete(session)
        publish(self.db, 41)
        first_receipt = get(self.db, 41).receipt
        self.reopen(session)
        self.assertEqual(publish(self.db, 41).issued.receipt, first_receipt)
        self.assertEqual(get(self.db, 41).receipt, first_receipt)
        self.assertEqual(row_count(self.db, 'issued_results'), 1)
        self.assertEqual(row_count(self.db, 'first_receipts'), 1)

    def test_requested_number(self):
        session = self.session()
        first, first_wave, _ = self.complete(session)
        middle = session.begin_capture()
        third, third_wave, _ = self.complete(session, (3, 5), 100)
        deliver(session, session.writer.write_range(middle, 10))
        middle_wave = session.writer.write_waveform(middle, (6,))
        deliver(session, middle_wave)
        self.assertEqual((first.slot, middle.slot, third.slot), ('A', 'B', 'A'))
        saved = {number: publish(self.db, number).issued for number in (41, 43, 42)}
        self.assertGreater(saved[42].receipt.receipt_id, saved[43].receipt.receipt_id)
        self.reopen(session)
        for number, values, source in ((41, (20, 40), first_wave.source),
                                       (42, (60,), middle_wave.source),
                                       (43, (300, 500), third_wave.source)):
            with self.subTest(number=number):
                archive = get(self.db, number)
                self.assertEqual(archive, saved[number])
                self.assertEqual(archive.result.scaled, values)
                self.assertEqual(project_issued_evidence(archive).waveform_source, source)
        self.assertIsNone(get(self.db, 99))

    def test_failed_publish(self):
        session = self.session()
        self.complete(session, (-4, 1), 10)
        self.assertEqual(publish(self.db, 41).state, 'issued')
        pending = session.begin_capture()
        deliver(session, session.writer.write_waveform(pending, (1000,)))
        self.complete(session, (1000,), 100)
        receipt_failure(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt unavailable'):
            publish(self.db, 43)
        self.reopen(session)
        self.assertEqual(shift(self.db, 'day').capture_ids, (41,))
        self.assertEqual(shift(self.db, 'day').peak, 40)
        self.assertIsNone(get(self.db, 42))
        self.assertIsNone(get(self.db, 43))
        self.assertEqual(row_count(self.db, 'first_receipts'), 1)

    def test_ordered_peak(self):
        with self.session() as session:
            self.complete(session, (-7, 2), 10)
            self.complete(session, (6,), 10)
            publish(self.db, 42)
            publish(self.db, 41)
        with self.session(shift='night') as session:
            self.complete(session, (500,), 100)
            publish(self.db, 43)
        self.assertEqual(shift(self.db, 'day').capture_ids, (41, 42))
        self.assertEqual((shift(self.db, 'day').peak, shift(self.db, 'day').peak_capture_id), (70, 41))
        self.assertEqual((shift(self.db, 'night').capture_ids, shift(self.db, 'night').peak), ((43,), 50000))

    def test_empty_zero(self):
        blank = shift(self.db, 'empty')
        self.assertEqual((blank.capture_ids, blank.peak, blank.peak_unit, blank.peak_capture_id),
                         ((), None, None, None))
        with self.session(shift=' zero ') as session:
            self.complete(session, (0, 0), 10)
            publish(self.db, 41)
        zero = shift(self.db, ' zero ')
        self.assertEqual((zero.peak, zero.peak_unit, zero.capture_ids), (0, 'mm/s', (41,)))
        self.assertEqual(shift(self.db, 'zero').capture_ids, ())
        with self.assertRaises(ValueError):
            shift(self.db, ' \t')

    def test_compatible_units_and_first_equal_peak(self):
        micro = register_micro(self.db)
        with self.session(key=micro.key) as session:
            self.complete(session, (2500,), 1)
            self.complete(session, (3000,), 1)
        with self.session() as session:
            self.complete(session, (-3,), 1)
        for number in (43, 41):
            publish(self.db, number)
        self.assertEqual((get(self.db, 41).result.peak, get(self.db, 41).result.unit.symbol), (2500, 'µm/s'))
        self.assertEqual((get(self.db, 43).result.peak, get(self.db, 43).result.unit.symbol), (3, 'mm/s'))
        summary = shift(self.db, 'day')
        self.assertEqual((summary.peak, summary.peak_unit, summary.peak_capture_id), (3, 'mm/s', 43))
        publish(self.db, 42)
        tied = shift(self.db, 'day')
        self.assertEqual(tied.capture_ids, (41, 42, 43))
        self.assertEqual((tied.peak, tied.peak_unit, tied.peak_capture_id), (3000, 'µm/s', 42))
