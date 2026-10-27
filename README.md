# 春风元力千面 2 Pro MCP

[中文](README.md) | [English](README.en.md)

这是一个面向春风元力千面 2 Pro（BLE 名称 `TF-PTPRO`）的本地 Bluetooth Low Energy（BLE）MCP 服务器。AI 客户端可通过 MCP 扫描设备、连接设备，并调用吮吸与震动控制工具。项目同时保留 `DK-META2` profile。

这是一个非官方、本地优先的 MCP 实现，主打隐私、低延迟和可控的设备操作。

本项目是非官方实现，不读取账号、不调用云端控制接口，也不包含官方客户端源码、安装包或设备固件。春风元力、千面及相关名称归其各自权利人所有；本项目与设备厂商没有隶属或背书关系。

> 状态：实验性项目。协议和设备行为来自对客户端通信行为的观察，使用前请在场确认设备状态，并保留实体停止方式。

## 工作原理

MCP 只是 AI 客户端与本地工具之间的调用协议，真正的设备通信链路如下：

```text
MCP 客户端 → FastMCP（stdio 或 HTTP）→ Bleak → Windows Bluetooth LE → 设备
```

Windows 直连无需 WSL：2026-10-27 已通过 Windows MCP 实际扫描、连接 `TF-PTPRO`，并由用户确认 5% 震动和单次吮吸均有动作。单通道停止帧已发送，但其对同时运行的吮吸是否完全隔离仍需验证。

当前实现两个按设备名匹配的 profile：

| 能力 | 命令码 |
| --- | --- |
| DK-META2 震动 | `0x08` |
| DK-META2 伸缩 | `0x0B` |
| DK-META2 旋转 | `0x0E` |
| TF-PTPRO 单次吮吸 | 复合 payload `10 01 12 N` |
| TF-PTPRO 震动 | 复合 payload `07 01 09 N`；MCP 保持输出，需单独调用停止工具 |

## 要求

- Python 3.11 或更高版本及支持 BLE 的 Windows 蓝牙适配器；
- 系统蓝牙已开启，设备没有被官方客户端或其他程序独占；
- 对应系统账户具有蓝牙扫描和连接权限。

## 安装

```powershell
git clone https://github.com/linyuye/chunfeng-yuanli-mcp-qianmianpro2.git
cd chunfeng-yuanli-mcp-qianmianpro2
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
```

检查运行环境和附近支持的设备：

```powershell
& .\.venv\Scripts\python.exe -m heartbeat_yuanli_mcp.server --doctor
```

### 在 WSL2 使用直通蓝牙

如果蓝牙适配器当前通过 usbipd 直通给 WSL，MCP 也应在 WSL 内运行。先进入 WSL shell：

```powershell
wsl.exe -d Ubuntu-24.04
```

进入 WSL shell 后克隆并安装项目及依赖（安装会从 Python 包索引下载依赖）：

```bash
git clone https://github.com/linyuye/chunfeng-yuanli-mcp-qianmianpro2.git
cd chunfeng-yuanli-mcp-qianmianpro2
python3 -m venv ~/.venvs/heartbeat-yuanli-mcp
~/.venvs/heartbeat-yuanli-mcp/bin/python -m pip install -e .
```

之后使用 [mcp-config.wsl.example.json](./mcp-config.wsl.example.json) 配置 MCP 客户端。网页控制台与 MCP 共用同一个 WSL 蓝牙适配器；同一台设备不能同时由两边连接。

## 启动 MCP 服务

标准 MCP 客户端可以用 stdio 启动本地服务，也可以连接 FastMCP 提供的 Streamable HTTP URL。stdio 是最简单的本地集成方式：

```text
python -m heartbeat_yuanli_mcp.server
```

也可以使用安装后的入口命令：

```text
heartbeat-yuanli-mcp
```

Windows stdio 配置见 [mcp-config.windows.example.json](./mcp-config.windows.example.json)，请将 `command` 替换为自己安装位置的 `.venv/Scripts/python.exe` 绝对路径。配置将 `TFPTPRO_WEB_BRIDGE` 设为 `0`，通过 Windows 蓝牙直连。如果需要固定设备，可以通过环境变量设置自己的设备名或蓝牙地址；公开仓库不会保存真实设备地址。

### 通过 URL 连接 FastMCP

在项目目录启动 Windows 直连版 Streamable HTTP 服务：

```powershell
& .\start-windows-mcp.ps1
```

脚本使用项目的虚拟环境并强制本次服务通过 Windows BLE 直连；要使用其他端口，可传入 `-Port 8001`。保持窗口运行；按 Ctrl+C 退出。确保设备已开机、可发现，且没有被手机或其他控制程序占用。

