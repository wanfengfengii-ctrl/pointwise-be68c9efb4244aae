import argparse
import json
from dataclasses import dataclass
from fractions import Fraction
from surface.results.codec import decode_payload, canonical_digest


@dataclass(frozen=True)
class CheckReport:
    faces: int
    samples: int
    boundary_total: Fraction
    internal_total: Fraction
    gap_total: Fraction


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def check_result_payload(payload):
    try:
        if type(payload) is dict and payload.get("kind") == "stitched-analysis-v1":
            return _check_stitched(payload)
        return _check(payload)
    except (KeyError, TypeError, IndexError, AttributeError, ZeroDivisionError) as exc:
        raise ValueError("invalid offline quality record") from exc


def _check(p):
    _require(type(p) is dict and set(p) == {
        "kind", "binding", "input", "requests", "samples", "circulations",
        "face_totals", "balance"}, "invalid payload members")
    _require(p["kind"] == "ordinary-analysis-v1", "unsupported analysis")
    binding, source = p["binding"], p["input"]
    _require(type(binding) is dict and set(binding) == {
        "plan_id", "plan_digest", "revision_id", "source_digest", "convention"},
        "incomplete binding")
    _require(all(type(binding[k]) is int and binding[k] > 0
                 for k in ("plan_id", "revision_id")), "invalid binding IDs")
    _require(binding["convention"] == "ordinary-surface-v1", "unsupported convention")
    _require(type(source) is dict and set(source) == {"mesh", "fields"},
             "incomplete source snapshot")
    _require(canonical_digest(source) == binding["source_digest"], "source digest mismatch")
    mesh, fields = source["mesh"], source["fields"]
    _require(type(mesh) is dict and set(mesh) == {"nodes", "faces", "edges", "face_edges"},
             "invalid ordinary mesh")
    nodes, faces = mesh["nodes"], mesh["faces"]
    _require(type(nodes) is dict and bool(nodes) and type(faces) is dict
             and 1 <= len(faces) <= 60, "invalid geometry size")
    for node, xyz in nodes.items():
        _require(type(node) is str and bool(node) and type(xyz) is tuple
                 and len(xyz) == 3 and all(isinstance(x, Fraction) for x in xyz),
                 "invalid node coordinates")
    uses, face_terms = {}, {}
    for face, vertices in sorted(faces.items()):
        _require(type(face) is str and bool(face) and type(vertices) is tuple
                 and len(vertices) == 3 and all(type(v) is str and v in nodes for v in vertices)
                 and len(set(vertices)) == 3, "invalid face references")
        a, b, c = [nodes[node] for node in vertices]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2],
                 u[0]*v[1]-u[1]*v[0])
        _require(any(x != 0 for x in cross), "degenerate triangle")
        terms = []
        for i in range(3):
            start, end = vertices[i], vertices[(i + 1) % 3]
            edge = tuple(sorted((start, end)))
            sign = 1 if start < end else -1
            terms.append({"edge": edge, "sign": sign})
            uses.setdefault(edge, []).append(sign)
        face_terms[face] = terms
    _require(all(1 <= len(signs) <= 2 for signs in uses.values()), "nonmanifold edge")
    _require(mesh["edges"] == sorted(uses) and mesh["face_edges"] == face_terms,
             "saved incidence differs from original face winding")
    _require(type(fields) is dict and set(fields) == {
        "node_scalars", "edge_integrals", "face_totals"}, "invalid fields")
    for name, identities in (("node_scalars", nodes), ("edge_integrals", uses),
                             ("face_totals", faces)):
        _require(type(fields[name]) is dict and set(fields[name]) == set(identities)
                 and all(isinstance(x, Fraction) for x in fields[name].values()),
                 "inexact field coverage")
    requests = p["requests"]
    _require(type(requests) is list and type(p["samples"]) is list, "invalid sample sequence")
    expected_samples = []
    for request in requests:
        _require(type(request) is dict and set(request) == {"face", "weights"},
                 "invalid request")
        face, weights = request["face"], request["weights"]
        _require(type(face) is str and face in faces and type(weights) is tuple
                 and len(weights) == 3 and all(isinstance(w, Fraction) and w >= 0 for w in weights)
                 and sum(weights) == 1, "invalid exact barycentric weights")
        vertices = faces[face]
        position = tuple(sum((weights[i] * nodes[vertices[i]][axis] for i in range(3)),
                             Fraction(0)) for axis in range(3))
        value = sum((weights[i] * fields["node_scalars"][vertices[i]]
                     for i in range(3)), Fraction(0))
        expected_samples.append({"face": face, "weights": weights,
                                 "position": position, "value": value})
    for sample in p["samples"]:
        _require(type(sample) is dict and set(sample) == {"face", "weights", "position", "value"}
                 and isinstance(sample["value"], Fraction)
                 and type(sample["position"]) is tuple and len(sample["position"]) == 3
                 and all(isinstance(x, Fraction) for x in sample["position"]),
                 "nonrational sample evidence")
    _require(p["samples"] == expected_samples, "sample position/value/order mismatch")
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"], "samples": requests,
                    "convention": binding["convention"]}
    _require(canonical_digest(plan_binding) == binding["plan_digest"], "plan digest mismatch")
    integrals = fields["edge_integrals"]
    circulations = {face: sum((term["sign"] * integrals[term["edge"]]
                              for term in terms), Fraction(0))
                    for face, terms in face_terms.items()}
    _require(type(p["circulations"]) is dict
             and all(isinstance(x, Fraction) for x in p["circulations"].values())
             and p["circulations"] == circulations, "incorrect face circulation")
    totals = fields["face_totals"]
    _require(type(p["face_totals"]) is dict
             and all(isinstance(x, Fraction) for x in p["face_totals"].values())
             and p["face_totals"] == totals, "incorrect saved face totals")
    boundary, internal = [], []
    for edge, signs in sorted(uses.items()):
        coefficient = sum(signs)
        item = {"edge": edge, "coefficient": coefficient,
                "value": coefficient * integrals[edge]}
        (boundary if len(signs) == 1 else internal).append(item)
    bt = sum((item["value"] for item in boundary), Fraction(0))
    it = sum((item["value"] for item in internal), Fraction(0))
    ct, ft = sum(circulations.values(), Fraction(0)), sum(totals.values(), Fraction(0))
    expected_balance = {
        "face_gaps": {face: circulations[face] - totals[face] for face in sorted(faces)},
        "boundary_terms": boundary, "internal_terms": internal,
        "boundary_total": bt, "internal_total": it,
        "circulation_total": ct, "face_total": ft, "gap_total": ct - ft}
    actual_balance = p["balance"]
    _require(type(actual_balance) is dict and set(actual_balance) == set(expected_balance),
             "incomplete balance")
    for key in ("boundary_total", "internal_total", "circulation_total", "face_total", "gap_total"):
        _require(isinstance(actual_balance[key], Fraction), "nonrational balance")
    _require(type(actual_balance["face_gaps"]) is dict
             and all(isinstance(x, Fraction) for x in actual_balance["face_gaps"].values()),
             "nonrational face gaps")
    for key in ("boundary_terms", "internal_terms"):
        _require(type(actual_balance[key]) is list, "invalid ledger sequence")
        for item in actual_balance[key]:
            _require(type(item) is dict and set(item) == {"edge", "coefficient", "value"}
                     and type(item["coefficient"]) is int
                     and isinstance(item["value"], Fraction), "nonrational edge ledger")
    _require(actual_balance == expected_balance and ct == bt + it,
             "quality ledger mismatch")
    return CheckReport(len(faces), len(expected_samples), bt, it, ct - ft)


