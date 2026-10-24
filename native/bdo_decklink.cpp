// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Thomas Doukinitsas

// Blender Live Camera Output for Blackmagic Design DeckLink - native bridge.
// See bdo_decklink.h for the contract.
//
// Threading: every DeckLink COM call except the completion callback's own scheduling runs
// on a short-lived MTA thread (InMta), because Blender's main thread is an STA and the
// DeckLink objects must live in the process MTA (kept alive with CoIncrementMTAUsage).
// The Python thread only ever copies pixels into the staging buffer.

#define BDO_BUILD
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objbase.h>
#include <d3d11.h>
#include <dxgi1_2.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "DeckLinkAPI_h.h"
#include "bdo_decklink.h"

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "user32.lib")

namespace {

// ------------------------------------------------------------------ helpers

std::string Utf8(BSTR value) {  // takes ownership of the BSTR
  if (value == nullptr) return {};
  std::string out;
  const int n = WideCharToMultiByte(CP_UTF8, 0, value, -1, nullptr, 0, nullptr, nullptr);
  if (n > 1) {
    out.resize(static_cast<size_t>(n) - 1);
    WideCharToMultiByte(CP_UTF8, 0, value, -1, out.data(), n, nullptr, nullptr);
  }
  SysFreeString(value);
  return out;
}

std::string Hresult(HRESULT hr) {
  char buffer[32];
  std::snprintf(buffer, sizeof(buffer), "0x%08lX", static_cast<unsigned long>(hr));
  return buffer;
}

std::string Json(const std::string& value) {
  std::string out = "\"";
  for (const unsigned char c : value) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buffer[8];
          std::snprintf(buffer, sizeof(buffer), "\\u%04x", c);
          out += buffer;
        } else {
          out += static_cast<char>(c);
        }
    }
  }
  return out + "\"";
}

int CopyOut(const std::string& text, char* buffer, int cap) {
  if (buffer != nullptr && cap > 0) {
    const size_t n = std::min(text.size(), static_cast<size_t>(cap) - 1);
    std::memcpy(buffer, text.data(), n);
    buffer[n] = '\0';
  }
  return static_cast<int>(text.size());
}

template <typename T>
void SafeRelease(T*& p) {
  if (p != nullptr) {
    p->Release();
    p = nullptr;
  }
}

void EnsureMta() {
  static std::once_flag once;
  std::call_once(once, [] {
    CO_MTA_USAGE_COOKIE cookie = nullptr;
    CoIncrementMTAUsage(&cookie);  // never released: the MTA lives as long as Blender
  });
}

template <typename F>
void InMta(F&& fn) {
  EnsureMta();
  std::thread worker([&fn] {
    const HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    fn();
    if (SUCCEEDED(hr)) CoUninitialize();
  });
  worker.join();
}

std::string FieldName(BMDFieldDominance field) {
  switch (field) {
    case bmdProgressiveFrame: return "p";
    case bmdProgressiveSegmentedFrame: return "psf";
    case bmdUpperFieldFirst: return "i_upper";
    case bmdLowerFieldFirst: return "i_lower";
    default: return "unknown";
  }
}

bool Supports(IDeckLinkOutput* output, BMDDisplayMode mode, BMDPixelFormat format) {
  BOOL supported = FALSE;
  BMDDisplayMode actual = mode;
  if (output->DoesSupportVideoMode(bmdVideoConnectionUnspecified, mode, format,
                                   bmdNoVideoOutputConversion, bmdSupportedVideoModeDefault,
                                   &actual, &supported) != S_OK) {
    return false;
  }
  return supported != FALSE;
}

IDeckLink* FindDevice(const std::string& name, std::string& error) {
  IDeckLinkIterator* iterator = nullptr;
  const HRESULT hr = CoCreateInstance(CLSID_CDeckLinkIterator, nullptr, CLSCTX_ALL,
                                      IID_IDeckLinkIterator, reinterpret_cast<void**>(&iterator));
  if (FAILED(hr) || iterator == nullptr) {
    error = "Blackmagic Desktop Video is not installed (DeckLink API " + Hresult(hr) + ")";
    return nullptr;
  }
  IDeckLink* found = nullptr;
  IDeckLink* device = nullptr;
  while (iterator->Next(&device) == S_OK) {
    BSTR display = nullptr;
    device->GetDisplayName(&display);
    if (found == nullptr && Utf8(display) == name) {
      found = device;
    } else {
      device->Release();
    }
    device = nullptr;
  }
  iterator->Release();
  if (found == nullptr) error = "no DeckLink device named '" + name + "'";
  return found;
}

int64_t BusyMask(IDeckLink* device) {
  int64_t busy = 0;
  IDeckLinkStatus* status = nullptr;
  if (SUCCEEDED(device->QueryInterface(IID_IDeckLinkStatus, reinterpret_cast<void**>(&status))) &&
      status != nullptr) {
    if (status->GetInt(bmdDeckLinkStatusBusy, &busy) != S_OK) busy = 0;
    status->Release();
  }
  return busy;
}

