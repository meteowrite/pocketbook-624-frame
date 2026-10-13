#!/bin/sh
# Build the PB624 image: Buildroot source in buildroot/, downloads in dl/,
# build output in output/ (all inside this project, git-ignored).
#   usage: build.sh [make targets...]
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
BR=${BR:-$HERE/buildroot}
OUT=${OUT:-$HERE/output}
export BR2_DL_DIR=${BR2_DL_DIR:-$HERE/dl}
if [ ! -f "$BR/Makefile" ]; then
	echo "buildroot/ is empty: run 'git submodule update --init'" >&2
	exit 1
fi
WBF=$HERE/br2-external/board/pb624/local/default.wbf
if [ ! -f "$WBF" ]; then
	echo "missing $WBF: copy the e-ink waveform from your device (see br2-external/board/pb624/local/README)" >&2
	exit 1
fi
if [ ! -f "$OUT/.config" ]; then
	make -C "$BR" O="$OUT" BR2_EXTERNAL="$HERE/br2-external" pb624_defconfig
fi
make -C "$OUT" "$@"
