"""Locally stored command timelines and observable, cancellable playback."""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import inspect
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any


MAX_SECONDS = 120.0
MAX_EVENTS = 100
MIN_SUCTION_INTERVAL = 2.0
INTENT_PRESETS: dict[str, dict[str, Any]] = {
    "gentle": {"minimum_level": 5, "maximum_level": 20, "angular_frequency": 1.5},
    "medium": {"minimum_level": 10, "maximum_level": 45, "angular_frequency": 0.8},
    "intense": {"minimum_level": 30, "maximum_level": 80, "angular_frequency": 0.4},
}
INTENT_TERMS = {
    "gentle": ("温柔", "轻柔", "轻一点", "柔和", "gentle", "soft", "mild"),
    "medium": ("普通", "适中", "正常", "中等", "medium", "normal", "moderate"),
    "intense": ("猛烈", "强烈", "刺激", "强一点", "大力", "intense", "strong", "powerful"),
}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限数字。")
    return float(value)


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise ValueError("名称须为 1–64 个字符。")
    name = name.strip()
    if any(ord(char) < 32 for char in name):
        raise ValueError("名称不能包含控制字符。")
    return name


def validate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError(f"波形必须包含 1–{MAX_EVENTS} 个事件。")
    normalized = []
    last_at = -1.0
    last_suction = -MIN_SUCTION_INTERVAL
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("波形事件必须是对象。")
        at = _number(event.get("at"), "at")
        if not 0 <= at <= MAX_SECONDS or at < last_at:
            raise ValueError(f"事件时间必须按顺序排列并处于 0–{MAX_SECONDS:g} 秒。")
        action = event.get("action")
        if action not in {"suction", "vibration", "stop_vibration", "stop_device"}:
            raise ValueError("事件动作须为 suction、vibration、stop_vibration 或 stop_device。")
        item: dict[str, Any] = {"at": round(at, 3), "action": action}
        if action in {"suction", "vibration"}:
            level = event.get("level")
            if type(level) is not int or not 1 <= level <= 100:
                raise ValueError("非零动作的 level 须为 1–100 的整数。")
            item["level"] = level
        if action == "suction":
            if at - last_suction < MIN_SUCTION_INTERVAL - 1e-6:
                raise ValueError(f"相邻吮吸触发至少间隔 {MIN_SUCTION_INTERVAL:g} 秒。")
            last_suction = at
        last_at = at
        normalized.append(item)
    return normalized


def sine_events(duration: float, period: float, minimum: int, maximum: int,
                interval: float, vibration: int | None, phase_radians: float = 0.0) -> list[dict[str, Any]]:
    duration = _number(duration, "duration_seconds")
    period = _number(period, "period_seconds")
    interval = _number(interval, "interval_seconds")
    phase_radians = _number(phase_radians, "phase_radians")
    if not 2 <= duration <= MAX_SECONDS or not 2 <= period <= MAX_SECONDS:
        raise ValueError(f"时长和周期须在 2–{MAX_SECONDS:g} 秒之间。")
    if interval < MIN_SUCTION_INTERVAL or interval > duration:
        raise ValueError(f"采样间隔须至少 {MIN_SUCTION_INTERVAL:g} 秒且不超过时长。")
    if type(minimum) is not int or type(maximum) is not int or not 1 <= minimum <= maximum <= 100:
        raise ValueError("最小和最大强度须为 1–100 的整数，且最小值不超过最大值。")
    if vibration is not None and (type(vibration) is not int or not 1 <= vibration <= 100):
        raise ValueError("叠加震动强度须为 1–100 的整数，或不设置。")
    count = math.floor(duration / interval) + 1
    if count + (vibration is not None) > MAX_EVENTS:
        raise ValueError("事件数过多，请增加采样间隔。")
    result: list[dict[str, Any]] = []
    if vibration is not None:
        result.append({"at": 0.0, "action": "vibration", "level": vibration})
    for index in range(count):
        at = round(index * interval, 3)
        level = round((minimum + maximum) / 2 + (maximum - minimum) / 2 * math.sin(2 * math.pi * at / period + phase_radians))
        result.append({"at": at, "action": "suction", "level": level})
    return validate_events(result)


