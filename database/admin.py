"""Admin tool for the file sharing database (Person 3).

Run from the project root:
    python database/admin.py init
    python database/admin.py users
    python database/admin.py add-user carol pass123 [--no-upload] [--no-download] [--premium] [--admin]
    python database/admin.py set-password carol newpass
    python database/admin.py files
    python database/admin.py log [-n 30]
    python database/admin.py summary
    python database/admin.py export-transfers results/transfers.csv
    python database/admin.py block 10.0.0.3       # tell the SDN controller
    python database/admin.py unblock 10.0.0.3
"""
import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))

from database import database as db  # noqa: E402
import metadata  # noqa: E402
import sdn_bridge  # noqa: E402


def show(rows, columns):
    rows = [[str(r[c]) for c in columns] for r in rows]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
              for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("users")
    a = sub.add_parser("add-user")
    a.add_argument("username")
    a.add_argument("password")
    a.add_argument("--no-upload", action="store_true")
    a.add_argument("--no-download", action="store_true")
    a.add_argument("--premium", action="store_true")
    a.add_argument("--admin", action="store_true")
    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    sp.add_argument("password")
    sub.add_parser("files")
    lg = sub.add_parser("log")
    lg.add_argument("-n", type=int, default=20)
    sub.add_parser("summary")
    ex = sub.add_parser("export-transfers")
    ex.add_argument("path")
    for name in ("block", "unblock"):
        b = sub.add_parser(name)
        b.add_argument("ip")
    args = p.parse_args()

    db.init_db()
    if args.cmd == "init":
        print(f"Database ready: {db.db_path()}")
    elif args.cmd == "users":
        show(db.list_users(), ["username", "can_upload", "can_download",
                               "is_admin", "is_premium", "created_at"])
    elif args.cmd == "add-user":
        try:
            db.create_user(args.username, args.password,
                           not args.no_upload, not args.no_download,
                           args.admin, args.premium)
            print(f"Created user {args.username}")
        except ValueError as e:
            sys.exit(f"Error: {e}")
    elif args.cmd == "set-password":
        if not db.set_password(args.username, args.password):
            sys.exit("No such user")
        print("Password updated")
    elif args.cmd == "files":
        show(metadata.list_files(), ["filename", "owner", "size",
                                     "uploaded_at", "download_count"])
    elif args.cmd == "log":
        show(reversed(db.recent_events(args.n)),
             ["ts", "username", "ip", "event", "detail"])
    elif args.cmd == "summary":
        show(metadata.user_summary(),
             ["username", "direction", "transfers", "bytes", "avg_mbps"])
    elif args.cmd == "export-transfers":
        rows = metadata.all_transfers()
        os.makedirs(os.path.dirname(os.path.abspath(args.path)), exist_ok=True)
        with open(args.path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "username", "ip", "direction", "filename",
                        "size_bytes", "seconds", "mbps"])
            for r in rows:
                w.writerow([r["ts"], r["username"], r["ip"], r["direction"],
                            r["filename"], r["size"], f"{r['seconds']:.4f}",
                            f"{r['mbps']:.3f}"])
        print(f"Wrote {len(rows)} transfers to {args.path}")
    elif args.cmd in ("block", "unblock"):
        ok = (sdn_bridge.block_ip if args.cmd == "block"
              else sdn_bridge.unblock_ip)(args.ip)
        print("Command sent to controller" if ok
              else "Not sent (invalid or loopback IP, or file not writable)")
        db.log_event("ADMIN_" + args.cmd.upper(), ip=args.ip, detail=f"sent={ok}")


if __name__ == "__main__":
    main()
