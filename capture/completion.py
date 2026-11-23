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


@dataclass(frozen=True, slots=True)
class CompletedCapture:
    lease: SlotLease
    shift: str
    procedure: ProcedureRevision
    waveform: tuple[int, ...]
    multiplier: int
    waveform_source: RegionSource
    range_source: RegionSource

    def as_evidence(self) -> dict:
        return {
            'lease': self.lease.as_evidence(),
            'shift': self.shift,
            'procedure': self.procedure.as_evidence(),
            'waveform': list(self.waveform),
            'multiplier': self.multiplier,
            'waveform_source': self.waveform_source.as_evidence(),
            'range_source': self.range_source.as_evidence(),
        }

    @classmethod
    def from_evidence(cls, evidence: dict) -> 'CompletedCapture':
        return cls(
            lease=SlotLease(**evidence['lease']),
            shift=evidence['shift'],
            procedure=ProcedureRevision.from_evidence(evidence['procedure']),
            waveform=tuple(evidence['waveform']),
            multiplier=evidence['multiplier'],
            waveform_source=RegionSource.from_evidence(evidence['waveform_source']),
            range_source=RegionSource.from_evidence(evidence['range_source']),
        )

    def event_for(self, region: str) -> RegionReady:
        if region == 'waveform':
            return RegionReady(self.waveform_source, self.waveform)
        if region == 'range':
            return RegionReady(self.range_source, self.multiplier)
        raise ValueError('unknown region')


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
