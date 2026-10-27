"""Weekly discovery on each platform. Writes the job's files, never turns them on."""

import shutil
import sys
from pathlib import Path

LABEL = "com.skillscout.weekly"


def _cmd(exe: Path) -> list[str]:
    return [str(exe), "-m", "skillscout.cli", "discover", "--notify"]


def plan(platform: str, exe: Path, state: Path, home: Path, has_systemd: bool) -> tuple[dict[Path, str], str]:
    """Returns the files to write and what to tell the user. Nothing here touches disk."""
    log = state / "weekly.log"
    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        args = "".join(f"<string>{a}</string>" for a in _cmd(exe))
        text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""
        return {plist: text}, f"wrote {plist}\nturn it on with:  launchctl load {plist}\nturn it off with: launchctl unload {plist}"

    if platform.startswith("linux") and has_systemd:
        unit = home / ".config" / "systemd" / "user"
        service = f"""[Unit]
Description=skillscout weekly discovery

[Service]
Type=oneshot
ExecStart={" ".join(f'"{a}"' for a in _cmd(exe))}
StandardOutput=append:{log}
StandardError=append:{log}
"""
        timer = """[Unit]
Description=Run skillscout discover every Monday at 10:00

[Timer]
OnCalendar=Mon *-*-* 10:00:00
Persistent=true

[Install]
WantedBy=timers.target
"""
        files = {unit / "skillscout.service": service, unit / "skillscout.timer": timer}
        return files, (f"wrote {unit}/skillscout.service and skillscout.timer\n"
                       "turn it on with:  systemctl --user daemon-reload && systemctl --user enable --now skillscout.timer\n"
                       "turn it off with: systemctl --user disable --now skillscout.timer")

    if platform in ("win32", "cygwin"):
        tr = " ".join(f'\\"{a}\\"' if " " in a else a for a in _cmd(exe))
        return {}, ("Windows has no file to write. Turn it on in a terminal with:\n"
                    f'  schtasks /Create /SC WEEKLY /D MON /ST 10:00 /TN skillscout-weekly /TR "{tr}"\n'
                    "turn it off with: schtasks /Delete /TN skillscout-weekly /F")

    line = f"0 10 * * 1 {' '.join(_cmd(exe))} >> {log} 2>&1"
    return {}, f"no systemd found. Add this line with `crontab -e`:\n  {line}"


def write(files: dict[Path, str]) -> None:
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def here(state: Path) -> tuple[dict[Path, str], str]:
    return plan(sys.platform, Path(sys.executable), state, Path.home(), shutil.which("systemctl") is not None)
