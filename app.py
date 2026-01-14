import streamlit as st
import json
import time
import requests
import telebot
import threading
import extra_streamlit_components as stx  
from datetime import datetime, time as dt_time, timedelta
import streamlit.components.v1 as components
import pytz 
SPAIN_TZ = pytz.timezone('Europe/Madrid')
TOKEN = st.secrets['TELEGRAM_TOKEN']

try:
    from src.scraper import Scraper
    from src.models import StationRecord
except ImportError as e:
    st.error(f"Error crítico: {e}. Revisa requirements.txt.")
    st.stop()

st.set_page_config(page_title="Renfe Web Monitor", page_icon="🚆", layout="wide")

# --- 0. PARCHE CSS (SOLUCIÓN ESPACIADO) ---
# Esto reduce el padding de los botones en la sidebar para que el emoji '?' no tenga espacios raros
st.markdown("""
<style>
    [data-testid="stSidebar"] button {
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
        min-width: 0 !important;
    }
</style>
""", unsafe_allow_html=True)

# --- 1. GESTOR DE COOKIES ---
cookie_manager = stx.CookieManager(key="renfebot_cookies")

# --- 2. FUNCIONES AUXILIARES (Notificaciones / Telegram) ---
@st.cache_resource
def iniciar_bot_background():
    bot = telebot.TeleBot(TOKEN)

    @bot.message_handler(commands=['id', 'start'])
    def send_id(message):
        chat_id = message.chat.id
        bot.reply_to(message, f"{chat_id}")

    def loop_polling():
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"Error en el bot: {e}")

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

def get_train_id(t):
    return f"{t.departure_time.strftime('%H:%M')}-{t.train_type}-{t.price}"

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
    with col_help:
        # El CSS inyectado arriba hará que este botón se vea compacto
        if st.button("❔", help="¿Cómo configurar esto?"):
            mostrar_ayuda_telegram()        
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
    
    origin_name = st.selectbox("📍 Origen", station_names, index=None, placeholder="Origen")
    dest_options = [s for s in station_names if s != origin_name]
    dest_name = st.selectbox("🏁 Destino", dest_options, index=None, placeholder="Destino")

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
    
    auto_refresh = st.checkbox("🔄 Auto-Monitor", help="Refresca automáticamente la búsqueda cada cierto tiempo")
    refresh_rate = st.number_input("Segundos", 5, 60, 30) if auto_refresh else 0
    
    if st.button("🔎 BUSCAR", type="primary", use_container_width=True):
        st.session_state['searching'] = True
        st.session_state['known'] = set()
    
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
            out, ret, new_msgs, current_ids = [], [], [], set()
            
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
                        new_msgs.append(f"🚆 <b>{lbl}</b> {t.departure_time.strftime('%H:%M')} ({t.price}€)")

            # Notificaciones
            if new_msgs and len(st.session_state.get('known', set())) > 0:
                msg = f"Detectados {len(new_msgs)} trenes nuevos."
                st.toast(msg, icon="🎉")
                trigger_notification("¡Novedades!", msg)
                if tg_chat_id:
                    enviar_telegram(tg_chat_id, f"🚨 <b>¡Novedades!</b> en trayecto en tu búsqueda entre {origin_name} y {dest_name} \n\n"+"\n".join(new_msgs))
            
            st.session_state['known'] = current_ids

            # --- FUNCIÓN DRAW ACTUALIZADA ---
            def draw(lst, h):
                # Usamos columnas para poner Título a la izq y Botón a la dcha
                col_txt, col_btn = st.columns([0.8, 0.2])
                with col_txt:
                    st.subheader(f"{h} ({len(lst)})")
                with col_btn:
                    # Un pequeño espacio y el botón
                    st.write("")
                    st.link_button(
                        "🛒 Ir a Renfe", 
                        "https://venta.renfe.com/vol/home.do", 
                        use_container_width=True,
                        help="Abre venta.renfe.com en otra pestaña"
                    )

                if lst: 
                    st.dataframe([{"Salida": t.departure_time.strftime("%H:%M"), "Llegada": t.arrival_time.strftime("%H:%M"), "Precio": t.price, "Tipo": t.train_type} for t in lst], use_container_width=True)
                else: 
                    st.info("No disponible")
            # --------------------------------

            t1, t2 = st.tabs(["IDA", "VUELTA"]) if trip_type != "Solo Ida" else (st.container(), None)
            with t1: draw(out, "Ida")
            if t2:
                with t2: draw(ret, "Vuelta")
            
            if auto_refresh:
                st.caption(f"Actualizado: {datetime.now(SPAIN_TZ).strftime('%H:%M:%S')}. Próxima en {refresh_rate}s.")
            else:
                st.caption(f"Última actualización: {datetime.now(SPAIN_TZ).strftime('%H:%M:%S')}.")

    except Exception as e: st.error(f"Error: {e}")

    if auto_refresh:
        time.sleep(refresh_rate)

        st.rerun()


