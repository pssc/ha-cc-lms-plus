# Changelog

## 0.1.0 — Experimental pre-release

This is the first experimental release of LMS Plus. Features and configuration
may change rapidly, and breaking changes are expected while compatibility is
being validated.

### Included

- Per-player lossless, bitrate, stream, queue position, and end-of-queue
  information.
- Playlist To-do controls to play, remove, and reorder queued tracks, with
  configurable summaries and descriptions.
- Current-track rating display and control, using Ratings Light with TrackStat
  as a fallback.
- Per-player repeat, shuffle, crossfade, ReplayGain, sleep timer, end-of-track
  sleep, and daily alarm controls.
- Optional server-level player moves and LMS playlist/library scan controls,
  including a guarded library wipe and rebuild action.
- Reuses the existing Home Assistant Squeezebox integration connections and
  player devices.

### Compatibility

- Compatibility target: Home Assistant Core 2025.11.1 with PySqueezebox 0.13.0.
- Development smoke test: Home Assistant Core 2026.11.1 with PySqueezebox
  0.14.0.
- Track ratings require the LMS Ratings Light or TrackStat plugin.