std::string ListDevicesJson() {
  std::string json;
  InMta([&json] {
    IDeckLinkIterator* iterator = nullptr;
    const HRESULT hr = CoCreateInstance(CLSID_CDeckLinkIterator, nullptr, CLSCTX_ALL,
                                        IID_IDeckLinkIterator,
                                        reinterpret_cast<void**>(&iterator));
    if (FAILED(hr) || iterator == nullptr) {
      json = "{\"available\":false,\"api_version\":\"\",\"error\":" +
             Json("Blackmagic Desktop Video is not installed (DeckLink API " + Hresult(hr) + ")") +
             ",\"devices\":[]}";
      return;
    }
    std::string version;
    IDeckLinkAPIInformation* info = nullptr;
    if (SUCCEEDED(CoCreateInstance(CLSID_CDeckLinkAPIInformation, nullptr, CLSCTX_ALL,
                                   IID_IDeckLinkAPIInformation,
                                   reinterpret_cast<void**>(&info))) &&
        info != nullptr) {
      BSTR text = nullptr;
      if (info->GetString(BMDDeckLinkAPIVersion, &text) == S_OK) version = Utf8(text);
      info->Release();
    }

    std::string devices;
    IDeckLink* device = nullptr;
    while (iterator->Next(&device) == S_OK) {
      BSTR display = nullptr;
      BSTR model = nullptr;
      device->GetDisplayName(&display);
      device->GetModelName(&model);
      const std::string name = Utf8(display);
      const std::string model_name = Utf8(model);

      bool playback = false;
      IDeckLinkProfileAttributes* attributes = nullptr;
      if (SUCCEEDED(device->QueryInterface(IID_IDeckLinkProfileAttributes,
                                           reinterpret_cast<void**>(&attributes))) &&
          attributes != nullptr) {
        int64_t io = 0;
        if (attributes->GetInt(BMDDeckLinkVideoIOSupport, &io) == S_OK) {
          playback = (io & bmdDeviceSupportsPlayback) != 0;
        }
        attributes->Release();
      }
      const int64_t busy = BusyMask(device);

      std::string modes;
      IDeckLinkOutput* output = nullptr;
      if (SUCCEEDED(device->QueryInterface(IID_IDeckLinkOutput,
                                           reinterpret_cast<void**>(&output))) &&
          output != nullptr) {
        IDeckLinkDisplayModeIterator* mode_iterator = nullptr;
        if (output->GetDisplayModeIterator(&mode_iterator) == S_OK) {
          IDeckLinkDisplayMode* mode = nullptr;
          while (mode_iterator->Next(&mode) == S_OK) {
            BSTR mode_name = nullptr;
            mode->GetName(&mode_name);
            BMDTimeValue duration = 0;
            BMDTimeScale scale = 0;
            mode->GetFrameRate(&duration, &scale);
            const BMDDisplayMode code = mode->GetDisplayMode();
            const bool bgra = Supports(output, code, bmdFormat8BitBGRA);
            const bool yuv = Supports(output, code, bmdFormat8BitYUV);
            const std::string label = Utf8(mode_name);
            if ((bgra || yuv) && duration > 0) {
              if (!modes.empty()) modes += ",";
              modes += "{\"name\":" + Json(label) + ",\"code\":" + std::to_string(code) +
                       ",\"width\":" + std::to_string(mode->GetWidth()) +
                       ",\"height\":" + std::to_string(mode->GetHeight()) +
                       ",\"scale\":" + std::to_string(scale) +
                       ",\"duration\":" + std::to_string(duration) +
                       ",\"field\":" + Json(FieldName(mode->GetFieldDominance())) +
                       ",\"bgra\":" + (bgra ? "true" : "false") +
                       ",\"yuv\":" + (yuv ? "true" : "false") + "}";
            }
            mode->Release();
          }
          mode_iterator->Release();
        }
        output->Release();
      }
      device->Release();
      device = nullptr;

      if (!devices.empty()) devices += ",";
      devices += "{\"name\":" + Json(name) + ",\"model\":" + Json(model_name) +
                 ",\"playback\":" + (playback ? "true" : "false") +
                 ",\"busy\":" + std::to_string(busy) + ",\"modes\":[" + modes + "]}";
    }
    iterator->Release();
    json = "{\"available\":true,\"api_version\":" + Json(version) +
           ",\"error\":\"\",\"devices\":[" + devices + "]}";
  });
  return json;
}

// ------------------------------------------------------------------ pixel packing

