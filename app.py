import streamlit as st
import json
import time
import requests
import telebot
import threading
import math
import extra_streamlit_components as stx  
from datetime import datetime, time as dt_time, timedelta
import streamlit.components.v1 as components
import pytz 
import pandas as pd
from src.models import TrainRideRecord
SPAIN_TZ = pytz.timezone('Europe/Madrid')
TOKEN = st.secrets['TELEGRAM_TOKEN']

try:
    from src.scraper import Scraper
    from src.models import StationRecord
    from src.browser import BrowserManager
    from src.session import SessionManager, SessionStatus
    from src.passenger import PassengerData, PaymentMethod, DocumentType, load_passenger, save_passenger
    from src.purchaser import Purchaser, PurchaseStep
except ImportError as e:
    st.error(f"Error crítico: {e}. Revisa requirements.txt.")
    st.stop()

st.set_page_config(page_title="Renfe Web Monitor", page_icon="🚆", layout="wide")


# --- 1. GESTOR DE COOKIES ---
cookie_manager = stx.CookieManager(key="renfebot_cookies")

# --- 2. FUNCIONES AUXILIARES (Notificaciones / Telegram) ---
def invertir():
        st.empty()
        st.session_state['searching'] = True
        st.session_state['first_run'] = True
        st.session_state['known'] = set()
        st.session_state["selected_trains"] = set()
        st.session_state["origin"], st.session_state["dest"] = (
        st.session_state.get("dest"),
        st.session_state.get("origin"),
    )
    
@st.cache_resource
def iniciar_bot_background():
    bot = telebot.TeleBot(TOKEN)

    @bot.message_handler(commands=['id', 'start'])
    def send_id(message):
        chat_id = message.chat.id
        bot.reply_to(message, f"{chat_id}")

    def loop_polling():
        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=20)
            except Exception as e:
                print(f"⚠️ Error en el bot (reintentando en 5s): {e}")
                time.sleep(5)  

    t = threading.Thread(target=loop_polling, daemon=True)
    t.start()
    
    return bot
if 'bot_iniciado' not in st.session_state:
    bot_instance = iniciar_bot_background()
    st.session_state['bot_iniciado'] = True

def enviar_telegram(chat_id, mensaje):
    if not chat_id: return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "HTML"}, timeout=5)
        return True
    except: return False

def trigger_notification(title, body):
    js = f"""
    <script>
        (function() {{
            if (Notification.permission !== "granted") Notification.requestPermission();
            new Notification("{title}", {{ body: "{body}"}});
        }})();
    </script>
    """
    components.html(js, height=0, width=0)

def request_perms():
    components.html("<script>if(Notification.permission==='default')Notification.requestPermission();</script>", height=0, width=0)

def get_train_id(t:TrainRideRecord):
    return f"{t.departure_time.strftime('%H:%M')}-{t.train_type}-{t.origin}-{t.destination}"

# --- DIALOGO DE AYUDA ---
@st.dialog("🤖 Guía de Configuración Telegram")
def mostrar_ayuda_telegram():
    st.markdown("""
    Para configurar las notificaciones de Telegram:
    1. Accede al bot [@RenfeWebMonitorBot](https://t.me/RenfeWebMonitor_bot) en Telegram.
    2. Haz clic en "Iniciar" o envía el comando /start.
    3. El bot te responderá con tu Chat ID.
    4. Copia ese número y pégalo en el campo "Chat ID" de la configuración en esta aplicación.
    5. Guarda los cambios.
    6. Prueba la conexión para asegurarte de que todo funciona correctamente.
    """)

@st.cache_data
def load_stations():
    try:
        with open("assets/stations.json", "r", encoding="utf-8") as f:
            return {n: i["cdgoEstacion"] for n, i in json.load(f).items()}
    except: return {}

stations_map = load_stations()
station_names = sorted(list(stations_map.keys()))

# --- 3. RECUPERAR DATOS DE COOKIES ---
cookie_chat_id = cookie_manager.get(cookie="tg_chat_id")

