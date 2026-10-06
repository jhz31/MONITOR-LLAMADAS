
import os

import time

import glob

import requests

import tkinter as tk

from threading import Thread



# Intentar cargar Pygame para máxima compatibilidad de audio sin importar la codificación del WAV

try:

    import pygame

    pygame.mixer.init()

    USAR_PYGAME = True

except Exception:

    USAR_PYGAME = False



import winsound



# ---------------------------------------------------------------------

# CONFIGURACIÓN GENERAL

# ---------------------------------------------------------------------

SERVIDOR_URL = "http://192.168.10.119:5000"

## ip telefonos: 
## IDT- 1128
## JHON- 192.168.10.47-1144
## ELBER- 192.168.10.45-1143
## ISABELLA- 192.168.10.67-1142
## ESTEBAN- 192.168.10.58-1145
## WILLIAM- 192.168.10.12-1146

ANCHO, ALTO, MARGEN = 380, 190, 15

popups_activos = []

slots_ocupados = set()          # posiciones de pantalla en uso (evita que se encimen)

popups_persistentes = {}        # id_llamada -> funcion cerrar() de popups que esperan fin de llamada



# Avisos de LLAMADA PERDIDA en el TV (duran 60 s)

AVISOS_PERDIDOS = []            # ventanas de aviso activas

avisos_perdidos_procesados = set()   # ids ya notificados (evita duplicados)

avisos_por_id = {}              # id_llamada -> ventana de aviso (evita duplicados entre hilos)



# Todas las llamadas (grupo 1128 y extensiones fijas): sus popups NO se cierran solos a los 8 s;

# se quedan hasta que la llamada sea contestada o colgada.

EXTENSIONES_IDT = {"1145", "1144", "1146", "1143", "1142", "1128"}

MAX_PERSISTENTE_MS = 35000     # seguridad del cliente; el servidor usa 300 s como maximo

DURACION_AVISO_PERDIDA_MS = 30000   # conserva la interfaz actual: 30 s

ALTO_AVISO = 190                    # alto de la ventana de aviso de perdida (misma tarjeta que la entrante)

ultimo_id_procesado = 0  



# Colores Institucionales Unimonserrate (Alineados con la web)

COLOR_AZUL = "#1B365D"

COLOR_VERDE = "#70B22D"

COLOR_BG_LIGHT = "#F4F6F9"

COLOR_CARD_BG = "#FFFFFF"

COLOR_TEXT_DARK = "#1E293B"

COLOR_TEXT_MUTED = "#64748B"

COLOR_BADGE_BG = "#EBF5E0"



# ---------------------------------------------------------------------

# DETECCIÓN Y REPRODUCCIÓN INTELIGENTE DE AUDIO

# ---------------------------------------------------------------------

def obtener_ruta_audio():

    """Busca automáticamente el archivo de audio subido en la misma carpeta."""

    directorio_actual = os.path.dirname(os.path.abspath(__file__))

   

    # 1. Buscar preferencialmente el archivo audio_adjunto.wav

    ruta_especifica = os.path.join(directorio_actual, "audio_adjunto.wav")

    if os.path.exists(ruta_especifica):

        return ruta_especifica

       

    # 2. Si no lo encuentra, busca cualquier archivo con extensión .wav en la carpeta

    archivos_wav = glob.glob(os.path.join(directorio_actual, "*.wav"))

    if archivos_wav:

        return archivos_wav[0]

       

    return None



def reproducir_audio_hilo():

    """Reproduce el audio detectado usando Pygame o Winsound de respaldo."""

    ruta_wav = obtener_ruta_audio()

   

    if ruta_wav and os.path.exists(ruta_wav):

        print(f"[AUDIO] Reproduciendo: {os.path.basename(ruta_wav)}")

        try:

            if USAR_PYGAME:

                sound = pygame.mixer.Sound(ruta_wav)

                sound.set_volume(1.0)

                sound.play()

            else:

                winsound.PlaySound(ruta_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)

        except Exception as e:

            print(f"[AUDIO ERROR] Error al reproducir el archivo con driver principal: {e}")

            try:

                winsound.PlaySound(ruta_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)

            except Exception:

                winsound.MessageBeep()

    else:

        print("[AUDIO ADVERTENCIA] No se encontró ningún archivo .wav en la carpeta. Usando tono de sistema.")

        winsound.MessageBeep()



