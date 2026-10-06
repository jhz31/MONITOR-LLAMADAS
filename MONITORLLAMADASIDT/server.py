# SERVIDOR CENTRAL (para Docker / servidor Linux institucional).
# Recibe las notificaciones "Action URL" de los telefonos Yealink,
# guarda TODO el historial en SQLite, agrupa llamadas simultaneas a
# un numero de grupo (ej. 1128 -> "Area de IDT"), y expone la interfaz web.

import os
import re
import csv
import io
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, render_template_string, Response

app = Flask(__name__)

# --- Configuración de extensiones ---
EXTENSIONES = {
    "1145": "Líder Soporte - Esteban Preciado",
    "1144": "Practicante - Jhon Quejada",
    "1146": "Líder Infraestructura - William Rincón",
    "1143": "Practicante - Elber Piza",
    "1142": "Líder Sistemas - Isabella González",
}

EXTENSION_GRUPO_IDT = "1128"
NOMBRE_GRUPO_IDT = "Área de IDT"
EXTENSIONES_DEL_GRUPO = {"1145", "1144", "1146", "1143", "1142"}
VENTANA_AGRUPACION_SEG = 2.0

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data" / "llamadas.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_llamadas_pendientes = {}
_lock_pendientes = threading.Lock()

# --- Estado de llamadas en curso (solo memoria, no toca la base de datos) ---
# Sirve para saber cuando una llamada fue contestada o colgada y asi cerrar
# la ventana emergente persistente del cliente.
TIMEOUT_MAX_LLAMADA_SEG = 300   # seguridad maxima; el fin normal llega por Action URL

# Tiempo de timbre sin respuesta: si nadie contesta en este tiempo, la llamada se
# marca como PERDIDA y el TV cambia la ventana entrante a "LLAMADA PERDIDA".
TIMEOUT_TIMBRE_SEG = int(os.environ.get("TIMEOUT_TIMBRE_SEG", "45"))
_llamadas_en_curso = []         # registros de llamadas que aun estan sonando
_ids_finalizadas = {}           # id_llamada -> momento en que finalizo
_lock_estado = threading.Lock()

# --- Llamadas perdidas (aviso en el TV durante 60 s) ---
TIMEOUT_PERDIDA_SEG = 70        # conserva el aviso de llamada perdida unos segundos para que el cliente lo vea
_llamadas_perdidas = {}         # id_llamada -> {"datos": {...}, "ts": momento del registro}