// Full-range BGRA (display encoded) -> limited-range 8-bit 4:2:2 Y'CbCr ('2vuy' = UYVY).
void PackUyvy(const uint8_t* bgra, uint8_t* out, int width, int height, int out_row_bytes,
              bool bt601) {
  const double kr = bt601 ? 0.299 : 0.2126;
  const double kb = bt601 ? 0.114 : 0.0722;
  const double kg = 1.0 - kr - kb;
  const int s = 1 << 16;
  const int yr = static_cast<int>(std::lround(kr * 219.0 / 255.0 * s));
  const int yg = static_cast<int>(std::lround(kg * 219.0 / 255.0 * s));
  const int yb = static_cast<int>(std::lround(kb * 219.0 / 255.0 * s));
  const double cb_scale = 224.0 / 255.0 / (2.0 * (1.0 - kb));
  const double cr_scale = 224.0 / 255.0 / (2.0 * (1.0 - kr));
  const int ur = static_cast<int>(std::lround(-kr * cb_scale * s));
  const int ug = static_cast<int>(std::lround(-kg * cb_scale * s));
  const int ub = static_cast<int>(std::lround((1.0 - kb) * cb_scale * s));
  const int vr = static_cast<int>(std::lround((1.0 - kr) * cr_scale * s));
  const int vg = static_cast<int>(std::lround(-kg * cr_scale * s));
  const int vb = static_cast<int>(std::lround(-kb * cr_scale * s));
  auto clamp8 = [](int v) { return static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v)); };
  for (int y = 0; y < height; ++y) {
    const uint8_t* src = bgra + static_cast<size_t>(y) * width * 4;
    uint8_t* dst = out + static_cast<size_t>(y) * out_row_bytes;
    for (int x = 0; x + 1 < width; x += 2) {
      const int b0 = src[0], g0 = src[1], r0 = src[2];
      const int b1 = src[4], g1 = src[5], r1 = src[6];
      const int r = (r0 + r1) >> 1, g = (g0 + g1) >> 1, b = (b0 + b1) >> 1;
      dst[0] = clamp8(128 + ((ur * r + ug * g + ub * b + (s >> 1)) >> 16));
      dst[1] = clamp8(16 + ((yr * r0 + yg * g0 + yb * b0 + (s >> 1)) >> 16));
      dst[2] = clamp8(128 + ((vr * r + vg * g + vb * b + (s >> 1)) >> 16));
      dst[3] = clamp8(16 + ((yr * r1 + yg * g1 + yb * b1 + (s >> 1)) >> 16));
      src += 8;
      dst += 4;
    }
  }
}

// Bilinear reframe of a top-down BGRA source onto a BGRA output (black outside `dst`).
void Reframe(const uint8_t* src, int sw, int sh, uint8_t* out, int ow, int oh, int dx, int dy,
             int dw, int dh) {
  for (size_t i = 0; i < static_cast<size_t>(ow) * oh; ++i) {
    reinterpret_cast<uint32_t*>(out)[i] = 0xFF000000u;
  }
  if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) return;
  const int x0 = std::max(dx, 0), x1 = std::min(dx + dw, ow);
  const int y0 = std::max(dy, 0), y1 = std::min(dy + dh, oh);
  if (x0 >= x1 || y0 >= y1) return;

  if (sw == dw && sh == dh) {  // 1:1, plain copy
    for (int y = y0; y < y1; ++y) {
      std::memcpy(out + (static_cast<size_t>(y) * ow + x0) * 4,
                  src + (static_cast<size_t>(y - dy) * sw + (x0 - dx)) * 4,
                  static_cast<size_t>(x1 - x0) * 4);
    }
    return;
  }

  struct Tap { int i0, i1, w1; };  // w1 in 1/256
  auto taps = [](int count, int origin, int dst_len, int src_len, int from, int to) {
    std::vector<Tap> result(static_cast<size_t>(to - from));
    const double scale = static_cast<double>(src_len) / dst_len;
    for (int o = from; o < to; ++o) {
      double f = (o - origin + 0.5) * scale - 0.5;
      f = std::clamp(f, 0.0, static_cast<double>(src_len - 1));
      const int i0 = static_cast<int>(f);
      const int i1 = std::min(i0 + 1, src_len - 1);
      result[static_cast<size_t>(o - from)] = {i0, i1, static_cast<int>((f - i0) * 256.0 + 0.5)};
    }
    (void)count;
    return result;
  };
  const std::vector<Tap> tx = taps(0, dx, dw, sw, x0, x1);
  const std::vector<Tap> ty = taps(0, dy, dh, sh, y0, y1);

  auto rows = [&](int from, int to) {
    for (int y = from; y < to; ++y) {
      const Tap& t = ty[static_cast<size_t>(y - y0)];
      const uint8_t* r0 = src + static_cast<size_t>(t.i0) * sw * 4;
      const uint8_t* r1 = src + static_cast<size_t>(t.i1) * sw * 4;
      uint8_t* dst = out + (static_cast<size_t>(y) * ow + x0) * 4;
      const int wy1 = t.w1, wy0 = 256 - t.w1;
      for (int x = x0; x < x1; ++x) {
        const Tap& u = tx[static_cast<size_t>(x - x0)];
        const int wx1 = u.w1, wx0 = 256 - u.w1;
        const uint8_t* a = r0 + u.i0 * 4;
        const uint8_t* b = r0 + u.i1 * 4;
        const uint8_t* c = r1 + u.i0 * 4;
        const uint8_t* d = r1 + u.i1 * 4;
        for (int ch = 0; ch < 3; ++ch) {
          const int top = a[ch] * wx0 + b[ch] * wx1;
          const int bottom = c[ch] * wx0 + d[ch] * wx1;
          dst[ch] = static_cast<uint8_t>((top * wy0 + bottom * wy1 + 32768) >> 16);
        }
        dst[3] = 255;
        dst += 4;
      }
    }
  };
  const int threads = std::clamp(static_cast<int>(std::thread::hardware_concurrency()) / 2, 1, 8);
  const int span = y1 - y0;
  if (threads == 1 || span < 64) {
    rows(y0, y1);
    return;
  }
  std::vector<std::thread> pool;
  for (int t = 0; t < threads; ++t) {
    const int from = y0 + span * t / threads, to = y0 + span * (t + 1) / threads;
    pool.emplace_back(rows, from, to);
  }
  for (std::thread& worker : pool) worker.join();
}

