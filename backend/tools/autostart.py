"""Start Jarvis when you sign in to Windows - or stop doing that.

    python tools/autostart.py install   # a "Jarvis" shortcut in the Startup folder
    python tools/autostart.py remove
    python tools/autostart.py status

The shortcut runs start_jarvis_voice.bat with "autostart": it starts Docker
Desktop if it isn't running (Home Assistant comes up with it), skips
Voicebox while spoken replies are off, and opens the Jarvis window
minimized. A shortcut in the Startup folder rather than a scheduled task or
a registry key on purpose: it shows up in Task Manager -> Startup apps like
any other, where it can be switched off with one click.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LAUNCHER = BACKEND / "start_jarvis_voice.bat"
SHORTCUT_NAME = "Jarvis.lnk"


def startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_folder() / SHORTCUT_NAME


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install() -> Path:
    path = shortcut_path()
    cmd = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
    script = "; ".join([
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(path)) + ")",
        "$s.TargetPath = " + _ps_quote(str(cmd)),
        "$s.Arguments = " + _ps_quote(f'/c ""{LAUNCHER}" autostart"'),
        "$s.WorkingDirectory = " + _ps_quote(str(BACKEND)),
        "$s.WindowStyle = 7",  # minimized: the launcher's console flashes in the taskbar, not on screen
        "$s.Description = 'Jarvis - голосовой помощник и дом'",
        "$s.Save()",
    ])
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
    return path


def remove() -> bool:
    path = shortcut_path()
    if path.exists():
        path.unlink()
        return True
    return False


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "install":
        print(f"Готово: Jarvis будет запускаться при входе в Windows ({install()}).")
    elif command == "remove":
        print("Автозапуск убран." if remove() else "Автозапуска и так не было.")
    elif command == "status":
        print(f"Автозапуск включён: {shortcut_path()}" if shortcut_path().exists() else "Автозапуск выключен.")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
