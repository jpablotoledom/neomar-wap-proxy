# neomar_relay - reach a remote gateway from the PPP host alone

*[Leer en español](LEAME.md)*

A tiny UDP relay (`neomar_relay.exe`, ~48 KB, no dependencies, runs on
Windows XP and later) for when the WAP gateway is **not on your LAN** - e.g.
a public server on the internet. It replaces the static route + static ARP
trick from [docs/SETUP.md §3](../../docs/SETUP.md#3-set-up-routing-on-the-ppp-host-windows-xp-example),
which only works when the gateway host is on the same Ethernet segment as
the PPP host.

```
Palm ─serial─ Windows XP (MochaPPP/Softick) ── internet ── remote gateway :49300
                └ neomar_relay on 165.160.13.20 / 165.160.15.20
```

## How it works

Rover always sends to its hardwired operator gateway (`165.160.13.20` /
`165.160.15.20`, UDP `49300` - confirm yours, see SETUP.md §1). Windows XP
can't rewrite a packet's destination (no DNAT), so instead the PPP host
**owns** those two addresses: on start, `neomar_relay` adds them, with the
smallest netmask Windows accepts (/32, or /29 on XP), to the network connection Windows uses to reach the gateway (the
default connection), and removes them again on exit. The PPP software
forwards the Palm's datagrams through Winsock, Windows delivers them
locally to `neomar_relay`, and the relay forwards each one to the real
gateway. Replies go back to the Palm **from the address it called**, which
is what Rover expects. One upstream socket per Palm-side peer keeps
concurrent transactions apart; idle ones expire after 2 minutes.

## Usage

Start your PPP software as usual, then run `neomar_relay.exe` **as
Administrator** (adding addresses needs it; on XP the usual account
already is one). Allow it if Windows Firewall asks, then open Neomar on the
Palm and press **Start**. Close it with **Ctrl+C** (or the window's close
button) so it removes the addresses it added; if it's killed instead, they
go away on the next reboot or when that connection resets.

```
neomar_relay.exe [-t host] [-p port] [-l ip,ip,...] [-n] [-q]
  -t  remote gateway host          (default theretrocenter.com)
  -p  remote and local UDP port    (default 49300)
  -l  local addresses to listen on (default 165.160.13.20,165.160.15.20)
  -n  don't add the addresses; they must already exist on this PC
  -q  quiet: don't log every datagram
```

e.g. `neomar_relay.exe -t my-gateway.example.com`.

The remote gateway must listen on its public address with UDP `49300`
reachable from the internet (open it in the server's firewall).

If you previously used the route/ARP method, remove those entries first
(`route delete 165.160.13.20`, `arp -d 165.160.13.20`, and the same for
`165.160.15.20`).

### Without administrator rights: `-n`

Configure the addresses once by hand and run with `-n`. Add the
**Microsoft Loopback Adapter** (Control Panel → Add Hardware → "Yes, I have
already connected the hardware" → "Add a new hardware device" → "Install
the hardware that I manually select" → Network adapters → Microsoft →
Microsoft Loopback Adapter), then give it the addresses:

```bat
netsh interface ip set address name="Local Area Connection 2" static 165.160.13.20 255.255.255.0
netsh interface ip add address name="Local Area Connection 2" 165.160.15.20 255.255.255.0
```

(`ipconfig /all` shows the adapter's connection name.)

## Troubleshooting from the relay's log

| What you see | Meaning |
|---|---|
| `run neomar_relay as Administrator` | Adding the addresses was denied - right-click → Run as…, or use `-n`. |
| `is not an address of this PC` | Only with `-n`: the address isn't configured on any adapter. |
| No `New session` when pressing Start | The Palm's traffic isn't reaching the relay: check the PPP link, and that Rover targets the IP:port you listen on (SETUP.md §1). |
| `Palm -> gateway` but never `gateway -> Palm` | The remote gateway isn't answering - firewall, or the gateway isn't running. |
| `gateway refused the datagram` | An ICMP "port unreachable" came back: nothing is listening on that UDP port on the server. |

## Building

From Linux with the mingw-w64 cross compiler
(`apt install gcc-mingw-w64-i686`):

```bash
tools/xp-relay/build.sh
```

The build pins the PE subsystem/OS version to 5.01 so XP accepts the
binary, and uses only APIs present on XP (Winsock 2 `select()`, IP Helper
`GetBestInterface`/`AddIPAddress`/`DeleteIPAddress`; no `inet_pton`, no
`GetTickCount64`).

*Status:* verified under Wine against a simulated gateway and Palm
(replies from the called address, >1200-byte datagrams, concurrent
peers), and end to end on real hardware: Palm m105 + Neomar/Rover 1.5 →
serial → Windows XP SP3 + MochaPPP (addresses auto-added as /29 on a DHCP
Wi-Fi connection) → internet → theretrocenter.com.
