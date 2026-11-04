<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/dark_logo@2x.png">
  <img alt="Cozy Home" src="brand/logo@2x.png" height="96">
</picture>

A Home Assistant custom integration that makes your room thermostats (TRVs) follow one
**master** thermostat — the one that controls the boiler — with a per-room temperature
**offset**, a per-room cut-off (or **frost protection**), one Hive-style **heating
schedule** for the master that the rooms follow, and a **boost** for thermostats that have none.

```
master (Hive, 20°)
   ├─ Bedroom   offset −2  → 18°
   ├─ Office    offset +1  → 21°
   └─ Hallway   offset −5  → 15°  (below its 16° cut-off → thermostat OFF, or 7.5° frost protection)
```

Requires Home Assistant **2025.3 or newer** (rooms are config subentries) with the standard frontend.

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
| `select.<room>_off_action` | What "off" means for this room: **Frost protection** (default — keep heating at the frost temperature) or **Switch off** (hvac `off`). Used below the cut-off, when the master is off, and for `off` schedule blocks |
| `number.<room>_frost_protection_temperature` | The target used by the *Frost protection* off action (default 7.5°) |
| `select.<room>_when_master_is_off` | **Off action** (default), or **Hold last target** — leave the thermostats as they are until the master comes back |
| `sensor.<room>_target_temperature` | What the integration wants the room at (attributes show mode, reason, master target, the room's thermostats and what each was set to, active schedule block) |
| `sensor.<room>_mode` | `follow`, `fixed`, `off`, `frost`, `hold`, `disabled`, `unavailable` |
| `sensor.<hub>_schedule` | What the heating schedule is doing, e.g. "Fixed 20° until 22:00"; attribute `schedule` holds the whole week |

All of these are **hidden by default** — the Cozy Home page and the cards are the UI, and
hidden entities stay out of Home Assistant's auto-generated dashboards (the Overview's
*Favourites* row otherwise fills up with them). They still work in automations and on the
device page, and any of them can be unhidden in its settings if you want it on a dashboard.

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

## The thermostat card

Edit a dashboard → **Add card** → *Cozy Home Thermostat*, or `type: custom:cozy-home-master-card`.
The master (boiler) thermostat at a glance: the current temperature large, whether it is
**heating**, idle or off, the target, and what the heating schedule is doing (shown only when
Home Assistant has a schedule), plus a **Boost** button (see [Boost](#boost)). Options:
`title`, `show_schedule: false`, `show_boost: false`, and `show_controls: true` to add −/+
buttons that nudge the target (off by default — the schedule drives the target; a nudge holds
until the next block, like turning the thermostat by hand).

## The rooms card

Edit a dashboard → **Add card** → *Cozy Home Rooms*, or in YAML:

```yaml
type: custom:cozy-home-rooms-card
# optional:
# title: Heating
# rooms: [sensor.upstairs_target_temperature, sensor.bedroom_target_temperature]   # only these, in this order
# show_thermostats: true
# show_master: true    # also show the master row (off by default; the thermostat card does that)
# show_boost: false    # hide the per-room Boost buttons
```

It shows every room: the room's temperature (average of its radiators) → target, what it is doing and why ("Following master",
"Off · below cut-off", "Frost protection · schedule"…), an offset −/+ control, the *Follow
master* toggle, a 🚀 **Boost** button, and the radiators it controls with their own temperature
and setpoint. Tap a radiator for its dialog, tap a room name for its device page.

## Boost

Most TRVs (and some boiler thermostats) have no boost function, so Cozy Home provides one that
works with any thermostat. **Boost** on the thermostat card holds the master at a temperature
for a while (default: 2° above the current target, for an hour — pick any temperature and 30
minutes to 4 hours in the chooser); all the rooms follow it with their offsets as usual. The
🚀 button on a room boosts just that room, even if it is currently off below its cut-off. The
card shows *Boosting to 22° until 18:30* with a *cancel* link while it runs.

When the boost ends (or you cancel it) everything goes back to normal: the master resumes the
heating schedule — or, if the thermostat runs its own schedule, it is put back to the mode and
target it had before, including *off* if you boosted from off — and a room goes back to
following the master. A boost survives a Home Assistant restart. If the thermostat has a boost
of its own (Hive does), use whichever you like: Cozy Home sees the thermostat's boost too and
waits for it before applying the next schedule block.

From an automation: `cozy_home.boost` with the hub's schedule entity or a room's target entity
(or the device), optional `temperature` and `minutes`; `cozy_home.cancel_boost` ends it.

```yaml
action: cozy_home.boost
data:
  entity_id: sensor.cozy_home_schedule   # the hub; or sensor.<room>_target_temperature for one room
  temperature: 22
  minutes: 90
```

## How it decides what a room should do

Evaluated whenever the master changes, a room setting changes, a schedule block starts, or a
room thermostat comes back online:

1. Integration disabled, or room's *Follow master* off → **don't touch** the thermostat.
2. Every thermostat in the room unavailable → wait (if only some are, the rest are still driven).
3. Work out the target:
   * no room schedule (the normal case) → target = master target + offset
   * master is **off** → room **off**, or **hold** (nothing sent) if *When master is off* = Hold;
     master unavailable → wait
   * (advanced room schedules: an `off` block → room **off**; `fixed` → that temperature;
     `follow` → master + offset)
4. If target < *Cut-off temperature* → room **off**.
5. Otherwise, for each thermostat in the room: round to its step, clamp to its min/max, switch
   it to **heat** if it was off, and set the temperature.

**"Off" means one of two things**, per room (*Off action*):

* **Frost protection** (default) — the TRV stays in `heat` and is set to the frost-protection
  temperature (7.5° by default), so the valve still opens if the room gets properly cold.
* **Switch off** — `climate.set_hvac_mode: off`. Most TRVs then rely on their own built-in
  frost protection.

Either way the room comes back on by itself: as soon as its target rises above the cut-off (or
the master turns back on / the schedule block ends) the thermostat is switched back to `heat`
if it was off and given the new target — no manual intervention for the next heating cycle.

Commands are only sent when the desired state changes, and never when the thermostat already
reports that state — so a TRV adjusted by hand is not fought until the next real change. If
you *want* it enforced (some TRVs drift back to an internal schedule), set **Re-apply targets
every** in the integration's Configure dialog, or call `cozy_home.sync` from an automation.

## The Cozy Home page

After installing, **Cozy Home** appears in the left sidebar. That page has the thermostat, the
heating schedule editor and the rooms overview — the three cards described below — with
nothing to configure. Use the cards on your own dashboards when you want them laid out your way.

## The heating schedule

The boiler fires when the master thermostat calls for heat, so there is **one schedule** and it
belongs to the master: a weekly timeline of temperatures and ❄ off blocks, like the Hive app.
Rooms follow it with their **offsets**, which are plain temperature deltas — a bedroom at −2°
is at 18° when the schedule says 20°. Each block lasts until the next one starts, rolling over
midnight and across the week.

### The schedule editor

Open **Cozy Home** in the sidebar, or add the card to a dashboard (*Add card → Cozy Home
Schedule*, or `type: custom:cozy-home-schedule-card`). It starts empty: the heating is running
on the thermostat's own schedule (the one in its app). **Create schedule** takes over — from
then on Home Assistant sets the master thermostat's temperature at each change (it switches
the thermostat to manual mode so its app schedule stops interfering; turn that schedule off in
the app too). Tap a block to set its time and temperature (or *Off*), drag a block's left edge
to move it, ＋/－ add and remove blocks, ⧉ copies a day to other days, then **Save**.
*Remove schedule* hands control back to the thermostat's own schedule.

