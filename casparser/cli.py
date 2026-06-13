import itertools
import os
import re
import sys
from decimal import Decimal
from typing import Union

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from . import __version__, read_cas_pdf
from .analysis.gains import CapitalGainsReport
from .enums import CASFileType, FileType
from .exceptions import GainsError, IncompleteCASError, ParserException
from .parsers.utils import cas2csv, cas2csv_summary, cas2json, is_close
from .types import CASData, NSDLCASData

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])
console = Console()


def formatINR(number):
    """format a number as INR (Indian grouping)"""
    prefix = {True: "-", False: ""}
    number = float(number)
    number = round(number, 2)
    is_negative = number < 0
    s = f"{abs(number):.2f}"
    int_part, dec_part = s.split(".")
    if len(int_part) <= 3:
        r = int_part
    else:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        groups = [rest[max(0, i - 2) : i or None] for i in range(len(rest), 0, -2)][::-1]
        if groups and groups[0]:
            r = ",".join(groups + [last3])
        else:
            r = last3
    return f"{prefix[is_negative]}₹{r}.{dec_part}"


def format_number(number):
    return f"{number:,}"


def validate_fy(ctx, param, value):
    return re.search(r"FY\d{4}-\d{2,4}", value, re.I) is not None


def get_color(amount: Union[Decimal, float, int]):
    """Coloured printing"""
    if amount >= 1e-3:
        return "green"
    elif amount <= -1e-3:
        return "red"
    return "white"


