FROM python:3.12-slim

# Veiligheid: draai als niet-root gebruiker
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Installeer dependencies eerst (cache-laag)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer applicatiecode
COPY . .

# Maak upload- en datamappen aan met juiste eigenaar
RUN mkdir -p uploads/Kantoor uploads/Marketing uploads/Transport uploads/Investering uploads/Overig \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

# Schakel over naar niet-root gebruiker
USER appuser

EXPOSE 5000

# Gebruik gunicorn-stijl productie-instelling via flask run met beperkte host
CMD ["python", "app.py"]
