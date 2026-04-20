#!/usr/bin/env python3
"""CLI-verktyg för att hantera Svenska lag-kallelser på Älta IF."""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import click
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / ".session.json"
DOMAIN = os.getenv("ALTAIF_DOMAIN", "www.altaif.se")
BASE_URL = f"https://{DOMAIN}"
SITE_SLUG = os.getenv("ALTAIF_SITE_SLUG", "altaif")

console = Console()


# ---------------------------------------------------------------------------
# Session / Auth
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 svlag-cli/1.0"})
    if SESSION_FILE.exists():
        try:
            s.cookies.update(json.loads(SESSION_FILE.read_text()))
        except Exception:
            pass
    return s


def _save_session(session: requests.Session):
    SESSION_FILE.write_text(json.dumps(dict(session.cookies)))


def _login(session: requests.Session) -> bool:
    user = os.getenv("ALTAIF_USER")
    password = os.getenv("ALTAIF_PASS")
    if not user or not password:
        console.print("[red]Saknar ALTAIF_USER eller ALTAIF_PASS i .env[/red]")
        sys.exit(1)

    resp = session.post(
        f"{BASE_URL}/{SITE_SLUG}/logga-in",
        data={"UserName": user, "UserPass": password, "cbautologin": "on"},
        headers={"Referer": f"{BASE_URL}/{SITE_SLUG}"},
    )
    resp.raise_for_status()
    try:
        data = resp.json()
        if data.get("error"):
            console.print(f"[red]Inloggning misslyckades: {data['error']}[/red]")
            return False
    except Exception:
        pass
    _save_session(session)
    return True


