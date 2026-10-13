# PB624 kernel patches for mainline

> **Decide before sending: the name on the Signed-off-by line.**
> All patches are signed off as `meteowrite <dtodorovtu@gmail.com>`, which is
> the git identity found on this machine. The kernel's Developer Certificate
> of Origin requires a known identity, which in practice means a real name.
> To change it, set `git config user.name "First Last"` in ~/projects/linux and
> regenerate the series (see below), or edit the `From:`/`Signed-off-by:`
> lines in `series/*/*.patch`.
>
> Nothing has been sent or pushed.

## Where things are

- Branch `pb624-upstream` in `~/projects/linux`. It has 7 commits on top of torvalds
  master `d5b6dc381db9` (v7.3-rc6+, fetched 2026-10-11).
  - The worktree checkout was removed because the disk ran out of
    space. To get a checkout again: `git -C ~/projects/linux worktree add <dir> pb624-upstream`.
    It needs about 1.8 GB.
- `series/` holds the patches generated with format-patch, one directory per maintainer tree.
- `out/` holds a mainline test build for booting on the hardware (see the end).
- `venv/` has dtschema, yamllint and GitPython, for `dtbs_check` and checkpatch.

## Status of the four bring-up patches (pb624-br/board/pb624/patches/linux)

| bring-up patch | mainline | action |
|---|---|---|
| 0001 rtc: pcf8563 trigger from firmware | **already upstream** (`irqflags = 0` when there is a fwnode) | dropped |
| 0002 irqchip/sun4i SKIP_SET_WAKE | missing | series 2 |
| 0003 pinctrl sun5i PE1 gpio_out | missing | series 1 (now PE0–PE2) |
| 0004 msg2638 msg2138 support | **support is upstream** (ae6b18e631d6, plus key events) | series 3: two fixes and a binding fix |

Differences found in mainline's msg2138 handler:

- **Second-finger delta.** Mainline adds the second-finger offset as an unsigned
  12-bit value. The vendor driver (Platform_A13 `cnt_ts_pb.c`) and our driver
  sign-extend it. Fixed by series 3, patch 1 (`Fixes:` tag).
- **Boot window.** The ~1.2 s firmware boot window logs I2C errors. Series 3,
  patch 2 keeps the time-based quiet window and drops a hard delay, because a
  hard delay would stall input open() and resume by 1.3 s. The commit message
  explains this.
  - Dmitry may prefer another approach. The patch is independent of patch 1, so it can be dropped on its own.
- **Binding.** It uses `additionalProperties: false`, so the
  `touchscreen-inverted-x` / `touchscreen-swapped-x-y` properties fail
  dtbs_check. Series 3, patch 3 switches it to `unevaluatedProperties`.
- **Packet magic.** Mainline does not check the 0x52 packet magic. That check is not needed, so it is left out.
- **Compatible.** A separate `mstar,msg2133` compatible was not added. The DT uses
  `mstar,msg2138`, because the protocol is identical.

## Validation

- `scripts/checkpatch.pl --strict`: 0 errors, 0 warnings, 0 checks on every patch.
  The only exception is the expected "does MAINTAINERS need updating?" warning on the new .dts.
- `make W=1`: irq-sun4i.o, pinctrl-sun5i.o and msg2638.o are warning-free
  (Bootlin armv7 gcc from build-pb624).
- `make dt_binding_check` (dtschema 2026.9): mstar,msg2638.yaml and arm/sunxi.yaml are clean.
- `make CHECK_DTBS=y allwinner/sun5i-a13-pocketbook-624.dtb` is clean.
  - Without series 3, patch 3 it reports `'touchscreen-inverted-x', 'touchscreen-swapped-x-y' do not match`.
- `sunxi_defconfig` (+ msg2638, pcf8563, gpio-keys, LRADC, AXP power) builds a zImage.
- **Boot-tested on mainline** (2026-10-12, 7.3.0-rc6 + all five series,
  out/zImage + upstream DT): see the results table at the end.

