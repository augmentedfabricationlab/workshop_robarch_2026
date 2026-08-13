import math

from compas.geometry import Frame, Polyline, Vector, cross_vectors
from compas_rhino.conversions import point_to_compas, vector_to_compas

import Rhino.Geometry as rg


def _rotated(vector, axis, angle):
    """Rotate `vector` by `angle` radians around unit `axis` (Rodrigues' formula)."""
    axis = axis.unitized()
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    term1 = vector.scaled(cos_a)
    term2 = Vector(*cross_vectors(axis, vector)).scaled(sin_a)
    term3 = axis.scaled(axis.dot(vector) * (1 - cos_a))
    return term1 + term2 + term3


def _with_lead_in(row, direction, lead_in, reversed_row, xaxis, yaxis):
    """
    Add a stand-off frame just before wherever `row`'s traversal actually
    starts (the end nearer the mesh edge, accounting for boustrophedon
    reversal), offset `lead_in` back along `direction` in free space
    (not reprojected onto the mesh).
    """
    if not row:
        return row
    start_frame = row[-1] if reversed_row else row[0]
    offset = direction.scaled(lead_in) if reversed_row else direction.scaled(-lead_in)
    lead_in_frame = Frame(start_frame.point + offset, xaxis, yaxis)
    if reversed_row:
        row.append(lead_in_frame)
    else:
        row.insert(0, lead_in_frame)
    return row


