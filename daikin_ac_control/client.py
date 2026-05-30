"""Daikin ONECTA API client and status normalizer."""

from __future__ import annotations

import time
from typing import Any

from .config import API_BASE_URL, Settings
from .http import get_json, patch_json
from .oauth import access_token

DEFAULT_OPERATION_MODE = "cooling"
DEFAULT_FAN_SPEED = 3
DEFAULT_HIGH_FAN_LEVEL = 5
SETPOINT_NAME = "roomTemperature"
FAN_UNLOCK_TIMEOUT_SECONDS = 20
STATUS_CONFIRM_TIMEOUT_SECONDS = 35
POLL_INTERVAL_SECONDS = 2


def get_gateway_devices(settings: Settings) -> list[dict[str, Any]]:
    token = access_token(settings)
    payload = get_json(f"{API_BASE_URL}/v1/gateway-devices", token)
    if not isinstance(payload, list):
        raise RuntimeError("Expected /v1/gateway-devices to return a list")
    return payload


def summarize_single_ac(devices: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize_ac_device(default_ac_device(devices))


def default_ac_device(devices: list[dict[str, Any]], device_id: str | None = None) -> dict[str, Any]:
    if not devices:
        raise RuntimeError("Daikin returned no gateway devices")

    if device_id:
        for device in devices:
            if isinstance(device, dict) and device.get("id") == device_id:
                return device
        raise RuntimeError(f"No Daikin gateway device found with id {device_id}")

    climate_devices = [
        device
        for device in devices
        if isinstance(device, dict)
        and _find_management_point(device.get("managementPoints", []), "climateControl")
        is not None
    ]

    if len(climate_devices) == 1:
        return climate_devices[0]
    if not climate_devices and len(devices) == 1 and isinstance(devices[0], dict):
        return devices[0]
    if not climate_devices:
        raise RuntimeError("Daikin returned no AC gateway device")

    ids = ", ".join(str(device.get("id")) for device in climate_devices)
    raise RuntimeError(
        "Daikin returned multiple AC gateway devices; set DAIKIN_DEVICE_ID to one of: "
        + ids
    )


def summarize_ac_device(device: dict[str, Any]) -> dict[str, Any]:
    management_points = device.get("managementPoints", [])
    climate = _find_management_point(management_points, "climateControl")
    gateway = _find_management_point(management_points, "gateway")

    if climate is None:
        raise RuntimeError("First gateway device has no climateControl management point")

    mode = _characteristic_value(climate, "operationMode")
    on_off = _characteristic_value(climate, "onOffMode")

    return {
        "device_id": device.get("id"),
        "device_name": device.get("deviceModel") or device.get("name"),
        "embedded_id": climate.get("embeddedId"),
        "power": on_off,
        "mode": mode,
        "setpoint_c": _room_setpoint(climate, mode),
        "room_temperature_c": _sensory_value(climate, "roomTemperature"),
        "outdoor_temperature_c": _sensory_value(climate, "outdoorTemperature"),
        "fan": _fan_status(climate, mode),
        "flaps": _flap_status(climate, mode),
        "powerful": _characteristic_value(climate, "powerfulMode"),
        "econo": _characteristic_value(climate, "econoMode"),
        "streamer": _characteristic_value(climate, "streamerMode")
        or _characteristic_value(climate, "airPurificationMode"),
        "gateway": _gateway_status(gateway),
    }


def apply_cooling_settings(
    settings: Settings,
    temperature: float | None = None,
    fan_speed: int | None = None,
    power: str | None = None,
    powerful: str | None = None,
    use_default_temperature: bool = False,
) -> dict[str, Any]:
    device = default_ac_device(get_gateway_devices(settings), settings.device_id)
    climate = _climate_control(device)
    device_id = _device_id(device)
    embedded_id = str(climate.get("embeddedId") or "climateControl")

    if power is not None:
        _validate_choice(climate, "onOffMode", power)
        patch_characteristic(settings, device_id, embedded_id, "onOffMode", None, power)

    _validate_choice(climate, "operationMode", DEFAULT_OPERATION_MODE)
    patch_characteristic(settings, device_id, embedded_id, "operationMode", None, DEFAULT_OPERATION_MODE)

    target_temperature = None
    if temperature is not None or use_default_temperature:
        target_temperature = (
            min_cooling_temperature(climate)
            if temperature is None
            else validate_temperature(climate, temperature)
        )
        patch_characteristic(
            settings,
            device_id,
            embedded_id,
            "temperatureControl",
            f"/operationModes/{DEFAULT_OPERATION_MODE}/setpoints/{SETPOINT_NAME}",
            target_temperature,
        )

    effective_powerful = "off" if fan_speed is not None and powerful is None else powerful
    if effective_powerful == "off":
        _validate_choice(climate, "powerfulMode", "off")
        if _characteristic_value(climate, "powerfulMode") != "off":
            patch_characteristic(settings, device_id, embedded_id, "powerfulMode", None, "off")
            device, climate = _wait_for_fan_settable(settings)

    if fan_speed is not None:
        validate_fan_speed(climate, fan_speed)
        fan_prefix = f"/operationModes/{DEFAULT_OPERATION_MODE}/fanSpeed"
        patch_characteristic(
            settings,
            device_id,
            embedded_id,
            "fanControl",
            f"{fan_prefix}/currentMode",
            "fixed",
        )
        patch_characteristic(
            settings,
            device_id,
            embedded_id,
            "fanControl",
            f"{fan_prefix}/modes/fixed",
            fan_speed,
        )

    if effective_powerful == "on":
        _validate_choice(climate, "powerfulMode", "on")
        patch_characteristic(settings, device_id, embedded_id, "powerfulMode", None, "on")

    return {
        "device_id": device_id,
        "embedded_id": embedded_id,
        "mode": DEFAULT_OPERATION_MODE,
        "power": power,
        "temperature": target_temperature,
        "fan_speed": fan_speed,
        "powerful": effective_powerful,
    }


def turn_on_default(settings: Settings, temperature: float | None = None, fan_speed: int = DEFAULT_FAN_SPEED) -> dict[str, Any]:
    return apply_cooling_settings(
        settings,
        temperature=temperature,
        fan_speed=fan_speed,
        power="on",
        powerful="off",
        use_default_temperature=True,
    )


def turn_off(settings: Settings) -> dict[str, Any]:
    device = default_ac_device(get_gateway_devices(settings), settings.device_id)
    climate = _climate_control(device)
    device_id = _device_id(device)
    embedded_id = str(climate.get("embeddedId") or "climateControl")
    patch_characteristic(settings, device_id, embedded_id, "onOffMode", None, "off")
    return {"device_id": device_id, "embedded_id": embedded_id, "power": "off"}


def set_powerful(settings: Settings, value: str) -> dict[str, Any]:
    device = default_ac_device(get_gateway_devices(settings), settings.device_id)
    climate = _climate_control(device)
    current = _characteristic_value(climate, "powerfulMode")
    target = "off" if current == "on" else "on" if value == "toggle" else value
    _validate_choice(climate, "powerfulMode", target)
    device_id = _device_id(device)
    embedded_id = str(climate.get("embeddedId") or "climateControl")
    patch_characteristic(settings, device_id, embedded_id, "powerfulMode", None, target)
    return {"device_id": device_id, "embedded_id": embedded_id, "powerful": target}


def patch_characteristic(
    settings: Settings,
    device_id: str,
    embedded_id: str,
    characteristic: str,
    path: str | None,
    value: Any,
) -> Any:
    token = access_token(settings)
    url = (
        f"{API_BASE_URL}/v1/gateway-devices/{device_id}"
        f"/management-points/{embedded_id}/characteristics/{characteristic}"
    )
    payload = {"value": value}
    if path:
        payload["path"] = path
    return patch_json(url, token, payload)


def min_cooling_temperature(climate: dict[str, Any]) -> float:
    return float(_cooling_setpoint(climate).get("minValue"))


def validate_temperature(climate: dict[str, Any], temperature: float) -> float:
    setpoint = _cooling_setpoint(climate)
    value = float(temperature)
    minimum = float(setpoint.get("minValue"))
    maximum = float(setpoint.get("maxValue"))
    step = float(setpoint.get("stepValue") or 1)
    if value < minimum or value > maximum:
        raise RuntimeError(f"Cooling temperature must be between {minimum:g} C and {maximum:g} C")

    steps = round((value - minimum) / step)
    if abs(minimum + steps * step - value) > 1e-9:
        raise RuntimeError(f"Cooling temperature must use {step:g} C steps")

    return int(value) if value.is_integer() else value


def validate_fan_speed(climate: dict[str, Any], fan_speed: int) -> int:
    fixed = _cooling_fixed_fan(climate)
    minimum = int(fixed.get("minValue"))
    maximum = int(fixed.get("maxValue"))
    step = int(fixed.get("stepValue") or 1)
    if fan_speed < minimum or fan_speed > maximum:
        raise RuntimeError(f"Fan speed must be between {minimum} and {maximum}")
    if (fan_speed - minimum) % step:
        raise RuntimeError(f"Fan speed must use {step} steps")
    return fan_speed


def current_status(settings: Settings) -> dict[str, Any]:
    payload = get_gateway_devices(settings)
    return summarize_ac_device(default_ac_device(payload, settings.device_id))


def wait_for_status(
    settings: Settings,
    expected: dict[str, Any] | None = None,
    timeout: int = STATUS_CONFIRM_TIMEOUT_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    expected = {key: value for key, value in (expected or {}).items() if value is not None}
    status = current_status(settings)

    while expected and not _status_matches(status, expected):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Daikin did not confirm the requested state. Last status: "
                + format_status(status)
            )
        time.sleep(interval)
        status = current_status(settings)

    return status


def expected_status_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "power": result.get("power"),
        "mode": result.get("mode"),
        "setpoint_c": result.get("temperature"),
        "fan_speed": result.get("fan_speed"),
        "powerful": result.get("powerful"),
    }


