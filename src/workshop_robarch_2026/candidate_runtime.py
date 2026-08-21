"""Execute a visible, LLM-authored Rhino geometry function.

Candidate code receives only ``ctx``, ``emit``, ``rg`` and ``math``.  It can
create any RhinoCommon geometry and any number of named outputs, while imports,
document mutation, file access, and hidden Python internals stay unavailable.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import sys
import time
from typing import Any, Iterable


MAX_CODE_CHARS = 40_000
MAX_EMITTED_GEOMETRIES = 200
BLOCKED_NAMES = {
    "__import__", "breakpoint", "compile", "eval", "exec", "globals",
    "input", "locals", "open", "os", "pathlib", "requests", "socket",
    "subprocess", "sys",
}
BLOCKED_NODES = (
    ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.While,
    ast.AsyncFunctionDef, ast.Await, ast.With, ast.AsyncWith,
    ast.ClassDef, ast.Yield, ast.YieldFrom,
)


class _RhinoCompatibility(ast.NodeTransformer):
    """Translate common CPython/Rhino Box dimension spellings."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        interval = {"Dx": "X", "Dy": "Y", "Dz": "Z"}.get(node.attr)
        if not interval:
            return node
        replacement = ast.Attribute(
            value=ast.Attribute(value=node.value, attr=interval, ctx=ast.Load()),
            attr="Length",
            ctx=node.ctx,
        )
        return ast.copy_location(replacement, node)


def compatible_tree(source: str) -> ast.Module:
    """Validate source, then apply small RhinoCommon compatibility translations."""
    return ast.fix_missing_locations(_RhinoCompatibility().visit(validate_code(source)))


def _as_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("expected one JSON object")
    return parsed


def validate_code(source: str) -> ast.Module:
    """Validate the small candidate-code contract before executing it."""
    code = str(source or "").strip()
    if not code:
        raise ValueError("candidate_code is empty")
    if len(code) > MAX_CODE_CHARS:
        raise ValueError("candidate_code exceeds {} characters".format(MAX_CODE_CHARS))
    tree = ast.parse(code, filename="<repair-candidate>", mode="exec")
    builders = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_candidate"
    ]
    if len(builders) != 1:
        raise ValueError("define exactly one build_candidate(ctx, emit) function")
    for node in tree.body:
        is_docstring = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if not isinstance(node, ast.FunctionDef) and not is_docstring:
            raise ValueError("candidate code may only define functions at module scope")
    args = builders[0].args
    if len(args.args) != 2 or [item.arg for item in args.args] != ["ctx", "emit"]:
        raise ValueError("build_candidate must have exactly the arguments ctx, emit")
    for node in ast.walk(tree):
        if isinstance(node, BLOCKED_NODES):
            raise ValueError("{} is unavailable in candidate code".format(type(node).__name__))
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise ValueError("name {!r} is unavailable in candidate code".format(node.id))
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("private attributes are unavailable in candidate code")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_NAMES:
                raise ValueError("call {!r} is unavailable in candidate code".format(node.func.id))
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            decorators = getattr(node, "decorator_list", [])
            args = node.args
            annotations = [item.annotation for item in args.args + args.kwonlyargs]
            annotations.extend([args.vararg.annotation if args.vararg else None, args.kwarg.annotation if args.kwarg else None])
            if decorators or args.defaults or args.kw_defaults.count(None) != len(args.kw_defaults):
                raise ValueError("candidate functions cannot use decorators or default arguments")
            if any(annotation is not None for annotation in annotations) or getattr(node, "returns", None) is not None:
                raise ValueError("candidate functions cannot use evaluated annotations")
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == function.name
            for node in ast.walk(function)
        ):
            raise ValueError("recursive candidate functions are unavailable")
    return tree


