"""Exercise router shell scripts using isolated paths and a simulated opkg.

These are transaction tests, not a claim that real opkg/301W has been tested.
"""
import hashlib
import gzip
import json
import os
import re
import subprocess
import tempfile
import time
import signal
import shutil
import unittest
from pathlib import Path
from test_core_feed import ipk, ARCH, VERSION, ROOT

MOCK = r'''#!/usr/bin/env python3
import json, os, shutil, sys, tarfile, io
from pathlib import Path
root = Path(os.environ['ROUTER_FIXTURE'])
args = sys.argv[1:]
if args[:1] == ['--tmp-dir']:
    temporary = Path(args[1])
    if not temporary.is_dir(): sys.exit('Missing explicit opkg workspace')
    args = args[2:]
database = root/'packages.json'
state = json.loads(database.read_text())
with (root/'events').open('a') as stream: stream.write('opkg ' + ' '.join(args) + '\n')
command = args[0]
if command == 'list-installed':
    print('luci-i18n-base-zh-cn - 1')
    sys.exit(0)
if command == 'install' and args[1:] == ['coreutils-stat']:
    if os.environ.get('FAIL_STAT_INSTALL'): sys.exit(17)
    (root/'stat-ready').touch()
    sys.exit(0)
if command == 'install' and args[1:] in (['nikki','luci-app-nikki'], ['luci-i18n-nikki-zh-cn']):
    sys.exit(0)
if command == 'status':
    name = args[1]
    if name == 'coreutils-stat':
        if (root/'stat-ready').exists(): print('Package: coreutils-stat\nVersion: 1\nStatus: install ok installed')
        sys.exit(0)
    if name in state:
        print('Package: '+name+'\nVersion: '+state[name]+'\nArchitecture: aarch64_generic\nStatus: install ok installed\nProvides: mihomo')
    sys.exit(0)
if command == 'compare-versions':
    old,new=args[1],args[3]
    sys.exit(0 if old==new+'~hfgjrestore' and args[2]=='<' else 1)
if command == 'download':
    sys.exit('Name-based package download must not be used')
if command == 'remove':
    name = args[1]
    if name in ('mihomo-hfgj','mihomo-meta','mihomo-alpha','mihomo-hfgj-rollback'): sys.exit('Virtual mihomo dependency still has installed dependers')
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
    if os.environ.get('PAUSE_CORE_INSTALL') and path.name == 'mihomo-hfgj.ipk':
        import time
        (root/'install-paused').touch()
        time.sleep(2)
    with tarfile.open(path, 'r:gz') as outer:
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./control.tar.gz').read()), mode='r:gz') as control:
            fields = dict(line.split(': ',1) for line in control.extractfile('./control').read().decode().splitlines() if ': ' in line)
        name = fields['Package']
        conflicts={v.strip() for v in fields.get('Conflicts','').split(',')}
        replaces={v.strip() for v in fields.get('Replaces','').split(',')}
        for old in list(state):
            if old==name: continue
            if old in conflicts:
                if old not in replaces: sys.exit(9)
                state.pop(old)
        if state.get(name)==fields['Version']: sys.exit(0)
        if name in state and fields['Version']==state[name]+'~hfgjrestore' and '--force-downgrade' not in args: sys.exit(10)
        if path.name=='recovery.ipk' and os.environ.get('FAIL_RECOVERY_INSTALL'): sys.exit(11)
        if path.name in ('mihomo-meta.ipk','mihomo-alpha.ipk') and os.environ.get('FAIL_EXACT_RESTORE'): sys.exit(12)
        if path.name=='mihomo-hfgj.ipk' and os.environ.get('PARTIAL_CORE_INSTALL'):
            database.write_text(json.dumps(state))
            (root/'usr/libexec/mihomo').unlink(missing_ok=True)
            sys.exit(13)
        state[name] = fields['Version']
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./data.tar.gz').read()), mode='r:gz') as data:
            for entry in data:
                if entry.isfile():
                    target = root/entry.name.removeprefix('./')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data.extractfile(entry).read()); target.chmod(entry.mode)
        if path.name=='mihomo-hfgj.ipk' and os.environ.get('BAD_CORE_WRITE'):
            (root/'usr/libexec/mihomo').write_bytes(b'partial corrupt payload')
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
        (self.root/'stat-ready').touch()
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
        control = b'Package: mihomo-meta\nVersion: 1.19.31\nArchitecture: aarch64_generic\nProvides: mihomo\nInstalled-Size: 512\n'
        previous.write_bytes(tar_bytes([('debian-binary', b'2.0\n', 0o644), ('control.tar.gz', tar_bytes([('control',control,0o644)]),0o644), ('data.tar.gz',payload,0o644)]))
        index = ''
        for package in ('mihomo-hfgj',):
            value = (self.root/'repository'/(package+'.ipk')).read_bytes()
            index += f'Package: {package}\nVersion: 1.19.31+hfgj.20261004000000-r1\nSHA256sum: {hashlib.sha256(value).hexdigest()}\nFilename: {package}.ipk\n\n'
        (self.root/'var/opkg-lists/hfgj-core').write_text(index)
        (self.root/'var/opkg-lists/hfgj-core.sig').write_text('signature fixture')
        (self.root/'var/opkg-lists/nikki').write_text('Package: mihomo-meta\nVersion: 1.19.31\nSHA256sum: '+hashlib.sha256(previous.read_bytes()).hexdigest()+'\nFilename: mihomo-meta.ipk\n\n')
        (self.root/'var/opkg-lists/nikki.sig').write_text('signature fixture')
        (self.root/'rollback-package.sh').write_text((ROOT/'rollback-package.sh').read_text())
        if shutil.which('gstat'): self.command('stat', '#!/bin/sh\nexec gstat \"$@\"\n')
        self.command('opkg', MOCK)
        (self.root/'etc/opkg.conf').write_text('option check_signature\nsrc/gz hfgj-core https://fixture.invalid/hfgj\nsrc/gz nikki https://fixture.invalid/nikki\n')
        self.command('wget', r'''#!/usr/bin/env python3
