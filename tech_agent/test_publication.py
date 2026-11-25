"""Isolated practice publication tests."""

import tempfile
import unittest
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import firestore_database as firestore_db
import sqlite_database as sqlite_db
from publication import PracticePublishError
from test_database_backends import FakeClient


STAGES = [{
    "title": "제작",
    "steps": [{"title": "작업", "activity": "활동", "source": ""}],
}]
SAFETY_ITEMS = [{
    "question": "안전 행동을 확인했나요?",
    "origin": "teacher_added",
    "source": "",
}]


class PublicationContractMixin:
    backend = None

    def prepare(self, practice_id):
        self.backend.set_vector_store(practice_id, "vs_test")
        self.backend.add_practice_file(
            practice_id, "guide.pdf", "file_test", "vector_file_test", "completed"
        )
        self.backend.save_stage_plan(practice_id, STAGES)
        self.backend.save_safety_checklist(practice_id, SAFETY_ITEMS)

    def check_contract(self, practice_id):
        self.assertNotIn(practice_id, [item["id"] for item in self.backend.list_published_practices()])
        self.assertIsNone(self.backend.get_student_practice_data(practice_id))
        with self.assertRaises(PracticePublishError):
            self.backend.set_practice_published(practice_id, True)
        self.assertEqual(0, self.backend.get_practice(practice_id)["is_published"])

        self.prepare(practice_id)
        requirements = self.backend.get_publish_requirements(practice_id)
        self.assertTrue(requirements["can_publish"])
        self.assertTrue(self.backend.set_practice_published(practice_id, True))
        self.assertEqual(1, self.backend.get_practice(practice_id)["is_published"])
        self.assertIn(practice_id, [item["id"] for item in self.backend.list_published_practices()])
        student_data = self.backend.get_student_practice_data(practice_id)
        self.assertEqual("vs_test", student_data["vector_store_id"])
        self.assertEqual(STAGES, student_data["stages"])
        self.assertEqual(SAFETY_ITEMS, student_data["safety_items"])
        self.assertEqual(STAGES, self.backend.get_stage_plan(practice_id)["stages"])
        self.assertEqual(
            SAFETY_ITEMS, self.backend.get_safety_checklist(practice_id)["items"]
        )

        self.assertTrue(self.backend.set_practice_published(practice_id, False))
        self.assertEqual(0, self.backend.get_practice(practice_id)["is_published"])
        self.assertIsNone(self.backend.get_student_practice_data(practice_id))


class SQLitePublicationTest(PublicationContractMixin, unittest.TestCase):
    backend = sqlite_db

    def test_publication_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sqlite_db, "DB_PATH", Path(directory) / "test.db"):
                sqlite_db.init_db()
                self.check_contract(sqlite_db.create_practice("test"))


class FirestorePublicationTest(PublicationContractMixin, unittest.TestCase):
    backend = firestore_db

    def test_publication_contract(self):
        client = FakeClient()
        with (
            patch.object(firestore_db, "get_firestore_client", return_value=client),
            patch.object(firestore_db.firestore, "transactional", side_effect=lambda fn: fn),
            patch.object(firestore_db.sqlite_database, "get_next_id_floor", return_value=1),
        ):
            self.check_contract(firestore_db.create_practice("test"))


class DatabaseFacadeTest(unittest.TestCase):
    def test_publication_and_safety_exports_for_both_backends(self):
        statement = (
            "from database import get_publish_requirements, set_practice_published, "
            "get_safety_checklist, save_safety_checklist, "
            "list_published_practices, get_student_practice_data"
        )
        for backend in ("sqlite", "firestore"):
            environment = {**os.environ, "PRACTICE_DB_BACKEND": backend}
            subprocess.run(
                [sys.executable, "-c", statement],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
