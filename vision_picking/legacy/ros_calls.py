"""Bounded service calls on an I/O node owned by the caller."""
import time

from rclpy.executors import SingleThreadedExecutor


def call_service(executor, client, request, timeout):
    deadline = time.monotonic() + timeout
    if not client.wait_for_service(timeout_sec=min(timeout, 2.0)):
        raise RuntimeError(f'Service unavailable: {client.srv_name}')
    future = client.call_async(request)
    try:
        executor.spin_until_future_complete(future, timeout_sec=max(0.0, deadline - time.monotonic()))
        if not future.done():
            raise TimeoutError(f'No response from {client.srv_name}; command outcome unknown')
        return future.result()
    finally:
        if not future.done():
            client.remove_pending_request(future)


def spin_node(node):
    # Use a dedicated executor so synchronous callbacks can spin their separate I/O node.
    executor = SingleThreadedExecutor(context=node.context)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
