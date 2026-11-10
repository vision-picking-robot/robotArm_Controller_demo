import json
import os
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from scripts import stack


class StackTests(unittest.TestCase):
    def test_only_this_projects_native_server_matches(self):
        command = [str(stack.ROOT / ".conda/mps/bin/python"), "-m", "uvicorn", "app.main:app"]
        self.assertTrue(stack.is_project_server(command, str(stack.ROOT)))
        self.assertFalse(stack.is_project_server(command, "/tmp/another-project"))
        self.assertFalse(stack.is_project_server(["/usr/bin/python3", *command[1:]], str(stack.ROOT)))
        self.assertFalse(stack.is_project_server([*command[:3], "other:app"], str(stack.ROOT)))
        self.assertFalse(stack.is_project_server([], str(stack.ROOT)))

    def test_stop_native_targets_matched_processes_and_waits(self):
        process = Mock(pid=42)
        process.children.return_value = []
        with patch.object(stack, "project_servers", return_value=[process]), \
             patch.object(stack.psutil, "wait_procs", return_value=([process], [])) as wait:
            stack.stop_native()
        process.terminate.assert_called_once()
        wait.assert_called_once_with([process], timeout=12)

    def test_discovered_process_timeout_does_not_force_kill(self):
        process = Mock(pid=42)
        process.children.return_value = []
        with patch.object(stack, "project_servers", return_value=[process]), \
             patch.object(stack.psutil, "wait_procs", return_value=([], [process])):
            with self.assertRaisesRegex(RuntimeError, "did not exit"):
                stack.stop_native()
        process.kill.assert_not_called()

    def run_with_gateway(self, *, running=False, start_error=None, supervise_error=None):
        gateway = Mock()
        gateway.start.return_value = "our-container-id"
        gateway.start.side_effect = start_error
        gateway.running.return_value = running
        process = Mock()
        process.poll.return_value = None
        patches = [patch.object(stack, "project_servers", return_value=[]),
                   patch.object(stack, "Gateway", return_value=gateway),
                   patch.object(stack.subprocess, "Popen", return_value=process)]
        if supervise_error:
            patches.append(patch.object(stack, "supervise", side_effect=supervise_error))
        from contextlib import ExitStack
        with ExitStack() as context:
            for item in patches:
                context.enter_context(item)
            if start_error:
                with self.assertRaises(type(start_error)):
                    stack.run_stack("mps")
            else:
                self.assertEqual(stack.run_stack("mps"), 0)
        return gateway, process

    def test_container_stop_also_stops_native_child(self):
        gateway, process = self.run_with_gateway()
        gateway.native_environment.assert_called_once_with("our-container-id")
        gateway.running.assert_called_once_with("our-container-id")
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=12)
        gateway.stop.assert_called_once()

    def test_ctrl_c_stops_native_child_and_container_and_restores_handlers(self):
        original = signal.getsignal(signal.SIGTERM)
        gateway, process = self.run_with_gateway(supervise_error=KeyboardInterrupt())
        process.terminate.assert_called_once()
        gateway.stop.assert_called_once()
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_docker_start_failure_cleans_up_without_starting_camera(self):
        gateway, process = self.run_with_gateway(start_error=RuntimeError("build failed"))
        process.terminate.assert_not_called()
        gateway.stop.assert_called_once()

    def test_missing_docker_still_stops_native_with_down(self):
        with patch("sys.argv", ["stack", "down"]), \
             patch.object(stack, "stop_native") as stop, \
             patch.object(stack, "Gateway", side_effect=RuntimeError("Docker unavailable")):
            self.assertEqual(stack.main(), 1)
        stop.assert_called_once()

    def test_docker_inspection_failure_means_stop_camera(self):
        with patch.object(stack.shutil, "which", return_value="/usr/bin/docker"):
            gateway = stack.Gateway()
        with patch.object(stack.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 3)):
            self.assertFalse(gateway.running("our-container-id"))

    def test_native_child_receives_compose_settings_with_shell_overrides(self):
        container_env = ["YOLO_VOICE_ENABLED=1", "YOLO_VOICE_BARGE_IN=0", "YOLO_STATE_DIR=/tmp/a $b=c",
                         "YOLO_BACKEND=mps", "ROBOT_IP=110.120.1.56", "ROBOT_ROS_PC_IP=192.168.1.2",
                         "ROBOT_ROSBRIDGE_PORT=9091",
                         "PATH=/container/bin", "GATEWAY_ONLY=yes"]
        shell_env = {"PATH": "/mac/bin", "YOLO_VOICE_BARGE_IN": "1", "YOLO_BACKEND": "coreml"}
        with patch.dict(os.environ, shell_env, clear=True), \
             patch.object(stack.shutil, "which", return_value="/usr/bin/docker"), \
             patch.object(stack.subprocess, "run") as inspect:
            inspect.return_value.stdout = json.dumps(container_env)
            env = stack.Gateway().native_environment("our-container-id")
        self.assertEqual(env, {**shell_env, "YOLO_ROBOT_WEB_OWNER": "compose", "YOLO_VOICE_ENABLED": "1", "YOLO_STATE_DIR": "/tmp/a $b=c",
                               "ROBOT_IP": "110.120.1.56", "ROBOT_ROS_PC_IP": "192.168.1.2",
                               "ROBOT_ROSBRIDGE_PORT": "9091"})


if __name__ == "__main__":
    unittest.main()


def test_training_cleanup_only_matches_actual_owned_worker():
    parent=Mock()
    child=Mock()
    child.cmdline.return_value=[str(stack.ROOT/".conda/train/bin/python"),"-m","scripts.training_worker","--request","test"]
    child.cwd.return_value=str(stack.ROOT)
    unrelated=Mock()
    unrelated.cmdline.return_value=["python","-m","other_worker"]
    parent.children.return_value=[child,unrelated]
    assert stack.training_children([parent])==[child]
    with patch.object(stack.psutil,"wait_procs",return_value=([child],[])):
        stack.finish_training_children([child])
    child.terminate.assert_called_once()
    unrelated.terminate.assert_not_called()