// ------------------------------------------------------------------ output

class Output;

// Captures a rectangle of a Blender window from the screen with DXGI desktop duplication
// and pushes it, reframed, into its Output.
class Mirror {
 public:
  explicit Mirror(Output* output) : output_(output) {}
  ~Mirror() { Stop(); }

  void Configure(const bdo_mirror_config& config);
  void Stop();
  std::string Error() {
    std::lock_guard<std::mutex> lock(mutex_);
    return error_;
  }
  std::atomic<int64_t> frames{0};
  std::atomic<int64_t> last_us{0};

 private:
  void Run();
  void SetError(const std::string& text) {
    std::lock_guard<std::mutex> lock(mutex_);
    error_ = text;
  }
  bool OpenDuplication(HMONITOR monitor);
  void CloseDuplication();

  Output* output_;
  std::mutex mutex_;
  bdo_mirror_config config_{};
  uint64_t config_version_ = 0;
  std::string error_;
  std::thread thread_;
  std::atomic<bool> stop_{false};
  bool running_ = false;

  // capture thread state
  HMONITOR monitor_ = nullptr;
  RECT monitor_rect_{};
  ID3D11Device* device_ = nullptr;
  ID3D11DeviceContext* context_ = nullptr;
  IDXGIOutputDuplication* duplication_ = nullptr;
  ID3D11Texture2D* staging_ = nullptr;
  int staging_w_ = 0, staging_h_ = 0;
};

class Output final : public IDeckLinkVideoOutputCallback {
 public:
  // IUnknown
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
    if (object == nullptr) return E_POINTER;
    if (iid == IID_IUnknown || iid == IID_IDeckLinkVideoOutputCallback) {
      *object = static_cast<IDeckLinkVideoOutputCallback*>(this);
      AddRef();
      return S_OK;
    }
    *object = nullptr;
    return E_NOINTERFACE;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
  ULONG STDMETHODCALLTYPE Release() override {
    const ULONG count = --refs_;
    if (count == 0) delete this;
    return count;
  }

