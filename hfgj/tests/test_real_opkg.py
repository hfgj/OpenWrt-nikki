"""Actual opkg + production recovery helper in disposable offline roots.

No router services or host package database are accessed. Fixture scripts exit 0.
CI builds the pinned opkg; local users can set HFGJ_TEST_OPKG to the same build.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_core_feed import ROOT, tar_bytes, feed


def package(path, name, version, payload, extra=''):
    control = (f'Package: {name}\nVersion: {version}\nArchitecture: all\n'
               'Installed-Size: 256\nDescription: disposable integration fixture\n' + extra)
    path.write_bytes(tar_bytes([
        ('debian-binary', b'2.0\n', 0o644),
        ('control.tar.gz', tar_bytes([('control', control.encode(), 0o644),
                                    ('postinst', b'#!/bin/sh\nexit 0\n', 0o755)]), 0o644),
        ('data.tar.gz', tar_bytes(payload), 0o644),
    ]))


class RealOpkgTests(unittest.TestCase):
    def setUp(self):
        self.opkg = os.environ.get('HFGJ_TEST_OPKG')
        if not self.opkg:
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                self.fail('CI must build pinned opkg, not skip the integration test')
            self.skipTest('Set HFGJ_TEST_OPKG to the pinned native opkg build')
        self.assertIn('38eccbb1', subprocess.check_output([self.opkg, '--version'], text=True))

    def scenario(self, name, version, bridge=False):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory).resolve()
            root = work / 'root'
            (root / 'usr/lib/opkg').mkdir(parents=True)
            (root / 'usr/lib/opkg/status').touch()
            (root / 'tmp').mkdir()
            (work / 'tmp').mkdir()
            (work / 'lists').mkdir()
            (work / 'tools').mkdir()
            env = dict(os.environ, PATH=str(work/'tools')+':'+os.environ['PATH'])
            if shutil.which('gstat'):
                (work / 'tools/stat').symlink_to(shutil.which('gstat'))
            cfg = work / 'opkg.conf'
            cfg.write_text(f'arch all 1\ndest root /\nlists_dir ext {work}/lists\n')
            other = 'mihomo-alpha' if name == 'mihomo-meta' else 'mihomo-meta'
            original, candidate, recovery = (work / v for v in ('original.ipk','candidate.ipk','recovery.ipk'))
            old_bytes, new_bytes = b'original executable\n', b'candidate executable\n'
            extra = 'Provides: mihomo\nConflicts: '+other+'\n'
            if name == 'mihomo-hfgj':
                extra = 'Provides: mihomo\nConflicts: mihomo-meta, mihomo-alpha, mihomo-hfgj-rollback\nReplaces: mihomo-meta, mihomo-alpha, mihomo-hfgj-rollback\n'
            package(original, name, version, [('usr/libexec/mihomo',old_bytes,0o755)], extra)
            original_hash = feed.digest(original.read_bytes())
            package(candidate, 'mihomo-hfgj', '9.0.0', [('usr/libexec/mihomo',new_bytes,0o755)],
                    'Provides: mihomo\nConflicts: mihomo-meta, mihomo-alpha, mihomo-hfgj-rollback\nReplaces: mihomo-meta, mihomo-alpha, mihomo-hfgj-rollback\n')
            nikki, marker = work / 'nikki.ipk', work / 'bridge.ipk'
            package(nikki, 'nikki', '1', [('usr/share/nikki/marker',b'nikki fixture',0o644)], 'Depends: mihomo\n')
            package(marker, 'mihomo-hfgj-rollback', '1', [('usr/share/mihomo-hfgj-rollback/marker',b'legacy',0o644)], 'Provides: mihomo\n')

            def run(*args, denied=False):
                result = subprocess.run([self.opkg,'-f',str(cfg),'-o',str(root),'--tmp-dir',str(work/'tmp'),*map(str,args)],capture_output=True,text=True)
                output = result.stdout + result.stderr
                if denied:
                    self.assertNotEqual(result.returncode,0,output)
                    self.assertIn('is depended upon by packages',output)
                else:
                    self.assertEqual(result.returncode,0,output)
                    self.assertNotIn('Collected errors:',output)
                return result

            def records():
                result = {}
                for paragraph in (root/'usr/lib/opkg/status').read_text().strip().split('\n\n'):
                    fields=dict(line.split(': ',1) for line in paragraph.splitlines() if ': ' in line)
                    if fields.get('Status','').endswith(' installed'):
                        result[fields['Package']]=fields
                return result

            def check(core, content, has_bridge=False):
                state=records()
                self.assertEqual(set(state),{core,'nikki'} | ({'mihomo-hfgj-rollback'} if has_bridge else set()))
                self.assertEqual(state['nikki']['Depends'],'mihomo')
                self.assertEqual((root/'usr/share/nikki/marker').read_bytes(),b'nikki fixture')
                self.assertEqual((root/'usr/libexec/mihomo').read_bytes(),content)
                self.assertEqual((root/'usr/libexec/mihomo').stat().st_mode & 0o777,0o755)
                return state[core]

            run('install',original)
            run('install',nikki)
            if bridge: run('install',marker)
            check(name,old_bytes,bridge)
            run('remove',name,denied=True)
            check(name,old_bytes,bridge)
            subprocess.run(['sh',str(ROOT/'rollback-package.sh'),'--build',str(original),str(recovery),str(work)],check=True,capture_output=True,env=env)
            subprocess.run(['sh',str(ROOT/'rollback-package.sh'),'--verify',str(original),str(recovery),str(work)],check=True,capture_output=True,env=env)
            self.assertEqual(feed.archive_members(original.read_bytes())['data.tar.gz'],feed.archive_members(recovery.read_bytes())['data.tar.gz'])
            old_controls=feed.archive_members(feed.archive_members(original.read_bytes())['control.tar.gz'])
            new_controls=feed.archive_members(feed.archive_members(recovery.read_bytes())['control.tar.gz'])
            self.assertEqual(old_controls['postinst'],new_controls['postinst'])
            run('compare-versions',version+'~hfgjrestore','<',version)
            run('install',candidate)
            check('mihomo-hfgj',new_bytes)
            self.assertFalse((root/'usr/share/mihomo-hfgj-rollback/marker').exists())
            run('install','--force-downgrade',recovery)
            self.assertEqual(check(name,old_bytes)['Version'],version+'~hfgjrestore')
            run('install',original)
            final=check(name,old_bytes)
            self.assertEqual(final['Version'],version)
            for key in ('Provides','Conflicts','Replaces'):
                expected=dict(line.split(': ',1) for line in old_controls['control'].decode().splitlines() if ': ' in line).get(key)
                self.assertEqual(final.get(key),expected)
            self.assertEqual(feed.digest(original.read_bytes()),original_hash)

    def test_official_core_replacement_and_exact_recovery(self):
        for name, versions in [('mihomo-meta',['1.19.31','1.19.31-r1']),('mihomo-alpha',['2026.10.04','2026.10.04-r1'])]:
            for version in versions:
                for bridge in (False,True):
                    with self.subTest(package=name,version=version,legacy_bridge=bridge):
                        self.scenario(name,version,bridge)

    def test_same_name_hfgj_downgrade_and_exact_recovery(self):
        self.scenario('mihomo-hfgj','1.19.31+hfgj.20261004000000-r1')

    def test_migration_script_with_actual_opkg_and_fake_service(self):
        # Only downloads/service/DNS/firmware paths are fixtures. Install/status/
        # version comparison/replacement/recovery use the real package manager.
        from test_migration import MigrationTests
        router=MigrationTests('test_plan_is_read_only')
        router.setUp()
        self.addCleanup(router.tearDown)
        (router.root/'tmp').mkdir()
        (router.root/'usr/lib/opkg/status').write_text('')
        cfg=router.root/'real-opkg.conf'
        (router.root/'lists').mkdir()
        cfg.write_text(f'arch aarch64_generic 10\ndest root /\nlists_dir ext {router.root}/lists\n')
        router.env.update(HFGJ_TEST_OPKG=self.opkg,HFGJ_REAL_CONFIG=str(cfg))
        # Real opkg implements alternatives; retain official-style alternative
        # metadata and interpret its absolute link inside the virtual router root.
        original=router.root/'repository/mihomo-meta.ipk'
        members=feed.archive_members(original.read_bytes())
        controls=feed.archive_members(members['control.tar.gz'])
        controls['control']+=b'Alternatives: 100:/usr/bin/mihomo:/usr/libexec/mihomo\n'
        members['control.tar.gz']=tar_bytes([(k,v,0o644) for k,v in controls.items()])
        original.write_bytes(tar_bytes([(k,v,0o644) for k,v in members.items()]))
        index=router.root/'var/opkg-lists/nikki'
        import re
        index.write_text(re.sub(r'SHA256sum: [0-9a-f]+','SHA256sum: '+feed.digest(original.read_bytes()),index.read_text()))
        router.command('readlink', r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(os.environ['ROUTER_FIXTURE']);path=Path(sys.argv[-1])
target=os.readlink(path) if path.is_symlink() else str(path)
if target.startswith('/usr/'):target=str(root)+target
print(Path(target).resolve())
''')
        subprocess.run([self.opkg,'-f',str(cfg),'-o',str(router.root),'install',str(router.root/'repository/mihomo-meta.ipk')],check=True,capture_output=True)
        nikki=router.root/'nikki.ipk'
        package(nikki,'nikki','1',[('usr/share/nikki/marker',b'nikki fixture',0o644)],'Depends: mihomo\n')
        cfg.write_text('arch all 1\n'+cfg.read_text())
        subprocess.run([self.opkg,'-f',str(cfg),'-o',str(router.root),'install',str(nikki)],check=True,capture_output=True)
        router.command('opkg', r'''#!/usr/bin/env python3
import os,shutil,subprocess,sys
from pathlib import Path
root=Path(os.environ['ROUTER_FIXTURE']);args=sys.argv[1:]
with (root/'events').open('a') as f:f.write('real-opkg '+' '.join(args)+'\n')
command_args=args[2:] if args[:1]==['--tmp-dir'] else args
if command_args[0]=='download':
    source=root/'repository'/(command_args[1]+'.ipk')
    if not source.exists():sys.exit(1)
    shutil.copy(source,Path.cwd()/source.name);sys.exit(0)
sys.exit(subprocess.run([os.environ['HFGJ_TEST_OPKG'],'-f',os.environ['HFGJ_REAL_CONFIG'],'-o',str(root),*args]).returncode)
''')
        for failure in (False,True):
            with self.subTest(failed_candidate_start=failure):
                before=set((router.root/'root/hfgj-core-backups').glob('migration.*')) if (router.root/'root').exists() else set()
                result=router.run_migration('--apply',**({'FAIL_NEW_START':'1'} if failure else {}))
                self.assertEqual(result.returncode==0,not failure,result.stdout+result.stderr)
                backup=next(iter(set((router.root/'root/hfgj-core-backups').glob('migration.*'))-before))
                if not failure:
                    self.assertEqual((router.root/'usr/libexec/mihomo').read_bytes(),router.new)
                    result=router.run_migration('--rollback',str(backup))
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual((router.root/'usr/libexec/mihomo').read_bytes(),router.old)
                self.assertTrue((router.root/'running').exists())
                status=(router.root/'usr/lib/opkg/status').read_text()
                self.assertIn('Package: mihomo-meta\n',status)
                self.assertIn('Package: nikki\n',status)
                self.assertNotIn('Package: mihomo-hfgj\n',status)
                self.assertNotIn('~hfgjrestore',status)
                self.assertEqual((backup/'transaction').read_text(),'phase=rolled-back\n')


if __name__ == '__main__': unittest.main()