def print_nsdl(parsed_data: NSDLCASData):
    """Print summary of parsed data."""

    count = 0
    err = 0

    data = parsed_data.model_dump(by_alias=True)
    # console.print(data)

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
    # summary_table.add_row(Padding("CAS Type :", spacing), f"[bold]{data['cas_type']}[/]")
    for key, value in (data.get("investor_info") or {}).items():
        summary_table.add_row(
            Padding(f"{key.capitalize()} :", spacing), re.sub(r"[^\S\r\n]+", " ", value)
        )
    console.print(summary_table)
    console.print("")

    table = Table(title="Portfolio Summary", show_lines=True)
    table.add_column("Name", max_width=30, overflow="fold")
    table.add_column("ISIN", width=12)
    table.add_column("Units", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Value", justify="right")

    value = Decimal(0)

    for account in parsed_data.accounts:
        balance = account.balance
        value += balance
        running_balance = 0
        table_rows = []
        if len(account.equities) > 0:
            table_rows.append(["[italic]Equities[/]"])
        for equity in account.equities:
            running_balance += equity.num_shares * equity.price
            table_rows.append(
                [
                    equity.name,
                    equity.isin,
                    format_number(equity.num_shares),
                    formatINR(equity.price),
                    formatINR(equity.value),
                ]
            )
        if len(account.mutual_funds) > 0:
            table_rows.append(["[italic]Mutual Funds[/]"])
        for mf in account.mutual_funds:
            running_balance += mf.nav * mf.balance
            table_rows.append(
                [
                    mf.name,
                    mf.isin,
                    format_number(mf.balance),
                    formatINR(mf.nav),
                    formatINR(mf.value),
                ]
            )
        if is_close(balance, running_balance, tol=float(balance or 1) * 0.01):
            status = "️✅"
        else:
            status = "❗️"
            err += 1
        count += 1
        table.add_row(
            f"[bold]{account.name}\n{account.dp_id} - {account.client_id}[/]", "", "", "", status
        )

        for row in table_rows:
            table.add_row(*row)

    console.print(table)

    # Asset class breakdown
    equities_total = Decimal(0)
    mf_demat_total = Decimal(0)
    mf_folio_total = Decimal(0)
    debts_total = Decimal(0)

    for account in parsed_data.accounts:
        if account.type == "MF":
            mf_folio_total += sum(m.value for m in account.mutual_funds)
        else:
            eq_val = sum(e.value for e in account.equities)
            mf_val = sum(m.value for m in account.mutual_funds)
            if eq_val > 0 and mf_val == 0 and len(account.equities) == 1:
                debts_total += eq_val
            else:
                equities_total += eq_val
            mf_demat_total += mf_val

    asset_table = Table.grid(expand=True)
    asset_table.add_column(justify="left", style="bold")
    asset_table.add_column(justify="right")
    if debts_total > 0:
        asset_table.add_row("  Debts / Bonds", formatINR(debts_total))
    if equities_total > 0:
        asset_table.add_row("  Equities (demat)", formatINR(equities_total))
    if mf_demat_total > 0:
        asset_table.add_row("  Mutual Funds (demat)", formatINR(mf_demat_total))
    if mf_folio_total > 0:
        asset_table.add_row("  Mutual Fund Folios", formatINR(mf_folio_total))
    total_all = debts_total + equities_total + mf_demat_total + mf_folio_total
    asset_table.add_row("  " + "─" * 20, "")
    asset_table.add_row(
        f"  Total Portfolio Value [As of {data['statement_period']['to']}]",
        f"[bold green]{formatINR(total_all)}[/]",
    )
    console.print(asset_table)

    console.print("[bold]Summary[/]")
    console.print(f"{'Total':8s}: [bold white]{count:4d}[/] accounts")
    console.print(f"{'Matched':8s}: [bold green]{count - err:4d}[/] accounts")
    console.print(f"{'Error':8s}: [bold red]{err:4d}[/] accounts")


def print_summary(parsed_data: CASData, output_filename=None, include_zero_folios=False):
    """Print summary of parsed data."""
    count = 0
    err = 0

    data = parsed_data.model_dump(by_alias=True)
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

    for key, value in (data.get("investor_info") or {}).items():
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
        console_col_align = ["left"] + ["right"] * (len(console_header) - 2) + ["center"]
    else:
        console_col_align = ["left"] + ["right"] * (len(console_header) - 2) + ["center"]

    current_amc = None
    value = Decimal(0)
    cost = Decimal(0)

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
            if valuation["cost"] is not None:
                cost += valuation["cost"]

            if not (is_summary or folio_header_added):
                console_rows.append(
                    {
                        k: f"[bold]{current_amc}[/]" if k == "scheme" else ""
                        for k in console_header.keys()
                    }
                )
                folio_header_added = True

            scheme_close = scheme["close"]

            console_row = {
                "scheme": scheme_name,
                "open": scheme["open"],
                "close": format_number(scheme_close)
                if is_summary
                else f"{format_number(scheme_close)}\n/\n{calc_close}",
                "value": f"{formatINR(valuation['value'])}\n@\n{formatINR(valuation['nav'])}",
                "txns": len(scheme["transactions"]),
                "status": status,
            }
            console_rows.append(console_row)
            count += 1

    table = Table(title="Portfolio Summary", show_lines=True)
    for hdr, align in zip(console_header.values(), console_col_align):
        # noinspection PyTypeChecker
        table.add_column(hdr, justify=align)
    for row in console_rows:
        table.add_row(*[str(row[key]) for key in console_header.keys()])
    console.print(table)
    if cost > 0:
        console.print(f"Portfolio Cost Value : [bold green]{formatINR(cost)}[/]")
        gains = value - cost
        color = "red" if gains < 0 else "green"
        console.print(f"Portfolio Gains      : [bold {color}]{formatINR(gains)}[/]")
    console.print(
        f"Portfolio Valuation  : [bold green]{formatINR(value)}[/] "
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


def print_gifts(cg: CapitalGainsReport):
    """Render the informational gift-transfer disclosure table."""
    table = Table(title="Gift transactions (informational — not in gains)", show_lines=True)
    table.add_column("FY", no_wrap=True)
    table.add_column("Fund")
    table.add_column("Dir")
    table.add_column("Date", no_wrap=True)
    table.add_column("Units", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("Counterparty Folio")
    for gift in cg.gifts:
        table.add_row(
            gift.fy,
            gift.fund.name,
            gift.direction,
            str(gift.date),
            f"{gift.units}",
            f"{formatINR(abs(gift.value))}" if gift.value is not None else "-",
            gift.counterparty_folio or "-",
        )
    console.print(table)
    console.print(
        "[bold yellow]Note:[/] Gifts are excluded from capital-gains figures. "
        "A gift is not a transfer for the donor (Sec 47(iii)). For the recipient, "
        "cost basis and holding period carry over from the donor (Sec 49(1) / "
        "2(42A)) and are not available in this statement; the gift itself may be "
        "taxable as income from other sources (Sec 56(2)(x)) if from a non-relative. "
        "This is a disclosure, not tax advice."
    )


def print_gains(parsed_data: CASData, output_file_path=None, gains_112a=""):
    cg = CapitalGainsReport(parsed_data)
    data = parsed_data.model_dump(by_alias=True)
    if not cg.has_gains():
        console.print("[bold yellow]Warning:[/] No capital gains info found in CAS")
        if cg.has_gifts():
            print_gifts(cg)
            if isinstance(output_file_path, str):
                base_path, ext = os.path.splitext(output_file_path)
                if ext.lower().endswith("csv"):
                    fname = f"{base_path}-gifts.csv"
                    with open(fname, "w", newline="", encoding="utf-8") as fp:
                        fp.write(cg.get_gifts_csv_data())
                    console.print(f"Gift transactions saved : [bold]{fname}[/]")
        return

    summary = cg.get_summary()
    table = Table(title="Capital Gains statement (Realised)", show_lines=True)
    table.add_column("FY", no_wrap=True)
    table.add_column("Fund")
    table.add_column("LTCG")
    table.add_column("LTCG (Taxable)")
    table.add_column("STCG")

    for fy, rows in itertools.groupby(summary, lambda x: x[0]):
        table.add_row(f"[bold]{fy}[/]", "", "", "")
        ltcg_total = Decimal(0.0)
        stcg_total = Decimal(0.0)
        ltcg_taxable_total = Decimal(0.0)
        for row in rows:
            _, fund, _, _, ltcg, ltcg_taxable, stcg = row
            ltcg_total += ltcg
            stcg_total += stcg
            ltcg_taxable_total += ltcg_taxable
            table.add_row(
                "",
                fund,
                f"{formatINR(ltcg)}",
                f"{formatINR(ltcg_taxable)}",
                f"{formatINR(stcg)}",
            )
        table.add_row(
            "",
            f"[bold]{fy} - Total Gains[/]",
            f"[bold {get_color(ltcg_total)}]{formatINR(ltcg_total)}[/]",
            f"[bold {get_color(ltcg_taxable_total)}]{formatINR(ltcg_taxable_total)}[/]",
            f"[bold {get_color(stcg_total)}]{formatINR(stcg_total)}[/]",
        )
    console.print(table)

    if gains_112a != "":
        if output_file_path is None:
            console.print(
                "[bold yellow]Warning:[/] `gains_112a` option requires an output "
                "csv file path via `-o` argument. Cannot continue..."
            )
            return

        save_gains_112a(cg, gains_112a, output_file_path)

    if isinstance(output_file_path, str):
        base_path, ext = os.path.splitext(output_file_path)
        if not ext.lower().endswith("csv"):
            return
        fname = f"{base_path}-gains-summary.csv"
        with open(fname, "w", newline="", encoding="utf-8") as fp:
            fp.write(cg.get_summary_csv_data())
            console.print(f"Gains summary report saved : [bold]{fname}[/]")
        fname = f"{base_path}-gains-detailed.csv"
        with open(fname, "w", newline="", encoding="utf-8") as fp:
            fp.write(cg.get_gains_csv_data())
            console.print(f"Detailed gains report saved : [bold]{fname}[/]")
        if cg.has_gifts():
            fname = f"{base_path}-gifts.csv"
            with open(fname, "w", newline="", encoding="utf-8") as fp:
                fp.write(cg.get_gifts_csv_data())
                console.print(f"Gift transactions saved : [bold]{fname}[/]")

    if cg.has_gifts():
        print_gifts(cg)

    if cg.has_error():
        console.print("[bold red]WARNING[/] Failed to calculate gains for the following funds.")
        md_txt = []
        for scheme, _ in cg.errors:
            md_txt.append(f"- {scheme}")
        console.print(Markdown("\n".join(md_txt)))

    console.print(f"\n[bold]PnL[/] as of [bold]{data['statement_period']['to']}[/]")
    console.print(f"{'Total Invested':20s}: [bold]{formatINR(cg.invested_amount)}[/]")
    console.print(f"{'Current Valuation':20s}: [bold]{formatINR(cg.current_value)}[/]")
    pnl = cg.current_value - cg.invested_amount
    console.print(f"{'Absolute PnL':20s}: [bold {get_color(pnl)}]{formatINR(pnl)}[/]")
    console.print(
        "\n[bold yellow]Warning:[/] Capital gains module is in beta stage. "
        "Please verify the generated data manually."
    )


def save_gains_112a(capital_gains: CapitalGainsReport, fy, output_path):
    fy = fy.upper()
    fy_list = capital_gains.get_fy_list()
    if fy == "ASK":
        fy = Prompt.ask("Enter FY year: ", choices=fy_list, default=fy_list[0])
    else:
        if fy.upper() not in fy_list:
            console.print(
                f"[bold red]Warning:[/] No capital gains found for {fy}. "
                f"Please try with `--gains112a ask` option"
            )
            return
    base_path, ext = os.path.splitext(output_path)
    csv_data = capital_gains.generate_112a_csv_data(fy.upper())
    fname = f"{base_path}-{fy}-gains-112a.csv"

    with open(fname, "w", newline="", encoding="utf-8") as fp:
        fp.write(csv_data)
        console.print(f"gains report (112a) saved : [bold]{fname}[/]")


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
@click.option("-g", "--gains", is_flag=True, help="Generate Capital Gains Report (BETA)")
@click.option(
    "--gains-112a",
    help="Generate Capital Gains Report - 112A format for a financial year - "
    "Use 'ask' for a prompt from available options (BETA)",
    default="",
    metavar="ask|FY2020-21",
)
@click.option(
    "--force-pdfminer", is_flag=True, help="Force PDFMiner parser even if MuPDF is detected"
)
@click.version_option(__version__, prog_name="casparser-cli")
@click.argument("filename", type=click.Path(exists=True), metavar="CAS_PDF_FILE")
def cli(output, summary, password, include_all, gains, gains_112a, force_pdfminer, filename):
    """CLI function."""
    output_ext = None
    if output is not None:
        output_ext = os.path.splitext(output)[-1].lower()

    if not (summary or gains or output_ext in (".csv", ".json")):
        summary = True
    try:
        with Progress(
            SpinnerColumn(spinner_name="clock"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(pulse_style="yellow"),
            transient=True,
        ) as progress:
            progress.add_task("Reading CAS file", start=False, total=10.0)
            data = read_cas_pdf(filename, password, force_pdfminer=force_pdfminer)
    except ParserException as exc:
        console.print(f"Error parsing pdf file :: [bold red]{str(exc)}[/]")
        sys.exit(1)

    # Surface non-fatal data-quality warnings (e.g. a scheme whose
    # transactions don't reconcile against its printed Unit Balance) so a
    # silently-wrong parse becomes a visible one.
    warnings_list = getattr(data, "parse_warnings", None) or []
    if warnings_list:
        console.print(
            f"[bold yellow]Warning:[/] {len(warnings_list)} data-quality "
            f"issue(s) detected while parsing:"
        )
        for w in warnings_list:
            console.print(f"  [yellow]•[/] {w}")

    if isinstance(data, NSDLCASData):
        print_nsdl(data)
    elif summary:
        print_summary(
            data,
            include_zero_folios=include_all,
            output_filename=None if output_ext in (".csv", ".json") else output,
        )

    if output_ext in (".csv", ".json"):
        if output_ext == ".csv" and data.file_type in (
            FileType.CAMS.value,
            FileType.KFINTECH.value,
        ):
            if summary or data.cas_type == CASFileType.SUMMARY.name:
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
    if data.file_type in (FileType.CAMS.value, FileType.KFINTECH.value) and (gains or gains_112a):
        try:
            print_gains(
                data,
                output_file_path=output if output_ext == ".csv" else None,
                gains_112a=gains_112a,
            )
        except IncompleteCASError:
            console.print("[bold red]Error![/] - Cannot compute gains. CAS is incomplete!")
            sys.exit(2)
        except GainsError as exc:
            console.print(exc)


if __name__ == "__main__":
    cli(prog_name="casparser")
