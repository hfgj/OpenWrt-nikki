"""Committed distribution inputs determine revisions; changed feeds cannot reuse versions."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_core_feed import ROOT, feed, fixture, ARCH


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = dict(os.environ, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='commit.gpgsign',
                        GIT_CONFIG_VALUE_0='false', GIT_CONFIG_KEY_1='core.hooksPath', GIT_CONFIG_VALUE_1='/dev/null')
        self.git('init', '-b', 'hfgj')
        self.git('config', 'user.name', 'Distribution fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        for path in feed.packaging_inputs(ROOT):
            target = self.root/path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        self.commit(1791138820)

    def git(self, *args, **extra):
        return subprocess.check_output(['git', '-C', str(self.root), *args],
                                       env=dict(self.env, **extra), text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, stamp):
        self.git('add', '.')
        self.git('commit', '-m', 'fixture revision', GIT_AUTHOR_DATE=f'{stamp} +0000', GIT_COMMITTER_DATE=f'{stamp} +0000')

    def test_every_bootstrap_change_increases_fingerprint_and_revision(self):
        revision = feed.packaging_release(self.root)
        fingerprint = feed.packaging_fingerprint(self.root)
        for offset, name in enumerate(feed.BOOTSTRAP_FILES, 1):
            path = self.root/name
            path.write_text(path.read_text()+'\n# reviewed change\n')
            with self.assertRaisesRegex(ValueError, 'uncommitted'):
                feed.packaging_release(self.root)
            self.commit(1791138820+offset)
            new_revision = feed.packaging_release(self.root)
            new_fingerprint = feed.packaging_fingerprint(self.root)
            self.assertGreater(new_revision, revision)
            self.assertNotEqual(new_fingerprint, fingerprint)
            revision, fingerprint = new_revision, new_fingerprint
        (self.root/'notes.txt').write_text('unrelated note')
        self.assertEqual(feed.packaging_release(self.root), revision)

    def test_dirty_prepare_refuses_before_network_or_generated_files(self):
        path = self.root/'install.sh'
        path.write_text(path.read_text()+'\n# uncommitted\n')
        args = SimpleNamespace(root=self.root, targets=self.root/'hfgj/targets.json')
        with patch.object(feed, 'get_bytes') as network:
            with self.assertRaisesRegex(ValueError, 'uncommitted'):
                feed.prepare(args)
            network.assert_not_called()
        self.assertFalse((self.root/'mihomo-hfgj/core.json').exists())

    def test_deleted_input_cannot_keep_old_revision(self):
        for name in ('migrate.sh', 'hfgj/scripts/setup-opkg.sh'):
            path = self.root/name
            original = path.read_bytes()
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'uncommitted'):
                feed.packaging_release(self.root)
            path.write_bytes(original)

    def test_changed_same_version_and_downgrade_refused(self):
        _, _, _, metadata = fixture()
        targets = {'include': [{'branch': 'openwrt-24.10', 'arch': ARCH}]}
        previous = {'core': metadata, 'targets': targets, 'packaging_sha256': 'old'}
        self.assertFalse(feed.feed_changed(previous, metadata, targets, 'old'))
        for current, matrix, fingerprint in ((metadata, targets, 'changed'),
                (dict(metadata, binary_sha256='0'*64), targets, 'old'),
                (metadata, {'include': [{'branch': 'openwrt-24.10', 'arch': 'aarch64_cortex-a53'}]}, 'old')):
            with self.assertRaisesRegex(ValueError, 'higher package version'):
                feed.feed_changed(previous, current, matrix, fingerprint)
        later = dict(metadata, package_release=2)
        self.assertTrue(feed.feed_changed(previous, later, targets, 'changed'))
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            feed.feed_changed(dict(previous, core=later), metadata, targets, 'old')

    def test_same_version_rejection_leaves_no_build_outputs(self):
        _, archive, release, metadata = fixture()
        metadata['package_release'] = feed.packaging_release(self.root)
        targets = json.loads((self.root/'hfgj/targets.json').read_text())
        previous = {'core': metadata, 'targets': targets, 'packaging_sha256': 'different'}
        responses = [json.dumps(release).encode(), metadata['core_version'].encode(),
                     (feed.digest(archive)+'  '+metadata['asset']+'\n').encode(), archive, json.dumps(previous).encode()]
        args = SimpleNamespace(root=self.root, targets=self.root/'hfgj/targets.json',
                               output=self.root/'hfgj/build', feed_url='https://fixture.invalid', github_output=None)
        with patch.object(feed, 'get_bytes', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'higher package version'):
                feed.prepare(args)
        self.assertFalse(args.output.exists())
        self.assertFalse((self.root/'mihomo-hfgj/core.json').exists())

    def test_runtime_target_override_is_locked_without_dirtying_source(self):
        _, archive, release, metadata = fixture()
        revision = feed.packaging_release(self.root)
        targets = {'include': [{'branch': 'openwrt-24.10', 'arch': ARCH}]}
        locked = self.root/'hfgj/build/targets.json'
        locked.parent.mkdir(parents=True)
        locked.write_text(json.dumps(targets))
        previous = {'core': dict(metadata, package_release=revision-1), 'targets': targets,
                    'packaging_sha256': feed.packaging_fingerprint(self.root)}
        responses = [json.dumps(release).encode(), metadata['core_version'].encode(),
                     (feed.digest(archive)+'  '+metadata['asset']+'\n').encode(), archive, json.dumps(previous).encode()]
        args = SimpleNamespace(root=self.root, targets=locked, output=locked.parent,
                               feed_url='https://fixture.invalid', github_output=self.root/'github-output')
        with patch.object(feed, 'get_bytes', side_effect=responses):
            feed.prepare(args)
        self.assertEqual(json.loads((args.output/'core.json').read_text())['package_release'], revision)
        self.assertIn('matrix='+json.dumps(targets,separators=(',',':')), args.github_output.read_text())
        self.assertEqual(feed.packaging_release(self.root), revision)


if __name__ == '__main__': unittest.main()
