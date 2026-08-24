DEFAULT_LOCALE = "nl"

TRANSLATIONS = {
    "nl": {
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
}


def t(key: str, locale: str = None) -> str:
    locale = locale or DEFAULT_LOCALE
    table = TRANSLATIONS.get(locale, TRANSLATIONS[DEFAULT_LOCALE])
    return table.get(key, TRANSLATIONS[DEFAULT_LOCALE].get(key, key))
