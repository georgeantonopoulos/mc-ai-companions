"""A throwaway test server for the real-engine tests (drive_test.py, portal_test.py): Mojang's Bedrock Dedicated
Server in Docker, a fresh world each time, separate from the play server. It publishes no ports, so it can run
while the play server is up.

  import bds_test as b
  b.prepare(); b.start(); b.command("say hi"); print(b.logs())
"""
import os
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "test", "data")
NAME = "ai-companions-test"
WORLD = "companiontest"


def sh(*a, check=True, **kw):
    return subprocess.run(list(a), check=check, capture_output=True, text=True, **kw)


def logs():
    r = sh("docker", "logs", NAME, check=False)
    return r.stdout + r.stderr


def command(c):
    """Type a command into the server console (writes to the server process's stdin; the image's
    send-command helper can't find the process under x86 emulation on Apple Silicon)."""
    script = ('for p in /proc/[0-9]*; do case "$(tr "\\0" " " < $p/cmdline 2>/dev/null)" in '
              './bedrock_server-*) echo "$0" > $p/fd/0; exit 0;; esac; done; exit 2')
    sh("docker", "exec", NAME, "sh", "-c", script, c, check=False)


def prepare():
    """A clean slate: no old container, no old world. The tests install the packs themselves."""
    sh("docker", "rm", "-f", NAME, check=False)
    shutil.rmtree(os.path.join(DATA, "worlds", WORLD), ignore_errors=True)
    os.makedirs(os.path.join(DATA, "worlds", WORLD), exist_ok=True)
    for kind in ("behavior_packs", "resource_packs"):
        os.makedirs(os.path.join(DATA, kind), exist_ok=True)


def start():
    sh("docker", "run", "-dit", "--name", NAME, "--platform", "linux/amd64",
       "-e", "EULA=TRUE", "-e", "VERSION=1.26.52.3", "-e", "ONLINE_MODE=false", "-e", "ALLOW_LIST=false",
       "-e", "LEVEL_NAME=" + WORLD, "-e", "GAMEMODE=survival", "-e", "DIFFICULTY=normal", "-e", "ALLOW_CHEATS=false",
       "-e", "CONTENT_LOG_FILE_ENABLED=true", "-e", "CONTENT_LOG_CONSOLE_OUTPUT_ENABLED=true",
       "-e", "VIEW_DISTANCE=10", "-e", "TICK_DISTANCE=4",
       "-v", DATA + ":/data", "itzg/minecraft-bedrock-server")  # no ports published: the play server owns 19132
    for _ in range(120):
        if "Server started" in logs():
            return
        time.sleep(2)
    raise SystemExit("server did not start:\n" + logs()[-3000:])
