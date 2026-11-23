from dataclasses import dataclass

from capture.completion import CompletedCapture, require_waveform, verify_capture_sources
from capture.custody import HeldCapture
from recipes.limits import require_allowed_multiplier
from recipes.revisions import require_name
from recipes.units import EngineeringUnit


@dataclass(frozen=True, slots=True)
class ReductionInput:
    evidence: CompletedCapture

    @property
    def waveform(self) -> tuple[int, ...]:
        return self.evidence.waveform

    @property
    def multiplier(self) -> int:
        return self.evidence.multiplier

    @property
    def unit(self) -> EngineeringUnit:
        return self.evidence.procedure.unit


def build_reduction_input(number: int, held: HeldCapture) -> ReductionInput:
    evidence = held.capture
    if evidence.lease.capture_id != number:
        raise ValueError('custody record is for a different capture')
    require_name(evidence.shift, 'source shift')
    require_waveform(evidence.waveform)
    require_allowed_multiplier(evidence.procedure.limits, evidence.multiplier)
    verify_capture_sources(evidence.lease, evidence.waveform, evidence.waveform_source,
                           evidence.range_source, evidence.trigger_segments)
    return ReductionInput(evidence)
