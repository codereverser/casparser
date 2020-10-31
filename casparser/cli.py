import json
import re
import sys

import click
import texttable

from .__version__ import __version__

try:
    from .parsers.mupdf import read_cas_pdf
except ImportError:
    from .parsers.pdfminer import read_cas_pdf
from .encoder import CASDataEncoder
from .exceptions import ParserException

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


# noinspection PyUnusedLocal
def validate_output_filename(ctx, param, filename: str):
    if filename is None or filename.lower().endswith(".json"):
        return filename
    raise click.BadParameter("Output filename should end with .json")


def print_summary(data):
    count = 0
    err = 0
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
    table = texttable.Texttable(max_width=100)
    table.set_cols_align(["l", "r", "r", "r", "r", "c"])
    table.set_cols_valign(["m", "m", "m", "m", "m", "m"])
    table.add_row(
        ["Scheme", "Open", "Close\nReported", "Close\nCalculated", "Transactions", "Status"]
    )
    current_amc = None
    for folio in data["folios"]:
        if current_amc != folio.get("amc", ""):
            current_amc = folio["amc"]
            table.add_row([current_amc] + [""] * 5)
        for scheme in folio["schemes"]:
            calc_close = scheme["open"] + sum([x["units"] for x in scheme["transactions"]])
            if calc_close != scheme["close"]:
                err += 1
                status = "❗️"
            else:
                status = "️✅"
            scheme_name = f"{scheme['scheme']}\nFolio: {folio['folio']}"
            table.add_row(
                [
                    scheme_name,
                    scheme["open"],
                    scheme["close"],
                    calc_close,
                    len(scheme["transactions"]),
                    status,
                ]
            )
            count += 1
    click.echo(table.draw())
    click.secho("Summary", bold=True)
    click.echo("Total   : " + click.style(f"{count:4d}", fg="white", bold=True) + " schemes")
    click.echo("Matched : " + click.style(f"{count - err:4d}", fg="green", bold=True) + " schemes")
    click.echo("Error   : " + click.style(f"{err:4d}", fg="red", bold=True) + " schemes")


@click.command(name="casparser", context_settings=CONTEXT_SETTINGS)
@click.option(
    "-o",
    "--output",
    help="Output file path (json)",
    callback=validate_output_filename,
    type=click.Path(exists=False, dir_okay=False, writable=True),
)
@click.option("-s", "--summary", is_flag=True, help="Print Summary of transactions parsed.")
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
    "--force-pdfminer", is_flag=True, help="Force PDFMiner parser even if MuPDF is detected"
)
@click.version_option(__version__, prog_name="casparser-cli")
@click.argument("filename", type=click.Path(exists=True), metavar="CAS_PDF_FILE")
def cli(output, summary, password, force_pdfminer, filename):
    if output is None and not summary:
        click.echo("No output file provided. Printing summary")
        summary = True
    try:
        if force_pdfminer:
            from .parsers.pdfminer import read_cas_pdf as read_cas_pdf_pm

            data = read_cas_pdf_pm(filename, password)
        else:
            data = read_cas_pdf(filename, password)
    except ParserException as exc:
        click.echo("Error parsing pdf file :: " + click.style(str(exc), bold=True, fg="red"))
        sys.exit(1)
    if summary:
        print_summary(data)
    if output is not None:
        with open(output, "w") as fp:
            json.dump(data, fp, cls=CASDataEncoder, indent=2)
        click.echo("File saved : " + click.style(output, bold=True))


if __name__ == "__main__":
    cli(prog_name="casparser")