# --- 4. BARRA LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuración")
    request_perms()
    
    col_header, col_help = st.columns([0.85, 0.15])
    with col_header:
        st.subheader("🤖 Telegram (opcional)")
       
    default_chat = cookie_chat_id if cookie_chat_id else ""
    
    with st.expander("Configurar Credenciales", expanded=not default_chat):
        tg_chat_id = st.text_input("Chat ID", value=default_chat)
        
        c1, c2 = st.columns(2)
        if c1.button("💾 Guardar Chat ID"):
            cookie_manager.set("tg_chat_id", tg_chat_id, expires_at=datetime.now(SPAIN_TZ) + timedelta(days=30), key="set_chat")
            st.success("Guardado.")
            time.sleep(1)
            st.rerun()
        if c2.button("🗑️ Borrar Chat ID"):
            cookie_manager.delete("tg_chat_id", key="delete_chat")
            st.success("Borradas.")
            time.sleep(1) 
            st.rerun()

        if c1.button("🔔 Probar Conexión"):
            if enviar_telegram(tg_chat_id, "🔔 ¡RenfeBot conectado!"):
                st.toast("Conexión correcta", icon="✅")
            else:
                st.error("Error. Revisa ID.")
        if c2.button("📩 Obtener Chat ID"):
            mostrar_ayuda_telegram()
            

    st.divider()
    
    origin_name = st.selectbox("📍 Origen", station_names, index=None, placeholder="Origen", key="origin")  
    dest_options = [s for s in station_names if s != origin_name]
    dest_name = st.selectbox("🏁 Destino", dest_options, index=None, placeholder="Destino", key="dest")
    st.button("Invertir", on_click=invertir, width="stretch")

    st.divider()

    trip_type = st.radio("Tipo", ["Solo Ida", "Ida y Vuelta"], horizontal=True)
    d1, d2 = st.columns(2)
    dept_date = d1.date_input("Fecha Ida", datetime.today(), min_value=datetime.today())
    min_time_out = d1.time_input("Hora Ida", dt_time(6, 0))
    
    ret_date, min_time_ret = None, dt_time(0,0)
    if trip_type == "Ida y Vuelta":
        ret_date = d2.date_input("Fecha Vuelta", dept_date, min_value=dept_date)
        min_time_ret = d2.time_input("Hora Vuelta", dt_time(16, 0))

    st.divider()
    
    desactivar = st.checkbox("❌ Desactivar la búsqueda automática")
    refresh_rate = st.number_input("Refresca la búsqueda cada (s)", 5, 60, 30) if not desactivar else 1
    
    if st.button("🔎 BUSCAR", type="primary", width='stretch'):
        st.empty()
        st.session_state['searching'] = True
        st.session_state['first_run'] = True
        st.session_state['known'] = set()
        st.session_state["selected_trains"] = set()
        
        st.rerun()
    
    if st.button("⏹️ PARAR"):
        st.session_state['searching'] = False
        st.rerun()

# --- 5. LÓGICA PRINCIPAL ---
st.title("🚆 Renfe Web Monitor")

if not st.session_state.get('searching'):
    with st.expander("ℹ️ ¿Qué es Renfe Web Monitor?", expanded=True):
        st.markdown("""
        **Renfe Web Monitor** es un bot diseñado para ayudar a los usuarios a comprar billetes de tren de Renfe. 
        Su función principal es **monitorear la disponibilidad de billetes**, especialmente útil cuando están agotados. 
        El sistema detecta automáticamente cuando alguien cancela una reserva y el billete vuelve a estar disponible, 
        **notificándote inmediatamente** (vía navegador o Telegram) para que puedas comprarlo antes que nadie. Este bot no permite comprar billetes automáticamente, debes hacerlo tú.  
          
        **IMPORTANTE**: La pestaña del navegador debe estar abierta (en móvil, debe estar en primer plano) para que el bot funcione.
        
        """)
    with st.expander("🔍 Funcionalidades" , expanded=True):
        st.markdown("""
        1️⃣: Permite realizar búsquedas automáticas de trayectos, sin tener que recargar siempre la página.  
        2️⃣: Permite ver los horarios de los trayectos. Además, existe la posibilidad de filtrar por trenes específicos.  
        3️⃣: Puedes desactivar la búsqueda automática pulsando en el botón de "❌ Desactivar la búsqueda automática".
        
        """)
    with st.expander("❔ Configuración bot de Telegram" , expanded=not default_chat):
        st.markdown("""
    Para configurar las notificaciones de Telegram:
    1. Accede al bot [@RenfeWebMonitorBot](https://t.me/RenfeWebMonitor_bot) en Telegram.
    2. Haz clic en "Iniciar" o envía el comando /start.
    3. El bot te responderá con tu Chat ID.
    4. Copia ese número y pégalo en el campo "Chat ID" de la configuración en esta aplicación.
    5. Guarda los cambios.
    6. Prueba la conexión para asegurarte de que todo funciona correctamente.
        
        """)

