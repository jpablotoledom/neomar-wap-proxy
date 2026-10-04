/*
 * neomar_relay - UDP relay that lets a Palm running Neomar/Rover 1.5 reach a
 * remote WAP gateway through the PC it is PPP-connected to (MochaPPP,
 * Softick PPP, ...), with nothing else on the network.
 *
 * Neomar is hardwired to its long-dead operator gateway (165.160.13.20 /
 * 165.160.15.20, UDP 49300). This program gives those two addresses to the
 * PC itself - added on the fly to the network connection Windows uses to
 * reach the gateway, removed again on exit - so every datagram the Palm sends
 * there is delivered locally and forwarded to the real gateway
 * (theretrocenter.com:49300 by default); replies are sent back to the Palm
 * *from* the address it called, which is what Rover expects.
 *
 * Usage (as Administrator, needed to add the addresses):
 *   neomar_relay.exe [-t host] [-p port] [-l ip,ip,...] [-n] [-q]
 *     -t  remote gateway host          (default theretrocenter.com)
 *     -p  remote and local UDP port    (default 49300)
 *     -l  local addresses to listen on (default 165.160.13.20,165.160.15.20)
 *     -n  don't add the addresses; they must already exist on this PC
 *         (e.g. configured by hand on the Microsoft Loopback Adapter)
 *     -q  quiet: don't log every datagram
 *
 * Builds for Windows XP and later (Winsock 2 + IP Helper, no post-XP APIs):
 *   i686-w64-mingw32-gcc -O2 -s -static -D_WIN32_WINNT=0x0501 \
 *     -o neomar_relay.exe neomar_relay.c -lws2_32 -liphlpapi
 */
#include <winsock2.h>
#include <windows.h>
#include <iphlpapi.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define DEFAULT_TARGET  "theretrocenter.com"
#define DEFAULT_PORT    49300
#define DEFAULT_LISTEN  "165.160.13.20,165.160.15.20"
#define MAX_LISTEN      8
#define MAX_SESSIONS    48      /* + listeners must stay under FD_SETSIZE (64) */
#define SESSION_IDLE_S  120
#define BUF_SIZE        65536

typedef struct {
    SOCKET fd;
    struct sockaddr_in addr;
} Listener;

/* One Palm-side peer (ip:port, as seen on one listener) and the socket used
 * to talk to the remote gateway on its behalf. */
typedef struct {
    int in_use;
    int listener;
    struct sockaddr_in peer;
    SOCKET upstream;
    time_t last_seen;
} Session;

static Listener listeners[MAX_LISTEN];
static int listener_count = 0;
static Session sessions[MAX_SESSIONS];
static struct sockaddr_in target;
static int quiet = 0;
static int auto_addresses = 1;

/* Addresses this program added (AddIPAddress NTE contexts), removed on exit. */
static ULONG added_contexts[MAX_LISTEN];
static int added_count = 0;

static void log_line(const char *fmt, ...) {
    char stamp[16];
    time_t now = time(NULL);
    strftime(stamp, sizeof(stamp), "%H:%M:%S", localtime(&now));
    printf("[%s] ", stamp);
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
    fflush(stdout);
}

static const char *addr_str(const struct sockaddr_in *a, char *out, size_t size) {
    _snprintf(out, size, "%s:%u", inet_ntoa(a->sin_addr), (unsigned)ntohs(a->sin_port));
    out[size - 1] = '\0';
    return out;
}

static int resolve_target(const char *host, unsigned short port) {
    memset(&target, 0, sizeof(target));
    target.sin_family = AF_INET;
    target.sin_port = htons(port);
    target.sin_addr.s_addr = inet_addr(host);
    if (target.sin_addr.s_addr != INADDR_NONE) return 0;

    struct hostent *he = gethostbyname(host);
    if (!he || he->h_addrtype != AF_INET || !he->h_addr_list[0]) return -1;
    memcpy(&target.sin_addr, he->h_addr_list[0], sizeof(target.sin_addr));
    return 0;
}

static void remove_added_addresses(void) {
    while (added_count > 0) DeleteIPAddress(added_contexts[--added_count]);
}

static BOOL WINAPI on_console_close(DWORD event) {
    (void)event;
    if (added_count > 0) {
        remove_added_addresses();
        printf("\nRemoved the gateway addresses from this PC.\n");
        fflush(stdout);
    }
    ExitProcess(0);
    return TRUE;
}

static void describe_interface(DWORD if_index, char *out, size_t size) {
    _snprintf(out, size, "interface %lu", (unsigned long)if_index);
    out[size - 1] = '\0';

    ULONG len = 0;
    if (GetAdaptersInfo(NULL, &len) != ERROR_BUFFER_OVERFLOW || len == 0) return;
    IP_ADAPTER_INFO *info = malloc(len);
    if (info && GetAdaptersInfo(info, &len) == NO_ERROR) {
        for (IP_ADAPTER_INFO *a = info; a; a = a->Next) {
            if (a->Index == if_index) {
                _snprintf(out, size, "%s (%s%s)", a->Description, a->IpAddressList.IpAddress.String,
                          a->DhcpEnabled ? ", DHCP" : "");
                out[size - 1] = '\0';
                break;
            }
        }
    }
    free(info);
}

