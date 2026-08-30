"""Device registry helpers for EVECCA assemblies."""

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .models import EveccaDevice


def evecca_device_info(device: EveccaDevice) -> DeviceInfo:
    """Build registry information and link child devices to their controller."""
    info = DeviceInfo(
        identifiers={(DOMAIN, str(device.device_id))},
        manufacturer="EVECCA",
        model=device.model,
        name=device_display_name(device),
        suggested_area=device.room_name,
        sw_version=device.firmware,
    )
    if device.parent_id is not None:
        info["via_device"] = (DOMAIN, str(device.parent_id))
    return info


def device_display_name(device: EveccaDevice) -> str:
    """Return a unique, user-readable device name."""
    parts = [part for part in (device.room_name, device.name) if part]
    name = " ".join(parts) or f"EVECCA {device.device_id}"
    return f"{name} ({str(device.device_id)[-4:]})"
