"""Launch the Windows development server independently of a chat terminal."""
from pathlib import Path
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Use python -m uvicorn backend.app:app on Linux/WSL.')
    root = Path(__file__).resolve().parents[1]
    url = f'http://127.0.0.1:{args.port}/health'
    try:
        with urlopen(url, timeout=2) as response:
            if json.load(response).get('status') == 'ok':
                print(f'Server already running: http://localhost:{args.port}/')
                return
    except OSError:
        pass
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise SystemExit(f'Port {args.port} is occupied by another service.')
    logs = root / 'backend' / 'data'
    logs.mkdir(exist_ok=True)
    # The Windows management provider creates the process outside the calling
    # terminal's process tree/job, so terminal cleanup cannot remove the server.
    def ps_literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    command_line = subprocess.list2cmdline([sys.executable, str(root / 'backend' / 'serve.py'), '--port', str(args.port)])
    command = ("$started = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{"
               "CommandLine=" + ps_literal(command_line) + ";CurrentDirectory=" + ps_literal(root) + "};"
               "if ($started.ReturnValue -ne 0) { throw ('Server launch failed: ' + $started.ReturnValue) }")
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], check=True)
    for _ in range(40):
        try:
            with urlopen(url, timeout=1) as response:
                if json.load(response).get('status') == 'ok':
                    print(f'Server running independently: http://localhost:{args.port}/')
                    return
        except OSError:
            time.sleep(0.25)
    raise SystemExit('Server startup timed out; see backend/data/server.stderr.log.')


if __name__ == '__main__':
    main()
