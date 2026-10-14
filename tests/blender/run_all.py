# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run installed-ZIP tests through tools/test_blender.py; refuse unguarded execution."""

import argparse
import faulthandler
import importlib
import importlib.abc
import ipaddress
import json
import os
import pathlib
import socket
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

from blender_env import inspect_zip, sha256, verify_installed  # noqa: E402

# The shipped assistant-only runtime. Inherited service tests remain available
# through explicit --test selection or --suite all for historical maintenance.
BASELINE = (
    "test_provider_setup",
    "test_assistant_mode",
    "test_agent_artifacts",
    "test_register",
    "test_updates",
    "test_mcp_panel",
    "test_mcp_server",
    "test_mcp_security.McpSecurityTests",
    "test_mcp_cli",
    "test_local_capture",
    "test_shot_planner",
    "test_file_modified.SceneToolTests",
    "test_file_modified.FileStateTests.test_a_mark_records_one_undo_step_that_marks_the_file",
    "test_file_modified.FileStateTests.test_a_timer_context_without_a_window_marks_through_the_first_window",
    "test_file_modified.FileStateTests.test_a_mark_during_a_stroke_waits_for_the_stroke_to_end",
    "test_file_modified.FileStateTests.test_strokes_are_recognized_by_their_operator_family",
    "test_file_modified.FileStateTests.test_reading_a_file_drops_a_waiting_mark",
    "test_file_modified.FileStateTests.test_marks_are_refused_off_the_main_thread",
    "test_desktop_open.DesktopOpenTests.test_local_folder_opens_through_the_filtered_launcher",
    "test_desktop_open.DesktopOpenTests.test_preference_links_open_through_the_filtered_launcher",
    "test_desktop_open.DesktopOpenTests.test_the_started_opener_gets_no_credentials_python_start_up_or_proxies",
    "test_desktop_open.DesktopOpenTests.test_refusals_are_reported_before_anything_starts",
    "test_desktop_open.DesktopOpenTests.test_a_missing_opener_is_reported",
    "test_desktop_open.DesktopOpenTests.test_a_launcher_that_exits_with_an_error_is_reported_on_the_status_line",
    "test_desktop_open.DesktopOpenTests.test_a_launcher_that_keeps_running_is_forgotten_without_a_report",
)


# A test running longer than this dumps every thread's stack and carries on, so
# a hang shows where it waits. The slowest installed test takes about ten seconds.
TEST_WATCHDOG_SECONDS = 60


class PromptResult(unittest.TextTestResult):
    """Print each failure's traceback when its test ends, not only in the summary.

    tools/test_blender.py stops Blender after its per-process timeout, before
    unittest prints the summary, so the reasons for earlier failures would be
    lost with it. A test running past ``TEST_WATCHDOG_SECONDS`` also dumps the
    stack of every thread without stopping the run.
    """

    def startTest(self, test):
        super().startTest(test)
        try:
            faulthandler.dump_traceback_later(TEST_WATCHDOG_SECONDS, exit=False)
        except (AttributeError, OSError, RuntimeError, ValueError):
            pass  # No file descriptor behind stderr: the summary still reports failures.

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)

    def _prompt(self, record, *args):
        failures, errors = len(self.failures), len(self.errors)
        record(*args)
        for flavour, items in (("FAIL", self.failures[failures:]), ("ERROR", self.errors[errors:])):
            for test, details in items:
                self.stream.writeln(f"{flavour} DETAILS: {self.getDescription(test)}")
                self.stream.writeln(details)
        self.stream.flush()

    def addError(self, test, err):
        self._prompt(super().addError, test, err)

    def addFailure(self, test, err):
        self._prompt(super().addFailure, test, err)

    def addSubTest(self, test, subtest, err):
        self._prompt(super().addSubTest, test, subtest, err)


class NoSourceImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scenario" or fullname.startswith("scenario."):
            raise ImportError("Source-checkout imports are forbidden in installed-ZIP tests")
        return None


def install_network_guard():
    violations = []

    def audit(event, args):
        if event == "socket.getaddrinfo":
            host = args[0]
        elif event in {"socket.connect", "socket.bind", "socket.sendto"}:
            if args[0].family == getattr(socket, "AF_UNIX", None):
                return
            address = args[1] if event != "socket.sendto" else args[-1]
            host = address[0] if isinstance(address, tuple) else address
        else:
            return
        if host in {"localhost", b"localhost"}:
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            # Non-numeric hosts other than localhost fall through to rejection.
            pass
        violations.append(event)
        raise RuntimeError("External network access is forbidden in offline Blender tests")

    sys.addaudithook(audit)
    return violations


