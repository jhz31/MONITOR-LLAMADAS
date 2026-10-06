# Monitor de Llamadas IDT — Unimonserrate

Sistema que muestra en tiempo real, en el televisor de la oficina de mesa de ayuda, quién está llamando y a qué extensión, aprovechando la función **Action URL** de los teléfonos Yealink. Además lleva un historial completo y clasificado (contestadas / perdidas) consultable desde el navegador.

## Problema que resuelve

Antes, cuando llamaban a cualquier extensión del área de IDT, nadie en la oficina se enteraba quién llamaba ni a qué número a menos que corrieran a contestar. Este sistema muestra esa información automáticamente en el TV, en tiempo real, con sonido, y conserva un historial completo para análisis posterior.

## Arquitectura

El proyecto tiene dos componentes independientes que trabajan juntos:

```
Teléfonos Yealink  --Action URL-->  server.py (Flask + SQLite, Docker)  <--polling--  cliente_popup.py (Tkinter, PC del TV)
                                              |
                                              +--> Monitor en Vivo (web)
                                              +--> Historial (web)
                                              +--> Exportar CSV
```

### 1. `server.py` — Servidor central (Flask + SQLite, en Docker)

Recibe las notificaciones Action URL que los teléfonos Yealink disparan automáticamente al sonar, contestarse, colgarse o perderse una llamada, guarda todo en SQLite y expone tanto la interfaz web como la API que consume el cliente del TV.

**Responsabilidades clave:**

- **Agrupación de llamadas simultáneas:** si suena en varias extensiones del equipo a la vez, espera 2 segundos (`VENTANA_AGRUPACION_SEG`) y agrupa el evento bajo la extensión de grupo `1128` ("Área de IDT") en vez de mostrar cada teléfono por separado.
- **Estados de llamada:** cada registro pasa por `sonando` → `contestada` **o** `perdida`, actualizado en la base de datos en el instante exacto en que se resuelve (no después, por polling).
- **Reconciliación automática:** si el servidor se reinicia con llamadas a medias en memoria, las reclasifica como `perdida` en vez de dejarlas "sonando" para siempre.
- **Rutas Action URL de los teléfonos:**
  - `GET /llamada` — Incoming Call
  - `GET /estado?evento=contestada|colgada|perdida|rechazada` — Call Established / Terminated
  - `GET /colgada?estado=NOANSWER|BUSY|ANSWER` — variante para centrales/PBX que reportan el resultado final
- **API para el cliente del TV:**
  - `GET /api/ultimas` — últimas 30 llamadas + KPIs
  - `GET /api/finalizadas` — ids de llamadas que ya terminaron (para cerrar popups)
  - `GET /api/perdidas` — llamadas perdidas recientes (para el aviso de 60 s)
- **Interfaz web:**
  - `/` — Monitor en Vivo: 4 KPIs (llamadas hoy, extensión más frecuente, última llamada, perdidas hoy), pestañas Todas/Contestadas/Perdidas, tarjetas con badge de color según el resultado.
  - `/historial` — tabla completa de todas las llamadas registradas.
  - `/exportar/csv` — descarga el historial completo en CSV.

### 2. `cliente_popup.py` — Cliente del TV (Python + Tkinter)

Programa de escritorio (no es una página web) que corre en el PC conectado al televisor por HDMI. Detecta automáticamente cuál pantalla es el TV y dibuja ahí tarjetas emergentes sin bordes de ventana, siempre encima de todo, con sonido.

**Tres hilos en paralelo:**

| Hilo | Función | Frecuencia |
|---|---|---|
| `consultar_servidor_loop` | Detecta llamadas nuevas y muestra la tarjeta "🔔 Llamada entrante" | cada 0.25 s |
| `vigilar_finalizadas_loop` | Detecta cuándo una llamada terminó: la cierra si fue contestada, o la **convierte** en aviso de "❌ Llamada perdida" si nadie contestó | cada 0.25 s |
| `consultar_perdidas_loop` | Respaldo: revisa directamente llamadas perdidas recientes por si el otro hilo no alcanzó a detectarlas | cada 0.25 s |

