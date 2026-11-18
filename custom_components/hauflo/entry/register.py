"""Registers the agent's HTTP views, once per HA run."""
from __future__ import annotations
from homeassistant.core import HomeAssistant
from .. import backup, exposure, history as history_mod, house, order as block_order, ownership, protect, room_pictures, self_update, sidebar, stock, titles
from ..automations import views as automations_views
from ..const import DATA_NOTIFY_VIEWS
from ..foreign import views as foreign_views
from ..guest import views as guest_views
from ..insight import views as insight_views
from ..install import views as install_views
from ..intercom import setup as intercom_setup
from ..noeffect import views as noeffect_views
from ..notify import views as notify_views
from ..policy.marks import protect_mark, protect_unmark
from ..policy.scrub import history_reads
from ..presence import views as presence_views
from ..rules import views as rules_views
from ..runtime import automations_of, exposure_of, guest_of, habits_set, habits_summary, house_of, insights_of, intercom_of, notify_of, ownership_of, pair_effect_of, presence_of, rules_of, scenes_of, season_of
from ..scenes import views as scenes_views
from ..season import views as season_views
from ..signals import views as signals_views
from .views import OwnedView, RangesView

def register_views(hass: HomeAssistant) -> None:
    """Every view of the agent's API — once, whatever entry is loaded."""
    if not hass.data.get(DATA_NOTIFY_VIEWS):
        hass.data[DATA_NOTIFY_VIEWS] = True
        hass.http.register_view(notify_views.NotifyView(lambda: notify_of(hass)))
        hass.http.register_view(notify_views.NotifyTestView(lambda: notify_of(hass)))
        hass.http.register_view(backup.BackupView())
        hass.http.register_view(install_views.InstallView())
        hass.http.register_view(install_views.RestartView())
        hass.http.register_view(self_update.SelfUpdateView())
        hass.http.register_view(notify_views.AlarmsView(lambda: notify_of(hass), lambda: house_of(hass)))
        hass.http.register_view(signals_views.SignalsView())
        hass.http.register_view(signals_views.SignalChooseView())
        hass.http.register_view(insight_views.InsightsView(lambda: insights_of(hass), lambda: habits_summary(hass), lambda on: habits_set(hass, on)))
        hass.http.register_view(OwnedView())
        hass.http.register_view(ownership.OwnershipView(ownership_of))
        hass.http.register_view(RangesView())
        hass.http.register_view(rules_views.RulesView(lambda: rules_of(hass)))
        hass.http.register_view(sidebar.SidebarView())
        hass.http.register_view(season_views.SeasonView(lambda: season_of(hass)))
        hass.http.register_view(season_views.SeasonConfigView(lambda: season_of(hass)))
        hass.http.register_view(titles.TitlesView())
        hass.http.register_view(block_order.OrderView())
        room_pictures.register(hass)
        hass.http.register_view(automations_views.AutomationsView(lambda: automations_of(hass)))
        hass.http.register_view(scenes_views.ScenesView(lambda: scenes_of(hass)))
        hass.http.register_view(presence_views.PresenceView(lambda: presence_of(hass)))
        hass.http.register_view(guest_views.GuestView(lambda: guest_of(hass), lambda: notify_of(hass)))
        hass.http.register_view(house.HouseView())
        hass.http.register_view(stock.StockView())
        hass.http.register_view(protect.ProtectView(protect_mark, protect_unmark))
        hass.http.register_view(foreign_views.ForeignView())
        hass.http.register_view(exposure.ExposureView(exposure_of))
        hass.http.register_view(intercom_setup.IntercomView(lambda: intercom_of(hass)))
        hass.http.register_view(noeffect_views.PairsView(lambda: pair_effect_of(hass)))
        hass.http.register_view(history_mod.HistoryView(lambda eid, attr: history_reads(hass, eid, attr)))
