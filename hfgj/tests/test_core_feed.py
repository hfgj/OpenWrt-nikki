import copy
import gzip
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("core_feed", ROOT / "hfgj/scripts/core_feed.py")
feed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feed)
VERSION = "v1.19.31-hfgj.123456abcdef"
ARCH = "aarch64_generic"


def tar_bytes(files):
    value = io.BytesIO()
    with tarfile.open(fileobj=value, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
        for name, content, mode in files:
            entry = tarfile.TarInfo("./" + name)
            entry.size, entry.mode = len(content), mode
            archive.addfile(entry, io.BytesIO(content))
    return value.getvalue()


def fixture():
    binary = bytearray(64)
    binary[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", binary, 18, 183)
    binary += VERSION.encode()
    archive = gzip.compress(bytes(binary), mtime=0)
    name = f"mihomo-linux-arm64-{VERSION}.gz"
    release = {"tag_name": "HFGJ-Stable", "draft": False, "prerelease": False, "assets": [{"name": name,
               "browser_download_url": f"https://github.com/hfgj/mihomo/releases/download/HFGJ-Stable/{name}",
               "size": len(archive), "updated_at": "2026-10-04T00:00:00Z", "digest": "sha256:" + feed.digest(archive)}]}
    metadata = feed.resolve(release, VERSION, feed.digest(archive) + "  " + name + "\n")
    feed.check_archive(archive, metadata)
    return bytes(binary), archive, release, metadata


def ipk(path, metadata, binary, *, bridge=False, extra=None, wrong_arch=False):
    name = "mihomo-hfgj-rollback" if bridge else "mihomo-hfgj"
    control = f"Package: {name}\nVersion: {feed.full_version(metadata)}\nArchitecture: {'x86_64' if wrong_arch else ARCH}\nProvides: mihomo\n"
    if not bridge:
        control += "Conflicts: mihomo-meta, mihomo-alpha\nAlternatives: 300:/usr/bin/mihomo:/usr/libexec/mihomo\n"
    payload = [("usr/share/mihomo-hfgj-rollback/README", b"temporary bridge\n", 0o644)] if bridge else [
        ("usr/libexec/mihomo", binary, 0o755), ("usr/share/mihomo-hfgj/core.json", json.dumps(metadata).encode(), 0o644)]
    if extra:
        payload.append(extra)
    path.write_bytes(tar_bytes([("debian-binary", b"2.0\n", 0o644),
                               ("control.tar.gz", tar_bytes([("control", control.encode(), 0o644)]), 0o644),
                               ("data.tar.gz", tar_bytes(payload), 0o644)]))


class CoreFeedTests(unittest.TestCase):
    def test_stable_source_and_checksums(self):
        binary, archive, release, metadata = fixture()
        self.assertEqual(metadata["package_version"], "1.19.31+hfgj.20261004000000")
        self.assertEqual(feed.check_archive(archive, metadata), binary)
        with self.assertRaises(ValueError):
            feed.check_archive(archive + b"tamper", metadata)
        for version in ("v1.19.31", "alpha-1234567", "v1.19.31-hfgj.local.123456abcdef", "$(touch /tmp/bad)"):
            with self.assertRaises(ValueError):
                feed.resolve(release, version, "")

    def test_release_api_digest_and_origin(self):
        _, archive, release, _ = fixture()
        name = release["assets"][0]["name"]
        for change in ({"digest": "sha256:" + "0" * 64}, {"browser_download_url": "https://example.com/core.gz"}):
            wrong = copy.deepcopy(release)
            wrong["assets"][0].update(change)
            with self.assertRaises(ValueError):
                feed.resolve(wrong, VERSION, feed.digest(archive) + " " + name)

    def test_wrong_cpu_and_binary_version(self):
        binary, _, _, metadata = fixture()
        for modified in (binary.replace(b"ELF", b"XXX", 1), binary.replace(VERSION.encode(), b"official-core")):
            packed = gzip.compress(modified)
            wrong = dict(metadata, sha256=feed.digest(packed))
            with self.assertRaises(ValueError):
                feed.check_archive(packed, wrong)

    def test_targets_are_explicit_and_initially_opkg_only(self):
        for value in ({"include": []}, {"include": [{"arch": ARCH, "branch": "openwrt-25.12"}]},
                      {"include": [{"arch": "arm64", "branch": "openwrt-24.10"}]}):
            with self.assertRaises(ValueError):
                feed.validate_targets(value)
        value = {"include": [{"arch": ARCH, "branch": "openwrt-24.10"}]}
        self.assertEqual(feed.validate_targets(value), value)

    def test_payload_and_dependency_contract(self):
        binary, _, _, metadata = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "core.ipk"
            ipk(path, metadata, binary)
            feed.inspect_ipk(path, metadata, ARCH)
            for change in ({"wrong_arch": True}, {"extra": ("etc/nikki/run/config.yaml", b"private", 0o644)}):
                ipk(path, metadata, binary, **change)
                with self.assertRaises(ValueError):
                    feed.inspect_ipk(path, metadata, ARCH)
            ipk(path, metadata, binary, bridge=True)
            feed.inspect_bridge(path, metadata, ARCH)
            with self.assertRaises(ValueError):
                feed.inspect_bridge(path, dict(metadata, package_release=2), ARCH)
            recipe_root = Path(temporary) / 'recipes'
            feed.write_recipe(recipe_root, metadata)
            self.assertEqual((recipe_root/'mihomo-hfgj/core.mk').read_bytes(), (recipe_root/'mihomo-hfgj-rollback/core.mk').read_bytes())

    def test_package_versions_increase_for_core_and_recipe_updates(self):
        _, _, _, metadata = fixture()
        later = dict(metadata, package_release=2)
        self.assertEqual(feed.full_version(later), '1.19.31+hfgj.20261004000000-r2')
        self.assertGreater(feed.package_order(later), feed.package_order(metadata))
        newer = dict(metadata, package_version="1.19.32+hfgj.20261003000000")
        self.assertGreater(feed.package_order(newer), feed.package_order(later))

    def test_signed_assembly_and_tamper_rejection(self):
        usign = os.environ.get("HFGJ_TEST_USIGN") or shutil.which("usign")
        if not usign:
            self.skipTest("usign not installed; signature integration runs when available")
        binary, _, _, metadata = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private, public = root / "test.sec", root / "test.pub"
            subprocess.run([usign, "-G", "-s", str(private), "-p", str(public)], check=True, capture_output=True)
            stage = root / "input/openwrt-24.10" / ARCH / "hfgj"
            stage.mkdir(parents=True)
            core = stage / "mihomo-hfgj_test.ipk"
            bridge = stage / "mihomo-hfgj-rollback_test.ipk"
            ipk(core, metadata, binary)
            ipk(bridge, metadata, binary, bridge=True)
            text = "".join(f"Package: {name}\nVersion: {feed.full_version(metadata)}\nArchitecture: {ARCH}\nProvides: mihomo\nFilename: {path.name}\nSize: {path.stat().st_size}\nSHA256sum: {feed.digest(path.read_bytes())}\n\n" for name, path in (("mihomo-hfgj", core), ("mihomo-hfgj-rollback", bridge)))
            feed.inspect_index(text, [core, bridge], metadata, ARCH)
            for changed in (text.replace(ARCH, 'x86_64'), text.replace('Filename: '+core.name, 'Filename: ../wrong.ipk'), text+text):
                with self.assertRaises(ValueError):
                    feed.inspect_index(changed, [core, bridge], metadata, ARCH)
            (stage / "Packages").write_text(text)
            (stage / "Packages.gz").write_bytes(gzip.compress(text.encode()))
            (stage / "index.json").write_text(json.dumps({'version': 2, 'architecture': ARCH, 'packages': {name: feed.full_version(metadata) for name in ('mihomo-hfgj','mihomo-hfgj-rollback')}}))
            subprocess.run([usign, "-S", "-m", str(stage / "Packages"), "-s", str(private)], check=True)
            (root / "targets.json").write_text(json.dumps({"include": [{"branch": "openwrt-24.10", "arch": ARCH}]}))
            (root / "core.json").write_text(json.dumps(metadata))
            args = SimpleNamespace(root=ROOT, metadata=root / "core.json", targets=root / "targets.json", input=root / "input", output=root / "public", usign=usign, public_key=public)
            feed.assemble(args)
            self.assertTrue((root / "public/feed-state.json").is_file())
            self.assertFalse(any(path.suffix == ".sec" for path in (root / "public").rglob("*")))
            (stage / "Packages").write_text(text + "tampered\n")
            args.output = root / "bad-output"
            with self.assertRaises(subprocess.CalledProcessError):
                feed.assemble(args)
            self.assertFalse(args.output.exists())


if __name__ == "__main__":
    unittest.main()
