#!/usr/bin/env python3
"""CLI-verktyg för att hantera Svenska lag-kallelser på Älta IF."""

import datetime
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def _split_time(time_str: str) -> tuple[str, str]:
    """Split 'HH:MM-HH:MM' into (startTime, endTime)."""
    m = re.match(r'(\d{1,2}:\d{2})-(\d{1,2}:\d{2})', time_str.strip())
    if m:
        return m.group(1), m.group(2)
    return time_str.strip(), ""


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
            start_time, end_time = _split_time(time_str)
            name_cell = cells[1]
            status_cell = cells[-1]

            link_tag = name_cell.find("a")
            if link_tag:
                href = link_tag.get("href", "")
                name = link_tag.get_text(strip=True)
                # Match both /aktivitet/ and /match/ URLs
                m = re.search(r'/(?:aktivitet|match)/(\d+)', href)
                schedule_id = int(m.group(1)) if m else None
                m2 = re.search(r'memberid=(\d+)', href)
                member_id = m2.group(1) if m2 else None
                m3 = re.match(r'/([^/]+)/(?:aktivitet|match)', href)
                team_slug = m3.group(1) if m3 else None
                can_reply = "delete" in link_tag.get("class", [])
            else:
                name = name_cell.find(string=True, recursive=False) or name_cell.get_text(strip=True).split("\n")[0]
                href = ""
                schedule_id = None
                member_id = None
                team_slug = None
                can_reply = False

            # Activity type from name and URL
            name_lower = (name or "").lower()
            href_lower = href.lower()
            if "/match/" in href_lower or any(w in name_lower for w in ("match", "cup", "serie", "turnering")):
                activity_type = "match"
            elif "träning" in name_lower or "training" in name_lower or "/aktivitet/" in href_lower:
                activity_type = "träning"
            else:
                activity_type = "övrigt"

            dimmed = name_cell.find("span", class_="dimmed")
            team_name = dimmed.get_text(strip=True) if dimmed else ""

            deadline_cells = row.find_all("td", class_=re.compile("hidden"))
            deadline = deadline_cells[0].get_text(strip=True) if deadline_cells else ""

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
                "startTime": start_time,
                "endTime": end_time,
                "name": name,
                "type": activity_type,
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


