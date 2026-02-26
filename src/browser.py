"""
Gestor de navegador Playwright con perfil persistente.

Mantiene la sesión del usuario (cookies, login de Renfe) entre ejecuciones
usando un directorio de perfil local. Así el usuario solo necesita loguearse
una vez y la sesión se reutiliza.
"""

import os
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

# Directorio donde se almacena el perfil persistente del navegador
DEFAULT_PROFILE_DIR = Path(os.path.expanduser("~")) / ".renfe-monitor" / "browser-profile"

RENFE_HOME_URL = "https://venta.renfe.com/vol/home.do"
RENFE_LOGIN_URL = "https://venta.renfe.com/vol/loginNuevo.do"


class BrowserManager:
    """Gestiona una instancia de Chromium con perfil persistente.
    
    El perfil persistente permite que las cookies y la sesión de Renfe
    se mantengan entre cierres y reaperturas del navegador.
    """

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        headless: bool = False,
        slow_mo: int = 100,
    ):
        """
        :param profile_dir: Directorio para el perfil persistente.
        :param headless: Si True, el navegador no muestra ventana.
        :param slow_mo: Milisegundos de retardo entre acciones (anti-detección).
        """
        self.profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        self.headless = headless
        self.slow_mo = slow_mo

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("El navegador no está iniciado. Llama a start() primero.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("El navegador no está iniciado. Llama a start() primero.")
        return self._context

    def start(self) -> Page:
        """Inicia Playwright y abre un navegador con perfil persistente."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            slow_mo=self.slow_mo,
            viewport={"width": 1280, "height": 800},
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

        return self._page

    def stop(self) -> None:
        """Cierra el navegador y libera recursos."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def is_running(self) -> bool:
        return self._context is not None and self._page is not None

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.page.goto(url, wait_until=wait_until)

    def screenshot(self, path: str = "screenshot.png") -> bytes:
        return self.page.screenshot(path=path)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
