<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/dark_logo@2x.png">
  <img alt="Cozy Home" src="brand/logo@2x.png" height="96">
</picture>

A Home Assistant custom integration that makes your room thermostats (TRVs) follow one
**master** thermostat — the one that controls the boiler — with a per-room **offset**, a
per-room **"turn off below"** cut-off (or **frost protection**), and optional **weekly
schedules** (Hive-style: time blocks per day).

```
master (Hive, 20°)
   ├─ Bedroom   offset −2  → 18°
   ├─ Office    offset +1  → 21°
   └─ Hallway   offset −5  → 15°  (below its 16° cut-off → thermostat OFF, or 7.5° frost protection)
```

Requires Home Assistant **2025.3 or newer** (rooms are config subentries).

## Install

**HACS:** add this repository as a custom repository (type *Integration*), install, restart.
Releases are tagged, so HACS shows the version and offers updates under Settings → Updates.

**Manual:** download `cozy_home.zip` from the latest
[release](https://github.com/ads1230/Cozy-Home/releases), unzip it into
`config/custom_components/cozy_home/` and restart Home Assistant.

Then **Settings → Devices & services → Add integration → Cozy Home**.

## Set up

1. **Choose the master thermostat** — the `climate.*` entity that calls for heat (e.g. your
   Hive thermostat).
2. **Tick the thermostats** that should follow it. The list is every other thermostat Home
   Assistant knows about (TRVs, smart radiator valves, heaters…), all pre-ticked.
3. **Set up each room.** The ticked thermostats are grouped by **area**, and you get one short
   form per room: its name (suggested from the area, or the thermostat's name), which
   thermostats belong to it, and its own offset and cut-off. Several radiators in one room share
   that offset, cut-off and schedule. Untick a thermostat if it should be a room of its own — it
   comes back in the next form.

Add thermostats later with **Add rooms** on the integration page (same steps, only for
thermostats not yet in a room). Rename a room, or change which thermostats it has, via its
⋮ → *Reconfigure*. A room's device is placed in the Home Assistant area its thermostats share.

Upgrading from 0.3 or earlier (one room per thermostat): on first start, rooms whose
thermostats are in the same area are merged into one room — the one named after the area keeps
its settings and schedule, the others are removed (a warning in the log says which).

Each room becomes a device with its own controls:

| Entity | What it does |
|---|---|
| `number.<room>_offset` | Live offset; change it any time |
| `switch.<room>_follow_master` | Off = leave this room's thermostats completely alone |
| `number.<room>_cut_off_temperature` | If the room's target (master + offset) would be below this, the room isn't heated normally — the *Off action* applies |
| `select.<room>_off_action` | What "off" means for this room: **Switch off** (hvac `off`) or **Frost protection** (keep heating at the frost temperature). Used below the cut-off, when the master is off, and for `off` schedule blocks |
| `number.<room>_frost_protection_temperature` | The target used by the *Frost protection* off action (default 7.5°) |
| `select.<room>_when_master_is_off` | **Off action** (default), or **Hold last target** — leave the thermostats as they are until the master comes back |
| `sensor.<room>_target_temperature` | What the integration wants the room at (attributes show mode, reason, master target, the room's thermostats and what each was set to, active schedule block) |
| `sensor.<room>_mode` | `follow`, `fixed`, `off`, `frost`, `hold`, `disabled`, `unavailable` |
| `sensor.<room>_schedule` | The block in effect now; attribute `schedule` holds the whole week (read by the card) |

The offset/cut-off entered when adding a room are the *initial* values; afterwards the
entities are the source of truth and they survive restarts. The cut-off, off action, frost
temperature and master-off setting live together in the room device's *Configuration* section.
(Installs that were created before 0.5.1 keep the old entity IDs such as
`number.<room>_turn_off_below`; the names on the device page update.)

### Integration settings

The ⚙️ **Configure** button on the integration page holds the settings that apply to the
whole integration rather than a room:

| Setting | What it does |
|---|---|
| **Enabled** | Turn off to stop Cozy Home touching any thermostat (e.g. over summer). Rooms keep their settings and schedules. (HA's own ⋮ → *Disable* on the integration does the same but also unloads all the entities.) |
| **Re-apply targets every** | Minutes between re-sending each room's target even if nothing changed — for thermostats that drift back to their own schedule. `0` (default) only pushes when something changes. Nothing is sent to a thermostat that already matches. |

## The rooms card

Edit a dashboard → **Add card** → *Cozy Home Rooms*, or in YAML:

```yaml
type: custom:cozy-home-rooms-card
# optional:
# title: Heating
# rooms: [sensor.upstairs_schedule, sensor.bedroom_schedule]   # only these, in this order
# show_thermostats: true
# show_master: true
```

It shows the master thermostat (current → target, heating or not) and every room: the room's
temperature (average of its radiators) → target, what it is doing and why ("Following master",
"Off · below cut-off", "Frost protection · schedule"…), an offset −/+ control, the *Follow
master* toggle, and the radiators it controls with their own temperature and setpoint. Tap a
radiator for its dialog, tap a room name for its device page.

## How it decides what a room should do

Evaluated whenever the master changes, a room setting changes, a schedule block starts, or a
room thermostat comes back online:

1. Integration disabled, or room's *Follow master* off → **don't touch** the thermostat.
2. Every thermostat in the room unavailable → wait (if only some are, the rest are still driven).
3. Look up the room's **schedule block** currently in effect (if any):
   * `off` → room **off** (see below)
   * `fixed` → target = block temperature (master ignored)
   * `follow` (or no schedule) → target = master target + offset (a block can carry its own offset)
   * master is **off** → room **off**, or **hold** (nothing sent) if *When master is off* = Hold;
     master unavailable → wait
4. If target < *Cut-off temperature* → room **off**.
5. Otherwise, for each thermostat in the room: round to its step, clamp to its min/max, switch
   it to **heat** if it was off, and set the temperature.

**"Off" means one of two things**, per room (*Off action*):

* **Switch off** — `climate.set_hvac_mode: off`. Most TRVs then rely on their own built-in
  frost protection.
* **Frost protection** — the TRV stays in `heat` and is set to the frost-protection temperature
  (7.5° by default), so the valve still opens if the room gets properly cold.

Either way the room comes back on by itself: as soon as its target rises above the cut-off (or
the master turns back on / the schedule block ends) the thermostat is switched back to `heat`
if it was off and given the new target — no manual intervention for the next heating cycle.

Commands are only sent when the desired state changes, and never when the thermostat already
reports that state — so a TRV adjusted by hand is not fought until the next real change. If
you *want* it enforced (some TRVs drift back to an internal schedule), set **Re-apply targets
every** in the integration's Configure dialog, or call `cozy_home.sync` from an automation.

## Schedules

A schedule is a list of **blocks per day**, exactly like the Hive app: each block starts at a
time and lasts until the next block starts — rolling over midnight and across the week. If
Monday has no block at 00:00, Sunday's last block is still in force.

### The schedule card

The integration bundles two dashboard cards and registers them itself (no *Resources* step):
*Cozy Home Rooms* (overview, see below) and *Cozy Home Schedule* (the editor).
Edit a dashboard → **Add card** → search *Cozy Home Schedule*, or in YAML:

```yaml
type: custom:cozy-home-schedule-card
# optional:
# title: Heating schedule
# entity: sensor.bedroom_schedule   # start on this room instead of the first
```

Pick a room (or the master) in the dropdown. Tap a block to edit it — start time, mode
(*Master* / *Fixed* / *Off*), temperature or offset — or **drag its left edge** to move the
start time (15-minute steps). ＋ adds a block after the selected one, － removes it, ⧉ copies
the day to other days, then **Save** (or **Undo All**). The current time is marked, and the
status line shows what the room is doing right now.

If the card doesn't show up right after installing, reload the browser page once — the
frontend only picks up new scripts on load.

### From an automation or Developer tools

Set one with the `cozy_home.set_schedule` action, targeting either the room **device** or its
`sensor.<room>_schedule` **entity**. Days are `mon` … `sun`; `weekdays`, `weekend` and `all`
are shortcuts (an explicit day overrides a shortcut).

```yaml
action: cozy_home.set_schedule
data:
  entity_id: sensor.bedroom_schedule
  schedule:
    weekdays:
      - { time: "06:30", mode: follow }               # master + room offset
      - { time: "08:30", mode: follow, offset: -3 }   # master − 3 while at work
      - { time: "17:00", mode: follow }
      - { time: "22:00", mode: fixed, temperature: 17 }
      - { time: "23:30", mode: off }
    weekend:
      - { time: "07:30", mode: follow }
      - { time: "23:00", mode: off }
```

Block modes:

| mode | fields | meaning |
|---|---|---|
| `follow` | optional `offset` | master target + offset (block offset overrides the room's offset) |
| `fixed` | `temperature` | this temperature, whatever the master says |
| `off` | — | thermostat off |

`cozy_home.get_schedule` returns the schedule and the block currently in effect;
`cozy_home.clear_schedule` removes it. The `sensor.<room>_schedule` entity always carries the
current schedule in its `schedule` attribute (that's what the card reads).

### Scheduling the master itself

Pick the **hub device** (the one named after your integration) instead of a room, and use only
`fixed` / `off` blocks. Home Assistant then drives the master thermostat from that schedule
and the rooms follow. When a master block applies, the master is put into manual **heat**
mode so the thermostat's own schedule (e.g. Hive's) doesn't override it — disable the Hive
schedule if you go this route, otherwise the two will take turns.

## Actions (services)

| Action | Fields | |
|---|---|---|
| `cozy_home.set_schedule` | `device_id` or `entity_id`, `schedule` | Replace a room's (or the master's) schedule |
| `cozy_home.get_schedule` | `device_id` or `entity_id` | Returns `schedule` and `active` block |
| `cozy_home.clear_schedule` | `device_id` or `entity_id` | Remove the schedule |
| `cozy_home.sync` | `device_id` / `entity_id` (optional) | Recompute and push now, even if unchanged |

## Notes

* Room thermostats are switched on using `heat` when available (then `heat_cool`, then
  `auto`) — manual heat is preferred so a TRV's internal schedule can't override the target.
* Only `climate.set_hvac_mode` and `climate.set_temperature` are used, so any climate entity
  with a target temperature works (Zigbee/Z-Wave TRVs, Tado, Hive TRVs, generic thermostats…).
* Settings and schedules are stored in `.storage/cozy_home.<entry_id>` and removed
  when you delete the integration.

## Logo

**Why the integrations page shows a placeholder:** Home Assistant never reads brand images from
an integration's own folder — it fetches them from
[brands.home-assistant.io](https://brands.home-assistant.io), which is populated only from the
[home-assistant/brands](https://github.com/home-assistant/brands) repository. So the logo
appears in HA only once it has been merged there. To do that:

1. Fork `home-assistant/brands`.
2. Add a folder `custom_integrations/cozy_home/` containing the four files from `brand/`:
   `icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` (they already match the size rules:
   256²/512² icons, 128/256-px-high logos, PNG, transparent, trimmed).
3. Open a pull request. Once it's merged, HA picks the images up without any change here
   (clear the browser cache if it still shows the placeholder).

`brand/` also has the SVG sources and `dark_` variants for dark backgrounds.

## Development

```
pip install pytest-homeassistant-custom-component
pytest
```

Releasing: bump `version` in `manifest.json`, commit and push to `main`. The *Release* workflow
creates the tag and the GitHub release (with a zip of the integration) automatically, and HACS
offers the update from there. The *Validate* workflow runs hassfest, the HACS checks and the
test suite on every push.
