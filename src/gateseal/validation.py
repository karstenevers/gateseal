import hashlib
import json
import math
import re
from typing import NoReturn, cast

from gateseal.diagnostics import GatesealError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(k, str) for k in cast(dict[object, object], value)
    ):
        raise GatesealError("invalid_field", f"{field} must be an object.")
    return cast(dict[str, object], value)


def fields(value: object, names: set[str], field: str) -> dict[str, object]:
    result = mapping(value, field)
    if set(result) != names:
        raise GatesealError(
            "invalid_field", f"{field} requires exactly {', '.join(sorted(names))}."
        )
    return result


def string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise GatesealError(
            "invalid_field", f"{field} must be a nonempty printable string."
        )
    return value


def sequence(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise GatesealError("invalid_field", f"{field} must be an array.")
    return cast(list[object], value)


def schema(value: object) -> None:
    if type(value) is not int:
        raise GatesealError("invalid_field", "schema must be an integer.")
    if value != 1:
        raise GatesealError("unsupported_schema", "Only schema 1 is supported.")


def version(value: object, field: str) -> str:
    text = string(value, field)
    if not re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", text
    ):
        raise GatesealError(
            "invalid_field", f"{field} must be a stable major.minor.patch release."
        )
    return text


def version_key(text: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version(text, "version").split("."))
    return major, minor, patch


def digest(value: object, field: str) -> str:
    text = string(value, field)
    if not re.fullmatch("[0-9a-f]{64}", text):
        raise GatesealError("invalid_field", f"{field} must be a lowercase SHA-256.")
    return text


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def parse_toml(data: bytes) -> dict[str, object]:
    import tomllib

    try:
        return cast(dict[str, object], tomllib.loads(data.decode("utf-8")))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise GatesealError(
            "invalid_toml", "Expected valid UTF-8 TOML without duplicate keys."
        ) from exc


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GatesealError("invalid_json", f"Duplicate JSON key {key!r}.")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise GatesealError(
        "invalid_json", f"Non-finite JSON number {value} is unsupported."
    )


def _finite(text: str) -> float:
    value = float(text)
    return value if math.isfinite(value) else _constant(text)


def decode_json(data: bytes) -> dict[str, object]:
    """Decode a UTF-8 JSON object, rejecting duplicate keys and nonfinite numbers."""
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_finite,
        )
    except ValueError as exc:
        raise GatesealError("invalid_json", "Expected UTF-8 JSON.") from exc
    return mapping(value, "document")
