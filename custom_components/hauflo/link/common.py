"""Link timings and the agent's features."""
from __future__ import annotations
import functools
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from .. import combos, exposure, helper_store, locks as locks_mod, self_update, servers, watchdog
from ..const import CONF_REFRESH_TOKEN_ID, DATA_GOOD_HOSTS
from ..foreign import common as foreign_common
from ..health import common as health_common
from ..insight import common as insight_common
from ..install import common as install_common
from ..intercom import setup as intercom_setup
from ..signals import canon as signals_canon
from ..tuning import tuning
knx_round_gap = functools.cache(lambda: tuning()['link']['knx_round_gap_s'])
knx_round_min_reads = functools.cache(lambda: tuning()['link']['knx_round_min_reads'])
event_recent = functools.cache(lambda: tuning()['link']['event_recent_s'])

async def local_token(hass: HomeAssistant, entry: ConfigEntry):
    """The refresh token of the agent's own system user (made once, kept with the entry). Access tokens made from"""
    user = await helper_store.agent_user(hass, entry)
    refresh = None
    if (rid := entry.data.get(CONF_REFRESH_TOKEN_ID)):
        refresh = hass.auth.async_get_refresh_token(rid)
    if refresh is None:
        refresh = await hass.auth.async_create_refresh_token(user)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN_ID: refresh.id})
    return refresh
HA_UP = dt_util.utcnow().isoformat()
FEATURES = [foreign_common.FEATURE, foreign_common.PLAN_FEATURE, foreign_common.WRITE_PLAN_FEATURE, foreign_common.MEMBERS_FEATURE, foreign_common.SHOWN_FEATURE, signals_canon.FEATURE, signals_canon.MUTE_FEATURE, signals_canon.SHELVE_FEATURE, combos.FEATURE, insight_common.FEATURE, insight_common.OBSERVE_FEATURE, watchdog.FEATURE, health_common.FEATURE, install_common.FEATURE, self_update.FEATURE, intercom_setup.FEATURE, locks_mod.FEATURE, exposure.FEATURE]
_t = functools.cache(lambda: tuning()['link'])
reconnect_hint_wait = functools.cache(lambda: tuple(_t()['reconnect_hint_wait_s']))
link_lost_issue_after = functools.cache(lambda: _t()['lost_issue_after_s'])
big_frame = functools.cache(lambda: _t()['big_frame_bytes'])
connect_timeout = functools.cache(lambda: _t()['connect_timeout_s'])
unpair_timeout = functools.cache(lambda: _t()['unpair_timeout_s'])

def good_hosts(hass: HomeAssistant) -> servers.GoodHosts:
    """The hosts that answered last, per region — this HA's, for this run (servers.GoodHosts)."""
    return hass.data.setdefault(DATA_GOOD_HOSTS, servers.GoodHosts())
