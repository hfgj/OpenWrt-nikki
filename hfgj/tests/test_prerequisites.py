"""Production bootstrap/tool checks in disposable router paths; no host installs."""
import hashlib
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from test_core_feed import ROOT
import test_migration as migration


class PrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.router = migration.MigrationTests('test_plan_is_read_only')
        self.router.setUp()
        self.addCleanup(self.router.tearDown)
        self.root = self.router.root
        self.stat = shutil.which('gstat') or shutil.which('stat')
        self.router.setup_job()
        (self.root/'feed.sh').write_text('#!/bin/sh\necho feed >> "$ROUTER_FIXTURE/events"\n')
        self.manifest()

    def manifest(self):
        names = ('feed.sh', 'install.sh', 'migrate.sh', 'migrate-job.sh', 'rollback-package.sh')
        (self.root/'bootstrap.sha256').write_text(''.join(
            hashlib.sha256((self.root/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in names))

    def run_script(self, script, action, **extra):
        return subprocess.run(['/bin/sh', str(self.root/script), action],
                              env=dict(self.router.env, **extra), text=True, capture_output=True, timeout=20)

    def events(self):
        path = self.root/'events'
        return path.read_text() if path.exists() else ''

    def assert_untouched(self):
        self.assertEqual(json.loads((self.root/'packages.json').read_text()), {'mihomo-meta': '1.19.31'})
        self.assertEqual((self.root/'usr/libexec/mihomo').read_bytes(), self.router.old)
        self.assertTrue((self.root/'running').exists())
        for forbidden in ('service stop', 'service start', 'opkg remove', 'opkg install ./'):
            self.assertNotIn(forbidden, self.events())

    def delayed_stat(self):
        (self.root/'stat-ready').unlink(missing_ok=True)
        self.router.command('stat', '#!/bin/sh\n[ -f "$ROUTER_FIXTURE/stat-ready" ] || exit 127\nexec '+self.stat+' "$@"\n')

    def test_prepare_installs_stat_once_without_core_changes(self):
        self.delayed_stat()
        first = self.run_script('install.sh', '--prepare-tools')
        self.assertEqual(first.returncode, 0, first.stdout+first.stderr)
        second = self.run_script('install.sh', '--prepare-tools')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.events().count('opkg install coreutils-stat\n'), 1)
        self.assertNotIn('feed\n', self.events())
        self.assert_untouched()

    def test_prepare_with_usable_stat_does_not_install(self):
        result = self.run_script('install.sh', '--prepare-tools')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('opkg install', self.events())
        self.assertNotIn('feed\n', self.events())
        self.assert_untouched()

    def test_failed_stat_install_keeps_core_and_service(self):
        self.delayed_stat()
        result = self.run_script('install.sh', '--prepare-tools', FAIL_STAT_INSTALL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('installation failed', result.stderr)
        self.assertFalse((self.root/'external-backup').exists())
        self.assert_untouched()

    def test_installed_but_still_incompatible_stat_is_rejected(self):
        self.router.command('stat', '#!/bin/sh\necho "%a:%u:%g:%Y"\n')
        result = self.run_script('install.sh', '--prepare-tools')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incompatible stat', result.stderr)
        self.assert_untouched()

    def test_empty_stat_output_is_rejected(self):
        self.router.command('stat', '#!/bin/sh\nexit 0\n')
        result = self.run_script('rollback-package.sh', '--check-tools')
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_missing_or_incompatible_stat_rejects_all_migration_entries_early(self):
        # PATH contains every required tool except stat. No fallback to host stat.
        tools = self.root/'only-tools'; tools.mkdir()
        for name in ('sh','dirname','id','uname','opkg','tar','gzip','awk','sha256sum','find','mktemp',
                     'cat','cut','sed','sort','uniq','wc','cp','basename','chmod','grep','rm','mkdir',
                     'nohup','setsid','python3'):
            source = self.root/'mock-bin'/name
            if not source.exists(): source = Path(shutil.which(name))
            (tools/name).symlink_to(source)
        for mode in ('missing', 'incompatible'):
            if mode == 'incompatible':
                (tools/'stat').write_text('#!/bin/sh\nexit 1\n'); (tools/'stat').chmod(0o755)
            for script, action in (('migrate.sh','--plan'), ('migrate.sh','--apply'), ('migrate-job.sh','--start')):
                with self.subTest(mode=mode, script=script, action=action):
                    result = self.run_script(script, action, PATH=str(tools))
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('stat', result.stderr)
                    self.assertNotIn('wget ', self.events())
                    self.assertFalse((self.root/'external-backup').exists())
                    self.assert_untouched()

    def test_bad_bootstrap_rejected_before_feed_or_tool_install(self):
        original = (self.root/'bootstrap.sha256').read_text()
        for bad in ('', '\n'.join(original.splitlines()[:-1])+'\n', original+original.splitlines()[0]+'\n',
                    original.replace('feed.sh', '../feed.sh'), original.replace(original[:64], '0'*64)):
            with self.subTest(manifest=bad[:20]):
                (self.root/'bootstrap.sha256').write_text(bad)
                for action in ('--prepare-tools', '--apply', '--check-bootstrap'):
                    self.assertNotEqual(self.run_script('install.sh', action).returncode, 0)
                self.assertEqual(self.events(), '')
                self.assert_untouched()
        (self.root/'bootstrap.sha256').unlink()
        self.assertNotEqual(self.run_script('install.sh', '--apply').returncode, 0)
        self.assert_untouched()

    def test_modified_bootstrap_script_is_rejected_by_install_and_job(self):
        with (self.root/'migrate.sh').open('a') as stream: stream.write('\n# changed after review\n')
        for script, action in (('install.sh','--apply'), ('migrate-job.sh','--start')):
            self.assertNotEqual(self.run_script(script, action).returncode, 0)
        self.assertEqual(self.events(), '')
        self.assert_untouched()

    def test_signature_policy_required_before_tool_install(self):
        self.delayed_stat()
        path = self.root/'etc/opkg.conf'
        path.write_text(path.read_text().replace('option check_signature\n', ''))
        self.assertNotEqual(self.run_script('install.sh', '--prepare-tools').returncode, 0)
        self.assertEqual(self.events(), '')
        self.assert_untouched()

    def test_first_install_orders_feed_tools_core_then_official_packages(self):
        self.delayed_stat()
        (self.root/'packages.json').write_text('{}')
        (self.root/'usr/bin/mihomo').unlink()
        (self.root/'usr/libexec/mihomo').unlink()
        (self.root/'running').unlink()
        result = self.run_script('install.sh', '--apply')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        events = self.events()
        positions = [events.index(value) for value in ('feed\n', 'opkg install coreutils-stat\n',
                     'install ./mihomo-hfgj.ipk', 'opkg install nikki luci-app-nikki\n', 'opkg install luci-i18n-nikki-zh-cn\n')]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('opkg download', events)
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())), {'mihomo-hfgj'})

    def test_usable_stat_without_package_is_prepared_before_migration(self):
        (self.root/'stat-ready').unlink()
        result = self.run_script('migrate.sh', '--plan')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('registered coreutils-stat', result.stderr)
        self.assertNotIn('wget ', self.events())
        result = self.run_script('install.sh', '--prepare-tools')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('opkg install coreutils-stat\n', self.events())
        self.assert_untouched()

    def test_install_existing_core_uses_exact_download_repair(self):
        result = self.run_script('install.sh', '--apply')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('wget https://fixture.invalid/nikki/mihomo-meta.ipk\n', self.events())
        self.assertNotIn('opkg download', self.events())
        saved = next((self.root/'external-backup').glob('migration.*'))
        self.assertEqual((saved/'mihomo-meta.ipk').read_bytes(), (self.root/'repository/mihomo-meta.ipk').read_bytes())


if __name__ == '__main__': unittest.main()
