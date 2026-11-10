"""RH-P12-RN(A) control by running the working DART/DRL flange code on the controller.

Why: controller DRCF v2.10 (GF02100300) does not answer the port-aware DRFL flange_serial_*
calls the PC plugin makes (DRFL GL013304), while the same Modbus frames work from DRL.
So the frames are sent from DRL via /<ns>/drl/drl_start and the result is returned through
PLC output registers, read back with /<ns>/plc/get_output_register_int.

The same version mismatch makes get_output_register_int return only the low byte of the stored
value (in bits 24..31), so every result is split into single bytes, one register each:
  12=nonce, 13=code+128, 14/15=voltage tenths, 16/17=position, 18/19=current (uint16), 20=moving
"""
import random
import time

REG_NONCE, REG_CODE, REG_VOLT, REG_POS, REG_CUR, REG_MOVING = 12, 13, 14, 16, 18, 20
CLOSE_TARGET = 740
GOAL_CURRENT_MA = 400
CODES = {1: 'ok', -1: 'voltage read failed', -2: 'feedback read failed', -3: 'goal current write failed',
         -4: 'goal position write failed', -5: 'torque write failed', -6: 'hardware error status'}

_DRL = '''
def _rx(n):
    buf = bytes([])
    tries = 0
    while len(buf) < n and tries < 10:
        size, data = flange_serial_read(0.3)
        if size > 0:
            buf = buf + bytes(data)[0:size]
        tries = tries + 1
    return buf

def _rd(addr, n):
    flange_serial_write(modbus_send_make(bytes([1, 3, addr >> 8, addr & 255, 0, n])))
    data = _rx(5 + 2 * n)
    if len(data) == 5 + 2 * n and data[0] == 1 and data[1] == 3 and modbus_recv_check(data):
        return data
    return None

def _wr(frame):
    flange_serial_write(modbus_send_make(bytes(frame)))
    data = _rx(8)
    return len(data) == 8 and data[0] == 1 and data[1] == frame[1] and modbus_recv_check(data)

def _put(addr, value, n):
    value = int(value) & ((1 << (8 * n)) - 1)
    for i in range(n):
        set_output_register_int(addr + i, (value >> (8 * (n - 1 - i))) & 255)

def _w(d, i):
    return d[3 + 2 * i] * 256 + d[4 + 2 * i]

code = 1
volt = -1
pos = -1
cur = 0
mov = 0
flange_serial_open(baudrate=57600, bytesize=DR_EIGHTBITS, parity=DR_PARITY_NONE, stopbits=DR_STOPBITS_ONE)
wait(0.5)
v = _rd(296, 1)
if v is None:
    v = _rd(296, 1)
if v is None:
    code = -1
else:
    volt = _w(v, 0)
TARGET = {target}
if code == 1 and TARGET >= 0:
    if not _wr([1, 6, 1, 19, {current} >> 8, {current} & 255]):
        code = -3
    elif not _wr([1, 16, 1, 26, 0, 2, 4, TARGET >> 8, TARGET & 255, 0, 0]):
        code = -4
    elif not _wr([1, 6, 1, 0, 0, 1]):
        code = -5
    elif not _wr([1, 16, 1, 26, 0, 2, 4, TARGET >> 8, TARGET & 255, 0, 0]):
        # Re-send after torque-on: a goal written while torque was off is ignored by the device.
        code = -4
    else:
        wait({settle})
if code == 1:
    f = _rd(285, 7)
    if f is None:
        code = -2
    else:
        mov = _w(f, 0) & 255
        cur = _w(f, 2)
        if cur >= 32768:
            cur = cur - 65536
        pos = _w(f, 5) + _w(f, 6) * 65536
        e = _rd(259, 1)
        if e is not None and (_w(e, 0) & 255) != 0:
            code = -6
flange_serial_close()
_put({r_code}, code + 128, 1)
_put({r_volt}, volt, 2)
_put({r_pos}, pos, 2)
_put({r_cur}, cur, 2)
_put({r_mov}, mov, 1)
_put({r_nonce}, {nonce}, 1)
tp_log("gripper target=" + str(TARGET) + " code=" + str(code) + " volt=" + str(volt) + " pos=" + str(pos) + " cur=" + str(cur))
'''


def build_program(target, nonce, settle=1.5, current=GOAL_CURRENT_MA):
    if target != -1 and not 0 <= target <= CLOSE_TARGET:
        raise ValueError('target must be -1 (status) or 0..740')
    return _DRL.format(target=int(target), nonce=int(nonce), settle=float(settle), current=int(current),
                       r_code=REG_CODE, r_volt=REG_VOLT, r_pos=REG_POS, r_cur=REG_CUR, r_mov=REG_MOVING, r_nonce=REG_NONCE)


