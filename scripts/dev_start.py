#!/usr/bin/env python3
"""启动完整本地开发环境：FastAPI 后端 + Next.js 开发服务器。"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _terminate(process: subprocess.Popen[object] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> int:
    backend_port = int(os.environ.get("PORT", "8600"))
    ui_port = int(os.environ.get("UI_PORT", "5173"))
    if backend_port == ui_port:
        print(f"后端端口和前端端口不能相同：{backend_port}", file=sys.stderr)
        return 2
    for label, port in (("后端", backend_port), ("前端", ui_port)):
        if _port_in_use(port):
            print(f"{label}端口 {port} 已被占用，请先执行 make stop", file=sys.stderr)
            return 1

    env = os.environ.copy()
    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "opencam.main:app",
        "--port",
        str(backend_port),
    ]
    if env.get("RELOAD", "1") == "1":
        backend_cmd.extend(
            [
                "--reload",
                "--reload-dir",
                "opencam",
                "--reload-exclude",
                "models.py",
                "--reload-exclude",
                "migrations/*",
            ]
        )
    ui_cmd = ["pnpm", "run", "dev", "--", "--port", str(ui_port)]

    backend: subprocess.Popen[object] | None = None
    frontend: subprocess.Popen[object] | None = None
    stopping = False

    def stop_all(_signum: int | None = None, _frame: object | None = None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        _terminate(frontend)
        _terminate(backend)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        backend = subprocess.Popen(backend_cmd, cwd=ROOT, env=env)
        frontend = subprocess.Popen(ui_cmd, cwd=ROOT / "web", env=env)
        print(
            f"开发环境已启动：前端 http://127.0.0.1:{ui_port}，"
            f"后端 http://127.0.0.1:{backend_port}",
            flush=True,
        )
        while True:
            if stopping:
                return 0
            backend_code = backend.poll()
            frontend_code = frontend.poll()
            if backend_code is not None:
                print(f"后端已退出，退出码：{backend_code}", file=sys.stderr)
                return backend_code if backend_code > 0 else 1
            if frontend_code is not None:
                print(f"前端已退出，退出码：{frontend_code}", file=sys.stderr)
                return frontend_code if frontend_code > 0 else 1
            time.sleep(0.2)
    finally:
        stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