def sonido():

    """Llama a la reproducción en segundo plano sin congelar la interfaz Tkinter."""

    Thread(target=reproducir_audio_hilo, daemon=True).start()



# ---------------------------------------------------------------------

# DETECCIÓN DE PANTALLA SECUNDARIA (TV)

# ---------------------------------------------------------------------

def obtener_rect_segunda_pantalla():

    try:

        import ctypes

        from ctypes import wintypes



        monitores = []

        MonitorEnumProc = ctypes.WINFUNCTYPE(

            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,

            ctypes.POINTER(wintypes.RECT), ctypes.c_double

        )



        def _callback(hmonitor, hdc, lprect, data):

            r = lprect.contents

            monitores.append((r.left, r.top, r.right, r.bottom))

            return 1



        callback = MonitorEnumProc(_callback)

        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback, 0)



        if len(monitores) < 2:

            return None

       

        for rect in monitores:

            if rect[0] != 0 or rect[1] != 0:

                return rect

        return monitores[1]

    except Exception:

        return None



# ---------------------------------------------------------------------

# CREACIÓN Y DESPLIEGUE DEL POPUP EN PANTALLA

# ---------------------------------------------------------------------

def mostrar_popup(root, evento):

    ventana = tk.Toplevel(root)

    ventana.overrideredirect(True)

    ventana.attributes("-topmost", True)



    rect_segunda = obtener_rect_segunda_pantalla()

    persistente = evento.get('id') is not None  # TODOS los telefonos (1128 y extensiones fijas): esperan contestada/colgada

    indice = 0

    while indice in slots_ocupados:

        indice += 1

    slots_ocupados.add(indice)

   

    if rect_segunda:

        left, top, right, bottom = rect_segunda

        x = right - ANCHO - 20

        y = bottom - (ALTO + MARGEN) * (indice + 1) - 50

    else:

        pantalla_ancho = ventana.winfo_screenwidth()

        pantalla_alto = ventana.winfo_screenheight()

        x = pantalla_ancho - ANCHO - 20

        y = pantalla_alto - (ALTO + MARGEN) * (indice + 1) - 50



    ventana.geometry(f"{ANCHO}x{ALTO}+{x}+{y}")

    ventana.configure(bg=COLOR_BG_LIGHT)



    # Contenedor principal estilo Tarjeta Web

    card = tk.Frame(ventana, bg=COLOR_CARD_BG)

    card.pack(fill="both", expand=True)



    # Encabezado estilo Header Web (Azul)

    header = tk.Frame(card, bg=COLOR_AZUL, height=42)

    header.pack(fill="x", side="top")

    header.pack_propagate(False)



    lbl_titulo = tk.Label(

        header,

        text="🔔 LLAMADA ENTRANTE",

        font=("Inter", 10, "bold"),

        fg="white",

        bg=COLOR_AZUL

    )

    lbl_titulo.pack(side="left", padx=12, pady=8)



    # Franja Borde Verde Institucional debajo del header

    border_bar = tk.Frame(card, bg=COLOR_VERDE, height=3)

    border_bar.pack(fill="x", side="top")



    # Cuerpo Interno

    body = tk.Frame(card, bg=COLOR_CARD_BG)

    body.pack(fill="both", expand=True, padx=14, pady=10)



    # Extensión y Badge del Área

    ext_texto = evento.get('extension') or '1144'

    area_texto = evento.get('area') or 'Extensión Individual'

   

    top_info = tk.Frame(body, bg=COLOR_CARD_BG)

    top_info.pack(fill="x", side="top")



    lbl_ext = tk.Label(

        top_info,

        text=f"Ext. {ext_texto}",

        font=("Inter", 11, "bold"),

        fg=COLOR_AZUL,

        bg=COLOR_CARD_BG

    )

    lbl_ext.pack(side="left")



    lbl_area = tk.Label(

        top_info,

        text=area_texto,

        font=("Inter", 8, "bold"),

        fg=COLOR_VERDE,

        bg=COLOR_BADGE_BG,

        padx=6,

        pady=2

    )

    lbl_area.pack(side="right")



    # Número telefónico

    num_texto = evento.get('numero') or 'Desconocido'

    lbl_numero = tk.Label(

        body,

        text=f"📞 {num_texto}",

        font=("Inter", 14, "bold"),

        fg=COLOR_TEXT_DARK,

        bg=COLOR_CARD_BG

    )

    lbl_numero.pack(anchor="w", pady=(8, 0))



    # Nombre Caller ID

    nom_texto = evento.get('nombre') or 'Sin nombre asignado'

    lbl_nombre = tk.Label(

        body,

        text=nom_texto,

        font=("Inter", 9),

        fg=COLOR_TEXT_MUTED,

        bg=COLOR_CARD_BG

    )

    lbl_nombre.pack(anchor="w", pady=(2, 0))



    # Pie con Fecha y Hora

    fecha_texto = evento.get('fecha_hora') or time.strftime("%Y-%m-%d %H:%M:%S")

    footer = tk.Frame(body, bg=COLOR_CARD_BG)

    footer.pack(fill="x", side="bottom", pady=(6, 0))



    # Línea sutil divisoria

    divisoria = tk.Frame(footer, bg="#F1F5F9", height=1)

    divisoria.pack(fill="x", side="top", pady=(0, 6))



    lbl_fecha_lbl = tk.Label(

        footer,

        text="Fecha y Hora",

        font=("Inter", 8),

        fg=COLOR_TEXT_MUTED,

        bg=COLOR_CARD_BG

    )

    lbl_fecha_lbl.pack(side="left")



    lbl_fecha_val = tk.Label(

        footer,

        text=fecha_texto,

        font=("Inter", 8, "bold"),

        fg=COLOR_TEXT_DARK,

        bg=COLOR_CARD_BG

    )

    lbl_fecha_val.pack(side="right")



    popups_activos.append(ventana)

   

    # Reproducir audio

    sonido()



    def cerrar():

        slots_ocupados.discard(indice)

        if persistente:

            popups_persistentes.pop(evento.get('id'), None)

        if ventana in popups_activos:

            popups_activos.remove(ventana)

        try:

            ventana.destroy()

        except tk.TclError:

            pass



    # Permitir cerrar al hacer clic en cualquier parte

    ventana.bind("<Button-1>", lambda e: cerrar())

    card.bind("<Button-1>", lambda e: cerrar())

    header.bind("<Button-1>", lambda e: cerrar())

    body.bind("<Button-1>", lambda e: cerrar())

   

    if persistente:

        popups_persistentes[evento.get('id')] = cerrar

        def cierre_por_tiempo():

            # Seguro del cliente: si la ventana sigue abierta sin contestar,

            # se convierte en aviso de LLAMADA PERDIDA en lugar de desaparecer.

            if evento.get('id') in popups_persistentes:

                cerrar()

                datos_perdida = dict(evento)

                avisos_perdidos_procesados.add(evento.get('id'))

                mostrar_aviso_perdida(root, datos_perdida)

        ventana.after(MAX_PERSISTENTE_MS, cierre_por_tiempo)

    else:

        ventana.after(8000, cerrar)

    ventana.update()