def _check_stitched(p):
    _require(type(p) is dict and set(p) == {
        "kind", "binding", "input", "requests", "samples", "circulations",
        "face_totals", "balance"}, "invalid payload members")
    _require(p["kind"] == "stitched-analysis-v1", "unsupported analysis")
    binding, source = p["binding"], p["input"]
    _require(type(binding) is dict and set(binding) == {
        "plan_id", "plan_digest", "revision_id", "source_digest", "convention"},
        "incomplete binding")
    _require(all(type(binding[k]) is int and binding[k] > 0
                 for k in ("plan_id", "revision_id")), "invalid binding IDs")
    _require(binding["convention"] == "stitched-surface-v1", "unsupported convention")
    _require(type(source) is dict and set(source) == {"mesh", "fields", "stitch"},
             "incomplete stitched source snapshot")
    _require(canonical_digest(source) == binding["source_digest"], "source digest mismatch")
    stitch = source["stitch"]
    _require(type(stitch) is dict and set(stitch) == {
        "source_revision", "source_digest", "source_input", "seams",
        "node_map", "edge_map", "face_map"}, "invalid stitch record")
    _require(type(stitch["source_revision"]) is int and stitch["source_revision"] > 0,
             "invalid source revision")
    original = stitch["source_input"]
    _require(type(original) is dict and set(original) == {"mesh", "fields"},
             "incomplete frozen source input")
    _require(canonical_digest(original) == stitch["source_digest"],
             "stitch source digest mismatch")
    _require(_ordinary_parts(original["mesh"], original["fields"]),
             "invalid frozen source input")
    src_nodes, src_faces, src_terms, src_uses = _ordinary_parts(
        original["mesh"], original["fields"])

    seams = stitch["seams"]
    _require(type(seams) is list, "seams must be an ordered list")
    normalized_seams = []
    for item in seams:
        _require(type(item) is tuple and len(item) == 2, "invalid seam entry")
        sides = []
        for side in item:
            _require(type(side) is tuple and len(side) == 2, "invalid seam side")
            sides.append(side)
        (a, b), (c, d) = sides
        _require(all(type(v) is str and v in src_nodes for v in (a, b, c, d)),
                 "unknown seam endpoint")
        _require(a != b and c != d, "degenerate seam side")
        _require(src_nodes[a] == src_nodes[c] and src_nodes[b] == src_nodes[d],
                 "seam endpoints disagree in exact coordinates")
        first, second = tuple(sorted((a, b))), tuple(sorted((c, d)))
        _require(first != second, "seam joins an edge to itself")
        _require(len(src_uses[first]) == 1 and len(src_uses[second]) == 1,
                 "only original boundary edges may be stitched")
        normalized_seams.append((tuple(side for side in (a, b)),
                                 tuple(side for side in (c, d))))
    identities = []
    for (a, b), (c, d) in normalized_seams:
        identities.extend([tuple(sorted((a, b))), tuple(sorted((c, d)))])
    _require(len(identities) == len(set(identities)),
             "an original edge participates in at most one seam")

    parent = {node: node for node in src_nodes}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for (a, b), (c, d) in normalized_seams:
        for left, right in ((a, c), (b, d)):
            left, right = find(left), find(right)
            if left != right:
                parent[right] = left
    groups = {}
    for node in src_nodes:
        groups.setdefault(find(node), []).append(node)
    node_map = {}
    coordinates = {}
    for members in groups.values():
        name = min(members)
        first = src_nodes[members[0]]
        _require(all(src_nodes[item] == first for item in members),
                 "merged vertex coordinates disagree")
        coordinates[name] = first
        for item in members:
            node_map[item] = name
    stored_nodes = stitch["node_map"]
    _require(type(stored_nodes) is dict and stored_nodes == node_map,
             "node mapping disagrees with seam pairing and naming rule")

    parent_edge = {edge: edge for edge in src_uses}

    def find_edge(edge):
        while parent_edge[edge] != edge:
            parent_edge[edge] = parent_edge[parent_edge[edge]]
            edge = parent_edge[edge]
        return edge

    for (a, b), (c, d) in normalized_seams:
        left, right = find_edge(tuple(sorted((a, b)))), find_edge(tuple(sorted((c, d))))
        if left != right:
            parent_edge[right] = left
    edge_groups = {}
    for edge in src_uses:
        edge_groups.setdefault(find_edge(edge), []).append(edge)
    edge_id_of, endpoints_of, sign_of = {}, {}, {}
    for members in edge_groups.values():
        edge_id = min(members)
        pairs = {tuple(sorted((node_map[u], node_map[v]))) for u, v in members}
        _require(len(pairs) == 1, "inconsistent stitched edge endpoints")
        endpoints = pairs.pop()
        _require(endpoints[0] != endpoints[1], "stitch collapses an edge to a loop")
        endpoints_of[edge_id] = endpoints
        for u, v in members:
            edge_id_of[(u, v)] = edge_id
            sign_of[(u, v)] = 1 if (node_map[u], node_map[v]) == endpoints else -1
    stored_edges = stitch["edge_map"]
    _require(type(stored_edges) is dict and set(stored_edges) == set(edge_id_of),
             "edge mapping incomplete")
    for edge, entry in stored_edges.items():
        _require(type(entry) is dict and set(entry) == {"edge", "sign"}
                 and type(entry["sign"]) is int and entry["sign"] in (-1, 1),
                 "invalid edge mapping")
        _require(entry["edge"] == edge_id_of[edge] and entry["sign"] == sign_of[edge],
                 "edge mapping disagrees with derived orientation")

    quotient = {face: tuple(node_map[node] for node in vertices)
                for face, vertices in src_faces.items()}
    for face, vertices in quotient.items():
        _require(len(set(vertices)) == 3, "stitched face has repeated vertices")
    per_face_edges, per_face_base = {}, {}
    for face, vertices in src_faces.items():
        identities, bases = [], []
        for position in range(3):
            start, end = vertices[position], vertices[(position + 1) % 3]
            identity = tuple(sorted((start, end)))
            identities.append(edge_id_of[identity])
            bases.append((1 if start < end else -1) * sign_of[identity])
        _require(len(set(identities)) == 3, "stitched face repeats an edge")
        per_face_edges[face], per_face_base[face] = identities, bases
    uses = {}
    for face, identities in per_face_edges.items():
        for position, edge_id in enumerate(identities):
            uses.setdefault(edge_id, []).append((face, position))
    _require(all(len(items) <= 2 for items in uses.values()),
             "stitched edge has more than two incident faces")
    _stitched_topology(quotient, uses, per_face_edges, endpoints_of)

    neighbours = {face: [] for face in quotient}
    for edge_id, items in uses.items():
        if len(items) == 2:
            (face_a, _), (face_b, _) = items
            neighbours[face_a].append((face_b, edge_id))
            neighbours[face_b].append((face_a, edge_id))
    base_at = {(face, edge_id): per_face_base[face][position]
               for face, identities in per_face_edges.items()
               for position, edge_id in enumerate(identities)}
    flip, unvisited = {}, set(quotient)
    while unvisited:
        anchor = min(unvisited)
        flip[anchor] = 1
        stack = [anchor]
        while stack:
            face = stack.pop()
            if face not in unvisited:
                continue
            unvisited.discard(face)
            for other, edge_id in neighbours[face]:
                expected = flip[face] * (-1 if base_at[(face, edge_id)]
                                         == base_at[(other, edge_id)] else 1)
                if other in flip:
                    _require(flip[other] == expected,
                             "incident faces cannot be oriented consistently")
                else:
                    flip[other] = expected
                    stack.append(other)

    oriented, face_terms = {}, {}
    for face, vertices in quotient.items():
        oriented[face] = vertices if flip[face] == 1 else (vertices[0], vertices[2], vertices[1])
        terms = []
        for position in range(3):
            start, end = oriented[face][position], oriented[face][(position + 1) % 3]
            edge_id = None
            for candidate in per_face_edges[face]:
                if endpoints_of[candidate] == tuple(sorted((start, end))):
                    edge_id = candidate
                    break
            _require(edge_id is not None, "cannot relate stitched edge to winding")
            terms.append({"edge": edge_id,
                          "sign": 1 if start < end else -1})
        _require(len({term["edge"] for term in terms}) == 3,
                 "oriented face repeats an edge")
        face_terms[face] = terms
    stored_face_map = stitch["face_map"]
    _require(type(stored_face_map) is dict and set(stored_face_map) == set(quotient),
             "face mapping incomplete")
    for face, entry in stored_face_map.items():
        _require(type(entry) is dict and set(entry) == {"sign", "corners"}
                 and entry["sign"] == flip[face]
                 and type(entry["corners"]) is tuple
                 and entry["corners"] == ((0, 1, 2) if flip[face] == 1
                                          else (0, 2, 1)),
                 "face mapping disagrees with derived orientation")

    mesh = source["mesh"]
    _require(type(mesh) is dict and set(mesh) == {
        "nodes", "faces", "edges", "face_edges", "edge_endpoints"},
        "invalid stitched mesh")
    _require(mesh["nodes"] == coordinates, "stitched node identity mismatch")
    _require(mesh["faces"] == oriented, "stitched face winding mismatch")
    _require(mesh["edges"] == sorted(endpoints_of), "stitched edge directory mismatch")
    _require(mesh["edge_endpoints"] == {edge: tuple(pair)
                                        for edge, pair in sorted(endpoints_of.items())},
             "stitched edge endpoints mismatch")
    _require(mesh["face_edges"] == {face: face_terms[face] for face in sorted(face_terms)},
             "stitched face edge terms mismatch")
    _require(_stitched_topology(quotient, uses, per_face_edges, endpoints_of),
             "invalid stitched topology")

    fields = source["fields"]
    _require(type(fields) is dict and set(fields) == {
        "node_scalars", "edge_integrals", "face_totals"}, "invalid fields")
    src_fields = original["fields"]
    expected_nodes = {}
    for members in groups.values():
        values = {src_fields["node_scalars"][item] for item in members}
        _require(len(values) == 1, "merged node scalars are not exactly equal")
        expected_nodes[min(members)] = values.pop()
    expected_edges = {}
    for members in edge_groups.values():
        values = {src_fields["edge_integrals"][item] * sign_of[item] for item in members}
        _require(len(values) == 1, "merged edge integrals are not exactly equal")
        expected_edges[min(members)] = values.pop()
    _require(fields["node_scalars"] == expected_nodes, "stitched node field mismatch")
    _require(fields["edge_integrals"] == expected_edges, "stitched edge field mismatch")
    _require(fields["face_totals"] == src_fields["face_totals"],
             "face totals must not change under stitching")

    requests = p["requests"]
    _require(type(requests) is list and type(p["samples"]) is list, "invalid sample sequence")
    expected_samples = []
    for request in requests:
        _require(type(request) is dict and set(request) == {"face", "weights"},
                 "invalid request")
        face, weights = request["face"], request["weights"]
        _require(type(face) is str and face in src_faces and type(weights) is tuple
                 and len(weights) == 3
                 and all(isinstance(w, Fraction) and w >= 0 for w in weights)
                 and sum(weights) == 1, "invalid exact barycentric weights")
        vertices = src_faces[face]
        position = tuple(sum((weights[i] * src_nodes[vertices[i]][axis]
                              for i in range(3)), Fraction(0)) for axis in range(3))
        value = sum((weights[i] * src_fields["node_scalars"][vertices[i]]
                     for i in range(3)), Fraction(0))
        expected_samples.append({"face": face, "weights": weights,
                                 "position": position, "value": value})
    for sample in p["samples"]:
        _require(type(sample) is dict and set(sample) == {"face", "weights", "position", "value"}
                 and isinstance(sample["value"], Fraction)
                 and type(sample["position"]) is tuple and len(sample["position"]) == 3
                 and all(isinstance(x, Fraction) for x in sample["position"]),
                 "nonrational sample evidence")
    _require(p["samples"] == expected_samples, "sample position/value/order mismatch")
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"], "samples": requests,
                    "convention": binding["convention"]}
    _require(canonical_digest(plan_binding) == binding["plan_digest"], "plan digest mismatch")

    integrals, totals = fields["edge_integrals"], fields["face_totals"]
    circulations = {face: sum((term["sign"] * integrals[term["edge"]]
                              for term in terms), Fraction(0))
                    for face, terms in face_terms.items()}
    _require(p["circulations"] == circulations, "incorrect face circulation")
    _require(p["face_totals"] == totals, "incorrect saved face totals")
    boundary, internal = [], []
    edge_signs = {}
    for face, terms in face_terms.items():
        for term in terms:
            edge_signs.setdefault(term["edge"], []).append(term["sign"])
    for edge_id in sorted(endpoints_of):
        if len(edge_signs[edge_id]) == 1:
            coefficient = edge_signs[edge_id][0]
        else:
            coefficient = sum(edge_signs[edge_id])
        item = {"edge": edge_id, "coefficient": coefficient,
                "value": coefficient * integrals[edge_id]}
        (boundary if len(edge_signs[edge_id]) == 1 else internal).append(item)
    bt = sum((item["value"] for item in boundary), Fraction(0))
    it = sum((item["value"] for item in internal), Fraction(0))
    ct, ft = sum(circulations.values(), Fraction(0)), sum(totals.values(), Fraction(0))
    expected_balance = {
        "face_gaps": {face: circulations[face] - totals[face] for face in sorted(oriented)},
        "boundary_terms": boundary, "internal_terms": internal,
        "boundary_total": bt, "internal_total": it,
        "circulation_total": ct, "face_total": ft, "gap_total": ct - ft}
    actual_balance = p["balance"]
    _require(actual_balance == expected_balance and ct == bt + it,
             "quality ledger mismatch")
    return CheckReport(len(oriented), len(expected_samples), bt, it, ct - ft)


