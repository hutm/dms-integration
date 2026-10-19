"""Offline regression checks for two-way sync and interrupted operations."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("google_tasks_sync", Path(__file__).with_name("google-tasks-sync.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def row(identifier, text="Task", **kwargs):
    return {"id": identifier, "text": text, "completed": False, "parentId": None, "createdAt": "2026-10-17T00:00:00Z", **kwargs}


def task(identifier, text="Task", **kwargs):
    return {"id": identifier, "title": text, "status": "needsAction", "etag": identifier + "-1", **kwargs}


class MemoryTodos:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.race = False

    def snapshot(self):
        return {"token": json.dumps(self.rows, sort_keys=True), "storageFilePath": "/todos.json", "todos": copy.deepcopy(self.rows)}

    def apply(self, snapshot, rows, directory):
        if self.race:
            raise sync.RetrySync("Concurrent local edit")
        if snapshot["token"] != self.snapshot()["token"]:
            raise sync.RetrySync("Concurrent local edit")
        self.rows = copy.deepcopy(rows)


class MemoryGoogle:
    def __init__(self, tasks=()):
        self.tasks = {item["id"]: copy.deepcopy(item) for item in tasks}
        self.created = 0
        self.updated = []
        self.lost_create_reply = False

    def list_tasks(self):
        return copy.deepcopy(list(self.tasks.values()))

    def create(self, fields, reference):
        self.created += 1
        item = task("new-" + str(self.created), fields["text"], notes=reference, status="completed" if fields["completed"] else "needsAction")
        self.tasks[item["id"]] = copy.deepcopy(item)
        if self.lost_create_reply:
            self.lost_create_reply = False
            raise sync.SyncError("Response lost after successful POST")
        return item

    def update(self, item, fields):
        target = self.tasks[item["id"]]
        target["title"] = fields["text"]
        target["status"] = "completed" if fields["completed"] else "needsAction"
        self.updated.append(item["id"])
        return copy.deepcopy(target)

    def delete(self, item):
        self.tasks[item["id"]]["deleted"] = True
        for child in list(self.tasks.values()):
            if child.get("parent") == item["id"]:
                self.delete(child)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state = {"version": 1, "instance": "test", "list_id": "list", "entries": {}}
        self.saved = copy.deepcopy(self.state)

    def checkpoint(self, state):
        self.saved = copy.deepcopy(state)

    def run_sync(self, local, google):
        return sync.sync(google, local, self.state, self.checkpoint, self.directory)

    def mapped(self, local_row=None, remote_task=None):
        local = MemoryTodos([local_row or row("local")])
        google = MemoryGoogle([remote_task or task("remote")])
        self.state["entries"]["local"] = {"remote_id": "remote", "base": {"text": "Task", "completed": False}}
        return local, google

    def test_first_sync_exports_and_imports_without_duplicate_tasks(self):
        local = MemoryTodos([row("local", "Desktop"), row("deleted", deletedAt="yesterday")])
        google = MemoryGoogle([task("remote", "Phone")])
        self.run_sync(local, google)
        self.assertEqual({item["text"] for item in local.rows if not item.get("deletedAt")}, {"Desktop", "Phone"})
        self.run_sync(local, google)
        self.assertEqual(google.created, 1)
        self.assertEqual(len(self.state["entries"]), 2)

    def test_local_edit_and_google_completion_merge(self):
        local, google = self.mapped(row("local", "Edited on desktop"), task("remote", status="completed", notes="Keep these notes", due="2026-10-20T00:00:00Z"))
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["text"], "Edited on desktop")
        self.assertTrue(local.rows[0]["completed"])
        self.assertEqual(google.tasks["remote"]["notes"], "Keep these notes")
        self.assertEqual(google.tasks["remote"]["due"], "2026-10-20T00:00:00Z")

    def test_same_field_conflict_preserves_both_titles(self):
        local, google = self.mapped(row("local", "Desktop edit"), task("remote", "Phone edit"))
        result = self.run_sync(local, google)
        self.assertEqual(result["conflicts_preserved"], 1)
        self.assertEqual({item["text"] for item in local.rows}, {"Desktop edit", "Phone edit (Google conflict copy)"})
        self.run_sync(local, google)
        self.assertEqual(google.created, 1)

    def test_local_delete_propagates(self):
        local, google = self.mapped(row("local", deletedAt="yesterday"))
        self.run_sync(local, google)
        self.assertTrue(google.tasks["remote"]["deleted"])
        self.run_sync(local, google)
        self.assertTrue(local.rows[0]["deletedAt"])

    def test_google_delete_propagates(self):
        local, google = self.mapped(remote_task=task("remote", deleted=True))
        self.run_sync(local, google)
        self.assertIn("deletedAt", local.rows[0])

    def test_delete_edit_conflicts_keep_edit_from_either_side(self):
        local, google = self.mapped(row("local", deletedAt="yesterday"), task("remote", "Phone edit"))
        self.run_sync(local, google)
        self.assertNotIn("deletedAt", local.rows[0])
        self.assertEqual(local.rows[0]["text"], "Phone edit")
        local.rows[0]["text"] = "Desktop edit"
        google.tasks["remote"]["deleted"] = True
        self.run_sync(local, google)
        self.assertEqual(google.tasks[self.state["entries"]["local"]["remote_id"]]["title"], "Desktop edit")

    def test_lost_create_reply_recovers_marker_without_duplicate(self):
        local, google = MemoryTodos([row("local")]), MemoryGoogle()
        google.lost_create_reply = True
        with self.assertRaises(sync.SyncError):
            self.run_sync(local, google)
        self.state = copy.deepcopy(self.saved)
        self.run_sync(local, google)
        self.assertEqual(google.created, 1)

    def test_lost_compare_and_swap_keeps_baseline_for_retry(self):
        local, google = self.mapped(remote_task=task("remote", "Phone edit"))
        local.race = True
        with self.assertRaises(sync.RetrySync):
            self.run_sync(local, google)
        self.assertEqual(self.state["entries"]["local"]["base"]["text"], "Task")
        self.assertEqual(local.rows[0]["text"], "Task")
        self.assertTrue(list((self.directory / "backups").glob("*.json")))
        local.race = False
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["text"], "Phone edit")

    def test_missing_local_record_is_restored_instead_of_deleting_google_task(self):
        local, google = self.mapped()
        local.rows = []
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["text"], "Task")
        self.assertFalse(google.tasks["remote"].get("deleted"))

    def test_interrupted_import_checkpoint_restores_task(self):
        local, google = MemoryTodos([]), MemoryGoogle([task("remote")])
        local.race = True
        with self.assertRaises(sync.RetrySync):
            self.run_sync(local, google)
        self.state = copy.deepcopy(self.saved)
        local.race = False
        self.run_sync(local, google)
        self.assertEqual(len(local.rows), 1)
        self.assertEqual(google.created, 0)

    def test_google_children_import_under_parent_and_remain_contiguous(self):
        local = MemoryTodos([row("local", "Desktop")])
        google = MemoryGoogle([task("parent", "Parent", position="1"), task("other", "Other", position="2"), task("child", "Child", parent="parent", position="3")])
        self.run_sync(local, google)
        parent_index = next(index for index, item in enumerate(local.rows) if item["text"] == "Parent")
        self.assertEqual(local.rows[parent_index + 1]["text"], "Child")
        self.assertEqual(local.rows[parent_index + 1]["parentId"], local.rows[parent_index]["id"])

    def test_parent_deletion_does_not_destroy_concurrent_child_edit(self):
        local, google = self.mapped(row("local", deletedAt="yesterday"))
        local.rows.append(row("child", "Child", parentId="local", deletedAt="yesterday"))
        google.tasks["child"] = task("child", "Phone edit", parent="remote")
        self.state["entries"]["child"] = {"remote_id": "child", "base": {"text": "Child", "completed": False}}
        self.run_sync(local, google)
        self.assertFalse(google.tasks["remote"].get("deleted"))
        self.assertFalse(google.tasks["child"].get("deleted"))
        self.assertTrue(all(not item.get("deletedAt") for item in local.rows))

    def test_storage_change_refuses_remote_writes(self):
        local, google = self.mapped()
        self.state["storage"] = "/other-todos.json"
        with self.assertRaises(sync.SyncError):
            self.run_sync(local, google)
        self.assertEqual(google.created, 0)
        self.assertEqual(google.updated, [])

    def test_long_google_titles_are_not_truncated(self):
        local, google = MemoryTodos([]), MemoryGoogle([task("remote", "x" * 1024)])
        self.run_sync(local, google)
        self.assertEqual(len(local.rows[0]["text"]), 1024)

    def test_atomic_files_are_private_and_valid(self):
        target = self.directory / "private/token.json"
        sync.atomic_json(target, {"token": "test"})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(target.read_text()), {"token": "test"})


class ApiTests(unittest.TestCase):
    def api(self):
        api = sync.GoogleTasks.__new__(sync.GoogleTasks)
        api.list_id = "list/id"
        api.session = Mock()
        return api

    def test_pagination_includes_completed_hidden_and_deleted_tasks(self):
        api = self.api()
        api.session.request.side_effect = [
            Mock(status_code=200, content=b"json", json=lambda: {"items": [task("1")], "nextPageToken": "next"}),
            Mock(status_code=200, content=b"json", json=lambda: {"items": [task("2")]}),
        ]
        self.assertEqual(len(api.list_tasks()), 2)
        params = api.session.request.call_args.kwargs["params"]
        self.assertEqual(params["pageToken"], "next")
        self.assertTrue(all(params[key] == "true" for key in ("showHidden", "showCompleted", "showDeleted")))

    def test_patch_preserves_notes_due_and_uses_etag(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=200, content=b"json", json=lambda: task("remote", "New"))
        api.update(task("remote", due="2026-10-20", notes="Note"), {"text": "New", "completed": False})
        call = api.session.request.call_args
        self.assertEqual(call.kwargs["json"], {"title": "New"})
        self.assertEqual(call.kwargs["headers"], {"If-Match": "remote-1"})
        self.assertIn("list%2Fid", call.args[1])

    def test_server_edit_aborts_without_retrying_mutation(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=412)
        with self.assertRaises(sync.RetrySync):
            api.delete(task("remote"))
        self.assertEqual(api.session.request.call_count, 1)

    def test_error_response_contents_are_not_logged(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=401, text="private-token")
        with self.assertRaises(sync.SyncError) as error:
            api.list_tasks()
        self.assertNotIn("private-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
