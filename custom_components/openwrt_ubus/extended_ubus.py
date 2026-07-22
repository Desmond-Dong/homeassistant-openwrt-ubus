"""Extended Ubus client with specific OpenWrt functionality."""

import logging

from .Ubus import Ubus
from .Ubus.interface import PreparedCall
from .const import (
    API_RPC_CALL,
    API_RPC_LIST,
    API_PARAM_CONFIG,
    API_PARAM_PATH,
    API_PARAM_TYPE,
    API_SUBSYS_DHCP,
    API_SUBSYS_FILE,
    API_SUBSYS_HOSTAPD,
    API_SUBSYS_IWINFO,
    API_SUBSYS_SYSTEM,
    API_SUBSYS_UCI,
    API_SUBSYS_QMODEM,
    API_SUBSYS_MWAN3,
    API_SUBSYS_RC,
    API_SUBSYS_LUCI_RPC,
    API_SUBSYS_WIRELESS,
    API_METHOD_BOARD,
    API_METHOD_GET,
    API_METHOD_GET_AP,
    API_METHOD_GET_CLIENTS,
    API_METHOD_GET_STA,
    API_METHOD_GET_QMODEM,
    API_METHOD_GET_MWAN3,
    API_METHOD_INFO,
    API_METHOD_READ,
    API_METHOD_REBOOT,
    API_METHOD_DEL_CLIENT,
    API_METHOD_LIST,
    API_METHOD_INIT,
    API_METHOD_GET_HOST_HINTS,
    API_METHOD_SET,
    API_METHOD_COMMIT,
    API_METHOD_EXEC,
    DEFAULT_BAN_TIME_MS,
    DEFAULT_DEAUTH_REASON,
)

_LOGGER = logging.getLogger(__name__)