  HRESULT STDMETHODCALLTYPE ScheduledFrameCompleted(IDeckLinkVideoFrame* frame,
                                                    BMDOutputFrameCompletionResult result) override {
    ++completed_;
    switch (result) {
      case bmdOutputFrameDisplayedLate: ++late_; break;
      case bmdOutputFrameDropped: ++dropped_; break;
      case bmdOutputFrameFlushed: ++flushed_; break;
      default: break;
    }
    if (stopping_) return S_OK;
    std::lock_guard<std::mutex> lock(schedule_mutex_);
    if (stopping_) return S_OK;
    for (size_t slot = 0; slot < frames_.size(); ++slot) {
      if (static_cast<IDeckLinkVideoFrame*>(frames_[slot]) == frame) {
        ScheduleSlot(slot);
        break;
      }
    }
    unsigned int buffered = 0;
    if (output_->GetBufferedVideoFrameCount(&buffered) == S_OK) buffered_ = buffered;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE ScheduledPlaybackHasStopped() override { return S_OK; }

  std::string Open(const std::string& name, BMDDisplayMode mode, int preroll) {
    std::string error;
    device_ = FindDevice(name, error);
    if (device_ == nullptr) return error;
    if (FAILED(device_->QueryInterface(IID_IDeckLinkOutput, reinterpret_cast<void**>(&output_))) ||
        output_ == nullptr) {
      return "'" + name + "' has no video output";
    }
    if ((BusyMask(device_) & bmdDevicePlaybackBusy) != 0) {
      return "'" + name + "' playback is in use by another application";
    }
    IDeckLinkDisplayMode* display = nullptr;
    if (output_->GetDisplayMode(mode, &display) != S_OK || display == nullptr) {
      return "'" + name + "' has no output mode " + std::to_string(mode);
    }
    width_ = static_cast<int>(display->GetWidth());
    height_ = static_cast<int>(display->GetHeight());
    display->GetFrameRate(&duration_, &scale_);
    BSTR mode_name = nullptr;
    display->GetName(&mode_name);
    const std::string label = Utf8(mode_name);
    display->Release();

    if (Supports(output_, mode, bmdFormat8BitBGRA)) {
      format_ = bmdFormat8BitBGRA;
    } else if (Supports(output_, mode, bmdFormat8BitYUV)) {
      format_ = bmdFormat8BitYUV;
    } else {
      return "'" + name + "' cannot output " + label + " as 8-bit BGRA or YUV";
    }
    int row_bytes = 0;
    if (output_->RowBytesForPixelFormat(format_, width_, &row_bytes) != S_OK || row_bytes <= 0) {
      row_bytes = format_ == bmdFormat8BitBGRA ? width_ * 4 : width_ * 2;
    }
    row_bytes_ = row_bytes;

    staging_.assign(static_cast<size_t>(width_) * height_ * 4, 0);
    for (size_t i = 3; i < staging_.size(); i += 4) staging_[i] = 255;

    output_->SetScheduledFrameCompletionCallback(this);
    HRESULT hr = output_->EnableVideoOutput(mode, bmdVideoOutputFlagDefault);
    if (FAILED(hr)) {
      output_->SetScheduledFrameCompletionCallback(nullptr);
      return "'" + name + "' refused to enable " + label + " output (" + Hresult(hr) +
             "); is another application using it?";
    }
    enabled_ = true;

    const int ring = std::clamp(preroll, 2, 8);
    for (int i = 0; i < ring; ++i) {
      IDeckLinkMutableVideoFrame* frame = nullptr;
      if (FAILED(output_->CreateVideoFrame(width_, height_, row_bytes_, format_,
                                           bmdFrameFlagDefault, &frame)) ||
          frame == nullptr) {
        return "the card would not allocate " + label + " frames";
      }
      frames_.push_back(frame);
      frame_version_.push_back(UINT64_MAX);
    }
    {
      std::lock_guard<std::mutex> lock(schedule_mutex_);
      for (size_t slot = 0; slot < frames_.size(); ++slot) ScheduleSlot(slot);
    }
    hr = output_->StartScheduledPlayback(0, scale_, 1.0);
    if (FAILED(hr)) return "could not start scheduled playback (" + Hresult(hr) + ")";
    playing_ = true;
    return {};
  }

  // Must run on an MTA thread.
  void Shutdown() {
    stopping_ = true;
    { std::lock_guard<std::mutex> lock(schedule_mutex_); }  // wait out a running callback
    if (output_ != nullptr) {
      if (playing_) output_->StopScheduledPlayback(0, nullptr, 0);
      if (enabled_) output_->DisableVideoOutput();
      output_->SetScheduledFrameCompletionCallback(nullptr);
    }
    playing_ = false;
    enabled_ = false;
    for (IDeckLinkMutableVideoFrame*& frame : frames_) SafeRelease(frame);
    frames_.clear();
    SafeRelease(output_);
    SafeRelease(device_);
  }

  int Push(const void* pixels, int width, int height, int row_bytes, int flags) {
    if (width != width_ || height != height_) return -2;
    if (row_bytes <= 0) row_bytes = width * 4;
    const auto* src = static_cast<const uint8_t*>(pixels);
    {
      std::lock_guard<std::mutex> lock(staging_mutex_);
      for (int y = 0; y < height; ++y) {
        const int sy = (flags & BDO_FLIP_Y) ? height - 1 - y : y;
        const uint8_t* in = src + static_cast<size_t>(sy) * row_bytes;
        uint8_t* out = staging_.data() + static_cast<size_t>(y) * width * 4;
        if (flags & BDO_RGBA) {
          for (int x = 0; x < width; ++x, in += 4, out += 4) {
            out[0] = in[2];
            out[1] = in[1];
            out[2] = in[0];
            out[3] = 255;
          }
        } else {
          std::memcpy(out, in, static_cast<size_t>(width) * 4);
        }
      }
      ++version_;
    }
    ++pushed_;
    return 0;
  }

  void Stats(bdo_stats* out) {
    out->pushed = pushed_;
    out->scheduled = scheduled_;
    out->completed = completed_;
    out->late = late_;
    out->dropped = dropped_;
    out->flushed = flushed_;
    out->resyncs = resyncs_;
    out->buffered = buffered_;
    out->width = width_;
    out->height = height_;
    out->time_scale = scale_;
    out->frame_duration = duration_;
    out->pixel_format = static_cast<int64_t>(format_);
    out->playing = playing_ ? 1 : 0;
    out->mirror_frames = mirror_ ? mirror_->frames.load() : 0;
    out->mirror_us = mirror_ ? mirror_->last_us.load() : 0;
  }

  int width() const { return width_; }
  int height() const { return height_; }
  double fps() const { return duration_ > 0 ? static_cast<double>(scale_) / duration_ : 25.0; }
  std::unique_ptr<Mirror> mirror_;

 private:
  ~Output() = default;

  void ScheduleSlot(size_t slot) {  // schedule_mutex_ held
    if (playing_) {
      BMDTimeValue stream_time = 0;
      double speed = 0.0;
      if (SUCCEEDED(output_->GetScheduledStreamTime(scale_, &stream_time, &speed)) &&
          next_index_ * duration_ <= stream_time) {
        next_index_ = stream_time / duration_ + 2;
        ++resyncs_;
      }
    }
    Fill(slot);
    if (SUCCEEDED(output_->ScheduleVideoFrame(frames_[slot], next_index_ * duration_, duration_,
                                              scale_))) {
      ++scheduled_;
    }
    ++next_index_;
  }

  void Fill(size_t slot) {
    std::lock_guard<std::mutex> lock(staging_mutex_);
    if (frame_version_[slot] == version_) return;
    IDeckLinkVideoBuffer* buffer = nullptr;
    if (FAILED(frames_[slot]->QueryInterface(IID_IDeckLinkVideoBuffer,
                                             reinterpret_cast<void**>(&buffer))) ||
        buffer == nullptr) {
      return;
    }
    void* bytes = nullptr;
    if (SUCCEEDED(buffer->StartAccess(bmdBufferAccessWrite))) {
      if (SUCCEEDED(buffer->GetBytes(&bytes)) && bytes != nullptr) {
        auto* dst = static_cast<uint8_t*>(bytes);
        if (format_ == bmdFormat8BitBGRA) {
          const size_t line = static_cast<size_t>(width_) * 4;
          for (int y = 0; y < height_; ++y) {
            std::memcpy(dst + static_cast<size_t>(y) * row_bytes_, staging_.data() + y * line, line);
          }
        } else {
          PackUyvy(staging_.data(), dst, width_, height_, row_bytes_, height_ < 720);
        }
        frame_version_[slot] = version_;
      }
      buffer->EndAccess(bmdBufferAccessWrite);
    }
    buffer->Release();
  }

  std::atomic<ULONG> refs_{1};
  IDeckLink* device_ = nullptr;
  IDeckLinkOutput* output_ = nullptr;
  int width_ = 0, height_ = 0, row_bytes_ = 0;
  BMDPixelFormat format_ = bmdFormat8BitBGRA;
  BMDTimeValue duration_ = 0;
  BMDTimeScale scale_ = 0;
  bool enabled_ = false;
  bool playing_ = false;
  std::atomic<bool> stopping_{false};

  std::vector<IDeckLinkMutableVideoFrame*> frames_;
  std::vector<uint64_t> frame_version_;
  std::mutex schedule_mutex_;
  int64_t next_index_ = 0;

  std::mutex staging_mutex_;
  std::vector<uint8_t> staging_;  // BGRA, top-down, output size
  uint64_t version_ = 0;

  std::atomic<int64_t> pushed_{0}, scheduled_{0}, completed_{0}, late_{0}, dropped_{0},
      flushed_{0}, resyncs_{0}, buffered_{0};
};

// ------------------------------------------------------------------ mirror

struct WindowSearch {
  DWORD pid;
  int w, h, x;
  HWND best;
  int best_score;
};

BOOL CALLBACK FindBlenderWindow(HWND hwnd, LPARAM param) {
  auto* search = reinterpret_cast<WindowSearch*>(param);
  DWORD pid = 0;
  GetWindowThreadProcessId(hwnd, &pid);
  if (pid != search->pid || !IsWindowVisible(hwnd)) return TRUE;
  wchar_t cls[64] = {};
  GetClassNameW(hwnd, cls, 64);
  if (wcscmp(cls, L"GHOST_WindowClass") != 0) return TRUE;
  RECT client{};
  GetClientRect(hwnd, &client);
  const int dw = std::abs((client.right - client.left) - search->w);
  const int dh = std::abs((client.bottom - client.top) - search->h);
  if (dw > 2 || dh > 2) return TRUE;
  POINT origin{0, 0};
  ClientToScreen(hwnd, &origin);
  const int score = (dw + dh) * 100000 + std::abs(origin.x - search->x);
  if (score < search->best_score) {
    search->best = hwnd;
    search->best_score = score;
  }
  return TRUE;
}

void Mirror::Configure(const bdo_mirror_config& config) {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    ++config_version_;
  }
  if (config.enabled && !running_) {
    stop_ = false;
    running_ = true;
    thread_ = std::thread([this] { Run(); });
  } else if (!config.enabled && running_) {
    Stop();
  }
}

