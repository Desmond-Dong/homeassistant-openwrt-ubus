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