def duplicate_geometry(value: Any) -> Any:
    """Duplicate common Rhino objects without modifying the document."""
    if value is None:
        return None
    for name in ("Duplicate", "DuplicateGeometry", "DuplicateBrep", "DuplicateCurve"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                pass
    return value


def coerce_geometry(value: Any) -> Any:
    """Convert Rhino value structs into previewable GeometryBase objects."""
    if value is None or hasattr(value, "GetBoundingBox"):
        return value
    import Rhino.Geometry as rg

    if isinstance(value, rg.Point3d):
        return rg.Point(value)
    for name in ("ToBrep", "ToNurbsCurve", "ToPolylineCurve"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                converted = method()
                if converted is not None and hasattr(converted, "GetBoundingBox"):
                    return converted
            except TypeError:
                pass
    return value


def box_from_context(context: dict, value: Any = None) -> Any:
    """Use a live Box when available, otherwise rebuild its recorded local frame."""
    if all(hasattr(value, name) for name in ("Plane", "X", "Y", "Z")):
        return value
    summary = ((context or {}).get("rhinoContext") or {}).get("targetBox") or {}
    try:
        import Rhino.Geometry as rg

        center = summary["center"]
        axes = summary["axes"]
        size = summary["size"]
        plane = rg.Plane(
            rg.Point3d(*center), rg.Vector3d(*axes["x"]), rg.Vector3d(*axes["y"])
        )
        return rg.Box(
            plane,
            rg.Interval(-0.5 * float(size["x"]), 0.5 * float(size["x"])),
            rg.Interval(-0.5 * float(size["y"]), 0.5 * float(size["y"])),
            rg.Interval(-0.5 * float(size["z"]), 0.5 * float(size["z"])),
        )
    except Exception:
        raise ValueError(
            "box is a Rhino GUID; right-click the GH Python box input and set Type hint to Box"
        )


def _xyz(value: Any) -> tuple[float, float, float]:
    if hasattr(value, "X"):
        return float(value.X), float(value.Y), float(value.Z)
    return float(value[0]), float(value[1]), float(value[2])


def runtime_signature(*values: Any) -> str:
    """Fingerprint JSON values and Rhino inputs for a cached local stage."""
    def compact(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): compact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [compact(item) for item in value]
        if all(hasattr(value, name) for name in ("Plane", "X", "Y", "Z")):
            plane = value.Plane
            return {
                "type": type(value).__name__,
                "origin": compact(plane.Origin),
                "axes": [compact(plane.XAxis), compact(plane.YAxis), compact(plane.ZAxis)],
                "intervals": [
                    [round(float(interval.Min), 9), round(float(interval.Max), 9)]
                    for interval in (value.X, value.Y, value.Z)
                ],
            }
        if hasattr(value, "X") and hasattr(value, "Y") and hasattr(value, "Z"):
            return [round(float(value.X), 9), round(float(value.Y), 9), round(float(value.Z), 9)]
        if hasattr(value, "GetBoundingBox"):
            box = value.GetBoundingBox(True)
            crc = None
            if hasattr(value, "DataCRC"):
                try:
                    crc = int(value.DataCRC(0))
                except Exception:
                    pass
            return {
                "type": type(value).__name__,
                "min": compact(box.Min),
                "max": compact(box.Max),
                "dataCRC": crc,
            }
        if hasattr(value, "ToBrep"):
            try:
                return compact(value.ToBrep())
            except Exception:
                return {"type": type(value).__name__, "unresolved": True}
        return str(value)

    payload = json.dumps(compact(values), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CandidateContext:
    """Read-only inputs and concise RhinoCommon helpers for candidate code."""

    def __init__(
        self,
        session: dict,
        box: Any,
        centers: Iterable[Any],
        damage: Iterable[float],
        threshold: float,
        neighbours: Iterable[Any] = (),
        neighbour_ids: Iterable[str] = (),
        tolerance: float | None = None,
    ) -> None:
        import Rhino
        import Rhino.Geometry as rg

        if box is None:
            raise ValueError("connect box from BEAM CELLS")
        self.session = dict(session or {})
        self.session.pop("workspaceSource", None)
        self.beam_id = str(
            self.session.get("beam_id") or self.session.get("beamId") or ""
        )
        self.box = rg.Box(box) if isinstance(box, rg.Box) else duplicate_geometry(box)
        self.target = box.ToBrep() if hasattr(box, "ToBrep") else duplicate_geometry(box)
        self.centers = list(centers or [])
        self.damage = [float(value) for value in (damage or [])]
        if len(self.centers) != len(self.damage):
            raise ValueError(
                "centers/damage length mismatch: {} vs {}".format(
                    len(self.centers), len(self.damage)
                )
            )
        self.threshold = float(threshold)
        doc = Rhino.RhinoDoc.ActiveDoc
        self.tolerance = float(
            tolerance if tolerance is not None else getattr(doc, "ModelAbsoluteTolerance", 0.01)
        )
        from . import repair_candidate

        cell_payload = [
            [list(_xyz(point)), value]
            for point, value in zip(self.centers, self.damage)
        ]
        cell_hash = repair_candidate.stable_json_hash(cell_payload)
        if self.session.get("cellDataHash") and self.session["cellDataHash"] != cell_hash:
            raise ValueError("centers/damage do not match session_json; rebuild Repair Context")
        if self.session.get("threshold") is not None and not math.isclose(
            float(self.session["threshold"]), self.threshold, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("threshold does not match session_json; rebuild Repair Context")
        if self.session.get("rhinoTolerance") is not None and not math.isclose(
            float(self.session["rhinoTolerance"]), self.tolerance, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("Rhino tolerance changed; rebuild Repair Context")
        ids = [str(value) for value in (neighbour_ids or [])]
        geos = [duplicate_geometry(value) for value in (neighbours or [])]
        if len(ids) != len(geos):
            raise ValueError(
                "neighbour_ids/neighbour_geometry length mismatch: {} vs {}".format(
                    len(ids), len(geos)
                )
            )
        if any(not value.strip() for value in ids):
            raise ValueError("neighbour_ids must be non-empty exact Workspace part ids")
        self.neighbours = list(geos)
        self.neighbour_ids = ids
        self._neighbours = {
            ids[index]: geometry
            for index, geometry in enumerate(geos)
        }
        self.analysis_input_hash = runtime_signature(
            self.box,
            self.centers,
            self.damage,
            self.threshold,
            self.neighbours,
            self.neighbour_ids,
            self.tolerance,
        )

        plane = box.Plane
        axes = [plane.XAxis, plane.YAxis, plane.ZAxis]
        intervals = [box.X, box.Y, box.Z]
        lengths = [float(interval.Length) for interval in intervals]
        axis_index = max(range(3), key=lambda index: lengths[index])
        section_indices = [index for index in range(3) if index != axis_index]
        self.member_axis = rg.Vector3d(axes[axis_index])
        self.section_u = rg.Vector3d(axes[section_indices[0]])
        self.section_v = rg.Vector3d(axes[section_indices[1]])
        self.length = lengths[axis_index]
        self.section_size = (lengths[section_indices[0]], lengths[section_indices[1]])
        corner = rg.Point3d(plane.Origin)
        for axis, interval in zip(axes, intervals):
            corner += float(interval.Min) * axis
        self.start = rg.Point3d(corner)
        self.start += 0.5 * lengths[section_indices[0]] * axes[section_indices[0]]
        self.start += 0.5 * lengths[section_indices[1]] * axes[section_indices[1]]
        self.end = self.start + self.length * self.member_axis

    @property
    def damaged_points(self) -> list[Any]:
        return [
            point for point, value in zip(self.centers, self.damage)
            if value >= self.threshold
        ]

    @property
    def sound_points(self) -> list[Any]:
        return [
            point for point, value in zip(self.centers, self.damage)
            if value < self.threshold
        ]

    def point_at(self, fraction: float) -> Any:
        return self.start + float(fraction) * self.length * self.member_axis

    def plane_at(self, fraction: float) -> Any:
        import Rhino.Geometry as rg

        return rg.Plane(self.point_at(fraction), self.member_axis)

    def neighbour(self, part_id: str) -> Any:
        value = self._neighbours.get(str(part_id))
        return duplicate_geometry(value)

    def copy(self, geometry: Any) -> Any:
        return duplicate_geometry(geometry)

    def union(self, geometries: Iterable[Any]) -> list[Any]:
        import Rhino.Geometry as rg

        values = [_as_brep(value) for value in geometries]
        values = [value for value in values if value is not None]
        if len(values) <= 1:
            return [duplicate_geometry(value) for value in values]
        result = list(rg.Brep.CreateBooleanUnion(values, self.tolerance) or [])
        if not result:
            raise ValueError("boolean union failed at the current Rhino tolerance")
        return result

    def difference(self, base: Any, cutters: Iterable[Any]) -> list[Any]:
        import Rhino.Geometry as rg

        current = [_as_brep(base)]
        for cutter in cutters:
            next_values = []
            for item in current:
                cutting = _as_brep(cutter)
                result = list(rg.Brep.CreateBooleanDifference(item, cutting, self.tolerance) or [])
                if result:
                    next_values.extend(result)
                elif _boxes_disjoint(item, cutting, self.tolerance):
                    next_values.append(duplicate_geometry(item))
                else:
                    raise ValueError("boolean difference failed at the current Rhino tolerance")
            current = next_values
        return current

    def intersection(self, first: Any, second: Any) -> list[Any]:
        import Rhino.Geometry as rg

        return list(
            rg.Brep.CreateBooleanIntersection(
                _as_brep(first), _as_brep(second), self.tolerance
            ) or []
        )


def _as_brep(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "ToBrep"):
        return value.ToBrep()
    return value


def _boxes_disjoint(first: Any, second: Any, tolerance: float) -> bool:
    a, b = first.GetBoundingBox(True), second.GetBoundingBox(True)
    return (
        a.Max.X < b.Min.X - tolerance or b.Max.X < a.Min.X - tolerance
        or a.Max.Y < b.Min.Y - tolerance or b.Max.Y < a.Min.Y - tolerance
        or a.Max.Z < b.Min.Z - tolerance or b.Max.Z < a.Min.Z - tolerance
    )


def _declarations(candidate: dict) -> dict[str, dict]:
    raw = candidate.get("entities") or candidate.get("outputs") or []
    if isinstance(raw, dict):
        raw = [dict(value, id=key) for key, value in raw.items() if isinstance(value, dict)]
    return {
        str(item.get("id")): item
        for item in raw
        if isinstance(item, dict) and item.get("id")
    }


def execute_candidate(
    session: Any,
    candidate: Any,
    code: str,
    box: Any,
    centers: Iterable[Any],
    damage: Iterable[float],
    threshold: float,
    neighbour_geometry: Iterable[Any] = (),
    neighbour_ids: Iterable[str] = (),
) -> tuple[list[Any], list[dict], dict, list[str]]:
    """Execute one candidate and return geometry, aligned entity records, and a run record."""
    import Rhino.Geometry as rg

    session_obj = _as_object(session)
    from . import repair_candidate

    selected_beam = str(session_obj.get("beamId") or session_obj.get("beam_id") or "")
    candidate_obj = repair_candidate.validate_scope(
        _as_object(candidate),
        beam_id=selected_beam,
        part_ids=session_obj.get("partIds"),
        action_ids=session_obj.get("actionIds"),
        workspace_hash=session_obj.get("workspaceHash"),
        context_hash=session_obj.get("contextHash"),
    )
    tree = compatible_tree(code)
    ctx = CandidateContext(
        session_obj, box, centers, damage, threshold,
        neighbours=neighbour_geometry, neighbour_ids=neighbour_ids,
    )
    declarations = _declarations(candidate_obj)
    geometry: list[Any] = []
    entities: list[dict] = []
    report: list[str] = []
    called_ids: set[str] = set()

    def emit(
        entity_id: str,
        value: Any,
        role: str = "",
        effect: str = "",
        purpose: str = "",
        relates_to: Iterable[str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        group_id = str(entity_id or "").strip()
        if not group_id:
            raise ValueError("emit id must be a non-empty string")
        if group_id in called_ids:
            raise ValueError("emit id {!r} was used more than once".format(group_id))
        called_ids.add(group_id)
        if isinstance(value, (list, tuple)):
            values = list(value)
        elif value is not None and not hasattr(value, "GetBoundingBox") and not hasattr(value, "ToBrep"):
            try:
                values = list(value)  # RhinoCommon often returns a .NET geometry array.
            except TypeError:
                values = [value]
        else:
            values = [value]
        declaration = declarations.get(group_id, {})
        values = [item for item in values if item is not None]
        if not values:
            report.append("{} emitted no geometry".format(group_id))
            return
        relation_values = [str(item) for item in (relates_to or declaration.get("relatesTo") or [])]
        metadata_value = dict(declaration.get("metadata") or {}, **dict(metadata or {}))
        json.dumps({"relatesTo": relation_values, "metadata": metadata_value}, allow_nan=False)
        for index, item in enumerate(values):
            if len(geometry) >= MAX_EMITTED_GEOMETRIES:
                raise RuntimeError("candidate emitted more than {} geometries".format(MAX_EMITTED_GEOMETRIES))
            item = coerce_geometry(item)
            if not hasattr(item, "GetBoundingBox"):
                raise TypeError("{} is not Rhino geometry".format(group_id))
            runtime_id = group_id if len(values) == 1 else "{}:{}".format(group_id, index + 1)
            record = {
                "id": runtime_id,
                "groupId": group_id,
                "geometryIndex": len(geometry),
                "geometryType": type(item).__name__,
                "role": str(role or declaration.get("role") or "unspecified"),
                "effect": str(effect or declaration.get("effect") or "unspecified"),
                "materialEffect": declaration.get("materialEffect"),
                "purpose": str(purpose or declaration.get("purpose") or ""),
                "relatesTo": relation_values,
                "partRefs": [str(value) for value in (declaration.get("partRefs") or [])],
                "actionRefs": [str(value) for value in (declaration.get("actionRefs") or [])],
                "metadata": metadata_value,
            }
            geometry.append(item)
            entities.append(record)

    def safe_range(*args: int) -> range:
        values = range(*args)
        if len(values) > 10_000:
            raise ValueError("candidate range is limited to 10000 iterations")
        return values

    safe_builtins = {
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
        "enumerate": enumerate, "Exception": Exception, "float": float,
        "hasattr": hasattr, "getattr": getattr, "int": int, "len": len,
        "list": list, "max": max, "min": min,
        "range": safe_range, "round": round, "set": set, "sorted": sorted,
        "str": str, "sum": sum, "tuple": tuple, "ValueError": ValueError,
        "zip": zip,
    }
    namespace = {"__builtins__": safe_builtins, "rg": rg, "math": math}
    started = time.time()
    exec(compile(tree, "<repair-candidate>", "exec"), namespace, namespace)
    steps = [0]

    def budget_trace(frame: Any, event: str, arg: Any):
        steps[0] += 1
        if steps[0] > 250_000 or time.time() - started > 10.0:
            raise RuntimeError("candidate execution exceeded its local step/time budget")
        return budget_trace

    previous_trace = sys.gettrace()
    try:
        sys.settrace(budget_trace)
        namespace["build_candidate"](ctx, emit)
    finally:
        sys.settrace(previous_trace)
    elapsed = round(time.time() - started, 4)

    emitted_groups = {item["groupId"] for item in entities}
    required_groups = {
        key for key, value in declarations.items() if not bool(value.get("optional"))
    }
    missing = sorted(required_groups - emitted_groups)
    optional_missing = sorted(set(declarations) - required_groups - emitted_groups)
    undeclared = sorted(emitted_groups - set(declarations))
    if missing:
        raise ValueError("declared output was not emitted: {}".format(", ".join(missing)))
    if undeclared:
        raise ValueError("emitted output lacks a manifest declaration: {}".format(", ".join(undeclared)))
    if optional_missing:
        report.append("optional output not emitted: {}".format(", ".join(optional_missing)))
    report.insert(0, "executed {} geometry item(s) in {:.3f}s".format(len(geometry), elapsed))
    execution = {
        "schema": "repair-candidate-execution@1",
        "candidateId": candidate_obj.get("id"),
        "manifestHash": repair_candidate.stable_json_hash(candidate_obj),
        "codeHash": repair_candidate.stable_json_hash(str(code or "")),
        "sessionHash": repair_candidate.stable_json_hash(
            {key: value for key, value in session_obj.items() if key != "workspaceSource"}
        ),
        "geometryHash": runtime_signature(geometry),
        "entitiesHash": repair_candidate.stable_json_hash(entities),
        "analysisInputHash": ctx.analysis_input_hash,
        "beamId": ctx.beam_id,
        "workspaceHash": session_obj.get("workspaceHash"),
        "contextHash": session_obj.get("contextHash"),
        "cellDataHash": session_obj.get("cellDataHash"),
        "status": "complete",
        "geometryCount": len(geometry),
        "entityCount": len(entities),
        "tolerance": ctx.tolerance,
        "elapsedSeconds": elapsed,
        "messages": list(report),
    }
    return geometry, entities, execution, report


def entity_envelope(execution: dict, entities: Iterable[dict]) -> dict:
    """Bind aligned runtime entity metadata to one execution identity."""
    from . import repair_candidate

    return {
        "schema": "repair-candidate-entities@1",
        **{
            key: execution.get(key)
            for key in repair_candidate.EXECUTION_IDENTITY_FIELDS
        },
        "entities": [dict(item) for item in entities],
    }
