"""The ubus component for OpenWrt."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    CONF_ENABLE_ETH_SENSORS,
    CONF_ENABLE_MWAN3_SENSORS,
    CONF_ENABLE_NLBWMON_SENSORS,
    DEFAULT_DHCP_SOFTWARE,
    DEFAULT_WIRELESS_SOFTWARE,
    DEFAULT_USE_HTTPS,
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_ENDPOINT,
    DEFAULT_ENABLE_QMODEM_SENSORS,
    DEFAULT_ENABLE_STA_SENSORS,
    DEFAULT_ENABLE_SYSTEM_SENSORS,
    DEFAULT_ENABLE_AP_SENSORS,
    DEFAULT_ENABLE_ETH_SENSORS,
    DEFAULT_ENABLE_MWAN3_SENSORS,
    DEFAULT_ENABLE_NLBWMON_SENSORS,
    DHCP_SOFTWARES,
    DOMAIN,
    PLATFORMS,
    WIRELESS_SOFTWARES,
    API_DEF_TIMEOUT,
    build_ubus_url,
    get_config_value,
)
from .extended_ubus import ExtendedUbus
from .shared_data_manager import SharedUbusDataManager
from .topology import async_setup_topology
from .ubus_client import create_enhanced_extended_ubus_client
from .security_utils import CredentialManager, safe_log_data

_LOGGER = logging.getLogger(__name__)


async def _check_ubus_availability(check_name: str, create_coro) -> bool:
    """Run a lightweight availability check with a few retries."""
    for attempt in range(1, 4):
        try:
            result = await create_coro()
            available = result is not None and bool(result)
            _LOGGER.debug("%s availability check: %s", check_name, available)
            return available
        except PermissionError as exc:
            _LOGGER.debug("%s availability denied: %s", check_name, exc)
            return False
        except Exception as exc:
            _LOGGER.debug("%s check failed (attempt %d/3): %s", check_name, attempt, exc)
            if attempt < 3:
                await asyncio.sleep(2)

    return False

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
    await async_setup_topology(hass)

    if DOMAIN not in config:
        return True

    # Store the configuration for the device tracker
    hass.data[DOMAIN]["config"] = config[DOMAIN]

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up openwrt ubus from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Backward compatibility: preserve pre-toggle behavior for legacy entries.
    if CONF_ENABLE_NLBWMON_SENSORS not in entry.data:
        new_data = dict(entry.data)
        new_data[CONF_ENABLE_NLBWMON_SENSORS] = True
        hass.config_entries.async_update_entry(entry, data=new_data)
        entry = hass.config_entries.async_get_entry(entry.entry_id)
        if entry is None:
            raise ConfigEntryNotReady("Failed to reload config entry data for nlbwmon migration")

    ubus = None
    # Test connection before setting up platforms
    try:
        # Build URL using utility function
        hostname = entry.data[CONF_HOST]
        use_https = get_config_value(entry, CONF_USE_HTTPS, DEFAULT_USE_HTTPS)
        port = get_config_value(
            entry,
            CONF_PORT,
            DEFAULT_HTTPS_PORT if use_https else DEFAULT_HTTP_PORT,
        )
        endpoint = get_config_value(entry, CONF_ENDPOINT, DEFAULT_ENDPOINT)
        url = build_ubus_url(hostname, use_https, port=port, endpoint=endpoint)

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
            hostname,
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
        modem_ctrl_available = await _check_ubus_availability(
            "Modem_ctrl", lambda: ubus.list_modem_ctrl()
        )

        # Store modem_ctrl availability in hass data
        hass.data[DOMAIN]["modem_ctrl_available"] = modem_ctrl_available

        # Check for mwan3 availability and store the result
        mwan3_available = await _check_ubus_availability(
            "MWAN3", lambda: ubus.list_mwan3()
        )
        hass.data[DOMAIN]["mwan3_available"] = mwan3_available

        # Check for nlbwmon availability/permission and store the result
        nlbwmon_available = await _check_ubus_availability(
            "nlbwmon", lambda: ubus.file_exec("/usr/sbin/nlbw", ["-h"])
        )
        hass.data[DOMAIN]["nlbwmon_available"] = nlbwmon_available

        # Create shared data manager
        data_manager = SharedUbusDataManager(hass, entry)
        hass.data[DOMAIN][f"data_manager_{entry.entry_id}"] = data_manager

        # Register UCI services once per integration domain
        if not hass.data[DOMAIN].get("uci_services_registered"):
            hass.data[DOMAIN]["uci_services_registered"] = True

            async def async_handle_uci_get(call):
                """Handle openwrt_ubus.uci_get service."""
                config = call.data["config"]
                section = call.data.get("section")
                option = call.data.get("option")
                target_entity_id = call.data.get("target_entity_id")

                shared_manager = None
                for key, value in hass.data[DOMAIN].items():
                    if key.startswith("data_manager_"):
                        shared_manager = value
                        break
                if shared_manager is None:
                    _LOGGER.error("No SharedUbusDataManager available for uci_get")
                    return

                client = await shared_manager.get_ubus_connection_async()
                result = await client.uci_get_option(config, section, option)
                value = None
                try:
                    if isinstance(result, dict) and "result" in result:
                        res_list = result["result"]
                        if len(res_list) >= 2 and isinstance(res_list[1], dict):
                            values_dict = res_list[1].get("values", {})
                            if option is not None:
                                value = values_dict.get(option)
                            elif values_dict:
                                value = next(iter(values_dict.values()))
                except Exception as exc:
                    _LOGGER.warning("Failed to parse UCI get result: %s", exc)

                if target_entity_id and value is not None:
                    hass.states.async_set(target_entity_id, value)

            async def async_handle_uci_set_commit(call):
                """Handle openwrt_ubus.uci_set_commit service."""
                config = call.data["config"]
                section = call.data["section"]
                option = call.data["option"]
                value = call.data["value"]
                services_to_restart = call.data.get("service")

                shared_manager = None
                for key, value_dm in hass.data[DOMAIN].items():
                    if key.startswith("data_manager_"):
                        shared_manager = value_dm
                        break
                if shared_manager is None:
                    _LOGGER.error("No SharedUbusDataManager available for uci_set_commit")
                    return

                client = await shared_manager.get_ubus_connection_async()
                await client.uci_set_option(config, section, option, value)
                await client.uci_commit_config(config)

                if services_to_restart:
                    service_list = services_to_restart if isinstance(services_to_restart, list) else [services_to_restart]
                    for service_name in service_list:
                        try:
                            result = await client.service_action(service_name, "restart")
                            _LOGGER.info("Restarted service %s after UCI change: %s", service_name, result)
                        except Exception as exc:
                            _LOGGER.warning("Failed to restart service %s: %s", service_name, exc)

            async def async_handle_uci_network_interface(call):
                """Handle openwrt_ubus.uci_network_interface service."""
                section = call.data["section"]
                option = call.data["option"]
                shared_manager = None
                for key, value in hass.data[DOMAIN].items():
                    if key.startswith("data_manager_"):
                        shared_manager = value
                        break
                if shared_manager is None:
                    _LOGGER.error("No SharedUbusDataManager available for uci_network_interface")
                    return
                client = await shared_manager.get_ubus_connection_async()
                await client.uci_network_interface(section, option)

            hass.services.async_register(DOMAIN, "uci_get", async_handle_uci_get)
            hass.services.async_register(DOMAIN, "uci_set_commit", async_handle_uci_set_commit)
            hass.services.async_register(DOMAIN, "uci_network_interface", async_handle_uci_network_interface)

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
        # Ensure setup probes do not leave rpcd sessions behind.
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
    eth_enabled = get_config_value(entry, CONF_ENABLE_ETH_SENSORS, DEFAULT_ENABLE_ETH_SENSORS)
    mwan3_enabled = get_config_value(entry, CONF_ENABLE_MWAN3_SENSORS, DEFAULT_ENABLE_MWAN3_SENSORS)

    _LOGGER.debug("Sensor states - System: %s, QModem: %s, STA: %s, AP: %s, ETH: %s, MWAN3: %s",
                  system_enabled, qmodem_enabled, sta_enabled, ap_enabled, eth_enabled, mwan3_enabled)

    # List all current devices for debugging
    all_devices = [device for device in device_registry.devices.values()
                   if any(identifier[0] == DOMAIN for identifier in device.identifiers)]
    _LOGGER.debug("Current devices in registry: %s",
                  [list(device.identifiers) for device in all_devices])

    # If system sensors are disabled, remove the main router device
    if not system_enabled:
        main_device = device_registry.async_get_device(identifiers={(DOMAIN, host)})
        if main_device:
            _LOGGER.info("Removing main router device %s (system sensors disabled)", host)
            device_registry.async_remove_device(main_device.id)
        else:
            _LOGGER.debug("Main router device not found for removal: %s", host)
    else:
        # QModem device cleanup
        if not qmodem_enabled:
            qmodem_identifier = (DOMAIN, f"{host}_qmodem")
            qmodem_device = device_registry.async_get_device(identifiers={qmodem_identifier})
            if qmodem_device:
                _LOGGER.info("Removing QModem device %s (QModem sensors disabled)", f"{host}_qmodem")
                device_registry.async_remove_device(qmodem_device.id)

        # STA device cleanup
        if not sta_enabled:
            removed_count = 0
            for device in list(device_registry.devices.values()):
                if device.via_device_id:
                    via_device = device_registry.devices.get(device.via_device_id)
                    if via_device and (DOMAIN, host) in via_device.identifiers:
                        for identifier in device.identifiers:
                            if identifier[0] == DOMAIN:
                                device_id = identifier[1]
                                is_main = device_id == host
                                is_qmodem = "_qmodem" in device_id
                                is_ap = "_ap_" in device_id
                                is_eth = "_eth" in device_id
                                is_mwan3 = "_mwan3" in device_id
                                if not (is_main or is_qmodem or is_ap or is_eth or is_mwan3):
                                    _LOGGER.info("Removing STA device %s (STA sensors disabled)", device_id)
                                    device_registry.async_remove_device(device.id)
                                    removed_count += 1
                                    break
            _LOGGER.debug("Removed %d STA devices", removed_count)

        # AP device cleanup
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

        # ETH device cleanup
        sensors = [
            ("ETH", eth_enabled, f"{host}_eth"),
            ("MWAN3", mwan3_enabled, f"{host}_mwan3"),
        ]
        for name, enabled, device_id in sensors:
            if enabled:
                continue
            main_device = device_registry.async_get_device(identifiers={(DOMAIN, device_id)})
            if not main_device:
                continue
            removed_count = 0
            for device in list(device_registry.devices.values()):
                if device.via_device_id == main_device.id:
                    device_registry.async_remove_device(device.id)
                    removed_count += 1
            _LOGGER.info("Removing %s device %s", name, device_id)
            device_registry.async_remove_device(main_device.id)
            _LOGGER.debug("Removed %d %s sub-devices", removed_count, name)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up shared data manager
        data_manager_key = f"data_manager_{entry.entry_id}"
        if DOMAIN in hass.data and data_manager_key in hass.data[DOMAIN]:
            data_manager = hass.data[DOMAIN][data_manager_key]
            try:
                await data_manager.logout()
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
            hass.data[DOMAIN].pop("mwan3_available", None)
            hass.data[DOMAIN].pop("nlbwmon_available", None)

    return unload_ok


async def async_remove_config_entry_device(_: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry) -> bool:
    """Handle device removal."""
    host = entry.data[CONF_HOST]
    for identifier in device_entry.identifiers:
        unique_id = str(identifier[1])
        if str(identifier[0]) == DOMAIN and not (
            unique_id == host or "_ap_" in unique_id or unique_id.endswith("_qmodem")
        ):
            return True
    return False