def _ordinary_parts(mesh, fields):
    _require(type(mesh) is dict and set(mesh) == {"nodes", "faces", "edges", "face_edges"},
             "invalid ordinary mesh")
    nodes, faces = mesh["nodes"], mesh["faces"]
    _require(type(nodes) is dict and bool(nodes) and type(faces) is dict
             and 1 <= len(faces) <= 60, "invalid geometry size")
    uses, face_terms = {}, {}
    for face, vertices in sorted(faces.items()):
        _require(type(vertices) is tuple and len(vertices) == 3
                 and all(type(v) is str and v in nodes for v in vertices)
                 and len(set(vertices)) == 3, "invalid face references")
        a, b, c = [nodes[node] for node in vertices]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        _require(any(x != 0 for x in cross), "degenerate triangle")
        terms = []
        for i in range(3):
            start, end = vertices[i], vertices[(i + 1) % 3]
            edge = tuple(sorted((start, end)))
            sign = 1 if start < end else -1
            terms.append({"edge": edge, "sign": sign})
            uses.setdefault(edge, []).append(sign)
        face_terms[face] = terms
    _require(all(1 <= len(signs) <= 2 for signs in uses.values()), "nonmanifold edge")
    _require(mesh["edges"] == sorted(uses) and mesh["face_edges"] == face_terms,
             "saved incidence differs from original face winding")
    _require(type(fields) is dict and set(fields) == {
        "node_scalars", "edge_integrals", "face_totals"}, "invalid fields")
    for name, identities in (("node_scalars", nodes), ("edge_integrals", uses),
                             ("face_totals", faces)):
        _require(type(fields[name]) is dict and set(fields[name]) == set(identities)
                 and all(isinstance(x, Fraction) for x in fields[name].values()),
                 "inexact field coverage")
    return nodes, faces, face_terms, uses


