"""Parameter schema derivation, dotted-path overrides, and a minimal TOML writer.

The schema endpoint exposes editable configuration leaves (numeric and string
fields of the main config sections) enriched with units parsed from field-name
suffix conventions and min/max bounds sourced from ``design_space``.

The TOML writer exists because the runtime environment ships ``tomllib``
(read-only) but not ``tomli-w``; it round-trips the nested dict shape produced
by parsing ``configs/default_wing.toml`` (scalars, lists, tables, and arrays
of tables).
"""

from __future__ import annotations

import json
from typing import Any

from wingopt.config.models import BoundRange, WingGenConfig

# Field-name suffix -> human unit. Longest suffix wins.
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_kmh", "km/h"),
    ("_kgm3", "kg/m^3"),
    ("_gm2", "g/m^2"),
    ("_mah", "mAh"),
    ("_ohm", "ohm"),
    ("_amp", "A"),
    ("_mm", "mm"),
    ("_pa", "Pa"),
    ("_km", "km"),
    ("_deg", "deg"),
    ("_in", "in"),
    ("_m", "m"),
    ("_g", "g"),
    ("_c", "degC"),
    ("_a", "A"),
)

# Dotted config path -> design_space bound attribute path.
_BOUND_SOURCES: dict[str, tuple[str, str]] = {
    "geometry.wingspan_m": ("wing", "wingspan_m"),
    "geometry.root_chord_m": ("wing", "root_chord_m"),
    "geometry.tip_chord_m": ("wing", "tip_chord_m"),
    "geometry.sweep_deg": ("wing", "sweep_deg"),
    "geometry.dihedral_deg": ("wing", "dihedral_deg"),
    "geometry.root_incidence_deg": ("wing", "root_incidence_deg"),
    "geometry.tip_incidence_deg": ("wing", "tip_incidence_deg"),
    "propulsion.prop.diameter_in": ("propulsion", "prop_diameter_in"),
    "propulsion.prop.pitch_in": ("propulsion", "prop_pitch_in"),
    "propulsion.battery.parallel": ("propulsion", "battery_parallel"),
    "environment.temperature_c": ("environment", "temperature_c"),
    "environment.altitude_m": ("environment", "altitude_m"),
    "components.payload_weight_g": ("environment", "payload_weight_g"),
}

# (dotted section prefix, config object attribute path, skipped field names)
_SECTION_SPECS: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    ("mission", ("mission",), frozenset()),
    ("environment", ("environment",), frozenset({"scenarios"})),
    ("geometry", ("geometry",), frozenset({"airfoil", "airfoil_candidates", "elevons"})),
    ("geometry.elevons", ("geometry", "elevons"), frozenset()),
    ("propulsion.motor", ("propulsion", "motor"), frozenset()),
    ("propulsion.prop", ("propulsion", "prop"), frozenset()),
    ("propulsion.battery", ("propulsion", "battery"), frozenset()),
    ("structure", ("structure",), frozenset()),
    ("components", ("components",), frozenset()),
    ("mass", ("mass",), frozenset()),
    ("stability", ("stability",), frozenset()),
)


def unit_for_field(name: str) -> str:
    """Return the display unit for a config field name based on its suffix."""
    for suffix, unit in _UNIT_SUFFIXES:
        if name.endswith(suffix):
            return unit
    if name == "relative_humidity" or name.endswith("_fraction") or name.endswith("_ratio"):
        return "fraction"
    return ""


def _bounds_for(path: str, config: WingGenConfig) -> BoundRange | None:
    source = _BOUND_SOURCES.get(path)
    if source is None:
        return None
    group = getattr(config.design_space, source[0])
    return getattr(group, source[1])


