from compas.geometry import Frame, Polyline, Vector, cross_vectors
from compas_rhino.conversions import point_to_compas, vector_to_compas

import Rhino.Geometry as rg


def mesh_to_scanframes(mesh, robot_base_frame, scan_size=0.1, boustrophedon=True):
    """
    Convert a mesh surface into a grid of scan frames, spaced so each frame
    covers roughly `scan_size` x `scan_size` of surface area (e.g. 0.1 for
    a 10x10cm scanner footprint).

    A planar grid is built across the mesh's best-fit plane at `scan_size`
    spacing, each grid point is projected onto the mesh, and a Frame is
    built at the projected point with Z looking down into the surface
    (opposite the mesh normal). X (and therefore Y) is oriented consistently
    across all frames relative to `robot_base_frame`, rather than varying
    with local surface curvature or row direction.

    Parameters
    ----------
    mesh : Rhino.Geometry.Mesh
    robot_base_frame : compas.geometry.Frame
        Reference used so every frame's X axis is oriented to match the
        robot base's Y axis, keeping X/Y consistent across the whole grid.
    scan_size : float
        Target spacing between frames / scan footprint size, in the mesh's
        units (e.g. 0.1 for 10x10cm if the model is in meters).
    boustrophedon : bool
        If True, alternate rows are reversed in order (zig-zag traversal)
        for a continuous scan path. Frame orientation is unaffected.

    Returns
    -------
    rows : list[list[Frame]]
        Frames arranged row by row.
    path : compas.geometry.Polyline
        The frame points strung together in scan (traversal) order, for
        visualizing the path.
    """
    fit_plane = rg.Plane.FitPlaneToPoints(mesh.Vertices.ToPoint3dArray())[1]
    box = rg.Box(fit_plane, mesh)
    rxvec = robot_base_frame.yaxis

    u_count = max(1, int(round(box.X.Length / scan_size)))
    v_count = max(1, int(round(box.Y.Length / scan_size)))

    rows = []
    for j in range(v_count + 1):
        v = box.Y.Min + j * (box.Y.Length / v_count)
        row = []
        for i in range(u_count + 1):
            u = box.X.Min + i * (box.X.Length / u_count)
            test_pt = box.Plane.PointAt(u, v, 0)

            mesh_pt = mesh.ClosestMeshPoint(test_pt, 0.0)
            if mesh_pt is None:
                continue

            # negate so Z looks down into the surface, opposite the outward mesh normal
            normal = -vector_to_compas(mesh.NormalAt(mesh_pt)).unitized()
            # project the robot base's -Y onto the local tangent plane, so X follows
            # the surface curvature while still looking the same general direction
            xaxis = (rxvec - normal.scaled(rxvec.dot(normal))).unitized()
            yaxis = Vector(*cross_vectors(normal, xaxis))

            row.append(Frame(point_to_compas(mesh_pt.Point), xaxis, yaxis))

        if boustrophedon and j % 2:
            row.reverse()

        rows.append(row)

    path = Polyline([frame.point for row in rows for frame in row])
    return rows, path
