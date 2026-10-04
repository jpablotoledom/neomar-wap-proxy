#!/usr/bin/env python3
"""
Neomar WAP Proxy Gateway
=========================

A minimal WAP 1.x gateway that lets the "Neomar" / "Rover 1.5" micro-browser
bundled on some classic Palm OS devices browse live web content again, by
reimplementing the (partially undocumented) WTP/WSP dialect it speaks over
UDP, fetching real pages over HTTP, and compiling WML into WBXML on the fly.

See ../docs/PROTOCOL.md for a full write-up of the wire protocol this file
implements, and ../README.md for setup instructions.

This module has no third-party dependencies (standard library only).
"""

import re
import socket
import sys
import urllib.request
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 49300            # Fixed port Rover 1.5 targets (see docs/PROTOCOL.md)

USER_AGENT = "Rover 1.5 (Palm; IP; OS v. 3.5.3)"

# URL Rover requests when the user presses "Start" (its hardcoded home page).
# We intercept it locally instead of proxying it anywhere, since the real
# wap.palm.com service has been dead for over two decades.
HOME_URL_MARKER = "wap.palm.com"
HOME_LINK_TARGET = "http://192.168.1.160/"   # <-- change to your own WAP site

# Rover does not reassemble multi-packet (segmented) WTP replies reliably.
# Instead of relying on WTP segmentation, long pages are split server-side
# into several small WML "cards" linked with Prev/Next navigation, each one
# small enough to fit in a single UDP reply. See docs/PROTOCOL.md for how
# this ceiling was determined empirically.
SAFE_PAGE_BYTES = 860           # target max size (bytes) of a single WBXML page
TEXT_CHUNK_CHARS = 760          # max characters per split text block

# WBMP images bigger than this are not resized by default (see tools/png2wbmp.py
# for a way to pre-shrink source images instead of scaling them at request time).
MAX_IMAGE_BYTES = 900
TARGET_IMAGE_WIDTH = 100

# --------------------------------------------------------------------------
# WML -> WBXML tag/attribute tables
# (WAP-191-WML tag code page 0, from the WAP Forum specification)
# --------------------------------------------------------------------------

WML_ELEMENTS = {
    "wml": 0x3F, "card": 0x27, "do": 0x28, "onevent": 0x33, "head": 0x2C,
    "template": 0x3B, "access": 0x23, "meta": 0x30, "go": 0x2B, "prev": 0x32,
    "refresh": 0x36, "noop": 0x31, "postfield": 0x21, "setvar": 0x3E,
    "select": 0x37, "optgroup": 0x34, "option": 0x35, "input": 0x2F,
    "fieldset": 0x2A, "timer": 0x3C, "img": 0x2E, "anchor": 0x22, "a": 0x1C,
    "table": 0x1F, "tr": 0x1E, "td": 0x1D, "em": 0x29, "strong": 0x39,
    "b": 0x24, "i": 0x2D, "u": 0x3D, "big": 0x25, "small": 0x38, "p": 0x20,
    "br": 0x26,
}

WML_ATTRS = {
    "accept-charset": 0x05, "accesskey": 0x5E, "align": 0x52, "alt": 0x0C,
    "class": 0x54, "columns": 0x53, "content": 0x0D, "domain": 0x0F,
    "enctype": 0x5F, "format": 0x12, "height": 0x13, "href": 0x4A,
    "hspace": 0x14, "http-equiv": 0x5A, "id": 0x55, "ivalue": 0x15,
    "iname": 0x16, "label": 0x18, "localsrc": 0x19, "maxlength": 0x1A,
    "method": None, "mode": None, "multiple": None, "name": 0x21,
    "newcontext": None, "onenterbackward": 0x25, "onenterforward": 0x26,
    "onpick": 0x24, "ontimer": 0x27, "optional": None, "path": 0x2A,
    "scheme": 0x2E, "sendreferer": None, "size": 0x31, "src": 0x32,
    "ordered": None, "tabindex": 0x35, "title": 0x36, "type": 0x37,
    "value": 0x4D, "vspace": 0x4E, "width": 0x4F, "xml:lang": 0x50,
    "xml:space": None,
}

