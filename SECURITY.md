# Security policy / Политика безопасности

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue:

- email **support@hauflo.com** (international) or **support@hauflo.ru** (Russia), subject starting with `SECURITY`;
- say what you found, how to reproduce it, and which agent version (`Settings → Devices & services → Hauflo`);
- you get an acknowledgement within **3 working days** and a status update at least every **14 days** until it is fixed.

We ask for coordinated disclosure: please give us a reasonable time to ship a fix before publishing details. We do not
take legal action against good-faith research that avoids other people's data and stops at proof of the problem.

Machine-readable contact: `/.well-known/security.txt` on app.hauflo.com and hauflo.com.

## Scope

The Hauflo agent (this repository: the Home Assistant integration, its dashboard cards and icons) and the Hauflo service
it talks to. Out of scope: Home Assistant itself, KNX hardware, third-party integrations.

## Supported versions

Security fixes go into the latest released agent version; the agent updates through HACS. Older versions are not patched.

## Сообщение об уязвимости

Сообщайте о проблемах безопасности **приватно**, не в публичных issue: на **support@hauflo.ru** (Россия) или
**support@hauflo.com**, тема письма начинается с `SECURITY`. Опишите находку, шаги воспроизведения и версию агента.
Подтверждение — в течение 3 рабочих дней, статус — не реже раза в 14 дней до исправления. Просим согласовывать
раскрытие: дайте время выпустить исправление.
