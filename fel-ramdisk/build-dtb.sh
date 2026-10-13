#!/bin/sh
# Build the PB624 DTBs against the Buildroot kernel tree:
#   sun5i-a13-pocketbook-624.dtb      full board (AXP209, RTC, Wi-Fi, keys, e-ink)
#   sun5i-a13-pocketbook-624-epd.dtb  minimal e-ink bring-up DT
set -e
P=$(cd "$(dirname "$0")/.." && pwd)
K=$P/output/build/linux-6.1.9
cd "$(dirname "$0")"
cp $P/br2-external/board/pb624/sun5i-a13-pocketbook-624.dts .   # the board DT
for d in sun5i-a13-pocketbook-624 sun5i-a13-pocketbook-624-epd; do
	cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
	    -I $K/arch/arm/boot/dts -I $K/include $d.dts > .tmp.dts
	dtc -I dts -O dtb -W no-unit_address_vs_reg -@ -o $d.dtb .tmp.dts
done
rm -f .tmp.dts sun5i-a13-pocketbook-624.dts
