// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Thomas Doukinitsas

/* Blender Live Camera Output for Blackmagic Design DeckLink - native bridge,
 * C ABI (loaded from Python with ctypes).
 *
 * One handle = one DeckLink output running scheduled playback. The card's clock paces
 * the output: every completed frame is re-scheduled with the most recent picture pushed
 * by the caller, so the caller can push at any rate (or not at all) and the signal never
 * stalls. Two picture sources feed a handle:
 *
 *   bdo_push()    a BGRA/RGBA image of exactly the output size (Blender's offscreen path)
 *   bdo_mirror()  a native thread that captures a rectangle of a Blender window from the
 *                 screen (DXGI desktop duplication) and reframes it to the output
 */
#pragma once
#include <stdint.h>

#ifdef BDO_BUILD
#define BDO_API extern "C" __declspec(dllexport)
#else
#define BDO_API extern "C" __declspec(dllimport)
#endif

#define BDO_API_VERSION 1

/* bdo_push flags */
#define BDO_FLIP_Y 1   /* source rows are bottom-up (OpenGL readback) */
#define BDO_RGBA 2     /* source is RGBA, not BGRA */

typedef struct bdo_stats {
  int64_t pushed;          /* frames handed in by bdo_push / the mirror thread */
  int64_t scheduled;       /* frames scheduled on the card */
  int64_t completed;
  int64_t late;
  int64_t dropped;
  int64_t flushed;
  int64_t resyncs;         /* schedule fell behind the card and jumped ahead */
  int64_t buffered;        /* frames queued on the card at the last completion */
  int64_t width;
  int64_t height;
  int64_t time_scale;
  int64_t frame_duration;
  int64_t pixel_format;    /* FourCC actually used: 'BGRA' or '2vuy' */
  int64_t playing;
  int64_t mirror_frames;   /* new frames the mirror thread captured */
  int64_t mirror_us;       /* last mirror capture+scale time, microseconds */
} bdo_stats;

typedef struct bdo_mirror_config {
  int32_t enabled;
  /* Blender window: client size and position (Window.width/height/x/y) to find its HWND. */
  int32_t win_w, win_h, win_x, win_y;
  /* Source rectangle in window client pixels, origin top-left. */
  int32_t src_x, src_y, src_w, src_h;
  /* Where the source lands on the output, in output pixels, origin top-left. May
   * extend past the output (fill) or leave bars (fit). */
  int32_t dst_x, dst_y, dst_w, dst_h;
  int32_t max_fps;         /* 0 = output rate */
} bdo_mirror_config;

BDO_API int bdo_api_version(void);

/* JSON: {"available":bool,"api_version":"..","error":"..","devices":[{"name","model",
 * "playback":bool,"busy":int,"modes":[{"name","code","width","height","scale","duration",
 * "field","bgra","yuv"}]}]}. Returns the full length (call again with a bigger buffer
 * when it is >= cap). */
BDO_API int bdo_list_devices(char* buffer, int cap);

/* Opens the device by display name and starts output in the given mode (BMDDisplayMode
 * FourCC). Returns a handle or NULL with the reason in `error`. */
BDO_API void* bdo_open(const char* device_name, uint32_t mode, int preroll, char* error, int cap);

/* Copies one frame of exactly the output size into the next-to-air slot. Returns 0, or a
 * negative error (-1 bad handle, -2 size mismatch). Never blocks on the card. */
BDO_API int bdo_push(void* handle, const void* pixels, int width, int height, int row_bytes,
                     int flags);

BDO_API int bdo_mirror(void* handle, const bdo_mirror_config* config);
BDO_API int bdo_mirror_error(void* handle, char* buffer, int cap);

BDO_API int bdo_get_stats(void* handle, bdo_stats* out);
BDO_API void bdo_close(void* handle);
