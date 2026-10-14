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
