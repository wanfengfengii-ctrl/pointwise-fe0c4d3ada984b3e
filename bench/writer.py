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

    def activate(self, lease: SlotLease) -> None:
        self.slots.require_active(lease)
        self._contents[lease.slot] = {}

    def write_waveform(self, lease: SlotLease, values: tuple | list) -> RegionReady:
        if not isinstance(values, (tuple, list)) or not all(map(is_device_scalar, values)):
            raise ValueError('device waveform must be a sequence of scalar readings')
        return self._write(RegionReady(RegionSource(lease, 'waveform'), tuple(values)))

    def write_range(self, lease: SlotLease, multiplier: object) -> RegionReady:
        if not is_device_scalar(multiplier):
            raise ValueError('device range must be a scalar reading')
        return self._write(RegionReady(RegionSource(lease, 'range'), multiplier))

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
