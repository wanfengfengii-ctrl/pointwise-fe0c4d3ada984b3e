import json
from dataclasses import dataclass
from pathlib import Path

from bench.writer import REGIONS, is_device_scalar
from recipes.revisions import require_name


@dataclass(frozen=True, slots=True)
class RecordingStep:
    operation: str
    label: str
    region: str | None = None
    value: object = None


@dataclass(frozen=True, slots=True)
class RecordingPlan:
    shift: str
    channel: str
    steps: tuple[RecordingStep, ...]

    @classmethod
    def read(cls, path: Path) -> 'RecordingPlan':
        return parse_recording(json.loads(path.read_text(encoding='utf-8')))


def parse_recording(document: dict) -> RecordingPlan:
    if not isinstance(document, dict) or document.keys() != {'shift', 'channel', 'steps'}:
        raise ValueError('recording requires shift, channel and steps')
    shift = require_name(document['shift'], 'shift')
    channel = require_name(document['channel'], 'channel')
    if not isinstance(document['steps'], list):
        raise ValueError('recording steps must be a list')

    writes: dict[str, set[str]] = {}
    steps = []
    for position, fields in enumerate(document['steps'], start=1):
        try:
            steps.append(_parse_step(fields, writes))
        except ValueError as error:
            raise ValueError(f'recording step {position}: {error}') from error
    return RecordingPlan(shift, channel, tuple(steps))


def _parse_step(fields: dict, writes: dict[str, set[str]]) -> RecordingStep:
    if not isinstance(fields, dict):
        raise ValueError('step must be an object')
    operation = fields.get('op')
    label = require_name(fields.get('label'), 'capture label')
    common = {'op', 'label'}
    if operation == 'begin':
        if fields.keys() != common or label in writes:
            raise ValueError('invalid or repeated begin')
        writes[label] = set()
        return RecordingStep(operation, label)
    if label not in writes:
        raise ValueError('capture label must first be allocated')
    if operation == 'deliver':
        region = fields.get('region')
        if fields.keys() != common | {'region'} or region not in REGIONS or region not in writes[label]:
            raise ValueError('delivery must refer to an earlier region write')
        return RecordingStep(operation, label, region)
    if operation == 'waveform':
        values = fields.get('values')
        if fields.keys() != common | {'values'} or not isinstance(values, list):
            raise ValueError('waveform step requires a values list')
        if not all(map(is_device_scalar, values)):
            raise ValueError('waveform readings must be scalar')
        value = tuple(values)
    elif operation == 'range':
        value = fields.get('multiplier')
        if fields.keys() != common | {'multiplier'} or not is_device_scalar(value):
            raise ValueError('range step requires a scalar multiplier')
    else:
        raise ValueError('unknown recording operation')
    if operation in writes[label]:
        raise ValueError('write a region once and replay its delivery instead')
    writes[label].add(operation)
    return RecordingStep(operation, label, operation, value)