# ---------------------------------------------------------------------

# AVISO DE LLAMADA PERDIDA (aparece 60 s cuando nadie contesta)

# ---------------------------------------------------------------------

COLOR_ROJO_AVISO = "#C0392B"
COLOR_ROJO_ACENTO = "#E74C3C"
COLOR_FONDO_AVISO = "#FDEDEC"
COLOR_BADGE_BG_ROJO = "#FDECEA"


def mostrar_aviso_perdida(root, datos):
    """Muestra el aviso de llamada perdida con la misma tarjeta profesional
    (header, franja de acento, badge de area, numero y pie con fecha/hora)
    que usa la ventana de llamada entrante, y la quita a los 60 s."""
    id_llamada = datos.get('id')
    # Evita ventanas duplicadas de la misma llamada (hilos que consultan a la vez)
    if id_llamada is not None and id_llamada in avisos_por_id:
        return
    ventana = tk.Toplevel(root)
    ventana.overrideredirect(True)
    ventana.attributes("-topmost", True)

    indice = 0
    while indice in slots_ocupados:
        indice += 1
    slots_ocupados.add(indice)

    rect_segunda = obtener_rect_segunda_pantalla()
    if rect_segunda:
        left, top, right, bottom = rect_segunda
        x = right - ANCHO - 20
        y = bottom - (ALTO_AVISO + MARGEN) * (indice + 1) - 50
    else:
        pantalla_ancho = ventana.winfo_screenwidth()
        pantalla_alto = ventana.winfo_screenheight()
        x = pantalla_ancho - ANCHO - 20
        y = pantalla_alto - (ALTO_AVISO + MARGEN) * (indice + 1) - 50

    ventana.geometry(f"{ANCHO}x{ALTO_AVISO}+{x}+{y}")
    ventana.configure(bg=COLOR_BG_LIGHT)

    # Contenedor principal estilo Tarjeta Web (mismo patron que la entrante)
    card = tk.Frame(ventana, bg=COLOR_CARD_BG)
    card.pack(fill="both", expand=True)

    # Encabezado rojo de aviso
    header = tk.Frame(card, bg=COLOR_ROJO_AVISO, height=42)
    header.pack(fill="x", side="top")
    header.pack_propagate(False)

    tk.Label(
        header,
        text="📵 LLAMADA PERDIDA",
        font=("Inter", 10, "bold"),
        fg="white",
        bg=COLOR_ROJO_AVISO
    ).pack(side="left", padx=12, pady=8)

    # Franja de acento debajo del header (mismo patron que la entrante)
    border_bar = tk.Frame(card, bg=COLOR_ROJO_ACENTO, height=3)
    border_bar.pack(fill="x", side="top")

    # Cuerpo interno
    body = tk.Frame(card, bg=COLOR_CARD_BG)
    body.pack(fill="both", expand=True, padx=14, pady=10)

    ext_texto = datos.get('extension') or '?'
    area_texto = datos.get('area') or 'Extensión Individual'
    num_texto = datos.get('numero') or 'Desconocido'
    nom_texto = datos.get('nombre') or 'Sin nombre asignado'

    # Extension y badge del area (mismo patron que la entrante)
    top_info = tk.Frame(body, bg=COLOR_CARD_BG)
    top_info.pack(fill="x", side="top")

    tk.Label(
        top_info,
        text=f"Ext. {ext_texto}",
        font=("Inter", 11, "bold"),
        fg=COLOR_ROJO_AVISO,
        bg=COLOR_CARD_BG
    ).pack(side="left")

    tk.Label(
        top_info,
        text=area_texto,
        font=("Inter", 8, "bold"),
        fg=COLOR_ROJO_AVISO,
        bg=COLOR_BADGE_BG_ROJO,
        padx=6,
        pady=2
    ).pack(side="right")

    # Numero telefonico
    tk.Label(
        body,
        text=f"📞 {num_texto}",
        font=("Inter", 14, "bold"),
        fg=COLOR_TEXT_DARK,
        bg=COLOR_CARD_BG
    ).pack(anchor="w", pady=(8, 0))

    # Nombre Caller ID
    tk.Label(
        body,
        text=nom_texto,
        font=("Inter", 9),
        fg=COLOR_TEXT_MUTED,
        bg=COLOR_CARD_BG
    ).pack(anchor="w", pady=(2, 0))

    # Pie con Fecha y Hora (mismo patron que la entrante)
    fecha_texto = datos.get('fecha_hora') or time.strftime("%Y-%m-%d %H:%M:%S")
    footer = tk.Frame(body, bg=COLOR_CARD_BG)
    footer.pack(fill="x", side="bottom", pady=(6, 0))

    divisoria = tk.Frame(footer, bg="#F1F5F9", height=1)
    divisoria.pack(fill="x", side="top", pady=(0, 6))

    tk.Label(
        footer,
        text="Fecha y Hora",
        font=("Inter", 8),
        fg=COLOR_TEXT_MUTED,
        bg=COLOR_CARD_BG
    ).pack(side="left")

    tk.Label(
        footer,
        text=fecha_texto,
        font=("Inter", 8, "bold"),
        fg=COLOR_TEXT_DARK,
        bg=COLOR_CARD_BG
    ).pack(side="right")

    AVISOS_PERDIDOS.append(ventana)
    if id_llamada is not None:
        avisos_por_id[id_llamada] = ventana

    try:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass

    def cerrar_aviso():
        slots_ocupados.discard(indice)
        if ventana in AVISOS_PERDIDOS:
            AVISOS_PERDIDOS.remove(ventana)
        avisos_por_id.pop(id_llamada, None)
        try:
            ventana.destroy()
        except tk.TclError:
            pass

    ventana.bind("<Button-1>", lambda e: cerrar_aviso())
    card.bind("<Button-1>", lambda e: cerrar_aviso())
    header.bind("<Button-1>", lambda e: cerrar_aviso())
    body.bind("<Button-1>", lambda e: cerrar_aviso())

    ventana.after(DURACION_AVISO_PERDIDA_MS, cerrar_aviso)   # desaparece a los 60 s
    ventana.update()


