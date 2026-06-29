#!/usr/bin/python3
import os
import signal
import subprocess
import sys
import time
import stat
import pwd
import grp

# --- Konfiguration ---
APP_NAME = "sprechtag"  # Name der Applikation
PORT = "8083"           # Port, auf dem der Server hört
WORKERS = 3            # Anzahl der Worker
LOGFOLDER = "/var/log/gunicorn/"  # Log-Verzeichnis
LOG_FILE_ACCESS = f"{LOGFOLDER}/{APP_NAME}_access.log"  # Pfad zur Access-Log-Datei
LOG_FILE_ERROR = f"{LOGFOLDER}/{APP_NAME}_error.log"   # Pfad zur Error-Log-Datei
GUNICORN_USER = "www-data"  # Benutzer, unter dem Gunicorn laufen soll
GUNICORN_GROUP = "www-data" # Gruppe, unter der Gunicorn laufen soll
# ---------------------

BIND_ADDRESS = f"0.0.0.0:{PORT}"
APP_DIR = os.path.abspath(os.path.dirname(__file__))
VENV_DIR = os.path.join(APP_DIR, ".venv")
GUNICORN_BIN = os.path.join(VENV_DIR, "bin", "gunicorn")

print(f"--- Deployment-Tool für {APP_NAME} ---")

# Wechsel ins Projektverzeichnis
os.chdir(APP_DIR)

def get_uid_gid(user: str, group: str) -> tuple[int, int]:
    """Gibt die UID und GID für den angegebenen Benutzer und die Gruppe zurück.

    Args:
        user (str): Benutzername.
        group (str): Gruppenname.

    Returns:
        tuple[int, int]: (UID, GID)
    """
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
        return uid, gid
    except KeyError as e:
        print(f"Fehler: Benutzer oder Gruppe nicht gefunden: {e}")
        sys.exit(1)

