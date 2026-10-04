"""Check sync transactions against disposable local repositories, never GitHub."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SyncTests(unittest.TestCase):
    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.local = self.root/'checkout'
        self.local.mkdir()
        self.git(self.local, 'init', '-b', 'main')
        self.git(self.local, 'config', 'user.name', 'Fixture')
        self.git(self.local, 'config', 'user.email', 'fixture@example.invalid')
        (self.local/'shared').write_text('base\n')
        self.git(self.local, 'add', '.')
        self.git(self.local, 'commit', '-m', 'base')
        for name in ('origin','upstream'):
            subprocess.run(['git','clone','--bare',str(self.local),str(self.root/name)],check=True,capture_output=True)
            self.git(self.local,'remote','add',name,str(self.root/name))
        self.git(self.local,'switch','-c','hfgj')
        (self.local/'hfgj/scripts').mkdir(parents=True)
        (self.local/'hfgj/tests').mkdir()
        (self.local/'hfgj/scripts/sync-upstream.sh').write_text((ROOT/'hfgj/scripts/sync-upstream.sh').read_text())
        (self.local/'hfgj/scripts/setup-opkg.sh').write_text((ROOT/'hfgj/scripts/setup-opkg.sh').read_text())
        (self.local/'hfgj/tests/test_gate.py').write_text('import unittest\nfrom pathlib import Path\nclass Gate(unittest.TestCase):\n def test_regression(self): self.assertFalse(Path("bad-regression").exists())\n')
        for name in ('feed.sh','install.sh','migrate.sh','migrate-job.sh','rollback-package.sh'):
            (self.local/name).write_text('#!/bin/sh\nexit 0\n')
        (self.local/'shared').write_text('HFGJ patch\n')
        self.git(self.local,'add','.')
        self.git(self.local,'commit','-m','distribution patch')
        self.git(self.local,'push','origin','hfgj')
        self.before = self.refs()
        self.official = self.root/'official'
        subprocess.run(['git','clone',str(self.root/'upstream'),str(self.official)],check=True,capture_output=True)
        self.git(self.official,'config','user.name','Official fixture')
        self.git(self.official,'config','user.email','official@example.invalid')

    def tearDown(self):
        self.temporary.cleanup()

    def refs(self):
        return {name:self.git(self.root/'origin','rev-parse',name) for name in ('main','hfgj')}

    def update_official(self, path, value):
        (self.official/path).write_text(value)
        self.git(self.official,'add','.')
        self.git(self.official,'commit','-m','official update')
        self.git(self.official,'push','origin','main')

    def sync(self):
        # Ignore unrelated global Git hooks and signing in this disposable fixture.
        return subprocess.run(['bash','hfgj/scripts/sync-upstream.sh'],cwd=self.local,
                              env=dict(os.environ,GIT_CONFIG_COUNT='2',GIT_CONFIG_KEY_0='commit.gpgsign',GIT_CONFIG_VALUE_0='false',GIT_CONFIG_KEY_1='core.hooksPath',GIT_CONFIG_VALUE_1='/dev/null'),text=True,capture_output=True)

    def test_success_advances_mirror_and_keeps_patch(self):
        self.update_official('upstream-feature','new upstream\n')
        result=self.sync()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.refs()['main'],self.git(self.root/'upstream','rev-parse','main'))
        self.assertEqual(self.git(self.root/'origin','show','hfgj:shared'),'HFGJ patch')
        self.assertEqual(self.git(self.root/'origin','show','hfgj:upstream-feature'),'new upstream')

    def test_conflict_leaves_both_published_branches_unchanged(self):
        self.update_official('shared','incompatible upstream\n')
        result=self.sync()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Conflict:',result.stderr)
        self.assertEqual(self.refs(),self.before)

    def test_regression_failure_leaves_both_published_branches_unchanged(self):
        self.update_official('bad-regression','fixture failure\n')
        result=self.sync()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('FAILED',result.stderr)
        self.assertEqual(self.refs(),self.before)
