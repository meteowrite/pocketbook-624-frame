// SPDX-License-Identifier: GPL-2.0
/*
 * epdshow - drive the PocketBook 624 ED060SCE e-ink panel from userspace.
 *
 * The A13 TCON0 scans out a 258x620 XRGB8888 "step buffer" through the
 * mainline sun4i DRM driver (panel-dpi in the DT).  Every bit of a pixel
 * word is a pin (PDn = bit n), so each scanned buffer is one e-ink frame:
 * gate/source control signals plus 2-bit drive codes for 4 pixels per clock.
 * Signal layout and power sequence follow the PocketBook GPL disp_eink.c and
 * the stock epdc.ko pin map.
 *
 * Usage: epdshow [options] <image.pgm | white | black | bars | checker>
 *   -w FILE   waveform (.wbf), default /boot/default.wbf
 *   -m N      waveform mode (0 INIT, 1 DU, 2 GC16, 3 GL16, 4 A2), default 2
 *   -t T      panel temperature in C, default: AXP209 sensor, else 24
 *   -s FILE   previous-screen state file, default /tmp/epd.state
 *   -p        partial: leave pixels whose level does not change undriven
 *             (no flash outside the changed area; use with GL16 or DU)
 *   -n        dry run: scan out frames but keep the panel rails off
 *   -r C:N    raw drive test: every pixel gets code C (1 black, 2 white)
 *             for N frames, no waveform
 *   -i        print waveform/DRM info and exit
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <linux/gpio.h>

#define PANEL_W		800
#define PANEL_H		600

/* step buffer geometry (disp_eink_config.h) */
#define EINK_LSL	10
#define EINK_LBL	4
#define EINK_LDL	200
#define EINK_LEL	44
#define EINK_FSL	5
#define EINK_FBL	3
#define EINK_FDL	600
#define EINK_FEL	12
#define FRAME_W		(EINK_LSL + EINK_LBL + EINK_LDL + EINK_LEL)	/* 258 */
#define FRAME_H		(EINK_FSL + EINK_FBL + EINK_FDL + EINK_FEL)	/* 620 */

/* control signals: bit n drives PDn */
#define SPV		(1u << 23)
#define CKV		(1u << 22)
#define XSTL		(1u << 21)
#define XOE		(1u << 20)
#define MODE		(1u << 15)
#define XLE		(1u << 13)
#define DMASK		0xff030303u

/* --- panel rails ------------------------------------------------------ */

enum { G_VDD, G_RL, G_UD, G_VNEG, G_VPOS, G_VGG, G_VCOM, G_NUM };

static const struct {
	unsigned int line;	/* bank * 32 + pin */
	int active_low;
	const char *name;
} rails[G_NUM] = {
	[G_VDD]  = { 4 * 32 + 1,  1, "VDD (PE1)" },
	[G_RL]   = { 3 * 32 + 2,  0, "RL (PD2)" },
	[G_UD]   = { 3 * 32 + 14, 0, "UD (PD14)" },
	[G_VNEG] = { 4 * 32 + 3,  0, "VNEG (PE3)" },
	[G_VPOS] = { 1 * 32 + 10, 0, "VPOS (PB10)" },
	[G_VGG]  = { 3 * 32 + 18, 0, "VGG (PD18)" },
	[G_VCOM] = { 3 * 32 + 19, 0, "VCOM (PD19)" },
};

static int gpio_fd = -1;
static struct gpiohandle_data gpio_vals;
static int rails_on;

static void rail_set(int r, int on)
{
	gpio_vals.values[r] = on ^ rails[r].active_low;
	if (ioctl(gpio_fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &gpio_vals) < 0)
		perror("GPIOHANDLE_SET_LINE_VALUES");
}

static void msleep(int ms)
{
	struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };

	nanosleep(&ts, NULL);
}