# ---------------------------------------------------------------------

# HILO EXTRA 2: CONSULTA LAS LLAMADAS PERDIDAS AL SERVIDOR

# ---------------------------------------------------------------------

def consultar_perdidas_loop(root):

    global avisos_perdidos_procesados



    while True:

        try:

            res = requests.get(f"{SERVIDOR_URL}/api/perdidas", timeout=3)

            if res.status_code == 200:

                perdidas = res.json().get('perdidas', [])

                for datos in perdidas:

                    id_llamada = datos.get('id')

                    if id_llamada is not None and id_llamada not in avisos_perdidos_procesados:

                        avisos_perdidos_procesados.add(id_llamada)

                        # Limitar memoria por si el servidor se reinicia con ids nuevos

                        if len(avisos_perdidos_procesados) > 2000:

                            avisos_perdidos_procesados.clear()

                            avisos_perdidos_procesados.add(id_llamada)

                        root.after(0, mostrar_aviso_perdida, root, datos)

        except Exception:

            pass



        time.sleep(0.25)



# ---------------------------------------------------------------------

# HILO SECUNDARIO: CONSULTA PERIÓDICA AL SERVIDOR (POLLING)

# ---------------------------------------------------------------------

def consultar_servidor_loop(root):

    global ultimo_id_procesado

   

    # Espera a que el servidor responda antes de empezar a mostrar llamadas.

    # Si el PC arranca antes que la VM, asi NO se muestran como nuevas las llamadas del historial.

    while True:

        try:

            res = requests.get(f"{SERVIDOR_URL}/api/ultimas", timeout=3)

            if res.status_code == 200:

                datos = res.json()

                # Compatible tanto con respuesta antigua (lista) como con nueva estructura (dict)

                llamadas = datos.get('llamadas', datos) if isinstance(datos, dict) else datos

                if llamadas and isinstance(llamadas, list) and len(llamadas) > 0:

                    ultimo_id_procesado = llamadas[0].get('id', 0)

                break

        except Exception:

            pass

        time.sleep(2)



    while True:

        try:

            res = requests.get(f"{SERVIDOR_URL}/api/ultimas", timeout=3)

            if res.status_code == 200:

                datos = res.json()

                llamadas = datos.get('llamadas', datos) if isinstance(datos, dict) else datos

                if llamadas and isinstance(llamadas, list):

                    nuevas = []

                    for llamada in llamadas:

                        llamada_id = llamada.get('id', 0)

                        if llamada_id > ultimo_id_procesado:

                            nuevas.append(llamada)

                        else:

                            break

                   

                    if nuevas:

                        ultimo_id_procesado = nuevas[0].get('id', ultimo_id_procesado)

                        for elem in reversed(nuevas):

                            root.after(0, mostrar_popup, root, elem)

        except Exception:

            pass

       

        time.sleep(0.25)



