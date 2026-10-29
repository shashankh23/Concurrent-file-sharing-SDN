"""File metadata and transfer records (Person 3).

files      one row per stored file: owner, size, upload time, download count
transfers  one row per upload/download with duration and throughput; Person 4
           can export this table to CSV for the performance evaluation
           (python database/admin.py export-transfers results/transfers.csv)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database import database as db  # noqa: E402


def get_file(filename):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM files WHERE filename = ?", (filename,)
        ).fetchone()


def get_owner(filename):
    row = get_file(filename)
    return row["owner"] if row else None


def _mbps(size, seconds):
    return size * 8 / max(seconds, 1e-6) / 1e6


def record_upload(filename, owner, size, seconds, ip=None):
    """Store/refresh the file row and add a transfer record."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO files (filename, owner, size, uploaded_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(filename) DO UPDATE SET owner = excluded.owner, "
            "size = excluded.size, uploaded_at = excluded.uploaded_at, "
            "download_count = 0",
            (filename, owner, size, db.now()),
        )
        conn.execute(
            "INSERT INTO transfers (ts, username, ip, direction, filename, "
            "size, seconds, mbps) VALUES (?,?,?,?,?,?,?,?)",
            (db.now(), owner, ip, "UPLOAD", filename, size, seconds,
             _mbps(size, seconds)),
        )
    db.log_event("UPLOAD", owner, ip, f"{filename} {size} bytes")


def record_download(filename, username, size, seconds, ip=None):
    with db.connect() as conn:
        conn.execute(
            "UPDATE files SET download_count = download_count + 1 "
            "WHERE filename = ?", (filename,),
        )
        conn.execute(
            "INSERT INTO transfers (ts, username, ip, direction, filename, "
            "size, seconds, mbps) VALUES (?,?,?,?,?,?,?,?)",
            (db.now(), username, ip, "DOWNLOAD", filename, size, seconds,
             _mbps(size, seconds)),
        )
    db.log_event("DOWNLOAD", username, ip, f"{filename} {size} bytes")


def list_files(files_dir=None):
    """Metadata rows for the LIST command.

    If files_dir is given, files that exist on disk but are missing from the
    database (copied in by hand) are listed with owner 'unknown', and rows
    whose file was deleted from disk are left out.
    """
    with db.connect() as conn:
        rows = {r["filename"]: dict(r) for r in
                conn.execute("SELECT * FROM files").fetchall()}
    if files_dir is not None:
        on_disk = {n for n in os.listdir(files_dir) if not n.endswith('.part')}
        rows = {k: v for k, v in rows.items() if k in on_disk}
        for name in on_disk - set(rows):
            path = os.path.join(files_dir, name)
            if os.path.isfile(path):
                rows[name] = {
                    "filename": name, "owner": "unknown",
                    "size": os.path.getsize(path), "uploaded_at": "-",
                    "download_count": 0,
                }
    return [rows[k] for k in sorted(rows)]


def format_row(row):
    """One LIST line: name first, then owner, size in bytes, upload time."""
    return (f"{row['filename']} | owner={row['owner']} | "
            f"{row['size']} bytes | {row['uploaded_at']}")


def user_summary():
    """Per-user totals: uploads, downloads, bytes moved, average Mbps."""
    with db.connect() as conn:
        return conn.execute(
            "SELECT username, direction, COUNT(*) AS transfers, "
            "SUM(size) AS bytes, AVG(mbps) AS avg_mbps "
            "FROM transfers GROUP BY username, direction "
            "ORDER BY username, direction"
        ).fetchall()


def all_transfers():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM transfers ORDER BY id").fetchall()
