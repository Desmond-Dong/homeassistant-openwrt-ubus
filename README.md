# OpenWrt ubus for Home Assistant

**English Version** | [中文版本](README_zh.md)

`openwrt_ubus` is a Home Assistant custom integration for OpenWrt routers. It connects to OpenWrt over ubus JSON-RPC and exposes router status, wireless access point data, connected clients, optional wired clients, service controls, and an integration-native topology page.

## Overview

This fork focuses on practical day-to-day monitoring and control:

- Router system sensors
- AP and station sensors
- Optional QModem sensors
- Optional network interface sensors
- Per-device `device_tracker` entities
- Optional wired client tracking
- Service switches and control buttons
- Wireless client kick buttons
- A built-in topology page for this integration

The topology page is not a Lovelace card. It is registered by the integration itself and can be opened from the integration options menu or directly at `/openwrt-ubus-topology`.

## Features

### Device tracking

- Tracks wireless clients from `iwinfo` or `hostapd`
- Can also track wired clients from ARP and neighbor data when enabled
- Creates Home Assistant `device_tracker` entities per client MAC
- Keeps AP relationships through `via_device`
- Uses client naming in this order: hostname, client IP address, MAC
- Preserves recently-seen wireless clients as offline instead of immediately misclassifying them as wired when stale ARP data lingers

### Sensors

- System sensors: uptime, load, memory, board data, temperatures, conntrack, DHCP count and more
- AP sensors: SSID, channel, frequency, mode, bitrate, signal/quality and related wireless metrics
- STA sensors: per-client wireless metrics such as RSSI, bitrate, connection time and AP association
- ETH/interface sensors: bridge, Ethernet, DSA and other network interface counters and status
- QModem sensors when `modem_ctrl` is available

### Service management

- Lists procd-managed services from `rc`
- Creates switch entities for selected services
- Creates buttons for start, stop, restart, enable and disable actions

### Wireless client control

- Optional per-client kick buttons for `hostapd`-managed wireless clients
- Intended for AP mode interfaces that expose `hostapd.*` ubus methods

### Topology page

- Built-in graph page for router, APs, wireless clients and wired clients
- Keeps recently-missing clients visible as offline for a short grace period
- Clickable main nodes that open the corresponding Home Assistant device page
- Discovery overlays for `zeroconf` and `ssdp`
- Protocol nodes are attached behind the matched client node and shown with dashed edges
- Discovery matching is conservative: first by IP, then by hostname, and only when the match is unique

## Requirements

Your OpenWrt router should provide ubus over HTTP or HTTPS.

Typical required packages:

```sh
opkg install rpcd uhttpd uhttpd-mod-ubus luci-app-uhttpd
```

Optional packages:

```sh
opkg install hostapd
```

Required services:

```sh
/etc/init.d/rpcd enable
/etc/init.d/rpcd start
/etc/init.d/uhttpd enable
/etc/init.d/uhttpd start
```

For best results, the Home Assistant user you use for this integration should have permission to:

- call ubus objects such as `system`, `iwinfo`, `hostapd.*`, `rc`, `uci`, `dhcp`, `network.device`, `luci-rpc`, `modem_ctrl`
- read files such as DHCP lease files when hostname resolution depends on them

## Installation

### HACS

Add this repository as a custom integration repository in HACS:

`https://github.com/Desmond-Dong/homeassistant-openwrt-ubus`

Then install `OpenWrt ubus`, restart Home Assistant, and add the integration from `Settings -> Devices & Services`.

### Manual

Clone or download this repository and copy `custom_components/openwrt_ubus` into your Home Assistant `custom_components` directory.

```sh
git clone https://github.com/Desmond-Dong/homeassistant-openwrt-ubus.git
```

Restart Home Assistant and add the integration from `Settings -> Devices & Services`.

## Router ACL example

If hostnames, file reads or service controls are missing because of permissions, grant the router user the needed ACLs.

Example full-access ACL for a trusted admin user:

```json
{
  "root": {
    "description": "Root user full access to ubus",
    "read": {
      "ubus": {
        "*": ["*"]
      }
    },
    "write": {
      "ubus": {
        "*": ["*"]
      }
    }
  }
}
```

Save it under `/usr/share/rpcd/acl.d/root.json`, then restart `rpcd` and `uhttpd`.

## Configuration

### Connection options

