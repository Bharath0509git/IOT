#!/usr/bin/env python3
"""Local Server: Embedded MQTT Broker + SQLite Database + Dashboard Web Server."""
import socket
import struct
import threading
import json
import math
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Fallback-safe Indian Standard Time (IST: UTC+5:30)
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    datetime.now(IST)
except Exception:
    IST = timezone(timedelta(hours=5, minutes=30))

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "iot_monitor.db"
LEGACY_DATA = ROOT / "file.txt"
HTTP_PORT = 8000
MQTT_PORT = 1883

# ==============================================================================
# 1. SQLITE DATABASE SETUP & MIGRATION
# ==============================================================================
def init_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                temp_dht REAL NOT NULL,
                temp_ds REAL NOT NULL,
                humidity REAL NOT NULL,
                status TEXT DEFAULT 'NORMAL'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reading_date ON sensor_readings(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reading_ts ON sensor_readings(timestamp)")

    # Check if DB is empty and legacy file.txt exists -> migrate once
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sensor_readings")
    count = cur.fetchone()[0]
    if count == 0 and LEGACY_DATA.exists():
        print("[DB] Migrating historical records from file.txt into SQLite...")
        migrated = 0
        try:
            with conn:
                for line in LEGACY_DATA.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        ts = r.get("timestamp") or f"{r.get('date')}T{r.get('time')}"
                        conn.execute("""
                            INSERT INTO sensor_readings (timestamp, date, time, temp_dht, temp_ds, humidity, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (ts, r.get("date", ""), r.get("time", ""), float(r["temp_dht"]), float(r["temp_ds"]), float(r["humidity"]), "HISTORICAL"))
                        migrated += 1
                    except Exception:
                        pass
            print(f"[DB] Successfully migrated {migrated} records into {DB_PATH.name}.")
        except Exception as e:
            print(f"[DB] Migration note: {e}")
    conn.close()

db_lock = threading.Lock()

def insert_reading(temp_dht, temp_ds, humidity, status="NORMAL", custom_time=None):
    now = custom_time or datetime.now(IST)
    ts = now.isoformat(timespec="seconds")
    d_str = now.strftime("%Y-%m-%d")
    t_str = now.strftime("%H:%M:%S")

    with db_lock:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        try:
            with conn:
                conn.execute("""
                    INSERT INTO sensor_readings (timestamp, date, time, temp_dht, temp_ds, humidity, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (ts, d_str, t_str, round(temp_dht, 2), round(temp_ds, 2), round(humidity, 2), status))
        finally:
            conn.close()
    return ts

def get_readings(from_date=None, to_date=None, limit=2000):
    with db_lock:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            if from_date and to_date:
                cur.execute("""
                    SELECT timestamp, date, time, temp_dht, temp_ds, humidity, status
                    FROM sensor_readings
                    WHERE date >= ? AND date <= ?
                    ORDER BY id ASC
                """, (from_date, to_date))
            else:
                cur.execute("""
                    SELECT timestamp, date, time, temp_dht, temp_ds, humidity, status
                    FROM sensor_readings
                    ORDER BY id DESC
                    LIMIT ?
                """, (limit,))
                rows = cur.fetchall()
                # Return in chronological order
                return [dict(r) for r in reversed(rows)]
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

# ==============================================================================
# 2. ZERO-DEPENDENCY EMBEDDED MQTT BROKER (PORT 1883)
# ==============================================================================
class EmbeddedMQTTBroker:
    """Lightweight MQTT v3.1.1 Broker supporting CONNECT, PUBLISH (QoS 0/1), SUBSCRIBE, PING."""
    def __init__(self, host="0.0.0.0", port=1883):
        self.host = host
        self.port = port
        self.server = None
        self.running = False
        self.clients = []
        self.subscribers = {} # topic -> list of client sockets

    def start(self):
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((self.host, self.port))
            self.server.listen(15)
            self.running = True
            threading.Thread(target=self._accept_loop, daemon=True, name="MQTT-Accept").start()
            print(f"[MQTT] Embedded MQTT Broker listening on {self.host}:{self.port}")
            return True
        except OSError as e:
            print(f"[MQTT] Could not bind to port {self.port} ({e}). An external broker may already be running.")
            return False

    def _read_exact(self, conn, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _read_var_len(self, conn):
        multiplier = 1
        value = 0
        while True:
            b = conn.recv(1)
            if not b:
                return None
            digit = b[0]
            value += (digit & 127) * multiplier
            multiplier *= 128
            if (digit & 128) == 0:
                break
        return value

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server.accept()
                threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def _handle_client(self, conn, addr):
        self.clients.append(conn)
        try:
            while self.running:
                b1 = conn.recv(1)
                if not b1:
                    break
                pkt_type = b1[0] >> 4
                flags = b1[0] & 0x0F
                rem_len = self._read_var_len(conn)
                if rem_len is None:
                    break
                payload = self._read_exact(conn, rem_len)
                if payload is None:
                    break

                # 1. CONNECT -> Reply CONNACK
                if pkt_type == 1:
                    conn.sendall(b"\x20\x02\x00\x00") # Accepted

                # 2. PUBLISH
                elif pkt_type == 3:
                    qos = (flags >> 1) & 0x03
                    if len(payload) < 2:
                        continue
                    tlen = struct.unpack("!H", payload[0:2])[0]
                    topic = payload[2:2+tlen].decode("utf-8", errors="ignore")
                    offset = 2 + tlen

                    # If QoS 1, reply with PUBACK
                    if qos > 0 and len(payload) >= offset + 2:
                        pkt_id = payload[offset:offset+2]
                        offset += 2
                        conn.sendall(b"\x40\x02" + pkt_id)

                    msg_bytes = payload[offset:]
                    msg_str = msg_bytes.decode("utf-8", errors="ignore")
                    process_mqtt_message(topic, msg_str)

                # 3. SUBSCRIBE -> Reply SUBACK
                elif pkt_type == 8:
                    if len(payload) >= 2:
                        pkt_id = payload[0:2]
                        conn.sendall(b"\x90\x03" + pkt_id + b"\x00") # QoS 0 granted

                # 4. PINGREQ -> Reply PINGRESP
                elif pkt_type == 12:
                    conn.sendall(b"\xD0\x00")

                # 5. DISCONNECT
                elif pkt_type == 14:
                    break
        except Exception:
            pass
        finally:
            if conn in self.clients:
                self.clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass

def process_mqtt_message(topic, message):
    """Processes incoming messages from the MQTT broker and writes to SQLite."""
    try:
        if topic == "iot/temperature/data":
            obj = json.loads(message)
            td = float(obj["temp_dht"])
            ts = float(obj["temp_ds"])
            hum = float(obj["humidity"])
            status = str(obj.get("status", "NORMAL"))
            is_buffered = bool(obj.get("buffered", False))

            if not all(math.isfinite(v) for v in (td, ts, hum)):
                return
            if not (-55 <= td <= 125 and -55 <= ts <= 125 and 0 <= hum <= 100):
                return

            saved_ts = insert_reading(td, ts, hum, status)
            buff_note = " [OFFLINE BUFFER RECOVERED]" if is_buffered else ""
            print(f"[MQTT RECEIVED]{buff_note} DHT: {td:.1f}°C | DS: {ts:.1f}°C | Hum: {hum:.1f}% | Status: {status} ({saved_ts})")

        elif topic == "iot/temperature/status":
            print(f"[MQTT ALERT] System Status Event: {message}")

    except Exception as e:
        print(f"[MQTT ERROR] Failed parsing payload: {e} | Raw: {message}")

# Fallback: If port 1883 was already taken by external Mosquitto, subscribe using paho-mqtt
def run_paho_subscriber_fallback():
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        def on_msg(c, u, msg):
            process_mqtt_message(msg.topic, msg.payload.decode("utf-8", errors="ignore"))
        client.on_message = on_msg
        client.connect("127.0.0.1", 1883, 60)
        client.subscribe("iot/temperature/#")
        print("[MQTT] Subscribed to external Mosquitto broker on 127.0.0.1:1883")
        client.loop_forever()
    except Exception as e:
        print(f"[MQTT] Paho fallback listener note: {e}")

# ==============================================================================
# 3. HTTP DASHBOARD & QUERY SERVER (PORT 8000)
# ==============================================================================
class DashboardHandler(BaseHTTPRequestHandler):
    def send_data(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self.send_data(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path in ("/statistics", "/statistics.html"):
            self.send_data(200, (ROOT / "statistics.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/data":
            from_d = query.get("from", [None])[0]
            to_d = query.get("to", [None])[0]
            records = get_readings(from_d, to_d)
            self.send_data(200, json.dumps({"records": records, "source": "sqlite"}).encode(), "application/json")
        elif path == "/api/status":
            self.send_data(200, json.dumps({"status": "running", "database": "sqlite", "mqtt_port": MQTT_PORT}).encode(), "application/json")
        else:
            self.send_data(404, b"Not found", "text/plain")

    def do_POST(self):
        # Optional backward-compatibility HTTP fallback
        if self.path.split("?", 1)[0] != "/api/data":
            self.send_data(404, b"Not found", "text/plain")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 10000:
                raise ValueError("Invalid request size")
            obj = json.loads(self.rfile.read(size).decode("utf-8"))
            td, ts, hum = float(obj["temp_dht"]), float(obj["temp_ds"]), float(obj["humidity"])
            saved_ts = insert_reading(td, ts, hum, "HTTP_FALLBACK")
            self.send_data(201, json.dumps({"ok": True, "timestamp": saved_ts}).encode(), "application/json")
        except Exception as e:
            self.send_data(400, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")

    def log_message(self, fmt, *args):
        # Silence routine GET polls from terminal flooding
        if "GET /api/data" in (fmt % args):
            return
        print("%s - %s" % (self.log_date_time_string(), fmt % args))

# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
if __name__ == "__main__":
    init_db()

    broker = EmbeddedMQTTBroker("0.0.0.0", MQTT_PORT)
    started = broker.start()
    if not started:
        threading.Thread(target=run_paho_subscriber_fallback, daemon=True).start()

    print(f"=====================================================")
    print(f"  Dual-Sensor IoT Server (MQTT + SQLite)")
    print(f"=====================================================")
    print(f"  Web Dashboard : http://127.0.0.1:{HTTP_PORT}")
    print(f"  Past Stats    : http://127.0.0.1:{HTTP_PORT}/statistics")
    print(f"  MQTT Broker   : port {MQTT_PORT} (Topic: iot/temperature/data)")
    print(f"  Database      : {DB_PATH.name} (SQLite)")
    print(f"=====================================================")

    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), DashboardHandler)
    httpd.serve_forever()
