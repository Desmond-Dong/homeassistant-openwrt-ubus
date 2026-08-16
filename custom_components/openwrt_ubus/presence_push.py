"""Event-driven presence push, migrated from ha-openwrt-presence's push model.

Instead of requiring a separate router agent with its own bearer token, this
reuses the integration's existing authenticated ubus session:

1. On setup the integration writes a tiny busybox-sh watcher to the router
   (ubus ``file write``) and starts it detached (``file exec``).
2. The watcher diffs hostapd client lists every ~2s and POSTs changes to an
   auto-generated Home Assistant webhook. No user credentials involved.
3. Device trackers consult the pushed state first (instant updates) and fall
   back to normal coordinator polling whenever the stream goes stale.
4. On unload the watcher is killed via the same ubus channel; /tmp placement
   means a router reboot also cleans it up automatically.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

from .const import (
    PRESENCE_PUSH_HEARTBEAT_LOOPS,
    PRESENCE_PUSH_INTERVAL_SECONDS,
    PRESENCE_PUSH_PID_PATH,
    PRESENCE_PUSH_SCRIPT_PATH,
    PRESENCE_PUSH_STALE_SECONDS,
)
from .Ubus.const import API_RPC_CALL
from .shared_data_manager import SharedUbusDataManager

_LOGGER = logging.getLogger(__name__)

WATCHER_SCRIPT_TEMPLATE = """#!/bin/sh
PIDF="{pid_path}"
if [ -f "$PIDF" ]; then
  kill -0 $(cat "$PIDF") 2>/dev/null && exit 0
fi
echo $$ > "$PIDF"
URL="{webhook_url}"
INT={interval}
BEAT={heartbeat}
PREV="__boot__"
N=0
while true; do
  CUR=$(ubus list 2>/dev/null | grep '^hostapd\\.' | while read I; do
    ubus call "$I" get_clients 2>/dev/null | grep -o '"addr":"[0-9A-Fa-f:]*"'
  done | sort -u)
  if [ "$CUR" != "$PREV" ] || [ "$N" -ge "$BEAT" ]; then
    MACS=$(echo "$CUR" | cut -d'"' -f4 | tr '\\n' ',' | sed 's/,$//')
    wget -q -T 4 -O /dev/null --post-data="macs=$MACS" "$URL" 2>/dev/null
    PREV="$CUR"
    N=0
  fi
  N=$((N+1))
  sleep "$INT"
