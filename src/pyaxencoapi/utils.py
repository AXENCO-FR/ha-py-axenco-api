"""Utility functions for PyAxencoAPI"""

from collections.abc import Sequence

from .models import AxencoDevice


def find_childs(devices: Sequence[AxencoDevice], parent_rfid: str) -> list[str]:
    """Find child devices by parent RFID.

    Args:
        devices: Device snapshots.
        parent_rfid (str): RFID of the parent device.

    Returns:
        list[str]: List of `_id` values of child devices.

    """
    return [device.id for device in devices if parent_rfid in device.parents]


def get_rfid_by_id(devices: Sequence[AxencoDevice], id: str) -> str:
    """Return the RFID of the device matching the given ID.

    Args:
        devices: Device snapshots.
        id (str): The ID of the device to search for.

    Returns:
        str: The RFID of the matching device, or an empty string if not found.

    """
    for device in devices:
        if device.id == id:
            return device.rfid or ""
    return ""
