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

**Manual:** copy `custom_components/cozy_home/` into your config's
`custom_components/` folder and restart Home Assistant.

Then **Settings → Devices & services → Add integration → Cozy Home**.

## Set up

1. **Choose the master thermostat** — the `climate.*` entity that calls for heat (e.g. your
   Hive thermostat).
2. On the integration's page, click **Add room** for each room:
   * **Room name** — e.g. *Bedroom*
   * **Room thermostat** — the room's `climate.*` entity (TRV)
   * **Offset** — degrees added to the master's target (`-2` with a master of 20° → 18°)
   * **Turn off below** — if the room's computed target would be lower than this, the room
     thermostat is switched **off** instead (default 10°)

Each room becomes a device with its own controls:

| Entity | What it does |
|---|---|
| `number.<room>_offset` | Live offset; change it any time |
| `number.<room>_turn_off_below` | Live cut-off temperature |
| `switch.<room>_follow_master` | Off = leave this room's thermostat completely alone |
| `select.<room>_off_mode` | How "off" is done: **Thermostat off** (hvac `off`) or **Frost protection** (keep heating at the frost temperature) |
| `number.<room>_frost_protection_temperature` | The frost-protection target (default 7.5°) |
| `select.<room>_when_master_is_off` | **Turn off** the room too (default), or **Hold last target** — leave the TRV as it is until the master comes back |
| `sensor.<room>_target_temperature` | What the integration wants the room at (attributes show mode, reason, master target, active schedule block) |
| `sensor.<room>_mode` | `follow`, `fixed`, `off`, `frost`, `hold`, `disabled`, `unavailable` |
| `switch.<hub>_enabled` | Global on/off (e.g. for summer) |

The offset/cut-off entered when adding a room are the *initial* values; afterwards the
entities are the source of truth and they survive restarts. The behaviour options (off mode,
frost temperature, master-off) live in the room device's *Configuration* section.

## How it decides what a room should do

Evaluated whenever the master changes, a room setting changes, a schedule block starts, or a
room thermostat comes back online:

1. Integration disabled, or room's *Follow master* off → **don't touch** the thermostat.
2. Room thermostat unavailable → wait.
3. Look up the room's **schedule block** currently in effect (if any):
   * `off` → room **off** (see below)
   * `fixed` → target = block temperature (master ignored)
   * `follow` (or no schedule) → target = master target + offset (a block can carry its own offset)
   * master is **off** → room **off**, or **hold** (nothing sent) if *When master is off* = Hold;
     master unavailable → wait
4. If target < *Turn off below* → room **off**.
5. Otherwise round to the thermostat's step, clamp to its min/max, switch it to **heat** if it
   was off, and set the temperature.

**"Off" means one of two things**, per room (*Off mode*):

* **Thermostat off** — `climate.set_hvac_mode: off`. Most TRVs then rely on their own built-in
  frost protection.
* **Frost protection** — the TRV stays in `heat` and is set to the frost-protection temperature
  (7.5° by default), so the valve still opens if the room gets properly cold.

Either way the room comes back on by itself: as soon as its target rises above the cut-off (or
the master turns back on / the schedule block ends) the thermostat is switched back to `heat`
if it was off and given the new target — no manual intervention for the next heating cycle.

Commands are only sent when the desired state changes, and never when the thermostat already
reports that state — so a TRV adjusted by hand is not fought until the next real change. If
you *want* it enforced (some TRVs drift back to an internal schedule), call
`cozy_home.sync` from an automation every so often.

## Schedules

A schedule is a list of **blocks per day**, exactly like the Hive app: each block starts at a
time and lasts until the next block starts — rolling over midnight and across the week. If
Monday has no block at 00:00, Sunday's last block is still in force.

Set one with the `cozy_home.set_schedule` action (Developer tools → Actions), picking the
**room device**. Days are `mon` … `sun`; `weekdays`, `weekend` and `all` are shortcuts (an
explicit day overrides a shortcut).

```yaml
action: cozy_home.set_schedule
data:
  device_id: 0123456789abcdef              # the room's device
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
`cozy_home.clear_schedule` removes it.

### Scheduling the master itself

Pick the **hub device** (the one named after your integration) instead of a room, and use only
`fixed` / `off` blocks. Home Assistant then drives the master thermostat from that schedule
and the rooms follow. When a master block applies, the master is put into manual **heat**
mode so the thermostat's own schedule (e.g. Hive's) doesn't override it — disable the Hive
schedule if you go this route, otherwise the two will take turns.

## Actions (services)

| Action | Fields | |
|---|---|---|
| `cozy_home.set_schedule` | `device_id`, `schedule` | Replace a room's (or the master's) schedule |
| `cozy_home.get_schedule` | `device_id` | Returns `schedule` and `active` block |
| `cozy_home.clear_schedule` | `device_id` | Remove the schedule |
| `cozy_home.sync` | `device_id` (optional) | Recompute and push now, even if unchanged |

## Notes

* Room thermostats are switched on using `heat` when available (then `heat_cool`, then
  `auto`) — manual heat is preferred so a TRV's internal schedule can't override the target.
* Only `climate.set_hvac_mode` and `climate.set_temperature` are used, so any climate entity
  with a target temperature works (Zigbee/Z-Wave TRVs, Tado, Hive TRVs, generic thermostats…).
* Settings and schedules are stored in `.storage/cozy_home.<entry_id>` and removed
  when you delete the integration.

## Logo

The `brand/` folder has the mark and wordmark as SVG plus the PNGs Home Assistant's
[brands repository](https://github.com/home-assistant/brands) expects (`icon.png`,
`icon@2x.png`, `logo.png`, `logo@2x.png`, and `dark_` variants). Home Assistant only shows
brand images that are in that repository, so to get the logo onto the integrations page open
a pull request there adding the four main files under `custom_integrations/cozy_home/`.

## Development

```
pip install pytest-homeassistant-custom-component
pytest
```
