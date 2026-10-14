# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open Studio, derived from Scenario for Blender.

Entry point of the extension. Imports of bpy stay inside register/unregister
so that `scenario.core` is importable from plain Python (unit tests, tooling).
"""

__version__ = "0.11.0"  # x-release-please-version


def check_registration_conflicts(enabled_packages, own_package):
    """Fail before registering shared legacy RNA names or starting any service."""
    for package in enabled_packages:
        if package == own_package:
            continue
        identity = package.rsplit(".", 1)[-1]
        if identity == "scenario":
            raise RuntimeError(
                "Disable Scenario before enabling Open Studio; they share legacy Blender tool identifiers"
            )
        if identity == "open_studio":
            raise RuntimeError(
                "Disable the other Open Studio installation before enabling this copy"
            )


def register():
    import bpy

    check_registration_conflicts(bpy.context.preferences.addons.keys(), __package__)
    from .blender import job_session, registry

    registry.register()
    job_session.register()


def unregister():
    from .blender import job_session, registry

    job_session.unregister()
    registry.unregister()
