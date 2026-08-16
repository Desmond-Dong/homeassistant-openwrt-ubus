"""Near-real-time presence via fast HA-side polling of the existing ubus session.

Nothing is installed or executed on the router. A background task in Home
Assistant queries hostapd client lists every few seconds through the already
authenticated ubus session (one batched JSON-RPC call), so device trackers
reflect association changes within seconds instead of waiting for the normal
coordinator polling cycle. When the fast loop fails or is disabled, trackers
transparently fall back to regular polling.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    PRESENCE_FAST_INTERVAL_SECONDS,
    PRESENCE_FAST_STALE_MULTIPLIER,
)
from .shared_data_manager import SharedUbusDataManager

_LOGGER = logging.getLogger(__name__)


class PresenceFastPoller:
    """Lightweight fast-poll presence provider (HA-side only)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 data_manager: SharedUbusDataManager) -> None:
        self._hass = hass
        self._entry = entry
        self._data_manager = data_manager

        # mac (upper) -> connected
        self._clients: dict[str, bool] = {}
        self._last_success: datetime | None = None
        self._listeners: dict[str, list[Callable[[], None]]] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._warned = False

    # ------------------------------------------------------------------ #
    # Public API for device trackers
    # ------------------------------------------------------------------ #

    @property
    def fresh(self) -> bool:
        """Whether fast-poll state is recent enough to be trusted."""
        return (
            self._last_success is not None
            and (datetime.now() - self._last_success)
            < timedelta(seconds=PRESENCE_FAST_INTERVAL_SECONDS * PRESENCE_FAST_STALE_MULTIPLIER)
        )

    def get_connected(self, mac: str) -> bool | None:
        """Latest fast-poll presence for a MAC, or None when not trustworthy."""
        if not self.fresh:
            return None
        return self._clients.get(mac.upper())

    def add_listener(self, mac: str, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when this MAC's presence changes."""
        mac_upper = mac.upper()
        self._listeners.setdefault(mac_upper, []).append(callback)

        def _remove() -> None:
            callbacks = self._listeners.get(mac_upper)
            if callbacks and callback in callbacks:
                callbacks.remove(callback)

        return _remove

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start the fast-poll loop in the background."""
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = self._hass.async_create_background_task(
            self._run(), "openwrt_ubus_presence_fast_poll"
        )
        _LOGGER.info("Presence fast-poll started for %s", self._entry.title)

    async def stop(self) -> None:
        """Stop the loop and clear state."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._clients.clear()
        self._last_success = None
        self._listeners.clear()

    # ------------------------------------------------------------------ #
    # Poll loop
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
                self._warned = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._warned:
                    _LOGGER.warning("Presence fast-poll error (falling back to normal polling): %s", exc)
                    self._warned = True
                self._last_success = None
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=PRESENCE_FAST_INTERVAL_SECONDS
                )
                return
            except asyncio.TimeoutError:
                continue

    async def _poll_once(self) -> None:
        """One fast cycle: batched get_clients over hostapd interfaces."""
        client = await self._data_manager._get_ubus_client("hostapd")

        interfaces_result = await client.get_hostapd()
        interfaces = (
            [i for i in interfaces_result.keys() if i.startswith("hostapd.")]
            if isinstance(interfaces_result, dict)
            else []
        )

        current: set[str] = set()
        if interfaces:
            sta_data = await client.get_all_sta_data_batch(interfaces, is_hostapd=True)
            for ap_info in sta_data.values():
                for mac in ap_info.get("devices", []):
                    if mac:
                        current.add(mac.upper())

        previous = dict(self._clients)
        known = set(previous) | current
        self._clients = {m: (m in current) for m in known}
        self._last_success = datetime.now()

        changed = {m for m in known if previous.get(m) != self._clients.get(m)}
        for mac in changed:
            for callback in self._listeners.get(mac, []):
                try:
                    callback()
                except Exception as exc:
                    _LOGGER.debug("Presence fast-poll listener error for %s: %s", mac, exc)
