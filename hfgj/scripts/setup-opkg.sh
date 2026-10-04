#!/usr/bin/env bash
# Compile the actual router opkg revision for isolated package-manager tests.
set -euo pipefail
hfgj_prefix="${RUNNER_TEMP:?RUNNER_TEMP must be disposable}/hfgj-opkg"
hfgj_opkg_revision=38eccbb1fd694d4798ac1baf88f9ba83d1eac616
hfgj_ubox_revision=e7608b69283d919d031d13cc8e21692503f5dbea
mkdir -p "$hfgj_prefix"
hfgj_source=$(mktemp -d "$hfgj_prefix/source.XXXXXX")
trap 'rm -rf "$hfgj_source"' EXIT
for hfgj_repo in opkg-lede libubox; do
    hfgj_checkout="$hfgj_source/$hfgj_repo"
    if [ "$hfgj_repo" = opkg-lede ]; then hfgj_revision=$hfgj_opkg_revision; else hfgj_revision=$hfgj_ubox_revision; fi
    git init -q "$hfgj_checkout"
    git -C "$hfgj_checkout" remote add origin "https://github.com/openwrt/$hfgj_repo.git"
    git -C "$hfgj_checkout" fetch --depth 1 origin "$hfgj_revision"
    git -C "$hfgj_checkout" checkout --detach FETCH_HEAD
    [ "$(git -C "$hfgj_checkout" rev-parse HEAD)" = "$hfgj_revision" ]
done
mkdir -p "$hfgj_source/prefix/include/libubox" "$hfgj_source/prefix/lib" "$hfgj_source/objects"
cp "$hfgj_source/libubox/"*.h "$hfgj_source/prefix/include/libubox/"
for hfgj_object in md5 blob utils avl avl-cmp; do
    # Do not add the libubox source root to -I: its assert.h shadows system assert.h.
    cc -D_GNU_SOURCE -c "$hfgj_source/libubox/$hfgj_object.c" -o "$hfgj_source/objects/$hfgj_object.o"
done
ar rcs "$hfgj_source/prefix/lib/libubox.a" "$hfgj_source/objects/"*.o
cmake -S "$hfgj_source/opkg-lede" -B "$hfgj_source/build" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DSTATIC_UBOX=ON -DBUILD_TESTS=OFF \
    -DENABLE_USIGN=ON -DVERSION=38eccbb1-isolated -DLOCK_FILE=/tmp/hfgj-opkg-test.lock \
    "-DCMAKE_C_FLAGS=-I$hfgj_source/prefix/include" "-DCMAKE_LIBRARY_PATH=$hfgj_source/prefix/lib"
cmake --build "$hfgj_source/build" --parallel 2
mkdir -p "$hfgj_prefix/bin"
install -m 0755 "$hfgj_source/build/src/opkg-cl" "$hfgj_prefix/bin/opkg-cl"
printf 'HFGJ_TEST_OPKG=%s/bin/opkg-cl\n' "$hfgj_prefix" >> "${GITHUB_ENV:?GITHUB_ENV is required}"
echo "Built official opkg $hfgj_opkg_revision for offline-root tests only"