Turning the thermostat up by hand holds until the next block, exactly like a normal
thermostat. A **boost** is left to finish: if a block starts during a boost — the thermostat's
own, or a Cozy Home [boost](#boost) — Home Assistant waits and applies that block the moment
the boost ends (the schedule card says "boost running — the schedule resumes when it ends").
Rooms follow the boosted target meanwhile. If you want the schedule re-imposed regardless of manual changes, set
*Re-apply targets every* in Configure (it also respects a running boost).

### From an automation or Developer tools

`cozy_home.set_schedule` on the **hub device** (or its `sensor.<hub>_schedule` entity). Days are
`mon` … `sun`; `weekdays`, `weekend` and `all` are shortcuts (an explicit day overrides a
shortcut). Blocks are `fixed` with a `temperature`, or `off`.

```yaml
action: cozy_home.set_schedule
data:
  entity_id: sensor.cozy_home_schedule
  schedule:
    weekdays:
      - { time: "06:30", mode: fixed, temperature: 20 }
      - { time: "08:30", mode: fixed, temperature: 16 }
      - { time: "17:00", mode: fixed, temperature: 20 }
      - { time: "22:00", mode: off }
    weekend:
      - { time: "07:30", mode: fixed, temperature: 20 }
      - { time: "22:30", mode: off }
```

`cozy_home.get_schedule` returns the schedule and the block currently in effect;
`cozy_home.clear_schedule` removes it. `sensor.<hub>_schedule` reads out what the schedule is
doing ("Fixed 20° until 22:00", or "Not scheduled here" when the thermostat runs its own).

### Advanced: a schedule for one room

Normally rooms don't need one — the offset does the job. If a room really needs its own
timeline (a nursery held at a fixed temperature overnight, say), enable that room's disabled
`sensor.<room>_schedule` entity and edit it with the card's `entity:` option
(`type: custom:cozy-home-schedule-card`, `entity: sensor.nursery_schedule`), or with
`cozy_home.set_schedule` on the room device. Room blocks can be `follow` (the heating schedule
+ offset, optionally with a block-specific `offset`), `fixed`, or `off`; such a schedule takes
precedence over following the master for that room.

## Actions (services)

| Action | Fields | |
|---|---|---|
| `cozy_home.set_schedule` | `device_id` or `entity_id`, `schedule` | Replace a room's (or the master's) schedule |
| `cozy_home.get_schedule` | `device_id` or `entity_id` | Returns `schedule` and `active` block |
| `cozy_home.clear_schedule` | `device_id` or `entity_id` | Remove the schedule |
| `cozy_home.sync` | `device_id` / `entity_id` (optional) | Recompute and push now, even if unchanged |
| `cozy_home.boost` | `device_id` or `entity_id`, `temperature` (optional), `minutes` (default 60) | Hold the master (hub) or a room at a temperature for a while |
| `cozy_home.cancel_boost` | `device_id` or `entity_id` | End a running boost now |

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
pip install pytest-homeassistant-custom-component home-assistant-frontend
pytest
```

Releasing: bump `version` in `manifest.json` **and** the `VERSION` constant at the top of the two
card files in `www/` (a test checks they match), commit and push to `main`. The *Release* workflow
creates the tag and the GitHub release (with a zip of the integration) automatically, and HACS
offers the update from there. The *Validate* workflow runs hassfest, the HACS checks and the
test suite on every push.