def load_suite(loader, suite_name, selected_tests):
    if selected_tests:
        return loader.loadTestsFromNames(selected_tests)
    if suite_name == "all":
        return loader.discover(str(HERE), pattern="test_*.py")
    return loader.loadTestsFromNames(BASELINE)


def main():
    # No real-profile opt-out: contributors should always use the runner.
    raw_profile = os.environ.get("BLENDER_USER_RESOURCES")
    if not raw_profile:
        print("Refusing real-profile tests: use make test-blender", file=sys.stderr)
        raise SystemExit(2)
    profile = pathlib.Path(raw_profile).resolve()
    marker = profile.parent / "runner.json"
    try:
        ownership = json.loads(marker.read_text())
    except (OSError, ValueError):
        ownership = None
    if not isinstance(ownership, dict) or ownership.get("profile") != str(profile):
        print("Refusing unmanaged profile: use make test-blender", file=sys.stderr)
        raise SystemExit(2)
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=pathlib.Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--suite", choices=("baseline", "all"), required=True)
    parser.add_argument("--test", action="append", default=[])
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    if args.zip.resolve().parent != profile.parent or sha256(args.zip) != args.sha256:
        raise RuntimeError("Candidate ZIP identity mismatch")
    import bpy
    import helpers

    if pathlib.Path(bpy.utils.resource_path("USER")).resolve() != profile:
        raise RuntimeError("Blender did not use the disposable profile")
    manifest, _ = inspect_zip(args.zip)
    package = f"bl_ext.user_default.{manifest['id']}"
    installed = profile / "extensions/user_default" / manifest["id"]
    verify_installed(args.zip, installed)
    module = importlib.import_module(package)
    if pathlib.Path(module.__file__).resolve() != installed / "__init__.py":
        raise RuntimeError("Blender loaded a different installed package")
    if package not in bpy.context.preferences.addons:
        raise RuntimeError("Candidate extension is not enabled")
    sys.meta_path.insert(0, NoSourceImports())
    violations = install_network_guard()
    bpy.context.preferences.system.use_online_access = False
    helpers.configure(package, installed, profile)
    print(f"Verified installed package: {package}; ZIP SHA-256: {args.sha256}", flush=True)
    loader = unittest.TestLoader()
    suite = load_suite(loader, args.suite, args.test)
    result = unittest.TextTestRunner(verbosity=2, resultclass=PromptResult).run(suite)
    # Byte equality and module origins must still hold after tests execute.
    if sha256(args.zip) != args.sha256:
        raise RuntimeError("Candidate ZIP changed during tests")
    verify_installed(args.zip, installed)
    for name, imported in tuple(sys.modules.items()):
        if name == "scenario" or name.startswith("scenario."):
            raise RuntimeError("Tests imported the source checkout")
        if name == package or name.startswith(package + "."):
            path = getattr(imported, "__file__", None)
            if path and not pathlib.Path(path).resolve().is_relative_to(installed):
                raise RuntimeError("An extension module was loaded from outside the candidate")
    # Exercise disable/re-enable on the actual registered extension, after all tests.
    import addon_utils

    addon_utils.disable(package, default_set=True)
    if package in bpy.context.preferences.addons or hasattr(bpy.types.Scene, "scenario_shot"):
        raise RuntimeError("Extension disable left registration state behind")
    addon_utils.enable(package, default_set=True)
    if package not in bpy.context.preferences.addons or not hasattr(
        bpy.types.Scene, "scenario_shot"
    ):
        raise RuntimeError("Extension re-enable failed")
    addon_utils.disable(package, default_set=True)
    success = result.wasSuccessful() and not violations and result.testsRun > 0
    args.report.write_text(
        json.dumps(
            {
                "success": success,
                "suite": "focused" if args.test else args.suite,
                "selected_tests": args.test,
                "tests_run": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skips": len(result.skipped),
                "network_violations": violations,
                "zip_sha256": args.sha256,
                "package": package,
                "installed_path": str(installed),
                "lifecycle": "passed",
                "sdk_bundle": getattr(sys.modules.get("test_sdk_bundle"), "EVIDENCE", None),
            },
            indent=2,
        )
        + "\n"
    )
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
