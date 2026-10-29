<img src="https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/hero.webp" width="840" alt="The Hauflo dashboard of a demo home on a wall tablet (light) and a phone (dark).">

# Hauflo agent

**English** · [Русский](https://github.com/Hauflo/hauflo-agent/blob/main/README.ru.md)

**ETS project in, finished Home Assistant out: rooms, devices, family dashboard and alarms. KNX first.**

Free during the beta; needs a Hauflo account ([request access](https://app.hauflo.com/request-access)).

**Try it without installing:** the [demo house](https://hauflo.com/demo/), the real cards on a made-up home, in your browser.

[![HACS custom](https://img.shields.io/badge/HACS-custom-41BDF5)](https://github.com/Hauflo/hauflo-agent#install) [![Home Assistant 2026.9+](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-18BCF2)](https://github.com/Hauflo/hauflo-agent#install) [![Release](https://img.shields.io/github/v/release/Hauflo/hauflo-agent)](https://github.com/Hauflo/hauflo-agent/releases)

**Install:** HACS → ⋮ → **Custom repositories** → `https://github.com/Hauflo/hauflo-agent` (Integration). [Full steps](#install).

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Hauflo&repository=hauflo-agent&category=integration)

## Project in. Home out.

The agent connects a home's Home Assistant to [Hauflo](https://hauflo.com), where the KNX integrator plans the home from the ETS project.

<img src="https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/from-ets.webp" width="840" alt="On the left, the living room's lights as group addresses in a demo ETS project. On the right, the same lights as the family's room card in Home Assistant.">

1. **Upload** a `.knxproj` or the ETS group address CSV.
2. **Review the plan.** Rooms, devices and scenes. Where an address can be read two ways, you decide.
3. **Deploy.** The agent makes a backup, then creates everything in Home Assistant.
4. **Hand over.** Phone and wall tablet, light and dark.

No hand-built cards. Change the ETS project, re-import: the dashboard follows.

## For the family

<img src="https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/family.webp" width="840" alt="Three phone screens of the demo home: a room with lights, scenes and blinds; air conditioning with a 24-hour chart in the dark theme; the family's own automations.">

- **The house at a glance.** “All quiet”, or what needs attention.
- **Rooms that glow** when something is on. One tap opens a room.
- **Climate** with a 24-hour chart. Heat and cool setpoints where the thermostat has both. Floor heating says “heating” or “holding”.
- **Blinds** for the whole room or one by one.
- **Their own look.** Scenes in their blocks or on top, climate large or compact. One tap back to the integrator’s choice.
- **Settings.** Light or dark theme for each person. Winter or summer for all air conditioners at once.
- **Alarms** that ring even on silent, with an action button and **Accept**.
- **Phone and wall tablet**, with kiosk mode and a night screen. English and Russian.

## Automations the family sets

In the dashboard: **Settings → Automations**. The family sets blinds and lights to sunrise, sunset, a time or the phone’s alarm clock. A rule can run up to two hours earlier or later, on the days they choose. There’s a wake-up light that brightens slowly, ready suggestions for this home, and a timeline of what happens next.

Rules only use Hauflo’s own lights, blinds and scenes. They never touch locks, the alarm, valves, garage doors or gates. Each rule is a plain Home Assistant automation, so it runs without Hauflo and without the internet.

## For you, the integrator

- **A plan before every deploy.** Nothing is applied until you confirm. Removing anything needs its own checkbox.
- **A backup before every change.** No backup, no deploy.
- **Re-import without starting over.** Rooms, names and scenes stay. A second deploy with no changes changes nothing.
- **No ports, no tokens.** One outgoing encrypted connection. No port forwarding.
- **Every site on one screen.** Link to the home, KNX bus, devices gone quiet, an event log.
- **Alarms the way you set them.** Push, Telegram and email, repeated until someone accepts.

## Install

**You need** Home Assistant 2026.9 or newer, [HACS](https://hacs.xyz), outbound HTTPS, and a KNX IP interface or router that Home Assistant’s KNX integration supports.

1. **Add the repository to HACS.** Use the button, or in HACS open ⋮ → **Custom repositories**, paste `https://github.com/Hauflo/hauflo-agent` and choose **Integration**.

   [![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Hauflo&repository=hauflo-agent&category=integration)

2. **Download Hauflo** in HACS and **restart Home Assistant**.
3. **Get a pairing code** in Hauflo: your project → **Connect the home** → **Get a code**. It works once, for 30 minutes.
4. **Add the integration** and enter the code. The code picks the server: `EU-…` → app.hauflo.com, `RU-…` → app.hauflo.ru. **Advanced → Hauflo server** is only for a server of your own; leave it as is. If this Home Assistant is still linked to a house of another Hauflo account, a checkbox **Yes, move this Home Assistant to this project** appears. Without it nothing changes.

   [![Open your Home Assistant instance and start setting up Hauflo.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hauflo)

5. **Confirm the KNX addresses.** Remove any Hauflo must not control, such as a gate or a lock.
6. Hauflo shows **agent online** in a few seconds. Open **Deploy**, check the plan, deploy.

## Options

**Settings → Devices & services → Hauflo → Configure.** Saved at once, no restart.

- **Allow Hauflo to change this home's setup** (on by default). Off: Hauflo changes nothing in Home Assistant. Deploys, dashboards, rooms, KNX entities and backups started by Hauflo are refused. Monitoring, alarms and voice keep working.
- **Allowed addresses.** The KNX group addresses Hauflo may send commands to: `1/1/5`, a span `1/1/1-21` or a whole middle group `1/1`, comma-separated or one per line. Remove any Hauflo must not control, such as a gate or a lock.
- **Allow these requested addresses.** Shown only when Hauflo asked for addresses outside the list (Repairs says so too). Unticked ones are declined when you save.
- **Make the house independent of Hauflo.** A tick that opens its own step: what it does, what stops, then your confirm. See [Leaving Hauflo](#leaving-hauflo).

Re-pair with a new code (another project): **⋮ → Reconfigure**. Unlinked in Hauflo: Home Assistant asks for a new code itself (**Re-authenticate**).

## Entities the integration creates

All of them belong to one service device, **Hauflo**.

| Entity | What it is | When |
| --- | --- | --- |
| `binary_sensor.hauflo_link` (**Link**, diagnostic) | On while the agent is connected to Hauflo. Attribute `server`: which Hauflo server. | Always |
| `climate.hauflo_comfort_<room>` (**Comfort · room**) | The room temperature the family wants. The agent keeps that room's floor heating between the integrator's limits. | Only rooms where the integrator turned the comfort floor on |
| `select.hauflo_house_mode` (**House mode**: Winter / Summer) | Winter: only the floor heats. Summer: the air conditioners cool, the floor rests. | With the first comfort floor room |
| `sensor.hauflo_made_in_this_home` (**Made in this home**, diagnostic) | How many entities Hauflo made or took over here. Attributes: `by_kind`, `hand_edited` (renamed or disabled in Home Assistant, or kept as a hand edit by the last plan), `next_deploy` (what the next deploy would create, update, offer to remove, and when Hauflo said it). Read only. | Always |

**The label «Hauflo».** Every entity Hauflo made or took over carries the label **Hauflo** (**Settings → Areas, labels & zones → Labels**, or the label filter on the entities page). The agent puts it on and takes it off itself, only on Hauflo’s own; a label set by hand on anything else is taken off. Entity ids never change. While **Allow Hauflo to change this home's setup** is off, the label isn’t touched. **Hauflo monitoring** is a different label: you put it on devices Hauflo didn’t make, to watch them.

Everything else a deploy makes (rooms, KNX entities, light groups, scenes, dashboards) belongs to Home Assistant's own integrations, not to this one.

## How data updates

No polling. The agent keeps one outgoing WebSocket to Hauflo, reconnecting by itself with backoff. **Link** changes the moment the connection does. **Comfort** and **House mode** follow the floor thermostats' state changes; the floor is written slowly, once per interval (10 minutes by default) and a minute after a new target. Every minute the agent sends Hauflo a short status (see [What leaves the house](#what-leaves-the-house)).

## What leaves the house

Hauflo can ask only for what is on the agent's fixed list, and the agent edits some answers before they leave. Exactly:

**During a deploy, a devices check and monitoring:**

- **The states of entities of any integration, not only KNX:** entity id, state, name and a fixed set of attributes (device class, unit, temperatures and setpoints, modes, brightness and colour temperature, positions, options, min / max / step). Every other attribute stays home.
- **Left out entirely:** the phones' entities (every `mobile_app` entity) — not even their entity ids, which carry the phone's name.
- **Scrubbed:** people, device trackers, zones, cameras, images, media players, calendars, to-do lists, weather, the sun, locks, alarm panels, notify / TTS / STT / conversation, and motion, occupancy, presence, door, window and garage door sensors (unless Hauflo made them or you gave them the **Hauflo monitoring** label). Of these only the name, device class, icon and supported features leave. The state reads `hidden`, the times are removed.
- **The entity registry list** (entity ids, names, integrations, unique ids, rooms, labels; without the phones' entities) and **the device registry list** (names, models, makers, rooms; for phones the name, model and maker are removed).
- **For the alarm settings** (who gets alarms): the people of this Home Assistant and the phones with the Home Assistant app — their names, OS and owner — so you can pick recipients in Hauflo.
- Rooms, floors and labels. The titles of all dashboards and the content of Hauflo's own dashboards (never the family's other dashboards). Which entities are exposed to voice assistants.
- Home Assistant's version, time zone, language, unit system, currency, the list of loaded integrations and the external URL without a login. Whether Home Assistant Cloud is on, with Alexa and Google, without the account. Not the home's coordinates or name.
- KNX: the ETS project loaded in Home Assistant's KNX integration, the settings of KNX entities, and which group addresses sent telegrams (addresses and times, never values).
- The HomeKit bridge Hauflo set up: its pairing code and QR, shown to you in Hauflo to add the bridge to Apple Home (not stored). Alice: its label settings.

**Every minute:** Home Assistant and agent versions, the KNX connection, how many KNX entities are unavailable (up to 10 ids), the name and state of entities labelled **Hauflo monitoring**, open alarms, and whether the owner's switch is off. For the service passport in the handover pack: how KNX is connected (tunnel or routing, the gateway's address and port, the individual address; whether KNX Secure is on — never its keys or passwords), Home Assistant's address and the names of its administrators (no passwords).

**Guest access:** while a guest uses a link, the states of the lights, blinds and climates of the rooms in that link pass through Hauflo to the guest's browser, and the guest's IP address reaches the Hauflo server like any visitor's. Who the guest is is not recorded; the guest log stays in Home Assistant.

**Never:** where people are, camera images or tokens, media, calendars, PIN codes (other than the HomeKit pairing code above), passwords, KNX Secure keys, backups, the history database.

**Where data is stored.** EU accounts at app.hauflo.com, in Germany. Russian accounts at app.hauflo.ru, in Russia. One exception in transit: the Russian region has no direct access to Telegram, so the text of a Telegram alert and the recipient's chat ID pass through a Hauflo relay server in Germany on the way to Telegram (not stored, not logged). The EU region sends them to Telegram directly.

## Security in plain words

- **One outgoing encrypted connection.** Nothing listens on the home network. Hauflo keeps no Home Assistant token: the agent is its own system user, removed together with the integration.
- **A fixed list of what Hauflo may do,** checked inside this Home Assistant. KNX commands go only to the addresses you confirmed.
- **The owner’s switch.** In the integration’s options: **Allow Hauflo to change this home’s setup**. Off, and Hauflo changes nothing. Monitoring, alarms and voice keep working.
- **A backup before changes.** Deploying without one is only an explicit choice, and then a notice stays in Home Assistant. Restore it from **Settings → System → Backups**, with or without Hauflo.
- **Open to read.** The source code is here, so you can check what runs in the home.

<details>
<summary><b>The full list: may and may not</b></summary>

**Hauflo may, through the agent:**

- create rooms, floors and labels, and the KNX devices of the project;
- create and update its own dashboards and scenes, set its dashboard and theme as the default;
- write its own rules (`hauflo_auto_*`) into `automations.yaml`, never touching other automations and never rewriting a hand-formatted file;
- set the home's location and time zone in Home Assistant: only an administrator, from the family's **Automations** page;
- hide Home Assistant's extra sidebar panels (Map, Energy, Logbook…) for the family (everyone but Home Assistant administrators — they keep the full menu unless **Administrators too** is on), with the **Clean up the menu** option;
- copy its card files to `/config/www/hauflo`;
- set up the KNX connection to an interface in the house, light groups, voice exposure and the Apple Home bridge;
- read states for monitoring and alarms;
- send KNX commands only to the group address ranges you confirmed.

**It may not:**

- download backups, or touch add-ons, other automations or arbitrary services;
- change or delete devices and entities it didn’t create (your hand-made ones stay yours);
- read KNX Secure keys or passwords;
- give a lock, an alarm panel or a valve to a voice assistant, a group or a scene.

**The backup** holds the settings without the history database. It is encrypted if your Home Assistant has a backup key. Without a fresh one, the agent changes nothing, unless the integrator explicitly chooses to deploy without it; then a notice stays in Home Assistant.

**What leaves the house** is listed exactly in [its own section](#what-leaves-the-house).

</details>

<details>
<summary><b>Alarms</b></summary>

Set up in Hauflo → **Alarms**. A rule is: what happened → which sensors → who gets it → how.

- **Push to the residents’ phones** through the Home Assistant app. Critical alarms ring even in silent mode (iOS Critical Alerts, the Android alarm stream). They repeat until someone taps **Accept**. An action button does the obvious thing, such as “Shut off water”.
- **Telegram** through [@hauflo_bot](https://t.me/hauflo_bot), and **email**, for you, the engineer on duty or the client. Each person gets only the alarms of their own sites.
- The push rules live in this Home Assistant, so they keep working when Hauflo or the internet is down.

To watch any other entity (a boiler fault, ventilation, a battery), give it the label **Hauflo monitoring** in Home Assistant.

</details>

<details>
<summary><b>Voice</b></summary>

- **Assist sentences for floor heating and thermostats.** A KNX floor heating often has no on/off object, so Home Assistant answers “turn off the floor heating” with an error. The agent adds its own sentences: it switches off where the thermostat can, and otherwise explains that it holds the setpoint. Setting or asking a room’s temperature goes to that room’s thermostat.
- **Apple Home and Yandex Alice.** A deploy can set up an Apple Home bridge (with a pairing QR code on the handover page) and expose rooms to Alice. Locks, alarm panels and valves are never exposed.

</details>

<details>
<summary><b>Does the agent send commands to the KNX bus by itself?</b></summary>

No. A command goes to the bus when a person acts: a tap on the dashboard, a scene, voice, an alarm’s action button, the Winter/Summer switch, or a rule the family set in **Automations**.

Two exceptions. The first, only if the integrator turns it on for a room: the **comfort floor**. The client sets the room temperature, and the agent keeps that room’s floor heating between a comfortable minimum and an upper limit. It writes slowly (once per interval, 10 minutes by default) and only inside the limits.

The second, when the family switches it on: **Away — presence simulation**. Lights only in the evening dark, blinds at the usual times; only Hauflo’s own lights and blinds. Switched off, it turns off the lights it turned on.

</details>

<details>
<summary><b>What happens if Hauflo or the internet is down?</b></summary>

The home keeps working. It is plain Home Assistant with standard KNX devices. The dashboard, automations, push alarms and the comfort floor run inside Home Assistant. Hauflo shows the site as offline, and the agent reconnects by itself.

</details>

<details>
<summary><b>Will Hauflo overwrite what I made by hand?</b></summary>

No. Hauflo keeps a list of what it created and changes only that. Your own entities, automations and dashboards stay as they are. If you edited something Hauflo made, it notices and keeps your edit.

</details>

<details>
<summary><b>How do I allow more KNX addresses later?</b></summary>

**Settings → Devices & services → Hauflo → Configure.** If Hauflo needs an address that isn’t allowed yet, it waits there for you to confirm.

</details>

<details>
<summary><b>“The code is wrong or expired”</b></summary>

A code works once, for 30 minutes. Get a new one in Hauflo: your project → **Connect the home** → **Get a code**. If Home Assistant can’t reach Hauflo at all, check its internet access.

</details>

<details>
<summary><b>“I press in HA — the actuator is silent”</b></summary>

Open the group monitor (**Settings → Devices & services → KNX → Monitor**) and press again. No telegram: the address is outside the agent’s allowed ranges (**Settings → Devices & services → Hauflo → Configure**) or the tunnel is busy (ETS, a visualisation). Telegram goes out but the actuator ignores it: the actuator’s object in ETS needs the **W** flag (and **T** for status) and the device must be programmed.

</details>

<details>
<summary><b>“My Home Assistant is older than 2026.9”</b></summary>

**Settings → About** shows the version; update in **Settings → System → Updates** (take a backup first). The agent needs 2026.9 or newer.

</details>

<details>
<summary><b>“What changes in the panel when the agent updates?”</b></summary>

After the update in HACS and a restart, the Hauflo cards and theme refresh themselves; your manual dashboard edits are kept. If cards look old, hard-reload the page (Ctrl+F5) or the HA app.

</details>

## Examples

Tell yourself when the home lost Hauflo for a while (the house keeps working; monitoring and deploys wait):

```yaml
automation:
  - alias: "Hauflo link lost"
    triggers:
      - trigger: state
        entity_id: binary_sensor.hauflo_link
        to: "off"
        for: "00:30:00"
    actions:
      - action: persistent_notification.create
        data:
          title: "Hauflo"
          message: "No link to Hauflo for 30 minutes."
```

Switch the whole house to summer from your own automation: `select.select_option` on `select.hauflo_house_mode` with `option: summer`.

## Known limitations

- **A Hauflo account is needed.** Without one the integration can't be set up.
- **One agent per Home Assistant.** One Home Assistant serves one Hauflo project.
- **The project import is KNX only** (`.knxproj` or ETS CSV). Devices of other integrations can be placed on the dashboard, not imported.
- **Home Assistant 2026.9 or newer.** The agent reads parts of Home Assistant’s KNX integration that are not a public API. If a new Home Assistant changes them, the agent switches off only those features and says which in **Repairs**.
- **Hand-formatted `automations.yaml` / `scenes.yaml`** (comments, `!include`, `!secret`) are never rewritten, so the family’s own automations and scenes can’t be saved there until the file is plain.
- **Dashboard resources in YAML mode** can’t be added by the agent. On removal (or **Make the house independent**) the cards are copied to `/config/www/hauflo` and Home Assistant shows a notification with the two lines to add to `configuration.yaml`.
- **Voice sentences for floor heating are Russian only** for now.
- **The Hauflo Atelier theme** lives in memory and goes with the agent; the dashboards keep their cards and fall back to Home Assistant’s look.

## Updating and removing

**Update** in HACS when it shows a new version, then restart Home Assistant. What changed is in the [releases](https://github.com/Hauflo/hauflo-agent/releases).

**Remove** in **Settings → Devices & services → Hauflo → Delete**, then in HACS. In Hauflo the project shows “agent offline”. Unlink it there, or pair again later.

**Removal takes with it:** the agent’s system user, its voice sentences (`custom_sentences/ru/hauflo.yaml`), the alarm rules and the comfort floor settings, the guest links and the guest log, who services the home and its access history (`.storage/hauflo.service`), the last plan shown on **Made in this home**, and the hidden sidebar panels come back for everyone. The lights “Away” turned on are switched off. Alarms stop with the agent: to keep them, run **Make the house independent** first.

**The family’s dashboard keeps working** (checked on a clean Home Assistant: removed, the integration’s files deleted, restarted). At removal the cards, icons and font are copied to `/config/www/hauflo` and added as dashboard resources `/local/hauflo/hauflo-icons.js` and `/local/hauflo/hauflo-cards.js`. Only the Hauflo Atelier theme goes: the cards fall back to Home Assistant’s look. In YAML resource mode Home Assistant shows what to add.

**Removal leaves behind, on purpose.** The client’s dashboard must keep working, and a re-added agent must find what it made instead of making duplicates. Remove by hand only what you no longer want:

| What stays | Why | To remove by hand |
| --- | --- | --- |
| Dashboards `hauflo-…` | The family’s dashboard | **Settings → Dashboards** → the dashboard → **Delete** |
| Hauflo’s dashboard as the default | So the family opens their home | **Settings → Dashboards**: make another dashboard the default |
| `/config/www/hauflo` and the Lovelace resources `/local/hauflo/hauflo-icons.js`, `/local/hauflo/hauflo-cards.js` | The cards of that dashboard | After deleting the dashboards: **Settings → Dashboards → ⋮ → Resources** (advanced mode) → delete both, then delete the folder |
| Automations `hauflo_auto_…` | The family’s own rules, plain HA automations | **Settings → Automations & scenes** → delete them |
| Scenes `hauflo_…` and `hauflo_fam_…` | Plain HA scenes | **Settings → Automations & scenes → Scenes** → delete them |
| `automations.yaml.hauflo-bak-*`, `scenes.yaml.hauflo-bak-*` (last 3 each) | Copies before each write | Delete the files in `/config` |
| The labels **Hauflo** and **Hauflo monitoring** (and the category labels a deploy made) | Labels on your entities: **Hauflo** still tells what Hauflo made | **Settings → Areas, labels & zones → Labels** |
| Automations `hauflo_independent_…` (if you made the house independent) | Your alarms, now plain HA automations | **Settings → Automations & scenes** |
| `/config/.storage/hauflo.*` (what Hauflo made, titles, order, scenes, automations, presence, season, open alarms) | A re-added agent picks up where it left | Stop Home Assistant, delete `/config/.storage/hauflo.*`, start it |
| Rooms, floors, KNX entities, light groups, the HomeKit bridge, voice exposure | The home itself | As any Home Assistant item, in **Settings** |
| Backups Hauflo made | Your safety net | **Settings → System → Backups** |

### Leaving Hauflo

**Settings → Devices & services → Hauflo → Configure → Make the house independent of Hauflo.** It asks first, then:

- keeps the dashboard cards in `/config/www/hauflo` as dashboard resources for good, also while the agent is installed (refreshed at each start), so the dashboard doesn’t depend on the agent however it is removed;
- writes each enabled Hauflo alarm rule as a plain automation in `automations.yaml`, named «Alarm: <rule>»: the same sensors and alarm states, a push to the same Home Assistant app phones (critical where the rule is), a notification in Home Assistant, and the rule’s button (such as “Shut off water”) on the push. A copy of `automations.yaml` is made first. A hand-formatted file is left alone and you’re told so. An automation made this way before is left as it is;
- deletes nothing. Until you remove the integration, an alarm comes twice (Hauflo and the automation).

**What stops once Hauflo is removed:** cloud monitoring (“agent offline”, “KNX bus silent”, the monitoring page), email and Telegram alarms, the alarm history, repeats until **Accept**, deploys and updates from the project, guest links, the “Away” presence simulation, the comfort floor, voice sentences, and editing the family’s automations and scenes on the dashboard (the ones already made keep running). The phones in the automations are the ones the app had then: a new phone needs to be added to them by hand.

## Support

- Email: [support@hauflo.com](mailto:support@hauflo.com) (EU) · [support@hauflo.ru](mailto:support@hauflo.ru) (Russia)
- Telegram: [@hauflo_bot](https://t.me/hauflo_bot)
- Bugs in the agent: [GitHub issues](https://github.com/Hauflo/hauflo-agent/issues). Please attach the diagnostics (Hauflo → ⋮ → **Download diagnostics**; secrets and personal data — contact names, phones, e-mails, project names — are removed).
- Website: [hauflo.com](https://hauflo.com) · [hauflo.ru](https://hauflo.ru)

## License

The source code is published so you can check what runs in the home. It is not open source: you may install and run the agent only with the Hauflo service, and not copy it into other products. See [LICENSE](https://github.com/Hauflo/hauflo-agent/blob/main/LICENSE).

<sub>Screenshots: Hauflo’s real cards in a demo home with simulated states.</sub>
