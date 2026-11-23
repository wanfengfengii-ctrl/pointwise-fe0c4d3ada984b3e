from dataclasses import dataclass

from bench.slots import SlotLease
from bench.writer import RegionSource
from capture.completion import require_waveform, verify_sources
from handover.ledger import IssuedRecord
from recipes.limits import require_allowed_multiplier
from recipes.revisions import ProcedureRevision, require_name


@dataclass(frozen=True, slots=True)
class IssuedEvidence:
    lease: SlotLease
    shift: str
    procedure: ProcedureRevision
    waveform: tuple[int, ...]
    multiplier: int
    waveform_source: RegionSource
    range_source: RegionSource


def project_issued_evidence(record: IssuedRecord) -> IssuedEvidence:
    original = record.result.evidence
    return IssuedEvidence(original.lease, original.shift, original.procedure, original.waveform,
                          original.multiplier, original.waveform_source, original.range_source)


def verify_provenance(record: IssuedRecord, requested_number: int) -> IssuedEvidence:
    evidence = project_issued_evidence(record)
    if evidence.lease.capture_id != requested_number or record.receipt.capture_id != requested_number:
        raise ValueError('archived evidence or receipt belongs to a different capture')
    require_name(evidence.shift, 'source shift')
    verify_sources(evidence.lease, evidence.waveform_source, evidence.range_source)
    require_waveform(evidence.waveform)
    require_allowed_multiplier(evidence.procedure.limits, evidence.multiplier)
    return evidence
