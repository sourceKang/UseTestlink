from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import RedmineMcpError
from .secret_scan import MAX_REPORTED_FINDINGS, RULESET_VERSION, SecretFinding, describe_findings, scan_text


MIB = 1024 * 1024

# Images keep the limits they had before videos and text were added.
MAX_IMAGE_ATTACHMENTS = 5
MAX_IMAGE_ATTACHMENT_BYTES = 10 * MIB
# The company Redmine attachment_max_size is not readable through the API, so
# these are local constants; Redmine still rejects larger uploads (HTTP 422)
# before any issue or comment is written.
MAX_VIDEO_ATTACHMENTS = 3
MAX_VIDEO_ATTACHMENT_BYTES = 20 * MIB
MAX_TEXT_ATTACHMENTS = 5
MAX_TEXT_ATTACHMENT_BYTES = 1 * MIB
MAX_ATTACHMENTS = 10
MAX_TOTAL_ATTACHMENT_BYTES = 64 * MIB

_ALLOWED_FIELDS = {"file", "filename", "description"}
_IMAGE_EXTENSIONS = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg", ".jpe"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "image/bmp": {".bmp"},
}
_VIDEO_EXTENSIONS = {
    "video/webm": {".webm"},
    "video/x-matroska": {".mkv"},
    "video/mp4": {".mp4", ".m4v"},
    "video/quicktime": {".mov"},
}
_TEXT_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".log": "text/plain",
    ".py": "text/x-python",
    ".sh": "text/x-shellscript",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".csv": "text/csv",
    ".md": "text/markdown",
}
_KIND_LIMITS = {
    "image": (MAX_IMAGE_ATTACHMENTS, MAX_IMAGE_ATTACHMENT_BYTES),
    "video": (MAX_VIDEO_ATTACHMENTS, MAX_VIDEO_ATTACHMENT_BYTES),
    "text": (MAX_TEXT_ATTACHMENTS, MAX_TEXT_ATTACHMENT_BYTES),
}
SUPPORTED_TYPES_SUMMARY = (
    "<=10 files, <=64 MiB total; image PNG/JPEG/GIF/WebP/BMP <=5x10 MiB; "
    "video WebM/MKV/MP4/MOV <=3x20 MiB; UTF-8 text py/sh/json/jsonl/log/txt/csv/md <=5x1 MiB, "
    "credential-like lines are flagged in preview, not blocked"
)


@dataclass(frozen=True)
class PreparedAttachment:
    path: Path
    filename: str
    description: str
    kind: str
    content_type: str
    content: bytes
    sha256: str
    content_scan: dict[str, Any] | None = field(default=None)

    def metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "filename": self.filename,
            "description": self.description,
            "kind": self.kind,
            "content_type": self.content_type,
            "size": len(self.content),
            "sha256": self.sha256,
        }
        if self.content_scan is not None:
            metadata["content_scan"] = self.content_scan
        return metadata

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
    # "BM" alone also starts ordinary text, so require the zeroed reserved header bytes.
    if content.startswith(b"BM") and content[6:10] == b"\x00\x00\x00\x00":
        return "image/bmp"
    return None


def _detect_video_content_type(content: bytes) -> str | None:
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        # The EBML header names its DocType within the first few dozen bytes.
        header = content[:64]
        if b"webm" in header:
            return "video/webm"
        if b"matroska" in header:
            return "video/x-matroska"
        return None
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return "video/quicktime" if content[8:12] == b"qt  " else "video/mp4"
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


