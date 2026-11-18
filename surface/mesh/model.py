from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType


@dataclass(frozen=True)
class FaceEdge:
    edge: tuple
    sign: int


@dataclass(frozen=True)
class OrdinaryMesh:
    nodes: object
    faces: object
    face_edges: object
    incidence: object

    def __post_init__(self):
        for name in ("nodes", "faces", "face_edges", "incidence"):
            object.__setattr__(self, name, MappingProxyType(
                {key: tuple(value) for key, value in getattr(self, name).items()}))

    @property
    def edges(self):
        return tuple(sorted(self.incidence))

    @property
    def edge_endpoints(self):
        return {edge: edge for edge in self.incidence}

    def sample_vertices(self, face):
        return tuple(self.faces[face])

    def to_record(self):
        return {"nodes": dict(self.nodes), "faces": dict(self.faces),
                "edges": list(self.edges),
                "face_edges": {face: [{"edge": term.edge, "sign": term.sign}
                                     for term in self.face_edges[face]]
                               for face in sorted(self.faces)}}

    @classmethod
    def from_record(cls, record):
        from surface.sources.mesh_input import normalize_mesh
        if type(record) is not dict or set(record) != {
                "nodes", "faces", "edges", "face_edges"}:
            raise ValueError("invalid ordinary mesh record")
        mesh = normalize_mesh({"nodes": record["nodes"], "faces": record["faces"]})
        if mesh.to_record() != record:
            raise ValueError("inconsistent ordinary incidence")
        return mesh


def _pairs_to_tuples(record):
    return {key: tuple(value) for key, value in record.items()}


def _validate_source_faces(faces, source_faces):
    if source_faces is None:
        return {face: tuple(vertices) for face, vertices in faces.items()}
    if type(source_faces) is not dict or set(source_faces) != set(faces):
        raise ValueError("source corner order must cover every stitched face")
    result = {}
    for face, vertices in faces.items():
        order = source_faces[face]
        if (type(order) not in (list, tuple) or len(order) != 3
                or sorted(order) != [0, 1, 2]):
            raise ValueError("invalid source corner permutation")
        result[face] = tuple(vertices[position] for position in order)
    return result


