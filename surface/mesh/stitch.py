from surface.fields.bindings import FieldValues, validate_coverage
from surface.mesh.incidence import canonical_edge
from surface.mesh.model import FaceEdge, OrdinaryMesh, StitchedMesh
from surface.results.codec import canonical_digest
from surface.sources.field_input import normalize_fields


class _DisjointSet:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _normalize_seams(source_mesh, seams):
    if type(seams) not in (list, tuple):
        raise ValueError("seams must be an ordered sequence")
    nodes = source_mesh.nodes
    boundary = {edge for edge, uses in source_mesh.incidence.items() if len(uses) == 1}
    used, result = set(), []
    for item in seams:
        if type(item) not in (list, tuple) or len(item) != 2:
            raise ValueError("each seam must pair two boundary edges")
        sides = []
        for side in item:
            if type(side) not in (list, tuple) or len(side) != 2:
                raise ValueError("each seam side must list two node IDs")
            start, end = side
            if (type(start) is not str or type(end) is not str or start == end
                    or start not in nodes or end not in nodes):
                raise ValueError("seam sides require two existing distinct node IDs")
            sides.append((start, end))
        identities = (canonical_edge(*sides[0]), canonical_edge(*sides[1]))
        if identities[0] == identities[1]:
            raise ValueError("a seam must join two different original edges")
        for identity in identities:
            if identity in used:
                raise ValueError("an original edge participates in at most one seam")
            if identity not in boundary:
                raise ValueError("only original boundary edges may be stitched")
            used.add(identity)
        (a, b), (c, d) = sides
        if nodes[a] != nodes[c] or nodes[b] != nodes[d]:
            raise ValueError("paired seam endpoints must agree exactly")
        result.append((tuple(sides[0]), tuple(sides[1])))
    return result


def _vertex_groups(nodes, seams):
    dsu = _DisjointSet(nodes)
    for (a, b), (c, d) in seams:
        dsu.union(a, c)
        dsu.union(b, d)
    groups = {}
    for node in nodes:
        groups.setdefault(dsu.find(node), []).append(node)
    name = {root: min(members) for root, members in groups.items()}
    node_map = {node: name[dsu.find(node)] for node in nodes}
    coordinates = {}
    for root, members in groups.items():
        first = nodes[members[0]]
        if any(nodes[member] != first for member in members):
            raise ValueError("merged vertex coordinates disagree")
        coordinates[name[root]] = first
    return groups, node_map, coordinates


def _edge_classes(source_mesh, seams, node_map):
    edges = list(source_mesh.incidence)
    dsu = _DisjointSet(edges)
    for (a, b), (c, d) in seams:
        dsu.union(canonical_edge(a, b), canonical_edge(c, d))
    groups = {}
    for edge in edges:
        groups.setdefault(dsu.find(edge), []).append(edge)
    edge_id_of, endpoints_of, sign_of = {}, {}, {}
    for members in groups.values():
        edge_id = min(members)
        endpoint_pairs = set()
        for u, v in members:
            left, right = node_map[u], node_map[v]
            if left == right:
                raise ValueError("stitching collapses an original edge to a loop")
            endpoint_pairs.add(tuple(sorted((left, right))))
        if len(endpoint_pairs) != 1:
            raise ValueError("stitched edge endpoints are inconsistent")
        endpoints = next(iter(endpoint_pairs))
        endpoints_of[edge_id] = endpoints
        for u, v in members:
            edge_id_of[(u, v)] = edge_id
            sign_of[(u, v)] = 1 if (node_map[u], node_map[v]) == endpoints else -1
    return groups, edge_id_of, endpoints_of, sign_of


def _corner_maps(source_mesh, edge_id_of, sign_of):
    per_face_edge, per_face_base = {}, {}
    for face, vertices in source_mesh.faces.items():
        identities, bases = [], []
        for position in range(3):
            start, end = vertices[position], vertices[(position + 1) % 3]
            identity = canonical_edge(start, end)
            identities.append(edge_id_of[identity])
            winding = 1 if start < end else -1
            bases.append(winding * sign_of[identity])
        if len(set(identities)) != 3:
            raise ValueError("stitched face repeats a stitched edge")
        per_face_edge[face] = identities
        per_face_base[face] = bases
    return per_face_edge, per_face_base


def _edge_uses(faces, per_face_edge):
    uses = {}
    for face in faces:
        for position, edge_id in enumerate(per_face_edge[face]):
            uses.setdefault(edge_id, []).append((face, position))
    if any(len(items) > 2 for items in uses.values()):
        raise ValueError("stitched edge has more than two incident faces")
    return uses


