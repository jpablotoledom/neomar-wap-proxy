# Notas de Protocolo: Ingeniería Inversa de Neomar / Rover 1.5

Este documento registra lo que se descubrió, mediante captura de paquetes y
prueba y error, sobre el transporte WAP que el navegador "Neomar" (llamado
internamente **Rover 1.5**) incluido en algunos CD de software clásicos de
Palm OS realmente habla. No es documentación oficial - las specs del WAP
Forum describen la forma general del protocolo, pero Rover se desvía de
ellas en algunos puntos específicos e indocumentados que este gateway debe
replicar byte a byte.

Si estás intentando construir un puente similar para un cliente WAP 1.x
*distinto*, lee primero
[Compatibilidad con otros clientes](#compatibilidad-con-otros-clientes) -
la mayor parte de lo que sigue es específico de Rover.

## 1. Por qué los trucos de DNS/HTTP no funcionan

El botón "Start" de Rover manda una petición a una **IP y puerto UDP fijos
grabados en el propio programa**, no a un hostname que resuelva por DNS.
Una captura de paquetes del lado de la Palm (vía el log de Softick PPP, ver
[CONFIGURACION.md](CONFIGURACION.md)) mostró:

```
UDP_IN: 10.0.0.1:1035 -> 165.160.13.20:49300
UDP_IN: 10.0.0.1:1036 -> 165.160.15.20:49300
```

Dos IPs de destino distintas, alternadas entre intentos, siempre puerto
**49300** - no los puertos "estándar" de WAP (9200/9201). Ambos registros
DNS de `wap.palm.com` llevan años muertos, y las IPs crudas de arriba fueron
reasignadas a infraestructura sin relación (una hace ahora un redirect 301
hacia un dominio ajeno). Ya no hay forma de alcanzar el backend real de
Neomar; la única opción es hacerse pasar por él.

Como el destino es una IP literal, **editar `hosts` o correr un resolver
DNS propio no tiene ningún efecto** - Rover nunca hace una consulta DNS
para esta conexión. La interceptación tiene que ocurrir a nivel de
IP/enrutamiento en su lugar (ver [CONFIGURACION.md](CONFIGURACION.md) para
el truco de `ip addr add` / `route add` / `arp -s` usado aquí).

## 2. Transporte: WTP sobre UDP crudo

Una vez que el tráfico llega al gateway, resulta que el payload es
[WTP](https://es.wikipedia.org/wiki/Wireless_Transaction_Protocol)
(Wireless Transaction Protocol) transportando un mensaje
[WSP](https://en.wikipedia.org/wiki/Wireless_Session_Protocol)
(Wireless Session Protocol), todo en un único datagrama UDP sin cifrar - el
stack clásico "sin conexión" de WAP 1.x.

### 2.1 Cabecera WTP

La cabecera WTP de cuatro bytes en un PDU **Invoke** (una petición del
cliente) está estructurada, según la spec WAP-224-WTP, así:

| Bits | Campo | Notas |
|---|---|---|
| 1 | `con` | Bandera de TPIs presentes (siempre 0 observado) |
| 4 | `type` | Tipo de PDU; `1` = Invoke, `2` = Result, `6` = Segmented_result |
| 1 | `gtr` | Group trailer |
| 1 | `ttr` | Transmission trailer (último paquete del mensaje) |
| 1 | `rid` | Indicador de retransmisión |
| 16 | `tid` | ID de transacción |
| 8 | (solo Invoke) | byte empacado version/tidnew/uack/reserved/class |

Una petición real capturada se veía así:

```
0f 00 01 0e 40 14 68 74 74 70 3a 2f 2f ...
^0 ^1 ^2 ^3 ^4 ^5 ^^^^^^^^^^^^^^^^^^^^^
|                 \-- "http://..." (la URL pedida)
|                 \- el payload WSP empieza aqui
\- cabecera WTP (4 bytes para Invoke)
```

`byte[0] = 0x0f` decodifica a `type = 0x0f >> 3 = 1` (Invoke), con los 3
bits bajos (`gtr=1, ttr=1, rid=1`) como flags. `byte[3]` es técnicamente el
último byte del campo trailer exclusivo de Invoke, pero en la práctica se
comporta como un contador incremental por intento (`0x0d`, luego `0x0e` en
el reintento) - trátalo como opaco y simplemente **refléjalo tal cual en
la respuesta**, no intentes interpretar sus sub-campos.

### 2.2 La particularidad crítica, no documentada

La cabecera **Result** oficial de la spec WAP-224-WTP tiene solo **3
bytes** (`con+type+gtr+ttr+rid`, luego un TID de 16 bits - sin byte final,
a diferencia de Invoke). Se probó primero una cabecera Result de 3 bytes
estrictamente conforme a la spec, y **Rover la ignoró silenciosamente**
("cargando..." eterno). Lo que realmente funciona es una cabecera Result
de **4 bytes** que refleja los bytes 1-3 del Invoke *tal cual*, incluyendo
ese byte "extra" que Result no debería tener según la spec:

```python
def build_wtp_result(invoke_header4: bytes) -> bytes:
    flags = invoke_header4[0] & 0x07
    b0 = (2 << 3) | flags          # cambia tipo de PDU Invoke(1) -> Result(2)
    return bytes([b0]) + invoke_header4[1:4]   # refleja TID + el byte extra
```

Además, el **bit de indicador de retransmisión (`rid`) debe reflejarse**
de la petición, no limpiarse. Una respuesta con `rid=0` (calzando con una
plantilla de referencia "limpia" y probada de Kannel/fakewap.c) fue
**rechazada de plano** (Rover seguía reintentando eternamente); reflejar el
`rid=1` de la petición hizo que Rover aceptara la respuesta como válida
para su Invoke. Esto sugiere que la lógica de correlación de TID de Rover
usa más que solo el campo TID, a diferencia de una implementación fiel a
la spec.

**Resumen: al construir un PDU Result, no lo reconstruyas desde cero -
copia los primeros 4 bytes del Invoke y solo cambia el nibble del tipo de
PDU.**

### 2.3 Payload WSP: petición Get

Justo después de la cabecera WTP, el PDU de método WSP (WAP-230-WSP,
"Get"):

```
byte[4] = 0x40         # Tipo de PDU WSP: Get
byte[5] = uri_len       # uintvar (un solo byte aqui, la URL siempre < 128 caracteres)
byte[6:6+uri_len]       # la URI en si, en ASCII plano
... siguen los headers WSP (Accept, User-Agent, etc, codificados en WSP)
```

### 2.4 Payload WSP: Reply

El PDU WSP **Reply** (según WAP-230-WSP, `TYPE(8,4)`):

```
PDU-Type   (1 byte)   = 0x04
Status     (1 byte)   = 0x20  (=200 OK; los codigos de estado WSP NO son el
                                mismo espacio numerico que HTTP - 0x20
                                equivale casualmente a 32 decimal pero
                                representa "200 OK")
Headers-Length (uintvar)  <- facil de olvidar! Sin esto, Rover no puede
                              saber donde terminan los headers y empieza
                              el cuerpo, y corrompe el render en silencio
                              ("WML not supported" o salida ilegible).
Headers    (headers_len bytes) = Content-Type como entero corto conocido,
                                  ej. 0x80 | 0x14 para application/vnd.wap.wmlc
Data       (resto del paquete) = el cuerpo codificado en WBXML o WBMP
```

```python
def build_wsp_reply(body: bytes, status=0x20, content_type=0x14) -> bytes:
    headers = bytes([0x80 | content_type])
    headers_len = bytes([len(headers)])
    return bytes([0x04, status]) + headers_len + headers + body
```

## 3. Códigos de Content-Type usados

Del registro WINA "WSP Content-Type Numbers" del WAP Forum:

| Content type | Código |
|---|---|
| `text/vnd.wap.wml` (WML fuente sin compilar) | `0x08` |
| `application/vnd.wap.wmlc` (WML compilado / WBXML) | `0x14` |
| `image/vnd.wap.wbmp` | `0x21` |

Rover acepta `0x08` (WML en texto plano) a nivel de transporte, pero
**rechaza el contenido real** con un error "WML not supported" - solo
renderiza la **forma binaria compilada (WBXML, `0x14`)**. Ver
[§4](#4-compilacion-wml---wbxml) abajo.

## 4. Compilación WML -> WBXML

[WBXML](https://en.wikipedia.org/wiki/WBXML) es una tokenización binaria
genérica de XML. Para WML específicamente, el WAP Forum asigna códigos
fijos de un byte a cada nombre de tag y de atributo (ver `WML_ELEMENTS` /
`WML_ATTRS` en [`src/neomar_wap_gateway.py`](../src/neomar_wap_gateway.py),
tomadas de las tablas `wml_definitions.h` del gateway Kannel).

Estructura del documento:

```
[version WBXML=0x01] [Public ID=0x04 (WML 1.1)] [Charset=0x6A (UTF-8)] [long. tabla strings=0x00]
[byte tag] [atributos...] [END] [contenido...] [END]
```

Un byte de tag es `tag_code | 0x80` si tiene atributos, `| 0x40` si tiene
contenido (hijos/texto), o ambos combinados con OR. Los atributos se
codifican como pares `[attr_code][string STR_I][NUL]` terminados con `END
(0x01)`; el contenido de texto usa la misma codificación de string inline
`STR_I (0x03) ... NUL`.

Por simplicidad, este gateway **siempre codifica los valores de atributos
como strings inline**, incluso para atributos que tienen un token de
"valor conocido" más corto disponible (ej. `align="center"`) - cuesta
algunos bytes extra por atributo pero evita una tabla de búsqueda mucho más
grande y funciona sin excepciones.

## 5. El techo de un solo paquete (y por qué existe la paginación)

WTP soporta segmentación (tipos de PDU 5/6, `Segmented_invoke` /
`Segmented_result`) para mensajes que abarcan varios datagramas UDP, con un
número de secuencia de paquete de 1 byte por segmento. Este gateway **lo
implementó y probó** - y encontró que **Rover solo renderiza el primer
paquete** de una respuesta segmentada, descartando el resto en silencio,
sin importar los siguientes ajustes:

- Demoras de espaciado entre paquetes (para descartar overflow de buffer
  del enlace serial)
- Varias codificaciones de flags/PSN para los paquetes de continuación
- Si el bit `ttr` del último paquete de continuación estaba activado

La teoría de trabajo es que la implementación SAR (segmentación y
reensamblado) de Rover espera un intercambio de confirmación (WTP Clase 2,
con el cliente devolviendo PDUs Ack/Negative-ack entre segmentos) que este
gateway no implementa, y simplemente descarta los paquetes de continuación
no solicitados que nunca negoció.

En vez de implementar la confiabilidad completa de Clase 2, **el contenido
se pagina del lado del servidor**: las páginas largas se dividen en varias
"tarjetas" independientes del tamaño de un solo paquete con enlaces
`<< Anterior` / `Siguiente >>` (ver `paginate()` en el código fuente del
gateway, y
[`diagrams/pagination-flow.puml`](diagrams/pagination-flow.puml)).

### 5.1 Cómo se encontró el techo de tamaño

Usando una página sintética tipo "regla" (`[0]xxxx...[50]xxxx...[100]...`,
para que un punto de corte sea visible de inmediato como una posición de
carácter), el máximo payload WBXML confiable de un solo paquete se
encontró por bisección en un dispositivo real a lo largo de varias
iteraciones:

| Bytes WBXML | Resultado |
|---|---|
| 291 | OK |
| 500 | **se cuelga (regresión a nivel de transporte por un bug de cabecera no relacionado, superado por pruebas posteriores)** |
| 935 | OK |
| 975 | OK |
| 986 | **falla ("incomplete data transfer" / carga eterna)** |
| 990 | **falla** |

El techo que funciona está justo alrededor de **975-990 bytes** de payload
WSP completo (sin contar la cabecera WTP). Se usa `SAFE_PAGE_BYTES = 860`
como objetivo por defecto, dejando margen para los enlaces de navegación
`<< Anterior` / `Siguiente >>` que se agregan después de la decisión de
empaquetado, más margen de seguridad - esta es una constante blanda,
ajustada empíricamente, no un límite documentado del protocolo, y puede
necesitar reajuste contra una build distinta de Rover o el MTU de un
enlace PPP diferente.

## 6. Imágenes WBMP

[WBMP](https://en.wikipedia.org/wiki/Wireless_Application_Protocol_Bitmap_Format)
ya es el formato de bitmap nativo de 1 bit de WAP, así que las URLs
`.wbmp` se reenvían tal cual como bytes crudos con `content_type = 0x21` -
**sin necesidad de transcodificación**, a diferencia de Xiino/OpenXiino que
convierte PNG/JPEG arbitrarios a un formato propietario.

Dos consecuencias:

1. **Otros formatos raster (GIF/PNG/JPEG) no son renderizables en
   absoluto** por un cliente WAP 1.x - el gateway devuelve una tarjeta de
   placeholder para esos casos en vez de intentar convertirlos al momento
   de la petición.
2. Las imágenes más grandes que el techo de un solo paquete (§5)
   simplemente no se mostrarán, ya que un WBMP es un blob binario opaco que
   no se puede paginar como el texto. **Reduce y aplica dithering a las
   imágenes de origen offline** con
   [`tools/png2wbmp.py`](../tools/png2wbmp.py) en vez de depender del
   helper opcional `shrink_wbmp_to_fit()` del gateway que reescala al
   vuelo - aplicar dithering a una imagen de origen de calidad completa da
   resultados visiblemente mejores que aplicarlo a una imagen que ya pasó
   por una cuantización con pérdida una vez.

## 7. User-Agent

El string de User-Agent propio de Rover, observado tal cual en sus
peticiones:

```
Rover 1.5 (Palm; IP; OS v. 3.5.3)
```

El gateway reenvía este mismo string al servidor HTTP de origen. Algunos
sitios WAP - incluido el sitio de prueba WML retro contra el que se
construyó este gateway - detectan el User-Agent (o un header
`Accept: text/vnd.wap.wml`) para decidir si servir WML o HTML normal;
reenviar el UA real de Rover es lo que hace que esa negociación de
contenido funcione correctamente en el origen.

## Compatibilidad con otros clientes

Todo en §1-2 (IP:puerto fijos, la particularidad de la cabecera Result de
4 bytes, el reflejo de `rid`, que `Segmented_result` no sea confiable) es
específico de cómo se comporta **esta build particular de Rover 1.5**,
descubierto empíricamente contra un dispositivo real. Es muy probable que
un navegador WAP 1.x distinto (Blazer, Palmscape/Xiino, Openwave, un stack
genérico de teléfono de gama media):

- Apunte a una dirección de gateway **configurable**, típicamente en los
  puertos estándar 9200/9201 UDP (WSP) en vez de 49300 - lo que significa
  que *puede* simplemente apuntarse a la IP de este gateway sin necesitar
  el truco de interceptación por ruta/ARP.
- Siga la cabecera Result de 3 bytes y la correlación de TID de la spec de
  forma más fiel - o de forma distinta pero infiel a su manera.
- Realmente implemente confirmaciones WTP de Clase 2, en cuyo caso
  `Segmented_result` podría funcionar bien (a diferencia de con Rover) y
  las páginas de tamaño completo no necesitarían dividirse.

El compilador WML→WBXML (§4) y la lógica de traer contenido HTTP/negociar
contenido son genéricos del protocolo y reutilizables tal cual; la capa de
framing WTP/WSP (§2) debe tratarse como un **punto de partida para probar
y ajustar**, no asumirse que funcionará sin modificaciones, contra
cualquier otro cliente.
