#!/usr/bin/env bash
# Run as the desktop user, not root. Installs persistent hackathon settings.
set -euo pipefail
[[ $(id -u) != 0 ]] || { echo 'Run as the desktop user (asus), not root.' >&2; exit 1; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
source "$here/desktop-env.sh"
sudo apt-get update
sudo apt-get install -y --no-install-recommends xdotool wmctrl mpv gh ripgrep python3-tk python3-pil python3-xlib libxcb-cursor0 gnome-shell-extension-appindicator

if [[ ! -x "$HOME/.local/bin/uv" ]] && ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
gsettings set org.gnome.settings-daemon.plugins.power idle-dim false
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
# Preserve unrelated GDM settings while making Xorg and automatic login persistent.
sudo python3 - "$USER" <<'PY'
import configparser, pathlib, sys
path = pathlib.Path('/etc/gdm3/custom.conf')
config = configparser.ConfigParser()
config.optionxform = str
config.read(path)
if not config.has_section('daemon'):
    config.add_section('daemon')
config['daemon'].update(WaylandEnable='false', AutomaticLoginEnable='true', AutomaticLogin=sys.argv[1])
with path.open('w') as stream:
    config.write(stream)
PY
sudo loginctl enable-linger "$USER"
install_dir="$HOME/.local/share/daybook-gx10"
mkdir -p "$install_dir" "$HOME/.local/bin" "$HOME/.config/autostart" "$HOME/.config/systemd/user" "$HOME/.config/daybook-gx10"
if [[ "$here" != "$install_dir" ]]; then
    cp "$here/"*.sh "$here/"*.py "$install_dir/"
fi
chmod +x "$install_dir/"*.sh "$install_dir/"*.py
cat > "$HOME/.local/bin/gx10" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
source "$HOME/.local/share/daybook-gx10/desktop-env.sh"
export PATH="$HOME/.local/bin:$PATH"
exec python3 "$HOME/.local/share/daybook-gx10/desktop-test.py" "$@"
WRAPPER
chmod +x "$HOME/.local/bin/gx10"
cat > "$HOME/.config/autostart/daybook-desktop.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Daybook desktop startup
Exec=$install_dir/session-start.sh
X-GNOME-Autostart-enabled=true
DESKTOP
cat > "$HOME/.config/systemd/user/daybook.service" <<'UNIT'
[Unit]
Description=Daybook application
After=graphical-session.target
PartOf=graphical-session.target
StartLimitIntervalSec=120
StartLimitBurst=5
ConditionPathExists=%h/.config/daybook-gx10/app-dir

[Service]
Type=simple
ExecStart=%h/.local/share/daybook-gx10/run-app.sh
Restart=on-failure
RestartSec=5

UNIT
systemctl --user daemon-reload
# Unlock the existing session without restarting GDM or interrupting other jobs.
while read -r session _; do
    if [[ $(loginctl show-session "$session" -p Type --value) == x11 ]] &&
       [[ $(loginctl show-session "$session" -p User --value) == "$(id -u)" ]]; then
        loginctl unlock-session "$session"
    fi
done < <(loginctl list-sessions --no-legend)
"$install_dir/session-start.sh"
if ! gnome-extensions enable ubuntu-appindicators@ubuntu.com; then
    echo 'AppIndicator installed; GNOME will discover it at the next login.'
fi
echo 'GX10 unattended setup installed. Run ~/.local/bin/gx10 status and gx10 smoke.'
