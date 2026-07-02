"""Dotted config-path utilities for rebuilding nested frozen dataclasses.

The simulator configuration (:class:`wingopt.config.models.WingGenConfig`) is a
tree of frozen dataclasses. This module applies a value at a dotted path such
as ``"propulsion.battery.parallel"`` by walking the path and rebuilding every
dataclass on the way back up with :func:`dataclasses.replace`.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, TypeVar

ConfigT = TypeVar("ConfigT")


class ConfigPathError(ValueError):
    """Raised when a dotted parameter path cannot be applied to a config."""


def _resolve_leaf_annotation(annotation: Any, path: str) -> Any:
    """Collapse ``X | None`` annotations to ``X``; reject other unions."""
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
        raise ConfigPathError(f"Parameter path {path!r} has an unsupported union type")
    return annotation


def _coerce_leaf(value: Any, annotation: Any, path: str) -> Any:
    """Coerce ``value`` to the declared leaf type, or raise :class:`ConfigPathError`.

    Integer-typed fields (e.g. ``propulsion.battery.parallel``) accept floats
    and are rounded to the nearest integer.
    """
    target = _resolve_leaf_annotation(annotation, path)
    if target is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigPathError(f"Parameter path {path!r} expects a number, got {value!r}")
        return round(float(value))
    if target is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigPathError(f"Parameter path {path!r} expects a number, got {value!r}")
        return float(value)
    if target is str:
        if not isinstance(value, str):
            raise ConfigPathError(f"Parameter path {path!r} expects a string, got {value!r}")
        return value
    raise ConfigPathError(
        f"Parameter path {path!r} does not resolve to a numeric or string leaf"
    )


def _apply(node: Any, parts: list[str], value: Any, full_path: str) -> Any:
    if not dataclasses.is_dataclass(node):
        raise ConfigPathError(
            f"Unknown parameter path {full_path!r}: {parts[0]!r} is not inside a config section"
        )
    name = parts[0]
    field_names = {field.name for field in dataclasses.fields(node)}
    if name not in field_names:
        raise ConfigPathError(
            f"Unknown parameter path {full_path!r}: "
            f"{type(node).__name__} has no field {name!r}"
        )
    if len(parts) == 1:
        hints = typing.get_type_hints(type(node))
        coerced = _coerce_leaf(value, hints[name], full_path)
        return dataclasses.replace(node, **{name: coerced})
    child = getattr(node, name)
    if not dataclasses.is_dataclass(child):
        raise ConfigPathError(
            f"Unknown parameter path {full_path!r}: {name!r} is not a nested config section"
        )
    rebuilt_child = _apply(child, parts[1:], value, full_path)
    return dataclasses.replace(node, **{name: rebuilt_child})


def set_config_value(config: ConfigT, path: str, value: Any) -> ConfigT:
    """Return a copy of ``config`` with the leaf at dotted ``path`` set to ``value``.

    Args:
        config: Root frozen dataclass (typically :class:`WingGenConfig`).
        path: Dotted attribute path, e.g. ``"geometry.wingspan_m"``.
        value: New leaf value; coerced to the declared field type.

    Returns:
        A rebuilt config of the same type.

    Raises:
        ConfigPathError: If the path is malformed, unknown, or not a leaf.
    """
    parts = path.split(".")
    if len(parts) < 2 or not all(parts):
        raise ConfigPathError(f"Invalid parameter path: {path!r} (need 'section.field')")
    return _apply(config, parts, value, path)


def apply_parameters(config: ConfigT, params: dict[str, Any]) -> ConfigT:
    """Apply a mapping of dotted paths to values, returning the rebuilt config."""
    for path, value in params.items():
        config = set_config_value(config, path, value)
    return config
