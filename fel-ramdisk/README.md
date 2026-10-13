# PocketBook 624 e-ink bring-up (mainline 6.1.9)

Working: bitmaps on the ED060SCE panel via the A13 TCON0 + sun4i DRM, waveform
from the stock `default.wbf`, composed and power-sequenced in userspace.

## Files
| File | Purpose |
|---|---|
| `sun5i-a13-pocketbook-624-epd.dts` | PB624 DT: TCON0 -> `panel-dpi` 258x620 @16 MHz, LCD pins, cpufreq disabled |
| `build-dtb.sh` | builds this DTB and the board DTB against the Buildroot kernel tree (`../output`) |
| `../br2-external/src/epdshow/epdshow.c` | WBF decoder + step-buffer composer + rail sequencing + DRM page flips |
| `make-initrd.sh`, `init.sh` | 3.6 MB test ramdisk (busybox, epdshow, waveform, images) -> `uRamdisk-epd` |
| `boot-epd.cmd/.scr`, `fel-epd.sh` | FEL boot of `../output/images/zImage` with the above |
| `../tools/wbf.py`, `bmp2pgm.py`, `mkimg.py` | waveform inspection, BMP -> PGM, text/heart test images |
| `../tools/power-cycle.py` | power button via FTDI DTR (NPN): 8 s off, 2 s on, retries until FEL attaches |
| `../tools/reboot-test.sh` | power-cycle -> FEL boot -> shell -> e-ink update, N times |
| `../tools/sercon.py`, `send.sh` | serial bridge (log + fifo) and command helper |

## Use
```
./build-dtb.sh; ./make-initrd.sh; ./fel-epd.sh        # host
epdshow -i                     # waveform + DRM info
epdshow -m 0 white             # INIT clear
epdshow -m 2 image.pgm         # GC16 (16 grey levels), 800x600 P5 PGM
epdshow -m 1 image.pgm         # DU (fast, black/white)
epdshow -r 1:40                # raw test: all pixels drive black for 40 frames
epdshow -n ...                 # dry run, rails stay off
```
Modes at 24 C: 0 INIT (109 frames), 1 DU (22), 2 GC16 (51), 3 GL16 (51), 4 A2 (10).
The last screen is kept in `/tmp/epd.state` (old pixel values for the waveform).

## Hardware (from stock epdc.ko + PocketBook GPL disp_eink)
- Signals (PDn = bit n of the XRGB8888 word): data PD3-7,PD10-12 (4 px x 2 bit/clk:
  p1->PD12,11  p2->PD10,7  p3->PD6,5  p4->PD4,3), XLE PD13, MODE PD15, XOE PD20,
  XSTL PD21, CKV PD22, SPV PD23, SDCLK PD24.
- Rails (GPIO, no PMIC chip): VDD PE1 (active low), VNEG PE3, VPOS PB10, VGG PD18,
  VCOM PD19; RL PD2 / UD PD14 = 1. On: VDD,UD,RL; 1ms; VNEG; 5ms; VPOS,VGG; 4ms; VCOM.
- Timing: 258x620 active, 271x695 total, 16 MHz, DCLK drive on falling edge -> 84.95 Hz
  (matches the waveform's 85 Hz).
- WBF frames are indexed [to][from]; code 1 = towards black, 2 = towards white.

## Known issues / next steps
- No AXP209 node: SPL reports "Failed to set core voltage", so cpufreq is disabled in
  the DT (it locked the CPU up). Describe the AXP209 properly to get full speed.
- U-Boot (A13-OLinuXino config) drives VGA-over-LCD on the e-ink pins at boot;
  disable `CONFIG_VIDEO_SUNXI`.
- Temperature is fixed (-t, default 24 C); the stock driver read a thermistor via ADC.
- VMware/Win11: after a FEL boot the BROM's USB connection used to linger, wedging
  VMware's USB stub (`VID_0E0F&PID_0001`, yellow mark) so the next FEL attach failed.
  Fixed by enabling `&otg_sram`, `&usb_otg` (peripheral) and `&usbphy` in the DT: musb
  drops the pull-up at boot. Workaround if it recurs: uninstall that stub in Device Manager.
  The .vmx has `usb.autoConnect` for 1f3a:efe8 and 0403:6001 plus a skip-reset quirk.
- Power button: DTR de-asserted (pin high) = pressed. After the FTDI re-enumerates, DTR
  idles high and holds the button until a tool opens the port (all tools assert DTR and
  clear HUPCL).
- ED060SCE white is naturally light grey (verified: raw 80-frame white = GC16 white).
