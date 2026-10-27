# Chunfeng Yuanli Qianmian 2 Pro MCP

[中文](README.md) | [English](README.en.md)

An unofficial local Bluetooth Low Energy (BLE) MCP server for the Chunfeng Yuanli Qianmian 2 Pro (`TF-PTPRO`). AI clients can scan and connect to nearby devices and invoke suction and vibration tools. The project also retains the `DK-META2` profile.

This experimental implementation is based on observed client communication. It does not read vendor accounts, call cloud control APIs or bundle official client software or firmware. Product names belong to their owners; there is no vendor affiliation or endorsement.

## Install and diagnose

Requirements: Python 3.11+, a Windows BLE adapter, Bluetooth enabled, scanning/connection permissions and an available supported device. Windows direct BLE control was verified on TF-PTPRO on 2026-10-27 without WSL; the user observed both 5% vibration and a one-shot suction action.

```powershell
git clone https://github.com/linyuye/chunfeng-yuanli-mcp-qianmianpro2.git
cd chunfeng-yuanli-mcp-qianmianpro2
py -3.11 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -e .
& ./.venv/Scripts/python.exe -m heartbeat_yuanli_mcp.server --doctor
```

The diagnostic command checks the environment and nearby supported devices. Release competing connections in the official client before connecting through MCP.

## Configure a client

Use the virtual environment's absolute Python path:

```json
{
  "mcpServers": {
    "heartbeat-yuanli": {
      "command": "C:/tools/chunfeng-yuanli-mcp-qianmianpro2/.venv/Scripts/python.exe",
      "args": ["-m", "heartbeat_yuanli_mcp.server"],
      "env": {"DOKIDOKI_DEVICE_NAME": "TF-PTPRO"}
    }
  }
}
```

Replace the path with the interpreter where you installed the project. Other systems use their corresponding interpreter. Entry points are `heartbeat-yuanli-mcp` and `python -m heartbeat_yuanli_mcp.server`. See [configuration example](mcp-config.example.json) and [Chinese guide](README.md).

For a URL-based Windows connection, run `& .\start-windows-mcp.ps1` in PowerShell from the project directory. The script selects direct Windows BLE and serves Streamable HTTP at `http://127.0.0.1:8000/mcp`; see [HTTP configuration example](mcp-config.http.example.json). For stdio, use [the Windows configuration example](mcp-config.windows.example.json) and replace the Python path with your own. The service binds to loopback by default. Do not expose the unauthenticated BLE control endpoint to the public internet.

## Windows web controller

Disconnect the device from MCP or another app, then run `& .\start-windows-web.ps1` in the project directory. Keep that window open. In a second PowerShell window, open the session URL with Chrome installed at its default path:

```powershell
$webSession = Get-Content -Raw -LiteralPath (Join-Path $env:TEMP 'tfptpro-web-8765.json') | ConvertFrom-Json
Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList $webSession.url
```

Scan and select the device in the page. Vibration tests last one second and send a stop frame; select vibration mode with the physical button first. The Windows web controller and the direct MCP server use separate BLE connections, so disconnect the active controller before switching. The runtime file contains a temporary control token; do not share or commit it.

## First use

Stay beside the device and retain a physical way to stop it. Example agent requests:

```text
Use heartbeat-yuanli to list nearby BLE devices. Report the list only;
do not send motion commands.
```

```text
Connect the TF-PTPRO device I selected by its BLE address. Read its connection status,
verify stop_device first, then disconnect.
```

These are sample prompts, not new hardware test results. An explicitly authorized low-intensity test can use:

```text
list_devices
connect_device(address="selected BLE address", name="TF-PTPRO")
set_suction(level=5, confirm=true)
stop_device
disconnect_device
```

| Tool | Behavior |
| --- | --- |
| `list_devices` | Scan nearby BLE devices and mark known profiles; no motion |
| `connect_device` | Connect by BLE address and match a supported profile |
| `get_device_status` | Report MCP's connection state |
| `set_vibration` | Set vibration level on DK-META2 / TF-PTPRO; output persists until a stop tool is called. Select vibration mode with the TF-PTPRO physical button first. |
| `set_suction` | One-shot TF-PTPRO suction protocol value, 1–100 |
| `set_linear` | Linear motion level, 0–100 |
| `set_rotary` | Rotation level, 0–100 |
| `stop_device` | Set supported outputs to zero |
| `stop_vibration` | Send the candidate vibration-channel stop frame; isolation from suction still needs hardware verification |
| `disconnect_device` | Stop heartbeat, zero outputs and disconnect |
| `create_sine_waveform` | Generate and save a sine strength timeline, optionally with persistent vibration |
| `create_intent_waveform` | Map intents such as gentle/medium/intense to deterministic strength, angular frequency and phase parameters |
| `save_waveform` / `list_waveforms` / `get_waveform` | Create, list and inspect reusable action timelines |
| `start_waveform` / `get_waveform_status` / `stop_waveform` | Play asynchronously, inspect sent events and stop future events |
| `start_action_recording` / `stop_action_recording` / `get_action_recording` | Record commands sent by this MCP and device replies locally |
| `save_recording_as_waveform` | Convert successfully sent recording commands into a reusable waveform |

