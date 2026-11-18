import copy
import json
import sqlite3
import tempfile
import unittest
from fractions import Fraction as F
from pathlib import Path

from tests.test_sources import fixture, fail_at
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import RevisionRepository
from surface.fields.bindings import bind_fields
from surface.tasks.plans import PlanService, STITCHED_CONVENTION
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.checking import check_export, check_result_payload
from surface.results.codec import decode_payload, canonical_digest
from stitched_cases import cases, parallel, zero_fields, strip


def empty_store():
    store = ArchiveStore.open()
    return store, SourceCatalog(store)


def stitched_source(catalog, name="raw", case=None):
    case = parallel() if case is None else case
    raw = catalog.put_source(name, case["mesh"], case["fields"])
    fixed = catalog.build_stitched(raw["revision_id"], case["seams"])
    return raw, fixed, case


class TestStitchedBuild(unittest.TestCase):
    def setUp(self):
        self.store, self.catalog = empty_store()
        self.addCleanup(self.store.close)

    def test_parallel_hand_expectation_and_edge_identities(self):
        raw, fixed, case = stitched_source(self.catalog)
        self.assertNotEqual(fixed["revision_id"], raw["revision_id"])
        self.assertEqual(self.catalog.current_revision(), fixed["revision_id"])
        stored = self.catalog.read_source(fixed["revision_id"])
        self.assertEqual(set(stored), {"revision_id", "fingerprint", "mesh", "fields", "stitch"})
        mesh = stored["mesh"]
        self.assertEqual(sorted(mesh["nodes"]), ["A", "B", "C"])
        self.assertEqual(mesh["edges"], [("A", "B"), ("A", "C"), ("B", "C"), ("X", "Z")])
        self.assertEqual(mesh["edge_endpoints"],
                         {("A", "B"): ("A", "B"), ("A", "C"): ("A", "C"),
                          ("B", "C"): ("B", "C"), ("X", "Z"): ("A", "C")})
        self.assertEqual(mesh["faces"], {"f1": ("A", "B", "C"), "f2": ("A", "C", "B")})
        stitch = stored["stitch"]
        self.assertEqual(stitch["source_revision"], raw["revision_id"])
        self.assertEqual(stitch["source_digest"], raw["fingerprint"])
        self.assertEqual(stitch["seams"],
                         [(("A", "B"), ("X", "Y")), (("B", "C"), ("Y", "Z"))])
        self.assertEqual(stitch["node_map"],
                         {"A": "A", "B": "B", "C": "C", "X": "A", "Y": "B", "Z": "C"})
        self.assertEqual(stitch["face_map"]["f2"], {"sign": -1, "corners": (0, 2, 1)})
        self.assertEqual(stitch["edge_map"][("X", "Z")], {"edge": ("X", "Z"), "sign": 1})
        self.assertEqual(stitch["edge_map"][("X", "Y")], {"edge": ("A", "B"), "sign": 1})
        self.assertEqual(canonical_digest(stitch["source_input"]), stitch["source_digest"])
        self.assertEqual(fixed["fingerprint"], canonical_digest(
            {"mesh": mesh, "fields": stored["fields"], "stitch": stitch}))

    def test_analysis_follows_source_corner_order(self):
        raw, fixed, case = stitched_source(self.catalog)
        plan = PlanService(self.store).create_plan("qc", fixed["revision_id"], case["samples"])
        self.assertEqual(PlanService(self.store).read_plan(plan).convention, STITCHED_CONVENTION)
        result = ResultReader(self.store).read_result(AnalysisService(self.store).execute(plan))
        payload = result["payload"]
        self.assertEqual(payload["kind"], "stitched-analysis-v1")
        self.assertEqual(payload["binding"]["convention"], "stitched-surface-v1")
        self.assertEqual(payload["binding"]["revision_id"], fixed["revision_id"])
        self.assertEqual(payload["input"]["stitch"], self.catalog.read_source(
            fixed["revision_id"])["stitch"])
        self.assertEqual(payload["circulations"], {"f1": F(1), "f2": F(2)})
        self.assertEqual(payload["balance"]["boundary_total"], F(3))
        self.assertEqual(payload["balance"]["internal_total"], F(0))
        self.assertEqual(payload["balance"]["gap_total"], F(6))
        self.assertEqual(payload["samples"][0]["value"], F(4, 3))
        self.assertEqual(payload["samples"][0]["position"], (F(1, 3), F(1, 6), F(0)))
        self.assertEqual(payload["samples"][0]["weights"], (F(1, 2), F(1, 3), F(1, 6)))
        check_result_payload(payload)
        raw_export = ResultExporter(ResultReader(self.store)).export_result(result["result_id"])
        self.assertEqual(check_export(raw_export).boundary_total, F(3))

    def test_parallel_edges_stay_independent(self):
        raw, fixed, case = stitched_source(self.catalog)
        revision = RevisionRepository(self.store).read(fixed["revision_id"])
        bundle = bind_fields(fixed["revision_id"], revision.mesh, revision.fields)
        with self.assertRaises(ValueError):
            bundle.edge_value("A", "C")
        self.assertEqual(bundle.edge_value("A", "C", edge_id=("A", "C")), 2)
        self.assertEqual(bundle.edge_value("A", "C", edge_id=("X", "Z")), 5)
        self.assertEqual(bundle.edge_value("C", "A", edge_id=("X", "Z")), -5)
        with self.assertRaises(KeyError):
            bundle.edge_value("A", "Q")
        with self.assertRaises(ValueError):
            bundle.edge_value("A", "A")
        with self.assertRaises(ValueError):
            bundle.edge_value("A", "B", edge_id=("A", "C"))
        with self.assertRaises(KeyError):
            bundle.edge_value("A", "B", edge_id=("Q", "R"))
        ordinary = RevisionRepository(self.store).read(raw["revision_id"])
        plain = bind_fields(raw["revision_id"], ordinary.mesh, ordinary.fields)
        with self.assertRaises(ValueError):
            plain.edge_value("A", "B", edge_id=("A", "B"))
        self.assertEqual(plain.edge_value("B", "A"), -1)

    def test_reversed_and_cylinder(self):
        for name in ("reversed", "cylinder"):
            case = cases()[name]
            raw = self.catalog.put_source(name, case["mesh"], case["fields"])
            fixed = self.catalog.build_stitched(raw["revision_id"], case["seams"])
            stored = self.catalog.read_source(fixed["revision_id"])
            self.assertEqual(len(stored["mesh"]["nodes"]), case["expected"]["vertices"])
            self.assertEqual(len(stored["mesh"]["edges"]), case["expected"]["edges"])
            if name == "reversed":
                self.assertEqual(stored["mesh"]["edges"],
                                 [("A", "B"), ("A", "C"), ("B", "C"), ("Y", "Z")])
                revision = RevisionRepository(self.store).read(fixed["revision_id"])
                bundle = bind_fields(fixed["revision_id"], revision.mesh, revision.fields)
                self.assertEqual(bundle.edge_value("A", "C", edge_id=("Y", "Z")), 5)
                self.assertEqual(bundle.edge_value("C", "A", edge_id=("Y", "Z")), -5)
            plan = PlanService(self.store).create_plan(name, fixed["revision_id"], case["samples"])
            payload = ResultReader(self.store).read_result(
                AnalysisService(self.store).execute(plan))["payload"]
            check_result_payload(payload)
            raw_export = ResultExporter(ResultReader(self.store)).export_result(
                payload and AnalysisService(self.store).execute(plan))
            check_export(raw_export)
            if name == "reversed":
                self.assertEqual(payload["circulations"], {"f1": F(1), "f2": F(2)})
                self.assertEqual(payload["samples"][0]["value"], F(4, 3))

    def test_empty_seams_and_current_revision(self):
        mesh, fields, _ = fixture()
        raw = self.catalog.put_source("plain", mesh, fields)
        fixed = self.catalog.build_stitched(raw["revision_id"], [])
        self.assertEqual(self.catalog.current_revision(), fixed["revision_id"])
        stored = self.catalog.read_source(fixed["revision_id"])
        self.assertEqual(stored["stitch"]["seams"], [])
        self.assertEqual(stored["mesh"]["faces"], {"f1": ("A", "B", "C"),
                                                   "f2": ("B", "D", "C")})
        self.assertEqual(stored["fields"], fields)
        self.assertEqual(self.catalog.read_source(raw["revision_id"]), {
            "revision_id": raw["revision_id"], "fingerprint": raw["fingerprint"],
            "mesh": stored["stitch"]["source_input"]["mesh"],
            "fields": raw["fingerprint"] and stored["stitch"]["source_input"]["fields"]})
        plan = PlanService(self.store).create_plan("plain-plan", fixed["revision_id"], [])
        payload = ResultReader(self.store).read_result(
            AnalysisService(self.store).execute(plan))["payload"]
        self.assertEqual(payload["circulations"], {"f1": F(62, 105), "f2": F(37, 70)})

    def test_errors_and_immutability(self):
        raw, fixed, case = stitched_source(self.catalog)
        before = {t: self.store.connection.execute(
            "SELECT * FROM " + t + " ORDER BY 1").fetchall()
            for t in ("source_revisions", "source_requests", "source_head")}
        with self.assertRaises(ValueError):
            self.catalog.build_stitched(fixed["revision_id"], [])
        with self.assertRaises(KeyError):
            self.catalog.build_stitched(9999, [])
        with self.assertRaises(KeyError):
            self.catalog.build_stitched("nope", [])
        with self.assertRaises(ValueError):
            self.catalog.build_stitched(raw["revision_id"], "not-a-sequence")
        bad_seams = [
            [(("A", "B"), ("X", "Y")), (("B", "C"), ("A", "B"))],          # repeated
            [(("A", "B"), ("Y", "Z"))],                                    # wrong coordinates
            [(("A", "B"), ("X", "Y"), ("Z", "X"))],                        # wrong arity
            [(("A", "B"), ("A", "B"))],                                    # same edge
            [((("A",), "B"), (("X",), "Y"))],                              # malformed side
            [(("A", "A"), ("X", "Y"))],                                    # loop side
            [(("A", "Z"), ("X", "Y"))],                                    # unknown endpoint
        ]
        for seams in bad_seams:
            with self.assertRaises(ValueError):
                self.catalog.build_stitched(raw["revision_id"], seams)
        after = {t: self.store.connection.execute(
            "SELECT * FROM " + t + " ORDER BY 1").fetchall()
            for t in ("source_revisions", "source_requests", "source_head")}
        self.assertEqual(before, after)
        # input mutation must not reach the persisted revision
        mutable = parallel()
        saved = self.catalog.build_stitched(
            self.catalog.put_source("mutable", mutable["mesh"], mutable["fields"])["revision_id"],
            mutable["seams"])
        snapshot = self.catalog.read_source(saved["revision_id"])
        mutable["mesh"]["nodes"]["A"] = (F(9), F(9), F(9))
        mutable["fields"]["node_scalars"]["A"] = F(99)
        mutable["seams"].clear()
        self.assertEqual(self.catalog.read_source(saved["revision_id"]), snapshot)
        detached = self.catalog.read_source(saved["revision_id"])
        detached["mesh"]["nodes"]["A"] = (F(1), F(2), F(3))
        detached["stitch"]["node_map"]["A"] = "Q"
        self.assertEqual(self.catalog.read_source(saved["revision_id"]), snapshot)

    def test_conflicts_and_topology_rejections(self):
        for name in ("node_conflict", "edge_conflict", "pinched", "mobius"):
            case = cases()[name]
            raw = self.catalog.put_source(name, case["mesh"], case["fields"])
            before = self.store.connection.execute("SELECT COUNT(*) FROM source_revisions").fetchone()
            with self.assertRaises(ValueError):
                self.catalog.build_stitched(raw["revision_id"], case["seams"])
            after = self.store.connection.execute("SELECT COUNT(*) FROM source_revisions").fetchone()
            self.assertEqual(before, after)
            self.assertEqual(self.catalog.current_revision(), raw["revision_id"])

    def test_isolated_vertex_rejected(self):
        mesh = {"nodes": {"A": (F(0), F(0), F(0)), "B": (F(1), F(0), F(0)),
                          "C": (F(0), F(1), F(0)), "Z": (F(5), F(5), F(5))},
                "faces": {"f1": ("A", "B", "C")}}
        fields = {"node_scalars": {k: F(0) for k in mesh["nodes"]},
                  "edge_integrals": {("A", "B"): F(0), ("A", "C"): F(0),
                                     ("B", "C"): F(0)},
                  "face_totals": {"f1": F(0)}}
        raw = self.catalog.put_source("isolated", mesh, fields)
        with self.assertRaises(ValueError):
            self.catalog.build_stitched(raw["revision_id"], [])

    def test_component_anchors_are_independent(self):
        # f1 (component 1) has a lower ID than f2, but component 2 is anchored on f3.
        nodes = {"a": (F(0), F(0), F(0)), "b": (F(1), F(0), F(0)),
                 "c": (F(0), F(1), F(0)), "d": (F(0), F(0), F(1)),
                 "e": (F(-1), F(0), F(0)), "f": (F(0), F(-1), F(0))}
        mesh = {"nodes": nodes,
                "faces": {"f1": ("a", "b", "c"), "f3": ("d", "e", "f")}}
        fields = zero_fields(mesh)
        raw = self.catalog.put_source("components", mesh, fields)
        fixed = self.catalog.build_stitched(raw["revision_id"], [])
        stored = self.catalog.read_source(fixed["revision_id"])
        self.assertEqual(stored["mesh"]["faces"], mesh["faces"])
        self.assertEqual(stored["stitch"]["face_map"]["f1"]["sign"], 1)
        self.assertEqual(stored["stitch"]["face_map"]["f3"]["sign"], 1)

    def test_stitched_publication_rollback_and_replay(self):
        raw, fixed, case = stitched_source(self.catalog)
        plan = PlanService(self.store).create_plan("qc", fixed["revision_id"], case["samples"])
        analysis = AnalysisService(self.store)
        tables = ("source_revisions", "source_requests", "source_head",
                  "analysis_plans", "analysis_results", "completion_receipts")
        initial = {t: self.store.connection.execute("SELECT * FROM " + t + " ORDER BY 1").fetchall()
                   for t in tables}
        for stage in ("compute_after_samples", "publication_after_result",
                      "publication_after_receipt"):
            self.store.fault = fail_at(stage)
            with self.assertRaises(sqlite3.OperationalError):
                analysis.execute(plan)
            self.assertEqual({t: self.store.connection.execute(
                "SELECT * FROM " + t + " ORDER BY 1").fetchall() for t in tables}, initial)
        self.store.fault = None
        result_id = analysis.execute(plan)
        self.assertEqual(analysis.execute(plan), result_id)
        self.store.fault = fail_at("compute_after_samples")
        self.assertEqual(analysis.execute(plan), result_id)
        self.store.fault = None
        # V2 result delivery must not read source or plan tables.
        denied = []
        forbidden = {"source_revisions", "source_requests", "source_head", "analysis_plans"}
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table in forbidden:
                denied.append((table, column))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.connection.set_authorizer(authorize)
        try:
            reader = ResultReader(self.store)
            saved = reader.read_result(result_id)
            exported = ResultExporter(reader).export_result(result_id)
        finally:
            self.store.connection.set_authorizer(None)
        self.assertEqual(denied, [])
        check_export(exported)
        self.assertEqual(saved["payload"]["kind"], "stitched-analysis-v1")

    def test_read_and_export_never_restich(self):
        import surface.mesh.stitch as stitch_module
        raw, fixed, case = stitched_source(self.catalog)
        plan = PlanService(self.store).create_plan("qc", fixed["revision_id"], case["samples"])
        result_id = AnalysisService(self.store).execute(plan)
        original = ResultReader(self.store).read_result(result_id)
        original_export = ResultExporter(ResultReader(self.store)).export_result(result_id)
        snapshot = self.catalog.read_source(fixed["revision_id"])

        def forbidden(*args, **kwargs):
            raise AssertionError("read path must not rebuild the quotient")

        saved = stitch_module.build_stitched_content
        stitch_module.build_stitched_content = forbidden
        try:
            self.assertEqual(self.catalog.read_source(fixed["revision_id"]), snapshot)
            reader = ResultReader(self.store)
            self.assertEqual(reader.read_result(result_id), original)
            self.assertEqual(ResultExporter(reader).export_result(result_id), original_export)
        finally:
            stitch_module.build_stitched_content = saved

    def test_corrupt_stored_revision_is_reported(self):
        raw, fixed, case = stitched_source(self.catalog)
        row = self.store.connection.execute(
            "SELECT payload FROM source_revisions WHERE id=?",
            (fixed["revision_id"],)).fetchone()
        envelope = decode_payload(row[0])
        envelope["stitch"]["node_map"]["X"] = "C"
        from surface.results.codec import encode_payload
        broken = encode_payload(envelope)
        digest = canonical_digest(envelope)
        self.store.connection.execute(
            "UPDATE source_revisions SET fingerprint=?,payload=? WHERE id=?",
            (digest, broken, fixed["revision_id"]))
        with self.assertRaises(ValueError):
            self.catalog.read_source(fixed["revision_id"])
        self.store.connection.execute(
            "UPDATE source_revisions SET fingerprint=?,payload=? WHERE id=?",
            (fixed["fingerprint"], row[0], fixed["revision_id"]))
        self.assertEqual(self.catalog.read_source(fixed["revision_id"])["revision_id"],
                         fixed["revision_id"])

    def test_old_and_derived_sources_coexist(self):
        mesh, fields, samples = fixture()
        raw = self.catalog.put_source("old", mesh, fields)
        fixed = self.catalog.build_stitched(raw["revision_id"], [])
        old_plan = PlanService(self.store).create_plan("old-plan", raw["revision_id"], samples)
        new_plan = PlanService(self.store).create_plan("new-plan", fixed["revision_id"], samples)
        old_payload = ResultReader(self.store).read_result(
            AnalysisService(self.store).execute(old_plan))["payload"]
        new_payload = ResultReader(self.store).read_result(
            AnalysisService(self.store).execute(new_plan))["payload"]
        self.assertEqual(old_payload["kind"], "ordinary-analysis-v1")
        self.assertEqual(new_payload["kind"], "stitched-analysis-v1")
        self.assertEqual(old_payload["input"]["mesh"]["edges"], solved_edges(mesh))
        self.assertNotIn("stitch", old_payload["input"])
        self.assertEqual(old_payload["circulations"], new_payload["circulations"])

    def test_asymmetric_and_duplicate_sampling_preserved(self):
        raw, fixed, case = stitched_source(self.catalog)
        queue = [
            {"face": "f2", "weights": (F(1, 6), F(1, 2), F(1, 3))},
            {"face": "f1", "weights": (F(1, 2), F(1, 3), F(1, 6))},
            {"face": "f1", "weights": (F(1, 2), F(1, 3), F(1, 6))},
            {"face": "f2", "weights": (F(1, 6), F(1, 2), F(1, 3))},
        ]
        plan = PlanService(self.store).create_plan("asym", fixed["revision_id"], queue)
        payload = ResultReader(self.store).read_result(
            AnalysisService(self.store).execute(plan))["payload"]
        self.assertEqual(payload["requests"], queue)
        self.assertEqual([item["weights"] for item in payload["samples"]],
                         [item["weights"] for item in queue])
        # f2 source corners are (X, Y, Z) with scalars 0, 2, 4; f1 (A,B,C) same.
        self.assertEqual([item["value"] for item in payload["samples"]],
                         [F(7, 3), F(4, 3), F(4, 3), F(7, 3)])
        self.assertEqual(payload["samples"][0]["position"],
                         (F(1, 2), F(1, 3), F(0)))
        self.assertEqual(payload["samples"][1]["position"], (F(1, 3), F(1, 6), F(0)))

    def test_failed_publication_leaves_no_revision(self):
        mesh, fields, _ = fixture()
        raw = self.catalog.put_source("fault", mesh, fields)
        before = {t: self.store.connection.execute(
            "SELECT * FROM " + t + " ORDER BY 1").fetchall()
            for t in ("source_revisions", "source_head")}
        for stage in ("source_after_revision", "source_after_head"):
            self.store.fault = fail_at(stage)
            with self.assertRaises(sqlite3.OperationalError):
                self.catalog.build_stitched(raw["revision_id"], [])
            self.assertEqual({t: self.store.connection.execute(
                "SELECT * FROM " + t + " ORDER BY 1").fetchall()
                for t in ("source_revisions", "source_head")}, before)
        self.store.fault = None
        retry = self.catalog.build_stitched(raw["revision_id"], [])
        self.assertEqual(self.catalog.current_revision(), retry["revision_id"])
        # the entry has no replay protocol: a further success appends anew
        again = self.catalog.build_stitched(raw["revision_id"], [])
        self.assertNotEqual(again["revision_id"], retry["revision_id"])
        self.assertEqual(self.catalog.current_revision(), again["revision_id"])
        one, two = (self.catalog.read_source(item["revision_id"])
                    for item in (retry, again))
        for record in (one, two):
            record.pop("revision_id")
        self.assertEqual(one, two)


