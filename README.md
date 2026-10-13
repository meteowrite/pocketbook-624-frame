# PocketBook 624 photo frame / weather station

Buildroot external tree that turns a PocketBook 624 (pb624-D-ww, Allwinner
A13, ED060SCE 800x600 e-ink) into a Wi-Fi photo frame and weather display
that pulls its data from a Home Assistant server and shows up in HA as an
MQTT device.

## Hardware support (mainline 6.1.9 + 2 small patches)
| Part | Driver / status |
|---|---|
| e-ink ED060SCE | TCON0 + sun4i DRM (`panel-dpi`, 258x620 step buffer), `epdshow` in userspace (WBF waveforms INIT/DU/GC16/GL16/A2 = 159/22/54/54/10 frames at 85 Hz, partial `-p`, rails via GPIO) |
| AXP209 PMIC | regulators, cpufreq 432-1008 MHz, battery/AC/USB monitor, power key, poweroff |
| PCF8563 RTC | time keeping; alarm INT shares the NMI with the AXP209 -> wakes from suspend (cannot power on from off) |
| Suspend | s2idle; wake by RTC alarm, front keys, power key (patch: `IRQCHIP_SKIP_SET_WAKE`) |
| Wi-Fi RTL8188EUS | staging `r8188eu` (wext), AXP LDO3 powered, firmware `rtl8188eufw.bin` |
| Keys | LRADC (left 0.29 V, right 1.33 V), gpio-keys PG10 home, PG9 back, AXP PEK power |
| Temperature | AXP209 die (also used for the waveform), SoC sensor |
| LED | PE8 `pb624:green:status` (lit during screen updates) |
| microSD | mmc0 external (PE4 power, PG0 CD), mmc2 internal slot |
| Touch | MStar msg2133 on i2c2 @0x26 (stock `cnt_ts`), power PE5 (low = on), reset PB3, INT PG11; mainline `msg2638` driver + patch 0004 (msg21xx packets), axes fixed in the DT |

## Layout
```
build.sh                     build: Buildroot in buildroot/, downloads in dl/,
                             output in output/ (images in output/images)
scripts/update.sh            update a running frame over ssh (U-Boot + rootfs)
scripts/flash-sd.sh          write sdcard.img to a card in a USB reader
scripts/fel-boot.sh          dev boot over FEL (kernel/DTB from the build, rootfs on SD)
br2-external/                Buildroot external tree (BR2_EXTERNAL "PB624")
  configs/pb624_defconfig    Buildroot config (Bootlin glibc toolchain)
  board/pb624/               DTS, kernel config + patches, U-Boot fragment +
                             patch, genimage, post-build/post-image, rootfs overlay
  board/pb624/local/         YOUR files, git-ignored: secrets (Wi-Fi, frame.json,
                             ssh key) for the data partition, and the e-ink
                             waveform default.wbf from your device (local/README)
  package/, src/epdshow      e-ink driver tool (C)
  package/, src/pb624-frame  the application (Python)
fel-ramdisk/                 FEL-booted test/flashing ramdisk: e-ink bring-up DT,
                             make-initrd.sh, fel-epd.sh, flash-sd-onboard.sh
tools/                       bench tools: power button via FTDI DTR, serial
                             bridge, soak test, waveform/image helpers
upstream/                    kernel patches prepared for mainline (see its README)
reference/                   not used by the build: stock kernel e-ink/touch
                             sources (GPL), old configs; your own stock SD dump
                             (git-ignored)
buildroot/                   git submodule: Buildroot 2026.02-git (16a0560)
dl/, output/                 git-ignored: download cache, build output
```

## Boot slots (important)
The external microSD slot is powered through PE4 (active low), which is
high after reset, so the A13 boot ROM can **not** boot from it. The BROM
boots from the internal slot (mmc2, PC6-11), like the stock firmware did.
U-Boot (own DT + `preboot` "gpio clear PE4") powers the external slot and
scans the internal card first, then the external one. The kernel finds its
rootfs by `root=PARTUUID=50623624-01` and S00data mounts the ext4 labelled
`data`, so the same card works in either slot:
- one card in the internal slot (everything), or
- a card with just U-Boot in the internal slot + the system card outside.
Without a card in the internal slot the board falls back to FEL; `scripts/fel-boot.sh`
(or `sunxi-fel uboot u-boot-sunxi-with-spl.bin`) then boots the SD.