**Comportamiento de las tarjetas:**

- Las llamadas de extensiones de IDT (`EXTENSIONES_IDT`) son **persistentes**: no se cierran solas a los pocos segundos, esperan a que la llamada se resuelva de verdad (máximo 35 s de seguridad local, `MAX_PERSISTENTE_MS`).
- Si nadie contesta, la misma tarjeta se transforma en un aviso rojo de "Llamada perdida" que permanece 30 segundos (`DURACION_AVISO_PERDIDA_MS`) y desaparece sola, o al hacer clic sobre ella.
- El audio se reproduce con **Pygame**: si falla, usa `winsound` de Windows como respaldo, y si no encuentra ningún archivo `.wav` en la carpeta, suena un beep del sistema.
- La posición en pantalla se calcula para no superponer varias tarjetas a la vez (control de "slots" ocupados).

## Requisitos

**Servidor** (`requirements.txt`):
- Python 3 + Flask
- Despliegue vía Docker / `docker-compose.yml`

**Cliente del TV:**
- Python 3 con `tkinter`, `requests`, `pygame` (opcional, con `winsound` como respaldo)
- Windows (usa la API `user32.dll` para detectar el monitor del TV)

## Puesta en marcha

### Servidor

```bash
cd MONITORLLAMADASIDT
docker-compose up -d --build
```

La base de datos SQLite se crea sola en el primer arranque (`data/llamadas.db`), y las migraciones de esquema (como la columna `estado`) se aplican automáticamente sin perder el historial existente.

Variables de entorno disponibles (`docker-compose.yml`):
- `DB_PATH` — ruta del archivo SQLite
- `TIMEOUT_TIMBRE_SEG` — segundos de timbre sin respuesta antes de marcar la llamada como perdida automáticamente (por defecto 45 s)

### Cliente del TV

1. Conectar el TV al PC por HDMI (segunda pantalla, modo "Ampliar").
2. Colocar `audio_adjunto.wav` en la misma carpeta que `cliente_popup.py` (o cualquier `.wav`, se detecta automáticamente).
3. Editar `SERVIDOR_URL` en `cliente_popup.py` con la IP del servidor.
4. Ejecutar:

```bash
python cliente_popup.py
```

### Configuración en los teléfonos Yealink

En la web de administración de cada teléfono, pestaña **Características → URL de acción**, configurar:

| Evento Yealink | Campo | URL |
|---|---|---|
| Llamada entrante | Incoming Call | `http://<IP_SERVIDOR>:5000/llamada?ext=$local&numero=$remote&nombre=$remote_name` |
| Llamada contestada | Call Established | `http://<IP_SERVIDOR>:5000/estado?evento=contestada&ext=$local&numero=$remote` |
| Llamada terminada / perdida | Call Terminated | `http://<IP_SERVIDOR>:5000/estado?evento=colgada&ext=$local&numero=$remote` |

## Extensiones configuradas

| Extensión | Persona |
|---|---|
| 1145 | Líder Soporte - NOMBRE DE LA PERSONA |
| 1144 | Practicante - NOMBRE DE LA PERSONAa |
| 1146 | Líder Infraestructura - NOMBRE DE LA PERSONA |
| 1143 | Practicante - NOMBRE DE LA PERSONA |
| 1142 | Líder Sistemas - NOMBRE DE LA PERSONA |
| 1128 | Grupo — Área de IDT (extensión que agrupa el timbrado simultáneo) |

## Estructura del repositorio

```
MONITORLLAMADASIDT/
├── server.py              # Servidor Flask + SQLite (API + interfaz web)
├── cliente_popup.py        # Cliente de escritorio para el TV (Tkinter)
├── docker-compose.yml      # Despliegue del servidor
├── Dockerfile
├── requirements.txt
└── audio_adjunto.wav       # Sonido de notificación
```

## Estado del proyecto

Funcional y en producción: proyección en TV, clasificación automática de llamadas por estado, KPIs e historial exportable. Próximas mejoras evaluadas: reforzar la central telefónica (PBX) como fuente adicional de eventos, y columna de estado/filtros también en la vista de Historial.
