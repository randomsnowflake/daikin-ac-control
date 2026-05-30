from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from daikin_ac_control import __main__ as cli
from daikin_ac_control import client, config, http, oauth
from daikin_ac_control.config import Settings


def settings(token_file: Path | None = None, device_id: str | None = "device-1") -> Settings:
    return Settings(
        client_id="client",
        client_secret="secret",
        redirect_uri=config.DEFAULT_REDIRECT_URI,
        token_file=token_file or Path("/tmp/daikin-token.json"),
        scope=config.DEFAULT_SCOPE,
        device_id=device_id,
    )


def ac_device(
    *,
    device_id: str = "device-1",
    power: str = "on",
    mode: str = "cooling",
    setpoint: float = 18,
    fan_mode: str = "fixed",
    fan_speed: int = 3,
    fan_settable: bool = True,
    powerful: str = "off",
    temperature_settable: bool = True,
) -> dict[str, object]:
    return {
        "id": device_id,
        "deviceModel": "dx23",
        "managementPoints": [
            {
                "managementPointType": "gateway",
                "embeddedId": "gateway",
                "firmwareVersion": {"value": "4_2_303"},
                "ipAddress": {"value": "192.168.1.10"},
            },
            {
                "managementPointType": "climateControl",
                "embeddedId": "climateControl",
                "onOffMode": {"settable": True, "value": power, "values": ["on", "off"]},
                "operationMode": {
                    "settable": True,
                    "value": mode,
                    "values": ["auto", "dry", "cooling", "heating", "fanOnly"],
                },
                "temperatureControl": {
                    "settable": True,
                    "value": {
                        "operationModes": {
                            "cooling": {
                                "setpoints": {
                                    "roomTemperature": {
                                        "minValue": 18,
                                        "maxValue": 32,
                                        "settable": temperature_settable,
                                        "stepValue": 0.5,
                                        "value": setpoint,
                                    }
                                }
                            }
                        }
                    },
                },
                "fanControl": {
                    "settable": True,
                    "value": {
                        "operationModes": {
                            "cooling": {
                                "fanSpeed": {
                                    "currentMode": {
                                        "settable": fan_settable,
                                        "value": fan_mode,
                                        "values": ["quiet", "auto", "fixed"],
                                    },
                                    "modes": {
                                        "fixed": {
                                            "maxValue": 5,
                                            "minValue": 1,
                                            "settable": fan_settable,
                                            "stepValue": 1,
                                            "value": fan_speed,
                                        }
                                    },
                                },
                                "fanDirection": {
                                    "vertical": {"currentMode": {"value": "stop"}},
                                    "horizontal": {"currentMode": {"value": "swing"}},
                                },
                            }
                        }
                    },
                },
                "powerfulMode": {
                    "settable": True,
                    "value": powerful,
                    "values": ["on", "off"],
                },
                "econoMode": {"value": "off"},
                "sensoryData": {
                    "value": {
                        "roomTemperature": {"value": 22},
                        "outdoorTemperature": {"value": 29},
                    }
                },
            },
        ],
    }


def without_climate(device_id: str = "gateway-only") -> dict[str, object]:
    return {
        "id": device_id,
        "deviceModel": "gateway",
        "managementPoints": [{"managementPointType": "gateway", "embeddedId": "gateway"}],
    }


