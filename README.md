# 春风元力千面 2 Pro MCP

[中文](README.md) | [English](README.en.md)

这是一个面向春风元力千面 2 Pro（BLE 名称 `TF-PTPRO`）的本地 Bluetooth Low Energy（BLE）MCP 服务器。AI 客户端可通过 MCP 扫描设备、连接设备，并调用吮吸与震动控制工具。项目同时保留 `DK-META2` profile。

这是一个非官方、本地优先的 MCP 实现，主打隐私、低延迟和可控的设备操作。

本项目是非官方实现，不读取账号、不调用云端控制接口，也不包含官方客户端源码、安装包或设备固件。春风元力、千面及相关名称归其各自权利人所有；本项目与设备厂商没有隶属或背书关系。

> 状态：实验性项目。协议和设备行为来自对客户端通信行为的观察，使用前请在场确认设备状态，并保留实体停止方式。

## 工作原理

MCP 只是 AI 客户端与本地工具之间的调用协议，真正的设备通信链路如下：

```text
MCP 客户端
    │ JSON-RPC / stdio
    ▼
heartbeat_yuanli_mcp.server
    │ 工具参数校验与生命周期管理
    ▼
Windows WebBridge（WSL 网页控制器模式）或 DokiDokiDeviceManager（直连模式）
    │ 本机受令牌保护的 HTTP API 或 Bleak
    ▼
WSL / Windows Bluetooth LE
    │ GATT service/characteristic 写入
    ▼
TF-PTPRO / DK-META2
```

当前实现两个按设备名匹配的 profile：

| 能力 | 命令码 |
| --- | --- |
| DK-META2 震动 | `0x08` |
| DK-META2 伸缩 | `0x0B` |
| DK-META2 旋转 | `0x0E` |
| TF-PTPRO 单次吮吸 | 复合 payload `10 01 12 N` |
| TF-PTPRO 震动 | 复合 payload `07 01 09 N`；MCP 保持输出，需单独调用停止工具 |

## 要求

- Python 3.11 或更高版本；
- Python 3.11 或更高版本及支持 BLE 的操作系统；本机当前由 Ubuntu-24.04 / WSL2 通过 usbipd 持有蓝牙适配器，MCP 的 WSL 配置见下文；
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

配置示例见 [mcp-config.example.json](./mcp-config.example.json)。如果需要固定设备，可以通过环境变量设置自己的设备名或蓝牙地址；公开仓库不会保存真实设备地址。

### 通过 URL 连接 FastMCP

启动 Streamable HTTP 服务（默认只绑定本机回环地址）。在这台电脑上，蓝牙由 WSL 网页控制器持有，所以通过 WebBridge 转发；若 MCP 所在系统直接持有 BLE 适配器，则省略前两行环境变量：

```powershell
$env:TFPTPRO_WEB_BRIDGE = "1"
$env:DOKIDOKI_DEVICE_NAME = "TF-PTPRO"
& .\.venv\Scripts\python.exe -m heartbeat_yuanli_mcp.server --transport http --host 127.0.0.1 --port 8000
```

MCP endpoint 是 `http://127.0.0.1:8000/mcp`。把它填入支持 Streamable HTTP 的 MCP 客户端；配置格式因客户端而异，[HTTP 配置示例](./mcp-config.http.example.json)使用常见的 `type: http` / `url` 字段。要让其他电脑或云端模型访问，需部署到受保护的主机或配置经过认证的安全隧道；不要把无认证的 BLE 控制端口直接暴露到公网。

### 与 WSL 网页控制台共用设备

本机适配器由 WSL2 持有时，推荐使用 [MCP Web 桥接配置](./mcp-config.web-bridge.example.json)：MCP 在 Windows 侧运行，通过本地受令牌保护的 API 调用 WSL 网页控制器，不会再创建第二个 BLE 连接。先启动你的 WSL 网页控制器，将配置中的 `command` 指向已安装本项目的 Python 解释器。桥接会从 WSL 读取当前令牌和 IP，不需要把令牌写进 MCP 配置。

若要让 MCP 自己直接持有 BLE 连接，也可在 WSL 安装依赖并使用 [WSL 直接 BLE 配置](./mcp-config.wsl.example.json)；此模式下网页与 MCP 不能同时连接设备。

## MCP 工具

| 工具 | 作用 |
| --- | --- |
| `list_devices` | 扫描附近 BLE 设备；若控制器已有连接，则返回最近一次扫描列表并标记已连接设备 |
| `connect_device` | 按扫描地址连接，并匹配已支持的型号及 GATT 能力；若已有其他设备连接，需先断开 |
| `get_device_status` | 查看 MCP 自己持有的连接状态 |
| `set_vibration` | 设置 DK-META2 / TF-PTPRO 震动强度并持续输出，需先用 TF-PTPRO 实体键切到震动模式 |
| `set_linear` | 设置伸缩强度，范围 `0–100` |
| `set_rotary` | 设置旋转强度，范围 `0–100` |
| `set_suction` | TF-PTPRO 单次吮吸，范围 `1–100` |
| `stop_device` | 将所有已支持输出归零 |
| `stop_vibration` | 发送候选震动通道停止帧；其与吮吸通道的隔离效果待真机验证 |
| `disconnect_device` | 停止心跳、归零输出并断开连接 |

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
- BLE 控制器一次只维护一个连接。已有连接时 `list_devices` 不会开始新扫描，而会返回上次发现列表；要连接另一设备，先调用 `disconnect_device`，再扫描并按地址连接；
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
