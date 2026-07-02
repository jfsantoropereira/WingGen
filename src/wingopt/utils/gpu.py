"""Small array backend helpers for optional MLX acceleration.

The VLM solver uses these helpers to keep backend selection and linear solves in
one place. MLX is used for vectorized AIC assembly when Metal is available; the
linear system is solved with NumPy for portability because MLX GPU linear solve
support varies by version and dtype.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

ArrayLike = npt.NDArray[np.float64]


def metal_available() -> bool:
    """Return whether MLX can use Apple Metal in this environment."""

    probe = (
        "import mlx.core as mx; "
        "ok = bool(mx.metal.is_available()); "
        "mx.eval(mx.array([0.0])) if ok else None; "
        "raise SystemExit(0 if ok else 1)"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def select_backend(requested: str) -> str:
    """Resolve a requested backend to the backend actually used.

    Args:
        requested: One of ``"auto"``, ``"mlx"``, or ``"numpy"``.

    Returns:
        ``"mlx"`` when requested/auto and Metal-backed MLX is available,
        otherwise ``"numpy"``.

    Raises:
        ValueError: If an unknown backend name is supplied.
    """

    if requested not in {"auto", "mlx", "numpy"}:
        raise ValueError("backend must be auto, mlx, or numpy")
    if requested == "numpy":
        return "numpy"
    if metal_available():
        return "mlx"
    return "numpy"


@dataclass(frozen=True)
class ArrayOps:
    """Minimal array facade used by hot-path VLM assembly."""

    backend: str
    module: Any

    def asarray(self, values: Any) -> Any:
        """Convert values to a backend float array."""

        if self.backend == "mlx":
            dtype = getattr(self.module, "float64", self.module.float32)
            return self.module.array(values, dtype=dtype)
        return self.module.asarray(values, dtype=np.float64)

    def to_numpy(self, values: Any) -> ArrayLike:
        """Return a NumPy ``float64`` copy of a backend array."""

        if self.backend == "mlx":
            return np.asarray(values, dtype=np.float64)
        return np.asarray(values, dtype=np.float64)


def get_array_ops(requested: str) -> ArrayOps:
    """Create array operations for the resolved backend."""

    backend = select_backend(requested)
    if backend == "mlx":
        import mlx.core as mx  # type: ignore[import-not-found]

        return ArrayOps(backend=backend, module=mx)
    return ArrayOps(backend="numpy", module=np)


def solve_linear(a_matrix: Any, rhs: Any) -> ArrayLike:
    """Solve a dense linear system using NumPy.

    MLX arrays are copied back to CPU before solving. AIC construction is the
    dominant vectorized workload for the small lattices used here, while this
    CPU solve keeps behavior consistent across Python/MLX versions.
    """

    a_np = np.asarray(a_matrix, dtype=np.float64)
    rhs_np = np.asarray(rhs, dtype=np.float64)
    return np.linalg.solve(a_np, rhs_np)
