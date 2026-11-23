from collections.abc import Mapping
from dataclasses import dataclass

from bench.session import CaptureRegistration
from bench.slots import SlotLease
from bench.writer import REGIONS, RegionReady, RegionSource
from recipes.limits import require_allowed_multiplier
from recipes.revisions import ProcedureRevision


def require_waveform(values: tuple[int, ...]) -> tuple[int, ...]:
    if type(values) is not tuple or not values or any(type(value) is not int for value in values):
        raise ValueError('waveform must contain one or more integer samples')
    return values


def verify_sources(lease: SlotLease, waveform: RegionSource, range_source: RegionSource) -> None:
    if waveform != RegionSource(lease, 'waveform') or range_source != RegionSource(lease, 'range'):
        raise ValueError('the two regions must originate from the specified capture instance')


def verify_trigger_segments(lease: SlotLease, waveform: tuple[int, ...],
                            waveform_source: RegionSource, range_source: RegionSource,
                            trigger_segments: object) -> None:
    if not isinstance(trigger_segments, tuple) or len(trigger_segments) != 2:
        raise ValueError('trigger evidence requires exactly two segments')
    checked = []
    for segment in trigger_segments:
        if not isinstance(segment, tuple) or len(segment) != 2:
            raise ValueError('invalid trigger segment')
        raw, segment_lease = segment
        require_waveform(raw)
        if not isinstance(segment_lease, SlotLease):
            raise ValueError('invalid trigger segment instance')
        if (segment_lease.session_id, segment_lease.capture_id) != (lease.session_id, lease.capture_id):
            raise ValueError('trigger segment belongs to a different capture')
        checked.append((raw, segment_lease))
    prefix, suffix = checked
    if prefix[0] + suffix[0] != waveform:
        raise ValueError('trigger waveform must be the prefix followed by the suffix')
    if suffix[1] != lease:
        raise ValueError('trigger evidence must describe the instance that actually wrote the suffix')
    if waveform_source != RegionSource(prefix[1], 'waveform'):
        raise ValueError('prefix waveform provenance does not match its first write instance')
    if range_source != RegionSource(prefix[1], 'range'):
        raise ValueError('prefix range provenance does not match its first write instance')


def verify_capture_sources(lease: SlotLease, waveform: tuple[int, ...],
                           waveform_source: RegionSource, range_source: RegionSource,
                           trigger_segments: object) -> None:
    if trigger_segments is None:
        verify_sources(lease, waveform_source, range_source)
    else:
        verify_trigger_segments(lease, waveform, waveform_source, range_source, trigger_segments)


@dataclass(frozen=True, slots=True)
class CompletedCapture:
    lease: SlotLease
    shift: str
    procedure: ProcedureRevision
    waveform: tuple[int, ...]
    multiplier: int
    waveform_source: RegionSource
    range_source: RegionSource
    trigger_segments: tuple[tuple[tuple[int, ...], SlotLease], ...] | None = None

    def as_evidence(self) -> dict:
        evidence = {
            'lease': self.lease.as_evidence(),
            'shift': self.shift,
            'procedure': self.procedure.as_evidence(),
            'waveform': list(self.waveform),
            'multiplier': self.multiplier,
            'waveform_source': self.waveform_source.as_evidence(),
            'range_source': self.range_source.as_evidence(),
        }
        if self.trigger_segments is not None:
            evidence['trigger_segments'] = [
                [list(raw), lease.as_evidence()] for raw, lease in self.trigger_segments
            ]
        return evidence

    @classmethod
    def from_evidence(cls, evidence: dict) -> 'CompletedCapture':
        raw_segments = evidence.get('trigger_segments')
        trigger_segments = None
        if raw_segments is not None:
            trigger_segments = tuple(
                (tuple(raw), SlotLease(**lease)) for raw, lease in raw_segments
            )
        return cls(
            lease=SlotLease(**evidence['lease']),
            shift=evidence['shift'],
            procedure=ProcedureRevision.from_evidence(evidence['procedure']),
            waveform=tuple(evidence['waveform']),
            multiplier=evidence['multiplier'],
            waveform_source=RegionSource.from_evidence(evidence['waveform_source']),
            range_source=RegionSource.from_evidence(evidence['range_source']),
            trigger_segments=trigger_segments,
        )

    def event_for(self, region: str) -> RegionReady:
        if region == 'waveform':
            return RegionReady(self.waveform_source, self.waveform)
        if region == 'range':
            return RegionReady(self.range_source, self.multiplier)
        raise ValueError('unknown region')

    def accepts_event(self, event: RegionReady) -> bool:
        if self.trigger_segments is None:
            return self.event_for(event.source.region).matches(event)
        if event.source.region == 'range':
            return RegionReady(self.range_source, self.multiplier).matches(event)
        for raw, lease in self.trigger_segments:
            if RegionReady(RegionSource(lease, 'waveform'), raw).matches(event):
                return True
        return False


def validate_region(registration: CaptureRegistration, event: RegionReady) -> None:
    if not isinstance(event, RegionReady) or not isinstance(event.source, RegionSource):
        raise ValueError('invalid device feedback')
    if event.source.lease != registration.lease:
        raise ValueError('feedback belongs to a different capture instance')
    if event.source.region == 'waveform':
        require_waveform(event.value)
    else:
        require_allowed_multiplier(registration.procedure.limits, event.value)


def assemble_capture(registration: CaptureRegistration,
                     regions: Mapping[str, RegionReady]) -> CompletedCapture | None:
    for region, event in regions.items():
        validate_region(registration, event)
        if event.source.region != region:
            raise ValueError('feedback is indexed under the wrong region')
    if regions.keys() != set(REGIONS):
        return None
    waveform, scale = regions['waveform'], regions['range']
    return CompletedCapture(registration.lease, registration.shift, registration.procedure,
                            waveform.value, scale.value, waveform.source, scale.source)
