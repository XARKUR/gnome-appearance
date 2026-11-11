"""Exercise streamed installers with local HTTP fixtures, never a desktop install."""
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def run_fixture(self, member_name):
        with tempfile.TemporaryDirectory(prefix='bootstrap-test-') as temp:
            root = Path(temp)
            payload = b'import os, pathlib, sys; pathlib.Path(os.environ["BOOTSTRAP_OUTPUT"]).write_text(" ".join(sys.argv[1:]))'
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                info = tarfile.TarInfo(member_name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            data = root / 'fixture.tar.gz'
            data.write_bytes(stream.getvalue())
            (root / 'sitecustomize.py').write_text(
                'import io, os, urllib.request\n'
                'urllib.request.urlopen = lambda *args, **kwargs: io.BytesIO(open(os.environ["BOOTSTRAP_ARCHIVE"], "rb").read())\n')
            output = root / 'result'
            env = dict(os.environ, PYTHONPATH=str(root), TMPDIR=str(root),
                       BOOTSTRAP_ARCHIVE=str(data), BOOTSTRAP_OUTPUT=str(output))
            result = subprocess.run(['sh', '-s', '--', '--fixture'], input=(ROOT / 'install.sh').read_bytes(),
                                    env=env, capture_output=True)
            return result, output.read_text() if output.exists() else None

    def test_streamed_install_dispatches_setup_without_git(self):
        result, output = self.run_fixture('project-main/appearance.py')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(output, 'setup --fixture')

    def test_archive_parent_traversal_is_rejected(self):
        result, output = self.run_fixture('../appearance.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Unsupported path', result.stderr)
        self.assertIsNone(output)


if __name__ == '__main__':
    unittest.main()