WBXML_TOKEN_END = 0x01
WBXML_TOKEN_STR_I = 0x03
WBXML_VERSION = 0x01
WBXML_PUBLIC_ID_WML11 = 0x04
WBXML_CHARSET_UTF8 = 0x6A

# WSP well-known content type codes (WAP Forum WINA registry)
CONTENT_TYPE_WMLC = 0x14        # application/vnd.wap.wmlc (compiled WML)
CONTENT_TYPE_WBMP = 0x21        # image/vnd.wap.wbmp


# --------------------------------------------------------------------------
# WML -> WBXML compiler
# --------------------------------------------------------------------------

def wbxml_string(s: str) -> bytes:
    """Encode an inline string (STR_I token, NUL-terminated)."""
    return bytes([WBXML_TOKEN_STR_I]) + s.encode("utf-8", errors="replace") + b"\x00"


def compile_element(el: ET.Element) -> bytes:
    """Recursively compile one WML element (and its children) to WBXML bytes."""
    tag = el.tag.lower()
    tag_code = WML_ELEMENTS.get(tag)
    if tag_code is None:
        # Unknown tag: skip the tag itself but keep its text/children.
        out = b""
        if el.text and el.text.strip():
            out += wbxml_string(el.text.strip())
        for child in el:
            out += compile_element(child)
            if child.tail and child.tail.strip():
                out += wbxml_string(child.tail.strip())
        return out

    attrs = b""
    for k, v in el.attrib.items():
        acode = WML_ATTRS.get(k.lower())
        if acode is None:
            continue
        attrs += bytes([acode]) + wbxml_string(v)
    has_attrs = len(attrs) > 0

    children_bytes = b""
    if el.text and el.text.strip():
        children_bytes += wbxml_string(el.text.strip())
    for child in el:
        children_bytes += compile_element(child)
        if child.tail and child.tail.strip():
            children_bytes += wbxml_string(child.tail.strip())
    has_content = len(children_bytes) > 0

    flag = 0
    if has_attrs:
        flag |= 0x80
    if has_content:
        flag |= 0x40

    out = bytes([tag_code | flag])
    if has_attrs:
        out += attrs + bytes([WBXML_TOKEN_END])
    if has_content:
        out += children_bytes + bytes([WBXML_TOKEN_END])
    return out


def wbxml_header() -> bytes:
    return bytes([WBXML_VERSION, WBXML_PUBLIC_ID_WML11, WBXML_CHARSET_UTF8, 0x00])


def parse_wml_root(wml_text: str) -> ET.Element:
    """Strip the XML prolog / DOCTYPE (ElementTree chokes on the external
    DTD reference) and parse the remaining WML as XML."""
    cleaned = re.sub(r"<\?xml[^>]*\?>", "", wml_text)
    cleaned = re.sub(r"<!DOCTYPE[^>]*>", "", cleaned)
    cleaned = cleaned.replace("&nbsp;", "&#160;")
    return ET.fromstring(cleaned.strip())


# --------------------------------------------------------------------------
# Pagination
#
# Rover only reliably renders a single-packet WTP reply. Instead of
# implementing full WTP segmentation with acknowledgements, long pages are
# split into several linked "cards", each compiled independently and kept
# under SAFE_PAGE_BYTES. See docs/PROTOCOL.md for how this limit was found.
# --------------------------------------------------------------------------

def compile_card(title: str, blocks) -> bytes:
    card = ET.Element("card", {"id": "p", "title": title[:30]})
    for b in blocks:
        card.append(b)
    wml = ET.Element("wml")
    wml.append(card)
    return wbxml_header() + compile_element(wml)


def make_paragraph(text: str) -> ET.Element:
    el = ET.Element("p")
    el.text = text
    return el


def make_link(text: str, href: str) -> ET.Element:
    p = ET.Element("p")
    a = ET.SubElement(p, "a", {"href": href})
    a.text = text
    return p