def _parse_activity_detail(soup: BeautifulSoup) -> dict:
    """Extract all available metadata from an activity or match detail page."""
    detail: dict = {}

    # ---- Time (start + end) ----
    # Training pages: h2 like "Onsdag 22 apr, 20:00-21:30"
    for h2 in soup.find_all("h2"):
        h2_text = h2.get_text(strip=True)
        m = re.search(r'(\d{1,2}:\d{2})-(\d{1,2}:\d{2})', h2_text)
        if m:
            detail["startTime"] = m.group(1)
            detail["endTime"] = m.group(2)
            break
        m_start = re.search(r'(\d{1,2}:\d{2})', h2_text)
        if m_start:
            detail["startTime"] = m_start.group(1)
            break

    # Match pages: h1.hColor like "25 apr, 15:00" (end time not in HTML for matches)
    if "startTime" not in detail:
        h1 = soup.find("h1", class_="hColor")
        if h1:
            m = re.search(r'(\d{1,2}:\d{2})', h1.get_text(strip=True))
            if m:
                detail["startTime"] = m.group(1)

    # ---- Location / Plan ----
    # Training pages: h2 after the time h2 (e.g. "Stavsborgs BP")
    location = None
    time_h2_found = False
    for h2 in soup.find_all("h2"):
        h2_text = h2.get_text(strip=True)
        if re.search(r'\d{1,2}:\d{2}', h2_text):
            time_h2_found = True
            continue
        if time_h2_found and h2_text and not re.search(r'fotboll|hockey|basket|handboll', h2_text, re.I):
            location = h2_text
            break

    # Match pages: <p class="text-muted"> sibling of h1.hColor
    if not location:
        h1 = soup.find("h1", class_="hColor")
        if h1:
            for sib in h1.next_siblings:
                if hasattr(sib, "name") and sib.name == "p" and "text-muted" in sib.get("class", []):
                    txt = sib.get_text(strip=True)
                    if txt and not re.search(r'facebook|twitter|share', txt, re.I):
                        location = txt
                        break

    if location:
        detail["location"] = location

    # ---- Trainers / Coaches ----
    # In the attendance table, tbodies have a role span: "| Lagledare/Tränare", "| Tränare", etc.
    trainer_roles = re.compile(r'lagledare|tränare|coach|ass\.?\s*tränare', re.I)
    trainers = []
    for tbody in soup.find_all("tbody", attrs={"data-memberid": True}):
        role_span = tbody.find("span", class_="text-muted",
                               string=lambda t: t and trainer_roles.search(t))
        if role_span:
            name_el = tbody.find("b")
            if name_el:
                raw_role = role_span.get_text(strip=True).lstrip("|").strip()
                trainers.append({"name": name_el.get_text(strip=True), "role": raw_role})
    if trainers:
        detail["trainers"] = trainers

    # ---- Meeting point / Samling ----
    # Training: <b class="grey">Samling: 19:50</b>
    samling_el = soup.find("b", class_="grey")
    if samling_el:
        m = re.match(r'Samling:\s*(.+)', samling_el.get_text(strip=True), re.I)
        if m:
            detail["meetingPoint"] = m.group(1).strip()

    # Match: bare <div> containing "Samling\nHH:MM"
    if "meetingPoint" not in detail:
        for div in soup.find_all("div"):
            txt = div.get_text(" ", strip=True)
            m = re.match(r'Samling\s+(\d{1,2}:\d{2})$', txt)
            if m:
                detail["meetingPoint"] = m.group(1)
                break

    # ---- Attendee counts ----
    for btn in soup.select("#attendanceList .btn"):
        btn_text = btn.get_text(" ", strip=True)
        m = re.search(r'(\d+(?:\+\d+)?)', btn_text)
        if not m:
            continue
        if "Kommer" in btn_text:
            detail["attendingCount"] = m.group(1)
        elif "Kan ej" in btn_text:
            detail["notAttendingCount"] = m.group(1)
        elif "Kallade" in btn_text:
            detail["invitedCount"] = m.group(1)

    invited_text = soup.find(string=re.compile(r'\d+ personer är kallade', re.I))
    if invited_text:
        m = re.search(r'(\d+)', invited_text)
        if m:
            detail["totalInvited"] = int(m.group(1))

    # ---- Match-specific: opponent, home/away, result ----
    # Opponent from page title: "Älta IF - Reymersholms IK 1"
    title_el = soup.find("title")
    if title_el:
        title = title_el.get_text(strip=True)
        # Format is usually "Team A - Team B"
        parts = re.split(r'\s+-\s+', title)
        if len(parts) == 2:
            detail["opponent"] = parts[1].strip()

    home_away_el = soup.find(string=re.compile(r'\bHemma\b|\bBorta\b', re.I))
    if home_away_el:
        txt = home_away_el.strip()
        if re.search(r'\bHemma\b', txt, re.I):
            detail["homeAway"] = "hemma"
        elif re.search(r'\bBorta\b', txt, re.I):
            detail["homeAway"] = "borta"

    score_el = soup.find(string=re.compile(r'^\s*\d+\s*[-–]\s*\d+\s*$'))
    if score_el:
        detail["result"] = score_el.strip()

    # ---- Description / info ----
    for selector in ("div.activity-description", "div.schedule-description",
                     "div.cp-description", "div.activity-info", ".ingress"):
        el = soup.select_one(selector)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                detail["description"] = text
            break

    return detail


