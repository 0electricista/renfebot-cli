from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import subprocess
import os
import subprocess
import os


# === ESTRUCTURA MODIFICADA PARA MANTENER LA INSTANCIA ===

def iniciar_sesion(email,password):
        # 1. Define la ruta exacta al ejecutable de Brave según tu SO.
    # Ejemplos comunes:
    # Windows: r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    # macOS: "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
    # Linux: "/usr/bin/brave-browser"
    brave_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"  # Reemplaza con tu ruta real

    # 2. Define un directorio de usuario temporal o específico para el debugging.
    # Si intentas usar el mismo perfil que tu navegación diaria mientras está abierto, fallará.
    user_data_dir = os.path.join(os.getcwd(), "brave_debug_profile")

    # 3. Configura los argumentos
    args = [
        brave_path,
        "--remote-debugging-port=9222",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",           # Evita pantallas de bienvenida
        "--no-default-browser-check" # Evita avisos innecesarios
    ]

    # 4. Inicia el proceso
    try:
        # Popen inicia el proceso sin bloquear la ejecución de tu script en Python
        process = subprocess.Popen(args)
        print(f"Brave iniciado correctamente. PID: {process.pid}")
        print("El puerto CDP 9222 está ahora a la escucha.")
    except FileNotFoundError:
        print(f"Error crítico: No se encontró el ejecutable en la ruta '{brave_path}'. Verifica que Brave esté instalado ahí.")
    except Exception as e:
        print(f"Se produjo un error inesperado al lanzar el proceso: {e}")
    # En vez de usar 'with', iniciamos Playwright y lo guardamos
    p = sync_playwright().start()
    
    # Iniciamos el navegador
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    
    # Creamos el contexto
    contexto = browser.new_context()
    stealth = Stealth()
    
    # Limpiamos todo al empezar (opcional si es un contexto nuevo, pero lo mantenemos como pedías)
    contexto.clear_cookies()
    page = contexto.new_page()
    stealth.apply_stealth_sync(page)
    
    page.goto("https://venta.renfe.com/vol/loginParticular.do")
    page.evaluate("window.localStorage.clear();")
    page.evaluate("window.sessionStorage.clear();")
    
    # Rellenamos el login
    page.fill('input[name="userId"]', email)
    page.fill('input[name="password"]', password)
    page.get_by_role('button', name='Entrar').click()
    
    try:
        # Reemplaza 'AQUÍ_EL_TEXTO' por el texto exacto que aparece, ej: 'Código de verificación' o 'Introduce tu PIN'
        page.wait_for_selector("text=Mis viajes", state='visible', timeout=40000)
        # O alternativa si es parte de un texto más grande: 
        # page.locator("text=AQUÍ_EL_TEXTO").wait_for(state='visible', timeout=40000)
    except Exception as e:
        print(f"Error esperando el OTP/Login: {e}")
        return p, browser, contexto, page, False
    
    # Devolvemos los objetos VIVOS para usarlos en el resto de tu código/aplicación
    return p, browser, contexto, page , True