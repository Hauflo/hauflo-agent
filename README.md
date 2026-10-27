**English** · [Русский](https://github.com/Hauflo/hauflo-agent/blob/main/README.ru.md)

# Hauflo for Home Assistant

**From a KNX ETS project to a finished Home Assistant: devices by room, a dashboard your client will enjoy, alarms and monitoring after handover.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/) [![Release](https://img.shields.io/github/v/release/Hauflo/hauflo-agent?label=release)](https://github.com/Hauflo/hauflo-agent/releases) [![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5.svg?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/) [![KNX](https://img.shields.io/badge/starting%20with-KNX-E8A33D.svg)](https://hauflo.com)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Hauflo&repository=hauflo-agent&category=integration)

![A Hauflo room on a tablet (light theme) and on a phone (dark theme)](https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/hero.webp)

This integration is the **Hauflo agent**. It connects the Home Assistant in a home to [Hauflo](https://hauflo.com), the service where a KNX integrator turns an ETS project into a ready Home Assistant. The agent brings the client's dashboard cards with it and keeps the link for deploys, monitoring and alarms.

> Hauflo is in private beta for smart-home integrators. You need a Hauflo account to use the agent: [request access](https://app.hauflo.com/request-access).

## What you get

**For the integrator**

- 📐 **The ETS project becomes Home Assistant.** Upload a `.knxproj` (or the group address export as CSV). Hauflo builds devices from the ETS objects: lights, dimmers, blinds, thermostats, floor heating, ACs, sensors, scenes. Rooms come from the ETS building or from room codes in the names.
- 👀 **A plan before every deploy.** You see what will be created and changed. Nothing is applied until you confirm. Removing anything needs its own checkbox.
- 💾 **A backup before every change.** The agent makes a standard Home Assistant backup first. No backup, no deploy.
- 🔁 **Re-import without starting over.** The project changed? Upload it again: rooms, names and scenes stay, and the plan shows the difference. A second deploy with no changes changes nothing.
- 🔌 **No ports, no tokens.** The agent keeps one outgoing encrypted connection. No port forwarding, no Nabu Casa.
- 📡 **Monitoring after handover.** Every site on one screen: link to the home, KNX bus connection, unavailable devices, alarms and an event log.
- 🚨 **Alarms the way you set them.** Leak, overheating, KNX bus lost, a quiet sensor: to Telegram, email and push to the residents' phones.

**For the people who live there**

- 🏠 **The whole house at a glance.** "All quiet", or what needs attention. Temperature, lights on, air conditioners, floor heating, all by floor.
- 🛋️ **Rooms that make sense.** Scenes, lights with zones, blinds, climate. One tap opens a room.
- 🌡️ **Climate with a 24-hour chart.** Swipe to set the temperature. See how the room or the floor behaved today.
- ⚙️ **Settings for the family.** Light or dark theme for each person. Winter or summer for all air conditioners at once.
- 📱 **Phone and wall tablet.** Kiosk mode for a wall tablet, with a night screen.
- 🔔 **Alarms that are hard to miss.** Push to the Home Assistant app. Critical alarms ring even in silent mode, with an action button such as "Shut off water".
- 🌍 **English and Russian.** The cards follow the language of the person's Home Assistant profile.

<!-- Automations (sun / time / alarm-clock schedules) are being built now. Add them here only once they ship. -->

## The client's dashboard

Hauflo builds the dashboard from the project. There are no hand-made cards to maintain. Light and dark themes, phone and tablet.

![The whole building, a floor and a room on a phone](https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/phones.webp)

**A floor on a tablet.** Each room glows when something is on. Tap a room to open it.

![Ground floor on a tablet, dark theme](https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/tablet-floor-dark.webp)

**Climate.** The target in big numbers, the current temperature and a chart of the last 24 hours. ACs get modes and fan speed. Floor heating shows "heating" or "holding".

![Air conditioner and floor heating cards with a 24-hour chart](https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/climate.webp)

**Blinds.** Open, stop, close, for the whole room or one by one. A blind that reports nothing shows no guess.

![Blinds card, light and dark](https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/covers.webp)

**Settings** (the gear at the top). Theme: light, dark or as the phone. Season: winter (ACs heat) or summer (ACs cool) in one tap. ACs that are off stay off. Floor heating and radiators are not touched.

![The Settings sheet, light and dark](https://raw.githubusercontent.com/Hauflo/hauflo-agent/main/docs/images/settings.webp)

Hauflo also has cards for devices from other integrations: weather, air quality, media, cameras, security and locks, graphs, other devices. A sensor with no signal says "no data" instead of an old value.

*The screenshots are a demo home with simulated data.*

## How it works

1. **Upload the project** in Hauflo: a `.knxproj` or the ETS group address CSV.
2. **Review the result.** Rooms, devices, scenes. Where an address can be read two ways, you decide.
3. **Connect the home.** Install this agent and pair it with a one-time code (below).
4. **See the plan, then deploy.** A backup, then rooms, devices, light groups and the dashboard.

## Install

**You need:** Home Assistant 2026.9 or newer, [HACS](https://hacs.xyz), outbound HTTPS from Home Assistant, and a KNX IP interface or router in the house that Home Assistant's KNX integration supports.

1. **Add the repository to HACS.** Click the button below, or in HACS open ⋮ → **Custom repositories**, paste `https://github.com/Hauflo/hauflo-agent` and choose type **Integration**.

   [![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Hauflo&repository=hauflo-agent&category=integration)

2. **Download Hauflo** in HACS, then **restart Home Assistant**.
3. **Get a pairing code** in Hauflo: your project → **Connect the home** → **Get a code**. It works once, for 30 minutes.
4. **Add the integration.** In Home Assistant open **Settings → Devices & services → Add integration → Hauflo** and enter the code. That's all: the code picks the server (`EU-…` → app.hauflo.com, `RU-…` → app.hauflo.ru).

   [![Open your Home Assistant instance and start setting up Hauflo.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hauflo)

5. **Confirm the KNX addresses.** Home Assistant shows the group address ranges Hauflo may send commands to. Remove any it must not control, such as a gate or a lock, and continue. You can change this later in the integration's options.
6. In a few seconds Hauflo shows **agent online**. Open **Deploy** in Hauflo, check the plan and deploy.

HACS only downloads the files. The integration is set up in step 4.

## Security in plain words

The agent sits in the client's home, so here is exactly what it can and can't do.

**How it connects.** One outgoing encrypted connection to Hauflo. Nothing listens on the home network. Hauflo keeps no Home Assistant token: the agent acts as its own system user, which is removed together with the integration.

**What Hauflo may do through the agent** (a fixed list, checked inside this Home Assistant):

- create rooms, floors and labels, and the KNX devices of the project;
- create and update its own dashboards and scenes;
- set up the KNX connection to an interface in the house, light groups, voice exposure and the Apple Home bridge;
- read states for monitoring and alarms;
- send KNX commands only to the group address ranges you confirmed in step 5.

**What it may not do:**

- download backups, touch add-ons, automations or arbitrary services;
- change or delete devices and entities it didn't create (your hand-made ones stay yours);
- read KNX Secure keys or passwords;
- hand a lock, an alarm panel or a valve to a voice assistant, a group or a scene.

**A backup before every change.** Before each deploy the agent makes a Home Assistant backup (settings, without the history database; encrypted if your Home Assistant has a backup key). It refuses to change anything without a fresh one. Restore it from **Settings → System → Backups**, with or without Hauflo.

**What leaves the house.** During a deploy: rooms and KNX devices. Ongoing: the Home Assistant version, the KNX bus connection and the states of devices that have alarms. Never: people's locations, cameras, media, calendars, PIN codes.

**Where data is stored.** EU accounts at app.hauflo.com, in Germany. Russian accounts at app.hauflo.ru, in Russia.

The source code is published here so you can check what runs in the home.

## Alarms

Set up in Hauflo → **Alarms**. A rule is: what happened → which sensors → who gets it → how.

- **Push to the residents' phones** through the Home Assistant app. Critical alarms ring even in silent mode (iOS Critical Alerts, Android alarm stream). They repeat until someone accepts the alarm. An action button can do the obvious thing, for example "Shut off water".
- **Telegram** through [@hauflo_bot](https://t.me/hauflo_bot), and **email**: for you, the engineer on duty or the client. Each person gets only the alarms of their own sites.
- The push rules live in this Home Assistant. They keep working when Hauflo or the internet is down.

To watch any other entity (a boiler fault, ventilation, a battery), give it the label **Hauflo monitoring** in Home Assistant.

## Voice

- **Russian Assist sentences for floor heating and thermostats.** A KNX floor heating often has no on/off object, and Home Assistant answers "turn off the floor heating" with an error. The agent adds its own sentences: it switches where the thermostat can, and otherwise explains that it keeps the setpoint. Setting or asking a room's temperature goes to that room's thermostat.
- **Apple Home and Yandex Alice.** The deploy can set up an Apple Home bridge (with a pairing QR code on the handover page) and expose rooms to Alice. Locks, alarm panels and valves are never exposed.

## FAQ

<details>
<summary><b>Does the agent send commands to the KNX bus by itself?</b></summary>

No. Commands go to the bus when a person acts: a tap on the dashboard, a scene, a voice command, an alarm's action button, the Winter/Summer switch. Home Assistant's KNX integration reads states as usual.

One exception, and only if the integrator turns it on for a room: **comfort floor**. The client sets the room temperature, and the agent keeps that room's floor heating between a comfortable minimum and an upper limit. It writes slowly (once per interval, 10 minutes by default) and only inside the limits.

</details>

<details>
<summary><b>What happens if Hauflo or the internet is down?</b></summary>

The home keeps working. It is plain Home Assistant with standard KNX devices. The dashboard, the push alarm rules and the comfort floor run inside Home Assistant. Hauflo shows the site as offline, and the agent reconnects by itself.

</details>

<details>
<summary><b>Will Hauflo overwrite what I made by hand?</b></summary>

No. Hauflo keeps a list of what it created and changes only that. Your own entities, automations and dashboards are not touched. If you edited something Hauflo made, it notices and doesn't overwrite your edit.

</details>

<details>
<summary><b>Which KNX hardware does it need?</b></summary>

Any KNX IP interface or router that Home Assistant's KNX integration supports. Hauflo connects only to an interface inside the house.

</details>

<details>
<summary><b>How do I allow more KNX addresses later?</b></summary>

**Settings → Devices & services → Hauflo → Configure.** If Hauflo needs an address that isn't allowed yet, it is listed there for you to confirm.

</details>

<details>
<summary><b>"The code is wrong or expired"</b></summary>

A code works once, for 30 minutes. Get a new one in Hauflo: your project → **Connect the home** → **Get a code**. If Home Assistant can't reach Hauflo at all, check its internet access.

</details>

<details>
<summary><b>How do I remove the agent?</b></summary>

**Settings → Devices & services → Hauflo → Delete**, then remove it in HACS. The agent's system user and its voice sentences go with it. The client's dashboard keeps working: its cards are left in `/config/www/hauflo`. In Hauflo the project shows "agent offline"; unlink it there or pair again later.

</details>

## Updating

HACS shows new versions. Update, then restart Home Assistant. What changed is in the [releases](https://github.com/Hauflo/hauflo-agent/releases).

## Support

- Email: [support@hauflo.com](mailto:support@hauflo.com) (EU) · [support@hauflo.ru](mailto:support@hauflo.ru) (Russia)
- Telegram: [@hauflo_bot](https://t.me/hauflo_bot)
- Bugs in the agent: [GitHub issues](https://github.com/Hauflo/hauflo-agent/issues). Please attach the integration's diagnostics (Hauflo → ⋮ → **Download diagnostics**; secrets are removed).
- Website: [hauflo.com](https://hauflo.com) · [hauflo.ru](https://hauflo.ru)

## License

The source code is published so you can check what runs in the home. It is not open source: you may install and run the agent only with the Hauflo service, and not copy it into other products. See [LICENSE](https://github.com/Hauflo/hauflo-agent/blob/main/LICENSE).
