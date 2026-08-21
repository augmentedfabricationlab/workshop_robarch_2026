"""Package accepted Rhino candidate geometry as a small, named 3dm artifact."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from . import repair_candidate
from .candidate_runtime import coerce_geometry


def build_3dm_bytes(
    proposal_id: str,
    geometry: Iterable[Any],
    entities: Iterable[dict],
    geometry_hash: str,
) -> tuple[bytes, dict]:
    """Return one 3dm payload and its portable Workspace reference."""
    import Rhino
    import Rhino.DocObjects as rd

    items = [coerce_geometry(item) for item in geometry]
    records = [dict(item) for item in entities]
    if not items:
        raise ValueError("candidate_geometry is empty")
    if len(items) != len(records):
        raise ValueError("candidate_geometry/entity_json length mismatch")

    model = Rhino.FileIO.File3dm()
    model.ApplicationName = "ROBARCH staged repair joinery"
    model.ApplicationDetails = str(proposal_id)
    for index, (item, record) in enumerate(zip(items, records)):
        if int(record.get("geometryIndex", -1)) != index:
            raise ValueError("entity_json geometryIndex order is invalid")
        attributes = rd.ObjectAttributes()
        attributes.Name = str(record.get("id") or "geometry_{}".format(index + 1))
        for key in ("role", "effect", "materialEffect", "purpose", "groupId"):
            if record.get(key) is not None:
                attributes.SetUserString("robarch.{}".format(key), str(record[key]))
        attributes.SetUserString(
            "robarch.refs",
            json.dumps(
                {
                    "partRefs": record.get("partRefs") or [],
                    "actionRefs": record.get("actionRefs") or [],
                    "relatesTo": record.get("relatesTo") or [],
                },
                ensure_ascii=False,
            ),
        )
        object_id = model.Objects.Add(item, attributes)
        if not object_id or str(object_id) == "00000000-0000-0000-0000-000000000000":
            raise ValueError("could not add {} to the 3dm artifact".format(attributes.Name))

    with tempfile.TemporaryDirectory(prefix="robarch_geometry_") as folder:
        path = Path(folder) / "candidate.3dm"
        if not model.Write(str(path), 0):
            raise ValueError("Rhino could not write the candidate 3dm artifact")
        payload = path.read_bytes()

    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(proposal_id)).strip("_")
    archive_path = "repair_geometry/{}.3dm".format(safe_id or "candidate")
    metadata = {
        "schema": "repair-geometry-artifact@1",
        "path": archive_path,
        "format": "3dm",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "geometryHash": str(geometry_hash),
        "entitiesHash": repair_candidate.stable_json_hash(records),
        "entityCount": len(items),
    }
    return payload, metadata


__all__ = ["build_3dm_bytes"]
