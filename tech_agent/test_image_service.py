"""Student image validation and privacy tests."""

import unittest
from io import BytesIO

from PIL import Image

from image_service import MAX_IMAGE_BYTES, prepare_image


def image_bytes(image_format="JPEG", size=(32, 24), exif=None):
    output = BytesIO()
    Image.new("RGB", size, "white").save(output, format=image_format, exif=exif or b"")
    return output.getvalue()


class ImageServiceTest(unittest.TestCase):
    def test_jpeg_and_png_are_validated_and_resized(self):
        jpeg, jpeg_type = prepare_image(image_bytes("JPEG", (2000, 1800)))
        png, png_type = prepare_image(image_bytes("PNG"))
        self.assertEqual("image/jpeg", jpeg_type)
        self.assertEqual("image/png", png_type)
        with Image.open(BytesIO(jpeg)) as image:
            self.assertLessEqual(max(image.size), 1600)
        with Image.open(BytesIO(png)) as image:
            self.assertEqual("PNG", image.format)

    def test_invalid_and_oversized_files_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "올바른 JPEG 또는 PNG"):
            prepare_image(b"not-an-image")
        with self.assertRaisesRegex(ValueError, "5MB 이하"):
            prepare_image(b"x" * (MAX_IMAGE_BYTES + 1))

    def test_exif_metadata_is_removed(self):
        exif = Image.Exif()
        exif[0x010E] = "student name"
        cleaned, _ = prepare_image(image_bytes(exif=exif))
        with Image.open(BytesIO(cleaned)) as image:
            self.assertEqual({}, dict(image.getexif()))


if __name__ == "__main__":
    unittest.main()