import os,shutil,sys
from pathlib import Path
root=Path(os.environ['ROUTER_FIXTURE']);args=sys.argv[1:]
if args[:2]!=['-q','-O'] or len(args)!=4:sys.exit('Unsupported exact download')
with (root/'events').open('a') as f:f.write('wget '+args[-1]+'\n')
source=root/'repository'/args[-1].rsplit('/',1)[-1]
if not source.is_file():sys.exit(1)
shutil.copy(source,args[2])
''')
        self.command('id', '#!/bin/sh\necho 0\n')
        self.command('uname', '#!/bin/sh\necho aarch64\n')
        self.command('usign', '#!/bin/sh\nexit 0\n')
        # Enforce Linux's same-file rename rejection on macOS fixtures too.
        self.command('mv', '#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\na,b=map(Path,sys.argv[-2:])\nif a.resolve()==b.resolve(): sys.exit("same-file rename rejected")\na.replace(b)\n')
        self.command('df', r'''#!/bin/sh
for path; do :; done
free=${FIXTURE_FREE_KIB:-1999999}
fs=storage
case "$path" in
    */usr/libexec)
        fs=core
        free=${FIXTURE_CORE_FREE_KIB:-$free}
        if [ ! -f "$ROUTER_FIXTURE/running" ]; then free=${FIXTURE_CORE_AFTER_STOP_FREE_KIB:-$free}; fi ;;
    */external-work*) fs=work; free=${FIXTURE_WORK_FREE_KIB:-$free} ;;
