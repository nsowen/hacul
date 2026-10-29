# CUL for Home Assistant

A Home Assistant integration for busware CUL sticks (and nanoCUL, CUNO, CUN)
running [culfw](http://culfw.de) or
[a-culfw](https://github.com/heliflieger/a-culfw). culfw speaks several 868 MHz
radio protocols; the radio side lives in the separate library
[pycul](https://github.com/nsowen/pycul).

Supported protocols:

| Protocol | Devices |
|---|---|
| eQ-3 MAX! | Radiator thermostats, wall thermostats, window contacts, eco switches |

A stick runs one radio protocol at a time, so each stick (or group of sticks)
is set up for one protocol.

With MAX!, no Cube and no cloud are needed: the stick acts as the controller
of your thermostats, the way FHEM's `CUL_MAX` does. The rest of this document
describes the MAX! support.

Status: alpha.

## Why

Our office heats with ten MAX! radiator thermostats and their wall
thermostats, run by FHEM with a CUL on a Raspberry Pi. When the Pi crashed,
it was time to move to Home Assistant, but not to replace working hardware.
This integration, and the library [pycul](https://github.com/nsowen/pycul) behind it, make that
possible: plug in the CUL, enter FHEM's controller address, and the existing
thermostats keep their pairing.

## Features

- Radiator thermostats, wall thermostats, window contacts and eco switches
- Climate entity per thermostat: automatic (week profile), manual, off, boost
- Week profiles edited with Home Assistant's schedule helper and stored on the
  thermostat, so they keep working when Home Assistant is offline
- Room temperature from any Home Assistant sensor (sent as a virtual wall thermostat)
- Window state from any Home Assistant binary sensor (sent as a virtual window contact)
- Heating zones (MAX! groups) with one climate entity each, set up from Home
  Assistant areas and from the groups and links the devices actually use,
  learned from radio traffic; large rooms can have several zones
- One radio message for a set point of a whole zone
- Temporary temperatures
- Several sticks for one MAX! network, for larger houses
- Send queue that respects the 1% duty cycle of the 868 MHz band
- Pairing from the UI, adoption of devices already paired with FHEM
- Reconnects on its own after the stick was unplugged or stopped answering
- English and German translations

## Installation

### HACS

1. HACS, three-dot menu, *Custom repositories*: add this repository as type *Integration*.
2. Install *CUL* and restart Home Assistant.

### Manual

Copy `custom_components/cul` into the `custom_components` folder of your
configuration directory and restart Home Assistant.

## Setup

*Settings > Devices & services > Add integration > CUL*. A busware CUL
plugged into the Home Assistant host is also discovered automatically.

| Field | Meaning |
|---|---|
| Device | Serial port of the stick, preferably the `/dev/serial/by-id/...` path. A stick on the network is entered as `socket://host:port` (ser2net, CUNO). |
| Baud rate | Ignored by the busware CUL; a nanoCUL needs 38400. |
| MAX! controller address | Six hex digits identifying the controller. See below. |

### The controller address

MAX! devices only accept commands from the controller they are paired with.

- **Coming from FHEM:** enter the address of your `CUL_MAX` device (FHEM's
  default is `123456`) and stop FHEM's `CUL_MAX` (or unplug its stick).
  Thermostats keep their pairing and appear as soon as they send something,
  usually within a few minutes, faster after turning the wheel.
- **Coming from a MAX! Cube:** factory-reset the device, press *Start pairing*
  on the CUL device in Home Assistant, then hold the device's boost button
  until it shows its pairing countdown.

## Entities

Per thermostat:

| Entity | Notes |
|---|---|
| Climate | *Auto* follows the week profile; setting a temperature in *Auto* holds it until the next switch point. *Heat* is a manual set point, *Off* sets 4.5 °C. Preset *Boost* opens the valve for the configured boost time. In a heating zone, a change reaches all its thermostats. |
| Temperature | Measured temperature. Radiator thermostats only report it now and then, typically when the valve moves. |
| Valve position | Radiator thermostats only. |
| Mode, Temporary until | The device's own mode (automatic, manual, temporary, boost) and the end of a temporary set point. |
| Scheduled temperature, Next schedule change | Derived from the week profile, once it is known (see below). |
| Show measured temperature | Wall thermostats: display the room temperature instead of the set point. |
| Battery, Radio link, Buttons locked, Heating zone | Diagnostic. *Radio link* reports a problem when the device lost contact to its link partners. |
| Signal strength, Last seen | Diagnostic; signal strength (also per stick, with several sticks) is disabled by default. |
| Automatic daylight saving time, Paired with controller | Diagnostic status flags, disabled by default. |

Window contacts get a window sensor, battery and radio link sensors. Eco
switches get an event entity that fires `auto` or `eco` per key press, for
automations.

All state comes from the devices' own messages, including the state each
device returns in its acknowledgements: every command, also a time update,
refreshes the device's values.

On the CUL device: *Start pairing* (accepts new devices for 60 seconds), one
connectivity sensor and one *Available airtime* sensor per stick, and the
number of queued messages.

## Week profiles

A thermostat runs its week profile on its own. Home Assistant's
[schedule helper](https://www.home-assistant.io/integrations/schedule/) is used
to edit it:

1. Create a schedule helper. Each block is a heating period. To give a block
   its own temperature, add `temperature: 22` under *Advanced settings >
   Additional data* of that block.
2. In the integration's options choose *Week profile from a schedule*, select
   the thermostat and the schedule, and set the base temperature (between
   blocks) and the default temperature (for blocks without own temperature).

Changes to the schedule are written automatically, and only for days that
changed: a full profile needs 7 to 14 radio messages, more than one stick may
send at once, so the rest follows within a few minutes. One schedule can drive
several thermostats.

A thermostat stores at most 13 switch points per day, in 5 minute steps. If a
schedule does not fit, a repair issue explains which day and why, and nothing
is sent.

Week profiles cannot be read from a thermostat. Until the integration has
written one (or paired the device, which sets the factory profile), the
profile is unknown and the schedule based sensors stay empty. This is the case
right after migrating from FHEM.

Without a schedule helper, use the actions below.

## Heating zones

A heating zone is a MAX! group: devices with the same group id, linked with
each other. A wall thermostat controls the radiator thermostats of its zone, a
window contact lowers them, and a set point reaches the whole zone with a
single radio message. Changing the temperature of one thermostat of a zone in
Home Assistant changes all of them; the others show the new set point right
away and confirm it with their next status message.

A zone is not necessarily a room. A large room or a loft with two wall
thermostats is two zones, each wall thermostat with the radiator thermostats
it controls. A radiator thermostat without wall thermostat can stay without
group (0): it is a zone of its own.

Each zone gets a climate entity, *Heating zone 3* for group 3. It shows the
set point and temperature of the zone's wall thermostat (or, without one, of
the thermostat received best) and heats while any valve of the zone is open.
Its commands reach all thermostats of the zone with one message;
`cul.set_week_profile` on it writes the profile to each of them.

### What is already there

Neither groups nor links can be read from a device. The integration learns
them by listening to the devices talking to each other: a wall thermostat
sending the room temperature to its radiator thermostats, a radiator
thermostat reporting to its wall thermostat, a window contact reporting a
change. These frames name the receiver (a link) and, if the sender is in a
group, its group id. Messages to the controller never carry the group.

Thermostats that have only ever been managed by FHEM usually have no group
(FHEM sets none unless asked to); links set up with a Cube or with FHEM are
learned as soon as the devices use them. The *Heating zone* sensor and the
diagnostics show what is known. To speed things up, turn the wheel of a wall
thermostat and open and close the windows once.

### Setting up a zone

*Options > Heating zones (MAX! groups)* sets a zone up the way a MAX! Cube
sets up a room:

1. **Suggested zone:** pick one of the suggestions, or leave the field empty
   to choose the devices yourself. Suggestions come from
   - Home Assistant areas with MAX! devices. An area with several wall
     thermostats is split: one zone per wall thermostat with the radiator
     thermostats and window contacts linked with it, plus the devices whose
     links are not known yet;
   - group ids the devices already use;
   - devices seen talking to each other.
2. **Heating zone:** check the devices and the group id. The default is the
   group the devices already share, otherwise the first free id; 0 removes
   the devices from their group. *Link the devices with each other* adds the
   links a zone needs; links that are already known are not sent again.
   *Window contacts shared with other zones* are linked with this zone's
   thermostats but keep their group, e.g. the door between two zones of a
   loft; best leave such contacts without group.

A zone with two wall thermostats is possible, but they overrule each other:
each sends its set point to every thermostat it is linked with. The dialog
asks once more before setting that up.

A zone costs one message per device plus one per link: four devices need
about 16 messages, half of the 36 seconds per hour a stick may send, so
setting them up takes up to a quarter of an hour and runs in the background;
the *Send queue* sensor shows the progress. Window contacts only receive right
after they sent something: open and close the window once. Devices that did
not confirm show up under *Repairs*; running the setup again only sends what
is still missing.

## Room temperature and window sensors

In the options, *Room temperature and window sensors* assigns Home Assistant
sensors to a thermostat:

- **Room temperature sensor:** the thermostat regulates on this value instead
  of the temperature measured next to the radiator. The CUL sends it as a
  virtual wall thermostat (address `111111`), at most once per configured
  interval (default 10 minutes) and only when it changed.
- **Window sensor:** opening the window makes the thermostat switch to its
  window-open temperature. Sent as a virtual window contact (`222222`) right
  when the sensor changes.

The integration links the thermostat with the virtual devices automatically
and unlinks them when the sensors are removed again.

## Actions

| Action | Purpose |
|---|---|
| `cul.set_week_profile` | Write a week profile given as periods per weekday. |
| `cul.get_week_profile` | Return the known week profile, the scheduled temperature and the next change. |
| `cul.set_temporary_temperature` | Hold a temperature until a point in time, then return to the week profile. |
| `cul.set_group` | Put a single thermostat into a MAX! group, or remove it with 0. Use the options for complete zones. |

```yaml
action: cul.set_week_profile
target:
  entity_id: climate.max_radiator_thermostat_0a1b2c
data:
  base_temperature: 17
  profile:
    monday:
      - { from: "06:00", to: "08:30", temperature: 21 }
      - { from: "17:00", to: "22:30", temperature: 21 }
    saturday:
      - { from: "08:00", to: "23:00", temperature: 21.5 }
```

```yaml
action: cul.set_temporary_temperature
target:
  entity_id: climate.max_radiator_thermostat_0a1b2c
data:
  temperature: 23
  until: "2026-11-01 18:00:00"
```

## Compared to FHEM

Coming from FHEM's `CUL_MAX`, these are the differences you will notice. The
pycul README lists the technical details.

- **Window contacts can be configured after pairing.** FHEM only sends queued
  messages to a window contact after the contact sends an acknowledgement,
  which in practice happens during pairing only; the trigger on its regular
  open/close messages is commented out in FHEM. Linking a window contact
  later therefore hardly ever reached it. Here, messages go out the moment
  the contact reports a change.
- **No silently lost commands.** When FHEM had no stick available, it dropped
  its entire send queue. Commands here wait until a stick is back.
- **Existing groups and links are recognised.** FHEM never reads the group id
  from the devices and only knows links it created itself.
- **A zone set point is one message**, not one per thermostat; repeated changes
  that have not been sent yet are merged into one.
- **The end of a temporary set point stays visible.** In FHEM every
  acknowledgement clears it.
- **Valve percentages are written exactly.** FHEM truncates them, so 50 %
  maximum valve opening becomes 127 instead of 128 of 255.

## Several sticks

A second stick extends the range of the same MAX! network: options, *Add a
stick*. Each message is sent through the stick that receives the target device
best; if a stick fails, the others take over.

## Airtime

On 868 MHz a stick may send for 36 seconds per hour. A message to a sleeping
thermostat takes about 1.1 seconds, so roughly 8 messages can be sent at once,
then about one every two minutes. The integration queues messages instead of
dropping them, merges repeated set point changes into one, and shows the
remaining airtime per stick. Keep this in mind for automations that change
many thermostats at the same time.

## Troubleshooting

- Unplugging the stick makes the entities unavailable; when it is plugged in
  again, the integration reconnects within seconds (Home Assistant reports the
  USB device) and resends what was queued. Configure the stick with its
  `/dev/serial/by-id/...` path, the default in the setup dialog: a path like
  `/dev/ttyACM0` may change after re-plugging.
- To see how an existing installation is grouped and linked before setting up
  zones, run pycul's `topology` command with Home Assistant stopped (one
  program per stick).
- Diagnostics (device page, *Download diagnostics*) contain the sticks'
  firmware, airtime, the send queue and all devices with their state, learned
  group and link partners.
- Debug logging:

  ```yaml
  logger:
    logs:
      custom_components.cul: debug
      pycul: debug
  ```

- To test a stick outside Home Assistant, pycul ships a command line tool
  (`pycul -d /dev/ttyACM0 monitor`).

## Development

The tests use pycul from a checkout next to this one
(`requirements_test.txt` installs `../pycul` in editable mode):

```bash
git clone https://github.com/nsowen/pycul ../pycul
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## License and credits

GNU General Public License v2.0 or later, see [LICENSE](LICENSE). The MAX!
protocol knowledge comes from FHEM (`10_MAX.pm`, `14_CUL_MAX.pm` by Matthias
Gehre and Wzut) and culfw; see pycul's NOTICE file.

Author: Nils Sowen <n.sowen@2scale.net>
