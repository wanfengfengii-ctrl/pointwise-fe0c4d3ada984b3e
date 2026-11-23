from dataclasses import dataclass


def validate_sampling_rate(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError('sampling rate must be a positive integer')
    return value


@dataclass(frozen=True, slots=True)
class AcquisitionLimits:
    sampling_rate: int
    multipliers: tuple[int, ...]

    def __post_init__(self) -> None:
        validate_sampling_rate(self.sampling_rate)
        if not isinstance(self.multipliers, (tuple, list)) or not self.multipliers:
            raise ValueError('at least one multiplier is required')
        if any(type(value) is not int or value <= 0 for value in self.multipliers):
            raise ValueError('allowed multipliers must be positive integers')
        if len(set(self.multipliers)) != len(self.multipliers):
            raise ValueError('duplicate allowed multiplier')
        object.__setattr__(self, 'multipliers', tuple(self.multipliers))


def require_allowed_multiplier(limits: AcquisitionLimits, value: int) -> int:
    if type(value) is not int or value not in limits.multipliers:
        raise ValueError('multiplier is outside the frozen procedure limits')
    return value
