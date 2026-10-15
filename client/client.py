"""File sharing client (TCP).

Run:  python client.py [server_ip] [port]
Defaults: 127.0.0.1 and 5000
Commands inside the client: list | upload <path> | download <name> | quit
"""
import os
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
CHUNK = 4096


def send_line(sock, text):
    sock.sendall((text + "\n").encode())


def read_line(rfile):
    return rfile.readline().decode().strip()


def do_list(sock, rfile):
    send_line(sock, "LIST")
    resp = read_line(rfile)
    if not resp.startswith("FILE_LIST"):
        print(resp)
        return
    count = int(resp.split()[1])
    print(f"{count} file(s) on server:")
    for _ in range(count):
        print("  " + read_line(rfile))


def do_upload(sock, rfile, path):
    if not os.path.isfile(path):
        print("File not found on your computer.")
        return
    name = os.path.basename(path)
    size = os.path.getsize(path)
    send_line(sock, f"UPLOAD {name} {size}")
    resp = read_line(rfile)
    if resp != "READY":
        print(resp)
        return
    start = time.time()
    with open(path, "rb") as f:
        while True:
            data = f.read(CHUNK)
            if not data:
                break
            sock.sendall(data)
    resp = read_line(rfile)
    elapsed = max(time.time() - start, 1e-6)
    print(f"{resp}: {name} ({size} bytes in {elapsed:.2f}s, "
          f"{size / elapsed / 1e6:.2f} MB/s)")


def do_download(sock, rfile, name):
    send_line(sock, f"DOWNLOAD {name}")
    resp = read_line(rfile)
    if not resp.startswith("FILE_INFO"):
        print(resp)
        return
    size = int(resp.split()[1])
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    path = os.path.join(DOWNLOAD_DIR, os.path.basename(name))
    start = time.time()
    remaining = size
    with open(path, "wb") as f:
        while remaining > 0:
            data = rfile.read(min(CHUNK, remaining))
            if not data:
                break
            f.write(data)
            remaining -= len(data)
    elapsed = max(time.time() - start, 1e-6)
    if remaining > 0:
        print("Download was cut off.")
    else:
        print(f"Downloaded {name} ({size} bytes in {elapsed:.2f}s, "
              f"{size / elapsed / 1e6:.2f} MB/s) -> {path}")


def main():
    try:
        sock = socket.create_connection((HOST, PORT))
    except OSError as e:
        print(f"Could not connect to {HOST}:{PORT} - {e}")
        return
    rfile = sock.makefile("rb")

    username = input("Username: ").strip()
    password = input("Password: ").strip()
    send_line(sock, f"LOGIN {username} {password}")
    if read_line(rfile) != "AUTH_OK":
        print("Login failed.")
        sock.close()
        return
    print("Logged in. Commands: list | upload <path> | download <name> | quit")

    try:
        while True:
            parts = input("> ").strip().split(maxsplit=1)
            if not parts:
                continue
            cmd = parts[0].lower()
            if cmd == "list":
                do_list(sock, rfile)
            elif cmd == "upload" and len(parts) == 2:
                do_upload(sock, rfile, parts[1].strip('"'))
            elif cmd == "download" and len(parts) == 2:
                do_download(sock, rfile, parts[1])
            elif cmd == "quit":
                send_line(sock, "QUIT")
                print(read_line(rfile))
                break
            else:
                print("Commands: list | upload <path> | download <name> | quit")
    except (ConnectionError, OSError) as e:
        print(f"Connection problem: {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