# ---------------------------------------------------------------------

# HILO EXTRA: CIERRA LOS POPUPS PERSISTENTES CUANDO LA LLAMADA TERMINA

# (y convierte la ventana entrante en LLAMADA PERDIDA si nadie contesto)

# ---------------------------------------------------------------------

def convertir_a_perdida(root, id_llamada, datos):

    """Reemplaza la ventana entrante por el aviso de llamada perdida (hilo del TV)."""

    cerrar_fn = popups_persistentes.get(id_llamada)

    if cerrar_fn:

        cerrar_fn()

    datos = dict(datos or {})

    datos['id'] = id_llamada

    avisos_perdidos_procesados.add(id_llamada)

    mostrar_aviso_perdida(root, datos)



def vigilar_finalizadas_loop(root):

    while True:

        try:

            if popups_persistentes:

                res = requests.get(f"{SERVIDOR_URL}/api/finalizadas", timeout=3)

                finalizadas = set(res.json().get('finalizadas', [])) if res.status_code == 200 else set()



                perdidas_ids = {}

                try:

                    res_p = requests.get(f"{SERVIDOR_URL}/api/perdidas", timeout=3)

                    if res_p.status_code == 200:

                        for p in res_p.json().get('perdidas', []):

                            perdidas_ids[p.get('id')] = p

                except Exception:

                    pass



                for id_llamada, cerrar_fn in list(popups_persistentes.items()):

                    if id_llamada in finalizadas:

                        if id_llamada in perdidas_ids:

                            # Nadie contesto: la ventana entrante SE CONVIERTE en perdida

                            root.after(0, convertir_a_perdida, root, id_llamada, perdidas_ids[id_llamada])

                        else:

                            # Contestaron (o se cerro manualmente): se cierra normal

                            root.after(0, cerrar_fn)

        except Exception:

            pass

        time.sleep(0.25)


# ---------------------------------------------------------------------

# INICIALIZACIÓN DE LA APLICACIÓN

# ---------------------------------------------------------------------

if __name__ == "__main__":

    root = tk.Tk()

    root.withdraw()



    # Comprobar estado del archivo de audio en la consola al iniciar

    archivo_sonido = obtener_ruta_audio()

   

    print("==================================================")

    print(" Monitor Unimonserrate - Cliente IDT Activo ")

    print(f" Conectado a: {SERVIDOR_URL}")

    if archivo_sonido:

        print(f" Estado de Audio: Vinculado correctamente ({os.path.basename(archivo_sonido)})")

    else:

        print(" Estado de Audio: NO se encontró archivo .wav en esta carpeta")

    print("==================================================")



    hilo_consulta = Thread(target=consultar_servidor_loop, args=(root,), daemon=True)

    hilo_consulta.start()

    hilo_vigilante = Thread(target=vigilar_finalizadas_loop, args=(root,), daemon=True)

    hilo_vigilante.start()



    hilo_perdidas = Thread(target=consultar_perdidas_loop, args=(root,), daemon=True)

    hilo_perdidas.start()



    root.mainloop()