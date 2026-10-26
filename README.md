<p align="center"><img src="custom_components/hauflo/brand/logo@2x.png" alt="Hauflo" width="240"></p>

# Hauflo agent for Home Assistant

**Hauflo** turns a KNX ETS project into a ready Home Assistant: entities, rooms, dashboard, scenes.
This integration connects the site's Home Assistant to Hauflo:

- **Deploy without an external address or token.** The agent keeps one outbound encrypted connection
  to Hauflo; Hauflo deploys through it. No port forwarding, no Nabu Casa needed.
- **Site monitoring.** Every minute: Home Assistant version, the KNX bus link, unavailable KNX entities.
- **Alarms to Telegram.** Put Hauflo's monitoring label on any entity (boiler or ventilation alarm, leak sensor,
  battery level): Hauflo shows it on its Monitoring page and sends a Telegram message when it goes into alarm
  and when it's back to normal.
- **Push alarms to the residents' phones.** Rules from Hauflo's Notifications step: alarm type → sensors → whom
  (everyone / people / groups) → critical or normal → an action button (e.g. «Shut off water») → repeat until
  acknowledged. Critical ones ring in silent mode (iOS Critical Alerts, Android alarm stream). The rules live in
  this HA — alarms work without Hauflo online.
- **Voice for floor heating and thermostats (Russian).** A KNX floor heating or room thermostat usually has no
  on/off object, and HA answers «turn off the floor heating» with a general error. The agent adds its own
  `custom_sentences/ru/hauflo.yaml`: it switches where the thermostat can, otherwise says it keeps the climate by
  its setpoint; a thermostat with building protection is offered that mode (never switched unasked); setting or
  asking a room's temperature goes to that room's thermostat, not to «several devices».

Nothing listens on your network. To control this Home Assistant the agent uses its own system user
(removed together with the integration). What Hauflo may do through it is an exact list, checked in this HA:
registries, the KNX UI, Hauflo's own dashboards, voice exposure, the HomeKit / Alice / group / KNX setup flows,
its own scenes and status reads — never backup downloads, add-ons, automations or arbitrary services. A lock, the
alarm panel or a valve is never handed to a voice assistant, a group or a scene; KNX connects only to an interface
in the house; Hauflo sends only to the group address ranges you allow when pairing; it changes and deletes only
the KNX entities it created itself, never the ones made by hand. People's locations, camera tokens and media never
leave this house in HA's states.

**A backup before every change.** Before each deploy Hauflo asks the agent for a Home Assistant backup (settings,
without the history database; encrypted when HA has a backup key) and deploys only when it's written — restore it
from Settings → System → Backups.

## Install

1. HACS → ⋮ → **Custom repositories** → add this repository, type **Integration** → install **Hauflo** →
   restart Home Assistant.
2. In Hauflo: your project → **Deploy** → **Through the Hauflo agent** → **Get a code** (valid 30 minutes, once).
3. In Home Assistant: **Settings → Devices & services → Add integration → Hauflo** → enter the code
   (Hauflo address: `https://app.hauflo.ru` unless your integrator says otherwise), then the group address
   ranges Hauflo may write to.
4. In a few seconds Hauflo shows **agent online**. Deploy as usual.

Requirements: Home Assistant 2026.9 or newer; outbound HTTPS from Home Assistant to Hauflo.

## Remove

Settings → Devices & services → Hauflo → Delete. The agent's system user and its voice sentences go with it; in
Hauflo the project shows «agent offline» — unlink it there or pair again later.