void Mirror::Stop() {
  if (!running_) return;
  stop_ = true;
  if (thread_.joinable()) thread_.join();
  running_ = false;
}

void Mirror::CloseDuplication() {
  SafeRelease(staging_);
  SafeRelease(duplication_);
  SafeRelease(context_);
  SafeRelease(device_);
  monitor_ = nullptr;
  staging_w_ = staging_h_ = 0;
}

bool Mirror::OpenDuplication(HMONITOR monitor) {
  CloseDuplication();
  IDXGIFactory1* factory = nullptr;
  if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory)))) {
    SetError("DXGI is not available");
    return false;
  }
  IDXGIAdapter1* adapter = nullptr;
  IDXGIOutput* found = nullptr;
  for (UINT a = 0; found == nullptr && factory->EnumAdapters1(a, &adapter) != DXGI_ERROR_NOT_FOUND;
       ++a) {
    IDXGIOutput* output = nullptr;
    for (UINT o = 0; adapter->EnumOutputs(o, &output) != DXGI_ERROR_NOT_FOUND; ++o) {
      DXGI_OUTPUT_DESC desc{};
      output->GetDesc(&desc);
      if (desc.Monitor == monitor) {
        found = output;
        monitor_rect_ = desc.DesktopCoordinates;
        break;
      }
      output->Release();
    }
    if (found == nullptr) SafeRelease(adapter);
  }
  factory->Release();
  if (found == nullptr) {
    SetError("the monitor showing the viewport was not found");
    return false;
  }
  HRESULT hr = D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0,
                                 D3D11_SDK_VERSION, &device_, nullptr, &context_);
  adapter->Release();
  if (FAILED(hr)) {
    found->Release();
    SetError("D3D11 device creation failed (" + Hresult(hr) + ")");
    return false;
  }
  IDXGIOutput1* output1 = nullptr;
  hr = found->QueryInterface(__uuidof(IDXGIOutput1), reinterpret_cast<void**>(&output1));
  found->Release();
  if (FAILED(hr)) {
    SetError("desktop duplication needs Windows 8 or newer");
    return false;
  }
  hr = output1->DuplicateOutput(device_, &duplication_);
  output1->Release();
  if (FAILED(hr)) {
    SetError("desktop duplication refused (" + Hresult(hr) + ")");
    CloseDuplication();
    return false;
  }
  monitor_ = monitor;
  SetError("");
  return true;
}

