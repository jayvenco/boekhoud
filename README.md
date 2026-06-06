# Boekhoud App V3

Eenvoudige boekhoudapplicatie gebouwd met Python Flask, SQLite en Docker.

## Functies
- ✅ Login systeem (sessiegebaseerd)
- ✅ Transacties (inkomsten & uitgaven)
- ✅ Categoriebeheer + upload per categorie
- ✅ Bonnen/facturen uploaden (PDF, JPG, PNG)
- ✅ AI OCR via OpenAI GPT-4.1-mini
- ✅ Activa & lineaire afschrijvingen
- ✅ Winst & verlies per jaar
- ✅ Dashboard met financieel overzicht

## Installatie

### Vereisten
- Docker
- Docker Compose

### Stap 1 – API Key instellen
Open `docker-compose.yml` en vul je OpenAI API key in:
```
OPENAI_API_KEY=sk-xxxxxx
```

Stel ook een sterke SECRET_KEY in:
```
SECRET_KEY=een_lange_willekeurige_string_hier
```

### Stap 2 – Starten
```bash
docker compose up --build
```

### Stap 3 – Inloggen
Open: http://localhost:5000

| Veld           | Waarde    |
|----------------|-----------|
| Gebruikersnaam | admin     |
| Wachtwoord     | admin123  |

> ⚠️ Wijzig het admin-wachtwoord na eerste inlog via de database.

## Veiligheidsmaatregelen (al ingebouwd)
- Wachtwoorden opgeslagen als Werkzeug bcrypt-hash
- Sessies: HttpOnly, SameSite=Lax, max 1 uur
- Alle invoer gesanitized via `bleach`
- Bestandsuploads: alleen PDF/JPG/PNG, max 10 MB
- Bestandsnamen vervangen door UUID (geen path traversal)
- Categorie-whitelist (geen willekeurige mapmaken)
- SQL: uitsluitend parameterized queries
- Docker: non-root gebruiker, no-new-privileges

## Projectstructuur
```
boekhoud_app_v3/
├── app.py                  # Flask applicatie
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── templates/
│   ├── index.html          # Alle pagina's (dashboard, transacties, activa, W&V)
│   └── login.html
├── uploads/                # Gegenereerd bij start
│   ├── Kantoor/
│   ├── Marketing/
│   ├── Transport/
│   ├── Investering/
│   └── Overig/
└── data/
    └── boekhouding.db      # SQLite database (via Docker volume)
```