| Option | Meaning | Default |
| --- | --- | --- |
| `host` | OpenWrt hostname or IP | required |
| `username` | Router username | required |
| `password` | Router password | required |
| `use_https` | Use HTTPS instead of HTTP | `false` |
| `verify_ssl` | Verify router certificate | `false` |
| `cert_path` | Optional client certificate path | empty |
| `port` | Custom ubus web port | `80` or `443` |
| `endpoint` | ubus URL path | `ubus` |
| `tracking_method` | Device tracker unique ID strategy | `combined` |
| `wireless_software` | Wireless data source | `iwinfo` |
| `dhcp_software` | DHCP/lease source for hostnames and IPs | `dnsmasq` |

### Feature toggles

| Option | Meaning | Default |
| --- | --- | --- |
| `enable_system_sensors` | System/router sensors | `true` |
| `enable_qmodem_sensors` | QModem sensors | `true` |
| `enable_sta_sensors` | Per-client wireless sensors | `true` |
| `enable_ap_sensors` | AP sensors | `true` |
| `enable_eth_sensors` | Network interface sensors | `true` |
| `enable_service_controls` | Service switches and buttons | `false` |
| `enable_device_kick_buttons` | Wireless kick buttons | `false` |
| `enable_wired_tracking` | Track wired LAN clients | `false` |

### Timeout options

| Option | Meaning | Default |
| --- | --- | --- |
| `system_sensor_timeout` | System and interface polling timeout | `30` |
| `qmodem_sensor_timeout` | QModem polling timeout | `120` |
| `sta_sensor_timeout` | STA polling timeout | `30` |
| `ap_sensor_timeout` | AP polling timeout | `60` |
| `service_timeout` | Service status/action timeout | `30` |

### Service selection

When service controls are enabled, open the integration options and use the `Services` step to pick which OpenWrt services should create Home Assistant entities.

## Topology page

Open the topology page from:

- `Settings -> Devices & Services -> OpenWrt ubus -> Configure -> Topology`
- or `/openwrt-ubus-topology`

The graph shows:

- the router node
- AP nodes
- wireless client nodes
- wired client nodes
- optional `zeroconf` and `ssdp` discovery nodes attached behind clients

Protocol overlays are informational only. Main client nodes keep their normal device navigation behavior.

## Notes on client naming

Client names are intentionally simple and stable:

- use the client hostname when available
- otherwise use the client IP address
- otherwise fall back to the MAC address without separators

If your clients still show MACs, the usual cause is that Home Assistant cannot read DHCP lease or host hint information because of OpenWrt ACL restrictions.

## Notes on wired tracking

Wired tracking is based on ARP and neighbor information from the router. That means:

- it is useful, but not as authoritative as active wireless station data
- stale ARP entries can briefly outlive real connectivity
- this fork uses conservative fallback logic to avoid incorrectly flipping a recently wireless client to wired too quickly

## Troubleshooting

### Cannot connect

- Confirm `rpcd` and `uhttpd` are running
- Confirm the URL is correct, including protocol, port and endpoint
- If you use HTTPS with a self-signed certificate, leave `verify_ssl` disabled unless you have a working certificate setup

### Devices only show MACs

- Check DHCP lease access and host hints on the router
- Check OpenWrt ACLs for the integration user
- Make sure the selected `dhcp_software` matches your router setup

### Service entities do not appear

- Enable service controls in the integration options
- Open the `Services` step and select at least one service
- Verify `rc` ubus access permissions

### Kick buttons do not appear

- Install and run `hostapd`
- Make sure the wireless side is configured to use `hostapd`
- Verify `hostapd.*` ubus methods are visible and allowed

### Topology page is empty or partial

- Open the integration once so its data manager is active
- Confirm AP and client data sources are enabled
- Check Home Assistant logs for ubus permission or parsing errors

## Debug logging

```yaml
logger:
  default: warning
  logs:
    custom_components.openwrt_ubus: debug
    custom_components.openwrt_ubus.shared_data_manager: debug
    custom_components.openwrt_ubus.extended_ubus: debug
    custom_components.openwrt_ubus.device_tracker: debug
```

## Project layout

```text
custom_components/openwrt_ubus/
|- __init__.py
|- config_flow.py
|- const.py
|- device_tracker.py
|- sensor.py
|- switch.py
|- button.py
|- topology.py
|- shared_data_manager.py
|- extended_ubus.py
|- ubus_client.py
|- buttons/
|- sensors/
|- frontend/
`- Ubus/
```

## License

This project is licensed under MPL-2.0. See `LICENSE`.