def _check_vertex_neighbourhoods(vertex_ids, quotient_faces, per_face_edge, uses):
    """Every vertex link must be a single cycle (disk) or path (half-disk).

    Link vertices are the stitched edges at the vertex; each incident face adds
    one link arc between its two edges at the vertex. Parallel arcs are kept,
    so a two-face two-edge loop is a legal 2-cycle rather than a doubled edge.
    """
    present = {vertex for vertices in quotient_faces.values() for vertex in vertices}
    if present != set(vertex_ids):
        raise ValueError("stitched surface contains an isolated vertex")
    link_degree = {vertex: {} for vertex in vertex_ids}
    arcs = {vertex: [] for vertex in vertex_ids}
    for face, vertices in quotient_faces.items():
        identities = per_face_edge[face]
        for position, vertex in enumerate(vertices):
            incoming = identities[(position - 1) % 3]
            outgoing = identities[position]
            for edge_id in (incoming, outgoing):
                link_degree[vertex][edge_id] = link_degree[vertex].get(edge_id, 0) + 1
            arcs[vertex].append((incoming, outgoing))
    for vertex in vertex_ids:
        degrees = link_degree[vertex]
        if not degrees:
            raise ValueError("stitched surface contains an isolated vertex")
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
        if seen != set(degrees):
            raise ValueError("vertex neighbourhood separates into multiple fans")
        if any(degree not in (1, 2) for degree in degrees.values()):
            raise ValueError("vertex neighbourhood is not a disk or half-disk")
        ends = sum(1 for degree in degrees.values() if degree == 1)
        if ends not in (0, 2):
            raise ValueError("vertex neighbourhood is not a disk or half-disk")


def _orient_faces(quotient_faces, per_face_edge, per_face_base, uses):
    base_at = {(face, edge_id): per_face_base[face][position]
               for face, identities in per_face_edge.items()
               for position, edge_id in enumerate(identities)}
    neighbours = {face: [] for face in quotient_faces}
    for edge_id, items in uses.items():
        if len(items) != 2:
            continue
        (face_a, _), (face_b, _) = items
        neighbours[face_a].append((face_b, edge_id))
        neighbours[face_b].append((face_a, edge_id))
    flip, components, unvisited = {}, [], set(quotient_faces)
    while unvisited:
        anchor = min(unvisited)
        flip[anchor] = 1
        stack, component = [anchor], []
        while stack:
            face = stack.pop()
            if face not in unvisited:
                continue
            unvisited.discard(face)
            component.append(face)
            for other, edge_id in neighbours[face]:
                base_face = base_at[(face, edge_id)]
                base_other = base_at[(other, edge_id)]
                expected = flip[face] * (-1 if base_face == base_other else 1)
                if other in flip:
                    if flip[other] != expected:
                        raise ValueError("incident faces cannot be oriented consistently")
                else:
                    flip[other] = expected
                    stack.append(other)
        components.append(sorted(component))
    return flip, components


