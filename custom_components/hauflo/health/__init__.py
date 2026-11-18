"""0.41 — the health of the house's Home Assistant itself: the age of the last backup, free disk space, an update of
core / OS / Supervisor waiting, the certificate of the external https address running out. One registry (runner.py),
one small file per check; each reads only HA's public API and says «not here» (None) where its source doesn't exist
(HA Container / Core: no Supervisor, no update entities; no https address: no certificate)."""