static int rails_open(void)
{
	struct gpiohandle_request req;
	int chip, i;

	chip = open("/dev/gpiochip0", O_RDWR);
	if (chip < 0) {
		perror("/dev/gpiochip0");
		return -1;
	}
	memset(&req, 0, sizeof(req));
	for (i = 0; i < G_NUM; i++) {
		req.lineoffsets[i] = rails[i].line;
		req.default_values[i] = rails[i].active_low;	/* all off */
	}
	req.lines = G_NUM;
	req.flags = GPIOHANDLE_REQUEST_OUTPUT;
	strcpy(req.consumer_label, "epdshow");
	if (ioctl(chip, GPIO_GET_LINEHANDLE_IOCTL, &req) < 0) {
		perror("GPIO_GET_LINEHANDLE_IOCTL");
		close(chip);
		return -1;
	}
	close(chip);
	gpio_fd = req.fd;
	for (i = 0; i < G_NUM; i++)
		gpio_vals.values[i] = req.default_values[i];
	return 0;
}

/* LCD_open_flow() in lcd0_panel_cfg.c */
static void rails_power_on(void)
{
	rail_set(G_VDD, 1);
	rail_set(G_UD, 1);
	rail_set(G_RL, 1);
	msleep(1);
	rail_set(G_VNEG, 1);
	msleep(5);
	rail_set(G_VPOS, 1);
	rail_set(G_VGG, 1);
	msleep(4);
	rail_set(G_VCOM, 1);
	msleep(1);
	rails_on = 1;
}

/* LCD_close_flow() */
static void rails_power_off(void)
{
	if (gpio_fd < 0)
		return;
	rail_set(G_VCOM, 0);
	msleep(1);
	rail_set(G_VNEG, 0);
	rail_set(G_VPOS, 0);
	rail_set(G_UD, 0);
	rail_set(G_RL, 0);
	rail_set(G_VGG, 0);
	rail_set(G_VDD, 0);
	rails_on = 0;
}

static volatile sig_atomic_t stop;

static void on_signal(int sig)
{
	(void)sig;
	stop = 1;
}

/* --- waveform ---------------------------------------------------------- */

struct waveform {
	int modes, temps;
	uint8_t trange[64];
	uint8_t *data;
	size_t len;
};

static uint32_t u24(const uint8_t *d, size_t o)
{
	return d[o] | d[o + 1] << 8 | d[o + 2] << 16;
}

static int wbf_load(struct waveform *wf, const char *path)
{
	struct stat st;
	int fd;

	fd = open(path, O_RDONLY);
	if (fd < 0 || fstat(fd, &st) < 0) {
		perror(path);
		return -1;
	}
	wf->len = st.st_size;
	wf->data = malloc(wf->len);
	if (!wf->data || read(fd, wf->data, wf->len) != (ssize_t)wf->len) {
		fprintf(stderr, "%s: read failed\n", path);
		close(fd);
		return -1;
	}
	close(fd);
	if (wf->len < 0x40 || u24(wf->data, 4) != wf->len) {
		fprintf(stderr, "%s: not a .wbf file\n", path);
		return -1;
	}
	if (wf->data[0x24] != 0) {
		fprintf(stderr, "%s: only 4-bit (16 level) waveforms supported\n", path);
		return -1;
	}
	wf->modes = wf->data[0x25] + 1;
	wf->temps = wf->data[0x26] + 1;
	if (wf->temps + 1 > (int)sizeof(wf->trange))
		return -1;
	memcpy(wf->trange, wf->data + 0x30, wf->temps + 1);
	return 0;
}

static int wbf_temp_index(const struct waveform *wf, int temp)
{
	int i;

	for (i = 0; i < wf->temps; i++)
		if (temp < wf->trange[i + 1])
			return i;
	return wf->temps - 1;
}

/*
 * Decode one mode/temperature waveform into frames of 256 2-bit codes,
 * indexed [to * 16 + from].  Code 1 drives towards black, 2 towards white.
 * Returns the number of frames (frames must be freed by the caller).
 */