void Mirror::Run() {
  uint64_t seen_version = UINT64_MAX;
  HWND hwnd = nullptr;
  std::vector<uint8_t> source;
  std::vector<uint8_t> frame(static_cast<size_t>(output_->width()) * output_->height() * 4);
  bool dirty = true;
  auto next_due = std::chrono::steady_clock::now();

  while (!stop_) {
    bdo_mirror_config config;
    uint64_t version;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      config = config_;
      version = config_version_;
    }
    if (version != seen_version) {
      seen_version = version;
      hwnd = nullptr;
      dirty = true;
    }
    const double fps = config.max_fps > 0 ? std::min<double>(config.max_fps, output_->fps())
                                          : output_->fps();
    const auto interval = std::chrono::microseconds(static_cast<int64_t>(1e6 / std::max(fps, 1.0)));

    if (config.src_w < 2 || config.src_h < 2) {
      SetError("waiting for the viewport (is it in camera view?)");
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      continue;
    }
    if (hwnd == nullptr || !IsWindow(hwnd)) {
      WindowSearch search{GetCurrentProcessId(), config.win_w, config.win_h, config.win_x, nullptr,
                          INT_MAX};
      EnumWindows(FindBlenderWindow, reinterpret_cast<LPARAM>(&search));
      hwnd = search.best;
      if (hwnd == nullptr) {
        SetError("Blender window not found on screen");
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
        continue;
      }
    }
    if (IsIconic(hwnd)) {
      SetError("the mirror window is minimised");
      std::this_thread::sleep_for(std::chrono::milliseconds(250));
      continue;
    }
    POINT origin{config.src_x, config.src_y};
    ClientToScreen(hwnd, &origin);
    const RECT rect{origin.x, origin.y, origin.x + config.src_w, origin.y + config.src_h};
    const POINT centre{(rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2};
    HMONITOR monitor = MonitorFromPoint(centre, MONITOR_DEFAULTTONEAREST);
    if (monitor != monitor_ || duplication_ == nullptr) {
      if (!OpenDuplication(monitor)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        continue;
      }
      dirty = true;
    }

    DXGI_OUTDUPL_FRAME_INFO info{};
    IDXGIResource* resource = nullptr;
    const DWORD wait = static_cast<DWORD>(std::max<int64_t>(
        1, std::chrono::duration_cast<std::chrono::milliseconds>(interval).count()));
    HRESULT hr = duplication_->AcquireNextFrame(wait, &info, &resource);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) continue;  // screen unchanged: the card repeats
    if (hr == DXGI_ERROR_ACCESS_LOST) {
      CloseDuplication();
      continue;
    }
    if (FAILED(hr)) {
      SetError("desktop capture failed (" + Hresult(hr) + ")");
      CloseDuplication();
      std::this_thread::sleep_for(std::chrono::milliseconds(250));
      continue;
    }
    const auto started = std::chrono::steady_clock::now();
    const bool changed = info.LastPresentTime.QuadPart != 0 || dirty;
    if (changed) {
      ID3D11Texture2D* desktop = nullptr;
      resource->QueryInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&desktop));
      // Visible part of the rectangle on this monitor, in monitor-local pixels.
      const LONG left = std::max(rect.left, monitor_rect_.left);
      const LONG top = std::max(rect.top, monitor_rect_.top);
      const LONG right = std::min(rect.right, monitor_rect_.right);
      const LONG bottom = std::min(rect.bottom, monitor_rect_.bottom);
      source.assign(static_cast<size_t>(config.src_w) * config.src_h * 4, 0);
      if (desktop != nullptr && right > left && bottom > top) {
        const int cw = right - left, ch = bottom - top;
        if (staging_ == nullptr || staging_w_ != cw || staging_h_ != ch) {
          SafeRelease(staging_);
          D3D11_TEXTURE2D_DESC desc{};
          desktop->GetDesc(&desc);
          desc.Width = cw;
          desc.Height = ch;
          desc.MipLevels = 1;
          desc.ArraySize = 1;
          desc.SampleDesc = {1, 0};
          desc.Usage = D3D11_USAGE_STAGING;
          desc.BindFlags = 0;
          desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
          desc.MiscFlags = 0;
          device_->CreateTexture2D(&desc, nullptr, &staging_);
          staging_w_ = cw;
          staging_h_ = ch;
        }
        if (staging_ != nullptr) {
          const D3D11_BOX box{static_cast<UINT>(left - monitor_rect_.left),
                              static_cast<UINT>(top - monitor_rect_.top), 0,
                              static_cast<UINT>(right - monitor_rect_.left),
                              static_cast<UINT>(bottom - monitor_rect_.top), 1};
          context_->CopySubresourceRegion(staging_, 0, 0, 0, 0, desktop, 0, &box);
          D3D11_MAPPED_SUBRESOURCE mapped{};
          if (SUCCEEDED(context_->Map(staging_, 0, D3D11_MAP_READ, 0, &mapped))) {
            const int ox = left - rect.left, oy = top - rect.top;
            for (int y = 0; y < ch; ++y) {
              std::memcpy(source.data() + (static_cast<size_t>(oy + y) * config.src_w + ox) * 4,
                          static_cast<const uint8_t*>(mapped.pData) +
                              static_cast<size_t>(y) * mapped.RowPitch,
                          static_cast<size_t>(cw) * 4);
            }
            context_->Unmap(staging_, 0);
          }
        }
      }
      SafeRelease(desktop);
    }
    resource->Release();
    duplication_->ReleaseFrame();

    if (changed) {
      Reframe(source.data(), config.src_w, config.src_h, frame.data(), output_->width(),
              output_->height(), config.dst_x, config.dst_y, config.dst_w, config.dst_h);
      output_->Push(frame.data(), output_->width(), output_->height(), output_->width() * 4, 0);
      ++frames;
      dirty = false;
      SetError("");
      last_us = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - started).count();
    }
    next_due += interval;
    const auto now = std::chrono::steady_clock::now();
    if (next_due > now) {
      std::this_thread::sleep_until(next_due);
    } else {
      next_due = now;
    }
  }
  CloseDuplication();
}

