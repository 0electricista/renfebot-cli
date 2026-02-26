"""
Gestión de sesión de usuario en Renfe.

Se encarga de:
- Detectar si el usuario ya tiene sesión activa (login previo persistido).
- Abrir la página de login y esperar a que el usuario se loguee manualmente.
- Verificar el estado de la sesión antes de cada operación de compra.
"""

import time
from enum import Enum
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

RENFE_HOME_URL = "https://venta.renfe.com/vol/home.do"
RENFE_LOGIN_URL = "https://venta.renfe.com/vol/loginNuevo.do"
RENFE_AREA_PERSONAL_URL = "https://venta.renfe.com/vol/areaPersonal.do"

# Selectores clave de la web de Renfe
SELECTOR_LOGGED_IN = "#nombreUsuarioLogin, .user-name, .area-personal"
SELECTOR_LOGIN_BUTTON = "#loginNuevoBtn, .btn-login, a[href*='login']"
SELECTOR_LOGIN_FORM = "#loginForm, .login-form, input[name='loginNuevo']"


class SessionStatus(Enum):
    """Estado de la sesión del usuario."""
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class SessionManager:
    """Gestiona la sesión de usuario en la web de Renfe.
    
    Usa el perfil persistente del BrowserManager para mantener cookies
    entre ejecuciones. El usuario solo necesita loguearse una vez.
    """

    def __init__(self, page: Page):
        """
        :param page: Página de Playwright (obtenida de BrowserManager).
        """
        self.page = page
        self._status = SessionStatus.UNKNOWN

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def is_logged_in(self) -> bool:
        return self._status == SessionStatus.LOGGED_IN

    def check_session(self, timeout_ms: int = 5000) -> SessionStatus:
        """Comprueba si el usuario tiene sesión activa navegando a Renfe.
        
        :param timeout_ms: Tiempo máximo de espera para detectar elementos de login.
        :return: Estado actual de la sesión.
        """
        try:
            self.page.goto(RENFE_HOME_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(1)  # Esperar renderizado JS

            # Intentar detectar indicadores de sesión activa
            if self._detect_logged_in(timeout_ms):
                self._status = SessionStatus.LOGGED_IN
            else:
                self._status = SessionStatus.LOGGED_OUT

        except PlaywrightTimeout:
            self._status = SessionStatus.UNKNOWN
        except Exception:
            self._status = SessionStatus.UNKNOWN

        return self._status

    def wait_for_manual_login(self, timeout_seconds: int = 300) -> bool:
        """Navega a la página de login y espera a que el usuario se loguee manualmente.
        
        Abre la página de login de Renfe en el navegador visible y espera
        hasta que se detecte que el usuario ha iniciado sesión.
        
        :param timeout_seconds: Tiempo máximo de espera en segundos (default: 5 min).
        :return: True si el usuario se logueó correctamente.
        """
        try:
            self.page.goto(RENFE_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
        except PlaywrightTimeout:
            # Si la página no carga, intentar con la home
            try:
                self.page.goto(RENFE_HOME_URL, wait_until="domcontentloaded", timeout=15000)
            except PlaywrightTimeout:
                return False

        # Esperar hasta que detectemos que el usuario se ha logueado
        start_time = time.time()
        check_interval = 2  # Comprobar cada 2 segundos

        while (time.time() - start_time) < timeout_seconds:
            if self._detect_logged_in(timeout_ms=2000):
                self._status = SessionStatus.LOGGED_IN
                return True
            
            # Comprobar si ha cambiado de URL (posible redirección post-login)
            current_url = self.page.url
            if "areaPersonal" in current_url or "home.do" in current_url:
                if self._detect_logged_in(timeout_ms=3000):
                    self._status = SessionStatus.LOGGED_IN
                    return True

            time.sleep(check_interval)

        self._status = SessionStatus.LOGGED_OUT
        return False

    def ensure_logged_in(self, timeout_seconds: int = 300) -> bool:
        """Verifica sesión activa. Si no hay, espera login manual.
        
        Flujo:
        1. Comprueba si ya hay sesión activa.
        2. Si no, abre login y espera a que el usuario se loguee.
        
        :param timeout_seconds: Tiempo máximo para el login manual.
        :return: True si hay sesión activa al terminar.
        """
        status = self.check_session()
        
        if status == SessionStatus.LOGGED_IN:
            return True
        
        # No hay sesión → pedir login manual
        return self.wait_for_manual_login(timeout_seconds)

    def _detect_logged_in(self, timeout_ms: int = 5000) -> bool:
        """Intenta detectar si el usuario tiene sesión activa.
        
        Busca elementos del DOM que solo aparecen cuando el usuario
        está logueado (nombre de usuario, área personal, etc.).
        
        :param timeout_ms: Milisegundos de espera para encontrar el selector.
        :return: True si se detecta sesión activa.
        """
        # Múltiples selectores posibles para detectar login
        login_indicators = [
            "#nombreUsuarioLogin",
            ".user-name",
            ".area-personal",
            "a[href*='areaPersonal']",
            ".nombre-usuario",
            # Texto que aparece cuando estás logueado
        ]
        
        for selector in login_indicators:
            try:
                element = self.page.wait_for_selector(
                    selector,
                    timeout=timeout_ms // len(login_indicators),
                    state="visible"
                )
                if element:
                    return True
            except PlaywrightTimeout:
                continue
            except Exception:
                continue

        # Método alternativo: comprobar cookies de sesión
        cookies = self.page.context.cookies()
        renfe_session_cookies = [
            c for c in cookies
            if "renfe" in c.get("domain", "").lower()
            and c.get("name", "").upper() in ("JSESSIONID", "DWRSESSIONID")
        ]
        
        # Si hay cookies de sesión, verificar navegando al área personal
        if renfe_session_cookies:
            try:
                self.page.goto(RENFE_AREA_PERSONAL_URL, wait_until="domcontentloaded", timeout=10000)
                # Si no nos redirige al login, estamos logueados
                time.sleep(1)
                if "login" not in self.page.url.lower():
                    return True
            except Exception:
                pass

        return False

    def get_user_info(self) -> Optional[str]:
        """Intenta obtener el nombre del usuario logueado.
        
        :return: Nombre del usuario o None si no se puede obtener.
        """
        if not self.is_logged_in:
            return None

        selectors = ["#nombreUsuarioLogin", ".user-name", ".nombre-usuario"]
        for selector in selectors:
            try:
                element = self.page.wait_for_selector(selector, timeout=2000)
                if element:
                    text = element.inner_text().strip()
                    if text:
                        return text
            except Exception:
                continue
        
        return None
