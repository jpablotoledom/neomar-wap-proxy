# Setup Guide

This guide covers the full chain: Palm device -> PPP host -> gateway ->
origin web server. Your exact IPs/interface names will differ - substitute
your own throughout.

See [`diagrams/network-topology.puml`](diagrams/network-topology.puml) for
a visual overview, and [PROTOCOL.md](PROTOCOL.md) for *why* each step below
is necessary.

## 0. Prerequisites

- A classic Palm OS device (tested on a Palm m105, Palm OS 3.5.3) with
  **Neomar / Rover 1.5** installed (it shipped on some official Palm
  software CDs; there is no known standalone download).
- A way to give the Palm a PPP/TCP-IP connection to a modern PC:
  [Softick PPP](https://palmdb.net/app/softick-ppp) is recommended over
  MochaPPP - it lets you force a specific DNS server, and more importantly
  its log (`Tools > Log` in Softick) is what let us see the real
  destination IP:port Rover was contacting.
- Python 3.9+ on the machine that will run the gateway (standard library
  only - no dependencies to install for `src/neomar_wap_gateway.py`).
  The optional image tool `tools/png2wbmp.py` needs Python 3.10+ and
  Pillow (see step 7).
- `sudo`/Administrator access on both the gateway host and the PPP host,
  for the routing/ARP steps below.

## 1. Find your Rover build's target IP:port

The two IPs used by this project (`165.160.13.20` and `165.160.15.20`,
port `49300`) were observed on one specific Rover 1.5 build. **Yours may
differ** - confirm it before proceeding:

1. Run Softick PPP (or MochaPPP + Wireshark on the PPP host's NIC) and
   enable logging.
2. On the Palm, open Neomar and press **Start**.
3. Look for a line like:
   ```
   UDP_IN: 10.0.0.1:xxxx -> <ip>:<port>
   ```
   That `<ip>:<port>` is what you'll intercept.

If you only have Wireshark (no Softick log), capture on the PPP host's
physical NIC while pressing Start, filter on `udp`, and read the
destination IP/port from the packet list.

## 2. Set up interception on the gateway host (Linux)

The gateway needs the OS to hand it packets addressed to the target IP(s),
even though that IP isn't really assigned to this machine by DHCP. This is
done by adding it as a secondary address on the network interface.

The examples below use `eth0`, but the interface name varies between
machines (`eno1`, `enp3s0`, `wlan0`, ...). List yours with:

```bash
ip -br link
```

Then add the addresses:

```bash
sudo ip addr add 165.160.13.20/32 dev eth0   # replace eth0 with your interface
sudo ip addr add 165.160.15.20/32 dev eth0
```

**This does not survive a reboot or a DHCP lease renewal that resets the
interface.** For anything beyond a one-off test, make it persistent - e.g.
a small systemd unit or a NetworkManager dispatcher script that re-runs the
two commands above on `network-online.target`.

Find this host's MAC address (needed for step 3):

```bash
ip link show eth0 | grep ether
```

## 3. Set up routing on the PPP host (Windows XP example)

> **Gateway not on your LAN?** (e.g. a public server on the internet) The
> route + ARP trick below only reaches hosts on the same Ethernet segment.
> Use [`tools/xp-relay`](../tools/xp-relay/README.md) instead: the PPP host
> takes Rover's gateway addresses itself and relays the UDP traffic to the
> remote gateway - no second machine needed.

The PPP host (running Softick/MochaPPP) is what actually forwards the
Palm's UDP packets onward. Its own NAT/forwarding logic uses the normal OS
network stack for the *destination MAC* lookup, so a static route plus a
static ARP entry pointing the target IP at the gateway host's MAC does the
job (see the note at the end of this step for why both are needed) -
**no DNS or `hosts` file edit is needed** (see [PROTOCOL.md §1](PROTOCOL.md#1-why-dnshttp-tricks-dont-work)
for why).

In an **Administrator** `cmd.exe`:

```bat
route add 165.160.13.20 mask 255.255.255.255 <gateway-host-ip>
route add 165.160.15.20 mask 255.255.255.255 <gateway-host-ip>
arp -s 165.160.13.20 <gateway-host-mac-with-dashes>
arp -s 165.160.15.20 <gateway-host-mac-with-dashes>
```

Example, matching this project's original setup (gateway host
`192.168.1.160`, MAC `c8:7f:54:57:75:90`):

```bat
route add 165.160.13.20 mask 255.255.255.255 192.168.1.160
route add 165.160.15.20 mask 255.255.255.255 192.168.1.160
arp -s 165.160.13.20 c8-7f-54-57-75-90
arp -s 165.160.15.20 c8-7f-54-57-75-90
```

**Neither `route add` nor `arp -s` survive a reboot** by default on
Windows. After restarting the PPP host, re-run all four commands (and
re-check step 2's `ip addr add` on the gateway host too, in case its
interface was reset in the meantime).

> Both `route add` *and* `arp -s` were needed in testing - the static route
> alone correctly redirected traffic **originated by Windows itself** (e.g.
> `ping`), but Softick's own NAT engine appeared to bypass the OS routing
> table for packets it forwards on the Palm's behalf, while still
> respecting the ARP table for the actual link-layer delivery. If your PPP
> tool behaves differently, you may only need one of the two.

## 4. Configure the Palm's TCP/IP settings

In **Prefs > Network** (the connection profile used by your PPP tool), the
DNS server field can usually be left blank/automatic - it's irrelevant
here since Rover connects by IP, not hostname (see
[PROTOCOL.md §1](PROTOCOL.md#1-why-dnshttp-tricks-dont-work)). No changes
should be needed on the Palm itself beyond having a working PPP connection.

## 5. Run the gateway

```bash
cd src
python3 neomar_wap_gateway.py
```

It listens on UDP `0.0.0.0:49300` and logs each request it handles. Edit
the constants at the top of `neomar_wap_gateway.py` (`LISTEN_PORT`,
`HOME_LINK_TARGET`, `SAFE_PAGE_BYTES`, ...) if your setup differs from the
defaults - see comments inline and [PROTOCOL.md](PROTOCOL.md) for what each
one controls.

To keep it running across reboots/logouts, wrap it in a systemd user
service or your init system of choice; a one-off `nohup ... &` is fine for
testing.

## 6. Test

On the Palm, open Neomar and press **Start**. You should land on a small
built-in "Connected" page. From there, navigate to your own WAP-capable
site (edit `HOME_LINK_TARGET` in the gateway source to point at it by
default).

If it hangs indefinitely: re-check steps 2-3 (the most common cause is a
DHCP renewal on the gateway host silently dropping the IP alias - see the
note in step 2). If it loads but shows a protocol/parse error: capture the
raw UDP payload and compare it against [PROTOCOL.md](PROTOCOL.md). The
gateway's existing `print()` calls only log the requested URI and any
error, not the raw bytes, so either capture with Wireshark on the gateway
host (filter `udp.port == 49300`) or temporarily add a
`print(data.hex())` right after `recvfrom()` in `serve_forever()`.

## 7. Converting images for your site

WAP 1.x only renders WBMP (1-bit) images. Convert and pre-shrink your PNGs
offline for the best-looking result. The converter needs Python 3.10+
and Pillow; install it once with:

```bash
pip install -r tools/requirements.txt
```

Then convert:

```bash
python3 tools/png2wbmp.py source.png destination.wbmp --width 100
```

See [PROTOCOL.md §6](PROTOCOL.md#6-wbmp-images) for sizing guidance (a
single-packet reply tops out around 900-950 bytes of image data, so keep
converted images under that unless you accept them being skipped).
