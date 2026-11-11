#!/bin/sh
# Run locally with ./install.sh, or stream this script to sh.
set -eu
if [ "$(id -u)" -eq 0 ]; then
    echo '请以桌面用户运行，不要使用 sudo。' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo '请先安装 Python 3。' >&2
    exit 1
fi
action=setup
case "${1:-}" in
    plan|check|status|apply|restore|build-shell|setup)
        action=$1
        shift
        ;;
esac
case "$0" in
    */install.sh|install.sh)
        project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
        if [ -f "$project_root/appearance.py" ]; then
            exec python3 "$project_root/appearance.py" "$action" "$@"
        fi
        ;;
esac
project_temp=$(mktemp -d)
trap 'rm -rf -- "$project_temp"' EXIT HUP INT TERM
python3 - "$project_temp" "$action" "$@" <<'PYINSTALL'
import io
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import urllib.request

root = Path(sys.argv[1])
address = 'https://codeload.github.com/XARKUR/gnome-appearance/tar.gz/refs/heads/main'
request = urllib.request.Request(address, headers={'User-Agent': 'gnome-appearance-installer'})
with urllib.request.urlopen(request, timeout=60) as response:
    archive = response.read(32 * 1024 * 1024 + 1)
if len(archive) > 32 * 1024 * 1024:
    raise SystemExit('Source archive exceeds the size limit.')
with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
    total = 0
    for member in source:
        name = PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or not (member.isdir() or member.isfile()):
            raise SystemExit('Unsupported path in source archive.')
        total += member.size
        if total > 128 * 1024 * 1024:
            raise SystemExit('Extracted source exceeds the size limit.')
        destination = root.joinpath(*name.parts)
        if member.isdir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as stream, destination.open('wb') as target:
                shutil.copyfileobj(stream, target)
projects = list(root.glob('*/appearance.py'))
if len(projects) != 1:
    raise SystemExit('Invalid source archive layout.')
subprocess.run([sys.executable, str(projects[0]), sys.argv[2], *sys.argv[3:]], check=True)
PYINSTALL
