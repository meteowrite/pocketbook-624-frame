#!/bin/sh
# Soak test: request a screen every PERIOD s for HOURS, log each e-ink update;
# on a hang (no web answer for 3 min) log the serial tail and power-cycle.
#   usage: soak.sh <workdir> <host> [hours] [period]   (host: pb624.local
#   follows DHCP changes)
W=$1
HOST=$2
HOURS=${3:-3}
PERIOD=${4:-60}
T=$(cd "$(dirname "$0")" && pwd)
LOG=$W/soak.log
end=$(( $(date +%s) + HOURS * 3600 ))
n=0 ok=0 fail=0 hangs=0

last_update() {
	curl -s -m 5 "http://$HOST/api/status" |
		python3 -c 'import json,sys; print(json.load(sys.stdin)["last_update"])' 2>/dev/null
}

recover() {
	hangs=$((hangs + 1))
	echo "$(date +%T) HANG #$hangs, serial tail:" >> "$LOG"
	tr -d '\r' < "$W/con.log" | tail -15 | sed 's/^/    /' >> "$LOG"
	# alive on the console?  then it is the network, not a hang: record why
	echo "$(date +%T) serial probe:" >> "$LOG"
	timeout 20 "$T/send.sh" "$W" "echo PROBE; uptime; ip -4 -o addr show wlan0; tail -3 /proc/net/wireless; ps | grep -c '[m]ain.py'; tail -25 /data/log/messages" 8 \
		| sed 's/^/    /' >> "$LOG"
	for p in $(pgrep -f 'python3 -I ./se[r]con'); do kill "$p"; done
	(cd "$T" && ./power-cycle.py off && sleep 3 && timeout 5 ./power-cycle.py on) >/dev/null 2>&1
	(cd "$T" && setsid nohup python3 -I ./sercon.py "$W/con.log" "$W/con.fifo" >/dev/null 2>&1 &)
	for i in $(seq 60); do last_update >/dev/null && [ -n "$(last_update)" ] && break; sleep 5; done
	echo "$(date +%T) recovered: $(last_update)" >> "$LOG"
}

echo "$(date +%T) soak start: $HOURS h, every $PERIOD s, host $HOST" >> "$LOG"
while [ "$(date +%s)" -lt "$end" ]; do
	for a in weather graph photos info next; do
		[ "$(date +%s)" -lt "$end" ] || break
		n=$((n + 1))
		prev=$(last_update)
		if [ -z "$prev" ]; then
			down=0
			while [ -z "$(last_update)" ] && [ $down -lt 180 ]; do sleep 10; down=$((down + 10)); done
			[ -z "$(last_update)" ] && { recover; continue; }
			prev=$(last_update)
		fi
		t0=$(date +%s)
		curl -s -m 10 -o /dev/null -d "action=$a" "http://$HOST/action"
		cur=$prev
		while [ "$cur" = "$prev" ] && [ $(( $(date +%s) - t0 )) -lt 90 ]; do
			sleep 2; cur=$(last_update)
		done
		dt=$(( $(date +%s) - t0 ))
		if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
			ok=$((ok + 1)); echo "$(date +%T) $a ok ${dt}s" >> "$LOG"
		else
			fail=$((fail + 1)); echo "$(date +%T) $a FAIL ${dt}s (status '$cur')" >> "$LOG"
		fi
		sleep $(( PERIOD > dt ? PERIOD - dt : 1 ))
	done
done
echo "$(date +%T) soak done: $n requests, $ok ok, $fail failed, $hangs hangs" >> "$LOG"
