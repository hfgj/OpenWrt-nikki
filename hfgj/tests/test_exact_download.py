"""Production download checks with native usign and isolated transport/services.

The successful preparation test executes the production prefix before the
transaction boundary; it never starts a host/router service or installs a package.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
import test_migration as migration


def preparation_prefix(script):
    action='case "$hfgj_action" in --plan|--apply|--apply-fresh|--rollback) ;; *) echo \'Usage: sh migrate.sh --plan|--apply|--apply-fresh|--rollback BACKUP_DIR\' >&2; exit 1 ;; esac'
    boundary='hfgj_transaction_active=1\n'
    assert script.count(action)==script.count(boundary)==1
    return script.split(boundary)[0].replace(action,
        'case "$hfgj_action" in --prepare) [ $# = 1 ] || exit 1 ;; *) exit 1 ;; esac')+\
        "printf 'backup_dir=%s\\n' \"$hfgj_backup\" > \"$hfgj_script_dir/prepared-state\"\n"


class ExactDownloadTests(unittest.TestCase):
    def setUp(self):
        self.usign = os.environ.get('HFGJ_TEST_USIGN') or shutil.which('usign')
        if not self.usign:
            if os.environ.get('GITHUB_ACTIONS') == 'true': self.fail('CI must provide native usign')
            self.skipTest('Set HFGJ_TEST_USIGN to native usign')
        self.router = migration.MigrationTests('test_plan_is_read_only')
        self.router.setUp()
        self.addCleanup(self.router.tearDown)
        root = self.router.root
        script = (root/'migrate.sh').read_text()
        self.helper = root / 'prepare-only.sh'
        self.helper.write_text(preparation_prefix(script))
        (root / 'etc/opkg/keys').mkdir(parents=True)
        (root / 'etc/opkg.conf').write_text('src/gz hfgj-core https://fixture.invalid/hfgj\nsrc/gz nikki https://fixture.invalid/nikki\n')
        subprocess.run([self.usign, '-G', '-s', str(root/'test.key'), '-p', str(root/'etc/opkg/keys/test.pub')], check=True, capture_output=True)
        fingerprint = subprocess.check_output([self.usign, '-F', '-p', str(root/'etc/opkg/keys/test.pub')], text=True).strip()
        (root/'etc/opkg/keys/test.pub').rename(root/'etc/opkg/keys'/fingerprint)
        self.router.command('usign', '#!/bin/sh\nexec '+self.usign+' "$@"\n')
        for name in ('hfgj-core','nikki'):
            self.sign(name)

    def sign(self, name):
        path = self.router.root / 'var/opkg-lists' / name
        subprocess.run([self.usign, '-S', '-m', str(path), '-s', str(self.router.root/'test.key'), '-x', str(path)+'.sig'], check=True, capture_output=True)

    def edit_index(self, name, change):
        path = self.router.root / 'var/opkg-lists' / name
        path.write_text(change(path.read_text()))
        self.sign(name)

    def prepare(self, expected_success=False, **extra):
        result = subprocess.run(['sh', str(self.helper), '--prepare'], env=dict(self.router.env, **extra), capture_output=True, text=True)
        events_path = self.router.root/'events'
        events = events_path.read_text() if events_path.exists() else ''
        for forbidden in ('service stop', 'service start', 'opkg install', 'opkg remove', 'opkg download'):
            self.assertNotIn(forbidden, events)
        self.assertEqual(json.loads((self.router.root/'packages.json').read_text()), {'mihomo-meta':'1.19.31'})
        self.assertEqual((self.router.root/'usr/libexec/mihomo').read_bytes(), self.router.old)
        self.assertTrue((self.router.root/'running').exists())
        self.assertEqual(result.returncode == 0, expected_success, result.stderr+result.stdout)
        self.assertEqual((self.router.root/'prepared-state').exists(), expected_success)
        self.assertFalse((self.router.root/'migration-ready').exists())
        return result

    def test_exact_signed_files_prepare_recovery_without_name_download(self):
        self.prepare(True)
        state = dict(line.split('=',1) for line in (self.router.root/'prepared-state').read_text().splitlines())
        backup = Path(state['backup_dir'])
        self.assertEqual((backup/'mihomo-meta.ipk').read_bytes(), (self.router.root/'repository/mihomo-meta.ipk').read_bytes())
        self.assertIn('recovery_protocol=2\n', (backup/'state').read_text())
        self.assertEqual((backup/'transaction').read_text(), 'phase=prepared\n')
        self.assertTrue((backup/'recovery.ipk').exists())
        self.assertEqual((backup/'migrate.sh').read_bytes(), (self.router.root/'migrate.sh').read_bytes())

    def test_compressed_signed_indexes(self):
        for name in ('nikki','hfgj-core'):
            path = self.router.root/'var/opkg-lists'/name
            path.write_bytes(gzip.compress(path.read_bytes()))
        self.prepare(True)

    def test_corrupt_signature(self):
        (self.router.root/'var/opkg-lists/nikki.sig').write_text('invalid signature\n')
        self.prepare()

    def test_download_hash_mismatch(self):
        path = self.router.root/'repository/mihomo-meta.ipk'
        path.write_bytes(path.read_bytes()+b'changed')
        self.prepare()

    def test_wrong_identity_under_signed_filename(self):
        old = self.router.root/'repository/mihomo-meta.ipk'
        old.write_bytes((self.router.root/'repository/mihomo-hfgj.ipk').read_bytes())
        self.edit_index('nikki', lambda text: re.sub(r'SHA256sum: [0-9a-f]+', 'SHA256sum: '+hashlib.sha256(old.read_bytes()).hexdigest(), text))
        self.prepare()

    def test_duplicate_exact_records(self):
        self.edit_index('nikki', lambda text: text+text)
        self.prepare()

    def test_unsafe_signed_filename(self):
        self.edit_index('nikki', lambda text: text.replace('Filename: mihomo-meta.ipk', 'Filename: ../mihomo-meta.ipk'))
        self.prepare()

    def test_missing_exact_version(self):
        self.edit_index('nikki', lambda text: text.replace('Version: 1.19.31', 'Version: 1.19.30'))
        self.prepare()

    def test_ambiguous_source_name(self):
        with (self.router.root/'etc/opkg.conf').open('a') as f:f.write('src nikki https://other.invalid/nikki\n')
        self.prepare()

    def test_unsupported_source_url(self):
        path = self.router.root/'etc/opkg.conf'
        path.write_text(path.read_text().replace('https://fixture.invalid/nikki', 'https://fixture.invalid/nikki?x=1'))
        self.prepare()

    def test_insufficient_overlay_never_prepares(self):
        self.prepare(FIXTURE_CORE_FREE_KIB='1')

    def test_transaction_flags_rejected(self):
        for flag in ('--apply','--rollback','--plan'):
            result = subprocess.run(['sh',str(self.helper),flag],env=self.router.env,capture_output=True)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.router.root/'prepared-state').exists())

    def test_full_migration_and_rollback_regression(self):
        result = self.router.run_migration('--apply')
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        backup = next((self.router.root/'root/hfgj-core-backups').glob('migration.*'))
        result = self.router.run_migration('--rollback',str(backup))
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertEqual(json.loads((self.router.root/'packages.json').read_text()), {'mihomo-meta':'1.19.31'})
        self.assertEqual((self.router.root/'usr/libexec/mihomo').read_bytes(),self.router.old)

if __name__ == '__main__':
    unittest.main(defaultTest='ExactDownloadTests')
