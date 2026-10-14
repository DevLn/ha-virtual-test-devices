# PistonCore Test Devices — fork provenance

This folder is a **fork of [`twrecked/hass-virtual`](https://github.com/twrecked/hass-virtual)**
(GPL-3.0), the base for PistonCore's **test devices** feature. See
`../VIRTUAL_DEVICES_SPEC.md` for the why and the plan.

## Upstream snapshot

- **Source:** https://github.com/twrecked/hass-virtual
- **Forked at commit:** `a056ceaf73d4907e258ae055c3541342a3275400` (2026-03-06)
- **Upstream version:** 0.9.4 — domain `virtual`, `config_flow: true`, `iot_class: local_push`
- **License:** GPL-3.0 (unchanged; matches PistonCore). Upstream copyright headers kept.
- Vendored: `LICENSE`, `README.md`, `info.md`, `hacs.json`, `custom_components/virtual/`.
  (Skipped upstream `images/`, `install/`, `changelog/` — not needed to run.)

## What upstream already gives us (verified from source, 2026-07-21)

- **10 platforms:** binary_sensor, sensor, switch, light, lock, fan, cover, valve,
  number, device_tracker.
- **Grouped multi-entity devices** (a device with several entities under one
  device-registry entry — the shape PistonCore's picker needs).
- **GUI config flow** (`config_flow.py`) — the standalone create/remove surface
  (VIRTUAL_DEVICES_SPEC §5.6: standalone self-sufficiency).
- **Set-state services:** `virtual.turn_on / turn_off / toggle / set / set_available / move`.

## What PistonCore adds on this fork (see VIRTUAL_DEVICES_SPEC §5.2)

- **Missing platforms** the compiler targets: `alarm_control_panel`, `climate`,
  `media_player`, `siren`, `humidifier`, `vacuum`, `button` (camera only if a corpus
  piston needs it).
- **Live create/remove an outside app can trigger** (PistonCore drives it without
  hand-YAML), routed through HA-native config-flow/services so standalone users get it too.
- **`virtual.clone_device` — cloning lives HERE, not in the caller (2026-07-31).**
  New `clone.py`. Reads a real device and creates a test copy reporting the same
  abilities. It belongs in this repo because the platforms validate against CLOSED
  schemas: the capture list and those schemas are one contract, and splitting them
  across two projects guarantees drift. PistonCore now only names a device. The
  service takes a `device` selector, so HA's own Actions screen gives standalone
  users a searchable picker with no frontend to maintain.
- **Entity names within a group must be unique (2026-07-31).** Identity is keyed by
  entity NAME within the group, so two devices with an identically-named entity
  re-register on every restart — one device cloned twice grew the entity registry
  by five entities per boot, forever, silently. `clone.py` therefore names entities
  `<device name> <capability>`.
- **Two data-loss bugs fixed in `cfg.py` (2026-07-31) — these are upstream, and
  they destroyed every test device on the bench before being found.**
  `_async_save_yaml`/`_async_save_json` opened the real file in `'w'` (which
  TRUNCATES IT TO ZERO) and only *then* serialized. Anything yaml couldn't
  represent therefore wiped the file, and the exception was swallowed at DEBUG
  level so nothing reported it. The integration then refused to load at all,
  because `_load_user_data` called `.get` on the `None` an empty file parses to.
  Now: serialize first, write to a temp file and `os.replace` it into place, log
  failures at ERROR and re-raise; and `_load_user_data` degrades to "no devices"
  rather than taking the integration down. Worth carrying upstream.
- **Concurrency: `create_device`/`remove_device` were racing.** Both did
  read-modify-write on one yaml file then reloaded the config entry. Fired
  concurrently, every caller loaded before any caller saved, so all but the last
  were silently erased — six concurrent creates produced one device and no error.
  `pistoncore_manage._mutate_and_reload` now serializes the file edit per entry
  and coalesces a burst into a single reload (measured: 8 concurrent creates cost
  the same one reload as 1).
- **Full-fidelity cloning (2026-07-31, VIRTUAL_DEVICES_SPEC §5.7a).** Every
  platform now accepts the abilities a real device advertises — `supported_features`
  verbatim, plus its mode menus and limits — instead of hardcoding one shape per
  domain. Shared helpers live in `entity.py` (`FEATURES_SCHEMA`, `feature_flags`,
  `optional_list`); each platform names its own list/range keys. **Every key is
  optional and falls back to the platform's previous hardcoded default**, so
  upstream behaviour and existing `virtual.yaml` files are unchanged.
  Touches upstream files (light, fan, cover, lock, sensor, number) as well as the
  PistonCore-added ones — keep that in mind on an upstream rebase; the changes are
  confined to the schema block and `__init__`, plus new `async_*` handlers.
  Two upstream bugs fixed in passing: `lock.open` called a sync stub and always
  raised, and cloning any device containing a `number` entity could never succeed
  because that platform requires min/max.

## Device tracker: migrated off `location_name` (2026-08-02)

HA deprecated `TrackerEntity.location_name` (removal 2027.7). Migrated NOW rather
than at the deadline, because the integration had been public for one day and
nobody depended on the old behaviour — waiting would only have meant doing it
when the person who understood it might be gone.

**This is a deliberate behaviour change, not a rename.** `location_name` returned
free text that HA used as the state verbatim, so a virtual tracker could report
`school` with no such zone anywhere. The replacement, `in_zones`, takes zone
entity_ids and HA discards zones that do not exist — so the state can now only be
`home`, `not_home`, or the name of a real zone.

Taken on purpose: a REAL device tracker can only report those things, so a
virtual one inventing states your actual devices can never produce is a worse
bench. A location naming no existing zone now reports `not_home` **and logs a
warning saying so** — silently reporting the wrong thing is what a test bench
must never do.

VERIFIED on 2026.8.0b3: `home` -> `home`, capitalisation handled, unknown zone ->
`not_home` + warning, `not_home` explicit, GPS outside a zone -> `not_home` with
coordinates intact, GPS inside the home zone -> `home`, and the state survives a
restart. Upstream had not migrated this as of 2026-08-02.

## Explicit entity ids, and orphans removed one row at a time (2026-10-14)

An entry with an `entity_id` now gets exactly that id, with its `unique_id`
taken from the file or derived from the id — never random — so a regenerated
file maps onto the same registry rows. This is the basis for using one HA as a
stand-in for another: the test instance carries the live instance's entity ids.
Such entries need no meta-file row (the identity is in the file), so they cannot
collide with the name-keyed ones. A wrong domain or a repeated id is logged at
ERROR and the entry skipped; the first entry keeps a contested id. `entity.py`
warns when HA registers an entity under a different id than the one requested.

Orphan handling was rewritten in passing, because the requirement "removing an
entity leaves its siblings alone" exposed an upstream bug: `async_setup_entry`
removed the orphan's whole DEVICE, which took the device's surviving entities
with it and recreated the device under a new id at platform setup, losing its
area and customisations and breaking anything that referred to it by device id.
Now the file is the source of truth: entity rows under the config entry whose
unique_id is not in the file are removed one at a time, and a device is removed
only when the file no longer lists it. Side effect: the first load after
upgrading also clears the dead rows left by the duplicate-name bug above. Worth
carrying upstream.

## A device in the file can say what it is (2026-10-14)

`Name: [entities]` still works; `Name: {manufacturer, model, sw_version, area,
entities}` also loads and reaches the device registry, so a stand-in's device
page reads like the real device's. `area` is honoured at creation only — HA's
own rule, verified in 2026.10.1's device registry: on an existing device,
`suggested_area` updates a runtime-only attribute and never moves the device.
One edit to upstream code: `entity.py` now passes `DeviceInfo(identifiers=...)`
only. It used to repeat manufacturer/model, which would have written the
constants back over the file's values on every entity add. Safe because the
device is created with its full details in `__init__` before any platform is
set up, so behaviour for existing files is unchanged. The file rewriters
(create, remove, clone) replace or drop whole device values and never look
inside the others, so a mapping-form device survives them untouched (tested).

## Export-only mode and `virtual.export_devices` (2026-10-14)

What the explicit ids exist for: one HA stands in for another. On the live HA,
`virtual: {export_only: true}` makes the integration provide exactly one thing,
the export action, and refuse everything else: `async_setup` starts no import
flow and raises no deprecation issue, `async_setup_entry` raises
ConfigEntryError (so the UI says why), and the config flow aborts with
`export_only`. The action is registered in `async_setup` in both modes, because
on a live HA there is no group to hang it on; HA calls `async_setup` whenever an
entry exists, so normal mode gets it too.

`export.py` walks the entity registry once (plus states with no row), buckets by
device, keeps the live friendly name and `entity_id`, takes class, unit and
abilities from clone.py's per-entity capture (`entity_spec`, extracted so clone
and export cannot disagree, including about codes), maps the live state to
`initial_value` (unavailable gives `initial_availability: false`; unknown gives
none; button, camera, event and notify give none; a light also gets brightness,
hs colour and effect) and validates every entry against the platform's own
closed schema before writing. A rejected entry goes to the report, never the
file, so the file always loads. The report (each individually skipped entity
with its reason, and non-reproducible domains as one count per domain, since a
live HA has hundreds) is returned by the action and appended to the file as
comments. Written with cfg.py's atomic writer, under a header naming export
time, HA version and integration version. Colour temperature is not exported:
the light platform reads `initial_color_temp` as kelvin but still validates it
as a byte, so no real value would pass.

## Version 0.14.0-hts.1 (2026-10-14)

Minor bump for the three features above, with a `-hts.1` pre-release tag
(ha-test-sim) so a build of this branch is told apart from an upstream release.
`__version__` in `__init__.py` had drifted to 0.9.4 while the manifest said
0.13.3, so every device's sw_version was wrong; the two are now the same string
and a test keeps them that way, and checks the format against the same
AwesomeVersion strategies HA's loader applies.

## Fix: the default export path was refused (2026-10-14, 0.14.0-hts.2)

HA's `is_allowed_path` only covers `allowlist_external_dirs`, which by default
is `<config>/www` and the media folders, not the config folder itself. The
exporter checked only that, so the default `<config>/virtual_export.yaml` was
refused on every real install. The tests missed it because the fake was more
lenient than HA; it now applies HA's rule. Writing is allowed inside the config
folder or under the allowlist.

## Fix: buttons with a device class were rejected (2026-10-14, 0.14.0-hts.3)

The button platform (added in upstream, jercoates) declared no `class`, so its
closed schema rejected any button carrying one. Real restart, identify and
update buttons all do. Export skipped every such button; `clone_device` was
worse, writing the entry unchecked so the next reload failed the whole button
platform for that group. Button now accepts `class` and uses it as its device
class, like switch. It was the only platform with this gap.

## Closer copies: entity category, MAC, Visit link; capitals in ids (2026-10-14, 0.14.0-hts.4)

Found by loading a real ESPHome export. Every platform now accepts
`entity_category` (via `virtual_schema`), so a stand-in sits in the same
Configuration/Diagnostic section as the real entity; the exporter copies it
from the registry. A device can carry `mac` and `configuration_url`. The MAC is
only given on HA 2026.8 or later: from 2026.8 the device registry matches a
connection within its own config entry only, so a stand-in cannot merge with
the real device. Before that the match spanned all integrations, which would
have joined the stand-in to the real device and let orphan removal delete it.
A configuration_url HA refuses is dropped with an error rather than taking the
group down. An explicit entity_id with capital letters is lowered with a
warning instead of refused: find-and-replace on an export had produced a
device with no entities and only an ERROR line per entity to say why.

## Select and text platforms (2026-10-14, 0.14.0-hts.5)

The settings devices expose (ESPHome log level, modes, names) were skipped by
the exporter as "domain not reproducible". `select.py` and `text.py` add them,
driven by HA's own select/text actions. Capture is HA's capability attributes:
`options` for select, `min`/`max`/`pattern`/`mode` for text, checked against HA
by the existing capability test. HA's TextEntity RAISES for a state that breaks
its own limits, so a starting or restored text value that does not fit becomes
empty with a warning; a select value off its menu falls back to the first
option. A `password` text is exported without its value, the same privacy
boundary as lock and alarm codes.

## Export: `exclude_devices` (2026-10-14, 0.14.0-hts.6)

Network integrations (Omada here) turn every unknown client into a device
named after its MAC, with a tracker and a handful of statistics on it, and
neither Omada integration can be told not to. Entity-id patterns cannot cut
those cleanly: their names overlap with Zigbee signal sensors, a NAS and a
wall tablet. `exclude_devices` takes device names or globs and drops every
entity on a matching device. It matches the name shown in the UI (the user's
rename, else the integration's), so renaming a device is how to keep one.
Entities with no device are untouched. Each excluded device is ONE report line
with its entity count, not one line per entity.

## Tests (added 2026-08-01)

`tests/` guards the three silent-data-loss bugs fixed in this fork — the mutation
race, the truncate-before-serialize save, and a clone dropping abilities. Run them
inside an HA container (`tests/README.md`); 37 pass, no `pytest-asyncio` needed.

**Tamper-verified**, and that mattered: the first version of the suite passed
every deliberate break, because `docker exec` silently discards stdin without
`-i`, so the tampering never ran. A suite nobody has watched fail is not evidence.

**Backward compatibility is covered by design, not by luck**: every cloning key is
optional and falls back to the platform's original hardcoded default. VERIFIED
2026-08-01 against a hand-written upstream-style `virtual.yaml` — 8/8 devices
behaved identically, including the light and fan paths this fork rewrote most
(`support_color: true` still gives hs mode; `speed: true` still gives 3 speeds).

## Rules

- **Standalone self-sufficiency is REQUIRED** (§5.6): everything works from HA's own
  UI/services with no PistonCore. PistonCore's `/test-devices` panel is a convenience
  layer, never the only control surface.
- Keep it rebase-friendly: prefer ADDING files (new platform modules) over rewriting
  upstream ones, so pulling upstream fixes stays easy.
- **Split to its own repo at release** (§5.5); in-repo folder is for iteration now.
