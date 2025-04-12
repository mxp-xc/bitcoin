# -*- coding: utf-8 -*-
import os
import time
from argparse import ArgumentParser
from pathlib import Path

import psutil
from loguru import logger
from rich import get_console
from rich.table import Table


class _BreakLoop(Exception):
    pass


def get_running_script():
    path = Path(__file__)
    current_name = path.name

    support_scripts = []
    for file in path.parent.iterdir():  # type: Path
        if file.suffix != ".py" or file.name in ("__init__.py", current_name):
            continue
        support_scripts.append(file.stem)

    running_script = set()
    running_process = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if proc.info["pid"] == os.getpid():
            continue
        cmdline = proc.info['cmdline']
        if not cmdline:
            continue
        try:
            for white in ("--client", path.stem):  # pycharm
                if white in cmdline:
                    raise _BreakLoop
        except _BreakLoop:
            continue
        for script in support_scripts:
            for part in cmdline:
                if script not in part:
                    continue
                if script in running_script:
                    raise RuntimeError(f"exist more than 2 script: `{script}` {cmdline}")
                running_script.add(script)
                running_process.append({
                    "pid": proc.info["pid"],
                    "script": script,
                    "cmdline": " ".join(cmdline)
                })
    return running_process


def list_script():
    running_script = get_running_script()
    if not running_script:
        print("nothing")
        return
    table = Table()
    table.add_column("pid", justify="center")
    table.add_column("script", justify="center")
    table.add_column("cmdline", justify="center")
    for d in running_script:
        table.add_row(
            str(d["pid"]),
            d["script"],
            d["cmdline"]
        )

    get_console().print(table)


def stop_script(name: str):
    running_script = {
        s["script"]: s
        for s in get_running_script()
    }
    script = running_script.get(name, None)
    if not script:
        print(f"script: `{name}` not found")
        return

    print(f"start stop {script}, info: {script}")
    process = psutil.Process(script["pid"])
    process.terminate()
    for _ in range(20):
        if not process.is_running():
            break
        time.sleep(.1)
    else:
        process.kill()

    if process.is_running():
        print(f"Failed to stop `{name}`")
    else:
        print(f"success to top `{name}`")


def main():
    parser = ArgumentParser()
    command = parser.add_subparsers(dest="command", required=True)
    command.add_parser("list", help="列出所有正在执行的脚本")

    stop_parser = command.add_parser("stop", help="停止应用")
    stop_parser.add_argument("name", help="需要停止的应用名称")
    args = parser.parse_args()
    match args.command:
        case "list":
            list_script()
        case "stop":
            stop_script(name=args.name)
        case _:
            logger.info("unknown args")


if __name__ == '__main__':
    main()
