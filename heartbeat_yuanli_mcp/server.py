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
from .waveforms import WaveService, WaveStore, sine_events

LOGGER = logging.getLogger("heartbeat_yuanli_mcp")
os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")


@dataclass
class AppContext:
    manager: Any
    waves: WaveService


def _new_manager() -> Any:
    if os.getenv("TFPTPRO_WEB_BRIDGE") == "1":
        return WebControllerBridge()
    return DokiDokiDeviceManager()


@contextlib.asynccontextmanager
async def app_lifespan(_server: Any) -> AsyncIterator[AppContext]:
    manager = _new_manager()
    waves = WaveService(manager)
    try:
        yield AppContext(manager=manager, waves=waves)
    finally:
        await waves.shutdown()
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


def _waves(ctx: Context) -> WaveService:
    lifespan_context = ctx.lifespan_context
    if lifespan_context is None:
        raise RuntimeError("MCP 请求上下文不可用。")
    return lifespan_context.waves


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
        return await _waves(ctx).perform("suction", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_vibration", description="设置支持设备的震动强度并持续运行，直到调用 stop_vibration 或 stop_device；TF-PTPRO 使用前须通过设备实体键切换到震动模式。非零值必须 confirm=true。")
async def set_vibration(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _waves(ctx).perform("vibration", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_linear", description="设置 DK-META2 伸缩强度；非零值必须 confirm=true。")
async def set_linear(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _waves(ctx).perform("linear", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="set_rotary", description="设置 DK-META2 旋转强度；非零值必须 confirm=true。")
async def set_rotary(ctx: Context, level: int, confirm: bool = False) -> dict[str, Any]:
    try:
        return await _waves(ctx).perform("rotary", level, confirm=confirm)
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="stop_device", description="将当前设备所有已支持输出归零；可重复调用。")
async def stop_device(ctx: Context) -> dict[str, Any]:
    try:
        await _waves(ctx).stop()
        return await _waves(ctx).perform("stop_device", source="manual")
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="stop_vibration", description="发送震动通道归零指令；TF-PTPRO 的单通道停止帧与吮吸通道的隔离效果仍需真机验证。若需立即停止所有输出，请调用 stop_device。")
async def stop_vibration(ctx: Context) -> dict[str, Any]:
    try:
        return await _waves(ctx).perform("stop_vibration", source="manual")
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="disconnect_device", description="停止心跳并断开 MCP 持有的 BLE 连接。")
async def disconnect_device(ctx: Context) -> dict[str, Any]:
    try:
        await _waves(ctx).stop()
        return {"ok": True, **(await _manager(ctx).disconnect(send_stop=True))}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="create_sine_waveform", description="创建并本地保存正弦强度曲线。吮吸是按间隔触发的单次动作；可选叠加持续震动。")
async def create_sine_waveform(name: str, duration_seconds: float, period_seconds: float,
                               minimum_level: int, maximum_level: int, interval_seconds: float = 2.0,
                               vibration_level: int | None = None) -> dict[str, Any]:
    try:
        events = sine_events(duration_seconds, period_seconds, minimum_level, maximum_level,
                             interval_seconds, vibration_level)
        item = {"name": name, "events": events}
        from .waveforms import validate_name, validate_events
        item["name"] = validate_name(name)
        item["events"] = validate_events(events)
        WaveStore().put("waveform", item["name"], item)
        return {"ok": True, **item, "note": "强度是发送给设备的协议值，不是物理测量值。"}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="save_waveform", description="按时间偏移保存自定义动作序列，事件为 [{at:秒, action:suction|vibration|stop_vibration|stop_device, level?:1-100}]。")
async def save_waveform(name: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        item = WaveService.__new__(WaveService)
        item.store = WaveStore()
        result = item.save_waveform(name, events)
        return {"ok": True, **result}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="list_waveforms", description="列出本机保存的波形名称。")
async def list_waveforms() -> dict[str, Any]:
    try:
        store = WaveStore()
        return {"ok": True, "waveforms": store.names("waveform"), "recordings": store.names("recording")}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="get_waveform", description="查看已保存波形的完整动作时间序列。")
async def get_waveform(name: str) -> dict[str, Any]:
    try:
        return {"ok": True, **WaveStore().get("waveform", name)}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="start_waveform", description="经 confirm=true 后在已连接设备上异步播放已保存波形。结束时默认保持震动，需单独 stop_vibration；停止波形本身不会停止震动。")
async def start_waveform(ctx: Context, name: str, confirm: bool = False) -> dict[str, Any]:
    try:
        return {"ok": True, **(await _waves(ctx).start(name, confirm))}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="get_waveform_status", description="查看波形播放状态、已发送命令、回报和录制状态。")
async def get_waveform_status(ctx: Context) -> dict[str, Any]:
    return {"ok": True, **_waves(ctx).status()}


@mcp.tool(name="stop_waveform", description="停止后续波形事件；默认不停止当前震动，stop_vibration=true 时另行发送震动停止。")
async def stop_waveform(ctx: Context, stop_vibration: bool = False) -> dict[str, Any]:
    try:
        return {"ok": True, **(await _waves(ctx).stop(stop_vibration=stop_vibration))}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="start_action_recording", description="开始记录本 MCP 发送的命令和设备回报；不测量实际吮吸或震动强度。")
async def start_action_recording(ctx: Context) -> dict[str, Any]:
    try:
        return {"ok": True, **_waves(ctx).start_recording()}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="stop_action_recording", description="结束并本地保存命令录制。")
async def stop_action_recording(ctx: Context, name: str) -> dict[str, Any]:
    try:
        return {"ok": True, **(await _waves(ctx).stop_recording(name))}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="get_action_recording", description="查看已保存的动作录制，包括命令时间、强度和设备回报。")
async def get_action_recording(name: str) -> dict[str, Any]:
    try:
        return {"ok": True, **WaveStore().get("recording", name)}
    except Exception as exc:
        return _error_result(exc)


@mcp.tool(name="save_recording_as_waveform", description="将成功发送的录制动作保存为可重复播放的波形。")
async def save_recording_as_waveform(recording_name: str, waveform_name: str) -> dict[str, Any]:
    try:
        service = WaveService.__new__(WaveService)
        service.store = WaveStore()
        return {"ok": True, **service.save_recording_as_waveform(recording_name, waveform_name)}
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