def _stitched_topology(faces, uses, per_face_edges, endpoints):
    link_degree, arcs = {}, {}
    for face, vertices in faces.items():
        identities = per_face_edges[face]
        for position, vertex in enumerate(vertices):
            incoming, outgoing = identities[(position - 1) % 3], identities[position]
            degrees = link_degree.setdefault(vertex, {})
            degrees[incoming] = degrees.get(incoming, 0) + 1
            degrees[outgoing] = degrees.get(outgoing, 0) + 1
            arcs.setdefault(vertex, []).append((incoming, outgoing))
    present = {vertex for vertices in faces.values() for vertex in vertices}
    _require(set(link_degree) == present, "isolated stitched vertex")
    for vertex, degrees in link_degree.items():
        _require(bool(degrees), "isolated stitched vertex")
        adjacency = {edge_id: set() for edge_id in degrees}
        for left, right in arcs[vertex]:
            adjacency[left].add(right)
            adjacency[right].add(left)
        seen, stack = set(), [next(iter(degrees))]
        while stack:
            edge_id = stack.pop()
            if edge_id in seen:
                continue
            seen.add(edge_id)
            stack.extend(adjacency[edge_id] - seen)
        _require(seen == set(degrees), "vertex neighbourhood separates into fans")
        _require(all(degree in (1, 2) for degree in degrees.values()),
                 "vertex neighbourhood is not a disk or half-disk")
        ends = sum(1 for degree in degrees.values() if degree == 1)
        _require(ends in (0, 2), "vertex neighbourhood is not a disk or half-disk")
    return True


