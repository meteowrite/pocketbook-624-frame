# Reference material (not used by the build)

## stock-firmware/ (not in the repository)
Keep your own dump of the original PocketBook 624 SD card's 32 MB boot
partition here (git-ignored; it is PocketBook / E Ink property). Useful for:
- `script.fex` (decompile `script.bin` with sunxi-tools `bin2fex`): the stock
  pin map (e-ink rails, touch, keys, Wi-Fi power, MMC)
- `default.wbf`: the panel waveform, which the build needs; copy it to
  `br2-external/board/pb624/local/default.wbf`
- `low_pwr.bmp`: stock low-battery picture (optional, see local/README)
- `linux/cramfs.img`: stock root filesystem (contains `epdc.ko`)

## stock-kernel/
Selected files from PocketBook's GPL release for this hardware,
https://github.com/pocketbook/Platform_A13 (commit 92e3ebdf, 2014-08-20):
e-ink driver (`disp_eink*`, signal timing and step-buffer layout used by
`epdshow`), panel power sequence (`lcd0_panel_cfg.c`), the touch driver
(`cnt_ts_pb.c`, msg21xx packet format), `einkwf.h`. GPL-2.0, as released.
`local-changes.diff` / `config-local-build`: the edits and config used to
build that tree locally.

## configs/
Kernel and Buildroot configs from earlier builds, kept for comparison.