## Series and recipients

All commands are run from the directory `~/projects/pocketbook-624/upstream/series`. Add
`--dry-run` first. The tree maintainers are on To:, everyone else is on Cc:.

### 1. pinctrl: sunxi: sun5i: Allow PE0-PE2 to be used as GPIO outputs (1 patch)

PE1 was verified on the hardware (EPD VDD; the stock fex has `lcd_eink_main_pwr = port:PE01<1>`).
PE0 and PE2 were added because the sun4i-a10 table has gpio_out for them, but
they were not tested. If a reviewer objects, cut the patch down to PE1.

```
git send-email --to="Linus Walleij <linusw@kernel.org>" \
  --cc="Chen-Yu Tsai <wens@kernel.org>" --cc="Jernej Skrabec <jernej.skrabec@gmail.com>" \
  --cc="Samuel Holland <samuel@sholland.org>" --cc=linux-gpio@vger.kernel.org \
  --cc=linux-arm-kernel@lists.infradead.org --cc=linux-sunxi@lists.linux.dev \
  --cc=linux-kernel@vger.kernel.org 1-pinctrl/*.patch
```

### 2. irqchip/sun4i: Allow interrupts to be used as wakeup sources (1 patch)

Tested: s2idle wakeup via the PCF8563 alarm (NMI line, shared with the AXP209), gpio-keys and LRADC keys.

```
git send-email --to="Thomas Gleixner <tglx@kernel.org>" \
  --cc="Radu Rendec <radu@rendec.net>" --cc="Chen-Yu Tsai <wens@kernel.org>" \
  --cc="Jernej Skrabec <jernej.skrabec@gmail.com>" --cc="Samuel Holland <samuel@sholland.org>" \
  --cc=linux-arm-kernel@lists.infradead.org --cc=linux-sunxi@lists.linux.dev \
  --cc=linux-kernel@vger.kernel.org 2-irqchip/*.patch
```

### 3. Input: msg2638 - msg2138 fixes for the msg2133 (cover letter + 3 patches)

Tested: single touch and gestures with the calibrated orientation, and the quiet boot window.
- Two-finger touch with a negative delta was not tested specifically.
  The sign handling follows the vendor driver.

```
git send-email --to="Dmitry Torokhov <dmitry.torokhov@gmail.com>" \
  --cc="Vincent Knecht <vincent.knecht@mailoo.org>" --cc="Rob Herring <robh@kernel.org>" \
  --cc="Krzysztof Kozlowski <krzk+dt@kernel.org>" --cc="Conor Dooley <conor+dt@kernel.org>" \
  --cc=linux-input@vger.kernel.org --cc=devicetree@vger.kernel.org \
  --cc=linux-kernel@vger.kernel.org 3-input/*.patch
```

### 4. ARM: dts: sun5i: Add PocketBook 624 (cover letter + 2 patches)

- The DT leaves out the e-ink panel; it has no upstream driver.
- The cover letter names series 3 as a dependency, because without it dtbs_check fails on the touchscreen node.
- The pocketbook vendor prefix already exists.

```
git send-email --to="Chen-Yu Tsai <wens@kernel.org>" \
  --to="Jernej Skrabec <jernej.skrabec@gmail.com>" --to="Samuel Holland <samuel@sholland.org>" \
  --cc="Rob Herring <robh@kernel.org>" --cc="Krzysztof Kozlowski <krzk+dt@kernel.org>" \
  --cc="Conor Dooley <conor+dt@kernel.org>" --cc="Maxime Ripard <mripard@kernel.org>" \
  --cc=devicetree@vger.kernel.org --cc=linux-arm-kernel@lists.infradead.org \
  --cc=linux-sunxi@lists.linux.dev --cc=linux-kernel@vger.kernel.org 4-board/*.patch
```

### Regenerating the series

Do this after changing the name or rebasing:

```
cd ~/projects/linux        # or a worktree of pb624-upstream
B=$(git merge-base pb624-upstream FETCH_HEAD 2>/dev/null || echo d5b6dc381db9)
git format-patch -o 1-pinctrl  <pinctrl commit>^!
git format-patch -o 2-irqchip  <irqchip commit>^!
git format-patch --cover-letter -o 3-input  <fix>^..<binding>
git format-patch --cover-letter -o 4-board  <dt-bindings arm>^..pb624-upstream
```

The cover letter texts are in the current `series/*/0000-cover-letter.patch`.

## Boot test of mainline (out/)

What `out/` contains:
- `zImage`: mainline with sunxi_defconfig plus the patches, all built in and no modules. Kernel config: `out/config`.
- `sun5i-a13-pocketbook-624.dtb`: the upstream DT, with no e-ink panel.

How to boot it:

1. Put the board in FEL mode.
2. Run:

   ```
   ~/projects/pocketbook-624/upstream/fel-boot-mainline.sh  (zImage/DTB/scr in out/, regenerate with mkimage from fel-boot-mainline.cmd)
   ```

It uses our U-Boot from ../output/images. The rootfs is the internal card, mounted
read-only by PARTUUID. Expected results:

- **Works:** serial console and login, buttons (`evtest`), touch (`evtest`),
  the RTC, and the AXP209 power supplies.
- **Does not work:**
  - **E-ink:** there is no panel or TCON in the upstream DT, so the frame app's epdshow fails.
  - **Wi-Fi:** the rootfs only has 6.1.9 modules, and r8188eu is not in mainline.
    For a Wi-Fi test, enable `CONFIG_RTL8XXXU` (8188EU support is mainline).
- **Suspend test:** `echo +60 > /sys/class/rtc/rtc0/wakealarm; echo freeze > /sys/power/state`.

## Series 5: rtc (found by the mainline boot test, 2026-10-12)
`series/5-rtc/0001-rtc-pcf8563-fix-the-interrupt-handler-s-dev_id.patch`

Mainline (since v6.13, commit 00f1bb9b8486 "rtc: pcf8563: Switch to regmap")
requests the IRQ with the i2c_client as dev_id but the handler uses dev_id as
struct pcf8563. On the PB624 the RTC shares the NMI line with the AXP209, so
the first power-key press oopsed in pcf8563_irq -> regmap_read. One-line fix,
`Fixes:` + `Cc: stable`. Before sending, check the rtc tree / linux-next for
an existing fix.

To: Alexandre Belloni <alexandre.belloni@bootlin.com>
Cc: Nobuhiro Iwamatsu <iwamatsu@nigauri.org>, linux-rtc@vger.kernel.org,
    linux-kernel@vger.kernel.org

Boot test results (2026-10-12, 7.3.0-rc6-g8a21f1cdd2cb = all five series,
upstream DT, rootfs from the internal card, booted by hand from U-Boot with
the kernel and DTB in /data/mainline):

| test | result |
|---|---|
| boot, probe (AXP209, PEK, PCF8563, LRADC, gpio-keys, msg2138, MMC, USB) | ok, dmesg clean |
| RTC alarm wake from s2idle (`wakealarm` +20 s) | ok, woke on the alarm minute (PCF8563 alarm has minute resolution), IRQ handled, no oops |
| power key while awake (KEY_POWER press/release) | ok, no oops (oopsed before series 5) |
| power key wake from s2idle | ok |
| PE1 as GPIO output via /dev/gpiochip0 (series 1) | ok, CFG function 1 and data bit follow 1/0/1/0 |
| LRADC keys (KEY_LEFT, KEY_RIGHT), gpio-keys (KEY_HOME, KEY_BACK) | ok |
| msg2138 touch: tap, swipe, two fingers (series 3) | ok, coordinates follow the swipe, second finger reported in slot 1 |

Not covered: a second finger *up-left* of the first (negative delta, the
case series 3 patch 1 fixes); in the test the delta was positive.