def solved_edges(mesh):
    edges = {tuple(sorted((v[i], v[(i + 1) % 3]))) for v in mesh["faces"].values()
             for i in range(3)}
    return sorted(edges)


class TestStitchedPersistence(unittest.TestCase):
    def test_disk_reopen_correction_and_offline(self):
        with tempfile.TemporaryDirectory(prefix="stitched-") as directory:
            path = Path(directory) / "archive.sqlite"
            store = ArchiveStore.open(path)
            try:
                catalog = SourceCatalog(store)
                case = parallel()
                raw = catalog.put_source("solver", case["mesh"], case["fields"])
                fixed = catalog.build_stitched(raw["revision_id"], case["seams"])
                plan = PlanService(store).create_plan("qc", fixed["revision_id"], case["samples"])
                result_id = AnalysisService(store).execute(plan)
                reader = ResultReader(store)
                original = reader.read_result(result_id)
                raw_export = ResultExporter(reader).export_result(result_id)
                source_snapshot = catalog.read_source(fixed["revision_id"])
                # upstream correction after the derived revision
                corrected = copy.deepcopy(case["fields"])
                corrected["node_scalars"]["A"] = F(100)
                corrected["node_scalars"]["X"] = F(100)
                catalog.put_source("solver-correction", case["mesh"], corrected)
                self.assertEqual(reader.read_result(result_id), original)
                self.assertEqual(catalog.read_source(fixed["revision_id"]), source_snapshot)
                # a second analysis of the same derived revision replays
                self.assertEqual(AnalysisService(store).execute(plan), result_id)
            finally:
                store.close()
            store = ArchiveStore.open(path)
            try:
                self.assertEqual(SourceCatalog(store).read_source(fixed["revision_id"]),
                                 source_snapshot)
                reader = ResultReader(store)
                self.assertEqual(reader.read_result(result_id), original)
                self.assertEqual(ResultExporter(reader).export_result(result_id), raw_export)
                self.assertEqual(AnalysisService(store).execute(plan), result_id)
                report = check_export(raw_export)
                self.assertEqual(report.boundary_total, F(3))
                self.assertEqual(report.gap_total, F(6))
                # the upstream correction is current; the derived revision is frozen
                self.assertNotEqual(SourceCatalog(store).current_revision(),
                                    fixed["revision_id"])
            finally:
                store.close()
            report = check_export(raw_export)
            self.assertEqual((report.faces, report.samples), (2, 1))
            exported = decode_payload(raw_export)["result"]
            self.assertEqual(exported["payload"]["kind"], "stitched-analysis-v1")
            self.assertEqual(exported["payload"]["input"]["stitch"]["source_input"]
                             ["fields"]["edge_integrals"][("X", "Z")], F(5))

    def test_offline_checker_rejects_tampering(self):
        store, catalog = empty_store()
        try:
            raw, fixed, case = stitched_source(catalog)
            plan = PlanService(store).create_plan("qc", fixed["revision_id"], case["samples"])
            result_id = AnalysisService(store).execute(plan)
            raw_export = ResultExporter(ResultReader(store)).export_result(result_id)
            for mutate in (
                    lambda p: p["input"]["stitch"]["node_map"].__setitem__("X", "B"),
                    lambda p: p["input"]["stitch"]["face_map"]["f2"].__setitem__("sign", 1),
                    lambda p: p["input"]["mesh"]["edges"].append(("Q", "R")),
                    lambda p: p["input"]["fields"]["face_totals"].__setitem__("f2", F(-2)),
                    lambda p: p["circulations"].__setitem__("f2", F(3)),
                    lambda p: p["samples"][0].__setitem__("value", F(5)),
            ):
                envelope = decode_payload(raw_export)
                mutate(envelope["result"]["payload"])
                envelope["result"]["fingerprint"] = canonical_digest(
                    envelope["result"]["payload"])
                tampered = _encode_envelope(envelope)
                with self.assertRaises(ValueError):
                    check_export(tampered)
        finally:
            store.close()


def _encode_envelope(envelope):
    from surface.results.codec import _pack
    import json as _json
    return _json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False,
                       default=None).encode("ascii") if False else _really_pack(envelope)


def _really_pack(envelope):
    from surface.results.codec import encode_payload
    return encode_payload({"export_kind": envelope["export_kind"],
                           "export_version": envelope["export_version"],
                           "result": envelope["result"]})


if __name__ == "__main__":
    unittest.main()
