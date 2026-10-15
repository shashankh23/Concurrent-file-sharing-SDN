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

## Test users (placeholder, to be replaced by the database)
| Username | Password |
|---|---|
| alice | 1234 |
| bob | abcd |

## Application protocol
Text commands, one per line (ending in newline), over TCP port 5000.

| Client sends | Server replies | Notes |
|---|---|---|
| LOGIN user pass | AUTH_OK or AUTH_FAIL | Required before other commands |
| LIST | list of filenames | Files stored in server/files |
| UPLOAD name size | READY, then UPLOAD_OK | Client then sends exactly `size` raw bytes |
| DOWNLOAD name | FILE_INFO size, then bytes | ERROR if file not found |
| QUIT | BYE | Closes the connection |

File transfers send the size first, then exactly that many raw bytes,
because TCP is a byte stream with no message boundaries.

## Concurrency
The server creates one thread per connected client, so several clients
can upload and download at the same time.

## Status
- [x] TCP server and client (LOGIN, LIST, UPLOAD, DOWNLOAD)
- [x] Multi-client support (threads)
- [ ] Mininet topology and SDN controller
- [ ] Database authentication and file metadata
- [ ] Performance tests (SDN vs static)