def _fetch_activity_details(session: requests.Session, activity: dict) -> tuple[str, dict]:
    """Fetch detail page for one activity. Returns (href_key, detail_dict)."""
    href = activity.get("href", "")
    if not href:
        return href, {}
    href_key = href.split("?")[0]
    try:
        member_id = activity.get("memberId")
        params = {"memberid": member_id} if member_id else {}
        resp = session.get(f"{BASE_URL}{href_key}", params=params, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        detail = _parse_activity_detail(soup)
        # Extract the iCal calendar ID (teamId) embedded in the page JS
        for script in soup.find_all("script"):
            m = re.search(r'var\s+teamId\s*=\s*(\d+)', script.string or "")
            if m:
                detail["_calendarId"] = m.group(1)
                break
        return href_key, detail
    except Exception:
        return href_key, {}


_SWEDISH_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def _date_str_to_ymd(date_str: str) -> str:
    """Convert 'Lör 13 jun' → 'YYYYMMDD', trying current and next year."""
    m = re.search(r'(\d{1,2})\s+(\w{3})', date_str.lower())
    if not m:
        return ""
    day = int(m.group(1))
    month = _SWEDISH_MONTHS.get(m.group(2)[:3])
    if not month:
        return ""
    today = datetime.date.today()
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            d = datetime.date(year, month, day)
            # Prefer the date closest to today
            if abs((d - today).days) < 400:
                return d.strftime("%Y%m%d")
        except ValueError:
            pass
    return ""


def _fetch_ical_data(calendar_id: str) -> tuple[dict[int, dict], dict[str, dict]]:
    """Fetch public iCal feed. Returns:
      by_id:       {scheduleId: {endTime, location, scheduleId}}
      by_datetime: {'YYYYMMDD_HHMM': {endTime, location, scheduleId}}
    """
    try:
        resp = requests.get(
            f"https://cal.svenskalag.se/{calendar_id}",
            headers={"User-Agent": "Mozilla/5.0 svlag-cli/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        by_id: dict[int, dict] = {}
        by_datetime: dict[str, dict] = {}

        for event_text in re.split(r'BEGIN:VEVENT', resp.text)[1:]:
            uid_m  = re.search(r'UID:cal(\d+)-\d+@', event_text)
            start_m = re.search(r'DTSTART[^:]*:(\d{8})T(\d{4})', event_text)
            end_m  = re.search(r'DTEND[^:]*:(\d{8})T(\d{4})', event_text)
            loc_m  = re.search(r'LOCATION:(.+)', event_text)

            if not (start_m and end_m):
                continue

            schedule_id = int(uid_m.group(1)) if uid_m else None
            end_time = f"{end_m.group(2)[:2]}:{end_m.group(2)[2:]}"
            location = loc_m.group(1).strip() if loc_m else ""
            entry = {"endTime": end_time, "location": location, "scheduleId": schedule_id}

            if schedule_id:
                by_id[schedule_id] = entry

            dt_key = f"{start_m.group(1)}_{start_m.group(2)}"  # e.g. "20260613_0900"
            by_datetime[dt_key] = entry

        return by_id, by_datetime
    except Exception:
        return {}, {}


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

    # Fetch detail pages in parallel for all activities that have a URL
    fetchable = [a for a in activities if a.get("href")]
    details_by_href: dict[str, dict] = {}
    if fetchable:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_activity_details, session, a): a for a in fetchable}
            for fut in as_completed(futures):
                href_key, detail = fut.result()
                if href_key:
                    details_by_href[href_key] = detail

    # Collect calendar IDs from detail pages, then fetch iCal feeds
    calendar_ids = {
        d["_calendarId"]
        for d in details_by_href.values()
        if d.get("_calendarId")
    }
    ical_by_id: dict[int, dict] = {}
    ical_by_dt: dict[str, dict] = {}
    for cal_id in calendar_ids:
        by_id, by_dt = _fetch_ical_data(cal_id)
        ical_by_id.update(by_id)
        ical_by_dt.update(by_dt)

    # Merge detail data + iCal into each activity
    for a in activities:
        key = a["href"].split("?")[0] if a.get("href") else ""
        detail = details_by_href.get(key, {})

        if detail.get("endTime"):
            a["endTime"] = detail["endTime"]
        if detail.get("startTime") and not a["startTime"]:
            a["startTime"] = detail["startTime"]

        # Resolve iCal entry: first by scheduleId, then by date+time
        ical = {}
        if a.get("scheduleId") and a["scheduleId"] in ical_by_id:
            ical = ical_by_id[a["scheduleId"]]
        elif not a["endTime"]:
            ymd = _date_str_to_ymd(a["date"])
            hhmm = (a["startTime"] or "").replace(":", "")
            if ymd and hhmm:
                ical = ical_by_dt.get(f"{ymd}_{hhmm}", {})

        if not a["endTime"] and ical.get("endTime"):
            a["endTime"] = ical["endTime"]
        # Fill scheduleId from iCal for linkless activities
        if not a["scheduleId"] and ical.get("scheduleId"):
            a["scheduleId"] = ical["scheduleId"]

        a["location"] = detail.get("location") or ical.get("location", "")
        a["trainers"] = detail.get("trainers", [])
        a["meetingPoint"] = detail.get("meetingPoint", "")
        a["attendingCount"] = detail.get("attendingCount", "")
        a["notAttendingCount"] = detail.get("notAttendingCount", "")
        a["invitedCount"] = detail.get("invitedCount", "")
        a["totalInvited"] = detail.get("totalInvited", None)

    if as_json:
        output = [
            {
                "scheduleId": a["scheduleId"],
                "date": a["date"],
                "time": a["time"],
                "startTime": a["startTime"],
                "endTime": a["endTime"],
                "name": a["name"],
                "type": a["type"],
                "team": a["team"],
                "teamSlug": a["teamSlug"],
                "location": a["location"],
                "trainers": a["trainers"],
                "meetingPoint": a["meetingPoint"],
                "attendingCount": a["attendingCount"],
                "notAttendingCount": a["notAttendingCount"],
                "invitedCount": a["invitedCount"],
                "totalInvited": a["totalInvited"],
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
    table.add_column("Datum", width=18)
    table.add_column("Start", width=7)
    table.add_column("Slut", width=7)
    table.add_column("Typ", width=9)
    table.add_column("Aktivitet", min_width=18)
    table.add_column("Plan", min_width=14)
    table.add_column("Lag", min_width=18)
    table.add_column("Svar senast", width=16)
    table.add_column("Svar", width=12)

    for a in activities:
        sid = str(a["scheduleId"]) if a["scheduleId"] else "–"
        start = a["startTime"] or "–"
        end = a["endTime"] or "–"
        typ = a["type"]
        if typ == "match":
            typ_text = "[bold magenta]Match[/bold magenta]"
        elif typ == "träning":
            typ_text = "[cyan]Träning[/cyan]"
        else:
            typ_text = "[dim]Övrigt[/dim]"
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

        location = a.get("location") or "–"
        table.add_row(sid, a["date"], start, end, typ_text, a["name"], location, a["team"], a["deadline"], status_text)

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
    activity_url = f"{BASE_URL}{target['href'].split('?')[0]}"
    resp2 = session.get(activity_url, params={"memberid": member_id} if member_id else {})
    resp2.raise_for_status()
    soup = BeautifulSoup(resp2.text, "html.parser")

    extra = _parse_activity_detail(soup)
    # Extract calendarId embedded in page JS for iCal lookup
    calendar_id = None
    for script in soup.find_all("script"):
        m = re.search(r'var\s+teamId\s*=\s*(\d+)', script.string or "")
        if m:
            calendar_id = m.group(1)
            break
    extra.pop("_calendarId", None)

    start_time = extra.pop("startTime", None) or target["startTime"]
    end_time = extra.pop("endTime", None) or target["endTime"]

    # Fall back to iCal if end time still missing
    if not end_time and calendar_id and target.get("scheduleId"):
        ical_by_id, _ = _fetch_ical_data(calendar_id)
        end_time = ical_by_id.get(target["scheduleId"], {}).get("endTime", "")

    # Parse player attendance (exclude trainers)
    trainer_roles = re.compile(r'lagledare|tränare|coach|ass\.?\s*tränare', re.I)
    attendance_table = soup.find("table", class_=re.compile("content-block-table"))
    yes_list, no_list, unanswered_list = [], [], []
    if attendance_table:
        for tbody in attendance_table.find_all("tbody", attrs={"data-memberid": True}):
            # Skip trainers/coaches
            role_span = tbody.find("span", class_="text-muted",
                                   string=lambda t: t and trainer_roles.search(t))
            if role_span:
                continue
            name_cell = tbody.find("b")
            name = name_cell.get_text(strip=True) if name_cell else "?"
            icon = tbody.find("i", class_=re.compile("fa-"))
            if icon:
                classes = " ".join(icon.get("class", []))
                if "check" in classes:
                    yes_list.append(name)
                elif "times" in classes or "close" in classes:
                    comment_el = tbody.find(class_=re.compile("comment|anledning"))
                    comment = comment_el.get_text(strip=True) if comment_el else ""
                    no_list.append({"name": name, "comment": comment})
                else:
                    unanswered_list.append(name)

    if as_json:
        print(json.dumps({
            "scheduleId": aktivitet_id,
            "name": target["name"],
            "type": target["type"],
            "date": target["date"],
            "time": target["time"],
            "startTime": start_time,
            "endTime": end_time,
            "team": target["team"],
            "teamSlug": target["teamSlug"],
            "deadline": target["deadline"],
            "status": target["status"],
            **extra,
            "attending": yes_list,
            "notAttending": no_list,
            "unanswered": unanswered_list,
        }, ensure_ascii=False))
        return

    time_str = start_time + (f"–{end_time}" if end_time else "")
    console.print(f"\n[bold]{target['name']}[/bold] – {target['date']} {time_str}")
    console.print(f"Lag: {target['team']}  |  Typ: {target['type']}")
    if extra.get("location"):
        console.print(f"Plan: {extra['location']}")
    if extra.get("trainers"):
        for t in extra["trainers"]:
            console.print(f"{t['role']}: {t['name']}")
    if extra.get("opponent"):
        console.print(f"Motståndare: {extra['opponent']}" +
                      (f"  ({extra['homeAway']})" if extra.get("homeAway") else ""))
    if extra.get("result"):
        console.print(f"Resultat: {extra['result']}")
    if extra.get("meetingPoint"):
        console.print(f"Samling: {extra['meetingPoint']}")
    if extra.get("description"):
        console.print(f"\n[dim]{extra['description']}[/dim]")
    console.print()

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
        for p in no_list:
            console.print(f"  ✗ {p['name']}" + (f" – [dim]{p['comment']}[/dim]" if p["comment"] else ""))
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
