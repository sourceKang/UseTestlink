"""Corporate TestLink custom endpoints (CA_*.php) used for Redmine bug linking.

TestLink 1.9.16 at the corporate site links bugs through the custom endpoint
``CA_set_result.php`` (table ``execution_spr``) instead of native XML-RPC. The
endpoint is a plain HTTP GET without a devKey: it appends one execution with the
given status and links the comma-separated ``issue_id`` values. It never reports
whether the link was stored, so callers must treat a reached endpoint as
"submitted", not as a verified link.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from testlink_agent_core.errors import TestLinkError


TESTER_ID_ENV = "TESTLINK_TESTER_ID"
WEB_BASE_URL_ENV = "TESTLINK_WEB_BASE_URL"
SET_RESULT_ENDPOINT = "CA_set_result.php"
RESPONSE_EXCERPT_LIMIT = 2000

# Internal host: bypass any corporate proxy picked up from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def configured_tester_id() -> str:
    """Return the TestLink user ID recorded as the tester; there is no default."""
    value = os.environ.get(TESTER_ID_ENV, "").strip()
    if not value:
        raise TestLinkError(
            f"{TESTER_ID_ENV} is required for bug linking; set it to your own TestLink user ID.",
            code="CONFIG_MISSING",
        )
    if not value.isdigit():
        raise TestLinkError(f"{TESTER_ID_ENV} must be a numeric TestLink user ID.", code="CONFIG_INVALID")
    return value


def web_base_url(xmlrpc_url: str) -> str:
    """Derive the TestLink web root from TESTLINK_URL, or use an explicit override on the same host."""
    configured = urlsplit(str(xmlrpc_url or "").strip())
    if configured.scheme not in {"http", "https"} or not configured.netloc:
        raise TestLinkError("TESTLINK_URL must be an http(s) URL to derive the TestLink web root.")
    override = os.environ.get(WEB_BASE_URL_ENV, "").strip()
    if override:
        parsed = urlsplit(override)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != configured.netloc.casefold():
            raise TestLinkError(
                f"{WEB_BASE_URL_ENV} must be an http(s) URL on the same host as TESTLINK_URL.",
                code="CONFIG_INVALID",
            )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    path = configured.path.rstrip("/")
    for marker in ("/lib/api/", "/index.php"):
        if marker in path:
            path = path.split(marker, 1)[0]
            break
    else:
        if path.casefold().endswith(".php"):
            path = path.rsplit("/", 1)[0]
    return urlunsplit((configured.scheme, configured.netloc, path.rstrip("/"), "", ""))


def set_result(base_url: str, params: dict[str, str], *, timeout: int) -> str:
    """Call CA_set_result.php once and return the response body."""
    url = f"{base_url.rstrip('/')}/{SET_RESULT_ENDPOINT}?{urllib.parse.urlencode(params)}"
    try:
        with _opener.open(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise TestLinkError(f"{SET_RESULT_ENDPOINT} request failed: {exc}", code="HTTP_ERROR") from exc


def response_reached(body: str) -> bool:
    """CA_set_result.php echoes its inputs; the echoed issue_id shows the request was processed."""
    return "issue_id:" in str(body or "")


def response_excerpt(body: str) -> str:
    return str(body or "").replace("<br>", "\n").strip()[:RESPONSE_EXCERPT_LIMIT]
