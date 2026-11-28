"""Isolated authentication and authorization tests; no live Firebase is used."""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import auth
import secure_database
import teacher_permissions
from auth import AuthenticationError, Identity


class AuthenticationTest(unittest.TestCase):
    def test_firebase_component_keeps_original_logout_protocol(self):
        source = (Path(__file__).parent / "firebase_auth_frontend" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("if(args.logout && !lastLogout)", source)
        self.assertIn("await performLogout()", source)
        self.assertNotIn("signed_out", source)

    def test_verified_firebase_token_is_accepted(self):
        claims = {"uid": "uid-1", "email": "Teacher@Example.com", "email_verified": True}
        with patch.object(auth.firebase_auth, "verify_id_token", return_value=claims), \
             patch.object(auth, "get_firebase_app"):
            self.assertEqual(
                Identity("uid-1", "teacher@example.com"), auth.verify_id_token("token")
            )

    def test_missing_forged_expired_and_unverified_tokens_are_rejected(self):
        with self.assertRaises(AuthenticationError):
            auth.verify_id_token("")
        errors = [
            ValueError("forged"),
            auth.firebase_auth.ExpiredIdTokenError("expired", None),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                 patch.object(auth.firebase_auth, "verify_id_token", side_effect=error), \
                 patch.object(auth, "get_firebase_app"), \
                 self.assertRaises(AuthenticationError):
                auth.verify_id_token("bad")
        with patch.object(auth.firebase_auth, "verify_id_token", return_value={
            "uid": "u", "email": "x@example.com", "email_verified": False,
        }), patch.object(auth, "get_firebase_app"), self.assertRaises(AuthenticationError):
            auth.verify_id_token("unverified")


class PermissionTest(unittest.TestCase):
    def test_missing_admin_setting_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(auth, "get_setting", return_value=""), \
             patch.object(teacher_permissions, "admin_email", return_value=""), \
             patch.object(teacher_permissions, "_reference") as reference, \
             self.assertRaises(AuthenticationError):
            reference.return_value.get.return_value.exists = False
            teacher_permissions.authorize(Identity("u", "any@example.com"))

    def test_admin_and_active_teacher_are_distinct(self):
        with patch.object(teacher_permissions, "admin_email", return_value="admin@example.com"):
            self.assertEqual("admin", teacher_permissions.authorize(
                Identity("a", "admin@example.com"))["role"])
            snapshot = MagicMock(exists=True)
            snapshot.to_dict.return_value = {
                "email": "teacher@example.com", "active": True, "school_id": "school-a"
            }
            with patch.object(teacher_permissions, "_reference") as reference:
                reference.return_value.get.return_value = snapshot
                actor = teacher_permissions.authorize(Identity("t", "teacher@example.com"))
                self.assertEqual(("teacher", "school-a"), (actor["role"], actor["school_id"]))

    def test_unapproved_and_disabled_teacher_are_rejected(self):
        with patch.object(teacher_permissions, "admin_email", return_value="admin@example.com"), \
             patch.object(teacher_permissions, "_reference") as reference:
            reference.return_value.get.return_value = MagicMock(exists=False)
            with self.assertRaises(AuthenticationError):
                teacher_permissions.authorize(Identity("u", "none@example.com"))
            disabled = MagicMock(exists=True)
            disabled.to_dict.return_value = {"active": False, "school_id": "s"}
            reference.return_value.get.return_value = disabled
            with self.assertRaises(AuthenticationError):
                teacher_permissions.authorize(Identity("u", "off@example.com"))

    def test_only_admin_can_manage_teachers_and_duplicates_are_blocked(self):
        teacher = {"role": "teacher", "email": "t@example.com"}
        with self.assertRaises(AuthenticationError):
            teacher_permissions.require_admin(teacher)
        with self.assertRaises(AuthenticationError):
            teacher_permissions.list_teachers(teacher)
        admin = {"role": "admin", "email": "admin@example.com"}
        with patch.object(teacher_permissions, "admin_email", return_value="admin@example.com"), \
             patch.object(teacher_permissions, "_reference") as reference:
            reference.return_value.get.return_value.exists = True
            with self.assertRaisesRegex(ValueError, "이미 등록"):
                teacher_permissions.add_teacher(admin, "new@example.com", "school")

    def test_admin_can_list_firestore_teacher_fields(self):
        snapshot = MagicMock(id="teacher-document")
        snapshot.to_dict.return_value = {
            "email": "teacher@example.com", "school_id": "school-a", "active": True,
        }
        client = MagicMock()
        client.collection.return_value.stream.return_value = [snapshot]
        admin = {"role": "admin", "email": "admin@example.com"}
        with patch.object(teacher_permissions, "admin_email", return_value="admin@example.com"), \
             patch.object(teacher_permissions, "get_firestore_client", return_value=client):
            teachers = teacher_permissions.list_teachers(admin)
        self.assertEqual(
            ("teacher@example.com", "school-a", True, "teacher-document"),
            (teachers[0]["email"], teachers[0]["school_id"], teachers[0]["active"], teachers[0]["id"]),
        )


class SecureDatabaseTest(unittest.TestCase):
    def setUp(self):
        secure_database.set_actor({"role": "teacher", "email": "t@example.com",
                                   "uid": "u", "school_id": "school-a", "active": True})

    def test_school_isolation_and_revocation_recheck(self):
        actor = {"role": "teacher", "email": "t@example.com", "uid": "u",
                 "school_id": "school-a", "active": True}
        with patch.object(secure_database, "authorize", return_value=actor), \
             patch.object(secure_database._database, "list_practices", return_value=[
                 {"id": 1, "school_id": "school-a"}, {"id": 2, "school_id": "school-b"},
                 {"id": 3, "school_id": None},
             ]):
            self.assertEqual([1], [item["id"] for item in secure_database.list_practices()])
        with patch.object(secure_database, "authorize", return_value=actor), \
             patch.object(secure_database._database, "get_practice", return_value={
                 "id": 2, "school_id": "school-b"
             }), patch.object(secure_database._database, "get_question_answers") as records, \
             self.assertRaises(AuthenticationError):
            secure_database.get_question_answers(2, "2026-11-01", "2026-11-30")
        records.assert_not_called()
        with patch.object(secure_database, "authorize", side_effect=AuthenticationError("revoked")), \
             self.assertRaises(AuthenticationError):
            secure_database.list_practices()

    def test_logout_state_blocks_protected_calls(self):
        secure_database.set_actor(None)
        with self.assertRaises(AuthenticationError):
            secure_database.list_practices()


if __name__ == "__main__":
    unittest.main()