class DrlGripper:
    """Uses an existing rclpy node + executor (e.g. RosBackend's) to run the DRL program."""

    def __init__(self, node, executor, namespace='dsr01'):
        from dsr_msgs2.srv import DrlStart, GetDrlState, GetOutputRegisterInt
        self.executor = executor
        self.types = (DrlStart, GetDrlState, GetOutputRegisterInt)
        self.start = node.create_client(DrlStart, f'/{namespace}/drl/drl_start')
        self.state = node.create_client(GetDrlState, f'/{namespace}/drl/get_drl_state')
        self.reg = node.create_client(GetOutputRegisterInt, f'/{namespace}/plc/get_output_register_int')
        for client in (self.start, self.state, self.reg):
            deadline = time.monotonic() + 10.0
            while not client.wait_for_service(timeout_sec=0.5):
                executor.spin_once(timeout_sec=0.1)
                if time.monotonic() > deadline:
                    raise RuntimeError(f'Service unavailable: {client.srv_name}')

    def _call(self, client, request, timeout=5.0):
        from vision_picking.legacy.ros_calls import call_service
        return call_service(self.executor, client, request, timeout)

    def _byte(self, address):
        result = self._call(self.reg, self.types[2].Request(address=address, timeout_ms=0))
        if not result.success:
            raise RuntimeError(f'Could not read output register {address}')
        return (result.value >> 24) & 0xFF

    def _read(self, address, n=1):
        value = 0
        for i in range(n):
            value = (value << 8) | self._byte(address + i)
        return value

    def _feedback(self, target):
        code = self._read(REG_CODE) - 128
        volt = self._read(REG_VOLT, 2)
        current = self._read(REG_CUR, 2)
        return dict(code=code, result=CODES.get(code, str(code)),
                    voltage=None if volt == 0xFFFF else volt / 10.0,
                    position_pulses=self._read(REG_POS, 2), current=current - 65536 if current >= 32768 else current,
                    moving=self._read(REG_MOVING), target=target)

    def _wait_stopped(self, timeout):
        GetDrlState = self.types[1]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._call(self.state, GetDrlState.Request())
            # 1 = STOP, 3 = no program has run since the driver connected; both mean idle.
            if state.success and state.drl_state in (1, 3):
                time.sleep(0.3)
                return
            time.sleep(0.2)
        raise TimeoutError('A DRL program is still running on the controller')

    def run(self, target, timeout=20.0):
        DrlStart, GetDrlState, _ = self.types
        self._wait_stopped(10.0)
        nonce = random.randint(1, 250)
        while nonce == self._read(REG_NONCE):
            self._wait_stopped(10.0)
        nonce = random.randint(1, 250)
        settle = 1.5 if target >= 0 else 0.0
        started = self._call(self.start, DrlStart.Request(robot_system=0, code=build_program(target, nonce, settle)))
        if not started.success:
            raise RuntimeError('drl_start refused (robot must be AUTO/STANDBY with no DRL program running)')
        started_at = time.monotonic()
        deadline = started_at + timeout
        while time.monotonic() < deadline:
            time.sleep(0.2)
            if self._read(REG_NONCE) == nonce:
                feedback = self._feedback(target)
                # The program still runs tp_log after the nonce; a drl_start sent before it
                # reaches STOP is silently dropped, so wait for STOP before returning.
                self._wait_stopped(5.0)
                return feedback
        state = self._call(self.state, GetDrlState.Request())
        raise TimeoutError(f'DRL gripper program did not report a result in {timeout:.0f}s '
                           f'(drl_state={state.drl_state}; check pendant log)')

    def status(self):
        return self.run(-1)

    def open(self):
        return self.run(0)

    def close(self):
        return self.run(CLOSE_TARGET)


def main(argv=None):
    import argparse
    import json
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    parser = argparse.ArgumentParser(description='RH-P12-RN(A) via controller DRL (never moves the arm)')
    parser.add_argument('--namespace', default='dsr01')
    parser.add_argument('--action', choices=['status', 'open', 'close'], default='status')
    args = parser.parse_args(argv)
    rclpy.init()
    node = rclpy.create_node('drl_gripper_cli')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        result = getattr(DrlGripper(node, executor, args.namespace), args.action)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['code'] == 1 else 1
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
