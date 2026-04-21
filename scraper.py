"""
Oris Dental – Availability Feed Generator
==========================================
Én feed. Alt automatisk.

Per klinikk-side scrapes:
  - Behandler-bilder
  - Åpningstider (brukes til custom_label_akutt)
  - Behandlingsliste (brukes til product_category)

Koordinater og radius hentes fra hardkodet CLINIC_GEO dict
basert på klinikk-slug.
"""

import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
import re
import os
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import unquote, urlparse, parse_qs
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ── Konfigurasjon ──────────────────────────────────────────────────────────────

DAYS_AHEAD   = 14
OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), "docs")
OUTPUT_FILE  = os.path.join(OUTPUT_DIR, "feed.xml")
BOOKING_BASE = "https://booking.orisdental.no"
CLINIC_BASE  = "https://orisdental.no/klinikker"
OSLO_TZ      = ZoneInfo("Europe/Oslo")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en-US;q=0.6",
    "Content-Type": "application/json",
    "Origin": "https://booking.orisdental.no",
    "Referer": "https://booking.orisdental.no/",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Platform": '"macOS"',
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# ── Koordinater og radius per klinikk (slug → geo-data) ───────────────────────
# Kilde: eksisterende Meta-kampanje sheet
CLINIC_GEO = {
    "aker-brygge":                    {"lat": 59.9103,  "lng": 10.7259,  "radius": 2,  "unit": "km"},
    "arendal-spesialistklinikk":      {"lat": 58.4612,  "lng": 8.7663,   "radius": 15, "unit": "km"},
    "arken-asane":                    {"lat": 60.4654,  "lng": 5.3235,   "radius": 5,  "unit": "km"},
    "arken-asane-tannregulering":     {"lat": 60.4654,  "lng": 5.3235,   "radius": 5,  "unit": "km"},
    "askoy":                          {"lat": 60.4087,  "lng": 5.2263,   "radius": 8,  "unit": "km"},
    "bodo":                           {"lat": 67.2831,  "lng": 14.3795,  "radius": 50, "unit": "km"},
    "bossekop":                       {"lat": 69.9664,  "lng": 23.2575,  "radius": 60, "unit": "km"},
    "broegelmannhuset":               {"lat": 60.3938,  "lng": 5.3236,   "radius": 2,  "unit": "km"},
    "brosundet":                      {"lat": 62.4722,  "lng": 6.1528,   "radius": 5,  "unit": "km"},
    "bryn":                           {"lat": 59.907,   "lng": 10.822,   "radius": 4,  "unit": "km"},
    "bryne":                          {"lat": 58.7356,  "lng": 5.6476,   "radius": 10, "unit": "km"},
    "bryne-tannregulering":           {"lat": 58.7356,  "lng": 5.6476,   "radius": 10, "unit": "km"},
    "drammen":                        {"lat": 59.7451,  "lng": 10.2062,  "radius": 5,  "unit": "km"},
    "drammen-avd-landfalloya":        {"lat": 59.7533,  "lng": 10.18,    "radius": 5,  "unit": "km"},
    "egersund":                       {"lat": 58.4526,  "lng": 6.0028,   "radius": 30, "unit": "km"},
    "eikas":                          {"lat": 61.9056,  "lng": 5.9902,   "radius": 40, "unit": "km"},
    "farmannsgate":                   {"lat": 59.2133,  "lng": 10.9388,  "radius": 5,  "unit": "km"},
    "fredrikstad":                    {"lat": 59.21,    "lng": 10.93,    "radius": 5,  "unit": "km"},
    "galleri-oslo":                   {"lat": 59.911,   "lng": 10.758,   "radius": 1,  "unit": "km"},
    "godtanna-gol":                   {"lat": 60.702,   "lng": 8.9485,   "radius": 40, "unit": "km"},
    "gressvik":                       {"lat": 59.225,   "lng": 10.89,    "radius": 5,  "unit": "km"},
    "gronnegata-tannlegesenter":      {"lat": 69.6517,  "lng": 18.9587,  "radius": 30, "unit": "km"},
    "hamar":                          {"lat": 60.7952,  "lng": 11.0694,  "radius": 20, "unit": "km"},
    "harstad-torv":                   {"lat": 68.8005,  "lng": 16.5413,  "radius": 5,  "unit": "km"},
    "harstad-ved-havet":              {"lat": 68.799,   "lng": 16.55,    "radius": 5,  "unit": "km"},
    "harstadtannlegene":              {"lat": 68.801,   "lng": 16.542,   "radius": 5,  "unit": "km"},
    "haugesund":                      {"lat": 59.4079,  "lng": 5.2753,   "radius": 20, "unit": "km"},
    "hinna-park":                     {"lat": 58.916,   "lng": 5.736,    "radius": 5,  "unit": "km"},
    "hokksund-tannregulering":        {"lat": 59.7708,  "lng": 9.9079,   "radius": 15, "unit": "km"},
    "homansbyen":                     {"lat": 59.923,   "lng": 10.728,   "radius": 2,  "unit": "km"},
    "hvalertannlegene":               {"lat": 59.1023,  "lng": 10.9105,  "radius": 15, "unit": "km"},
    "indre-arna":                     {"lat": 60.4224,  "lng": 5.4678,   "radius": 5,  "unit": "km"},
    "jorpeland":                      {"lat": 59.0142,  "lng": 6.0463,   "radius": 25, "unit": "km"},
    "knapstad":                       {"lat": 59.6179,  "lng": 11.0425,  "radius": 15, "unit": "km"},
    "kolsas":                         {"lat": 59.916,   "lng": 10.518,   "radius": 5,  "unit": "km"},
    "kristiansand":                   {"lat": 58.1461,  "lng": 7.9961,   "radius": 5,  "unit": "km"},
    "leutenhaven":                    {"lat": 63.43,    "lng": 10.39,    "radius": 2,  "unit": "km"},
    "levanger":                       {"lat": 63.7461,  "lng": 11.2982,  "radius": 25, "unit": "km"},
    "lillehammer":                    {"lat": 61.115,   "lng": 10.4672,  "radius": 30, "unit": "km"},
    "lykkegarden-tidligere-trondheim-torg": {"lat": 63.431, "lng": 10.395, "radius": 2, "unit": "km"},
    "lysaker":                        {"lat": 59.913,   "lng": 10.6409,  "radius": 3,  "unit": "km"},
    "lokketangen":                    {"lat": 59.892,   "lng": 10.525,   "radius": 3,  "unit": "km"},
    "lokkeveien":                     {"lat": 58.9696,  "lng": 5.7289,   "radius": 3,  "unit": "km"},
    "madla":                          {"lat": 58.955,   "lng": 5.69,     "radius": 4,  "unit": "km"},
    "martin-stage":                   {"lat": 58.955,   "lng": 5.692,    "radius": 4,  "unit": "km"},
    "melhus":                         {"lat": 63.2874,  "lng": 10.2783,  "radius": 15, "unit": "km"},
    "mo-i-rana":                      {"lat": 66.3134,  "lng": 14.1432,  "radius": 50, "unit": "km"},
    "moa":                            {"lat": 62.47,    "lng": 6.33,     "radius": 10, "unit": "km"},
    "moelv":                          {"lat": 60.9328,  "lng": 10.6974,  "radius": 15, "unit": "km"},
    "moss-spesialistklinikk":         {"lat": 59.4344,  "lng": 10.6582,  "radius": 15, "unit": "km"},
    "munkegata":                      {"lat": 63.431,   "lng": 10.394,   "radius": 2,  "unit": "km"},
    "maloy":                          {"lat": 61.9347,  "lng": 5.1141,   "radius": 40, "unit": "km"},
    "namsos-kjeveortopedi":           {"lat": 64.4657,  "lng": 11.4957,  "radius": 30, "unit": "km"},
    "narvik":                         {"lat": 68.4388,  "lng": 17.4278,  "radius": 40, "unit": "km"},
    "nationaltheatret-tannregulering":{"lat": 59.914,   "lng": 10.73,    "radius": 2,  "unit": "km"},
    "naerbo":                         {"lat": 58.6657,  "lng": 5.6358,   "radius": 15, "unit": "km"},
    "nesttun":                        {"lat": 60.317,   "lng": 5.35,     "radius": 5,  "unit": "km"},
    "nittedal":                       {"lat": 60.065,   "lng": 10.872,   "radius": 10, "unit": "km"},
    "oralkirurgisk-klinikk":          {"lat": 59.93,    "lng": 10.71,    "radius": 2,  "unit": "km"},
    "orkanger":                       {"lat": 63.3039,  "lng": 9.8519,   "radius": 20, "unit": "km"},
    "oyrane-torg":                    {"lat": 60.42,    "lng": 5.465,    "radius": 5,  "unit": "km"},
    "raufoss":                        {"lat": 60.7267,  "lng": 10.6033,  "radius": 15, "unit": "km"},
    "raufoss-sagvollvegen":           {"lat": 60.7267,  "lng": 10.6033,  "radius": 15, "unit": "km"},
    "rommen":                         {"lat": 59.96,    "lng": 10.905,   "radius": 4,  "unit": "km"},
    "ryen":                           {"lat": 59.896,   "lng": 10.805,   "radius": 3,  "unit": "km"},
    "sandane":                        {"lat": 61.7738,  "lng": 6.2163,   "radius": 30, "unit": "km"},
    "sanden":                         {"lat": 63.43,    "lng": 10.385,   "radius": 2,  "unit": "km"},
    "sandnes":                        {"lat": 58.8514,  "lng": 5.7368,   "radius": 5,  "unit": "km"},
    "sandsli-tannregulering":         {"lat": 60.29,    "lng": 5.28,     "radius": 5,  "unit": "km"},
    "sandvika-storsenter":            {"lat": 59.8913,  "lng": 10.5238,  "radius": 3,  "unit": "km"},
    "sarpsborg":                      {"lat": 59.284,   "lng": 11.1091,  "radius": 8,  "unit": "km"},
    "sirkus-shopping":                {"lat": 63.435,   "lng": 10.45,    "radius": 3,  "unit": "km"},
    "ski":                            {"lat": 59.72,    "lng": 10.835,   "radius": 5,  "unit": "km"},
    "skoyen":                         {"lat": 59.921,   "lng": 10.68,    "radius": 2,  "unit": "km"},
    "slemmestad":                     {"lat": 59.7821,  "lng": 10.4939,  "radius": 10, "unit": "km"},
    "sogne":                          {"lat": 58.0932,  "lng": 7.7834,   "radius": 15, "unit": "km"},
    "sortland":                       {"lat": 68.6958,  "lng": 15.4125,  "radius": 30, "unit": "km"},
    "stoa-i-arendal":                 {"lat": 58.46,    "lng": 8.7,      "radius": 10, "unit": "km"},
    "stokmarknes":                    {"lat": 68.5653,  "lng": 14.9126,  "radius": 30, "unit": "km"},
    "stord-heiane":                   {"lat": 59.77,    "lng": 5.49,     "radius": 10, "unit": "km"},
    "stord-leirvik":                  {"lat": 59.78,    "lng": 5.5,      "radius": 10, "unit": "km"},
    "strandgaten":                    {"lat": 60.394,   "lng": 5.324,    "radius": 2,  "unit": "km"},
    "tannteam-spesialistklinikk":     {"lat": 60.317,   "lng": 5.352,    "radius": 5,  "unit": "km"},
    "tarnplassen":                    {"lat": 60.392,   "lng": 5.323,    "radius": 2,  "unit": "km"},
    "tonsberg":                       {"lat": 59.2678,  "lng": 10.4072,  "radius": 15, "unit": "km"},
    "vagsbygd":                       {"lat": 58.12,    "lng": 7.96,     "radius": 5,  "unit": "km"},
    "vestkanten":                     {"lat": 60.38,    "lng": 5.23,     "radius": 5,  "unit": "km"},
    "voyenenga":                      {"lat": 59.9079,  "lng": 10.4851,  "radius": 5,  "unit": "km"},
    "al":                             {"lat": 60.6288,  "lng": 8.5606,   "radius": 40, "unit": "km"},
}

