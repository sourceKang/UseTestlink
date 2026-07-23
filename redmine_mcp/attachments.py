from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RedmineMcpError


MAX_IMAGE_ATTACHMENTS = 5
MAX_IMAGE_ATTACHMENT_BYTES = 10 * 1024 * 1024

_ALLOWED_FIELDS = {"file", "filename", "description"}
_IMAGE_EXTENSIONS = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg", ".jpe"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "image/bmp": {".bmp"},
}


@dataclass(frozen=True)
class PreparedImageAttachment:
    path: Path
    filename: str
    description: str
    content_type: str
    content: bytes
    sha256: str

    def metadata(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "description": self.description,
            "content_type": self.content_type,
            "size": len(self.content),
            "sha256": self.sha256,
        }

    def upload_reference(self, upload_token: str) -> dict[str, str]:
        reference = {
            "token": upload_token,
            "filename": self.filename,
            "content_type": self.content_type,
        }
        if self.description:
            reference["description"] = self.description
        return reference


def _detect_image_content_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    return None


def _safe_filename(value: Any, *, fallback: str, index: int) -> str:
    filename = str(value or fallback).strip()
    if not filename or filename in {".", ".."}:
        raise RedmineMcpError(
            f"attachments[{index}].filename is invalid.",
            code="ATTACHMENT_INVALID",
        )
    if len(filename) > 255 or any(character in filename for character in ("/", "\\", "\x00")):
        raise RedmineMcpError(
            f"attachments[{index}].filename must be a plain filename up to 255 characters.",
            code="ATTACHMENT_INVALID",
        )
    if any(ord(character) < 32 for character in filename):
        raise RedmineMcpError(
            f"attachments[{index}].filename contains control characters.",
            code="ATTACHMENT_INVALID",
        )
    return filename


def prepare_image_attachments(value: Any) -> list[PreparedImageAttachment]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise RedmineMcpError("attachments must be an array.", code="ATTACHMENT_INVALID")
    if len(value) > MAX_IMAGE_ATTACHMENTS:
        raise RedmineMcpError(
            f"At most {MAX_IMAGE_ATTACHMENTS} image attachments are allowed per bug.",
            code="ATTACHMENT_LIMIT_EXCEEDED",
        )

    prepared: list[PreparedImageAttachment] = []
    filenames: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RedmineMcpError(
                f"attachments[{index}] must be an object.",
                code="ATTACHMENT_INVALID",
            )
        unknown = sorted(set(item) - _ALLOWED_FIELDS)
        if unknown:
            raise RedmineMcpError(
                f"attachments[{index}] contains unsupported fields: {', '.join(unknown)}.",
                code="ATTACHMENT_INVALID",
            )
        file_value = str(item.get("file") or "").strip()
        if not file_value:
            raise RedmineMcpError(
                f"attachments[{index}].file is required.",
                code="ATTACHMENT_INVALID",
            )
        path = Path(file_value).expanduser()
        if not path.exists() or not path.is_file():
            raise RedmineMcpError(
                f"Attachment file does not exist or is not a file: {path}",
                code="ATTACHMENT_NOT_FOUND",
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise RedmineMcpError(
                f"Attachment file could not be read: {path}: {exc}",
                code="ATTACHMENT_READ_FAILED",
            ) from exc
        if not content:
            raise RedmineMcpError(
                f"Attachment file must not be empty: {path}",
                code="ATTACHMENT_INVALID",
            )
        if len(content) > MAX_IMAGE_ATTACHMENT_BYTES:
            raise RedmineMcpError(
                f"Attachment exceeds the {MAX_IMAGE_ATTACHMENT_BYTES}-byte limit: {path}",
                code="ATTACHMENT_LIMIT_EXCEEDED",
            )

        content_type = _detect_image_content_type(content)
        if content_type is None:
            raise RedmineMcpError(
                f"Attachment is not a supported PNG, JPEG, GIF, WebP, or BMP image: {path}",
                code="ATTACHMENT_TYPE_UNSUPPORTED",
            )
        filename = _safe_filename(item.get("filename"), fallback=path.name, index=index)
        if Path(filename).suffix.casefold() not in _IMAGE_EXTENSIONS[content_type]:
            raise RedmineMcpError(
                f"attachments[{index}].filename extension does not match the image content.",
                code="ATTACHMENT_TYPE_MISMATCH",
            )
        normalized_filename = filename.casefold()
        if normalized_filename in filenames:
            raise RedmineMcpError(
                f"Duplicate attachment filename: {filename}",
                code="ATTACHMENT_INVALID",
            )
        filenames.add(normalized_filename)

        description = str(item.get("description") or "").strip()
        if len(description) > 255:
            raise RedmineMcpError(
                f"attachments[{index}].description exceeds 255 characters.",
                code="ATTACHMENT_INVALID",
            )
        prepared.append(
            PreparedImageAttachment(
                path=path,
                filename=filename,
                description=description,
                content_type=content_type,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return prepared
