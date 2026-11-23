def scale_waveform(waveform: tuple[int, ...], multiplier: int) -> tuple[int, ...]:
    if type(multiplier) is not int or multiplier <= 0:
        raise ValueError('scale requires a positive integer multiplier')
    if not waveform or any(type(sample) is not int for sample in waveform):
        raise ValueError('scale requires a nonempty integer waveform')
    return tuple(sample * multiplier for sample in waveform)
