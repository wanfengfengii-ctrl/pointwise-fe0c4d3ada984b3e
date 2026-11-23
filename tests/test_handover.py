import sqlite3

from capture.intake import deliver
from handover.publish import publish
from review.readout import get
from tests.support import BenchCase, receipt_failure, row_count


class TestHandover(BenchCase):
    def test_wait_for_range(self):
        session = self.session()
        lease = session.begin_capture()
        wave = session.writer.write_waveform(lease, (2, 4))
        self.assertEqual(deliver(session, wave).state, 'pending')
        result = publish(self.db, lease.capture_id)
        self.assertEqual(result.state, 'pending')
        self.assertIsNone(result.issued)
        self.assertIsNone(get(self.db, lease.capture_id))
        self.assertEqual(row_count(self.db, 'first_receipts'), 0)

    def test_exact_scale(self):
        session = self.session()
        lease, wave, scale = self.complete(session, (-2, 4), 10)
        record = publish(self.db, lease.capture_id).issued
        self.assertEqual(record.result.scaled, (-20, 40))
        self.assertEqual(record.result.peak, 40)
        self.assertEqual(record.result.unit.symbol, 'mm/s')
        self.assertEqual(record.result.evidence.waveform, (-2, 4))
        self.assertEqual(record.result.evidence.multiplier, 10)
        self.assertEqual(record.result.evidence.waveform_source, wave.source)
        self.assertEqual(record.result.evidence.range_source, scale.source)
        self.assertEqual(record.receipt.capture_id, lease.capture_id)

    def test_retry_after_commit_error(self):
        session = self.session()
        self.complete(session)
        receipt_failure(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt unavailable'):
            publish(self.db, 41)
        self.assertIsNone(get(self.db, 41))
        self.assertEqual(row_count(self.db, 'issued_results'), 0)
        self.assertEqual(row_count(self.db, 'first_receipts'), 0)
        self.reopen(session)
        issued = publish(self.db, 41).issued
        self.assertEqual(issued.result.scaled, (20, 40))
        self.assertEqual(get(self.db, 41), issued)
        self.assertEqual(publish(self.db, 41).issued.receipt, issued.receipt)
        self.assertEqual(row_count(self.db, 'first_receipts'), 1)
