# Daikin AC Control

Small Python CLI and LLM skill for reading and controlling a Daikin ONECTA air
conditioner via the official Daikin cloud API. It is designed for simple
terminal automation and Codex/LLM agent workflows.

This is an independent open source project. It is not affiliated with,
endorsed by, or supported by Daikin or the AC manufacturer.

No third-party Python packages are required.

## Features

- OAuth setup against the Daikin ONECTA cloud API.
- Local refresh-token storage outside the repository.
- AC status summaries for agent and voice workflows.
- Cooling controls for power, setpoint, fixed fan level, and Daikin
  `powerfulMode`/power mode.
- Automatic single-AC selection and explicit `DAIKIN_DEVICE_ID` support for
  accounts with multiple AC devices.
- Bundled Codex/LLM skill for natural-language AC control.

## Requirements

- Python 3.11 or newer.
- A Daikin account with an AC visible in the ONECTA app.
- A Daikin Developer Portal application with the `openid`,
  `onecta:basic.integration`, and `offline_access` scopes.
- A redirect URL that you control and have registered with the Daikin
  Developer Portal.

## Quick Start

Clone the repository and create a local config file:

```bash
git clone https://github.com/randomsnowflake/daikin-ac-control.git
cd daikin-ac-control
cp .env-example .env
```

Edit `.env` with your Daikin client ID, client secret, and redirect URI. Then
create an authorization URL:

```bash
python3 -m daikin_ac_control auth-url
```

Open the URL, authorize with Daikin, copy the code from your callback handler,
and exchange it for a local token file:

```bash
python3 -m daikin_ac_control exchange-code "CODE_FROM_CALLBACK"
```

Verify access:

```bash
python3 -m daikin_ac_control status
```

## CLI Usage

Run without parameters to see the available commands:

```bash
python3 -m daikin_ac_control
```

The CLI loads `.env` from the current directory automatically, so you do not
need to source it before each command when running from the repository root.
Commands that operate on a device default to the single AC in the account.
Cooling is the assumed mode for control commands. Turning the AC on defaults to
the lowest supported cooling setpoint and fixed fan level 3/5.
Plain-text status output is a compact, labeled line suitable for agents and
voice summaries.

```bash
python3 -m daikin_ac_control status
python3 -m daikin_ac_control on
python3 -m daikin_ac_control off
python3 -m daikin_ac_control set --temperature 18 --fan-level 3
python3 -m daikin_ac_control fan-level 5
python3 -m daikin_ac_control power-mode on
```

Example status output:

```text
AC: on | mode: cooling | setpoint: 18 C | fan: 3/5 | flaps: vertical stop, horizontal swing | room: 22 C | outside: 29 C | powerful: off
```

If installed as a package, the console script is also available:

```bash
daikin-ac-control status
```

## Device Selection

If your Daikin account has exactly one AC gateway device, no device
configuration is needed. Leave `DAIKIN_DEVICE_ID` unset or empty, and the CLI
will use that single AC automatically.

If the account has multiple AC gateway devices, commands that operate on an AC
need `DAIKIN_DEVICE_ID` so the CLI knows which unit to control. Find the
available IDs with:

```bash
python3 -m daikin_ac_control devices --all
```

Then set the selected device ID in `.env` or your shell:

```bash
export DAIKIN_DEVICE_ID="your-gateway-device-id"
```

If multiple AC devices are returned and `DAIKIN_DEVICE_ID` is not set, the CLI
fails with a message listing the available IDs instead of guessing.

## Redirect URL

Register a redirect URL that you control in the Daikin developer portal, then
use the same URL as `DAIKIN_REDIRECT_URI`.

For example:

```text
https://example.com/daikin/callback
```

The callback handler does not need to call this CLI or store anything. It only
needs to:

1. Receive the OAuth redirect from Daikin.
2. Read the `code` query parameter from the request URL.
3. Show that code to the user so they can paste it into
   `python3 -m daikin_ac_control exchange-code "CODE_FROM_CALLBACK"`.

You may implement this handler as a tiny web page, server route, serverless
function, or local callback server, as long as the exact URL is registered in
the developer portal and configured in `.env`.

Here is a minimal dependency-free Python example for a local callback handler:

```python
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]

        if code:
            body = (
                "<h1>Daikin authorization code</h1>"
                "<p>Copy this code into the CLI:</p>"
                f"<pre>{escape(code)}</pre>"
            )
        else:
            body = (
                "<h1>No authorization code found</h1>"
                f"<p>Error: {escape(error or 'missing code')}</p>"
            )

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


HTTPServer(("127.0.0.1", 8765), CallbackHandler).serve_forever()
```

If you use that local example, register and configure this exact redirect URI:

```text
http://127.0.0.1:8765/callback
```

For a hosted callback, use the same handler idea behind your own HTTPS URL and
register that exact URL instead.

## Setup

Use environment variables for credentials. Do not commit them.