@dataclass(frozen=True)
class StitchedMesh:
    nodes: object
    faces: object
    face_edges: object
    incidence: object
    edge_endpoints: object
    source_faces: object = None

    def __post_init__(self):
        for name in ("nodes", "faces", "face_edges", "incidence",
                     "edge_endpoints"):
            object.__setattr__(self, name, MappingProxyType(
                {key: tuple(value) for key, value in getattr(self, name).items()}))
        faces = {face: tuple(vertices) for face, vertices in self.faces.items()}
        source_faces = _validate_source_faces(faces, self.source_faces)
        object.__setattr__(self, "source_faces", MappingProxyType(source_faces))

    @property
    def edges(self):
        return tuple(sorted(self.edge_endpoints))

    def sample_vertices(self, face):
        return tuple(self.source_faces[face])

    def to_record(self):
        return {"nodes": dict(self.nodes), "faces": dict(self.faces),
                "edges": list(self.edges),
                "face_edges": {face: [{"edge": term.edge, "sign": term.sign}
                                     for term in self.face_edges[face]]
                               for face in sorted(self.faces)},
                "edge_endpoints": {edge: tuple(self.edge_endpoints[edge])
                                   for edge in self.edges}}

    @classmethod
    def from_record(cls, record, stitch=None):
        if type(record) is not dict or set(record) != {
                "nodes", "faces", "edges", "face_edges", "edge_endpoints"}:
            raise ValueError("invalid stitched mesh record")
        nodes = record["nodes"]
        if type(nodes) is not dict or not nodes:
            raise ValueError("nonempty stitched node mapping required")
        checked_nodes = {}
        for node, coordinates in nodes.items():
            if type(node) is not str or not node:
                raise ValueError("invalid stitched node ID")
            if (type(coordinates) not in (list, tuple) or len(coordinates) != 3
                    or any(not isinstance(x, Fraction)
                           for x in coordinates)):
                raise ValueError("coordinates must be three Fractions")
            checked_nodes[node] = tuple(coordinates)
        faces = record["faces"]
        if type(faces) is not dict or not 1 <= len(faces) <= 60:
            raise ValueError("one to sixty stitched faces required")
        checked_faces = {}
        for face, vertices in faces.items():
            if type(face) is not str or not face:
                raise ValueError("invalid stitched face ID")
            if (type(vertices) not in (list, tuple) or len(vertices) != 3
                    or any(type(v) is not str or v not in checked_nodes for v in vertices)
                    or len(set(vertices)) != 3):
                raise ValueError("invalid stitched triangle references")
            checked_faces[face] = tuple(vertices)
        edges = record["edges"]
        endpoints = record["edge_endpoints"]
        if type(edges) is not list or type(endpoints) is not dict:
            raise ValueError("invalid stitched edge directory")
        if len(set(edges)) != len(edges) or edges != sorted(edges):
            raise ValueError("unordered stitched edges")
        checked_endpoints = {}
        for edge in edges:
            if (type(edge) is not tuple or len(edge) != 2
                    or any(type(v) is not str or not v for v in edge)
                    or edge[0] == edge[1]):
                raise ValueError("invalid stitched edge identity")
            pair = endpoints.get(edge)
            if (type(pair) not in (list, tuple) or len(pair) != 2
                    or any(type(v) is not str or v not in checked_nodes for v in pair)
                    or pair[0] >= pair[1]):
                raise ValueError("invalid stitched edge endpoints")
            checked_endpoints[edge] = tuple(pair)
        if set(endpoints) != set(edges):
            raise ValueError("stitched edge endpoints do not match edges")
        face_edges = record["face_edges"]
        if type(face_edges) is not dict or set(face_edges) != set(checked_faces):
            raise ValueError("stitched face edge directory incomplete")
        checked_face_edges = {}
        per_edge = {}
        for face, vertices in checked_faces.items():
            terms = face_edges[face]
            if type(terms) not in (list, tuple) or len(terms) != 3:
                raise ValueError("three stitched edge terms required")
            built = []
            for position, item in enumerate(terms):
                if (type(item) is not dict or set(item) != {"edge", "sign"}
                        or type(item["edge"]) is not tuple
                        or item["edge"] not in checked_endpoints
                        or type(item["sign"]) is not int
                        or item["sign"] not in (-1, 1)):
                    raise ValueError("invalid stitched edge term")
                start, end = vertices[position], vertices[(position + 1) % 3]
                if tuple(sorted((start, end))) != checked_endpoints[item["edge"]]:
                    raise ValueError("stitched term disagrees with face winding")
                if item["sign"] != (1 if start < end else -1):
                    raise ValueError("incorrect stitched traversal sign")
                built.append(FaceEdge(item["edge"], item["sign"]))
                per_edge.setdefault(item["edge"], []).append((face, item["sign"]))
            if len({term.edge for term in built}) != 3:
                raise ValueError("stitched face repeats an edge")
            checked_face_edges[face] = tuple(built)
        if set(per_edge) != set(edges):
            raise ValueError("stitched edge without incident face")
        if any(len(items) > 2 for items in per_edge.values()):
            raise ValueError("stitched edge has more than two faces")
        source_faces = None
        if stitch is not None:
            face_map = stitch.get("face_map") if type(stitch) is dict else None
            if type(face_map) is not dict or set(face_map) != set(checked_faces):
                raise ValueError("stitch face mapping incomplete")
            source_faces = {}
            for face in checked_faces:
                entry = face_map[face]
                if (type(entry) is not dict
                        or set(entry) != {"sign", "corners"}
                        or type(entry["corners"]) is not tuple
                        or sorted(entry["corners"]) != [0, 1, 2]):
                    raise ValueError("invalid stitch face entry")
                source_faces[face] = entry["corners"]
        return cls(checked_nodes, checked_faces, checked_face_edges,
                   {edge: tuple(items) for edge, items in per_edge.items()},
                   checked_endpoints, source_faces)
