"""Bridge from the file server (application) to the SDN controller (network).

The controller (sdn/controller.py) polls a small command file once per second.
This module writes commands into it, so an application event such as
"too many failed logins from 10.0.0.3" becomes a network action
"drop 10.0.0.3 -> server TCP/5000 at switch s1".

Commands written (one per line):
    BLOCK <ip>
    UNBLOCK <ip>

Mininet hosts share the VM's filesystem, so the server running on h4 can
write the file that the controller running on the VM reads.
"""
import ipaddress
import os
import threading

POLICY_FILE = os.environ.get("SDN_POLICY_FILE", "/tmp/sdn_policy_command.txt")
_lock = threading.Lock()


def _valid_remote_ip(ip):
    """Only real, non-loopback IPv4 addresses are sent to the controller."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not addr.is_loopback


def send_command(command, ip):
    """Append '<command> <ip>' to the controller's command file.

    Returns True if the command was written, False if it was skipped
    (loopback/invalid address) or the file could not be written.
    """
    if not _valid_remote_ip(ip):
        return False
    try:
        with _lock, open(POLICY_FILE, "a") as f:
            f.write(f"{command} {ip}\n")
        return True
    except OSError as e:
        print(f"[SDN-BRIDGE] could not write {POLICY_FILE}: {e}")
        return False


def block_ip(ip):
    return send_command("BLOCK", ip)


def unblock_ip(ip):
    return send_command("UNBLOCK", ip)
