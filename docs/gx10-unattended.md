# Unattended GX10 operation

The GX10 is a dedicated hackathon machine. Its desktop stays awake, unlocked,
and available to agents over SSH. These are persistent settings, with no
restore-profile machinery.

## Install

Copy `scripts/gx10/` to the GX10 and run `bash setup.sh` as `asus`.
The installer:

- disables GNOME automatic lock, idle blanking, dimming, and AC suspend;
- masks system sleep targets;
- configures GDM for automatic login as the current user, using Xorg;
- disables X11 screensaver and DPMS, now and on each desktop login;
- installs xdotool, wmctrl, mpv, screenshot/test dependencies, gh, uv, and tray support;
- installs the `gx10` command and the Daybook user service.

It unlocks the existing desktop without restarting GDM or rebooting. GDM's
boot-login settings take effect on the next login/boot. Keep the physical
monitor connected. A monitor's own hardware sleep settings are separate.

The scripts discover `DISPLAY` and `XAUTHORITY` from the running GNOME session,
so a change from `:1` after reboot doesn't require editing scripts. For other
SSH tools:

```sh
source ~/.local/share/daybook-gx10/desktop-env.sh
```

## Check and test

```sh
~/.local/bin/gx10 status
~/.local/bin/gx10 smoke
~/.local/bin/gx10 smoke --vision
```

To run a test independently of SSH or your laptop connection:

```sh
systemd-run --user --unit=gx10-smoke --collect ~/.local/bin/gx10 smoke --vision
journalctl --user -u gx10-smoke -f
```

`status` reports the desktop, power settings, audio output, and model API.
`smoke` opens a disposable native test window, focuses it, types into an entry,
clicks a button, goes fullscreen, captures pixels in memory, and verifies Escape.
It closes its own window and returns focus to the previous window even on failure.
`--vision` also asks the local model to read `GX10READY` from that actual screenshot.
This verifies desktop mechanics and image input, not Daybook's future note quality,
Qt overlay, music selection, or privacy implementation.

`status` and `smoke --vision` read the model profile from
`$DAYBOOK_HOME/daybook.toml` (default `~/.daybook/daybook.toml`): `[model]`
`base_url`, `model`, and `api_key_file`. They only read the config and key file;
they do not import or install Daybook, create a config, or start a model.

If the config or a model setting is absent, the fallback matches the app's current
Qwen profile: `http://127.0.0.1:18300/v1`, `qwen3.8-flash-next`, and
`~/.config/qwen38/api-key`. An unreadable or malformed config, or an invalid
model setting, fails with the config path rather than falling back silently.
An empty `api_key_file` disables authentication. Desktop-only `smoke`, `run`,
and `enable-app` do not read the model profile.

To test another already-running backend, supply `--base-url`, `--model`, and
`--key-file`. Changing `--base-url` to a different endpoint drops the inherited
profile key; supply `--key-file` explicitly if that backend needs authentication.
An override of the same endpoint (ignoring a trailing slash) keeps profile auth.
`--model` changes only the model ID. Use `--key-file ''` to disable authentication.
Keys are read from files and are not included in status output. Qwen3.8 is serving
on the shared machine; Ollama must stay stopped while it is serving.

The test takes the same `/tmp/daybook-capture.lock` specified by the app's design,
so it refuses to run during another capture/test session. Wrap other standalone
desktop tests with:

```sh
gx10 run -- python3 my_desktop_test.py
```

Don't wrap `daybook run` itself: it owns that lock. For unattended test scripts,
keep synthetic activity inside their own test windows; reset them between cases.
Screenshots in this smoke test never go to disk. It does not move the mouse or
type periodically to defeat Daybook's separate five-minute away detection.

## Start the application automatically

Once the application package exists in a checkout:

```sh
PATH="$HOME/.local/bin:$PATH" gx10 enable-app /home/asus/auto-daybook
systemctl --user status daybook
journalctl --user -u daybook -f
```

`enable-app` installs the checkout's dependencies, checks the CLI, records its
path, and starts the service. It starts again at desktop login and restarts on
failure, with a limit of five starts in two minutes. After fixing a persistent
failure, run `systemctl --user reset-failed daybook` and
`systemctl --user start daybook`. The service uses `uv run --no-sync`, so recovery
doesn't depend on downloading packages.

Until there is an application checkout, the installed service stays inactive;
there is no dummy app or failing restart loop. The service uses the application's
own persisted settings and does not change its capture/consent behavior.

Tailscale has an enabled system service on the GX10. Model serving is managed
separately; this setup doesn't start a second model or restart its services.

## Verification scope

A reboot is not part of installation: other people may have long-running jobs.
Check automatic login after the next planned reboot. Manual lock and application
pause remain distinct from automatic idle locking; test their application behavior
once the capture loop exists.
