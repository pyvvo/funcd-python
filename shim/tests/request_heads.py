"""The request heads the raw tests send to the solo shim and to the pool host (funcd ADR-0049)."""

import pytest


def long_field(length: int) -> bytes:
    """A field line of *length* bytes, without its CRLF."""
    return b"X: " + b"y" * (length - 3)


def with_line(line: bytes) -> bytes:
    """What follows a request line: a head that holds *line* and frames a body."""
    return b"Host: x\r\n%b\r\nContent-Length: 2\r\n\r\n{}" % line


REFUSED_HEADS = [
    pytest.param(with_line(b"X-A : 1"), id="space-before-colon"),
    pytest.param(with_line(b"X-A\t: 1"), id="tab-before-colon"),
    pytest.param(b"Host: x\r\nContent-Length : 2\r\n\r\n{}", id="length-space-before-colon"),
    pytest.param(b"Host: x\r\nTransfer-Encoding : chunked\r\n\r\n0\r\n\r\n", id="coding-space-before-colon"),
    pytest.param(with_line(b"X-A"), id="no-colon"),
    pytest.param(with_line(b"X-A B"), id="no-colon-with-space"),
    pytest.param(with_line(b"X A: 1"), id="space-in-name"),
    pytest.param(with_line(b"From x"), id="from-no-colon"),
    pytest.param(with_line(b": 1"), id="no-name"),
    pytest.param(with_line(b"X\0A: 1"), id="name-nul"),
    pytest.param(with_line(b"X\xffA: 1"), id="name-high-byte"),
    *[pytest.param(with_line(b"X%cA: 1" % c), id=f"name-delimiter-{c:02x}") for c in b'"(),/;<=>?@[\\]{}'],
    pytest.param(with_line(b"X-A: 1\r\n 2"), id="fold-space"),
    pytest.param(with_line(b"X-A: 1\r\n\t2"), id="fold-tab"),
    pytest.param(with_line(b"X-A: 1\r\n "), id="fold-only-a-space"),
    pytest.param(b" X-A: 1\r\nHost: x\r\n\r\n", id="fold-first-line"),
    pytest.param(b"Host: x\r\nX-A: 1\r\n Content-Length: 2\r\n\r\n{}", id="fold-length"),
    pytest.param(b"Host: x\r\nX-A: 1\r\n Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n", id="fold-coding"),
    pytest.param(b"Host: x\nContent-Length: 2\r\n\r\n{}", id="first-line-ends-lf"),
    pytest.param(b"Host: x\r\nContent-Length: 2\n\r\n{}", id="last-line-ends-lf"),
    pytest.param(b"Host: x\r\nContent-Length: 2\r\n\n{}", id="blank-line-is-lf"),
    pytest.param(b"Host: x\nContent-Length: 2\n\n{}", id="every-line-ends-lf"),
    pytest.param(with_line(b"X-A: 1\r2"), id="value-bare-cr"),
    pytest.param(b"Host: x\r\nX-A: 1\rContent-Length: 2\r\n\r\n{}", id="line-ends-cr"),
    pytest.param(with_line(b"X-A: 1\r"), id="line-ends-cr-cr-lf"),
    pytest.param(b"Host: x\r\n\r\r\n", id="blank-line-cr-cr-lf"),
    pytest.param(b"Host: x\r\n \r\n\r\n", id="blank-line-with-space"),
    *[
        pytest.param(with_line(b"X-A: 1%c2" % c), id=f"value-control-{c:02x}")
        for c in (*range(0x09), *range(0x0A, 0x20), 0x7F)
    ],
    pytest.param(b"Host: x\r\nExpect: 100-continue\r\nX-A : 1\r\n\r\n", id="after-expect"),
]

HEADS_PAST_THE_LIMITS = [
    # These end at the refused line, so the server closes with nothing unread and the client sees no reset.
    pytest.param(b"Host: x\r\n%b\r\n" % long_field(65537 - 2), id="line"),
    pytest.param(b"Host: x\r\n" + b"X-A: 1\r\n" * 99 + b"\r\n", id="lines"),
]

