import json
import sys

import click

from .__version__ import __version__
from .parser import read_cas_pdf
from .encoder import CASDataEncoder
from .exceptions import ParserException


# noinspection PyUnusedLocal
def validate_output_filename(ctx, param, filename: str):
    if filename is None or filename.lower().endswith(".json"):
        return filename
    raise click.BadParameter("Output filename should end with .json")


def print_summary(data):
    count = 0
    err = 0
    for folio in data["folios"].values():
        for scheme in folio["schemes"]:
            calc_close = scheme["open"] + sum([x[3] for x in scheme["transactions"]])
            close_summary = f"{scheme['close']:20.4f}\t{calc_close:20.4f}"
            if calc_close != scheme["close"]:
                err += 1
                close_summary = click.style(close_summary, bold=True, fg="red")
            click.echo(
                f"{count + 1:5d}\t{scheme['scheme']:60.60s}\t{scheme['open']:20.4f}\t"
                f"{close_summary}"
            )
            count += 1
    click.secho("Summary", bold=True)
    click.echo(
        "Total   : " + click.style(f"{count:4d}", fg="white", bold=True) + " schemes"
    )
    click.echo(
        "Matched : "
        + click.style(f"{count - err:4d}", fg="green", bold=True)
        + " schemes"
    )
    click.echo(
        "Error   : " + click.style(f"{err:4d}", fg="red", bold=True) + " schemes"
    )


@click.command(name="casparser")
@click.option(
    "-o",
    "--output",
    help="Output file path (json)",
    callback=validate_output_filename,
    type=click.Path(exists=False, dir_okay=False, writable=True),
)
@click.option(
    "-s", "--summary", is_flag=True, help="Print Summary of transactions parsed."
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
@click.version_option(__version__, prog_name="casparser-cli")
@click.argument("filename", type=click.Path(exists=True), metavar="CAS_PDF_FILE")
def cli(output, summary, filename, password):
    if output is None:
        summary = True
    try:
        data = read_cas_pdf(filename, password)
    except ParserException as exc:
        click.echo(
            "Error parsing pdf file :: " + click.style(str(exc), bold=True, fg="red")
        )
        sys.exit(1)
    if summary:
        print_summary(data)
    if output is not None:
        with open(output, "w") as fp:
            json.dump(data, fp, cls=CASDataEncoder, indent=2)
        click.echo("File saved : " + click.style(output, bold=True))


if __name__ == "__main__":
    cli(prog_name="casparser")
