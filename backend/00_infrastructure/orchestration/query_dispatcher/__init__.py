"""Smart business-query dispatch helpers.

This package keeps the orchestration rules separate from the low-level
``SmartQueryGate`` mutex.  The gate still owns the single-business-API lock;
these helpers decide cadence, candidate visibility and UI state.
"""

