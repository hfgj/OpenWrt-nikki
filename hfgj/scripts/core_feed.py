#!/usr/bin/env python3
"""Resolve a published HFGJ core, lock its digest, inspect SDK packages and feeds.

Only the stable Linux ARM64/opkg target is supported initially. No secrets are
read or printed by this module. SDK signs the index; assemble verifies it again.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "hfgj/mihomo"
CHANNEL = "HFGJ-Stable"
ARCHITECTURES = {"aarch64_generic", "aarch64_cortex-a53", "aarch64_cortex-a72", "aarch64_cortex-a76"}
CORE_RE = re.compile(r"v(\d+\.\d+\.\d+)-hfgj\.([0-9a-f]{12})\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_BINARY = 128 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def get_bytes(url, limit=MAX_ARCHIVE):
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS release/feed URLs are accepted")
    request = urllib.request.Request(url, headers={"User-Agent": "hfgj-core-feed", "Accept": "application/vnd.github+json" if "api.github.com" in url else "*/*"})
    with urllib.request.urlopen(request, timeout=60) as response:
        value = response.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Download exceeds size limit")
    return value


def validate_targets(value):
    targets = value.get("include", [])
    if not targets:
        raise ValueError("hfgj/targets.json is empty: record the device DISTRIB_ARCH and DISTRIB_RELEASE first")
    seen = set()
    for target in targets:
        if set(target) != {"arch", "branch"}:
            raise ValueError("Each target must contain arch and branch only")
        if target["arch"] not in ARCHITECTURES or target["branch"] != "openwrt-24.10":
            raise ValueError("Initial support is Linux ARM64 / OpenWrt 24.10 / opkg only; APK needs separate validation")
        pair = (target["arch"], target["branch"])
        if pair in seen:
            raise ValueError("Duplicate target")
        seen.add(pair)
    return value


def resolve(release, version, checksums):
    match = CORE_RE.fullmatch(version)
    if not match or release.get("tag_name") != CHANNEL or release.get("draft") or release.get("prerelease"):
        raise ValueError("Expected a published HFGJ stable version; local/official/Alpha versions cannot enter this feed")
    name = f"mihomo-linux-arm64-{version}.gz"
    assets = {asset["name"]: asset for asset in release["assets"]}
    if name not in assets:
        raise ValueError("HFGJ release has no Linux ARM64 asset")
    asset = assets[name]
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{CHANNEL}/{name}"
    if asset["browser_download_url"] != expected_url or not 0 < asset["size"] <= MAX_ARCHIVE:
        raise ValueError("Unexpected release asset URL or size")
    sums = {}
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]):
            raise ValueError("Invalid SHA256SUMS line")
        filename = parts[1].lstrip("*")
        if filename in sums:
            raise ValueError("Duplicate SHA256SUMS entry")
        sums[filename] = parts[0]
    if name not in sums:
        raise ValueError("Missing core checksum")
    api_digest = asset.get("digest")
    if api_digest and api_digest != "sha256:" + sums[name]:
        raise ValueError("GitHub asset digest differs from SHA256SUMS")
    timestamp = dt.datetime.fromisoformat(asset["updated_at"].replace("Z", "+00:00"))
    package_version = f"{match[1]}+hfgj.{timestamp.strftime('%Y%m%d%H%M%S')}"
    return {"core_version": version, "package_version": package_version, "asset": name,
            "sha256": sums[name], "base_url": expected_url.rsplit("/", 1)[0],
            "repository": REPOSITORY, "channel": CHANNEL, "asset_updated_at": asset["updated_at"]}


def check_archive(data, metadata):
    if len(data) > MAX_ARCHIVE or digest(data) != metadata["sha256"]:
        raise ValueError("Core archive hash/size mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        binary = stream.read(MAX_BINARY + 1)
    if not 64 <= len(binary) <= MAX_BINARY:
        raise ValueError("Invalid binary size")
    if binary[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", binary, 18)[0] != 183:
        raise ValueError("Expected a little-endian ELF64 AArch64 core")
    if metadata["core_version"].encode() not in binary:
        raise ValueError("Core binary does not contain the locked HFGJ version")
    metadata["binary_sha256"] = digest(binary)
    return binary


def write_recipe(root, metadata):
    directory = root / "mihomo-hfgj"
    directory.mkdir(parents=True, exist_ok=True)
    fields = {"HFGJ_CORE_VERSION": metadata["core_version"], "HFGJ_PACKAGE_VERSION": metadata["package_version"],
              "HFGJ_PACKAGE_RELEASE": str(metadata.get("package_release", 1)),
              "HFGJ_CORE_ASSET": metadata["asset"], "HFGJ_CORE_SHA256": metadata["sha256"], "HFGJ_CORE_BASE_URL": metadata["base_url"]}
    recipe = "# Generated by core_feed.py; never edit by hand.\n" + "".join(f"{key}:={value}\n" for key, value in fields.items())
    (directory / "core.mk").write_text(recipe)
    bridge = root / "mihomo-hfgj-rollback"
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / "core.mk").write_text(recipe)
    (directory / "core.json").write_text(json.dumps(metadata, indent=2) + "\n")


def archive_members(data):
    # OpenWrt IPKs can be gzip/tar or Debian ar containers. Inspect both.
    if data.startswith(b"!<arch>\n"):
        members = {}
        offset = 8
        while offset < len(data):
            header = data[offset:offset + 60]
            if len(header) != 60 or header[-2:] != b"`\n":
                raise ValueError("Malformed IPK ar header")
            size = int(header[48:58])
            name = header[:16].decode().strip().rstrip("/")
            if name in members or offset + 60 + size > len(data):
                raise ValueError("Invalid/duplicate IPK ar member")
            members[name] = data[offset + 60:offset + 60 + size]
            offset += 60 + size + size % 2
        return members
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = {}
        for entry in archive:
            name = entry.name.removeprefix("./")
            if entry.isfile():
                if name in members:
                    raise ValueError("Duplicate IPK outer member")
                members[name] = archive.extractfile(entry).read()
        return members


def inspect_ipk(path, metadata, arch):
    members = archive_members(path.read_bytes())
    if members.get("debian-binary") != b"2.0\n":
        raise ValueError("Invalid IPK format")
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as archive:
        controls = {entry.name.removeprefix("./"): archive.extractfile(entry).read() for entry in archive if entry.isfile()}
    fields = {}
    for line in controls["control"].decode().splitlines():
        if line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    for key, expected in {"Package": "mihomo-hfgj", "Version": full_version(metadata), "Architecture": arch,
                          "Provides": "mihomo", "Alternatives": "300:/usr/bin/mihomo:/usr/libexec/mihomo"}.items():
        if fields.get(key) != expected:
            raise ValueError(f"Wrong IPK {key}")
    for key in ("Conflicts",):
        if set(re.split(r"[, ]+", fields.get(key, ""))) != {"mihomo-meta", "mihomo-alpha"}:
            raise ValueError(f"Wrong IPK {key}")
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as archive:
        entries = {entry.name.removeprefix("./"): entry for entry in archive if not entry.isdir()}
        if set(entries) != {"usr/libexec/mihomo", "usr/share/mihomo-hfgj/core.json"}:
            raise ValueError("Unexpected IPK payload: it must not contain Nikki/configuration files")
        entry = entries["usr/libexec/mihomo"]
        if not entry.isfile() or entry.mode & 0o777 != 0o755:
            raise ValueError("Invalid core executable")
        if digest(archive.extractfile(entry).read()) != metadata["binary_sha256"]:
            raise ValueError("SDK changed the verified core payload")
        if json.loads(archive.extractfile(entries["usr/share/mihomo-hfgj/core.json"]).read()) != metadata:
            raise ValueError("Wrong core identity metadata")
    return fields


def prepare(args):
    targets = validate_targets(json.loads(args.targets.read_text()))
    base = f"https://github.com/{REPOSITORY}/releases/download/{CHANNEL}"
    release = json.loads(get_bytes(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{CHANNEL}", 1024 * 1024))
    version = get_bytes(base + "/version.txt", 128).decode().strip()
    metadata = resolve(release, version, get_bytes(base + "/SHA256SUMS", 64 * 1024).decode())
    stamp = subprocess.check_output(["git", "log", "-1", "--format=%ct", "--", "mihomo-hfgj/Makefile", "mihomo-hfgj-rollback/Makefile", "hfgj/scripts/core_feed.py"], cwd=args.root, text=True).strip()
    metadata["package_release"] = int(stamp or "1")
    archive = get_bytes(metadata["base_url"] + "/" + metadata["asset"])
    check_archive(archive, metadata)
    write_recipe(args.root, metadata)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / metadata["asset"]).write_bytes(archive)
    (args.output / "core.json").write_text(json.dumps(metadata, indent=2) + "\n")
    changed = True
    if args.feed_url:
        try:
            previous = json.loads(get_bytes(args.feed_url.rstrip("/") + "/feed-state.json", 1024 * 1024))
            if package_order(previous["core"]) > package_order(metadata):
                raise ValueError("Refusing an automatic feed downgrade")
            fingerprint = packaging_fingerprint(args.root)
            changed = previous != {"core": metadata, "targets": targets, "packaging_sha256": fingerprint}
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    print(json.dumps({"core_version": version, "targets": targets, "changed": changed}))
    if args.github_output:
        with args.github_output.open("a") as stream:
            stream.write(f"changed={str(changed).lower()}\nmatrix={json.dumps(targets, separators=(',', ':'))}\n")


def packaging_fingerprint(root):
    result = hashlib.sha256()
    paths = [root / "mihomo-hfgj/Makefile", root / "mihomo-hfgj-rollback/Makefile", root / "hfgj/targets.json", root / ".github/workflows/hfgj-feed.yml"]
    paths += [root / name for name in ("feed.sh", "install.sh", "migrate.sh")]
    paths += sorted((root / "hfgj/scripts").glob("*"))
    for path in paths:
        if path.is_file():
            result.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return result.hexdigest()


def full_version(metadata):
    # OpenWrt 24.10 Package/Default uses PKG_VERSION-rPKG_RELEASE.
    return metadata["package_version"] + "-r" + str(metadata.get("package_release", 1))


def package_order(metadata):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)\+hfgj\.(\d{14})", metadata["package_version"])
    if not match:
        raise ValueError("Invalid HFGJ package version")
    return (*map(int, match.groups()), int(metadata.get("package_release", 1)))


def inspect_index(text, ipks, metadata, arch):
    packages = {}
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        fields = {}
        for line in paragraph.splitlines():
            if line.startswith((' ', '\t')):
                continue
            key, value = line.split(':', 1)
            if key in fields:
                raise ValueError('Duplicate index field')
            fields[key] = value.strip()
        name = fields.get('Package')
        if name in packages:
            raise ValueError('Duplicate index package')
        packages[name] = fields
    if set(packages) != {'mihomo-hfgj', 'mihomo-hfgj-rollback'}:
        raise ValueError('Unexpected signed index package set')
    for path in ipks:
        name = 'mihomo-hfgj-rollback' if path.name.startswith('mihomo-hfgj-rollback_') else 'mihomo-hfgj'
        expected = {'Version': full_version(metadata), 'Architecture': arch,
                    'Filename': path.name, 'SHA256sum': digest(path.read_bytes()),
                    'Size': str(path.stat().st_size), 'Provides': 'mihomo'}
        if any(packages[name].get(key) != value for key, value in expected.items()):
            raise ValueError('Signed index differs from verified package')
    return packages


def assemble(args):
    # Prepare a fresh tree; a failed target/signature never changes an existing feed.
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hfgj-feed-stage-", dir=output.parent) as temporary:
        args.output = Path(temporary) / "public"
        _assemble(args)
        if output.exists():
            raise ValueError("Output already exists; choose a fresh staging directory")
        args.output.rename(output)
    args.output = output


def _assemble(args):
    metadata = json.loads(args.metadata.read_text())
    targets = validate_targets(json.loads(args.targets.read_text()))
    args.output.mkdir(parents=True, exist_ok=True)
    for target in targets["include"]:
        arch, branch = target["arch"], target["branch"]
        source = args.input / branch / arch / "hfgj"
        destination = args.output / branch / arch / "hfgj"
        ipks = list(source.glob("*.ipk"))
        core_ipks = [path for path in ipks if path.name.startswith("mihomo-hfgj_")]
        bridge_ipks = [path for path in ipks if path.name.startswith("mihomo-hfgj-rollback_")]
        if len(ipks) != 2 or len(core_ipks) != 1 or len(bridge_ipks) != 1:
            raise ValueError("Feed must contain the core IPK and rollback bridge only")
        inspect_ipk(core_ipks[0], metadata, arch)
        inspect_bridge(bridge_ipks[0], metadata, arch)
        subprocess.run([args.usign, "-V", "-m", str(source / "Packages"), "-p", str(args.public_key), "-x", str(source / "Packages.sig")], check=True)
        index = (source / "Packages").read_text()
        packages = inspect_index(index, ipks, metadata, arch)
        json_index = json.loads((source / 'index.json').read_text())
        if json_index != {'version': 2, 'architecture': arch,
                          'packages': {name: fields['Version'] for name, fields in packages.items()}}:
            raise ValueError('JSON index differs from signed package index')
        destination.mkdir(parents=True, exist_ok=True)
        for name in (*[path.name for path in ipks], "Packages", "Packages.gz", "Packages.sig", "index.json"):
            shutil.copy2(source / name, destination / name)
        if gzip.decompress((destination / "Packages.gz").read_bytes()) != (destination / "Packages").read_bytes():
            raise ValueError("Compressed index differs")
    shutil.copy2(args.public_key, args.output / "key-build.pub")
    for name in ("feed.sh", "install.sh", "migrate.sh"):
        shutil.copy2(args.root / name, args.output / name)
    state = {"core": metadata, "targets": targets, "packaging_sha256": packaging_fingerprint(args.root)}
    (args.output / "feed-state.json").write_text(json.dumps(state, indent=2) + "\n")
    (args.output / ".nojekyll").touch()
    (args.output / "index.html").write_text("<!doctype html><meta charset=utf-8><title>HFGJ Mihomo feed</title><p>HFGJ Mihomo stable packages. Nikki and LuCI use the official feed.</p>\n")


def inspect_bridge(path, metadata, arch):
    members = archive_members(path.read_bytes())
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as archive:
        entry = next(entry for entry in archive if entry.name.removeprefix("./") == "control")
        control = archive.extractfile(entry).read().decode()
    for line in ("Package: mihomo-hfgj-rollback", "Provides: mihomo", "Architecture: " + arch,
                 "Version: " + full_version(metadata)):
        if line not in control.splitlines():
            raise ValueError("Invalid rollback bridge control")
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as archive:
        paths = {entry.name.removeprefix("./") for entry in archive if not entry.isdir()}
        if paths != {"usr/share/mihomo-hfgj-rollback/README"}:
            raise ValueError("Rollback bridge must not install executable/configuration files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--targets", type=Path, default=Path("hfgj/targets.json"))
    p.add_argument("--output", type=Path, default=Path("hfgj/build"))
    p.add_argument("--feed-url", default="")
    p.add_argument("--github-output", type=Path)
    p.set_defaults(function=prepare)
    p = sub.add_parser("assemble")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--targets", type=Path, default=Path("hfgj/targets.json"))
    p.add_argument("--metadata", type=Path, default=Path("hfgj/build/core.json"))
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("public"))
    p.add_argument("--public-key", type=Path, required=True)
    p.add_argument("--usign", default="usign")
    p.set_defaults(function=assemble)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
