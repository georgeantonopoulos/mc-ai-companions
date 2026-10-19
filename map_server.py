"""A live top-down map of the play world, in the browser. No AI involved: it asks the game where everyone is
(/scriptevent claude:map) every couple of seconds and draws it.

  python3 map_server.py            # then open http://localhost:8765 (or http://<this Mac's address>:8765 on the iPad)
"""
import http.server
import json
import os
import re
import socket
import subprocess
import threading
import time

SERVER = os.environ.get("MC_SERVER", "ai-companions-server")  # the Bedrock server container
PORT = 8765
STATE = {"snap": None, "terrain": None}
HERE = os.path.dirname(os.path.abspath(__file__))


def console(cmd):
    script = ('for p in /proc/[0-9]*; do case "$(tr "\\0" " " < $p/cmdline 2>/dev/null)" in '
              './bedrock_server-*) echo "$0" > $p/fd/0; exit 0;; esac; done; exit 2')
    subprocess.run(["docker", "exec", SERVER, "sh", "-c", script, cmd], capture_output=True)


def snapshot(terrain):
    tag = "m%d" % (time.time() * 1000)
    since = str(int(time.time()) - 1)
    console("scriptevent claude:map %s%s" % (tag, " terrain" if terrain else ""))
    for _ in range(10):
        time.sleep(0.3)
        out = subprocess.run(["docker", "logs", "--since", since, SERVER], capture_output=True)
        text = (out.stdout + out.stderr).decode("utf-8", "replace")
        m = re.search(r'\[claude\] (\{"who":"map","map":"%s".*\})' % tag, text)
        if m:
            return json.loads(m.group(1))
    return None


def poll():
    n = 0
    while True:
        try:
            snap = snapshot(terrain=(n % 10 == 0))  # land every ~20 s, people every 2 s
            if snap:
                if snap.get("terrain"):
                    STATE["terrain"] = {**snap["terrain"], "cx": snap["cx"], "cz": snap["cz"]}
                STATE["snap"] = snap
        except Exception as e:
            print("poll:", e, flush=True)
        n += 1
        time.sleep(2)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/state"):
            body = json.dumps({**(STATE["snap"] or {}), "land": STATE["terrain"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = open(os.path.join(HERE, "map.html"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    print("Map: http://localhost:%d   (iPad: http://%s:%d)" % (PORT, s.getsockname()[0], PORT), flush=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
