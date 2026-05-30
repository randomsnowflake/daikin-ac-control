---
name: daikin-ac-control
description: Read/control a Daikin ONECTA AC with the local CLI. Use for common voice intents: AC status, turn AC on/off, set temperature, set fixed fan level, and set/toggle literal Daikin power mode.
---

# Daikin AC Control

Run commands from the local repository root. The CLI auto-loads `.env` from the current directory. Never expose OAuth credentials, tokens, or raw token files in prompts, logs, commits, screenshots, or replies.

The OAuth callback URL must be a URL controlled by the user and registered in
the Daikin developer portal. The handler only needs to receive the redirect,
read the `code` query parameter, and display it so it can be pasted into:

```text
python3 -m daikin_ac_control exchange-code "CODE_FROM_CALLBACK"
```

Defaults: commands target the configured Daikin account's default AC device,
control mode is cooling,
temperature defaults to the lowest supported cooling setpoint, and `on` uses
fixed fan level 3/5 with Daikin power mode off.

Device selection: if the account has exactly one AC gateway device, leave
`DAIKIN_DEVICE_ID` unset or empty and the CLI selects it automatically. If the
account has multiple AC gateway devices, set `DAIKIN_DEVICE_ID` to the intended
gateway device id. Use `python3 -m daikin_ac_control devices --all` to list
available devices.

## Vocabulary

- "Power mode" means the literal Daikin app power/powerful/boost switch, exposed by the API as `powerfulMode`.
- "Fan level" means the separate fixed fan level, 1/5 through 5/5.
- Do not map "enable power mode" to fan level 5. Use `power-mode on`.

## Command Map

```bash
python3 -m daikin_ac_control status
```

Use for "what is the AC doing?", "AC status", "temperature?", or "is the AC on?"

```bash
python3 -m daikin_ac_control on
python3 -m daikin_ac_control off
```

Use for "turn on/start cooling" and "turn off/stop the AC".

```bash
python3 -m daikin_ac_control set --temperature 18
```

Use for "set AC to 18 degrees". Replace `18` with the requested Celsius value.

```bash
python3 -m daikin_ac_control power-mode on
python3 -m daikin_ac_control power-mode off
python3 -m daikin_ac_control power-mode toggle
```

Use for "enable power mode", "disable power mode", "boost", "turbo", or "powerful mode".

```bash
python3 -m daikin_ac_control fan-level 5
python3 -m daikin_ac_control set --fan-level 3
python3 -m daikin_ac_control fan-level toggle
```

Use for "set fan to 5", "set fan level to 3", or "toggle fan level". This is separate from power mode.

## OAuth Bootstrap

If no token file exists:

```bash
python3 -m daikin_ac_control auth-url
python3 -m daikin_ac_control exchange-code "CODE_FROM_CALLBACK"
```

Ask the account owner to authorize the URL and provide the callback code.

## Pitfalls

- When setting a fixed fan level, Daikin may require `powerfulMode` to be off before `fanControl` becomes settable. Use the CLI `fan-level` command and let the client handle this sequence.
- Avoid redundant `powerfulMode=off` writes when it is already off; they waste API quota and can trigger `HTTP 429` rate limits.
- If Daikin returns `HTTP 429`, wait about 60 seconds before retrying instead of looping aggressively.
- "Power mode" and "fan level 5" are separate controls. Do not map power/boost/turbo requests to fan level 5.

## Safety

Only use the implemented CLI commands above for writes. Do not invent write
commands or call PATCH/POST directly.
