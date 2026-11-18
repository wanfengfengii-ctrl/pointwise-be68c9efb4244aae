from dataclasses import dataclass
from surface.fields.bindings import bind_fields
from surface.mesh.barycentric import validate_sample
from surface.tasks.plans import CONVENTION, STITCHED_CONVENTION


@dataclass(frozen=True)
class ResolvedInputs:
    plan: object
    revision: object
    bundle: object


def resolve_inputs(plan, revisions):
    revision = revisions.read(plan.revision_id)
    if revision.fingerprint != plan.source_digest:
        raise ValueError("plan source digest mismatch")
    expected = STITCHED_CONVENTION if revision.stitch is not None else CONVENTION
    if plan.convention != expected:
        raise ValueError("plan convention does not match the frozen source")
    for sample in plan.samples:
        validate_sample(revision.mesh, sample)
    bundle = bind_fields(plan.revision_id, revision.mesh, revision.fields)
    return ResolvedInputs(plan, revision, bundle)
