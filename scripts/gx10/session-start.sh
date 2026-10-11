#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
source "$here/desktop-env.sh"
xset s off
xset s noblank
xset -dpms
xset dpms force on
# GNOME can change DPMS while waking; disable it after the wake as well.
xset -dpms
systemctl --user import-environment DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR
# Starting is a no-op while the application checkout has not been configured.
if [[ -f "$HOME/.config/daybook-gx10/app-dir" ]]; then
    systemctl --user reset-failed daybook.service || true
    systemctl --user start daybook.service
fi