REFUSED_REQUEST_LINES = [
    pytest.param(b"GET /health/liveness HTTP/1.1\nHost: x\r\n\r\n", id="ends-lf"),
    pytest.param(b"GET /health/liveness HTTP/1.1\nHost: x\n\n", id="ends-lf-and-every-line"),
    pytest.param(b"POST / HTTP/1.1\nHost: x\r\nContent-Length: 2\r\n\r\n{}", id="post-ends-lf"),
    pytest.param(b"GET /health/liveness\n\n", id="no-version-ends-lf"),
    pytest.param(b"GET /health/liveness HTTP/1.1\r\r\nHost: x\r\n\r\n", id="ends-cr-cr-lf"),
    pytest.param(b" GET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="leading-space"),
    pytest.param(b"\tGET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="leading-tab"),
    pytest.param(b"GET /health/liveness HTTP/1.1 \r\nHost: x\r\n\r\n", id="trailing-space"),
    pytest.param(b"GET /health/liveness \r\n\r\n", id="no-version-trailing-space"),
    pytest.param(b"GET /health/liveness x HTTP/1.1\r\nHost: x\r\n\r\n", id="four-words"),
    pytest.param(b"G(T /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="method-not-a-token"),
    *[
        pytest.param(b"GET%c/health/liveness HTTP/1.1\r\nHost: x\r\n\r\n" % c, id=f"after-method-{c:02x}")
        for c in b"\t\x0b\x0c\r\x1c\x85\xa0"
    ],
    *[
        pytest.param(b"GET /health/liveness%cHTTP/1.1\r\nHost: x\r\n\r\n" % c, id=f"before-version-{c:02x}")
        for c in b"\t\x0b\x0c\r\x1c\x85\xa0"
    ],
    *[
        pytest.param(b"GET /health/liveness%c HTTP/1.1\r\nHost: x\r\n\r\n" % c, id=f"target-byte-{c:02x}")
        for c in b"\x00\x01\x1f\x7f\x80\xff"
    ],
]

REFUSED_VERSIONS = [
    pytest.param(version, id=version.decode())
    for version in (
        b"HTTP/0.9",
        b"HTTP/1.2",
        b"HTTP/1.01",
        b"HTTP/1x1",
        b"HTTP/2.0",
        b"HTTP/1",
        b"HTTP/1.",
        b"HTTP/11",
        b"HTTP/1.1.1",
        b"http/1.1",
        b"FOO/1.1",
    )
]

TAKEN_REQUEST_LINES = [
    pytest.param(b"GET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="plain"),
    pytest.param(b"GET  /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="spaces-before-target"),
    pytest.param(b"GET /health/liveness   HTTP/1.1\r\nHost: x\r\n\r\n", id="spaces-before-version"),
    pytest.param(b"\r\nGET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="after-an-empty-line"),
    pytest.param(b"\r\n\r\nGET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="after-empty-lines"),
    pytest.param(b"\nGET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="after-a-lf"),
    pytest.param(b"\r\r\nGET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="after-cr-cr-lf"),
    pytest.param(b"\rGET /health/liveness HTTP/1.1\r\nHost: x\r\n\r\n", id="after-a-cr"),
]

CUT_HEADS = [
    pytest.param(b"POST / HTTP/1.1", id="in-the-request-line"),
    pytest.param(b"GET /health/liveness", id="in-a-request-line-with-no-version"),
    pytest.param(b"POST / HTTP/1.1\r\n", id="after-the-request-line"),
    pytest.param(b"POST / HTTP/1.1\r\nHost: x", id="in-a-line"),
    pytest.param(b"POST / HTTP/1.1\r\nHost: x\r", id="before-a-lf"),
    pytest.param(b"POST / HTTP/1.1\r\nHost: x\r\n", id="before-the-blank-line"),
    pytest.param(b"POST / HTTP/1.1\r\nHost: x\r\n\r", id="in-the-blank-line"),
    pytest.param(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n", id="after-the-length"),
    pytest.param(b"GET /health/liveness HTTP/1.1\r\nHost: x\r\n", id="get-before-the-blank-line"),
]

TAKEN_LINES = [
    pytest.param(b"X-A:1", id="no-space-after-colon"),
    pytest.param(b"X-A:", id="value-empty"),
    pytest.param(b"X-A:  \t ", id="value-only-whitespace"),
    pytest.param(b"X-A:\t1", id="value-after-tab"),
    pytest.param(b"X-A: 1 \t", id="value-then-whitespace"),
    pytest.param(b"X-A: 1\t2 3", id="value-with-tab-and-space"),
    pytest.param(b"X-A: a:b:c", id="value-with-colons"),
    pytest.param(b"X-A: !~\x80\xff", id="value-first-and-last-bytes"),
    pytest.param(b"!#$%&'*+-.^_`|~09AZaz: 1", id="name-every-tchar"),
    pytest.param(b"From: a@b", id="from"),
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
    pytest.param(b"X-A: 1\r\nX-A: 2", id="name-twice"),
    pytest.param(b"X-Funcd-Timeout-Ms: %b" % (b"1" * 4301), id="timeout-past-what-int-reads"),
]

LINES_AT_THE_LIMITS = [
    pytest.param(long_field(65536 - 2), id="line"),
    pytest.param(b"\r\n".join([b"X-A: 1"] * 97), id="lines"),
]

METHODS_OF_TOKEN_BYTES = [
    pytest.param(b"M-SEARCH", id="with-a-dash"),
    pytest.param(b"G!T", id="with-a-symbol"),
    pytest.param(b"get", id="lower-case"),
]

TARGETS_OF_VISIBLE_BYTES = [
    pytest.param(b"/!~", id="first-and-last-bytes"),
    pytest.param(b"/?a=b", id="query"),
    pytest.param(b'/"', id="quote"),
    pytest.param(b"/<>", id="angle-brackets"),
]

REFUSED_LENGTHS = [
    # A tab after the length: the Node of funcd's runtime image refuses it (llhttp 9.3; llhttp 9.4 takes it).
    pytest.param(b"11\t", id="then-tab"),
    pytest.param(b"11 \t", id="then-space-tab"),
    pytest.param(b"11\t ", id="then-tab-space"),
    pytest.param(b"18446744073709551616", id="past-64-bits"),
    pytest.param(b"00018446744073709551616", id="past-64-bits-after-zeros"),
    pytest.param(b"100000000000000000000", id="21-digits"),
    pytest.param(b"1" * 4301, id="past-what-int-reads"),
]

TAKEN_LENGTHS = [
    pytest.param(b"11 ", id="then-space"),
    pytest.param(b"11  ", id="then-spaces"),
    pytest.param(b"\t11", id="after-tab"),
    pytest.param(b"0011", id="after-zeros"),
    pytest.param(b"0" * 5000 + b"11", id="after-many-zeros"),
]

LENGTHS_PAST_ONE_READ = [
    pytest.param(1 << 40, id="2^40"),
    pytest.param(1 << 63, id="2^63"),
    pytest.param((1 << 64) - 1, id="largest"),
]


def post_with_line(path: bytes, line: bytes) -> bytes:
    """A POST to *path* whose head holds *line*; an echo handler answers its body with 42."""
    return b'POST %b HTTP/1.1\r\nHost: x\r\n%b\r\nContent-Length: 11\r\n\r\n{"data":42}' % (path, line)


def post_with_length(path: bytes, length: bytes) -> bytes:
    """An 11-byte POST to *path* under a Content-Length of *length*; an echo handler answers it with 42."""
    return b'POST %b HTTP/1.1\r\nHost: x\r\nContent-Length: %b\r\n\r\n{"data":42}' % (path, length)
