"""Isolated contract tests; no production Firestore or SQLite data is changed."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import firestore_database as firestore_db
import migrate_sqlite_to_firestore as migration
import sqlite_database as sqlite_db
from stage_editor import (
    StagePlanConflictError, add_stage, add_step, delete_step, move_stage,
    move_step, transfer_step, validate_stages,
)
from stage_ui import _clear_stage_widget_keys


class Snapshot:
    def __init__(self, identifier, value):
        self.id = identifier
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return dict(self._value) if self._value is not None else None


class Reference:
    def __init__(self, client, collection, identifier):
        self.client = client
        self.collection_name = collection
        self.id = identifier

    def get(self, transaction=None):
        return Snapshot(self.id, self.client.data[self.collection_name].get(self.id))

    def set(self, value, merge=False):
        self.client.write(self, value, merge)

    def collection(self, name):
        path = f"{self.collection_name}/{self.id}/{name}"
        self.client.data.setdefault(path, {})
        return Collection(self.client, path)


class Collection:
    def __init__(self, client, name, condition=None):
        self.client = client
        self.name = name
        self.condition = condition

    def document(self, identifier):
        return Reference(self.client, self.name, identifier)

    def stream(self, transaction=None):
        values = self.client.data[self.name].items()
        if self.condition:
            field, expected = self.condition
            values = ((key, value) for key, value in values if value.get(field) == expected)
        return [Snapshot(key, value) for key, value in values]

    def where(self, field, operator, expected):
        if operator != "==":
            raise AssertionError("Only equality is needed by these tests")
        return Collection(self.client, self.name, (field, expected))


class Transaction:
    def __init__(self, client):
        self.client = client

    def set(self, reference, value, merge=False):
        self.client.write(reference, value, merge)

    def delete(self, reference):
        self.client.data[reference.collection_name].pop(reference.id, None)


class Batch:
    def __init__(self, client):
        self.client = client
        self.pending = []

    def create(self, reference, value):
        if reference.get().exists:
            raise RuntimeError("document exists")
        self.pending.append((reference, value))

    def commit(self):
        for reference, value in self.pending:
            self.client.write(reference, value, False)


class FakeClient:
    def __init__(self):
        self.data = {}

    def collection(self, name):
        self.data.setdefault(name, {})
        return Collection(self, name)

    def transaction(self):
        return Transaction(self)

    def batch(self):
        return Batch(self)

    def write(self, reference, value, merge):
        current = self.data[reference.collection_name].get(reference.id, {}) if merge else {}
        resolved = {
            key: datetime.now(timezone.utc) if item is firestore_db.firestore.SERVER_TIMESTAMP else item
            for key, item in value.items()
        }
        self.data[reference.collection_name][reference.id] = {**current, **resolved}


class SQLiteContractTest(unittest.TestCase):
    def test_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sqlite_db, "DB_PATH", Path(directory) / "test.db"):
                sqlite_db.init_db()
                first = sqlite_db.create_practice(" first ", " description ")
                second = sqlite_db.create_practice("second")
                self.assertEqual([second, first], [item["id"] for item in sqlite_db.list_practices()])
                self.assertTrue(sqlite_db.update_practice(first, "updated", "text"))
                self.assertFalse(sqlite_db.update_practice(999, "missing", ""))
                sqlite_db.set_vector_store(first, "vs_original")
                file_id = sqlite_db.add_practice_file(
                    first, "guide.pdf", "file_original", "vector_file_original"
                )
                second_file_id = sqlite_db.add_practice_file(
                    first, "guide-2.pdf", "file_second", "vector_file_second"
                )
                self.assertEqual(
                    [second_file_id, file_id],
                    [item["id"] for item in sqlite_db.list_practice_files(first)],
                )
                self.assertIsNone(sqlite_db.get_practice(999))
                self.assertIsNone(sqlite_db.get_practice_file(999))
                self.assertTrue(sqlite_db.remove_practice_file(file_id))
                self.assertFalse(sqlite_db.remove_practice_file(file_id))
                stages = [{
                    "title": "stage",
                    "steps": [{"title": "step", "activity": "act", "source": ""}],
                }]
                self.assertEqual(1, sqlite_db.save_stage_plan(first, stages))
                self.assertEqual(stages, sqlite_db.get_stage_plan(first)["stages"])
                self.assertEqual(2, sqlite_db.save_stage_plan(first, stages, 1))
                with self.assertRaises(StagePlanConflictError):
                    sqlite_db.save_stage_plan(first, stages, 1)
                with self.assertRaises(NotImplementedError):
                    sqlite_db.delete_practice(first)


class FirestoreContractTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.patches = (
            patch.object(firestore_db, "get_firestore_client", return_value=self.client),
            patch.object(firestore_db.firestore, "transactional", side_effect=lambda function: function),
            patch.object(
                firestore_db.sqlite_database,
                "get_next_id_floor",
                side_effect=lambda table: 5 if table == "practices" else 8,
            ),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_contract_and_integer_ids(self):
        first = firestore_db.create_practice(" first ", " description ")
        second = firestore_db.create_practice("second")
        self.assertEqual((5, 6), (first, second))
        self.assertEqual([6, 5], [item["id"] for item in firestore_db.list_practices()])
        self.assertTrue(firestore_db.update_practice(first, "updated", "text"))
        self.assertFalse(firestore_db.update_practice(999, "missing", ""))
        firestore_db.set_vector_store(first, "vs_original")
        file_id = firestore_db.add_practice_file(
            first, "guide.pdf", "file_original", "vector_file_original"
        )
        second_file_id = firestore_db.add_practice_file(
            first, "guide-2.pdf", "file_second", "vector_file_second"
        )
        self.assertEqual(8, file_id)
        self.assertEqual(
            [second_file_id, file_id],
            [item["id"] for item in firestore_db.list_practice_files(first)],
        )
        self.assertIsNone(firestore_db.get_practice(999))
        self.assertIsNone(firestore_db.get_practice_file(999))
        self.assertTrue(firestore_db.remove_practice_file(file_id))
        self.assertFalse(firestore_db.remove_practice_file(file_id))
        stages = [{
            "title": "stage",
            "steps": [{"title": "step", "activity": "act", "source": ""}],
        }]
        self.assertEqual(1, firestore_db.save_stage_plan(first, stages))
        self.assertEqual(stages, firestore_db.get_stage_plan(first)["stages"])
        self.assertEqual(2, firestore_db.save_stage_plan(first, stages, 1))
        with self.assertRaises(StagePlanConflictError):
            firestore_db.save_stage_plan(first, stages, 1)
        with self.assertRaises(NotImplementedError):
            firestore_db.delete_practice(first)


class MigrationTest(unittest.TestCase):
    def test_repeated_migration_is_idempotent(self):
        client = FakeClient()
        created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        source = {
            "practices": [{
                "id": 3, "title": "practice", "description": "", "vector_store_id": "vs_keep",
                "is_published": False, "created_at": created,
            }],
            "practice_files": [{
                "id": 7, "practice_id": 3, "filename": "guide.pdf",
                "openai_file_id": "file_keep", "vector_store_file_id": "vs_file_keep",
                "status": "completed", "created_at": created,
            }],
            "floors": {"practices": 4, "practice_files": 8},
        }
        with (
            patch.object(migration, "load_source", return_value=source),
            patch.object(migration, "ensure_counter_floors"),
        ):
            first = migration.migrate(client)
            second = migration.migrate(client)
        self.assertTrue(first["matches"])
        self.assertTrue(second["matches"])
        self.assertEqual(1, len(client.data["practices"]))
        self.assertEqual(1, len(client.data["practice_files"]))
        self.assertEqual("vs_keep", client.data["practices"]["3"]["vector_store_id"])
        self.assertEqual("file_keep", client.data["practice_files"]["7"]["openai_file_id"])


class StageEditorTest(unittest.TestCase):
    def test_structure_changes_and_validation(self):
        stages = []
        add_stage(stages)
        add_stage(stages)
        add_step(stages, 0)
        stages[0]["title"] = "first"
        stages[1]["title"] = "second"
        stages[0]["steps"][0].update(title="step", activity="activity")
        self.assertEqual([], validate_stages([stages[0]]))
        self.assertTrue(move_stage(stages, 1, -1))
        self.assertTrue(transfer_step(stages, 1, 0, 0))
        add_step(stages, 0)
        self.assertTrue(move_step(stages, 0, 1, -1))
        delete_step(stages, 0, 0)
        self.assertEqual("step", stages[0]["steps"][0]["title"])
        stages[0]["steps"][0]["activity"] = ""
        self.assertTrue(any("학생 활동" in error for error in validate_stages(stages)))

    def test_only_selected_practice_widget_keys_are_cleared(self):
        state = {
            "stage_title_1_2_0": "old",
            "step_title_1_2_0_0": "old",
            "activity_1_2_0_0": "old",
            "source_1_2_0_0": "old",
            "move_target_1_2_0_0": 1,
            "stage_title_2_1_0": "keep",
            "teacher_authenticated": True,
        }
        _clear_stage_widget_keys(state, 1)
        self.assertEqual(
            {"stage_title_2_1_0": "keep", "teacher_authenticated": True},
            state,
        )


if __name__ == "__main__":
    unittest.main()
