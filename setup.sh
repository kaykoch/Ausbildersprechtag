#!/usr/bin/env bash

set -euo pipefail
IFS=$'\n\t'

# Konfiguration
VENV_DIR=".venv"
REQUIREMENTS_FILE="requirements.txt"
PYTHON_CMD="python3"
MIN_PY_MAJOR=3
MIN_PY_MINOR=11

# Hilfsfunktionen
info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$1"; }
error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$1" >&2; }
okay() { printf '\033[1;32m[OK]\033[0m %s\n' "$1"; }

# 1) Prüfungen: Existenz notwendiger Programme
info "Prüfe erforderliche Programme..."

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

MISSING=()

if ! command_exists "$PYTHON_CMD"; then
  MISSING+=("$PYTHON_CMD")
fi

# pip in System-Python prüfen (manche Distros haben pip3)
if ! command_exists pip3 && ! command_exists pip; then
  MISSING+=("pip3/pip")
fi

# Prüfen ob python venv Modul verfügbar ist (wird später nochmal im venv geprüft)
if command_exists "$PYTHON_CMD"; then
  if ! "$PYTHON_CMD" -c "import sys,venv" >/dev/null 2>&1; then
    # Falls venv nicht verfügbar, prüfen ob virtualenv installiert ist
    if ! command_exists virtualenv; then
      MISSING+=("python3 venv Modul oder virtualenv")
    fi
  fi
fi

# 1a) Prüfung: Mindestversion von python3 (>= 3.11)
if command_exists "$PYTHON_CMD"; then
  PY_VER_RAW=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") || PY_VER_RAW=""
  if [ -z "$PY_VER_RAW" ]; then
    error "Konnte python-Version nicht ermitteln."
    exit 2
  fi

  PY_MAJOR=$(echo "$PY_VER_RAW" | cut -d. -f1)
  PY_MINOR=$(echo "$PY_VER_RAW" | cut -d. -f2)

  if [ "$PY_MAJOR" -lt "$MIN_PY_MAJOR" ] || { [ "$PY_MAJOR" -eq "$MIN_PY_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_PY_MINOR" ]; }; then
    MISSING+=("python3 >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} (gefunden: ${PY_VER_RAW})")
  else
    okay "Gefundene python-Version: $PY_VER_RAW (>= ${MIN_PY_MAJOR}.${MIN_PY_MINOR})"
  fi
fi

if [ "${#MISSING[@]}" -ne 0 ]; then
  error "Es fehlen oder sind nicht geeignet: ${MISSING[*]}"
  warn "Bitte installiere die fehlenden Abhängigkeiten und starte das Script erneut."
  warn "Beispiele (Debian/Ubuntu): sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 2
fi

okay "Alle benötigten Programme sind verfügbar."

# --- NEU: Prüfung, ob der Benutzer www-data existiert ---
info "Prüfe, ob Benutzer 'www-data' existiert..."
if ! id -u www-data >/dev/null 2>&1; then
    error "Benutzer 'www-data' existiert nicht. Abbruch."
    exit 1
fi
okay "Benutzer 'www-data' existiert."

# --- NEU: Berechtigungen für das aktuelle Verzeichnis und alle Unterverzeichnisse setzen ---
info "Setze Berechtigungen für das aktuelle Verzeichnis und alle Unterverzeichnisse auf www-data:www-data..."
if ! chown -R www-data:www-data .; then
    error "Berechtigungen konnten nicht gesetzt werden. Abbruch."
    exit 1
fi
okay "Berechtigungen erfolgreich gesetzt."

# 2) requirements.txt prüfen
if [ ! -f "$REQUIREMENTS_FILE" ]; then
  warn "Datei '$REQUIREMENTS_FILE' wurde nicht gefunden im aktuellen Verzeichnis."
  read -r -p "Möchtest du fortfahren ohne Install von requirements? [y/N] " yn
  case "$yn" in
    [Yy]* ) info "Fortfahren ohne requirements-Installation." ;;
    * ) info "Abbruch."; exit 0 ;;
  esac
fi

# 3) Virtual Environment anlegen
if [ -d "$VENV_DIR" ]; then
  warn "Venv-Ordner '$VENV_DIR' existiert bereits."
  read -r -p "Soll das bestehende venv gelöscht und neu erstellt werden? [y/N] " yn
  case "$yn" in
    [Yy]* )
      info "Lösche vorhandenes venv: $VENV_DIR"
      rm -rf "$VENV_DIR"
      ;;
    * )
      info "Verwende vorhandenes venv."
      ;;
  esac
fi