if st.session_state.get('searching'):
    if not origin_name or not dest_name:
        st.error("⚠️ Faltan estaciones")
        st.stop()
        
    origin = StationRecord(name=origin_name, code=stations_map[origin_name])
    dest = StationRecord(name=dest_name, code=stations_map[dest_name])
    d_dt = datetime.combine(dept_date, min_time_out)
    r_dt = datetime.combine(ret_date, min_time_ret) if ret_date else None
    
    try:    
        with st.spinner(f"Monitorizando... ({refresh_rate}s)"):
            all_trains = Scraper(origin, dest, d_dt, r_dt).get_trainrides()
            
        if not all_trains:
            st.warning("⚠️ Sin resultados")
        else:
            seleccionados, out, ret, new_msgs, current_ids = False, [], [], [], set()
            
            for t in all_trains:
                if not t.available: continue
                is_out = t.origin.upper() == origin_name.upper()
                
                valid, tid, lbl = False, "", ""
                if is_out and t.departure_time.time() >= min_time_out:
                    out.append(t); valid=True; tid=get_train_id(t)+"_I"; lbl="IDA"
                elif trip_type != "Solo Ida" and not is_out and t.departure_time.time() >= min_time_ret:
                    ret.append(t); valid=True; tid=get_train_id(t)+"_V"; lbl="VUELTA"
                    
                if valid:
                    current_ids.add(tid)
                    if tid not in st.session_state.get('known', set()):
                        if st.session_state["selected_trains"]:
                            if tid in st.session_state["selected_trains"]:
                                new_msgs.append(f"🚆 <b>{lbl}</b> {t.departure_time.strftime('%H:%M')} ({t.price}€)")
                        else:
                            new_msgs.append(f"🚆 <b>{lbl}</b> {t.departure_time.strftime('%H:%M')} ({t.price}€)")
                            

            # Notificaciones
            if not st.session_state['first_run']:
                if st.session_state.get("selected_trains",):
                    pass
                if new_msgs:
                    msg = f"Detectados {len(new_msgs)} trenes nuevos."
                    st.toast(msg, icon="🎉")
                    trigger_notification("¡Novedades!", msg)
                    if tg_chat_id:
                        enviar_telegram(tg_chat_id, f"🚨 <b>¡Novedades!</b> en trayecto en tu búsqueda entre {origin_name} y {dest_name} \n\n"+"\n".join(new_msgs))
            
            st.session_state['known'] = current_ids
            st.session_state['first_run'] = False

            # --- FUNCIÓN DRAW ACTUALIZADA ---
            def draw(lst, h, selectable, mostrar_trayecto=False):
                # Cabecera y botón de Renfe
                col_txt, col_btn = st.columns([0.8, 0.2])
                with col_txt:
                    st.subheader(f"{h} ({len(lst)})")
                with col_btn:
                    st.write("")
                    st.link_button("🛒 Ir a Renfe", "https://venta.renfe.com/vol/home.do", width='stretch')

                if lst: 
                    # 1. Preparar datos base
                    data = []
                    for t in lst:
                        tid = get_train_id(t)
                        row = {
                            "Salida": t.departure_time.strftime("%H:%M"), 
                            "Llegada": t.arrival_time.strftime("%H:%M"), 
                            "Precio": t.price, 
                            "Tipo": t.train_type
                        }
                        if mostrar_trayecto:
                            row["Trayecto"] = "IDA" if t.origin.upper() == origin_name.upper() else "VUELTA"
                        # Pre-marcar si ya estaba en session_state
                        if selectable:
                            is_checked = tid in st.session_state.get('selected_trains', set())
                            row["Monitorizar"] = is_checked
                            row["_id_interno"] = tid
                        
                        data.append(row)
                    
                    df = pd.DataFrame(data)

                    # 2. Renderizado
                    if selectable:
                        # --- USO DE FORMULARIO PARA EVITAR RECARGAS CONSTANTES ---
                        with st.form(key=f"form_{h}"):
                            disabled_cols = ["Salida", "Llegada", "Precio", "Tipo"]
                            if mostrar_trayecto:
                                disabled_cols.append("Trayecto")
                            
                            edited_df = st.data_editor(
                                df,
                                column_config={
                                    "Monitorizar": st.column_config.CheckboxColumn(
                                        "Monitorizar",
                                        default=False,
                                        width="small"
                                    ),
                                    "_id_interno": None
                                },
                                disabled=disabled_cols,
                                hide_index=True,
                                key=f"editor_{h}",
                                width="stretch"
                            )

                            # El botón de envío dentro del form
                            if st.form_submit_button("💾 Guardar Selección"):
                                # Lógica de guardado masivo:
                                
                                # 1. Sacamos los IDs de ESTA tabla que el usuario ha dejado marcados (True)
                                ids_seleccionados_en_tabla = set(
                                    edited_df[edited_df["Monitorizar"] == True]["_id_interno"]
                                )
                                
                                # 2. Sacamos TODOS los IDs que había en esta tabla (marcados o no)
                                # Esto es vital para no borrar los trenes de la pestaña "Vuelta" si estamos en "Ida"
                                ids_totales_en_tabla = set(edited_df["_id_interno"])

                                # 3. Actualizamos la memoria global (Set Operations)
                                if 'selected_trains' not in st.session_state:
                                    st.session_state['selected_trains'] = set()
                                
                                # A. Quitamos de la memoria global todos los trenes que aparecen en ESTA tabla
                                st.session_state['selected_trains'].difference_update(ids_totales_en_tabla)
                                
                                # B. Añadimos a la memoria global solo los que el usuario marcó en ESTA tabla
                                st.session_state['selected_trains'].update(ids_seleccionados_en_tabla)
                                st.success("¡Selección actualizada!")
                    else:
                        st.dataframe(df, width='stretch', hide_index=True)
                        
                else: 
                    st.info("No hay trenes disponibles.")
            # --------------------------------

            if trip_type != "Solo Ida":
                t1, t2, t3 = st.tabs(["IDA", "VUELTA", "HORARIOS"])
                with t1: draw(out, "Ida", False, mostrar_trayecto=False)
                with t2: draw(ret, "Vuelta", False, mostrar_trayecto=False)
                with t3: draw([t for t in all_trains if not math.isnan(t.price)], "Todos los trenes", True, mostrar_trayecto=True)
            else:
                t1,t2 = st.tabs(["IDA","HORARIOS"])
                with t1: draw(out, "Ida", False, mostrar_trayecto=False)
                with t2: draw([t for t in all_trains if not math.isnan(t.price)], "Todos los trenes", True, mostrar_trayecto=True)
            
            if not desactivar:
                st.caption(f"Actualizado: {datetime.now(SPAIN_TZ).strftime('%H:%M:%S')}. Próxima en {refresh_rate}s.")
            else:
                st.caption(f"Última actualización: {datetime.now(SPAIN_TZ).strftime('%H:%M:%S')}.")

    except Exception as e: st.error(f"Error: {e}")

    if not desactivar:
        time.sleep(refresh_rate)

        st.rerun()

