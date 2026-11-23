from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class EngineeringUnit:
    symbol: str
    micrometres_per_second: int

    def comparison_value(self, magnitude: int) -> int:
        return magnitude * self.micrometres_per_second


VIBRATION_UNITS = MappingProxyType({
    'mm/s': EngineeringUnit('mm/s', 1000),
    'µm/s': EngineeringUnit('µm/s', 1),
})


def resolve_unit(symbol: str) -> EngineeringUnit:
    if not isinstance(symbol, str) or symbol not in VIBRATION_UNITS:
        raise ValueError('unsupported vibration unit')
    return VIBRATION_UNITS[symbol]
