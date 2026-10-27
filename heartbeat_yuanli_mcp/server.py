"""FastMCP server for local control of supported DokiDoki BLE devices."""

from __future__ import annotations

import asyncio
import contextlib
import json
import inspect
import logging
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastmcp import Context, FastMCP

from .device_manager import DokiDokiDeviceManager, DokiDokiError
from .web_bridge import WebControllerBridge

LOGGER = logging.getLogger("heartbeat_yuanli_mcp")
os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")


@dataclass
class AppContext:
    manager: Any


def _new_manager() -> Any:
    if os.getenv("TFPTPRO_WEB_BRIDGE") == "1":
        return WebControllerBridge()
    return DokiDokiDeviceManager()


@contextlib.asynccontextmanager
async def app_lifespan(_server: Any) -> AsyncIterator[AppContext]:
    manager = _new_manager()
    try:
        yield AppContext(manager=manager)
    finally:
        await manager.shutdown()


mcp = FastMCP(
    name="heartbeat-yuanli-mcp",
    instructions=(
        "先调用 get_device_status 或 list_devices，再调用 connect_device。"
        "非零动作必须显式传 confirm=true；TF-PTPRO 震动持续运行，使用 stop_vibration 单独停止，stop_device 会停止所有支持的输出。"
    ),
    lifespan=app_lifespan,
)


def _manager(ctx: Context) -> Any:
    lifespan_context = ctx.lifespan_context
    if lifespan_context is None:
        raise RuntimeError("MCP 请求上下文不可用。")
    return lifespan_context.manager


def _error_result(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, DokiDokiError):
        code = exc.code
    elif isinstance(exc, ValueError):
        code = "invalid_input"
    else:
        code = "internal_error"
    return {"ok": False, "error": {"code": code, "message": str(exc)}}


@mcp.tool(name="list_devices", description="扫描附近所有 BLE 设备；结果会标明本 MCP 可识别的设备型号。")
async def list_devices(ctx: Context, prefix: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """Return nearby BLE devices without connecting or sending commands."""

    try:
        return {"ok": True, **(await _manager(ctx).list_devices(timeout=timeout, prefix=prefix))}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="connect_device", description="按扫描列表中的 BLE 地址连接设备，并通过名称与 GATT 服务匹配已支持协议。")
async def connect_device(ctx: Context, address: str | None = None, name: str | None = None) -> dict[str, Any]:
    try:
        return {"ok": True, **(await _manager(ctx).connect(address=address, name=name))}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="get_device_status", description="读取 MCP 自己持有的设备连接、profile 和 GATT 状态。")
async def get_device_status(ctx: Context) -> dict[str, Any]:
    try:
        status = _manager(ctx).status()
        if inspect.isawaitable(status):
            status = await status
        return {"ok": True, **status}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_suction", description="触发已匹配 TF-PTPRO 的一次吮吸；level 为协议强度 1–100，非零必须 confirm=true。")
async def set_suction(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _manager(ctx).set_action("suction", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_vibration", description="设置支持设备的震动强度并持续运行，直到调用 stop_vibration 或 stop_device；TF-PTPRO 使用前须通过设备实体键切换到震动模式。非零值必须 confirm=true。")
async def set_vibration(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _manager(ctx).set_action("vibration", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_linear", description="设置 DK-META2 伸缩强度；非零值必须 confirm=true。")
async def set_linear(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _manager(ctx).set_action("linear", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_rotary", description="设置 DK-META2 旋转强度；非零值必须 confirm=true。")
async def set_rotary(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _manager(ctx).set_action("rotary", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="stop_device", description="将当前设备所有已支持输出归零；可重复调用。")
async def stop_device(ctx: Context) -> dict[str, Any]:
    try:
        return await _manager(ctx).stop()
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="stop_vibration", description="发送震动通道归零指令；TF-PTPRO 的单通道停止帧与吮吸通道的隔离效果仍需真机验证。若需立即停止所有输出，请调用 stop_device。")
async def stop_vibration(ctx: Context) -> dict[str, Any]:
    try:
        return await _manager(ctx).stop_vibration()
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="disconnect_device", description="停止心跳并断开 MCP 持有的 BLE 连接。")
async def disconnect_device(ctx: Context) -> dict[str, Any]:
    try:
        return {"ok": True, **(await _manager(ctx).disconnect(send_stop=True))}
    except Exception as exc:
        return _error_result(exc)


async def _doctor() -> int:
    manager = _new_manager()
    result: dict[str, Any] = {
        "ok": True,
        "server": "heartbeat-yuanli-mcp",
        "version": "0.2.0",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "preferred_name": manager.preferred_name,
        "preferred_address_configured": bool(manager.preferred_address),
        "dependencies": {"bleak": True, "mcp": True},
    }
    try:
        result["devices"] = await manager.list_devices(timeout=3.0)
    except Exception as exc:
        result["ok"] = False
        result["error"] = {"code": "doctor_failed", "message": str(exc)}
    # Windows consoles may use GBK and nearby device names can contain emoji.
    # ASCII JSON escapes preserve every name without relying on the console codec.
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if "--doctor" in sys.argv:
        raise SystemExit(asyncio.run(_doctor()))
    import argparse

    parser = argparse.ArgumentParser(description="Heartbeat Yuanli DokiDoki MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address; loopback by default")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

