# Virtual Test Devices for Home Assistant

Virtual devices for testing Home Assistant automations — including **clones of
your real devices** that report the same abilities, so you can try an automation
against a stand-in instead of the real thing.

Works entirely on its own from Home Assistant's own **Developer tools → Actions**.
It is also the test bench for
[PistonCore](https://github.com/jercoates/pistoncore-v2), which is what it was
built for — but nothing here needs PistonCore installed.

> [!IMPORTANT]
> This is a fork of [twrecked/hass-virtual](https://github.com/twrecked/hass-virtual)
> and **uses the same `virtual` domain**, so it installs to the same folder.
> Install this **or** hass-virtual, not both.

---

## What it adds over hass-virtual

**More device kinds.** Upstream covers binary_sensor, sensor, switch, light,
lock, fan, cover, valve, number and device_tracker. This fork adds
`alarm_control_panel`, `camera`, `climate`, `media_player`, `notify`,
`siren`, `humidifier`, `vacuum`, `button` and `event`.

**Devices that state their real abilities.** Every platform accepts Home
Assistant's `supported_features` plus the mode lists and limits that go with it,
instead of hardcoding one shape per domain. A virtual thermostat can be a
cool-only single-setpoint unit or a full heat/cool-range one with fan and preset
menus, because that is what you told it to be.

**Cloning a real device** (`virtual.clone_device`). Point it at a device you own
and it creates a test copy reporting the same abilities — mode lists, limits,
feature flags. The list of what gets copied comes from Home Assistant's own
definition of capability attributes, not from guesswork, so it keeps up as HA
adds them.

**Describing a device without creating it** (`virtual.describe_device`) returns
that same specification as data — useful for putting "here is what my device
looks like" into a bug report so somebody else can rebuild it.

> [!NOTE]
> `describe_device` returns your **real device and entity names**. Nothing is
> sent anywhere — this integration makes no network requests of any kind, so the
> only way that leaves your system is if you paste it somewhere. Give it a read
> before putting it in a public issue. (PistonCore replaces names with stand-ins
> automatically in the reports it builds; this integration on its own does not.)

**A camera that hands over a real picture.** Home Assistant's own snapshot
action does the work — it asks the camera for an image and writes the file — so a
snapshot lands as a real file at a real path, under HA's normal rules about where
it may write. An automation asking for a snapshot somewhere HA won't write fails
here exactly as it would on real hardware. The picture changes every time it is
asked, so two snapshots look different and you can tell an automation fired twice
rather than once.

**A notifier that records what was sent.** Home Assistant ships with almost
nowhere to send a notification — a dashboard pop-up, and that is all; everything
else arrives when you install a phone app, an SMS service or email. That makes
notification automations awkward to test. Send to a virtual notifier instead and
the message, the title and a running count appear on the entity, visible in
Developer tools → States or by clicking it on a dashboard. It also fires a
`virtual_notify_sent` event, so notifications can be watched arriving live in
Developer tools → Events. The count is the useful part: it shows an automation
firing twice when it should have fired once, which the message alone cannot.

### What cloning will and will not do

It reproduces **shape, not behaviour**: what a device *says it can do*, not how
its own integration handles values. A capability or shape bug is reproducible on
a clone. A bug in how some bridge mangles a value on the way through is not, and
no amount of attribute copying will change that.

It also **never copies lock or alarm codes**. Bridged panels and locks expose
user code tables — sometimes plaintext PINs with the names attached — and sticking
to Home Assistant's capability attributes keeps all of it out. That matters if you
share a clone in a bug report, and it is guarded by a test: feeding the capture a
real bridged lock and alarm panel and asserting no code, name or PIN survives.

Nothing this integration produces ever leaves your Home Assistant on its own. It
makes **no network requests at all** — no telemetry, no analytics, no calls home.
Clones are written to a file in your own config folder and nowhere else.

---

## Fixes carried over upstream

Three bugs that each caused **silent** data loss, all found and fixed here:

- **Concurrent creates lost devices.** `create_device` / `remove_device` did
  read-modify-write on one file then reloaded. Fired together, every caller read
  before any caller wrote — six concurrent creates produced one device and
  reported success. Now serialised, with a burst collapsing to a single reload.
- **A failed save destroyed the device file.** The savers opened the real file in
  write mode (truncating it) and only then serialised, swallowing failures at
  debug level. One value yaml couldn't represent emptied the file, wiped every
  device, and left the integration unable to load — with nothing in the log.
  Now: serialise first, write via a temp file, replace atomically, and log loudly.
- **`lock.open` never worked.** It called a stub that always raised.

---

## Install

Add this repository to HACS as a custom repository (type: Integration), install,
and restart Home Assistant. Then add **Virtual Test Devices** from
Settings → Devices & Services → Add Integration.

## Usage

Everything is a normal Home Assistant action, so it all works from
**Developer tools → Actions** with no other software:

| action | what it does |
|---|---|
| `virtual.clone_device` | make a test copy of a real device, abilities included |
| `virtual.describe_device` | return that copy's specification without creating it |
| `virtual.create_device` | create a device from an explicit list of entities |
| `virtual.remove_device` | remove one |
| `virtual.set` | set a sensor's value |
| `virtual.set_available` | mark a device available or unavailable — the field is `value: true/false`, **not** `available:` |
| `virtual.fire_event` | fire a button / scene event |
| `virtual.turn_on` / `turn_off` / `toggle` / `move` | drive a device directly |

Per-platform configuration options are documented in
**[UPSTREAM_README.md](UPSTREAM_README.md)** — upstream's documentation, kept
because it is still accurate for everything it covers.

## Tests

`tests/` guards the three data-loss bugs above and the cloning rules. See
[tests/README.md](tests/README.md) — they run inside a Home Assistant container
and need nothing beyond HA and pytest.

## Credit and licence

A fork of [twrecked/hass-virtual](https://github.com/twrecked/hass-virtual) by
**@twrecked**, whose work this is built on. GPL-3.0, unchanged — see
[LICENSE](LICENSE). Upstream copyright headers are intact.

Please report problems with **this fork** here rather than upstream.
