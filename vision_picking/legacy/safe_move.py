"""안전 이동 래퍼 (safe_move).

로봇을 움직이는 모든 코드는 DSR_ROBOT2 의 movel/movej 를 직접 부르지 말고
반드시 SafeMover 를 통해서만 부른다.

검사 내용
  - 목표 TCP z 가 소프트웨어 하한선(z_min)보다 낮으면 명령을 보내지 않고 SafetyLimitError
  - movel 절대 이동: 목표 z 를 바로 검사
  - movel 상대 이동: 현재 posx 를 읽어 도착 위치를 계산한 뒤 검사 (DR_TOOL 은 현재 자세로 회전 변환)
  - movej: 목표 관절값을 fkin(정기구학)으로 posx 로 바꿔 검사. fkin 실패 시에도 차단 (fail-closed)
  - 속도/가속도: vel, acc 를 반드시 명시해야 하고 max_vel / max_acc 를 넘으면 차단

⚠️ 한계 — 이 검사가 막지 못하는 것
  - **도착점만** 검사한다. 이동 **경로** 중간에 하한선 아래로 내려가는 경우는 못 막는다.
    특히 movej 는 관절 공간 보간이라 TCP 경로가 직선이 아니므로, 시작·끝이 안전해도
    중간에 바닥 쪽으로 처질 수 있다.
  - TCP 한 점만 본다. 그리퍼 옆 NFC 리더기 등 다른 부품의 충돌은 못 막는다
    (nfc_protrusion 을 하한선에 더해 일부만 보완).
  - 따라서 두산 펜던트의 안전 영역(공간 제한) 설정을 최후 방어선으로 반드시 함께 쓴다.
"""
import math
from .validation import finite_number, pose6

DR_BASE = 0
DR_TOOL = 1
DR_WORLD = 2
DR_MV_MOD_ABS = 0
DR_MV_MOD_REL = 1

_REF_NAMES = {DR_BASE: 'DR_BASE', DR_TOOL: 'DR_TOOL', DR_WORLD: 'DR_WORLD'}
_MOD_NAMES = {DR_MV_MOD_ABS: 'ABS', DR_MV_MOD_REL: 'REL'}


class SafetyLimitError(Exception):
    """안전 검사에 걸려 명령을 보내지 않았을 때."""


class MotionError(Exception):
    """명령은 보냈으나 컨트롤러가 실패를 돌려줬을 때."""


def _vec6(pos, what):
    return pose6(pos, what)


def zyz_rotation(a_deg, b_deg, c_deg):
    """두산 posx 의 (rx, ry, rz) = ZYZ 오일러 각 → 3x3 회전행렬 Rz(a)·Ry(b)·Rz(c)."""
    a, b, c = (math.radians(v) for v in (a_deg, b_deg, c_deg))
    ca, sa = math.cos(a), math.sin(a)
    cb, sb = math.cos(b), math.sin(b)
    cc, sc = math.cos(c), math.sin(c)
    return [
        [ca * cb * cc - sa * sc, -ca * cb * sc - sa * cc, ca * sb],
        [sa * cb * cc + ca * sc, -sa * cb * sc + ca * cc, sa * sb],
        [-sb * cc, sb * sc, cb],
    ]


def tool_offset_to_base(current_posx, delta_xyz):
    """툴 좌표계 기준 이동량 (dx, dy, dz) 을 base 좌표계 이동량으로 변환."""
    r = zyz_rotation(*current_posx[3:6])
    return [sum(r[i][k] * delta_xyz[k] for k in range(3)) for i in range(3)]


