"""Generate damage fields on a cellularized beam.

Random noise makes uninterpretable test cases. Real decay has shapes, and the
shapes are what decide which repair is right -- rot rising from a sill wants a
splice, a weathered arris wants PJ3, a shake wants a bowtie. So the generator
produces named ARCHETYPES with randomised parameters: every case can be
labelled, and results can be reported per archetype rather than as an average
over nothing in particular.

All archetypes work in beam-local coordinates normalised to 0..1
(u across the width, v along the length, w across the height), so they are
independent of how the member sits in the world.

    cases = sample(frame, cells, n=50, seed=0)
    for c in cases:
        res, rep = placement.search(root, frame, cells, c["damage"], 0.5)
"""
from __future__ import annotations

import numpy as np

from . import scoring


# --------------------------------------------------------------- helpers
def local_unit(cells, frame):
    """World points -> (u, v, w) each normalised to 0..1 over the beam."""
    loc = scoring.to_local(cells, frame)
    ext = np.array([float(frame["width"]), float(frame["length"]),
                    float(frame["height"])], float)
    return loc / ext


def _blob(P, centre, radius, peak=1.0):
    d = np.linalg.norm((P - centre) / radius, axis=1)
    return peak * np.clip(1.0 - d, 0.0, 1.0)


# ------------------------------------------------------------ archetypes
def foot_rot(P, rng):
    """Decay rising from one end. The commonest failure on a post: water at
    the sill, worst at the foot, fading upward. Wants a splice."""
    end = rng.choice([0.0, 1.0])
    reach = rng.uniform(0.10, 0.30)
    v = P[:, 1] if end == 0.0 else 1.0 - P[:, 1]
    core_bias = rng.uniform(0.0, 0.4)      # 0 = through, 1 = surface only
    surf = np.maximum(np.abs(P[:, 0] - 0.5), np.abs(P[:, 2] - 0.5)) * 2.0
    d = np.clip(1.0 - v / reach, 0, 1) * (1.0 - core_bias * (1.0 - surf))
    return np.clip(d, 0, 1), {"end": float(end), "reach": reach}


def arris_weather(P, rng):
    """Along one arris, shallow and long. Exposed corners lose material first.
    Wants PJ3."""
    cu = rng.choice([0.0, 1.0])
    cw = rng.choice([0.0, 1.0])
    depth = rng.uniform(0.15, 0.35)
    v0 = rng.uniform(0.05, 0.55)
    v1 = v0 + rng.uniform(0.20, 0.40)
    du = np.abs(P[:, 0] - cu) / depth
    dw = np.abs(P[:, 2] - cw) / depth
    near = np.clip(1.0 - np.maximum(du, dw), 0, 1)
    inband = ((P[:, 1] > v0) & (P[:, 1] < v1)).astype(float)
    return np.clip(near * inband, 0, 1), {"arris": [float(cu), float(cw)],
                                          "v": [v0, v1]}


def face_pocket(P, rng):
    """A compact soft spot on one face. Wants PJ1."""
    face = rng.integers(0, 4)
    u = [0.08, 0.92, rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)][face]
    w = [rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8), 0.08, 0.92][face]
    v = rng.uniform(0.20, 0.80)
    r = np.array([rng.uniform(0.15, 0.35), rng.uniform(0.04, 0.10),
                  rng.uniform(0.15, 0.35)])
    return _blob(P, np.array([u, v, w]), r), {"centre": [u, v, w]}


def core_decay(P, rng):
    """Deep and compact, mid-span, reaching the core. Wants PJ4, or a splice
    if it goes through."""
    v = rng.uniform(0.30, 0.70)
    r = np.array([rng.uniform(0.35, 0.60), rng.uniform(0.05, 0.12),
                  rng.uniform(0.35, 0.60)])
    u = rng.uniform(0.35, 0.65)
    return _blob(P, np.array([u, v, 0.5]), r), {"v": v}


def shake(P, rng):
    """A split along the grain: thin in one section axis, long in v, through
    the depth. Wants PJ2, and nothing else will do."""
    u0 = rng.uniform(0.3, 0.7)
    thin = rng.uniform(0.06, 0.14)
    v0 = rng.uniform(0.10, 0.50)
    v1 = v0 + rng.uniform(0.20, 0.45)
    near = np.clip(1.0 - np.abs(P[:, 0] - u0) / thin, 0, 1)
    inband = ((P[:, 1] > v0) & (P[:, 1] < v1)).astype(float)
    return np.clip(near * inband, 0, 1), {"u": u0, "v": [v0, v1]}


def two_regions(P, rng):
    """Two separated pockets. A single splice must span both, so this is the
    case where two local repairs beat one long one -- and the search cannot
    see that, because it only ever places one joint."""
    a, _ = face_pocket(P, rng)
    b, _ = face_pocket(P, rng)
    return np.maximum(a, b), {"regions": 2}


def notch_rot(P, rng):
    """Decay around a mortise or notch -- all round the section at one
    station, where water collects in the joint."""
    v = rng.uniform(0.25, 0.75)
    half = rng.uniform(0.04, 0.09)
    near = np.clip(1.0 - np.abs(P[:, 1] - v) / half, 0, 1)
    return np.clip(near * rng.uniform(0.75, 1.0), 0, 1), {"v": v}


ARCHETYPES = {
    "foot_rot": foot_rot,
    "arris_weather": arris_weather,
    "face_pocket": face_pocket,
    "core_decay": core_decay,
    "shake": shake,
    "two_regions": two_regions,
    "notch_rot": notch_rot,
}


# ---------------------------------------------------------------- sample
def make(frame, cells, archetype, rng=None, speckle=0.04):
    """One damage field. Returns (damage, meta)."""
    rng = rng or np.random.default_rng(0)
    P = local_unit(cells, frame)
    d, meta = ARCHETYPES[archetype](P, rng)
    if speckle:
        d = np.clip(d + rng.uniform(-speckle, speckle, len(d)), 0, 1)
    meta = dict(meta, archetype=archetype)
    return d, meta


def sample(frame, cells, n=50, seed=0, archetypes=None, threshold=0.5,
           min_damaged=3):
    """n cases, cycling through the archetypes. Cases with too little damage
    to be worth searching are resampled rather than returned -- an empty case
    tells you nothing and skews any average."""
    rng = np.random.default_rng(seed)
    names = list(archetypes or ARCHETYPES)
    out = []
    tries = 0
    while len(out) < n and tries < 40 * n:
        tries += 1
        name = names[len(out) % len(names)]
        d, meta = make(frame, cells, name, rng)
        if int((d >= threshold).sum()) < min_damaged:
            continue
        meta["n_damaged"] = int((d >= threshold).sum())
        out.append({"damage": d, "meta": meta})
    return out