def _ensure_logged_in(session: requests.Session):
    resp = session.get(
        f"{BASE_URL}/{SITE_SLUG}/minasidor/kallelser",
        allow_redirects=True,
    )
    if "logga-in" in resp.url or "logga-ut" not in resp.text:
        console.print("[yellow]Loggar in...[/yellow]")
        if not _login(session):
            sys.exit(1)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_kallelser(html: str) -> list[dict]:
    """Parse /minasidor/kallelser HTML into a list of activity dicts."""
    soup = BeautifulSoup(html, "html.parser")
    activities = []
    current_date = ""

    table = soup.find("table", class_=re.compile("cp-table"))
    if not table:
        return []

    for row in table.find_all("tr"):
        if "header" in row.get("class", []):
            th = row.find("th")
            if th:
                current_date = th.get_text(strip=True)
        else:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            time_str = cells[0].get_text(strip=True)
            name_cell = cells[1]
            status_cell = cells[-1]

            # Extract name, team, link, memberid
            link_tag = name_cell.find("a")
            if link_tag:
                href = link_tag.get("href", "")
                name = link_tag.get_text(strip=True)
                # Extract scheduleId from URL like /team-slug/aktivitet/12345/slug
                m = re.search(r'/aktivitet/(\d+)', href)
                schedule_id = int(m.group(1)) if m else None
                m2 = re.search(r'memberid=(\d+)', href)
                member_id = m2.group(1) if m2 else None
                # Extract team slug
                m3 = re.match(r'/([^/]+)/aktivitet', href)
                team_slug = m3.group(1) if m3 else None
                expired = link_tag.get("class", [])
                can_reply = "delete" in expired  # "delete" means reply still open
            else:
                # No link = expired/closed
                name = name_cell.find(string=True, recursive=False) or name_cell.get_text(strip=True).split("\n")[0]
                href = ""
                schedule_id = None
                member_id = None
                team_slug = None
                can_reply = False

            # Team name from dimmed span
            dimmed = name_cell.find("span", class_="dimmed")
            team_name = dimmed.get_text(strip=True) if dimmed else ""

            # Deadline from 3rd cell (hidden-xs-portrait)
            deadline_cells = row.find_all("td", class_=re.compile("hidden"))
            deadline = deadline_cells[0].get_text(strip=True) if deadline_cells else ""

            status_text = status_cell.get_text(strip=True)
            status_class = status_cell.get("class", [])
            if "yes" in status_class:
                status = "ja"
            elif "no" in status_class:
                status = "nej"
            else:
                status = "?"

            activities.append({
                "date": current_date,
                "time": time_str,
                "name": name,
                "team": team_name,
                "href": href,
                "scheduleId": schedule_id,
                "memberId": member_id,
                "teamSlug": team_slug,
                "deadline": deadline,
                "status": status,
                "canReply": can_reply,
            })

    return activities


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Svenska lag CLI – hantera dina kallelser på Älta IF."""


@cli.command()
@click.option("--alla", is_flag=True, default=False, help="Visa alla historiska aktiviteter (standard: 15 senaste)")
@click.option("--json", "as_json", is_flag=True, default=False, help="Skriv ut JSON istället för tabell")
def bokningar(alla: bool, as_json: bool):
    """Lista aktiviteter du blivit kallad till."""
    session = _build_session()
    _ensure_logged_in(session)

    resp = session.get(f"{BASE_URL}/{SITE_SLUG}/minasidor/kallelser")
    resp.raise_for_status()
    activities = _parse_kallelser(resp.text)

    if not activities:
        if as_json:
            print(json.dumps([], ensure_ascii=False))
        else:
            console.print("[yellow]Inga kallelser hittades.[/yellow]")
        return

    if not alla:
        activities = activities[:15]

    if as_json:
        output = [
            {
                "scheduleId": a["scheduleId"],
                "date": a["date"],
                "time": a["time"],
                "name": a["name"],
                "team": a["team"],
                "deadline": a["deadline"],
                "status": a["status"],
                "canReply": a["canReply"],
            }
            for a in activities
        ]
        print(json.dumps(output, ensure_ascii=False))
        return

    table = Table(title="Mina kallelser", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Datum & tid", width=20)
    table.add_column("Aktivitet", min_width=20)
    table.add_column("Lag", min_width=20)
    table.add_column("Svar senast", width=16)
    table.add_column("Svar", width=12)

    for a in activities:
        sid = str(a["scheduleId"]) if a["scheduleId"] else "–"
        date_time = f"{a['date']} {a['time']}"
        status = a["status"]
        if status == "ja":
            status_text = "[green]✓ Kommer[/green]"
        elif status == "nej":
            status_text = "[red]✗ Kommer ej[/red]"
        else:
            if a["canReply"]:
                status_text = "[yellow]? Ej svarat[/yellow]"
            else:
                status_text = "[dim]Utgången[/dim]"

        table.add_row(sid, date_time, a["name"], a["team"], a["deadline"], status_text)

    console.print(table)
    unanswered = sum(1 for a in activities if a["status"] == "?" and a["canReply"])
    if unanswered:
        console.print(f"\n[yellow]{unanswered} obesvarade kallelser.[/yellow]")
    console.print("[dim]Svara med: python svlag.py svara <ID> ja/nej[/dim]")


@cli.command()
@click.argument("aktivitet_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False, help="Skriv ut JSON istället för text")
def svar(aktivitet_id: int, as_json: bool):
    """Visa vem som svarat på en specifik aktivitet."""
    session = _build_session()
    _ensure_logged_in(session)

    # Get personal kallelser to find the activity URL/team
    resp = session.get(f"{BASE_URL}/{SITE_SLUG}/minasidor/kallelser")
    resp.raise_for_status()
    activities = _parse_kallelser(resp.text)

    target = next((a for a in activities if a["scheduleId"] == aktivitet_id), None)
    if not target:
        if as_json:
            print(json.dumps({"error": f"Hittade inte aktivitet {aktivitet_id}"}, ensure_ascii=False))
            sys.exit(1)
        console.print(f"[red]Hittade inte aktivitet {aktivitet_id}. Kör 'bokningar' för att se ID:n.[/red]")
        sys.exit(1)

    member_id = target["memberId"]

    # Fetch the activity page to get attendance list
    activity_url = f"{BASE_URL}{target['href'].split('?')[0]}"
    resp2 = session.get(activity_url, params={"memberid": member_id} if member_id else {})
    resp2.raise_for_status()
    html = resp2.text
    soup = BeautifulSoup(html, "html.parser")

    # Parse attendance table
    attendance_table = soup.find("table", class_=re.compile("content-block-table"))
    yes_list, no_list, unanswered_list = [], [], []
    if attendance_table:
        for tbody in attendance_table.find_all("tbody", attrs={"data-memberid": True}):
            name_cell = tbody.find("b")
            name = name_cell.get_text(strip=True) if name_cell else "?"
            icon = tbody.find("i", class_=re.compile("fa-"))
            if icon:
                classes = " ".join(icon.get("class", []))
                if "check" in classes:
                    yes_list.append(name)
                elif "times" in classes or "close" in classes:
                    comment_el = tbody.find(class_=re.compile("comment|anledning|text-muted"))
                    comment = comment_el.get_text(strip=True) if comment_el else ""
                    no_list.append((name, comment))
                else:
                    unanswered_list.append(name)

    if as_json:
        print(json.dumps({
            "scheduleId": aktivitet_id,
            "name": target["name"],
            "date": target["date"],
            "time": target["time"],
            "team": target["team"],
            "attending": yes_list,
            "notAttending": [{"name": n, "comment": c} for n, c in no_list],
            "unanswered": unanswered_list,
        }, ensure_ascii=False))
        return

    console.print(f"\n[bold]{target['name']}[/bold] – {target['date']} {target['time']}")
    console.print(f"Lag: {target['team']}\n")

    if not attendance_table:
        console.print("[yellow]Kunde inte hämta deltagarlistan.[/yellow]")
        return

    if yes_list:
        console.print(f"[green]Kommer ({len(yes_list)}):[/green]")
        for n in yes_list:
            console.print(f"  ✓ {n}")
        console.print()

    if no_list:
        console.print(f"[red]Kommer ej ({len(no_list)}):[/red]")
        for n, comment in no_list:
            console.print(f"  ✗ {n}" + (f" – [dim]{comment}[/dim]" if comment else ""))
        console.print()

    if unanswered_list:
        console.print(f"[yellow]Ej svarat ({len(unanswered_list)}):[/yellow]")
        for n in unanswered_list:
            console.print(f"  ? {n}")


@cli.command()
@click.argument("aktivitet_id", type=int)
@click.argument("svar_typ", type=click.Choice(["ja", "nej"], case_sensitive=False))
@click.option("-k", "--kommentar", default="", help="Anledning (krävs vid nej)")
@click.option("--json", "as_json", is_flag=True, default=False, help="Skriv ut JSON istället för text")
def svara(aktivitet_id: int, svar_typ: str, kommentar: str, as_json: bool):
    """Svara ja eller nej på en kallelse."""
    if svar_typ.lower() == "nej" and not kommentar:
        if as_json:
            print(json.dumps({"success": False, "error": "Kommentar krävs vid nej (-k)"}, ensure_ascii=False))
        else:
            console.print("[red]Du måste ange en anledning med -k när du svarar nej.[/red]")
            console.print('Exempel: python svlag.py svara 12345 nej -k "Bortrest"')
        sys.exit(1)

    session = _build_session()
    _ensure_logged_in(session)

    resp = session.get(f"{BASE_URL}/{SITE_SLUG}/minasidor/kallelser")
    resp.raise_for_status()
    activities = _parse_kallelser(resp.text)

    target = next((a for a in activities if a["scheduleId"] == aktivitet_id), None)
    if not target:
        if as_json:
            print(json.dumps({"success": False, "error": f"Hittade inte aktivitet {aktivitet_id}"}, ensure_ascii=False))
        else:
            console.print(f"[red]Hittade inte aktivitet {aktivitet_id}.[/red]")
        sys.exit(1)

    if not target["canReply"]:
        if as_json:
            print(json.dumps({"success": False, "error": "Svarstiden för denna aktivitet har gått ut"}, ensure_ascii=False))
        else:
            console.print(f"[red]Svarstiden för denna aktivitet har gått ut.[/red]")
        sys.exit(1)

    team_slug = target["teamSlug"]
    member_id = target["memberId"]
    attending = svar_typ.lower() == "ja"

    resp2 = session.post(
        f"{BASE_URL}/{team_slug}/invites/savereply",
        params={"code": ""},
        data={
            "memberId": member_id,
            "scheduleId": aktivitet_id,
            "attending": "true" if attending else "false",
            "comment": kommentar,
        },
        headers={"Referer": f"{BASE_URL}{target['href']}"},
    )
    resp2.raise_for_status()

    try:
        result = resp2.json()
        success = result.get("status") == "OK"
        if as_json:
            if success:
                print(json.dumps({"success": True, "scheduleId": aktivitet_id, "reply": svar_typ.lower()}, ensure_ascii=False))
            else:
                print(json.dumps({"success": False, "error": result.get("errorMessage", "Okänt fel")}, ensure_ascii=False))
        else:
            if success:
                emoji = "✓" if attending else "✗"
                color = "green" if attending else "red"
                console.print(f"[{color}]{emoji} Svar sparat: {svar_typ.upper()}[/{color}]")
                console.print(f"[dim]Aktivitet: {target['name']} – {target['date']} {target['time']}[/dim]")
            else:
                console.print(f"[red]Misslyckades: {result.get('errorMessage', 'Okänt fel')}[/red]")
    except Exception:
        ok = resp2.text.strip() == "OK"
        if as_json:
            if ok:
                print(json.dumps({"success": True, "scheduleId": aktivitet_id, "reply": svar_typ.lower()}, ensure_ascii=False))
            else:
                print(json.dumps({"success": False, "error": resp2.text[:200]}, ensure_ascii=False))
        else:
            if ok:
                console.print(f"[green]✓ Svar sparat: {svar_typ.upper()}[/green]")
            else:
                console.print(f"[yellow]Svar: {resp2.text[:200]}[/yellow]")


@cli.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Skriv ut JSON istället för tabell")
def lag(as_json: bool):
    """Lista tillgängliga lag-slugs på Älta IF."""
    resp = requests.get(f"{BASE_URL}/lag", headers={"User-Agent": "Mozilla/5.0 svlag-cli/1.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    seen = set()
    teams = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.match(r'^/altaif-[a-z0-9-]+$', href) and href not in seen:
            seen.add(href)
            slug = href.lstrip("/")
            name = a.get_text(strip=True)
            if name:
                teams.append({"slug": slug, "name": name})

    if as_json:
        print(json.dumps(teams, ensure_ascii=False))
        return

    console.print("[bold]Hämtar lag-lista...[/bold]\n")
    table = Table(title="Tillgängliga lag", show_header=True)
    table.add_column("Slug", min_width=45)
    table.add_column("Namn", min_width=25)
    for t in teams:
        table.add_row(t["slug"], t["name"])
    console.print(table)


if __name__ == "__main__":
    cli()
