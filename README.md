# Neomar WAP Proxy

*[Léeme en español](LEAME.md)*

A minimal WAP 1.x gateway that revives **Neomar** (internally named
**Rover 1.5**), the WAP micro-browser bundled with some classic Palm OS
software CDs, letting it browse live web content again on real (or
emulated) Palm OS hardware - over 20 years after its original backend went
offline.

<p align="center">
  <em>Palm m105 &rarr; PPP link &rarr; this gateway &rarr; any WAP-capable web server</em>
</p>

## Why

Neomar's browser and its "gateway" backend were both proprietary services
Palm/Neomar Inc. shut down long ago. Unlike Xiino (which has
[OpenXiino](https://github.com/nicl83/OpenXiino), an open-source
replacement for its dataserver protocol), Neomar had no such
reimplementation - and its wire protocol was largely undocumented outside
the original WAP Forum specs, which Rover doesn't follow to the letter.

This project reverse-engineers that protocol from real device traffic and
implements just enough of it - WTP transaction framing, WSP request/reply
PDUs, WML-to-WBXML compilation, WBMP image support, and server-side
pagination to work around Rover's unreliable multi-packet reassembly - to
serve it real web pages again.

## Features

- **No dependencies** for the gateway itself - pure Python 3 standard
  library.
- Fetches real HTTP content and forwards Rover's actual User-Agent, so
  sites that do WAP content negotiation work correctly.
- Compiles WML to WBXML on the fly (own compiler, not a wrapper around an
  existing WAP toolkit).
- Serves WBMP images as-is (WAP's native bitmap format needs no
  conversion); a standalone `tools/png2wbmp.py` script pre-converts PNGs
  with Floyd-Steinberg dithering.
- Automatic pagination of long pages into linked, single-packet-sized WML
  cards, since Rover does not reliably reassemble WTP-segmented replies
  (see [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for why, and how the size
  limit was determined empirically).

## Repository layout

```
neomar-wap-proxy/
├── src/
│   └── neomar_wap_gateway.py   # the gateway itself (stdlib only)
├── tools/
│   ├── png2wbmp.py             # offline PNG -> WBMP converter (needs Pillow)
│   └── xp-relay/               # Windows XP UDP relay for a gateway outside your LAN
├── docs/
│   ├── PROTOCOL.md             # reverse-engineering notes / wire format
│   ├── SETUP.md                # network interception + run instructions
│   ├── PROTOCOLO.md            # ^ same, in Spanish
│   ├── CONFIGURACION.md        # ^ same, in Spanish
│   └── diagrams/                # PlantUML sources (topology, sequence, pagination)
├── README.md / LEAME.md
└── LICENSE
```

## Quick start

1. Read [`docs/SETUP.md`](docs/SETUP.md) - getting traffic from the Palm to
   this gateway requires some network interception (Neomar connects to a
   hardcoded, long-dead IP address, not a configurable hostname), which is
   the fiddly part.
2. Edit the constants at the top of
   [`src/neomar_wap_gateway.py`](src/neomar_wap_gateway.py) for your
   network (target gateway IPs/port, your WAP site's URL).
3. Run it:
   ```bash
   python3 src/neomar_wap_gateway.py
   ```
4. On the Palm, open Neomar and press **Start**.

## How it works, in one paragraph

Rover sends a UDP datagram containing a WTP *Invoke* PDU wrapping a WSP
*Get* request to a fixed IP:port baked into the app. Since DNS is never
consulted, this gateway is made reachable at that address via a local IP
alias plus a static route/ARP entry on whatever machine bridges the Palm's
PPP link to the network (not DNS tricks - see
[`docs/PROTOCOL.md`](docs/PROTOCOL.md)). The gateway parses the requested
URL, fetches it over normal HTTP, compiles any WML response into WBXML
(Rover rejects plain-text WML), splits long pages into linked
single-packet-sized "cards" (Rover doesn't reassemble multi-packet WTP
replies reliably), and replies with a WTP *Result* PDU wrapping a WSP
*Reply* - whose header has to mirror several bytes of the original request
verbatim, a quirk documented in detail in
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Diagrams

PlantUML sources live in [`docs/diagrams/`](docs/diagrams/):

- [`network-topology.puml`](docs/diagrams/network-topology.puml) - the full
  Palm-to-origin-server path and where interception happens.
- [`protocol-sequence.puml`](docs/diagrams/protocol-sequence.puml) - one
  request/response exchange.
- [`pagination-flow.puml`](docs/diagrams/pagination-flow.puml) - how long
  pages get split into linked cards.

Render them with the [PlantUML](https://plantuml.com/) CLI/extension, or
paste their contents into an online PlantUML renderer.

## Compatibility

This gateway is tuned against one specific Rover 1.5 build. Other WAP 1.x
browsers (Blazer, Xiino/Palmscape, Openwave, generic feature-phone stacks)
will very likely need a different target IP/port and may have different
low-level WTP framing requirements - see
["Compatibility with other clients"](docs/PROTOCOL.md#compatibility-with-other-clients)
in the protocol notes for what's reusable and what isn't.

## Known limitations

- No WTP Class-2 (acknowledged) segmentation - relies on server-side
  pagination instead (see [`docs/PROTOCOL.md §5`](docs/PROTOCOL.md#5-the-single-packet-ceiling-and-why-pagination-exists)).
- Only WBMP images render; GIF/PNG/JPEG show a placeholder.
- The single-packet size ceiling (~975 bytes) was found empirically against
  one device/PPP link and may not generalize.
- No forms/POST support, no WMLScript, no cookies/session handling.

## License

See [`LICENSE`](LICENSE). This project is not affiliated with, endorsed
by, or associated with Palm Inc., Neomar Inc., or any of their successors.

## Credits

Wire-format details for WTP/WSP/WBXML were cross-referenced against the
[Kannel](https://www.kannel.org/) open-source WAP/SMS gateway's source
(`gw/wtp_pdu.def`, `gw/wsp_pdu.def`, `wml/wml_definitions.h`) and its
`test/fakewap.c` reference client, and the WAP Forum's WINA content-type
registry. None of that code is copied here - only the documented PDU
field layouts and well-known token tables were used as a reference while
reverse-engineering Rover's actual on-the-wire behavior.
