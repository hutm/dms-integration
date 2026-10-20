"""Offline regression checks for two-way sync and interrupted operations."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
        self.moves = []
        self.lost_create_reply = False

    def list_tasks(self):
        return copy.deepcopy(list(self.tasks.values()))

    def create(self, fields):
        self.created += 1
        item = task("new-" + str(self.created), fields["text"], status="completed" if fields["completed"] else "needsAction")
        if fields.get("notes"):
            item["notes"] = fields["notes"]
        if fields.get("dueDate"):
            item["due"] = fields["dueDate"] + "T00:00:00.000Z"
        self.tasks[item["id"]] = copy.deepcopy(item)
        if self.lost_create_reply:
            self.lost_create_reply = False
            raise sync.SyncError("Response lost after successful POST")
        return item

    def move(self, item, parent_id):
        target = self.tasks[item["id"]]
        if parent_id:
            target["parent"] = parent_id
        else:
            target.pop("parent", None)
        sync.validate_tree({key: value.get("parent") for key, value in self.tasks.items()})
        self.moves.append((item["id"], parent_id))
        return copy.deepcopy(target)

    def remove_marker(self, item, reference):
        target = self.tasks[item["id"]]
        if "notes" in target:
            target["notes"] = "".join(line for line in target["notes"].splitlines(keepends=True)
                                      if line.rstrip("\r\n") != reference)
        return copy.deepcopy(target)

    def update(self, item, fields):
        target = self.tasks[item["id"]]
        target["title"] = fields["text"]
        target["status"] = "completed" if fields["completed"] else "needsAction"
        if "notes" in fields and fields["notes"] != target.get("notes", ""):
            target["notes"] = fields["notes"]
        if "dueDate" in fields and fields["dueDate"] != (target.get("due") or "")[:10]:
            if fields["dueDate"]:
                target["due"] = fields["dueDate"] + "T00:00:00.000Z"
            else:
                target.pop("due", None)
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

    def test_local_snapshot_saved_before_network_failure(self):
        local = MemoryTodos([row("parent"), row("child", parentId="parent")])
        google = MemoryGoogle()
        google.list_tasks = Mock(side_effect=sync.SyncError("Offline"))
        with self.assertRaises(sync.SyncError):
            self.run_sync(local, google)
        backup = next((self.directory / "backups").glob("*-local.json"))
        self.assertEqual(json.loads(backup.read_text())["todos"], local.rows)

    def test_unchanged_local_snapshot_does_not_repeat_backups(self):
        local, google = self.mapped()
        self.run_sync(local, google)
        self.run_sync(local, google)
        count = len(list((self.directory / "backups").glob("*-local.json")))
        self.run_sync(local, google)
        self.assertEqual(len(list((self.directory / "backups").glob("*-local.json"))), count)

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

    def test_upgrade_imports_existing_remote_metadata(self):
        local, google = self.mapped(remote_task=task("remote", notes="Existing notes\nSecond line", due="2026-10-21T00:00:00Z"))
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["notes"], "Existing notes\nSecond line")
        self.assertEqual(local.rows[0]["dueDate"], "2026-10-21")

    def test_new_local_task_exports_date_and_multiline_notes(self):
        local = MemoryTodos([row("local", dueDate="2026-11-01", notes="First line\nSecond line")])
        google = MemoryGoogle()
        self.run_sync(local, google)
        self.assertEqual(google.tasks["new-1"]["due"], "2026-11-01T00:00:00.000Z")
        self.assertEqual(google.tasks["new-1"]["notes"], "First line\nSecond line")

    def test_independent_date_and_note_changes_merge(self):
        local, google = self.mapped(row("local", dueDate="2026-10-22"), task("remote", notes="Phone notes"))
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["notes"], "Phone notes")
        self.assertEqual(google.tasks["remote"]["due"][:10], "2026-10-22")

    def test_clear_metadata_in_both_directions(self):
        local, google = self.mapped(row("local", dueDate="2026-10-22", notes="Notes"), task("remote", due="2026-10-22T00:00:00Z", notes="Notes"))
        self.run_sync(local, google)
        local.rows[0]["dueDate"] = ""
        local.rows[0]["notes"] = ""
        self.run_sync(local, google)
        self.assertNotIn("due", google.tasks["remote"])
        self.assertEqual(google.tasks["remote"]["notes"], "")
        local.rows[0].update(dueDate="2026-10-23", notes="More notes")
        self.run_sync(local, google)
        google.tasks["remote"].pop("due")
        google.tasks["remote"]["notes"] = ""
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["dueDate"], "")
        self.assertEqual(local.rows[0]["notes"], "")

    def test_concurrent_note_edits_preserve_both_versions(self):
        local, google = self.mapped()
        self.run_sync(local, google)
        local.rows[0]["notes"] = "Desktop notes"
        google.tasks["remote"]["notes"] = "Phone notes"
        self.run_sync(local, google)
        self.assertEqual({r["notes"] for r in local.rows}, {"Desktop notes", "Phone notes"})
        self.assertTrue(all(r["text"] == "Task" for r in local.rows))

    def test_concurrent_dates_survive_local_apply_conflict(self):
        local, google = self.mapped()
        self.run_sync(local, google)
        local.rows[0]["dueDate"] = "2026-10-24"
        google.tasks["remote"]["due"] = "2026-10-25T00:00:00Z"
        local.race = True
        with self.assertRaises(sync.RetrySync):
            self.run_sync(local, google)
        self.state = copy.deepcopy(self.saved)
        local.race = False
        self.run_sync(local, google)
        self.assertEqual({r["dueDate"] for r in local.rows}, {"2026-10-24", "2026-10-25"})

    def test_remote_deletion_preserves_a_concurrent_note_edit(self):
        local, google = self.mapped()
        self.run_sync(local, google)
        local.rows[0]["notes"] = "New local note"
        google.tasks["remote"]["deleted"] = True
        self.run_sync(local, google)
        self.assertFalse(local.rows[0].get("deletedAt"))
        self.assertEqual(google.tasks["new-1"]["notes"], "New local note")

    def test_invalid_dates_and_long_notes_abort_before_remote_mutation(self):
        for details in [{"dueDate": "2026-02-30"}, {"dueDate": "20261020"}, {"notes": "x" * 8193}]:
            with self.subTest(details=list(details)):
                local = MemoryTodos([row("local", **details)])
                google = MemoryGoogle()
                with self.assertRaises(sync.SyncError):
                    self.run_sync(local, google)
                self.assertEqual(google.created, 0)

    def test_same_field_conflict_preserves_both_titles(self):
        local, google = self.mapped(row("local", "Desktop edit"), task("remote", "Phone edit"))
        result = self.run_sync(local, google)
        self.assertEqual(result["conflicts_preserved"], 1)
        self.assertEqual({item["text"] for item in local.rows}, {"Desktop edit", "Phone edit"})
        self.assertTrue(self.state["conflicts"])
        self.run_sync(local, google)
        self.assertEqual(google.created, 1)

    def test_title_conflict_survives_local_cas_failure_after_remote_update(self):
        local, google = self.mapped(row("local", "Desktop edit"), task("remote", "Phone edit"))
        local.race = True
        with self.assertRaises(sync.RetrySync):
            self.run_sync(local, google)
        self.assertEqual(google.tasks["remote"]["title"], "Desktop edit")
        self.state = copy.deepcopy(self.saved)
        self.assertTrue(self.state["pending_conflict_copies"])
        local.race = False
        self.run_sync(local, google)
        self.assertEqual({item["text"] for item in local.rows}, {"Desktop edit", "Phone edit"})
        self.assertFalse(self.state["pending_conflict_copies"])

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

    def test_lost_create_reply_requires_explicit_review_without_duplicate(self):
        local, google = MemoryTodos([row("local")]), MemoryGoogle()
        google.lost_create_reply = True
        with self.assertRaises(sync.SyncError):
            self.run_sync(local, google)
        self.state = copy.deepcopy(self.saved)
        with self.assertRaises(sync.SyncError):
            self.run_sync(local, google)
        self.assertEqual(google.created, 1)
        report = sync.pending_report(google, self.state)
        self.assertEqual(report[0]["candidate_remote_ids"], ["new-1"])
        sync.resolve_creation(google, self.state, self.checkpoint, "local", "new-1")
        self.run_sync(local, google)
        self.assertEqual(google.created, 1)
        self.assertNotIn("notes", google.tasks["new-1"])

    def test_confirming_no_creation_allows_retry(self):
        local, google = MemoryTodos([row("local")]), MemoryGoogle()
        self.state["pending_creations"] = {"local": {
            "fields": sync.local_fields(local.rows[0]), "before_ids": [],
            "previous_remote_id": None, "started_at": sync.now(),
        }}
        sync.resolve_creation(google, self.state, self.checkpoint, "local")
        self.run_sync(local, google)
        self.assertEqual(google.created, 1)

    def test_rejected_create_clears_intent_without_needing_manual_review(self):
        local, google = MemoryTodos([row("local")]), MemoryGoogle()
        google.create = Mock(side_effect=sync.RequestRejected("HTTP 400"))
        with self.assertRaises(sync.RequestRejected):
            self.run_sync(local, google)
        self.assertFalse(self.state["pending_creations"])

    def test_pending_creation_cannot_bind_an_unrelated_or_existing_task(self):
        google = MemoryGoogle([task("existing"), task("other", "Different")])
        self.state["pending_creations"] = {"local": {
            "fields": {"text": "Task", "completed": False},
            "before_ids": ["existing"], "started_at": sync.now(),
        }}
        for remote_id in ("existing", "other", "missing"):
            with self.assertRaises(sync.SyncError):
                sync.resolve_creation(google, self.state, self.checkpoint, "local", remote_id)
        self.assertIn("local", self.state["pending_creations"])

    def test_durable_mapping_clears_stale_creation_intent(self):
        local, google = self.mapped()
        self.state["pending_creations"] = {"local": {
            "previous_remote_id": None, "fields": sync.local_fields(local.rows[0]),
            "before_ids": [], "started_at": sync.now(),
        }}
        self.run_sync(local, google)
        self.assertFalse(self.state["pending_creations"])
        self.assertEqual(google.created, 0)

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

    def test_local_hierarchy_exports_even_when_child_precedes_parent(self):
        local = MemoryTodos([row("child", "Child", parentId="parent"), row("parent", "Parent")])
        google = MemoryGoogle()
        self.run_sync(local, google)
        entries = self.state["entries"]
        self.assertEqual(google.tasks[entries["child"]["remote_id"]]["parent"], entries["parent"]["remote_id"])
        self.assertEqual([item["id"] for item in local.rows], ["parent", "child"])
        self.run_sync(local, google)
        self.assertEqual(len(google.moves), 1)

    def grouped(self):
        local = MemoryTodos([row("a", "A"), row("b", "B"), row("child", "Child", parentId="a")])
        google = MemoryGoogle()
        self.run_sync(local, google)
        return local, google

    def test_reparenting_and_promotion_propagate_from_dms(self):
        local, google = self.grouped()
        child = next(item for item in local.rows if item["id"] == "child")
        remote_id = self.state["entries"]["child"]["remote_id"]
        for parent in ("b", None, "a"):
            child["parentId"] = parent
            self.run_sync(local, google)
            expected = self.state["entries"][parent]["remote_id"] if parent else None
            self.assertEqual(google.tasks[remote_id].get("parent"), expected)
            child = next(item for item in local.rows if item["id"] == "child")

    def test_reparenting_and_promotion_propagate_from_google(self):
        local, google = self.grouped()
        remote_id = self.state["entries"]["child"]["remote_id"]
        for parent in ("b", None, "a"):
            expected = self.state["entries"][parent]["remote_id"] if parent else None
            google.move(google.tasks[remote_id], expected)
            self.run_sync(local, google)
            self.assertEqual(next(item for item in local.rows if item["id"] == "child")["parentId"], parent)

    def test_simultaneous_parent_moves_keep_desktop_choice_and_private_conflict(self):
        local, google = self.grouped()
        child = self.state["entries"]["child"]["remote_id"]
        next(item for item in local.rows if item["id"] == "child")["parentId"] = "b"
        google.move(google.tasks[child], None)
        result = self.run_sync(local, google)
        self.assertEqual(result["conflicts_preserved"], 1)
        self.assertEqual(google.tasks[child]["parent"], self.state["entries"]["b"]["remote_id"])
        self.assertEqual(self.state["conflicts"]["child:parent"]["remote_parent"], None)

    def test_remote_parent_move_merges_with_local_title_edit(self):
        local, google = self.grouped()
        child = self.state["entries"]["child"]["remote_id"]
        next(item for item in local.rows if item["id"] == "child")["text"] = "Desktop title"
        google.move(google.tasks[child], self.state["entries"]["b"]["remote_id"])
        self.run_sync(local, google)
        changed = next(item for item in local.rows if item["id"] == "child")
        self.assertEqual((changed["text"], changed["parentId"]), ("Desktop title", "b"))

    def test_local_parent_move_resurrects_remotely_deleted_task(self):
        local, google = self.grouped()
        child = self.state["entries"]["child"]["remote_id"]
        google.tasks[child]["deleted"] = True
        next(item for item in local.rows if item["id"] == "child")["parentId"] = "b"
        self.run_sync(local, google)
        replacement = self.state["entries"]["child"]["remote_id"]
        self.assertNotEqual(child, replacement)
        self.assertEqual(google.tasks[replacement]["parent"], self.state["entries"]["b"]["remote_id"])

    def test_legacy_flat_exports_are_nested_without_recreating(self):
        local, google = self.grouped()
        for entry in self.state["entries"].values():
            entry.pop("parent_base", None)
        remote_id = self.state["entries"]["child"]["remote_id"]
        google.tasks[remote_id].pop("parent")
        self.run_sync(local, google)
        self.assertEqual(google.created, 3)
        self.assertEqual(google.tasks[remote_id]["parent"], self.state["entries"]["a"]["remote_id"])

    def test_local_parent_child_inversion_detaches_before_attaching(self):
        local, google = self.grouped()
        next(item for item in local.rows if item["id"] == "a")["parentId"] = "child"
        next(item for item in local.rows if item["id"] == "child")["parentId"] = None
        self.run_sync(local, google)
        a = self.state["entries"]["a"]["remote_id"]
        child = self.state["entries"]["child"]["remote_id"]
        self.assertEqual(google.tasks[a]["parent"], child)
        self.assertIsNone(google.tasks[child].get("parent"))

    def test_parent_delete_does_not_cascade_to_a_child_moved_out(self):
        local, google = self.grouped()
        next(item for item in local.rows if item["id"] == "a")["deletedAt"] = "today"
        next(item for item in local.rows if item["id"] == "child")["parentId"] = "b"
        self.run_sync(local, google)
        self.assertTrue(google.tasks[self.state["entries"]["a"]["remote_id"]]["deleted"])
        self.assertFalse(google.tasks[self.state["entries"]["child"]["remote_id"]].get("deleted"))

    def test_remote_parent_move_is_not_lost_to_a_local_delete(self):
        local, google = self.grouped()
        child = next(item for item in local.rows if item["id"] == "child")
        child["deletedAt"] = "today"
        remote_id = self.state["entries"]["child"]["remote_id"]
        google.move(google.tasks[remote_id], self.state["entries"]["b"]["remote_id"])
        self.run_sync(local, google)
        child = next(item for item in local.rows if item["id"] == "child")
        self.assertNotIn("deletedAt", child)
        self.assertEqual(child["parentId"], "b")

    def test_nested_grandchildren_export_and_import(self):
        local = MemoryTodos([row("root"), row("child", parentId="root"), row("grandchild", parentId="child")])
        google = MemoryGoogle()
        self.run_sync(local, google)
        child = self.state["entries"]["child"]["remote_id"]
        grandchild = self.state["entries"]["grandchild"]["remote_id"]
        self.assertEqual(google.tasks[grandchild]["parent"], child)

    def test_local_cycle_aborts_before_google_writes(self):
        local = MemoryTodos([row("a", parentId="b"), row("b", parentId="a")])
        google = MemoryGoogle()
        with self.assertRaises(sync.SyncError):
            self.run_sync(local, google)
        self.assertEqual(google.created, 0)

    def test_hierarchy_compare_and_swap_race_keeps_parent_baseline(self):
        local, google = self.grouped()
        child = self.state["entries"]["child"]["remote_id"]
        google.move(google.tasks[child], self.state["entries"]["b"]["remote_id"])
        local.race = True
        with self.assertRaises(sync.RetrySync):
            self.run_sync(local, google)
        self.assertEqual(self.state["entries"]["child"]["parent_base"], "a")
        local.race = False
        self.run_sync(local, google)
        self.assertEqual(next(item for item in local.rows if item["id"] == "child")["parentId"], "b")

    def test_legacy_marker_cleanup_preserves_user_notes(self):
        reference = sync.marker("test", "local")
        local, google = self.mapped(remote_task=task("remote", notes="Keep my notes\n"+reference+"\nMore notes"))
        self.run_sync(local, google)
        self.assertEqual(google.tasks["remote"]["notes"], "Keep my notes\nMore notes")

    def test_legacy_unmapped_creation_recovers_then_removes_marker(self):
        local = MemoryTodos([row("local")])
        google = MemoryGoogle([task("remote", notes=sync.marker("test", "local"))])
        self.run_sync(local, google)
        self.assertEqual(google.created, 0)
        self.assertEqual(google.tasks["remote"]["notes"], "")

    def test_legacy_conflict_suffix_is_removed_from_both_sides(self):
        identifier = "conflict-"+"a"*24
        title = "Retained title (Google conflict copy)"
        local = MemoryTodos([row(identifier, title)])
        google = MemoryGoogle([task("remote", title)])
        self.state["entries"][identifier] = {"remote_id": "remote", "base": {"text": title, "completed": False}}
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["text"], "Retained title")
        self.assertEqual(google.tasks["remote"]["title"], "Retained title")

    def test_user_authored_conflict_suffix_is_not_removed(self):
        title = "My title (Google conflict copy)"
        local, google = self.mapped(row("local", title), task("remote", title))
        self.state["entries"]["local"]["base"]["text"] = title
        self.run_sync(local, google)
        self.assertEqual(local.rows[0]["text"], title)

    def test_hidden_completed_task_move_is_deferred(self):
        local, google = self.grouped()
        child = self.state["entries"]["child"]["remote_id"]
        google.tasks[child]["hidden"] = True
        google.tasks[child]["status"] = "completed"
        next(item for item in local.rows if item["id"] == "child")["parentId"] = "b"
        result = self.run_sync(local, google)
        self.assertEqual(result["hierarchy_deferred"], 1)
        self.assertEqual(self.state["entries"]["child"]["parent_base"], "a")
        google.tasks[child].pop("hidden")
        self.run_sync(local, google)
        self.assertEqual(google.tasks[child]["parent"], self.state["entries"]["b"]["remote_id"])

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

    def test_create_does_not_annotate_notes(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=200, content=b"json", json=lambda: task("new"))
        api.create({"text": "Task", "completed": False})
        self.assertEqual(api.session.request.call_args.kwargs["json"], {"title": "Task", "status": "needsAction"})

    def test_create_writes_date_and_user_notes(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=200, content=b"json", json=lambda: task("new"))
        api.create({"text": "Task", "completed": False, "dueDate": "2026-11-02", "notes": "A\nB"})
        body = api.session.request.call_args.kwargs["json"]
        self.assertEqual(body["due"], "2026-11-02T00:00:00.000Z")
        self.assertEqual(body["notes"], "A\nB")

    def test_patch_clears_date_and_notes_explicitly(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=200, content=b"json", json=lambda: task("remote"))
        api.update(task("remote", due="2026-11-02T00:00:00Z", notes="Old"),
                   {"text": "Task", "completed": False, "dueDate": "", "notes": ""})
        self.assertEqual(api.session.request.call_args.kwargs["json"], {"due": None, "notes": ""})

    def test_unchanged_metadata_is_not_sent(self):
        api = self.api()
        api.update(task("remote", due="2026-11-02T00:00:00Z", notes="Same"),
                   {"text": "Task", "completed": False, "dueDate": "2026-11-02", "notes": "Same"})
        api.session.request.assert_not_called()

    def test_old_plugin_schema_is_rejected_before_sync(self):
        local = sync.DmsTodos()
        local.ipc = Mock(return_value=json.dumps({"todos": [], "token": "old", "storageFilePath": "/old"}))
        with self.assertRaises(sync.SyncError):
            local.snapshot()

    def test_missing_widget_ipc_refreshes_plugin_then_retries(self):
        with patch.object(sync.subprocess, "run", side_effect=[
                Mock(stdout="Target not found.\n", returncode=0),
                Mock(stdout="RESCAN_TRIGGERED: dankTodo", returncode=0)]) as run:
            with self.assertRaises(sync.RetrySync):
                sync.DmsTodos().snapshot()
            self.assertEqual(run.call_args.args[0],
                             ["dms", "ipc", "call", "plugin-scan", "rescan", "dankTodo"])

    def test_ready_widget_does_not_reload_on_normal_polls(self):
        payload = {"schemaVersion": 3, "todos": [], "token": "current", "storageFilePath": "/todos"}
        with patch.object(sync.subprocess, "run", return_value=Mock(stdout=json.dumps(payload), returncode=0)) as run:
            self.assertEqual(sync.DmsTodos().snapshot(), payload)
            self.assertEqual(run.call_count, 1)

    def test_move_uses_parent_query_and_conditional_request(self):
        api = self.api()
        api.session.request.side_effect = [
            Mock(status_code=200, content=b"json", json=lambda: task("remote", etag="fresh")),
            Mock(status_code=200, content=b"json", json=lambda: task("remote", parent="parent")),
            Mock(status_code=200, content=b"json", json=lambda: task("remote")),
            Mock(status_code=200, content=b"json", json=lambda: task("remote")),
        ]
        api.move(task("remote"), "parent")
        call = api.session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertTrue(call.args[1].endswith("/remote/move"))
        self.assertEqual(call.kwargs["params"], {"parent": "parent"})
        self.assertEqual(call.kwargs["headers"], {"If-Match": "fresh"})
        api.move(task("remote"), None)
        self.assertEqual(api.session.request.call_args.kwargs["params"], {})

    def test_move_does_not_overwrite_an_intervening_parent_edit(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=200, content=b"json", json=lambda: task("remote", parent="another-parent"))
        with self.assertRaises(sync.RetrySync):
            api.move(task("remote"), "parent")
        self.assertEqual(api.session.request.call_count, 1)

    def test_marker_cleanup_is_exact_and_preserves_unrelated_notes(self):
        api = self.api()
        api.session.request.return_value = Mock(status_code=200, content=b"json", json=lambda: task("remote"))
        reference = sync.marker("test", "local")
        api.remove_marker(task("remote", notes="My note\n"+reference+"\nOther note"), reference)
        call = api.session.request.call_args
        self.assertEqual(call.kwargs["json"], {"notes": "My note\nOther note"})
        self.assertEqual(call.kwargs["headers"], {"If-Match": "remote-1"})
        api.session.request.reset_mock()
        api.remove_marker(task("remote", notes="My note mentions "+reference), reference)
        api.session.request.assert_not_called()

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
