"""把子进程拆出当前进程组，避免 make / 非交互 shell 退出后被 SIGHUP 带走。"""

from __future__ import annotations

import os
import sys


def main() -> None:
    if len(sys.argv) < 4:
        sys.stderr.write("用法: run_detached.py <pidfile> <logfile> <cmd>...\n")
        sys.exit(2)
    pidfile, logfile, *cmd = sys.argv[1:]

    if os.fork() > 0:
        sys.exit(0)

    os.setsid()
    child = os.fork()
    if child > 0:
        with open(pidfile, "w", encoding="utf-8") as f:
            f.write(str(child))
        sys.exit(0)

    fd = os.open(os.devnull, os.O_RDWR)
    os.dup2(fd, 0)
    logfd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