def mesh_to_scanframes(mesh, robot_base_frame, scan_size=0.08, boustrophedon=True, tilt_deg=30, mode="stepwise", flip=False):
    """
    Convert a mesh surface into a grid of scan frames.

    A planar grid is built across the mesh's best-fit plane, each grid
    point is projected onto the mesh for its position. Every frame shares
    the same fixed orientation: X points along the mesh box's short side,
    and Z is `robot_base_frame`'s -Z axis (straight down) tilted `tilt_deg`
    degrees around that X axis, so it looks down into the surface at an
    angle rather than straight down. Orientation does not vary with local
    mesh curvature or row/column position.

    Rows always run along whichever of the mesh's X/Y extents is longer
    (the "row direction"), and are stepped `scan_size` apart along the
    shorter extent.

    Two traversal modes are supported:

    - "stepwise": rows are also subdivided by `scan_size` along the row
      direction, giving a full grid. Meant for point-and-shoot sensors
      (e.g. a RealSense) that capture at each discrete frame.
    - "sweep": each row only contains a start and end frame (the sensor
      sweeps continuously between them, so no intermediate steps are
      generated), matching a linear sensor `scan_size` wide.

    In both modes each row gets an extra lead-in frame at whichever end its
    traversal starts from, offset `0.5 * scan_size` back in free space so
    the scan approaches from just off the surface rather than starting
    exactly on it.

    Parameters
    ----------
    mesh : Rhino.Geometry.Mesh
    robot_base_frame : compas.geometry.Frame
        Reference frame every scan frame's orientation is fixed relative to.
    scan_size : float
        In "stepwise" mode, the target spacing between frames / scan
        footprint size in both directions. In "sweep" mode, the spacing
        between passes (i.e. the linear sensor's width), in the mesh's
        units (e.g. 0.08 for an 8cm-wide sensor).
    boustrophedon : bool
        If True, alternate rows are reversed in order (zig-zag traversal)
        for a continuous scan path. Frame orientation is unaffected.
    tilt_deg : float
        Fixed angle, in degrees, that each frame's Z axis is tilted around
        its own X axis away from straight down (opposite the robot base's
        Z axis). 0 points straight down; 90 lies flat, perpendicular to
        straight down. Flip the sign to tilt the other way around X.
    mode : str
        "stepwise" or "sweep", see above.
    flip : bool
        If True, look from the opposite side of the mesh: X (and therefore
        Z) reverses direction, while Y is unchanged. Nothing about the mesh
        geometry indicates which side it's meant to be scanned from, so use
        this to pick the correct side for a given setup.

    Returns
    -------
    rows : list[list[Frame]]
        Frames arranged row by row.
    path : compas.geometry.Polyline
        The frame points strung together in scan (traversal) order, for
        visualizing the path.
    """
    if mode not in ("stepwise", "sweep"):
        raise ValueError("mode must be 'stepwise' or 'sweep', got {!r}".format(mode))
    verts = mesh.Vertices.ToPoint3dArray()
    fit_plane = rg.Plane.FitPlaneToPoints(verts)[1]

    # FitPlaneToPoints only fixes the plane's normal; its in-plane X/Y axes
    # are arbitrary and don't track the mesh's in-plane rotation. Align them
    # to the mesh's principal in-plane axes (via PCA) so the box - and every
    # frame derived from it - rotates together with the mesh.
    uv = [fit_plane.ClosestParameter(pt)[1:] for pt in verts]
    mean_s = sum(s for s, t in uv) / len(uv)
    mean_t = sum(t for s, t in uv) / len(uv)
    sxx = sum((s - mean_s) ** 2 for s, t in uv)
    syy = sum((t - mean_t) ** 2 for s, t in uv)
    sxy = sum((s - mean_s) * (t - mean_t) for s, t in uv)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    fit_plane.Rotate(theta, fit_plane.ZAxis)

    box = rg.Box(fit_plane, mesh)
    lead_in = 0.5 * scan_size

    # rows run along whichever extent is longer; the other extent is the
    # direction rows are stepped/stacked along
    row_along_x = box.X.Length >= box.Y.Length
    if row_along_x:
        row_min, row_length = box.X.Min, box.X.Length
        step_min, step_length = box.Y.Min, box.Y.Length
        row_dir = vector_to_compas(box.Plane.XAxis).unitized()
        rxvec = vector_to_compas(box.Plane.YAxis).unitized()

        def point_at(row_val, step_val):
            return box.Plane.PointAt(row_val, step_val, 0)
    else:
        row_min, row_length = box.Y.Min, box.Y.Length
        step_min, step_length = box.X.Min, box.X.Length
        row_dir = vector_to_compas(box.Plane.YAxis).unitized()
        rxvec = vector_to_compas(box.Plane.XAxis).unitized()

        def point_at(row_val, step_val):
            return box.Plane.PointAt(step_val, row_val, 0)

    step_count = max(1, int(round(step_length / scan_size)))

    # fixed orientation shared by every frame: xaxis follows the mesh box's
    # short side; Z is tilted `tilt_deg` around that xaxis, away from
    # straight down (opposite the robot base's Z axis)
    xaxis = rxvec.unitized()
    tilt_angle = math.radians(tilt_deg)
    normal = _rotated(-robot_base_frame.zaxis, xaxis, -tilt_angle).unitized()
    yaxis = Vector(*cross_vectors(normal, xaxis))

    # `flip` looks from the opposite side of the mesh: X reverses direction
    # (and so, implicitly, does Z) while Y is left unchanged
    if flip:
        xaxis = xaxis.scaled(-1)

    if mode == "stepwise":
        row_count = max(1, int(round(row_length / scan_size)))

        def row_values():
            return [row_min + i * (row_length / row_count) for i in range(row_count + 1)]
    else:  # sweep

        def row_values():
            return (row_min, row_min + row_length)

    rows = []
    for j in range(step_count + 1):
        step_val = step_min + j * (step_length / step_count)
        row = []
        for row_val in row_values():
            test_pt = point_at(row_val, step_val)

            mesh_pt = mesh.ClosestMeshPoint(test_pt, 0.0)
            if mesh_pt is None:
                continue

            row.append(Frame(point_to_compas(mesh_pt.Point), xaxis, yaxis))

        reversed_row = boustrophedon and j % 2
        _with_lead_in(row, row_dir, lead_in, reversed_row, xaxis, yaxis)

        if reversed_row:
            row.reverse()

        rows.append(row)

    path = Polyline([frame.point for row in rows for frame in row])
    return rows, path