MCP endpoint 是 `http://127.0.0.1:8000/mcp`。把它填入支持 Streamable HTTP 的 MCP 客户端；配置格式因客户端而异，[HTTP 配置示例](./mcp-config.http.example.json)使用常见的 `type: http` / `url` 字段。要让其他电脑或云端模型访问，需部署到受保护的主机或配置经过认证的安全隧道；不要把无认证的 BLE 控制端口直接暴露到公网。

### Windows 网页控制台

网页控制台可独立使用 Windows 蓝牙。先让 MCP 或手机 App 断开设备，再在项目目录启动网页服务：

```powershell
& .\start-windows-web.ps1
```

保持该窗口运行，在另一个 PowerShell 窗口打开带本次会话授权的页面（以下为 Chrome 安装在默认位置的示例）：

```powershell
$webSession = Get-Content -Raw -LiteralPath (Join-Path $env:TEMP 'tfptpro-web-8765.json') | ConvertFrom-Json
Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList $webSession.url
```

页面中扫描并选择设备，连接后可触发吮吸或 1 秒震动测试。震动前需用实体键切换模式；网页的震动测试会自动发送停止帧。网页与 Windows MCP 各自持有蓝牙连接，切换控制端前先断开当前连接。运行时文件包含临时控制令牌，请勿分享或提交；关闭服务后重新启动会生成新令牌。

### 与 WSL 网页控制台共用设备

可选的 [MCP Web 桥接配置](./mcp-config.web-bridge.example.json) 用于适配器由 WSL2 持有、且已启动兼容网页控制器的情况；Windows 原生直连不需要它。此配置依赖 WSL 控制器的运行时文件和网络设置，不能直接连接上面的 Windows 网页服务。

若要让 MCP 自己直接持有 BLE 连接，也可在 WSL 安装依赖并使用 [WSL 直接 BLE 配置](./mcp-config.wsl.example.json)；此模式下网页与 MCP 不能同时连接设备。

## MCP 工具

| 工具 | 作用 |
| --- | --- |
| `list_devices` | 扫描附近 BLE 设备，标记可识别的型号及当前连接 |
| `connect_device` | 按扫描地址连接，并匹配已支持的型号及 GATT 能力 |
| `get_device_status` | 查看 MCP 自己持有的连接状态 |
| `set_vibration` | 设置 DK-META2 / TF-PTPRO 震动强度并持续输出，需先用 TF-PTPRO 实体键切到震动模式 |
| `set_linear` | 设置伸缩强度，范围 `0–100` |
| `set_rotary` | 设置旋转强度，范围 `0–100` |
| `set_suction` | TF-PTPRO 单次吮吸，范围 `1–100` |
| `stop_device` | 将所有已支持输出归零 |
| `stop_vibration` | 发送候选震动通道停止帧；其与吮吸通道的隔离效果待真机验证 |
| `disconnect_device` | 停止心跳、归零输出并断开连接 |
| `create_sine_waveform` | 生成并本地保存正弦强度序列，可选同时开启持续震动 |
| `save_waveform` / `list_waveforms` / `get_waveform` | 创建、列出和查看可复用的动作时间序列 |
| `start_waveform` / `get_waveform_status` / `stop_waveform` | 异步播放、观察发送进度并停止后续事件 |
| `start_action_recording` / `stop_action_recording` / `get_action_recording` | 记录本 MCP 发出的动作及设备回报，并保存到本机 |
| `save_recording_as_waveform` | 将录制中成功发送的命令转换为可复用波形 |

波形示例：先用 `create_sine_waveform(name="gentle", duration_seconds=20, period_seconds=10, minimum_level=5, maximum_level=25, interval_seconds=2, vibration_level=5)` 生成序列，再调用 `start_waveform(name="gentle", confirm=true)`。Agent 可通过 `get_waveform_status` 观察每个事件的请求时间、发送时间、结果和可用回报。波形结束或 `stop_waveform` 默认都不停止震动；需要时单独调用 `stop_vibration`，或显式调用 `stop_waveform(stop_vibration=true)`。

也可以先 `start_action_recording`，再执行单次动作，随后 `stop_action_recording(name="take1")` 保存，并用 `save_recording_as_waveform(recording_name="take1", waveform_name="replay1")` 转成波形。录制数据保存在用户本机数据目录的 SQLite 文件中，可通过 `TFPTPRO_WAVEFORM_DIR` 指定目录；不保存 BLE 地址。时间序列记录的是 MCP 命令及回报，不是传感器测得的实际吸力或震动。TF-PTPRO 吮吸协议是一次性触发，因此正弦曲线由间隔触发的单次动作近似，不能视为连续模拟输出；事件最大 100 个、时间范围最大 120 秒、相邻吮吸触发至少间隔 2 秒。

典型调用顺序：

```text
list_devices
connect_device
set_vibration(level=20, confirm=true)
set_linear(level=20, confirm=true)
set_rotary(level=20, confirm=true)
stop_device
disconnect_device
```

TF-PTPRO 的调用方式：