esac
echo 'Filesystem 1024-blocks Used Available Capacity Mounted'
echo "$fs 2000000 1 $free 1% /"
''')
        self.command('setsid', '#!/usr/bin/env python3\nimport os,sys\nos.setsid(); os.execvp(sys.argv[1],sys.argv[1:])\n')
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
        script = re.sub(r'/(?:etc|usr|var|root|overlay|proc)(?=/|[ )\n])', lambda match: str(self.root)+match[0], script)
        script = script.replace(' -C /etc nikki', f' -C {self.root}/etc nikki')
        script = script.replace('check_space /usr/libexec ', f'check_space {self.root}/usr/libexec ')
        script = script.replace(f'300:{self.root}/usr/bin/mihomo:{self.root}/usr/libexec/mihomo', '300:/usr/bin/mihomo:/usr/libexec/mihomo')
        (self.root/'migrate.sh').write_text(script)
        (self.root/'proc').mkdir()
        (self.root/'proc/mounts').write_text('')
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

    def test_compressed_core_and_rollback_indexes(self):
        for name in ('hfgj-core', 'nikki'):
            index = self.root/'var/opkg-lists'/name
            index.write_bytes(gzip.compress(index.read_bytes()))
        result = self.run_migration('--apply')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.root/'running').exists())
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())), {'mihomo-hfgj'})

    def test_invalid_index_signature_never_stops_service(self):
        self.command('usign', '#!/bin/sh\nexit 1\n')
        result = self.run_migration('--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('service stop', (self.root/'events').read_text())
        self.assertTrue((self.root/'running').exists())
        self.assertEqual(json.loads((self.root/'packages.json').read_text()), {'mihomo-meta': '1.19.31'})

    def test_success_and_explicit_rollback_restore_package_and_actual_bytes(self):
        result = self.run_migration('--apply')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = json.loads((self.root/'packages.json').read_text())
        self.assertEqual(set(state), {'mihomo-hfgj'})
        backup = next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        result = self.run_migration('--rollback', str(backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root/'packages.json').read_text()), {'mihomo-meta': '1.19.31'})
        self.assertEqual((self.root/'usr/libexec/mihomo').read_bytes(), self.old)
        self.assertTrue((self.root/'running').exists())

    def test_failed_install_or_start_rolls_back(self):
        for failure in ('FAIL_CORE_INSTALL','FAIL_NEW_START','INTERRUPT_CORE_INSTALL','PARTIAL_CORE_INSTALL','BAD_CORE_WRITE'):
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

    def test_legacy_bridge_is_replaced_without_any_remove(self):
        (self.root/'packages.json').write_text(json.dumps({'mihomo-meta':'1.19.31','mihomo-hfgj-rollback':'1'}))
        result=self.run_migration('--apply')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())),{'mihomo-hfgj'})
        events=(self.root/'events').read_text()
        self.assertNotIn('opkg remove',events)
        self.assertNotIn('opkg install ./rollback-provider.ipk',events)

    def test_original_unchanged_failure_and_legacy_backup_only_restore_service(self):
        result=self.run_migration('--apply',FAIL_CORE_INSTALL='1')
        self.assertNotEqual(result.returncode,0)
        backup=next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        self.assertEqual((backup/'transaction').read_text(),'phase=rolled-back-unchanged\n')
        (backup/'state').write_text((backup/'state').read_text().replace('recovery_protocol=2\n',''))
        (backup/'recovery.ipk').unlink()
        (self.root/'running').unlink()
        before=(self.root/'events').read_text()
        result=self.run_migration('--rollback',str(backup))
        self.assertEqual(result.returncode,0,result.stderr)
        events=(self.root/'events').read_text()[len(before):]
        self.assertNotIn('opkg install',events)
        self.assertNotIn('service stop',events)
        self.assertTrue((self.root/'running').exists())

    def test_recovery_stage_failure_reports_failure_and_can_retry(self):
        self.assertEqual(self.run_migration('--apply').returncode,0)
        backup=next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        result=self.run_migration('--rollback',str(backup),FAIL_RECOVERY_INSTALL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.root/'running').exists())
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())),{'mihomo-hfgj'})
        self.assertEqual((backup/'transaction').read_text(),'phase=rollback-failed\n')
        result=self.run_migration('--rollback',str(backup),FAIL_EXACT_RESTORE='1')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.root/'running').exists())
        self.assertEqual(json.loads((self.root/'packages.json').read_text()),{'mihomo-meta':'1.19.31~hfgjrestore'})
        result=self.run_migration('--rollback',str(backup))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.root/'packages.json').read_text()),{'mihomo-meta':'1.19.31'})
        self.assertEqual((backup/'transaction').read_text(),'phase=rolled-back\n')

    def test_backup_of_updater_modified_actual_binary_is_restored(self):
        actual=self.old+b'# local updater fixture\n'
        (self.root/'usr/libexec/mihomo').write_bytes(actual)
        self.assertEqual(self.run_migration('--apply').returncode,0)
        backup=next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        result=self.run_migration('--rollback',str(backup))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'usr/libexec/mihomo').read_bytes(),actual)

    def test_corrupt_recovery_or_legacy_changed_backup_never_stops_current_core(self):
        self.assertEqual(self.run_migration('--apply').returncode,0)
        backup=next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        recovery=(backup/'recovery.ipk').read_bytes()
        before=(self.root/'events').read_text()
        (backup/'recovery.ipk').write_bytes(b'corrupted')
        self.assertNotEqual(self.run_migration('--rollback',str(backup)).returncode,0)
        (backup/'recovery.ipk').write_bytes(recovery)
        (backup/'state').write_text((backup/'state').read_text().replace('recovery_protocol=2\n',''))
        result=self.run_migration('--rollback',str(backup))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Legacy backup',result.stderr)
        self.assertNotIn('service stop',(self.root/'events').read_text()[len(before):])
        self.assertTrue((self.root/'running').exists())

    def test_already_stopped_service_stays_stopped_after_migration_and_rollback(self):
        (self.root/'running').unlink()
        result=self.run_migration('--apply')
        self.assertEqual(result.returncode,0,result.stderr)
        backup=next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        result=self.run_migration('--rollback',str(backup))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'running').exists())
        self.assertNotIn('service stop',(self.root/'events').read_text())

    def test_missing_replaces_is_rejected_before_stop(self):
        from test_core_feed import feed,tar_bytes
        package=self.root/'repository/mihomo-hfgj.ipk'
        members=feed.archive_members(package.read_bytes())
        controls=feed.archive_members(members['control.tar.gz'])
        controls['control']=re.sub(rb'^Replaces:.*\n',b'',controls['control'],flags=re.M)
        members['control.tar.gz']=tar_bytes([(k,v,0o644) for k,v in controls.items()])
        package.write_bytes(tar_bytes([(k,v,0o644) for k,v in members.items()]))
        index=self.root/'var/opkg-lists/hfgj-core'
        index.write_text(re.sub(r'SHA256sum: [0-9a-f]+','SHA256sum: '+hashlib.sha256(package.read_bytes()).hexdigest(),index.read_text()))
        result=self.run_migration('--apply')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('replacement contract',result.stderr)
        self.assertNotIn('service stop',(self.root/'events').read_text())

    def test_insufficient_space_never_stops_service(self):
        result = self.run_migration('--apply', FIXTURE_FREE_KIB='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Insufficient free space', result.stderr)
        self.assertNotIn('service stop', (self.root/'events').read_text())

    def test_corrupt_backup_is_rejected_before_stop(self):
        result = self.run_migration('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        (backup/'core.binary').write_bytes(b'corrupted')
        events = (self.root/'events').read_text()
        result = self.run_migration('--rollback', str(backup))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('service stop', (self.root/'events').read_text()[len(events):])
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())), {'mihomo-hfgj'})

    def geometry(self):
        # Model the measured ~55 MiB files without allocating huge fixture IPKs.
        for file in (self.root/'repository').glob('*.ipk'):
            from test_core_feed import tar_bytes,feed
            members=feed.archive_members(file.read_bytes())
            controls=feed.archive_members(members['control.tar.gz'])
            controls['control']=re.sub(rb'Installed-Size: [0-9]+',b'Installed-Size: 57278590',controls['control'])
            members['control.tar.gz']=tar_bytes([(k,v,0o644) for k,v in controls.items()])
            file.write_bytes(tar_bytes([(k,v,0o644) for k,v in members.items()]))
        for index in (self.root/'var/opkg-lists').glob('*'):
            if index.suffix=='.sig':continue
            text=index.read_text()
            for package in ('mihomo-meta','mihomo-hfgj'):
                file=self.root/'repository'/(package+'.ipk')
                text=re.sub(r'(Package: '+package+r'\n[^\n]*\nSHA256sum: )[0-9a-f]+',lambda m:m[1]+hashlib.sha256(file.read_bytes()).hexdigest(),text)
            index.write_text(text)
        self.command('wc', '#!/bin/sh\nif [ \"${1:-}\" = -l ]; then exec /usr/bin/wc \"$@\"; fi\ncat >/dev/null; echo 57278590\n')
        self.command('du', r'''#!/bin/sh