def flatten_blocks(card: ET.Element, char_chunk: int = TEXT_CHUNK_CHARS):
    """Turn card's children into a flat list of "atomic" WML elements,
    splitting the text of any child too large to fit in one page."""
    blocks = []
    for child in list(card):
        if len(compile_element(child)) <= SAFE_PAGE_BYTES:
            blocks.append(child)
            continue
        # Too big on its own: split its text into chunks (losing nested
        # inline markup inside it - an acceptable trade-off here).
        text = (child.text or "").strip()
        if not text:
            continue
        for i in range(0, len(text), char_chunk):
            blocks.append(make_paragraph(text[i:i + char_chunk]))
    return blocks


def get_page_param(uri: str) -> int:
    m = re.search(r"[?&]__page=(\d+)", uri)
    return int(m.group(1)) if m else 0


def set_page_param(uri: str, page: int) -> str:
    base = uri.split("#")[0]
    base = re.sub(r"[?&]__page=\d+", "", base)
    if page <= 0:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}__page={page}"


def paginate(blocks, title: str, base_uri: str, page: int) -> bytes:
    """Pack 'blocks' into pages <= SAFE_PAGE_BYTES and return the WBXML for
    the requested page, with Prev/Next navigation links appended."""
    pages = []
    current = []
    for blk in blocks:
        trial = current + [blk]
        if len(compile_card(title, trial)) > SAFE_PAGE_BYTES and current:
            pages.append(current)
            current = [blk]
        else:
            current.append(blk)
    if current or not pages:
        pages.append(current)

    page = max(0, min(page, len(pages) - 1))
    content = list(pages[page])

    nav = []
    if page > 0:
        nav.append(make_link("<< Prev", set_page_param(base_uri, page - 1)))
    if page < len(pages) - 1:
        nav.append(make_link("Next >>", set_page_param(base_uri, page + 1)))
    content = content + nav

    return compile_card(f"{title} ({page + 1}/{len(pages)})", content)


def wml_to_paginated_wbxml(wml_text: str, uri: str) -> bytes:
    root = parse_wml_root(wml_text)
    card = root.find(".//card")
    if card is None:
        card = root
    title = card.attrib.get("title", "")
    blocks = flatten_blocks(card)
    page = get_page_param(uri)
    return paginate(blocks, title, uri, page)


# --------------------------------------------------------------------------
# WBMP (Wireless Bitmap) codec
#
# WBMP is a trivial 1-bit-per-pixel format - it's already WAP's native image
# format, so it is proxied through untouched. wbmp_downscale_ratio() is kept
# around for on-the-fly resizing if you ever need it (see docs/PROTOCOL.md);
# by default images are expected to already be pre-shrunk with
# tools/png2wbmp.py, which produces better-looking dithering than a live
# per-request resize.
# --------------------------------------------------------------------------

def wbmp_read_mbuint(data: bytes, pos: int):
    """Read a WBXML/WBMP multi-byte uint (7 bits/byte, MSB = continuation)."""
    val = 0
    while True:
        b = data[pos]
        pos += 1
        val = (val << 7) | (b & 0x7f)
        if not (b & 0x80):
            break
    return val, pos


def wbmp_write_mbuint(val: int) -> bytes:
    out = [val & 0x7f]
    val >>= 7
    while val:
        out.insert(0, (val & 0x7f) | 0x80)
        val >>= 7
    return bytes(out)


def wbmp_decode(data: bytes):
    pos = 2  # type field + fix header field, both assumed 0 (level-0 WBMP)
    width, pos = wbmp_read_mbuint(data, pos)
    height, pos = wbmp_read_mbuint(data, pos)
    row_bytes = (width + 7) // 8
    pixels = [[0] * width for _ in range(height)]
    for y in range(height):
        for xb in range(row_bytes):
            byte = data[pos]
            pos += 1
            for bit in range(8):
                x = xb * 8 + bit
                if x < width:
                    pixels[y][x] = (byte >> (7 - bit)) & 1
    return width, height, pixels


def wbmp_encode(width: int, height: int, pixels) -> bytes:
    out = bytearray([0, 0]) + bytearray(wbmp_write_mbuint(width)) + bytearray(wbmp_write_mbuint(height))
    row_bytes = (width + 7) // 8
    for y in range(height):
        for xb in range(row_bytes):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                v = pixels[y][x] if x < width else 1
                byte = (byte << 1) | v
            out.append(byte)
    return bytes(out)


