# LMS Plus

> **Experimental:** LMS Plus is under active development. Features, setup,
> entity names, and configuration options may change rapidly and without
> notice. Expect occasional breaking changes while the integration is being
> tested against its pinned Home Assistant Core and PySqueezebox versions.
> Back up your Home Assistant configuration before trying it, and avoid
> relying on it for essential automations until the project declares a stable
> release.

LMS Plus is a Home Assistant custom integration that adds per-player sensors
and controls using the existing Squeezebox (Lyrion Music Server) integration.
It does not replace or modify Home Assistant Core or the Squeezebox
integration.

[![Open LMS Plus in the Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=pssc&repository=ha-cc-lms-plus&category=integration)
[![Open LMS Plus in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pssc&repository=ha-cc-lms-plus&category=integration)

## License

The LMS Plus integration code is licensed under **GNU GPL version 2 only**;
see [LICENSE](LICENSE). The LMS waveform icon is a separate Lyrion brand asset
with its own applicable terms.

LMS Plus adds LMS-specific playback information and controls to Squeezebox
players, plus optional controls for the LMS server.

## Requirements

- Home Assistant with the built-in Squeezebox integration configured.
- A reachable Lyrion Music Server (LMS) with the player or players to manage.
- For track ratings, the Ratings Light or TrackStat LMS plugin.

LMS Plus reuses the Squeezebox integration's server connections. Some features
depend on private Squeezebox and PySqueezebox APIs; the supported versions are
listed in [ha-core-compatibility.txt](ha-core-compatibility.txt).

## Features

### Player

LMS Plus adds entities to each selected player's existing Squeezebox device:

- Sensors for lossless status, bitrate, streaming status, queue position, and
  end of queue.
- A Playlist To-do list for viewing, playing, removing, and reordering queued
  tracks. Track artwork is provided as a link.
- Controls for current-track rating, sleep timer, end-of-track sleep, and daily
  alarm time.
- Crossfade and ReplayGain settings. Repeat and shuffle use the existing
  Squeezebox media player controls.

### Server

Optional server controls are added to the existing Squeezebox server device:

- **Player management:** move a player to this LMS. LMS may move its
  synchronized group too.
- **Scans:** rescan playlists or check the music library for changes. The
  built-in Squeezebox integration already provides the library-rescan status
  sensor.
- **Clean Library Scan:** wipe and rebuild the library with a separate
  confirmation. This is destructive and intended for recovery, not routine
  scans.

## Compatibility

LMS Plus currently targets **Home Assistant Core 2025.11.1** with
**PySqueezebox 0.13.0**. A development smoke test also passed on Home Assistant
Core 2026.11.1 with PySqueezebox 0.14.0. Some features rely on private APIs in
the built-in Squeezebox integration, so compatibility with other versions is
not guaranteed. The [compatibility notes](ha-core-compatibility.txt) record
the checked versions and implementation details.

## Installation

### Home Assistant Marketplace

Once this repository is public, add `pssc/ha-cc-lms-plus` from **Settings →
Marketplace → Add from link**, select the integration type if prompted, then
install **LMS Plus**. A GitHub connection is needed by Marketplace to add a
custom repository and keep it updated. Catalog listing is a separate later
step; you can install it as a custom repository before then.

### HACS

Add `pssc/ha-cc-lms-plus` as a custom repository with the **Integration**
category, then install **LMS Plus**. Restart Home Assistant if prompted.

### Manual installation

Copy the `custom_components/lms_plus` directory into the Home Assistant
configuration's `custom_components` directory, then restart Home Assistant.

## Setup

Add **LMS Plus** from **Settings → Devices & services → Add integration**.

### Player entities

Choose a Squeezebox server, then select a player. Create one player entry for
each player you want to manage. Entities are added to that player's existing
Squeezebox device, and the entry is named **Server — Player**. Use the options
to change the selected server or player. Moving a player from another configured
server requires confirmation; LMS may move its synchronized group too.

### Server controls

Add a separate LMS Plus entry, choose the Squeezebox server, and enable
**Add server-level controls**. The controls are added to that server's existing
Squeezebox device.

### Playlist formats

Set the playlist summary and description formats in the player entry's options.
The defaults are `{track_number}. {trackname} {rating}` for the summary and
artist, album and year, bitrate, format, and duration for the description.
Available placeholders are `{track_number}`, `{trackname}`, `{rating}`,
`{artist}`, `{album}`, `{year}`, `{bitrate}`, `{format}`, and `{duration}`.
Values LMS does not provide are left blank.

