#!/usr/bin/env python3
"""Portable per-user GNOME appearance tool. The default command is read-only plan."""
import argparse
import ast
import configparser
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
USER_THEME = 'user-theme@gnome-shell-extensions.gcampax.github.com'
PARTS = ('theme', 'icons', 'cursor')
ACTIVITIES_ICONS = ('auto', 'default', 'apple', 'simple', 'gnome', 'ubuntu', 'arch',
                    'manjaro', 'fedora', 'debian', 'void', 'opensuse', 'popos', 'mxlinux',
                    'zorin', 'endeavouros', 'tux', 'nixos', 'gentoo', 'budgie', 'solus', 'kali')


class Error(RuntimeError):
    pass


def command(argv, env=None, check=True):
    result = subprocess.run(list(map(str, argv)), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise Error(f'{argv[0]} 失败：{result.stderr.strip()[-1500:]}')
    return result


def digest(file):
    h = hashlib.sha256()
    with Path(file).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def bounded(root, relative):
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts:
        raise Error('相对路径越界：' + str(relative))
    try:
        path = (root / part).resolve()
    except RuntimeError:
        raise Error('资源存在循环符号链接：' + str(relative))
    if os.path.commonpath([str(path), str(root.resolve())]) != str(root.resolve()):
        raise Error('资源链接越界：' + str(relative))
    return path


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'\w[\w .+-]*', name) or name in ('.', '..'):
        raise Error('主题名称不能包含路径或控制字符')
    return name


def extract_tar(archive, destination):
    """Extract regular files first, then internal links. Works on Python 3.9+."""
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise Error('安全解包要求空目录')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if len(members) > 100000 or sum(m.size for m in members) > 1024 ** 3:
            raise Error('归档大小异常')
        names = set()
        for m in members:
            if not m.name or m.name.startswith('/') or '..' in PurePosixPath(m.name).parts:
                raise Error('归档路径越界')
            if m.name in names or not (m.isdir() or m.isfile() or m.issym() or m.islnk()):
                raise Error('重复路径或不支持的归档类型')
            names.add(m.name)
            if m.issym() or m.islnk():
                if Path(m.linkname).is_absolute():
                    raise Error('归档不允许绝对链接')
                parent = Path(m.name).parent if m.issym() else Path()
                normalized = Path(os.path.normpath(str(parent / m.linkname)))
                if '..' in normalized.parts or normalized.is_absolute():
                    raise Error('归档链接越界')
        # No links exist yet: regular-file writes cannot traverse archive links.
        for m in members:
            path = bounded(destination, m.name)
            if m.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as f:
                    shutil.copyfileobj(tar.extractfile(m), f)
                path.chmod(0o755 if m.mode & 0o111 else 0o644)
        for m in members:
            if not m.islnk():
                continue
            path, target = bounded(destination, m.name), bounded(destination, m.linkname)
            if not target.is_file() or target.is_symlink():
                raise Error('无效硬链接')
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, path)
        for m in members:
            if not m.issym():
                continue
            path = destination / m.name
            parent = path.parent
            if any(p.is_symlink() for p in [parent, *parent.parents] if p != destination.parent):
                raise Error('链接父目录不可为符号链接')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or path.is_symlink():
                raise Error('链接覆盖其他归档成员')
            path.symlink_to(m.linkname)
        for m in members:
            if m.issym():
                bounded(destination, m.name)


