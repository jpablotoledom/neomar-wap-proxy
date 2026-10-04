# Protocol Notes: Reverse-Engineering Neomar / Rover 1.5

This document records what was learned, by packet capture and trial and
error, about the WAP transport that the "Neomar" browser (internally named
**Rover 1.5**) bundled with some classic Palm OS software CDs actually
speaks. It is not official documentation - the WAP Forum specs describe the
general shape of the protocol, but Rover deviates from them in a few
specific, undocumented ways that this gateway has to match byte-for-byte.

If you are trying to build a similar bridge for a *different* WAP 1.x
client, read [Compatibility with other clients](#compatibility-with-other-clients)
first - most of what's below is Rover-specific.

## 1. Why DNS/HTTP tricks don't work

Rover's "Start" button sends a request to a **hardcoded IP address and UDP
port**, not a hostname it resolves via DNS. A packet capture on the Palm
side (via a Softick PPP log, see [SETUP.md](SETUP.md)) showed:

```
UDP_IN: 10.0.0.1:1035 -> 165.160.13.20:49300
UDP_IN: 10.0.0.1:1036 -> 165.160.15.20:49300
```

Two different destination IPs, alternated across attempts, always port
**49300** - not the "standard" WAP ports (9200/9201). Both `wap.palm.com`
DNS entries are long dead, and the raw IPs above have been reassigned to
unrelated infrastructure (one now 301-redirects to an unrelated domain).
There is no way to reach the real Neomar backend anymore; the only option
is to impersonate it.

Because the destination is a literal IP, **editing `hosts` or running a
custom DNS resolver has no effect** - Rover never performs a DNS lookup for
this connection. Interception has to happen at the IP/routing layer
instead (see [SETUP.md](SETUP.md) for the `ip addr add` / `route add` /
`arp -s` trick used here).

## 2. Transport: WTP over raw UDP

Once traffic reaches the gateway, the payload turns out to be
[WTP](https://en.wikipedia.org/wiki/Wireless_Transaction_Protocol)
(Wireless Transaction Protocol) carrying a [WSP](https://en.wikipedia.org/wiki/Wireless_Session_Protocol)
(Wireless Session Protocol) message, all in a single unencrypted UDP
datagram - the classic WAP 1.x connectionless stack.

### 2.1 WTP header

The four-byte WTP header on an **Invoke** PDU (a client request) is laid
out, per the WAP-224-WTP spec, as:

| Bits | Field | Notes |
|---|---|---|
| 1 | `con` | TPIs present flag (always 0 observed) |
| 4 | `type` | PDU type; `1` = Invoke, `2` = Result, `6` = Segmented_result |
| 1 | `gtr` | Group trailer |
| 1 | `ttr` | Transmission trailer (last packet of the message) |
| 1 | `rid` | Retransmission indicator |
| 16 | `tid` | Transaction ID |
| 8 | (Invoke only) | version/tidnew/uack/reserved/class packed byte |

A captured real request looked like:

```
0f 00 01 0e 40 14 68 74 74 70 3a 2f 2f ...
^0 ^1 ^2 ^3 ^4 ^5 ^^^^^^^^^^^^^^^^^^^^^
|                 \-- "http://..." (the requested URL)
|                 \- WSP payload starts here
\- WTP header (4 bytes for Invoke)
```

`byte[0] = 0x0f` decodes to `type = 0x0f >> 3 = 1` (Invoke), with the low 3
bits (`gtr=1, ttr=1, rid=1`) as flags. `byte[3]` is technically the last
byte of the Invoke-only trailer field, but in practice it behaves like an
incrementing per-attempt counter (`0x0d`, then `0x0e` on retry) - treat it
as opaque and just **mirror it back verbatim**, don't try to interpret its
sub-fields.

### 2.2 The critical, undocumented quirk

The official WAP-224-WTP spec's **Result** PDU header is only **3 bytes**
(`con+type+gtr+ttr+rid`, then a 16-bit TID - no trailing byte, unlike
Invoke). A strictly spec-compliant 3-byte Result header was tried first and
**Rover silently ignored it** (infinite "loading..."). What actually works
is a **4-byte** Result header that mirrors the Invoke's bytes 1-3
*verbatim*, including that "extra" trailer byte Result isn't supposed to
have:

```python
def build_wtp_result(invoke_header4: bytes) -> bytes:
    flags = invoke_header4[0] & 0x07
    b0 = (2 << 3) | flags          # switch PDU type Invoke(1) -> Result(2)
    return bytes([b0]) + invoke_header4[1:4]   # mirror TID + the extra byte
```

Additionally, the **retransmission indicator (`rid`) bit must be mirrored**
from the request, not cleared. A `rid=0` reply (matching a "clean", known
good Kannel/fakewap.c reference template) was **rejected outright**
(Rover kept retrying forever); mirroring the request's `rid=1` made Rover
accept the reply as a valid match for its Invoke. This suggests Rover's TID
correlation logic keys on more than just the TID field, unlike a
spec-faithful implementation.

**Summary: when building a Result PDU, don't reconstruct the header from
first principles - copy the Invoke's first 4 bytes and only flip the PDU
type nibble.**

### 2.3 WSP payload: Get request

Right after the WTP header, the WSP method PDU (WAP-230-WSP, "Get"):

```
byte[4] = 0x40         # WSP PDU type: Get
byte[5] = uri_len       # uintvar (single byte here, URL always < 128 chars)
byte[6:6+uri_len]       # the URI itself, as plain ASCII
... WSP headers (Accept, User-Agent, etc, WSP-encoded) follow
```

### 2.4 WSP payload: Reply

The WSP **Reply** PDU (per WAP-230-WSP, `TYPE(8,4)`):

```
PDU-Type   (1 byte)   = 0x04
Status     (1 byte)   = 0x20  (=200 OK; WSP status codes are NOT the same
                                numeric space as HTTP - 0x20 happens to
                                equal 32 decimal but represents "200 OK")
Headers-Length (uintvar)  <- easy to miss! Without it, Rover can't tell
                              where headers end and the body begins, and
                              silently corrupts the render ("WML not
                              supported" or garbled output).
Headers    (headers_len bytes) = Content-Type as a well-known short integer,
                                  e.g. 0x80 | 0x14 for application/vnd.wap.wmlc
Data       (rest of the packet) = the WBXML- or WBMP-encoded body
```

```python
def build_wsp_reply(body: bytes, status=0x20, content_type=0x14) -> bytes:
    headers = bytes([0x80 | content_type])
    headers_len = bytes([len(headers)])
    return bytes([0x04, status]) + headers_len + headers + body
```

## 3. Content-Type codes used

From the WAP Forum WINA "WSP Content-Type Numbers" registry:

| Content type | Code |
|---|---|
| `text/vnd.wap.wml` (uncompiled WML source) | `0x08` |
| `application/vnd.wap.wmlc` (compiled WML / WBXML) | `0x14` |
| `image/vnd.wap.wbmp` | `0x21` |

Rover accepts `0x08` (plain-text WML) at the transport level, but
**rejects the actual content** with a "WML not supported" error - it only
renders the **compiled binary form (WBXML, `0x14`)**. See
[§4](#4-wml---wbxml-compilation) below.

## 4. WML -> WBXML compilation

[WBXML](https://en.wikipedia.org/wiki/WBXML) is a generic binary tokenization
of XML. For WML specifically, the WAP Forum assigns fixed one-byte codes to
each tag name and attribute name (see `WML_ELEMENTS` / `WML_ATTRS` in
[`src/neomar_wap_gateway.py`](../src/neomar_wap_gateway.py), sourced from
the Kannel gateway's `wml_definitions.h` tables).

Document layout:

```
[WBXML version=0x01] [Public ID=0x04 (WML 1.1)] [Charset=0x6A (UTF-8)] [String table len=0x00]
[tag byte] [attributes...] [END] [content...] [END]
```

A tag byte is `tag_code | 0x80` if it has attributes, `| 0x40` if it has
content (children/text), or both ORed together. Attributes are encoded as
`[attr_code][STR_I string][NUL]` pairs terminated by `END (0x01)`; text
content uses the same `STR_I (0x03) ... NUL` inline-string encoding.

For simplicity, this gateway **always encodes attribute values as inline
strings**, even for attributes that have a shorter "well-known value" token
available (e.g. `align="center"`) - it costs a few extra bytes per
attribute but avoids a much bigger lookup table and works unconditionally.

## 5. The single-packet ceiling (and why pagination exists)

WTP supports segmentation (PDU types 5/6, `Segmented_invoke` /
`Segmented_result`) for messages spanning multiple UDP datagrams, with a
1-byte packet sequence number per segment. This gateway **implemented and
tested it** - and found that **Rover renders only the first packet** of a
segmented reply, silently discarding the rest, regardless of the additions
below:

- Inter-packet pacing delays (to rule out serial-link buffer overflow)
- Various flag/PSN encodings for the continuation packets
- Whether the last continuation packet's `ttr` bit was set

The working theory is that Rover's SAR (segmentation and reassembly)
implementation expects an acknowledgement round-trip (WTP Class 2, with the
client sending back Ack/Negative-ack PDUs between segments) that this
gateway does not implement, and simply drops unsolicited continuation
packets it never negotiated.

Rather than implement full Class-2 reliability, **content is instead
paginated server-side**: long pages are split into several independent
single-packet "cards" with `<< Prev` / `Next >>` links (see
`paginate()` in the gateway source, and
[`diagrams/pagination-flow.puml`](diagrams/pagination-flow.puml)).

### 5.1 How the size ceiling was found

Using a synthetic "ruler" page (`[0]xxxx...[50]xxxx...[100]...`, so a
truncation point is immediately visible as a character position), the
maximum reliable single-packet WBXML payload was found by bisection on a
real device over several iterations:

| WBXML bytes | Result |
|---|---|
| 291 | OK |
| 500 | **hangs (transport-level regression from an unrelated header bug, superseded by later tests)** |
| 935 | OK |
| 975 | OK |
| 986 | **fails ("incomplete data transfer" / infinite loading)** |
| 990 | **fails** |

The working ceiling sits right around **975-990 bytes** of full WSP payload
(WTP header not included). `SAFE_PAGE_BYTES = 860` is used as the default
target, leaving headroom for the `<< Prev` / `Next >>` navigation links
that get appended after the packing decision, plus safety margin - this is
a soft, empirically-tuned constant, not a documented protocol limit, and
may need retuning against a different Rover build or a different PPP
link's MTU.

## 6. WBMP images

[WBMP](https://en.wikipedia.org/wiki/Wireless_Application_Protocol_Bitmap_Format)
is already WAP's native 1-bit bitmap format, so `.wbmp` URLs are proxied
through as raw bytes with `content_type = 0x21` - **no transcoding
needed**, unlike Xiino/OpenXiino which converts arbitrary PNG/JPEG into a
proprietary format.

Two consequences:

1. **Other raster formats (GIF/PNG/JPEG) are not renderable at all** by a
   WAP 1.x client - the gateway returns a placeholder card for those
   instead of attempting conversion at request time.
2. Images larger than the single-packet ceiling (§5) simply won't display,
   since a WBMP is one opaque binary blob that can't be paginated the way
   text can. **Pre-shrink and dither source images offline** with
   [`tools/png2wbmp.py`](../tools/png2wbmp.py) rather than relying on the
   gateway's optional on-the-fly `shrink_wbmp_to_fit()` helper - dithering
   a full-quality source image gives visibly better results than dithering
   an image that's already been through lossy quantization once.

## 7. User-Agent

Rover's own User-Agent string, observed verbatim in its requests:

```
Rover 1.5 (Palm; IP; OS v. 3.5.3)
```

The gateway forwards this same string to the origin HTTP server. Some WAP
sites - including the retro-WML test site this gateway was built against -
sniff the User-Agent (or an `Accept: text/vnd.wap.wml` header) to decide
whether to serve WML or regular HTML; forwarding Rover's real UA is what
makes that content negotiation work correctly upstream.

## Compatibility with other clients

Everything in §1-2 (fixed IP:port, the 4-byte Result-header quirk, `rid`
mirroring, `Segmented_result` being unreliable) is specific to how **this
particular Rover 1.5 build** behaves, discovered empirically against one
real device. A different WAP 1.x browser (Blazer, Palmscape/Xiino,
Openwave, a generic feature-phone stack) will very likely:

- Target a **configurable** gateway address, typically on standard ports
  9200/9201 UDP (WSP) rather than 49300 - meaning it *can* just be pointed
  at this gateway's IP without needing the route/ARP interception trick.
- Follow the spec's 3-byte Result header and TID correlation more
  faithfully - or differently non-faithfully in its own way.
- Actually implement Class-2 WTP acknowledgements, in which case
  `Segmented_result` might work fine (unlike with Rover) and full-size
  pages wouldn't need splitting.

The WML→WBXML compiler (§4) and the HTTP-fetch/content-negotiation logic
are protocol-generic and reusable as-is; the WTP/WSP framing layer (§2)
should be treated as a **starting point to test and adjust**, not assumed
to work unmodified, against any other client.
