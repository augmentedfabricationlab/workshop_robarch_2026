from roslibpy import Message, Header
from compas.geometry import Point, Vector
from compas.colors import Color
import struct
import base64


class PointCloud2Header(Header):
    @property
    def __data__(self):
        return {
            "seq": self.data.get("seq"),
            "stamp": self.data.get("stamp").data,
            "frame_id": self.data.get("frame_id"),
        }


class PointCloud2(Message):
    def __init__(self):
        self.header = PointCloud2Header(seq=0, stamp=0, frame_id="robot_base_footprint")
        self.fields = []
        self.points = []
        self.normals = []
        self.rgb = []
        self.data = {}
        self.byte_format = ""
        self.update(
            {
                "header": self.header,
                "height": 1,
                "width": 0,
                "fields": [],
                "is_bigendian": False,
                "point_step": None,
                "row_step": None,
                "data": [],  # actual point data
                "is_dense": True,  # true if there are no invalid points
            }
        )

    @property
    def __data__(self):
        return {
            "header": self.header.__data__,
            "height": self.data.get("height"),
            "width": self.data.get("width"),
            "fields": [field.data for field in self.fields],
            "is_bigendian": self.data.get("is_bigendian"),
            "point_step": self.data.get("point_step"),
            "row_step": self.data.get("row_step"),
            "data": self.data.get("data"),
            "is_dense": self.data.get("is_dense"),
            "points": [pt.__data__ for pt in self.points],
            "normals": [nml.__data__ for nml in self.normals],
            "rgb": [c.__data__ for c in self.rgb],
        }

    @property
    def field_names(self):
        _field_names = []
        for field in self.fields:
            _field_names.append(field.get("name"))
        return _field_names

    @classmethod
    def from_points(cls, points, normals=None):
        msg = cls()
        cls.fields = [
            PointField("x", 0, 7, 1).data,
            PointField("y", 4, 7, 1).data,
            PointField("z", 8, 7, 1).data,
            PointField("n_x", 12, 7, 1).data,
            PointField("n_y", 16, 7, 1).data,
            PointField("n_z", 20, 7, 1).data,
        ]
        msg["fields"] = cls.fields
        msg["height"] = 1  # assuming unstructured
        msg["width"] = len(points)
        msg["point_step"] = len(cls.fields) * 4
        msg["row_step"] = msg["point_step"] * len(points)
        msg["is_dense"] = True
        # print(msg)
        ba = []
        if normals is None:
            normals = [Vector.Zaxis()] * len(points)
        for point, normal in zip(points, normals):
            ba.extend(
                struct.pack(
                    "ffffff", point.x, point.y, point.z, normal.x, normal.y, normal.z
                )
            )
        # print(ba)
        msg["data"] = ba
        return msg

    @classmethod
    def from_msg(cls, msg):
        pc = cls()
        pc.update(msg)
        header = dict(msg.get("header"))
        stamp = header.get("stamp")
        if stamp:
            header["stamp"] = {
                "secs": stamp.get("secs", stamp.get("sec")),
                "nsecs": stamp.get("nsecs", stamp.get("nanosec")),
            }
        pc.header = PointCloud2Header(**header)
        pc.fields = [PointField(**field) for field in msg.get("fields")]
        pc.byte_format = ""
        size = 0
        if msg.get("is_bigendian"):
            pc.byte_format += ">"
        else:
            pc.byte_format += "<"
        for field in msg.get("fields"):
            if field.get("name") == "rgb":
                pc.byte_format += "2f"
                size += 8
            elif field.get("datatype") == 7:
                pc.byte_format += "f"
                size += 4
        if size < pc.__data__["point_step"]:
            pc.byte_format += "{}x".format(pc.__data__["point_step"]-size)
        pc._decode_data()
        return pc

    def _decode_data(self):
        _raw = base64.standard_b64decode(self.data.get("data"))
        # count = len(self.data.get("fields"))
        print(self.byte_format)
        data = struct.iter_unpack(self.byte_format, _raw)
        self.points = []
        self.normals = []
        self.rgb = []
        for subset in data:
            if all(item in self.field_names for item in ["x", "y", "z"]):
                xyz = [subset[self.field_names.index(item)] for item in ["x", "y", "z"]]
                self.points.append(Point(*xyz))
            if all(item in self.field_names for item in ["n_x", "n_y", "n_z"]):
                nxnynz = [
                    subset[self.field_names.index(item)]
                    for item in ["n_x", "n_y", "n_z"]
                ]
                self.normals.append(Vector(*nxnynz))
            if "rgb" in self.field_names:
                self.rgb.append(Color.from_i(subset[self.field_names.index("rgb") + 1]))


class PointField(Message):
    def __init__(self, name, offset, datatype, count):
        # self.name = name
        # self.offset = offset
        # self.datatype = datatype
        # self.count = count
        self.data = {}
        self.update(
            {"name": name, "offset": offset, "datatype": datatype, "count": count}
        )

    #                     'INT8' : 1,
    #                     'UINT8' : 2,
    #                     'INT16' : 3,
    #                     'UINT16' : 4,
    #                     'INT32' : 5,
    #                     'UINT32' : 6,
    #                     'FLOAT32' : 7,
    #                     'FLOAT64' : 8,


if __name__ == "__main__":
    from roslibpy import Ros
    from roslibpy import Topic
    import time

    ros = Ros("192.168.0.200", 9090)
    ros.run()
    time.sleep(1)

    # topic = Topic(ros, "/line_scanner/cloud_out", "sensor_msgs/PointCloud2")
    topic = Topic(ros, "/camera/depth/color/points", "sensor_msgs/PointCloud2")

    msgs = []

    def store_msg(msg):
        msgs.append(msg)
        # print(msg)

    topic.subscribe(store_msg)
    time.sleep(1)
    while msgs == []:
        time.sleep(0.1)
    topic.unsubscribe()

    ros.terminate()
    # print(msgs[0])
    pc = PointCloud2.from_msg(msgs[0])

    msgname = "depth_camera_msg.json"
    filename = "depth_camera_pc.json"
    import json

    with open(msgname, "w") as f:
        json.dump(msgs[0], f)

    with open(filename, "w") as f:
        json.dump(pc.__data__, f)

    # print(pc.points)
