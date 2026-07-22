"""Topology panel and websocket support for OpenWrt ubus."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from .const import (
    CONF_ENABLE_TOPOLOGY_PANEL,
    DEFAULT_ENABLE_TOPOLOGY_PANEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

TOPOLOGY_PANEL_PATH = "openwrt-ubus-topology"
TOPOLOGY_STATIC_URL = "/openwrt_ubus_static"
TOPOLOGY_PANEL_MODULE_URL = f"{TOPOLOGY_STATIC_URL}/openwrt-ubus-topology.js"
TOPOLOGY_WEBCOMPONENT = "openwrt-ubus-topology-panel"
TOPOLOGY_WS_TYPE = "openwrt_ubus/topology"
TOPOLOGY_OFFLINE_GRACE_SECONDS = 90


async def async_setup_topology(hass: HomeAssistant) -> None:
    """Register static assets and websocket command (called once at startup)."""
    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get("topology_registered"):
        return

    static_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(TOPOLOGY_STATIC_URL, str(static_dir), cache_headers=False)]
    )
    websocket_api.async_register_command(hass, websocket_get_topology)
    hass.data[DOMAIN]["topology_registered"] = True


async def async_register_topology_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register or update the topology panel with per-entry sidebar setting."""
    if hass.data[DOMAIN].get("topology_panel_registered"):
        return

    # Remove stale panel if left over from a previous unload
    hass.data.setdefault("frontend_panels", {}).pop(TOPOLOGY_PANEL_PATH, None)

    show_in_sidebar = entry.options.get(
        CONF_ENABLE_TOPOLOGY_PANEL,
        entry.data.get(CONF_ENABLE_TOPOLOGY_PANEL, DEFAULT_ENABLE_TOPOLOGY_PANEL),
    )

    kwargs = {
        "frontend_url_path": TOPOLOGY_PANEL_PATH,
        "webcomponent_name": TOPOLOGY_WEBCOMPONENT,
        "module_url": TOPOLOGY_PANEL_MODULE_URL,
        "require_admin": True,
        "config": {"domain": DOMAIN, "title": "OpenWrt Topology"},
    }
    if show_in_sidebar:
        kwargs["sidebar_title"] = "OpenWrt Topology"
        kwargs["sidebar_icon"] = "mdi:graph"

    try:
        await panel_custom.async_register_panel(hass, **kwargs)
    except Exception as exc:
        _LOGGER.warning("Failed to register topology panel: %s", exc)
    hass.data[DOMAIN]["topology_panel_registered"] = True


def _device_id_for_identifier(
    device_registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
) -> str | None:
    """Look up a Home Assistant device id by integration identifier."""
    device = device_registry.async_get_device(identifiers={identifier})
    return device.id if device else None


def _format_device_label(device_data: dict, fallback: str) -> str:
    """Build a readable device label."""
    hostname = device_data.get("hostname")
    ip_address = device_data.get("ip_address")

    if hostname and hostname not in ("*", fallback, fallback.upper()):
        # If hostname looks like an IP address, return it as-is
        if hostname.replace(".", "").isdigit() or ":" in hostname:
            return hostname
        return hostname.split(".", 1)[0]
    if ip_address and ip_address != "Unknown IP":
        return ip_address
    return fallback.replace(":", "")


def _infer_ap_band(ap_data: dict) -> str | None:
    """Infer Wi-Fi band from AP info."""
    channel = ap_data.get("channel")
    frequency = ap_data.get("frequency")

    try:
        if frequency is not None:
            freq = int(float(frequency))
            if 2400 <= freq < 2500:
                return "2.4G"
            if 4900 <= freq < 5900:
                return "5G"
            if 5900 <= freq < 7100:
                return "6G"
    except (TypeError, ValueError):
        pass

    try:
        if channel is not None:
            ch = int(channel)
            if 1 <= ch <= 14:
                return "2.4G"
            if 36 <= ch <= 177:
                return "5G"
    except (TypeError, ValueError):
        pass

    return None