class ExtendedUbus(Ubus):
    """Extended Ubus client with specific OpenWrt functionality."""

    def __init__(self, url, hostname, username, password, session, timeout, verify):
        super().__init__(url, hostname, username, password, session, timeout, verify)
        self._interface_to_ssid_cache = {}

    async def get_interface_to_ssid_mapping(self):
        """Get mapping of physical interface names to SSIDs."""
        if self._interface_to_ssid_cache:
            return self._interface_to_ssid_cache

        mapping = {}

        try:
            result = await self.api_call(API_RPC_CALL, API_SUBSYS_WIRELESS, "status", {})
            if result:
                for _radio_name, radio_data in result.items():
                    if isinstance(radio_data, dict) and "interfaces" in radio_data:
                        for interface in radio_data["interfaces"]:
                            ifname = interface.get("ifname")
                            ssid = interface.get("config", {}).get("ssid")
                            if ifname and ssid:
                                mapping[ifname] = ssid
                                mapping[f"hostapd.{ifname}"] = ssid
                                _LOGGER.debug("Mapped interface %s to SSID %s", ifname, ssid)
        except Exception as primary_exc:
            _LOGGER.debug("network.wireless status unavailable (%s), trying iwinfo fallback", primary_exc)

        if not mapping:
            try:
                ap_result = await self.api_call(API_RPC_CALL, API_SUBSYS_IWINFO, API_METHOD_GET_AP)
                ap_devices = list(ap_result.get("devices", [])) if isinstance(ap_result, dict) else []
                for ifname in ap_devices:
                    try:
                        info = await self.api_call(API_RPC_CALL, API_SUBSYS_IWINFO, API_METHOD_INFO, {"device": ifname})
                        ssid = info.get("ssid") if isinstance(info, dict) else None
                        if ssid:
                            mapping[ifname] = ssid
                            mapping[f"hostapd.{ifname}"] = ssid
                            _LOGGER.debug("iwinfo fallback: mapped interface %s to SSID %s", ifname, ssid)
                    except Exception:
                        pass
            except Exception as iwinfo_exc:
                _LOGGER.debug("iwinfo SSID fallback also unavailable: %s", iwinfo_exc)

        if mapping:
            self._interface_to_ssid_cache = mapping
        else:
            _LOGGER.debug("Could not resolve interface-to-SSID mapping via netifd or iwinfo")

        return mapping

    async def file_read(self, path):
        """Read file content."""
        return await self.api_call(
            API_RPC_CALL,
            API_SUBSYS_FILE,
            API_METHOD_READ,
            {API_PARAM_PATH: path},
        )

    async def file_exec(self, command, params=None):
        """Execute a command through ubus file.exec."""
        return await self.api_call(
            API_RPC_CALL,
            API_SUBSYS_FILE,
            API_METHOD_EXEC,
            {
                "command": command,
                "params": params or [],
            },
        )

    async def get_ethers_mapping(self):
        """Read /etc/ethers file to get MAC to hostname mapping."""
        try:
            result = await self.file_read("/etc/ethers")
            if not result or "data" not in result:
                return {}

            mapping = {}
            for line in result["data"].splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    mac = parts[0].upper()
                    hostname = parts[1]
                    mapping[mac] = {"hostname": hostname, "ip": hostname}
                    _LOGGER.debug("Added ethers mapping: %s -> %s", mac, hostname)
            return mapping
        except Exception as exc:
            _LOGGER.debug("Error reading /etc/ethers: %s", exc)
            return {}

    async def get_conntrack_count(self):
        """Read connection tracking count from /proc/sys/net/netfilter/nf_conntrack_count."""
        try:
            result = await self.file_read("/proc/sys/net/netfilter/nf_conntrack_count")
            if result and "data" in result:
                # Convert the data to an integer
                return int(result["data"].strip())
            return None
        except Exception as exc:
            _LOGGER.debug("Error reading connection tracking count: %s", exc)
            return None

    async def get_system_temperatures(self):
        """Read system temperature sensors, trying multiple discovery paths."""
        temperatures = await self._get_hwmon_temperatures()
        if not temperatures:
            _LOGGER.debug("No hwmon temperatures found, trying thermal_zone fallback")
            temperatures = await self._get_thermal_zone_temperatures()
        if not temperatures:
            _LOGGER.warning("No temperature sensors found on this device. Checked /sys/class/hwmon/ and /sys/class/thermal/.")
        return temperatures

    async def _get_hwmon_temperatures(self):
        """Read temperatures from /sys/class/hwmon/*/temp1_input."""
        try:
            temperatures = {}
            hwmon_dirs = []

            hwmon_list_result = await self.api_call(
                API_RPC_CALL, API_SUBSYS_FILE, "list", {"path": "/sys/class/hwmon/"},
            )
            _LOGGER.debug("hwmon list result: %s", hwmon_list_result)
            if hwmon_list_result and "entries" in hwmon_list_result:
                hwmon_dirs = [
                    entry["name"]
                    for entry in hwmon_list_result["entries"]
                    if entry.get("type") in ("directory", "symlink", "link")
                ]

            if not hwmon_dirs:
                hwmon_dirs = [f"hwmon{i}" for i in range(32)]

            for hwmon_dir in hwmon_dirs:
                hwmon_path = f"/sys/class/hwmon/{hwmon_dir}"
                sensor_name = hwmon_dir
                try:
                    name_result = await self.file_read(f"{hwmon_path}/name")
                    if name_result and "data" in name_result:
                        sensor_name = name_result["data"].strip() or hwmon_dir

                    for temp_index in range(1, 6):
                        temp_path = f"{hwmon_path}/temp{temp_index}_input"
                        temp_result = await self.file_read(temp_path)
                        if not temp_result or "data" not in temp_result:
                            continue
                        raw_value = temp_result["data"].strip()
                        temp_value = int(raw_value) / 1000.0
                        key = sensor_name if temp_index == 1 else f"{sensor_name}_{temp_index}"
                        temperatures[key] = temp_value
                except (ValueError, TypeError):
                    continue
                except Exception:
                    continue
            return temperatures
        except Exception as exc:
            _LOGGER.debug("Error reading hwmon temperatures: %s", exc)
            return {}

    async def _get_thermal_zone_temperatures(self):
        """Read temperatures from /sys/class/thermal/thermal_zone*/temp."""
        try:
            list_result = await self.api_call(
                API_RPC_CALL, API_SUBSYS_FILE, "list", {"path": "/sys/class/thermal/"},
            )
            _LOGGER.debug("thermal list result: %s", list_result)
            if not list_result or "entries" not in list_result:
                return {}

            temperatures = {}
            for entry in list_result["entries"]:
                zone_name = entry.get("name", "")
                if not zone_name.startswith("thermal_zone"):
                    continue
                if entry.get("type") not in ("directory", "symlink", "link"):
                    continue
                zone_path = f"/sys/class/thermal/{zone_name}"
                try:
                    sensor_name = zone_name
                    type_result = await self.file_read(f"{zone_path}/type")
                    if type_result and "data" in type_result:
                        raw_type = type_result["data"].strip()
                        zone_index = zone_name.replace("thermal_zone", "")
                        sensor_name = f"{raw_type}-{zone_index}" if zone_index else raw_type
                    temp_result = await self.file_read(f"{zone_path}/temp")
                    if temp_result and "data" in temp_result:
                        temp_raw = int(temp_result["data"].strip())
                        temp_c = temp_raw / 1000.0 if temp_raw > 1000 else float(temp_raw)
                        temperatures[sensor_name] = temp_c
                except (ValueError, TypeError):
                    continue
            return temperatures
        except Exception as exc:
            _LOGGER.debug("Error reading thermal zone temperatures: %s", exc)
            return {}

    async def get_dhcp_clients_count(self):
        """Read DHCP leases file and count non-empty lines to determine client count."""
        try:
            result = await self.file_read("/tmp/dhcp.leases")
            if result and "data" in result:
                # Count non-empty lines
                lines = result["data"].splitlines()
                client_count = sum(1 for line in lines if line.strip())
                return client_count
            return 0
        except Exception as exc:
            _LOGGER.debug("Error reading DHCP leases file: %s", exc)
            return 0

    async def get_dhcp_method(self, method):
        """Get DHCP method."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_DHCP, method)

    async def get_hostapd(self):
        """Get hostapd data."""
        return await self.api_call(API_RPC_LIST, API_SUBSYS_HOSTAPD)

    async def get_hostapd_clients(self, hostapd):
        """Get hostapd clients."""
        return await self.api_call(API_RPC_CALL, hostapd, API_METHOD_GET_CLIENTS)

    async def get_uci_config(self, _config, _type):
        """Get UCI config."""
        return await self.api_call(
            API_RPC_CALL,
            API_SUBSYS_UCI,
            API_METHOD_GET,
            {
                API_PARAM_CONFIG: _config,
                API_PARAM_TYPE: _type,
            },
        )

    async def uci_get_option(self, config: str, section: str | None = None, option: str | None = None):
        """Get a specific UCI option value."""
        params = {API_PARAM_CONFIG: config}
        if section is not None:
            params["section"] = section
        if option is not None:
            params["option"] = option
        return await self.api_call(API_RPC_CALL, API_SUBSYS_UCI, API_METHOD_GET, params)

    async def uci_set_option(self, config: str, section: str, option: str, value):
        """Set a specific UCI option value."""
        params = {"config": config, "section": section, "values": {option: value}}
        return await self.api_call(API_RPC_CALL, API_SUBSYS_UCI, API_METHOD_SET, params)

    async def uci_commit_config(self, config: str):
        """Commit changes to a UCI config."""
        params = {"config": config}
        return await self.api_call(API_RPC_CALL, API_SUBSYS_UCI, API_METHOD_COMMIT, params)

    async def uci_network_interface(self, section: str, option: str):
        """Call network.interface ubus method."""
        try:
            _LOGGER.debug("Calling UCI call network")
            return await self.api_call(API_RPC_CALL, section, option)
        except Exception as exc:
            _LOGGER.error("UCI call %s failed: %s", section, exc)
            raise

    async def list_modem_ctrl(self):
        """List available modem_ctrl subsystems."""
        return await self.api_call(API_RPC_LIST, API_SUBSYS_QMODEM)

    async def get_qmodem_info(self):
        """Get QModem info."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_QMODEM, API_METHOD_GET_QMODEM)

    async def list_mwan3(self):
        """List available mwan3 subsystems."""
        return await self.api_call(API_RPC_LIST, API_SUBSYS_MWAN3)

    async def get_mwan3_status(self):
        """Get MWAN3 status."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_MWAN3, API_METHOD_GET_MWAN3)

    async def get_system_method(self, method):
        """Get system method."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_SYSTEM, method)

    async def system_board(self):
        """System board."""
        return await self.get_system_method(API_METHOD_BOARD)

    async def system_info(self):
        """System info."""
        return await self.get_system_method(API_METHOD_INFO)

    async def system_stat(self):
        """Kernel system statistics."""
        return await self.file_read("/proc/stat")

    async def system_reboot(self):
        """System reboot."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_SYSTEM, API_METHOD_REBOOT, {})

    # iwinfo specific methods
    async def get_ap_devices(self):
        """Get access point devices."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_IWINFO, API_METHOD_GET_AP)

    async def get_sta_devices(self, ap_device):
        """Get station devices."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_IWINFO, API_METHOD_GET_STA, {"device": ap_device})

    async def get_sta_statistics(self, ap_device):
        """Get detailed station statistics for all connected devices."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_IWINFO, API_METHOD_GET_STA, {"device": ap_device})

    async def get_ap_info(self, ap_device):
        """Get detailed access point information."""
        return await self.api_call(API_RPC_CALL, API_SUBSYS_IWINFO, API_METHOD_INFO, {"device": ap_device})

    async def get_root_partition_info(self):
        """Get root partition information (total, free, used, avail in MB)."""
        try:
            result = await self.api_call(API_RPC_CALL, API_SUBSYS_SYSTEM, API_METHOD_INFO)
            _LOGGER.debug("system info raw result: %s", result)
            if result and "root" in result:
                # Convert KB to MB
                try:
                    return {
                        "total": result["root"]["total"] / 1024,
                        "free": result["root"]["free"] / 1024,
                        "used": result["root"]["used"] / 1024,
                        "avail": result["root"]["avail"] / 1024
                    }
                except Exception as exc:
                    _LOGGER.debug("Error parsing root partition values: %s", exc)
                    return {"total": 0, "free": 0, "used": 0, "avail": 0}
            return {"total": 0, "free": 0, "used": 0, "avail": 0}
        except Exception as exc:
            _LOGGER.error("Failed to get root partition info: %s", exc)
            return {"total": 0, "free": 0, "used": 0, "avail": 0}

    def parse_sta_devices(self, result):
        """Parse station devices from the ubus result."""
        sta_devices = []
        if not result:
            return sta_devices

        # Handle different response formats from iwinfo
        if isinstance(result, list):
            # Direct list format
            sta_devices.extend(
                device["mac"] for device in result if isinstance(device, dict) and "mac" in device
            )
        elif isinstance(result, dict):
            # Dictionary format with "results" key
            sta_devices.extend(
                device["mac"] for device in result.get("results", [])
                if isinstance(device, dict) and "mac" in device
            )
        return sta_devices

    def parse_sta_statistics(self, result):
        """Parse detailed station statistics from the ubus result."""
        sta_statistics = {}
        if not result:
            return sta_statistics

        # Handle different response formats from iwinfo
        devices_list = []
        if isinstance(result, list):
            # Direct list format
            devices_list = result
        elif isinstance(result, dict):
            # Dictionary format with "results" key
            devices_list = result.get("results", [])
        else:
            _LOGGER.warning("Unexpected result type in parse_sta_statistics: %s", type(result).__name__)
            return sta_statistics

        # iwinfo format - each device has detailed statistics
        for device in devices_list:
            if isinstance(device, dict) and "mac" in device:
                mac = device["mac"]
                sta_statistics[mac] = device
            else:
                _LOGGER.debug("Invalid device format: %s", device)

        return sta_statistics

    def parse_ap_devices(self, result):
        """Parse access point devices from the ubus result."""
        return list(result.get("devices", []))

    def parse_ap_info(self, result, ap_device):
        """Parse access point information from the ubus result."""
        if not result:
            return {}

        # The result should contain the AP information directly
        ap_info = dict(result)
        ap_info["device"] = ap_device  # Add device name for identification

        # Set device name based on SSID and mode
        if "ssid" in ap_info and "mode" in ap_info:
            ssid = ap_info["ssid"]
            mode = ap_info["mode"].lower() if ap_info["mode"] else "unknown"
            ap_info["device_name"] = f"{ssid}({mode})"
        else:
            ap_info["device_name"] = ap_device

        return ap_info

    # hostapd specific methods
    def parse_hostapd_sta_devices(self, result):
        """Parse station devices from hostapd ubus result."""
        sta_devices = []
        if not result:
            return sta_devices

        for key in result.get("clients", {}):
            device = result["clients"][key]
            if device.get("authorized"):
                sta_devices.append(key)
        return sta_devices

    def parse_hostapd_sta_statistics(self, result):
        """Parse detailed station statistics from hostapd ubus result."""
        sta_statistics = {}
        if not result:
            return sta_statistics

        # hostapd format - each device has detailed statistics
        for mac, device in result.get("clients", {}).items():
            if device.get("authorized"):
                sta_statistics[mac] = device
        return sta_statistics

    def parse_hostapd_ap_devices(self, result):
        """Parse access point devices from hostapd ubus result."""
        return result

    async def get_all_sta_data_batch(self, ap_devices, is_hostapd=False):
        """Get station data for all AP devices using batch call."""
        if not ap_devices:
            return {}

        prepared_calls = []
        for ap_device in ap_devices:
            if is_hostapd:
                prepared_calls.append(PreparedCall(rpc_method=API_RPC_CALL, subsystem=ap_device, method=API_METHOD_GET_CLIENTS, params=None, rpc_id=ap_device))
            else:
                prepared_calls.append(PreparedCall(rpc_method=API_RPC_CALL, subsystem=API_SUBSYS_IWINFO, method=API_METHOD_GET_STA, params={"device": ap_device}, rpc_id=ap_device))

        results = await self.batch_call(prepared_calls)
        if not results:
            return {}

        sta_data = {}
        for ap_device, result in results:
            try:
                if isinstance(result, dict) and result:
                    if is_hostapd:
                        sta_data[ap_device] = {
                            'devices': self.parse_hostapd_sta_devices(result),
                            'statistics': self.parse_hostapd_sta_statistics(result)
                        }
                    else:
                        sta_data[ap_device] = {
                            'devices': self.parse_sta_devices(result),
                            'statistics': self.parse_sta_statistics(result)
                        }
                elif isinstance(result, Exception):
                    _LOGGER.error("Exception in batch call for %s: %s", ap_device, result)
                    continue
            except (IndexError, KeyError) as exc:
                _LOGGER.debug("Error parsing sta data index %s: %s", ap_device, exc)
        return sta_data

    async def get_all_ap_info_batch(self, ap_devices):
        """Get AP info for all AP devices using batch call."""
        if not ap_devices:
            return {}

        prepared_calls = []
        for ap_device in ap_devices:
            prepared_calls.append(PreparedCall(rpc_method=API_RPC_CALL, subsystem=API_SUBSYS_IWINFO, method=API_METHOD_INFO, params={"device": ap_device}, rpc_id=ap_device))

        results = await self.batch_call(prepared_calls)
        if not results:
            return {}

        ap_info_data = {}
        for ap_device, result in results:
            try:
                if isinstance(result, dict) and result:
                    ap_info = self.parse_ap_info(result, ap_device)
                    if ap_info and ap_info.get("ssid"):
                        ap_info_data[ap_device] = ap_info
                        _LOGGER.debug("AP info fetched for device %s with SSID %s", ap_device, ap_info.get("ssid"))
                    else:
                        _LOGGER.debug("Skipping AP device %s - no SSID found", ap_device)
                elif isinstance(result, Exception):
                    _LOGGER.error("Exception in batch call for %s: %s", ap_device, result)
                    continue
            except (IndexError, KeyError) as exc:
                _LOGGER.debug("Error parsing AP info for %s: %s", ap_device, exc)
        return ap_info_data

    # RC (service control) specific methods
    async def list_services(self, include_status=False):
        """List available services, optionally including their status."""
        if not include_status:
            return await self.api_call(API_RPC_CALL, API_SUBSYS_RC, API_METHOD_LIST)

        service_list_result = await self.api_call(API_RPC_CALL, API_SUBSYS_RC, API_METHOD_LIST)
        if not service_list_result:
            _LOGGER.warning("Failed to get service list from RC")
            return {}

        _LOGGER.debug("Got service list: %s", service_list_result)

        services_with_status = {}
        prepared_calls = []

        for service_name in service_list_result:
            prepared_calls.append(PreparedCall(rpc_method=API_RPC_CALL, subsystem=API_SUBSYS_RC, method=API_METHOD_LIST, params={"name": service_name}, rpc_id=service_name))

        if prepared_calls:
            _LOGGER.debug("Executing batch call for %d services", len(prepared_calls))
            status_results = await self.batch_call(prepared_calls)
            if status_results:
                for service_name, result in status_results:
                    if isinstance(result, dict):
                        if service_name in result:
                            service_status = result[service_name]
                            parsed_status = self._parse_service_status(service_status, service_name)
                            services_with_status[service_name] = parsed_status
                        else:
                            services_with_status[service_name] = {"running": False, "enabled": False}
                    elif isinstance(result, Exception):
                        services_with_status[service_name] = {"running": False, "enabled": False}
                    else:
                        services_with_status[service_name] = {"running": False, "enabled": False}
            else:
                _LOGGER.warning("Batch call returned no results")

        _LOGGER.debug("Final services with status: %s", services_with_status)
        return services_with_status

    def _parse_service_status(self, status_data, service_name):
        """Parse service status from RC API response."""
        _LOGGER.debug("Parsing service status for %s: %s (type: %s)", service_name, status_data, type(status_data))

        if not status_data:
            _LOGGER.debug("Service %s: No status data, returning disabled", service_name)
            return {"running": False, "enabled": False}

        # OpenWrt RC list returns a dict with service properties:
        # {"start": 99, "enabled": true, "running": false}
        if isinstance(status_data, dict):
            _LOGGER.debug("Service %s: Dict status keys=%s", service_name, list(status_data.keys()))

            # Extract running and enabled status
            running = status_data.get("running", False)
            enabled = status_data.get("enabled", False)
            start_priority = status_data.get("start", 0)

            _LOGGER.debug("Service %s: running=%s, enabled=%s, start=%s",
                          service_name, running, enabled, start_priority)

            result = {
                "running": bool(running),
                "enabled": bool(enabled),
                "start_priority": start_priority,
                "raw_status": status_data
            }
            _LOGGER.debug("Service %s: Final parsed result=%s", service_name, result)
            return result

        # Fallback for string or other formats (shouldn't happen with RC list)
        if isinstance(status_data, str):
            running = status_data.lower() in ["running", "active", "started"]
            _LOGGER.debug("Service %s: String status '%s', running=%s", service_name, status_data, running)
            return {"running": running, "enabled": running, "status": status_data}

        # Fallback for unexpected formats
        _LOGGER.warning("Service %s: Unexpected status format (type %s): %s",
                        service_name, type(status_data), status_data)
        return {"running": False, "enabled": False, "raw_status": status_data}

    async def service_action(self, service_name, action):
        """Perform action on a service (start, stop, restart)."""
        return await self.api_call(
            API_RPC_CALL,
            API_SUBSYS_RC,
            API_METHOD_INIT,
            {"name": service_name, "action": action}
        )

    async def check_hostapd_available(self):
        """Check if hostapd service is available via ubus list."""
        try:
            result = await self.api_call(API_RPC_LIST, "*")
            if not result:
                return False

            # Look for any hostapd.* interfaces in the result
            for interface_name in result.keys():
                if interface_name.startswith("hostapd."):
                    _LOGGER.debug("Found hostapd interface: %s", interface_name)
                    return True

            _LOGGER.debug("No hostapd interfaces found in ubus list")
            return False

        except Exception as exc:
            _LOGGER.warning("Failed to check hostapd availability: %s", exc)
            return False

    async def kick_device(
        self,
        hostapd_interface: str,
        mac_address: str,
        ban_time: int = DEFAULT_BAN_TIME_MS,
        reason: int = DEFAULT_DEAUTH_REASON,
    ):
        """Kick a device from the AP interface.

        Args:
            hostapd_interface: The hostapd interface name (e.g. "hostapd.phy0-ap0")
            mac_address: MAC address of the device to kick
            ban_time: Ban time in milliseconds (default: 60000ms = 60s)
            reason: 802.11 deauthentication reason code (default: 5)
        """
        return await self.api_call(
            API_RPC_CALL,
            hostapd_interface,
            API_METHOD_DEL_CLIENT,
            {
                "addr": mac_address,
                "deauth": True,
                "reason": reason,
                "ban_time": ban_time
            }
        )

    async def get_network_devices(self):
        """Get network device status."""
        return await self.api_call(API_RPC_CALL, "network.device", "status")

    async def get_ip_neighbors(self):
        """Get neighbor table entries (ARP and NDP) using ip neigh commands."""
        result = {"ipv4": [], "ipv6": []}
        try:
            try:
                ipv4_result = await self.api_call(
                    API_RPC_CALL, "file", "exec",
                    {"command": "/sbin/ip", "params": ["-4", "neigh", "show"]}
                )
                if ipv4_result and "stdout" in ipv4_result:
                    result["ipv4"] = self._parse_ip_neigh_output(ipv4_result["stdout"], "ipv4")
            except PermissionError:
                _LOGGER.warning("Permission denied for '/sbin/ip -4 neigh show'")
                return {"error": "permission_denied"}
            except Exception as exc:
                _LOGGER.debug("IPv4 neighbor query error: %s", exc)

            try:
                ipv6_result = await self.api_call(
                    API_RPC_CALL, "file", "exec",
                    {"command": "/sbin/ip", "params": ["-6", "neigh", "show"]}
                )
                if ipv6_result and "stdout" in ipv6_result:
                    result["ipv6"] = self._parse_ip_neigh_output(ipv6_result["stdout"], "ipv6")
            except PermissionError:
                _LOGGER.warning("Permission denied for '/sbin/ip -6 neigh show'")
                return {"error": "permission_denied"}
            except Exception as exc:
                _LOGGER.debug("IPv6 neighbor query error: %s", exc)
        except Exception as exc:
            _LOGGER.error("Error getting IP neighbors: %s", exc)
        return result

    def _parse_ip_neigh_output(self, output, ip_version):
        """Parse output from ip neigh command."""
        neighbors = []
        if not output:
            return neighbors
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                entry = {"ip": parts[0], "interface": None, "mac": None, "state": None, "ip_version": ip_version}
                for i, part in enumerate(parts[1:], 1):
                    if part == "dev" and i + 1 < len(parts):
                        entry["interface"] = parts[i + 1]
                    elif part == "lladdr" and i + 1 < len(parts):
                        entry["mac"] = parts[i + 1].upper()
                    elif part in ["REACHABLE", "STALE", "DELAY", "PROBE", "FAILED", "PERMANENT", "NOARP"]:
                        entry["state"] = part
                if entry["mac"]:
                    neighbors.append(entry)
            except Exception:
                continue
        return neighbors

    # luci-rpc specific methods for wired device tracking
    async def get_host_hints(self):
        """Get host hints from luci-rpc for device name/IP mapping.

        Returns a dictionary with MAC addresses as keys containing:
        - ipaddrs: List of IPv4 addresses
        - ip6addrs: List of IPv6 addresses
        - name: Hostname if available
        """
        try:
            result = await self.api_call(
                API_RPC_CALL,
                API_SUBSYS_LUCI_RPC,
                API_METHOD_GET_HOST_HINTS
            )
            if result and isinstance(result, dict):
                return result
            return {}
        except Exception as exc:
            _LOGGER.debug("Error getting host hints: %s", exc)
            return {}

    async def get_arp_table(self):
        """Read and parse ARP table from /proc/net/arp.

        Returns a list of dictionaries containing:
        - ip: IP address
        - mac: MAC address (uppercase)
        - device: Network interface
        - state: ARP entry state (derived from flags)
        """
        try:
            result = await self.file_read("/proc/net/arp")
            if not result or "data" not in result:
                return []

            return self.parse_arp_table(result["data"])
        except Exception as exc:
            _LOGGER.debug("Error reading ARP table: %s", exc)
            return []

    async def get_ip_neigh_table(self) -> list[dict]:
        """Read and parse neighbor table from `ip neigh`.

        Expected line examples:
        - 192.168.1.10 dev br-lan lladdr AA:BB:CC:DD:EE:FF REACHABLE
        - 192.168.1.20 dev br-lan lladdr AA:BB:CC:DD:EE:11 STALE
        """
        try:
            ip_neigh = await self.api_call(API_RPC_CALL, API_SUBSYS_FILE, "exec", {
                "command": "ip",
                "params": ["neigh", "show"]
            })
            if not ip_neigh or "stdout" not in ip_neigh:
                return []
            return self.parse_ip_neigh_table(ip_neigh["stdout"])
        except Exception as exc:
            _LOGGER.debug("Error reading ip neigh table: %s", exc)
            return []

    def parse_arp_table(self, arp_data: str) -> list[dict]:
        """Parse ARP table data from /proc/net/arp.

        Format of /proc/net/arp:
        IP address       HW type     Flags       HW address            Mask     Device
        192.168.1.1      0x1         0x2         00:11:22:33:44:55     *        br-lan

        Flags:
        - 0x0: incomplete
        - 0x2: reachable/complete
        - 0x4: permanent
        - 0x6: reachable + permanent
        """
        arp_entries = []
        if not arp_data:
            return arp_entries

        lines = arp_data.strip().split("\n")
        # Skip header line
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 6:
                ip_addr = parts[0]
                flags = parts[2]
                mac_addr = parts[3].upper()
                device = parts[5]

                # Skip incomplete entries (flags 0x0) and broadcast/multicast
                if flags == "0x0" or mac_addr == "00:00:00:00:00:00":
                    continue

                # Determine state based on flags
                try:
                    flag_int = int(flags, 16)
                    if flag_int & 0x4:
                        state = "permanent"
                    elif flag_int & 0x2:
                        state = "reachable"
                    else:
                        state = "stale"
                except ValueError:
                    state = "unknown"

                arp_entries.append({
                    "ip": ip_addr,
                    "mac": mac_addr,
                    "device": device,
                    "state": state,
                    "flags": flags
                })

        return arp_entries

    def parse_ip_neigh_table(self, neigh_data: str) -> list[dict]:
        """Parse `ip neigh show` output."""
        neigh_entries = []
        if not neigh_data:
            return neigh_entries

        for line in neigh_data.strip().split("\n"):
            parts = line.split()
            if len(parts) < 4:
                continue

            ip_addr = parts[0]
            device = ""
            mac_addr = ""
            state = parts[-1].lower()

            for i, token in enumerate(parts):
                if token == "dev" and i + 1 < len(parts):
                    device = parts[i + 1]
                elif token == "lladdr" and i + 1 < len(parts):
                    mac_addr = parts[i + 1].upper()

            if not mac_addr or mac_addr == "00:00:00:00:00:00":
                continue

            neigh_entries.append({
                "ip": ip_addr,
                "mac": mac_addr,
                "device": device,
                "state": state,
            })

        return neigh_entries

    async def get_wired_devices(self, wireless_macs: set[str] | None = None) -> dict[str, dict]:
        """Get wired devices by combining ARP table with host hints.

        Args:
            wireless_macs: Set of MAC addresses that are wireless (to exclude)

        Returns:
            Dictionary with MAC addresses as keys containing device info:
            - ip_address: IPv4 address
            - hostname: Device hostname if available
            - connected: Whether device is currently connected
            - connection_type: "wired"
            - ap_device: "LAN" for wired devices
        """
        if wireless_macs is None:
            wireless_macs = set()

        # Normalize wireless MACs to uppercase for comparison
        wireless_macs_upper = {mac.upper() for mac in wireless_macs}

        # Get ARP and neighbor table entries
        arp_entries = await self.get_arp_table()
        neigh_entries = await self.get_ip_neigh_table()

        # Get host hints for name/IP mapping
        host_hints = await self.get_host_hints()

        wired_devices = {}

        # Build neighbor index by MAC for confidence scoring
        neigh_by_mac = {entry["mac"]: entry for entry in neigh_entries}

        for entry in arp_entries:
            mac = entry["mac"]

            # Skip wireless devices
            if mac in wireless_macs_upper:
                _LOGGER.debug("Skipping wireless device %s from wired tracking", mac)
                continue

            # Get additional info from host hints
            hint = host_hints.get(mac, {})

            # Determine hostname - prefer host hints name, fallback to MAC
            hostname = hint.get("name", "")
            if not hostname:
                # Try lowercase MAC lookup as well
                hint = host_hints.get(mac.lower(), {})
                hostname = hint.get("name", "")

            # Determine connection state with ARP + ip neigh fusion
            arp_connected = entry["state"] in ("reachable", "permanent")
            neigh_entry = neigh_by_mac.get(mac)
            neigh_state = neigh_entry.get("state") if neigh_entry else None
            neigh_connected = neigh_state in (
                "reachable",
                "delay",
                "probe",
                "permanent",
            )
            connected = arp_connected or neigh_connected

            interface = entry["device"]
            if neigh_entry and neigh_entry.get("device"):
                interface = neigh_entry["device"]

            confidence = "low"
            if arp_connected and neigh_connected:
                confidence = "high"
            elif arp_connected or neigh_connected:
                confidence = "medium"

            wired_devices[mac] = {
                "ip_address": entry["ip"],
                "hostname": hostname if hostname else mac,
                "connected": connected,
                "connection_type": "wired",
                "ap_device": "LAN",
                "arp_state": entry["state"],
                "neighbor_state": neigh_state,
                "confidence": confidence,
                "interface": interface,
            }

            _LOGGER.debug(
                "Found wired device %s: IP=%s, hostname=%s, connected=%s",
                mac, entry["ip"], hostname, connected
            )

        return wired_devices
