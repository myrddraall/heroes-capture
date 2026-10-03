"""JSON written the way JavaScript's JSON.stringify writes it, so files the Node tools wrote
(manifests, light-sets.json) come out byte for byte the same: numbers as JavaScript prints them
(26, not 26.0; 0.00001, not 1e-05) and its indentation."""

import json
from decimal import Decimal


def number(value: int | float) -> str:
    """A number as JavaScript's Number#toString prints it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"not a number: {value!r}")
    if isinstance(value, int):
        return str(value)
    if value != value or value in (float("inf"), float("-inf")):
        return "null"  # as JSON.stringify writes NaN and the infinities
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))  # -0.0 included, which JavaScript prints as 0
    # The shortest digits that round-trip (repr's, the same digits JavaScript picks), placed as
    # ECMAScript's Number::toString places them.
    sign, all_digits, exponent = Decimal(repr(value)).as_tuple()
    point = len(all_digits) + exponent  # where the decimal point goes, counted from the first digit
    digits = "".join(map(str, all_digits)).rstrip("0") or "0"
    k = len(digits)
    minus = "-" if sign else ""
    if k <= point <= 21:
        return minus + digits + "0" * (point - k)
    if 0 < point <= 21:
        return minus + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return minus + "0." + "0" * -point + digits
    mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{minus}{mantissa}e{'+' if point - 1 > 0 else '-'}{abs(point - 1)}"


def dumps(value, indent: int | None = None, _level: int = 0) -> str:
    """JSON.stringify(value, null, indent)."""
    if value is None or value is True or value is False:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        items = [(json.dumps(str(k), ensure_ascii=False), v) for k, v in value.items()]
        if not items:
            return "{}"
        parts = [f"{k}:{' ' if indent else ''}{dumps(v, indent, _level + 1)}" for k, v in items]
        return _wrap("{", parts, "}", indent, _level)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return _wrap("[", [dumps(v, indent, _level + 1) for v in value], "]", indent, _level)
    raise TypeError(f"can't write {type(value).__name__} as JSON")


def _wrap(open_: str, parts: list[str], close: str, indent: int | None, level: int) -> str:
    if not indent:
        return open_ + ",".join(parts) + close
    inner = "\n" + " " * (indent * (level + 1))
    return open_ + inner + ("," + inner).join(parts) + "\n" + " " * (indent * level) + close
