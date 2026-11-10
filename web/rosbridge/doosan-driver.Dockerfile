FROM ros:humble-ros-base
SHELL ["/bin/bash", "-c"]
ARG DRCF_MAJOR=2
RUN test "$(dpkg --print-architecture)" = amd64 && test "$DRCF_MAJOR" = 2
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libyaml-cpp-dev ros-humble-backward-ros ros-humble-forward-command-controller \
        ros-humble-controller-manager ros-humble-joint-state-broadcaster ros-humble-moveit-msgs \
        ros-humble-kdl-parser ros-humble-robot-state-publisher ros-humble-xacro \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /doosan_ws
COPY --from=doosan dsr_msgs2/ src/dsr_msgs2/
COPY --from=dsr_interfaces msg/RobotDisconnection.msg src/dsr_msgs2/msg/
RUN sed -i '/"msg\/RobotError.msg"/a\  "msg/RobotDisconnection.msg"' src/dsr_msgs2/CMakeLists.txt
ENV CMAKE_BUILD_PARALLEL_LEVEL=4
RUN source /opt/ros/humble/setup.bash && colcon build --packages-select dsr_msgs2 --cmake-args -DBUILD_TESTING=OFF
COPY --from=doosan common2/ src/common2/
COPY --from=drfl include/ src/common2/include/
COPY --from=drfl library/Linux/64bits/20.04/ /opt/doosan/lib/
COPY gl010117.sha256 ./
RUN sha256sum -c gl010117.sha256 && cd /opt/doosan/lib && \
    cp libDRFL.a /doosan_ws/src/common2/lib/humble/x86_64/libDRFL.a && \
    ln -s /opt/doosan/lib/libPocoFoundation.so.62 /doosan_ws/src/common2/lib/humble/x86_64/libPocoFoundation.so && \
    ln -s /opt/doosan/lib/libPocoNet.so.62 /doosan_ws/src/common2/lib/humble/x86_64/libPocoNet.so && \
    echo /opt/doosan/lib > /etc/ld.so.conf.d/doosan.conf && ldconfig
COPY --from=doosan dsr_hardware2/ src/dsr_hardware2/
COPY --from=doosan dsr_controller2/ src/dsr_controller2/
COPY --from=doosan dsr_description2/ src/dsr_description2/
COPY --from=doosan dsr_bringup2/ src/dsr_bringup2/
COPY gl010117.patch ./
COPY direct_teach.hpp src/dsr_controller2/src/direct_teach.hpp
RUN cd src && sed -i 's/\r$//' dsr_hardware2/src/dsr_hw_interface2.cpp dsr_controller2/src/dsr_controller2.cpp dsr_bringup2/launch/dsr_bringup2_rviz.launch.py && patch -p1 < ../gl010117.patch
RUN source install/setup.bash && colcon build --executor sequential --packages-skip dsr_msgs2 --cmake-args -DBUILD_TESTING=OFF && \
    source install/setup.bash && for p in dsr_hardware2 dsr_controller2; do \
        ldd -r "install/$p/lib/lib$p.so" > /tmp/link-check; \
        ! grep -E 'not found|undefined symbol' /tmp/link-check || exit 1; \
    done
COPY check-controller.cpp ./
RUN g++ -std=c++17 -Isrc/common2/include check-controller.cpp /opt/doosan/lib/libDRFL.a \
        /opt/doosan/lib/libPocoNet.so.62 /opt/doosan/lib/libPocoFoundation.so.62 -pthread -o /usr/local/bin/check-e0509 && \
    test "$(check-e0509 --sdk-version)" = GL010117
COPY start-driver.sh driver_health.py /doosan_ws/
COPY fastdds.xml /etc/doosan/fastdds.xml
ENV FASTRTPS_DEFAULT_PROFILES_FILE=/etc/doosan/fastdds.xml
CMD ["bash", "/doosan_ws/start-driver.sh", "--force-request-access"]
