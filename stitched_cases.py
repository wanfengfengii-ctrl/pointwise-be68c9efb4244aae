"""Public inputs and hand expectations; no stitching implementation."""
from copy import deepcopy
from fractions import Fraction as F


def parallel(reverse=False):
    nodes = {"A": (F(0), F(0), F(0)), "B": (F(1), F(0), F(0)),
             "C": (F(0), F(1), F(0))}
    if reverse:
        nodes.update(X=nodes["B"], Y=nodes["A"], Z=nodes["C"])
        faces = {"f1": ("A", "B", "C"), "f2": ("Y", "X", "Z")}
        extra = {("X", "Y"): F(-1), ("X", "Z"): F(2), ("Y", "Z"): F(5)}
        seams = [(("A", "B"), ("Y", "X")), (("B", "C"), ("X", "Z"))]
        scalars = {"X": F(2), "Y": F(0), "Z": F(4)}
    else:
        nodes.update(X=nodes["A"], Y=nodes["B"], Z=nodes["C"])
        faces = {"f1": ("A", "B", "C"), "f2": ("X", "Y", "Z")}
        extra = {("X", "Y"): F(1), ("X", "Z"): F(5), ("Y", "Z"): F(2)}
        seams = [(("A", "B"), ("X", "Y")), (("B", "C"), ("Y", "Z"))]
        scalars = {"X": F(0), "Y": F(2), "Z": F(4)}
    fields = {"node_scalars": {"A": F(0), "B": F(2), "C": F(4), **scalars},
              "edge_integrals": {("A", "B"): F(1), ("A", "C"): F(2),
                                 ("B", "C"): F(2), **extra},
              "face_totals": {"f1": F(0), "f2": F(-3)}}
    samples = [{"face": "f2", "weights": (F(1, 2), F(1, 3), F(1, 6))}]
    return {"mesh": {"nodes": nodes, "faces": faces}, "fields": fields,
            "seams": seams, "samples": samples,
            "expected": {"vertices": 3, "edges": 4,
                         "circulations": {"f1": F(1), "f2": F(2)},
                         "boundary": F(3), "internal": F(0), "gap": F(6),
                         "sample": F(4, 3), "position": (F(1, 3), F(1, 6), F(0))}}


def zero_fields(mesh):
    edges = {tuple(sorted((v[i], v[(i + 1) % 3])))
             for v in mesh["faces"].values() for i in range(3)}
    return {"node_scalars": {k: F(0) for k in mesh["nodes"]},
            "edge_integrals": {k: F(0) for k in edges},
            "face_totals": {k: F(0) for k in mesh["faces"]}}


def strip(twisted):
    left = [(0, 0, 0), (1, 0, 0), (2, 0, 1)]
    right = [(0, 1, 0), (1, 1, 0), (2, 1, 1)]
    left.append(right[0] if twisted else left[0])
    right.append(left[0] if twisted else right[0])
    nodes = {f"{side}{i}": tuple(F(x) for x in xyz)
             for side, row in (("L", left), ("R", right))
             for i, xyz in enumerate(row)}
    faces = {}
    for i in range(3):
        faces[f"f{2*i}"] = (f"L{i}", f"R{i}", f"L{i+1}")
        faces[f"f{2*i+1}"] = (f"R{i}", f"R{i+1}", f"L{i+1}")
    mesh = {"nodes": nodes, "faces": faces}
    other = ("R3", "L3") if twisted else ("L3", "R3")
    return {"mesh": mesh, "fields": zero_fields(mesh),
            "seams": [(("L0", "R0"), other)], "samples": [],
            "expected": "reject" if twisted else {"vertices": 6, "edges": 12}}


def pinched():
    nodes = {"A": (0, 0, 0), "B": (1, 0, 0), "C": (0, 1, 0),
             "D": (-1, 0, 0), "E": (0, -1, 0)}
    mesh = {"nodes": {k: tuple(F(x) for x in xyz) for k, xyz in nodes.items()},
            "faces": {"f1": ("A", "B", "C"), "f2": ("A", "D", "E")}}
    return {"mesh": mesh, "fields": zero_fields(mesh), "seams": [],
            "samples": [], "expected": "reject"}


def cases():
    bad_node = deepcopy(parallel())
    bad_node["fields"]["node_scalars"]["X"] = F(1)
    bad_node["expected"] = "reject"
    bad_edge = deepcopy(parallel())
    bad_edge["fields"]["edge_integrals"][("X", "Y")] = F(7)
    bad_edge["expected"] = "reject"
    return {"parallel": parallel(), "reversed": parallel(True),
            "cylinder": strip(False), "mobius": strip(True), "pinched": pinched(),
            "node_conflict": bad_node, "edge_conflict": bad_edge}