def _classify(
    content: bytes, filename: str, *, path: Path, index: int
) -> tuple[str, str, dict[str, Any] | None]:
    """Return (kind, content_type, content_scan); binary content wins over the extension."""
    suffix = Path(filename).suffix.casefold()
    for kind, detected, extensions in (
        ("image", _detect_image_content_type(content), _IMAGE_EXTENSIONS),
        ("video", _detect_video_content_type(content), _VIDEO_EXTENSIONS),
    ):
        if detected is None:
            continue
        if suffix not in extensions[detected]:
            raise RedmineMcpError(
                f"attachments[{index}].filename extension does not match the {kind} content.",
                code="ATTACHMENT_TYPE_MISMATCH",
            )
        return kind, detected, None

    content_type = _TEXT_CONTENT_TYPES.get(suffix)
    if content_type is None:
        raise RedmineMcpError(
            f"Attachment is not a supported image, video, or text file: {path}. "
            f"Supported: {SUPPORTED_TYPES_SUMMARY}.",
            code="ATTACHMENT_TYPE_UNSUPPORTED",
        )
    if b"\x00" in content:
        raise RedmineMcpError(
            f"Text attachment contains NUL bytes and looks binary: {filename}",
            code="ATTACHMENT_TYPE_MISMATCH",
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RedmineMcpError(
            f"Text attachment must be UTF-8: {filename} (invalid byte at offset {exc.start}).",
            code="ATTACHMENT_ENCODING_INVALID",
        ) from exc
    # Attachments are evidence for RD, which may need test accounts to reproduce, so
    # credential-like lines are flagged for review in the preview instead of blocked.
    findings = scan_text(text, suffix=suffix)
    content_scan: dict[str, Any] = {
        "status": "flagged" if findings else "passed",
        "ruleset_version": RULESET_VERSION,
    }
    if findings:
        content_scan["finding_count"] = len(findings)
        content_scan["findings"] = [
            {"line": item.line, "rule": item.rule} for item in findings[:MAX_REPORTED_FINDINGS]
        ]
    return "text", content_type, content_scan


def attachment_warnings(metadata: list[dict[str, Any]]) -> list[str]:
    """Preview warnings for text attachments with credential-like lines (no values)."""
    warnings = []
    for item in metadata:
        scan = item.get("content_scan") or {}
        if scan.get("status") != "flagged":
            continue
        findings = [SecretFinding(line=entry["line"], rule=entry["rule"]) for entry in scan["findings"]]
        hidden = scan["finding_count"] - len(findings)
        detail = describe_findings(findings) + (f", and {hidden} more" if hidden > 0 else "")
        warnings.append(
            f"Text attachment {item['filename']} has credential-like lines ({detail}); "
            "it is uploaded unchanged for RD, so confirm its content before writing."
        )
    return warnings


def prepare_attachments(value: Any) -> list[PreparedAttachment]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise RedmineMcpError("attachments must be an array.", code="ATTACHMENT_INVALID")
    if len(value) > MAX_ATTACHMENTS:
        raise RedmineMcpError(
            f"At most {MAX_ATTACHMENTS} attachments are allowed per write.",
            code="ATTACHMENT_LIMIT_EXCEEDED",
        )

    prepared: list[PreparedAttachment] = []
    filenames: set[str] = set()
    kind_counts: dict[str, int] = {}
    total_bytes = 0
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
        # Refuse oversized files before reading them into memory.
        largest = max(limit for _, limit in _KIND_LIMITS.values())
        try:
            size_on_disk = path.stat().st_size
        except OSError as exc:
            raise RedmineMcpError(
                f"Attachment file could not be read: {path}: {exc}",
                code="ATTACHMENT_READ_FAILED",
            ) from exc
        if size_on_disk > largest:
            raise RedmineMcpError(
                f"Attachment exceeds the {largest}-byte limit: {path}",
                code="ATTACHMENT_LIMIT_EXCEEDED",
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

        filename = _safe_filename(item.get("filename"), fallback=path.name, index=index)
        kind, content_type, content_scan = _classify(content, filename, path=path, index=index)
        max_count, max_bytes = _KIND_LIMITS[kind]
        if len(content) > max_bytes:
            raise RedmineMcpError(
                f"{kind.capitalize()} attachment exceeds the {max_bytes}-byte limit: {path}",
                code="ATTACHMENT_LIMIT_EXCEEDED",
            )
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind_counts[kind] > max_count:
            raise RedmineMcpError(
                f"At most {max_count} {kind} attachments are allowed per write.",
                code="ATTACHMENT_LIMIT_EXCEEDED",
            )
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
            raise RedmineMcpError(
                f"Attachments exceed the {MAX_TOTAL_ATTACHMENT_BYTES}-byte total limit.",
                code="ATTACHMENT_LIMIT_EXCEEDED",
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
            PreparedAttachment(
                path=path,
                filename=filename,
                description=description,
                kind=kind,
                content_type=content_type,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                content_scan=content_scan,
            )
        )
    return prepared
