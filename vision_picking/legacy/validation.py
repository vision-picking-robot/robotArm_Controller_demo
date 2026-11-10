"""Shared validation at configuration and motion boundaries."""
import math
from numbers import Real


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f'{label}: 유한한 숫자가 필요합니다: {value!r}')
    return float(value)


def pose6(value, label):
    try:
        values = list(value)
    except TypeError:
        raise ValueError(f'{label}: 숫자 6개가 필요합니다') from None
    if len(values) != 6:
        raise ValueError(f'{label}: 숫자 6개가 필요합니다')
    return [finite_number(v, label) for v in values]
