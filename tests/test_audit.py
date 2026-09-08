from __future__ import annotations

import unittest

from aag_safe_suspend import audit


def mount(owner: str, namespace: str, shared: str | None = None, master: str | None = None) -> dict:
    propagation = {}
    if shared:
        propagation["shared"] = shared
    if master:
        propagation["master"] = master
    return {
        "kind": "mount",
        "owner": owner,
        "namespace": namespace,
        "mount_id": "10",
        "parent_id": "1",
        "dev": "65:145",
        "root": "/",
        "target": "/media/test/backup-a",
        "options": "rw",
        "propagation": propagation,
        "fstype": "ext4",
        "source": "/dev/sdz1",
    }


def result(records: list[dict], complete: bool = True, stable: bool = True) -> dict:
    return {
        "AUDIT_COMPLETE": complete,
        "EXTERNAL_OWNER": "MOUNT_ONLY" if records else "NONE",
        "records": records,
        "gaps": [] if complete else [{"kind": "permission"}],
        "semantic_stable_scan_2_to_3": stable,
    }


class AuditTests(unittest.TestCase):
    def test_host_mount_and_snap_slave_mirror_are_safe(self) -> None:
        rows = [mount("HOST", "host", shared="77"), mount("CONTAINER", "snap", master="77")]
        self.assertEqual(audit.unsafe_mount_topology(rows), [])
        self.assertEqual(audit.disposition(result(rows), True)[0], "UNMOUNT")

    def test_systemd_sandbox_propagated_mirror_is_safe(self) -> None:
        rows = [mount("HOST", "host", shared="91"), mount("CONTAINER", "sandbox", master="91")]
        self.assertEqual(audit.disposition(result(rows), True)[0], "UNMOUNT")

    def test_independent_container_mount_fails_closed(self) -> None:
        rows = [mount("HOST", "host", shared="77"), mount("CONTAINER", "container")]
        self.assertEqual(
            audit.disposition(result(rows), True), ("FAIL", "independent-mount-namespace")
        )

    def test_raw_fd_owner_fails_closed(self) -> None:
        row = {"kind": "raw-fd", "owner": "RAW", "pid": 42, "start": "100", "dev": "65:145"}
        self.assertEqual(audit.disposition(result([row]), True)[0], "FAIL")

    def test_mmap_owner_fails_closed(self) -> None:
        row = {"kind": "mmap", "owner": "HOST", "pid": 43, "start": "101", "dev": "65:145"}
        self.assertEqual(audit.disposition(result([row]), True)[0], "FAIL")

    def test_dm_nbd_loop_and_guest_owners_fail_closed(self) -> None:
        for kind, owner in (
            ("dm", "RAW"),
            ("nbd", "RAW"),
            ("loop-backing", "RAW"),
            ("fd", "GUEST"),
        ):
            row = {"kind": kind, "owner": owner, "dev": "65:145"}
            with self.subTest(kind=kind):
                self.assertEqual(audit.disposition(result([row]), True)[0], "FAIL")

    def test_permission_gap_fails_closed(self) -> None:
        self.assertEqual(audit.disposition(result([], complete=False), True)[0], "FAIL")

    def test_mount_change_retries(self) -> None:
        value = result([])
        value["semantic_stable_scan_2_to_3"] = False
        self.assertEqual(audit.disposition(value, True)[0], "RETRY")

    def test_pid_churn_without_semantic_change_is_not_meaning_change(self) -> None:
        first = result([{"kind": "fd", "owner": "HOST", "pid": 100, "start": "1", "dev": "65:145"}])
        second = result(
            [{"kind": "fd", "owner": "HOST", "pid": 101, "start": "2", "dev": "65:145"}]
        )
        change = audit.delta(first, second)
        self.assertIn("PID_STARTED", change["classes"])
        self.assertIn("PID_EXITED", change["classes"])
        self.assertFalse(change["meaning_changed"])

    def test_zombie_stat_is_parsed_without_resource_assumption(self) -> None:
        raw = "9 (finished worker) Z 1 0 0 0 0 0 0 0 0 0 2 3 0 0 0 0 0 0 987 0 0"
        value = audit.parse_proc_stat(raw)
        self.assertEqual(value["state"], "Z")
        self.assertEqual(value["start"], "987")


if __name__ == "__main__":
    unittest.main()
