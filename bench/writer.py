from dataclasses import dataclass

from bench.slots import SlotLease, SlotPool


REGIONS = ('waveform', 'range')


def is_device_scalar(value: object) -> bool:
    return type(value) in (int, float, bool, str, type(None))


@dataclass(frozen=True, slots=True)
class RegionSource:
    lease: SlotLease
    region: str

    def __post_init__(self) -> None:
        if not isinstance(self.lease, SlotLease) or self.region not in REGIONS:
            raise ValueError('invalid region source')

    def as_evidence(self) -> dict:
        return {**self.lease.as_evidence(), 'region': self.region}

    @classmethod
    def from_evidence(cls, evidence: dict) -> 'RegionSource':
        fields = dict(evidence)
        region = fields.pop('region')
        return cls(SlotLease(**fields), region)


def _same_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, tuple):
        return len(left) == len(right) and all(
            type(a) is type(b) and a == b for a, b in zip(left, right)
        )
    return left == right


@dataclass(frozen=True, slots=True)
class RegionReady:
    source: RegionSource
    value: object

    def matches(self, other: 'RegionReady') -> bool:
        return self.source == other.source and _same_value(self.value, other.value)


class DeviceWriter:
    def __init__(self, slots: SlotPool) -> None:
        self.slots = slots
        self._contents: dict[str, dict[str, RegionReady]] = {}
        self._triggers: dict[str, RegionReady] = {}
        self._guarded: set[int] = set()

    def activate(self, lease: SlotLease) -> None:
        self.slots.require_active(lease)
        self._contents[lease.slot] = {}
        self._triggers.pop(lease.slot, None)

    def guard(self, lease: SlotLease) -> None:
        self._guarded.add(lease.capture_id)

    def unguard(self, lease: SlotLease) -> None:
        self._guarded.discard(lease.capture_id)

    def is_guarded(self, lease: SlotLease) -> bool:
        return lease.capture_id in self._guarded

    def rebind(self, old: SlotLease, new: SlotLease) -> None:
        self.slots.require_active(new)
        self._contents[new.slot] = {}
        self._triggers.pop(new.slot, None)

    def write_waveform(self, lease: SlotLease, values: tuple | list) -> RegionReady:
        if self.is_guarded(lease):
            raise ValueError('capture is reserved for a pre-trigger slot transfer')
        if not isinstance(values, (tuple, list)) or not all(map(is_device_scalar, values)):
            raise ValueError('device waveform must be a sequence of scalar readings')
        return self._write(RegionReady(RegionSource(lease, 'waveform'), tuple(values)))

    def write_range(self, lease: SlotLease, multiplier: object) -> RegionReady:
        if self.is_guarded(lease):
            raise ValueError('capture is reserved for a pre-trigger slot transfer')
        if not is_device_scalar(multiplier):
            raise ValueError('device range must be a scalar reading')
        return self._write(RegionReady(RegionSource(lease, 'range'), multiplier))

    def write_trigger(self, lease: SlotLease, values: tuple) -> RegionReady:
        self.slots.require_active(lease)
        event = RegionReady(RegionSource(lease, 'waveform'), values)
        previous = self._triggers.get(lease.slot)
        if previous is not None:
            if not previous.matches(event):
                raise ValueError('capture trigger was already written')
            return previous
        self._triggers[lease.slot] = event
        return event

    def verify_trigger(self, event: RegionReady) -> RegionReady:
        lease = event.source.lease
        self.slots.require_active(lease)
        if event.source.region != 'waveform':
            raise ValueError('the trigger segment is a waveform region')
        actual = self._triggers.get(lease.slot)
        if actual is None or not actual.matches(event):
            raise ValueError('feedback does not match an actual completed trigger write')
        return actual

    def _write(self, event: RegionReady) -> RegionReady:
        lease = event.source.lease
        self.slots.require_active(lease)
        contents = self._contents[lease.slot]
        previous = contents.get(event.source.region)
        if previous is not None:
            if not previous.matches(event):
                raise ValueError('device region already written')
            return previous
        contents[event.source.region] = event
        return event

    def verify_ready(self, event: RegionReady) -> RegionReady:
        lease = event.source.lease
        self.slots.require_active(lease)
        actual = self._contents[lease.slot].get(event.source.region)
        if actual is None or not actual.matches(event):
            raise ValueError('feedback does not match an actual completed device write')
        return actual

    def close(self) -> None:
        self.slots.close()
        self._contents.clear()
        self._triggers.clear()
        self._guarded.clear()
