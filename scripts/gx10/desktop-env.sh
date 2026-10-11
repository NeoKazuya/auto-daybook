#!/usr/bin/env bash
# Source this from SSH or a service to use the actual logged-in GNOME desktop.
eval "$(python3 - <<'PY'
import os, pathlib, shlex, sys
for proc in pathlib.Path('/proc').glob('[0-9]*'):
    try:
        if proc.stat().st_uid != os.getuid() or (proc / 'comm').read_text().strip() != 'gnome-shell':
            continue
        env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
        if env.get(b'DISPLAY') and env.get(b'XAUTHORITY'):
            # Keep desktop launchers (including Snap browsers) discoverable.
            for key in ('DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE', 'XDG_DATA_DIRS'):
                if key.encode() in env:
                    print(f'export {key}={shlex.quote(os.fsdecode(env[key.encode()]))}')
            break
    except (OSError, ValueError):
        continue
else:
    print('No logged-in GNOME X11 desktop found', file=sys.stderr)
    print('return 1 2>/dev/null || exit 1')
PY
)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
