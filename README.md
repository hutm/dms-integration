# dms-integration

A personal desktop bridge between [Dank Todo](https://github.com/deepu105/dms-dank-todo), a [Dank Material Shell](https://github.com/AvengeMedia/DankMaterialShell) plugin, and Google Tasks.

[Homepage](https://hutm.github.io/dms-integration/) · [Privacy policy](https://hutm.github.io/dms-integration/privacy.html) · [Terms of use](https://hutm.github.io/dms-integration/terms.html)

The sync runs locally as your desktop user. GitHub Pages hosts informational documents only. This is an independent personal integration, not an official Google or DMS service.

## Behavior

- Synchronizes task titles, calendar dates, multiline notes, completion/reopening, intentional deletions, and parent/subtask relationships in both directions, including reparenting and promotion to the top level.
- Polls every 30 seconds while the graphical session runs; it does not receive Google push notifications.
- Merges independent field edits. Conflicting edits to the same title, date or notes keep both versions as ordinary tasks, with conflict details stored privately. Concurrent parent moves prefer the desktop choice and record both choices privately.
- Uses Google ETags and DMS compare-and-swap IPC to detect concurrent changes.
- Recovers a missing widget IPC after DMS startup without reloading an available widget during normal polls.
- Keeps identifiers and recovery metadata in private local state, without adding technical labels to task titles or notes.
- Shares one task model across display widgets so another monitor cannot overwrite newly added tasks with stale data.
- Backs up changed local task snapshots before network access, including offline runs, and before applying sync changes.

Dates have no time component or reminders; sibling ordering is not actively synchronized. Existing exact sync-marker lines and generated conflict-copy suffixes are removed automatically while preserving other user text. Existing flat exports acquire their DMS parent relationships without recreating the tasks. Google does not allow hidden completed tasks to be reparented; such moves are deferred until Google permits them. Existing tasks on both sides are combined on first sync, not matched by title. Intentional deletions propagate, so keep backups of important tasks.

## Multiple lists

Switch lists using the horizontally scrollable tabs at the top of the DMS popup.
The **+** button creates a separate list in DMS and Google Tasks. All connected
lists sync in the background, including inactive tabs. Active / All / Done and
Clear done apply only to the selected list. Save or cancel an open task editor
before switching tabs.

Rename lists in Google Tasks; their tab names update automatically. Duplicate
names remain separate lists. Deleting a Google list keeps its local tasks with
an unavailable message. Moving tasks between lists and deleting lists from DMS
are not implemented.

The first sync migrates storage and sync state to version 4, privately backing up
the previous mapping and local document. Existing tasks keep their original list
and Google identifiers. Deploy the plugin and sync client together; older clients
refuse the migrated state.

## Dates and notes

Click a task’s pencil to edit its title, date, and multiline notes. Choose a date
from Calendar or enter YYYY-MM-DD. Clear removes the date. Save changes applies
the edit; Escape cancels. Dates and a Notes indicator appear under task titles.
Notes allow up to 8192 characters. Existing Google dates and notes are imported
on upgrade; missing fields in old local task files do not erase Google metadata.
An open editor preserves incoming changes to untouched fields and blocks an
overwrite if the same field has changed differently elsewhere.

The fork is maintained as ordinary source files under `plugin/`, with its pinned
upstream base and MIT license recorded there. See `plugin/UPSTREAM.md`.

## NixOS installation

Requires NixOS/nixpkgs with `programs.dms-shell.plugins` support, a running DMS desktop session, and Home Manager integrated as a NixOS module. The module installs the maintained Dank Todo v1.4.0 fork under `plugin/` declaratively; no `dms plugins install` step is needed.

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

Keep your existing DMS configuration enabled. Rebuild and reload DMS so the maintained plugin loads. The activation helper enables the widget, and archives any CLI-installed copy that would override the declarative plugin. The sync service runs as your desktop user without sudo. Credentials must stay outside the Nix store.

For Niri, add this binding to your existing configuration:

```kdl
Super+Shift+F12 { spawn "dms" "ipc" "call" "widget" "toggle" "dankTodo"; }
```

The package alone can also be built with `nix build` or run with `nix run . -- status`. It still requires the maintained, running DMS plugin. On non-Nix systems, install the `plugin/` directory as your Dank Todo plugin and run `python google-tasks-sync.py` in an environment installed from `requirements.txt`.

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

Sign in with the intended Google account. The authorization uses Google's browser flow, PKCE, and a temporary loopback callback. It requests offline Tasks access. The original list is **Dank Todo**, created if needed. All Google Tasks lists are then discovered and synchronized; each appears as a separate DMS tab. Use `auth --list-id LIST_ID` when selecting an existing list for the first authorization. Reauthorization preserves the existing list mapping and verifies API access before replacing credentials.

After switching from Testing to In production, run `auth` again to obtain a new authorization. This removes the Testing-specific seven-day limit; tokens can still be revoked or expire for other reasons.

## Operations

```bash
dank-todo-google-sync status
dank-todo-google-sync sync
systemctl --user status dank-todo-google-sync.timer
journalctl --user -u dank-todo-google-sync.service -n 20
```

User state defaults to `~/.local/state/dank-todo-google-tasks/`. Credentials, settings, mappings, and local snapshots are private files. Do not delete mappings to repair routine errors: doing so can create duplicates. The status command reports connection-file presence and last sync metadata, not a live Google authentication check.

### Interrupted task creation

Google Tasks has no private application metadata or client-assigned IDs. Without visible note markers, a lost creation response cannot be safely retried automatically. The client checkpoints intent before each creation and pauses sync if its outcome is unknown. Ordinary successful creations require no intervention; definitive HTTP rejections clear the intent for retry.

```bash
dank-todo-google-sync pending
# After reviewing the matching task in Google Tasks:
dank-todo-google-sync resolve-create --local-id LOCAL_ID --remote-id GOOGLE_ID
# Or, only after confirming that no task was created:
dank-todo-google-sync resolve-create --local-id LOCAL_ID --not-created
dank-todo-google-sync sync
```

The pending command reports identifiers and candidate identifiers for tasks and lists, without printing task text. For another list, include `--local-list-id LOCAL_LIST_ID` with `resolve-create`. For interrupted list creation use `resolve-list --local-list-id LOCAL_LIST_ID --remote-list-id GOOGLE_LIST_ID`, or `--not-created` only after confirming no list was created. It never guesses based on titles alone. Only bind a task after checking it in Google Tasks.

The first run migrates synchronization state to version 4 and saves private backups of the previous version and local document. Older clients refuse this state format. Rollbacks require care to avoid restoring stale task mappings; keep the upgraded client installed with its state.

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

The maintained plugin fork is based on upstream commit `ff1b82c3061fce3c32c8f65792fa81b928ab35d1` (Dank Todo v1.2.0). Its original MIT license is retained in `plugin/LICENSE`; provenance and maintenance notes are in `plugin/UPSTREAM.md`. DMS retains its own license.
