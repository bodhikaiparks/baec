"""BAEC Engine 3 deterministic preparation core.

Stage D intentionally contains no AI provider, network, persistence, UI,
outreach execution, CRM integration, or write path into Engines 1 or 2.
"""

from .validator import validate_case

__all__ = ["validate_case"]
