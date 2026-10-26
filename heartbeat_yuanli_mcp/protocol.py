"""Observed DokiDoki BLE frames for explicitly implemented device profiles.

Frame constructors are pure so they can be checked without a BLE device.
TF-PTPRO exposes one-shot suction and persistent vibration actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable, Sequence

SERVICE_UUID: Final = "0000ffac-0000-1000-8000-00805f9b34fb"
SERVICE_SHORT_UUID: Final = "ffac"
CHAR_WRITE_RESPONSE: Final = "ffb5"
CHAR_WRITE_NO_RESPONSE: Final = "ffb7"
CHAR_NOTIFY: Final = "ffb8"

CMD_APP_REQUEST: Final = 0x02
CMD_VIBRATION: Final = 0x08
CMD_LINEAR: Final = 0x0B
CMD_ROTARY: Final = 0x0E
CMD_HEARTBEAT: Final = 0xEF

DEVICE_PREFIXES: Final = ("TF-", "DK-", "TRYFUN-")
DEFAULT_DEVICE_NAME: Final = "DK-META2"


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    protocol: str
    commands: dict[str, int]

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self.commands)


DK_META2_PROFILE: Final = DeviceProfile(
    name=DEFAULT_DEVICE_NAME,
    protocol="dokidoki",
    commands={
        "vibration": CMD_VIBRATION,
        "linear": CMD_LINEAR,
        "rotary": CMD_ROTARY,
    },
)

TF_PTPRO_PROFILE: Final = DeviceProfile(
    name="TF-PTPRO",
    protocol="dokidoki-tfptpro",
    commands={"suction": 0x12, "vibration": 0x09},
)

PROFILES: Final = {DK_META2_PROFILE.name: DK_META2_PROFILE, "DK-PTPRO": TF_PTPRO_PROFILE}


def normalize_profile_name(device_name: str | None) -> str:
    """Normalize the aliases used by the official client."""

    normalized = (device_name or "").strip().upper().rstrip("\x00")
    if normalized.startswith("TF-"):
        return f"DK-{normalized[3:]}"
    return normalized


def get_device_profile(device_name: str | None) -> DeviceProfile | None:
    return PROFILES.get(normalize_profile_name(device_name))


def is_supported_device_name(device_name: str | None) -> bool:
    normalized = (device_name or "").strip().upper().rstrip("\x00")
    return normalized.startswith(DEVICE_PREFIXES)


def _as_bytes(values: Iterable[int]) -> bytes:
    result = bytes(values)
    if any(value < 0 or value > 0xFF for value in result):
        raise ValueError("BLE frame bytes must be between 0 and 255")
    return result


def calculate_checksum(payload: Iterable[int]) -> int:
    """Return the two's-complement byte checksum used by the app."""

    payload_bytes = _as_bytes(payload)
    return (-sum(payload_bytes)) & 0xFF


def build_frame(message_id: int, command_type: int, payload: Sequence[int]) -> bytes:
    """Build one DokiDoki frame.

    The installed client uses a single frame (total/current = 1/0).  The
    checksum covers the payload only, matching its JavaScript implementation.
    """

    if not isinstance(message_id, int) or isinstance(message_id, bool) or not 1 <= message_id <= 15:
        raise ValueError("message_id must be an integer from 1 to 15")
    if not isinstance(command_type, int) or isinstance(command_type, bool) or not 0 <= command_type <= 0xFF:
        raise ValueError("command_type must be an integer from 0 to 255")

    payload_bytes = _as_bytes(payload)
    length = len(payload_bytes) + 1
    if length > 0xFF:
        raise ValueError("BLE payload is too large")

    header = bytes((message_id & 0x0F, command_type, 0x00, length))
    return header + payload_bytes + bytes((calculate_checksum(payload_bytes),))


def build_command_frame(message_id: int, command: int, value: int) -> bytes:
    return build_frame(message_id, CMD_APP_REQUEST, (command, value))


def build_action_frame(
    message_id: int,
    action: str,
    level: int,
    profile: DeviceProfile | None = None,
) -> bytes:
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
        raise ValueError("level must be an integer from 0 to 100")
    selected_profile = profile or (TF_PTPRO_PROFILE if action == "suction" else DK_META2_PROFILE)
    if action not in selected_profile.commands:
        raise ValueError(f"No verified DokiDoki profile supports {action!r}")
    # TF-PTPRO uses compound action payloads, unlike DK-META2's one-byte commands.
    if action == "suction" and selected_profile.protocol == TF_PTPRO_PROFILE.protocol:
        return build_frame(message_id, CMD_APP_REQUEST, (0x10, 0x01, 0x12, level))
    if action == "vibration" and selected_profile.protocol == TF_PTPRO_PROFILE.protocol:
        if level == 0:
            return build_frame(message_id, CMD_APP_REQUEST, (0x07, 0x00, 0x09, 0x00))
        return build_frame(message_id, CMD_APP_REQUEST, (0x07, 0x01, 0x09, level))
    command = selected_profile.commands[action]
    return build_command_frame(message_id, command, level)


def build_stop_frame(message_id: int, profile: DeviceProfile) -> bytes:
    if profile.protocol == TF_PTPRO_PROFILE.protocol:
        return build_frame(message_id, CMD_APP_REQUEST, (0x07, 0x00, 0x10, 0x00, 0x09, 0x00, 0x12, 0x00))
    payload = tuple(value for action in ("linear", "rotary", "vibration")
                    if action in profile.commands for value in (profile.commands[action], 0))
    return build_frame(message_id, CMD_APP_REQUEST, payload) if payload else build_command_frame(message_id, CMD_HEARTBEAT, 1)


@dataclass
class MessageIdGenerator:
    """Cycle message IDs in the same 1..15 range as the official client."""

    current: int = 0

    def reset(self) -> None:
        self.current = 0

    def next(self) -> int:
        self.current = 1 if self.current >= 15 else self.current + 1
        return self.current
