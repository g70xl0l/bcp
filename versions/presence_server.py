# frontend is vibecoded and this code is half vibecoded
import socket
import hashlib
import struct
import os
import time
import threading
import json
import requests
import urllib3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import webbrowser

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROTOCOL_MAGIC = b"BCI1"
PROTOCOL_VERSION = 1
PACKET_JOIN = 1
PACKET_HEARTBEAT = 2
PACKET_LEAVE = 3

TOKEN_URL = "https://150.241.70.188:8779/token.json"
SERVER_HOST = "150.241.70.188"
SERVER_PORT = 8778
WEB_PORT = 7799


def write_string(s: str) -> bytes:
    encoded = s.encode()[:65535]
    return struct.pack(">H", len(encoded)) + encoded


def build_packet(packet_type: int, shared_token: str, server_address: str,
                 player_name: str, client_id: int, instance_id: bytes) -> bytes:
    payload = (
        PROTOCOL_MAGIC +
        bytes([packet_type, PROTOCOL_VERSION]) +
        instance_id +
        os.urandom(16) +
        struct.pack(">Q", int(time.time())) +
        write_string(server_address) +
        write_string(player_name) +
        struct.pack(">h", client_id)
    )
    proof = hashlib.sha256(shared_token.encode() + payload).digest()
    return payload + proof


def fetch_token(url: str) -> str:
    response = requests.get(url, verify=False, timeout=5)
    return response.json()["token"]


class PresenceClient:
    def __init__(self, shared_token: str, server_address: str,
                 player_name: str, client_id: int):
        self.shared_token = shared_token
        self.server_address = server_address
        self.player_name = player_name
        self.client_id = client_id
        self.instance_id = os.urandom(16)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = False
        self._thread = None

    def _send(self, packet_type: int):
        pkt = build_packet(packet_type, self.shared_token,
                           self.server_address, self.player_name,
                           self.client_id, self.instance_id)
        self.sock.sendto(pkt, (SERVER_HOST, SERVER_PORT))

    def start(self):
        self.running = True
        self._send(PACKET_JOIN)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self.running:
            time.sleep(10)
            if self.running:
                self._send(PACKET_HEARTBEAT)

    def stop(self):
        self.running = False
        self._send(PACKET_LEAVE)


# ── global state ───────────────────────────────────────────────────────────────
state_lock = threading.Lock()
clients: dict[str, PresenceClient] = {}
log_entries: list[dict] = []
shared_token: str | None = None
token_error: str | None = None


def add_log(action: str, name: str, server: str):
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "action": action,
        "name": name,
        "server": server,
    }
    with state_lock:
        log_entries.append(entry)
        if len(log_entries) > 100:
            log_entries.pop(0)


def init_token():
    global shared_token, token_error
    try:
        shared_token = fetch_token(TOKEN_URL)
        add_log("TOKEN", "fetched", TOKEN_URL)
    except Exception as ex:
        token_error = str(ex)
        add_log("ERROR", "token fetch failed", str(ex))


# ── HTTP handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default access log

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            with open(os.path.join(os.path.dirname(__file__), "presence_ui.html"), "rb") as f:
                self._html(f.read())

        elif path == "/api/status":
            with state_lock:
                self._json({
                    "token": shared_token[:8] + "..." if shared_token else None,
                    "token_error": token_error,
                    "clients": [
                        {
                            "id": cid,
                            "name": c.player_name,
                            "server": c.server_address,
                            "client_id": c.client_id,
                        }
                        for cid, c in clients.items()
                    ],
                    "log": list(reversed(log_entries[-30:])),
                })

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == "/api/connect":
            name = body.get("name", "").strip()
            server = body.get("server", "").strip()
            cid_raw = body.get("client_id", 1)
            uid = body.get("uid", "")

            if not name or not server or not uid:
                return self._json({"error": "missing fields"}, 400)
            if not shared_token:
                return self._json({"error": "token not ready"}, 503)

            try:
                cid = int(cid_raw)
            except (ValueError, TypeError):
                return self._json({"error": "client_id must be integer"}, 400)

            with state_lock:
                if uid in clients:
                    return self._json({"error": "already connected"}, 409)
                client = PresenceClient(shared_token, server, name, cid)
                client.start()
                clients[uid] = client

            add_log("JOIN", name, server)
            self._json({"ok": True})

        elif path == "/api/disconnect":
            uid = body.get("uid", "")
            with state_lock:
                client = clients.pop(uid, None)
            if client:
                threading.Thread(target=client.stop, daemon=True).start()
                add_log("LEAVE", client.player_name, client.server_address)
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)

        else:
            self.send_response(404)
            self.end_headers()


def main():
    print("Fetching token...")
    threading.Thread(target=init_token, daemon=True).start()

    server = HTTPServer(("127.0.0.1", WEB_PORT), Handler)
    url = f"http://127.0.0.1:{WEB_PORT}"
    print(f"GUI running at {url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        with state_lock:
            for c in clients.values():
                c.stop()


if __name__ == "__main__":
    main()
