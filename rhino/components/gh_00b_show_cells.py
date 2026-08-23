"""GH Python 3, 00b SHOW CELLS: display the damage 00 read, on the cell part.

Wire 00's `timber` into `part`. The output names are the ones the viewer already
has, so nothing on the canvas needs rewiring:

    plane  mesh  cell_meshes  cell_centers  cell_bins  cell_damage  report

Colour it the way you already do: `cell_damage` into a Gradient, and that into
Custom Preview beside `cell_meshes`. `cell_colours` is provided if you would
rather not, and `only_damaged` hides the sound cells, which is usually what you
want on a post of several hundred cells.

Inputs
------
part          CellularizedPart   `timber` from 00
damage        float[]            optional; 00's `damage`, used only if the
                                 cells carry no damage_score of their own
threshold     float              cells at or above this read as decayed [0.50]
only_damaged  bool               show only those cells
show          bool               [True]
"""

plane = []
mesh = []
cell_meshes = []
cell_centers = []
cell_bins = []
cell_damage = []
cell_colours = []
report = ["00b Show cells"]


def _colour(value, gate):
    from System.Drawing import Color

    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0:
        return Color.FromArgb(50, 205, 205, 195)
    if value < gate:
        t = value / max(gate, 1e-6)
        return Color.FromArgb(150, int(205 + 50 * t), int(205 - 25 * t),
                              int(195 - 130 * t))
    t = (value - gate) / max(1.0 - gate, 1e-6)
    return Color.FromArgb(235, int(255 - 85 * t), int(180 - 150 * t),
                          int(65 - 45 * t))


try:
    from compas_rhino.conversions import (frame_to_rhino, mesh_to_rhino,
                                          point_to_rhino)

    if part is None:
        raise ValueError("connect `timber` from 00 to part")
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    if globals().get("show") is None:
        show = True

    net = part.cell_network
    keys = list(net.cells())
    if not keys:
        raise ValueError("this part has no cells")

    spare = [float(v) for v in (globals().get("damage") or [])]
    if spare and len(spare) != len(keys):
        report.append("WARNING: %d damage value(s) for %d cell(s), which "
                      "different run; ignoring it" % (len(spare), len(keys)))
        spare = []

    scores = []
    for index, key in enumerate(keys):
        value = net.cell_attribute(key, "damage_score")
        if value is None and index < len(spare):
            value = spare[index]
        scores.append(float(value or 0.0))

    hot = sum(1 for v in scores if v >= gate)
    report.append("%d cell(s), %d at or above %.2f, peak %.2f"
                  % (len(keys), hot, gate, max(scores) if scores else 0.0))
    if hot == 0:
        report.append("nothing at or above the threshold. Either 00 has not been "
                      "run, or the survey put no damage on this member")

    if show:
        mesh = [mesh_to_rhino(part.mesh)]
        plane = [frame_to_rhino(part.frame)]
        empty = 0
        for key, value in zip(keys, scores):
            if bool(globals().get("only_damaged")) and value < gate:
                continue
            # Irregular mesh when box_mode=False, otherwise the grid box.
            cell = net.cell_attribute(key, "cell_mesh") or net.cell_to_mesh(key)
            rhino_cell = mesh_to_rhino(cell) if cell is not None else None
            if rhino_cell is None:
                empty += 1
            cell_meshes.append(rhino_cell)
            cell_centers.append(point_to_rhino(net.cell_centroid(key)))
            cell_damage.append(value)
            cell_bins.append(0 if value <= 0.0 else (1 if value < gate else 2))
            cell_colours.append(_colour(value, gate))
        report.append("showing %d cell(s)%s" % (len(cell_meshes),
                      " (damaged only)" if globals().get("only_damaged") else ""))
        if empty:
            report.append("WARNING: %d cell(s) gave no mesh; cell_to_mesh "
                          "returned nothing for them" % empty)
except Exception as exc:
    report.append("ERROR: {}".format(exc))
