"""Utility functions for PyAxencoAPI"""

from typing import Any

def find_childs(devices: list[dict[str, Any]], parent_rfid: str) -> list[str]:
    """Find child devices by parent RFID.

    Args:
        devices (list[dict[str, Any]]): List of device dictionaries.
        parent_rfid (str): RFID of the parent device.

    Returns:
        list[str]: List of `_id` values of child devices.

    """
    return [
        device["_id"]
        for device in devices
        if "parents" in device and parent_rfid in device["parents"].split(",")
    ]


def get_rfid_by_id(devices: list[dict[str, Any]], id: str) -> str:
    """Return the RFID of the device matching the given ID.

    Args:
        devices (list[dict[str, Any]]): List of device dictionaries.
        id (str): The ID of the device to search for.

    Returns:
        str: The RFID of the matching device, or an empty string if not found.

    """
    for device in devices:
        if device.get("_id") == id:
            return device.get("rfid", "")
    return ""