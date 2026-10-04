# Guía de Configuración

Esta guía cubre toda la cadena: dispositivo Palm -> host PPP -> gateway ->
servidor web de origen. Tus IPs/nombres de interfaz exactos serán
distintos - sustituye por los tuyos en todo momento.

Ver [`diagrams/network-topology.puml`](diagrams/network-topology.puml) para
una vista visual, y [PROTOCOLO.md](PROTOCOLO.md) para *por qué* cada paso
de abajo es necesario.

## 0. Requisitos previos

- Un dispositivo Palm OS clásico (probado en un Palm m105, Palm OS 3.5.3)
  con **Neomar / Rover 1.5** instalado (venía en algunos CD de software
  oficiales de Palm; no se conoce una descarga independiente).
- Una forma de darle a la Palm una conexión PPP/TCP-IP hacia una PC
  moderna: se recomienda [Softick PPP](https://palmdb.net/app/softick-ppp)
  por sobre MochaPPP - permite forzar un servidor DNS específico, y más
  importante, su log (`Tools > Log` en Softick) fue lo que permitió ver la
  IP:puerto de destino real que Rover estaba contactando.
- Python 3.9+ en la máquina que correrá el gateway (solo librería estándar
  - sin dependencias que instalar para `src/neomar_wap_gateway.py`).
  La herramienta opcional de imágenes `tools/png2wbmp.py` necesita
  Python 3.10+ y Pillow (ver paso 7).
- Acceso `sudo`/Administrador tanto en el host del gateway como en el host
  PPP, para los pasos de enrutamiento/ARP de abajo.

## 1. Encuentra la IP:puerto objetivo de tu build de Rover

Las dos IPs usadas en este proyecto (`165.160.13.20` y `165.160.15.20`,
puerto `49300`) fueron observadas en una build específica de Rover 1.5.
**La tuya puede ser distinta** - confírmalo antes de continuar:

1. Corre Softick PPP (o MochaPPP + Wireshark en la NIC del host PPP) y
   activa el logging.
2. En la Palm, abre Neomar y presiona **Start**.
3. Busca una línea como:
   ```
   UDP_IN: 10.0.0.1:xxxx -> <ip>:<puerto>
   ```
   Esa `<ip>:<puerto>` es lo que vas a interceptar.

Si solo tienes Wireshark (sin log de Softick), captura en la NIC física del
host PPP mientras presionas Start, filtra por `udp`, y lee la IP/puerto de
destino en la lista de paquetes.

## 2. Configura la interceptación en el host del gateway (Linux)

El gateway necesita que el sistema operativo le entregue paquetes
dirigidos a la(s) IP(s) objetivo, aunque esa IP no esté realmente asignada
a esta máquina por DHCP. Esto se hace agregándola como dirección
secundaria en la interfaz de red.

Los ejemplos de abajo usan `eth0`, pero el nombre de la interfaz varía
según la máquina (`eno1`, `enp3s0`, `wlan0`, ...). Lista las tuyas con:

```bash
ip -br link
```

Luego agrega las direcciones:

```bash
sudo ip addr add 165.160.13.20/32 dev eth0   # reemplaza eth0 por tu interfaz
sudo ip addr add 165.160.15.20/32 dev eth0
```

**Esto no sobrevive un reinicio ni una renovación de lease DHCP que
reinicie la interfaz.** Para algo más que una prueba puntual, hazlo
persistente - por ejemplo una pequeña unidad systemd o un script
dispatcher de NetworkManager que vuelva a correr los dos comandos de
arriba en `network-online.target`.

Encuentra la dirección MAC de este host (necesaria para el paso 3):

```bash
ip link show eth0 | grep ether
```

## 3. Configura el enrutamiento en el host PPP (ejemplo Windows XP)

> **¿El gateway no está en tu LAN?** (por ejemplo, un servidor público en
> internet) El truco de ruta + ARP de abajo solo llega a hosts del mismo
> segmento Ethernet. Usa [`tools/xp-relay`](../tools/xp-relay/LEAME.md) en
> su lugar: el host PPP toma las direcciones del gateway de Rover y reenvía
> el tráfico UDP al gateway remoto - sin necesidad de una segunda máquina.

El host PPP (corriendo Softick/MochaPPP) es lo que realmente reenvía los
paquetes UDP de la Palm hacia adelante. Su propia lógica de NAT/reenvío usa
la pila de red normal del sistema operativo para la búsqueda de la *MAC de
destino*, así que una ruta estática más una entrada ARP estática
apuntando la IP objetivo hacia la MAC del host del gateway resuelven el
problema (ver la nota al final de este paso sobre por qué hacen falta
ambas) - **no hace falta editar DNS ni el archivo `hosts`** (ver [PROTOCOLO.md §1](PROTOCOLO.md#1-por-que-los-trucos-de-dnshttp-no-funcionan)
para el por qué).

En un `cmd.exe` como **Administrador**:

```bat
route add 165.160.13.20 mask 255.255.255.255 <ip-del-host-gateway>
route add 165.160.15.20 mask 255.255.255.255 <ip-del-host-gateway>
arp -s 165.160.13.20 <mac-del-host-gateway-con-guiones>
arp -s 165.160.15.20 <mac-del-host-gateway-con-guiones>
```

Ejemplo, calzando con la configuración original de este proyecto (host
gateway `192.168.1.160`, MAC `c8:7f:54:57:75:90`):

```bat
route add 165.160.13.20 mask 255.255.255.255 192.168.1.160
route add 165.160.15.20 mask 255.255.255.255 192.168.1.160
arp -s 165.160.13.20 c8-7f-54-57-75-90
arp -s 165.160.15.20 c8-7f-54-57-75-90
```

**Ni `route add` ni `arp -s` sobreviven un reinicio** por defecto en
Windows. Después de reiniciar el host PPP, vuelve a correr los cuatro
comandos (y revisa también el `ip addr add` del paso 2 en el host del
gateway, por si su interfaz se reinició mientras tanto).

> Tanto `route add` *como* `arp -s` fueron necesarios en las pruebas - la
> ruta estática por sí sola redirigía correctamente el tráfico **originado
> por el propio Windows** (ej. `ping`), pero el motor NAT propio de
> Softick parecía saltarse la tabla de rutas del sistema operativo para los
> paquetes que reenvía en nombre de la Palm, mientras seguía respetando la
> tabla ARP para la entrega real a nivel de enlace. Si tu herramienta PPP
> se comporta distinto, puede que solo necesites uno de los dos.

## 4. Configura los ajustes TCP/IP de la Palm

En **Prefs > Network** (el perfil de conexión usado por tu herramienta
PPP), el campo de servidor DNS generalmente se puede dejar en
blanco/automático - es irrelevante aquí ya que Rover se conecta por IP, no
por hostname (ver [PROTOCOLO.md §1](PROTOCOLO.md#1-por-que-los-trucos-de-dnshttp-no-funcionan)).
No debería hacer falta ningún cambio en la propia Palm más allá de tener
una conexión PPP funcional.

## 5. Corre el gateway

```bash
cd src
python3 neomar_wap_gateway.py
```

Escucha en UDP `0.0.0.0:49300` y registra cada petición que maneja. Edita
las constantes al inicio de `neomar_wap_gateway.py` (`LISTEN_PORT`,
`HOME_LINK_TARGET`, `SAFE_PAGE_BYTES`, ...) si tu configuración difiere de
los valores por defecto - ver los comentarios inline y
[PROTOCOLO.md](PROTOCOLO.md) para qué controla cada uno.

Para mantenerlo corriendo entre reinicios/cierres de sesión, envuélvelo en
un servicio systemd de usuario o el sistema de init que prefieras; un
`nohup ... &` puntual está bien para pruebas.

## 6. Prueba

En la Palm, abre Neomar y presiona **Start**. Deberías llegar a una
pequeña página "Connected" incluida por defecto. Desde ahí, navega a tu
propio sitio compatible con WAP (edita `HOME_LINK_TARGET` en el código
fuente del gateway para que apunte ahí por defecto).

Si se queda cargando indefinidamente: revisa de nuevo los pasos 2-3 (la
causa más común es una renovación de DHCP en el host del gateway que quita
el alias de IP en silencio - ver la nota del paso 2). Si carga pero
muestra un error de protocolo/parseo: captura el payload UDP crudo y
compáralo contra [PROTOCOLO.md](PROTOCOLO.md). Los `print()` que ya tiene
el gateway solo registran la URI pedida y los errores, no los bytes
crudos, así que captura con Wireshark en el host del gateway (filtro
`udp.port == 49300`) o agrega temporalmente un `print(data.hex())` justo
después de `recvfrom()` en `serve_forever()`.

## 7. Convertir imágenes para tu sitio

WAP 1.x solo renderiza imágenes WBMP (1 bit). Convierte y reduce tus PNGs
de antemano, offline, para el mejor resultado. El conversor necesita
Python 3.10+ y Pillow; instálalo una vez con:

```bash
pip install -r tools/requirements.txt
```

Luego convierte:

```bash
python3 tools/png2wbmp.py origen.png destino.wbmp --width 100
```

Ver [PROTOCOLO.md §6](PROTOCOLO.md#6-imagenes-wbmp) para orientación de
tamaño (una respuesta de un solo paquete tiene un techo de alrededor de
900-950 bytes de datos de imagen, así que mantén las imágenes convertidas
bajo eso a menos que aceptes que se salten).
