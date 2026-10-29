import os
import threading
import unittest

from _helpers import TempEnv, db

import metadata


class MetadataTests(TempEnv, unittest.TestCase):
    def test_upload_creates_row_and_transfer(self):
        metadata.record_upload("a.txt", "alice", 2_000_000, 2.0, "10.0.0.1")
        row = metadata.get_file("a.txt")
        self.assertEqual((row["owner"], row["size"]), ("alice", 2_000_000))
        (t,) = metadata.all_transfers()
        self.assertEqual(t["direction"], "UPLOAD")
        self.assertAlmostEqual(t["mbps"], 8.0)   # 2 MB in 2 s = 8 Mbps

    def test_download_increments_counter(self):
        metadata.record_upload("a.txt", "alice", 10, 1, "10.0.0.1")
        metadata.record_download("a.txt", "bob", 10, 1, "10.0.0.2")
        metadata.record_download("a.txt", "bob", 10, 1, "10.0.0.2")
        self.assertEqual(metadata.get_file("a.txt")["download_count"], 2)

    def test_reupload_replaces_row(self):
        metadata.record_upload("a.txt", "alice", 10, 1)
        metadata.record_upload("a.txt", "alice", 99, 1)
        self.assertEqual(metadata.get_file("a.txt")["size"], 99)
        self.assertEqual(len(metadata.list_files()), 1)

    def test_list_merges_disk_and_database(self):
        d = os.path.join(self.tmp.name, "files")
        os.makedirs(d)
        for n in ("a.txt", "manual.txt", "x.txt.5.part"):
            open(os.path.join(d, n), "w").write("hello")
        metadata.record_upload("a.txt", "alice", 5, 1)
        metadata.record_upload("deleted.txt", "bob", 5, 1)   # gone from disk
        rows = {r["filename"]: r for r in metadata.list_files(d)}
        self.assertEqual(set(rows), {"a.txt", "manual.txt"})
        self.assertEqual(rows["manual.txt"]["owner"], "unknown")
        self.assertTrue(metadata.format_row(rows["a.txt"]).startswith("a.txt | owner=alice"))

    def test_concurrent_writes_do_not_lose_records(self):
        def work(i):
            for j in range(10):
                metadata.record_upload(f"f{i}_{j}", "alice", 1, 1)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(metadata.list_files()), 80)
        self.assertEqual(len(metadata.all_transfers()), 80)


if __name__ == "__main__":
    unittest.main()
