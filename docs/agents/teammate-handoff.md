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
~/.local/bin/gx10 run -- ~/.local/bin/gx10 status \
  --base-url http://127.0.0.1:18300/v1 --model qwen3.8-flash-next \
  --key-file ~/.config/qwen38/api-key
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
- The legacy `~/.local/bin/gx10 smoke --vision` default tested Ollama reading
  a code from a screenshot at setup time. **Do not run that default now:**
  Qwen3.8 is serving and Ollama must stay stopped. Use the current model check
  in [setup §6](../setup.md#6-the-model).

Both smoke tests passed on 2026-10-10. Reboot/autologin has not been exercised.
See [gx10-unattended.md](../gx10-unattended.md) for desktop mechanics and verification scope. Its installed checker retains the older Ollama defaults; use the explicit Qwen flags above and the current [model checks](../setup.md#6-the-model), not its bare status or vision examples.

For desktop commands launched over SSH:

```sh
source ~/.local/share/daybook-gx10/desktop-env.sh
```

## Share the machine

- Coding and ordinary tests can run concurrently. Only one real-screen capture or
  desktop test runs at a time; the smoke runner uses `/tmp/daybook-capture.lock`.
- Use replay/no-capture instances when developing without the physical desktop.
- Qwen3.8 Flash Next is serving through vLLM at localhost:18300. Ollama is
  deliberately stopped; never load another large model beside vLLM. Check
  `cd ~/gx10-model-plan && ./gx10.sh status` and follow the model runbook.
  Existing Daybook configs are not automatically migrated; [setup §6](../setup.md#6-the-model)
  explains selecting the profile and checking it without exposing the API key.
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