/* Netmasks tried in order. Windows XP rejects /32 (ERROR_INVALID_PARAMETER -
 * the address would be its own subnet's network and broadcast address);
 * /29 still keeps the side effect to an 8-address slice of a dead range. */
static const char *ADD_MASKS[] = { "255.255.255.255", "255.255.255.248", "255.255.255.0", NULL };

/* Adds `ip` to the connection Windows routes the gateway through (the
 * default connection, in practice). Not persistent: Windows also drops it on
 * reboot or when that connection resets, and we remove it on exit. */
static int add_address(const char *ip) {
    DWORD if_index = 0;
    DWORD rc = GetBestInterface(target.sin_addr.s_addr, &if_index);
    if (rc != NO_ERROR) {
        log_line("ERROR: no network connection reaches the gateway (error %lu)", (unsigned long)rc);
        return -1;
    }

    char name[160];
    describe_interface(if_index, name, sizeof(name));

    ULONG context = 0, instance = 0;
    const char **mask = ADD_MASKS;
    for (; *mask; mask++) {
        rc = AddIPAddress(inet_addr(ip), inet_addr(*mask), if_index, &context, &instance);
        if (rc != ERROR_INVALID_PARAMETER) break;
    }
    if (rc == ERROR_OBJECT_ALREADY_EXISTS || rc == ERROR_DUP_DOMAINNAME) return 0;
    if (rc != NO_ERROR) {
        if (rc == ERROR_ACCESS_DENIED)
            log_line("ERROR: cannot add %s - run neomar_relay as Administrator", ip);
        else
            log_line("ERROR: cannot add %s to %s [index %lu] (error %lu)", ip, name,
                     (unsigned long)if_index, (unsigned long)rc);
        return -1;
    }
    added_contexts[added_count++] = context;
    log_line("Added %s/%s to %s", ip, *mask, name);
    return 0;
}

static int open_listeners(char *list, unsigned short port) {
    for (char *ip = strtok(list, ","); ip && listener_count < MAX_LISTEN; ip = strtok(NULL, ",")) {
        Listener *l = &listeners[listener_count];
        memset(&l->addr, 0, sizeof(l->addr));
        l->addr.sin_family = AF_INET;
        l->addr.sin_port = htons(port);
        l->addr.sin_addr.s_addr = inet_addr(ip);
        if (l->addr.sin_addr.s_addr == INADDR_NONE) {
            log_line("ERROR: '%s' is not an IPv4 address", ip);
            continue;
        }
        l->fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        int bound = l->fd != INVALID_SOCKET && bind(l->fd, (struct sockaddr *)&l->addr, sizeof(l->addr)) == 0;
        if (!bound && l->fd != INVALID_SOCKET && WSAGetLastError() == WSAEADDRNOTAVAIL && auto_addresses) {
            if (add_address(ip) != 0) {     /* add_address() already logged why */
                closesocket(l->fd);
                continue;
            }
            /* a freshly added address can take a moment to become usable */
            for (int tries = 0; !bound && tries < 30; tries++) {
                Sleep(100);
                bound = bind(l->fd, (struct sockaddr *)&l->addr, sizeof(l->addr)) == 0;
            }
        }
        if (!bound) {
            int err = WSAGetLastError();
            if (err == WSAEADDRNOTAVAIL)
                log_line("ERROR: %s is not an address of this PC%s", ip,
                         auto_addresses ? "" : " - add it to the Microsoft Loopback Adapter, or drop -n");
            else if (err == WSAEADDRINUSE)
                log_line("ERROR: %s:%u is already in use by another program", ip, port);
            else
                log_line("ERROR: cannot listen on %s:%u (Winsock error %d)", ip, port, err);
            if (l->fd != INVALID_SOCKET) closesocket(l->fd);
            continue;
        }
        log_line("Listening on %s:%u", ip, port);
        listener_count++;
    }
    return listener_count;
}

static Session *find_or_open_session(int li, const struct sockaddr_in *peer) {
    time_t now = time(NULL);
    Session *free_slot = NULL, *oldest = NULL;

    for (int i = 0; i < MAX_SESSIONS; i++) {
        Session *s = &sessions[i];
        if (!s->in_use) { if (!free_slot) free_slot = s; continue; }
        if (s->listener == li && s->peer.sin_addr.s_addr == peer->sin_addr.s_addr &&
            s->peer.sin_port == peer->sin_port) {
            s->last_seen = now;
            return s;
        }
        if (!oldest || s->last_seen < oldest->last_seen) oldest = s;
    }

    Session *s = free_slot;
    if (!s) {               /* table full: recycle the least recently used */
        s = oldest;
        closesocket(s->upstream);
        s->in_use = 0;
    }

    SOCKET up = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (up == INVALID_SOCKET) return NULL;
    if (connect(up, (struct sockaddr *)&target, sizeof(target)) != 0) {
        log_line("ERROR: cannot reach gateway (Winsock error %d)", WSAGetLastError());
        closesocket(up);
        return NULL;
    }
    s->in_use = 1;
    s->listener = li;
    s->peer = *peer;
    s->upstream = up;
    s->last_seen = now;

    /* inet_ntoa() reuses one static buffer, so format each address separately */
    char a[32], b[32], c[32];
    addr_str(peer, a, sizeof(a));
    addr_str(&listeners[li].addr, b, sizeof(b));
    addr_str(&target, c, sizeof(c));
    log_line("New session: Palm %s -> %s -> gateway %s", a, b, c);
    return s;
}

