"""Command line interface for Daikin AC reads."""

from __future__ import annotations

import argparse
import json
import sys

from .client import (
    DEFAULT_FAN_SPEED,
    DEFAULT_HIGH_FAN_LEVEL,
    apply_cooling_settings,
    current_status,
    default_ac_device,
    expected_status_from_result,
    format_status,
    get_gateway_devices,
    set_powerful,
    summarize_ac_device,
    turn_off,
    turn_on_default,
    wait_for_status,
)
from .config import load_settings
from .config import Settings
from .http import ApiError
from .oauth import build_authorization_url, exchange_code


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="python3 -m daikin_ac_control")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("auth-url", help="Print the Daikin authorization URL")

    exchange = subparsers.add_parser("exchange-code", help="Exchange an OAuth code for tokens")
    exchange.add_argument("code", help="Authorization code from the callback page")

    devices = subparsers.add_parser("devices", help="Fetch the default AC gateway device")
    devices.add_argument("--all", action="store_true", help="Print all gateway devices")
    devices.add_argument("--raw", action="store_true", help="Print raw Daikin JSON")

    status = subparsers.add_parser("status", help="Print normalized status for the default AC")
    status.add_argument("--json", action="store_true", help="Print normalized JSON")

    on = subparsers.add_parser("on", help="Turn on cooling at the lowest temperature")
    on.add_argument("--temperature", "-t", type=float, help="Cooling setpoint in Celsius")
    on.add_argument(
        "--fan",
        type=int,
        default=DEFAULT_FAN_SPEED,
        help=f"Fixed fan speed, default {DEFAULT_FAN_SPEED}",
    )
    on.add_argument("--json", action="store_true", help="Print normalized JSON after the change")

    off = subparsers.add_parser("off", help="Turn the default AC off")
    off.add_argument("--json", action="store_true", help="Print normalized JSON after the change")

    set_cmd = subparsers.add_parser("set", help="Set cooling temperature, fan speed, and/or power mode")
    set_cmd.add_argument("--temperature", "-t", type=float, help="Cooling setpoint in Celsius")
    set_cmd.add_argument("--fan", "--fan-level", dest="fan", type=int, help="Fixed fan level, 1-5")
    set_cmd.add_argument(
        "--power-mode",
        choices=["on", "off", "toggle"],
        help="Literal Daikin powerful/power mode switch",
    )
    set_cmd.add_argument(
        "--powerful",
        choices=["on", "off", "toggle"],
        help="Set or toggle powerful mode",
    )
    set_cmd.add_argument("--json", action="store_true", help="Print normalized JSON after the change")

    powerful = subparsers.add_parser("powerful", help="Set or toggle powerful mode")
    powerful.add_argument("value", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    powerful.add_argument("--json", action="store_true", help="Print normalized JSON after the change")

    power_mode = subparsers.add_parser("power-mode", help="Set Daikin powerful/power mode")
    power_mode.add_argument("value", nargs="?", choices=["on", "off", "toggle"], default="on")
    power_mode.add_argument("--json", action="store_true", help="Print normalized JSON after the change")

    fan_level = subparsers.add_parser("fan-level", help="Set or toggle fixed fan level")
    fan_level.add_argument("value", nargs="?", choices=["1", "2", "3", "4", "5", "toggle"], default="toggle")
    fan_level.add_argument("--json", action="store_true", help="Print normalized JSON after the change")

    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)

    try:
        if args.command == "auth-url":
            settings = load_settings(require_secret=False)
            print(build_authorization_url(settings))
            return 0

        settings = load_settings(require_secret=True)

        if args.command == "exchange-code":
            exchange_code(settings, args.code)
            print(f"Token saved to {settings.token_file}")
            return 0

        if args.command == "devices":
            payload = get_gateway_devices(settings)
            selected = None if args.all else default_ac_device(payload, settings.device_id)
            if args.raw:
                print(json.dumps(payload if args.all else selected, indent=2, sort_keys=True))
            else:
                overviews = (
                    [_device_overview(device) for device in payload]
                    if args.all
                    else _device_overview(selected)
                )
                print(json.dumps(overviews, indent=2))
            return 0

        if args.command == "status":
            payload = get_gateway_devices(settings)
            normalized = summarize_ac_device(default_ac_device(payload, settings.device_id))
            if args.json:
                print(json.dumps(normalized, indent=2, sort_keys=True))
            else:
                print(format_status(normalized))
            return 0

        if args.command == "on":
            result = turn_on_default(settings, temperature=args.temperature, fan_speed=args.fan)
            _print_current_status(settings, args.json, expected_status_from_result(result))
            return 0

        if args.command == "off":
            result = turn_off(settings)
            _print_current_status(settings, args.json, expected_status_from_result(result))
            return 0

        if args.command == "set":
            if args.temperature is None and args.fan is None and args.power_mode is None and args.powerful is None:
                raise RuntimeError("set requires --temperature, --fan/--fan-level, --power-mode, or --powerful")
            if args.power_mode is not None and args.temperature is None and args.fan is None and args.powerful is None:
                result = set_powerful(settings, args.power_mode)
            else:
                result = apply_cooling_settings(
                    settings,
                    temperature=args.temperature,
                    fan_speed=args.fan,
                    powerful=_resolve_powerful(settings, args.powerful or args.power_mode),
                )
            _print_current_status(settings, args.json, expected_status_from_result(result))
            return 0

        if args.command == "powerful":
            result = set_powerful(settings, args.value)
            _print_current_status(settings, args.json, expected_status_from_result(result))
            return 0

        if args.command == "power-mode":
            result = set_powerful(settings, args.value)
            _print_current_status(settings, args.json, expected_status_from_result(result))
            return 0

        if args.command == "fan-level":
            fan_speed = _resolve_fan_level(settings, args.value)
            result = apply_cooling_settings(settings, fan_speed=fan_speed)
            _print_current_status(settings, args.json, expected_status_from_result(result))
            return 0

    except ApiError as exc:
        print(f"Daikin API error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 1


def _print_current_status(
    settings: Settings,
    as_json: bool,
    expected: dict[str, object] | None = None,
) -> None:
    normalized = wait_for_status(settings, expected) if expected else current_status(settings)
    if as_json:
        print(json.dumps(normalized, indent=2, sort_keys=True))
    else:
        print(format_status(normalized))


def _resolve_powerful(settings: Settings, value: str | None) -> str | None:
    if value != "toggle":
        return value
    payload = get_gateway_devices(settings)
    normalized = summarize_ac_device(default_ac_device(payload, settings.device_id))
    return "off" if normalized.get("powerful") == "on" else "on"


def _resolve_fan_level(settings: Settings, value: str) -> int:
    if value != "toggle":
        return int(value)
    payload = get_gateway_devices(settings)
    normalized = summarize_ac_device(default_ac_device(payload, settings.device_id))
    fan = normalized.get("fan") or {}
    return DEFAULT_FAN_SPEED if fan.get("speed") == DEFAULT_HIGH_FAN_LEVEL else DEFAULT_HIGH_FAN_LEVEL


def _device_overview(device: dict[str, object]) -> dict[str, object]:
    management_points = device.get("managementPoints", [])
    point_types = []
    if isinstance(management_points, list):
        point_types = [
            str(point.get("managementPointType"))
            for point in management_points
            if isinstance(point, dict)
        ]

    return {
        "id": device.get("id"),
        "deviceModel": device.get("deviceModel"),
        "managementPoints": point_types,
    }


if __name__ == "__main__":
    raise SystemExit(main())
