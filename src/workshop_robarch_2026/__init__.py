"""

Intro to project ...


Setup
=====

In order to use this library, ...


Main concepts
=============

Describe typical classes found in project

.. autoclass:: SampleClassName
   :members:


"""

from .sample_module import SampleClassName

# The pointcloud publisher needs roslibpy, which is installed into Rhino's
# CPython but not into every plain interpreter. Importing the package must
# never depend on it -- kernel/joints/evaluator have no ROS dependency.
try:
    from .pointcloud_publisher import *
except ImportError:  # pragma: no cover - roslibpy absent
    pass

# scanframes needs compas, likewise present in Rhino but not everywhere.
try:
    from .scanframes import *
except ImportError:  # pragma: no cover - compas absent
    pass

__all__ = ['SampleClassName']