done
"""


class PresencePushManager:
    """Deploys the router-side watcher and serves pushed presence state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 data_manager: SharedUbusDataManager) -> None:
        self._hass = hass
        self._entry = entry
        self._data_manager = data_manager
        self._webhook_id: str | None = None

        # mac (upper) -> connected, timestamp of last authoritative push
        self._clients: dict[str, bool] = {}
        self._last_push: datetime | None = None
        self._listeners: dict[str, list[Callable[[], None]]] = {}
        self._deployed = False

    # ------------------------------------------------------------------ #
    # Public API for device trackers
    # ------------------------------------------------------------------ #

    @property
    def fresh(self) -> bool:
        """Whether pushed state is recent enough to be trusted."""
        return (
            self._last_push is not None
            and (datetime.now() - self._last_push) < timedelta(seconds=PRESENCE_PUSH_STALE_SECONDS)
        )

    def get_connected(self, mac: str) -> bool | None:
        """Latest pushed presence for a MAC, or None when stream is stale."""
        if not self.fresh:
            return None
        return self._clients.get(mac.upper())

    def add_listener(self, mac: str, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when this MAC's pushed state changes."""
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
        """Register the webhook and deploy the watcher on the router."""
        from homeassistant.components import webhook

        self._webhook_id = f"{self._entry.entry_id}_{secrets.token_hex(8)}"

        webhook.async_register(
            self._hass,
            "openwrt_ubus",
            f"Presence push {self._entry.title}",
            self._webhook_id,
            self._handle_webhook,
        )

        await self._deploy_watcher()
        _LOGGER.info(
            "Presence push enabled for %s (deployed=%s)",
            self._entry.title,
            self._deployed,
        )

    async def stop(self) -> None:
        """Unregister the webhook and stop the router-side watcher."""
        from homeassistant.components import webhook

        if self._webhook_id:
            webhook.async_unregister(self._hass, self._webhook_id)
            self._webhook_id = None
        await self._stop_watcher()
        self._clients.clear()
        self._last_push = None
        self._listeners.clear()

    # ------------------------------------------------------------------ #
    # Router-side watcher
    # ------------------------------------------------------------------ #

    async def _deploy_watcher(self) -> None:
        """Write the watcher script to the router and start it detached."""
        try:
            base_url = get_url(self._hass, allow_ip=True, prefer_external=False)
            webhook_url = f"{base_url}/api/webhook/{self._webhook_id}"
        except Exception as exc:  # no URL resolvable
            _LOGGER.warning("Presence push disabled: cannot determine HA URL (%s)", exc)
            return

        script = WATCHER_SCRIPT_TEMPLATE.format(
            pid_path=PRESENCE_PUSH_PID_PATH,
            webhook_url=webhook_url,
            interval=PRESENCE_PUSH_INTERVAL_SECONDS,
            heartbeat=PRESENCE_PUSH_HEARTBEAT_LOOPS,
        )

        try:
            client = await self._data_manager._get_ubus_client()
            write_result = await client.api_call(
                API_RPC_CALL, "file", "write", {"path": PRESENCE_PUSH_SCRIPT_PATH, "data": script}
            )
            if isinstance(write_result, Exception):
                raise write_result

            # Start detached; the -c wrapper returns immediately.
            exec_result = await client.file_exec(
                "/bin/sh",
                ["-c", f"nohup /bin/sh {PRESENCE_PUSH_SCRIPT_PATH} >/dev/null 2>&1 &"],
            )
            if isinstance(exec_result, Exception):
                raise exec_result
            self._deployed = True
        except PermissionError:
            _LOGGER.warning(
                "Presence push needs ubus ACL 'file' write + exec for %s. "
                "Falling back to polling.",
                PRESENCE_PUSH_SCRIPT_PATH,
            )
        except Exception as exc:
            _LOGGER.warning("Presence push could not start watcher: %s", exc)

    async def _stop_watcher(self) -> None:
        """Kill the watcher on the router (best effort)."""
        if not self._deployed:
            return
        try:
            client = await self._data_manager._get_ubus_client()
            await client.file_exec(
                "/bin/sh",
                ["-c", f'kill $(cat {PRESENCE_PUSH_PID_PATH}) 2>/dev/null; rm -f {PRESENCE_PUSH_PID_PATH}'],
            )
        except Exception as exc:
            _LOGGER.debug("Presence push watcher stop failed (router gone?): %s", exc)
        self._deployed = False

    # ------------------------------------------------------------------ #
    # Webhook handler
    # ------------------------------------------------------------------ #

    async def _handle_webhook(self, hass: HomeAssistant, webhook_id: str, request) -> None:
        """Receive the pushed MAC list and refresh tracked entities."""
        body = await request.post()
        raw = body.get("macs", "")
        macs = {m.strip().upper() for m in raw.split(",") if m.strip()}

        previous = dict(self._clients)
        # Absence from the authoritative list means disconnected: keep every
        # previously seen MAC as an explicit False so trackers don't fall back.
        known = set(previous) | macs
        self._clients = {m: (m in macs) for m in known}
        self._last_push = datetime.now()

        changed = {m for m in known if previous.get(m) != self._clients.get(m)}
        for mac in changed:
            for callback in self._listeners.get(mac, []):
                try:
                    callback()
                except Exception as exc:
                    _LOGGER.debug("Presence push listener error for %s: %s", mac, exc)
