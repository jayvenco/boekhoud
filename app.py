import os
import re
import sqlite3
import uuid
import base64
import logging
from datetime import datetime, date
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import bleach

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── App configuratie ──────────────────────────────────────────────────────────
app = Flask(__name__)

# Secret key vanuit omgevingsvariabele (verplicht in productie)
secret_key = os.environ.get("SECRET_KEY", "")
if not secret_key or secret_key == "vervang_dit_met_een_lange_willekeurige_string":
    import secrets
    secret_key = secrets.token_hex(32)
    logger.warning("SECRET_KEY niet ingesteld — tijdelijke sleutel gegenereerd. Stel SECRET_KEY in voor productie.")

app.secret_key = secret_key

# Sessie-veiligheid
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,   # intern netwerk, geen HTTPS vereist
    PERMANENT_SESSION_LIFETIME=3600,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # max 10 MB upload
)

# Mappen
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_BASE = BASE_DIR / "uploads"
DB_PATH = DATA_DIR / "boekhouding.db"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_BASE.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
ALLOWED_CATEGORIES = ["Kantoor", "Marketing", "Transport", "Investering", "Overig"]

for cat in ALLOWED_CATEGORIES:
    (UPLOAD_BASE / cat).mkdir(exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS gebruikers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gebruikersnaam TEXT UNIQUE NOT NULL,
            wachtwoord_hash TEXT NOT NULL,
            aangemaakt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transacties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datum DATE NOT NULL,
            factuurnummer TEXT,
            omschrijving TEXT NOT NULL,
            bedrag REAL NOT NULL CHECK(bedrag >= 0),
            type TEXT NOT NULL CHECK(type IN ('inkomst','uitgave')),
            categorie TEXT NOT NULL,
            bestand_pad TEXT,
            aangemaakt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS activa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL,
            aanschafdatum DATE NOT NULL,
            aanschafwaarde REAL NOT NULL CHECK(aanschafwaarde >= 0),
            restwaarde REAL NOT NULL CHECK(restwaarde >= 0),
            levensduur_jaren INTEGER NOT NULL CHECK(levensduur_jaren > 0),
            aangemaakt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Maak standaard admin-gebruiker aan als die nog niet bestaat
    existing = db.execute(
        "SELECT id FROM gebruikers WHERE gebruikersnaam = ?", ("admin",)
    ).fetchone()
    if not existing:
        pw_hash = generate_password_hash("admin123")
        db.execute(
            "INSERT INTO gebruikers (gebruikersnaam, wachtwoord_hash) VALUES (?, ?)",
            ("admin", pw_hash)
        )
    db.commit()

# ── Hulpfuncties ──────────────────────────────────────────────────────────────
def login_vereist(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "gebruiker_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def sanitize(text: str, max_length: int = 500) -> str:
    """Strip HTML en beperk lengte."""
    return bleach.clean(str(text), tags=[], strip=True)[:max_length]

def validate_bedrag(value) -> float:
    try:
        amount = float(str(value).replace(",", "."))
        if amount < 0 or amount > 1_000_000_000:
            raise ValueError
        return round(amount, 2)
    except (ValueError, TypeError):
        raise ValueError("Ongeldig bedrag")

def validate_datum(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise ValueError("Ongeldige datum")

def validate_categorie(cat: str) -> str:
    if cat not in ALLOWED_CATEGORIES:
        raise ValueError("Ongeldige categorie")
    return cat

# ── Routes: Auth ──────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if "gebruiker_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        gebruikersnaam = sanitize(request.form.get("gebruikersnaam", ""), 100)
        wachtwoord = request.form.get("wachtwoord", "")

        if not gebruikersnaam or not wachtwoord:
            flash("Vul gebruikersnaam en wachtwoord in.", "error")
            return render_template("login.html")

        db = get_db()
        gebruiker = db.execute(
            "SELECT id, wachtwoord_hash FROM gebruikers WHERE gebruikersnaam = ?",
            (gebruikersnaam,)
        ).fetchone()

        # Constante-tijd vergelijking (voorkomt timing-aanvallen)
        if gebruiker and check_password_hash(gebruiker["wachtwoord_hash"], wachtwoord):
            session.clear()
            session["gebruiker_id"] = gebruiker["id"]
            session["gebruikersnaam"] = gebruikersnaam
            session.permanent = True
            logger.info("Ingelogd: %s", gebruikersnaam)
            return redirect(url_for("dashboard"))
        else:
            flash("Ongeldige inloggegevens.", "error")
            logger.warning("Mislukte inlogpoging voor: %s", gebruikersnaam)

    return render_template("login.html")

@app.route("/logout")
@login_vereist
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Routes: Dashboard ─────────────────────────────────────────────────────────
@app.route("/")
@login_vereist
def dashboard():
    db = get_db()

    inkomsten = db.execute(
        "SELECT COALESCE(SUM(bedrag),0) as totaal FROM transacties WHERE type='inkomst'"
    ).fetchone()["totaal"]

    uitgaven = db.execute(
        "SELECT COALESCE(SUM(bedrag),0) as totaal FROM transacties WHERE type='uitgave'"
    ).fetchone()["totaal"]

    activa_rows = db.execute("SELECT * FROM activa ORDER BY aanschafdatum DESC").fetchall()
    totaal_afschrijving = 0.0
    totaal_restwaarde = 0.0
    for a in activa_rows:
        jaren_gebruikt = (date.today() - datetime.strptime(a["aanschafdatum"], "%Y-%m-%d").date()).days / 365.25
        jaren_gebruikt = min(jaren_gebruikt, a["levensduur_jaren"])
        jaarlijks = (a["aanschafwaarde"] - a["restwaarde"]) / a["levensduur_jaren"]
        afgeschreven = round(min(jaarlijks * jaren_gebruikt, a["aanschafwaarde"] - a["restwaarde"]), 2)
        totaal_afschrijving += afgeschreven
        totaal_restwaarde += round(a["aanschafwaarde"] - afgeschreven, 2)

    recente = db.execute(
        "SELECT * FROM transacties ORDER BY datum DESC, id DESC LIMIT 5"
    ).fetchall()

    return render_template("index.html",
        pagina="dashboard",
        inkomsten=inkomsten,
        uitgaven=uitgaven,
        winst=inkomsten - uitgaven,
        totaal_afschrijving=round(totaal_afschrijving, 2),
        totaal_restwaarde=round(totaal_restwaarde, 2),
        recente_transacties=recente,
        gebruikersnaam=session.get("gebruikersnaam")
    )

# ── Routes: Transacties ───────────────────────────────────────────────────────
@app.route("/transacties")
@login_vereist
def transacties():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM transacties ORDER BY datum DESC, id DESC"
    ).fetchall()
    return render_template("index.html", pagina="transacties",
                           transacties=rows, categorieen=ALLOWED_CATEGORIES,
                           gebruikersnaam=session.get("gebruikersnaam"))

@app.route("/transactie/toevoegen", methods=["POST"])
@login_vereist
def transactie_toevoegen():
    try:
        datum = validate_datum(request.form.get("datum", ""))
        factuurnummer = sanitize(request.form.get("factuurnummer", ""), 50)
        omschrijving = sanitize(request.form.get("omschrijving", ""), 500)
        bedrag = validate_bedrag(request.form.get("bedrag", "0"))
        transactie_type = request.form.get("type", "")
        categorie = validate_categorie(request.form.get("categorie", ""))

        if transactie_type not in ("inkomst", "uitgave"):
            flash("Ongeldig transactietype.", "error")
            return redirect(url_for("transacties"))

        if not omschrijving:
            flash("Omschrijving is verplicht.", "error")
            return redirect(url_for("transacties"))

        # Bestand upload
        bestand_pad = None
        if "bon" in request.files:
            bestand = request.files["bon"]
            if bestand and bestand.filename and allowed_file(bestand.filename):
                ext = bestand.filename.rsplit(".", 1)[1].lower()
                veilige_naam = f"{uuid.uuid4().hex}.{ext}"
                doel = UPLOAD_BASE / categorie / veilige_naam
                bestand.save(str(doel))
                bestand_pad = f"{categorie}/{veilige_naam}"

        db = get_db()
        db.execute(
            """INSERT INTO transacties
               (datum, factuurnummer, omschrijving, bedrag, type, categorie, bestand_pad)
               VALUES (?,?,?,?,?,?,?)""",
            (datum, factuurnummer, omschrijving, bedrag, transactie_type, categorie, bestand_pad)
        )
        db.commit()
        flash("Transactie toegevoegd.", "success")

    except ValueError as e:
        flash(f"Invoerfout: {e}", "error")

    return redirect(url_for("transacties"))

@app.route("/transactie/verwijderen/<int:tid>", methods=["POST"])
@login_vereist
def transactie_verwijderen(tid):
    db = get_db()
    rij = db.execute("SELECT bestand_pad FROM transacties WHERE id=?", (tid,)).fetchone()
    if rij and rij["bestand_pad"]:
        pad = UPLOAD_BASE / rij["bestand_pad"]
        if pad.exists():
            pad.unlink()
    db.execute("DELETE FROM transacties WHERE id=?", (tid,))
    db.commit()
    flash("Transactie verwijderd.", "success")
    return redirect(url_for("transacties"))

# ── Routes: Activa ────────────────────────────────────────────────────────────
@app.route("/activa")
@login_vereist
def activa():
    db = get_db()
    rijen = db.execute("SELECT * FROM activa ORDER BY aanschafdatum DESC").fetchall()

    activa_lijst = []
    for a in rijen:
        jaren_gebruikt = (date.today() - datetime.strptime(a["aanschafdatum"], "%Y-%m-%d").date()).days / 365.25
        jaren_resterend = max(0, a["levensduur_jaren"] - jaren_gebruikt)
        jaarlijks = (a["aanschafwaarde"] - a["restwaarde"]) / a["levensduur_jaren"]
        afgeschreven = round(min(jaarlijks * jaren_gebruikt, a["aanschafwaarde"] - a["restwaarde"]), 2)
        boekwaarde = round(a["aanschafwaarde"] - afgeschreven, 2)
        activa_lijst.append({
            "id": a["id"],
            "naam": a["naam"],
            "aanschafdatum": a["aanschafdatum"],
            "aanschafwaarde": a["aanschafwaarde"],
            "restwaarde": a["restwaarde"],
            "levensduur_jaren": a["levensduur_jaren"],
            "jaarlijkse_afschrijving": round(jaarlijks, 2),
            "totaal_afgeschreven": afgeschreven,
            "boekwaarde": boekwaarde,
            "jaren_resterend": round(jaren_resterend, 1),
        })

    return render_template("index.html", pagina="activa",
                           activa=activa_lijst,
                           gebruikersnaam=session.get("gebruikersnaam"))

@app.route("/activum/toevoegen", methods=["POST"])
@login_vereist
def activum_toevoegen():
    try:
        naam = sanitize(request.form.get("naam", ""), 200)
        aanschafdatum = validate_datum(request.form.get("aanschafdatum", ""))
        aanschafwaarde = validate_bedrag(request.form.get("aanschafwaarde", "0"))
        restwaarde = validate_bedrag(request.form.get("restwaarde", "0"))
        levensduur = int(request.form.get("levensduur_jaren", "0"))

        if not naam:
            flash("Naam is verplicht.", "error")
            return redirect(url_for("activa"))
        if levensduur <= 0 or levensduur > 100:
            flash("Ongeldige levensduur.", "error")
            return redirect(url_for("activa"))
        if restwaarde > aanschafwaarde:
            flash("Restwaarde mag niet hoger zijn dan aanschafwaarde.", "error")
            return redirect(url_for("activa"))

        db = get_db()
        db.execute(
            """INSERT INTO activa (naam, aanschafdatum, aanschafwaarde, restwaarde, levensduur_jaren)
               VALUES (?,?,?,?,?)""",
            (naam, aanschafdatum, aanschafwaarde, restwaarde, levensduur)
        )
        db.commit()
        flash("Activum toegevoegd.", "success")

    except ValueError as e:
        flash(f"Invoerfout: {e}", "error")

    return redirect(url_for("activa"))

@app.route("/activum/verwijderen/<int:aid>", methods=["POST"])
@login_vereist
def activum_verwijderen(aid):
    db = get_db()
    db.execute("DELETE FROM activa WHERE id=?", (aid,))
    db.commit()
    flash("Activum verwijderd.", "success")
    return redirect(url_for("activa"))

# ── Routes: Winst & Verlies / Balans ─────────────────────────────────────────
@app.route("/winst-verlies")
@login_vereist
def winst_verlies():
    db = get_db()
    jaar = request.args.get("jaar", str(date.today().year))
    try:
        jaar_int = int(jaar)
        if jaar_int < 2000 or jaar_int > 2100:
            raise ValueError
    except ValueError:
        jaar_int = date.today().year
        jaar = str(jaar_int)

    inkomsten_rows = db.execute(
        "SELECT categorie, SUM(bedrag) as totaal FROM transacties WHERE type='inkomst' AND strftime('%Y',datum)=? GROUP BY categorie",
        (jaar,)
    ).fetchall()
    uitgaven_rows = db.execute(
        "SELECT categorie, SUM(bedrag) as totaal FROM transacties WHERE type='uitgave' AND strftime('%Y',datum)=? GROUP BY categorie",
        (jaar,)
    ).fetchall()

    totaal_inkomsten = sum(r["totaal"] for r in inkomsten_rows)
    totaal_uitgaven = sum(r["totaal"] for r in uitgaven_rows)

    # Afschrijvingen dit jaar
    activa_rows = db.execute("SELECT * FROM activa").fetchall()
    afschrijvingen_jaar = 0.0
    for a in activa_rows:
        jaarlijks = (a["aanschafwaarde"] - a["restwaarde"]) / a["levensduur_jaren"]
        aanschaf = datetime.strptime(a["aanschafdatum"], "%Y-%m-%d").date()
        if aanschaf.year <= jaar_int <= aanschaf.year + a["levensduur_jaren"] - 1:
            afschrijvingen_jaar += jaarlijks

    winst = totaal_inkomsten - totaal_uitgaven - afschrijvingen_jaar
    jaren = list(range(date.today().year - 5, date.today().year + 1))

    return render_template("index.html", pagina="winst_verlies",
                           inkomsten_rows=inkomsten_rows,
                           uitgaven_rows=uitgaven_rows,
                           totaal_inkomsten=totaal_inkomsten,
                           totaal_uitgaven=totaal_uitgaven,
                           afschrijvingen_jaar=round(afschrijvingen_jaar, 2),
                           winst=round(winst, 2),
                           geselecteerd_jaar=jaar_int,
                           jaren=jaren,
                           gebruikersnaam=session.get("gebruikersnaam"))

# ── Routes: OCR ──────────────────────────────────────────────────────────────
@app.route("/ocr", methods=["POST"])
@login_vereist
def ocr():
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("your_"):
        return jsonify({"error": "OpenAI API key niet ingesteld."}), 503

    if "bestand" not in request.files:
        return jsonify({"error": "Geen bestand meegestuurd."}), 400

    bestand = request.files["bestand"]
    if not bestand or not allowed_file(bestand.filename):
        return jsonify({"error": "Ongeldig bestandstype."}), 400

    try:
        data = bestand.read(5 * 1024 * 1024)  # max 5 MB lezen
        b64 = base64.b64encode(data).decode("utf-8")
        ext = bestand.filename.rsplit(".", 1)[1].lower()
        media_type = "application/pdf" if ext == "pdf" else f"image/{ext}"

        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        prompt = """Analyseer dit document en retourneer ALLEEN een geldig JSON object met:
{
  "leverancier": "naam van leverancier",
  "factuurnummer": "factuurnummer of leeg",
  "datum": "YYYY-MM-DD formaat of leeg",
  "btw": nummer of 0,
  "totaalbedrag": nummer of 0,
  "categorie": "een van: Kantoor, Marketing, Transport, Investering, Overig",
  "omschrijving": "korte omschrijving"
}
Geen extra tekst, alleen JSON."""

        if ext == "pdf":
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": f"[PDF als base64: {b64[:500]}...]"}
                    ]
                }],
                max_tokens=400,
            )
        else:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}
                    ]
                }],
                max_tokens=400,
            )

        import json
        tekst = response.choices[0].message.content.strip()
        # Strip eventuele markdown
        tekst = re.sub(r"```(?:json)?", "", tekst).strip().rstrip("```").strip()
        resultaat = json.loads(tekst)

        # Valideer en sanitize teruggestuurde waarden
        veilig = {
            "leverancier": sanitize(str(resultaat.get("leverancier", "")), 200),
            "factuurnummer": sanitize(str(resultaat.get("factuurnummer", "")), 50),
            "datum": resultaat.get("datum", ""),
            "btw": float(resultaat.get("btw", 0) or 0),
            "totaalbedrag": float(resultaat.get("totaalbedrag", 0) or 0),
            "categorie": resultaat.get("categorie", "Overig") if resultaat.get("categorie") in ALLOWED_CATEGORIES else "Overig",
            "omschrijving": sanitize(str(resultaat.get("omschrijving", "")), 500),
        }
        return jsonify(veilig)

    except Exception as e:
        logger.error("OCR fout: %s", e)
        return jsonify({"error": "OCR verwerking mislukt."}), 500