def format_status(status: dict[str, Any]) -> str:
    parts: list[str] = []
    power = status.get("power")
    mode = status.get("mode")
    setpoint = status.get("setpoint_c")

    if power == "off":
        parts.append("AC: off")
    else:
        parts.append("AC: on")
        if status.get("powerful") == "on":
            parts.append("powerful: on")
        if mode:
            parts.append(f"mode: {mode}")
        if setpoint is not None:
            parts.append(f"setpoint: {setpoint:g} C")

    fan = status.get("fan") or {}
    fan_mode = fan.get("mode")
    fan_speed = fan.get("speed")
    if fan_mode == "fixed" and fan_speed is not None:
        max_speed = fan.get("max")
        suffix = f"/{max_speed}" if max_speed else ""
        parts.append(f"fan: {fan_speed}{suffix}")
    elif fan_mode:
        parts.append(f"fan: {fan_mode}")

    flaps = status.get("flaps") or {}
    flap_bits = [
        f"{name} {value}"
        for name, value in flaps.items()
        if value is not None
    ]
    if flap_bits:
        parts.append("flaps: " + ", ".join(flap_bits))

    room = status.get("room_temperature_c")
    outdoor = status.get("outdoor_temperature_c")
    temps = []
    if room is not None:
        temps.append(f"room: {room:g} C")
    if outdoor is not None:
        temps.append(f"outside: {outdoor:g} C")
    parts.extend(temps)

    if power != "off" and status.get("powerful") not in (None, "on"):
        parts.append(f"powerful: {status.get('powerful')}")

    return " | ".join(parts)