static void expire_sessions(void) {
    time_t now = time(NULL);
    for (int i = 0; i < MAX_SESSIONS; i++) {
        if (sessions[i].in_use && now - sessions[i].last_seen > SESSION_IDLE_S) {
            closesocket(sessions[i].upstream);
            sessions[i].in_use = 0;
        }
    }
}

int main(int argc, char **argv) {
    const char *target_host = DEFAULT_TARGET;
    unsigned short port = DEFAULT_PORT;
    char listen_list[256];
    strcpy(listen_list, DEFAULT_LISTEN);

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-t") && i + 1 < argc) target_host = argv[++i];
        else if (!strcmp(argv[i], "-p") && i + 1 < argc) port = (unsigned short)atoi(argv[++i]);
        else if (!strcmp(argv[i], "-l") && i + 1 < argc) {
            strncpy(listen_list, argv[++i], sizeof(listen_list) - 1);
            listen_list[sizeof(listen_list) - 1] = '\0';
        } else if (!strcmp(argv[i], "-n")) auto_addresses = 0;
        else if (!strcmp(argv[i], "-q")) quiet = 1;
        else {
            printf("usage: %s [-t host] [-p port] [-l ip,ip,...] [-n] [-q]\n", argv[0]);
            return 2;
        }
    }

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        printf("ERROR: WSAStartup failed\n");
        return 1;
    }

    printf("neomar_relay - Neomar/Rover WAP relay\n\n");
    if (resolve_target(target_host, port) != 0) {
        log_line("ERROR: cannot resolve %s - check this PC's internet/DNS", target_host);
        return 1;
    }
    char tbuf[32];
    log_line("Gateway: %s (%s)", target_host, addr_str(&target, tbuf, sizeof(tbuf)));

    SetConsoleCtrlHandler(on_console_close, TRUE);
    atexit(remove_added_addresses);
    if (open_listeners(listen_list, port) == 0) {
        log_line("Nothing to listen on - exiting.");
        return 1;
    }
    log_line("Ready. Open Neomar on the Palm and press Start. Ctrl+C to quit.");

    static char buf[BUF_SIZE];
    for (;;) {
        fd_set rd;
        FD_ZERO(&rd);
        for (int i = 0; i < listener_count; i++) FD_SET(listeners[i].fd, &rd);
        for (int i = 0; i < MAX_SESSIONS; i++)
            if (sessions[i].in_use) FD_SET(sessions[i].upstream, &rd);

        struct timeval tv = { 5, 0 };
        int ready = select(0, &rd, NULL, NULL, &tv);
        if (ready == SOCKET_ERROR) {
            log_line("ERROR: select failed (Winsock error %d)", WSAGetLastError());
            Sleep(1000);
            continue;
        }
        expire_sessions();
        if (ready == 0) continue;

        /* Palm -> gateway */
        for (int li = 0; li < listener_count; li++) {
            if (!FD_ISSET(listeners[li].fd, &rd)) continue;
            struct sockaddr_in peer;
            int plen = sizeof(peer);
            int n = recvfrom(listeners[li].fd, buf, sizeof(buf), 0, (struct sockaddr *)&peer, &plen);
            if (n <= 0) continue;   /* e.g. WSAECONNRESET from an earlier ICMP error */
            Session *s = find_or_open_session(li, &peer);
            if (!s) continue;
            if (send(s->upstream, buf, n, 0) == SOCKET_ERROR)
                log_line("ERROR: send to gateway failed (Winsock error %d)", WSAGetLastError());
            else if (!quiet) {
                char a[32];
                log_line("Palm %s -> gateway: %d bytes", addr_str(&peer, a, sizeof(a)), n);
            }
        }

        /* gateway -> Palm, sent from the address the Palm called */
        for (int i = 0; i < MAX_SESSIONS; i++) {
            Session *s = &sessions[i];
            if (!s->in_use || !FD_ISSET(s->upstream, &rd)) continue;
            int n = recv(s->upstream, buf, sizeof(buf), 0);
            if (n == SOCKET_ERROR) {
                if (WSAGetLastError() == WSAECONNRESET)
                    log_line("ERROR: gateway refused the datagram (is UDP %u open on the server?)", port);
                continue;
            }
            s->last_seen = time(NULL);
            sendto(listeners[s->listener].fd, buf, n, 0, (struct sockaddr *)&s->peer, sizeof(s->peer));
            if (!quiet) {
                char a[32];
                log_line("gateway -> Palm %s: %d bytes", addr_str(&s->peer, a, sizeof(a)), n);
            }
        }
    }
}
