#!/usr/bin/env bash
# Ubuntu runners do not ship an apt usign package. Build a pinned official copy.
set -euo pipefail
hfgj_prefix="${RUNNER_TEMP:?RUNNER_TEMP must point to a disposable build directory}/hfgj-usign"
hfgj_revision=c4c72b1b07945ee192361dc751291a7c98d6adcd
mkdir -p "$hfgj_prefix"
hfgj_source=$(mktemp -d "$hfgj_prefix/source.XXXXXX")
trap 'rm -rf "$hfgj_source"' EXIT
git -C "$hfgj_source" init -q
git -C "$hfgj_source" remote add origin https://github.com/openwrt/usign.git
git -C "$hfgj_source" fetch --depth 1 origin "$hfgj_revision"
git -C "$hfgj_source" checkout --detach FETCH_HEAD
[ "$(git -C "$hfgj_source" rev-parse HEAD)" = "$hfgj_revision" ]
cmake -S "$hfgj_source" -B "$hfgj_source/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$hfgj_source/build" --parallel 2
mkdir -p "$hfgj_prefix/bin"
install -m 0755 "$hfgj_source/build/usign" "$hfgj_prefix/bin/usign"
printf '%s\n' "$hfgj_prefix/bin" >> "${GITHUB_PATH:?GITHUB_PATH is required}"
echo "Built official usign at locked revision $hfgj_revision"
