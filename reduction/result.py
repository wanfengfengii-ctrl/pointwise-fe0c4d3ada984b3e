from dataclasses import dataclass

from capture.completion import CompletedCapture
from recipes.units import EngineeringUnit
from reduction.inputs import ReductionInput
from reduction.peak import absolute_peak
from reduction.scaling import scale_waveform


@dataclass(frozen=True, slots=True)
class ReducedResult:
    evidence: CompletedCapture
    scaled: tuple[int, ...]
    peak: int

    @property
    def unit(self) -> EngineeringUnit:
        return self.evidence.procedure.unit

    def as_archive(self) -> dict:
        return {
            'evidence': self.evidence.as_evidence(),
            'scaled': list(self.scaled),
            'peak': self.peak,
        }

    @classmethod
    def from_archive(cls, archive: dict) -> 'ReducedResult':
        return cls(CompletedCapture.from_evidence(archive['evidence']),
                   tuple(archive['scaled']), archive['peak'])


def reduce_capture(inputs: ReductionInput) -> ReducedResult:
    scaled = scale_waveform(inputs.waveform, inputs.multiplier)
    return ReducedResult(inputs.evidence, scaled, absolute_peak(scaled))