class FakeResponse:
    def __init__(self, body: str = "") -> None:
        self.body = body.encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class ConfigTests(unittest.TestCase):
    def test_load_settings_reads_dotenv_defaults_and_example_callback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path(".env").write_text(
                    "\n".join(
                        [
                            'export DAIKIN_CLIENT_ID="client-from-env"',
                            'DAIKIN_CLIENT_SECRET="secret-from-env"',
                            'DAIKIN_DEVICE_ID=""',
                        ]
                    ),
                    encoding="utf-8",
                )
                loaded = config.load_settings()
            finally:
                os.chdir(cwd)

        self.assertEqual(loaded.client_id, "client-from-env")
        self.assertEqual(loaded.client_secret, "secret-from-env")
        self.assertEqual(loaded.redirect_uri, "https://example.com/daikin/callback")
        self.assertEqual(loaded.scope, "openid onecta:basic.integration offline_access")
        self.assertIsNone(loaded.device_id)

    def test_environment_overrides_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"DAIKIN_CLIENT_ID": "outer"},
            clear=True,
        ):
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path(".env").write_text(
                    'DAIKIN_CLIENT_ID="inner"\nDAIKIN_CLIENT_SECRET="secret"\n',
                    encoding="utf-8",
                )
                loaded = config.load_settings()
            finally:
                os.chdir(cwd)

        self.assertEqual(loaded.client_id, "outer")
        self.assertEqual(loaded.client_secret, "secret")

    def test_missing_client_id_and_secret_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "DAIKIN_CLIENT_ID"):
                        config.load_settings()

                with patch.dict(os.environ, {"DAIKIN_CLIENT_ID": "client"}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "DAIKIN_CLIENT_SECRET"):
                        config.load_settings()
            finally:
                os.chdir(cwd)

    def test_invalid_dotenv_syntax_reports_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            path = Path(tmp) / ".env"
            path.write_text('DAIKIN_CLIENT_ID="unterminated\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Invalid .env syntax"):
                config.load_dotenv(path)


class HttpTests(unittest.TestCase):
    def test_post_form_sends_urlencoded_form(self) -> None:
        seen_request = None

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            nonlocal seen_request
            seen_request = request
            return FakeResponse('{"token": "ok"}')

        with patch("daikin_ac_control.http.urlopen", fake_urlopen):
            payload = http.post_form("https://example.test/token", {"grant_type": "refresh_token"})

        self.assertEqual(payload, {"token": "ok"})
        self.assertEqual(seen_request.get_method(), "POST")
        self.assertEqual(
            seen_request.get_header("Content-type"),
            "application/x-www-form-urlencoded",
        )
        self.assertEqual(seen_request.data.decode("utf-8"), "grant_type=refresh_token")

    def test_get_json_adds_bearer_header_and_decodes_json(self) -> None:
        seen_request = None

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            nonlocal seen_request
            seen_request = request
            self.assertEqual(timeout, 30)
            return FakeResponse('{"ok": true}')

        with patch("daikin_ac_control.http.urlopen", fake_urlopen):
            payload = http.get_json("https://example.test/data", "token")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(seen_request.get_header("Authorization"), "Bearer token")
        self.assertEqual(seen_request.get_method(), "GET")

    def test_patch_json_sends_json_patch_body(self) -> None:
        seen_request = None

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            nonlocal seen_request
            seen_request = request
            return FakeResponse("")

        with patch("daikin_ac_control.http.urlopen", fake_urlopen):
            payload = http.patch_json("https://example.test/thing", "token", {"value": 18})

        self.assertEqual(payload, {})
        self.assertEqual(seen_request.get_method(), "PATCH")
        self.assertEqual(seen_request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(seen_request.data.decode("utf-8")), {"value": 18})

    def test_http_error_includes_json_detail(self) -> None:
        error = HTTPError(
            "https://example.test",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error_description": "bad scope"}'),
        )
        with patch("daikin_ac_control.http.urlopen", side_effect=error):
            with self.assertRaises(http.ApiError) as raised:
                http.get_json("https://example.test", "token")

        self.assertEqual(raised.exception.status, 400)
        self.assertIn("bad scope", str(raised.exception))

    def test_network_error_is_wrapped(self) -> None:
        with patch("daikin_ac_control.http.urlopen", side_effect=URLError("timed out")):
            with self.assertRaisesRegex(http.ApiError, "Network error"):
                http.get_json("https://example.test", "token")

    def test_invalid_json_and_empty_body(self) -> None:
        with patch("daikin_ac_control.http.urlopen", return_value=FakeResponse("")):
            self.assertEqual(http.get_json("https://example.test", "token"), {})

        with patch("daikin_ac_control.http.urlopen", return_value=FakeResponse("nope")):
            with self.assertRaisesRegex(http.ApiError, "Expected JSON"):
                http.get_json("https://example.test", "token")


class OAuthTests(unittest.TestCase):
    def test_build_authorization_url_uses_scope_and_example_callback(self) -> None:
        url = oauth.build_authorization_url(settings(device_id=None), state="state")
        self.assertIn("redirect_uri=https%3A%2F%2Fexample.com%2Fdaikin%2Fcallback", url)
        self.assertIn("scope=openid+onecta%3Abasic.integration+offline_access", url)
        self.assertIn("state=state", url)

    def test_save_token_adds_expiry_and_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            with patch("daikin_ac_control.oauth.time.time", return_value=1000):
                oauth.save_token(path, {"access_token": "a", "expires_in": 3600})

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["expires_at"], 4600)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_exchange_code_and_refresh_token_persist_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded_settings = settings(Path(tmp) / "tokens.json")

            with patch(
                "daikin_ac_control.oauth.post_form",
                return_value={"access_token": "from-code"},
            ) as post_form:
                self.assertEqual(
                    oauth.exchange_code(loaded_settings, "code")["access_token"],
                    "from-code",
                )
            self.assertEqual(json.loads(loaded_settings.token_file.read_text())["access_token"], "from-code")
            self.assertEqual(post_form.call_args.args[1]["grant_type"], "authorization_code")
            self.assertEqual(post_form.call_args.args[1]["code"], "code")

            with patch(
                "daikin_ac_control.oauth.post_form",
                return_value={"access_token": "from-refresh"},
            ) as post_form:
                self.assertEqual(
                    oauth.refresh_token(loaded_settings, "refresh")["access_token"],
                    "from-refresh",
                )
            self.assertEqual(post_form.call_args.args[1]["grant_type"], "refresh_token")
            self.assertEqual(post_form.call_args.args[1]["refresh_token"], "refresh")

    def test_access_token_refreshes_expired_token_and_requires_access_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "tokens.json"
            token_file.write_text('{"expires_at": 1, "refresh_token": "refresh"}', encoding="utf-8")
            loaded_settings = settings(token_file)

            with patch("daikin_ac_control.oauth.time.time", return_value=1000), patch(
                "daikin_ac_control.oauth.refresh_token",
                return_value={"access_token": "new"},
            ) as refresh:
                self.assertEqual(oauth.access_token(loaded_settings), "new")

            refresh.assert_called_once_with(loaded_settings, "refresh")

            token_file.write_text('{"expires_at": 9999999999}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "access_token"):
                oauth.access_token(loaded_settings)

    def test_missing_or_expired_token_errors_are_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded_settings = settings(Path(tmp) / "missing.json")
            with self.assertRaisesRegex(RuntimeError, "No token file"):
                oauth.access_token(loaded_settings)

            loaded_settings.token_file.write_text('{"expires_at": 1}', encoding="utf-8")
            with patch("daikin_ac_control.oauth.time.time", return_value=1000):
                with self.assertRaisesRegex(RuntimeError, "refresh_token"):
                    oauth.access_token(loaded_settings)


class ClientSelectionAndSummaryTests(unittest.TestCase):
    def test_get_gateway_devices_requires_list_payload(self) -> None:
        with patch("daikin_ac_control.client.access_token", return_value="token"), patch(
            "daikin_ac_control.client.get_json",
            return_value={"not": "a list"},
        ):
            with self.assertRaisesRegex(RuntimeError, "return a list"):
                client.get_gateway_devices(settings())

    def test_default_device_selects_only_climate_device_or_configured_device(self) -> None:
        climate = ac_device(device_id="climate")
        gateway = without_climate()

        self.assertEqual(client.default_ac_device([gateway, climate])["id"], "climate")
        self.assertEqual(client.default_ac_device([gateway, climate], "gateway-only")["id"], "gateway-only")

    def test_default_device_failure_edges(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no gateway devices"):
            client.default_ac_device([])
        with self.assertRaisesRegex(RuntimeError, "multiple AC"):
            client.default_ac_device([ac_device(device_id="a"), ac_device(device_id="b")])
        with self.assertRaisesRegex(RuntimeError, "no AC"):
            client.default_ac_device([without_climate(), "bad"])
        with self.assertRaisesRegex(RuntimeError, "device-404"):
            client.default_ac_device([ac_device()], "device-404")

    def test_summarize_and_format_status_are_readable(self) -> None:
        status = client.summarize_ac_device(ac_device())
        self.assertEqual(status["device_id"], "device-1")
        self.assertEqual(status["setpoint_c"], 18)
        self.assertEqual(status["fan"]["speed"], 3)

        formatted = client.format_status(status)
        self.assertEqual(
            formatted,
            "AC: on | mode: cooling | setpoint: 18 C | fan: 3/5 | "
            "flaps: vertical stop, horizontal swing | room: 22 C | outside: 29 C | powerful: off",
        )

        off = client.summarize_ac_device(ac_device(power="off"))
        self.assertTrue(client.format_status(off).startswith("AC: off"))

    def test_summarize_errors_without_climate_control(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "climateControl"):
            client.summarize_ac_device(without_climate())


class ClientValidationAndWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = settings()
        self.device = ac_device()
        self.climate = self.device["managementPoints"][1]

    def test_temperature_validation_good_bad_and_edges(self) -> None:
        self.assertEqual(client.validate_temperature(self.climate, 18), 18)
        self.assertEqual(client.validate_temperature(self.climate, 18.5), 18.5)
        self.assertEqual(client.validate_temperature(self.climate, 32), 32)

        with self.assertRaisesRegex(RuntimeError, "between 18 C and 32 C"):
            client.validate_temperature(self.climate, 17.5)
        with self.assertRaisesRegex(RuntimeError, "0.5 C steps"):
            client.validate_temperature(self.climate, 18.25)
        with self.assertRaisesRegex(RuntimeError, "not settable"):
            client.validate_temperature(ac_device(temperature_settable=False)["managementPoints"][1], 18)

    def test_fan_validation_good_bad_and_edges(self) -> None:
        self.assertEqual(client.validate_fan_speed(self.climate, 1), 1)
        self.assertEqual(client.validate_fan_speed(self.climate, 5), 5)

        with self.assertRaisesRegex(RuntimeError, "between 1 and 5"):
            client.validate_fan_speed(self.climate, 6)
        with self.assertRaisesRegex(RuntimeError, "not settable"):
            client.validate_fan_speed(ac_device(fan_settable=False)["managementPoints"][1], 3)

    def test_apply_cooling_settings_default_on_patches_expected_characteristics(self) -> None:
        with patch("daikin_ac_control.client.get_gateway_devices", return_value=[self.device]), patch(
            "daikin_ac_control.client.access_token",
            return_value="token",
        ), patch("daikin_ac_control.client.patch_json", return_value={}) as patch_json:
            result = client.turn_on_default(self.settings)

        self.assertEqual(result["power"], "on")
        self.assertEqual(result["mode"], "cooling")
        self.assertEqual(result["temperature"], 18)
        self.assertEqual(result["fan_speed"], 3)
        self.assertEqual(result["powerful"], "off")

        calls = [call.args[2] for call in patch_json.call_args_list]
        self.assertEqual(
            calls,
            [
                {"value": "on"},
                {"value": "cooling"},
                {"value": 18, "path": "/operationModes/cooling/setpoints/roomTemperature"},
                {"value": "fixed", "path": "/operationModes/cooling/fanSpeed/currentMode"},
                {"value": 3, "path": "/operationModes/cooling/fanSpeed/modes/fixed"},
            ],
        )

    def test_apply_cooling_settings_fan_only_preserves_existing_temperature(self) -> None:
        with patch("daikin_ac_control.client.get_gateway_devices", return_value=[self.device]), patch(
            "daikin_ac_control.client.access_token",
            return_value="token",
        ), patch("daikin_ac_control.client.patch_json", return_value={}) as patch_json:
            result = client.apply_cooling_settings(self.settings, fan_speed=5)

        self.assertIsNone(result["temperature"])
        calls = [call.args[2] for call in patch_json.call_args_list]
        self.assertEqual(
            calls,
            [
                {"value": "cooling"},
                {"value": "fixed", "path": "/operationModes/cooling/fanSpeed/currentMode"},
                {"value": 5, "path": "/operationModes/cooling/fanSpeed/modes/fixed"},
            ],
        )

    def test_apply_cooling_settings_waits_for_fan_to_unlock_after_powerful_off(self) -> None:
        locked = ac_device(fan_settable=False, powerful="on")
        unlocked = ac_device(fan_settable=True, powerful="off")

        with patch(
            "daikin_ac_control.client.get_gateway_devices",
            side_effect=[[locked], [unlocked]],
        ), patch("daikin_ac_control.client.access_token", return_value="token"), patch(
            "daikin_ac_control.client.patch_json",
            return_value={},
        ), patch("daikin_ac_control.client.time.sleep") as sleep:
            result = client.apply_cooling_settings(self.settings, fan_speed=5)

        self.assertEqual(result["fan_speed"], 5)
        self.assertEqual(result["powerful"], "off")
        sleep.assert_not_called()

    def test_apply_cooling_settings_errors_when_fan_never_unlocks(self) -> None:
        locked = ac_device(fan_settable=False, powerful="on")

        with patch("daikin_ac_control.client.get_gateway_devices", return_value=[locked]), patch(
            "daikin_ac_control.client.access_token",
            return_value="token",
        ), patch("daikin_ac_control.client.patch_json", return_value={}), patch(
            "daikin_ac_control.client.time.monotonic",
            side_effect=[0, 100],
        ), patch("daikin_ac_control.client.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "not settable"):
                client.apply_cooling_settings(self.settings, fan_speed=5)

    def test_turn_off_and_powerful_toggle(self) -> None:
        powerful_on = ac_device(powerful="on")
        with patch("daikin_ac_control.client.get_gateway_devices", return_value=[powerful_on]), patch(
            "daikin_ac_control.client.access_token",
            return_value="token",
        ), patch("daikin_ac_control.client.patch_json", return_value={}) as patch_json:
            self.assertEqual(client.turn_off(self.settings)["power"], "off")
            self.assertEqual(client.set_powerful(self.settings, "toggle")["powerful"], "off")

        self.assertEqual(patch_json.call_args_list[0].args[2], {"value": "off"})
        self.assertEqual(patch_json.call_args_list[1].args[2], {"value": "off"})

    def test_patch_characteristic_builds_daikin_url(self) -> None:
        with patch("daikin_ac_control.client.access_token", return_value="token"), patch(
            "daikin_ac_control.client.patch_json",
            return_value={"ok": True},
        ) as patch_json:
            payload = client.patch_characteristic(
                self.settings,
                "device-1",
                "climateControl",
                "temperatureControl",
                "/path",
                18,
            )

        self.assertEqual(payload, {"ok": True})
        url, token, body = patch_json.call_args.args
        self.assertEqual(token, "token")
        self.assertTrue(url.endswith("/v1/gateway-devices/device-1/management-points/climateControl/characteristics/temperatureControl"))
        self.assertEqual(body, {"value": 18, "path": "/path"})

    def test_wait_for_status_polls_until_expected_state_or_times_out(self) -> None:
        old = ac_device(fan_speed=3)
        new = ac_device(fan_speed=5)

        with patch("daikin_ac_control.client.get_gateway_devices", side_effect=[[old], [new]]), patch(
            "daikin_ac_control.client.access_token",
            return_value="token",
        ), patch("daikin_ac_control.client.time.sleep") as sleep:
            status = client.wait_for_status(self.settings, {"fan_speed": 5})

        self.assertEqual(status["fan"]["speed"], 5)
        sleep.assert_called_once()

        with patch("daikin_ac_control.client.get_gateway_devices", return_value=[old]), patch(
            "daikin_ac_control.client.access_token",
            return_value="token",
        ), patch("daikin_ac_control.client.time.monotonic", side_effect=[0, 100]), patch(
            "daikin_ac_control.client.time.sleep"
        ):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                client.wait_for_status(self.settings, {"fan_speed": 5})

    def test_expected_status_from_result_omits_none_during_wait(self) -> None:
        expected = client.expected_status_from_result(
            {"power": "on", "mode": "cooling", "temperature": None, "fan_speed": 3}
        )
        self.assertEqual(
            expected,
            {
                "power": "on",
                "mode": "cooling",
                "setpoint_c": None,
                "fan_speed": 3,
                "powerful": None,
            },
        )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = settings()
        self.device = ac_device()
        self.status = client.summarize_ac_device(self.device)

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_args_prints_help(self) -> None:
        code, out, err = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("usage: python3 -m daikin_ac_control", out)
        self.assertEqual(err, "")

    def test_status_plain_and_json(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.get_gateway_devices",
            return_value=[self.device],
        ):
            code, out, err = self.run_cli(["status"])
            self.assertEqual(code, 0)
            self.assertIn("AC: on", out)
            self.assertEqual(err, "")

            code, out, err = self.run_cli(["status", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["setpoint_c"], 18)
            self.assertEqual(err, "")

    def test_devices_default_all_and_raw(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.get_gateway_devices",
            return_value=[self.device],
        ):
            code, out, _ = self.run_cli(["devices"])
            self.assertEqual(code, 0)
            self.assertIsInstance(json.loads(out), dict)

            code, out, _ = self.run_cli(["devices", "--all"])
            self.assertEqual(code, 0)
            self.assertIsInstance(json.loads(out), list)

            code, out, _ = self.run_cli(["devices", "--raw"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["id"], "device-1")

    def test_control_commands_print_confirmed_status(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.turn_on_default",
            return_value={"power": "on", "mode": "cooling", "temperature": 18, "fan_speed": 3, "powerful": "off"},
        ) as turn_on, patch("daikin_ac_control.__main__.wait_for_status", return_value=self.status) as wait:
            code, out, err = self.run_cli(["on"])

        self.assertEqual(code, 0)
        turn_on.assert_called_once()
        wait.assert_called_once()
        self.assertIn("setpoint: 18 C", out)
        self.assertEqual(err, "")

    def test_off_command_prints_confirmed_status(self) -> None:
        off_status = client.summarize_ac_device(ac_device(power="off"))
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.turn_off",
            return_value={"power": "off"},
        ) as turn_off, patch("daikin_ac_control.__main__.wait_for_status", return_value=off_status):
            code, out, err = self.run_cli(["off"])

        self.assertEqual(code, 0)
        turn_off.assert_called_once_with(self.settings)
        self.assertIn("AC: off", out)
        self.assertEqual(err, "")

    def test_set_requires_a_setting(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings):
            code, out, err = self.run_cli(["set"])

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("set requires", err)

    def test_power_mode_controls_literal_powerful_mode(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.set_powerful",
            return_value={"powerful": "on"},
        ) as set_powerful, patch("daikin_ac_control.__main__.wait_for_status", return_value=self.status):
            code, _, err = self.run_cli(["power-mode"])

        self.assertEqual(code, 0)
        set_powerful.assert_called_once_with(self.settings, "on")
        self.assertEqual(err, "")

    def test_fan_level_controls_fixed_fan_speed(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.apply_cooling_settings",
            return_value={"mode": "cooling", "temperature": 18, "fan_speed": 5, "powerful": "off"},
        ) as apply, patch("daikin_ac_control.__main__.wait_for_status", return_value=self.status):
            code, _, err = self.run_cli(["fan-level", "5"])

        self.assertEqual(code, 0)
        apply.assert_called_once_with(self.settings, fan_speed=5)
        self.assertEqual(err, "")

    def test_set_power_mode_controls_literal_powerful_mode(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.set_powerful",
            return_value={"powerful": "on"},
        ) as set_powerful, patch("daikin_ac_control.__main__.wait_for_status", return_value=self.status):
            code, _, err = self.run_cli(["set", "--power-mode", "on"])

        self.assertEqual(code, 0)
        set_powerful.assert_called_once_with(self.settings, "on")
        self.assertEqual(err, "")

    def test_powerful_toggle_uses_client_result(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.set_powerful",
            return_value={"powerful": "on"},
        ) as set_powerful, patch("daikin_ac_control.__main__.wait_for_status", return_value=self.status):
            code, _, err = self.run_cli(["powerful"])

        self.assertEqual(code, 0)
        set_powerful.assert_called_once_with(self.settings, "toggle")
        self.assertEqual(err, "")

    def test_auth_url_and_api_error_output(self) -> None:
        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.build_authorization_url",
            return_value="https://auth.example",
        ):
            code, out, err = self.run_cli(["auth-url"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "https://auth.example")
        self.assertEqual(err, "")

        with patch("daikin_ac_control.__main__.load_settings", return_value=self.settings), patch(
            "daikin_ac_control.__main__.get_gateway_devices",
            side_effect=http.ApiError("HTTP 500 from Daikin"),
        ):
            code, out, err = self.run_cli(["status"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Daikin API error: HTTP 500", err)


if __name__ == "__main__":
    unittest.main()
