# New ROS service adapter and the corrected shared-DRFL gripper use Jazzy.
FROM ros:jazzy-ros-base
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/demo
COPY . .
RUN python3 -m venv --system-site-packages /opt/demo && /opt/demo/bin/pip install --no-cache-dir -e .
ENV MOCK_MODE=true
CMD ["bash", "-c", "source /opt/ros/jazzy/setup.bash && exec /opt/demo/bin/uvicorn vision_picking.server:create_app --factory --host 0.0.0.0 --port 8010 --workers 1"]
