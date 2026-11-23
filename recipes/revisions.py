from dataclasses import dataclass

from recipes.limits import AcquisitionLimits
from recipes.units import EngineeringUnit, resolve_unit


def require_name(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be nonblank text')
    return value


@dataclass(frozen=True, slots=True)
class ProcedureRevision:
    name: str
    revision: str
    channel: str
    unit: EngineeringUnit
    limits: AcquisitionLimits

    def __post_init__(self) -> None:
        require_name(self.name, 'procedure')
        require_name(self.revision, 'revision')
        require_name(self.channel, 'channel')
        if not isinstance(self.unit, EngineeringUnit) or self.unit != resolve_unit(self.unit.symbol):
            raise ValueError('invalid engineering unit')
        if not isinstance(self.limits, AcquisitionLimits):
            raise ValueError('invalid acquisition limits')

    @property
    def key(self) -> tuple[str, str]:
        return self.name, self.revision

    def as_evidence(self) -> dict:
        return {
            'name': self.name,
            'revision': self.revision,
            'channel': self.channel,
            'sampling_rate': self.limits.sampling_rate,
            'multipliers': list(self.limits.multipliers),
            'unit': self.unit.symbol,
            'unit_ratio': self.unit.micrometres_per_second,
        }

    @classmethod
    def from_evidence(cls, evidence: dict) -> 'ProcedureRevision':
        revision = build_revision(
            name=evidence['name'], revision=evidence['revision'], channel=evidence['channel'],
            sampling_rate=evidence['sampling_rate'], multipliers=evidence['multipliers'],
            unit=evidence['unit'],
        )
        ratio = evidence['unit_ratio']
        if type(ratio) is not int or ratio != revision.unit.micrometres_per_second:
            raise ValueError('invalid frozen unit ratio')
        return revision


def build_revision(*, name: str, revision: str, channel: str, sampling_rate: int,
                   multipliers: list[int] | tuple[int, ...], unit: str) -> ProcedureRevision:
    return ProcedureRevision(name, revision, channel, resolve_unit(unit),
                             AcquisitionLimits(sampling_rate, multipliers))
