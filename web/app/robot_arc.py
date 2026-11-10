"""Cartesian arc validity, shared by scenario preview and execution admission."""
import math
from .store import DomainError


def validate_arc(start,via,end):
    if via is None:raise DomainError("movec 원호 경유 TCP 여섯 값을 입력하세요.")
    a=[via[i]-start[i] for i in range(3)];b=[end[i]-start[i] for i in range(3)]
    c=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
    la=math.hypot(*a);lb=math.hypot(*b);lc=math.dist(via[:3],end[:3])
    if min(la,lb,lc)<.01 or math.hypot(*c)<=1e-4*la*lb:
        raise DomainError("movec의 시작·경유·목표 위치는 서로 다르고 일직선에 놓이지 않아야 합니다.")
