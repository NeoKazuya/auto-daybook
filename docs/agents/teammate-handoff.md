# Teammate agent handoff

This is auto-daybook, a hackathon project on one shared ASUS GX10.

## Connect

Your human installs Tailscale and accepts the machine-share invitation emailed to
them. GitHub access to [NeoKazuya/auto-daybook](https://github.com/NeoKazuya/auto-daybook)
is separate.

```sh
ssh asus@1s6m.taild3a24b.ts.net
# Fallback address: 100.123.175.71
```

The shared account's login details are in [gx10.md](../gx10.md). Use the full
hostname for machines shared across tailnets.

Start your own persistent session and check the machine:

```sh
tmux new -s <your-name>
~/.local/bin/gx10 status
```

VS Code/Cursor Remote-SSH can use the same SSH address. Everyone shares the `asus`
Unix account; set your own `GH_TOKEN` in your agent session for GitHub operations.
See [setup.md](../setup.md#3-github-access).

## Start work

1. Read [CONTRIBUTING.md](../../CONTRIBUTING.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
   and [skills.md](../skills.md). These define the workflow, build plan, and contracts.
2. Read Tracker issue #1 and open PRs. Claim your assigned task without duplicating
   another agent's work.
3. Use your own branch and worktree, plus your own `DAYBOOK_HOME` and development
   port. Follow the repository workflow through verification and merge.
4. Check the current implementation before coding. Architecture sketches are design
   references, not a finished application.

## Already working on the GX10

The unattended setup was installed and merged in PR #3:

- Automatic sleep, locking, dimming, and screen blanking are disabled persistently.
- Automatic Xorg login and desktop startup are configured.
- xdotool, wmctrl, mpv, gh, uv, and native desktop test tools are installed.
- `~/.local/bin/gx10 smoke` tests focus, typing, clicking, fullscreen, capture, and Escape.
- `~/.local/bin/gx10 smoke --vision` also tests the local Ollama `qwen3.6:35b` model
  reading a code from a real screenshot.

Both smoke tests passed on 2026-10-10. Reboot/autologin has not been exercised.
See [gx10-unattended.md](../gx10-unattended.md) for commands and verification scope.

For desktop commands launched over SSH:

```sh
source ~/.local/share/daybook-gx10/desktop-env.sh
```

## Share the machine

- Coding and ordinary tests can run concurrently. Only one real-screen capture or
  desktop test runs at a time; the smoke runner uses `/tmp/daybook-capture.lock`.
- Use replay/no-capture instances when developing without the physical desktop.
- Only one large model should be loaded at a time. Check existing processes and
  `ollama ps` before loading one. The Qwen3.8 download was still running at setup;
  check its current state rather than restarting another agent's job.
- This is a dedicated hackathon machine. Keep setup practical; no temporary power
  profiles or settings-restoration machinery is needed.

## Application startup

The user service is installed. It was left inactive because the runnable app did
not exist at setup time. Once the app exists, the integration owner activates it:

```sh
~/.local/bin/gx10 enable-app /absolute/path/to/checkout
```

That installs dependencies and configures automatic startup and crash recovery.
Coordinate one main live instance; don't point the shared service at each agent's
individual development checkout.
