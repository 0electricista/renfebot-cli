from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

import os
from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv('EMAIL')
PASSWORD = os.getenv('PASSWORD')

with Stealth().use_sync(sync_playwright()) as p:
    browser = p.chromium.launch(headless=False)
    contexto = browser.new_context()
    contexto.clear_cookies()
    page = contexto.new_page()
    page.goto("https://venta.renfe.com/vol/loginParticular.do")
    page.evaluate("window.localStorage.clear();")
    page.evaluate("window.sessionStorage.clear();")
    page.fill('input[name="userId"]', EMAIL)
    page.fill('input[name="password"]', PASSWORD)
    page.get_by_role('button', name='Entrar').click()
    try:
        page.wait_for_selector('#codigoValidaLogin2F',state='visible', timeout=5000)
        otp = input("Pon el OTP:")
        page.fill('input[name="codigoValidaLogin2F"]', otp)
        page.locator('#idBotonValDispositivo').click()
    except Exception:
        pass
        
    page.goto('https://venta.renfe.com/vol/myPassesCard.do')
    page.locator('.btn.btn-sm.btn-trans-purple', has_text='Nueva formalización').click()
    page.screenshot(path='screenshots/screenshot.png')
    browser.close()