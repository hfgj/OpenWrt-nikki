"""Run the real setup entry with isolated router paths and mocked package/transport tools."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_core_feed import ROOT, feed

KEY_HASH = '5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0'


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('etc', 'sbin', 'root', 'usr/bin', 'usr/libexec', 'bin', 'published'):
            (self.root/directory).mkdir(parents=True)
        (self.root/'etc/openwrt_release').write_text("DISTRIB_RELEASE='24.10.2'\nDISTRIB_ARCH='aarch64_cortex-a53'\n")
        (self.root/'etc/opkg.conf').write_text('option check_signature\n')
        (self.root/'packages.json').write_text('{}')
        (self.root/'sbin/fw4').write_text('#!/bin/sh\nexit 0\n')
        (self.root/'sbin/fw4').chmod(0o755)
        script = re.sub(r'/(?:etc|usr|root|sbin)(?=/|[ )\n])', lambda m: str(self.root)+m[0], (ROOT/'setup.sh').read_text())
        self.entry = self.root/'entry.sh'
        self.entry.write_text(script)
        self.published = self.root/'published'
        (self.published/'setup.sh').write_text(script)
        for name in feed.BOOTSTRAP_FILES:
            if name != 'setup.sh': (self.published/name).write_text('#!/bin/sh\nexit 0\n')
        (self.published/'install.sh').write_text('''#!/bin/sh
printf '%s\\n' "$*" > "$ROUTER_FIXTURE/invoked"
printf '%s\\n' "$HFGJ_KEY_SHA256" > "$ROUTER_FIXTURE/pinned-key"
[ "$1" = --apply-fresh ] || exit 9
exit "${INSTALL_EXIT:-0}"
''')
        (self.published/'key-build.pub').write_text('public key fixture')
        self.manifest()
        self.tool('id', '#!/bin/sh\necho "${TEST_UID:-0}"\n')
        self.tool('uname', '#!/bin/sh\ncase "$1" in -m) echo aarch64 ;; -r) echo "${TEST_KERNEL:-6.6.100}" ;; *) exit 1 ;; esac\n')
        self.tool('df', '#!/bin/sh\nprintf "Filesystem Blocks Used Available Use%% Mounted\\n%s 999999 1 %s 1%% /\\n" "${TEST_FILESYSTEM:-storage}" "${TEST_FREE_KIB:-999998}"\n')
        for name in ('usign', 'jsonfilter'): self.tool(name, '#!/bin/sh\nexit 0\n')
        self.tool('opkg', r'''#!/usr/bin/env python3
import os,json,sys
from pathlib import Path
root=Path(os.environ['ROUTER_FIXTURE'])
if os.environ.get('STATUS_FAIL'): sys.exit(7)
if sys.argv[1]!='status': sys.exit('Only status is allowed in setup checks')
name=sys.argv[2];state=json.loads((root/'packages.json').read_text())
if name in state: print('Package: '+name+'\nStatus: install ok installed')
''')
        real_sha = shutil.which('sha256sum')
        assert real_sha
        self.tool('sha256sum', f'''#!/bin/sh
if [ "$1" = key-build.pub ]; then
    printf '%s  key-build.pub\\n' "${{TEST_KEY_HASH:-{KEY_HASH}}}"
else
    exec {real_sha} "$@"
fi
''')
        self.tool('wget', r'''#!/usr/bin/env python3
import os,shutil,sys
from pathlib import Path
root=Path(os.environ['ROUTER_FIXTURE']);name=sys.argv[-1].rsplit('/',1)[-1]
with (root/'downloads').open('a') as f: f.write(name+'\n')
if name==os.environ.get('DOWNLOAD_FAIL'): sys.exit(8)
shutil.copyfile(root/'published'/name,sys.argv[3])
''')
        self.env = dict(os.environ, ROUTER_FIXTURE=str(self.root), PATH=str(self.root/'bin')+os.pathsep+os.environ['PATH'])
        for name in ('HFGJ_FEED_URL','HFGJ_OFFICIAL_URL','HFGJ_KEY_SHA256'):
            self.env[name] = 'https://untrusted.invalid'

    def tool(self, name, text):
        path=self.root/'bin'/name; path.write_text(text); path.chmod(0o755)

    def manifest(self):
        (self.published/'bootstrap.sha256').write_text(''.join(
            hashlib.sha256((self.published/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in feed.BOOTSTRAP_FILES))

    def run_entry(self, *args, **env):
        return subprocess.run(['sh', str(self.entry), *args], env=dict(self.env, **env), capture_output=True, text=True, timeout=10)

    def assert_not_installed(self):
        self.assertFalse((self.root/'invoked').exists())

    def assert_no_download(self):
        self.assert_not_installed()
        self.assertFalse((self.root/'downloads').exists())
        self.assertEqual(list((self.root/'root').iterdir()), [])

    def test_clean_router_invokes_fresh_only_and_pins_key(self):
        result=self.run_entry()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((self.root/'invoked').read_text().strip(), '--apply-fresh')
        self.assertEqual((self.root/'pinned-key').read_text().strip(), KEY_HASH)
        directories=list((self.root/'root').glob('hfgj-install.*'))
        self.assertEqual(len(directories), 1)
        self.assertTrue((directories[0]/'install.log').is_file())

    def test_existing_registered_core_only_shows_migration(self):
        for package in ('mihomo-meta','mihomo-alpha','nikki','luci-app-nikki','mihomo-hfgj-rollback','mihomo'):
            with self.subTest(package=package):
                (self.root/'packages.json').write_text(json.dumps({package:'1'}))
                result=self.run_entry()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('迁移计划', result.stdout)
                self.assert_no_download()

    def test_existing_hfgj_does_not_repeat_installation(self):
        (self.root/'packages.json').write_text('{"mihomo-hfgj":"1"}')
        result=self.run_entry()
        self.assertEqual(result.returncode, 0)
        self.assertIn('LuCI', result.stdout)
        self.assert_no_download()

    def test_unregistered_core_and_broken_link_block_install(self):
        for relative in ('usr/bin/mihomo','usr/libexec/mihomo'):
            path=self.root/relative
            for broken in (False,True):
                with self.subTest(path=relative,broken=broken):
                    if broken: path.symlink_to(self.root/'missing')
                    else: path.write_text('old binary')
                    result=self.run_entry()
                    self.assertIn('迁移计划', result.stdout)
                    self.assert_no_download()
                    path.unlink()

    def test_residual_nikki_configuration_blocks_install(self):
        (self.root/'etc/nikki').mkdir()
        result=self.run_entry()
        self.assertIn('迁移计划', result.stdout)
        self.assert_no_download()

    def test_download_failure_stops_before_install(self):
        result=self.run_entry(DOWNLOAD_FAIL='migrate.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('下载失败', result.stderr)
        self.assert_not_installed()

    def test_corrupt_bundle_stops_before_install(self):
        (self.published/'migrate.sh').write_text('tampered')
        result=self.run_entry()
        self.assertNotEqual(result.returncode, 0)
        self.assert_not_installed()

    def test_empty_duplicate_and_incomplete_manifest_stop(self):
        original=(self.published/'bootstrap.sha256').read_text()
        for value in ('', original+original.splitlines()[0]+'\n', '\n'.join(original.splitlines()[:-1])+'\n'):
            (self.published/'bootstrap.sha256').write_text(value)
            self.assertNotEqual(self.run_entry().returncode, 0)
            self.assert_not_installed()

    def test_wrong_public_key_stops_before_install(self):
        result=self.run_entry(TEST_KEY_HASH='0'*64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('公钥', result.stderr)
        self.assert_not_installed()

    def test_changed_published_entry_requires_redownload(self):
        with (self.published/'setup.sh').open('a') as stream: stream.write('\n# changed release\n')
        self.manifest()
        result=self.run_entry()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('重新下载', result.stderr)
        self.assert_not_installed()

    def test_package_read_failure_stops(self):
        self.assertNotEqual(self.run_entry(STATUS_FAIL='1').returncode, 0)
        self.assert_no_download()

    def test_unsupported_firmware_and_architecture_stop(self):
        for release,arch in [('25.12.0','aarch64_cortex-a53'),('24.10.2','aarch64_generic'),('24.10evil','aarch64_cortex-a53')]:
            (self.root/'etc/openwrt_release').write_text(f"DISTRIB_RELEASE='{release}'\nDISTRIB_ARCH='{arch}'\n")
            self.assertNotEqual(self.run_entry().returncode, 0)
            self.assert_no_download()

    def test_old_kernel_missing_firewall_and_signature_policy_stop(self):
        self.assertNotEqual(self.run_entry(TEST_KERNEL='5.10.0').returncode, 0)
        self.assert_no_download()
        (self.root/'etc/opkg.conf').write_text('')
        self.assertNotEqual(self.run_entry().returncode, 0)
        self.assert_no_download()
        (self.root/'etc/opkg.conf').write_text('option check_signature\n')
        (self.root/'sbin/fw4').unlink()
        self.assertNotEqual(self.run_entry().returncode, 0)
        self.assert_no_download()

    def test_low_or_volatile_storage_stops(self):
        for env in ({'TEST_FREE_KIB':'100'},{'TEST_FILESYSTEM':'tmpfs'}):
            self.assertNotEqual(self.run_entry(**env).returncode, 0)
            self.assert_no_download()

    def test_install_failure_keeps_log_and_exit_code(self):
        result=self.run_entry(INSTALL_EXIT='17')
        self.assertEqual(result.returncode, 17)
        self.assertIn('安装失败', result.stderr)
        self.assertTrue(next((self.root/'root').glob('hfgj-install.*')).joinpath('install.log').is_file())

    def test_unexpected_arguments_and_non_root_stop(self):
        self.assertNotEqual(self.run_entry('--migrate').returncode, 0)
        self.assertNotEqual(self.run_entry(TEST_UID='1000').returncode, 0)
        self.assert_no_download()
