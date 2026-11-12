import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import appearance as a


class FakeSettings:
    values = {}
    fail_once = None

    def __init__(self, schema, schema_dir=None):
        self.schema, self.schema_dir = schema, str(schema_dir) if schema_dir else None

    def get(self, key):
        return self.values[(self.schema, key)]

    def set(self, key, value):
        if self.fail_once == key:
            type(self).fail_once = None
            raise a.Error('simulated settings failure')
        self.values[(self.schema, key)] = value


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.profile = a.load_profile(ROOT / 'profile.json')

    def test_dynamic_accent_named_colors_preserve_valid_gtk_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gtk = root / 'gtk-4.0'
            gtk.mkdir()
            css = gtk / 'gtk.css'
            css.write_text('@define-color accent_color #3281EA;\n'
                           '@define-color theme_selected_bg_color var(--accent-bg-color);\n'
                           '@define-color theme_unfocused_selected_bg_color var(--accent-bg-color);\n'
                           'button { background: var(--accent-bg-color); }\n')
            a.normalize_gtk4_named_colors(root)
            result = css.read_text()
            self.assertIn('@define-color theme_selected_bg_color @accent_color;', result)
            self.assertIn('@define-color theme_unfocused_selected_bg_color @accent_color;', result)
            self.assertIn('button { background: var(--accent-bg-color); }', result)
            a.normalize_gtk4_named_colors(root)
            self.assertEqual(css.read_text(), result)

    def test_resource_dependency_graph_survives_relocation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base, dark = root / 'build/Tela', root / 'build/Tela-dark'
            (base / 'scalable').mkdir(parents=True)
            dark.mkdir(parents=True)
            (base / 'scalable/folder.svg').write_text('fixture')
            (base / 'scalable/folder-open.svg').symlink_to('folder.svg')
            (base / 'scalable/folder-drag.svg').symlink_to('folder-open.svg')
            (dark / 'scalable').symlink_to('../Tela/scalable')
            resources = [(base, root / 'installed/Tela', 'icons'), (dark, root / 'installed/Tela-dark', 'icons')]
            a.validate_resource_links(resources)
            with self.assertRaisesRegex(a.Error, '未随包安装'):
                a.validate_resource_links(resources[1:])
            for source, target, _ in resources:
                a.copy(source, target)
            shutil.rmtree(root / 'build')
            a.validate_resource_links([(target, target, part) for _, target, part in resources])

    def test_broken_and_absolute_links_fail_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'source'
            source.mkdir()
            (source / 'bad').symlink_to('missing.svg')
            with self.assertRaisesRegex(a.Error, '损坏'):
                a.validate_resource_links([(source, root / 'installed', 'icons')])
            (source / 'bad').unlink()
            (source / 'file').write_text('fixture')
            (source / 'bad').symlink_to(source / 'file')
            with self.assertRaisesRegex(a.Error, '绝对链接'):
                a.validate_resource_links([(source, root / 'installed', 'icons')])

    def test_empty_svg_is_not_a_valid_icon(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'folder.svg').touch()
            with self.assertRaisesRegex(a.Error, '空图像'):
                a.validate_resource_links([(root, root, 'icons')])

    @unittest.skipUnless(shutil.which('gtk-update-icon-cache'), 'GTK cache tool not installed')
    def test_real_tela_bundle_has_scaled_dirs_and_repaired_alias_after_copy(self):
        with tempfile.TemporaryDirectory() as build, tempfile.TemporaryDirectory() as installed:
            app = a.Appearance(self.profile, ['icons', 'cursor'])
            original = a.digest(ROOT / 'resources/sources.tar.xz')
            resources = app.build(Path(build))
            for source, destination, part in resources:
                a.copy(source, Path(installed) / destination.name)
            # Test the installed family after all build paths have disappeared.
            shutil.rmtree(build)
            relocated = [(Path(installed) / target.name, Path(installed) / target.name, part)
                         for _source, target, part in resources]
            a.validate_resource_links(relocated)
            for name in ['Tela', 'Tela-dark']:
                theme = Path(installed) / name
                self.assertTrue((theme / 'symbolic/apps/xsi-addon-symbolic.svg').is_file())
                for icon in ['inode-directory.svg', 'folder-videos.svg', 'folder-photos.svg']:
                    file = theme / 'scalable/places' / icon
                    self.assertGreater(file.stat().st_size, 0, icon)
                    self.assertIn(b'<svg', file.read_bytes()[:300], icon)
                self.assertTrue((theme / 'scalable@3x/apps').is_dir())
                self.assertTrue((theme / '16@3x/actions').is_dir())
                a.command(['gtk-update-icon-cache', '--validate', str(theme)])
            self.assertEqual(original, a.digest(ROOT / 'resources/sources.tar.xz'))

    def test_default_plan_never_calls_build_or_apply(self):
        with patch.object(a.Appearance, 'apply', side_effect=AssertionError('apply invoked')), \
             patch.object(a.Appearance, 'build', side_effect=AssertionError('build invoked')), \
             patch('subprocess.run', side_effect=AssertionError('external process invoked')):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(a.main(['plan', '--gnome-version', '48', '--json']), 0)
            self.assertEqual(json.loads(out.getvalue())['selected'], ['theme', 'icons', 'cursor'])

    def test_shell51_minor_version_plan_is_read_only(self):
        with patch.object(a.Appearance, 'apply', side_effect=AssertionError('apply invoked')), \
             patch.object(a.Appearance, 'build', side_effect=AssertionError('upstream build invoked')), \
             patch.object(a, 'build_shell51', side_effect=AssertionError('Sass build invoked')), \
             patch('subprocess.run', side_effect=AssertionError('external process invoked')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            a.main(['plan', '--gnome-version', '51.1', '--json'])
        plan = json.loads(out.getvalue())
        self.assertEqual(plan['gnome_major'], 51)
        self.assertEqual(plan['gnome_version'], '51.1')
        self.assertEqual(plan['actions'][0]['shell_style']['baseline'], '51.1')
        self.assertTrue(plan['actions'][0]['gtk4'])

    def test_target_version_cannot_be_used_to_apply(self):
        with patch.object(a.Appearance, 'apply', side_effect=AssertionError('apply invoked')):
            with self.assertRaisesRegex(a.Error, '不伪装'):
                a.main(['apply', '--gnome-version', '51.1'])

    def test_shell51_requires_a_real_port_and_future_shell_is_rejected(self):
        app = a.Appearance(self.profile, ['theme'])
        with patch('shutil.which', return_value='/fake/tool'), \
             patch.object(app, 'gnome_major', return_value=43), \
             patch.object(app, 'shell_extension') as extension:
            self.assertEqual(app.check(target_version='51.1'), 51)
            extension.assert_called_with(51)
            self.assertEqual(app.check(target_version='48'), 48)
            extension.assert_called_with(48)
            with self.assertRaisesRegex(a.Error, '尚未提供'):
                app.check(target_version='52.1')
            # Unported intermediate versions must not silently use the 48 theme.
            with self.assertRaisesRegex(a.Error, '尚未提供'):
                app.check(target_version='50')

    def test_user_theme_51_metadata_and_stale_user_copy(self):
        app = a.Appearance(self.profile, ['theme'])
        with zipfile.ZipFile(ROOT / 'resources/user-themes-51.zip') as z:
            meta = json.loads(z.read('metadata.json'))
        self.assertEqual(meta['shell-version'], ['51'])
        self.assertEqual(meta['version-name'], '51.0')
        with tempfile.TemporaryDirectory() as d:
            app.data = Path(d)
            old = app.data / 'gnome-shell/extensions' / a.USER_THEME
            old.mkdir(parents=True)
            (old / 'metadata.json').write_text(json.dumps({'shell-version': ['43']}))
            destination, archive = app.shell_extension(51)
            self.assertEqual(destination, old)
            self.assertEqual(archive, 'user-themes-51.zip')

    def test_shell51_compiles_variants_without_touching_original_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a.extract_tar(ROOT / 'resources/sources.tar.xz', root / 'sources')
            source = root / 'sources' / self.profile['theme']['source']
            before = a.fingerprint(source)
            port_before = a.fingerprint(ROOT / 'resources/shell-51')
            for variant, color, size in [('default', 'dark', 'standard'), ('default', 'light', 'compact'),
                                          ('purple', 'dark', 'compact'), ('grey', 'standard', 'standard')]:
                with self.subTest(variant=variant, color=color, size=size):
                    theme = dict(self.profile['theme'], variant=variant, color=color, size=size)
                    output = root / 'compiled' / f'{variant}-{color}-{size}'
                    a.build_shell51(source, output, theme)
                    css = (output / 'gnome-shell.css').read_text()
                    self.assertIn('.qr-code', css)
                    self.assertIn('.message-notification-group', css)
                    self.assertIn('rgba(0, 0, 0, 0.65)', css)  # Orchis floating panel
                    self.assertIn('assets/calendar-today.svg', css)
                    self.assertNotIn('resource:///org/gnome/shell/theme/', css)
                    if variant != 'default':
                        self.assertNotIn('-st-accent-color', css)
            self.assertEqual(before, a.fingerprint(source))
            self.assertEqual(port_before, a.fingerprint(ROOT / 'resources/shell-51'))

    def test_debian48_theme_and_user_themes_remain_available(self):
        app = a.Appearance(self.profile, ['theme'])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app.data = root / 'empty-data'
            self.assertEqual(app.shell_style(48)['provider'], 'orchis-upstream')
            self.assertEqual(app.shell_extension(48)[1], 'user-themes-48.zip')
            a.extract_tar(ROOT / 'resources/sources.tar.xz', root / 'sources')
            source = root / 'sources' / self.profile['theme']['source']
            before = a.fingerprint(source)
            for color, size in [('dark', 'standard'), ('light', 'compact')]:
                theme = dict(self.profile['theme'], color=color, size=size)
                output = root / ('shell-48-' + color)
                a.build_shell48(source, output, theme)
                css = (output / 'gnome-shell.css').read_text()
                self.assertIn('.quick-settings', css)
                self.assertIn('.calendar', css)
                self.assertGreater(len(css), 10000)
            self.assertEqual(before, a.fingerprint(source))

    def test_full_build_routes_shell51_to_the_new_renderer(self):
        app = a.Appearance(self.profile, ['theme'])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app.home = root / 'mock-home'
            def skip_upstream(_profile, _selected, _source, output):
                # Only emulate the old installer's output. Never execute install.sh.
                theme = output / 'themes' / self.profile['theme']['name']
                for rel in ['gtk-3.0/gtk.css', 'gtk-4.0/gtk.css', 'gtk-4.0/gtk-dark.css',
                            'gnome-shell/old-48-marker']:
                    file = theme / rel
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text('fixture')
                return []
            with patch.object(app, 'gnome_major', return_value=51), \
                 patch.object(a, 'build_commands', side_effect=skip_upstream):
                (root / 'build').mkdir()
                built = app.build(root / 'build')
            theme = built[0][0]
            self.assertIn('.qr-code', (theme / 'gnome-shell/gnome-shell.css').read_text())
            self.assertFalse((theme / 'gnome-shell/old-48-marker').exists())
            self.assertEqual((theme / 'gtk-4.0/gtk.css').read_text(), 'fixture')
            self.assertFalse(app.home.exists())

    def test_partial_plan_and_opt_outs(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            a.main(['plan', '--gnome-version', '43', '--only', 'theme', '--no-shell', '--no-gtk4', '--json'])
        action = json.loads(out.getvalue())['actions'][0]
        self.assertFalse(action['shell'])
        self.assertFalse(action['gtk4'])

    def test_no_distro_or_debian_setup_dependency(self):
        text = (ROOT / 'appearance.py').read_text()
        self.assertNotIn('debian-setup', text)
        self.assertNotRegex(text, r'/home/[A-Za-z0-9_-]+/')
        app = a.Appearance(self.profile, ['cursor'])
        with patch('shutil.which', return_value='/fake/gsettings'), patch.object(app, 'gnome_major', return_value=99):
            self.assertEqual(app.check(), 99)  # Cursor replacement has no Shell-version restriction.

    def test_source_command_selection_is_minimal(self):
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d), Path(d) / 'output'
            commands = a.build_commands(self.profile, ['theme', 'icons'], source, output)
            self.assertEqual(commands[0][1][-8:-2], ['-t', 'default', '-c', 'dark', '-s', 'standard'])
            self.assertEqual(commands[0][1][-2:], ['-i', a.activities_icon(self.profile['theme'])])
            self.assertEqual(commands[1][1][-1], 'standard')
            self.assertFalse(any('-a' in cmd or '--tweaks' in cmd or '-l' in cmd or 'sudo' in cmd for _, cmd in commands))

    def test_activity_icon_auto_and_explicit_selection(self):
        for distro, expected in [('debian', 'debian'), ('arch', 'arch'), ('ubuntu', 'default')]:
            with patch.object(Path, 'read_text', return_value='ID="' + distro + '"\n'):
                self.assertEqual(a.activities_icon({'activities_icon': 'auto'}), expected)
        self.assertEqual(a.activities_icon({'activities_icon': 'arch'}), 'arch')
        self.assertIsNone(a.activities_icon({}))  # Existing profiles keep their normal activities button.

    def test_activity_icon_assets_match_both_shell_builders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a.extract_tar(ROOT / 'resources/sources.tar.xz', root / 'sources')
            source = root / 'sources' / self.profile['theme']['source']
            for major, builder in [(48, a.build_shell48), (51, a.build_shell51)]:
                for icon in ['debian', 'arch']:
                    theme = dict(self.profile['theme'], activities_icon=icon)
                    output = root / (str(major) + '-' + icon)
                    builder(source, output, theme)
                    expected = source / ('src/gnome-shell/activities/activities-' + icon + '.svg')
                    self.assertEqual((output / 'assets/activities.svg').read_bytes(), expected.read_bytes())
                    self.assertIn('assets/activities.svg', (output / 'gnome-shell.css').read_text())
                    if major == 51:
                        self.assertIn('background-size: 24px;', (output / 'gnome-shell.css').read_text())
                        self.assertNotIn('background-position: center;', (output / 'gnome-shell.css').read_text())
                        self.assertNotIn('background-size: 24px 24px;', (output / 'gnome-shell.css').read_text())
                        css = (output / 'gnome-shell.css').read_text()
                        self.assertIn('#panel:overview .panel-button#panelActivities .workspace-dot', css)
                        self.assertIn('background-color: transparent;', css.rsplit('.workspace-dot', 1)[-1])
                        self.assertNotIn('opacity:', css.rsplit('.workspace-dot', 1)[-1])

    def test_invalid_resource_path_and_name_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'bad.json'
            profile = copy.deepcopy(self.profile)
            profile['theme']['source'] = '../private'
            file.write_text(json.dumps(profile))
            with self.assertRaises(a.Error):
                a.load_profile(file)
            with self.assertRaises(a.Error):
                a.safe_name('../foo')

    def test_tar_path_and_symlink_attacks_are_rejected(self):
        for name, target in [('../escape', None), ('link', '/etc/passwd'), ('dir/link', '../../escape')]:
            with self.subTest(name=name, target=target), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                archive = root / 'bad.tar'
                with tarfile.open(archive, 'w') as tar:
                    item = tarfile.TarInfo(name)
                    if target:
                        item.type, item.linkname = tarfile.SYMTYPE, target
                        tar.addfile(item)
                    else:
                        item.size = 1
                        tar.addfile(item, io.BytesIO(b'x'))
                with self.assertRaises(a.Error):
                    a.extract_tar(archive, root / 'extract')
                self.assertFalse((root / 'escape').exists())

    def test_bundled_resources_and_native_archive_extractor(self):
        app = a.Appearance(self.profile, list(a.PARTS))
        app.verify()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a.extract_tar(ROOT / 'resources/sources.tar.xz', root)
            self.assertTrue((root / self.profile['theme']['source'] / 'src/gnome-shell/shell-48-0').is_dir())
            cursor = root / self.profile['cursor']['source'] / 'cursors/left_ptr'
            self.assertEqual(cursor.read_bytes()[:4], b'Xcur')

    def test_conflict_blocks_restore_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source, target = root / 'new', root / 'user-file'
            source.write_text('new')
            target.write_text('original')
            journal = a.Journal(root / 'state')
            journal.install(source, target, 'icons')
            journal.finish()
            target.write_text('user change')
            with self.assertRaises(a.Error):
                journal.restore()
            self.assertEqual(target.read_text(), 'user change')
            journal.restore(force=True)
            self.assertEqual(target.read_text(), 'original')

    def test_noop_does_not_replace_last_meaningful_restore_point(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src, dst = root / 'source', root / 'target'
            src.write_text('new')
            first = a.Journal(root / 'state')
            first.install(src, dst, 'icons')
            self.assertTrue(first.finish())
            second = a.Journal(root / 'state')
            second.install(src, dst, 'icons')
            self.assertFalse(second.finish())
            self.assertEqual((root / 'state/latest').read_text().strip(), first.root.name)

    def fixture(self, root):
        profile = copy.deepcopy(self.profile)
        profile['theme']['shell'] = False
        app = a.Appearance(profile, list(a.PARTS))
        app.home, app.data, app.config, app.state = (root / p for p in ['home', 'data', 'config', 'state'])
        resources = []
        for part, name in [('theme', 'Orchis-Dark'), ('icons', 'Tela-dark'), ('cursor', 'Breeze')]:
            source = root / 'fixtures' / part
            source.mkdir(parents=True)
            (source / 'file').write_text('new ' + part)
            target = app.home / '.themes' / name if part == 'theme' else app.data / 'icons' / name
            if part == 'theme':
                gtk4 = source / 'gtk-4.0'
                (gtk4 / 'assets').mkdir(parents=True)
                (gtk4 / 'gtk.css').write_text('new gtk4')
                (gtk4 / 'gtk-dark.css').write_text('new gtk4 dark')
            resources.append((source, target, part))
        old = app.config / 'gtk-4.0'
        old.mkdir(parents=True)
        (old / 'gtk.css').write_text('original CSS')
        (old / 'settings.ini').write_text('untouched')
        schema = 'org.gnome.desktop.interface'
        FakeSettings.values = {(schema, k): v for k, v in {
            'gtk-theme': "'Original'", 'icon-theme': "'OriginalIcons'",
            'cursor-theme': "'OriginalCursor'", 'cursor-size': '20'}.items()}
        return app, resources

    def test_simulated_apply_restore_and_gtk4_do_not_touch_real_home(self):
        with tempfile.TemporaryDirectory() as d:
            app, resources = self.fixture(Path(d))
            before = dict(FakeSettings.values)
            with patch.object(app, 'check', return_value=43), patch.object(app, 'build', return_value=resources), \
                 patch.object(a, 'Settings', FakeSettings), patch('subprocess.run', side_effect=AssertionError('real process')):
                with contextlib.redirect_stdout(io.StringIO()):
                    app.apply()
                link = app.config / 'gtk-4.0/gtk.css'
                self.assertTrue(link.is_symlink())
                run = (app.state / 'latest').read_text().strip()
                a.Journal(app.state, run).restore()
            self.assertEqual(link.read_text(), 'original CSS')
            self.assertFalse(link.is_symlink())
            self.assertEqual((app.config / 'gtk-4.0/settings.ini').read_text(), 'untouched')
            self.assertEqual(FakeSettings.values, before)
            self.assertFalse((app.data / 'icons/Breeze').exists())

    def test_simulated_settings_failure_rolls_back_every_previous_change(self):
        with tempfile.TemporaryDirectory() as d:
            app, resources = self.fixture(Path(d))
            before = dict(FakeSettings.values)
            FakeSettings.fail_once = 'icon-theme'
            with patch.object(app, 'check', return_value=43), patch.object(app, 'build', return_value=resources), \
                 patch.object(a, 'Settings', FakeSettings), patch('subprocess.run', side_effect=AssertionError('real process')):
                with self.assertRaisesRegex(a.Error, 'simulated'):
                    app.apply()
            self.assertEqual(FakeSettings.values, before)
            self.assertFalse((app.home / '.themes/Orchis-Dark').exists())
            self.assertEqual((app.config / 'gtk-4.0/gtk.css').read_text(), 'original CSS')

    def test_restore_does_not_require_a_working_profile_or_source_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state = root / 'gnome-appearance'
            source, target = root / 'src', root / 'dst'
            source.write_text('new')
            target.write_text('old')
            journal = a.Journal(state)
            journal.install(source, target, 'icons')
            journal.finish()
            with patch.dict(os.environ, XDG_STATE_HOME=d, DBUS_SESSION_BUS_ADDRESS='test-only'), \
                 patch('os.geteuid', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
                a.main(['restore', '--config', str(root / 'missing.json')])
            self.assertEqual(target.read_text(), 'old')


if __name__ == '__main__':
    unittest.main()


class CursorCompatibilityTests(unittest.TestCase):
    def test_legacy_lookup_alias_and_original_directory_restore(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);home=root/'home';data=home/'.local/share'
            target=data/'icons/Breeze';target.mkdir(parents=True)
            (target/'index.theme').write_text('new cursor theme')
            legacy=home/'.icons/Breeze';legacy.mkdir(parents=True)
            (legacy/'index.theme').write_text('original cursor theme')
            temp=root/'stage';temp.mkdir()
            journal=a.Journal(root/'state')
            a.install_cursor_alias(journal,temp,home,data,'Breeze')
            journal.finish()
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.resolve(),target)
            journal.restore()
            self.assertFalse(legacy.is_symlink())
            self.assertEqual((legacy/'index.theme').read_text(),'original cursor theme')

    def test_existing_legacy_parent_alias_does_not_create_self_link(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);home=root/'home';data=home/'.local/share'
            target=data/'icons/Breeze';target.mkdir(parents=True)
            (home/'.icons').symlink_to(data/'icons')
            temp=root/'stage';temp.mkdir()
            journal=a.Journal(root/'state')
            a.install_cursor_alias(journal,temp,home,data,'Breeze')
            self.assertEqual(journal.data['items'],[])
            self.assertFalse(target.is_symlink())