def resolve_intent(intent: str) -> tuple[str, dict[str, Any]]:
    """Resolve a natural-language style phrase to a deterministic preset."""
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("intent 须描述温柔、普通或猛烈等强度风格。")
    normalized = intent.strip().casefold()
    matches = [key for key, terms in INTENT_TERMS.items() if any(term.casefold() in normalized for term in terms)]
    if len(matches) != 1:
        if not matches:
            raise ValueError("无法识别意图；请使用温柔、普通或猛烈，或提供明确的强度和角频率。")
        raise ValueError("意图同时匹配多个强度档位，请改为单一风格。")
    key = matches[0]
    return key, dict(INTENT_PRESETS[key])


def intent_waveform(name: str, intent: str, duration: float = 20.0, interval: float = 2.0,
                    vibration: int | None = None, minimum: int | None = None,
                    maximum: int | None = None, angular_frequency: float | None = None,
                    phase_radians: float = 0.0) -> dict[str, Any]:
    """Build a named preset waveform, with explicit optional parameter overrides."""
    preset_name, params = resolve_intent(intent)
    lo = params["minimum_level"] if minimum is None else minimum
    hi = params["maximum_level"] if maximum is None else maximum
    omega = params["angular_frequency"] if angular_frequency is None else _number(angular_frequency, "angular_frequency")
    if omega <= 0:
        raise ValueError("angular_frequency 必须大于 0。")
    period = 2 * math.pi / omega
    events = sine_events(duration, period, lo, hi, interval, vibration, phase_radians)
    return {
        "name": validate_name(name), "intent": preset_name,
        "parameters": {"minimum_level": lo, "maximum_level": hi,
                       "angular_frequency": round(omega, 6), "period_seconds": round(period, 3),
                       "phase_radians": phase_radians, "duration_seconds": duration,
                       "interval_seconds": interval, "vibration_level": vibration},
        "events": events,
    }


class WaveStore:
    """SQLite stores user-defined timelines and command recordings, never BLE addresses."""

    def __init__(self, path: Path | None = None):
        root = os.getenv("TFPTPRO_WAVEFORM_DIR") or os.getenv("LOCALAPPDATA")
        if not root:
            root = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        self.path = path or Path(root) / "heartbeat-yuanli-mcp" / "waveforms.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE IF NOT EXISTS items (kind TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind, name))")
        return db

    def put(self, kind: str, name: str, data: dict[str, Any]) -> None:
        name = validate_name(name)
        with closing(self._connect()) as db, db:
            try:
                db.execute("INSERT INTO items VALUES (?, ?, ?)", (kind, name, json.dumps(data, ensure_ascii=False)))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"{kind} 名称已存在，请使用新名称。") from exc

    def get(self, kind: str, name: str) -> dict[str, Any]:
        name = validate_name(name)
        with closing(self._connect()) as db:
            row = db.execute("SELECT data FROM items WHERE kind = ? AND name = ?", (kind, name)).fetchone()
        if row is None:
            raise ValueError(f"找不到 {kind}：{name}。")
        return json.loads(row[0])

    def names(self, kind: str) -> list[str]:
        with closing(self._connect()) as db:
            return [row[0] for row in db.execute("SELECT name FROM items WHERE kind = ? ORDER BY name", (kind,))]


