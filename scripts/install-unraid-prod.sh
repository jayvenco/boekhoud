#!/usr/bin/env bash
# Boekhoud App PRODUCTIE - installeren/updaten op Unraid (idempotent). Container boekhoud-app, poort 7777, tag latest.
# Aanpasbaar via omgevingsvariabelen:
#   NAME (boekhoud-app)  TAG (latest)  PORT (7777)  APPDATA (/mnt/user/appdata/boekhoud)
#   BRANCH (main; waar template/icoon vandaan komen)  GHCR_USER / GHCR_TOKEN (optioneel docker login)
set -euo pipefail

NAME="${NAME:-boekhoud-app}"
TAG="${TAG:-latest}"
PORT="${PORT:-7777}"
APPDATA="${APPDATA:-/mnt/user/appdata/boekhoud}"
BRANCH="${BRANCH:-main}"
IMAGE="ghcr.io/jayvenco/boekhoud:${TAG}"
CPORT=5000
RAW="https://raw.githubusercontent.com/jayvenco/boekhoud/${BRANCH}"
TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"

info() { echo "==> $*"; }
fout() { echo "FOUT: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fout "docker niet gevonden (draai dit op de Unraid-server)."

info "Appdata-mappen controleren in ${APPDATA}"
for d in data uploads backups logs; do mkdir -p "${APPDATA}/${d}"; done
chown -R 99:100 "${APPDATA}" 2>/dev/null || true

# Geheime sleutel: hergebruik bestand, anders de sleutel van een bestaande container, anders nieuw.
# Belangrijk: SECRET_KEY mag niet wijzigen (versleutelde AI-sleutels en sessies zouden ongeldig worden).
SECRET_FILE="${APPDATA}/secret_key"
if [ -s "${SECRET_FILE}" ]; then
  info "Bestaande SECRET_KEY hergebruiken"
else
  OUD=""
  if [ -n "${SECRET_KEY:-}" ]; then
    OUD="${SECRET_KEY}"   # expliciet meegegeven sleutel (bijv. de bestaande productiesleutel)
  elif docker inspect "${NAME}" >/dev/null 2>&1; then
    OUD="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${NAME}" | sed -n 's/^SECRET_KEY=//p' | head -n1)"
  fi
  if [ -n "${OUD}" ]; then
    info "SECRET_KEY overnemen (meegegeven of van bestaande container)"
    SECRET="${OUD}"
  else
    info "Nieuwe SECRET_KEY genereren"
    SECRET="$( (openssl rand -hex 32 2>/dev/null) || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  ( umask 077; printf '%s' "${SECRET}" > "${SECRET_FILE}" )
fi
chmod 600 "${SECRET_FILE}"
SECRET_KEY="$(cat "${SECRET_FILE}")"

# Optionele AI-instellingen: omgeving > bestaande container
bestaand_env() { docker inspect "${NAME}" >/dev/null 2>&1 && docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${NAME}" | sed -n "s/^$1=//p" | head -n1 || true; }
OPENAI_API_KEY="${OPENAI_API_KEY:-$(bestaand_env OPENAI_API_KEY)}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-$(bestaand_env OLLAMA_BASE_URL)}"

if [ -n "${GHCR_USER:-}" ] && [ -n "${GHCR_TOKEN:-}" ]; then
  info "Inloggen bij ghcr.io als ${GHCR_USER}"
  printf '%s' "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin
fi

info "Image ophalen: ${IMAGE}"
docker pull "${IMAGE}"

if docker inspect "${NAME}" >/dev/null 2>&1; then
  info "Bestaande container '${NAME}' stoppen en verwijderen (data blijft op ${APPDATA})"
  docker rm -f "${NAME}" >/dev/null
fi

info "Container '${NAME}' starten"
docker run -d --name "${NAME}" \
  --restart unless-stopped \
  -p "${PORT}:${CPORT}" \
  -v "${APPDATA}/data:/app/data" \
  -v "${APPDATA}/uploads:/app/uploads" \
  -v "${APPDATA}/backups:/app/backups" \
  -v "${APPDATA}/logs:/app/logs" \
  -e "SECRET_KEY=${SECRET_KEY}" \
  -e "OPENAI_API_KEY=${OPENAI_API_KEY}" \
  -e "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}" \
  -e DATA_DIR=/app/data -e UPLOAD_DIR=/app/uploads -e BACKUP_DIR=/app/backups -e LOG_DIR=/app/logs \
  --label net.unraid.docker.managed=dockerman \
  --label "net.unraid.docker.webui=http://[IP]:[PORT:${CPORT}]/" \
  --label "net.unraid.docker.icon=${RAW}/unraid/icon.png" \
  "${IMAGE}" >/dev/null

if [ -d "${TEMPLATE_DIR}" ]; then
  info "Unraid-template bijwerken voor de Docker-tab"
  TMP="$(mktemp)"
  if curl -fsSL "${RAW}/unraid/boekhoud-app.xml" -o "${TMP}"; then
    sed -i \
      -e "s|<Name>boekhoud-app</Name>|<Name>${NAME}</Name>|" \
      -e "s|ghcr.io/jayvenco/boekhoud:latest|${IMAGE}|" \
      -e "s|>7777</Config>|>${PORT}</Config>|" \
      -e "s|Default=\"7777\"|Default=\"${PORT}\"|" \
      -e "s|/mnt/user/appdata/boekhoud|${APPDATA}|g" \
      -e "s|\(Target=\"SECRET_KEY\"[^>]*>\)</Config>|\1${SECRET_KEY}</Config>|" \
      "${TMP}"
    [ -n "${OPENAI_API_KEY}" ] && sed -i "s|\(Target=\"OPENAI_API_KEY\"[^>]*>\)</Config>|\1${OPENAI_API_KEY}</Config>|" "${TMP}"
    [ -n "${OLLAMA_BASE_URL}" ] && sed -i "s|\(Target=\"OLLAMA_BASE_URL\"[^>]*>\)</Config>|\1${OLLAMA_BASE_URL}</Config>|" "${TMP}"
    install -m 600 "${TMP}" "${TEMPLATE_DIR}/my-${NAME}.xml"
    echo "    Template: ${TEMPLATE_DIR}/my-${NAME}.xml"
  else
    echo "    Waarschuwing: template downloaden mislukt; container werkt wel, maar koppeling met de Docker-tab ontbreekt." >&2
  fi
  rm -f "${TMP}"
else
  info "Geen ${TEMPLATE_DIR} gevonden - template-stap overgeslagen (niet op Unraid?)"
fi

info "Wachten tot de container gezond is (max 90 s)"
STATUS="starting"
for _ in $(seq 1 45); do
  STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${NAME}" 2>/dev/null || echo onbekend)"
  case "${STATUS}" in healthy|running) break ;; unhealthy|exited|dead) break ;; esac
  sleep 2
done
[ "${STATUS}" = "healthy" ] || [ "${STATUS}" = "running" ] || { docker logs --tail 30 "${NAME}" >&2 || true; fout "Container is niet gezond (status: ${STATUS})."; }

IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
echo
info "Klaar! Status: ${STATUS}"
echo "    Open: http://${IP:-<server-ip>}:${PORT}/"
echo "    Beheer verder via de Docker-tab (Edit / Check for Updates / Apply Update)."
