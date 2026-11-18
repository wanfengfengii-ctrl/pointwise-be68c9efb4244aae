"""Smoke checks for STITCHING.md; independent admission adds contract cases."""
import argparse
import json
from copy import deepcopy
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import RevisionRepository
from surface.fields.bindings import bind_fields
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.checking import check_export
from stitched_cases import cases


def snapshot(store):
    return tuple(store.connection.iterdump())


def run(fixtures_only=False):
    observed = {}
    for name, case in cases().items():
        store = ArchiveStore.open()
        try:
            catalog = SourceCatalog(store)
            source = catalog.put_source(name, case["mesh"], case["fields"])
            if fixtures_only:
                observed[name] = {"raw_revision": source["revision_id"],
                                  "raw_faces": len(case["mesh"]["faces"])}
                continue
            before = snapshot(store)
            if case["expected"] == "reject":
                try:
                    catalog.build_stitched(source["revision_id"], case["seams"])
                except ValueError:
                    pass
                else:
                    raise AssertionError(name + " must be rejected")
                assert snapshot(store) == before
                observed[name] = "rejected without changes"
                continue
            receipt = catalog.build_stitched(source["revision_id"], case["seams"])
            fixed = catalog.read_source(receipt["revision_id"])
            mesh, expected = fixed["mesh"], case["expected"]
            assert len(mesh["nodes"]) == expected["vertices"]
            assert len(mesh["edges"]) == expected["edges"]
            plan = PlanService(store).create_plan(name, receipt["revision_id"], case["samples"])
            result_id = AnalysisService(store).execute(plan)
            reader = ResultReader(store)
            saved = reader.read_result(result_id)
            payload = saved["payload"]
            raw = ResultExporter(reader).export_result(result_id)
            check_export(raw)
            if "circulations" in expected:
                assert payload["circulations"] == expected["circulations"]
                assert payload["balance"]["boundary_total"] == expected["boundary"]
                assert payload["balance"]["internal_total"] == expected["internal"]
                assert payload["balance"]["gap_total"] == expected["gap"]
                assert payload["samples"][0]["value"] == expected["sample"]
                assert payload["samples"][0]["position"] == expected["position"]
                assert payload["samples"][0]["weights"] == case["samples"][0]["weights"]
                revision = RevisionRepository(store).read(receipt["revision_id"])
                bundle = bind_fields(revision.revision_id, revision.mesh, revision.fields)
                try:
                    bundle.edge_value("A", "C")
                except ValueError:
                    pass
                else:
                    raise AssertionError("parallel edge selection must be explicit")
                last = ("Y", "Z") if name == "reversed" else ("X", "Z")
                assert bundle.edge_value("A", "C", edge_id=last) == 5
            detached = deepcopy(saved)
            detached["payload"]["circulations"].clear()
            fixed["fields"]["node_scalars"].clear()
            changed = deepcopy(case["fields"])
            changed["face_totals"][next(iter(changed["face_totals"]))] += 1
            catalog.put_source(name + "-correction", case["mesh"], changed)
            assert reader.read_result(result_id) == saved
            assert ResultExporter(reader).export_result(result_id) == raw
            observed[name] = {"result": result_id,
                              "circulations": {k: str(v) for k, v in payload["circulations"].items()},
                              "boundary": str(payload["balance"]["boundary_total"])}
        finally:
            store.close()
    print(json.dumps({"fixture_only": fixtures_only, "observed": observed}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", action="store_true")
    args = parser.parse_args()
    run(args.fixtures)
