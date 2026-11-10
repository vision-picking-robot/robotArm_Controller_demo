"""Run the validated shared-DRFL ROS/Mock standalone gripper checker."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vision_picking.gripper_cli import main
if __name__ == '__main__':
    raise SystemExit(main())
