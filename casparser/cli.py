from decimal import Decimal
import os
import re
import sys

import click
from rich.console import Console
from rich.padding import Padding
from rich.progress import BarColumn, TextColumn, SpinnerColumn, Progress
from rich.table import Table

from .__version__ import __version__

from . import read_cas_pdf
from .enums import CASFileType
from .exceptions import ParserException
from .parsers.utils import is_close, cas2json, cas2csv, cas2csv_summary

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])
console = Console()


def print_summary(data, output_filename=None, include_zero_folios=False):
    """Print summary of parsed data."""
    count = 0
    err = 0

    is_summary = data["cas_type"] == CASFileType.SUMMARY.name

    # Print CAS header stuff
    summary_table = Table.grid(expand=True)
    summary_table.add_column(justify="right")
    summary_table.add_column(justify="left")
    spacing = (0, 1)
    summary_table.add_row(
        Padding("Statement Period :", spacing),
        f"[bold green]{data['statement_period']['from']}[/] To "
        f"[bold green]{data['statement_period']['to']}[/]",
    )
    summary_table.add_row(Padding("File Type :", spacing), f"[bold]{data['file_type']}[/]")
    summary_table.add_row(Padding("CAS Type :", spacing), f"[bold]{data['cas_type']}[/]")

    for key, value in data["investor_info"].items():
        summary_table.add_row(
            Padding(f"{key.capitalize()} :", spacing), re.sub(r"[^\S\r\n]+", " ", value)
        )
    console.print(summary_table)
    console.print("")

    console_rows = []

    console_header = {
        "scheme": "Scheme",
        "open": "Open",
        "close": "Close" if is_summary else "Close\n\nReported\nvs.\nCalculated",
        "value": f"Value\n({data['statement_period']['to']})",
        "txns": "Txns",
        "status": "",
    }
    if is_summary:
        console_header.update(close="Balance")
        console_header.pop("open")
        console_header.pop("txns")
        console_col_align = ["left"] + ["right"] * (len(console_header) - 4) + ["center"]
    else:
        console_col_align = ["left"] + ["right"] * (len(console_header) - 2) + ["center"]

    current_amc = None
    value = Decimal(0)

    folio_header_added = False
    for folio in data["folios"]:
        if current_amc != folio.get("amc", ""):
            folio_header_added = False
            current_amc = folio["amc"]
        for scheme in folio["schemes"]:

            if scheme["close"] < 1e-3 and not include_zero_folios:
                continue

            calc_close = scheme.get("close_calculated", scheme["open"])
            valuation = scheme["valuation"]

            # Check is calculated close (i.e. open + units from all transactions) is same as
            # reported close and also the scheme valuation = nav * calculated close.
            if calc_close != scheme["close"] or not is_close(
                valuation["nav"] * calc_close, valuation["value"], tol=2
            ):
                err += 1
                status = "❗️"
            else:
                status = "️✅"
            folio_number = re.sub(r"\s+", "", folio["folio"])
            scheme_name = f"{scheme['scheme']}\nFolio: {folio_number}"
            value += valuation["value"]

            if not (is_summary or folio_header_added):
                console_rows.append(
                    {k: current_amc if k == "scheme" else "" for k in console_header.keys()}
                )
                folio_header_added = True

            console_row = {
                "scheme": scheme_name,
                "open": scheme["open"],
                "close": scheme["close"] if is_summary else f"{scheme['close']}\n/\n{calc_close}",
                "value": f"₹{valuation['value']:,.2f}\n@\n₹{valuation['nav']:,.2f}",
                "txns": len(scheme["transactions"]),
                "status": status,
            }
            console_rows.append(console_row)
            count += 1

    table = Table(title="Portfolio Summary", show_lines=True)
    for (hdr, align) in zip(console_header.values(), console_col_align):
        # noinspection PyTypeChecker
        table.add_column(hdr, justify=align)
    for row in console_rows:
        table.add_row(*[str(row[key]) for key in console_header.keys()])
    console.print(table)
    console.print(
        f"Portfolio Valuation : [bold green]₹{value:,.2f}[/] "
        f"[As of {data['statement_period']['to']}]"
    )
    console.print("[bold]Summary[/]")
    console.print(f"{'Total':8s}: [bold white]{count:4d}[/] schemes")
    console.print(f"{'Matched':8s}: [bold green]{count - err:4d}[/] schemes")
    console.print(f"{'Error':8s}: [bold red]{err:4d}[/] schemes")

    if output_filename:
        with open(output_filename, "w", encoding="utf-8") as fp:
            writer = Console(file=fp, width=80)
            writer.print(table)
        console.print(f"File saved : [bold]{output_filename}[/]")


@click.command(name="casparser", context_settings=CONTEXT_SETTINGS)
@click.option(
    "-o",
    "--output",
    help="Output file path",
    type=click.Path(dir_okay=False, writable=True),
)
@click.option(
    "-s",
    "--summary",
    is_flag=True,
    help="Print Summary of transactions parsed.",
)
@click.option(
    "-p",
    "password",
    metavar="PASSWORD",
    prompt="Enter PDF password",
    hide_input=True,
    confirmation_prompt=False,
    help="CAS password",
)
@click.option(
    "-a",
    "--include-all",
    is_flag=True,
    help="Include schemes with zero valuation in the summary output",
)
@click.option("--sort", is_flag=True, help="Sort transactions by date")
@click.option(
    "--force-pdfminer", is_flag=True, help="Force PDFMiner parser even if MuPDF is detected"
)
@click.version_option(__version__, prog_name="casparser-cli")
@click.argument("filename", type=click.Path(exists=True), metavar="CAS_PDF_FILE")
def cli(output, summary, password, include_all, sort, force_pdfminer, filename):
    """CLI function."""
    output_ext = None
    if output is not None:
        output_ext = os.path.splitext(output)[-1].lower()

    if not (summary or output_ext in (".csv", ".json")):
        summary = True
    try:
        with Progress(
            SpinnerColumn(spinner_name="clock"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(pulse_style="yellow"),
            transient=True,
        ) as progress:
            progress.add_task("Reading CAS file", start=False, total=10.0)
            data = read_cas_pdf(
                filename, password, force_pdfminer=force_pdfminer, sort_transactions=sort
            )
    except ParserException as exc:
        console.print(f"Error parsing pdf file :: [bold red]{str(exc)}[/]")
        sys.exit(1)
    if summary:
        print_summary(
            data,
            include_zero_folios=include_all,
            output_filename=None if output_ext in (".csv", ".json") else output,
        )

    if output_ext in (".csv", ".json"):
        if output_ext == ".csv":
            if summary or data["cas_type"] == CASFileType.SUMMARY.name:
                description = "Generating summary CSV file..."
                conv_fn = cas2csv_summary
            else:
                description = "Generating detailed CSV file..."
                conv_fn = cas2csv
        else:
            description = "Generating JSON file..."
            conv_fn = cas2json
        console.print(description)
        with open(output, "w", newline="", encoding="utf-8") as fp:
            fp.write(conv_fn(data))
        console.print(f"File saved : [bold]{output}[/]")


if __name__ == "__main__":
    cli(prog_name="casparser")
