from dataclasses import dataclass


class SlotsFull(RuntimeError):
    pass


def capture_number(value: int) -> int:
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError('capture number must be a positive SQLite integer')
    return value


@dataclass(frozen=True, slots=True)
class SlotLease:
    session_id: int
    capture_id: int
    slot: str
    instance: int

    def __post_init__(self) -> None:
        capture_number(self.session_id)
        capture_number(self.capture_id)
        capture_number(self.instance)
        if self.slot not in ('A', 'B'):
            raise ValueError('unknown device slot')

    def as_evidence(self) -> dict:
        return {
            'session_id': self.session_id, 'capture_id': self.capture_id,
            'slot': self.slot, 'instance': self.instance,
        }


class SlotPool:
    names = ('A', 'B')

    def __init__(self) -> None:
        self._active: dict[str, SlotLease] = {}
        self._returned: set[SlotLease] = set()
        self._generations = dict.fromkeys(self.names, 0)
        self._next = 0
        self._closed = False

    @property
    def occupied(self) -> tuple[SlotLease, ...]:
        return tuple(self._active[slot] for slot in self.names if slot in self._active)

    def reserve(self, session_id: int, number: int) -> SlotLease:
        if self._closed:
            raise ValueError('device is closed')
        for offset in range(len(self.names)):
            cursor = (self._next + offset) % len(self.names)
            slot = self.names[cursor]
            if slot in self._active:
                continue
            lease = SlotLease(session_id, number, slot, self._generations[slot] + 1)
            self._generations[slot] = lease.instance
            self._active[slot] = lease
            self._next = (cursor + 1) % len(self.names)
            return lease
        raise SlotsFull('both device slots are occupied')

    def require_active(self, lease: SlotLease) -> None:
        if self._closed or self._active.get(lease.slot) != lease:
            raise ValueError('device slot is not occupied by this instance')

    def release(self, lease: SlotLease) -> SlotLease:
        if self._closed:
            raise ValueError('device is closed')
        if lease not in self._returned:
            self.require_active(lease)
            del self._active[lease.slot]
            self._returned.add(lease)
        return lease

    def cancel_reservation(self, lease: SlotLease) -> None:
        self.require_active(lease)
        del self._active[lease.slot]
        self._next = self.names.index(lease.slot)

    def close(self) -> None:
        self._closed = True
        self._active.clear()
        self._returned.clear()
