"""Block text attachments that look like they carry credentials.

The scanner is a gate, not a sanitizer: a hit rejects the attachment and the
caller fixes the file. Findings carry only the line number and rule name, never
the matched value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import (
    _LONG_PASSWORD_FLAG_RE,
    _SHORT_PASSWORD_FLAG_RE,
    _SSHPASS_RE,
    _URL_USERINFO_RE,
    _USER_FLAG_RE,
    _known_secret_values,
)


RULESET_VERSION = 2
MAX_REPORTED_FINDINGS = 20

_KEYS = (
    r"passw(?:or)?d|passphrase|secret|api[_-]?key|dev[_-]?key|token"
    r"|session[_-]?id|registration[_-]?id|private[_-]?key"
)
# Any identifier prefix is allowed (client_secret, csrftoken, X-Redmine-API-Key,
# JSESSIONID), but the key must end right before the separator.
_ASSIGNMENT_RE = re.compile(
    rf"""(?<![\w-])[\w-]*?(?:{_KEYS})['"]?\s*[:=]\s*"""
    r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[^\s,;'"{}\[\]()\\&]+)""",
    re.IGNORECASE,
)
# Chinese labels ("密碼：abc123", "帳密 admin/abc123"). Only an ASCII value counts, so
# prose such as "密碼：見附件" or "密碼是否正確" is not a hit.
_CHINESE_LABEL_RE = re.compile(
    r"(?:帳號密碼|账号密码|帳密|账密|密碼|密码|口令|金鑰|密鑰|密钥|權杖|令牌)"
    r"\s*(?:[:：=]|為|为|是)\s*([!-~]+)"
)
# Without a separator only the account/password pair form counts ("帳密 admin/abc123"),
# so prose like "修改密碼 API 失敗" is not a hit.
_CHINESE_PAIR_RE = re.compile(r"(?:帳號密碼|账号密码|帳密|账密)\s+([!-~]+/[!-~]+)")
# A JWT is header.payload.signature, each base64url; header and payload start with eyJ ('{"').
_JWT_RE = re.compile(r"(?<![\w-])eyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}")
_AUTH_SCHEME_RE = re.compile(r"\b(?:Bearer|Basic|Digest)\s+([A-Za-z0-9._~+/=-]{8,})")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PLACEHOLDER_RE = re.compile(
    r"""^(?:
        |[*xX.•_-]+                         # *****, xxx, ...
        |<[^<>]*>                           # <password>
        |\$\{?\w+\}?|\$\(.*\)|%\w+%          # $EMS_PASS, ${X}, $(cmd), %X%
        |.*\{[^{}]*\}.*                     # {password}, f-string or template slot
        |(?:os\.)?(?:environ|getenv)\b.*    # os.environ[...], os.getenv(...)
        |none|null|nil|true|false|redacted|masked|hidden|changeme|placeholder|n/a
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
# Python code reads unquoted values as expressions (password=args.password), so
# only string literals count there.
_LITERAL_ONLY_SUFFIXES = {".py"}


@dataclass(frozen=True)
class SecretFinding:
    line: int
    rule: str


def _is_placeholder(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        stripped = stripped[1:-1].strip()
    return bool(_PLACEHOLDER_RE.match(stripped))


def _line_findings(line: str, *, literal_only: bool) -> list[str]:
    rules: list[str] = []
    if _PRIVATE_KEY_RE.search(line):
        rules.append("private-key-block")
    for match in _ASSIGNMENT_RE.finditer(line):
        value = match.group(1)
        if literal_only and value[0] not in "\"'":
            continue
        if not _is_placeholder(value):
            rules.append("credential-assignment")
            break
    # Chinese labels count in .py too: they appear in comments and docstrings.
    # In "account/password" only the part after the last slash is the secret.
    if any(
        not (_is_placeholder(match.group(1)) or _is_placeholder(match.group(1).rsplit("/", 1)[-1]))
        for pattern in (_CHINESE_LABEL_RE, _CHINESE_PAIR_RE)
        for match in pattern.finditer(line)
    ):
        rules.append("chinese-credential-label")
    if _JWT_RE.search(line):
        rules.append("jwt")
    if any(not _is_placeholder(match.group(1)) for match in _AUTH_SCHEME_RE.finditer(line)):
        rules.append("authorization-header")
    for rule, pattern in (("password-flag", _LONG_PASSWORD_FLAG_RE), ("sshpass", _SSHPASS_RE)):
        if any(not _is_placeholder(match.group(2)) for match in pattern.finditer(line)):
            rules.append(rule)
    if _USER_FLAG_RE.search(line) and any(
        not _is_placeholder(match.group(2)) for match in _SHORT_PASSWORD_FLAG_RE.finditer(line)
    ):
        rules.append("password-flag")
    if any(not _is_placeholder(match.group(2)) for match in _URL_USERINFO_RE.finditer(line)):
        rules.append("url-userinfo")
    if any(secret in line for secret in _known_secret_values()):
        rules.append("configured-credential")
    return list(dict.fromkeys(rules))


def scan_text(text: str, *, suffix: str = "") -> list[SecretFinding]:
    literal_only = suffix.casefold() in _LITERAL_ONLY_SUFFIXES
    findings: list[SecretFinding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        findings.extend(
            SecretFinding(line=number, rule=rule)
            for rule in _line_findings(line, literal_only=literal_only)
        )
    return findings


def describe_findings(findings: list[SecretFinding]) -> str:
    shown = ", ".join(f"line {item.line}: {item.rule}" for item in findings[:MAX_REPORTED_FINDINGS])
    hidden = len(findings) - MAX_REPORTED_FINDINGS
    return shown + (f", and {hidden} more" if hidden > 0 else "")
