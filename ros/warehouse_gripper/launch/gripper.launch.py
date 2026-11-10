"""Load the gripper on an existing Doosan controller manager; motion stays disabled."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('controller_manager', default_value='/dsr01/controller_manager'),
        DeclareLaunchArgument(
            'params_file',
            default_value=PathJoinSubstitution([
                FindPackageShare('warehouse_gripper'), 'config', 'gripper.yaml',
            ]),
        ),
        Node(
            package='controller_manager', executable='spawner', output='screen',
            arguments=[
                'warehouse_gripper', '-c', LaunchConfiguration('controller_manager'),
                '--param-file', LaunchConfiguration('params_file'),
                '--controller-manager-timeout', '60',
            ],
        ),
    ])
