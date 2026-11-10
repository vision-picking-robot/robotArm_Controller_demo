"""poses.yaml / products.yaml 로더.

실물(real) 모드에서는
  - floor_z, nfc_protrusion 이 측정되지 않았으면(null) 하한선을 만들지 않는다 (= 모든 이동 차단)
  - taught: false 인 자세·상품은 꺼내주지 않는다 (예시 좌표가 실물에 쓰이는 것을 막음)
가상(virtual) 모드에서는 poses.yaml 의 virtual 값과 products.yaml 의 virtual 좌표를 쓴다.
"""
import os
import sys
from dataclasses import dataclass

import yaml
from .validation import finite_number, pose6

MODES = ('real', 'virtual')


class ConfigError(Exception):
    """설정이 비어 있거나 실물에 쓸 수 없는 값일 때."""


def default_config_dir():
    """설치된 share/warehouse_motion/config, 없으면 소스 트리의 config."""
    try:
        from ament_index_python.packages import get_package_share_directory
        path = os.path.join(get_package_share_directory('warehouse_motion'), 'config')
        if os.path.isdir(path):
            return path
    except Exception:
        pass
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config'),
        os.path.join(sys.prefix, 'share', 'warehouse_motion', 'config'),
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, 'poses.yaml')):
            return path
    raise ConfigError('설정 디렉터리를 찾을 수 없습니다. --config-dir로 지정하세요')


def load_yaml(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f'{path}: YAML 최상위가 매핑이 아닙니다')
    return data


def _check_mode(mode):
    if mode not in MODES:
        raise ConfigError(f'mode 는 {MODES} 중 하나여야 합니다: {mode!r}')


@dataclass(frozen=True)
class FloorLimit:
    floor_z: float
    safety_margin: float
    nfc_protrusion: float

    def __post_init__(self):
        try:
            for key in ('floor_z', 'safety_margin', 'nfc_protrusion'):
                finite_number(getattr(self, key), key)
            if self.safety_margin < 0 or self.nfc_protrusion < 0:
                raise ValueError('여유 높이와 돌출량은 0 이상이어야 합니다')
            finite_number(self.z_min, 'z_min')
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    @property
    def z_min(self):
        """소프트웨어 하한선 (TCP z 가 이 값보다 낮으면 이동 차단)."""
        return self.floor_z + self.safety_margin + self.nfc_protrusion


def floor_limit(poses_cfg, mode):
    """poses.yaml 내용으로 하한선을 계산한다."""
    _check_mode(mode)
    floor = poses_cfg.get('floor') or {}
    margin = floor.get('safety_margin')
    if margin is None:
        raise ConfigError('floor.safety_margin 이 없습니다')

    src = floor if mode == 'real' else (poses_cfg.get('virtual') or {})
    floor_z = src.get('floor_z')
    nfc = src.get('nfc_protrusion')
    if floor_z is None:
        raise ConfigError(f'[{mode}] floor_z 가 측정되지 않았습니다 (교시 도우미 바닥 측정 모드로 기록)')
    if nfc is None:
        raise ConfigError(f'[{mode}] nfc_protrusion 이 측정되지 않았습니다 (리더기가 손끝보다 위면 0)')
    return FloorLimit(floor_z, margin, nfc)


def get_pose(poses_cfg, name, mode):
    """이름으로 자세를 꺼낸다. 실물 모드에서는 교시된 자세만."""
    _check_mode(mode)
    pose = (poses_cfg.get('poses') or {}).get(name)
    if pose is None:
        raise ConfigError(f'poses.yaml 에 자세 {name!r} 가 없습니다')
    return _checked_pose(pose, f'자세 {name!r}', mode)


def get_box_slot(poses_cfg, slot, mode):
    _check_mode(mode)
    slots = poses_cfg.get('box_slots') or {}
    pose = slots.get(slot, slots.get(str(slot)))
    if pose is None:
        raise ConfigError(f'poses.yaml 에 박스 슬롯 {slot} 이 없습니다')
    return _checked_pose(pose, f'박스 슬롯 {slot}', mode)


def _checked_pose(pose, label, mode):
    if mode == 'real' and pose.get('taught') is not True:
        raise ConfigError(f'{label} 은 아직 교시되지 않았습니다 (taught: false) — 실물에서 사용 불가')
    if pose.get('posx') is None and pose.get('posj') is None:
        raise ConfigError(f'{label} 의 posx/posj 가 비어 있습니다')
    try:
        for key in ('posx', 'posj'):
            if pose.get(key) is not None:
                pose6(pose[key], f'{label}.{key}')
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return pose


@dataclass(frozen=True)
class GraspTarget:
    name: str
    x: float
    y: float
    rz: float
    grasp_z: float
    grip_force: object


def get_product(products_cfg, name, mode, limit):
    """상품의 집기 목표를 계산한다. grasp_z 가 하한선보다 낮으면 ConfigError."""
    _check_mode(mode)
    prod = (products_cfg.get('products') or {}).get(name)
    if prod is None:
        raise ConfigError(f'products.yaml 에 상품 {name!r} 가 없습니다')
    src = prod.get(mode) or {}
    if mode == 'real' and src.get('taught') is not True:
        raise ConfigError(f'상품 {name!r} 은 아직 교시되지 않았습니다 (taught: false) — 실물에서 사용 불가')
    for key in ('x', 'y', 'rz', 'grasp_height_from_floor'):
        if src.get(key) is None:
            raise ConfigError(f'상품 {name!r} [{mode}] 의 {key} 가 비어 있습니다')

    try:
        values = {key: finite_number(src[key], f'{name}.{key}')
                  for key in ('x', 'y', 'rz', 'grasp_height_from_floor')}
        grasp_z = finite_number(limit.floor_z + values['grasp_height_from_floor'], 'grasp_z')
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if grasp_z < limit.z_min:
        raise ConfigError(
            f'상품 {name!r}: 집기 z={grasp_z:.1f} 가 하한선 {limit.z_min:.1f} 보다 낮아 실행하지 않습니다')
    return GraspTarget(name, values['x'], values['y'], values['rz'],
                       grasp_z, prod.get('grip_force'))