def check_export(raw):
    envelope = decode_payload(raw)
    _require(type(envelope) is dict and set(envelope) == {
        "export_kind", "export_version", "result"}
        and envelope["export_kind"] == "surface-quality-record"
        and type(envelope["export_version"]) is int and envelope["export_version"] == 1,
        "unsupported export")
    result = envelope["result"]
    _require(type(result) is dict and set(result) == {"result_id", "fingerprint", "payload"}
             and type(result["result_id"]) is int and result["result_id"] > 0,
             "invalid result directory")
    _require(canonical_digest(result["payload"]) == result["fingerprint"],
             "export digest mismatch")
    return check_result_payload(result["payload"])


def load_solver_sample(path):
    with open(path, encoding="utf-8") as stream:
        source = json.load(stream)
    def rational(pair):
        if (type(pair) is not list or len(pair) != 2
                or any(type(x) is not int for x in pair) or pair[1] == 0):
            raise ValueError("sample rational pair required")
        return Fraction(*pair)
    mesh = {"nodes": {node: tuple(rational(x) for x in xyz)
                      for node, xyz in source["mesh"]["nodes"].items()},
            "faces": {face: tuple(vertices) for face, vertices in source["mesh"]["faces"].items()}}
    fields = {"node_scalars": {k: rational(v) for k, v in source["fields"]["node_scalars"].items()},
              "edge_integrals": {tuple(edge): rational(value)
                                 for edge, value in source["fields"]["edge_integrals"]},
              "face_totals": {k: rational(v) for k, v in source["fields"]["face_totals"].items()}}
    samples = [{"face": item["face"], "weights": tuple(rational(w) for w in item["weights"])}
               for item in source["samples"]]
    return mesh, fields, samples