def validate_stitch_record(stitch, mesh, fields):
    """Structural re-parse of saved V2 associations; no quotient rebuild."""
    names = {"source_revision", "source_digest", "source_input", "seams",
             "node_map", "edge_map", "face_map"}
    if type(stitch) is not dict or set(stitch) != names:
        raise ValueError("incomplete stitch record")
    source_input = stitch["source_input"]
    if (type(stitch["source_revision"]) is not int or stitch["source_revision"] < 1
            or type(stitch["source_digest"]) is not str
            or type(source_input) is not dict
            or set(source_input) != {"mesh", "fields"}
            or canonical_digest(source_input) != stitch["source_digest"]):
        raise ValueError("invalid stitch source binding")
    source_mesh = OrdinaryMesh.from_record(source_input["mesh"])
    source_fields = normalize_fields(source_mesh, source_input["fields"])
    seams = stitch["seams"]
    if type(seams) is not list:
        raise ValueError("stitch seams must be an ordered list")
    seen_edges = set()
    for item in seams:
        if (type(item) is not tuple or len(item) != 2
                or any(type(side) is not tuple or len(side) != 2 for side in item)):
            raise ValueError("invalid normalized seam")
        (a, b), (c, d) = item
        if any(type(v) is not str or v not in source_mesh.nodes for v in (a, b, c, d)):
            raise ValueError("unknown seam endpoint")
        first, second = canonical_edge(a, b), canonical_edge(c, d)
        if first == second:
            raise ValueError("a seam must join two different original edges")
        for edge in (first, second):
            if edge in seen_edges or len(source_mesh.incidence[edge]) != 1:
                raise ValueError("only distinct original boundary edges may be stitched")
            seen_edges.add(edge)
        if (source_mesh.nodes[a] != source_mesh.nodes[c]
                or source_mesh.nodes[b] != source_mesh.nodes[d]):
            raise ValueError("paired seam endpoints must agree exactly")
    node_map = stitch["node_map"]
    if type(node_map) is not dict or set(node_map) != set(source_mesh.nodes):
        raise ValueError("stitch node mapping incomplete")
    for source_node, target in node_map.items():
        if (type(target) is not str or target not in mesh.nodes
                or mesh.nodes[target] != source_mesh.nodes[source_node]):
            raise ValueError("stitch node mapping disagrees with exact coordinates")
    if set(node_map.values()) != set(mesh.nodes):
        raise ValueError("stitched nodes are not exactly the mapped source nodes")
    for target in mesh.nodes:
        if target != min(item for item, name in node_map.items() if name == target):
            raise ValueError("stitched vertex is not named by the smallest source ID")
    for edge in seen_edges:
        a, b = edge
        if node_map[a] == node_map[b]:
            raise ValueError("stitching collapses a seam edge to a loop")
    grouped = {}
    for source_node, target in node_map.items():
        grouped.setdefault(target, set()).add(source_fields.node_scalars[source_node])
    if any(len(values) != 1 for values in grouped.values()):
        raise ValueError("merged node scalars are not exactly equal")

    edge_map = stitch["edge_map"]
    if type(edge_map) is not dict or set(edge_map) != set(source_mesh.incidence):
        raise ValueError("stitch edge mapping incomplete")
    if set(entry.get("edge") for entry in edge_map.values()
           if type(entry) is dict) != set(mesh.edge_endpoints):
        raise ValueError("stitched edges are not exactly the mapped source edges")
    classes = {}
    for source_edge, entry in edge_map.items():
        if (type(entry) is not dict or set(entry) != {"edge", "sign"}
                or type(entry["sign"]) is not int or entry["sign"] not in (-1, 1)
                or entry["edge"] not in mesh.edge_endpoints):
            raise ValueError("invalid stitch edge mapping")
        left, right = node_map[source_edge[0]], node_map[source_edge[1]]
        endpoints = mesh.edge_endpoints[entry["edge"]]
        if tuple(sorted((left, right))) != endpoints:
            raise ValueError("mapped edge endpoints disagree with stitch mapping")
        if entry["sign"] != (1 if (left, right) == endpoints else -1):
            raise ValueError("incorrect stitched edge orientation")
        classes.setdefault(entry["edge"], []).append(source_edge)
    for edge_id, members in classes.items():
        converted = {source_fields.edge_integrals[item] * edge_map[item]["sign"]
                     for item in members}
        if len(converted) != 1 or converted.pop() != fields.edge_integrals[edge_id]:
            raise ValueError("stitched edge field disagrees with converted source")
        if edge_id != min(members):
            raise ValueError("stitched edge is not named by the smallest original edge")
    for seam in seams:
        (a, b), (c, d) = seam
        if node_map[a] != node_map[c] or node_map[b] != node_map[d]:
            raise ValueError("seam endpoints do not share stitched vertices")
        first, second = canonical_edge(a, b), canonical_edge(c, d)
        if edge_map[first]["edge"] != edge_map[second]["edge"]:
            raise ValueError("seam sides do not share a stitched edge")

    face_map = stitch["face_map"]
    if type(face_map) is not dict or set(face_map) != set(source_mesh.faces):
        raise ValueError("stitch face mapping incomplete")
    if set(mesh.faces) != set(source_mesh.faces):
        raise ValueError("stitched face identity changed")
    for face, vertices in source_mesh.faces.items():
        entry = face_map[face]
        if (type(entry) is not dict or set(entry) != {"sign", "corners"}
                or type(entry["sign"]) is not int or entry["sign"] not in (-1, 1)
                or type(entry["corners"]) is not tuple
                or entry["corners"] != ((0, 1, 2) if entry["sign"] == 1
                                        else (0, 2, 1))):
            raise ValueError("invalid stitch face mapping")
        quotient = tuple(node_map[node] for node in vertices)
        expected = quotient if entry["sign"] == 1 else (quotient[0], quotient[2], quotient[1])
        if mesh.faces[face] != expected:
            raise ValueError("stitched face winding disagrees with face mapping")
        if mesh.sample_vertices(face) != quotient:
            raise ValueError("stitched source corner order lost")
        if fields.face_totals[face] != source_fields.face_totals[face]:
            raise ValueError("face totals must not change under stitching")
        mapped = {edge_map[canonical_edge(vertices[index],
                                          vertices[(index + 1) % 3])]["edge"]
                  for index in range(3)}
        if mapped != {term.edge for term in mesh.face_edges[face]}:
            raise ValueError("face edge identity disagrees with saved edge mapping")
    for target, values in grouped.items():
        if fields.node_scalars[target] != next(iter(values)):
            raise ValueError("stitched node field disagrees with converted source")
    return True


