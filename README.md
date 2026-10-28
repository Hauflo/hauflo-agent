<img src="https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/hero.webp" width="840" alt="The Hauflo dashboard of a demo home on a wall tablet (light) and a phone (dark).">

# Hauflo agent

**English** · [Русский](https://github.com/Hauflo/hauflo-agent/blob/main/README.ru.md)

**Your ETS project, handed over as a home.** The agent connects a home's Home Assistant to [Hauflo](https://hauflo.com), where a KNX integrator turns an ETS project into a finished Home Assistant: rooms, devices, a dashboard the family enjoys, then alarms and monitoring after handover. Starting with KNX.

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Hauflo&repository=hauflo-agent&category=integration)

> Private beta for smart-home integrators. You need a Hauflo account: [request access](https://app.hauflo.com/request-access).

## Project in. Home out.

<img src="https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/from-ets.webp" width="840" alt="On the left, the living room's lights as group addresses in a demo ETS project. On the right, the same lights as the family's room card in Home Assistant.">

1. **Upload** a `.knxproj` or the ETS group address CSV.
2. **Review the plan.** Rooms, devices and scenes. Where an address can be read two ways, you decide.
3. **Deploy.** The agent makes a backup, then creates everything in Home Assistant.
4. **Hand over.** Phone and wall tablet, light and dark.

Nobody builds cards by hand. When the project changes, the dashboard follows.

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
4. **Add the integration** and enter the code. The code picks the server: `EU-…` → app.hauflo.com, `RU-…` → app.hauflo.ru.

   [![Open your Home Assistant instance and start setting up Hauflo.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hauflo)

5. **Confirm the KNX addresses.** Remove any Hauflo must not control, such as a gate or a lock.
6. Hauflo shows **agent online** in a few seconds. Open **Deploy**, check the plan, deploy.

## Security in plain words

- **One outgoing encrypted connection.** Nothing listens on the home network. Hauflo keeps no Home Assistant token: the agent is its own system user, removed together with the integration.
- **A fixed list of what Hauflo may do,** checked inside this Home Assistant. KNX commands go only to the addresses you confirmed.
- **The owner’s switch.** In the integration’s options: **Allow Hauflo to change this home’s setup**. Off, and Hauflo changes nothing. Monitoring, alarms and voice keep working.
- **A backup before changes.** Deploying without one is only an explicit choice, and then a notice stays in Home Assistant. Restore it from **Settings → System → Backups**, with or without Hauflo.
- **Open to read.** The source code is here, so you can check what runs in the home.

<details>
<summary><b>The full list: may, may not, what leaves the house</b></summary>

**Hauflo may, through the agent:**

- create rooms, floors and labels, and the KNX devices of the project;
- create and update its own dashboards and scenes, set its dashboard and theme as the default;
- write its own rules (`hauflo_auto_*`) into `automations.yaml`, never touching other automations and never rewriting a hand-formatted file;
- set the home's location and time zone in Home Assistant: only an administrator, from the family's **Automations** page;
- hide Home Assistant's extra sidebar panels (Map, Energy, Logbook…) for every person, with the **Clean up the menu** option;
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

**What leaves the house.** During a deploy: rooms and KNX devices. Ongoing: the Home Assistant version, the KNX bus connection and the states of devices that have alarms. Never: people’s locations, cameras, media, calendars or PIN codes.

**Where data is stored.** EU accounts at app.hauflo.com, in Germany. Russian accounts at app.hauflo.ru, in Russia.

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

## Updating and removing

**Update** in HACS when it shows a new version, then restart Home Assistant. What changed is in the [releases](https://github.com/Hauflo/hauflo-agent/releases).

**Remove** in **Settings → Devices & services → Hauflo → Delete**, then in HACS. The agent’s system user and voice sentences go with it, the hidden sidebar panels come back, and the lights “Away” turned on are switched off. The client’s dashboard keeps working: its cards stay in `/config/www/hauflo`. In Hauflo the project shows “agent offline”. Unlink it there, or pair again later.

## Support

- Email: [support@hauflo.com](mailto:support@hauflo.com) (EU) · [support@hauflo.ru](mailto:support@hauflo.ru) (Russia)
- Telegram: [@hauflo_bot](https://t.me/hauflo_bot)
- Bugs in the agent: [GitHub issues](https://github.com/Hauflo/hauflo-agent/issues). Please attach the diagnostics (Hauflo → ⋮ → **Download diagnostics**; secrets are removed).
- Website: [hauflo.com](https://hauflo.com) · [hauflo.ru](https://hauflo.ru)

## License

The source code is published so you can check what runs in the home. It is not open source: you may install and run the agent only with the Hauflo service, and not copy it into other products. See [LICENSE](https://github.com/Hauflo/hauflo-agent/blob/main/LICENSE).

<sub>Screenshots: Hauflo’s real cards in a demo home with simulated states.</sub>