def _leaf_entries(
    prefix: str, obj: Any, skip: frozenset[str], config: WingGenConfig
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name, value in vars(obj).items():
        if name in skip or isinstance(value, (tuple, list, dict)):
            continue
        if isinstance(value, bool):
            kind = "int"
        elif isinstance(value, int):
            kind = "int"
        elif isinstance(value, float) or value is None:
            kind = "float"
        elif isinstance(value, str):
            kind = "str"
        else:
            continue
        path = f"{prefix}.{name}"
        entry: dict[str, Any] = {
            "path": path,
            "unit": unit_for_field(name),
            "default": value,
            "kind": kind,
        }
        bounds = _bounds_for(path, config)
        if bounds is not None:
            entry["min"] = bounds.minimum
            entry["max"] = bounds.maximum
        entries.append(entry)
    return entries


def build_param_schema(config: WingGenConfig) -> list[dict[str, Any]]:
    """Derive the editable parameter schema from a loaded configuration.

    Args:
        config: Fully validated simulator configuration.

    Returns:
        List of ``{path, unit, default, kind, choices?, min?, max?}`` entries.
    """
    entries: list[dict[str, Any]] = []
    for prefix, attr_path, skip in _SECTION_SPECS:
        obj: Any = config
        for attr in attr_path:
            obj = getattr(obj, attr)
        entries.extend(_leaf_entries(prefix, obj, skip, config))
    entries.append(
        {
            "path": "geometry.airfoil",
            "unit": "",
            "default": config.geometry.airfoil,
            "kind": "enum",
            "choices": list(config.geometry.airfoil_candidates),
        }
    )
    entries.sort(key=lambda item: item["path"])
    return entries


# --------------------------------------------------------------------- TOML


def apply_overrides(raw: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply dotted-path overrides onto a raw (parsed TOML) config dict.

    Args:
        raw: Nested dict from ``tomllib``; not mutated.
        overrides: Mapping of dotted paths (``"geometry.wingspan_m"``) to values.

    Returns:
        A deep-copied dict with overrides applied.

    Raises:
        ValueError: If a path traverses a non-table value.
    """
    result = json.loads(json.dumps(raw))
    for dotted, value in overrides.items():
        parts = [part for part in str(dotted).split(".") if part]
        if not parts:
            raise ValueError(f"Invalid override path: {dotted!r}")
        node = result
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Override path {dotted!r} traverses non-table {part!r}")
            node = child
        node[parts[-1]] = value
    return result


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    raise ValueError(f"Unsupported TOML value type: {type(value)!r}")


def _is_table_array(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) > 0
        and all(isinstance(item, dict) for item in value)
    )


def _emit_table(name: str, table: dict[str, Any], lines: list[str], header: str | None) -> None:
    scalars = {
        k: v for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)
    }
    subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
    table_arrays = {k: v for k, v in table.items() if _is_table_array(v)}

    if header is not None and (scalars or not (subtables or table_arrays)):
        lines.append(f"[{header}]")
    for key, value in scalars.items():
        if value is None:
            continue
        lines.append(f"{key} = {_format_toml_value(value)}")
    if scalars and header is not None:
        lines.append("")

    for key, value in table_arrays.items():
        child = f"{name}.{key}" if name else key
        for item in value:
            lines.append(f"[[{child}]]")
            for sub_key, sub_value in item.items():
                if isinstance(sub_value, dict) or _is_table_array(sub_value) or sub_value is None:
                    raise ValueError(f"Nested tables inside table array {child!r} unsupported")
                lines.append(f"{sub_key} = {_format_toml_value(sub_value)}")
            lines.append("")

    for key, value in subtables.items():
        child = f"{name}.{key}" if name else key
        _emit_table(child, value, lines, header=child)


def dump_toml(data: dict[str, Any]) -> str:
    """Serialize a nested dict of scalars/lists/tables to TOML text.

    Supports the value shapes present in ``configs/default_wing.toml``:
    scalars, homogeneous scalar lists, nested tables, and arrays of flat
    tables. ``None`` values are omitted.
    """
    lines: list[str] = []
    top_scalars = {
        k: v for k, v in data.items() if not isinstance(v, dict) and not _is_table_array(v)
    }
    for key, value in top_scalars.items():
        if value is None:
            continue
        lines.append(f"{key} = {_format_toml_value(value)}")
    if top_scalars:
        lines.append("")
    for key, value in data.items():
        if isinstance(value, dict):
            _emit_table(key, value, lines, header=key)
        elif _is_table_array(value):
            for item in value:
                lines.append(f"[[{key}]]")
                for sub_key, sub_value in item.items():
                    lines.append(f"{sub_key} = {_format_toml_value(sub_value)}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"