# --- 6. SECCIÓN DE AUTOCOMPRA ---

st.divider()
st.header("🛒 Autocompra de Billetes")
st.caption("Configura tus datos y el bot comprará automáticamente cuando detecte disponibilidad.")

with st.expander("👤 Datos del Pasajero", expanded=not load_passenger()):
    saved_passenger = load_passenger()
    
    col_p1, col_p2 = st.columns(2)
    p_nombre = col_p1.text_input("Nombre", value=saved_passenger.nombre if saved_passenger else "")
    p_apellido1 = col_p2.text_input("Primer Apellido", value=saved_passenger.apellido1 if saved_passenger else "")
    p_apellido2 = col_p1.text_input("Segundo Apellido (opcional)", value=saved_passenger.apellido2 if saved_passenger and saved_passenger.apellido2 else "")
    
    col_d1, col_d2 = st.columns(2)
    p_tipo_doc = col_d1.selectbox("Tipo Documento", ["DNI", "NIE", "PASAPORTE"], index=0)
    p_num_doc = col_d2.text_input("Nº Documento", value=saved_passenger.numero_documento if saved_passenger else "")
    
    col_c1, col_c2 = st.columns(2)
    p_email = col_c1.text_input("Email", value=saved_passenger.email if saved_passenger else "")
    p_telefono = col_c2.text_input("Teléfono (opcional)", value=saved_passenger.telefono if saved_passenger and saved_passenger.telefono else "")
    
    p_metodo_pago = st.radio(
        "Método de pago",
        ["Abono / Bono", "Tarjeta"],
        horizontal=True,
        index=0 if (not saved_passenger or saved_passenger.metodo_pago == PaymentMethod.ABONO) else 1,
        help="Con abono se completa la compra automáticamente. Con tarjeta, el bot espera tu confirmación antes de pagar."
    )
    
    if st.button("💾 Guardar Datos del Pasajero"):
        try:
            passenger = PassengerData(
                nombre=p_nombre,
                apellido1=p_apellido1,
                apellido2=p_apellido2 or None,
                tipo_documento=DocumentType(p_tipo_doc),
                numero_documento=p_num_doc,
                email=p_email,
                telefono=p_telefono or None,
                metodo_pago=PaymentMethod.ABONO if "Abono" in p_metodo_pago else PaymentMethod.TARJETA,
            )
            save_passenger(passenger)
            st.success("✅ Datos guardados correctamente.")
        except Exception as e:
            st.error(f"Error al guardar: {e}")

