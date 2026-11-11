"""Verify setup without installing system packages or changing a desktop."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_support as install


class SetupTests(unittest.TestCase):
    def app(self):
        app = Mock()
        app.selected = ['theme', 'icons', 'cursor']
        app.profile = {'theme': {'adapter': 'orchis', 'shell': True}}
        app.requirements.return_value = ['sassc', 'gtk-update-icon-cache', 'gsettings']
        app.gnome_major.return_value = 48
        return app

    def test_debian_plan_only_missing_packages(self):
        with patch.object(install, 'distribution', return_value={'debian'}), \
             patch.object(install.shutil, 'which', side_effect=lambda c: None if c == 'sassc' else '/bin/tool'), \
             patch.object(install, 'package_installed', side_effect=lambda m, p: p == 'gnome-themes-extra'):
            self.assertEqual(install.dependency_plan(self.app()), ('apt', ['gtk2-engines-murrine', 'sassc']))

    def test_arch_package_mapping_and_no_database_refresh(self):
        with patch.object(install, 'distribution', return_value={'arch'}), \
             patch.object(install.shutil, 'which', return_value=None), \
             patch.object(install, 'package_installed', return_value=True):
            manager, packages = install.dependency_plan(self.app())
        self.assertEqual(manager, 'pacman')
        self.assertEqual(packages, ['glib2', 'gtk3', 'sassc'])
        self.assertEqual(install.package_command(manager, packages),
                         ['sudo', 'pacman', '-S', '--needed', '--noconfirm', *packages])

    def test_cursor_only_does_not_install_theme_engines(self):
        app = self.app()
        app.selected = ['cursor']
        app.requirements.return_value = ['gsettings']
        with patch.object(install, 'distribution', return_value={'debian'}), \
             patch.object(install.shutil, 'which', return_value='/bin/tool'), \
             patch.object(install, 'package_installed', side_effect=AssertionError('irrelevant package query')):
            self.assertEqual(install.dependency_plan(app), ('apt', []))

    def test_arch_without_legacy_gtk2_packages_needs_no_optional_installs(self):
        with patch.object(install, 'distribution', return_value={'arch'}), \
             patch.object(install.shutil, 'which', return_value='/bin/tool'), \
             patch.object(install, 'package_installed', side_effect=AssertionError('legacy package query')):
            self.assertEqual(install.dependency_plan(self.app()), ('pacman', []))

    def test_arch_missing_build_tool_does_not_pull_in_aur_packages(self):
        with patch.object(install, 'distribution', return_value={'arch'}), \
             patch.object(install.shutil, 'which', side_effect=lambda c: None if c == 'sassc' else '/bin/tool'), \
             patch.object(install, 'package_installed', return_value=False):
            self.assertEqual(install.dependency_plan(self.app()), ('pacman', ['sassc']))

    def test_unknown_distribution_with_missing_tools_stops(self):
        with patch.object(install, 'distribution', return_value={'other'}), \
             patch.object(install.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '自动安装仅支持'):
                install.dependency_plan(self.app())

    def test_dry_run_never_applies_or_installs_packages(self):
        app = self.app()
        with patch.object(install.os, 'geteuid', return_value=1000), \
             patch.object(install, 'dependency_plan', return_value=('apt', ['sassc'])), \
             patch.object(install.subprocess, 'run', side_effect=AssertionError('package install invoked')), \
             contextlib.redirect_stdout(io.StringIO()):
            install.setup(app, dry_run=True)
        app.verify.assert_called_once()
        app.apply.assert_not_called()

    def test_skip_dependencies_leaves_packages_alone(self):
        app = self.app()
        with patch.object(install.os, 'geteuid', return_value=1000), \
             patch.dict(install.os.environ, {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/fixture/bus'}), \
             patch.object(install, 'dependency_plan', side_effect=AssertionError('dependency query invoked')), \
             patch.object(install.subprocess, 'run', side_effect=AssertionError('package install invoked')), \
             contextlib.redirect_stdout(io.StringIO()):
            install.setup(app, skip_deps=True)
        app.apply.assert_called_once()

    def test_package_failure_prevents_appearance_changes(self):
        app = self.app()
        with patch.object(install.os, 'geteuid', return_value=1000), \
             patch.dict(install.os.environ, {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/fixture/bus'}), \
             patch.object(install, 'dependency_plan', return_value=('apt', ['sassc'])), \
             patch.object(install.shutil, 'which', return_value='/bin/sudo'), \
             patch.object(install.subprocess, 'run', return_value=install.subprocess.CompletedProcess(['apt-get'], 1)), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '尚未应用外观'):
                install.setup(app)
        app.apply.assert_not_called()

    def test_root_rejected_before_resource_or_desktop_changes(self):
        app = self.app()
        with patch.object(install.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(RuntimeError, '不要 sudo'):
                install.setup(app)
        app.verify.assert_not_called()
        app.apply.assert_not_called()