Example: call `create_sine_waveform(name="gentle", duration_seconds=20, period_seconds=10, minimum_level=5, maximum_level=25, interval_seconds=2, vibration_level=5)`, then `start_waveform(name="gentle", confirm=true)`. The agent can inspect requested time, send time, result and available device reply for each event with `get_waveform_status`. Vibration stays on when a waveform completes or `stop_waveform` is called. Stop it separately with `stop_vibration`, or explicitly request `stop_waveform(stop_vibration=true)`.

For natural-language intent, call `create_intent_waveform(name="soft1", intent="gentle", duration_seconds=20, interval_seconds=2)`. It accepts Chinese and English synonyms for gentle, medium and intense; ambiguous or unknown intents return an error. Defaults are gentle `(level 5–20, ω=1.5)`, medium `(10–45, ω=0.8)` and intense `(30–80, ω=0.4)`, where ω is in rad/s. Override with `minimum_level`, `maximum_level`, `angular_frequency`, `phase_radians` or `vibration_level`. Creation only saves and returns the proposed plan; inspect it before `start_waveform(name="soft1", confirm=true)`. These are fixed mappings, not learned from user history or physical feedback. A larger ω means a shorter period and faster changes; the gentle profile also uses lower strength limits.

For recording, call `start_action_recording`, perform actions, then `stop_action_recording(name="take1")`. Convert it with `save_recording_as_waveform(recording_name="take1", waveform_name="replay1")`. Data is stored in a local SQLite file under the user's data directory; `TFPTPRO_WAVEFORM_DIR` can select another directory. BLE addresses are not stored. Timelines capture MCP commands and available replies, not sensor-measured physical suction or vibration. TF-PTPRO suction is a one-shot trigger, so a sine curve is approximated by spaced individual actions rather than continuous analog output. Limits: 100 events, 120 seconds and at least 2 seconds between suction triggers.

Nonzero motion requires `confirm=true`. This is a local gate, not Bluetooth authentication. Connection state does not prove actual physical output; observe it directly. Arbitrary GATT writes, command codes and raw hexadecimal commands are not exposed.

## Compatibility and limits

Only `DK-META2` and `TF-PTPRO` have explicit profiles. TF-PTPRO requires its physical button to switch into vibration mode. The web page's one-second vibration test still stops automatically; MCP vibration persists until `stop_vibration`, which sends a candidate per-channel stop frame whose isolation from suction still needs hardware verification. `stop_device` stops every supported output. The user confirmed perceiving suction and vibration on 2026-10-26. Protocol notifications are not force measurements. Listing an unknown device does not make it controllable.

A heartbeat is sent every two seconds after connection. Heartbeat/write failures, shutdown and explicit disconnect trigger attempts to stop supported outputs and disconnect. These actions depend on the OS, BLE link and firmware. Do not run nonzero motion unattended or when device state and a quick stop are uncertain.

The official client and MCP should not hold the same GATT connection simultaneously. The WSL USB Bluetooth passthrough adapter cannot be used by Windows MCP while attached to WSL; detach it from WSL before using Windows BLE. Vendor account authentication, encrypted pairing implementation and cloud authorization are not included. Commands send one frame per action with no additional 120 ms application throttle; frequent calls need hardware stability checks.

## Protocol and tests

The profiles use service `0000ffac-0000-1000-8000-00805f9b34fb`, write characteristics `ffb5` / `ffb7`, and notification characteristic `ffb8`. DK-META2 uses vibration, linear and rotary codes `0x08`, `0x0B` and `0x0E`. TF-PTPRO uses a compound one-shot suction payload. See [protocol notes](docs/protocol.md).

```powershell
& ./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Pure protocol construction tests need no hardware; they do not establish real BLE compatibility. Hardware checks should begin at low intensity and verify stopping first.

**Cannot connect?** Check permissions, proximity and battery, release competing connections and run `--doctor`.

**Does it use the cloud?** Control runs locally over BLE without vendor account access.

**Can I use another model?** Nearby devices are listed, but only devices with an explicit profile expose control tools.

MIT license: [LICENSE](LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Security or unexpected behavior: [SECURITY.md](SECURITY.md).

## Projects used

- [chunfeng-yuanli-ai-mcp](https://github.com/vmayfuture/chunfeng-yuanli-ai-mcp): This project builds on and adapts this open-source MCP server and BLE control implementation. Thanks to the original author.
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/): protocol between AI clients and local tool servers.
- [FastMCP](https://github.com/jlowin/fastmcp): Python MCP server framework used for stdio and Streamable HTTP transports.
- [Bleak](https://github.com/hbldh/bleak): Python library for BLE scanning and GATT connections.
- [`@tryjoy/dokidoki` npm CLI](https://www.npmjs.com/package/%40tryjoy/dokidoki): its publicly available client behavior was used as a reference when analyzing BLE control. This is an independent Python MCP implementation; it does not require Node.js/npm and does not bundle the CLI's code or distribution files.
