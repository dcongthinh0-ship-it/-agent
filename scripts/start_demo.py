"""Start only this project's loopback demo, preserving every previous demo round."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "artifacts" / "demo-20261008"
processes = []


def occupied(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def status(url):
    with urllib.request.urlopen(url, timeout=1) as response:
        return json.load(response)


def stop(*_args):
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def launch(args, folder, filename, env=None):
    log = (STATE / filename).open("a")
    process = subprocess.Popen(
        args, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    )
    log.close()
    processes.append(process)


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    if occupied(8012):
        if not status("http://127.0.0.1:8012/api/v1/status").get("demo_mode"):
            raise RuntimeError("8012已被其他服务占用，请检查后重试；本脚本不会停止它。")
    else:
        launch(
            [
                str(ROOT / "backend/.venv/bin/python"),
                "-m",
                "chemo_agent_product.demo",
                "--source-env",
                str(ROOT / "backend/.env"),
                "--state",
                str(STATE),
            ],
            ROOT / "backend",
            "backend.log",
        )
    if occupied(5174):
        if not status("http://127.0.0.1:5174/api/v1/status").get("demo_mode"):
            raise RuntimeError("5174已被其他页面占用，请检查后重试；本脚本不会停止它。")
    else:
        launch(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5174", "--strictPort"],
            ROOT / "frontend",
            "frontend.log",
            {**os.environ, "CHEMO_API_ORIGIN": "http://127.0.0.1:8012"},
        )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError(f"演示服务启动失败，请查看 {STATE} 中的日志。")
        try:
            if status("http://127.0.0.1:5174/api/v1/status").get("demo_mode"):
                print("演示已启动：http://127.0.0.1:8012/demo/host", flush=True)
                print("关闭此终端只停止本次脚本启动的服务，演示数据仍保留。", flush=True)
                if "--no-open" not in sys.argv:
                    subprocess.run(["open", "http://127.0.0.1:8012/demo/host"], check=False)
                while processes and all(p.poll() is None for p in processes):
                    time.sleep(1)
                return 0
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"演示启动超过60秒，请查看 {STATE} 中的日志。")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        sys.exit(main())
    finally:
        stop()