def obtener_hora_local():
    try:
        return datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def conectar_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def iniciar_db():
    with conectar_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llamadas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT NOT NULL,
                extension  TEXT,
                area       TEXT,
                numero     TEXT,
                nombre     TEXT,
                estado     TEXT DEFAULT 'sonando'
            )
        """)
        # Migracion: si la base de datos ya existia de antes (sin la columna "estado"),
        # la agregamos ahora sin perder ningun registro anterior.
        columnas = [fila["name"] for fila in conn.execute("PRAGMA table_info(llamadas)").fetchall()]
        if "estado" not in columnas:
            conn.execute("ALTER TABLE llamadas ADD COLUMN estado TEXT DEFAULT 'sonando'")
        # Al arrancar no hay llamadas en curso en memoria: lo que quedó "sonando" es huérfano
        conn.execute("UPDATE llamadas SET estado = 'perdida' WHERE estado = 'sonando'")
        conn.commit()

iniciar_db()

def guardar_llamada(evento, estado="sonando"):
    with conectar_db() as conn:
        cur = conn.execute(
            "INSERT INTO llamadas (fecha_hora, extension, area, numero, nombre, estado) VALUES (?, ?, ?, ?, ?, ?)",
            (evento["fecha_hora"], evento["extension"], evento["area"], evento["numero"], evento["nombre"], estado),
        )
        conn.commit()
        return cur.lastrowid

def actualizar_estado_llamada(id_llamada, estado):
    """Actualiza el resultado real de la llamada una vez se sabe (contestada o perdida)."""
    if id_llamada is None:
        return
    with conectar_db() as conn:
        conn.execute("UPDATE llamadas SET estado = ? WHERE id = ?", (estado, id_llamada))
        conn.commit()

def reconciliar_sonando():
    """Todo registro 'sonando' que ya no esta en curso en memoria pasa a 'perdida'."""
    with _lock_estado:
        _limpiar_estado()
        ids_activos = {r["id"] for r in _llamadas_en_curso if r["id"] is not None}
    with conectar_db() as conn:
        filas = conn.execute("SELECT id FROM llamadas WHERE estado = 'sonando'").fetchall()
        for f in filas:
            if f["id"] not in ids_activos:
                conn.execute("UPDATE llamadas SET estado = 'perdida' WHERE id = ?", (f["id"],))
        conn.commit()

def obtener_ultimas(cantidad=30):
    reconciliar_sonando()
    with conectar_db() as conn:
        filas = conn.execute("SELECT * FROM llamadas ORDER BY id DESC LIMIT ?", (cantidad,)).fetchall()
    return [dict(f) for f in filas]

def obtener_kpis():
    reconciliar_sonando()
    hoy = datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d") if os.environ.get("TZ") else datetime.now().strftime("%Y-%m-%d")
    with conectar_db() as conn:
        total_hoy = conn.execute("SELECT COUNT(*) AS c FROM llamadas WHERE fecha_hora LIKE ?", (f"{hoy}%",)).fetchone()["c"]

        top_ext = conn.execute(
            "SELECT extension, area, COUNT(*) as cant FROM llamadas WHERE fecha_hora LIKE ? GROUP BY extension ORDER BY cant DESC LIMIT 1",
            (f"{hoy}%",)
        ).fetchone()

        ult_llamada = conn.execute("SELECT fecha_hora FROM llamadas ORDER BY id DESC LIMIT 1").fetchone()

        perdidas_hoy = conn.execute(
            "SELECT COUNT(*) AS c FROM llamadas WHERE fecha_hora LIKE ? AND estado = 'perdida'",
            (f"{hoy}%",)
        ).fetchone()["c"]

    ext_mas_frecuente = f"Ext. {top_ext['extension']} ({top_ext['cant']})" if top_ext else "Sin registros hoy"
    ultima_hora = ult_llamada["fecha_hora"].split(" ")[1] if ult_llamada else "N/A"

    return {
        "total_hoy": total_hoy,
        "top_extension": ext_mas_frecuente,
        "ultima_hora": ultima_hora,
        "perdidas_hoy": perdidas_hoy
    }

def obtener_historial(limite=5000):
    reconciliar_sonando()
    with conectar_db() as conn:
        filas = conn.execute("SELECT * FROM llamadas ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM llamadas").fetchone()["c"]
    return [dict(f) for f in filas], total

def limpiar_numero(valor):
    if not valor:
        return valor
    m = re.search(r"(\d{2,})", valor)
    return m.group(1) if m else valor

def arreglar_acentos(texto):
    if not texto:
        return texto
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return texto


def _registrar_perdida(rec):
    """Guarda la llamada perdida para que el TV muestre el aviso 60 s (con _lock_estado tomado)."""
    if rec["id"] is None or rec.get("contestada"):
        return
    _llamadas_perdidas[rec["id"]] = {
        "datos": dict(rec["info"], id=rec["id"]),
        "ts": time.time(),
    }

def _finalizar_registro(rec):
    """Marca una llamada como finalizada (llamar con _lock_estado tomado)."""
    rec["activa"] = False
    estado_final = "contestada" if rec.get("contestada") else "perdida"
    rec["estado_final"] = estado_final
    if rec["id"] is not None:
        _ids_finalizadas[rec["id"]] = time.time()
        actualizar_estado_llamada(rec["id"], estado_final)
        # Si nadie contesto, queda registrada como llamada perdida (aviso de 60 s en el TV)
        _registrar_perdida(rec)

def _limpiar_estado():
    """Aplica el tiempo maximo y limpia registros viejos (llamar con _lock_estado tomado)."""
    ahora = time.time()
    for rec in _llamadas_en_curso:
        # Si nadie contesta dentro del tiempo de timbre, queda como llamada perdida
        if rec["activa"] and ahora - rec["inicio"] > TIMEOUT_TIMBRE_SEG:
            _finalizar_registro(rec)
    _llamadas_en_curso[:] = [r for r in _llamadas_en_curso if r["activa"]]
    for id_llamada in [i for i, t in _ids_finalizadas.items() if ahora - t > 600]:
        del _ids_finalizadas[id_llamada]
    for id_llamada in [i for i, p in _llamadas_perdidas.items() if ahora - p["ts"] > TIMEOUT_PERDIDA_SEG]:
        del _llamadas_perdidas[id_llamada]

def procesar_llamada_agrupada(numero):
    with _lock_pendientes:
        info = _llamadas_pendientes.pop(numero, None)
    if not info:
        return
    evento = info["evento"]
    if len(info["extensiones"] & EXTENSIONES_DEL_GRUPO) >= 2:
        evento["extension"] = EXTENSION_GRUPO_IDT
        evento["area"] = NOMBRE_GRUPO_IDT
    rec = info.get("registro")
    estado_inicial = "sonando"
    if rec is not None and not rec["activa"] and rec.get("estado_final"):
        # La llamada ya se resolvio (contestada o perdida) antes de terminar de agruparse
        estado_inicial = rec["estado_final"]
    evento["id"] = guardar_llamada(evento, estado_inicial)
    if rec is not None:
        with _lock_estado:
            rec["id"] = evento["id"]
            if evento["extension"] == EXTENSION_GRUPO_IDT:
                rec["info"]["extension"] = EXTENSION_GRUPO_IDT
                rec["info"]["area"] = NOMBRE_GRUPO_IDT
            if not rec["activa"]:
                _ids_finalizadas[rec["id"]] = time.time()
                # Si ya habia terminado sin ser contestada, queda como llamada perdida
                _registrar_perdida(rec)

@app.route("/llamada", methods=["GET"])
@app.route("/api/llamada", methods=["GET"])
def registrar_llamada():
    ext = limpiar_numero(request.args.get("ext", "")) or "desconocida"
    numero = limpiar_numero(request.args.get("numero", "")) or "desconocido"
    nombre = arreglar_acentos(request.args.get("nombre", ""))

    with _lock_pendientes:
        if numero in _llamadas_pendientes:
            _llamadas_pendientes[numero]["extensiones"].add(ext)
        else:
            evento = {
                "fecha_hora": obtener_hora_local(),
                "extension": ext,
                "area": EXTENSIONES.get(ext, "Extensión Individual"),
                "numero": numero,
                "nombre": nombre,
            }
            temporizador = threading.Timer(VENTANA_AGRUPACION_SEG, procesar_llamada_agrupada, args=(numero,))
            temporizador.daemon = True
            exts = {ext}
            registro = {"numero": numero, "exts": exts, "terminadas": set(), "id": None,
                        "inicio": time.time(), "activa": True, "contestada": False,
                        "estado_final": None, "info": dict(evento)}
            with _lock_estado:
                _limpiar_estado()
                _llamadas_en_curso.append(registro)
            _llamadas_pendientes[numero] = {"extensiones": exts, "evento": evento, "temporizador": temporizador, "registro": registro}
            temporizador.start()
    return "OK", 200

def _aplicar_estado(evento, ext, numero):
    """Aplica contestada/colgada/perdida a la llamada en curso (lo usan /estado y /colgada)."""
    with _lock_estado:
        _limpiar_estado()
        candidatos = list(_llamadas_en_curso)
        if numero:
            candidatos = [r for r in candidatos if r["numero"] == numero]
        elif ext:
            candidatos = [r for r in candidatos if ext in r["exts"]]
        if not candidatos:
            return
        rec = candidatos[-1]

        if evento == "contestada":
            rec["contestada"] = True
            _finalizar_registro(rec)
        elif evento in ("perdida", "rechazada"):
            # Nadie contesto (o rechazaron): se cierra TODO el grupo de inmediato
            _finalizar_registro(rec)
        else:
            # "colgada" es el evento definitivo de final de llamada.
            # Para un grupo (1128) NO esperamos a que todos los telefonos
            # del grupo envien el evento: basta con que llegue el fin de la
            # llamada asociado al numero del llamante. Esto permite cambiar
            # el TV a "LLAMADA PERDIDA" inmediatamente.
            #
            # Tambien usamos este comportamiento para llamadas individuales:
            # si el Yealink envia colgada, la llamada ya termino.
            rec["terminadas"].add(ext) if ext else None
            _finalizar_registro(rec)


@app.route("/estado", methods=["GET"])
@app.route("/api/estado", methods=["GET"])
def registrar_estado():
    # Action URL de los telefonos: Call Established -> evento=contestada
    #                              Call Terminated / Missed Call -> evento=colgada
    evento = (request.args.get("evento", "") or "").strip().lower()
    ext = limpiar_numero(request.args.get("ext", "")) or None
    numero = limpiar_numero(request.args.get("numero", "")) or None

    if evento not in ("contestada", "colgada", "perdida", "rechazada"):
        return "evento invalido", 400

    print(f"[ACTION URL] /estado evento={evento} ext={ext or '-'} numero={numero or '-'}")
    _aplicar_estado(evento, ext, numero)

    return "OK", 200


@app.route("/colgada", methods=["GET"])
@app.route("/api/colgada", methods=["GET"])
def registrar_colgada():
    # Fin de llamada explicito (desde la central/PBX o prueba manual):
    #   estado = NOANSWER (nadie contesto) | BUSY (ocupado) | ANSWER (contestaron)
    estado = (request.args.get("estado", "") or "").strip().upper()
    ext = limpiar_numero(request.args.get("ext", "")) or None
    numero = limpiar_numero(request.args.get("numero", "")) or None

    if estado not in ("NOANSWER", "BUSY", "ANSWER"):
        return "estado invalido", 400

    evento = "contestada" if estado == "ANSWER" else "perdida"
    print(f"[ACTION URL] /colgada estado={estado} -> evento={evento} ext={ext or '-'} numero={numero or '-'}")
    _aplicar_estado(evento, ext, numero)

    return "OK", 200

@app.route("/api/finalizadas", methods=["GET"])
def api_finalizadas():
    with _lock_estado:
        _limpiar_estado()
        ids = list(_ids_finalizadas.keys())
    return jsonify({"finalizadas": ids})

@app.route("/api/perdidas", methods=["GET"])
def api_perdidas():
    with _lock_estado:
        _limpiar_estado()
        perdidas = [p["datos"] for p in sorted(_llamadas_perdidas.values(), key=lambda x: x["datos"]["id"])]
    return jsonify({"perdidas": perdidas})

@app.route("/api/ultimas", methods=["GET"])
def api_ultimas():
    return jsonify({
        "llamadas": obtener_ultimas(30),
        "kpis": obtener_kpis()
    })

@app.route("/exportar/csv", methods=["GET"])
def exportar_csv():
    filas, _ = obtener_historial(limite=10000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Fecha y Hora", "Extension", "Area/Usuario", "Numero", "Nombre Caller ID", "Estado"])
    for f in filas:
        writer.writerow([f["id"], f["fecha_hora"], f["extension"], f["area"], f["numero"], f["nombre"], f.get("estado", "")])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=historial_llamadas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return response

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def monitor_en_vivo():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monitor de Llamadas - Unimonserrate IDT</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --primary-blue: #1B365D;
                --primary-green: #70B22D;
                --bg-light: #F4F6F9;
                --card-bg: #FFFFFF;
                --text-dark: #1E293B;
                --text-muted: #64748B;
                --border-color: #E2E8F0;
                --color-rojo: #C0392B;
                --color-rojo-bg: #FDEDEC;
                --color-verde-bg: #EBF5E0;
                --color-ambar: #B7791F;
                --color-ambar-bg: #FEF3DC;
            }
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: 'Inter', sans-serif; background-color: var(--bg-light); color: var(--text-dark); min-height: 100vh; }

            header { background: var(--primary-blue); color: white; padding: 1.2rem 2rem; border-bottom: 4px solid var(--primary-green); display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
            .brand { display: flex; align-items: center; gap: 12px; }
            .brand-logo-text { font-size: 1.8rem; font-weight: 800; color: var(--primary-green); font-style: italic; font-family: serif; }
            .brand-title { font-size: 1.25rem; font-weight: 700; }
            .brand-subtitle { font-size: 0.8rem; opacity: 0.8; font-weight: 400; }

            .header-actions { display: flex; gap: 10px; }
            .nav-btn { background: var(--primary-green); color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 0.9rem; transition: background 0.2s; display: inline-flex; align-items: center; gap: 6px; }
            .nav-btn:hover { background: #5d9724; }
            .nav-btn-secondary { background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.3); }
            .nav-btn-secondary:hover { background: rgba(255,255,255,0.25); }

            main { max-width: 1200px; margin: 1.5rem auto; padding: 0 1rem; }

            /* KPIs */
            .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
            .kpi-card { background: white; padding: 1.2rem; border-radius: 8px; border: 1px solid var(--border-color); box-shadow: 0 2px 4px rgba(0,0,0,0.02); }
            .kpi-title { font-size: 0.8rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
            .kpi-value { font-size: 1.5rem; font-weight: 800; color: var(--primary-blue); margin-top: 4px; }
            .kpi-value.kpi-rojo { color: var(--color-rojo); }

            /* Pestañas de filtro por estado */
            .tabs-bar { display: flex; gap: 8px; margin-bottom: 1rem; }
            .tab-btn { background: white; border: 1px solid var(--border-color); color: var(--text-muted); padding: 8px 16px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; cursor: pointer; transition: all 0.15s; }
            .tab-btn:hover { border-color: var(--primary-green); color: var(--primary-blue); }
            .tab-btn.activa { background: var(--primary-blue); border-color: var(--primary-blue); color: white; }

            /* Barra de Control y Búsqueda */
            .control-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.5rem; background: white; padding: 1rem 1.5rem; border-radius: 8px; border: 1px solid var(--border-color); }
            .status-indicator { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 0.9rem; color: var(--primary-blue); }
            .pulse-dot { width: 10px; height: 10px; background-color: var(--primary-green); border-radius: 50%; animation: pulse 1.8s infinite; }

            .search-box { position: relative; min-width: 280px; }
            .search-input { width: 100%; padding: 8px 12px 8px 36px; border-radius: 6px; border: 1px solid var(--border-color); font-size: 0.9rem; outline: none; transition: border 0.2s; }
            .search-input:focus { border-color: var(--primary-green); }
            .search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 0.9rem; }

            @keyframes pulse {
                0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(112, 178, 45, 0.7); }
                70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(112, 178, 45, 0); }
                100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(112, 178, 45, 0); }
            }

            .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }
            .card { background: var(--card-bg); border-radius: 8px; border: 1px solid var(--border-color); border-left: 5px solid var(--primary-green); padding: 1.25rem; box-shadow: 0 2px 6px rgba(0,0,0,0.03); transition: transform 0.2s, box-shadow 0.2s; }
            .card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
            /* Borde izquierdo dinamico segun el resultado real de la llamada */
            .card.estado-contestada { border-left-color: var(--primary-green); }
            .card.estado-perdida { border-left-color: var(--color-rojo); }
            .card.estado-sonando { border-left-color: var(--color-ambar); }
            .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem; gap: 6px; flex-wrap: wrap; }
            .card-ext { font-weight: 700; color: var(--primary-blue); font-size: 1.1rem; }
            .card-badges { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
            .card-badge { background: var(--color-verde-bg); color: var(--primary-green); padding: 4px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; }
            .estado-badge { padding: 4px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; white-space: nowrap; }
            .estado-badge.estado-contestada { background: var(--color-verde-bg); color: var(--primary-green); }
            .estado-badge.estado-perdida { background: var(--color-rojo-bg); color: var(--color-rojo); }
            .estado-badge.estado-sonando { background: var(--color-ambar-bg); color: var(--color-ambar); }
            .card-number { font-size: 1.2rem; font-weight: 600; color: var(--text-dark); margin-bottom: 0.25rem; }
            .card-name { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 0.75rem; }
            .card-time { font-size: 0.8rem; color: var(--text-muted); border-top: 1px solid #F1F5F9; padding-top: 0.5rem; display: flex; justify-content: space-between; }

            .empty-state { text-align: center; padding: 3rem; background: white; border-radius: 8px; border: 1px dashed var(--border-color); color: var(--text-muted); grid-column: 1 / -1; }
        </style>
    </head>
    <body>
        <header>
            <div class="brand">
                <span class="brand-logo-text">U</span>
                <div>
                    <div class="brand-title">Fundación Universitaria Unimonserrate</div>
                    <div class="brand-subtitle">Sistema de Monitoreo de Llamadas - IDT</div>
                </div>
            </div>
            <div class="header-actions">
                <a href="/exportar/csv" class="nav-btn nav-btn-secondary">📥 Exportar CSV</a>
                <a href="/historial" class="nav-btn">📋 Ver Historial</a>
            </div>
        </header>
        <main>
            <!-- Cuadros KPI -->
            <div class="kpi-grid">
                <div class="kpi-card">
                    <div class="kpi-title">Llamadas Hoy</div>
                    <div class="kpi-value" id="kpi-total-hoy">0</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-title">Ext. Más Frecuente (Hoy)</div>
                    <div class="kpi-value" id="kpi-top-ext" style="font-size: 1.1rem;">Cargando...</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-title">Hora Última Llamada</div>
                    <div class="kpi-value" id="kpi-ult-hora">--:--</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-title">Llamadas Perdidas Hoy</div>
                    <div class="kpi-value kpi-rojo" id="kpi-perdidas-hoy">0</div>
                </div>
            </div>

            <!-- Pestañas de filtro por estado -->
            <div class="tabs-bar">
                <button type="button" class="tab-btn activa" data-estado="todas" onclick="cambiarPestana('todas', this)">Todas</button>
                <button type="button" class="tab-btn" data-estado="contestada" onclick="cambiarPestana('contestada', this)">Contestadas</button>
                <button type="button" class="tab-btn" data-estado="perdida" onclick="cambiarPestana('perdida', this)">Perdidas</button>
            </div>

            <!-- Controles y Filtro -->
            <div class="control-bar">
                <div class="status-indicator">
                    <span class="pulse-dot"></span>
                    <span>Monitor en Vivo Activo</span>
                </div>
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="filtro-input" class="search-input" placeholder="Buscar por ext, nombre o número..." onkeyup="filtrarTarjetas()">
                </div>
                <div style="font-size: 0.85rem; color: var(--text-muted);" id="last-update">Sincronizando...</div>
            </div>

            <div id="contenido" class="grid">
                <div class="empty-state">Cargando llamadas de la red...</div>
            </div>
        </main>

        <script>
            let todashLlamadas = [];
            let filtroEstado = 'todas';

            const ETIQUETAS_ESTADO = {
                contestada: '✅ Contestada',
                perdida: '❌ Perdida',
                sonando: '🔔 Sonando'
            };

            async function cargar() {
                try {
                    const res = await fetch('/api/ultimas');
                    const responseData = await res.json();

                    todashLlamadas = responseData.llamadas;
                    const kpis = responseData.kpis;

                    // Actualizar KPIs
                    document.getElementById('kpi-total-hoy').innerText = kpis.total_hoy;
                    document.getElementById('kpi-top-ext').innerText = kpis.top_extension;
                    document.getElementById('kpi-ult-hora').innerText = kpis.ultima_hora;
                    document.getElementById('kpi-perdidas-hoy').innerText = kpis.perdidas_hoy;
                    document.getElementById('last-update').innerText = 'Última sincro: ' + new Date().toLocaleTimeString();

                    renderizarLlamadas(todashLlamadas);
                } catch(e) {
                    console.error("Error cargando llamadas:", e);
                }
            }

            function cambiarPestana(estado, boton) {
                filtroEstado = estado;
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('activa'));
                boton.classList.add('activa');
                renderizarLlamadas(todashLlamadas);
            }

            function renderizarLlamadas(lista) {
                const query = document.getElementById('filtro-input').value.toLowerCase().trim();
                const filtradas = lista.filter(i => {
                    const ext = (i.extension || '').toLowerCase();
                    const num = (i.numero || '').toLowerCase();
                    const nom = (i.nombre || '').toLowerCase();
                    const area = (i.area || '').toLowerCase();
                    const coincideTexto = ext.includes(query) || num.includes(query) || nom.includes(query) || area.includes(query);
                    const coincideEstado = filtroEstado === 'todas' || (i.estado || 'sonando') === filtroEstado;
                    return coincideTexto && coincideEstado;
                });

                if (filtradas.length === 0) {
                    document.getElementById('contenido').innerHTML = '<div class="empty-state">No se encontraron llamadas con ese filtro.</div>';
                    return;
                }

                document.getElementById('contenido').innerHTML = filtradas.map(i => {
                    const estado = i.estado || 'sonando';
                    const etiquetaEstado = ETIQUETAS_ESTADO[estado] || ETIQUETAS_ESTADO.sonando;
                    return `
                    <div class="card estado-${estado}">
                        <div class="card-header">
                            <div class="card-ext">Ext. ${i.extension}</div>
                            <div class="card-badges">
                                <span class="card-badge">${i.area}</span>
                                <span class="estado-badge estado-${estado}">${etiquetaEstado}</span>
                            </div>
                        </div>
                        <div class="card-number">📞 ${i.numero}</div>
                        <div class="card-name">${i.nombre ? i.nombre : 'Sin nombre asignado'}</div>
                        <div class="card-time">
                            <span>Fecha y Hora</span>
                            <strong>${i.fecha_hora}</strong>
                        </div>
                    </div>
                `;
                }).join('');
            }

            function filtrarTarjetas() {
                renderizarLlamadas(todashLlamadas);
            }

            cargar();
            setInterval(cargar, 3000);
        </script>
    </body>
    </html>
    """)

