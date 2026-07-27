from roslibpy import Ros, Topic
from compas.data import Data
from .pointcloud2 import PointCloud2


class PointcloudPublisher(Data):
    def __init__(self, ros_client, topic_name):
        self.topic_name = topic_name
        self.ros_client = ros_client
        self.topic_type = "sensor_msgs/PointCloud2"

    def publish(self, pointcloud):
        self.publisher = Topic(self.ros_client, self.topic_name, self.topic_type)
        self.publisher.advertise()

        self.publisher.publish(PointCloud2.from_points(pointcloud))

    def unadvertise(self):
        self.publisher.unadvertise()


if __name__ == "__main__":
    client = Ros(host="10.181.162.159", port=9090)
    client.run()

    from compas.geometry import Point, Pointcloud

    points = []
    for i in range(10):
        for j in range(15):
            points.append(Point(i, j, 0))

    pointcloud = Pointcloud(points)
    publisher = PointcloudPublisher(client, "/cloud_in")
    publisher.publish(pointcloud)

    try:
        print("Waiting...")
        while True:
            pass
    except KeyboardInterrupt:
        client.terminate()
