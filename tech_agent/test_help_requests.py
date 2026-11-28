"""Teacher-help queue tests; isolated from production data and OpenAI."""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from auth import AuthenticationError, Identity
import firestore_database as firestore_db
import secure_database
import sqlite_database as sqlite_db
from test_database_backends import FakeClient


class SQLiteHelpRequestTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(sqlite_db, "DB_PATH", Path(self.directory.name) / "help.db")
        self.db_patch.start()
        sqlite_db.init_db()
        self.practice_id = sqlite_db.create_practice("트러스", school_id="school-a")
        with closing(sqlite_db.get_connection()) as connection, connection:
            connection.execute("UPDATE practices SET is_published=1 WHERE id=?", (self.practice_id,))

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def _start(self, teacher="teacher-1", label="1반"):
        sqlite_db.save_help_settings(self.practice_id, teacher, True, label, 3, 4)
        return sqlite_db.start_help_session(
            self.practice_id, teacher, f"{teacher}@example.com", "school-a"
        )

    def test_default_disabled_start_join_and_end(self):
        self.assertFalse(sqlite_db.get_help_settings(self.practice_id, "teacher-1")["enabled"])
        with self.assertRaises(ValueError):
            sqlite_db.start_help_session(
                self.practice_id, "teacher-1", "teacher@example.com", "school-a"
            )
        session = self._start()
        self.assertEqual(session["id"], sqlite_db.join_help_session(
            self.practice_id, session["join_code"]
        )["id"])
        self.assertIsNone(sqlite_db.join_help_session(self.practice_id, "BAD999"))
        self.assertTrue(sqlite_db.end_help_session(session["id"]))
        self.assertIsNone(sqlite_db.join_help_session(self.practice_id, session["join_code"]))

    def test_request_validation_duplicates_cancel_complete_and_order(self):
        session = self._start()
        with self.assertRaises(ValueError):
            sqlite_db.create_help_request(session["id"], self.practice_id, "student-a", 4, 1)
        first = sqlite_db.create_help_request(
            session["id"], self.practice_id, "student-a", 1, 1,
            {"stage_index": 0, "stage_title": "조립", "step_index": 1, "step_title": "접착"},
        )
        with self.assertRaises(ValueError):
            sqlite_db.create_help_request(session["id"], self.practice_id, "student-a", 1, 2)
        with self.assertRaises(ValueError):
            sqlite_db.create_help_request(session["id"], self.practice_id, "student-b", 1, 1)
        second = sqlite_db.create_help_request(
            session["id"], self.practice_id, "student-b", 2, 1
        )
        self.assertEqual([first["id"], second["id"]], [
            item["id"] for item in sqlite_db.list_pending_help_requests(session["id"])
        ])
        self.assertFalse(sqlite_db.cancel_help_request(
            first["id"], session["id"], "student-b"
        ))
        self.assertTrue(sqlite_db.cancel_help_request(
            first["id"], session["id"], "student-a"
        ))
        self.assertTrue(sqlite_db.complete_help_request(second["id"], session["id"]))
        self.assertEqual("completed", sqlite_db.get_student_help_request(
            session["id"], "student-b"
        )["status"])
        self.assertEqual([], sqlite_db.list_pending_help_requests(session["id"]))
        self.assertEqual("waiting", sqlite_db.create_help_request(
            session["id"], self.practice_id, "student-a", 1, 1
        )["status"])

    def test_teacher_sessions_and_classes_are_isolated(self):
        first = self._start("teacher-1", "1반")
        second = self._start("teacher-2", "2반")
        sqlite_db.create_help_request(first["id"], self.practice_id, "a", 1, 1)
        sqlite_db.create_help_request(second["id"], self.practice_id, "b", 1, 1)
        self.assertEqual("a", sqlite_db.list_pending_help_requests(first["id"])[0]["student_session_id"])
        self.assertEqual("b", sqlite_db.list_pending_help_requests(second["id"])[0]["student_session_id"])
        sqlite_db.save_help_settings(self.practice_id, "teacher-1", False, "1반", 3, 4)
        self.assertIsNone(sqlite_db.join_help_session(self.practice_id, first["join_code"]))
        self.assertIsNotNone(sqlite_db.join_help_session(self.practice_id, second["join_code"]))


class FirestoreHelpRequestTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.patches = (
            patch.object(firestore_db, "get_firestore_client", return_value=self.client),
            patch.object(firestore_db.firestore, "transactional", side_effect=lambda fn: fn),
            patch.object(firestore_db.sqlite_database, "get_next_id_floor", return_value=1),
        )
        for item in self.patches:
            item.start()
        self.practice_id = firestore_db.create_practice("트러스", school_id="school-a")
        self.client.collection("practices").document(str(self.practice_id)).set(
            {"is_published": True}, merge=True
        )

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_firestore_lifecycle_ownership_and_duplicate_locks(self):
        firestore_db.save_help_settings(self.practice_id, "teacher-1", True, "1반", 2, 3)
        session = firestore_db.start_help_session(
            self.practice_id, "teacher-1", "teacher@example.com", "school-a"
        )
        request = firestore_db.create_help_request(
            session["id"], self.practice_id, "student-a", 1, 1
        )
        with self.assertRaises(ValueError):
            firestore_db.create_help_request(
                session["id"], self.practice_id, "student-b", 1, 1
            )
        self.assertFalse(firestore_db.cancel_help_request(
            request["id"], session["id"], "student-b"
        ))
        self.assertTrue(firestore_db.complete_help_request(request["id"], session["id"]))
        self.assertEqual("completed", firestore_db.get_student_help_request(
            session["id"], "student-a"
        )["status"])
        self.assertEqual([], firestore_db.list_pending_help_requests(session["id"]))


class SecureHelpRequestTest(unittest.TestCase):
    def test_other_teacher_cannot_manage_session(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sqlite_db, "DB_PATH", Path(directory) / "secure-help.db"), \
                patch.object(secure_database, "_database", sqlite_db), \
                patch.object(secure_database, "authorize", side_effect=lambda identity: {
                    "uid": identity.uid, "email": identity.email, "active": True,
                    "role": "teacher", "school_id": "school-a",
                }):
            sqlite_db.init_db()
            practice_id = sqlite_db.create_practice("실습", school_id="school-a")
            with closing(sqlite_db.get_connection()) as connection, connection:
                connection.execute("UPDATE practices SET is_published=1 WHERE id=?", (practice_id,))
            secure_database.set_actor({
                "uid": "owner", "email": "owner@example.com", "active": True,
                "role": "teacher", "school_id": "school-a",
            })
            secure_database.save_help_settings(practice_id, True, "1반", 2, 2)
            session = secure_database.start_help_session(practice_id)
            secure_database.set_actor({
                "uid": "other", "email": "other@example.com", "active": True,
                "role": "teacher", "school_id": "school-a",
            })
            with self.assertRaises(AuthenticationError):
                secure_database.list_pending_help_requests(session["id"])
        secure_database.set_actor(None)

    def test_fragment_context_revalidates_and_never_leaks_actor(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sqlite_db, "DB_PATH", Path(directory) / "fragment-help.db"), \
                patch.object(secure_database, "_database", sqlite_db), \
                patch.object(secure_database, "verify_id_token", side_effect=lambda token: (
                    Identity("owner", "owner@example.com")
                    if token == "valid-token" else (_ for _ in ()).throw(
                        AuthenticationError("expired")
                    )
                )), \
                patch.object(secure_database, "authorize", side_effect=lambda identity: {
                    "uid": identity.uid, "email": identity.email, "active": True,
                    "role": "teacher", "school_id": "school-a",
                }):
            sqlite_db.init_db()
            practice_id = sqlite_db.create_practice("실습", school_id="school-a")
            with closing(sqlite_db.get_connection()) as connection, connection:
                connection.execute("UPDATE practices SET is_published=1 WHERE id=?", (practice_id,))
            sqlite_db.save_help_settings(practice_id, "owner", True, "1반", 2, 2)
            session = sqlite_db.start_help_session(
                practice_id, "owner", "owner@example.com", "school-a"
            )
            secure_database.set_actor(None)
            for _ in range(3):
                with secure_database.authenticated_actor_context("valid-token"):
                    self.assertEqual([], secure_database.list_pending_help_requests(session["id"]))
                self.assertIsNone(secure_database._actor.get())
            with self.assertRaises(AuthenticationError):
                with secure_database.authenticated_actor_context("expired-token"):
                    self.fail("만료된 토큰으로 인증 컨텍스트가 열리면 안 됩니다.")
            with secure_database.authenticated_actor_context("valid-token"), patch.object(
                secure_database, "authorize", side_effect=AuthenticationError("revoked")
            ):
                with self.assertRaises(AuthenticationError):
                    secure_database.list_pending_help_requests(session["id"])
            self.assertIsNone(secure_database._actor.get())


if __name__ == "__main__":
    unittest.main()
