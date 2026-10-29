import sqlite3
import time
import unittest

from _helpers import TempEnv, authentication, db


class PasswordTests(TempEnv, unittest.TestCase):
    def test_passwords_are_hashed_and_salted(self):
        with db.connect() as c:
            rows = c.execute("SELECT username, password_hash FROM users").fetchall()
        hashes = [r["password_hash"] for r in rows]
        self.assertTrue(all(h.startswith("pbkdf2$") for h in hashes))
        self.assertNotIn("1234", " ".join(hashes))
        self.assertEqual(len(set(hashes)), len(hashes))

    def test_same_password_gets_different_hash(self):
        self.assertNotEqual(db.hash_password("x"), db.hash_password("x"))

    def test_verify_rejects_garbage_hash(self):
        self.assertFalse(db.verify_password("x", "not-a-hash"))


class LoginTests(TempEnv, unittest.TestCase):
    def test_good_login(self):
        r = authentication.authenticate("alice", "1234", "10.0.0.1")
        self.assertTrue(r.ok)
        self.assertEqual(r.user["username"], "alice")

    def test_wrong_password_and_unknown_user_fail(self):
        self.assertFalse(authentication.authenticate("alice", "nope", "10.0.0.1").ok)
        self.assertFalse(authentication.authenticate("ghost", "1234", "10.0.0.1").ok)

    def test_events_are_logged(self):
        authentication.authenticate("alice", "1234", "10.0.0.1")
        authentication.authenticate("alice", "bad", "10.0.0.1")
        events = [e["event"] for e in db.recent_events()]
        self.assertIn("LOGIN_OK", events)
        self.assertIn("LOGIN_FAIL", events)


class PermissionTests(TempEnv, unittest.TestCase):
    def test_guest_can_download_but_not_upload(self):
        guest = db.get_user("guest")
        self.assertTrue(authentication.check_permission(guest, "download"))
        self.assertFalse(authentication.check_permission(guest, "upload"))

    def test_admin_can_do_everything(self):
        admin = db.get_user("admin")
        db.set_permissions("admin", can_upload=False, can_download=False)
        admin = db.get_user("admin")
        self.assertTrue(authentication.check_permission(admin, "upload"))

    def test_overwrite_rules(self):
        alice, bob, admin = (db.get_user(n) for n in ("alice", "bob", "admin"))
        self.assertTrue(authentication.can_overwrite(alice, None))
        self.assertTrue(authentication.can_overwrite(alice, "alice"))
        self.assertFalse(authentication.can_overwrite(bob, "alice"))
        self.assertTrue(authentication.can_overwrite(admin, "alice"))

    def test_duplicate_and_invalid_users_rejected(self):
        with self.assertRaises(ValueError):
            db.create_user("alice", "x")
        with self.assertRaises(ValueError):
            db.create_user("bad name", "x")


class LockoutTests(TempEnv, unittest.TestCase):
    IP = "10.0.0.3"

    def fail(self, n, ip=None):
        for _ in range(n):
            authentication.authenticate("alice", "wrong", ip or self.IP)

    def test_lockout_blocks_ip_via_sdn_bridge(self):
        self.fail(3)
        self.assertTrue(authentication.is_locked(self.IP))
        self.assertEqual(self.policy_lines(), [f"BLOCK {self.IP}"])
        # even the right password is refused while locked
        r = authentication.authenticate("alice", "1234", self.IP)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "locked")

    def test_two_failures_do_not_lock(self):
        self.fail(2)
        self.assertFalse(authentication.is_locked(self.IP))
        self.assertEqual(self.policy_lines(), [])

    def test_success_resets_failure_count(self):
        self.fail(2)
        self.assertTrue(authentication.authenticate("alice", "1234", self.IP).ok)
        self.fail(2)
        self.assertFalse(authentication.is_locked(self.IP))

    def test_lockout_is_per_ip(self):
        self.fail(3)
        self.assertTrue(authentication.authenticate("alice", "1234", "10.0.0.1").ok)

    def test_lockout_expires_and_controller_is_told_to_unblock(self):
        authentication.LOCKOUT_SECONDS = 0.3
        self.fail(3)
        self.assertTrue(authentication.is_locked(self.IP))
        time.sleep(0.8)
        self.assertFalse(authentication.is_locked(self.IP))
        self.assertEqual(self.policy_lines(),
                         [f"BLOCK {self.IP}", f"UNBLOCK {self.IP}"])
        self.assertTrue(authentication.authenticate("alice", "1234", self.IP).ok)

    def test_loopback_is_never_sent_to_controller(self):
        self.fail(3, ip="127.0.0.1")
        self.assertTrue(authentication.is_locked("127.0.0.1"))
        self.assertEqual(self.policy_lines(), [])


if __name__ == "__main__":
    unittest.main()
