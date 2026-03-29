from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

import os
from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv('EMAIL')
PASSWORD = os.getenv('PASSWORD')

# === ESTRUCTURA MODIFICADA PARA MANTENER LA INSTANCIA ===

def iniciar_sesion(email,password):
    # En vez de usar 'with', iniciamos Playwright y lo guardamos
    p = sync_playwright().start()
    
    # Iniciamos el navegador
    browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"], headless=False)
    
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
    
    # Esperamos el OTP o fallamos
    try:
        page.wait_for_selector('#codigoValidaLogin2F', state='visible', timeout=30000)
        return p, browser, contexto, page, True, False
    except Exception:
        pass
        
    print("✅ Inicio de sesión terminado. El navegador sigue abierto.")
    page.wait_for_timeout(5000)
    
    # Devolvemos los objetos VIVOS para usarlos en el resto de tu código/aplicación
    return p, browser, contexto, page, False, True

def rellenar_otp(page, otp):
        page.fill('input[name="codigoValidaLogin2F"]', otp)
        page.locator('#idBotonValDispositivo').click()
        page.wait_for_timeout(5000)
        return page, True

# --- Ejemplo de cómo usarlo sin que se cierre ---
if __name__ == "__main__":
    p_instance, navegador, contexto_vivo, pagina_viva = iniciar_sesion(EMAIL, PASSWORD)
    
    # Aquí puedes seguir navegando sin volver a hacer login ni OTP
    # Usando 'pagina_viva'
    print("Navegando a los billetes...")
    pagina_viva.goto('https://venta.renfe.com/vol/myPassesCard.do')
    pagina_viva.locator('.btn.btn-sm.btn-trans-purple', has_text='Nueva formalización').click()
    
    # Cuando TERMINES todo y ya no necesites el bot, lo cierras tú.
    # navegador.close()
    # p_instance.stop()