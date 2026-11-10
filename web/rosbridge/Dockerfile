FROM ros:humble-ros-base
SHELL ["/bin/bash", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-humble-rosbridge-suite ros-humble-rosidl-default-generators \
        python3-colcon-common-extensions build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /ros_ws
COPY --from=dsr_interfaces . src/dsr_msgs2/
RUN source /opt/ros/humble/setup.bash && \
    colcon build --packages-select dsr_msgs2 --cmake-args -DBUILD_TESTING=OFF
COPY start.sh /ros_ws/start.sh
COPY read_joints.py /ros_ws/read_joints.py
COPY fastdds.xml /etc/doosan/fastdds.xml
ENV FASTRTPS_DEFAULT_PROFILES_FILE=/etc/doosan/fastdds.xml
CMD ["bash", "/ros_ws/start.sh"]
