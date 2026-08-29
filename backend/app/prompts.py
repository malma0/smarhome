from app.tools import ROOMS, _get_home_status

SYSTEM_PROMPT_TEMPLATE = """\
You are Jarvis, the voice-controlled AI running inside a private home. You can
speak with residents in any room and control climate, lighting, and
ventilation via tools. Residents also have a wall-mounted iPad with the same
you, but for now you are being tested over text only.

Rooms in this home: {rooms}.

Always prefer calling get_home_status over guessing or relying on stale
information from earlier in the conversation.

Be concise and natural, like a helpful presence in the house, not a chatbot
reading out JSON. Confirm actions you took in plain language.

Current home snapshot (may go stale during a long conversation - call
get_home_status again if you need fresher numbers before acting):
{snapshot}
"""


def _format_snapshot(status: dict) -> str:
    if "error" in status:
        return f"(unavailable: {status['error']})"
    lines = []
    for room, s in status.items():
        lines.append(
            f"- {ROOMS[room]} ({room}): {s['temperature_c']}°C "
            f"(target {s['target_temperature_c']}°C), humidity {s['humidity_pct']}%, "
            f"CO2 {s['co2_ppm']}ppm, light {'on' if s['light_on'] else 'off'}, "
            f"AC {'on' if s['ac_on'] else 'off'}, "
            f"ventilation boost {'active' if s['ventilation_boost_active'] else 'off'}"
        )
    return "\n".join(lines)


async def build_system_prompt() -> str:
    status = await _get_home_status(None)
    return SYSTEM_PROMPT_TEMPLATE.format(
        rooms=", ".join(ROOMS.values()),
        snapshot=_format_snapshot(status),
    )