def _find_management_point(
    management_points: Any,
    management_point_type: str,
) -> dict[str, Any] | None:
    if not isinstance(management_points, list):
        return None

    for point in management_points:
        if isinstance(point, dict) and point.get("managementPointType") == management_point_type:
            return point
    return None


def _climate_control(device: dict[str, Any]) -> dict[str, Any]:
    climate = _find_management_point(device.get("managementPoints", []), "climateControl")
    if climate is None:
        raise RuntimeError("Default gateway device has no climateControl management point")
    return climate


def _device_id(device: dict[str, Any]) -> str:
    device_id = device.get("id")
    if not device_id:
        raise RuntimeError("Default gateway device has no id")
    return str(device_id)


def _wait_for_fan_settable(settings: Settings) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + FAN_UNLOCK_TIMEOUT_SECONDS
    last_error = "Fixed cooling fan speed is not settable"

    while True:
        device = default_ac_device(get_gateway_devices(settings), settings.device_id)
        climate = _climate_control(device)
        try:
            _cooling_fixed_fan(climate)
            return device, climate
        except RuntimeError as exc:
            last_error = str(exc)

        if time.monotonic() >= deadline:
            raise RuntimeError(last_error)
        time.sleep(POLL_INTERVAL_SECONDS)


def _status_matches(status: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        if key == "fan_speed":
            actual = (status.get("fan") or {}).get("speed")
        else:
            actual = status.get(key)
        if actual != expected_value:
            return False
    return True


def _validate_choice(climate: dict[str, Any], characteristic: str, value: str) -> None:
    item = climate.get(characteristic)
    if not isinstance(item, dict):
        raise RuntimeError(f"Device does not expose {characteristic}")
    if item.get("settable") is not True:
        raise RuntimeError(f"{characteristic} is not settable")
    values = item.get("values", [])
    if isinstance(values, list) and value not in values:
        raise RuntimeError(f"{characteristic} must be one of: {', '.join(map(str, values))}")


def _characteristic_value(point: dict[str, Any], name: str) -> Any:
    characteristic = point.get(name)
    if isinstance(characteristic, dict):
        return characteristic.get("value")
    return None


def _cooling_setpoint(climate: dict[str, Any]) -> dict[str, Any]:
    temperature_control = _characteristic_value(climate, "temperatureControl")
    if not isinstance(temperature_control, dict):
        raise RuntimeError("Device does not expose temperatureControl")
    setpoint = (
        temperature_control.get("operationModes", {})
        .get(DEFAULT_OPERATION_MODE, {})
        .get("setpoints", {})
        .get(SETPOINT_NAME)
    )
    if not isinstance(setpoint, dict):
        raise RuntimeError("Device does not expose cooling room temperature control")
    if setpoint.get("settable") is not True:
        raise RuntimeError("Cooling room temperature is not settable")
    return setpoint


def _cooling_fixed_fan(climate: dict[str, Any]) -> dict[str, Any]:
    fan_control = _characteristic_value(climate, "fanControl")
    if not isinstance(fan_control, dict):
        raise RuntimeError("Device does not expose fanControl")
    fixed = (
        fan_control.get("operationModes", {})
        .get(DEFAULT_OPERATION_MODE, {})
        .get("fanSpeed", {})
        .get("modes", {})
        .get("fixed")
    )
    if not isinstance(fixed, dict):
        raise RuntimeError("Device does not expose fixed cooling fan speed")
    if fixed.get("settable") is not True:
        raise RuntimeError("Fixed cooling fan speed is not settable")
    return fixed


def _room_setpoint(climate: dict[str, Any], mode: Any) -> float | None:
    temperature_control = _characteristic_value(climate, "temperatureControl")
    if not isinstance(temperature_control, dict) or not isinstance(mode, str):
        return None

    operation_modes = temperature_control.get("operationModes", {})
    mode_info = operation_modes.get(mode, {})
    room = mode_info.get("setpoints", {}).get("roomTemperature", {})
    value = room.get("value")
    return float(value) if isinstance(value, int | float) else None


def _sensory_value(climate: dict[str, Any], name: str) -> float | None:
    sensory_data = _characteristic_value(climate, "sensoryData")
    if not isinstance(sensory_data, dict):
        return None
    item = sensory_data.get(name)
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    return float(value) if isinstance(value, int | float) else None


def _fan_status(climate: dict[str, Any], mode: Any) -> dict[str, Any]:
    fan_control = _characteristic_value(climate, "fanControl")
    if not isinstance(fan_control, dict) or not isinstance(mode, str):
        return {}

    operation_mode = fan_control.get("operationModes", {}).get(mode, {})
    fan_speed = operation_mode.get("fanSpeed", {})
    current_mode = fan_speed.get("currentMode", {}).get("value")
    fixed = fan_speed.get("modes", {}).get("fixed", {})

    return {
        "mode": current_mode,
        "speed": fixed.get("value") if current_mode == "fixed" else None,
        "min": fixed.get("minValue"),
        "max": fixed.get("maxValue"),
        "step": fixed.get("stepValue"),
    }


def _flap_status(climate: dict[str, Any], mode: Any) -> dict[str, Any]:
    fan_control = _characteristic_value(climate, "fanControl")
    if not isinstance(fan_control, dict) or not isinstance(mode, str):
        return {}

    operation_mode = fan_control.get("operationModes", {}).get(mode, {})
    direction = operation_mode.get("fanDirection", {})

    return {
        "vertical": direction.get("vertical", {}).get("currentMode", {}).get("value"),
        "horizontal": direction.get("horizontal", {}).get("currentMode", {}).get("value"),
    }


def _gateway_status(gateway: dict[str, Any] | None) -> dict[str, Any]:
    if gateway is None:
        return {}

    values = {}
    for key in (
        "wifiConnectionSSID",
        "ipAddress",
        "macAddress",
        "firmwareVersion",
        "signalStrength",
    ):
        value = _characteristic_value(gateway, key)
        if value is not None:
            values[key] = value
    return values