def wbmp_downscale_ratio(width: int, height: int, pixels, scale: float):
    """Box-filter area averaging + Floyd-Steinberg dithering, so gradients
    survive the drop to 1-bit reasonably well instead of looking speckled."""
    nw, nh = max(1, round(width * scale)), max(1, round(height * scale))

    gray = [[0.0] * nw for _ in range(nh)]
    for ny in range(nh):
        y0 = int(ny / scale)
        y1 = min(height, max(y0 + 1, int((ny + 1) / scale)))
        for nx in range(nw):
            x0 = int(nx / scale)
            x1 = min(width, max(x0 + 1, int((nx + 1) / scale)))
            total = 0
            count = 0
            for yy in range(y0, y1):
                row = pixels[yy]
                for xx in range(x0, x1):
                    total += row[xx]
                    count += 1
            gray[ny][nx] = total / count if count else 1.0

    npix = [[0] * nw for _ in range(nh)]
    for ny in range(nh):
        for nx in range(nw):
            old = gray[ny][nx]
            new = 1 if old >= 0.5 else 0
            npix[ny][nx] = new
            err = old - new
            if nx + 1 < nw:
                gray[ny][nx + 1] += err * 7 / 16
            if ny + 1 < nh:
                if nx > 0:
                    gray[ny + 1][nx - 1] += err * 3 / 16
                gray[ny + 1][nx] += err * 5 / 16
                if nx + 1 < nw:
                    gray[ny + 1][nx + 1] += err * 1 / 16
    return nw, nh, npix


def shrink_wbmp_to_fit(data: bytes, target_width: int, max_bytes: int) -> bytes:
    width, height, pixels = wbmp_decode(data)
    if width <= target_width and len(data) <= max_bytes:
        return data

    scale = min(target_width / width, 1.0)
    nw, nh, npix = wbmp_downscale_ratio(width, height, pixels, scale)
    enc = wbmp_encode(nw, nh, npix)
    if len(enc) <= max_bytes:
        return enc

    lo, hi = 0.02, scale
    best = None
    for _ in range(14):
        mid = (lo + hi) / 2
        nw, nh, npix = wbmp_downscale_ratio(width, height, pixels, mid)
        enc = wbmp_encode(nw, nh, npix)
        if len(enc) <= max_bytes:
            best = enc
            lo = mid
        else:
            hi = mid
    return best if best is not None else wbmp_encode(1, 1, [[1]])


# --------------------------------------------------------------------------
# HTTP fetching
# --------------------------------------------------------------------------

def fetch_text(url: str, user_agent: str = USER_AGENT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=10) as resp:
        charset = resp.headers.get_content_charset() or "iso-8859-1"
        return resp.read().decode(charset, errors="replace")