# ── Hjelpefunksjoner ───────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def name_match(api_name: str, web_name: str) -> bool:
    a = normalize_name(api_name)
    b = normalize_name(web_name)
    if a == b:
        return True
    a_last = a.split()[-1] if a.split() else a
    b_last = b.split()[-1] if b.split() else b
    return a_last == b_last and len(a_last) > 3


def format_oslo_time(iso_str: str) -> tuple[str, str, str]:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(OSLO_TZ)
    måneder = ["januar","februar","mars","april","mai","juni",
               "juli","august","september","oktober","november","desember"]
    ukedager = ["Mandag","Tirsdag","Onsdag","Torsdag","Fredag","Lørdag","Søndag"]
    return (
        f"{dt.day}. {måneder[dt.month - 1]}",
        dt.strftime("%H:%M"),
        ukedager[dt.weekday()],
    )


def compute_duration(slot: dict) -> int:
    t_from = datetime.fromisoformat(slot["time_from"].replace("Z", "+00:00"))
    t_to   = datetime.fromisoformat(slot["time_to"].replace("Z", "+00:00"))
    return int((t_to - t_from).total_seconds() / 60)


def extract_photo_url(img_tag) -> str:
    src = img_tag.get("src", "")
    if not src:
        return ""
    if "_next/image" in src:
        try:
            qs = parse_qs(urlparse(src).query)
            original = qs.get("url", [""])[0]
            if original:
                return unquote(original)
        except Exception:
            pass
    if src.startswith("http"):
        return src
    if src.startswith("/"):
        return f"https://orisdental.no{src}"
    return src


