def absolute_peak(waveform: tuple[int, ...]) -> int:
    if not waveform:
        raise ValueError('an empty waveform has no absolute peak')
    return max(abs(sample) for sample in waveform)