# ── Routes: Excel export ─────────────────────────────────────────────────────
@app.route("/export/excel")
@login_vereist
def export_excel():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from flask import send_file

    db = get_db()
    wb = Workbook()

    # ── Kleurstijlen ──
    HEADER_FILL   = PatternFill("solid", start_color="1A1A2E")
    HEADER_FONT   = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    INKOMST_FILL  = PatternFill("solid", start_color="F0FDF4")
    UITGAVE_FILL  = PatternFill("solid", start_color="FEF2F2")
    TOTAAL_FONT   = Font(bold=True, name="Arial", size=10)
    NORMAL_FONT   = Font(name="Arial", size=10)
    THIN          = Side(style="thin", color="E5E7EB")
    BORDER        = Border(bottom=THIN)

    def style_header(ws, headers, col_widths):
        ws.append(headers)
        for i, cell in enumerate(ws[1], 1):
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 22
        for i, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def style_rows(ws, start=2):
        for row in ws.iter_rows(min_row=start):
            for cell in row:
                cell.font = NORMAL_FONT
                cell.border = BORDER
                cell.alignment = Alignment(vertical="center")
            ws.row_dimensions[cell.row].height = 18

    # ── Blad 1: Transacties ──
    ws1 = wb.active
    ws1.title = "Transacties"
    style_header(ws1,
        ["ID", "Datum", "Factuurnummer", "Omschrijving", "Type", "Categorie", "Bedrag (€)", "Bijlage"],
        [6, 13, 18, 40, 12, 14, 13, 10])

    transacties = db.execute(
        "SELECT id, datum, factuurnummer, omschrijving, type, categorie, bedrag, bestand_pad "
        "FROM transacties ORDER BY datum DESC"
    ).fetchall()

    for t in transacties:
        row = ws1.max_row + 1
        ws1.append([
            t["id"], t["datum"], t["factuurnummer"] or "",
            t["omschrijving"], t["type"].capitalize(), t["categorie"],
            t["bedrag"], "Ja" if t["bestand_pad"] else "Nee"
        ])
        fill = INKOMST_FILL if t["type"] == "inkomst" else UITGAVE_FILL
        for cell in ws1[row]:
            cell.fill = fill

    # Totaalrij
    last = ws1.max_row
    if last >= 2:
        totaal_row = last + 1
        ws1.cell(totaal_row, 6, "TOTAAL").font = TOTAAL_FONT
        ws1.cell(totaal_row, 7, f"=SUMIF(E2:E{last},\"Inkomst\",G2:G{last})-SUMIF(E2:E{last},\"Uitgave\",G2:G{last})").font = TOTAAL_FONT
        ws1.cell(totaal_row, 7).number_format = '€#,##0.00'
        ws1.cell(totaal_row, 6).fill = PatternFill("solid", start_color="E5E7EB")
        ws1.cell(totaal_row, 7).fill = PatternFill("solid", start_color="E5E7EB")

    ws1.freeze_panes = "A2"
    style_rows(ws1)

    # ── Blad 2: Activa ──
    ws2 = wb.create_sheet("Activa & Afschrijvingen")
    style_header(ws2,
        ["ID", "Naam", "Aanschafdatum", "Aanschafwaarde (€)", "Restwaarde (€)", "Levensduur (jr)",
         "Jaarl. Afschrijving (€)", "Totaal Afgeschreven (€)", "Boekwaarde (€)", "Jaren Resterend"],
        [6, 24, 15, 20, 16, 16, 22, 22, 16, 16])

    activa_rijen = db.execute("SELECT * FROM activa ORDER BY aanschafdatum DESC").fetchall()
    for a in activa_rijen:
        jaren_gebruikt = (date.today() - datetime.strptime(a["aanschafdatum"], "%Y-%m-%d").date()).days / 365.25
        jaarlijks = (a["aanschafwaarde"] - a["restwaarde"]) / a["levensduur_jaren"]
        afgeschreven = round(min(jaarlijks * jaren_gebruikt, a["aanschafwaarde"] - a["restwaarde"]), 2)
        boekwaarde = round(a["aanschafwaarde"] - afgeschreven, 2)
        jaren_rest = round(max(0, a["levensduur_jaren"] - jaren_gebruikt), 1)
        ws2.append([
            a["id"], a["naam"], a["aanschafdatum"],
            a["aanschafwaarde"], a["restwaarde"], a["levensduur_jaren"],
            round(jaarlijks, 2), afgeschreven, boekwaarde, jaren_rest
        ])

    ws2.freeze_panes = "A2"
    style_rows(ws2)
    for col in [4, 5, 7, 8, 9]:
        for cell in ws2.iter_rows(min_row=2, min_col=col, max_col=col):
            for c in cell:
                c.number_format = '€#,##0.00'

    # ── Blad 3: Winst & Verlies huidig jaar ──
    ws3 = wb.create_sheet("Winst & Verlies")
    huidig_jaar = str(date.today().year)
    ws3.title = f"Winst & Verlies {huidig_jaar}"

    ws3["A1"] = f"Winst & Verlies {huidig_jaar}"
    ws3["A1"].font = Font(bold=True, name="Arial", size=14)
    ws3["A1"].fill = PatternFill("solid", start_color="1A1A2E")
    ws3["A1"].font = Font(bold=True, color="FFFFFF", name="Arial", size=14)
    ws3.merge_cells("A1:B1")
    ws3.row_dimensions[1].height = 28

    ws3.column_dimensions["A"].width = 28
    ws3.column_dimensions["B"].width = 18

    def wv_sectie(ws, start_row, titel, rijen, kleur):
        ws.cell(start_row, 1, titel).font = Font(bold=True, name="Arial", size=10, color="FFFFFF")
        ws.cell(start_row, 1).fill = PatternFill("solid", start_color=kleur)
        ws.cell(start_row, 2).fill = PatternFill("solid", start_color=kleur)
        r = start_row + 1
        for cat, tot in rijen:
            ws.cell(r, 1, cat).font = NORMAL_FONT
            ws.cell(r, 2, tot).font = NORMAL_FONT
            ws.cell(r, 2).number_format = '€#,##0.00'
            r += 1
        if rijen:
            ws.cell(r, 1, "Totaal").font = TOTAAL_FONT
            ws.cell(r, 2, f"=SUM(B{start_row+1}:B{r-1})").font = TOTAAL_FONT
            ws.cell(r, 2).number_format = '€#,##0.00'
            ws.cell(r, 1).fill = PatternFill("solid", start_color="E5E7EB")
            ws.cell(r, 2).fill = PatternFill("solid", start_color="E5E7EB")
        return r + 2

    ink_rijen = db.execute(
        "SELECT categorie, SUM(bedrag) FROM transacties WHERE type='inkomst' AND strftime('%Y',datum)=? GROUP BY categorie",
        (huidig_jaar,)
    ).fetchall()
    uit_rijen = db.execute(
        "SELECT categorie, SUM(bedrag) FROM transacties WHERE type='uitgave' AND strftime('%Y',datum)=? GROUP BY categorie",
        (huidig_jaar,)
    ).fetchall()

    next_row = wv_sectie(ws3, 3, "INKOMSTEN", ink_rijen, "166534")
    next_row = wv_sectie(ws3, next_row, "UITGAVEN", uit_rijen, "991B1B")

    # Nettowinst
    ws3.cell(next_row, 1, "NETTO WINST / VERLIES").font = Font(bold=True, color="FFFFFF", name="Arial", size=11)
    ws3.cell(next_row, 1).fill = PatternFill("solid", start_color="1A1A2E")
    ws3.cell(next_row, 2).fill = PatternFill("solid", start_color="1A1A2E")
    totaal_ink = sum(r[1] for r in ink_rijen) if ink_rijen else 0
    totaal_uit = sum(r[1] for r in uit_rijen) if uit_rijen else 0
    ws3.cell(next_row, 2, totaal_ink - totaal_uit).font = Font(bold=True, color="FFFFFF", name="Arial", size=11)
    ws3.cell(next_row, 2).number_format = '€#,##0.00'
    ws3.row_dimensions[next_row].height = 24

    # Opslaan in geheugen en sturen
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    bestandsnaam = f"boekhouding_export_{date.today().isoformat()}.xlsx"
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=bestandsnaam)


