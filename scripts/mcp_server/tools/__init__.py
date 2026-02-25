"""Auto-discovery for tool modules.

Any .py file in this directory that exposes a `register(mcp)` function
will be picked up automatically.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_all(mcp: FastMCP) -> None:
    """Import every submodule and call its ``register(mcp)`` if present."""
    for module_info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        if hasattr(module, "register"):
            module.register(mcp)
