"""Workshop RobArch 2026: AI assisted repair joinery for historic timber.

The Grasshopper pipeline (components 00 to 04) uses these modules:

    agents      the Gemini calls, the prompt files and the joint corpus
    context     the Workspace: parts, conditions, evidence, the repair plan
    damagemap   survey regions painted onto the cells
    joinery     joints as planes, their placement and every measurement
    kernel      the cut definitions and the point classifier
    neighbours  member frames, part boxes, and what touches what
    scoring     point in solid tests
    evaluator   turns the cut expressions into Rhino Breps

The other modules belong to the manual, algorithmic and scanning definitions.
"""

# The point cloud publisher needs roslibpy, and scanframes needs compas. Both
# are installed in Rhino's Python but not in every plain interpreter, so
# importing this package must not depend on either of them.
try:
    from .pointcloud_publisher import *  # noqa: F401,F403
except ImportError:  # pragma: no cover - roslibpy absent
    pass

try:
    from .scanframes import *  # noqa: F401,F403
except ImportError:  # pragma: no cover - compas absent
    pass