@app.route("/historial", methods=["GET"])
def historial():
    filas, total = obtener_historial()
    filas_html = "".join([
        f"<tr><td>{f['id']}</td><td>{f['fecha_hora']}</td><td><span class='badge'>{f['extension']}</span></td><td>{f['area']}</td><td><strong>{f['numero']}</strong></td><td>{f['nombre']}</td><td>{f['estado']}</td></tr>"
        for f in filas
    ])
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Historial - Unimonserrate IDT</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --primary-blue: #1B365D;
                --primary-green: #70B22D;
                --bg-light: #F4F6F9;
                --border-color: #E2E8F0;
            }}
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: 'Inter', sans-serif; background-color: var(--bg-light); color: #1E293B; }}
            header {{ background: var(--primary-blue); color: white; padding: 1.2rem 2rem; border-bottom: 4px solid var(--primary-green); display: flex; justify-content: space-between; align-items: center; }}
            .brand-title {{ font-size: 1.25rem; font-weight: 700; }}
            .actions {{ display: flex; gap: 10px; }}
            .nav-btn {{ background: var(--primary-green); color: white; padding: 8px 18px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 0.9rem; }}
            .btn-csv {{ background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.3); color: white; }}
            main {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
            .card-table {{ background: white; border-radius: 8px; border: 1px solid var(--border-color); overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.03); }}
            .table-header {{ padding: 1rem 1.5rem; background: #FAF5FF; border-bottom: 1px solid var(--border-color); font-weight: 700; color: var(--primary-blue); display: flex; justify-content: space-between; align-items: center; }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem; }}
            th {{ background: #F8FAFC; color: #475569; padding: 12px 16px; border-bottom: 1px solid var(--border-color); font-weight: 600; }}
            td {{ padding: 12px 16px; border-bottom: 1px solid var(--border-color); color: #334155; }}
            tr:hover {{ background: #F8FAFC; }}
            .badge {{ background: #E2E8F0; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; color: var(--primary-blue); }}
        </style>
    </head>
    <body>
        <header>
            <div class="brand-title">Fundación Universitaria Unimonserrate</div>
            <div class="actions">
                <a href="/exportar/csv" class="nav-btn btn-csv">📥 Exportar CSV</a>
                <a href="/" class="nav-btn">← Monitor en Vivo</a>
            </div>
        </header>
        <main>
            <div class="card-table">
                <div class="table-header">
                    <span>Historial General de Llamadas</span>
                    <span>Total: {total} registros</span>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Fecha y Hora</th>
                            <th>Extensión</th>
                            <th>Área / Usuario</th>
                            <th>Número</th>
                            <th>Nombre</th>
                            <th>Estado</th>
                        </tr>
                    </thead>
                    <tbody>{filas_html}</tbody>
                </table>
            </div>
        </main>
    </body>
    </html>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)