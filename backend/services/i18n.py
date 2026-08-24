"""Eenvoudige i18n-laag.

De actieve taal wordt per request in een ContextVar gezet door
SettingsMiddleware (op basis van CompanySettings.language). Alle bestaande
`t(key)`-aanroepen worden zo automatisch taalbewust zonder dat elke route
de locale hoeft door te geven.
"""
import contextvars

DEFAULT_LOCALE = "nl"
SUPPORTED_LOCALES = ("nl", "en")

_current_locale: contextvars.ContextVar = contextvars.ContextVar(
    "current_locale", default=DEFAULT_LOCALE
)


def set_locale(locale: str) -> None:
    _current_locale.set(locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE)


def get_locale() -> str:
    return _current_locale.get()


TRANSLATIONS = {
    "nl": {
        # ── Navigatie ──────────────────────────────────────────
        "brand_sub": "Boekhouding",
        "nav_overview": "Overzicht",
        "nav_dashboard": "Dashboard",
        "nav_reports": "Rapportages",
        "nav_yearoverview": "Jaaroverzicht",
        "nav_fiscalyears": "Boekjaren",
        "nav_transactions": "Transacties",
        "nav_income": "Inkomsten",
        "nav_expenses": "Uitgaven",
        "nav_planned": "Gepland",
        "nav_management": "Beheer",
        "nav_backups": "Back-ups",
        "nav_checklist": "Controlechecklist",
        "nav_export": "Export",
        "nav_csv_income": "CSV Inkomsten",
        "nav_csv_expenses": "CSV Uitgaven",
        "nav_settings": "Instellingen",
        "nav_logout": "Uitloggen",

        # ── Taalinstelling ─────────────────────────────────────
        "settings_language_title": "Taal / Language",
        "settings_language_hint": "Kies de taal van de interface.",
        "language_dutch": "Nederlands",
        "language_english": "Engels",
        "language_saved": "Taal opgeslagen.",

        # ── Bestaande sleutels ─────────────────────────────────
        "recurring_info_text": (
            "Terugkerende kosten worden automatisch vooruit geregistreerd op basis van "
            "de gekozen frequentie. Vergeet niet om aan het einde van ieder boekjaar alle "
            "bijbehorende bonnen en facturen toe te voegen aan de administratie."
        ),
        "recurring_badge": "Abonnement",
        "recurring_receipt_present": "Bon toegevoegd",
        "recurring_receipt_missing": "Bon ontbreekt",

        # "Bon"/"factuur" zoals Moneybird en e-Boekhouden.nl deze ook gebruiken
        # (herkenbaar voor zzp'ers) — bewust niet het formelere "bewijsstuk".
        "receipt_upload_label": "Bonnen uploaden (PDF/JPG/PNG) — meerdere mogelijk",
        "receipt_view_link": "Bekijk bon",
        "receipt_table_header": "Bon",
        "receipt_button_label": "Bon",
        "receipt_being_read": "Bon wordt uitgelezen…",
        "receipt_ready_prefix": "✅ Bon klaar om op te slaan: ",
        "ocr_panel_hint": "Upload een bon of factuur en laat AI de gegevens automatisch invullen.",
        "missing_receipts_alert": "{count} abonnementskosten missen nog een bon of factuur.",
        "ai_settings_ocr_hint": "Kies de AI-provider en het model dat gebruikt wordt voor het automatisch uitlezen van bonnen/facturen.",
        "invoice_numbering_hint": "Stel in of nieuwe inkomsten en uitgaven automatisch een volgnummer per boekjaar krijgen.",
    },
    "en": {
        # ── Navigation ─────────────────────────────────────────
        "brand_sub": "Bookkeeping",
        "nav_overview": "Overview",
        "nav_dashboard": "Dashboard",
        "nav_reports": "Reports",
        "nav_yearoverview": "Annual overview",
        "nav_fiscalyears": "Fiscal years",
        "nav_transactions": "Transactions",
        "nav_income": "Income",
        "nav_expenses": "Expenses",
        "nav_planned": "Planned",
        "nav_management": "Management",
        "nav_backups": "Backups",
        "nav_checklist": "Audit checklist",
        "nav_export": "Export",
        "nav_csv_income": "CSV Income",
        "nav_csv_expenses": "CSV Expenses",
        "nav_settings": "Settings",
        "nav_logout": "Log out",

        # ── Language setting ───────────────────────────────────
        "settings_language_title": "Taal / Language",
        "settings_language_hint": "Choose the interface language.",
        "language_dutch": "Dutch",
        "language_english": "English",
        "language_saved": "Language saved.",

        # ── Existing keys ──────────────────────────────────────
        "recurring_info_text": (
            "Recurring costs are automatically registered in advance based on the chosen "
            "frequency. Don't forget to add all associated receipts and invoices to your "
            "records at the end of each fiscal year."
        ),
        "recurring_badge": "Subscription",
        "recurring_receipt_present": "Receipt added",
        "recurring_receipt_missing": "Receipt missing",
        "receipt_upload_label": "Upload receipts (PDF/JPG/PNG) — multiple allowed",
        "receipt_view_link": "View receipt",
        "receipt_table_header": "Receipt",
        "receipt_button_label": "Receipt",
        "receipt_being_read": "Reading receipt…",
        "receipt_ready_prefix": "✅ Receipt ready to save: ",
        "ocr_panel_hint": "Upload a receipt or invoice and let AI fill in the details automatically.",
        "missing_receipts_alert": "{count} subscription costs are still missing a receipt or invoice.",
        "ai_settings_ocr_hint": "Choose the AI provider and model used to automatically read receipts/invoices.",
        "invoice_numbering_hint": "Set whether new income and expenses automatically receive a sequence number per fiscal year.",
    },
}


def t(key: str, locale: str = None) -> str:
    locale = locale or get_locale() or DEFAULT_LOCALE
    table = TRANSLATIONS.get(locale, TRANSLATIONS[DEFAULT_LOCALE])
    return table.get(key, TRANSLATIONS[DEFAULT_LOCALE].get(key, key))
