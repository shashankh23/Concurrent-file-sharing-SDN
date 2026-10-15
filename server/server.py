"""Concurrent file server (TCP).

Run:  python server.py [port]
Protocol (text commands end with newline):
  LOGIN user pass        -> AUTH_OK / AUTH_FAIL
  LIST                   -> FILE_LIST n, then n filenames
  UPLOAD name size       -> READY, (client sends bytes), UPLOAD_OK
  DOWNLOAD name          -> FILE_INFO size, (server sends bytes) / ERROR ...
  QUIT                   -> BYE
Filenames must not contain spaces.
"""
import os
import socket
import sys
import threading
import time

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(BASE_DIR, "files")
CHUNK = 4096

# Placeholder users. Person 3 will replace this with the database.
USERS = {"alice": "1234", "bob": "abcd"}


def send_line(conn, text):
    conn.sendall((text + "\n").encode())


def handle_client(conn, addr):
    print(f"[CONNECT] {addr}")
    rfile = conn.makefile("rb")
    logged_in = False
    user = None
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
                if len(parts) == 3 and USERS.get(parts[1]) == parts[2]:
                    logged_in = True
                    user = parts[1]
                    send_line(conn, "AUTH_OK")
                    print(f"[LOGIN] {user} from {addr}")
                else:
                    send_line(conn, "AUTH_FAIL")

            elif cmd == "QUIT":
                send_line(conn, "BYE")
                break

            elif not logged_in:
                send_line(conn, "ERROR Please login first")

            elif cmd == "LIST":
                names = sorted(os.listdir(FILES_DIR))
                send_line(conn, f"FILE_LIST {len(names)}")
                for n in names:
                    send_line(conn, n)

            elif cmd == "UPLOAD":
                if len(parts) != 3 or not parts[2].isdigit():
                    send_line(conn, "ERROR Usage: UPLOAD name size")
                    continue
                name = os.path.basename(parts[1])
                size = int(parts[2])
                path = os.path.join(FILES_DIR, name)
                send_line(conn, "READY")
                start = time.time()
                remaining = size
                with open(path, "wb") as f:
                    while remaining > 0:
                        data = rfile.read(min(CHUNK, remaining))
                        if not data:
                            break
                        f.write(data)
                        remaining -= len(data)
                if remaining > 0:
                    os.remove(path)
                    print(f"[ERROR] Upload of {name} from {addr} was cut off")
                    break
                elapsed = max(time.time() - start, 1e-6)
                send_line(conn, "UPLOAD_OK")
                print(f"[UPLOAD] {user} {name} {size} bytes in {elapsed:.2f}s "
                      f"({size / elapsed / 1e6:.2f} MB/s)")

            elif cmd == "DOWNLOAD":
                if len(parts) != 2:
                    send_line(conn, "ERROR Usage: DOWNLOAD name")
                    continue
                name = os.path.basename(parts[1])
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
                print(f"[DOWNLOAD] {user} {name} {size} bytes in {elapsed:.2f}s "
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