```bash
export DAIKIN_CLIENT_ID="..."
export DAIKIN_CLIENT_SECRET="..."
export DAIKIN_REDIRECT_URI="https://example.com/daikin/callback"
export DAIKIN_SCOPE="openid onecta:basic.integration offline_access"
# Optional when the account has exactly one AC. Required for multiple ACs.
export DAIKIN_DEVICE_ID=""
```

Create an authorization URL:

```bash
python3 -m daikin_ac_control auth-url
```

Open the URL, authorize with Daikin, then copy the code from the callback page:

```bash
python3 -m daikin_ac_control exchange-code "CODE_FROM_CALLBACK"
```

Read the AC status:

```bash
python3 -m daikin_ac_control status
```

Turn the AC on with the default cooling settings:

```bash
python3 -m daikin_ac_control on
```

The `on` command always sets cooling mode, the lowest supported cooling
temperature unless `--temperature` is passed, fixed fan level 3/5 unless
`--fan` or `--fan-level` is passed, and Daikin power mode off.

Set cooling controls:

```bash
python3 -m daikin_ac_control set --temperature 18
python3 -m daikin_ac_control set --fan-level 5
python3 -m daikin_ac_control fan-level toggle
```

`fan-level toggle` switches the fixed fan level between 3/5 and 5/5.
Daikin's literal app "power mode" switch is the `powerfulMode` boost and can be
controlled with:

```bash
python3 -m daikin_ac_control power-mode on
python3 -m daikin_ac_control power-mode off
python3 -m daikin_ac_control power-mode toggle
```

Power mode and fixed fan level are separate controls. Do not treat "power mode",
"boost", or "turbo" as fan level 5. The CLI also avoids redundant
`powerfulMode=off` writes when it is already off, because Daikin can rate-limit
unnecessary control calls.

Turn the AC off:

```bash
python3 -m daikin_ac_control off
```

Get raw JSON for debugging:

```bash
python3 -m daikin_ac_control devices --raw
```

List every gateway device instead of the default AC:

```bash
python3 -m daikin_ac_control devices --all
```

## Token Storage

Tokens are stored at:

```text
~/.config/daikin-ac-control/tokens.json
```

Override with:

```bash
export DAIKIN_TOKEN_FILE="/secure/path/tokens.json"
```

## Skill Installation

This repository includes a Codex/LLM skill at
`skills/daikin-ac-control/SKILL.md`. Install it by copying or symlinking the
skill directory into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/daikin-ac-control" ~/.codex/skills/daikin-ac-control
```

If you prefer a copy instead of a symlink:

```bash
mkdir -p ~/.codex/skills
cp -R skills/daikin-ac-control ~/.codex/skills/
```

Start a new Codex session after installing the skill so it is loaded. The skill
expects commands to run from this repository root, where `.env` can be loaded
automatically. Before using the skill, complete the setup above and verify:

```bash
python3 -m daikin_ac_control status
```

After that, you can ask your assistant for common AC tasks in natural language:

```text
What is the AC status?
Turn the AC on.
Set the AC to 18 degrees.
Set the fan level to 5.
Enable power mode.
Turn the AC off.
```

If OAuth has not been completed yet, ask the assistant to create the Daikin auth
URL. Open it yourself, authorize with Daikin, then give the assistant the
callback code so it can run `exchange-code`.

## Testing

Run the dependency-free unit suite before deploying:

```bash
python3 -m unittest discover -v
```

The tests mock the Daikin API and cover config loading, OAuth token handling,
HTTP error handling, device selection, status formatting, CLI routing, write
payloads, validation failures, and polling for confirmed state.

## Limitations

- This project uses the Daikin cloud API, so it requires internet access and a
  working Daikin cloud service.
- Control commands are intentionally narrow and cooling-focused.
- Supported devices are the AC gateway devices exposed through the tested
  ONECTA API shape. Other Daikin device classes may need additional mapping.
- The bundled LLM skill shells out to this CLI. Complete OAuth setup and verify
  `status` before expecting agent workflows to work.

## License

MIT. See [LICENSE](LICENSE).

## Server Deployment

On the live server, clone or update this repository, create a local `.env` from
`.env-example`, copy or bootstrap `~/.config/daikin-ac-control/tokens.json`, and
run:

```bash
python3 -m unittest discover -v
python3 -m daikin_ac_control status
```

If `status` succeeds, the agent can use the command map above without extra
setup. Keep `.env` and `tokens.json` out of git and readable only by the service
user.

## API Notes

This uses the official ONECTA endpoints:

```text
https://idp.onecta.daikineurope.com/v1/oidc/authorize
https://idp.onecta.daikineurope.com/v1/oidc/token
https://api.onecta.daikineurope.com/v1/gateway-devices
PATCH https://api.onecta.daikineurope.com/v1/gateway-devices/{id}/management-points/{embeddedId}/characteristics/{characteristic}
```
