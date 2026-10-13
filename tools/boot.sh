#!/bin/sh
# Power-cycle into FEL, FEL-boot (../fel-ramdisk/fel-epd.sh, KERNEL/DTB env honoured),
# start the serial bridge and wait for the initramfs shell.
#   usage: boot.sh <workdir>
W=$1
T=$(cd "$(dirname "$0")" && pwd)
pkill -f "$T/sercon.py"
sleep 1
"$T/power-cycle.py" || { echo "power cycle failed"; exit 1; }
: > "$W/con.log"
setsid nohup python3 -I "$T/sercon.py" "$W/con.log" "$W/con.fifo" >/dev/null 2>&1 &
sleep 1
(cd "$T/../fel-ramdisk" && ./fel-epd.sh 2>&1 | tr '\r' '\n' | grep -E "ERROR|Starting U-Boot")
for i in $(seq 40); do
	sleep 1
	grep -q "PB624 e-ink test initramfs" "$W/con.log" && { echo "shell up after ~${i}s"; exit 0; }
done
echo "no shell after 40 s"; tail -5 "$W/con.log"; exit 1
