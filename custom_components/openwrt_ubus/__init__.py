"""The ubus component for OpenWrt."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging

from aiohttp import web
import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DHCP_SOFTWARE,
    CONF_WIRELESS_SOFTWARE,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    CONF_CERT_PATH,
    CONF_PORT,
    CONF_ENDPOINT,
    CONF_ENABLE_QMODEM_SENSORS,
    CONF_ENABLE_STA_SENSORS,
    CONF_ENABLE_SYSTEM_SENSORS,
    CONF_ENABLE_AP_SENSORS,
    DEFAULT_DHCP_SOFTWARE,
    DEFAULT_WIRELESS_SOFTWARE,
    DEFAULT_USE_HTTPS,
    DEFAULT_VERIFY_SSL,
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_ENDPOINT,
    DEFAULT_ENABLE_QMODEM_SENSORS,
    DEFAULT_ENABLE_STA_SENSORS,
    DEFAULT_ENABLE_SYSTEM_SENSORS,
    DEFAULT_ENABLE_AP_SENSORS,
    DHCP_SOFTWARES,
    DOMAIN,
    PLATFORMS,
    WIRELESS_SOFTWARES,
    build_ubus_url,
    get_config_value,
)
from .shared_data_manager import SharedUbusDataManager
from .ubus_client import create_enhanced_extended_ubus_client
from .security_utils import CredentialManager, safe_log_data

_LOGGER = logging.getLogger(__name__)

TOPOLOGY_PANEL_PATH = "openwrt-ubus-topology"
TOPOLOGY_PANEL_MODULE_URL = "/api/openwrt_ubus/topology-panel.js"
TOPOLOGY_WEBCOMPONENT = "openwrt-ubus-topology-panel"
TOPOLOGY_WS_TYPE = "openwrt_ubus/topology"
TOPOLOGY_OFFLINE_GRACE_SECONDS = 90

TOPOLOGY_PANEL_JS = r'''const navigateToPath = (path) => {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new Event("location-changed", { bubbles: true, composed: true }));
};

let visScriptPromise;

const ensureVis = async () => {
  if (window.vis?.Network) {
    return window.vis;
  }
  if (!visScriptPromise) {
    visScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js";
      script.onload = () => resolve(window.vis);
      script.onerror = () => reject(new Error("Unable to load vis-network"));
      document.head.appendChild(script);
    });
  }
  return visScriptPromise;
};

class OpenWrtUbusTopologyPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._graphs = [];
    this._selectedEntryId = null;
    this._loading = false;
    this._error = null;
    this._resizeObserver = null;
    this._network = null;
    this._shouldFit = true;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._render();
      this._fetchData();
    }
  }

  connectedCallback() {}

  disconnectedCallback() {
    this._disposeNetwork();
  }

  _startRefresh() {}

  async _fetchData() {
    if (!this._hass) {
      return;
    }
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.callWS({ type: "openwrt_ubus/topology" });
      this._graphs = result.graphs || [];
      if (!this._selectedEntryId && this._graphs.length) {
        this._selectedEntryId = this._graphs[0].entry_id;
      }
      this._shouldFit = true;
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _disposeNetwork() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
    if (this._network) {
      this._network.destroy();
      this._network = null;
    }
  }

  _selectedGraph() {
    return this._graphs.find((graph) => graph.entry_id === this._selectedEntryId) || this._graphs[0] || null;
  }

  _buildVisData(graph) {
    const style = getComputedStyle(this);
    const apColor = style.getPropertyValue("--info-color").trim() || "#03a9f4";
    const wirelessColor = style.getPropertyValue("--success-color").trim() || "#4caf50";
    const wiredColor = style.getPropertyValue("--warning-color").trim() || "#ff9800";

    const nodes = graph.nodes.map((node) => {
      const details = [node.secondary];
      if (node.signal !== undefined && node.signal !== null) {
        details.push(`Signal: ${node.signal} dBm`);
      }
      if (node.confidence) {
        details.push(`Confidence: ${node.confidence}`);
      }
      if (node.status) {
        details.push(`Status: ${node.status}`);
      }

      return {
        id: node.id,
        label: node.label,
        title: details.filter(Boolean).join("<br>"),
        physics: true,
        color:
          node.type === "ap"
            ? apColor
            : node.type === "wired_device"
              ? wiredColor
              : node.type === "wireless_device"
                ? wirelessColor
                : undefined,
        rawNode: node,
      };
    });

    const edges = graph.edges.map((edge, index) => ({
      id: `${edge.source}-${edge.target}-${index}`,
      from: edge.source,
      to: edge.target,
      label: edge.label || undefined,
    }));

    return { nodes, edges };
  }

  async _renderNetwork() {
    const graph = this._selectedGraph();
    const container = this.shadowRoot?.getElementById("network");
    if (!graph || !container) {
      return;
    }

    try {
      const vis = await ensureVis();
      const data = this._buildVisData(graph);
      const options = {
        autoResize: true,
        physics: {
          enabled: true,
          stabilization: {
            enabled: true,
            iterations: 150,
            updateInterval: 25,
            fit: true,
          },
          barnesHut: {
            gravitationalConstant: -5000,
            centralGravity: 0.18,
            springLength: 220,
            springConstant: 0.05,
            damping: 0.12,
          },
        },
        interaction: {
          dragNodes: true,
          dragView: true,
          zoomView: true,
          hover: true,
          tooltipDelay: 80,
        },
        nodes: {
          shadow: true,
        },
        edges: {
          shadow: true,
        },
      };

      if (!this._network) {
        this._network = new vis.Network(container, data, options);
        this._network.on("click", (params) => {
          if (!params.nodes?.length) {
            return;
          }
          const nodeId = params.nodes[0];
          const clickedNode = graph.nodes.find((node) => node.id === nodeId);
          const deviceId = clickedNode?.device_id;
          if (deviceId) {
            navigateToPath(`/config/devices/device/${deviceId}`);
          }
        });
      } else {
        this._network.setData(data);
        this._network.setOptions(options);
      }

      if (this._shouldFit) {
        this._network.fit({ animation: { duration: 250, easingFunction: "easeInOutQuad" } });
        this._shouldFit = false;
      }

      if (!this._resizeObserver) {
        this._resizeObserver = new ResizeObserver(() => {
          if (this._network) {
            this._network.redraw();
          }
        });
      }
      this._resizeObserver.disconnect();
      this._resizeObserver.observe(container);
    } catch (err) {
      this._error = `Failed to load topology renderer: ${err?.message || err}`;
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }

    this._disposeNetwork();

    const graph = this._selectedGraph();
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; }
        .wrap { display:flex; flex-direction:column; gap:16px; padding:12px 16px 16px; height:100%; box-sizing:border-box; color:var(--primary-text-color); }
        .header { display:flex; justify-content:space-between; align-items:center; gap:10px; }
        .title { font-size: 22px; font-weight: 600; }
        .toolbar { display:flex; gap:10px; align-items:center; }
        select, .refresh { border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); border-radius:10px; padding:10px 12px; }
        .refresh { cursor:pointer; }
        .panel { flex:1; min-height:calc(100vh - 110px); display:flex; border:1px solid var(--divider-color); border-radius:18px; background:var(--card-background-color); overflow:hidden; }
        .state { padding:32px; color:var(--secondary-text-color); }
        #network { width:100%; height:calc(100vh - 130px); min-height:calc(100vh - 130px); }
      </style>
      <div class="wrap">
        <div class="header">
          <div class="title">OpenWrt Topology</div>
          <div class="toolbar">
            <select id="entrySelect">
              ${this._graphs.map((item) => `<option value="${item.entry_id}" ${item.entry_id === this._selectedEntryId ? "selected" : ""}>${item.title}</option>`).join("")}
            </select>
            <button class="refresh" id="refreshBtn">Refresh</button>
          </div>
        </div>
        <div class="panel">
          ${this._loading ? `<div class="state">Loading topology...</div>` : ""}
          ${this._error ? `<div class="state">${this._error}</div>` : ""}
          ${!this._loading && !this._error && !graph ? `<div class="state">No OpenWrt topology data available.</div>` : ""}
          ${!this._loading && !this._error && graph ? `<div id="network"></div>` : ""}
        </div>
      </div>
    `;

    const select = this.shadowRoot.getElementById("entrySelect");
    if (select) {
      select.addEventListener("change", (event) => {
        this._selectedEntryId = event.target.value;
        this._shouldFit = true;
        this._render();
      });
    }

    const refreshBtn = this.shadowRoot.getElementById("refreshBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", async () => this._fetchData());
    }

    if (!this._loading && !this._error && graph) {
      this._renderNetwork();
    }
  }
}

customElements.define("openwrt-ubus-topology-panel", OpenWrtUbusTopologyPanel);
'''


class OpenWrtTopologyPanelView(HomeAssistantView):
    """Serve the OpenWrt topology panel javascript module."""

    url = TOPOLOGY_PANEL_MODULE_URL
    name = "api:openwrt_ubus:topology_panel"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return the topology panel module."""
        return web.Response(text=TOPOLOGY_PANEL_JS, content_type="application/javascript")


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
    prefixes = (
        "hostapd.",
        "hostapd:",
        "phy",
    )

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
        if node_id in existing_node_ids:
            continue
        if old_node.get("type") in ("router", "ap"):
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
    {
        vol.Required("type"): TOPOLOGY_WS_TYPE,
        vol.Optional("entry_id"): str,
    }
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

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_WIRELESS_SOFTWARE, default=DEFAULT_WIRELESS_SOFTWARE): vol.In(
                    WIRELESS_SOFTWARES
                ),
                vol.Optional(CONF_DHCP_SOFTWARE, default=DEFAULT_DHCP_SOFTWARE): vol.In(
                    DHCP_SOFTWARES
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the openwrt ubus component."""
    hass.data.setdefault(DOMAIN, {})

    if not hass.data[DOMAIN].get("topology_registered"):
        hass.http.register_view(OpenWrtTopologyPanelView)
        websocket_api.async_register_command(hass, websocket_get_topology)
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=TOPOLOGY_PANEL_PATH,
            webcomponent_name=TOPOLOGY_WEBCOMPONENT,
            module_url=TOPOLOGY_PANEL_MODULE_URL,
            require_admin=True,
            config={"domain": DOMAIN, "title": "OpenWrt Topology"},
        )
        hass.data[DOMAIN]["topology_registered"] = True

    if DOMAIN not in config:
        return True

    # Store the configuration for the device tracker
    hass.data[DOMAIN]["config"] = config[DOMAIN]

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up openwrt ubus from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    ubus = None
    # Test connection before setting up platforms
    try:
        # Build URL using utility function
        use_https = get_config_value(entry, CONF_USE_HTTPS, DEFAULT_USE_HTTPS)
        port = get_config_value(
            entry,
            CONF_PORT,
            DEFAULT_HTTPS_PORT if use_https else DEFAULT_HTTP_PORT,
        )
        endpoint = get_config_value(entry, CONF_ENDPOINT, DEFAULT_ENDPOINT)
        url = build_ubus_url(entry.data[CONF_HOST], use_https, port=port, endpoint=endpoint)

        # Configure SSL verification
        verify_ssl = get_config_value(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        cert_path = get_config_value(entry, CONF_CERT_PATH, None)

        # If using HTTPS with unverified SSL, warn user
        if use_https and not verify_ssl:
            _LOGGER.warning(
                "HTTPS enabled with SSL verification disabled - "
                "this is insecure but necessary for self-signed certificates"
            )

        # Create credential manager for secure handling
        credentials = CredentialManager(
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD]
        )

        safe_log_data(credentials.get_connection_info(), "debug", "Testing connection")

        session = async_get_clientsession(hass)
        ubus = create_enhanced_extended_ubus_client(
            url,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            session=session,
            verify_ssl=verify_ssl,
            cert_file=cert_path
        )

        # Test connection
        _LOGGER.debug("Testing connection to %s with user %s", url, entry.data[CONF_USERNAME])
        session_id = await ubus.connect()
        if session_id is None:
            _LOGGER.error("Setup failed: session_id is None for %s", url)
            raise ConfigEntryNotReady(
                f"Failed to connect to OpenWrt device at {
                    entry.data[CONF_HOST]} - session_id is None")

        _LOGGER.info("Successfully connected to OpenWrt device at %s during setup", url)

        # Check for modem_ctrl availability and store the result
        modem_ctrl_available = False
        try:
            modem_ctrl_list = await ubus.list_modem_ctrl()
            modem_ctrl_available = modem_ctrl_list is not None and bool(modem_ctrl_list)
            _LOGGER.debug("Modem_ctrl availability check: %s", modem_ctrl_available)
        except Exception as exc:
            _LOGGER.debug("Modem_ctrl not available: %s", exc)
            modem_ctrl_available = False

        # Store modem_ctrl availability in hass data
        hass.data[DOMAIN]["modem_ctrl_available"] = modem_ctrl_available

        # Create shared data manager
        data_manager = SharedUbusDataManager(hass, entry)
        hass.data[DOMAIN][f"data_manager_{entry.entry_id}"] = data_manager

    except ConnectionRefusedError as exc:
        _LOGGER.error("Setup failed: Connection refused for OpenWrt device at %s", entry.data[CONF_HOST])
        raise ConfigEntryNotReady(
            f"Connection refused - check if OpenWrt device is running "
            f"and accessible at {entry.data[CONF_HOST]}"
        ) from exc
    except asyncio.TimeoutError as exc:
        _LOGGER.error(
            "Setup failed: Connection timeout for OpenWrt device at %s",
            entry.data[CONF_HOST]
        )
        raise ConfigEntryNotReady(
            f"Connection timeout - check network connectivity to "
            f"{entry.data[CONF_HOST]}"
        ) from exc
    except PermissionError as exc:
        _LOGGER.error(
            "Setup failed: Authentication failed for user %s on %s",
            entry.data[CONF_USERNAME], entry.data[CONF_HOST]
        )
        raise ConfigEntryNotReady(
            f"Authentication failed - check username and password for "
            f"{entry.data[CONF_HOST]}"
        ) from exc
    except Exception as exc:
        _LOGGER.exception(
            "Setup failed: Unexpected error connecting to OpenWrt device at %s: %s",
            entry.data[CONF_HOST],
            str(exc)
        )
        raise ConfigEntryNotReady(
            f"Failed to connect to OpenWrt device at {entry.data[CONF_HOST]}: {exc}"
        ) from exc
    finally:
        # Ensure ubus client is always closed to prevent unclosed session warnings
        if ubus is not None:
            try:
                await ubus.close()
            except Exception as exc:
                _LOGGER.debug("Error closing test ubus client: %s", exc)

    # Store the config entry data as a mutable dict
    hass.data[DOMAIN][f"entry_data_{entry.entry_id}"] = dict(entry.data)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Clean up devices for disabled sensors after setting up platforms
    # This ensures devices exist before we try to clean them up
    await _cleanup_disabled_sensor_devices(hass, entry)

    return True


async def _cleanup_disabled_sensor_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up devices for disabled sensor types."""
    device_registry = dr.async_get(hass)
    host = entry.data[CONF_HOST]

    _LOGGER.debug("Starting device cleanup for host: %s", host)

    # Get sensor enable states using utility function
    system_enabled = get_config_value(entry, CONF_ENABLE_SYSTEM_SENSORS, DEFAULT_ENABLE_SYSTEM_SENSORS)
    qmodem_enabled = get_config_value(entry, CONF_ENABLE_QMODEM_SENSORS, DEFAULT_ENABLE_QMODEM_SENSORS)
    sta_enabled = get_config_value(entry, CONF_ENABLE_STA_SENSORS, DEFAULT_ENABLE_STA_SENSORS)
    ap_enabled = get_config_value(entry, CONF_ENABLE_AP_SENSORS, DEFAULT_ENABLE_AP_SENSORS)

    _LOGGER.debug("Sensor states - System: %s, QModem: %s, STA: %s, AP: %s",
                  system_enabled, qmodem_enabled, sta_enabled, ap_enabled)

    # List all current devices for debugging
    all_devices = [device for device in device_registry.devices.values()
                   if any(identifier[0] == DOMAIN for identifier in device.identifiers)]
    _LOGGER.debug("Current devices in registry: %s",
                  [list(device.identifiers) for device in all_devices])

    # If system sensors are disabled, remove the main router device
    # (this will also remove any via_device dependencies like QModem and STA devices)
    if not system_enabled:
        main_device = device_registry.async_get_device(identifiers={(DOMAIN, host)})
        if main_device:
            _LOGGER.info("Removing main router device %s (system sensors disabled)", host)
            device_registry.async_remove_device(main_device.id)
        else:
            _LOGGER.debug("Main router device not found for removal: %s", host)
    else:
        # If system sensors are enabled but QModem sensors are disabled,
        # only remove the QModem device
        if not qmodem_enabled:
            qmodem_identifier = (DOMAIN, f"{host}_qmodem")
            qmodem_device = device_registry.async_get_device(identifiers={qmodem_identifier})
            if qmodem_device:
                _LOGGER.info("Removing QModem device %s (QModem sensors disabled)", f"{host}_qmodem")
                device_registry.async_remove_device(qmodem_device.id)
            else:
                _LOGGER.debug("QModem device not found for removal: %s", f"{host}_qmodem")

        # If STA sensors are disabled, remove all STA devices
        if not sta_enabled:
            removed_count = 0
            for device in list(device_registry.devices.values()):
                if device.via_device_id:
                    via_device = device_registry.devices.get(device.via_device_id)
                    if via_device and (DOMAIN, host) in via_device.identifiers:
                        # Check if it's a STA device by exclusion
                        for identifier in device.identifiers:
                            if identifier[0] == DOMAIN:
                                device_id = identifier[1]
                                # STA devices are identified by MAC address format or
                                # not matching known device patterns
                                is_main_router = device_id == host
                                is_qmodem = "_qmodem" in device_id
                                is_ap_device = "_ap_" in device_id
                                is_network_interface = "_eth" in device_id

                                if not (is_main_router or is_qmodem or is_ap_device or is_network_interface):
                                    _LOGGER.info("Removing STA device %s (STA sensors disabled)", device_id)
                                    device_registry.async_remove_device(device.id)
                                    removed_count += 1
                                    break
            _LOGGER.debug("Removed %d STA devices", removed_count)

        # If AP sensors are disabled, remove all AP devices
        if not ap_enabled:
            removed_count = 0
            for device in list(device_registry.devices.values()):
                for identifier in device.identifiers:
                    if identifier[0] == DOMAIN and "_ap_" in identifier[1]:
                        _LOGGER.info("Removing AP device %s (AP sensors disabled)", identifier[1])
                        device_registry.async_remove_device(device.id)
                        removed_count += 1
                        break
            _LOGGER.debug("Removed %d AP devices", removed_count)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up shared data manager
        data_manager_key = f"data_manager_{entry.entry_id}"
        if DOMAIN in hass.data and data_manager_key in hass.data[DOMAIN]:
            data_manager = hass.data[DOMAIN][data_manager_key]
            try:
                await data_manager.close()
            except Exception as exc:
                _LOGGER.debug("Error closing data manager: %s", exc)
            hass.data[DOMAIN].pop(data_manager_key, None)

        # Clean up coordinators
        if DOMAIN in hass.data and "coordinators" in hass.data[DOMAIN]:
            coordinators = hass.data[DOMAIN]["coordinators"]
            for coordinator in coordinators:
                if hasattr(coordinator, 'async_shutdown'):
                    try:
                        await coordinator.async_shutdown()
                    except Exception as exc:
                        _LOGGER.debug("Error shutting down coordinator: %s", exc)
            # Clear the coordinators list
            hass.data[DOMAIN]["coordinators"] = []

        # Clean up entry-specific data
        hass.data[DOMAIN].pop(f"entry_data_{entry.entry_id}", None)

        # Clean up device kick coordinators
        if "device_kick_coordinators" in hass.data[DOMAIN]:
            hass.data[DOMAIN]["device_kick_coordinators"].pop(entry.entry_id, None)

        # Clean up modem_ctrl availability data if no more entries
        if len([e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]) == 0:
            hass.data[DOMAIN].pop("modem_ctrl_available", None)

    return unload_ok
