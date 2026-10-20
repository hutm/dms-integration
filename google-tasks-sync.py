"""Two-way Dank Todo / Google Tasks sync. OAuth credentials stay in user state."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

SCOPE = "https://www.googleapis.com/auth/tasks"
API_URL = "https://tasks.googleapis.com/tasks/v1/"


class SyncError(Exception):
    pass


class RetrySync(SyncError):
    pass


class RequestRejected(SyncError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text())


def local_fields(row):
    due = row.get("dueDate") or ""
    if due:
        try:
            if date.fromisoformat(due).isoformat() != due:
                raise ValueError()
        except (ValueError, TypeError) as error:
            raise SyncError("A task date must be a valid YYYY-MM-DD date") from error
    notes = row.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > 8192:
        raise SyncError("Task notes must contain at most 8192 characters")
    return {"text": row["text"], "completed": bool(row.get("completed")),
            "dueDate": due, "notes": notes}


def remote_fields(task):
    return local_fields({"text": task.get("title", ""),
                         "completed": task.get("status") == "completed",
                         "dueDate": (task.get("due") or "")[:10],
                         "notes": task.get("notes", "")})


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def marker(instance, local_id):
    return f"[dank-todo-sync:{instance}:{local_id}]"


def validate_tree(parents):
    for identifier in parents:
        seen = {identifier}
        parent = parents[identifier]
        while parent in parents:
            if parent in seen:
                raise SyncError("Task hierarchy contains a cycle; resolve it before syncing")
            seen.add(parent)
            parent = parents[parent]


def private_conflict(state, local_id, kind, **details):
    # Keep diagnostic metadata out of task titles and Google notes.
    state.setdefault("conflicts", {})[local_id + ":" + kind] = {
        "kind": kind, "detected_at": now(), **details,
    }


def import_row(local_id, task):
    row = {"id": local_id, **remote_fields(task), "parentId": None, "createdAt": now()}
    if row["completed"]:
        row["completedAt"] = task.get("completed", now())
    return row


class GoogleTasks:
    def __init__(self, credentials):
        from google.auth.transport.requests import AuthorizedSession
        self.session = AuthorizedSession(credentials)
        self.list_id = None

    def request(self, method, path, *, body=None, params=None, etag=None):
        # Never automatically retry mutations: a lost POST response may still
        # have created a task. Durable creation intents require reconciliation.
        response = self.session.request(
            method, API_URL + path, json=body, params=params,
            headers={"If-Match": etag} if etag else {}, timeout=25,
        )
        if response.status_code in (409, 412):
            raise RetrySync("Google task changed during sync; retrying on the next run")
        if response.status_code >= 400:
            # Don't log HTTP bodies, credentials, or task contents to journald.
            error = RequestRejected if response.status_code < 500 else SyncError
            raise error(f"Google Tasks returned HTTP {response.status_code} for {method}")
        return response.json() if response.content else {}

    def pages(self, path, **params):
        items = []
        while True:
            page = self.request("GET", path, params=params)
            items.extend(page.get("items", []))
            if not page.get("nextPageToken"):
                return items
            params["pageToken"] = page["nextPageToken"]

    def select_list(self, list_id, title):
        if list_id:
            self.request("GET", "users/@me/lists/" + quote(list_id, safe=""))
            self.list_id = list_id
            return list_id
        matches = [item for item in self.pages("users/@me/lists", maxResults=100) if item["title"] == title]
        if len(matches) > 1:
            raise SyncError("Multiple matching Google lists; select one with auth --list-id")
        selected = matches[0] if matches else self.request("POST", "users/@me/lists", body={"title": title})
        self.list_id = selected["id"]
        return self.list_id

    def path(self, task_id=None):
        result = "lists/" + quote(self.list_id, safe="") + "/tasks"
        return result + "/" + quote(task_id, safe="") if task_id else result

    def list_tasks(self):
        return self.pages(self.path(), maxResults=100, showCompleted="true", showHidden="true", showDeleted="true")

    def create(self, fields):
        body = {
            "title": fields["text"],
            "status": "completed" if fields["completed"] else "needsAction",
        }
        if fields.get("notes"):
            body["notes"] = fields["notes"]
        if fields.get("dueDate"):
            body["due"] = fields["dueDate"] + "T00:00:00.000Z"
        return self.request("POST", self.path(), body=body)

    def move(self, task, parent_id):
        # Creating/moving siblings can invalidate other ETags. Refresh without
        # losing an intervening parent edit, and condition the move on this read.
        current = self.request("GET", self.path(task["id"]))
        if current.get("parent") != task.get("parent"):
            raise RetrySync("Google task hierarchy changed during sync")
        return self.request(
            "POST", self.path(task["id"]) + "/move",
            params={"parent": parent_id} if parent_id else {},
            etag=current.get("etag"),
        )

    def remove_marker(self, task, sync_marker):
        notes = task.get("notes", "")
        cleaned = "".join(line for line in notes.splitlines(keepends=True)
                          if line.rstrip("\r\n") != sync_marker)
        if cleaned == notes:
            return task
        return self.request("PATCH", self.path(task["id"]),
                            body={"notes": cleaned}, etag=task.get("etag"))

    def update(self, task, fields):
        body = {}
        if fields["text"] != task.get("title", ""):
            body["title"] = fields["text"]
        if fields["completed"] != (task.get("status") == "completed"):
            body["status"] = "completed" if fields["completed"] else "needsAction"
            if not fields["completed"]:
                body["completed"] = None
        if "notes" in fields and fields["notes"] != task.get("notes", ""):
            body["notes"] = fields["notes"]
        if "dueDate" in fields and fields["dueDate"] != (task.get("due") or "")[:10]:
            body["due"] = fields["dueDate"] + "T00:00:00.000Z" if fields["dueDate"] else None
        return self.request("PATCH", self.path(task["id"]), body=body, etag=task.get("etag")) if body else task

    def delete(self, task):
        self.request("DELETE", self.path(task["id"]), etag=task.get("etag"))


class DmsTodos:
    @staticmethod
    def ipc(method, *args):
        result = subprocess.run(
            ["dms", "ipc", "call", "dankTodo", method, *args],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip() == "Target not found.":
            # DMS can discover its system plugin before the user override,
            # unload the system widget, then leave a stale "loaded" flag.
            # Rescan only when IPC is absent; never interrupt an open editor
            # during normal polls. This also repairs startup after a crash.
            subprocess.run(
                ["dms", "ipc", "call", "plugin-scan", "rescan", "dankTodo"],
                capture_output=True, text=True, timeout=15, check=True,
            )
            raise RetrySync("Dank Todo was unavailable; refreshed its plugin and will retry")
        if result.returncode:
            raise SyncError("Dank Todo sync IPC unavailable; enable the Nix-managed Dank Todo plugin in DMS")
        return result.stdout.strip()

    def snapshot(self):
        try:
            snapshot = json.loads(self.ipc("syncSnapshot"))
        except json.JSONDecodeError as error:
            raise SyncError("Dank Todo sync IPC returned invalid data; reload its plugin") from error
        if "error" in snapshot:
            raise RetrySync("Dank Todo is still loading")
        if snapshot.get("schemaVersion", 0) < 3:
            raise SyncError("Reload the updated Dank Todo plugin before syncing dates and notes")
        return snapshot

    def apply(self, snapshot, rows, state_dir):
        # A file avoids Linux's per-argument size limit for larger lists.
        with tempfile.TemporaryDirectory(prefix="apply-", dir=state_dir) as temporary:
            payload = Path(temporary) / "todos.json"
            atomic_json(payload, rows)
            result = self.ipc("syncApply", snapshot["token"], str(payload))
        if result == "CONFLICT":
            raise RetrySync("Local tasks changed during sync; retrying on the next run")
        if result != "OK":
            raise SyncError("Dank Todo could not apply the synchronized tasks: " + result)


def create_mapped(api, state, checkpoint, tasks, local_id, fields):
    # Tasks has no private metadata or client-assigned IDs. Write intent before
    # POST and stop for explicit reconciliation if its outcome becomes unknown.
    # Retrying an uncertain POST would risk duplicates without visible markers.
    entries = state.setdefault("entries", {})
    pending = state.setdefault("pending_creations", {})
    if local_id in pending:
        raise SyncError("A Google task creation needs review; run dank-todo-google-sync pending")
    pending[local_id] = {
        "fields": fields, "before_ids": list(tasks), "started_at": now(),
        "previous_remote_id": entries.get(local_id, {}).get("remote_id"),
    }
    checkpoint(state)
    try:
        task = api.create(fields)
    except (RequestRejected, RetrySync):
        # A definitive rejection did not create a task. Transport failures and
        # server errors remain uncertain and keep their intent for review.
        del pending[local_id]
        checkpoint(state)
        raise
    tasks[task["id"]] = task
    entry = entries.setdefault(local_id, {"base": fields})
    entry["remote_id"] = task["id"]
    checkpoint(state)
    del pending[local_id]
    checkpoint(state)
    return task


def check_pending(state, checkpoint):
    for local_id, pending in list(state.get("pending_creations", {}).items()):
        remote_id = state.get("entries", {}).get(local_id, {}).get("remote_id")
        if remote_id and remote_id != pending.get("previous_remote_id"):
            # Mapping was durably written before the final intent cleanup.
            del state["pending_creations"][local_id]
            checkpoint(state)
        else:
            raise SyncError("A Google task creation needs review; run dank-todo-google-sync pending")


def pending_report(api, state):
    tasks = api.list_tasks()
    return [{
        "local_id": local_id, "started_at": pending["started_at"],
        "candidate_remote_ids": [task["id"] for task in tasks
                                 if task["id"] not in pending["before_ids"]
                                 and not task.get("deleted")
                                 and remote_fields(task) == local_fields(pending["fields"])],
    } for local_id, pending in state.get("pending_creations", {}).items()]


def resolve_creation(api, state, checkpoint, local_id, remote_id=None):
    pending = state.get("pending_creations", {}).get(local_id)
    if not pending:
        raise SyncError("No pending creation for that local identifier")
    if remote_id:
        if remote_id in pending["before_ids"] or any(
                entry["remote_id"] == remote_id for entry in state.get("entries", {}).values()):
            raise SyncError("That Google task already existed or is mapped to another task")
        task = next((task for task in api.list_tasks() if task["id"] == remote_id), None)
        if not task or task.get("deleted") or remote_fields(task) != local_fields(pending["fields"]):
            raise SyncError("The selected Google task does not match the pending creation")
        entry = state.setdefault("entries", {}).setdefault(local_id, {"base": pending["fields"]})
        entry["remote_id"] = remote_id
        checkpoint(state)
    del state["pending_creations"][local_id]
    checkpoint(state)


def sync(api, local, state, checkpoint, state_dir):
    """Merge against the last successful common version; preserve conflicts."""
    snapshot = local.snapshot()
    storage = snapshot["storageFilePath"]
    if state.get("storage") not in (None, storage):
        raise SyncError("Dank Todo storage location changed; restore it before syncing")
    # Capture local additions even when no Google changes need applying or the
    # network fails. Previously backups only covered sync writes to the widget.
    latest_snapshot = state_dir / "last-local-snapshot.json"
    if read_json(latest_snapshot, {}).get("todos") != snapshot["todos"]:
        atomic_json(state_dir / "backups" / (str(time.time_ns()) + "-local.json"),
                    {"version": 2, "todos": snapshot["todos"]})
        atomic_json(latest_snapshot, snapshot)
    state["storage"] = storage
    entries = state.setdefault("entries", {})
    rows = copy.deepcopy(snapshot["todos"])
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise SyncError("Duplicate local task identifiers")
    for identifier, saved in state.get("pending_conflict_copies", {}).items():
        if identifier not in by_id:
            row = copy.deepcopy(saved)
            rows.append(row)
            by_id[identifier] = row
    for row in rows:
        row.update(local_fields(row))
    validate_tree({row["id"]: row.get("parentId") for row in rows})
    check_pending(state, checkpoint)
    tasks = {task["id"]: task for task in api.list_tasks()}
    instance = state["instance"]

    # Recover legacy marked creations before removing their visible markers.
    for local_id, row in list(by_id.items()):
        if local_id in entries or row.get("deletedAt"):
            continue
        reference = marker(instance, local_id)
        matches = [task for task in tasks.values() if not task.get("deleted") and reference in task.get("notes", "").splitlines()]
        if len(matches) > 1:
            raise SyncError("Duplicate sync references in Google Tasks; resolve before syncing")
        if matches:
            task = matches[0]
            entries[local_id] = {"remote_id": task["id"], "base": local_fields(row)}
            checkpoint(state)
        else:
            create_mapped(api, state, checkpoint, tasks, local_id, local_fields(row))

    reverse = {entry["remote_id"]: local_id for local_id, entry in entries.items()}
    for task in sorted(tasks.values(), key=lambda item: item.get("position", "")):
        if task.get("deleted") or task["id"] in reverse:
            continue
        local_id = stable_id("google-", state["list_id"] + ":" + task["id"])
        entries[local_id] = {"remote_id": task["id"], "base": remote_fields(task)}
        reverse[task["id"]] = local_id
        checkpoint(state)

    next_bases = {}
    deletions = []
    conflicts = 0
    for local_id, entry in entries.items():
        task = tasks.get(entry["remote_id"])
        if task and not task.get("deleted"):
            tasks[task["id"]] = api.remove_marker(task, marker(instance, local_id))
    # Only old generated conflict rows qualify for this title migration.
    # Unrelated user titles and notes are never cleaned by a broad regex.
    suffix = " (Google conflict copy)"
    for row in rows:
        if re.fullmatch(r"conflict-[0-9a-f]{24}", row["id"]) and row["text"].endswith(suffix):
            row["text"] = row["text"][:-len(suffix)]
    for local_id, entry in list(entries.items()):
        row = by_id.get(local_id)
        task = tasks.get(entry["remote_id"])
        remote_deleted = task is None or task.get("deleted", False)
        # Old versions did not synchronize dates or notes. Empty defaults mean
        # existing Google metadata is imported rather than erased on upgrade.
        base = local_fields(entry["base"])
        if row is None:
            # Missing records are never interpreted as a request to delete.
            # Dank Todo expresses intentional deletions with deletedAt.
            if remote_deleted:
                continue
            row = import_row(local_id, task)
            row["parentId"] = reverse.get(task.get("parent"))
            rows.append(row)
            by_id[local_id] = row
        current = local_fields(row)
        local_deleted = bool(row.get("deletedAt"))
        if local_deleted and remote_deleted:
            next_bases[local_id] = base
            continue
        if local_deleted:
            if (remote_fields(task) != base
                    or reverse.get(task.get("parent")) != entry.get("parent_base")):
                # A phone edit concurrent with deletion is kept, not discarded.
                row.pop("deletedAt", None)
                row.update(remote_fields(task))
                conflicts += 1
            else:
                deletions.append(task)
                next_bases[local_id] = base
                continue
        elif remote_deleted:
            if current != base or row.get("parentId") != entry.get("parent_base"):
                # Keep a desktop edit concurrent with phone deletion.
                task = create_mapped(api, state, checkpoint, tasks, local_id, current)
                reverse[task["id"]] = local_id
                conflicts += 1
            else:
                row["deletedAt"] = now()
                next_bases[local_id] = base
                continue
        else:
            remote = remote_fields(task)
            merged = {}
            collision = False
            for field in ("text", "completed", "dueDate", "notes"):
                local_changed = current[field] != base[field]
                remote_changed = remote[field] != base[field]
                collision |= local_changed and remote_changed and current[field] != remote[field]
                merged[field] = current[field] if local_changed else remote[field]
            if collision:
                # Deterministic IDs avoid repeated copies if the local compare
                # and swap loses a race and the sync is retried.
                conflict_id = stable_id("conflict-", entry["remote_id"] + json.dumps(remote, sort_keys=True))
                if conflict_id not in by_id:
                    conflict = import_row(conflict_id, task)
                    conflict["parentId"] = reverse.get(task.get("parent"))
                    rows.append(conflict)
                    by_id[conflict_id] = conflict
                    private_conflict(state, conflict_id, "fields", source=local_id)
                    # Preserve Google's alternative before overwriting it.
                    # A failed local CAS must not lose that version on retry.
                    state.setdefault("pending_conflict_copies", {})[conflict_id] = copy.deepcopy(conflict)
                    checkpoint(state)
                conflicts += 1
            task = api.update(task, merged)
            tasks[task["id"]] = task
            row.update(merged)
        if row.get("completed"):
            row["completedAt"] = task.get("completed", row.get("completedAt", now()))
        else:
            row.pop("completedAt", None)
        next_bases[local_id] = local_fields(row)

    parents = {}
    for row in rows:
        if row.get("deletedAt"):
            continue
        entry = entries.get(row["id"])
        task = tasks.get(entry["remote_id"]) if entry else None
        local_parent = row.get("parentId") if row.get("parentId") in by_id else None
        if not task or task.get("deleted"):
            parents[row["id"]] = local_parent
            continue
        remote_parent = reverse.get(task.get("parent"))
        base_parent = entry.get("parent_base")
        local_changed = local_parent != base_parent
        remote_changed = remote_parent != base_parent
        parents[row["id"]] = local_parent if local_changed else remote_parent
        if local_changed and remote_changed and local_parent != remote_parent:
            conflicts += 1
            private_conflict(state, row["id"], "parent", local_parent=local_parent,
                             remote_parent=remote_parent)
            checkpoint(state)
    validate_tree(parents)

    # Delete children first. If the server changes between read and mutation,
    # If-Match aborts the run instead of overwriting the intervening edit.
    def remote_depth(task):
        depth, seen = 0, set()
        while task.get("parent") in tasks and task["parent"] not in seen:
            seen.add(task["parent"])
            task = tasks[task["parent"]]
            depth += 1
        return depth

    # Google may cascade parent deletion. Preserve an edited child by keeping
    # its ancestors too, rather than losing the child through that cascade.
    delete_ids = {task["id"] for task in deletions}
    protected = set()
    for local_id, parent in parents.items():
        seen = set()
        while parent in by_id and parent not in seen:
            seen.add(parent)
            if parent in entries:
                protected.add(entries[parent]["remote_id"])
            parent = parents.get(parent, by_id[parent].get("parentId"))
    for remote_id in delete_ids & protected:
        local_id = reverse[remote_id]
        row = by_id[local_id]
        row.pop("deletedAt", None)
        row.update(remote_fields(tasks[remote_id]))
        next_bases[local_id] = local_fields(row)
        parents[local_id] = row.get("parentId")
        conflicts += 1
    for local_id, parent in list(parents.items()):
        if parent not in by_id or by_id[parent].get("deletedAt"):
            parents[local_id] = None
        by_id[local_id]["parentId"] = parents[local_id]
    validate_tree(parents)

    moves = []
    deferred = set()
    for local_id, parent in parents.items():
        entry = entries.get(local_id)
        if not entry:
            continue  # A new conflict copy is exported on the next run.
        task = tasks[entry["remote_id"]]
        if task.get("deleted"):
            continue
        parent_id = entries[parent]["remote_id"] if parent in entries else None
        if parent and parent not in entries:
            deferred.add(local_id)
            continue
        if task.get("parent") == parent_id:
            continue
        if task.get("hidden") or (parent_id and tasks[parent_id].get("hidden")):
            deferred.add(local_id)
            continue  # Google refuses to reparent hidden completed tasks.
        moves.append((local_id, parent_id))

    # Detach first so reversing a parent/child relationship does not create a
    # temporary cycle on Google. Attach only after the desired parents exist.
    for local_id, _ in moves:
        remote_id = entries[local_id]["remote_id"]
        if tasks[remote_id].get("parent"):
            tasks[remote_id] = api.move(tasks[remote_id], None)
    def depth(local_id):
        count = 0
        while parents.get(local_id):
            count += 1
            local_id = parents[local_id]
        return count
    for local_id, parent_id in sorted(moves, key=lambda move: depth(move[0])):
        remote_id = entries[local_id]["remote_id"]
        if parent_id:
            tasks[remote_id] = api.move(tasks[remote_id], parent_id)
    for task in sorted(deletions, key=remote_depth, reverse=True):
        if task["id"] not in protected:
            api.delete(task)

    # Keep existing sibling order and make imported children contiguous.
    ordered, visited = [], set()
    def append_tree(row):
        if row["id"] in visited:
            return
        visited.add(row["id"])
        ordered.append(row)
        for child in rows:
            if child.get("parentId") == row["id"]:
                append_tree(child)
    for row in rows:
        parent = by_id.get(row.get("parentId"))
        if not parent or parent.get("deletedAt"):
            row["parentId"] = None
            append_tree(row)
    for row in rows:
        append_tree(row)

    if ordered != snapshot["todos"]:
        backup = state_dir / "backups" / (str(time.time_ns()) + ".json")
        atomic_json(backup, {"version": 2, "todos": snapshot["todos"]})
        local.apply(snapshot, ordered, state_dir)
    else:
        # Still verify the snapshot: a local edit may have happened during HTTP.
        if local.snapshot()["token"] != snapshot["token"]:
            raise RetrySync("Local tasks changed during sync")
    for local_id, base in next_bases.items():
        entries[local_id]["base"] = base
        if local_id in parents and local_id not in deferred:
            entries[local_id]["parent_base"] = parents[local_id]
    for identifier in list(state.get("pending_conflict_copies", {})):
        if identifier in by_id:
            del state["pending_conflict_copies"][identifier]
    state["last_sync"] = now()
    checkpoint(state)
    return {"tasks": sum(not row.get("deletedAt") for row in ordered),
            "conflicts_preserved": conflicts, "hierarchy_deferred": len(deferred)}


def authenticate(args, directory):
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not args.client_secrets.is_file():
        raise SyncError(f"Desktop OAuth client JSON not found at {args.client_secrets}")
    client = read_json(args.client_secrets)
    if not isinstance(client, dict) or "installed" not in client:
        raise SyncError("Choose a Google OAuth client of type Desktop app")
    old = read_json(directory / "settings.json", {})
    if old and args.list_id and old.get("list_id") != args.list_id:
        raise SyncError("Changing lists requires a separate --state-dir to preserve the existing mapping")
    flow = InstalledAppFlow.from_client_config(client, [SCOPE], autogenerate_code_verifier=True)
    credentials = flow.run_local_server(
        host="127.0.0.1", port=0, open_browser=not args.no_browser,
        access_type="offline", prompt="consent", timeout_seconds=300,
        success_message="Dank Todo is connected. You can close this tab.",
    )
    if not credentials.refresh_token:
        raise SyncError("Google did not return an offline refresh token; authorize again")
    # Verify API access before replacing working credentials. A reauthorization
    # must not silently switch the existing mapping to another account/list.
    api = GoogleTasks(credentials)
    list_id = api.select_list(old.get("list_id") or args.list_id, args.list_name)
    atomic_json(directory / "token.json", json.loads(credentials.to_json()))
    atomic_json(directory / "settings.json", {"list_id": list_id, "list_name": args.list_name})
    print("Google Tasks connected. Run: dank-todo-google-sync sync")


def main():
    default_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dank-todo-google-tasks"
    default_client = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dank-todo-google-tasks/client.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=default_state)
    subparsers = parser.add_subparsers(dest="command", required=True)
    auth = subparsers.add_parser(
        "auth", help="Connect your Google account using a Desktop OAuth client",
        epilog="Enable Google Tasks API in Google Cloud, configure Google Auth Platform, "
        "add your account as a test user if using Testing, and download a Desktop app client JSON. "
        "Testing refresh tokens expire after 7 days; use In production for ongoing personal use. "
        "Keep the downloaded file outside this repository. Only Tasks access is requested.",
    )
    auth.add_argument("--client-secrets", default=default_client, type=Path, help="Desktop OAuth client JSON (default: %(default)s)")
    auth.add_argument("--list-name", default="Dank Todo")
    auth.add_argument("--list-id", help="Use an existing Google Tasks list instead")
    auth.add_argument("--no-browser", action="store_true", help="Print the URL for manual opening on this computer")
    subparsers.add_parser(
        "sync", help="Perform one two-way synchronization",
        description="Synchronize titles, dates, notes, completion and deletions with the selected Google list. "
        "Synchronize parent/subtask relationships in both directions. Concurrent text edits "
        "retain both versions without adding labels. Dates are calendar dates without times. "
        "Sibling ordering is left unchanged. Legacy sync markers are removed from Google notes.",
    )
    subparsers.add_parser("status", help="Show connection and last-sync status without task contents")
    subparsers.add_parser("pending", help="Show identifiers for interrupted task creations needing review")
    resolve = subparsers.add_parser("resolve-create", help="Reconcile an interrupted Google task creation")
    resolve.add_argument("--local-id", required=True)
    resolution = resolve.add_mutually_exclusive_group(required=True)
    resolution.add_argument("--remote-id", help="Bind to the matching Google task after reviewing it")
    resolution.add_argument("--not-created", action="store_true",
                            help="Confirm no task was created; allow creation on the next sync")
    args = parser.parse_args()
    directory = args.state_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    with (directory / "sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "auth":
            authenticate(args, directory)
            return
        state = read_json(directory / "state.json", {})
        if args.command == "status":
            print(json.dumps({"connected": (directory / "token.json").exists(),
                              "last_sync": state.get("last_sync"),
                              "mapped_tasks": len(state.get("entries", {})),
                              "pending_creations": len(state.get("pending_creations", {}))}, indent=2))
            return
        if not (directory / "token.json").exists():
            raise SyncError("Connect first: dank-todo-google-sync auth")
        from google.oauth2.credentials import Credentials
        credentials = Credentials.from_authorized_user_file(str(directory / "token.json"), [SCOPE])
        settings = read_json(directory / "settings.json", {})
        if not settings.get("list_id"):
            raise SyncError("Missing list settings; run auth again")
        if state.get("version", 1) not in (1, 2, 3):
            raise SyncError("Unsupported sync state version")
        if state.get("list_id", settings["list_id"]) != settings["list_id"]:
            raise SyncError("Google list changed; refusing to reuse another list's task mapping")
        if state.get("version", 1) != 3:
            atomic_json(directory / "backups" / (str(time.time_ns()) +
                        "-state-v" + str(state.get("version", 1)) + ".json"), state)
        # Old clients would discard date/note fields when applying local data.
        state["version"] = 3
        state.setdefault("instance", uuid.uuid4().hex)
        state["list_id"] = settings["list_id"]
        checkpoint = lambda data: atomic_json(directory / "state.json", data)
        # Persist the instance before any remote create, including empty lists.
        checkpoint(state)
        api = GoogleTasks(credentials)
        api.list_id = settings["list_id"]
        try:
            if args.command == "pending":
                print(json.dumps(pending_report(api, state), indent=2))
            elif args.command == "resolve-create":
                resolve_creation(api, state, checkpoint, args.local_id, args.remote_id)
                print("Creation reconciled. Run: dank-todo-google-sync sync")
            else:
                print(json.dumps(sync(api, DmsTodos(), state, checkpoint, directory)))
        finally:
            atomic_json(directory / "token.json", json.loads(credentials.to_json()))


if __name__ == "__main__":
    try:
        main()
    except RetrySync as error:
        print(str(error), file=sys.stderr)
    except (SyncError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Google/requests exceptions may include responses with credentials.
        # Keep those out of the journal; reauthorization fixes expired grants.
        print(f"Google sync failed ({type(error).__name__}); check connectivity or run auth again", file=sys.stderr)
        sys.exit(1)
