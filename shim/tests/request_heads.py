"""The request heads the raw tests send to the solo shim and to the pool host (funcd ADR-0049)."""

import pytest

CONTENT_TYPE_LINES = [
    pytest.param(b"Content-Type: message/rfc822", id="content-type-message"),
    pytest.param(b"Content-Type: multipart/mixed; boundary=x", id="content-type-multipart"),
    pytest.param(
        b"Content-Type: multipart/mixed; boundary*=undefined''x", id="content-type-charset-undefined"
    ),
    pytest.param(b"Content-Type: multipart/mixed; boundary*=idna''x", id="content-type-charset-idna"),
    pytest.param(b"Content-Type: multipart/mixed; boundary*=a%00b''x", id="content-type-charset-nul"),
    pytest.param(
        b"Content-Type: multipart/mixed; boundary*%b=a" % (b"9" * 5000), id="content-type-long-section"
    ),
    pytest.param(b"Content-Type: MULTIPART/MIXED; BOUNDARY*=undefined''x", id="content-type-upper-case"),
    pytest.param(b"Content-Type: multipart/mixed; boundary*=x; boundary*0=y", id="content-type-two-forms"),
]


def post_with_line(path: bytes, line: bytes) -> bytes:
    """A POST to *path* whose head holds *line*; an echo handler answers its body with 42."""
    return b'POST %b HTTP/1.1\r\nHost: x\r\n%b\r\nContent-Length: 11\r\n\r\n{"data":42}' % (path, line)
