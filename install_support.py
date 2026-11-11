"""One-command setup; only supported package managers install missing packages."""
import os
from pathlib import Path
import shutil
import subprocess


def distribution():
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip('"')
    return {values.get('ID', ''), *values.get('ID_LIKE', '').split()}


def package_installed(manager, name):
    if manager == 'apt':
        result = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Abbrev}', name],
                                text=True, capture_output=True)
        return result.returncode == 0 and result.stdout.startswith('ii')
    return subprocess.run(['pacman', '-Qq', name], capture_output=True).returncode == 0


def dependency_plan(app):
    missing = [name for name in app.requirements() if not shutil.which(name)]
    distro = distribution()
    manager = 'apt' if distro & {'debian', 'ubuntu'} else 'pacman' if 'arch' in distro else None
    if manager is None:
        if missing:
            raise RuntimeError('请先安装依赖：' + ', '.join(missing) + '；自动安装仅支持 Debian/Ubuntu 和 Arch。')
        return None, []
    mapping = {'apt': {'gsettings': 'libglib2.0-bin', 'glib-compile-schemas': 'libglib2.0-bin',
                       'gtk-update-icon-cache': 'libgtk-3-bin', 'sassc': 'sassc', 'bash': 'bash'},
               'pacman': {'gsettings': 'glib2', 'glib-compile-schemas': 'glib2',
                          'gtk-update-icon-cache': 'gtk3', 'sassc': 'sassc', 'bash': 'bash'}}
    packages = set()
    for command in missing:
        if command not in mapping[manager]:
            raise RuntimeError('请先安装并登录 GNOME 桌面；缺少命令：' + command)
        packages.add(mapping[manager][command])
    if 'theme' in app.selected and app.profile['theme']['adapter'] == 'orchis':
        optional = ['gtk2-engines-murrine', 'gnome-themes-extra'] if manager == 'apt' else ['gtk-engine-murrine', 'gnome-themes-extra']
        packages.update(name for name in optional if not package_installed(manager, name))
    return manager, sorted(packages)


def package_command(manager, packages):
    if manager == 'apt':
        return ['sudo', 'apt-get', 'install', '-y', '--no-install-recommends', *packages]
    return ['sudo', 'pacman', '-S', '--needed', '--noconfirm', *packages]


def setup(app, skip_deps=False, dry_run=False):
    if os.geteuid() == 0:
        raise RuntimeError('请以 GNOME 桌面用户运行安装器，不要 sudo。')
    app.verify()
    major = app.gnome_major()
    if 'theme' in app.selected and app.profile['theme'].get('shell'):
        app.shell_style(major)
        app.shell_extension(major)
    manager, packages = dependency_plan(app) if not skip_deps else (None, [])
    if dry_run:
        print('仅预览，不安装依赖、不应用外观。')
        print('组件：' + ', '.join(app.selected))
        print('需要的系统包：' + (', '.join(packages) if packages else '无'))
        return
    bus = Path('/run/user') / str(os.getuid()) / 'bus'
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        if not bus.exists():
            raise RuntimeError('请在已登录的 GNOME 桌面会话中运行。')
        os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(bus)
        os.environ.setdefault('XDG_RUNTIME_DIR', str(bus.parent))
    if packages:
        if not shutil.which('sudo'):
            raise RuntimeError('需要管理员安装这些依赖：' + ', '.join(packages))
        print('安装缺少的依赖：' + ', '.join(packages), flush=True)
        # sudo reads the controlling terminal; passwords are never read here.
        subprocess.run(package_command(manager, packages), check=True)
    app.apply()
    print('安装完成。首次安装 User Themes 后请注销并重新登录；已有应用可重新打开。')
