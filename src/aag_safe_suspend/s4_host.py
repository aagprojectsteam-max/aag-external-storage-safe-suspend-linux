"""S4 specialization of the accepted Host; no second storage preparation stack."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

from . import production as p
from . import resume_observer as observer
from . import s4_checks as checks
from . import s4_transaction as s4
from . import transaction as tx
from .transaction import Refusal

NATIVE = "systemd-hibernate.service"
FINISH = s4.OWNER
PERMIT = p.STATE / "hibernate-permit.json"
RETIRED = (
    "aag-hibernate-abort-reconcile.service",
    "aag-hibernate-boot-check.service",
    "aag-hibernate-resume-check.service",
    "aag-hibernate-wwan-recovery.service",
)


class Host(p.Host):
    def __init__(self, config=None, s4_config=None):
        super().__init__(config)
        self.s4_cfg = checks.configuration() if s4_config is None else s4_config
        self.before = None

    def persist(self, path, value):
        self.r.atomic_json(path, value)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def save(self, value=None):
        value = self.value if value is None else value
        if (
            self.before is not None
            and value.get("episode")
            and "transaction_type" not in value
            and value.get("prep_invocation") == os.environ.get("INVOCATION_ID")
            and value.get("stage") == "REQUESTED"
            and value.get("state") == "PREPARING_SLEEP"
        ):
            s4.attach(value, self.before)
            s4.observe(value, "PREPARING", reused="v1.3.1 Host.begin")
        super().save(value)

    def phase(self, name, **evidence):
        with self.r.state_lock():
            live = self.r.load_state()
            if live.get("episode") != self.value.get("episode"):
                raise Refusal("S4 phase owner changed")
            s4.observe(live, name, **evidence)
            self.value = live
            self.save()
        self.emit("S4_PHASE", phase=name, **evidence)

    def load_current(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        return self.value

    def live_owner(self, value):
        if super().live_owner(value):
            return True
        for unit, key in ((NATIVE, "native_invocation"), (FINISH, "finish_invocation")):
            state = p.properties(unit)
            if (
                value.get(key)
                and value[key] == state.get("InvocationID")
                and state.get("ActiveState") in {"active", "activating", "deactivating"}
            ):
                return True
        return False

    def owner_graph(self):
        result = {"owner": FINISH, "checks": {}}
        for unit in RETIRED:
            output = p.run(
                ["/usr/bin/systemctl", "show", unit, "--property=ExecStart,ActiveState,Job"]
            ).stdout
            fields = dict(x.split("=", 1) for x in output.splitlines() if "=" in x)
            result["checks"][unit] = (
                "/usr/bin/false" in fields.get("ExecStart", "")
                and fields.get("ActiveState") in {"inactive", "failed"}
                and fields.get("Job") in {"", "0"}
            )
        for unit in (p.VERIFY,):
            fields = p.properties(unit)
            result["checks"][unit] = fields.get("ActiveState") in {
                "inactive",
                "failed",
            } and fields.get("Job") in {"", "0"}
        result["checks"]["no_suspend_device_transaction"] = not any(
            x.exists() for x in (self.t.PENDING, self.t.QUEUE, self.t.BLOCKED)
        )
        native = p.run(
            [
                "/usr/bin/systemctl",
                "show",
                NATIVE,
                "--property=ExecStartPre,ExecStopPost,OnSuccess,OnFailure",
            ]
        ).stdout
        fields = dict(x.split("=", 1) for x in native.splitlines() if "=" in x)
        result["checks"]["native_owner"] = (
            set(fields.get("OnSuccess", "").split()) == {FINISH}
            and set(fields.get("OnFailure", "").split()) == {FINISH}
            and "hibernate-native-pre" in native
            and "hibernate-native-post" in native
            and "aag-hibernate-qualifier" not in native
        )
        result["result"] = "PASS" if all(result["checks"].values()) else "FAIL"
        return result

    def network_sample(self):
        sample = checks.modem_generation(self.s4_cfg)
        sample.update(self.t.core.cellular())
        enumeration = p.run(["/usr/bin/mmcli", "-L"], timeout=12, check=False)
        sample["modem_present"] = enumeration.returncode == 0 and "/Modem/" in enumeration.stdout
        return sample

    def snapshot(self, *, allow_lid_ignore=False):
        return {
            "data": checks.findmnt("/mnt/data"),
            "ugreen": self.audit_storage(),
            "network": self.network_sample(),
            "sessions": checks.sessions(),
            "locklock": checks.locklock(allow_lid_ignore=allow_lid_ignore),
            "modem_policy": self.t.policy(),
            "boot": self.r.boot_id(),
            "created": time.time(),
        }

    def native_request_active(self):
        state = p.properties(NATIVE)
        return state.get("Job") not in {"", "0"} or state.get("ActiveState") in {
            "activating",
            "active",
        }

    def production_snapshot(self):
        if not self.native_request_active():
            raise Refusal("production Hibernate requires an active native systemd request")
        before = self.snapshot(allow_lid_ignore=True)
        prepared = dict(before["network"])
        if not prepared.get("healthy") or not prepared.get("connection_uuid"):
            cfg = self.t.config()
            profile = cfg.get("tested_cellular_profile")
            if not profile or profile == "--":
                raise Refusal("production Hibernate has no qualified cellular profile")
            radio = p.run(["/usr/bin/nmcli", "-t", "-f", "WWAN", "radio"], timeout=4).stdout.strip()
            auto = p.run(
                [
                    "/usr/bin/nmcli",
                    "-g",
                    "connection.autoconnect",
                    "connection",
                    "show",
                    profile,
                ],
                timeout=4,
            ).stdout.strip()
            expected = radio == "enabled" and auto == "yes"
            network = dict(prepared)
            network.update(
                healthy=expected,
                connection_uuid=profile,
                basis="WWAN radio enabled and existing profile autoconnect=yes",
                radio=radio,
                autoconnect=auto,
                prepared_snapshot=prepared,
            )
            before["network"] = network
        before["request_mode"] = "native-production"
        return before

    def permit(self):
        p.trusted(PERMIT)
        permit = json.loads(PERMIT.read_text())
        if (
            permit.get("boot_id") != self.r.boot_id()
            or permit.get("expires", 0) < time.time()
            or permit.get("consumed")
        ):
            raise Refusal("stale S4 qualification permit")
        if (
            permit.get("go") != "PASS"
            or permit.get("config_sha256") != hashlib.sha256(checks.CONFIG.read_bytes()).hexdigest()
        ):
            raise Refusal("invalid S4 qualification permit")
        permit["consumed"] = True
        self.persist(PERMIT, permit)
        return permit

    def begin(self):
        if not os.environ.get("INVOCATION_ID"):
            raise Refusal("S4 preparation requires its systemd invocation")
        self.load_current()
        if (
            self.value.get("transaction_type") == s4.TYPE
            and self.value.get("state") not in tx.TERMINAL
        ):
            if self.live_owner(self.value):
                raise Refusal("active S4 transaction owns preparation")
            p.run(["/usr/bin/systemctl", "--no-block", "start", FINISH], timeout=5)
            raise Refusal("prior S4 recovery queued to its sole owner")
        explicit = True
        try:
            permit = self.permit()
        except FileNotFoundError:
            explicit = False
            permit = None
        except Refusal as exc:
            if str(exc) != "stale S4 qualification permit":
                raise
            explicit = False
            permit = None

        if explicit:
            self.before = self.snapshot()
            original = permit.get("before_dispatch")
            if not original or not original.get("sessions"):
                raise Refusal("S4 permit lacks the pre-logind session/network snapshot")
            # logind PrepareForSleep can quiesce NM before V2 begins. Preserve
            # the pre-dispatch identity for an explicitly qualified cycle.
            self.before.update(
                network=original["network"],
                sessions=original["sessions"],
                modem_policy=original["modem_policy"],
            )
            self.before["request_mode"] = "qualified-explicit"
            self.before["permit"] = permit
        else:
            # GUI, UPower and direct logind/systemctl requests arrive here only
            # after logind has started the native Hibernate transaction.
            self.before = self.production_snapshot()
            self.before["permit"] = {
                "source": "native-systemd",
                "consumed": True,
                "boot_id": self.r.boot_id(),
            }

        if self.before["locklock"]["result"] != "PASS":
            raise Refusal("LockLock state does not permit Hibernate")
        if explicit and self.r.lid_state() != "open":
            raise Refusal("explicit Hibernate qualification requires the lid open")
        # Host.begin calls the unchanged accepted workload/clone/process code.
        try:
            super().begin()
            self.phase("BLOCKERS_HANDLED")
            self.update(
                preparation_state="PREPARED",
                blocker_state="HANDLED",
                consumer_state="QUIESCED",
                data_state="MOUNTED_HEALTHY",
            )
        except Exception as exc:
            self.load_current()
            if self.value.get("transaction_type") == s4.TYPE:
                self.phase("PREP_FAILED", reason=str(exc))
            raise

    def verify_release(self):
        super().verify_release()
        self.phase("STORAGE_SAFE")
        self.update(ugreen_state="UNMOUNTED_SAFE", preparation_state="COMPLETE")

    def teardown(self):
        super().teardown()
        if self.value.get("state") == "FAILURE_PENDING":
            if self.value.get("s4_phase") != "PREP_FAILED":
                self.phase("PREP_FAILED", reason=self.value.get("reason"))
            p.run(["/usr/bin/systemctl", "--no-block", "start", FINISH], timeout=5)

    def native_pre(self):
        self.load_current()
        invocation = os.environ.get("INVOCATION_ID")
        if (
            not invocation
            or invocation != p.properties(NATIVE).get("InvocationID")
            or self.value.get("transaction_type") != s4.TYPE
            or self.value.get("state") != "SLEEP_COMMITTED"
            or self.value.get("s4_phase") != "STORAGE_SAFE"
        ):
            raise Refusal("S4 native preparation owner/state mismatch")
        self.update(native_invocation=invocation)
        try:
            self.quiesce_clones()
            result = checks.readiness(
                self,
                self.s4_cfg,
                before_image=True,
                allow_lid_ignore=(
                    self.value.get("s4_before", {}).get("request_mode") == "native-production"
                ),
            )
            self.update(s4_gate=result)
            if result["result"] != "PASS":
                raise Refusal(
                    "final S4 gate failed: "
                    + ",".join(
                        k
                        for k, v in result.items()
                        if (v.get("result") if isinstance(v, dict) else v) != "PASS"
                    )
                )
            cursor = p.run(["/usr/bin/journalctl", "-n", "0", "--show-cursor", "--no-pager"]).stdout
            if "-- cursor: " not in cursor:
                raise Refusal("journal cursor unavailable before image creation")
            self.update(
                s4_cursor=cursor.split("-- cursor: ", 1)[1].strip(),
                state="SUSPEND_REQUESTED",
                storage_state="HIBERNATE_PENDING",
            )
            self.phase("IMAGE_WRITE_REQUESTED", kernel_entry_observed=False)
            p.run(["/usr/bin/journalctl", "--sync"], timeout=10)
        except Exception as exc:
            self.failed("S4_IMAGE_GATE", str(exc))
            self.phase("PREP_FAILED", reason=str(exc))
            raise

    def native_post(self):
        self.load_current()
        invocation = os.environ.get("INVOCATION_ID")
        if (
            self.value.get("transaction_type") != s4.TYPE
            or not invocation
            or invocation != self.value.get("native_invocation")
            or self.value.get("origin_boot_id") != self.r.boot_id()
            or self.value.get("final_outcome")
        ):
            self.emit("STALE_S4_CALLBACK_IGNORED")
            return
        result = os.environ.get("SERVICE_RESULT", "unknown")
        self.update(s4_native_result=result, s4_native_returned=time.time())
        # Native OnSuccess/OnFailure queues the sole finish owner after this
        # process exits and releases the operation lock. Do not also queue it
        # here while native/V2 teardown is still in flight.

    def journal(self):
        cursor = self.value.get("s4_cursor")
        if not cursor:
            return ""
        return p.run(
            [
                "/usr/bin/journalctl",
                "--after-cursor=" + cursor,
                "--no-pager",
                "-o",
                "cat",
                "-b",
                uuid.UUID(self.value["origin_boot_id"]).hex,
            ],
            timeout=20,
        ).stdout

    def confirm_image(self):
        if self.value.get("image_resume_confirmed"):
            return True
        text = self.journal()
        evidence = s4.image_evidence(
            self.value,
            self.r.boot_id(),
            p.properties(NATIVE).get("InvocationID"),
            self.value.get("s4_native_result"),
            text,
            checks.same_sessions(self.value["s4_before"]["sessions"]),
        )
        self.persist(
            p.STATE / "s4-evidence" / (self.value["episode"] + ".json"),
            {"journal": text, "assessment": evidence},
        )
        self.update(s4_image_evidence=evidence)
        if evidence["confirmed"]:
            for phase in (
                "HIBERNATE_ENTRY",
                "POWERED_OFF",
                "BOOT_RESUME_DETECTED",
                "IMAGE_RESUME_CONFIRMED",
            ):
                self.phase(phase, retrospectively_confirmed=True)
            self.update(image_resume_confirmed=True, kernel_cycle=True, actual_sleep=True)
            return True
        return False

    def recover_modem(self, before, generation):
        def unchanged():
            current = checks.modem_generation(self.s4_cfg)
            if not current["hardware_ok"] or current["generation"] != generation:
                raise Refusal("modem generation changed before recovery action")

        unchanged()
        current = self.network_sample()
        if not current["modem_present"]:
            p.run(["/usr/bin/systemctl", "restart", "ModemManager.service"], timeout=40)
            # systemctl returning only proves the daemon started. MM still
            # needs to probe MBIM/AT and export the modem object before NM can
            # activate its saved profile. No second restart on slow probing.
            deadline = time.monotonic() + 45
            while True:
                unchanged()
                current = self.network_sample()
                if current.get("modem_present"):
                    break
                if time.monotonic() >= deadline:
                    raise Refusal("ModemManager enumeration timed out after the sole restart")
                time.sleep(1)
        # Never turn a previously disabled radio on or choose a profile by name.
        if before.get("healthy") and before.get("connection_uuid"):
            unchanged()
            current = self.network_sample()
            if not current.get("healthy"):
                p.run(
                    [
                        "/usr/bin/nmcli",
                        "--wait",
                        "55",
                        "connection",
                        "up",
                        "uuid",
                        before["connection_uuid"],
                    ],
                    timeout=60,
                )

    def restore_network(self, image, cold):
        invocation = os.environ.get("INVOCATION_ID")
        if not invocation or invocation != p.properties(FINISH).get("InvocationID"):
            raise Refusal("WWAN recovery may run only in the selected finish service")
        receipt = self.value.setdefault("s4_wwan_receipt", {})
        receipt.update(owner=FINISH, invocation=invocation, boot_id=self.r.boot_id())
        before = self.value["s4_before"]["network"]
        if receipt.get("status") == "RESTORED":
            current = self.network_sample()
            if current.get("hardware_ok") and (
                not before.get("healthy")
                or (
                    current.get("healthy")
                    and current.get("connection_uuid") == before.get("connection_uuid")
                )
            ):
                return
        s4.recover_wwan(
            before,
            self.network_sample,
            self.recover_modem,
            lambda: self.save(),
            receipt,
            settle=110 if image else 0,
            timeout=260 if image else 100,
        )
        self.update(
            wwan_state="RESTORED", cold_boot_network_policy_replayed=False if cold else None
        )

    def restore_storage(self, cold=False):
        healthy, detail = self.r.internal_data_healthy()
        if not healthy:
            raise Refusal("S4 DATA restoration refused: " + detail)
        before = self.value["s4_before"]["data"]
        after = checks.findmnt("/mnt/data")
        if any(before.get(key) != after.get(key) for key in ("target", "uuid", "fstype")):
            raise Refusal("DATA identity changed across S4")
        # Accepted V2 intentionally leaves removable filesystems unmounted.
        # Identity/re-enumeration and harmless automount cleanup are performed
        # by the accepted auditor; no mount/device-letter guessing is added.
        self.update(resume_verify_started_monotonic=time.monotonic())
        terminal = self.r.terminal_ugreen_reaudit(self.value)
        if not terminal.get("ok"):
            raise Refusal(
                "S4 external storage restore audit failed: " + str(terminal.get("reason"))
            )
        self.update(
            data_state="MOUNTED_HEALTHY",
            ugreen_state="SAFE_RELEASED_BY_POLICY",
            storage_state="HEALTHY",
            terminal_ugreen_reaudit=terminal,
            s4_storage_restore={
                "data_changed": False,
                "ugreen_policy": "NO_FORCED_REMOUNT",
                "identity_audit": terminal,
                "cold_boot": cold,
            },
        )

    def restore_consumers(self):
        receipts = self.value.get("stopped_workloads", [])
        for receipt in receipts:
            if receipt.get("restart_after_resume") != "if_was_running":
                continue
            row = next((x for x in self.cfg["workloads"] if x["name"] == receipt["name"]), None)
            if row and row.get("storage_dependency") == "UGREEN":
                raise Refusal(
                    "required external consumer needs an explicit safe mount restore contract"
                )
        s4.restore_consumers(
            self.cfg["workloads"], receipts, self.workload_run, lambda: self.save()
        )
        self.update(consumer_state="RESTORED_BY_SAVED_POLICY")

    def complete(self, outcome):
        if outcome == "COMPLETE":
            self.phase("COMPLETE")
        self.update(final_outcome=outcome, retry_state="COMPLETE", reason=None)
        # Preserve the exact S4 outcome in the common terminal archive; use the
        # existing runtime terminal vocabulary so Suspend can immediately start.
        with self.r.state_lock():
            if self.r.load_state().get("episode") != self.value.get("episode"):
                raise Refusal("S4 terminal owner changed")
            self.r.validate_automount_marker_for_release(self.value)
            completed = {
                **self.value,
                "state": "COMPLETE" if outcome == "COMPLETE" else "RECOVERED",
                "completed_at": time.time(),
            }
            self.persist(p.STATE / "transactions" / (self.value["episode"] + ".json"), completed)
            self.r.clear_to_idle(self.value, completed["state"])
            self.persist(p.STATE / "current.json", self.r.load_state())
        self.emit("S4_TERMINAL", outcome=outcome)
        if outcome == "COMPLETE" and self.value.get("image_resume_confirmed"):
            observer.success(
                self.cfg,
                "hibernate",
                self.value.get("episode"),
                started_at=self.value.get("resume_observer_started_at"),
                terminal=outcome,
            )

    def finish(self, cold=False):
        if not cold:
            self.load_current()
        if self.value.get("transaction_type") != s4.TYPE or self.value.get("state") in tx.TERMINAL:
            return
        cold = cold or self.value.get("origin_boot_id") != self.r.boot_id()
        if not cold:
            native = p.properties(NATIVE)
            if native.get("ActiveState") in {"active", "activating", "deactivating"}:
                raise Refusal("cannot restore during native Hibernate execution")
        self.update(finish_invocation=os.environ.get("INVOCATION_ID"))
        outcome = self.value.get("s4_failure_outcome")
        try:
            if cold:
                outcome = s4.reboot_outcome(self.value)
            elif not outcome:
                if self.confirm_image():
                    outcome = "COMPLETE"
                    marker = observer.start(
                        self.cfg,
                        "hibernate",
                        self.value.get("episode"),
                        request_mode=self.value.get("s4_before", {}).get("request_mode"),
                    )
                    changes = {
                        "resume_observer_started_at": marker.get("started_at"),
                        "resume_log_path": marker.get("log_path"),
                    }
                    self.update(**{k: v for k, v in changes.items() if v is not None})
                elif self.value.get("s4_phase") == "PREP_FAILED":
                    outcome = "PREP_FAILED"
                elif self.value.get("s4_phase") == "IMAGE_WRITE_REQUESTED":
                    outcome = "IMAGE_WRITE_FAILED"
                else:
                    outcome = "ABORTED"
            if outcome != "COMPLETE":
                self.phase(
                    outcome, image_resume_confirmed=self.value.get("image_resume_confirmed", False)
                )
                self.update(s4_failure_outcome=outcome)
            self.phase("RESTORING", target_outcome=outcome)
            log_resume = bool(outcome == "COMPLETE" and not cold)
            if log_resume:
                observer.stage("hibernate", self.value.get("episode"), "STORAGE_RESTORE", "START")
            self.restore_storage(cold)
            if log_resume:
                observer.stage("hibernate", self.value.get("episode"), "STORAGE_RESTORE", "PASS")
            # USB Clone is prepared by the shared Host path and carries its own
            # durable was_running/if_was_running receipt. Restore it only after
            # DATA/storage identity is healthy, for both image resume and abort.
            if log_resume:
                observer.stage("hibernate", self.value.get("episode"), "USBCLONE_RESTORE", "START")
            self.restore_clones()
            if log_resume:
                observer.stage("hibernate", self.value.get("episode"), "USBCLONE_RESTORE", "PASS")
                observer.stage("hibernate", self.value.get("episode"), "WWAN_RESTORE", "START")
            self.restore_network(outcome == "COMPLETE", cold)
            if log_resume:
                observer.stage("hibernate", self.value.get("episode"), "WWAN_RESTORE", "PASS")
                observer.stage("hibernate", self.value.get("episode"), "CONSUMERS_RESTORE", "START")
            self.restore_consumers()
            if log_resume:
                observer.stage("hibernate", self.value.get("episode"), "CONSUMERS_RESTORE", "PASS")
            if not cold and self.t.policy() != self.value["s4_before"]["modem_policy"]:
                raise Refusal("S4 modem power policy unexpectedly changed")
            # /run and kernel inhibitor FDs are fresh on cold boot. Desktop
            # login may not exist yet; it must not block safe boot reconciliation.
            if not cold and checks.locklock()["result"] != "PASS":
                raise Refusal("LockLock did not return to safe OFF state")
            self.complete(outcome)
        except Exception as exc:
            self.failed("S4_RESTORE", str(exc))
            self.phase("RESTORE_FAILED", reason=str(exc), target_outcome=outcome)
            raise

    def reconcile(self):
        if self.value.get("transaction_type") == s4.TYPE:
            p.run(["/usr/bin/systemctl", "--no-block", "start", FINISH], timeout=5)
            raise Refusal("S4 reconciliation delegated to its sole finish owner")
        else:
            super().reconcile()

    def boot(self):
        old = json.loads((p.STATE / "current.json").read_text())
        if old.get("transaction_type") != s4.TYPE or old.get("state") in tx.TERMINAL:
            return super().boot()
        if old.get("origin_boot_id") == self.r.boot_id():
            return  # Same boot can be an image resume; never call it cold boot.
        # Preserve the previous boot's complete ledger before adapting /run
        # identity. In particular, never erase a successful image return just
        # because the user rebooted later to recover a peripheral.
        self.persist(
            p.STATE
            / "s4-evidence"
            / (old["episode"] + "-before-boot-" + self.r.boot_id() + ".json"),
            old,
        )
        with self.r.state_lock():
            if self.r.load_state().get("state") != "IDLE":
                raise Refusal("cold boot will not overwrite another active transaction")
            self.value = old
            self.value.update(
                boot_id=self.r.boot_id(),
                reconciliation_boot_id=self.r.boot_id(),
                reconciliation_reason=s4.reboot_outcome(old),
            )
            for key in tuple(self.value):
                if key.startswith("automount_suppression"):
                    del self.value[key]
            self.save()
            self.r.arm_automount_suppression(self.value)
            self.save()
        p.run(["/usr/bin/systemctl", "--no-block", "start", FINISH], timeout=5)
