# -*- coding: utf-8 -*-
"""临时：本地 HTTP 实测宣传页与 zip 的下载可达性。"""
import http.server
import socketserver
import threading
import urllib.request

class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

srv = socketserver.TCPServer(("127.0.0.1", 8138), H)
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()

for path in ("index.html", "FloatPulse.zip"):
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:8138/{path}", timeout=5)
        size = int(r.headers.get("Content-Length", 0))
        print(f"{path}: HTTP {r.status}, {size // 1024 // 1024} MB"
              if path.endswith("zip") else f"{path}: HTTP {r.status}, {size} B")
    except Exception as e:
        print(f"{path}: FAIL {e}")
srv.shutdown()
print("[DONE]")
