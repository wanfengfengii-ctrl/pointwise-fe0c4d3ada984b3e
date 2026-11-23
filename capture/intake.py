from bench.session import BenchSession, CaptureRegistration
from bench.writer import DeviceWriter, RegionReady, RegionSource
from capture.completion import assemble_capture, validate_region
from capture.custody import CustodyStore
from capture.resume import CaptureStatus, confirm_return
from capture.transfer import TransferStore, complete_transfer


class PendingRegions:
    def __init__(self) -> None:
        self._captures: dict[int, dict[str, RegionReady]] = {}

    def for_capture(self, number: int) -> dict[str, RegionReady]:
        return dict(self._captures.get(number, {}))

    def accept(self, registration: CaptureRegistration, event: RegionReady, writer: DeviceWriter) -> None:
        validate_region(registration, event)
        number = registration.lease.capture_id
        region = event.source.region
        previous = self._captures.get(number, {}).get(region)
        if previous is not None:
            if not previous.matches(event):
                raise ValueError('conflicting feedback replay')
            return
        actual = writer.verify_ready(event)
        self._captures.setdefault(number, {})[region] = actual

    def forget(self, number: int) -> None:
        self._captures.pop(number, None)

    def clear(self) -> None:
        self._captures.clear()


def deliver(session: BenchSession, event: RegionReady) -> CaptureStatus:
    session.require_open()
    if not isinstance(event, RegionReady) or not isinstance(event.source, RegionSource):
        raise ValueError('invalid device feedback')
    lease = event.source.lease
    registration = session.index.get_capture(lease.capture_id)
    if registration is None or registration.lease.session_id != session.session_id:
        raise ValueError('feedback is outside this recording session')
    store = CustodyStore(session.db)
    held = store.get(lease.capture_id)
    transfer = TransferStore(session.db).get(lease.capture_id)
    if held is not None:
        if not held.capture.accepts_event(event):
            raise ValueError('conflicting feedback for a capture in custody')
    elif transfer is not None:
        if transfer.session_id != session.session_id:
            raise ValueError('feedback is outside this recording session')
        complete = complete_transfer(session, registration, transfer, event)
        held = store.commit(complete)
        TransferStore(session.db).delete(lease.capture_id)
        session.prepared.discard(lease.capture_id)
        session.writer.unguard(transfer.prefix_lease)
        returned = confirm_return(session, lease.capture_id)
        return CaptureStatus('held', registration, returned)
    else:
        validate_region(registration, event)
        session.pending.accept(registration, event, session.writer)
        complete = assemble_capture(registration, session.pending.for_capture(lease.capture_id))
        if complete is None:
            return CaptureStatus('pending', registration)
        store.commit(complete)
        session.pending.forget(lease.capture_id)
    returned = confirm_return(session, lease.capture_id)
    return CaptureStatus('held', registration, returned)