if [ ! -d "$VENV_DIR" ]; then
  info "Erstelle virtuelle Umgebung in '$VENV_DIR'..."
  if "$PYTHON_CMD" -c "import venv" >/dev/null 2>&1; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
  else
    virtualenv -p "$PYTHON_CMD" "$VENV_DIR"
  fi
  okay "Virtuelle Umgebung erstellt."
fi

# 4) Aktivieren und pip updaten
ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"

if [ ! -f "$ACTIVATE_SCRIPT" ]; then
  error "Aktivierungsskript nicht gefunden: $ACTIVATE_SCRIPT"
  exit 3
fi

info "Aktiviere venv und installiere Pakete..."
(
  # shellcheck disable=SC1090
  source "$ACTIVATE_SCRIPT"

  if ! command -v pip >/dev/null 2>&1; then
    error "pip nicht im virtualenv verfügbar."
    exit 4
  fi

  info "Aktualisiere pip, setuptools und wheel im venv..."
  pip install --upgrade pip setuptools wheel

  if [ -f "$REQUIREMENTS_FILE" ]; then
    info "Installiere Pakete aus $REQUIREMENTS_FILE ..."
    if pip install -r "$REQUIREMENTS_FILE"; then
      okay "Alle Pakete aus $REQUIREMENTS_FILE wurden installiert."
    else
      error "Fehler beim Installieren der Pakete. Prüfe die Fehlermeldungen oben."
      exit 5
    fi
  else
    info "Keine requirements.txt gefunden – überspringe Paketinstallation."
  fi

  # 5) Prüfung: Sind alle Pakete installiert?
  if [ -f "$REQUIREMENTS_FILE" ]; then
    info "Überprüfe installierte Pakete gegen $REQUIREMENTS_FILE ..."
    MISSING_PKGS=()
    while IFS= read -r line || [ -n "$line" ]; do
      pkg=$(echo "$line" | sed 's/[[:space:]]*#.*//' | tr -d ' \t')
      [ -z "$pkg" ] && continue
      name=$(echo "$pkg" | sed -E 's/([<>=~!].*)$//')
      if ! pip show "$name" >/dev/null 2>&1; then
        MISSING_PKGS+=("$name")
      fi
    done < "$REQUIREMENTS_FILE"

    if [ "${#MISSING_PKGS[@]}" -eq 0 ]; then
      okay "Alle in requirements.txt gelisteten Pakete sind installiert."
    else
      warn "Folgende Pakete fehlen oder konnten nicht gefunden werden: ${MISSING_PKGS[*]}"
      warn "Mögliche Ursachen: abweichende Paketnamen, Installationsfehler oder Fehler in requirements.txt"
    fi
  fi

  okay "Venv-Setup in Subshell abgeschlossen."
)

info "Fertig. Um die virtuelle Umgebung jetzt zu aktivieren, führe aus:"
echo ""
echo "  source $VENV_DIR/bin/activate"
echo ""
info "Innerhalb der venv kannst du mit 'deactivate' die Umgebung wieder verlassen."
echo ""

# Zusatz: Hinweise zur Nutzung von startGunicorn.py
info "Hinweis: Anwendung starten / stoppen mit startGunicorn.py"
cat <<'USAGE'

Verwendung von startGunicorn.py:

  - Starten (Standardverhalten):
      ./startGunicorn.py
    Dieses Script beendet zunächst alle laufenden Gunicorn-Instanzen für die konfigurierte APP_NAME
    (SIGTERM, ggf. SIGKILL) und startet anschließend Gunicorn neu aus dem .venv/bin-Ordner
    (oder aus dem PATH, falls dort kein Binary vorhanden ist).

  - Nur beenden (kein Neustart):
      ./startGunicorn.py kill
    Mit dem Parameter 'kill' werden alle Gunicorn-Instanzen, die zum APP_NAME gehören, beendet.
    Danach wird kein Neustart durchgeführt.

Wichtige Hinweise:
  - Das Script erwartet, dass es im Projektverzeichnis neben der Anwendung liegt.
  - APP_NAME, PORT, WORKERS und der Pfad zur .venv können direkt in startGunicorn.py angepasst werden.
  - Wenn Gunicorn nicht im .venv/bin gefunden wird, versucht das Script 'gunicorn' aus dem System-PATH zu verwenden.
  - Zum manuellen Testen von laufenden Gunicorn-Prozessen nutze z.B.:
        pgrep -af "gunicorn"
    und zum gezielten Beenden:
        pkill -f "gunicorn.*<APP_NAME>"

USAGE

info "Setup abgeschlossen."
