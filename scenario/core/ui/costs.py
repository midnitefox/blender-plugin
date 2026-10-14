# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""CU presentation matching Scenario's web generation indicator."""

from decimal import ROUND_HALF_UP, Decimal, localcontext


def format_cu(value):
    """Group CU values, using three decimals or three significant compact digits.

    Display only: approvals and persisted quotes must retain the server value.
    """
    amount = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits), amount.adjusted() + 4)
        suffix = ""
        places = 3
        if amount >= 10_000:
            for exponent, unit in ((12, "T"), (9, "B"), (6, "M"), (3, "K")):
                if amount >= Decimal(10) ** exponent:
                    amount /= Decimal(10) ** exponent
                    suffix = unit
                    break
            places = 2 - amount.adjusted()
        rounded = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        if suffix and rounded >= 1000 and suffix != "T":
            rounded /= 1000
            suffix = {"K": "M", "M": "B", "B": "T"}[suffix]
        text = format(rounded, "f" if suffix and rounded < 10_000 else ",f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text + suffix


def workflow_loop_warning(loop_steps):
    """Nonblocking text for a workflow quote that may not cover every loop pass.

    `loop_steps` counts the quoted definition's loop nodes, or is None when unknown.
    """
    if loop_steps == 0:
        return None
    if loop_steps is None:
        return (
            "Loop steps could not be checked. The price may cover one loop pass; "
            "the final charge can be higher."
        )
    return (
        "This workflow repeats steps in a loop. The price covers one loop pass; "
        "the final charge can be higher."
    )


def workflow_quote_notice(loop_steps):
    """Structured MCP fields for the same warning; approval stays unchanged."""
    return {
        "loop_steps": loop_steps,
        "quote_may_understate": loop_steps != 0,
        "cost_warning": workflow_loop_warning(loop_steps),
    }
