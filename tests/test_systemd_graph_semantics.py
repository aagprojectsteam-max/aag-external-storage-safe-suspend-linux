from __future__ import annotations

import unittest

from aag_safe_suspend import systemd_graph


def command(path: str, argv: str, *, runtime: str = "[n/a]") -> str:
    return (
        f"{{ path={path} ; argv[]={argv} ; ignore_errors=no ; "
        f"start_time={runtime} ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }}"
    )


def snapshot(*, installed: bool, reorder: bool = False, changed_command: bool = False) -> str:
    prepare = systemd_graph.ORDINARY_PREPARE if installed else systemd_graph.OLD_PREPARE
    requires = ["system.slice", prepare, "sleep.target"]
    after = [prepare, "system.slice", "systemd-journald.socket", "sleep.target"]
    before = [systemd_graph.RESUME, "suspend.target"]
    if reorder:
        requires.reverse()
        after.reverse()
        before.reverse()
    executable = "/usr/lib/systemd/systemd-sleep"
    argv = executable + (" wrong" if changed_command else " suspend")
    return (
        "\n".join(
            (
                "Requires=" + " ".join(requires),
                "Requisite=",
                "Wants=" + systemd_graph.RESUME,
                "Upholds=",
                "Conflicts=",
                "Before=" + " ".join(before),
                "After=" + " ".join(after),
                "OnFailure=" + systemd_graph.FAILURE,
                "OnSuccess=",
                "DropInPaths=" + systemd_graph.ORDINARY_DROPIN,
                "ExecCondition=",
                "ExecStartPre=",
                "ExecStart=" + command(executable, argv, runtime="synthetic-runtime"),
                "ExecStartPost=",
                "ExecStop=",
                "ExecStopPost=",
            )
        )
        + "\n"
    )


class SystemdGraphSemanticTests(unittest.TestCase):
    def test_capture_surface_pins_the_complete_accepted_graph_contract(self) -> None:
        for dependency in ("Upholds", "Requires", "Before", "After", "OnFailure"):
            self.assertIn(dependency, systemd_graph.SET_PROPERTIES)
        for scalar in (
            "FragmentPath",
            "UnitFileState",
            "DefaultDependencies",
            "OnFailureJobMode",
            "Type",
            "TimeoutStartUSec",
            "RemainAfterExit",
            "OOMPolicy",
            "KillMode",
        ):
            self.assertIn(scalar, systemd_graph.CAPTURE_PROPERTIES)

    def test_order_and_exec_runtime_noise_are_not_semantic(self) -> None:
        before = snapshot(installed=False)
        after = snapshot(installed=True, reorder=True)
        systemd_graph.require_ordinary_install(before, after, prior_release=True)

    def test_real_command_or_dependency_change_is_rejected(self) -> None:
        before = snapshot(installed=False)
        with self.assertRaises(systemd_graph.GraphError):
            systemd_graph.require_ordinary_install(
                before,
                snapshot(installed=True, changed_command=True),
                prior_release=True,
            )
        extra = snapshot(installed=True).replace("Requires=", "Requires=unexpected.service ", 1)
        with self.assertRaises(systemd_graph.GraphError):
            systemd_graph.require_ordinary_install(before, extra, prior_release=True)

    def test_dropin_order_and_membership_are_semantic(self) -> None:
        before = snapshot(installed=False)
        extra = snapshot(installed=True).replace(
            "DropInPaths=",
            "DropInPaths=/etc/systemd/system/systemd-suspend.service.d/99-extra.conf ",
            1,
        )
        with self.assertRaises(systemd_graph.GraphError):
            systemd_graph.require_ordinary_install(before, extra, prior_release=True)

    def test_hibernate_set_order_is_noise_but_usbclone_edge_is_rejected(self) -> None:
        before = snapshot(installed=False)
        reordered = snapshot(installed=False, reorder=True)
        systemd_graph.require_equal(before, reordered, "Hibernate fixture")
        leaked = systemd_graph.canonical(reordered)
        leaked["After"].append(systemd_graph.ORDINARY_PREPARE)
        with self.assertRaises(systemd_graph.GraphError):
            systemd_graph.require_hibernate_isolation(leaked)


if __name__ == "__main__":
    unittest.main()