# --- Estado del navegador y sesión ---
with st.expander("🌐 Navegador y Sesión de Renfe"):
    st.markdown("""
    El bot usa un navegador automatizado para comprar billetes. 
    Necesitas **iniciar sesión en Renfe una sola vez** y la sesión se mantendrá.
    """)
    
    col_b1, col_b2 = st.columns(2)
    
    if col_b1.button("🚀 Abrir Navegador y Login"):
        with st.spinner("Abriendo navegador..."):
            try:
                browser = BrowserManager(headless=False)
                browser.start()
                st.session_state['browser'] = browser
                
                session = SessionManager(browser.page)
                status = session.check_session()
                
                if status == SessionStatus.LOGGED_IN:
                    user = session.get_user_info()
                    st.success(f"✅ Sesión activa" + (f" ({user})" if user else ""))
                else:
                    st.warning("⏳ Inicia sesión en la ventana del navegador que se ha abierto. Tienes 5 minutos.")
                    if session.wait_for_manual_login(timeout_seconds=300):
                        st.success("✅ ¡Login correcto! Sesión guardada.")
                    else:
                        st.error("❌ No se detectó login. Inténtalo de nuevo.")
            except Exception as e:
                st.error(f"Error al abrir navegador: {e}")
    
    if col_b2.button("🔌 Cerrar Navegador"):
        if 'browser' in st.session_state:
            st.session_state['browser'].stop()
            del st.session_state['browser']
            st.success("Navegador cerrado.")

# --- Activar Autocompra ---
with st.expander("⚡ Activar Autocompra"):
    st.markdown("""
    Cuando la autocompra está activa, el bot intentará comprar automáticamente 
    los trenes que hayas marcado en "Monitorizar" en cuanto estén disponibles.
    
    - **Con Abono**: La compra se completa automáticamente.
    - **Con Tarjeta**: El bot te notificará por Telegram para que confirmes el pago.
    """)
    
    autocompra_activa = st.toggle("Activar Autocompra", value=st.session_state.get('autocompra_activa', False))
    st.session_state['autocompra_activa'] = autocompra_activa
    
    if autocompra_activa:
        passenger = load_passenger()
        if not passenger:
            st.error("⚠️ Configura tus datos de pasajero primero.")
        elif 'browser' not in st.session_state or not st.session_state['browser'].is_running():
            st.warning("⚠️ Abre el navegador e inicia sesión primero.")
        else:
            st.success("✅ Autocompra activa. El bot comprará trenes monitorizados cuando estén disponibles.")