def demo(sample_path):
    import tempfile
    from pathlib import Path
    from surface.sources.storage import ArchiveStore
    from surface.sources.catalog import SourceCatalog
    from surface.tasks.plans import PlanService
    from surface.tasks.execution import AnalysisService
    from surface.results.reading import ResultReader
    from surface.results.exporting import ResultExporter
    mesh, fields, samples = load_solver_sample(sample_path)
    with tempfile.TemporaryDirectory(prefix="surface-demo-") as directory:
        database = Path(directory) / "quality.sqlite"
        store = ArchiveStore.open(database)
        catalog = SourceCatalog(store)
        receipt = catalog.put_source("solver-day-1", mesh, fields)
        plan = PlanService(store).create_plan("quality-day-1", receipt["revision_id"], samples)
        result_id = AnalysisService(store).execute(plan)
        original = ResultReader(store).read_result(result_id)
        fields["node_scalars"]["A"] = Fraction(99)
        catalog.put_source("solver-correction", mesh, fields)
        store.close()
        reopened = ArchiveStore.open(database)
        try:
            reader = ResultReader(reopened)
            assert reader.read_result(result_id) == original
            raw = ResultExporter(reader).export_result(result_id)
        finally:
            reopened.close()
        report = check_export(raw)
        payload = original["payload"]
        expected = {"samples": ["0", "1", "4/3", "5", "4/3"],
                    "circulations": {"f1": "62/105", "f2": "37/70"}}
        actual = {"samples": [str(item["value"]) for item in payload["samples"]],
                  "circulations": {k: str(v) for k, v in payload["circulations"].items()}}
        downstream = {"boundary": str(report.boundary_total), "internal": str(report.internal_total),
                      "gap": str(report.gap_total), "frozen_revision": payload["binding"]["revision_id"]}
        downstream_expected = {"boundary": "47/42", "internal": "0", "gap": "25/14",
                               "frozen_revision": receipt["revision_id"]}
        assert actual == expected
        assert downstream == downstream_expected
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v8.offline_exact", "input": {"sample": sample_path, "correction_A": "99"},
            "expected": expected, "actual": actual,
            "downstream_expected": downstream_expected, "downstream_actual": downstream,
            "code_paths": ["surface/tasks/execution.py", "surface/tasks/inputs.py",
                           "surface/fields/sampling.py", "surface/fields/circulation.py",
                           "surface/fields/balances.py", "surface/results/exporting.py",
                           "surface/results/checking.py"],
            "oracle": "independent hand fractions and offline traversal of saved solver input"
        }, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="Offline surface quality review")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", metavar="SOLVER_JSON")
    group.add_argument("--check", metavar="EXPORT_FILE")
    args = parser.parse_args()
    if args.demo:
        demo(args.demo)
    else:
        with open(args.check, "rb") as stream:
            report = check_export(stream.read())
        print(json.dumps({"faces": report.faces, "samples": report.samples,
                          "boundary_total": str(report.boundary_total),
                          "internal_total": str(report.internal_total),
                          "gap_total": str(report.gap_total)}, sort_keys=True))


if __name__ == "__main__":
    main()