for path; do :; done
case "$path" in */usr/libexec/mihomo) echo "56592 $path";; *) echo "24477 $path";; esac
''')

    def test_external_storage_with_sufficient_raw_opkg_space(self):
        self.geometry()
        backup=self.root/'external-backup'
        work=self.root/'external-work'
        result=self.run_migration('--apply', HFGJ_BACKUP_DIR=str(backup),HFGJ_WORK_DIR=str(work),
                                  FIXTURE_CORE_FREE_KIB='67000',FIXTURE_CORE_AFTER_STOP_FREE_KIB='67000')
        self.assertEqual(result.returncode,0,result.stderr)
        saved=next(backup.glob('migration.*'))
        self.assertTrue((saved/'core.binary').exists())
        self.assertIn(str(work), (saved/'state').read_text())
        self.assertTrue(list(work.glob('migration.*/candidate')))
        result=self.run_migration('--rollback',str(saved),FIXTURE_CORE_FREE_KIB='67000')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'usr/libexec/mihomo').read_bytes(),self.old)

    def test_raw_opkg_space_check_does_not_credit_removable_core_or_process(self):
        self.geometry()
        result=self.run_migration('--apply',FIXTURE_CORE_FREE_KIB='11004',FIXTURE_CORE_AFTER_STOP_FREE_KIB='67000')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Insufficient free space',result.stderr)
        self.assertNotIn('service stop',(self.root/'events').read_text())

    def test_post_stop_space_failure_restarts_without_package_changes(self):
        self.geometry()
        result=self.run_migration('--apply',FIXTURE_CORE_FREE_KIB='67000',FIXTURE_CORE_AFTER_STOP_FREE_KIB='1')
        self.assertNotEqual(result.returncode,0)
        events=(self.root/'events').read_text()
        self.assertIn('service stop',events)
        self.assertIn('service start',events)
        self.assertNotIn('opkg install',events)
        self.assertEqual(json.loads((self.root/'packages.json').read_text()),{'mihomo-meta':'1.19.31'})
        self.assertTrue((self.root/'running').exists())

    def test_workspace_full_never_stops_and_missing_mount_never_falls_back(self):
        result=self.run_migration('--apply',HFGJ_WORK_DIR=str(self.root/'external-work'),FIXTURE_WORK_FREE_KIB='1')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('service stop',(self.root/'events').read_text())
        mount=self.root/'storage'
        result=self.run_migration('--apply',HFGJ_STORAGE_MOUNT=str(mount),
                                  HFGJ_BACKUP_DIR=str(mount/'backups'),HFGJ_WORK_DIR=str(mount/'work'))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('mount is absent',result.stderr)
        self.assertFalse(mount.exists())

    def test_required_mount_and_shared_storage_budget(self):
        mount=self.root/'storage'
        (self.root/'proc/mounts').write_text(f'/dev/fixture {mount} ext4 rw,relatime 0 0\n')
        options={'HFGJ_STORAGE_MOUNT':str(mount),'HFGJ_BACKUP_DIR':str(mount/'backups'),'HFGJ_WORK_DIR':str(mount/'work')}
        result=self.run_migration('--plan',**options)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(mount.exists())
        # Each individual coarse budget would fit, their sum on one volume does not.
        result=self.run_migration('--apply',FIXTURE_FREE_KIB='200000',**options)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('service stop',(self.root/'events').read_text())

    def test_rollback_space_failure_restarts_current_core_without_removing_it(self):
        self.assertEqual(self.run_migration('--apply').returncode,0)
        backup=next((self.root/'root/hfgj-core-backups').glob('migration.*'))
        events=(self.root/'events').read_text()
        result=self.run_migration('--rollback',str(backup),FIXTURE_CORE_FREE_KIB='1')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('opkg remove',(self.root/'events').read_text()[len(events):])
        self.assertEqual(set(json.loads((self.root/'packages.json').read_text())),{'mihomo-hfgj'})
        self.assertTrue((self.root/'running').exists())

    def write_install_script(self):
        script = (ROOT/'install.sh').read_text()
        script = re.sub(r'/(?:etc|usr|var|root|overlay|proc)(?=/|[ )\n])', lambda match: str(self.root)+match[0], script)
        (self.root/'install.sh').write_text(script)

    def setup_job(self, *, fail=False):
        self.write_install_script()
        (self.root/'feed.sh').write_text('#!/bin/sh\nsleep 1\n'+('exit 7\n' if fail else 'exit 0\n'))
        (self.root/'migrate-job.sh').write_text((ROOT/'migrate-job.sh').read_text())
        self.root.joinpath('bootstrap.sha256').write_text(''.join(
            hashlib.sha256((self.root/name).read_bytes()).hexdigest()+'  '+name+'\n'
            for name in ('feed.sh','install.sh','migrate.sh','migrate-job.sh','rollback-package.sh')))
        self.env['HFGJ_BACKUP_DIR']=str(self.root/'external-backup')
        self.env['HFGJ_WORK_DIR']=str(self.root/'external-work')

    def await_job(self):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            jobs=list((self.root/'external-backup/jobs').glob('job.*'))
            # The worker writes its final status before releasing the lock.
            # Wait for cleanup too before asserting that the job has finished.
            if (jobs and (jobs[0]/'status').exists()
                    and 'exit_code=' in (jobs[0]/'status').read_text()
                    and not (jobs[0].parent/'active.lock').exists()): return jobs[0]
            time.sleep(.1)
        self.fail('Detached worker did not finish cleanup')

    def test_final_status_does_not_finish_wait_before_unlock(self):
        from unittest.mock import patch
        jobs=self.root/'external-backup/jobs'
        job=jobs/'job.fixture'
        job.mkdir(parents=True)
        (job/'status').write_text('exit_code=143\n')
        lock=jobs/'active.lock'
        lock.mkdir()
        # Model the exact window between publishing the exit code and unlock.
        with patch('test_migration.time.sleep',side_effect=lambda _:lock.rmdir()) as wait:
            self.assertEqual(self.await_job(),job)
            wait.assert_called_once_with(.1)
        self.assertFalse(lock.exists())

    def test_detached_job_survives_launcher_session_disconnect(self):
        self.setup_job()
        parent=subprocess.Popen(['sh','-c','sh "$1" --start; sleep 30','sh',str(self.root/'migrate-job.sh')],
                                env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            deadline=time.monotonic()+5
            while not list((self.root/'external-backup/jobs').glob('job.*/status')) and time.monotonic()<deadline: time.sleep(.05)
            time.sleep(.2)
            os.killpg(parent.pid,signal.SIGTERM)
            parent.wait(timeout=3)
            job=self.await_job()
            self.assertEqual((job/'status').read_text(),'exit_code=0\n',(job/'job.log').read_text())
            self.assertEqual(set(json.loads((self.root/'packages.json').read_text())),{'mihomo-hfgj'})
            self.assertFalse((job.parent/'active.lock').exists())
        finally:
            if parent.poll() is None: os.killpg(parent.pid,signal.SIGKILL);parent.wait()

    def test_detached_job_records_failure_and_rejects_duplicate_launch(self):
        self.setup_job(fail=True)
        launch=lambda:subprocess.run(['sh',str(self.root/'migrate-job.sh'),'--start'],env=self.env,capture_output=True,text=True,timeout=5)
        first=launch(); self.assertEqual(first.returncode,0,first.stderr)
        second=launch(); self.assertNotEqual(second.returncode,0)
        self.assertIn('Another job',second.stderr)
        job=self.await_job()
        self.assertEqual((job/'status').read_text(),'exit_code=7\n')
        self.assertTrue((self.root/'running').exists())
    def test_cancel_worker_waits_for_migration_rollback_before_unlocking(self):
        self.setup_job()
        self.env['PAUSE_CORE_INSTALL']='1'
        result=subprocess.run(['sh',str(self.root/'migrate-job.sh'),'--start'],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        deadline=time.monotonic()+8
        while not (self.root/'install-paused').exists() and time.monotonic()<deadline: time.sleep(.05)
        self.assertTrue((self.root/'install-paused').exists())
        job=next((self.root/'external-backup/jobs').glob('job.*'))
        pid=int(re.search(r'pid=(\d+)',(job/'status').read_text())[1])
        os.kill(pid,signal.SIGTERM)
        job=self.await_job()
        self.assertEqual((job/'status').read_text(),'exit_code=143\n',(job/'job.log').read_text())
        self.assertEqual(json.loads((self.root/'packages.json').read_text()),{'mihomo-meta':'1.19.31'})
        self.assertTrue((self.root/'running').exists())
        self.assertFalse((job.parent/'active.lock').exists())

    def test_job_rejects_modified_script_before_launch(self):
        self.setup_job()
        (self.root/'migrate.sh').write_text((self.root/'migrate.sh').read_text()+'\n# unexpected change\n')
        result=subprocess.run(['sh',str(self.root/'migrate-job.sh'),'--start'],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'external-backup/jobs').exists())
        self.assertTrue((self.root/'running').exists())



if __name__ == '__main__':
    unittest.main()
