# neomar_relay - llegar a un gateway remoto solo desde el host PPP

*[Read in English](README.md)*

Un relay UDP mínimo (`neomar_relay.exe`, ~48 KB, sin dependencias, corre en
Windows XP y posteriores) para cuando el gateway WAP **no está en tu LAN**
- por ejemplo, un servidor público en internet. Reemplaza el truco de ruta
estática + ARP estático de [docs/CONFIGURACION.md §3](../../docs/CONFIGURACION.md#3-configura-el-enrutamiento-en-el-host-ppp-ejemplo-windows-xp),
que solo funciona cuando el host del gateway está en el mismo segmento
Ethernet que el host PPP.

```
Palm ─serial─ Windows XP (MochaPPP/Softick) ── internet ── gateway remoto :49300
                └ neomar_relay en 165.160.13.20 / 165.160.15.20
```

## Cómo funciona

Rover siempre envía a su gateway de operador fijo (`165.160.13.20` /
`165.160.15.20`, UDP `49300` - confirma los tuyos, ver CONFIGURACION.md §1).
Windows XP no puede reescribir el destino de un paquete (no tiene DNAT), así
que en su lugar el host PPP **es dueño** de esas dos direcciones: al arrancar,
`neomar_relay` las agrega, con la máscara más
pequeña que Windows acepte (/32, o /29 en XP), a la conexión de red que
Windows usa para llegar al gateway (la conexión por defecto), y las quita
al salir. El software PPP reenvía los datagramas de la Palm a través de
Winsock, Windows se los entrega localmente a `neomar_relay`, y el relay
reenvía cada uno al gateway real.
Las respuestas vuelven a la Palm **desde la dirección a la que llamó**, que
es lo que Rover espera. Un socket hacia el gateway por cada par del lado de
la Palm mantiene separadas las transacciones concurrentes; los inactivos
expiran a los 2 minutos.

## Uso

Inicia tu software PPP como siempre y ejecuta `neomar_relay.exe` **como
Administrador** (hace falta para agregar las direcciones; en XP la cuenta
habitual ya lo es). Permítelo si el Firewall de Windows pregunta, abre
Neomar en la Palm y presiona **Start**. Ciérralo con **Ctrl+C** (o el botón
de cerrar la ventana) para que quite las direcciones que agregó; si se
mata de otra forma, desaparecen al reiniciar o cuando esa conexión se
reinicia.

```
neomar_relay.exe [-t host] [-p puerto] [-l ip,ip,...] [-n] [-q]
  -t  host del gateway remoto            (por defecto theretrocenter.com)
  -p  puerto UDP remoto y local          (por defecto 49300)
  -l  direcciones locales donde escuchar (por defecto 165.160.13.20,165.160.15.20)
  -n  no agregar las direcciones; deben existir ya en este PC
  -q  silencioso: no registra cada datagrama
```

Ej.: `neomar_relay.exe -t mi-gateway.example.com`.

El gateway remoto debe escuchar en su dirección pública con UDP `49300`
accesible desde internet (ábrelo en el firewall del servidor).

Si antes usaste el método de ruta/ARP, borra esas entradas primero
(`route delete 165.160.13.20`, `arp -d 165.160.13.20`, y lo mismo para
`165.160.15.20`).

### Sin permisos de administrador: `-n`

Configura las direcciones una vez a mano y ejecuta con `-n`. Agrega el
**Adaptador de bucle invertido de Microsoft** (Panel de control → Agregar
hardware → "Sí, ya conecté el hardware" → "Agregar un nuevo dispositivo de
hardware" → "Instalar el hardware seleccionado manualmente de una lista" →
Adaptadores de red → Microsoft → Adaptador de bucle invertido de
Microsoft), y dale las direcciones:

```bat
netsh interface ip set address name="Conexión de área local 2" static 165.160.13.20 255.255.255.0
netsh interface ip add address name="Conexión de área local 2" 165.160.15.20 255.255.255.0
```

(`ipconfig /all` muestra el nombre de conexión del adaptador.)

## Diagnóstico desde el log del relay

| Lo que ves | Significado |
|---|---|
| `run neomar_relay as Administrator` | Se negó el permiso para agregar las direcciones - clic derecho → Ejecutar como…, o usa `-n`. |
| `is not an address of this PC` | Solo con `-n`: la dirección no está configurada en ningún adaptador. |
| No aparece `New session` al presionar Start | El tráfico de la Palm no llega al relay: revisa el enlace PPP, y que Rover apunte a la IP:puerto donde escuchas (CONFIGURACION.md §1). |
| `Palm -> gateway` pero nunca `gateway -> Palm` | El gateway remoto no responde - firewall, o el gateway no está corriendo. |
| `gateway refused the datagram` | Volvió un ICMP "port unreachable": nada escucha en ese puerto UDP del servidor. |

## Compilación

Desde Linux con el compilador cruzado mingw-w64
(`apt install gcc-mingw-w64-i686`):

```bash
tools/xp-relay/build.sh
```

La compilación fija la versión de subsistema/SO del PE en 5.01 para que XP
acepte el binario, y usa solo APIs presentes en XP (`select()` de Winsock 2,
`GetBestInterface`/`AddIPAddress`/`DeleteIPAddress` de IP Helper; sin
`inet_pton`, sin `GetTickCount64`).

*Estado:* verificado bajo Wine contra un gateway y una Palm simulados
(respuestas desde la dirección llamada, datagramas de más de 1200 bytes,
pares concurrentes), y de punta a punta en hardware real: Palm m105 +
Neomar/Rover 1.5 → serial → Windows XP SP3 + MochaPPP (direcciones
agregadas automáticamente como /29 sobre una conexión Wi-Fi con DHCP) →
internet → theretrocenter.com.
