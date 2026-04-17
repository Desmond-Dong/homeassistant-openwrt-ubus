const navigateToPath = (path) => {
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
      script.src = "/openwrt_ubus_static/vis-network.min.js";
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
    this._zeroconfDiscoveries = [];
    this._zeroconfUnsubscribe = null;
    this._ssdpDiscoveries = [];
    this._ssdpUnsubscribe = null;
    this._currentRenderedGraph = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._render();
      this._fetchData();
    }
    this._ensureZeroconfSubscription();
    this._ensureSSDPSubscription();
  }

  connectedCallback() {}

  disconnectedCallback() {
    this._disposeNetwork();
    if (this._zeroconfUnsubscribe) {
      this._zeroconfUnsubscribe();
      this._zeroconfUnsubscribe = null;
    }
    if (this._ssdpUnsubscribe) {
      this._ssdpUnsubscribe();
      this._ssdpUnsubscribe = null;
    }
  }

  _mergeDiscoveryByKey(currentItems, event, getKey) {
    const next = [...currentItems];

    for (const item of event.add || []) {
      const index = next.findIndex((existing) => getKey(existing) === getKey(item));
      if (index === -1) {
        next.push(item);
      } else {
        next[index] = item;
      }
    }

    for (const item of event.change || []) {
      const index = next.findIndex((existing) => getKey(existing) === getKey(item));
      if (index !== -1) {
        next[index] = item;
      }
    }

    for (const item of event.remove || []) {
      const index = next.findIndex((existing) => getKey(existing) === getKey(item));
      if (index !== -1) {
        next.splice(index, 1);
      }
    }

    return next;
  }

  async _ensureZeroconfSubscription() {
    if (!this._hass?.connection || this._zeroconfUnsubscribe) {
      return;
    }

    try {
      this._zeroconfUnsubscribe = await this._hass.connection.subscribeMessage(
        (event) => {
          this._zeroconfDiscoveries = this._mergeDiscoveryByKey(
            this._zeroconfDiscoveries,
            event,
            (item) => item.name
          );
          if (!this._loading && !this._error) {
            this._render();
          }
        },
        { type: "zeroconf/subscribe_discovery" }
      );
    } catch (err) {
      this._error = `Failed to subscribe to zeroconf discovery: ${err?.message || err}`;
      this._render();
    }
  }

  async _ensureSSDPSubscription() {
    if (!this._hass?.connection || this._ssdpUnsubscribe) {
      return;
    }

    try {
      this._ssdpUnsubscribe = await this._hass.connection.subscribeMessage(
        (event) => {
          this._ssdpDiscoveries = this._mergeDiscoveryByKey(
            this._ssdpDiscoveries,
            event,
            (item) => `${item.ssdp_st || ""}|${item.ssdp_location || ""}`
          );
          if (!this._loading && !this._error) {
            this._render();
          }
        },
        { type: "ssdp/subscribe_discovery" }
      );
    } catch (err) {
      this._error = `Failed to subscribe to SSDP discovery: ${err?.message || err}`;
      this._render();
    }
  }

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

  _normalizeHostname(value) {
    if (!value || value === "*" || value === "Unknown IP") {
      return "";
    }
    return String(value).trim().toLowerCase().replace(/\.$/, "").replace(/\.local$/, "").split(".")[0];
  }

  _serviceInstanceName(discovery) {
    const suffix = `.${discovery.type}`;
    return discovery.name.endsWith(suffix) ? discovery.name.slice(0, -suffix.length) : discovery.name;
  }

  _extractIPsFromUrl(url) {
    if (!url) {
      return [];
    }
    try {
      return [new URL(url).hostname].filter(Boolean);
    } catch (_err) {
      return [];
    }
  }

  _escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  _formatTitle(details) {
    return details
      .filter(Boolean)
      .map((detail) => this._escapeHtml(detail))
      .join("<br>");
  }

  _discoveryHostnames(discovery) {
    const properties = discovery.properties || {};
    const candidates = [
      this._serviceInstanceName(discovery),
      properties.host,
      properties.hostname,
      properties.name,
      properties.fn,
    ];
    return [...new Set(candidates.map((value) => this._normalizeHostname(value)).filter(Boolean))];
  }

  _addIndexValue(index, key, node) {
    if (!key) {
      return;
    }
    const current = index.get(key) || [];
    current.push(node);
    index.set(key, current);
  }

  _uniqueIndexedNode(index, keys) {
    for (const key of keys) {
      const matches = index.get(key) || [];
      if (matches.length === 1) {
        return matches[0];
      }
    }
    return null;
  }

  _graphWithZeroconf(graph) {
    if (!graph || !this._zeroconfDiscoveries.length) {
      return graph;
    }

    const deviceNodes = graph.nodes.filter(
      (node) => node.type === "wired_device" || node.type === "wireless_device"
    );
    const ipIndex = new Map();
    const hostnameIndex = new Map();

    for (const node of deviceNodes) {
      this._addIndexValue(ipIndex, node.ip_address, node);
      this._addIndexValue(hostnameIndex, this._normalizeHostname(node.hostname), node);
      this._addIndexValue(hostnameIndex, this._normalizeHostname(node.label), node);
    }

    const extraNodes = [];
    const extraEdges = [];

    for (const discovery of this._zeroconfDiscoveries) {
      const parentNode =
        this._uniqueIndexedNode(ipIndex, discovery.ip_addresses || []) ||
        this._uniqueIndexedNode(hostnameIndex, this._discoveryHostnames(discovery));

      if (!parentNode) {
        continue;
      }

      const nodeId = `protocol:zeroconf:${parentNode.id}:${discovery.name}`;
      const serviceName = this._serviceInstanceName(discovery);
      extraNodes.push({
        id: nodeId,
        type: "protocol_zeroconf",
        label: `${serviceName} (zeroconf)`,
        secondary: [discovery.type, ...(discovery.ip_addresses || [])].filter(Boolean).join(" | "),
        details: [
          discovery.type ? `Service: ${discovery.type}` : null,
          discovery.port ? `Port: ${discovery.port}` : null,
          discovery.ip_addresses?.length ? `IPs: ${discovery.ip_addresses.join(", ")}` : null,
          discovery.properties?.host ? `Host: ${discovery.properties.host}` : null,
          discovery.properties?.hostname ? `Hostname: ${discovery.properties.hostname}` : null,
          discovery.properties?.fn ? `Friendly name: ${discovery.properties.fn}` : null,
        ],
        discovery_type: discovery.type,
        port: discovery.port,
        status: "online",
      });
      extraEdges.push({
        source: parentNode.id,
        target: nodeId,
        kind: "protocol_zeroconf",
        active: parentNode.connected !== false,
      });
    }

    if (!extraNodes.length) {
      return graph;
    }

    return {
      ...graph,
      nodes: [...graph.nodes, ...extraNodes],
      edges: [...graph.edges, ...extraEdges],
    };
  }

  _ssdpProtocolTag(discovery) {
    const values = [
      discovery.ssdp_st,
      discovery.ssdp_nt,
      discovery.name,
      discovery.upnp?.deviceType,
      discovery.upnp?.friendlyName,
      discovery.ssdp_server,
    ]
      .filter(Boolean)
      .map((value) => String(value).toLowerCase());

    if (values.some((value) => value.includes("dlna"))) {
      return "dlna/ssdp";
    }
    if (values.some((value) => value.includes("upnp"))) {
      return "upnp/ssdp";
    }
    return "ssdp";
  }

  _ssdpDisplayName(discovery) {
    return (
      discovery.name ||
      discovery.upnp?.friendlyName ||
      discovery.upnp?.modelName ||
      discovery.upnp?.deviceType ||
      discovery.ssdp_st ||
      "SSDP service"
    );
  }

  _ssdpHostnames(discovery) {
    const candidates = [
      discovery.name,
      discovery.upnp?.friendlyName,
      discovery.upnp?.modelName,
      discovery.ssdp_headers?.HOST,
      discovery.ssdp_headers?.Host,
      discovery.ssdp_headers?.host,
    ];
    return [...new Set(candidates.map((value) => this._normalizeHostname(value)).filter(Boolean))];
  }

  _graphWithSSDP(graph) {
    if (!graph || !this._ssdpDiscoveries.length) {
      return graph;
    }

    const deviceNodes = graph.nodes.filter(
      (node) => node.type === "wired_device" || node.type === "wireless_device"
    );
    const ipIndex = new Map();
    const hostnameIndex = new Map();

    for (const node of deviceNodes) {
      this._addIndexValue(ipIndex, node.ip_address, node);
      this._addIndexValue(hostnameIndex, this._normalizeHostname(node.hostname), node);
      this._addIndexValue(hostnameIndex, this._normalizeHostname(node.label), node);
    }

    const extraNodes = [];
    const extraEdges = [];

    for (const discovery of this._ssdpDiscoveries) {
      const locationIPs = [
        ...this._extractIPsFromUrl(discovery.ssdp_location),
        ...(discovery.ssdp_all_locations || []).flatMap((url) => this._extractIPsFromUrl(url)),
      ];

      const parentNode =
        this._uniqueIndexedNode(ipIndex, locationIPs) ||
        this._uniqueIndexedNode(hostnameIndex, this._ssdpHostnames(discovery));

      if (!parentNode) {
        continue;
      }

      const tag = this._ssdpProtocolTag(discovery);
      const displayName = this._ssdpDisplayName(discovery);
      const nodeId = `protocol:ssdp:${parentNode.id}:${discovery.ssdp_st || "unknown"}:${discovery.ssdp_location || displayName}`;
      extraNodes.push({
        id: nodeId,
        type: "protocol_ssdp",
        label: `${displayName} (${tag})`,
        secondary: [discovery.ssdp_st, discovery.ssdp_location].filter(Boolean).join(" | "),
        details: [
          discovery.ssdp_st ? `ST: ${discovery.ssdp_st}` : null,
          discovery.ssdp_nt ? `NT: ${discovery.ssdp_nt}` : null,
          discovery.ssdp_location ? `Location: ${discovery.ssdp_location}` : null,
          discovery.upnp?.deviceType ? `Device type: ${discovery.upnp.deviceType}` : null,
          discovery.upnp?.friendlyName ? `Friendly name: ${discovery.upnp.friendlyName}` : null,
          discovery.ssdp_server ? `Server: ${discovery.ssdp_server}` : null,
        ],
        discovery_type: discovery.ssdp_st,
        status: "online",
      });
      extraEdges.push({
        source: parentNode.id,
        target: nodeId,
        kind: "protocol_ssdp",
        active: parentNode.connected !== false,
      });
    }

    if (!extraNodes.length) {
      return graph;
    }

    return {
      ...graph,
      nodes: [...graph.nodes, ...extraNodes],
      edges: [...graph.edges, ...extraEdges],
    };
  }

  _buildVisData(graph) {
    const style = getComputedStyle(this);
    const apColor = style.getPropertyValue("--info-color").trim() || "#03a9f4";
    const wirelessColor = style.getPropertyValue("--success-color").trim() || "#4caf50";
    const wiredColor = style.getPropertyValue("--warning-color").trim() || "#ff9800";
    const protocolColor = style.getPropertyValue("--secondary-text-color").trim() || "#9e9e9e";

    const nodes = graph.nodes.map((node) => {
      const details = [node.secondary];
      if (Array.isArray(node.details)) {
        details.push(...node.details);
      }
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
        title: this._formatTitle(details),
        physics: true,
        shape: node.type === "protocol_zeroconf" || node.type === "protocol_ssdp" ? "box" : undefined,
        size: node.type === "protocol_zeroconf" || node.type === "protocol_ssdp" ? 14 : undefined,
        font: node.type === "protocol_zeroconf" || node.type === "protocol_ssdp" ? { size: 14 } : undefined,
        color:
          node.type === "ap"
            ? apColor
            : node.type === "wired_device"
              ? wiredColor
              : node.type === "wireless_device"
                ? wirelessColor
                : node.type === "protocol_zeroconf" || node.type === "protocol_ssdp"
                  ? protocolColor
                  : undefined,
        rawNode: node,
      };
    });

    const edges = graph.edges.map((edge, index) => ({
      id: `${edge.source}-${edge.target}-${index}`,
      from: edge.source,
      to: edge.target,
      label: edge.label || undefined,
      dashes: edge.kind === "protocol_zeroconf" || edge.kind === "protocol_ssdp",
      color:
        edge.kind === "protocol_zeroconf" || edge.kind === "protocol_ssdp"
          ? { color: protocolColor }
          : undefined,
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
      const mergedGraph = this._graphWithSSDP(this._graphWithZeroconf(graph));
      this._currentRenderedGraph = mergedGraph;
      const data = this._buildVisData(mergedGraph);
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
          const clickedNode = this._currentRenderedGraph?.nodes.find((node) => node.id === nodeId);
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