```text
list_devices
connect_device(address="扫描列表中的 BLE 地址", name="TF-PTPRO")
set_vibration(level=25, confirm=true)  # 持续输出；先用设备实体键切到震动模式
set_suction(level=5, confirm=true)      # 单次触发，可与震动并用
stop_vibration                          # 只停震动
disconnect_device
```

非零动作必须显式传入 `confirm=true`。确认参数只是本地安全门槛，不是设备的蓝牙认证机制。当前 MCP 不提供任意特征、任意命令码或原始十六进制写入工具。

## BLE 协议概览

设备使用以下 GATT 能力：

```text
Service:                 0000ffac-0000-1000-8000-00805f9b34fb
Write with response:     ffb5
Write without response:  ffb7
Notify:                  ffb8
```

一个普通控制帧为：

```text
[message_id] [0x02] [0x00] [length] [command] [value] [checksum]
```

例如震动强度 20：

```text
01 02 00 03 08 14 E4
```

其中 `0x14` 是十进制 20，校验和只覆盖 `[command, value]`：

```text
checksum = (-(command + value)) & 0xFF
```

完整说明见 [docs/protocol.md](./docs/protocol.md)。

## 安全与已知限制

- 连接成功后每两秒发送一次心跳；心跳或写入失败时会尝试停止输出并断开；
- MCP 退出、显式断开和停止操作都会尝试把支持的输出归零；
- 官方心跳元力客户端和 MCP 不应同时持有同一个 GATT 连接。连接失败时先在官方客户端中断开设备；
- `list_devices` 展示附近设备不代表它们可控制；只有列出的已知 profile 可连接并开放相应 MCP 动作。未知名称设备即使广播 FFAC 也不会自动套用控制命令；
- BLE 控制器一次只维护一个连接。扫描时会标记当前连接；切换设备时，直连模式会先尝试停止并断开已有连接；
- TF-PTPRO 震动需先由设备实体键切换模式。网页的一秒测试仍自动停止；MCP `set_vibration` 持续输出，`stop_vibration` 发送候选单通道停止帧，隔离效果尚未真机验证；`stop_device` 会归零所有输出。2026-10-26 用户确认吮吸和震动均可感知；协议通知不测量物理力度；
- TF-PTPRO 的扫描与连接运行在 MCP 所在操作系统的蓝牙栈。当前蓝牙适配器直通 WSL 时，Windows MCP 无法同时使用该适配器；
- BLE 连接本身依赖操作系统权限和设备固件行为，本项目不实现厂商账号认证、加密配对或云端授权；
- 协议层当前按单动作发送一帧，快速连续调用没有额外的 120ms 应用层节流。需要高频控制时请先在实际设备上验证稳定性。

请勿在无人看护、无法快速停止或不清楚设备当前状态时运行非零动作。

## 开发与测试

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

协议构造函数是纯函数，可以在没有蓝牙设备的环境中测试。实机测试应从低强度开始，并先验证 `stop_device`。

## 许可证

本项目使用 MIT License，见 [LICENSE](./LICENSE)。依赖包各自遵循其上游许可证。

## 使用的项目

- [chunfeng-yuanli-ai-mcp](https://github.com/vmayfuture/chunfeng-yuanli-ai-mcp)：本项目基于该开源项目继续改造，保留并扩展其 MCP 服务与 BLE 控制实现。感谢原作者的工作。
- [Model Context Protocol（MCP）](https://modelcontextprotocol.io/)：AI 客户端与本地工具服务器之间的协议。
- [FastMCP](https://github.com/jlowin/fastmcp)：构建本项目 Python MCP 服务，支持 stdio 与 Streamable HTTP。
- [Bleak](https://github.com/hbldh/bleak)：Python BLE 扫描与 GATT 连接库。
- [`@tryjoy/dokidoki` npm CLI](https://www.npmjs.com/package/%40tryjoy/dokidoki)：参考其公开客户端行为分析 BLE 控制协议。本项目是独立的 Python MCP 实现，不依赖 Node.js/npm，也不包含该 CLI 的代码或发行文件。

## 贡献

提交问题或改动前请阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。涉及安全、设备异常动作或潜在隐私泄露的问题，请优先阅读 [SECURITY.md](./SECURITY.md)。

## 使用场景与常见问题

下面是调用示例，不是实机测试记录。先在设备旁检查连接与停止功能：

```text
请使用 heartbeat-yuanli 扫描支持的设备，只报告列表，不发送动作。
```

```text
连接我明确选定的 DK-META2，查看连接状态，先验证 stop_device，再断开。
```

- **支持其他型号吗？** 仅 DK-META2 与 TF-PTPRO 有显式 profile；扫描能看到其他设备不代表本 MCP 支持控制它们。
- **状态证明实际输出吗？** 状态只表示 MCP 持有的连接，不能替代在场观察。
- **连接失败怎么办？** 检查蓝牙权限、距离、电量，释放官方客户端连接，再运行 `--doctor`。
