"""Real stitched-surface demonstration: build, sample, correct, reopen, export."""
import json
import tempfile
from fractions import Fraction
from pathlib import Path

from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import RevisionRepository
from surface.fields.bindings import bind_fields
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.checking import check_export, demo as ordinary_demo
from stitched_cases import parallel


def _scenario(database):
    store = ArchiveStore.open(database)
    try:
        catalog = SourceCatalog(store)
        case = parallel()
        raw = catalog.put_source("solver-parallel", case["mesh"], case["fields"])
        derived = catalog.build_stitched(raw["revision_id"], case["seams"])
        fixed = catalog.read_source(derived["revision_id"])

        revision = RevisionRepository(store).read(derived["revision_id"])
        bundle = bind_fields(derived["revision_id"], revision.mesh, revision.fields)
        try:
            bundle.edge_value("A", "C")
        except ValueError:
            ambiguous = True
        else:
            ambiguous = False
        explicit = {"ac": str(bundle.edge_value("A", "C", edge_id=("A", "C"))),
                    "xz": str(bundle.edge_value("A", "C", edge_id=("X", "Z")))}

        plan = PlanService(store).create_plan("qc-parallel", derived["revision_id"],
                                              case["samples"])
        result_id = AnalysisService(store).execute(plan)
        reader = ResultReader(store)
        saved = reader.read_result(result_id)
        export = ResultExporter(reader).export_result(result_id)

        corrected = {name: dict(values) for name, values in case["fields"].items()}
        corrected["node_scalars"]["A"] = Fraction(10)
        corrected["node_scalars"]["X"] = Fraction(10)
        catalog.put_source("solver-correction", case["mesh"], corrected)
        derived_still = catalog.read_source(derived["revision_id"])
        current_after_correction = catalog.current_revision()
    finally:
        store.close()

    store = ArchiveStore.open(database)
    try:
        reopened_result = ResultReader(store).read_result(result_id)
        reopened_export = ResultExporter(ResultReader(store)).export_result(result_id)
    finally:
        store.close()

    report = check_export(reopened_export)
    payload = saved["payload"]
    return {
        "case_id": "v2.stitched_parallel",
        "derived_revision": derived["revision_id"],
        "source_revision": raw["revision_id"],
        "current_after_correction": current_after_correction,
        "quotient": {"vertices": len(fixed["mesh"]["nodes"]),
                     "edges": len(fixed["mesh"]["edges"]),
                     "faces": fixed["mesh"]["faces"]},
        "parallel_edge_needs_explicit_id": ambiguous,
        "explicit_integrals": explicit,
        "flipped_face": fixed["stitch"]["face_map"]["f2"],
        "expected": {"circulations": {"f1": "1", "f2": "2"},
                     "boundary": "3", "internal": "0", "gap": "6",
                     "sample": "4/3", "position": ["1/3", "1/6", "0"]},
        "actual": {"circulations": {k: str(v) for k, v in payload["circulations"].items()},
                   "boundary": str(report.boundary_total),
                   "internal": str(report.internal_total),
                   "gap": str(report.gap_total),
                   "sample": str(payload["samples"][0]["value"]),
                   "position": [str(x) for x in payload["samples"][0]["position"]]},
        "derived_revision_frozen": derived_still == fixed,
        "reopen_identical": reopened_result == saved and reopened_export == export,
        "code_paths": ["surface/mesh/stitch.py", "surface/sources/catalog.py",
                       "surface/fields/bindings.py", "surface/tasks/execution.py",
                       "surface/results/checking.py"],
        "oracle": "hand seam accounting with independent offline re-derivation",
    }


def main():
    ordinary_demo("samples/normal.json")
    with tempfile.TemporaryDirectory(prefix="stitched-demo-") as directory:
        scenario = _scenario(Path(directory) / "stitched.sqlite")
    assert scenario["actual"] == scenario["expected"], scenario
    assert scenario["parallel_edge_needs_explicit_id"]
    assert scenario["derived_revision_frozen"] and scenario["reopen_identical"]
    print("POINTWISE_SCENARIO " + json.dumps(scenario, sort_keys=True))


if __name__ == "__main__":
    main()
