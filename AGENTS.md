# Agent guidance

## Project scope

- This repository contains the `lms_plus` Home Assistant custom integration.
- It augments Home Assistant's built-in Squeezebox (Lyrion Music Server)
  integration. Do not modify or copy Home Assistant Core files into this
  repository.
- Keep additions focused on the requested LMS Plus functionality.
- Do not duplicate functionality already provided by the built-in Squeezebox
  integration. Before adding an entity or control, check what the existing
  integration exposes and add only the missing LMS Plus functionality.

## Compatibility

- The Squeezebox runtime API compatibility target is recorded in
  `ha-core-compatibility.txt`, including the paired PySqueezebox version.
- Before changing code that depends on Squeezebox internals or PySqueezebox
  APIs, check the pinned Home Assistant Core release and verify the relevant
  source for that release and its PySqueezebox dependency.
- Update `ha-core-compatibility.txt` and the compatibility notes in `README.md`
  whenever the target Core version changes.
- Prefer using the existing Squeezebox config entry's runtime data, player
  coordinators, and player objects rather than creating another LMS connection.

## Integration structure

- Place integration code under `custom_components/lms_plus/`.
- Name each per-player LMS Plus config entry `Squeezebox server — player`,
  matching the server-first relationship used by the Squeezebox integration.
  Keep LMS Plus entities attached to that player's existing Squeezebox device.
- Keep the integration manifest accurate and minimal. Do not add dependencies
  unless the feature requires them.
- Add entities to the existing Squeezebox device when they represent a selected
  Squeezebox player.
- The setup flow selects a destination Squeezebox config entry first, then a
  player entity known to Home Assistant's Squeezebox integration. Players
  already assigned to the destination can be selected directly. For a player
  assigned to another loaded Squeezebox entry, clearly label the move and ask
  for explicit confirmation before sending LMS's `disconnect` command from the
  destination server connection. Continue setup only after the Squeezebox
  integration assigns the player to the destination entry.
- The options flow follows the same server/player flow and can move a player
  from another configured server after confirmation. Resolve the destination
  coordinator only from the selected Squeezebox config entry. Use its existing
  PySqueezebox server connection; do not open another LMS connection.
- LMS may move every player in a synchronized group when moving one player.
  State this consequence in the confirmation form.
- The LMS pull command is server-level: use the destination player's existing
  PySqueezebox server object via the pinned private `Player._lms` reference and
  call `Server.async_query("disconnect", player_id, source_server_host)` with
  its default empty player argument. LMS uses that source host to send a
  `connect` request to the remote server, which then directs the player to the
  destination. Do not send this through `Player.async_query`, which prefixes a
  player ID.
- After asking LMS to move a player, verify through the existing source and
  destination server connections that it is connected to the destination and
  no longer connected to the source. Read the `connected` flags from the LMS
  `players status` response; PySqueezebox's `async_get_players()` constructs
  player objects without populating their connected status. Only then update the existing Squeezebox
  device registry entry to the destination config entry. Core may already have
  created an empty duplicate device record on the destination; remove that
  record before transferring the existing device, and refuse the move if that
  duplicate has entities. Support the pinned
  Core 2025.11 `add_config_entry_id`/`remove_config_entry_id` API and newer
  `new_config_entry_id` API. Still wait for the Squeezebox integration to
  assign the player entity to the destination before completing LMS Plus setup.
- Keep player-specific sensors and controls on that player's existing device;
  use its coordinator and player object rather than opening another LMS
  connection.
- Repeat and shuffle are controlled through the built-in Squeezebox media
  player entity and Home Assistant's standard media-player actions; do not add
  duplicate LMS Plus controls for these features.
- Crossfade and ReplayGain selectors use the player's `transitionType` and
  `replayGainMode` preferences through its existing `async_query` connection.
  Mark these preference entities as configuration entities. Validate selector
  values with `playerpref validate`, then read back after writing. The Sleep
  timer uses the player's `sleep` command. Send sleep durations in seconds to
  LMS while displaying minutes in Home Assistant. The end-of-track sleep button
  checks that a timed track is playing, then invokes LMS's
  `jiveendoftracksleep` command over the same player connection.
- The playlist To-do entity operates on the live LMS queue: completing an item
  plays it with the selected player's `async_index`, deleting items sends LMS
  `playlist delete` commands in descending index order, and moving an item
  sends `playlist move`. Resolve item IDs against the latest `playlist_loop`
  before issuing commands, then refresh the existing player coordinator.
- The current-track rating control uses Ratings Light first and TrackStat as a
  fallback. Confirm a write from the plugin response's `ratingpercentage`.
- The daily alarm time entity edits the earliest configured daily alarm through
  the selected player's existing connection, or creates an enabled daily alarm
  when none exists. The built-in Squeezebox integration already exposes alarm
  status and the global alarms-enabled switch; do not duplicate those entities.
- Name server-level config entries `Squeezebox server — LMS Plus controls`,
  matching the server-first `Squeezebox server — player` entry naming.
- Attach server-level LMS Plus entities to the selected Squeezebox server's
  existing device using that config entry's unique ID; do not create a second
  LMS Plus device for the same server.
- Playlist-only scans use the server-level `rescan playlists` command. Normal
  music library scans use `rescan` directly. The destructive `wipecache`
  rebuild has a separate arm and confirmation, limited to 60 seconds.
- Use LMS's `lossless` track flag (`Q` tag) for lossless classification instead
  of maintaining a separate codec allowlist. If LMS does not report the flag,
  represent the result as unknown.

## Validation

- For changes to Python modules, run Python compilation checks.
- For changes to `manifest.json`, validate that it parses as JSON.
- Do not change Home Assistant Core or the upstream Squeezebox integration.
