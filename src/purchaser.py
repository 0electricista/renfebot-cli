"""
Módulo de compra automatizada de billetes de Renfe.

Flujo completo:
1. Buscar el tren en la web de Renfe (navegador automatizado).
2. Seleccionar el tren y la tarifa.
3. Rellenar datos del pasajero.
4. Llegar al paso de pago.
5. Según configuración: aplicar abono o esperar confirmación para tarjeta.
6. Notificar resultado al usuario.

Soporta:
- Pago con tarjeta (requiere confirmación del usuario antes del paso final).
- Pago con abono/bono (se aplica automáticamente si está vinculado a la cuenta).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .passenger import PassengerData, PaymentMethod
from .models import StationRecord, TrainRideRecord


# --- URLs de Renfe ---
RENFE_SEARCH_URL = "https://venta.renfe.com/vol/home.do"
RENFE_BUY_BASE = "https://venta.renfe.com/vol"

# --- Selectores de la web de Renfe ---
# Formulario de búsqueda
SEL_INPUT_ORIGIN = "#IdOrigen, input[name='desOrigen']"
SEL_INPUT_DEST = "#IdDestino, input[name='desDestino']"
SEL_INPUT_DATE = "#__fechaIdaVisual, input[name='_fechaIdaVisual']"
SEL_BTN_SEARCH = "#buttonSubmit, button[type='submit']"

# Lista de trenes
SEL_TRAIN_ROW = ".trayectoRow, .trenes-list tr, .listado-trenes .tren"
SEL_TRAIN_TIME = ".horasalida, .hora-salida, td:nth-child(1)"
SEL_TRAIN_PRICE = ".precio, .tarifa-minima, td.precio"
SEL_BTN_SELECT_TRAIN = ".btnReservar, .btn-seleccionar, button.seleccionar"

# Selección de tarifa
SEL_TARIFF_OPTIONS = ".listaTarifas .tarifa, .tarifas-disponibles .tarifa-item"
SEL_BTN_CONTINUE_TARIFF = "#btnSiguiente, .btn-siguiente, button.continuar"

# Datos del pasajero
SEL_INPUT_NAME = "#nombre, input[name='nombre']"
SEL_INPUT_SURNAME1 = "#apellido1, input[name='apellido1']"
SEL_INPUT_SURNAME2 = "#apellido2, input[name='apellido2']"
SEL_INPUT_DOCTYPE = "#tipoDocumento, select[name='tipoDocumento']"
SEL_INPUT_DOCNUM = "#numDocumento, input[name='numDocumento']"
SEL_INPUT_EMAIL = "#email, input[name='email']"
SEL_INPUT_PHONE = "#telefono, input[name='telefono']"
SEL_BTN_CONTINUE_PASSENGER = "#btnSiguiente, .btn-siguiente"

# Paso de pago
SEL_PAYMENT_CARD = ".pago-tarjeta, input[value='tarjeta'], #pagoTarjeta"
SEL_PAYMENT_ABONO = ".pago-abono, input[value='abono'], #pagoAbono, .btn-abono"
SEL_ABONO_SELECT = ".seleccionar-abono, select.abono, #selectorAbono"
SEL_BTN_PAY = "#btnPagar, .btn-pagar, button.finalizar-compra"
SEL_BTN_APPLY_ABONO = "#btnAplicarAbono, .btn-aplicar-abono, button.aplicar"

# Confirmación
SEL_CONFIRMATION = ".confirmacion-compra, .localizador, #localizador"
SEL_LOCALIZADOR = ".codigo-localizador, .localizador-texto, #textoLocalizador"


class PurchaseStep(Enum):
    """Pasos del flujo de compra."""
    NOT_STARTED = "not_started"
    SEARCHING = "searching"
    SELECTING_TRAIN = "selecting_train"
    SELECTING_TARIFF = "selecting_tariff"
    FILLING_PASSENGER = "filling_passenger"
    PAYMENT = "payment"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


class PurchaseError(Exception):
    """Error durante el proceso de compra."""
    pass


@dataclass
class PurchaseResult:
    """Resultado de un intento de compra."""
    success: bool
    step_reached: PurchaseStep
    localizador: Optional[str] = None
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


class Purchaser:
    """Automatiza el flujo de compra de billetes en la web de Renfe.
    
    Navega por la web paso a paso:
    1. Búsqueda → 2. Selección de tren → 3. Tarifa → 4. Pasajero → 5. Pago
    
    Para abonos: aplica el abono automáticamente.
    Para tarjeta: llega al paso de pago y espera confirmación del usuario.
    """

    def __init__(
        self,
        page: Page,
        passenger: PassengerData,
        on_status_change: Optional[Callable[[PurchaseStep, str], None]] = None,
        on_confirmation_needed: Optional[Callable[[str], bool]] = None,
    ):
        """
        :param page: Página de Playwright con sesión activa.
        :param passenger: Datos del pasajero para rellenar formularios.
        :param on_status_change: Callback llamado en cada cambio de paso.
                                 Recibe (paso, mensaje).
        :param on_confirmation_needed: Callback para pedir confirmación antes de pagar.
                                       Recibe (mensaje) y devuelve True/False.
        """
        self.page = page
        self.passenger = passenger
        self.on_status_change = on_status_change or (lambda step, msg: None)
        self.on_confirmation_needed = on_confirmation_needed
        self._current_step = PurchaseStep.NOT_STARTED

    @property
    def current_step(self) -> PurchaseStep:
        return self._current_step

    def _update_step(self, step: PurchaseStep, message: str = "") -> None:
        """Actualiza el paso actual y notifica."""
        self._current_step = step
        self.on_status_change(step, message)

    def purchase(
        self,
        origin: StationRecord,
        destination: StationRecord,
        departure_date: datetime,
        target_departure_time: str,
        target_train_type: Optional[str] = None,
    ) -> PurchaseResult:
        """Ejecuta el flujo completo de compra para un tren específico.
        
        :param origin: Estación origen.
        :param destination: Estación destino.
        :param departure_date: Fecha de salida.
        :param target_departure_time: Hora de salida del tren deseado (formato "HH:MM").
        :param target_train_type: Tipo de tren (opcional, para desambiguar).
        :return: Resultado de la compra.
        """
        try:
            # Paso 1: Buscar trayecto
            self._update_step(PurchaseStep.SEARCHING, "Buscando trayecto...")
            self._do_search(origin, destination, departure_date)

            # Paso 2: Seleccionar tren
            self._update_step(PurchaseStep.SELECTING_TRAIN, f"Buscando tren de las {target_departure_time}...")
            self._select_train(target_departure_time, target_train_type)

            # Paso 3: Seleccionar tarifa
            self._update_step(PurchaseStep.SELECTING_TARIFF, "Seleccionando tarifa...")
            self._select_tariff()

            # Paso 4: Rellenar datos del pasajero
            self._update_step(PurchaseStep.FILLING_PASSENGER, "Rellenando datos del pasajero...")
            self._fill_passenger_data()

            # Paso 5: Pago
            self._update_step(PurchaseStep.PAYMENT, "Procesando pago...")
            result = self._handle_payment()

            return result

        except PurchaseError as e:
            screenshot_path = self._take_error_screenshot()
            return PurchaseResult(
                success=False,
                step_reached=self._current_step,
                error_message=str(e),
                screenshot_path=screenshot_path,
            )
        except PlaywrightTimeout as e:
            screenshot_path = self._take_error_screenshot()
            return PurchaseResult(
                success=False,
                step_reached=self._current_step,
                error_message=f"Timeout en paso {self._current_step.value}: {e}",
                screenshot_path=screenshot_path,
            )
        except Exception as e:
            screenshot_path = self._take_error_screenshot()
            return PurchaseResult(
                success=False,
                step_reached=self._current_step,
                error_message=f"Error inesperado: {e}",
                screenshot_path=screenshot_path,
            )

    # ---- PASO 1: BÚSQUEDA ----

    def _do_search(self, origin: StationRecord, dest: StationRecord, date: datetime) -> None:
        """Rellena el formulario de búsqueda de Renfe y envía."""
        self.page.goto(RENFE_SEARCH_URL, wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)

        # Limpiar y rellenar origen
        self._fill_autocomplete(SEL_INPUT_ORIGIN, origin.name)
        
        # Limpiar y rellenar destino
        self._fill_autocomplete(SEL_INPUT_DEST, dest.name)
        
        # Rellenar fecha
        date_str = date.strftime("%d/%m/%Y")
        self._clear_and_type(SEL_INPUT_DATE, date_str)

        # Enviar formulario
        self.page.click(SEL_BTN_SEARCH)
        self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(3)  # Esperar carga dinámica de resultados

    # ---- PASO 2: SELECCIÓN DE TREN ----

    def _select_train(self, target_time: str, target_type: Optional[str] = None) -> None:
        """Busca y selecciona el tren por hora de salida."""
        self.page.wait_for_selector(SEL_TRAIN_ROW, timeout=10000)
        time.sleep(1)

        train_rows = self.page.query_selector_all(SEL_TRAIN_ROW)
        if not train_rows:
            raise PurchaseError("No se encontraron trenes en los resultados")

        target_row = None
        for row in train_rows:
            time_el = row.query_selector(SEL_TRAIN_TIME)
            if time_el:
                row_time = time_el.inner_text().strip()
                if target_time in row_time:
                    if target_type:
                        row_text = row.inner_text()
                        if target_type.upper() not in row_text.upper():
                            continue
                    target_row = row
                    break

        if not target_row:
            raise PurchaseError(
                f"No se encontró el tren de las {target_time}"
                + (f" tipo {target_type}" if target_type else "")
            )

        # Clic en el botón de seleccionar dentro de la fila
        select_btn = target_row.query_selector(SEL_BTN_SELECT_TRAIN)
        if select_btn:
            select_btn.click()
        else:
            # Intentar clic directo en la fila
            target_row.click()

        self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        time.sleep(2)

    # ---- PASO 3: SELECCIÓN DE TARIFA ----

    def _select_tariff(self) -> None:
        """Selecciona la tarifa más barata disponible."""
        try:
            self.page.wait_for_selector(SEL_TARIFF_OPTIONS, timeout=8000)
            time.sleep(1)
        except PlaywrightTimeout:
            # Algunas rutas pasan directamente al paso de pasajero
            return

        tariff_options = self.page.query_selector_all(SEL_TARIFF_OPTIONS)
        if tariff_options:
            # Seleccionar la primera tarifa disponible (más barata)
            tariff_options[0].click()
            time.sleep(1)

        # Continuar al siguiente paso
        try:
            btn = self.page.wait_for_selector(SEL_BTN_CONTINUE_TARIFF, timeout=5000)
            if btn:
                btn.click()
                self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(2)
        except PlaywrightTimeout:
            pass  # Puede que ya haya avanzado automáticamente

    # ---- PASO 4: DATOS DEL PASAJERO ----

    def _fill_passenger_data(self) -> None:
        """Rellena el formulario con los datos del pasajero."""
        try:
            self.page.wait_for_selector(SEL_INPUT_NAME, timeout=8000)
        except PlaywrightTimeout:
            # Si el usuario está logueado, los datos pueden estar pre-rellenados
            return

        time.sleep(1)

        # Nombre
        self._clear_and_type(SEL_INPUT_NAME, self.passenger.nombre)

        # Apellidos
        self._clear_and_type(SEL_INPUT_SURNAME1, self.passenger.apellido1)
        if self.passenger.apellido2:
            self._clear_and_type(SEL_INPUT_SURNAME2, self.passenger.apellido2)

        # Tipo de documento
        try:
            self.page.select_option(SEL_INPUT_DOCTYPE, value=self.passenger.tipo_documento.value)
        except Exception:
            try:
                self.page.select_option(SEL_INPUT_DOCTYPE, label=self.passenger.tipo_documento.value)
            except Exception:
                pass  # Puede que ya esté seleccionado

        # Número de documento
        self._clear_and_type(SEL_INPUT_DOCNUM, self.passenger.numero_documento)

        # Email
        self._clear_and_type(SEL_INPUT_EMAIL, self.passenger.email)

        # Teléfono (opcional)
        if self.passenger.telefono:
            self._clear_and_type(SEL_INPUT_PHONE, self.passenger.telefono)

        time.sleep(1)

        # Continuar
        try:
            btn = self.page.wait_for_selector(SEL_BTN_CONTINUE_PASSENGER, timeout=5000)
            if btn:
                btn.click()
                self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(2)
        except PlaywrightTimeout:
            pass

    # ---- PASO 5: PAGO ----

    def _handle_payment(self) -> PurchaseResult:
        """Gestiona el paso de pago según el método configurado."""
        time.sleep(2)

        if self.passenger.metodo_pago == PaymentMethod.ABONO:
            return self._pay_with_abono()
        else:
            return self._pay_with_card()

    def _pay_with_abono(self) -> PurchaseResult:
        """Aplica un abono/bono al billete.
        
        Los abonos están vinculados a la cuenta del usuario.
        Se selecciona el método de pago por abono y se aplica.
        """
        try:
            # Seleccionar método de pago: abono
            abono_btn = self.page.wait_for_selector(SEL_PAYMENT_ABONO, timeout=8000)
            if abono_btn:
                abono_btn.click()
                time.sleep(2)

            # Si hay selector de abono (cuando el usuario tiene varios)
            try:
                abono_select = self.page.wait_for_selector(SEL_ABONO_SELECT, timeout=3000)
                if abono_select:
                    # Seleccionar el primer abono disponible
                    options = self.page.query_selector_all(f"{SEL_ABONO_SELECT} option")
                    if len(options) > 1:  # Ignorar placeholder
                        self.page.select_option(SEL_ABONO_SELECT, index=1)
                        time.sleep(1)
            except PlaywrightTimeout:
                pass  # Solo hay un abono, se aplica directamente

            # Aplicar abono
            try:
                apply_btn = self.page.wait_for_selector(SEL_BTN_APPLY_ABONO, timeout=5000)
                if apply_btn:
                    apply_btn.click()
                    time.sleep(2)
            except PlaywrightTimeout:
                pass

            # Confirmar compra con abono (no requiere confirmación del usuario)
            pay_btn = self.page.wait_for_selector(SEL_BTN_PAY, timeout=8000)
            if pay_btn:
                # Para abonos, confirmar directamente
                pay_btn.click()
                time.sleep(3)

            # Verificar confirmación
            return self._check_confirmation()

        except PlaywrightTimeout:
            raise PurchaseError("No se encontró la opción de pago con abono. ¿Tienes un abono activo?")

    def _pay_with_card(self) -> PurchaseResult:
        """Prepara el pago con tarjeta y espera confirmación del usuario.
        
        NO completa el pago automáticamente. Llega al paso final
        y pide confirmación al usuario vía callback.
        """
        try:
            # Seleccionar método de pago: tarjeta
            card_btn = self.page.wait_for_selector(SEL_PAYMENT_CARD, timeout=8000)
            if card_btn:
                card_btn.click()
                time.sleep(2)

        except PlaywrightTimeout:
            # Puede que tarjeta sea la opción por defecto
            pass

        self._update_step(PurchaseStep.WAITING_CONFIRMATION, "Esperando confirmación para pagar...")

        # Tomar screenshot del resumen antes de pagar
        screenshot_path = "pre_payment_screenshot.png"
        self.page.screenshot(path=screenshot_path)

        # Pedir confirmación al usuario
        if self.on_confirmation_needed:
            price_text = self._get_total_price()
            message = f"💳 Billete listo para pagar. Precio: {price_text}. ¿Confirmar pago?"
            
            confirmed = self.on_confirmation_needed(message)
            
            if not confirmed:
                return PurchaseResult(
                    success=False,
                    step_reached=PurchaseStep.WAITING_CONFIRMATION,
                    error_message="Pago cancelado por el usuario",
                    screenshot_path=screenshot_path,
                )

            # Si el usuario confirma, completar el pago
            pay_btn = self.page.wait_for_selector(SEL_BTN_PAY, timeout=5000)
            if pay_btn:
                pay_btn.click()
                time.sleep(5)

            return self._check_confirmation()

        else:
            # Sin callback de confirmación → no pagar, solo llegar hasta aquí
            return PurchaseResult(
                success=False,
                step_reached=PurchaseStep.WAITING_CONFIRMATION,
                error_message="Pago preparado pero sin mecanismo de confirmación",
                screenshot_path=screenshot_path,
            )

    # ---- VERIFICACIÓN ----

    def _check_confirmation(self) -> PurchaseResult:
        """Verifica si la compra se completó correctamente."""
        try:
            self.page.wait_for_selector(SEL_CONFIRMATION, timeout=15000)
            time.sleep(2)

            # Intentar extraer localizador
            localizador = None
            try:
                loc_el = self.page.wait_for_selector(SEL_LOCALIZADOR, timeout=5000)
                if loc_el:
                    localizador = loc_el.inner_text().strip()
            except PlaywrightTimeout:
                pass

            screenshot_path = "purchase_confirmation.png"
            self.page.screenshot(path=screenshot_path)

            self._update_step(PurchaseStep.COMPLETED, f"¡Compra completada! Localizador: {localizador or 'Ver email'}")

            return PurchaseResult(
                success=True,
                step_reached=PurchaseStep.COMPLETED,
                localizador=localizador,
                screenshot_path=screenshot_path,
            )

        except PlaywrightTimeout:
            screenshot_path = self._take_error_screenshot()
            
            # Comprobar si hay errores visibles
            error_text = self._get_visible_errors()
            
            return PurchaseResult(
                success=False,
                step_reached=self._current_step,
                error_message=f"No se detectó confirmación de compra. {error_text}",
                screenshot_path=screenshot_path,
            )

    # ---- UTILIDADES ----

    def _fill_autocomplete(self, selector: str, value: str) -> None:
        """Rellena un campo con autocompletado (como los selectores de estación)."""
        try:
            el = self.page.wait_for_selector(selector, timeout=5000)
            if el:
                el.click()
                el.fill("")
                time.sleep(0.5)
                el.type(value, delay=100)
                time.sleep(1)
                # Intentar seleccionar la primera sugerencia
                try:
                    self.page.keyboard.press("ArrowDown")
                    time.sleep(0.3)
                    self.page.keyboard.press("Enter")
                except Exception:
                    pass
                time.sleep(0.5)
        except PlaywrightTimeout:
            raise PurchaseError(f"No se encontró el campo: {selector}")

    def _clear_and_type(self, selector: str, value: str) -> None:
        """Limpia un campo y escribe un valor."""
        try:
            el = self.page.wait_for_selector(selector, timeout=5000)
            if el:
                el.click()
                el.fill("")
                el.type(value, delay=50)
        except PlaywrightTimeout:
            pass  # Campo no encontrado, puede estar oculto o pre-rellenado

    def _get_total_price(self) -> str:
        """Intenta obtener el precio total de la página de pago."""
        price_selectors = [".precio-total", ".importe-total", "#precioTotal", ".total"]
        for sel in price_selectors:
            try:
                el = self.page.query_selector(sel)
                if el:
                    return el.inner_text().strip()
            except Exception:
                continue
        return "Precio no disponible"

    def _get_visible_errors(self) -> str:
        """Intenta obtener mensajes de error visibles en la página."""
        error_selectors = [".error", ".alert-danger", ".mensaje-error", ".error-message"]
        errors = []
        for sel in error_selectors:
            try:
                elements = self.page.query_selector_all(sel)
                for el in elements:
                    text = el.inner_text().strip()
                    if text:
                        errors.append(text)
            except Exception:
                continue
        return " | ".join(errors) if errors else ""

    def _take_error_screenshot(self) -> Optional[str]:
        """Toma screenshot de error."""
        try:
            path = f"error_{self._current_step.value}_{int(time.time())}.png"
            self.page.screenshot(path=path)
            return path
        except Exception:
            return None
