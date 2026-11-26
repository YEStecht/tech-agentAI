"""Validate and sanitize student images without storing the original file."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_SIZE = (1600, 1600)
SUPPORTED_FORMATS = {"JPEG": ("JPEG", "image/jpeg"), "PNG": ("PNG", "image/png")}


def prepare_image(data: bytes) -> tuple[bytes, str]:
    if not data:
        raise ValueError("비어 있는 사진은 첨부할 수 없어요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("사진은 5MB 이하만 첨부할 수 있어요.")

    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            if image.format not in SUPPORTED_FORMATS:
                raise ValueError("JPEG 또는 PNG 사진만 첨부할 수 있어요.")
            output_format, mime_type = SUPPORTED_FORMATS[image.format]
            image = ImageOps.exif_transpose(image)
            image.thumbnail(MAX_IMAGE_SIZE)
            if output_format == "JPEG" and image.mode != "RGB":
                image = image.convert("RGB")
            output = BytesIO()
            save_options = {"quality": 85, "optimize": True} if output_format == "JPEG" else {"optimize": True}
            image.save(output, format=output_format, **save_options)
            return output.getvalue(), mime_type
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("올바른 JPEG 또는 PNG 사진인지 확인해 주세요.") from None
