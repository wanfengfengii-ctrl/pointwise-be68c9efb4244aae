from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from surface.mesh.incidence import canonical_edge
from surface.mesh.model import StitchedMesh


def validate_coverage(mesh, values):
    expected = (set(mesh.nodes), set(mesh.edges), set(mesh.faces))
    actual = (set(values.node_scalars), set(values.edge_integrals),
              set(values.face_totals))
    if expected != actual:
        raise ValueError("field identities must exactly cover the ordinary mesh")
    for mapping in (values.node_scalars, values.edge_integrals, values.face_totals):
        if any(not isinstance(value, Fraction) for value in mapping.values()):
            raise ValueError("field values must be Fractions")


@dataclass(frozen=True)
class FieldValues:
    node_scalars: object
    edge_integrals: object
    face_totals: object

    def __post_init__(self):
        for name in ("node_scalars", "edge_integrals", "face_totals"):
            object.__setattr__(self, name, MappingProxyType(
                dict(sorted(getattr(self, name).items()))))

    def to_record(self):
        return {name: dict(getattr(self, name))
                for name in ("node_scalars", "edge_integrals", "face_totals")}


@dataclass(frozen=True)
class FieldBundle:
    revision_id: int
    mesh: object
    values: FieldValues

    def _ordinary_edge_value(self, start, end):
        key = canonical_edge(start, end)
        value = self.values.edge_integrals[key]
        return value if (start, end) == key else -value

    def _stitched_edge_value(self, start, end, edge_id):
        nodes = self.mesh.nodes
        if type(start) is not str or type(end) is not str:
            raise KeyError((start, end))
        if start not in nodes or end not in nodes:
            raise KeyError((start, end))
        if start == end:
            raise ValueError("edge lookup requires two distinct node IDs")
        integrals = self.values.edge_integrals
        if edge_id is not None:
            if type(edge_id) is not tuple or edge_id not in integrals:
                raise KeyError(edge_id)
            endpoints = self.mesh.edge_endpoints[edge_id]
            if tuple(sorted((start, end))) != endpoints:
                raise ValueError("endpoints do not belong to the requested edge")
            value = integrals[edge_id]
            return value if (start, end) == endpoints else -value
        wanted = canonical_edge(start, end)
        candidates = sorted(edge for edge, pair in self.mesh.edge_endpoints.items()
                            if pair == wanted)
        if not candidates:
            raise KeyError((start, end))
        if len(candidates) > 1:
            raise ValueError("parallel edge selection requires explicit edge_id")
        edge = candidates[0]
        value = integrals[edge]
        return value if (start, end) == wanted else -value

    def edge_value(self, start, end, *, edge_id=None):
        if isinstance(self.mesh, StitchedMesh):
            return self._stitched_edge_value(start, end, edge_id)
        if edge_id is not None:
            raise ValueError("ordinary surface edges have no independent identity")
        return self._ordinary_edge_value(start, end)


def bind_fields(revision_id, mesh, values):
    if type(revision_id) is not int or revision_id < 1:
        raise ValueError("invalid revision ID")
    validate_coverage(mesh, values)
    return FieldBundle(revision_id, mesh, values)
