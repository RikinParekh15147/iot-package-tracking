"""Persistent development server entry point (launched by the Windows shell)."""
from pathlib import Path
import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    data = root / 'backend' / 'data'
    data.mkdir(exist_ok=True)
    sys.stdout = (data / 'server.stdout.log').open('a', encoding='utf-8', buffering=1)
    sys.stderr = (data / 'server.stderr.log').open('a', encoding='utf-8', buffering=1)
    (data / 'server.pid').write_text(str(os.getpid()), encoding='ascii')
    import uvicorn
    uvicorn.run('backend.app:app', host='0.0.0.0', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