# ── Token-henting via Playwright ───────────────────────────────────────────────

def get_bearer_token() -> str | None:
    env_token = os.environ.get("ORIS_BEARER_TOKEN")
    if env_token:
        print("✓ Bearer-token fra environment variable")
        return env_token

    if not PLAYWRIGHT_AVAILABLE:
        print("⚠ Playwright ikke installert")
        return None

    print("→ Henter Bearer-token via Playwright...")
    token = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            page = browser.new_context(
                user_agent=HEADERS["User-Agent"], locale="nb-NO"
            ).new_page()

            def intercept(request):
                nonlocal token
                if token:
                    return
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer ") and "api.orisdental.no" in request.url:
                    token = auth.replace("Bearer ", "").strip()
                    print(f"✓ Token fanget (lengde: {len(token)})")

            page.on("request", intercept)
            page.goto("https://booking.orisdental.no/", wait_until="networkidle", timeout=30000)

            if not token:
                page.wait_for_timeout(3000)
            if not token:
                try:
                    page.wait_for_selector("button", timeout=5000)
                    page.query_selector_all("button")[0].click()
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            browser.close()

    except Exception as e:
        print(f"⚠ Playwright-feil: {e}")

    return token


def build_session(token: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


# ── API-kall ───────────────────────────────────────────────────────────────────

def fetch_clinics(session: requests.Session) -> list[dict]:
    print("→ Henter klinikker fra API...")
    try:
        resp = session.get("https://api.orisdental.no/api/clinicsandregions", timeout=15)
        resp.raise_for_status()
        clinics = [c for c in resp.json().get("clinics", []) if c.get("published")]
        print(f"✓ {len(clinics)} klinikker funnet")
        return clinics
    except Exception as e:
        print(f"✗ {e}")
        return []


def fetch_specialists(session: requests.Session, clinic_id: int, opus_id: str) -> dict[int, dict]:
    """
    Henter behandlere med navn ved å kalle services-endpointet med numerisk clinic_id.
    Dette er samme endpoint vi så i DevTools som returnerte specialists[] + timeslots[].
    Returnerer: {clinician_id: {name, profession}}
    """
    import time
    import calendar
    time.sleep(1)

    today   = datetime.now(OSLO_TZ)
    last_day = calendar.monthrange(today.year, today.month)[1]
    from_dt = today.strftime("%Y-%m-%dT00:00:00Z")
    to_dt   = today.strftime(f"%Y-%m-{last_day:02d}T23:59:59Z")

    # Prøv med numerisk clinic_id først (gir specialists + timeslots)
    try:
        resp = session.get(
            "https://api.orisdental.no/api/services",
            params={
                "clinic_id": clinic_id,
                "from_date": from_dt,
                "to_date":   to_dt,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "specialists" in data:
                return {s["id"]: s for s in data.get("specialists", [])}
    except Exception:
        pass

    return {}


def fetch_slots(session: requests.Session, opus_id: str) -> dict:
    """
    To-stegs API-kall:
    1. Hent behandlingstyper (services) for å få service_id og duration
    2. Hent timeslots via timeslotmonth for inneværende + neste måned
       - Returnerer specialists[] med navn + timeslots[]
    """
    import time
    import calendar
    time.sleep(2)

    today = datetime.now(OSLO_TZ)

    # Steg 1: Hent services for å få service_id og duration
    try:
        last_day = calendar.monthrange(today.year, today.month)[1]
        resp = session.get(
            "https://api.orisdental.no/api/services",
            params={
                "clinic_id": opus_id,
                "from_date": today.strftime("%Y-%m-%dT00:00:00Z"),
                "to_date":   today.strftime(f"%Y-%m-{last_day:02d}T23:59:59Z"),
            },
            timeout=15,
        )
        resp.raise_for_status()
        services = resp.json()
        if not services:
            return {}
    except Exception as e:
        print(f"  ⚠ Services-feil: {e}")
        return {}

    # Bruk første service
    service_id = services[0]["id"]
    duration   = services[0].get("duration", 30)

    # Steg 2: Hent timeslots via timeslotmonth for inneværende + neste måned
    all_timeslots  = []
    all_specialists = {}

    for month_offset in range(2):
        if month_offset == 0:
            year, month = today.year, today.month
        else:
            if today.month == 12:
                year, month = today.year + 1, 1
            else:
                year, month = today.year, today.month + 1

        try:
            time.sleep(1)
            resp = session.get(
                "https://api.orisdental.no/api/timeslotmonth",
                params={
                    "clinic_id":  opus_id,
                    "service_id": service_id,
                    "duration":   duration,
                    "year":       year,
                    "month":      month,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            slots = data.get("timeslots", [])
            specs = data.get("specialists", [])

            all_timeslots.extend(slots)
            for s in specs:
                all_specialists[s["id"]] = s

        except requests.exceptions.HTTPError as e:
            if e.response.status_code not in (404, 400):
                print(f"  ⚠ HTTP {e.response.status_code}")
        except Exception as e:
            print(f"  ⚠ Timeslots-feil: {e}")

    return {
        "specialists": list(all_specialists.values()),
        "timeslots":   all_timeslots,
    }


# ── Scraping av klinikk-side ───────────────────────────────────────────────────

def scrape_clinic_page(slug: str) -> dict:
    """
    Scraper én klinikk-side og returnerer:
    {
        photos:     {'Navn': 'https://...bilde...'},
        treatments: ['Tannundersøkelse', 'Tannrens', ...],
        is_akutt:   True/False  (lang åpningstid eller helgeåpent)
    }
    """
    url = f"{CLINIC_BASE}/{slug}"
    result = {"photos": {}, "treatments": [], "is_akutt": False}

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=15,
        )
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Behandler-bilder ──────────────────────────────────────────────────
        for link in soup.find_all("a", href=re.compile(r"/ansatte/")):
            name = link.get_text(strip=True)
            if not name or len(name) < 3:
                continue
            photo_url = ""
            for parent in link.parents:
                img = parent.find("img")
                if img:
                    photo_url = extract_photo_url(img)
                    break
            if name and photo_url:
                result["photos"][name] = photo_url

        # ── Behandlingsliste ──────────────────────────────────────────────────
        # Finn seksjonen "Behandlinger hos Oris Dental X"
        treatments = []
        for heading in soup.find_all(["h2", "h3"]):
            if "behandlinger hos" in heading.get_text(strip=True).lower():
                # Finn alle list-items etter denne headingen
                container = heading.find_next(["ul", "ol"])
                if container:
                    for li in container.find_all("li"):
                        text = li.get_text(strip=True)
                        if text:
                            treatments.append(text)
                break
        result["treatments"] = treatments

        # ── By-navn fra klinikksiden (korrigerer API-feil som "UIset") ──────────
        city = ""
        address_tag = soup.find("a", href=re.compile(r"google.com/maps"))
        if address_tag:
            # Adressen er i link-teksten: "Åsane Senter 37, Ulset"
            addr_text = address_tag.get_text(strip=True)
            if "," in addr_text:
                city = addr_text.split(",")[-1].strip()
                # Fjern postnummer hvis det er med
                city = re.sub(r"^\d{4}\s*", "", city).strip()
        result["city"] = city
        # Sjekk om noen dag har åpningstider etter 17:00 eller er åpen i helgen
        page_text = soup.get_text()

        # Lørdag eller søndag åpent
        helg_patterns = [
            r"[Ll]ørdag\s*\d{2}:\d{2}",
            r"[Ss]øndag\s*\d{2}:\d{2}",
        ]
        for pattern in helg_patterns:
            if re.search(pattern, page_text):
                result["is_akutt"] = True
                break

        # Åpent etter 17:00 (f.eks. 17:30, 18:00, 19:00, 20:00)
        if not result["is_akutt"]:
            late_hours = re.findall(r"(\d{2}):(\d{2})", page_text)
            for hour, minute in late_hours:
                if int(hour) >= 17 and int(minute) > 0 or int(hour) >= 18:
                    result["is_akutt"] = True
                    break

    except Exception as e:
        print(f"  ⚠ Scraping feilet for {slug}: {e}")

    return result


# ── XML-generering ─────────────────────────────────────────────────────────────

def build_feed(items: list[dict]) -> str:
    root = ET.Element("listings")
    root.set("xmlns:g", "http://base.google.com/ns/1.0")
    generated_at = datetime.now(OSLO_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    for item in items:
        listing = ET.SubElement(root, "listing")

        def add(tag: str, text):
            el = ET.SubElement(listing, tag)
            el.text = str(text) if text is not None else ""

        # Påkrevde felt
        add("id",                           item["id"])
        add("title",                        item["title"])
        add("description",                  item["description"])
        add("url",                          item["url"])
        add("availability",                 "in stock")

        # Behandler
        add("clinician_name",               item["clinician_name"])
        add("clinician_title",              item["clinician_title"])
        add("clinician_id",                 item["clinician_id"])

        # Tidspunkt
        add("appointment_date",             item["appointment_date"])
        add("appointment_time",             item["appointment_time"])
        add("appointment_weekday",          item["appointment_weekday"])
        add("appointment_duration_minutes", item["duration_minutes"])
        add("time_from_iso",                item["time_from_iso"])

        # Klinikk
        add("clinic_name",                  item["clinic_name"])
        add("clinic_address",               item["clinic_address"])
        add("clinic_city",                  item["clinic_city"])
        add("clinic_zip",                   item["clinic_zip"])
        add("clinic_phone",                 item["clinic_phone"])
        add("clinic_region",                item["clinic_region"])

        # Geo
        add("latitude",                     item["latitude"])
        add("longitude",                    item["longitude"])
        add("geo_radius_value",             item["radius_value"])
        add("geo_radius_unit",              item["radius_unit"])
        # Kombinert felt for plattformer som støtter det
        add("geo_coordinates",              f"{item['latitude']},{item['longitude']}")

        # Behandlinger og kategori
        add("product_category",             item["product_category"])

        # Custom labels
        add("custom_label_akutt",           item["custom_label_akutt"])

        # Meta
        add("feed_generated_at",            generated_at)

        # Bilde
        if item.get("photo_url"):
            photo_el = ET.SubElement(listing, "g:image_link")
            photo_el.text = item["photo_url"]

    raw = ET.tostring(root, encoding="unicode")
    reparsed = minidom.parseString(f'<?xml version="1.0" encoding="UTF-8"?>{raw}')
    return reparsed.toprettyxml(indent="  ", encoding=None).replace(
        '<?xml version="1.0" ?>', '<?xml version="1.0" encoding="UTF-8"?>'
    )


# ── Hoved-logikk ──────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  Oris Dental – Feed Generator")
    print(f"  {datetime.now(OSLO_TZ).strftime('%d.%m.%Y %H:%M')}")
    print("="*60 + "\n")

    token   = get_bearer_token()
    session = build_session(token)
    clinics = fetch_clinics(session)

    if not clinics:
        print("✗ Ingen klinikker. Avslutter.")
        return

    now       = datetime.now(timezone.utc)
    all_items = []

    for clinic in clinics:
        clinic_id   = clinic["id"]
        clinic_name = clinic["name"]
        clinic_slug = clinic["slug"]

        print(f"\n→ [{clinic_name}]")

        # Hent geo-data
        geo = CLINIC_GEO.get(clinic_slug, {})
        if not geo:
            print(f"  ⚠ Ingen geo-data for slug '{clinic_slug}' – hopper over")
            continue

        # Scrape klinikk-siden (bilder + behandlinger + akutt)
        page_data = scrape_clinic_page(clinic_slug)

        photos     = page_data["photos"]
        treatments = page_data["treatments"]
        is_akutt   = page_data["is_akutt"]
        scraped_city = page_data.get("city", "")

        product_category   = ", ".join(treatments) if treatments else ""
        custom_label_akutt = "akutt" if is_akutt else ""

        if photos:
            print(f"  ✓ {len(photos)} bilder hentet")
        if treatments:
            print(f"  ✓ {len(treatments)} behandlingstyper hentet")
        if is_akutt:
            print(f"  ⚡ Merket som akutt (lang åpningstid / helgeåpent)")

        # Hent ledige timer fra API — krever opus_id
        opus_id = clinic.get("opus_id")
        if not opus_id:
            print(f"  ⚠ Ingen opus_id – hopper over")
            continue

        data = fetch_slots(session, opus_id)
        if not data:
            continue

        # Specialists og timeslots kommer nå fra timeslotmonth
        specialists = {s["id"]: s for s in data.get("specialists", [])}
        timeslots   = data.get("timeslots", [])

        if not timeslots:
            print(f"  Ingen ledige timer")
            continue

        region = ""
        if clinic.get("category_slugs"):
            region = clinic["category_slugs"][0].replace("-", " ").title()

        seen_clinicians = set()

        for slot in sorted(timeslots, key=lambda s: s["time_from"]):
            clinician_id = slot.get("clinician_id")
            if clinician_id in seen_clinicians:
                continue

            slot_time = datetime.fromisoformat(slot["time_from"].replace("Z", "+00:00"))
            if slot_time <= now:
                continue

            seen_clinicians.add(clinician_id)

            specialist      = specialists.get(clinician_id, {})
            clinician_name  = specialist.get("name", "Tilgjengelig behandler")
            clinician_title = specialist.get("profession", "Tannlege")

            # Match bilde på navn
            photo_url = ""
            for web_name, web_photo in photos.items():
                if name_match(clinician_name, web_name):
                    photo_url = web_photo
                    break

            dato, klokkeslett, ukedag = format_oslo_time(slot["time_from"])
            duration = compute_duration(slot)

            all_items.append({
                "id":                   f"oris-{clinic_slug}-{clinician_id}",
                "title":                f"Ledig time – {clinic_name} – {ukedag} {dato} kl. {klokkeslett}",
                "description":          (
                    f"Book time hos {clinician_name} ({clinician_title}) "
                    f"ved Oris Dental {clinic_name}. "
                    f"Første ledige time: {ukedag} {dato} kl. {klokkeslett}. "
                    f"Varighet: {duration} min."
                ),
                "url":                  f"{BOOKING_BASE}/?clinic={clinic_slug}",
                "clinician_name":       clinician_name,
                "clinician_title":      clinician_title,
                "clinician_id":         str(clinician_id),
                "appointment_date":     dato,
                "appointment_time":     klokkeslett,
                "appointment_weekday":  ukedag,
                "duration_minutes":     str(duration),
                "time_from_iso":        slot["time_from"],
                "clinic_name":          clinic_name,
                "clinic_address":       clinic.get("address", ""),
                "clinic_city":          scraped_city or clinic.get("city", ""),
                "clinic_zip":           clinic.get("zip", ""),
                "clinic_phone":         clinic.get("phone", ""),
                "clinic_region":        region,
                "latitude":             str(geo["lat"]),
                "longitude":            str(geo["lng"]),
                "radius_value":         str(geo["radius"]),
                "radius_unit":          geo["unit"],
                "product_category":     product_category,
                "custom_label_akutt":   custom_label_akutt,
                "photo_url":            photo_url,
            })

            photo_icon = "📷" if photo_url else "  "
            print(f"  {photo_icon} {clinician_name} ({clinician_title}) – {ukedag} {dato} kl. {klokkeslett}")

    print(f"\n{'='*60}")
    print(f"  Totalt: {len(all_items)} items generert")
    print(f"{'='*60}\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_feed(all_items))

    print(f"✓ Feed skrevet til: {OUTPUT_FILE}\n")


if __name__ == "__main__":
    main()