def build_stitched_content(source_revision, source_digest, source_mesh,
                           source_fields, seams):
    seams = _normalize_seams(source_mesh, seams)
    groups, node_map, coordinates = _vertex_groups(source_mesh.nodes, seams)
    classes, edge_id_of, endpoints_of, sign_of = _edge_classes(
        source_mesh, seams, node_map)

    node_scalars = {}
    for root, members in groups.items():
        values = {source_fields.node_scalars[member] for member in members}
        if len(values) != 1:
            raise ValueError("node scalars conflict on a merged vertex")
        node_scalars[node_map[members[0]]] = values.pop()

    edge_integrals = {}
    for members in classes.values():
        edge_id = min(members)
        values = {sign_of[edge] * source_fields.edge_integrals[edge]
                  for edge in members}
        if len(values) != 1:
            raise ValueError("edge integrals conflict on a merged edge")
        edge_integrals[edge_id] = values.pop()

    face_totals = dict(source_fields.face_totals)
    per_face_edge, per_face_base = _corner_maps(source_mesh, edge_id_of, sign_of)
    quotient_faces = {face: tuple(node_map[node] for node in vertices)
                      for face, vertices in source_mesh.faces.items()}
    for face, vertices in quotient_faces.items():
        if len(set(vertices)) != 3:
            raise ValueError("stitched face has fewer than three distinct vertices")
    uses = _edge_uses(quotient_faces, per_face_edge)
    _check_vertex_neighbourhoods(coordinates, quotient_faces, per_face_edge, uses)
    flip, _components = _orient_faces(
        quotient_faces, per_face_edge, per_face_base, uses)

    oriented_faces, face_map, face_edges = {}, {}, {}
    for face, source_order in quotient_faces.items():
        if flip[face] == 1:
            oriented = source_order
            corners = (0, 1, 2)
        else:
            oriented = (source_order[0], source_order[2], source_order[1])
            corners = (0, 2, 1)
        oriented_faces[face] = oriented
        face_map[face] = {"sign": flip[face], "corners": corners}
        terms = []
        for position in range(3):
            target = {position, (position + 1) % 3}
            selected = None
            for corner in range(3):
                if {corners[corner], corners[(corner + 1) % 3]} == target:
                    selected = corner
                    break
            if selected is None:
                raise ValueError("cannot locate source corner for stitched edge")
            identity = per_face_edge[face][selected]
            sign = 1 if oriented[position] < oriented[(position + 1) % 3] else -1
            terms.append(FaceEdge(identity, sign))
        face_edges[face] = terms

    edge_endpoints = {edge_id: endpoints for edge_id, endpoints in endpoints_of.items()}
    mesh_record = {
        "nodes": dict(coordinates),
        "faces": oriented_faces,
        "edges": sorted(edge_endpoints),
        "face_edges": {face: [{"edge": term.edge, "sign": term.sign}
                             for term in face_edges[face]]
                       for face in sorted(oriented_faces)},
        "edge_endpoints": {edge_id: tuple(edge_endpoints[edge_id])
                           for edge_id in sorted(edge_endpoints)},
    }
    stitch = {
        "source_revision": source_revision,
        "source_digest": source_digest,
        "source_input": {"mesh": source_mesh.to_record(),
                         "fields": source_fields.to_record()},
        "seams": [tuple(seam) for seam in seams],
        "node_map": dict(node_map),
        "edge_map": {edge: {"edge": edge_id_of[edge], "sign": sign_of[edge]}
                     for edge in sorted(edge_id_of)},
        "face_map": {face: {"sign": face_map[face]["sign"],
                            "corners": face_map[face]["corners"]}
                     for face in sorted(face_map)},
    }
    if stitch["source_digest"] != canonical_digest(stitch["source_input"]):
        raise ValueError("source digest does not match the frozen source input")
    fields_record = {"node_scalars": node_scalars,
                     "edge_integrals": edge_integrals,
                     "face_totals": face_totals}
    mesh = StitchedMesh.from_record(mesh_record, stitch)
    values = FieldValues(node_scalars, edge_integrals, face_totals)
    validate_coverage(mesh, values)
    return {"mesh": mesh_record, "fields": fields_record, "stitch": stitch}