## Build and flash
Needs a Linux host with the usual Buildroot dependencies
(https://buildroot.org/downloads/manual/manual.html#requirement) and about
10 GB of disk. The e-ink waveform is not in this repository (it belongs to
E Ink / PocketBook): copy `default.wbf` from your PB624's original internal
microSD card (first partition), see `br2-external/board/pb624/local/README`.
```
git clone --recursive <this repo> pocketbook-624 && cd pocketbook-624
# (already cloned without --recursive: git submodule update --init)
L=br2-external/board/pb624/local
cp /path/to/stock-card/default.wbf $L/default.wbf    # required
cp $L/wpa_supplicant.conf.example $L/wpa_supplicant.conf
$EDITOR $L/wpa_supplicant.conf                       # SSID / password
./build.sh                                           # first build ~1 h
sudo scripts/flash-sd.sh -b old-rootfs.tar.gz /dev/sdX   # card in a USB reader
scripts/update.sh                                    # or: update a running frame
```
Or without a card reader, from the board (FEL flashing ramdisk, see
`fel-ramdisk/make-initrd.sh` FLASH_IMG= and `fel-ramdisk/flash-sd-onboard.sh`): an
existing PB624 card is updated in place (U-Boot + rootfs, `/data` kept,
rootfs verified by md5); any other card is fully rewritten, its old p1 tarred
into `/data/backup/old-rootfs.tar.gz`.
Rebuild after app changes: `./build.sh pb624-frame-rebuild all`.

SD card: U-Boot at 8 KiB, p1 ext4 rootfs (read-only, kernel and DTB in
`/boot`, extlinux), p2 ext4 `/data` (grown to the full card):
`/data/config/{frame.json,wpa_supplicant.conf}`, `/data/photos`,
`/data/dropbear` (host keys), `/data/ssh/authorized_keys`.

Login: serial console (ttyS0 115200) or ssh, root password `pb624` (set in
the defconfig; prefer `authorized_keys`).

## The application
Screens: weather dashboard (HA weather entity, daily forecast, extra
sensors), photos (Floyd-Steinberg dithered to 16 greys), info page (IP, Wi-Fi, battery, HA and
MQTT status), free-text messages, images pushed from HA.

Modes: `weather`, `photos`, `graph`, `mixed` (rotates weather, photos and,
if enabled, graph), refreshed every `interval_min`.

History: HA entity history (`history.entities`, default = the extra sensors;
`history.hours`, default 168) is drawn as a chart. `history.show`: `strip`
replaces the forecast on the weather screen, `screen` adds the full graph
screen (one panel per unit), `both`, or `off`.

Photos: `/data/photos` (web upload) plus `photo_sources`: HA `camera.*` /
`image.*` entities (fetched via `/api/camera_proxy` / `/api/image_proxy`) and
image URLs (HA URLs get the token), fetched each time they come up.
`photo_enhance: on` lifts the mid-tones (gamma 0.8, e-ink greys look darker
than on a monitor) and sharpens before dithering.

Live updates: while the frame stays awake (USB power with `sleep: auto`, or
`sleep: never`) the weather screen gets a clock and is redrawn every
`live_min` minutes with fresh HA states (history/calendar from the last full
refresh) as a fast partial update: only changed pixels are driven
(`epdshow -p`), with DU (0.26 s) when they all end black/white, else GL16; no
full-screen flash. The regular `interval_min` refresh is a normal GC16 that
clears the ghosting; after 60 partials in a row one is forced as well.

Calendar: `calendar.entities` (calendar.*) adds an "Upcoming" column with the
next `max_events` events within `days` to the weather screen.

Keys: left/right = previous/next photo, home = weather, back = info page,
power (short) = refresh.  Holding power 6 s switches the AXP off.
Touch: swipe left/right or tap the left/right third = next/previous photo,
tap the middle = cycle weather -> graph -> photo. Gestures also go to HA as button
events (swipe_left, swipe_right, tap_left, tap_center, tap_right).
Measured calibration (screen px -> raw 0..2047): (100,100)->(224,242),
(700,500)->(1757,1780).

Sleep: with `sleep: auto` the frame suspends between refreshes when it runs
on battery only (Wi-Fi and MQTT go down, RTC alarm wakes it; a key wakes it
early and keeps it awake for `awake_after_key_s`). On USB power it stays on.
Below `low_battery_pct` it shows the low-battery picture and powers off.

### Web UI  `http://<ip>/`
Preview of the panel, actions, all settings (HA URL/token, MQTT, mode,
rotation, language en/bg, ...), photo upload/delete. API:
```
curl http://pb624/api/status
curl --data-binary @pic.jpg http://pb624/api/show
curl -H 'Content-Type: application/json' -d '{"text":"Hello"}' http://pb624/api/show
curl -H 'Content-Type: application/json' -d '{"url":"http://.../x.png"}' http://pb624/api/show
```

### Home Assistant
1. Profile -> Security -> create a long-lived access token; enter HA URL,
   token and the weather entity (e.g. `weather.forecast_home`) in the web UI.
   Optional extra sensors (one entity id per line) appear under the current
   conditions.
2. Optional MQTT (Mosquitto add-on): enter broker/user/password. The frame
   appears as device "PocketBook 624" with: battery, battery voltage,
   temperature, Wi-Fi signal, IP, last update, showing, external power,
   charging, mode (select), refresh interval (number), message (text),
   refresh / next / previous / clean (buttons), button presses (event
   entity), and the current screen (image entity).
3. Push content from HA, e.g.
```yaml
action: mqtt.publish
data:
  topic: pb624/cmd/show_url
  payload: "http://homeassistant.local:8123/api/camera_proxy/camera.door"
```
   (URLs on your HA server get the token automatically.) Other commands:
   `pb624/cmd/{refresh,next,prev,clean,mode,interval,message,show_image}`.
   Setting the Message entity shows that text until it is cleared.

Host test without hardware: `python3 src/pb624-frame/main.py --sim /tmp/sim`
(web UI on :8080, screens in /tmp/sim/screen.png).

## Power supply (important)
Without the battery, a PC USB port cannot cover a full-speed CPU plus an
e-ink update: the board browns out into a silent hang (no reset, no
console). Measured USB input during INIT+GC16: 464 mA at 432 MHz, 591 mA at
624 MHz, hang at 1008 MHz. The app therefore caps cpufreq at
`cpu_max_khz_no_battery` (432 MHz) whenever no battery is detected. With the
battery fitted or a >= 1 A charger the cap is lifted.

## Updating a running frame
- Web UI "System update": upload `output/images/pb624-rootfs.ext2.gz` (or
  `curl -F rootfs=@pb624-rootfs.ext2.gz http://pb624.local/api/update`). The
  upload is streamed to `/data/update`, checked (gzip, exact partition size),
  the rootfs is rewritten and the watchdog reboots the frame (~5 min).
- `scripts/update.sh [host]` (default pb624.local) does the same over ssh (key
  `br2-external/board/pb624/local/pb624_dev_key`) and also updates U-Boot.

The frame answers mDNS: `http://pb624.local/` works whatever IP DHCP gives.

## Known limits
- No suspend-to-RAM on the A13 in mainline: s2idle only, so sleep current is
  higher than the stock firmware's standby. Measure with a USB meter.
- The RTC cannot power the board on from full off, so "sleep" never
  powers off (except at low battery).
- `r8188eu` is a staging, wext-only driver (`iw` does not work; use
  `iwconfig`/`wpa_cli`). Newer kernels replace it with `rtl8xxxu`.
- U-Boot SPL reports "Failed to set core voltage"; Linux sets it via cpufreq.

## License
- Everything written for this project (the frame application, scripts,
  tools, Buildroot external tree, docs): MIT, see `LICENSE`.
- `br2-external/src/epdshow` (follows PocketBook's GPL e-ink driver), the
  device tree, the kernel patches (`br2-external/board/pb624/patches/linux`,
  `upstream/`) and the U-Boot patch: GPL-2.0, like the code they modify or
  derive from.
- `reference/stock-kernel`: PocketBook's GPL-2.0 release, unchanged.
- Not included: the E Ink waveform and other stock firmware files; the
  Wi-Fi firmware comes from Buildroot's linux-firmware package.
