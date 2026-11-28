"""PDF page rendering, storage, validation, and access tests."""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import firestore_database as firestore_db
import sqlite_database as sqlite_db
from agent_service import DiagnosisTurn, validated_material_references
from material_service import render_pdf_pages
from test_database_backends import FakeClient


def sample_pdf():
    output = tempfile.SpooledTemporaryFile()
    first = Image.new("RGB", (400, 300), "white")
    second = Image.new("RGB", (400, 300), "lightgray")
    first.save(output, format="PDF", save_all=True, append_images=[second])
    output.seek(0)
    data = output.read()
    output.close()
    return data


class MaterialRenderingTest(unittest.TestCase):
    def test_pdf_pages_are_rendered_once_as_bounded_images(self):
        pages = render_pdf_pages(sample_pdf())
        self.assertEqual([1, 2], [item["page_number"] for item in pages])
        self.assertTrue(all(item["mime_type"] == "image/jpeg" for item in pages))
        self.assertTrue(all(len(item["image_bytes"]) < 850_000 for item in pages))

    def test_server_rejects_unknown_and_out_of_range_references(self):
        turn = DiagnosisTurn(answer="답", material_references=[
            {"file_id": 7, "page_number": 2}, {"file_id": 7, "page_number": 9},
            {"file_id": 999, "page_number": 1}, {"file_id": 8, "page_number": 1},
        ])
        catalog = [
            {"file_id": 7, "filename": "guide.pdf", "page_count": 2},
            {"file_id": 8, "filename": "other.pdf", "page_count": 1},
        ]
        self.assertEqual(
            [(7, 2), (8, 1)],
            [(item["file_id"], item["page_number"])
             for item in validated_material_references(turn, catalog)],
        )


class SQLiteMaterialStorageTest(unittest.TestCase):
    def test_pages_follow_publication_and_file_deletion(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            sqlite_db, "DB_PATH", Path(directory) / "materials.db"
        ):
            sqlite_db.init_db()
            practice_id = sqlite_db.create_practice("실습")
            file_id = sqlite_db.add_practice_file(
                practice_id, "slides.pptx", "file-1", "vector-file-1"
            )
            pages = render_pdf_pages(sample_pdf())
            sqlite_db.save_material_pages(file_id, pages, "slides.pdf")
            self.assertEqual([], sqlite_db.get_student_material_catalog(practice_id))
            with closing(sqlite_db.get_connection()) as connection, connection:
                connection.execute("UPDATE practices SET is_published = 1 WHERE id = ?", (practice_id,))
            catalog = sqlite_db.get_student_material_catalog(practice_id)
            self.assertEqual((file_id, 2, "slides.pdf"), (
                catalog[0]["file_id"], catalog[0]["page_count"],
                catalog[0]["visual_source_filename"],
            ))
            self.assertIsNotNone(sqlite_db.get_student_material_page(practice_id, file_id, 2))
            self.assertIsNone(sqlite_db.get_student_material_page(practice_id, file_id, 3))
            self.assertTrue(sqlite_db.remove_practice_file(file_id))
            self.assertEqual([], sqlite_db.get_student_material_catalog(practice_id))


class FirestoreMaterialStorageTest(unittest.TestCase):
    def test_pages_are_isolated_to_published_practice(self):
        client = FakeClient()
        with (
            patch.object(firestore_db, "get_firestore_client", return_value=client),
            patch.object(firestore_db.firestore, "transactional", side_effect=lambda fn: fn),
            patch.object(firestore_db.sqlite_database, "get_next_id_floor", return_value=1),
        ):
            practice_id = firestore_db.create_practice("실습", school_id="school-a")
            client.collection("practices").document(str(practice_id)).set({"is_published": True}, merge=True)
            file_id = firestore_db.add_practice_file(
                practice_id, "guide.pdf", "file-1", "vector-file-1"
            )
            pages = render_pdf_pages(sample_pdf())
            firestore_db.save_material_pages(file_id, pages, "guide.pdf")
            self.assertEqual(2, firestore_db.get_student_material_catalog(practice_id)[0]["page_count"])
            self.assertIsNotNone(firestore_db.get_student_material_page(practice_id, file_id, 1))
            client.collection("practices").document(str(practice_id)).set({"is_published": False}, merge=True)
            self.assertEqual([], firestore_db.get_student_material_catalog(practice_id))
            self.assertIsNone(firestore_db.get_student_material_page(practice_id, file_id, 1))


if __name__ == "__main__":
    unittest.main()