static int wbf_decode(const struct waveform *wf, int mode, int temp,
		      uint8_t **frames)
{
	const uint8_t *d = wf->data;
	size_t i, cap = 64;
	uint8_t *out;
	int n = 0, pos = 0, rle = 1;

	i = u24(d, u24(d, 0x20) + 4 * mode);
	i = u24(d, i + 4 * wbf_temp_index(wf, temp));
	out = calloc(cap, 256);
	while (i < wf->len && d[i] != 0xff) {
		uint8_t b = d[i];
		int count, k, j;

		if (b == 0xfc) {
			rle = !rle;
			i++;
			continue;
		}
		if (rle) {
			count = d[i + 1] + 1;
			i += 2;
		} else {
			count = 1;
			i++;
		}
		for (j = 0; j < count; j++) {
			for (k = 0; k < 4; k++)
				out[n * 256 + pos++] = (b >> (2 * k)) & 3;
			if (pos == 256) {
				pos = 0;
				if (++n == (int)cap) {
					cap *= 2;
					out = realloc(out, cap * 256);
					memset(out + n * 256, 0, (cap - n) * 256);
				}
			}
		}
	}
	*frames = out;
	return n;
}

/* --- step buffer -------------------------------------------------------- */

static void fill(uint32_t **p, int n, uint32_t v)
{
	while (n--)
		*(*p)++ = v | DMASK;
}

/* init_wav_buffer(): signal skeleton with all source data at 0 (no-op) */
static void frame_init(uint32_t *buf, int stride)
{
	const int v1 = (EINK_LSL + EINK_LBL + EINK_LDL + 1) & ~1;
	const int v2 = FRAME_W - v1;
	uint32_t *p;
	int line = 0, i;

	for (i = 0; i < EINK_FSL; i++, line++) {
		p = buf + line * stride;
		fill(&p, FRAME_W, SPV | XSTL);
	}
	p = buf + line++ * stride;
	fill(&p, v1 / 2, SPV | CKV | XSTL | MODE);
	fill(&p, v1 / 2, CKV | XSTL | MODE);
	fill(&p, v2, XSTL | MODE);
	p = buf + line++ * stride;
	fill(&p, v1 / 2, CKV | XSTL | MODE);
	fill(&p, v1 / 2, SPV | CKV | XSTL | MODE);
	fill(&p, v2, SPV | XSTL | MODE);
	p = buf + line++ * stride;
	fill(&p, v1, SPV | CKV | XSTL | MODE);
	fill(&p, v2, SPV | XSTL | MODE);
	for (i = 0; i < EINK_FDL; i++, line++) {
		p = buf + line * stride;
		fill(&p, EINK_LSL, SPV | CKV | XSTL | XLE | MODE);
		fill(&p, EINK_LBL - 1, SPV | CKV | XSTL | MODE);
		fill(&p, 1, SPV | CKV | XOE | XSTL | MODE);
		fill(&p, 4, SPV | CKV | XOE | MODE);
		fill(&p, EINK_LDL - 3, SPV | CKV | XOE | XSTL | MODE);
		fill(&p, EINK_LEL - 4, SPV | XOE | XSTL | MODE);
		fill(&p, 3, SPV | XSTL | MODE);
	}
	p = buf + line++ * stride;
	fill(&p, v1, SPV | CKV | XSTL | MODE);
	fill(&p, v2, SPV | XSTL | MODE);
	for (i = 0; i < EINK_FEL - 1; i++, line++) {
		p = buf + line * stride;
		fill(&p, FRAME_W, SPV | XSTL | MODE);
	}
}

/*
 * eink_gc16_update_waveform(): data lines for one frame.  idx[] holds
 * (to << 4 | from) per pixel; codes[] is the decoded frame.
 */
