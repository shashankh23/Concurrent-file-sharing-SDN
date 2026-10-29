"""Shared set-up for the Person 3 tests (temporary database per test)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "server")):
    if p not in sys.path:
        sys.path.insert(0, p)

from database import database as db  # noqa: E402
import authentication  # noqa: E402
import sdn_bridge  # noqa: E402


class TempEnv:
    """Mixin: fresh database, policy file and auth state for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_db = os.environ.get("FILESHARE_DB")
        os.environ["FILESHARE_DB"] = os.path.join(self.tmp.name, "test.db")
        self.policy_file = os.path.join(self.tmp.name, "policy.txt")
        self._old = (sdn_bridge.POLICY_FILE, authentication.MAX_FAILED,
                     authentication.LOCKOUT_SECONDS)
        sdn_bridge.POLICY_FILE = self.policy_file
        authentication.MAX_FAILED = 3
        authentication.LOCKOUT_SECONDS = 60
        authentication.reset_state()
        db.init_db()

    def tearDown(self):
        sdn_bridge.POLICY_FILE, authentication.MAX_FAILED, \
            authentication.LOCKOUT_SECONDS = self._old
        authentication.reset_state()
        if self._old_db is None:
            os.environ.pop("FILESHARE_DB", None)
        else:
            os.environ["FILESHARE_DB"] = self._old_db
        self.tmp.cleanup()

    def policy_lines(self):
        if not os.path.exists(self.policy_file):
            return []
        with open(self.policy_file) as f:
            return f.read().split("\n")[:-1]
