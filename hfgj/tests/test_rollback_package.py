"""Local recovery derivation: unchanged compressed payload and maintainer metadata."""
import os
import shutil
import subprocess
import tarfile
import io
import tempfile
import unittest
from pathlib import Path
from test_core_feed import ROOT, tar_bytes, feed


class RecoveryPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name).resolve()
        self.original=self.root/'official.ipk'
        self.recovery=self.root/'recovery.ipk'
        self.env=dict(os.environ)
        tools=self.root/'tools';tools.mkdir()
        if shutil.which('gstat'): (tools/'stat').symlink_to(shutil.which('gstat'))
        self.env['PATH']=str(tools)+':'+os.environ['PATH']
        self.control=b'Package: mihomo-meta\nVersion: 1.19.31-r1\nArchitecture: all\nProvides: mihomo\nInstalled-Size: 128\nConflicts: mihomo-alpha\nDescription: original description\n continuation retained\n'
        self.make_original()

    def tearDown(self): self.temporary.cleanup()

    def make_original(self, control=None, extra=None):
        controls=[('control',control or self.control,0o640),('postinst',b'#!/bin/sh\nexit 0\n',0o751),('prerm',b'#!/bin/sh\nexit 0\n',0o750)]
        if extra: controls.append(extra)
        self.original.write_bytes(tar_bytes([('debian-binary',b'2.0\n',0o644),('control.tar.gz',tar_bytes(controls),0o644),('data.tar.gz',tar_bytes([('usr/libexec/mihomo',b'original binary fixture',0o755)]),0o644)]))

    def run_helper(self, action):
        return subprocess.run(['sh',str(ROOT/'rollback-package.sh'),action,str(self.original),str(self.recovery),str(self.root)],env=self.env,text=True,capture_output=True)

    def test_unchanged_original_payload_scripts_and_metadata(self):
        raw=self.original.read_bytes()
        result=self.run_helper('--build');self.assertEqual(result.returncode,0,result.stderr)
        result=self.run_helper('--verify');self.assertEqual(result.returncode,0,result.stderr)
        old,new=map(lambda p:feed.archive_members(p.read_bytes()),(self.original,self.recovery))
        self.assertEqual(old['data.tar.gz'],new['data.tar.gz'])
        with tarfile.open(fileobj=io.BytesIO(old['control.tar.gz'])) as a, tarfile.open(fileobj=io.BytesIO(new['control.tar.gz'])) as b:
            for file in ('postinst','prerm'):
                x,y=a.getmember('./'+file),b.getmember('./'+file)
                self.assertEqual((x.mode,x.uid,x.gid,x.mtime),(y.mode,y.uid,y.gid,y.mtime))
                self.assertEqual(a.extractfile(x).read(),b.extractfile(y).read())
        controls=feed.archive_members(new['control.tar.gz'])
        self.assertIn(b'Version: 1.19.31-r1~hfgjrestore\n',controls['control'])
        self.assertIn(b'Description: original description\n continuation retained\n',controls['control'])
        self.assertEqual(self.original.read_bytes(),raw)
        self.assertNotEqual(self.run_helper('--build').returncode,0)  # never overwrite

    def test_tampered_payload_script_field_and_mode_rejected(self):
        self.assertEqual(self.run_helper('--build').returncode,0)
        baseline=self.recovery.read_bytes()
        for change in ('payload','script','field','mode'):
            with self.subTest(change=change):
                members=feed.archive_members(baseline)
                if change=='payload': members['data.tar.gz']=tar_bytes([('usr/libexec/mihomo',b'tampered',0o755)])
                else:
                    controls=feed.archive_members(members['control.tar.gz'])
                    if change=='script':controls['postinst']+=b'# tampered\n'
                    if change=='field':controls['control']=controls['control'].replace(b'Provides: mihomo',b'Provides: wrong')
                    members['control.tar.gz']=tar_bytes([(k,v,(0o700 if change=='mode' else 0o751) if k=='postinst' else 0o750 if k=='prerm' else 0o640) for k,v in controls.items()])
                self.recovery.write_bytes(tar_bytes([(k,v,0o644) for k,v in members.items()]))
                self.assertNotEqual(self.run_helper('--verify').returncode,0)

    def test_stat_failure_on_control_files_cannot_compare_two_empty_values(self):
        self.assertEqual(self.run_helper('--build').returncode,0)
        stat = shutil.which('gstat') or shutil.which('stat')
        wrapper = self.root/'tools/stat'
        wrapper.unlink(missing_ok=True)
        wrapper.write_text('#!/bin/sh\nfor path; do :; done\ncase "$path" in */rollback-package.sh) exec '+stat+' "$@" ;; *) exit 1 ;; esac\n')
        wrapper.chmod(0o755)
        self.assertNotEqual(self.run_helper('--verify').returncode,0)

    def test_unsupported_or_ambiguous_archive_rejected(self):
        for control in (self.control+b'Version: 1.19.32\n',self.control.replace(b'1.19.31-r1',b'1.19.31~hfgjrestore'),self.control.replace(b'Conflicts: mihomo-alpha',b'Conflicts: mihomo-alpha\n continuation'),self.control.replace(b'mihomo-meta',b'other-package',1)):
            with self.subTest(control=control):
                self.make_original(control)
                self.assertNotEqual(self.run_helper('--build').returncode,0)
                self.assertFalse(self.recovery.exists())
        self.make_original(extra=('../escape',b'unsupported',0o644))
        self.assertNotEqual(self.run_helper('--build').returncode,0)
        self.assertFalse((self.root/'escape').exists())
        self.original.write_bytes(b'!<arch>\nunsupported layout')
        self.assertNotEqual(self.run_helper('--build').returncode,0)
        self.assertFalse(self.recovery.exists())
