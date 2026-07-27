from roslibpy import Message, Header


class Pointcloud(Message):
    def __init__(self, values=None):
        super().__init__(values)
        self.update(
            {
                "header": Header(frame_id="robot_base_footprint"),
                "points": [],
                "channels": [],
            }
        )

    @classmethod
    def from_points(cls, points):
        raise NotImplementedError


class Point32(Message):
    def __init__(self, point, values=None):
        super().__init__(values)
        self.update({"x": point.x, "y": point.y, "z": point.z})