# ── Routes: Backup ────────────────────────────────────────────────────────────
@app.route("/backup")
@login_vereist
def backup_pagina():
    backups = sorted(
        (BASE_DIR / "backups").glob("backup_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    ) if (BASE_DIR / "backups").exists() else []
    return render_template("index.html", pagina="backup",
                           backups=[p.name for p in backups[:20]],
                           gebruikersnaam=session.get("gebruikersnaam"))

@app.route("/backup/maken", methods=["POST"])
@login_vereist
def backup_maken():
    import zipfile
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_pad = backup_dir / f"backup_{ts}.zip"

    with zipfile.ZipFile(str(backup_pad), "w", zipfile.ZIP_DEFLATED) as zf:
        # Database
        if DB_PATH.exists():
            zf.write(str(DB_PATH), "boekhouding.db")
        # Uploads (bonnen/facturen)
        for bestand in UPLOAD_BASE.rglob("*"):
            if bestand.is_file():
                zf.write(str(bestand), str(bestand.relative_to(BASE_DIR)))

    flash(f"Backup aangemaakt: {backup_pad.name}", "success")
    logger.info("Backup gemaakt door %s: %s", session.get("gebruikersnaam"), backup_pad.name)
    return redirect(url_for("backup_pagina"))

@app.route("/backup/downloaden/<naam>")
@login_vereist
def backup_downloaden(naam):
    from flask import send_file
    # Valideer bestandsnaam — geen path traversal
    if not re.fullmatch(r"backup_\d{8}_\d{6}\.zip", naam):
        abort(400)
    pad = BASE_DIR / "backups" / naam
    if not pad.exists():
        abort(404)
    return send_file(str(pad), as_attachment=True, download_name=naam)

@app.route("/backup/verwijderen/<naam>", methods=["POST"])
@login_vereist
def backup_verwijderen(naam):
    if not re.fullmatch(r"backup_\d{8}_\d{6}\.zip", naam):
        abort(400)
    pad = BASE_DIR / "backups" / naam
    if pad.exists():
        pad.unlink()
        flash(f"Backup verwijderd: {naam}", "success")
    return redirect(url_for("backup_pagina"))


# ── App start ─────────────────────────────────────────────────────────────────
with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
