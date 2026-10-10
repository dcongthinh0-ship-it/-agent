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


def model_environment(path=None):
    """Load this workstation's private model configuration, never database overrides."""
    env = os.environ.copy()
    path = path or STATE / "model-settings.json"
    if not path.exists():
        return env
    values = json.loads(path.read_text())
    allowed = {
        "CHEMO_PRODUCT_MODEL_ENABLED",
        "CHEMO_PRODUCT_MODEL_API_KEY",
        "CHEMO_PRODUCT_MODEL_BASE_URL",
        "CHEMO_PRODUCT_MODEL_NAME",
        "CHEMO_PRODUCT_REVIEWER_MODEL_NAME",
        "CHEMO_PRODUCT_MODEL_TIMEOUT_SECONDS",
        "CHEMO_PRODUCT_MODEL_MAX_TURNS",
        "CHEMO_PRODUCT_MODEL_MAX_BUDGET_USD",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    }
    if not isinstance(values, dict) or any(
        key not in allowed or not isinstance(value, str) for key, value in values.items()
    ):
        raise RuntimeError("独立模型配置只接受模型环境变量及字符串值，不允许数据库或身份配置。")
    env.update(values)
    return env


def occupied(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def status(url):
    with urllib.request.urlopen(url, timeout=1) as response:
        return json.load(response)


def same_workspace(origin):
    try:
        runtime = status(origin + "/workstation/runtime")
        return runtime == {"workspace": str(ROOT), "database": "chemo_demo_test_20261008"}
    except (OSError, ValueError):
        return False


def wait_for_backend(deadline):
    # An existing Vite proxy cannot identify its workspace while its backend restarts.
    while not same_workspace("http://127.0.0.1:8012"):
        if time.monotonic() >= deadline or any(p.poll() is not None for p in processes):
            raise RuntimeError("工作站后端尚未就绪，请查看本目录启动日志。")
        time.sleep(0.25)


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
    deadline = time.monotonic() + 60
    if occupied(8012):
        if not same_workspace("http://127.0.0.1:8012"):
            raise RuntimeError("8012已被其他工作目录的服务占用，请检查后重试；本脚本不会停止它。")
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
            model_environment(),
        )
    wait_for_backend(deadline)
    if occupied(5174):
        if not same_workspace("http://127.0.0.1:5174"):
            raise RuntimeError("5174已被其他页面占用，请检查后重试；本脚本不会停止它。")
    else:
        launch(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5174", "--strictPort"],
            ROOT / "frontend",
            "frontend.log",
            {**os.environ, "CHEMO_API_ORIGIN": "http://127.0.0.1:8012"},
        )
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError(f"工作站启动失败，请查看 {STATE} 中的日志。")
        try:
            if same_workspace("http://127.0.0.1:5174"):
                print("工作站已启动：http://127.0.0.1:8012/workstation", flush=True)
                print("关闭此终端只停止本次脚本启动的服务，已有数据仍保留。", flush=True)
                if "--no-open" not in sys.argv:
                    subprocess.run(["open", "http://127.0.0.1:8012/workstation"], check=False)
                while processes and all(p.poll() is None for p in processes):
                    time.sleep(1)
                return 0
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"工作站启动超过60秒，请查看 {STATE} 中的日志。")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        sys.exit(main())
    finally:
        stop()