static void frame_compose(uint32_t *buf, int stride, const uint8_t *idx,
			  const uint8_t *codes)
{
	uint32_t t1[256], t2[256], t3[256], t4[256];
	int row, col, i;

	for (i = 0; i < 256; i++) {
		uint32_t c = codes[i];

		t1[i] = c << 11;				/* PD12,PD11 */
		t2[i] = (c & 2) << 9 | (c & 1) << 7;		/* PD10,PD7 */
		t3[i] = c << 5;					/* PD6,PD5 */
		t4[i] = c << 3;					/* PD4,PD3 */
	}
	for (row = 0; row < EINK_FDL; row++) {
		uint32_t *p = buf + (EINK_FSL + EINK_FBL + row) * stride +
			      EINK_LSL + EINK_LBL;
		const uint8_t *s = idx + row * PANEL_W;
		uint32_t xmask = SPV | CKV | XOE | MODE | DMASK;

		for (col = 0; col < EINK_LDL; col++, s += 4) {
			*p++ = xmask | t1[s[0]] | t2[s[1]] | t3[s[2]] | t4[s[3]];
			if (col == 4)
				xmask |= XSTL;
		}
	}
}

/* --- DRM ------------------------------------------------------------------ */

#define NBUF 3

struct drm {
	int fd;
	uint32_t crtc, conn;
	struct drm_mode_modeinfo mode;
	uint32_t fb[NBUF], handle[NBUF], pitch;
	uint32_t *map[NBUF];
	size_t size;
};