class SafeMover:
    """z 하한선과 속도 상한을 검사한 뒤에만 backend 로 이동 명령을 보낸다.

    backend 는 아래 메서드를 가진 객체 (실물/에뮬레이터: DsrBackend, 테스트: 가짜 객체)
      get_current_posx(ref) -> [6]
      get_current_posj() -> [6]
      fkin(posj, ref) -> [6] 또는 None
      movel(pos, vel, acc, ref, mod, radius) -> 0 성공 / 그 외 실패
      movej(pos, vel, acc, mod, radius) -> 0 성공 / 그 외 실패
    """

    def __init__(self, z_min, backend, max_vel, max_acc, logger=None):
        self.z_min = finite_number(z_min, 'z_min')
        self.backend = backend
        self.max_vel = finite_number(max_vel, 'max_vel')
        self.max_acc = finite_number(max_acc, 'max_acc')
        if self.max_vel <= 0 or self.max_acc <= 0:
            raise ValueError('속도·가속도 상한은 0보다 커야 합니다')
        self._log = logger or _PrintLogger()

    # ---------- 공개 API ----------

    def movel(self, pos, vel, acc, ref, mod=DR_MV_MOD_ABS, radius=0.0):
        """직선 이동. ref(DR_BASE/DR_TOOL) 는 반드시 명시."""
        pos = _vec6(pos, 'movel 목표')
        self._check_speed(vel, acc, 'movel')
        target = self.movel_target(pos, ref, mod)
        self._check_z(target, f'movel {_REF_NAMES.get(ref, ref)} {_MOD_NAMES.get(mod, mod)} {pos}')
        ret = self.backend.movel(pos, vel, acc, ref, mod, radius)
        self._check_ret(ret, 'movel')
        return target

    def movej(self, pos, vel, acc, mod=DR_MV_MOD_ABS, radius=0.0):
        """관절 이동. 목표 관절값을 fkin 으로 변환해 TCP z 를 검사한다 (경로는 검사하지 않음)."""
        pos = _vec6(pos, 'movej 목표')
        self._check_speed(vel, acc, 'movej')
        target_j, target_x = self.movej_target(pos, mod)
        self._check_z(target_x, f'movej {_MOD_NAMES.get(mod, mod)} {pos} (posj {target_j})')
        ret = self.backend.movej(pos, vel, acc, mod, radius)
        self._check_ret(ret, 'movej')
        return target_x

    # ---------- 도착점 계산 (로봇을 움직이지 않음) ----------

    def movel_target(self, pos, ref, mod):
        """movel 의 도착 posx(base 기준)를 계산한다."""
        if mod == DR_MV_MOD_ABS:
            if ref != DR_BASE:
                raise SafetyLimitError(
                    f'movel 절대 이동은 ref=DR_BASE 만 허용합니다 (받은 ref={ref!r})')
            return list(pos)
        if mod == DR_MV_MOD_REL:
            if ref not in (DR_BASE, DR_TOOL):
                raise SafetyLimitError(
                    f'movel 상대 이동은 ref=DR_BASE/DR_TOOL 만 허용합니다 (받은 ref={ref!r})')
            cur = self._read_posx()
            if ref == DR_BASE:
                d = pos[0:3]
            else:
                d = tool_offset_to_base(cur, pos[0:3])
            # 자세(rx, ry, rz)는 z 검사에 쓰지 않으므로 현재값을 그대로 둔다
            return [cur[0] + d[0], cur[1] + d[1], cur[2] + d[2]] + cur[3:6]
        raise SafetyLimitError(f'알 수 없는 mod={mod!r}')

    def movej_target(self, pos, mod):
        """movej 의 도착 posj 와, fkin 으로 구한 도착 posx(base) 를 계산한다."""
        if mod == DR_MV_MOD_ABS:
            target_j = list(pos)
        elif mod == DR_MV_MOD_REL:
            cur = self.backend.get_current_posj()
            if cur is None:
                raise SafetyLimitError('현재 posj 를 읽지 못해 movej 를 차단합니다')
            cur = _vec6(cur, '현재 posj')
            target_j = [c + d for c, d in zip(cur, pos)]
        else:
            raise SafetyLimitError(f'알 수 없는 mod={mod!r}')

        target_x = self.backend.fkin(target_j, DR_BASE)
        if target_x is None:
            raise SafetyLimitError(f'fkin 실패로 movej 목표 {target_j} 의 z 를 확인할 수 없어 차단합니다')
        return target_j, _vec6(target_x, 'fkin 결과')

    # ---------- 내부 ----------

    def _read_posx(self):
        cur = self.backend.get_current_posx(DR_BASE)
        if cur is None:
            raise SafetyLimitError('현재 posx 를 읽지 못해 상대 이동을 차단합니다')
        return _vec6(cur, '현재 posx')

    def _check_z(self, target_posx, desc):
        z = target_posx[2]
        if z < self.z_min:
            msg = (f'[safe_move] 차단: 목표 z={z:.2f} mm < 하한선 z_min={self.z_min:.2f} mm '
                   f'— 명령을 보내지 않았습니다 ({desc})')
            self._log.error(msg)
            raise SafetyLimitError(msg)

    def _check_speed(self, vel, acc, what):
        for name, val, lim in (('vel', vel, self.max_vel), ('acc', acc, self.max_acc)):
            if val is None:
                raise SafetyLimitError(f'{what}: {name} 를 반드시 명시해야 합니다 (상한 {lim})')
            vals = list(val) if isinstance(val, (list, tuple)) else [val]
            try:
                vals = [finite_number(v, name) for v in vals]
            except ValueError as exc:
                raise SafetyLimitError(str(exc)) from exc
            if not vals or any(v <= 0 or v > lim for v in vals):
                raise SafetyLimitError(f'{what}: {name}={val} 가 허용 범위 (0, {lim}] 를 벗어났습니다')

    @staticmethod
    def _check_ret(ret, what):
        if ret != 0:
            raise MotionError(f'{what} 실패 (컨트롤러 반환값 {ret!r})')


def _as_list(value):
    try:
        vals = list(value)
    except TypeError:
        return None
    return vals if len(vals) == 6 else None


class _PrintLogger:
    def error(self, msg):
        print(msg)


class DsrBackend:
    """DSR_ROBOT2 를 감싼 실제 backend.

    생성 전에 DR_init.__dsr__id / __dsr__model / __dsr__node 가 설정되어 있어야 한다.
    (DSR_ROBOT2 는 import 시점에 그 노드로 서비스 클라이언트를 만든다)
    """

    def __init__(self):
        import DSR_ROBOT2 as dr  # noqa: N813  (노드 설정 후에만 import 가능)
        self._dr = dr

    # DSR_ROBOT2 는 실패 시 None 이나 -1 을 돌려준다 → 시퀀스가 아니면 모두 None (fail-closed)

    def get_current_posx(self, ref):
        ret = self._dr.get_current_posx(ref)
        if not isinstance(ret, (list, tuple)) or len(ret) != 2:
            return None
        return _as_list(ret[0])

    def get_current_posj(self):
        return _as_list(self._dr.get_current_posj())

    def fkin(self, posj, ref):
        return _as_list(self._dr.fkin(posj, ref))

    def movel(self, pos, vel, acc, ref, mod, radius):
        return self._dr.movel(pos, vel=vel, acc=acc, radius=radius, ref=ref, mod=mod)

    def movej(self, pos, vel, acc, mod, radius):
        return self._dr.movej(pos, vel=vel, acc=acc, radius=radius, mod=mod)