// ------------------------------------------------------------------ handles

std::mutex g_handles_mutex;
std::set<Output*> g_handles;

Output* Lookup(void* handle) {
  std::lock_guard<std::mutex> lock(g_handles_mutex);
  auto* output = static_cast<Output*>(handle);
  return g_handles.count(output) ? output : nullptr;
}

}  // namespace

// ------------------------------------------------------------------ C API

BDO_API int bdo_api_version(void) { return BDO_API_VERSION; }

BDO_API int bdo_list_devices(char* buffer, int cap) {
  return CopyOut(ListDevicesJson(), buffer, cap);
}

BDO_API void* bdo_open(const char* device_name, uint32_t mode, int preroll, char* error, int cap) {
  auto* output = new Output();
  std::string reason;
  const std::string name = device_name ? device_name : "";
  InMta([&] {
    reason = output->Open(name, static_cast<BMDDisplayMode>(mode), preroll);
    if (!reason.empty()) output->Shutdown();
  });
  if (!reason.empty()) {
    CopyOut(reason, error, cap);
    output->Release();
    return nullptr;
  }
  CopyOut("", error, cap);
  std::lock_guard<std::mutex> lock(g_handles_mutex);
  g_handles.insert(output);
  return output;
}

BDO_API int bdo_push(void* handle, const void* pixels, int width, int height, int row_bytes,
                     int flags) {
  Output* output = Lookup(handle);
  if (output == nullptr || pixels == nullptr) return -1;
  return output->Push(pixels, width, height, row_bytes, flags);
}

BDO_API int bdo_mirror(void* handle, const bdo_mirror_config* config) {
  Output* output = Lookup(handle);
  if (output == nullptr || config == nullptr) return -1;
  if (!output->mirror_) output->mirror_ = std::make_unique<Mirror>(output);
  output->mirror_->Configure(*config);
  return 0;
}

BDO_API int bdo_mirror_error(void* handle, char* buffer, int cap) {
  Output* output = Lookup(handle);
  if (output == nullptr) return CopyOut("", buffer, cap);
  return CopyOut(output->mirror_ ? output->mirror_->Error() : std::string(), buffer, cap);
}

BDO_API int bdo_get_stats(void* handle, bdo_stats* out) {
  Output* output = Lookup(handle);
  if (output == nullptr || out == nullptr) return -1;
  output->Stats(out);
  return 0;
}

BDO_API void bdo_close(void* handle) {
  Output* output = nullptr;
  {
    std::lock_guard<std::mutex> lock(g_handles_mutex);
    auto* candidate = static_cast<Output*>(handle);
    if (g_handles.erase(candidate)) output = candidate;
  }
  if (output == nullptr) return;
  if (output->mirror_) output->mirror_->Stop();
  InMta([output] { output->Shutdown(); });
  output->mirror_.reset();
  output->Release();
}