def extract_zip(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > 32 * 1024 ** 2:
            raise Error('扩展归档大小异常')
        for item in z.infolist():
            bounded(destination, item.filename)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise Error('扩展 ZIP 不允许符号链接')
        z.extractall(destination)


def fingerprint(path):
    if path.is_symlink():
        return 'link:' + str(path.readlink())
    if not path.exists():
        return None
    if path.is_file():
        return 'file:' + digest(path)
    h = hashlib.sha256()
    for f in sorted(path.rglob('*')):
        name = str(f.relative_to(path))
        if f.is_symlink():
            value = 'link:' + str(f.readlink())
        elif f.is_file():
            value = 'file:' + digest(f)
        else:
            value = 'directory'
        h.update(name.encode() + b'\0' + value.encode() + b'\0')
    return 'tree:' + h.hexdigest()


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(source.readlink())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


def validate_resource_links(resources):
    """Reject empty graphics and links that will not survive relocation."""
    roots = [(source.resolve(), target) for source, target, _scope in resources]
    for source, target, _scope in resources:
        for link in source.rglob('*'):
            if (link.is_file() and link.suffix.lower() in ('.svg', '.png', '.xpm', '.cur')
                    and link.stat().st_size == 0):
                raise Error('资源包含空图像文件：' + str(link))
            if not link.is_symlink():
                continue
            if link.readlink().is_absolute():
                raise Error('资源包含不可迁移的绝对链接：' + str(link))
            try:
                resolved = link.resolve(strict=True)
            except (OSError, RuntimeError):
                raise Error('资源包含损坏或循环链接：' + str(link))
            immediate = Path(os.path.normpath(str(link.parent / link.readlink())))
            installed = Path(os.path.normpath(str(target / link.relative_to(source).parent / link.readlink())))
            # Preserve the immediate edge, not just its final target: aliases
            # commonly point to other valid aliases in the same theme.
            if (not any(resolved.is_relative_to(root) for root, _destination in roots) or
                    not any(immediate.is_relative_to(root) and
                            installed == destination / immediate.relative_to(root)
                            for root, destination in roots)):
                raise Error('资源链接依赖未随包安装的目录：' + str(link))


def recover_tela_links(source):
    """Recover zero-byte aliases using targets from the matching upstream tree."""
    recovery = json.loads((ROOT / 'resources/tela-link-recovery.json').read_text())
    count = 0
    for relative, target in recovery['links'].items():
        name = Path(relative)
        if name.is_absolute() or '..' in name.parts or Path(target).is_absolute():
            raise Error('图标恢复清单路径不合法')
        bounded(source, name.parent)
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != 0:
            continue
        normalized = Path(os.path.normpath(str(name.parent / target)))
        if normalized.is_absolute() or '..' in normalized.parts:
            raise Error('图标恢复清单链接越界')
        path.unlink()
        path.symlink_to(target)
        count += 1
    if count:
        print('已从固定官方源码恢复图标别名：' + str(count), flush=True)


def prepare_tela(directory):
    """Repair known upstream output defects, then rebuild its icon cache."""
    alias = directory / 'symbolic/apps/xsi-addon-symbolic.svg'
    if alias.is_symlink() and not alias.exists():
        replacement = directory / 'symbolic/mimetypes/application-x-addon-symbolic.svg'
        if alias.readlink() == Path('application-x-addon-symbolic.svg') and replacement.is_file():
            alias.unlink()
            alias.symlink_to('../mimetypes/application-x-addon-symbolic.svg')
    index = configparser.ConfigParser(interpolation=None)
    index.read(directory / 'index.theme')
    declared = [value.strip() for key in ('Directories', 'ScaledDirectories')
                for value in index.get('Icon Theme', key, fallback='').split(',') if value.strip()]
    for name in declared:
        # The pinned upstream installer creates @2x aliases but omits the @3x
        # roots advertised by index.theme. Reuse the same vector icon sources.
        first = Path(name).parts[0]
        if first.endswith('@3x'):
            link = directory / first
            original = directory / first[:-3]
            if not link.exists() and not link.is_symlink() and original.is_dir():
                link.symlink_to(original.name)
        if not (directory / name).is_dir():
            raise Error('图标主题声明的目录缺失：' + str(directory / name))
    command(['gtk-update-icon-cache', '--force', str(directory)])
    command(['gtk-update-icon-cache', '--validate', str(directory)])


def normalize_gtk4_named_colors(directory):
    # Modern GTK supports custom properties in widget declarations, but the
    # legacy @define-color parser does not accept var(). Orchis exports these
    # two named colors from its dynamic accent; use its existing named accent
    # for the legacy API, retaining custom properties everywhere else.
    pattern = r'(@define-color\s+theme_(?:unfocused_)?selected_bg_color\s+)var\(--accent-bg-color\)(\s*;)'
    for file in (directory / 'gtk-4.0').glob('*.css'):
        text = file.read_text()
        fixed = re.sub(pattern, r'\1@accent_color\2', text)
        if text != fixed:
            file.write_text(fixed)


class Settings:
    def __init__(self, schema, schema_dir=None):
        self.schema = schema
        self.env = os.environ.copy()
        if schema_dir:
            self.env['GSETTINGS_SCHEMA_DIR'] = str(schema_dir)
        self.schema_dir = str(schema_dir) if schema_dir else None

    def get(self, key):
        return command(['gsettings', 'get', self.schema, key], env=self.env).stdout.strip()

    def set(self, key, value):
        command(['gsettings', 'set', self.schema, key, value], env=self.env)


class Journal:
    """Save originals before changing anything; restore in reverse order."""
    def __init__(self, state, run=None):
        self.root = state / 'runs' / run if run else state / 'runs' / (
            datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        self.state = state
        self.file = self.root / 'journal.json'
        self.data = json.loads(self.file.read_text()) if run else {'version': 1, 'status': 'applying', 'items': []}

    def save(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        temp = self.root / 'journal.tmp'
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + '\n')
        temp.chmod(0o600)
        temp.replace(self.file)

    def install(self, source, target, scope):
        before = fingerprint(target)
        after = fingerprint(source)
        if before == after:
            return
        index = len(self.data['items'])
        backup = self.root / 'before' / str(index)
        if before is not None:
            copy(target, backup)
        entry = dict(type='path', scope=scope, target=str(target), before=before, after=after,
                     backup=str(backup.relative_to(self.root)) if before is not None else None)
        self.data['items'].append(entry)
        self.save()
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = target.parent / ('.gnome-appearance-' + uuid.uuid4().hex)
        try:
            copy(source, stage)
            remove(target)
            stage.replace(target)
        finally:
            remove(stage)

    def set(self, settings, key, value, scope):
        before = settings.get(key)
        if before == value:
            return
        self.data['items'].append(dict(type='setting', scope=scope, schema=settings.schema,
            schema_dir=settings.schema_dir, key=key, before=before, after=value))
        self.save()
        settings.set(key, value)

    def finish(self):
        if not self.data['items']:
            return False
        self.data['status'] = 'applied'
        self.save()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'latest').write_text(self.root.name + '\n')
        return True

    def restore(self, scopes=None, force=False):
        items = [i for i in reversed(self.data['items']) if not i.get('restored') and
                 (not scopes or i['scope'] in scopes)]
        # Preflight all conflicts, so an ordinary conflict causes zero restore writes.
        for item in items:
            now = (fingerprint(Path(item['target'])) if item['type'] == 'path' else
                   Settings(item['schema'], item['schema_dir']).get(item['key']))
            if not force and now not in (item['after'], item['before']):
                raise Error('恢复前发现后续修改；未覆盖。检查备份或明确使用 --force。')
        for item in items:
            if item['type'] == 'path':
                target = Path(item['target'])
                remove(target)
                if item['backup']:
                    copy(bounded(self.root, item['backup']), target)
            else:
                Settings(item['schema'], item['schema_dir']).set(item['key'], item['before'])
            item['restored'] = True
            self.save()
        self.data['status'] = 'restored' if all(i.get('restored') for i in self.data['items']) else 'partly-restored'
        self.save()


def load_profile(path):
    data = json.loads(path.read_text())
    for part in PARTS:
        if not isinstance(data, dict) or not isinstance(data.get(part), dict):
            raise Error('配置缺少组件：' + part)
        config = data[part]
        if not all(k in config for k in ('name', 'source', 'adapter')):
            raise Error('组件必须包含 name、source 和 adapter：' + part)
        config.setdefault('enabled', True)
        safe_name(config['name'])
        if not isinstance(config['source'], str) or config['source'] in ('', '.') or Path(config['source']).is_absolute() or '..' in Path(config['source']).parts:
            raise Error('source 必须是资源根目录内的相对目录')
        allowed = ('directory', 'orchis') if part == 'theme' else ('directory', 'tela') if part == 'icons' else ('directory',)
        if config['adapter'] not in allowed:
            raise Error('不支持的资源适配器：' + config['adapter'])
    if not isinstance(data['cursor']['size'], int) or not 8 <= data['cursor']['size'] <= 128:
        raise Error('指针尺寸须为 8..128 的整数')
    theme = data['theme']
    if theme['adapter'] == 'orchis':
        if theme.get('variant') not in ('default', 'purple', 'pink', 'red', 'orange', 'yellow', 'green', 'teal', 'grey'):
            raise Error('Orchis 配色无效')
        if theme.get('color') not in ('standard', 'light', 'dark') or theme.get('size') not in ('standard', 'compact'):
            raise Error('Orchis 明暗或尺寸无效')
        if 'activities_icon' in theme and theme['activities_icon'] not in ACTIVITIES_ICONS:
            raise Error('Orchis 活动按钮图标无效')
    if data['icons']['adapter'] == 'tela':
        safe_name(data['icons']['base_name'])
        if data['icons'].get('variant') not in ('standard', 'black', 'blue', 'brown', 'green', 'grey', 'orange', 'pink',
                                               'purple', 'red', 'yellow', 'manjaro', 'ubuntu', 'dracula', 'nord'):
            raise Error('Tela 配色无效')
    return data


def xdg(name, fallback):
    value = Path(os.environ.get(name) or fallback).expanduser()
    return value if value.is_absolute() else Path(fallback)


def version_major(value):
    if not re.fullmatch(r'[1-9][0-9]*(?:\.[0-9]+){0,2}', str(value)):
        raise Error('GNOME 版本应为 51、51.1 等数字版本')
    return int(str(value).split('.')[0])


def activities_icon(theme):
    icon = theme.get('activities_icon')
    if icon != 'auto':
        return icon
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    distro = release.get('ID', '').strip('"\'')
    return distro if distro in ('debian', 'arch') else 'default'


def build_shell48(orchis, output, theme):
    """Compile the existing Orchis 48 stylesheet without running install.sh."""
    if not shutil.which('sassc'):
        raise Error('生成 Shell 48 样式需要 sassc；项目不会自动安装。')
    with tempfile.TemporaryDirectory(prefix='orchis-shell48-') as d:
        work = Path(d)
        source = work / 'orchis'
        copy(orchis / 'src', source)
        tweaks = (source / '_sass/_tweaks.scss').read_text()
        tweaks = tweaks.replace("$theme: 'default';", "$theme: '" + theme['variant'] + "';")
        tweaks = tweaks.replace("$gnome_version: 'old';", "$gnome_version: 'new';")
        icon = activities_icon(theme)
        if icon:
            tweaks = tweaks.replace("$activities: 'normal';", "$activities: 'icon';")
        if theme['variant'] != 'default':
            tweaks = tweaks.replace("$accent_type: 'default';", "$accent_type: 'fixed';")
        (source / '_sass/_tweaks-temp.scss').write_text(tweaks)
        dark = '-Dark' if theme['color'] == 'dark' else ''
        compact = '-Compact' if theme['size'] == 'compact' else ''
        shell = source / 'gnome-shell'
        result = work / 'result'
        result.mkdir()
        command(['sassc', '-t', 'expanded', shell / 'shell-48-0' /
                 ('gnome-shell' + dark + compact + '.scss'), result / 'gnome-shell.css'])
        copy(shell / 'common-assets', result / 'assets')
        for svg in (shell / ('assets' + dark)).glob('*.svg'):
            shutil.copy2(svg, result / 'assets' / svg.name)
        palette = shell / ('theme' + ('-' + theme['variant'].title() if theme['variant'] != 'default' else ''))
        for name in ['more-results', 'toggle-on']:
            shutil.copy2(palette / (name + dark + '.svg'), result / 'assets' / (name + '.svg'))
        copy(shell / ('activities/activities-' + (icon or 'default') + '.svg'), result / 'assets/activities.svg')
        copy(shell / 'pad-osd.css', result / 'pad-osd.css')
        copy(orchis / 'COPYING', result / 'COPYING-Orchis')
        for name in ['no-events.svg', 'process-working.svg', 'no-notifications.svg']:
            (result / name).symlink_to(Path('assets') / name)
        css = (result / 'gnome-shell.css').read_text()
        for asset in re.findall(r'url\([\"\']?([^\"\')]+)[\"\']?\)', css):
            if not asset.startswith('resource:///') and not bounded(result, asset).is_file():
                raise Error('Shell 48 输出缺少资源：' + asset)
        remove(output)
        copy(result, output)


def build_shell51(orchis, output, theme):
    """Compile project resources only. Never invoke the upstream installer or settings."""
    if not shutil.which('sassc'):
        raise Error('生成 Shell 51 样式需要 sassc；项目不会自动安装。')
    with tempfile.TemporaryDirectory(prefix='orchis-shell51-') as d:
        work = Path(d)
        source = work / 'orchis'
        copy(orchis / 'src/_sass', source)
        # Build from the profile, independent of leftovers in an external source tree.
        tweaks = (source / '_tweaks.scss').read_text()
        tweaks = tweaks.replace("$theme: 'default';", "$theme: '" + theme['variant'] + "';")
        icon = activities_icon(theme)
        if icon:
            tweaks = tweaks.replace("$activities: 'normal';", "$activities: 'icon';")
        (source / '_tweaks-temp.scss').write_text(tweaks)
        port = work / 'port'
        copy(ROOT / 'resources/shell-51', port)
        # These SVGs are bundled into the generated theme, not resolved from the host Shell.
        for f in port.rglob('*.scss'):
            text = f.read_text().replace('resource:///org/gnome/shell/theme/', 'assets/')
            # Upstream Orchis follows GNOME accent for its default variant on 47+.
            # Explicit purple/red/etc variants retain their selected theme color.
            if theme['variant'] != 'default':
                text = text.replace('-st-accent-fg-color', '$primary-text')
                text = text.replace('-st-accent-color', '$primary')
            f.write_text(text)
        variant = 'dark' if theme['color'] == 'dark' else 'light'
        compact = 'true' if theme['size'] == 'compact' else 'false'
        entry = work / 'entry.scss'
        entry.write_text("$variant: '" + variant + "';\n$topbar: 'dark';\n$compact: '" + compact +
                         "';\n$contrast: 'normal';\n@import " + json.dumps(str(port / 'orchis.scss')) + ';\n')
        result = work / 'result'
        result.mkdir()
        command(['sassc', '-t', 'expanded', '-I', source, entry, result / 'gnome-shell.css'])
        assets = result / 'assets'
        assets.mkdir()
        for svg in (port / 'vendor').glob('*.svg'):
            copy(svg, assets / svg.name)
        if icon:
            copy(orchis / ('src/gnome-shell/activities/activities-' + icon + '.svg'), assets / 'activities.svg')
        copy(port / 'vendor/pad-osd.css', result / 'pad-osd.css')
        copy(port / 'vendor/COPYING', result / 'COPYING-GNOME')
        copy(ROOT / 'resources/licenses/LGPL-2.1.txt', result / 'COPYING-LGPL-2.1')
        copy(port / 'upstream.lock.json', result / 'upstream.lock.json')
        (result / 'NOTICE').write_text(
            'GNOME Shell 51.1 widgets with local Orchis palette, geometry and appearance port.\n'
            'See the project resources/shell-51/ for corresponding Sass source and notices.\n')
        css = (result / 'gnome-shell.css').read_text()
        for asset in re.findall(r'url\([\"\']?([^\"\')]+)[\"\']?\)', css):
            if not bounded(result, asset).is_file():
                raise Error('Shell 51 输出缺少资源：' + asset)
        for selector in ['.qr-code', '.quick-settings', '.datemenu-calendar-column', '.message-notification-group']:
            if selector not in css:
                raise Error('Shell 51 输出缺少原生控件规则：' + selector)
        remove(output)
        copy(result, output)


def build_commands(profile, selected, source, output):
    result = []
    theme, icons = profile['theme'], profile['icons']
    if 'theme' in selected and theme['adapter'] == 'orchis':
        # Let upstream detect the actual Shell version. Its explicit --shell flag
        # omits another GNOME flag required by recent accent styles.
        argv = ['bash', 'install.sh', '-d', str(output / 'themes'),
                '-t', theme['variant'], '-c', theme['color'], '-s', theme['size']]
        icon = activities_icon(theme)
        if icon:
            argv += ['-i', icon]
        result.append((bounded(source, theme['source']), argv))
    if 'icons' in selected and icons['adapter'] == 'tela':
        result.append((bounded(source, icons['source']), ['bash', 'install.sh', '-d', str(output / 'icons'), icons['variant']]))
    return result


class Appearance:
    def __init__(self, profile, selected, sources=None):
        self.profile, self.selected, self.sources = profile, selected, sources
        self.home = Path.home()
        self.data = xdg('XDG_DATA_HOME', self.home / '.local/share')
        self.config = xdg('XDG_CONFIG_HOME', self.home / '.config')
        self.state = xdg('XDG_STATE_HOME', self.home / '.local/state') / 'gnome-appearance'
        self.manifest = json.loads((ROOT / 'resources/manifest.json').read_text())

    def gnome_major(self):
        if not shutil.which('gnome-shell'):
            return None
        value = command(['gnome-shell', '--version']).stdout
        match = re.search(r'\b(\d+)\.', value)
        return int(match[1]) if match else None

    def verify(self):
        for name, expected in self.manifest['files'].items():
            file = bounded(ROOT / 'resources', name)
            if not file.is_file() or digest(file) != expected:
                raise Error('资源缺失或 SHA256 校验失败：' + name)
        for ext in self.manifest['extensions']:
            with zipfile.ZipFile(ROOT / 'resources' / ext['file']) as z:
                metadata = json.loads(z.read('metadata.json'))
            if metadata['uuid'] != USER_THEME or metadata['shell-version'] != ext['shell_versions']:
                raise Error('User Themes 元数据与资源清单不符：' + ext['file'])

    def shell_style(self, major):
        if self.profile['theme']['adapter'] != 'orchis':
            return {'provider': 'directory', 'baseline': None}
        if major and major <= 48:
            return {'provider': 'orchis-upstream', 'baseline': str(major)}
        port = self.manifest['shell_ports'].get(str(major))
        if port:
            return {'provider': 'orchis-local-port', 'baseline': port['baseline'], 'source': port['source']}
        raise Error('尚未提供 GNOME ' + str(major) + ' 的 Orchis Shell 适配；可 --no-shell 仅设置 GTK 主题。')

    def requirements(self):
        required = ['gsettings']
        if 'theme' in self.selected and self.profile['theme']['adapter'] == 'orchis':
            required += ['bash', 'sassc', 'gnome-shell']
        if 'theme' in self.selected and self.profile['theme'].get('shell'):
            required += ['glib-compile-schemas', 'gnome-shell']
        if 'icons' in self.selected and self.profile['icons']['adapter'] == 'tela':
            required += ['bash', 'gtk-update-icon-cache']
        return sorted(set(required))

    def shell_extension(self, major):
        user_base = self.data / 'gnome-shell/extensions'
        for base in [user_base, Path('/usr/share/gnome-shell/extensions')]:
            # A stale user copy shadows the system extension in GNOME Shell.
            if base != user_base and (user_base / USER_THEME).exists():
                continue
            folder = base / USER_THEME
            metadata = folder / 'metadata.json'
            if metadata.is_file():
                versions = json.loads(metadata.read_text()).get('shell-version', [])
                if str(major) in versions:
                    return folder, None
        for ext in self.manifest['extensions']:
            if str(major) in ext['shell_versions']:
                return self.data / 'gnome-shell/extensions' / USER_THEME, ext['file']
        raise Error('需要当前 GNOME 版本兼容的 User Themes 扩展；可使用 --no-shell 仅设置 GTK 主题。')

    def check(self, applying=False, target_version=None):
        if applying and target_version is not None:
            raise Error('实际应用不能指定其他机器的 GNOME 版本')
        self.verify()
        missing = [name for name in self.requirements() if not shutil.which(name)]
        if missing:
            raise Error('缺少依赖：' + ', '.join(missing) + '。见 README 中各发行版依赖说明；脚本不自动安装。')
        major = version_major(target_version) if target_version is not None else self.gnome_major()
        if 'theme' in self.selected and self.profile['theme'].get('shell'):
            if not major:
                raise Error('未检测到 GNOME Shell；可 --no-shell 只设置 GTK 主题。')
            self.shell_style(major)
            self.shell_extension(major)
        if applying:
            if os.geteuid() == 0:
                raise Error('请以普通桌面用户运行，不要 sudo。')
            if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
                raise Error('请在目标用户的 GNOME 登录会话中运行。')
            if self.data == self.home or self.config == self.home:
                raise Error('XDG 数据/配置路径不能等于 HOME')
            interface = Settings('org.gnome.desktop.interface')
            interface.get('gtk-theme')
            if 'theme' in self.selected and self.profile['theme'].get('shell'):
                if Settings('org.gnome.shell').get('disable-user-extensions') == 'true':
                    raise Error('GNOME 当前禁用了全部用户扩展，请先自行启用 User Themes 或 --no-shell。')
        return major

    def plan(self, major=None, version=None):
        result = {'mode': 'plan', 'project_target': self.manifest['target_gnome'],
                  'gnome_major': major, 'gnome_version': version or (str(major) if major else None),
                  'selected': self.selected, 'actions': []}
        for part in self.selected:
            c = self.profile[part]
            action = {'part': part, 'adapter': c['adapter'], 'name': c['name']}
            if part == 'theme':
                action.update(shell=bool(c.get('shell')), gtk4=bool(c.get('gtk4')), destination=str(self.home / '.themes' / c['name']))
                if c['adapter'] == 'orchis':
                    action['activities_icon'] = activities_icon(c)
                if c.get('shell'):
                    try:
                        action['shell_style'] = self.shell_style(major)
                    except Error as error:
                        action['shell_unavailable'] = str(error)
            else:
                action['destination'] = str(self.data / 'icons' / c['name'])
            result['actions'].append(action)
        return result

    def build(self, temp):
        source, output, home = temp / 'source', temp / 'output', temp / 'build-home'
        source.mkdir()
        output.mkdir()
        home.mkdir()
        if self.sources:
            for part in self.selected:
                relative = self.profile[part]['source']
                target = bounded(source, relative)
                if not target.exists():
                    copy(bounded(self.sources.resolve(), relative), target)
        else:
            extract_tar(ROOT / 'resources/sources.tar.xz', source)
        if 'icons' in self.selected and self.profile['icons']['adapter'] == 'tela':
            recover_tela_links(bounded(source, self.profile['icons']['source']))
        env = os.environ.copy()
        env.update(HOME=str(home), XDG_DATA_HOME=str(home / '.local/share'), XDG_CONFIG_HOME=str(home / '.config'),
                   XDG_CACHE_HOME=str(home / '.cache'), DESKTOP_SESSION='gnome')
        for key in ['DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY', 'SUDO_USER', 'BASH_ENV', 'ENV']:
            env.pop(key, None)
        bin_dir = temp / 'bin'
        bin_dir.mkdir()
        wrappers = {'which': '#!/bin/sh\ncommand -v "$@"\n'}
        for name in ['sudo', 'apt', 'apt-get', 'dnf', 'yum', 'pacman', 'zypper',
                     'gsettings', 'dconf', 'dbus-launch', 'notify-send', 'xfce4-panel']:
            wrappers[name] = '#!/bin/sh\necho "Not available during resource build" >&2\nexit 99\n'
        for name, text in wrappers.items():
            f = bin_dir / name
            f.write_text(text)
            f.chmod(0o755)
        env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
        for cwd, argv in build_commands(self.profile, self.selected, source, output):
            print('生成：' + str(cwd.name), flush=True)
            result = subprocess.run(
                argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if result.returncode:
                raise Error('上游生成失败：' + result.stdout[-2000:])
        theme = self.profile['theme']
        if ('theme' in self.selected and theme['adapter'] == 'orchis' and theme.get('shell')
                and self.gnome_major() == 51):
            # Replace the intermediate upstream 48 stylesheet with the actual 51 port.
            # GTK output stays from the pinned Orchis source. No old Shell assets remain.
            build_shell51(bounded(source, theme['source']),
                          output / 'themes' / theme['name'] / 'gnome-shell', theme)
        resources = []
        for part in self.selected:
            c = self.profile[part]
            directory = bounded(source, c['source']) if c['adapter'] == 'directory' else output / ('themes' if part == 'theme' else 'icons') / c['name']
            if part == 'theme':
                if c['adapter'] == 'orchis':
                    normalize_gtk4_named_colors(directory)
                required = ['gtk-3.0/gtk.css'] + (['gnome-shell/gnome-shell.css'] if c.get('shell') else [])
                if c.get('gtk4'):
                    required += ['gtk-4.0/gtk.css', 'gtk-4.0/gtk-dark.css']
                for name in required:
                    f = directory / name
                    if not f.is_file() or not f.stat().st_size:
                        raise Error('主题输出缺失或为空：' + name)
                target = self.home / '.themes' / c['name']
            else:
                if not (directory / 'index.theme').is_file():
                    raise Error('缺少 index.theme：' + c['name'])
                target = self.data / 'icons' / c['name']
            if part == 'icons' and c['adapter'] == 'tela':
                base = output / 'icons' / c['base_name']
                if not (base / 'index.theme').is_file():
                    raise Error('缺少 Tela 基础图标')
                if base != directory:
                    resources.append((base, self.data / 'icons' / c['base_name'], part))
            if part == 'cursor' and (directory / 'cursors/left_ptr').read_bytes()[:4] != b'Xcur':
                raise Error('无效的 Xcursor 指针主题')
            resources.append((directory, target, part))
        # Prepare the complete Tela dependency family before caching/validating.
        # Never silently discard a broken alias: fail before desktop mutation.
        if 'icons' in self.selected and self.profile['icons']['adapter'] == 'tela':
            for directory, _target, part in resources:
                if part == 'icons':
                    prepare_tela(directory)
        validate_resource_links(resources)
        return resources

    def apply(self):
        major = self.check(applying=True)
        with tempfile.TemporaryDirectory(prefix='gnome-appearance-') as d:
            temp = Path(d)
            resources = self.build(temp)
            # Build/validate everything before creating the first desktop mutation.
            journal = Journal(self.state)
            try:
                theme = self.profile['theme']
                if 'theme' in self.selected and theme.get('shell'):
                    folder, archive = self.shell_extension(major)
                    if archive:
                        extension = temp / 'extension'
                        extract_zip(ROOT / 'resources' / archive, extension)
                        schemas = extension / 'schemas'
                        if schemas.is_dir():
                            command(['glib-compile-schemas', str(schemas)])
                        journal.install(extension, folder, 'theme')
                    shell = Settings('org.gnome.shell')
                    enabled = ast.literal_eval(shell.get('enabled-extensions').replace('@as ', '', 1))
                    if USER_THEME not in enabled:
                        journal.set(shell, 'enabled-extensions', repr(enabled + [USER_THEME]), 'theme')
                    disabled = ast.literal_eval(shell.get('disabled-extensions').replace('@as ', '', 1))
                    if USER_THEME in disabled:
                        journal.set(shell, 'disabled-extensions', repr([e for e in disabled if e != USER_THEME]), 'theme')
                    schema_dir = folder / 'schemas'
                    user_theme = Settings('org.gnome.shell.extensions.user-theme', schema_dir if schema_dir.is_dir() else None)
                for source, target, scope in resources:
                    journal.install(source, target, scope)
                # Check the actual installed graph before selecting any theme.
                validate_resource_links([(target, target, scope) for _source, target, scope in resources])
                interface = Settings('org.gnome.desktop.interface')
                for part, key in [('theme', 'gtk-theme'), ('icons', 'icon-theme'), ('cursor', 'cursor-theme')]:
                    if part in self.selected:
                        journal.set(interface, key, repr(self.profile[part]['name']), part)
                if 'cursor' in self.selected:
                    journal.set(interface, 'cursor-size', str(self.profile['cursor']['size']), 'cursor')
                if 'theme' in self.selected:
                    if theme.get('shell'):
                        journal.set(user_theme, 'name', repr(theme['name']), 'theme')
                    if theme.get('gtk4'):
                        directory = self.home / '.themes' / theme['name'] / 'gtk-4.0'
                        for name in ['gtk.css', 'gtk-dark.css', 'assets']:
                            link = temp / ('gtk4-' + name)
                            link.symlink_to(directory / name)
                            journal.install(link, self.config / 'gtk-4.0' / name, 'gtk4')
                changed = journal.finish()
            except Exception:
                if journal.data['items']:
                    try:
                        journal.restore(force=True)
                    except Exception as rollback:
                        print('自动恢复未完成，请检查备份：' + str(journal.root) + '\n' + str(rollback), file=sys.stderr)
                raise
        if changed:
            print('外观已应用。备份编号：' + journal.root.name)
            print('新装 Shell 扩展或部分程序可能需要注销后重新登录。')
        else:
            print('当前已是所选外观，无需修改；保留上一次有效恢复点。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='plan', choices=['plan', 'check', 'status', 'apply', 'restore', 'build-shell', 'setup'])
    parser.add_argument('--dry-run', action='store_true', help='setup：只预览安装及依赖计划')
    parser.add_argument('--skip-deps', action='store_true', help='setup：不自动安装系统依赖')
    parser.add_argument('--config', type=Path, default=ROOT / 'profile.json')
    parser.add_argument('--only', help='theme,icons,cursor；restore 还可单选 gtk4')
    parser.add_argument('--sources', type=Path, help='使用外部资源根目录；会复制到临时目录处理')
    parser.add_argument('--no-shell', action='store_true', help='只设置 GTK 主题，不设置 GNOME Shell')
    parser.add_argument('--no-gtk4', action='store_true', help='本次不改 GTK4 链接；已有链接不会被删除')
    parser.add_argument('--gnome-version', help='plan/check/build-shell 的目标版本，如 51.1；不能用于 apply')
    parser.add_argument('--run', help='restore 时指定备份编号，默认最近一次成功应用')
    parser.add_argument('--force', action='store_true', help='restore 时覆盖应用后发生的修改')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if (args.dry_run or args.skip_deps) and args.action != 'setup':
        raise Error('--dry-run 和 --skip-deps 只适用于一键安装 setup')
    if args.gnome_version is not None:
        version_major(args.gnome_version)
        if args.action not in ('plan', 'check', 'build-shell'):
            raise Error('--gnome-version 仅供预览和项目检查，不伪装实际安装环境')
    if args.force and args.action != 'restore':
        raise Error('--force 只用于恢复')
    if args.action == 'restore':
        if os.geteuid() == 0 or not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            raise Error('恢复请在普通用户的 GNOME 会话中运行，不要 sudo。')
        scopes = [p.strip() for p in args.only.split(',')] if args.only else None
        if scopes is not None and (not scopes or set(scopes) - set(PARTS + ('gtk4',))):
            raise Error('恢复组件名称无效')
        state = xdg('XDG_STATE_HOME', Path.home() / '.local/state') / 'gnome-appearance'
        latest = state / 'latest'
        if not args.run and not latest.exists():
            raise Error('没有可恢复的应用记录')
        run = args.run or latest.read_text().strip()
        if not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{8}', run):
            raise Error('备份编号无效')
        Journal(state, run).restore(scopes, args.force)
        print('已恢复：' + run)
        return 0
    profile = load_profile(args.config)
    selected = [x.strip() for x in args.only.split(',')] if args.only else [p for p in PARTS if profile[p]['enabled']]
    allowed = set(PARTS) | ({'gtk4'} if args.action == 'restore' else set())
    if not selected or set(selected) - allowed:
        raise Error('--only 组件名称无效')
    selected = list(dict.fromkeys(selected))
    if args.no_shell:
        profile['theme']['shell'] = False
    if args.no_gtk4:
        profile['theme']['gtk4'] = False
    app = Appearance(profile, selected, args.sources)
    if args.action == 'setup':
        from install_support import setup
        setup(app, skip_deps=args.skip_deps, dry_run=args.dry_run)
        return 0
    if args.action == 'plan':
        major = version_major(args.gnome_version) if args.gnome_version is not None else app.gnome_major()
        data = app.plan(major, args.gnome_version)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print('仅预览，不安装依赖、不运行源码脚本、不修改外观。')
            print('项目适配目标：' + app.manifest['target_gnome'])
            print('本次预览 GNOME：' + str(data['gnome_version'] or '未检测到') + '；组件：' + ', '.join(selected))
            for item in data['actions']:
                print(item['part'] + ': ' + item['name'] + ' -> ' + item['destination'])
                if item['part'] == 'theme':
                    print('  Shell: ' + str(item['shell']) + '；GTK4 全局样式: ' + str(item['gtk4']))
                    if 'shell_style' in item:
                        print('  Shell 样式：' + json.dumps(item['shell_style'], ensure_ascii=False))
                    if 'shell_unavailable' in item:
                        print('  ' + item['shell_unavailable'])
        return 0
    if args.action == 'check':
        major = app.check(target_version=args.gnome_version)
        print('资源校验、命令依赖和 Shell 资源检查通过；GNOME ' + str(args.gnome_version or major))
        if args.gnome_version:
            print('这是指定目标的项目检查，不代表当前运行会话的版本。')
        print('未运行构建或安装，未验证实际显示效果。')
    elif args.action == 'build-shell':
        detected = app.gnome_major()
        version = args.gnome_version or (str(detected) if detected in (48, 51)
                                         else app.manifest['default_shell_build_version'])
        if 'theme' not in app.selected or not app.profile['theme'].get('shell'):
            raise Error('build-shell 需要选择 theme 并启用 Shell 样式')
        major = version_major(version)
        if major not in (48, 51) or app.profile['theme']['adapter'] != 'orchis':
            raise Error('build-shell 当前支持 Orchis 的 GNOME 48 和 51 样式')
        app.verify()
        baseline = '48' if major == 48 else '51.1'
        output = ROOT / 'build' / (app.profile['theme']['name'] + '-shell-' + baseline) / 'gnome-shell'
        with tempfile.TemporaryDirectory(prefix='orchis-preview-') as d:
            source = Path(d) / 'source'
            if app.sources:
                orchis = bounded(app.sources.resolve(), app.profile['theme']['source'])
            else:
                extract_tar(ROOT / 'resources/sources.tar.xz', source)
                orchis = bounded(source, app.profile['theme']['source'])
            renderer = build_shell48 if major == 48 else build_shell51
            renderer(orchis, output, app.profile['theme'])
        print('仅生成项目内文件，未安装或应用：' + str(output))
    elif args.action == 'status':
        interface = Settings('org.gnome.desktop.interface')
        for key in ['gtk-theme', 'icon-theme', 'cursor-theme', 'cursor-size']:
            print(key + ': ' + interface.get(key))
    elif args.action == 'apply':
        app.apply()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print('停止：' + str(exc), file=sys.stderr)
        sys.exit(1)
