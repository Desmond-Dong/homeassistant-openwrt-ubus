"""Support for OpenWrt router MWAN3 (Multi-WAN) information sensors."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import (
    DOMAIN,
    CONF_USE_HTTPS,
    CONF_PORT,
    DEFAULT_USE_HTTPS,
    CONF_MWAN3_SENSOR_TIMEOUT,
    DEFAULT_MWAN3_SENSOR_TIMEOUT,
    build_configuration_url,
)
from ..shared_data_manager import SharedDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

INTERFACE_SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(key="status", name="Status", icon="mdi:wan"),
    SensorEntityDescription(
        key="uptime", name="Uptime", device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS, icon="mdi:timer-outline",
    ),
    SensorEntityDescription(key="enabled", name="Enabled", icon="mdi:power", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="running", name="Running", icon="mdi:play-circle", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="tracking", name="Tracking", icon="mdi:target"),
    SensorEntityDescription(key="up", name="Up", icon="mdi:arrow-up-circle", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="track_ips_total", name="Track IPs Total", state_class=SensorStateClass.MEASUREMENT, icon="mdi:ip-network", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="track_ips_up", name="Track IPs Up", state_class=SensorStateClass.MEASUREMENT, icon="mdi:check-circle", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="track_ips_skipped", name="Track IPs Skipped", state_class=SensorStateClass.MEASUREMENT, icon="mdi:skip-next-circle", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="track_ips_down", name="Track IPs Down", state_class=SensorStateClass.MEASUREMENT, icon="mdi:close-circle", entity_category=EntityCategory.DIAGNOSTIC),
]

POLICY_SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(key="ipv4_active_interfaces", name="IPv4 Active Interfaces", state_class=SensorStateClass.MEASUREMENT, icon="mdi:counter", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="ipv4_primary_interface", name="IPv4 Primary Interface", icon="mdi:star"),
    SensorEntityDescription(key="ipv4_interface_list", name="IPv4 Interface List", icon="mdi:format-list-numbered"),
    SensorEntityDescription(key="ipv6_active_interfaces", name="IPv6 Active Interfaces", state_class=SensorStateClass.MEASUREMENT, icon="mdi:counter", entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="ipv6_primary_interface", name="IPv6 Primary Interface", icon="mdi:star"),
    SensorEntityDescription(key="ipv6_interface_list", name="IPv6 Interface List", icon="mdi:format-list-numbered"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> SharedDataUpdateCoordinator | None:
    """Set up OpenWrt MWAN3 sensors from a config entry."""
    mwan3_available = hass.data.get(DOMAIN, {}).get("mwan3_available", False)
    if not mwan3_available:
        _LOGGER.info("MWAN3 entities not created - mwan3 is not available")
        return None

    data_manager_key = f"data_manager_{entry.entry_id}"
    data_manager = hass.data[DOMAIN][data_manager_key]

    timeout = entry.options.get(CONF_MWAN3_SENSOR_TIMEOUT, entry.data.get(CONF_MWAN3_SENSOR_TIMEOUT, DEFAULT_MWAN3_SENSOR_TIMEOUT))
    scan_interval = timedelta(seconds=timeout)

    coordinator = SharedDataUpdateCoordinator(hass, data_manager, ["mwan3_status"], f"{DOMAIN}_mwan3_{entry.data[CONF_HOST]}", scan_interval)
    coordinator.known_interfaces = set()
    coordinator.known_policies = set()
    coordinator.async_add_entities = async_add_entities

    async def _handle_coordinator_update_async():
        if not coordinator.data or "mwan3_status" not in coordinator.data:
            return
        mwan3_data = coordinator.data["mwan3_status"]
        if not mwan3_data or not isinstance(mwan3_data, dict):
            return

        interfaces = mwan3_data.get("interfaces", {})
        current_interfaces = set(interfaces.keys())
        new_interfaces = current_interfaces - coordinator.known_interfaces
        if new_interfaces:
            _LOGGER.info("Found %d new MWAN3 interfaces: %s", len(new_interfaces), new_interfaces)
            entity_registry = er.async_get(hass)
            new_entities = []
            for interface in new_interfaces:
                interface_sensors = []
                for description in INTERFACE_SENSOR_DESCRIPTIONS:
                    unique_id = f"{entry.data[CONF_HOST]}_mwan3_intf_{interface}_{description.key}"
                    if entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id):
                        continue
                    interface_data = interfaces.get(interface, {})
                    if isinstance(interface_data, dict) and interface_data:
                        interface_sensors.append(description)
                if interface_sensors:
                    new_entities.extend([MWAN3InterfaceSensor(coordinator, description, interface) for description in interface_sensors])
                coordinator.known_interfaces.add(interface)
            if new_entities:
                async_add_entities(new_entities, True)

        policies = mwan3_data.get("policies", {})
        current_policies = set()
        if isinstance(policies, dict):
            for ip_version in ["ipv4", "ipv6"]:
                if ip_version in policies and isinstance(policies[ip_version], dict):
                    current_policies.update(policies[ip_version].keys())
        new_policies = current_policies - coordinator.known_policies
        if new_policies:
            _LOGGER.info("Found %d new MWAN3 policies: %s", len(new_policies), new_policies)
            entity_registry = er.async_get(hass)
            new_policy_entities = []
            for policy in new_policies:
                policy_sensors = []
                for description in POLICY_SENSOR_DESCRIPTIONS:
                    unique_id = f"{entry.data[CONF_HOST]}_mwan3_policy_{policy}_{description.key}"
                    if entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id):
                        continue
                    policy_sensors.append(description)
                if policy_sensors:
                    new_policy_entities.extend([MWAN3PolicySensor(coordinator, description, policy) for description in policy_sensors])
                coordinator.known_policies.add(policy)
            if new_policy_entities:
                async_add_entities(new_policy_entities, True)

    def _handle_coordinator_update():
        hass.async_create_task(_handle_coordinator_update_async())

    coordinator.async_add_listener(_handle_coordinator_update)
    await coordinator.async_config_entry_first_refresh()

    host = coordinator.data_manager.entry.data[CONF_HOST]
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{host}_mwan3")},
        name=f"{host} MWAN3 Interfaces and Policies",
        manufacturer="OpenWrt",
        via_device=(DOMAIN, host),
    )

    initial_entities = []
    if coordinator.data and "mwan3_status" in coordinator.data:
        mwan3_data = coordinator.data["mwan3_status"]
        if isinstance(mwan3_data, dict):
            interfaces = mwan3_data.get("interfaces", {})
            if isinstance(interfaces, dict):
                for interface in interfaces:
                    if isinstance(interfaces.get(interface), dict) and interfaces[interface]:
                        initial_entities.extend([MWAN3InterfaceSensor(coordinator, description, interface) for description in INTERFACE_SENSOR_DESCRIPTIONS])
                        coordinator.known_interfaces.add(interface)
            policies = mwan3_data.get("policies", {})
            if isinstance(policies, dict):
                current_policies = set()
                for ip_version in ["ipv4", "ipv6"]:
                    ip_policies = policies.get(ip_version)
                    if isinstance(ip_policies, dict):
                        current_policies.update(ip_policies.keys())
                for policy in current_policies:
                    initial_entities.extend([MWAN3PolicySensor(coordinator, description, policy) for description in POLICY_SENSOR_DESCRIPTIONS])
                    coordinator.known_policies.add(policy)

    if initial_entities:
        async_add_entities(initial_entities, True)
    return coordinator


class MWAN3InterfaceSensor(CoordinatorEntity, SensorEntity):
    """Representation of a MWAN3 interface-specific sensor."""

    def __init__(self, coordinator, description, interface):
        super().__init__(coordinator)
        self.entity_description = description
        self._host = coordinator.data_manager.entry.data[CONF_HOST]
        self._interface = interface
        self._attr_unique_id = f"{self._host}_mwan3_intf_{interface}_{description.key}"
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._host}_mwan3_intf_{self._interface}")},
            name=f"MWAN3 Interface {self._interface} ({self._host})",
            manufacturer="OpenWrt",
            model="MWAN3 Interface",
            configuration_url=build_configuration_url(self._host, self.coordinator.data_manager.entry.data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS), self.coordinator.data_manager.entry.data.get(CONF_PORT)),
            via_device=(DOMAIN, f"{self._host}_mwan3"),
        )

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        mwan3_status = self.coordinator.data.get("mwan3_status")
        if mwan3_status is None:
            return None
        try:
            return self._extract_interface_value(mwan3_status, self._interface, self.entity_description.key)
        except Exception:
            return None

    def _extract_interface_value(self, mwan3_data, interface, key):
        if not isinstance(mwan3_data, dict):
            return None
        interface_data = mwan3_data.get("interfaces", {}).get(interface, {})
        if not isinstance(interface_data, dict):
            return None
        if key == "status":
            return interface_data.get("status", "unknown")
        if key == "uptime":
            uptime = interface_data.get("uptime")
            try:
                return int(uptime) if uptime is not None else 0
            except (ValueError, TypeError):
                return 0
        if key == "enabled":
            enabled = interface_data.get("enabled")
            return "On" if isinstance(enabled, bool) and enabled else "Off"
        if key == "running":
            running = interface_data.get("running")
            return "On" if isinstance(running, bool) and running else "Off"
        if key == "tracking":
            return interface_data.get("tracking", "Unknown")
        if key == "up":
            up = interface_data.get("up")
            return "On" if isinstance(up, bool) and up else "Off"
        if key == "track_ips_total":
            track_ips = interface_data.get("track_ip", [])
            return len(track_ips) if isinstance(track_ips, list) else 0
        if key == "track_ips_up":
            track_ips = interface_data.get("track_ip", [])
            return sum(1 for ip_entry in track_ips if isinstance(ip_entry, dict) and ip_entry.get("status") == "up") if isinstance(track_ips, list) else 0
        if key == "track_ips_skipped":
            track_ips = interface_data.get("track_ip", [])
            return sum(1 for ip_entry in track_ips if isinstance(ip_entry, dict) and ip_entry.get("status") == "skipped") if isinstance(track_ips, list) else 0
        if key == "track_ips_down":
            track_ips = interface_data.get("track_ip", [])
            return sum(1 for ip_entry in track_ips if isinstance(ip_entry, dict) and ip_entry.get("status") == "down") if isinstance(track_ips, list) else 0
        return interface_data.get(key)

    @property
    def available(self) -> bool:
        if not (self.coordinator.last_update_success and self.coordinator.data and self.coordinator.data.get("mwan3_status") is not None):
            return False
        mwan3_status = self.coordinator.data.get("mwan3_status", {})
        return self._interface in mwan3_status.get("interfaces", {})


class MWAN3PolicySensor(CoordinatorEntity, SensorEntity):
    """Representation of a MWAN3 policy-specific sensor."""

    def __init__(self, coordinator, description, policy):
        super().__init__(coordinator)
        self.entity_description = description
        self._host = coordinator.data_manager.entry.data[CONF_HOST]
        self._policy = policy
        self._attr_unique_id = f"{self._host}_mwan3_policy_{policy}_{description.key}"
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._host}_mwan3_policy_{self._policy}")},
            name=f"MWAN3 Policy {self._policy} ({self._host})",
            manufacturer="OpenWrt",
            model="MWAN3 Policy",
            configuration_url=build_configuration_url(self._host, self.coordinator.data_manager.entry.data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS), self.coordinator.data_manager.entry.data.get(CONF_PORT)),
            via_device=(DOMAIN, f"{self._host}_mwan3"),
        )

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        mwan3_status = self.coordinator.data.get("mwan3_status")
        if mwan3_status is None:
            return None
        try:
            return self._extract_policy_value(mwan3_status, self._policy, self.entity_description.key)
        except Exception:
            return None

    def _extract_policy_value(self, mwan3_data, policy, key):
        if not isinstance(mwan3_data, dict):
            return None
        policies = mwan3_data.get("policies", {})
        if not isinstance(policies, dict):
            return None
        ip_version = "ipv4" if key.startswith("ipv4_") else "ipv6"
        policy_data = policies.get(ip_version, {}).get(policy, [])
        if not isinstance(policy_data, list):
            return ""
        if key.endswith("_active_interfaces"):
            return len(policy_data)
        if key.endswith("_primary_interface"):
            if not policy_data:
                return ""
            primary = max(policy_data, key=lambda x: x.get("percent", 0) if isinstance(x, dict) else 0)
            return primary.get("interface", "") if isinstance(primary, dict) else ""
        if key.endswith("_interface_list"):
            if not policy_data:
                return ""
            sorted_interfaces = sorted([x for x in policy_data if isinstance(x, dict)], key=lambda x: x.get("percent", 0), reverse=True)
            return ", ".join(f"{iface.get('interface', 'unknown')} ({iface.get('percent', 0)}%)" for iface in sorted_interfaces)
        return ""

    @property
    def available(self) -> bool:
        if not (self.coordinator.last_update_success and self.coordinator.data and self.coordinator.data.get("mwan3_status") is not None):
            return False
        mwan3_status = self.coordinator.data.get("mwan3_status", {})
        policies = mwan3_status.get("policies", {})
        for ip_version in ["ipv4", "ipv6"]:
            if isinstance(policies.get(ip_version), dict) and self._policy in policies[ip_version]:
                return True
        return False