import hashlib
import os
import requests
import socket
import struct
import threading
import time
import urllib3
import warnings

warnings.filterwarnings("ignore")

MAGIC = b"BCI1"

JOIN = 1
HEARTBEAT = 2

VERSION = 1


def get_token(url):
    r = requests.get(url, verify=False)

    if r.status_code != 200:
        raise Exception("failed to get token")

    data = r.json()

    if "token" not in data:
        raise Exception("invalid token response")

    return data["token"]


def pack_str(text):
    raw = text.encode("utf-8")

    if len(raw) > 65535:
        raw = raw[:65535]

    return struct.pack(">H", len(raw)) + raw


def make_packet(kind, token, addr, name, cid, iid):
    buf = bytearray()

    buf += MAGIC
    buf += bytes([kind])
    buf += bytes([VERSION])

    buf += iid
    buf += os.urandom(16)

    now = int(time.time())
    buf += struct.pack(">Q", now)

    buf += pack_str(addr)
    buf += pack_str(name)

    buf += struct.pack(">h", cid)

    sig = hashlib.sha256(token.encode() + bytes(buf)).digest()

    return bytes(buf) + sig


def start_presence(
    host,
    port,
    token_url,
    server_addr,
    player_name,
    client_id=1,
    interval=10
):
    print("getting token...")

    token = get_token(token_url)

    print("token ok")

    iid = os.urandom(16)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    join_packet = make_packet(
        JOIN,
        token,
        server_addr,
        player_name,
        client_id,
        iid
    )

    sock.sendto(join_packet, (host, port))

    print("join packet sent")

    def hb():
        while True:
            time.sleep(interval)

            try:
                packet = make_packet(
                    HEARTBEAT,
                    token,
                    server_addr,
                    player_name,
                    client_id,
                    iid
                )

                sock.sendto(packet, (host, port))

            except Exception as e:
                print("heartbeat error:", e)

    t = threading.Thread(target=hb)
    t.daemon = True
    t.start()

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nstopping...")


if __name__ == "__main__":
    print("====== presence ======\n")

    host = input("host | dont change: ").strip()
    if not host:
        host = "150.241.70.188"

    port = input("port | dont change: ").strip()
    port = int(port) if port else 8778

    token_url = input("token url | dont change if successful: ").strip()
    if not token_url:
        token_url = "https://150.241.70.188:8779/token.json"

    server_addr = input("server addr: ").strip()
    if not server_addr:
        server_addr = "123.45.67.89:8303"

    player_name = input("player name: ").strip()
    if not player_name:
        player_name = "presencebot"

    client_id = input("client id: ").strip()
    client_id = int(client_id) if client_id else 1

    interval = input("heartbeat interval | dont change if dont need to: ").strip()
    interval = int(interval) if interval else 10

    start_presence(
        host,
        port,
        token_url,
        server_addr,
        player_name,
        client_id,
        interval
    )
