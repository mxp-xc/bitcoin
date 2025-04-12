# -*- coding: utf-8 -*-
import importlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import typer
from rich import get_console
from rich.table import Table

from bitcoin.conf import settings

app = typer.Typer()


class _BreakLoop(Exception):
    pass


_console = get_console()


def get_support_scripts(path: Path | None = None) -> list[str]:
    path = path or Path(__file__)
    current_name = path.name

    support_scripts = []
    for file in path.parent.iterdir():  # type: Path
        if file.suffix != ".py" or file.name in ("__init__.py", current_name):
            continue
        support_scripts.append(file.stem)
    return support_scripts


def get_running_script(ignored_error: bool = False):
    path = Path(__file__)
    support_scripts = get_support_scripts(path)

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
                if script in running_script and not ignored_error:
                    raise RuntimeError(f"exist more than 2 script: `{script}` {cmdline}")
                running_script.add(script)
                running_process.append({
                    "pid": proc.info["pid"],
                    "script": script,
                    "cmdline": " ".join(cmdline)
                })
    return running_process


@app.command("script")
def list_script():
    _console.print(get_support_scripts())


@app.command("list")
def list_script(ignored_error: bool = False):
    running_script = get_running_script(ignored_error)
    if not running_script:
        _console.print("nothing")
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

    _console.print(table)


@app.command("stop")
def stop_script(name: str):
    running_script = {
        s["script"]: s
        for s in get_running_script()
    }
    if name != "all":
        script = running_script.get(name, None)
        if script:
            scripts = [script]
        else:
            scripts = []
    else:
        scripts = list(running_script.values())
    if not scripts:
        _console.print(f"script: `{name}` not found")
        return

    _console.print(f"start stop {scripts}")
    for script in scripts:
        script_name = script['script']
        process = psutil.Process(script["pid"])
        process.terminate()
        for _ in range(20):
            if not process.is_running():
                break
            time.sleep(.1)
        else:
            process.kill()

        if process.is_running():
            _console.print(f"Failed to stop `{script_name}`")
        else:
            _console.print(f"success to stop `{script_name}`")


@app.command("start")
def start_script(name: str, background: bool = False, func: str = "main"):
    support_script = get_support_scripts()
    if name not in support_script:
        _console.print(f"script: {name} not found. support script: {support_script}")
        return
    stop_script(name)

    module_name = f"bitcoin.scripts.{name}"
    module = importlib.import_module(module_name)
    runner = getattr(module, func)
    if not background:
        runner()
        return
    python_path = sys.executable
    script_path = (Path(__file__).parent / f"{name}.py").resolve()
    nohup_dir = settings.project_path / 'script_nohup'
    nohup_dir.mkdir(exist_ok=True)
    path = nohup_dir / f'{name}_nohup.out'
    _console.print(f"{python_path} {script_path}")

    with open(path, 'w') as f:
        process = subprocess.Popen(
            [str(python_path), str(script_path)],
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        _console.print(process, process.pid)

    tail_process = subprocess.Popen(
        ["tail", "-f", str(path)],
        stdout=sys.stdout,
        stderr=subprocess.STDOUT
    )

    # 捕获 Ctrl+C 信号，优雅退出
    def signal_handler(*args):
        _console.print("\n终止日志监控...")
        tail_process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.pause()


if __name__ == '__main__':
    app()
