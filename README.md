# dms-integration

A personal desktop bridge between [Dank Todo](https://github.com/deepu105/dms-dank-todo), a [Dank Material Shell](https://github.com/AvengeMedia/DankMaterialShell) plugin, and Google Tasks.

[Homepage](https://hutm.github.io/dms-integration/) · [Privacy policy](https://hutm.github.io/dms-integration/privacy.html) · [Terms of use](https://hutm.github.io/dms-integration/terms.html)

The sync runs locally as your desktop user. GitHub Pages hosts informational documents only. This is an independent personal integration, not an official Google or DMS service.

## Behavior

- Synchronizes task titles, completion/reopening, and intentional deletions in both directions.
- Polls every 30 seconds while the graphical session runs; it does not receive Google push notifications.
- Merges independent field edits. Concurrent title edits retain a Google conflict copy.
- Uses Google ETags and DMS compare-and-swap IPC to detect concurrent changes.
- Uses stable task markers to recover interrupted task creation without duplicating it.
- Backs up local task snapshots before applying changes.

Due dates, user notes, and ordering are not actively synchronized. A marker is added to the notes of tasks created by this integration. New desktop tasks are exported without Google nesting; local nesting is retained, and Google nesting is imported when a task is first downloaded. Existing tasks on both sides are combined on first sync, not matched by title. Intentional deletions propagate, so keep backups of important tasks.

## NixOS installation

Requires NixOS/nixpkgs with `programs.dms-shell.plugins` support, a running DMS desktop session, and Home Manager integrated as a NixOS module. The module installs and patches Dank Todo v1.2.0 declaratively; no `dms plugins install` step is needed.

Add this flake input:

```nix
inputs.dms-integration = {
  url = "github:hutm/dms-integration";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

Include `inputs.dms-integration.nixosModules.default` in your NixOS modules, alongside the existing Home Manager module. Configure the desktop user:

```nix
services.dankTodoGoogleTasks = {
  enable = true;
  user = "alice"; # your actual desktop account
};
```

Keep your existing DMS configuration enabled. Rebuild and reload DMS so the patched plugin loads. The activation helper enables the widget, and archives any CLI-installed copy that would override the declarative plugin. The sync service runs as your desktop user without sudo. Credentials must stay outside the Nix store.

For Niri, add this binding to your existing configuration:

```kdl
Super+Shift+F12 { spawn "dms" "ipc" "call" "widget" "toggle" "dankTodo"; }
```

The package alone can also be built with `nix build` or run with `nix run . -- status`. It still requires the patched, running DMS plugin. On non-Nix systems, apply `dank-todo-sync.patch` to the pinned Dank Todo version and run `python google-tasks-sync.py` in an environment installed from `requirements.txt`.

## Google authorization

1. Create/select your own Google Cloud project and enable **Google Tasks API**.
2. Configure **Google Auth Platform → Branding** with your app name, support email, and developer email. For personal use you can stay unverified, subject to Google's user cap and warning screen.
3. In **Data Access**, add only `https://www.googleapis.com/auth/tasks`.
4. In **Audience**, use External. For Testing, add your own account as a test user. Testing refresh tokens expire after seven days for this scope.
5. For ongoing personal use, switch to **In production**. The console may require a homepage, privacy policy, and authorized domain before enabling **Publish app**. The pages in `docs/` describe this deployment; a fork must update identity, contact, and hosting links to its own operator. See [branding setup](BRANDING.md).
6. Create a **Desktop app** OAuth client and download its JSON. Store it privately outside this repository:

```bash
install -d -m 700 ~/.config/dank-todo-google-tasks
install -m 600 /path/to/downloaded-client.json ~/.config/dank-todo-google-tasks/client.json
dank-todo-google-sync auth
dank-todo-google-sync sync
systemctl --user restart dank-todo-google-sync.timer
```

Sign in with the intended Google account. The authorization uses Google's browser flow, PKCE, and a temporary loopback callback. It requests offline Tasks access. The default list is **Dank Todo**, created if needed. Use `auth --list-id LIST_ID` when selecting an existing list for the first authorization. Reauthorization preserves the existing list mapping and verifies API access before replacing credentials.

After switching from Testing to In production, run `auth` again to obtain a new authorization. This removes the Testing-specific seven-day limit; tokens can still be revoked or expire for other reasons.

## Operations

```bash
dank-todo-google-sync status
dank-todo-google-sync sync
systemctl --user status dank-todo-google-sync.timer
journalctl --user -u dank-todo-google-sync.service -n 20
```

User state defaults to `~/.local/state/dank-todo-google-tasks/`. Credentials, settings, mappings, and local snapshots are private files. Do not delete mappings to repair routine errors: doing so can create duplicates. The status command reports connection-file presence and last sync metadata, not a live Google authentication check.

To disconnect:

```bash
systemctl --user stop dank-todo-google-sync.timer
```

Disable the NixOS integration and rebuild to keep it stopped after the next login. Revoke access in [Google Account connections](https://myaccount.google.com/connections). Revocation does not delete existing tasks or stored local files; remove those separately if desired. See [SECURITY.md](SECURITY.md) for credential handling.

## Development

```bash
python3 -B -m unittest discover -v
nix flake check
```

The offline suite covers creation, edits, deletion conflicts, interrupted operations, nesting, private file permissions, pagination, and conditional updates. It does not contact Google or read personal tasks. Live two-way synchronization was also checked on the original desktop deployment with temporary tasks.

The QML patch targets upstream commit `ff1b82c3061fce3c32c8f65792fa81b928ab35d1` (Dank Todo v1.2.0). Upstream DMS and Dank Todo retain their respective licenses. This repository distributes integration code and a patch rather than a copy of the plugin.