def _normalize_ap_identifier(value: str | None) -> str:
    """Normalize AP identifiers across iwinfo and hostapd naming."""
    if not value:
        return ""

    normalized = value.strip().lower()
    if normalized.startswith("hostapd.") or normalized.startswith("hostapd:"):
        normalized = normalized.split(".", 1)[-1].split(":", 1)[-1]

    if "." in normalized and normalized.startswith("radio"):
        normalized = normalized.split(".", 1)[-1]

    return normalized


def _merge_with_previous_topology(
    previous_graph: dict | None,
    nodes: list[dict],
    edges: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Keep recently missing devices visible as offline in topology."""
    if not previous_graph:
        return nodes, edges

    now_ts = datetime.now().timestamp()
    existing_node_ids = {node["id"] for node in nodes}
    previous_nodes = {node["id"]: node for node in previous_graph.get("nodes", [])}
    previous_edges = previous_graph.get("edges", [])

    merged_nodes = list(nodes)
    merged_edges = list(edges)
    edge_keys = {(edge["source"], edge["target"], edge.get("kind")) for edge in edges}

    for node_id, old_node in previous_nodes.items():
        if node_id in existing_node_ids or old_node.get("type") in ("router", "ap"):
            continue

        last_seen = old_node.get("topology_last_seen")
        if last_seen is None or now_ts - last_seen > TOPOLOGY_OFFLINE_GRACE_SECONDS:
            continue

        offline_node = dict(old_node)
        offline_node["status"] = "offline"
        offline_node["connected"] = False
        merged_nodes.append(offline_node)

        for edge in previous_edges:
            if edge.get("target") != node_id:
                continue
            edge_key = (edge.get("source"), edge.get("target"), edge.get("kind"))
            if edge_key in edge_keys:
                continue
            offline_edge = dict(edge)
            offline_edge["active"] = False
            merged_edges.append(offline_edge)
            edge_keys.add(edge_key)

    return merged_nodes, merged_edges


async def _build_entry_topology(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Build topology data for a single config entry."""
    data_manager = hass.data.get(DOMAIN, {}).get(f"data_manager_{entry.entry_id}")
    previous_graph = hass.data.get(DOMAIN, {}).get("topology_cache", {}).get(entry.entry_id)
    if data_manager is None:
        return {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "host": entry.data[CONF_HOST],
            "nodes": [],
            "edges": [],
            "summary": {"routers": 0, "aps": 0, "wireless": 0, "wired": 0, "online": 0},
        }

    combined = await data_manager.get_combined_data(["ap_info", "device_statistics", "wired_devices"])
    ap_info = combined.get("ap_info", {}) or {}
    wireless_devices = combined.get("device_statistics", {}) or {}
    wired_devices = combined.get("wired_devices", {}) or {}

    device_registry = dr.async_get(hass)
    host = entry.data[CONF_HOST]
    router_node_id = f"router:{entry.entry_id}"
    now_ts = datetime.now().timestamp()

    nodes: list[dict] = [
        {
            "id": router_node_id,
            "type": "router",
            "label": entry.title or host,
            "secondary": host,
            "status": "online",
            "device_id": _device_id_for_identifier(device_registry, (DOMAIN, host)),
        }
    ]
    edges: list[dict] = []

    ap_node_ids: dict[str, str] = {}
    ap_node_ids_normalized: dict[str, str] = {}
    for ap_device, ap_data in sorted(ap_info.items()):
        ap_node_id = f"ap:{entry.entry_id}:{ap_device}"
        ap_node_ids[ap_device] = ap_node_id
        ap_node_ids_normalized[_normalize_ap_identifier(ap_device)] = ap_node_id
        ssid = ap_data.get("ssid") or ap_data.get("device_name") or ap_device
        band = _infer_ap_band(ap_data)
        mode = ap_data.get("mode") or "AP"
        label = f"{ssid} {band}" if band else ssid
        secondary = f"{ap_device} | {mode}"
        if band:
            secondary = f"{secondary} | {band}"
        nodes.append(
            {
                "id": ap_node_id,
                "type": "ap",
                "label": label,
                "secondary": secondary,
                "status": "online",
                "topology_last_seen": now_ts,
                "device_id": _device_id_for_identifier(device_registry, (DOMAIN, f"{host}_ap_{ap_device}")),
            }
        )
        edges.append(
            {
                "source": router_node_id,
                "target": ap_node_id,
                "kind": "uplink",
                "label": ap_data.get("channel"),
                "active": True,
            }
        )

    wireless_count = 0
    wired_count = 0
    online_count = 1

    for mac, device_data in sorted(wireless_devices.items()):
        node_id = f"device:{entry.entry_id}:{mac}"
        connected = bool(device_data.get("connected", False))
        status = "online" if connected else "offline"
        if connected:
            wireless_count += 1
            online_count += 1

        ap_device = device_data.get("ap_device")
        target_id = ap_node_ids.get(ap_device)
        if not target_id:
            target_id = ap_node_ids_normalized.get(_normalize_ap_identifier(ap_device))
        if not target_id:
            target_id = router_node_id
        signal = device_data.get("signal")
        nodes.append(
            {
                "id": node_id,
                "type": "wireless_device",
                "label": _format_device_label(device_data, mac),
                "secondary": mac,
                "mac": mac,
                "hostname": device_data.get("hostname"),
                "ip_address": device_data.get("ip_address"),
                "status": status,
                "connected": connected,
                "signal": signal,
                "topology_last_seen": now_ts,
                "device_id": _device_id_for_identifier(device_registry, (DOMAIN, mac)),
            }
        )
        edges.append(
            {
                "source": target_id,
                "target": node_id,
                "kind": "wireless",
                "label": f"{signal} dBm" if signal is not None else None,
                "active": connected,
            }
        )

    for mac, device_data in sorted(wired_devices.items()):
        node_id = f"device:{entry.entry_id}:wired:{mac}"
        connected = bool(device_data.get("connected", False))
        status = "online" if connected else "offline"
        if connected:
            wired_count += 1
            online_count += 1

        interface = device_data.get("interface") or "LAN"
        confidence = device_data.get("confidence")
        nodes.append(
            {
                "id": node_id,
                "type": "wired_device",
                "label": _format_device_label(device_data, mac),
                "secondary": f"{mac} | {interface}",
                "mac": mac,
                "hostname": device_data.get("hostname"),
                "ip_address": device_data.get("ip_address"),
                "status": status,
                "connected": connected,
                "confidence": confidence,
                "topology_last_seen": now_ts,
                "device_id": _device_id_for_identifier(device_registry, (DOMAIN, mac)),
            }
        )
        edges.append(
            {
                "source": router_node_id,
                "target": node_id,
                "kind": "wired",
                "label": interface,
                "active": connected,
            }
        )

    nodes, edges = _merge_with_previous_topology(previous_graph, nodes, edges)
    graph = {
        "entry_id": entry.entry_id,
        "title": entry.title or host,
        "host": host,
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "routers": 1,
            "aps": len(ap_node_ids),
            "wireless": wireless_count,
            "wired": wired_count,
            "online": online_count,
        },
    }
    hass.data.setdefault(DOMAIN, {}).setdefault("topology_cache", {})[entry.entry_id] = graph
    return graph


@websocket_api.websocket_command(
    {vol.Required("type"): TOPOLOGY_WS_TYPE, vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def websocket_get_topology(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return topology graph data."""
    entry_id = msg.get("entry_id")
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]

    graphs = []
    for entry in entries:
        try:
            graphs.append(await _build_entry_topology(hass, entry))
        except Exception as exc:
            _LOGGER.error("Failed building topology for %s: %s", entry.entry_id, exc)

    connection.send_result(msg["id"], {"graphs": graphs})
