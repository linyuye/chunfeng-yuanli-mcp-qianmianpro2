# Contributing

感谢贡献。这个项目的范围是：通过标准 MCP 工具和本地 BLE 控制已验证的 DokiDoki 设备。

## 开发约束

- 不提交真实蓝牙地址、API Key、日志中的个人信息或本地绝对路径；
- 不提交官方客户端、APK、EXE、固件或从官方安装包复制的资源；
- 不添加绕过 profile 检查的任意命令码、任意 GATT 特征或原始十六进制写入接口；
- 改动协议构造时必须同步更新纯函数测试和协议文档；
- 涉及设备动作的改动必须保留显式确认、停止清理和错误返回。

## 本地验证

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

实机测试应使用低强度、有人在场，并在开始前确认 `stop_device` 能正常工作。
