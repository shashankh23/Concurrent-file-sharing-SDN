"""End-to-end test: starts the real server and talks to it over TCP."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Conn:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.r = self.s.makefile("rb")

    def send(self, text):
        self.s.sendall((text + "\n").encode())

    def line(self):
        return self.r.readline().decode().strip()

    def login(self, u, p):
        self.send(f"LOGIN {u} {p}")
        return self.line()

    def upload(self, name, data):
        self.send(f"UPLOAD {name} {len(data)}")
        first = self.line()
        if first != "READY":
            return first
        self.s.sendall(data)
        return self.line()

    def download(self, name):
        self.send(f"DOWNLOAD {name}")
        resp = self.line()
        if not resp.startswith("FILE_INFO"):
            return resp
        return self.r.read(int(resp.split()[1]))

    def close(self):
        self.s.close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        s = socket.socket(); s.bind(("127.0.0.1", 0)); cls.port = s.getsockname()[1]; s.close()
        env = dict(os.environ,
                   FILESHARE_DB=os.path.join(cls.tmp.name, "t.db"),
                   FILESHARE_FILES_DIR=os.path.join(cls.tmp.name, "files"),
                   SDN_POLICY_FILE=os.path.join(cls.tmp.name, "policy.txt"),
                   FILESHARE_MAX_FAILED="3", FILESHARE_LOCKOUT_SECONDS="60")
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server", "server.py"), str(cls.port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(); cls.tmp.cleanup()

    def test_1_upload_list_download_roundtrip(self):
        c = Conn(self.port)
        self.assertEqual(c.login("alice", "1234"), "AUTH_OK")
        data = os.urandom(300_000)
        self.assertEqual(c.upload("rt.bin", data), "UPLOAD_OK")
        c.send("LIST")
        n = int(c.line().split()[1])
        lines = [c.line() for _ in range(n)]
        self.assertTrue(any(l.startswith("rt.bin | owner=alice") for l in lines))
        self.assertEqual(c.download("rt.bin"), data)
        c.close()

    def test_2_permissions(self):
        g = Conn(self.port)
        self.assertEqual(g.login("guest", "guest"), "AUTH_OK")
        self.assertIn("Permission denied", g.upload("g.txt", b"x"))
        g.close()
        b = Conn(self.port)
        b.login("bob", "abcd")
        self.assertIn("owned by alice", b.upload("rt.bin", b"overwrite"))
        b.close()

    def test_3_commands_need_login(self):
        c = Conn(self.port)
        c.send("LIST")
        self.assertIn("login first", c.line())
        c.close()

    def test_4_concurrent_clients(self):
        import threading
        results = {}
        def work(user, pw, name):
            c = Conn(self.port)
            c.login(user, pw)
            data = os.urandom(200_000)
            ok = c.upload(name, data) == "UPLOAD_OK" and c.download(name) == data
            results[name] = ok
            c.close()
        ts = [threading.Thread(target=work, args=a) for a in
              [("alice", "1234", "c1.bin"), ("bob", "abcd", "c2.bin"), ("admin", "admin123", "c3.bin")]]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results, {"c1.bin": True, "c2.bin": True, "c3.bin": True})

    def test_5_lockout_after_failed_logins(self):
        for _ in range(3):
            c = Conn(self.port)
            self.assertEqual(c.login("alice", "wrong"), "AUTH_FAIL")
            c.close()
        c = Conn(self.port)
        self.assertTrue(c.login("alice", "1234").startswith("AUTH_FAIL"))
        c.close()


if __name__ == "__main__":
    unittest.main()
