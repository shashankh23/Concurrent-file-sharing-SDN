"""Concurrent file server (TCP).

Run:  python server.py [port]
Protocol (text commands end with newline):
  LOGIN user pass        -> AUTH_OK / AUTH_FAIL (checked against the database)
  LIST                   -> FILE_LIST n, then n lines "name | owner | size | time"
  UPLOAD name size       -> READY, (client sends bytes), UPLOAD_OK
                            (ERROR Permission denied if the user may not upload)
  DOWNLOAD name          -> FILE_INFO size, (server sends bytes) / ERROR ...
  QUIT                   -> BYE
Filenames must not contain spaces.
"""
import os
import socket
import sys
import threading
import time

import authentication
import metadata
from database import database as db

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.environ.get("FILESHARE_FILES_DIR", os.path.join(BASE_DIR, "files"))
CHUNK = 4096


def send_line(conn, text):
    conn.sendall((text + "\n").encode())


def handle_client(conn, addr):
    print(f"[CONNECT] {addr}")
    rfile = conn.makefile("rb")
    ip = addr[0]
    logged_in = False
    user = None          # database row of the logged-in user
    try:
        while True:
            line = rfile.readline()
            if not line:
                break
            parts = line.decode().strip().split()
            if not parts:
                continue
            cmd = parts[0].upper()

            if cmd == "LOGIN":
                if len(parts) != 3:
                    send_line(conn, "AUTH_FAIL")
                    continue
                result = authentication.authenticate(parts[1], parts[2], ip)
                if result.ok:
                    logged_in = True
                    user = result.user
                    send_line(conn, "AUTH_OK")
                    print(f"[LOGIN] {parts[1]} from {addr}")
                elif result.reason == "locked":
                    send_line(conn, "AUTH_FAIL Too many failed attempts, try later")
                    print(f"[LOGIN] refused (locked out) {ip}")
                else:
                    send_line(conn, "AUTH_FAIL")
                    print(f"[LOGIN] failed for '{parts[1]}' from {addr}")

            elif cmd == "QUIT":
                send_line(conn, "BYE")
                break

            elif not logged_in:
                send_line(conn, "ERROR Please login first")

            elif cmd == "LIST":
                rows = metadata.list_files(FILES_DIR)
                send_line(conn, f"FILE_LIST {len(rows)}")
                for row in rows:
                    send_line(conn, metadata.format_row(row))

            elif cmd == "UPLOAD":
                if len(parts) != 3 or not parts[2].isdigit():
                    send_line(conn, "ERROR Usage: UPLOAD name size")
                    continue
                name = os.path.basename(parts[1])
                size = int(parts[2])
                username = user["username"]
                if not authentication.check_permission(user, "upload"):
                    db.log_event("DENIED", username, ip, f"upload {name}")
                    send_line(conn, "ERROR Permission denied: upload")
                    continue
                owner = metadata.get_owner(name)
                if not authentication.can_overwrite(user, owner):
                    db.log_event("DENIED", username, ip,
                                 f"overwrite {name} owned by {owner}")
                    send_line(conn, f"ERROR File is owned by {owner}")
                    continue
                path = os.path.join(FILES_DIR, name)
                # Write to a temporary file so a cut-off upload never destroys
                # an existing file; rename only when complete.
                tmp_path = f"{path}.{threading.get_ident()}.part"
                send_line(conn, "READY")
                start = time.time()
                remaining = size
                with open(tmp_path, "wb") as f:
                    while remaining > 0:
                        data = rfile.read(min(CHUNK, remaining))
                        if not data:
                            break
                        f.write(data)
                        remaining -= len(data)
                if remaining > 0:
                    os.remove(tmp_path)
                    print(f"[ERROR] Upload of {name} from {addr} was cut off")
                    break
                os.replace(tmp_path, path)
                elapsed = max(time.time() - start, 1e-6)
                metadata.record_upload(name, username, size, elapsed, ip)
                send_line(conn, "UPLOAD_OK")
                print(f"[UPLOAD] {username} {name} {size} bytes in {elapsed:.2f}s "
                      f"({size / elapsed / 1e6:.2f} MB/s)")

            elif cmd == "DOWNLOAD":
                if len(parts) != 2:
                    send_line(conn, "ERROR Usage: DOWNLOAD name")
                    continue
                name = os.path.basename(parts[1])
                username = user["username"]
                if not authentication.check_permission(user, "download"):
                    db.log_event("DENIED", username, ip, f"download {name}")
                    send_line(conn, "ERROR Permission denied: download")
                    continue
                path = os.path.join(FILES_DIR, name)
                if not os.path.isfile(path):
                    send_line(conn, "ERROR File not found")
                    continue
                size = os.path.getsize(path)
                send_line(conn, f"FILE_INFO {size}")
                start = time.time()
                with open(path, "rb") as f:
                    while True:
                        data = f.read(CHUNK)
                        if not data:
                            break
                        conn.sendall(data)
                elapsed = max(time.time() - start, 1e-6)
                metadata.record_download(name, username, size, elapsed, ip)
                print(f"[DOWNLOAD] {username} {name} {size} bytes in {elapsed:.2f}s "
                      f"({size / elapsed / 1e6:.2f} MB/s)")

            else:
                send_line(conn, "ERROR Unknown command")
    except (ConnectionError, OSError) as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        rfile.close()
        conn.close()
        print(f"[DISCONNECT] {addr}")


def main():
    os.makedirs(FILES_DIR, exist_ok=True)
    db.init_db()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Server listening on port {PORT}. Press Ctrl+C to stop.")
    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
