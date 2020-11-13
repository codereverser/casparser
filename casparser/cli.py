from decimal import Decimal
import json
import os
import re
import shutil
import sys
import textwrap

import click

# noinspection PyProtectedMember
from tabulate import tabulate, _table_formats


from .__version__ import __version__

from . import read_cas_pdf
from .encoder import CASDataEncoder
from .exceptions import ParserException
from .parsers.utils import isclose

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def print_summary(data, tablefmt="fancy_grid", output_filename=None, include_zero_folios=False):
    """Print summary of parsed data."""
    count = 0
    err = 0

    if output_filename:
        fmt = "fancy_grid"
    else:
        fmt = tablefmt

    print_extra_info = fmt in ("simple", "plain", "fancy_grid", "grid", "pretty")
    if print_extra_info:
        click.echo("\n")
        click.echo(
            f"{'Statement Period':>40s}: "
            f"{click.style(data['statement_period']['from'], fg='green', bold=True)}"
            f"  To {click.style(data['statement_period']['to'], fg='green', bold=True)}"
        )
        click.echo(f"{'File Type':>40s}: {click.style(data['file_type'], bold=True)}")
        for key, value in data["investor_info"].items():
            fmt_value = " ".join([x.strip() for x in value.splitlines()])
            fmt_value = re.sub(r"\s+", " ", fmt_value)
            if len(fmt_value) > 40:
                fmt_value = fmt_value[:37] + "..."
            click.echo(f"{key:>40s}: {fmt_value}")
        click.echo("")
    rows = []
    console_rows = []

    console_header = [
        "Scheme",
        "Open",
        "Close\n\nReported\nvs.\nCalculated",
        f"Value\n({data['statement_period']['to']})",
        "Txns",
        "",
    ]
    header = [
        "Scheme",
        "Open",
        "Close Reported",
        "Close Calculated",
        f"NAV ({data['statement_period']['to']})",
        f"Value ({data['statement_period']['to']})",
        "Transactions",
        "Status",
    ]
    col_align = ["left"] + ["right"] * (len(header) - 2) + ["center"]
    console_col_align = ["left"] + ["right"] * (len(console_header) - 2) + ["center"]

    current_amc = None
    value = Decimal(0)
    columns, _ = shutil.get_terminal_size()
    scheme_col_width = columns - 66

    folio_header_added = False
    for folio in data["folios"]:
        if current_amc != folio.get("amc", ""):
            folio_header_added = False
            current_amc = folio["amc"]
        for scheme in folio["schemes"]:

            if scheme["close"] < 1e-3 and not include_zero_folios:
                continue

            calc_close = scheme["open"] + sum([x["units"] for x in scheme["transactions"]])
            valuation = scheme["valuation"]

            # Check is calculated close (i.e. open + units from all transactions) is same as
            # reported close and also the scheme valuation = nav * calculated close.
            if calc_close != scheme["close"] or not isclose(
                valuation["nav"] * calc_close, valuation["value"], tol=2
            ):
                err += 1
                status = "❗️"
            else:
                status = "️✅"
            wrapped_name = textwrap.fill(scheme["scheme"], width=scheme_col_width)
            folio_number = re.sub(r"\s+", "", folio["folio"])
            folio_string = textwrap.fill(f"Folio: {folio_number}", width=scheme_col_width)
            scheme_name = f"{wrapped_name}\n{folio_string}"
            value += valuation["value"]

            if not folio_header_added:
                rows.append(
                    [textwrap.fill(current_amc, width=scheme_col_width)] + [""] * (len(header) - 1)
                )
                console_rows.append(
                    [textwrap.fill(current_amc, width=scheme_col_width)]
                    + [""] * (len(console_header) - 1)
                )
                folio_header_added = True

            console_rows.append(
                [
                    scheme_name,
                    scheme["open"],
                    f"{scheme['close']}\n/\n{calc_close}",
                    f"₹{valuation['value']:,.2f}\n@\n₹{valuation['nav']:,.2f}",
                    len(scheme["transactions"]),
                    status,
                ]
            )
            rows.append(
                [
                    scheme_name,
                    scheme["open"],
                    scheme["close"],
                    calc_close,
                    valuation["nav"],
                    valuation["value"],
                    len(scheme["transactions"]),
                    status,
                ]
            )
            count += 1
    if print_extra_info:
        click.echo(tabulate(console_rows, console_header, tablefmt=fmt, colalign=console_col_align))
        click.echo(
            "Portfolio Valuation : "
            + click.style(f"₹{value:,.2f}", fg="green", bold=True)
            + f" [As of {data['statement_period']['to']}]"
        )
        click.secho("Summary", bold=True)
        click.echo("Total   : " + click.style(f"{count:4d}", fg="white", bold=True) + " schemes")
        click.echo(
            "Matched : " + click.style(f"{count - err:4d}", fg="green", bold=True) + " schemes"
        )
        click.echo("Error   : " + click.style(f"{err:4d}", fg="red", bold=True) + " schemes")
    else:
        click.echo(tabulate(rows, header, tablefmt=fmt, colalign=col_align))

    if output_filename:
        with open(output_filename, "w") as f:
            f.write(tabulate(rows, header, tablefmt=tablefmt, colalign=col_align))
        click.echo("File saved : " + click.style(output_filename, bold=True))


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
    type=click.Choice(_table_formats.keys()),
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
    help="Include schemes with zero valuation in the summary output"
)
@click.option(
    "--force-pdfminer", is_flag=True, help="Force PDFMiner parser even if MuPDF is detected"
)
@click.version_option(__version__, prog_name="casparser-cli")
@click.argument("filename", type=click.Path(exists=True), metavar="CAS_PDF_FILE")
def cli(output, summary, password, include_all, force_pdfminer, filename):
    """CLI function."""
    output_ext = None
    if output is not None:
        output_ext = os.path.splitext(output)[-1].lower()

    if not (summary or output_ext == ".json"):
        summary = "fancy_grid"

    try:
        data = read_cas_pdf(filename, password, force_pdfminer=force_pdfminer)
    except ParserException as exc:
        click.echo("Error parsing pdf file :: " + click.style(str(exc), bold=True, fg="red"))
        sys.exit(1)
    if summary:
        print_summary(
            data, tablefmt=summary, include_zero_folios=include_all, output_filename=None if output_ext == ".json" else output
        )
    if output_ext == ".json":
        with open(output, "w") as fp:
            json.dump(data, fp, cls=CASDataEncoder, indent=2)
        click.echo("File saved : " + click.style(output, bold=True))


if __name__ == "__main__":
    cli(prog_name="casparser")
