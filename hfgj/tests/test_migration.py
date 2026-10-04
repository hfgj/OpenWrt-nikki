"""Exercise router shell scripts using isolated paths and a simulated opkg.

These are transaction tests, not a claim that real opkg/301W has been tested.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_core_feed import ipk, ARCH, VERSION, ROOT

MOCK = r'''#!/usr/bin/env python3
import json, os, shutil, sys, tarfile, io
from pathlib import Path
root = Path(os.environ['ROUTER_FIXTURE'])
args = sys.argv[1:]
database = root/'packages.json'
state = json.loads(database.read_text())
with (root/'events').open('a') as stream: stream.write('opkg ' + ' '.join(args) + '\n')
command = args[0]
if command == 'status':
    name = args[1]
    if name in state:
        print('Package: '+name+'\nVersion: '+state[name]+'\nArchitecture: aarch64_generic\nStatus: install ok installed\nProvides: mihomo')
    sys.exit(0)
if command == 'download':
    path = root/'repository'/(args[1]+'.ipk')
    if not path.exists(): sys.exit(1)
    shutil.copy(path, Path.cwd()/path.name)
    sys.exit(0)
if command == 'remove':
    name = args[1]
    if name in ('mihomo-hfgj','mihomo-meta','mihomo-alpha') and 'mihomo-hfgj-rollback' not in state: sys.exit(3)
    state.pop(name, None)
    if name != 'mihomo-hfgj-rollback':
        (root/'usr/bin/mihomo').unlink(missing_ok=True)
        (root/'usr/libexec/mihomo').unlink(missing_ok=True)
elif command == 'install':
    path = Path(args[-1])
    if os.environ.get('INTERRUPT_CORE_INSTALL') and path.name == 'mihomo-hfgj.ipk':
        import signal
        os.kill(os.getppid(), signal.SIGTERM)
        sys.exit(143)
    if os.environ.get('FAIL_CORE_INSTALL') and path.name == 'mihomo-hfgj.ipk': sys.exit(7)
    with tarfile.open(path, 'r:gz') as outer:
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./control.tar.gz').read()), mode='r:gz') as control:
            fields = dict(line.split(': ',1) for line in control.extractfile('./control').read().decode().splitlines() if ': ' in line)
        name = fields['Package']
        if name in ('mihomo-meta','mihomo-alpha') and 'mihomo-hfgj' in state: sys.exit(9)
        if name == 'mihomo-hfgj' and ('mihomo-meta' in state or 'mihomo-alpha' in state): sys.exit(9)
        if name == 'mihomo-hfgj':
            state.pop('mihomo-meta', None); state.pop('mihomo-alpha', None)
        state[name] = fields['Version']
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./data.tar.gz').read()), mode='r:gz') as data:
            for entry in data:
                if entry.isfile():
                    target = root/entry.name.removeprefix('./')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data.extractfile(entry).read()); target.chmod(entry.mode)
        if name != 'mihomo-hfgj-rollback':
            link = root/'usr/bin/mihomo'
            link.unlink(missing_ok=True); link.symlink_to(root/'usr/libexec/mihomo')
else: sys.exit(8)
database.write_text(json.dumps(state))
'''


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for name in ('etc/nikki', 'etc/config', 'usr/bin', 'usr/libexec', 'var/opkg-lists', 'usr/lib/opkg', 'repository', 'mock-bin', 'etc/init.d'):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        (self.root/'etc/openwrt_release').write_text("DISTRIB_RELEASE='24.10.4'\nDISTRIB_ARCH='aarch64_generic'\n")
        (self.root/'etc/config/nikki').write_text('private fixture configuration')
        (self.root/'usr/lib/opkg/status').write_text('fixture package status')
        self.old = b'#!/bin/sh\necho "Mihomo Meta v1.19.31 linux arm64"\n'
        self.new = f'#!/bin/sh\necho "Mihomo Meta {VERSION} linux arm64"\n'.encode()
        (self.root/'usr/libexec/mihomo').write_bytes(self.old)
        (self.root/'usr/libexec/mihomo').chmod(0o755)
        (self.root/'usr/bin/mihomo').symlink_to(self.root/'usr/libexec/mihomo')
        (self.root/'packages.json').write_text(json.dumps({'mihomo-meta': '1.19.31'}))
        metadata = {'core_version': VERSION, 'binary_sha256': hashlib.sha256(self.new).hexdigest(), 'package_version': '1.19.31+hfgj.20261004000000'}
        ipk(self.root/'repository/mihomo-hfgj.ipk', metadata, self.new)
        ipk(self.root/'repository/mihomo-hfgj-rollback.ipk', metadata, self.new, bridge=True)
        old_metadata = dict(metadata, package_version='1.19.31')
        ipk(self.root/'repository/mihomo-meta.ipk', old_metadata, self.old)
        # The rollback fixture's control must name the actual previous package.
        import io, tarfile
        from test_core_feed import tar_bytes
        previous = self.root/'repository/mihomo-meta.ipk'
        with tarfile.open(previous, 'r:gz') as archive:
            payload = archive.extractfile('./data.tar.gz').read()
        control = b'Package: mihomo-meta\nVersion: 1.19.31\nArchitecture: aarch64_generic\nProvides: mihomo\n'
        previous.write_bytes(tar_bytes([('debian-binary', b'2.0\n', 0o644), ('control.tar.gz', tar_bytes([('control',control,0o644)]),0o644), ('data.tar.gz',payload,0o644)]))
        index = ''
        for package in ('mihomo-hfgj','mihomo-hfgj-rollback'):
            value = (self.root/'repository'/(package+'.ipk')).read_bytes()
            index += f'Package: {package}\nVersion: 1.19.31+hfgj.20261004000000-r1\nSHA256sum: {hashlib.sha256(value).hexdigest()}\n\n'
        (self.root/'var/opkg-lists/hfgj-core').write_text(index)
        (self.root/'var/opkg-lists/hfgj-core.sig').write_text('signature fixture')
        (self.root/'var/opkg-lists/nikki').write_text('Package: mihomo-meta\nVersion: 1.19.31\nSHA256sum: '+hashlib.sha256(previous.read_bytes()).hexdigest()+'\n\n')
        (self.root/'var/opkg-lists/nikki.sig').write_text('signature fixture')
        self.command('opkg', MOCK)
        self.command('id', '#!/bin/sh\necho 0\n')
        self.command('uname', '#!/bin/sh\necho aarch64\n')
        self.command('usign', '#!/bin/sh\nexit 0\n')
        self.command('df', '#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted"\necho "fixture 2000000 1 ${FIXTURE_FREE_KIB:-1999999} 1% /"\n')
        self.command('jsonfilter', '#!/usr/bin/env python3\nimport json,sys\nx=json.load(open(sys.argv[2])); print(x[sys.argv[4].removeprefix("@.")])\n')
        service = r'''#!/bin/sh
echo "service $1" >> "$ROUTER_FIXTURE/events"
case "$1" in
running) [ -f "$ROUTER_FIXTURE/running" ] ;;
stop) rm -f "$ROUTER_FIXTURE/running" ;;
start)
  if [ -n "${FAIL_NEW_START:-}" ] && "$ROUTER_FIXTURE/usr/libexec/mihomo" -v | grep -q hfgj; then exit 1; fi
  touch "$ROUTER_FIXTURE/running" ;;
esac
'''
        (self.root/'etc/init.d/nikki').write_text(service)
        (self.root/'etc/init.d/nikki').chmod(0o755)
        (self.root/'running').touch()
        script = (ROOT/'migrate.sh').read_text()
        script = re.sub(r'/(?:etc|usr|var|root)/', lambda match: str(self.root)+match[0], script)
        script = script.replace(' -C /etc nikki', f' -C {self.root}/etc nikki')
        script = script.replace('check_space /usr/libexec ', f'check_space {self.root}/usr/libexec ')
        script = script.replace(f'300:{self.root}/usr/bin/mihomo:{self.root}/usr/libexec/mihomo', '300:/usr/bin/mihomo:/usr/libexec/mihomo')
        (self.root/'migrate.sh').write_text(script)
        self.env = dict(os.environ, ROUTER_FIXTURE=str(self.root), PATH=str(self.root/'mock-bin')+':'+os.environ['PATH'])

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, name, contents):
        path = self.root/'mock-bin'/name
        path.write_text(contents)
        path.chmod(0o755)

    def run_migration(self, *arguments, **extra):
        return subprocess.run(['sh', str(self.root/'migrate.sh'), *arguments], env=dict(self.env, **extra), text=True, capture_output=True)

    def test_plan_is_read_only(self):
        result = self.run_migration('--plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root/'running').exists())
        self.assertNotIn('service stop', (self.root/'events').read_text())
        self.assertFalse((self.root/'root').exists())

    def test_success_and_explicit_rollback_restore_package_and_actual_bytes(self):
        result = self.run_migration('--apply')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = json.loads((self.root/'packages.json').read_text())
        self.assertEqual(set(state), {'mihomo-hfgj'})
        backup = next((self.root/'root/hfgj-core-backups').iterdir())
        result = self.run_migration('--rollback', str(backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root/'packages.json').read_text()), {'mihomo-meta': '1.19.31'})
        self.assertEqual((self.root/'usr/libexec/mihomo').read_bytes(), self.old)
        self.assertTrue((self.root/'running').exists())

    def test_failed_install_or_start_rolls_back(self):
        for failure in ('FAIL_CORE_INSTALL','FAIL_NEW_START','INTERRUPT_CORE_INSTALL'):
            with self.subTest(failure=failure):
                result = self.run_migration('--apply', **{failure: '1'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Migration failed', result.stderr)
                self.assertEqual(json.loads((self.root/'packages.json').read_text()), {'mihomo-meta': '1.19.31'}, result.stderr)
                self.assertEqual((self.root/'usr/libexec/mihomo').read_bytes(), self.old)
                self.assertTrue((self.root/'running').exists())

    def test_unavailable_exact_previous_package_never_stops_service(self):
        (self.root/'repository/mihomo-meta.ipk').unlink()
        result = self.run_migration('--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('service stop', (self.root/'events').read_text())
        self.assertTrue((self.root/'running').exists())

    def test_insufficient_space_never_stops_service(self):
        result = self.run_migration('--apply', FIXTURE_FREE_KIB='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Insufficient free space', result.stderr)
        self.assertNotIn('service stop', (self.root/'events').read_text())

    def test_corrupt_backup_is_rejected_before_stop(self):
        result = self.run_migration('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next((self.root/'root/hfgj-core-backups').iterdir())
        (backup/'core.binary').write_bytes(b'corrupted')
        events = (self.root/'events').read_text()
        result = self.run_migration('--rollback', str(backup))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('service stop', (self.root/'events').read_text()[len(events):])
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())), {'mihomo-hfgj'})


if __name__ == '__main__':
    unittest.main()