class WaveService:
    def __init__(self, manager: Any, store: WaveStore | None = None):
        self.manager = manager
        self.store = store or WaveStore()
        self._action_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._run: dict[str, Any] = {"state": "idle", "events": []}
        self._recording: dict[str, Any] | None = None
        self._record_started = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def save_waveform(self, name: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        name = validate_name(name)
        item = {"name": name, "events": validate_events(events)}
        self.store.put("waveform", name, item)
        return item

    def start_recording(self) -> dict[str, Any]:
        if self._recording is not None:
            raise ValueError("已有录制进行中，请先停止录制。")
        self._record_started = time.monotonic()
        self._recording = {"started_at": datetime.now(timezone.utc).isoformat(), "events": []}
        return {"recording": True, "meaning": "记录本服务发送的动作及回报，不是物理强度测量。"}

    async def stop_recording(self, name: str) -> dict[str, Any]:
        name = validate_name(name)
        async with self._action_lock:
            if self._recording is None:
                raise ValueError("当前没有录制。")
            result = {**self._recording, "name": name}
            self.store.put("recording", name, result)
            self._recording = None
        return result

    def _record(self, action: str, level: int | None, source: str, started: float,
                result: dict[str, Any] | None, error: str | None) -> None:
        if self._recording is None:
            return
        event: dict[str, Any] = {"at": round(max(0.0, started - self._record_started), 3),
                                 "action": action, "source": source,
                                 "ok": bool(result and result.get("ok")),
                                 "acknowledged": result.get("acknowledged") if result else None}
        if level is not None:
            event["level"] = level
        if error:
            event["error"] = error
        self._recording["events"].append(event)

    async def perform(self, action: str, level: int | None = None, *, confirm: bool = False,
                      source: str = "manual") -> dict[str, Any]:
        async with self._action_lock:
            started = time.monotonic()
            result = None
            error = None
            cancelled = False
            if action == "stop_vibration":
                operation = self.manager.stop_vibration()
            elif action == "stop_device":
                operation = self.manager.stop()
            else:
                operation = self.manager.set_action(action, level, confirm=confirm)
            task = asyncio.create_task(operation)
            try:
                try:
                    result = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Wait for an in-flight write to finish before any stop can run.
                    cancelled = True
                    result = await task
                if not result.get("ok", False):
                    error = str(result.get("error") or result.get("reason") or "设备动作未成功。")
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                self._record(action, level, source, started, result, error)
                if cancelled:
                    raise asyncio.CancelledError

    async def start(self, name: str, confirm: bool, stop_vibration_on_end: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("播放波形需要 confirm=true。")
        async with self._lifecycle_lock:
            if self.running:
                raise ValueError("已有波形正在播放，请先停止。")
            waveform = self.store.get("waveform", name)
            events = validate_events(waveform["events"])
            status = self.manager.status()
            if inspect.isawaitable(status):
                status = await status
            if not status.get("connected"):
                raise ValueError("设备未连接；请先连接设备。")
            capabilities = set(status.get("capabilities", []))
            missing = {e["action"] for e in events if e["action"] in {"suction", "vibration"}} - capabilities
            if missing:
                raise ValueError(f"当前设备不支持波形动作：{', '.join(sorted(missing))}。")
            self._run = {"name": name, "state": "playing", "total": len(events), "completed": 0,
                         "events": [], "error": None, "vibration_stops_automatically": bool(stop_vibration_on_end)}
            self._task = asyncio.create_task(self._play(events, stop_vibration_on_end), name="tfptpro-waveform")
        return self.status()

    async def _play(self, events: list[dict[str, Any]], stop_vibration_on_end: bool = False) -> None:
        started = time.monotonic()
        last_suction = -math.inf
        try:
            for event in events:
                deadline = started + event["at"]
                if event["action"] == "suction":
                    deadline = max(deadline, last_suction + MIN_SUCTION_INTERVAL)
                await asyncio.sleep(max(0.0, deadline - time.monotonic()))
                if event["action"] == "suction":
                    last_suction = time.monotonic()
                result = await self.perform(event["action"], event.get("level"), confirm=True, source="waveform")
                self._run["events"].append({**event, "sent_at": round(time.monotonic() - started, 3),
                                            "ok": bool(result.get("ok")), "acknowledged": result.get("acknowledged")})
                if not result.get("ok"):
                    raise RuntimeError(str(result.get("error") or result.get("reason") or "设备动作失败。"))
                self._run["completed"] += 1
            if stop_vibration_on_end and any(e["action"] == "vibration" for e in events):
                result = await self.perform("stop_vibration", source="waveform_end")
                self._run["vibration_stop_result"] = result
            self._run["state"] = "completed"
        except asyncio.CancelledError:
            self._run["state"] = "stopped"
            raise
        except Exception as exc:
            self._run["state"] = "failed"
            self._run["error"] = str(exc)

    def status(self) -> dict[str, Any]:
        return {**self._run, "recording": self._recording is not None,
                "recorded_events": len(self._recording["events"]) if self._recording else 0}

    async def stop(self, stop_vibration: bool = False) -> dict[str, Any]:
        async with self._lifecycle_lock:
            task = self._task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if stop_vibration:
                self._run["vibration_stop_result"] = await self.perform("stop_vibration", source="waveform_stop")
            return self.status()

    def save_recording_as_waveform(self, recording_name: str, waveform_name: str) -> dict[str, Any]:
        recording = self.store.get("recording", recording_name)
        events = [{"at": e["at"], "action": e["action"], **({"level": e["level"]} if "level" in e else {})}
                  for e in recording["events"] if e.get("ok") and e.get("action") in {"suction", "vibration", "stop_vibration", "stop_device"}]
        return self.save_waveform(waveform_name, events)

    async def shutdown(self) -> None:
        await self.stop()
