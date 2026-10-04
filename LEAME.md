# Neomar WAP Proxy

*[Read in English](README.md)*

Un gateway WAP 1.x mínimo que revive **Neomar** (internamente llamado
**Rover 1.5**), el micronavegador WAP que venía incluido en algunos CD de
software oficiales de Palm OS, permitiéndole navegar contenido web real de
nuevo en hardware Palm OS real (o emulado) - más de 20 años después de que
su backend original dejara de funcionar.

<p align="center">
  <em>Palm m105 &rarr; enlace PPP &rarr; este gateway &rarr; cualquier servidor web compatible con WAP</em>
</p>

## Por qué

Tanto el navegador de Neomar como su "gateway" backend eran servicios
propietarios que Palm/Neomar Inc. cerró hace mucho tiempo. A diferencia de
Xiino (que cuenta con [OpenXiino](https://github.com/nicl83/OpenXiino), un
reemplazo open-source de su protocolo de dataserver), Neomar nunca tuvo una
reimplementación así - y su protocolo de red estaba en gran parte
indocumentado más allá de las specs originales del WAP Forum, que Rover no
sigue al pie de la letra.

Este proyecto reconstruye ese protocolo por ingeniería inversa a partir de
tráfico real capturado del dispositivo, e implementa lo justo y necesario
- framing de transacciones WTP, PDUs de petición/respuesta WSP, compilación
WML a WBXML, soporte de imágenes WBMP, y paginación del lado del servidor
para sortear el reensamblado poco confiable de mensajes multi-paquete de
Rover - para volver a servirle páginas web reales.

## Características

- **Sin dependencias** para el gateway en sí - solo librería estándar de
  Python 3.
- Trae contenido HTTP real y reenvía el User-Agent real de Rover, para que
  los sitios que hacen negociación de contenido WAP funcionen
  correctamente.
- Compila WML a WBXML al vuelo (compilador propio, no un wrapper sobre un
  toolkit WAP existente).
- Sirve imágenes WBMP tal cual (el formato de bitmap nativo de WAP no
  necesita conversión); un script independiente `tools/png2wbmp.py`
  convierte PNGs de antemano con dithering Floyd-Steinberg.
- Paginación automática de páginas largas en tarjetas WML enlazadas del
  tamaño de un solo paquete, ya que Rover no reensambla de forma confiable
  respuestas segmentadas por WTP (ver
  [`docs/PROTOCOLO.md`](docs/PROTOCOLO.md) para el por qué, y cómo se
  determinó el límite de tamaño empíricamente).

## Estructura del repositorio

```
neomar-wap-proxy/
├── src/
│   └── neomar_wap_gateway.py   # el gateway en si (solo libreria estandar)
├── tools/
│   ├── png2wbmp.py             # convertidor PNG -> WBMP offline (necesita Pillow)
│   └── xp-relay/               # relay UDP para Windows XP, para un gateway fuera de tu LAN
├── docs/
│   ├── PROTOCOL.md             # notas de ingenieria inversa / formato de red
│   ├── SETUP.md                # interceptacion de red + instrucciones de uso
│   ├── PROTOCOLO.md            # ^ lo mismo, en espanol
│   ├── CONFIGURACION.md        # ^ lo mismo, en espanol
│   └── diagrams/                # fuentes PlantUML (topologia, secuencia, paginacion)
├── README.md / LEAME.md
└── LICENSE
```

## Inicio rápido

1. Lee [`docs/CONFIGURACION.md`](docs/CONFIGURACION.md) - lograr que el
   tráfico llegue de la Palm a este gateway requiere cierta interceptación
   de red (Neomar se conecta a una IP fija ya muerta hace años, no a un
   hostname configurable), que es la parte más delicada.
2. Edita las constantes al inicio de
   [`src/neomar_wap_gateway.py`](src/neomar_wap_gateway.py) para tu red
   (IPs/puerto del gateway objetivo, la URL de tu sitio WAP).
3. Ejecútalo:
   ```bash
   python3 src/neomar_wap_gateway.py
   ```
4. En la Palm, abre Neomar y presiona **Start**.

## Cómo funciona, en un párrafo

Rover manda un datagrama UDP con un PDU WTP *Invoke* envolviendo una
petición WSP *Get* hacia una IP:puerto fija grabada en la propia app. Como
nunca consulta DNS, este gateway se hace alcanzable en esa dirección
mediante un alias de IP local más una ruta estática/entrada ARP en la
máquina que hace de puente entre el enlace PPP de la Palm y la red (no
trucos de DNS - ver [`docs/PROTOCOLO.md`](docs/PROTOCOLO.md)). El gateway
interpreta la URL pedida, la trae por HTTP normal, compila cualquier
respuesta WML a WBXML (Rover rechaza el WML en texto plano), divide las
páginas largas en "tarjetas" enlazadas del tamaño de un solo paquete (Rover
no reensambla de forma confiable respuestas WTP multi-paquete), y responde
con un PDU WTP *Result* envolviendo un WSP *Reply* - cuya cabecera tiene
que reflejar varios bytes de la petición original tal cual, una
particularidad documentada en detalle en
[`docs/PROTOCOLO.md`](docs/PROTOCOLO.md).

## Diagramas

Las fuentes PlantUML están en [`docs/diagrams/`](docs/diagrams/):

- [`network-topology.puml`](docs/diagrams/network-topology.puml) - el
  camino completo desde la Palm hasta el servidor de origen y dónde ocurre
  la interceptación.
- [`protocol-sequence.puml`](docs/diagrams/protocol-sequence.puml) - un
  intercambio de petición/respuesta.
- [`pagination-flow.puml`](docs/diagrams/pagination-flow.puml) - cómo se
  dividen las páginas largas en tarjetas enlazadas.

Renderízalos con el CLI/extensión de [PlantUML](https://plantuml.com/), o
pega su contenido en un renderizador de PlantUML online.

## Compatibilidad

Este gateway está ajustado contra una versión específica de Rover 1.5.
Otros navegadores WAP 1.x (Blazer, Xiino/Palmscape, Openwave, stacks
genéricos de teléfonos de gama media) muy probablemente necesiten una
IP/puerto objetivo distinta y puede que tengan requisitos de framing WTP
de bajo nivel diferentes - ver
["Compatibilidad con otros clientes"](docs/PROTOCOLO.md#compatibilidad-con-otros-clientes)
en las notas del protocolo para ver qué es reutilizable y qué no.

## Limitaciones conocidas

- Sin segmentación WTP Clase 2 (con confirmación) - depende de la
  paginación del lado del servidor en su lugar (ver
  [`docs/PROTOCOLO.md §5`](docs/PROTOCOLO.md#5-el-techo-de-un-solo-paquete-y-por-que-existe-la-paginacion)).
- Solo renderizan las imágenes WBMP; GIF/PNG/JPEG muestran un placeholder.
- El techo de tamaño de un solo paquete (~975 bytes) se determinó
  empíricamente contra un dispositivo/enlace PPP específico y puede no
  generalizar.
- Sin soporte de formularios/POST, sin WMLScript, sin cookies/manejo de
  sesión.

## Licencia

Ver [`LICENSE`](LICENSE). Este proyecto no está afiliado, respaldado ni
asociado con Palm Inc., Neomar Inc., ni ninguno de sus sucesores.

## Créditos

Los detalles del formato de red de WTP/WSP/WBXML se contrastaron contra el
código fuente del gateway open-source WAP/SMS [Kannel](https://www.kannel.org/)
(`gw/wtp_pdu.def`, `gw/wsp_pdu.def`, `wml/wml_definitions.h`) y su cliente
de referencia `test/fakewap.c`, y el registro de content-types WINA del WAP
Forum. Nada de ese código está copiado aquí - solo se usaron como
referencia los layouts de campos de PDU documentados y las tablas de
tokens conocidos, mientras se hacía ingeniería inversa del comportamiento
real de Rover en la red.
