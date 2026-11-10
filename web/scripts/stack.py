"""Keep the native camera server and Docker gateway in one foreground lifecycle."""
import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def is_project_server(command, cwd):
    """Match our launcher, interpreter and working directory, not a broad pkill."""
    if not command or command[1:4] != ["-m", "uvicorn", "app.main:app"]:
        return False
    if not cwd or Path(cwd).resolve() != ROOT:
        return False
    interpreters = {(ROOT / ".conda" / backend / "bin/python").resolve()
                    for backend in ("mps", "coreml")}
    return Path(command[0]).resolve() in interpreters


def project_servers():
    matches = []
    for process in psutil.process_iter():
        try:
            if is_project_server(process.cmdline(), process.cwd()):
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def training_children(servers):
    """Snapshot only our servers' actual descendants, before the parent exits."""
    workers = []
    interpreters = {(ROOT / ".conda" / backend / "bin/python").resolve()
                    for backend in ("train", "coreml")}
    for server in servers:
        try:
            for child in server.children(recursive=True):
                command = child.cmdline()
                if (len(command) >= 3 and command[1:3] == ["-m", "scripts.training_worker"]
                        and Path(command[0]).resolve() in interpreters
                        and Path(child.cwd()).resolve() == ROOT):
                    workers.append(child)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return workers


def finish_training_children(workers):
    if not workers:
        return
    # Normally FastAPI's shutdown/EOF watchdog has already stopped these children.
    # psutil retains each process's creation time, protecting against PID reuse.
    for worker in workers:
        try:
            worker.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(workers, timeout=4)
    for worker in alive:
        try:
            worker.kill()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(alive, timeout=3) if alive else ([], [])
    if remaining:
        raise RuntimeError("Owned training workers did not exit")


def stop_native():
    processes = project_servers()
    workers = training_children(processes)
    for process in processes:
        try:
            print(f"Stopping native camera server (PID {process.pid})", flush=True)
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=12)
    finish_training_children(workers)
    if alive:
        raise RuntimeError(f"Native server did not exit: {[p.pid for p in alive]}")
    print("Native camera server stopped." if processes else "No native camera server is running.",
          flush=True)


class Gateway:
    def __init__(self):
        docker = shutil.which("docker")
        if not docker:
            bundled = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
            if bundled.is_file():
                docker = str(bundled)
        if not docker:
            raise RuntimeError("Docker CLI not found. Install and start Docker Desktop.")
        self.docker = docker
        self.env = {**os.environ, "PATH": str(Path(docker).parent) + os.pathsep + os.environ.get("PATH", "")}

    def compose(self, *args, capture=False, timeout=120):
        return subprocess.run([self.docker, "compose", "--file", str(ROOT / "docker-compose.yml"), *args],
                              cwd=ROOT, env=self.env, check=True, text=True,
                              capture_output=capture, timeout=timeout)

    def start(self):
        self.compose("up", "--build", "-d", "gateway", "joint-monitor", timeout=1800)
        container_id = self.compose("ps", "-q", "gateway", capture=True, timeout=5).stdout.strip()
        if not container_id:
            raise RuntimeError("Gateway did not start; the camera server was not started.")
        return container_id

    def native_environment(self, container_id):
        # Read the values Docker actually applied, including dotenv quoting and interpolation.
        result = subprocess.run([self.docker, "inspect", "--format", "{{json .Config.Env}}", container_id],
                                env=self.env, check=True, text=True, stdout=subprocess.PIPE, timeout=5)
        settings = dict(entry.split("=", 1) for entry in json.loads(result.stdout)
                        if entry.startswith(("YOLO_", "ROBOT_")) and "=" in entry)
        return {**settings, **os.environ, "YOLO_ROBOT_WEB_OWNER": "compose"}

    def running(self, container_id):
        try:
            result = subprocess.run([self.docker, "inspect", "--format", "{{.State.Running}}", container_id],
                                    env=self.env, text=True, capture_output=True, timeout=3)
            return result.returncode == 0 and result.stdout.strip() == "true"
        except (OSError, subprocess.TimeoutExpired):
            # If Docker becomes unreachable, release the camera rather than leave it running.
            return False

    def stop(self):
        self.compose("down", "gateway", timeout=20)


def supervise(server, gateway_running):
    while server.poll() is None:
        if not gateway_running():
            print("Gateway stopped or unavailable; stopping the native camera server.", flush=True)
            return 0
        time.sleep(1)
    return server.returncode


def stop_child(server):
    if server is None or server.poll() is not None:
        return
    try:
        workers = training_children([psutil.Process(server.pid)]) if isinstance(server.pid, int) else []
    except psutil.NoSuchProcess:
        workers = []
    try:
        server.terminate()
        server.wait(timeout=12)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        # This is only the child we created, never an arbitrary discovered process.
        print("Native server shutdown timed out; terminating our child process.", flush=True)
        server.kill()
        server.wait(timeout=5)
    finally:
        finish_training_children(workers)


def run_stack(backend):
    if project_servers():
        raise RuntimeError("A native server is already running. Run bash scripts/stack.sh down first.")
    gateway = Gateway()
    server = None
    cleanup_gateway = False

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    old_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        cleanup_gateway = True
        container_id = gateway.start()
        server = subprocess.Popen(["bash", str(ROOT / "scripts/start.sh"), backend],
                                  cwd=ROOT, env=gateway.native_environment(container_id), stdin=subprocess.DEVNULL)
        print("Open http://127.0.0.1:8080 — Ctrl+C or stopping the gateway also stops the camera.", flush=True)
        return supervise(server, lambda: gateway.running(container_id))
    except KeyboardInterrupt:
        return 0
    finally:
        # Finish cleanup even if Ctrl+C is pressed a second time.
        for sig in old_handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            stop_child(server)
            if cleanup_gateway:
                try:
                    gateway.stop()
                except (OSError, subprocess.SubprocessError) as exc:
                    print(f"Camera server stopped; Docker cleanup failed: {exc}", flush=True)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "stop-native"))
    parser.add_argument("--backend", choices=("mps", "coreml", "cpu"), default="mps")
    args = parser.parse_args()
    try:
        if args.action == "up":
            return run_stack(args.backend)
        # Camera teardown does not depend on Docker being available.
        stop_native()
        if args.action == "down":
            Gateway().stop()
        return 0
    except (RuntimeError, OSError, psutil.Error, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