def ensure_log_directory(log_folder: str, log_files: list[str], user: str, group: str) -> bool:
    """
    Stellt sicher, dass das Log-Verzeichnis existiert und die Log-Dateien erstellt werden.
    Setzt den Besitzer und die Berechtigungen korrekt.

    Args:
        log_folder (str): Pfad zum Log-Verzeichnis.
        log_files (list[str]): Liste der Log-Dateipfade.
        user (str): Benutzer, der Besitzer der Log-Dateien sein soll.
        group (str): Gruppe, die Besitzer der Log-Dateien sein soll.

    Returns:
        bool: True, wenn erfolgreich, False bei Fehlern.
    """
    try:
        uid, gid = get_uid_gid(user, group)

        # Verzeichnis erstellen, falls nicht vorhanden
        if not os.path.exists(log_folder):
            print(f"Erstelle Log-Verzeichnis: {log_folder}")
            os.makedirs(log_folder, mode=0o755, exist_ok=True)

        # Besitzer und Gruppe des Verzeichnisses setzen
        os.chown(log_folder, uid, gid)

        # Log-Dateien erstellen, falls nicht vorhanden
        for log_file in log_files:
            if not os.path.exists(log_file):
                print(f"Erstelle Log-Datei: {log_file}")
                with open(log_file, 'a') as f:
                    pass  # Datei erstellen
                os.chown(log_file, uid, gid)
                os.chmod(log_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

        # Berechtigungen prüfen
        if not os.access(log_folder, os.W_OK):
            print(f"Warnung: Keine Schreibrechte für {log_folder}. Versuche, Berechtigungen anzupassen...")
            os.chmod(log_folder, 0o755)
            os.chown(log_folder, uid, gid)
            for log_file in log_files:
                if os.path.exists(log_file):
                    os.chmod(log_file, 0o644)
                    os.chown(log_file, uid, gid)

        return True
    except Exception as e:
        print(f"Fehler beim Erstellen des Log-Verzeichnisses oder der Log-Dateien: {e}")
        return False

def find_gunicorn_pids(app_name: str = None) -> list:
    """Gibt eine Liste von PIDs zurück, die zu laufenden gunicorn-Prozessen passen.
    Wenn app_name gesetzt ist, wird nach 'gunicorn' und dem app_name im Kommando gesucht.

    Args:
        app_name (str, optional): Name der Application. Defaults to None.

    Returns:
        list: Liste mit PIDs
    """
    try:
        if app_name:
            pgrep_output = subprocess.check_output(["pgrep", "-f", f"gunicorn.*{app_name}"])
        else:
            pgrep_output = subprocess.check_output(["pgrep", "-f", "gunicorn"])
        pids = [int(p) for p in pgrep_output.decode().strip().splitlines() if p.strip()]
        return pids
    except subprocess.CalledProcessError:
        # pgrep gibt einen Fehlercode zurück, wenn nichts gefunden wird
        return []

def kill_pids(pids: list, sig=signal.SIGTERM) -> list:
    """Versucht die PIDs mit sig zu beenden. Gibt zurück, welche PIDs noch existieren.

    Args:
        pids (list): Liste mit PIDs, die beendet werden sollen.
        sig (signal): Signal, mit dem beendet werden soll. Defaults to signal.SIGTERM.

    Returns:
        list: Liste mit PIDs, die nicht beendet werden konnten.
    """
    for pid in pids:
        try:
            print(f"Versuche, PID {pid} mit Signal {sig.name} zu beenden...")
            os.kill(pid, sig)
        except ProcessLookupError:
            print(f"PID {pid} existiert nicht mehr.")
        except PermissionError:
            print(f"Keine Berechtigung, PID {pid} zu beenden.")
        except Exception as e:
            print(f"Fehler beim Beenden von PID {pid}: {e}")

    # Kurze Pause, dann prüfen welche noch laufen
    time.sleep(1)
    remaining = []
    for pid in pids:
        try:
            os.kill(pid, 0)
            remaining.append(pid)
        except OSError:
            pass
    return remaining

def start_gunicorn_as_user(user: str, group: str, cmd: list[str]) -> bool:
    """
    Startet Gunicorn unter dem angegebenen Benutzer und der Gruppe.

    Args:
        user (str): Benutzername.
        group (str): Gruppenname.
        cmd (list[str]): Befehl, der ausgeführt werden soll.

    Returns:
        bool: True, wenn erfolgreich, False bei Fehlern.
    """
    try:
        uid, gid = get_uid_gid(user, group)

        # Umgebungsvariablen für den neuen Prozess setzen
        env = os.environ.copy()
        env["PATH"] = f"{os.path.join(VENV_DIR, 'bin')}:{env.get('PATH', '')}"

        # Gunicorn als www-data starten
        print(f"Starte Gunicorn als Benutzer {user}:{group}...")
        process = subprocess.Popen(
            cmd,
            preexec_fn=lambda: os.setuid(uid),  # Setze UID vor dem Start
            env=env,
            cwd=APP_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Warten, bis der Prozess gestartet ist (Daemon-Modus)
        time.sleep(1)
        return True
    except Exception as e:
        print(f"Fehler beim Starten von Gunicorn als {user}: {e}")
        return False

def main():
    # Prüfe CLI-Parameter
    action_kill_only = False
    if len(sys.argv) > 1 and sys.argv[1].lower() == "kill":
        action_kill_only = True
        print("Parameter 'kill' erkannt: Beende alle Gunicorn-Instanzen und starte nichts neu.")

    # 1) Alle relevanten Gunicorn-PIDs finden und beenden
    pids = find_gunicorn_pids(APP_NAME)
    if not pids:
        print("Kein laufender Gunicorn-Prozess gefunden.")
    else:
        print(f"Gefundene Gunicorn-PIDs: {pids}")
        # Zuerst versuchen, ordentlich zu beenden (SIGTERM)
        remaining = kill_pids(pids, sig=signal.SIGTERM)
        if remaining:
            print(f"PIDs {remaining} reagieren nicht auf SIGTERM, sende SIGKILL...")
            remaining = kill_pids(remaining, sig=signal.SIGKILL)
            if remaining:
                print(f"Folgende PIDs konnten nicht beendet werden: {remaining}")
            else:
                print("Alle gefundenen PIDs wurden beendet.")
        else:
            print("Alle gefundenen PIDs wurden mit SIGTERM beendet.")

    # Wenn nur kill gefordert war, beenden wir hier
    if action_kill_only:
        print("Beenden nach 'kill' Aktion — kein Neustart wird durchgeführt.")
        return

    # 2) Log-Verzeichnis und Log-Dateien sicherstellen
    if not ensure_log_directory(LOGFOLDER, [LOG_FILE_ACCESS, LOG_FILE_ERROR], GUNICORN_USER, GUNICORN_GROUP):
        print("Fehler: Log-Verzeichnis oder Log-Dateien konnten nicht erstellt werden. Abbruch.")
        sys.exit(1)

    # 3) Gunicorn neu starten
    print("Starte Gunicorn neu...")

    if not os.path.isfile(GUNICORN_BIN):
        print(
            f"Warnung: Gunicorn-Binary nicht gefunden unter '{GUNICORN_BIN}'."
            " Versuche, 'gunicorn' aus PATH zu verwenden."
        )
        gunicorn_cmd = "gunicorn"
    else:
        gunicorn_cmd = GUNICORN_BIN

    cmd = [
        gunicorn_cmd,
        "--workers",
        str(WORKERS),
        "--bind",
        BIND_ADDRESS,
        "--log-level",
        "info",
        "--access-logfile",
        LOG_FILE_ACCESS,
        "--error-logfile",
        LOG_FILE_ERROR,
        "--daemon",
        f"{APP_NAME}:app",
    ]

    # Gunicorn als www-data starten
    if not start_gunicorn_as_user(GUNICORN_USER, GUNICORN_GROUP, cmd):
        print("Fehler: Gunicorn konnte nicht als www-data gestartet werden.")
        sys.exit(1)

    print("--- Neustart erfolgreich! ---")
    time.sleep(1)
    pids = find_gunicorn_pids(APP_NAME)
    if not pids:
        print("Kein laufender Gunicorn-Prozess gefunden.")
    else:
        print(f"Gefundene Gunicorn-PIDs: {pids}")

if __name__ == "__main__":
    main()
