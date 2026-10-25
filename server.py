#!/usr/bin/env python3
"""Local HTTP server: dashboard + append-only temperature history."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json, math

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "file.txt"
PORT = 8000

class Handler(BaseHTTPRequestHandler):
    def send_data(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_data(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path in ("/statistics", "/statistics.html"):
            self.send_data(200, (ROOT / "statistics.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/data":
            records = []
            for line in DATA.read_text(encoding="utf-8").splitlines():
                try: records.append(json.loads(line))
                except json.JSONDecodeError: pass
            self.send_data(200, json.dumps({"records": records}).encode(), "application/json")
        elif path == "/file.txt":
            self.send_data(200, DATA.read_bytes(), "text/plain; charset=utf-8")
        else:
            self.send_data(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/data":
            self.send_data(404, b"Not found", "text/plain"); return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 10000: raise ValueError("Invalid request size")
            obj = json.loads(self.rfile.read(size).decode("utf-8"))
            td, ts, hum = float(obj["temp_dht"]), float(obj["temp_ds"]), float(obj["humidity"])
            if not all(math.isfinite(v) for v in (td, ts, hum)): raise ValueError("Values must be finite")
            if not (-55 <= td <= 125 and -55 <= ts <= 125 and 0 <= hum <= 100):
                raise ValueError("Reading outside accepted range")
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            rec = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S"),
                   "timestamp": now.isoformat(timespec="seconds"),
                   "temp_dht": round(td, 2), "temp_ds": round(ts, 2), "humidity": round(hum, 2)}
            with DATA.open("a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
            self.send_data(201, json.dumps({"ok": True}).encode(), "application/json")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self.send_data(400, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")

    def log_message(self, fmt, *args): print("%s - %s" % (self.log_date_time_string(), fmt % args))

if __name__ == "__main__":
    DATA.touch(exist_ok=True)
    print("Dashboard: http://127.0.0.1:8000")
    print("ESP32 endpoint: http://<COMPUTER-LAN-IP>:8000/api/data")
    print("History file:", DATA)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
