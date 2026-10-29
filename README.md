# Concurrent File Sharing System (TCP + Mininet + SDN)

A multi-client file server over TCP sockets, deployed on a Mininet
topology controlled by an SDN controller (Ryu / OpenFlow).

## Team
| Person | Role | Files |
|---|---|---|
| 1 | TCP file server and client | server/server.py, client/client.py |
| 2 | Mininet + SDN controller | sdn/ |
| 3 | Authentication, database, metadata | database/ |
| 4 | Testing, performance, documentation | tests/, results/ |

## How to run

Start the server (default port 5000):

    python server/server.py
    python server/server.py 6000        # custom port

Start a client (default 127.0.0.1:5000):

    python client/client.py
    python client/client.py 10.0.0.4 5000     # server IP and port

In Mininet, run the server on one host and clients on the others:

    h4 python server/server.py &
    h1 python client/client.py 10.0.0.4 5000

## Users (stored in the SQLite database, passwords hashed)
Created automatically on first server start (`database/fileshare.db`, not committed).

| Username | Password | Permissions |
|---|---|---|
| alice | 1234 | upload + download, premium (h1 in the demo) |
| bob | abcd | upload + download |
| guest | guest | download only |
| admin | admin123 | everything, may overwrite other users' files |

Add users and inspect data with `python database/admin.py` (see its header for commands):
`users`, `add-user`, `files`, `log`, `summary`, `export-transfers results/transfers.csv`, `block <ip>`, `unblock <ip>`.

## Application protocol
Text commands, one per line (ending in newline), over TCP port 5000.

| Client sends | Server replies | Notes |
|---|---|---|
| LOGIN user pass | AUTH_OK or AUTH_FAIL | Required before other commands. Locked IPs get `AUTH_FAIL Too many failed attempts...` |
| LIST | one line per file: `name | owner=.. | size bytes | upload time` | Metadata from the database |
| UPLOAD name size | READY, then UPLOAD_OK | Client then sends exactly `size` raw bytes |
| DOWNLOAD name | FILE_INFO size, then bytes | ERROR if file not found |
| QUIT | BYE | Closes the connection |

Errors: `ERROR Permission denied: upload|download`, `ERROR File is owned by <user>` (only the owner or admin may overwrite).

File transfers send the size first, then exactly that many raw bytes,
because TCP is a byte stream with no message boundaries.

## Concurrency
The server creates one thread per connected client, so several clients
can upload and download at the same time.

## Authentication, database and SDN link (Person 3)
- `database/database.py`: tables `users`, `files`, `transfers`, `activity_log`; PBKDF2 password hashing.
- `server/authentication.py`: login check, permissions, lockout.
- `server/metadata.py`: file metadata plus a `transfers` table (size, seconds, Mbps) used for the performance results.
- `server/sdn_bridge.py`: writes `BLOCK <ip>` / `UNBLOCK <ip>` to `/tmp/sdn_policy_command.txt`, which the controller reads every second.

Application-to-network flow: 3 failed logins from one IP (`FILESHARE_MAX_FAILED`) -> server locks the IP
and sends `BLOCK <ip>` -> controller installs a DROP rule on s1 -> after `FILESHARE_LOCKOUT_SECONDS` (60)
the server sends `UNBLOCK <ip>` and access recovers. Loopback addresses are never sent to the controller,
so test the SDN part from Mininet hosts (e.g. `h3`), not from localhost.

Run the tests from the project root: `python -m unittest discover -s tests`

## Status
- [x] TCP server and client (LOGIN, LIST, UPLOAD, DOWNLOAD)
- [x] Multi-client support (threads)
- [ ] Mininet topology and SDN controller
- [x] Database authentication and file metadata
- [ ] Performance tests (SDN vs static)