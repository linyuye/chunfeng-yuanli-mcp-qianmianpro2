# DokiDoki BLE 协议说明

本文描述本项目当前实现的、面向 `DK-META2` 的最小协议子集。内容来自对已安装客户端通信行为的观察和本项目的自动化帧测试；本项目不分发官方客户端代码、安装包或固件。

## GATT

```text
Service:                    0000ffac-0000-1000-8000-00805f9b34fb
Write with response:        0000ffb5-0000-1000-8000-00805f9b34fb
Write without response:     0000ffb7-0000-1000-8000-00805f9b34fb
Notify:                     0000ffb8-0000-1000-8000-00805f9b34fb
```

普通控制优先使用无响应写入特征 `FFB7`；如果设备没有该特征，则回退到带响应写入 `FFB5`。心跳优先使用带响应写入。通知特征是可选的，当前实现只记录通知到达时间，不把通知当作动作确认。

## 帧布局

`DK-META2` 的单命令帧通常是 7 字节：

```text
byte 0: message id
byte 1: command type = 0x02
byte 2: fragment marker = 0x00
byte 3: length = payload length + checksum length = 0x03
byte 4: command
byte 5: value
byte 6: checksum
```

消息序号使用 `1..15` 循环。`byte 2` 在当前实现中始终表示单帧传输，不代表动作值。

校验和只覆盖 payload，也就是命令码和参数值：

```text
checksum = (-(command + value)) & 0xFF
```

## 命令映射

```text
0x08 -> vibration
0x0B -> linear
0x0E -> rotary
0xEF -> heartbeat, value 0x01
```

例如 `linear=20`：

```text
command  = 0x0B
value    = 0x14
sum      = 0x1F
checksum = 0xE1

01 02 00 03 0B 14 E1
```

## 多个动作

当前 Python MCP 没有批量动作工具。三个动作会通过三次独立工具调用、三次独立 GATT 写入完成：

```text
01 02 00 03 0B 14 E1   # linear = 20
02 02 00 03 08 14 E4   # vibration = 20
03 02 00 03 0E 14 DE   # rotary = 20
```

设备会分别保存三个通道的当前值，因此三次写入完成后三个输出可以同时保持在 20。`stop_device` 则分别发送三个通道的零值。

## TF-PTPRO profile

`TF-PTPRO` 共用 FFAC 服务、FFB5 心跳写入、FFB7 动作写入和 FFB8 通知。动作帧 payload 与 DK-META2 的单命令帧不同：

```text
suction N:  10 01 12 N
vibration:  07 01 09 N
stop:       07 00 10 00 09 00 12 00
heartbeat:  EF 01  (FFB5, with response)
```

例如 5% 单次吮吸：

```text
03 02 00 05 10 01 12 05 D8
```

该帧由本地 `@tryjoy/dokidoki@1.0.5` 客户端逻辑构造并在真机写入。TF-PTPRO 震动 payload 为 `07 01 09 N`，需要先用设备实体键切换到震动模式。网页保留一秒测试自动停止；MCP 的 `set_vibration` 持续运行，`stop_vibration` 发送候选单通道归零帧 `07 00 09 00`，`stop_device` 仍发送已观察到的全设备停止帧。候选单通道停止帧已通过软件帧测试，尚需在真机上确认不会影响吮吸通道。回报不测量物理强度；其他档位尚未逐一实测。
