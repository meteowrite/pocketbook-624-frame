#!/bin/sh
# Full reboot loop: power-cycle into FEL, FEL-boot the e-ink setup, verify
# the shell and DRM, run an e-ink update.   usage: reboot-test.sh <workdir> [runs]
W=$1
RUNS=${2:-2}
T=$(cd "$(dirname "$0")" && pwd)
for run in $(seq "$RUNS"); do
	echo "=== run $run"
	"$T/boot.sh" "$W" || exit 1
	ok=
	for i in $(seq 15); do      # sun4i-drm binds ~12 s after boot
		"$T/send.sh" "$W" "test -e /dev/dri/card0 && echo DRM_\"\"OK" 2 | grep -q "^DRM_OK" && ok=1 && break
	done
	[ -n "$ok" ] || { echo "no /dev/dri/card0"; tail -c 600 "$W/con.log" | tr -d '\r'; exit 1; }
	"$T/send.sh" "$W" "epdshow -m 1 checker" 4 | grep -E "frames|done|rror" ||
		{ echo "epdshow gave no output"; tail -c 600 "$W/con.log" | tr -d '\r'; exit 1; }
done
