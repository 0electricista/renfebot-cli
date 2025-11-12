"""This module contains the logic to find trains from the CLI."""

import argparse
from datetime import datetime

from rich.box import HEAVY
from rich.console import Console
from rich.table import Table

from .scraper import Scraper
from .validators import validate_station, validate_date


def main(origin: str, destination: str, departure_date: str, from_time: str = None,console=None):
    """Searches for available train rides between the specified origin and destination stations
    on the given departure date, optionally filtering trains that depart after a given time.

    The function validates the input parameters, scrapes train ride data, and prints a table
    with train type, departure and arrival times, duration, and price. If no trains are found,
    a message is displayed.
    """

    print("\n")  # Padding line
    if console is None:
        console = Console()

    ctx = {
        "origin": validate_station(origin),
        "destination": validate_station(destination),
        "departure_date": validate_date(departure_date),
    }

    for validations in ctx.values():
        if not validations:
            console.print(f"[i][red]{validations.error_message}[/i]")
            return

    scraper = Scraper(
        ctx["origin"].station,
        ctx["destination"].station,
        ctx["departure_date"].date
    )

    trains = scraper.get_trainrides()

    # ✅ Filtrar por hora si se pasa el argumento
    if from_time:
        try:
            filter_time = datetime.strptime(from_time, "%H:%M").time()
            trains = [t for t in trains if t.departure_time.time() >= filter_time]
        except ValueError:
            console.print("[i][red]Formato de tiempo invalido. Usa HH:MM[/i]")
            return

    if not trains:
        console.print("[i][red]No se han encontrado trenes para ese filtro[/i]")
        return
    validated_date_str = ctx["departure_date"].date.strftime('%d/%m/%Y')
    # Crear tabla
    title = f"Trenes de {origin} a {destination} - {validated_date_str}"
    if from_time:
        title += f" (después de las {from_time})"

    table = Table(
        header_style="bold blue",
        title=title,
        box=HEAVY,
    )

    table.add_column("Tipo de tren")
    table.add_column("Hora salida", justify="center")
    table.add_column("Hora llegada", justify="center")
    table.add_column("Duracion", justify="right")
    table.add_column("Precio", justify="right")

    train_prices = [train.price for train in trains]
    mean_price = sum(train_prices) / len(train_prices)

    for train in trains:
        if train.available:
            price_cell = (
                f"[bold green]{train.price:.02f} €[/]"
                if train.price < mean_price
                else f"{train.price:.02f} €"
            )
            table.add_row(
                train.train_type,
                f"{train.departure_time.strftime('%H:%M')}",
                f"{train.arrival_time.strftime('%H:%M')}",
                f"{train.duration / 60:.01f} h.",
                price_cell,
            )

    console.print(table)
    return trains


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="renfe-bot", description="Find trains from the CLI")

    parser.add_argument(
        "-o", "--origin", required=True, help="Origin station code or name (required)"
    )
    parser.add_argument(
        "-d", "--destination", required=True, help="Destination station code or name (required)"
    )
    parser.add_argument(
        "--departure_date", required=True, help="Departure date in DD/MM/YYYY format (required)"
    )
    parser.add_argument(
        "--from_time", required=False, help="Filter trains departing after HH:MM (optional)"
    )

    args = parser.parse_args()

    main(args.origin, args.destination, args.departure_date, args.from_time)