def fetch_binary(url: str, user_agent: str = USER_AGENT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


# --------------------------------------------------------------------------
# WTP / WSP framing
#
# See docs/PROTOCOL.md for the full reverse-engineering notes behind these
# byte layouts. In short: Rover speaks WTP (RFC-ish, WAP-224-WTP) wrapping
# WSP (WAP-230-WSP) over plain UDP, on a fixed non-standard port, with a
# couple of quirks (an extra header byte, and requiring the response's
# retransmission-indicator bit to mirror the request's) not obviously
# implied by the spec text alone.
# --------------------------------------------------------------------------

def parse_wtp_header(data: bytes):
    """Return (pdu_type, tid) from the first 3 bytes of a WTP PDU."""
    pdu_type = data[0] >> 3
    tid = (data[1] << 8) | data[2]
    return pdu_type, tid


def parse_get_request(data: bytes) -> str:
    """Extract the requested URI from a WSP Get PDU (after the 4-byte WTP
    Invoke header): byte[4] = WSP PDU type (0x40 = GET), byte[5] = uri-len
    (uintvar, assumed < 128 here), followed by the URI itself."""
    uri_len = data[5]
    return data[6:6 + uri_len].decode("latin-1", errors="replace")


def build_wtp_result(invoke_header4: bytes) -> bytes:
    """Build a 4-byte WTP Result header that mirrors the Invoke's flags/TID
    byte-for-byte, only flipping the PDU type field to Result (2)."""
    flags = invoke_header4[0] & 0x07
    b0 = (2 << 3) | flags
    return bytes([b0]) + invoke_header4[1:4]


def build_wsp_reply(body: bytes, status: int = 0x20, content_type: int = CONTENT_TYPE_WMLC) -> bytes:
    """Build a WSP Reply PDU: type, status, headers-length (uintvar),
    headers (just Content-Type here, as a well-known short-integer), body."""
    pdu_type = 0x04  # Reply
    headers = bytes([0x80 | content_type])
    headers_len = bytes([len(headers)])
    return bytes([pdu_type, status]) + headers_len + headers + body


# --------------------------------------------------------------------------
# Request handling
# --------------------------------------------------------------------------

def build_home_page() -> str:
    return f"""<?xml version="1.0"?>
<!DOCTYPE wml PUBLIC "-//WAPFORUM//DTD WML 1.1//EN" "http://www.wapforum.org/DTD/wml_1.1.xml">
<wml><card id="c1" title="OK"><p>Connected. Try: <a href="{HOME_LINK_TARGET}">Go</a></p></card></wml>"""


def handle_request(uri: str) -> bytes:
    """Fetch/build the WSP reply payload (everything after the WTP header)
    for a given requested URI. Raises on fetch/parse errors; the caller is
    expected to catch and turn that into an error card."""
    fetch_uri = set_page_param(uri, 0)

    if HOME_URL_MARKER in uri:
        wmlc = wml_to_paginated_wbxml(build_home_page(), uri)
        return build_wsp_reply(wmlc, content_type=CONTENT_TYPE_WMLC)

    if re.search(r"\.wbmp([?#]|$)", uri, re.IGNORECASE):
        img_bytes = fetch_binary(fetch_uri)
        return build_wsp_reply(img_bytes, content_type=CONTENT_TYPE_WBMP)

    if re.search(r"\.(gif|png|jpe?g)([?#]|$)", uri, re.IGNORECASE):
        wml = """<?xml version="1.0"?>
<!DOCTYPE wml PUBLIC "-//WAPFORUM//DTD WML 1.1//EN" "http://www.wapforum.org/DTD/wml_1.1.xml">
<wml><card id="i1" title="Image"><p>(format not supported by WAP 1.x - use WBMP)</p></card></wml>"""
        wmlc = wml_to_paginated_wbxml(wml, uri)
        return build_wsp_reply(wmlc, content_type=CONTENT_TYPE_WMLC)

    body_wml = fetch_text(fetch_uri)
    wmlc = wml_to_paginated_wbxml(body_wml, uri)
    return build_wsp_reply(wmlc, content_type=CONTENT_TYPE_WMLC)


def build_error_page(uri: str, exc: Exception) -> bytes:
    wml = f"""<?xml version="1.0"?>
<!DOCTYPE wml PUBLIC "-//WAPFORUM//DTD WML 1.1//EN" "http://www.wapforum.org/DTD/wml_1.1.xml">
<wml><card id="e1" title="Error"><p>{type(exc).__name__}: {str(exc)[:100]}</p></card></wml>"""
    wmlc = wml_to_paginated_wbxml(wml, uri)
    return build_wsp_reply(wmlc, content_type=CONTENT_TYPE_WMLC)


def serve_forever(host: str = LISTEN_HOST, port: int = LISTEN_PORT):
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind((host, port))
    print(f"Listening on UDP {host}:{port} ...", flush=True)

    while True:
        data, addr = srv.recvfrom(65535)
        if len(data) < 6:
            continue

        pdu_type, tid = parse_wtp_header(data)
        if pdu_type != 1:  # only handle Invoke (a client request)
            continue

        uri = ""
        try:
            uri = parse_get_request(data)
            print(f"[{addr}] GET {uri}", flush=True)
            wsp_payload = handle_request(uri)
        except Exception as exc:
            print(f"[{addr}] ERROR: {exc!r}", flush=True)
            wsp_payload = build_error_page(uri, exc)

        response = build_wtp_result(data[:4]) + wsp_payload
        srv.sendto(response, addr)


if __name__ == "__main__":
    try:
        serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