static int drm_open(struct drm *drm)
{
	struct drm_mode_card_res res = { 0 };
	struct drm_mode_get_connector conn = { 0 };
	struct drm_mode_get_encoder enc = { 0 };
	uint32_t conns[8], crtcs[8], encs[8], fbs[8];
	struct drm_mode_modeinfo modes[8];
	int i;

	drm->fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
	if (drm->fd < 0) {
		perror("/dev/dri/card0");
		return -1;
	}
	if (ioctl(drm->fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0)
		goto err;
	if (res.count_connectors < 1 || res.count_connectors > 8 ||
	    res.count_crtcs < 1 || res.count_crtcs > 8 ||
	    res.count_encoders > 8 || res.count_fbs > 8)
		goto err;
	res.connector_id_ptr = (uintptr_t)conns;
	res.crtc_id_ptr = (uintptr_t)crtcs;
	res.encoder_id_ptr = (uintptr_t)encs;
	res.fb_id_ptr = (uintptr_t)fbs;
	if (ioctl(drm->fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0)
		goto err;

	conn.connector_id = conns[0];
	if (ioctl(drm->fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) < 0)
		goto err;
	if (conn.count_modes < 1 || conn.count_modes > 8) {
		fprintf(stderr, "connector has %u modes\n", conn.count_modes);
		return -1;
	}
	conn.count_props = 0;
	conn.count_encoders = 0;
	conn.modes_ptr = (uintptr_t)modes;
	if (ioctl(drm->fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) < 0)
		goto err;
	drm->conn = conns[0];
	drm->mode = modes[0];
	drm->crtc = crtcs[0];
	if (conn.encoder_id) {
		enc.encoder_id = conn.encoder_id;
		if (ioctl(drm->fd, DRM_IOCTL_MODE_GETENCODER, &enc) == 0 && enc.crtc_id)
			drm->crtc = enc.crtc_id;
	}
	if (drm->mode.hdisplay != FRAME_W || drm->mode.vdisplay != FRAME_H) {
		fprintf(stderr, "unexpected mode %ux%u (want %ux%u) - wrong DTB?\n",
			drm->mode.hdisplay, drm->mode.vdisplay, FRAME_W, FRAME_H);
		return -1;
	}

	for (i = 0; i < NBUF; i++) {
		struct drm_mode_create_dumb cd = { 0 };
		struct drm_mode_fb_cmd fb = { 0 };
		struct drm_mode_map_dumb md = { 0 };

		cd.width = FRAME_W;
		cd.height = FRAME_H;
		cd.bpp = 32;
		if (ioctl(drm->fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd) < 0)
			goto err;
		drm->handle[i] = cd.handle;
		drm->pitch = cd.pitch;
		drm->size = cd.size;
		fb.width = FRAME_W;
		fb.height = FRAME_H;
		fb.pitch = cd.pitch;
		fb.bpp = 32;
		fb.depth = 24;
		fb.handle = cd.handle;
		if (ioctl(drm->fd, DRM_IOCTL_MODE_ADDFB, &fb) < 0)
			goto err;
		drm->fb[i] = fb.fb_id;
		md.handle = cd.handle;
		if (ioctl(drm->fd, DRM_IOCTL_MODE_MAP_DUMB, &md) < 0)
			goto err;
		drm->map[i] = mmap(NULL, cd.size, PROT_READ | PROT_WRITE,
				   MAP_SHARED, drm->fd, md.offset);
		if (drm->map[i] == MAP_FAILED)
			goto err;
	}
	return 0;
err:
	perror("drm");
	return -1;
}

static int drm_set(struct drm *drm, int buf)
{
	struct drm_mode_crtc c = { 0 };

	c.crtc_id = drm->crtc;
	if (buf >= 0) {
		c.fb_id = drm->fb[buf];
		c.set_connectors_ptr = (uintptr_t)&drm->conn;
		c.count_connectors = 1;
		c.mode = drm->mode;
		c.mode_valid = 1;
	}
	if (ioctl(drm->fd, DRM_IOCTL_MODE_SETCRTC, &c) < 0) {
		perror("DRM_IOCTL_MODE_SETCRTC");
		return -1;
	}
	return 0;
}

static int drm_flip(struct drm *drm, int buf)
{
	struct drm_mode_crtc_page_flip f = { 0 };

	f.crtc_id = drm->crtc;
	f.fb_id = drm->fb[buf];
	f.flags = DRM_MODE_PAGE_FLIP_EVENT;
	if (ioctl(drm->fd, DRM_IOCTL_MODE_PAGE_FLIP, &f) < 0) {
		perror("DRM_IOCTL_MODE_PAGE_FLIP");
		return -1;
	}
	return 0;
}

/* wait for the pending flip; returns the vblank sequence number */
static int drm_wait(struct drm *drm, unsigned int *seq)
{
	char ev[256];
	ssize_t n;

	for (;;) {
		n = read(drm->fd, ev, sizeof(ev));
		if (n < 0 && errno == EINTR)
			continue;
		if (n < (ssize_t)sizeof(struct drm_event_vblank))
			return -1;
		if (((struct drm_event *)ev)->type == DRM_EVENT_FLIP_COMPLETE) {
			*seq = ((struct drm_event_vblank *)ev)->sequence;
			return 0;
		}
	}
}

/* --- images ------------------------------------------------------------- */

static int load_image(const char *arg, uint8_t *img)
{
	int x, y, w, h, max;
	FILE *f;

	if (!strcmp(arg, "white") || !strcmp(arg, "black")) {
		memset(img, arg[0] == 'w' ? 0xff : 0x00, PANEL_W * PANEL_H);
		return 0;
	}
	if (!strcmp(arg, "bars")) {	/* 16 vertical gray bars */
		for (y = 0; y < PANEL_H; y++)
			for (x = 0; x < PANEL_W; x++)
				img[y * PANEL_W + x] = (x * 16 / PANEL_W) * 0x11;
		return 0;
	}
	if (!strcmp(arg, "checker")) {
		for (y = 0; y < PANEL_H; y++)
			for (x = 0; x < PANEL_W; x++)
				img[y * PANEL_W + x] = ((x / 50) ^ (y / 50)) & 1 ? 0xff : 0;
		return 0;
	}
	f = fopen(arg, "rb");
	if (!f) {
		perror(arg);
		return -1;
	}
	if (fscanf(f, "P5 %d %d %d", &w, &h, &max) != 3 || fgetc(f) == EOF ||
	    w != PANEL_W || h != PANEL_H || max != 255 ||
	    fread(img, 1, PANEL_W * PANEL_H, f) != PANEL_W * PANEL_H) {
		fprintf(stderr, "%s: need an 800x600 8-bit binary PGM (P5)\n", arg);
		fclose(f);
		return -1;
	}
	fclose(f);
	return 0;
}

/*
 * Panel temperature from the AXP209 internal sensor (IIO, millidegrees as
 * (raw + offset) * scale).  The PMIC sits next to the panel and idles near
 * ambient; there is no separate thermistor on this board.
 */
static int axp_temp(void)
{
	static const char *base = "/sys/bus/iio/devices/iio:device0/in_temp_";
	static const char *names[] = { "raw", "offset", "scale" };
	double v[3];
	char path[96];
	int i;

	for (i = 0; i < 3; i++) {
		FILE *f;

		snprintf(path, sizeof(path), "%s%s", base, names[i]);
		f = fopen(path, "r");
		if (!f)
			return 24;
		if (fscanf(f, "%lf", &v[i]) != 1) {
			fclose(f);
			return 24;
		}
		fclose(f);
	}
	return (int)((v[0] + v[1]) * v[2] / 1000.0 + 0.5);
}

static double now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

int main(int argc, char **argv)
{
	const char *wfpath = "/boot/default.wbf";
	const char *state = "/tmp/epd.state";
	int mode = 2, temp = -1000, dry = 0, info = 0, opt;
	int raw_code = -1, raw_frames = 0, partial = 0, changed = 0;
	const char *image;
	static uint8_t img[PANEL_W * PANEL_H], old[PANEL_W * PANEL_H];
	static uint8_t idx[PANEL_W * PANEL_H];
	struct waveform wf;
	struct drm drm;
	uint8_t *frames;
	unsigned int seq, last_seq = 0, missed = 0;
	int nframes, i, cur, ret = 1;
	double t0, tmax = 0;
	FILE *f;

	while ((opt = getopt(argc, argv, "w:m:t:s:pnir:")) != -1) {
		switch (opt) {
		case 'w': wfpath = optarg; break;
		case 'm': mode = atoi(optarg); break;
		case 't': temp = atoi(optarg); break;
		case 's': state = optarg; break;
		case 'p': partial = 1; break;
		case 'n': dry = 1; break;
		case 'i': info = 1; break;
		case 'r':
			if (sscanf(optarg, "%d:%d", &raw_code, &raw_frames) != 2 ||
			    raw_code < 0 || raw_code > 3 || raw_frames < 1) {
				fprintf(stderr, "-r needs CODE:FRAMES\n");
				return 1;
			}
			break;
		default:
			fprintf(stderr, "usage: %s [-w wbf] [-m mode] [-t temp] [-s state] [-p] [-n] [-i] "
				"<image.pgm|white|black|bars|checker>\n", argv[0]);
			return 1;
		}
	}
	if (temp == -1000)
		temp = axp_temp();
	if (wbf_load(&wf, wfpath) < 0)
		return 1;
	if (mode < 0 || mode >= wf.modes) {
		fprintf(stderr, "mode %d out of range (0..%d)\n", mode, wf.modes - 1);
		return 1;
	}
	if (raw_code >= 0) {
		nframes = raw_frames;
		frames = malloc(nframes * 256);
		memset(frames, raw_code, nframes * 256);
	} else {
		nframes = wbf_decode(&wf, mode, temp, &frames);
	}
	if (info) {
		int m;

		printf("waveform %s: %d modes, %d temperature ranges\n",
		       wfpath, wf.modes, wf.temps);
		for (m = 0; m < wf.modes; m++) {
			uint8_t *fr;

			printf("  mode %d: %d frames at %dC\n", m,
			       wbf_decode(&wf, m, temp, &fr), temp);
			free(fr);
		}
	}
	image = optind < argc ? argv[optind] : NULL;
	if (!image && raw_code >= 0)	/* resulting screen, for the state file */
		image = raw_code == 1 ? "black" : "white";
	if (!info && !image) {
		fprintf(stderr, "no image given\n");
		return 1;
	}
	if (!info && load_image(image, img) < 0)
		return 1;

	if (drm_open(&drm) < 0)
		return 1;
	if (info) {
		printf("drm: crtc %u connector %u mode %ux%u clock %u kHz "
		       "htotal %u vtotal %u (%.2f Hz) pitch %u\n",
		       drm.crtc, drm.conn, drm.mode.hdisplay, drm.mode.vdisplay,
		       drm.mode.clock, drm.mode.htotal, drm.mode.vtotal,
		       drm.mode.clock * 1000.0 / drm.mode.htotal / drm.mode.vtotal,
		       drm.pitch);
		return 0;
	}

	/* previous screen contents; a fresh panel is assumed white */
	memset(old, 0xff, sizeof(old));
	f = fopen(state, "rb");
	if (f) {
		if (fread(old, 1, sizeof(old), f) != sizeof(old))
			memset(old, 0xff, sizeof(old));
		fclose(f);
	}
	for (i = 0; i < PANEL_W * PANEL_H; i++) {
		idx[i] = (img[i] >> 4) << 4 | old[i] >> 4;
		changed += (img[i] >> 4) != (old[i] >> 4);
	}
	if (partial) {
		int k;

		if (!changed) {
			printf("partial: nothing changed\n");
			return 0;
		}
		for (i = 0; i < nframes; i++)		/* level k -> k: no drive */
			for (k = 0; k < 16; k++)
				frames[i * 256 + k * 17] = 0;
	}

	for (i = 0; i < NBUF; i++)
		frame_init(drm.map[i], drm.pitch / 4);

	/*
	 * One composed frame per vblank (11.8 ms at 85 Hz) leaves little slack:
	 * run real-time (schedutil then also goes to full clock) and keep our
	 * pages resident.  Failure is not fatal, frames just risk repeating.
	 */
	{
		struct sched_param sp = { .sched_priority = 50 };

		sched_setscheduler(0, SCHED_FIFO, &sp);
		mlockall(MCL_CURRENT | MCL_FUTURE);
	}

	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	if (!dry && rails_open() < 0)
		return 1;

	printf("mode %d, %dC: %d frames, %d pixels change%s%s\n", mode, temp,
	       nframes, changed, partial ? " (partial)" : "",
	       dry ? " (dry run, rails off)" : "");

	/* start scanning no-op frames, then bring the rails up */
	if (drm_set(&drm, 0) < 0)
		goto out;
	if (!dry)
		rails_power_on();

	cur = 1;
	for (i = 0; i <= nframes && !stop; i++) {
		t0 = now_ms();
		if (i < nframes)
			frame_compose(drm.map[cur], drm.pitch / 4, idx,
				      frames + i * 256);
		else
			frame_init(drm.map[cur], drm.pitch / 4);	/* idle */
		if (now_ms() - t0 > tmax)
			tmax = now_ms() - t0;
		if (drm_flip(&drm, cur) < 0 || drm_wait(&drm, &seq) < 0)
			goto out;
		if (last_seq && seq != last_seq + 1)
			missed += seq - last_seq - 1;
		last_seq = seq;
		cur = (cur + 1) % NBUF;
	}
	/* let the idle frame scan once more before cutting power */
	if (drm_flip(&drm, (cur + NBUF - 1) % NBUF) == 0)
		drm_wait(&drm, &seq);
	ret = stop ? 1 : 0;
out:
	rails_power_off();
	drm_set(&drm, -1);
	printf("done: compose max %.1f ms, %u vblanks repeated%s\n", tmax, missed,
	       stop ? " (interrupted)" : "");
	if (ret == 0 && !dry) {
		f = fopen(state, "wb");
		if (f) {
			fwrite(img, 1, sizeof(img), f);
			fclose(f);
		}
	}
	return ret;
}
