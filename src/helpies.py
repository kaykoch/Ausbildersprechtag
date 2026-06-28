from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
import locale
import logging
from pathlib import Path
from smtplib import SMTPAuthenticationError, SMTPException

from flask import Response, abort, make_response, render_template, request
from flask_mail import Message
from markupsafe import Markup
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.inspection import inspect
from weasyprint import CSS, HTML

from src.models import Berater, Buchung, ConfigSetting


logger = logging.getLogger(__name__)


# +-------------------------------------------------------------------------------------------------
# + Datenbankkontrolle und Initialisierung
# +-------------------------------------------------------------------------------------------------
def _init_db(state) -> None:
    """Initialisiert die SQLite-Datenbank beim App-Start, falls noch nicht vorhanden.

    Es werden, wenn nicht vorhanden, alle Tabellen erstellt. Im Besonderen wird eine Zeile
    der ConfigSetting Tabelle erstellt, um die Standardwerte für den Admin-Zugang

    Args:
        app (Flask): Flask-Applikation (wird für `app.instance_path` und `app.app_context()` benötigt).

    Returns:
        None
    """
    global STATE
    STATE = state
    try:
        # Instance-Ordner sicherstellen (muss vor Zugriff auf Datei geschehen)
        Path(STATE.app.instance_path).mkdir(parents=True, exist_ok=True)

        # Import hier, damit Modelle registriert sind, bevor create_all() aufgerufen wird
        import src.models  # noqa: F41

        try:
            locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")

            # state.db.drop_all()
            STATE.db.create_all()
            # update_db()

            existing_config = src.models.ConfigSetting.query.first()
            if not existing_config:
                config = src.models.ConfigSetting()
                state.db.session.add(config)  # Config Model mit Default Werten erstellen

            # Beispiel-Personen anlegen, falls die Tabelle noch leer ist
            if not Berater.query.first():
                STATE.db.session.add_all(
                    [
                        Berater(berater_nachname="Koch", berater_vorname="Kay", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Rass", berater_vorname="Markus", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Tigges", berater_vorname=" Ute", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Dinstuhl", berater_vorname="Ralf", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Kues", berater_vorname="Max", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Brungs", berater_vorname="Thomas", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Röder", berater_vorname="David", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Recht", berater_vorname="Christian", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Glatt", berater_vorname="Sebastian", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Weber", berater_vorname="Marius", berater_mail="koch@tssbit.de"),
                        Berater(berater_nachname="Marweld", berater_vorname="Torsten", berater_mail="koch@tssbit.de"),
                    ]
                )

            state.db.session.commit()
            logger.info("Datenbanktabellen erstellt/überprüft.")

        except SQLAlchemyError as sqle:
            logger.exception("Fehler beim Erstellen der Datenbanktabellen: %s", sqle)
            raise

    except Exception as e:
        # Globales Fehler-Logging, App-Start nicht unbedingt abbrechen, aber Hinweis geben
        logger.exception(f"_init_db -> Fehler bei der Datenbankinitialisierung: {e}")
        # Optional: weiter hochwerfen, wenn du das Starten bei Fehlern verhindern willst:
        # raise


def _update_app() -> None:
    """Lädt dynamische Konfigurationswerte aus der Datenbank und aktualisiert die Flask-App-Konfiguration.
    Diese Funktion erwartet, dass ein gültiger Application Context aktiv ist.
    Die Attributnamen des Models werden in Großbuchstaben geändert und in app.config gespeichert:
    admin_login -> app.config[ADMIN_LOGIN] = value

    Returns:
        None
    """

    def __to_dict(obj) -> dict:
        """Wandelt den Inhalt der Tabelle in ein Dictionary um"""
        mapper = inspect(obj).mapper
        return {c.key: getattr(obj, c.key) for c in mapper.columns}

    ignore_keys = ["admin_login", "admin_password", "tss_login", "tss_password"]

    try:
        for cfg in STATE.db.session.query(ConfigSetting):
            data = __to_dict(cfg)
            data.pop("id")
            for key, value in data.items():
                if key in ignore_keys:
                    continue
                STATE.app.config[key.upper()] = value

        STATE.set_sprechtag(
            STATE.app.config["SPRECHTAG_TERMIN"].strftime("%A, %e. %B %Y "),
            STATE.app.config["SPRECHTAG_BEGINN"],
            STATE.app.config["SPRECHTAG_ENDE"],
        )

    except Exception as e:
        logger.exception(f"Konnte App-Konfiguration nicht aus DB laden: {e}")


# +-------------------------------------------------------------------------------------------------
# + Berater und Burchungen
# +-------------------------------------------------------------------------------------------------
def _delete_berater(berater: Berater) -> tuple:
    """löscht eine Lehrkraft und alle verbundenen Buchungen

    Args:
        berater (Berater): Lehrkraft, der gelöscht werdn soll

    Returns:
        tuple: Info, fehlercode (error| success)
    """
    try:
        # Kopie der Beraterdaten für die spätere Info
        berater_data = _copy_model_attributes(berater)
        # Instanz direkt löschen
        STATE.db.session.delete(berater)
        STATE.db.session.commit()

        # Ausgabe der Löschbestätigung
        info = f"{berater_data.berater_vorname} {berater_data.berater_nachname} und alle Termine gelöscht."
        logger.info(info)
        return (info, "success")

    except Exception as e:
        STATE.db.session.rollback()
        info = f"Fehler beim Löschen der Lehrkraft {berater_data.berater_vorname} {berater.berater_nachname} : {e}"
        logger.error(info)
        return (info, "error")


def _get_berater_by_token_or_abort(berater_token: str | None = None) -> Berater:
    """Versucht einen Berater aufgrund seines Token zu lesen
    Wenn das Token None ist oder der berater nicht existiert, wird die Applikation sofort abgebrochen

    Args:
        berater_token (str | None, optional): Token des Beraters. Defaults to None.

    Returns:
        Berater: berater zum Token
    """
    if berater_token:
        try:
            stmt = STATE.db.select(Berater).where(Berater.token == berater_token)
            berater: Berater | None = STATE.db.session.execute(stmt).scalar_one_or_none()

            if berater is None:
                logger.warning(f"Kein Berater mit Token '{berater_token}' gefunden.")
                abort(make_response("bad request! (Der angegebene Token ist ungültig oder abgelaufen)", 400))
            return berater

        except Exception as e:
            logger.error(f"Fehler beim Laden des Beraters mit Token '{berater_token}': {e}")
            abort(make_response("bad request! (Fehler beim Laden des Beraters)", 500))

    abort(make_response("bad request! (Kein Token angegeben)", 400))


def _delete_old_orders():
    """Löscht alte Buchungen, die nicht bestätigt wurden"""
    wait_time = datetime.now() - timedelta(minutes=STATE.app.config["SPRECHTAG_WARTEZEIT"])
    try:
        stmt = STATE.db.delete(Buchung).where(Buchung.bestaetigt.is_(False)).where(Buchung.erstellt_um < wait_time)
        result = STATE.db.session.execute(stmt)
        STATE.db.session.commit()
        logger.info(f"Modul _delete_old_orders: {result.rowcount} alte Buchung(en) gelöscht.")

    except Exception as e:
        STATE.db.session.rollback()
        logger.error(f"Fehler beim Löschen alter Buchungen: {e}")


# +-------------------------------------------------------------------------------------------------
# + Buchbare Zeiten
# +-------------------------------------------------------------------------------------------------
def _generiere_zeiten(dauer: int = 15) -> list:
    """Erstellt eine Liste von Uhrzeiten von Beginn bis Ende
        Die Abstände zwischen den Terminen wird als integer übergeben

    Args:
        dauer (int, optional): Dauer des jeweiligen termins. Defaults to 15.

    Returns:
        list: Liste mit Uhrzeiten ["16:00", "16:15"...]
    """
    zeiten = []
    start = datetime.strptime(STATE.sprechtag.beginn, "%H:%M")
    ende = datetime.strptime(STATE.sprechtag.ende, "%H:%M")

    while start <= ende:
        zeiten.append(start.strftime("%H:%M"))
        start += timedelta(minutes=dauer)
    return zeiten


def _get_gebuchte_zeiten(berater_id: int) -> list:
    """liefert eine Liste aller bereits gebuchter Termine

    Args:
        berater_id (int): id des Beraters

    Returns:
        list: Liste mit gebuchten Uhrzeiten ["16:30", "17:15"...]
    """
    # Welche Zeiten sind für diesen spezifischen Berater schon weg?
    stmt = STATE.db.select(Buchung.uhrzeit_id).filter_by(berater_id=berater_id)
    return STATE.db.session.execute(stmt).scalars().all()


def _get_freie_zeiten_fuer_berater(berater_id: int) -> list:
    """liefert eine Liste aller noch freien Termine eines bestimmten Beraters

    Args:
        berater_id (int): ID des Beraters

    Returns:
        list: Liste mit noch freien Terminen ["16:00", "16:15", "16:45"...]
    """
    stmt = STATE.db.select(Berater.berater_dauer).filter_by(berater_id=berater_id)
    dauer = STATE.db.session.execute(stmt).scalars().first()
    alle_zeiten = _generiere_zeiten(dauer)
    gebuchte_zeiten = _get_gebuchte_zeiten(berater_id)

    # Nur die noch freien Zeiten behalten
    verfuegbare_zeiten = [z for z in alle_zeiten if z not in gebuchte_zeiten]
    return verfuegbare_zeiten


# +-------------------------------------------------------------------------------------------------
# + Mails
# +-------------------------------------------------------------------------------------------------
def __send_mail(msg: Message) -> bool:
    """sendet eine Mail-Message

    Args:
        msg (Message): Message, die versendet werden soll

    Raises:
        SMTPAuthenticationError:
        SMTPException:

    Returns:
        bool: True, bei erfolgreichem Versand
    """

    # Standardrückgabewert auf False setzen
    returnvalue = False
    try:
        # STATE.mail.send(msg)
        print("SEND: ", msg.recipients)
        # print("SEND: \n", msg.html)
        returnvalue = True

    except SMTPAuthenticationError:
        # Spezieller Fehler: Benutzername oder Passwort für den Mailserver falsch
        logger.error("Mail-Fehler: Authentifizierung am SMTP-Server fehlgeschlagen.")

    except SMTPException as e:
        # Allgemeiner SMTP-Fehler (z.B. Mailserver nicht erreichbar, Timeout, Verbindungsabbruch)
        logger.error(f"Allgemeiner SMTP-Fehler beim Mailversand: {e}")

    except Exception as e:
        # Ein anderer unerwarteter Fehler (z.B. Programmierfehler im Code davor)
        logger.exception(f"Unerwarteter Fehler beim E-Mail-Versand: {e}")

    return returnvalue


def _send_mail_to_bucher(buchung: Buchung) -> tuple:
    """sendet eine Mail mit der Bestätigung der Anmeldung an die Firma

    Parameters:
    buchung (Buchung): Eine neue Buchung
    """
    subject = "Bestätigung; Ausbildersprechtag"
    msg = Message(subject=subject, recipients=[buchung.betrieb_mail])

    msg.html = render_template(
        "mail/mail_bucher.html",
        buchung=buchung,
        sprechtag=STATE.sprechtag,
        server_url=f"https://{request.host}",
    )
    if __send_mail(msg):
        # Erfolgreich versendet
        info = (
            f"Die Mail wurde an {buchung.betrieb_mail} gesendet<br>"
            "Bitte bestätigen Sie Ihre Daten innerhalb von 2 Stunden"
        )
        logger.info(f"Mail verschickt an:{buchung.betrieb_name}, ({buchung.betrieb_mail})")
        result = "warning"
    else:
        # Fehler beim Versand
        info = f"Die Mail an {buchung.betrieb_mail} konnte nicht versandt werden."
        logger.error(f"Fehler in Modul: _send_mail_to_bucher -> {buchung.betrieb_mail}")
        result = "error"

    return (Markup(info), result)


def _send_mail_to_berater(buchung: Buchung, delete: bool = False):
    """Baut das Gerüst einer Mail an die Lehrkraft, nachdem ein Betrieb einen Termin
    gebucht hat, oder wenn er seinen Termin löscht. Die Mail wird nur gesendet, wenn
    deie Lehrkraft ihren "berater_will_mail" auf True gesetzt hat

    Args:
        buchung (Buchung): Buchung des Betriebes
        delete (bool, optional): True, wenn es sich um eine Löschung handelt. Defaults to False.

    Returns:
        tuple: tuple: Info, fehlercode (error| success)
    """
    berater: Berater = buchung.berater
    if berater.berater_will_mail:
        subject = "Anmeldung; Ausbildersprechtag"
        msg = Message(subject=subject, recipients=[berater.berater_mail])
        msg.html = render_template(
            "mail/mail_berater.html",
            buchung=buchung,
            sprechtag=STATE.sprechtag,
            server_url=f"https://{request.host}",
            delete=delete,
        )

        if __send_mail(msg):
            # Erfolgreich versendet
            logger.info(f"Mail verschickt an:{buchung.berater.berater_nachname}, ({berater.berater_mail})")
        else:
            # Fehler beim Versand
            logger.error(f"Fehler in Modul: _send_mail_to_bucher -> {berater.berater_mail}")


def _send_anmeldung_mail_to_berater(berater: Berater) -> tuple:
    """Baut das Gerüst einer Mail an die Lehrkraft, nachdem sie sich angemeldet hat

    Args:
        berater (Berater): Lehrkraft

    Returns:
        tuple: tuple: Info, fehlercode (error| success)
    """
    subject = "Registration; Ausbildersprechtag"
    msg = Message(subject=subject, recipients=[berater.berater_mail])

    msg.html = render_template(
        "mail/mail_anmeldungberater.html",
        berater=berater,
        server_url=f"https://{request.host}",
    )

    if __send_mail(msg):
        # Erfolgreich versendet
        info = f"Die Mail wurde an {berater.berater_mail} gesendet"
        logger.info(f"Mail verschickt an:{berater.berater_mail}, ({berater.berater_mail})")
        result = "success"
    else:
        # Fehler beim Versand
        info = f"Die Mail an {berater.berater_mail} konnte nicht versandt werden."
        logger.error(f"Fehler in Modul: _send_mail_to_bucher -> {berater.berater_mail}")
        result = "error"

    return (Markup(info), result)


# +-------------------------------------------------------------------------------------------------
# + Diverses
# +-------------------------------------------------------------------------------------------------
def _copy_model_attributes(obj) -> dict:
    """_Kopiert alle Attribute eines SQLAlchemy-Objekts in ein Dictionary.
    Filtert interne Attribute (z. B. `_sa_instance_state`) herausmmary_

    Args:
        obj (db.Mode): Medel dessen Atribute kopiert werden sollen

    Returns:
        dict: Dictionary mit kopierten Attributen
    """
    if obj is None:
        return {}

    return {key: getattr(obj, key) for key in dir(obj) if not key.startswith("_") and not callable(getattr(obj, key))}


def _export_to_pdf(berater: Berater) -> BytesIO:
    """Erzeugt ein PDF mit allen übergebenen Schülerdatensätzen und liefert es als BytesIO zurück.

    Die Funktion rendert zunächst ein HTML-Template mit Titel/Untertitel (aktuelles Schuljahr)
    und der Schülerliste. Anschließend wird das HTML mit WeasyPrint in ein PDF konvertiert und
    als BytesIO zurück gesendet

    Args:
        alle_schueler (list | tuple): Sammlung der zu exportierenden Schülerdatensätze,
            typischerweise eine Liste von ORM-Objekten oder Dictionaries, die das Template erwartet.
        schulform (str): Schlüssel/Bezeichnung der Schulform (z. B. "berufsschule", "vollzeitschule");
            wird für Titel/Dateinamen verwendet.
        klasse (str): Klassennamen

    Returns:
        BytesIO: Inhalt der zu sendenden Datei

    Hinweise:
        - Das Template "pdf_layout.html" muss die Variablen "schueler", "titel" und "untertitel" erwarten.
        - WeasyPrint führt kein JavaScript aus; eingebettete Links bleiben klickbar.
        - Für konsistente Jahresdarstellung wird das Schuljahr als "YYYY/YYYY+1" formatiert.
    """
    titel = ""

    # HTML aus Template erzeugen
    try:
        html_content = render_template(
            "pdf_layout.html",
            berater=berater,
            titel=titel,
        )
        css_path = STATE.staticfolder / "pdf.css"
        # PDF-Daten generieren
        pdf_io = BytesIO()
        HTML(string=html_content).write_pdf(
            target=pdf_io,
            stylesheets=[CSS(filename=css_path)],
        )
        pdf_io.seek(0)
        return pdf_io

    except Exception as e:
        logger.error(f"_export_to_pdf -> PDF‑Erstellung fehlgeschlagen: {e}")
        return False


# +-----------------------------------------------
# + AUTHENTIFIZIERUNG
# +-----------------------------------------------
def __check_auth_and_get_type(username, password):
    """überprüft, ob  username und password mit einem der Login-Paare übereinstimmt
    und liefert den Typ des erfolgreichen logins zurück.
    Im Fehlerfall wird ein leerer string zurück gegeben

    Args:
        username (str): Benutzername_
        password (_type_): Passwort

    Returns:
        str: Liefert den typ des Logins zurück ("admin" | "tss" | "")
    """
    # Lade die Konfiguration aus der Datenbank
    stmt = STATE.db.select(ConfigSetting)
    config = STATE.db.session.execute(stmt).scalars().first()

    if not config:
        # Keine Konfiguration gefunden, niemand kann sich anmelden
        return None

    # Prüfe auf Admin-Anmeldedaten
    if username == config.admin_login and password == config.admin_password:
        return "admin"

    # Prüfe auf TSS Anmeldedaten
    if username == config.tss_login and password == config.tss_password:  # noqa: SIM103
        return "tss"

    # Wenn keines der Paare passt
    return None


def __authenticate():
    return Response("Login erforderlich", 401, {"WWW-Authenticate": 'Basic realm="Login erforderlich"'})


def _requires_auth(allowed_login_types):  # Dies ist jetzt eine Dekorator-Fabrik
    # Stelle sicher, dass allowed_login_types immer eine Liste ist
    if not isinstance(allowed_login_types, (list, tuple)):
        allowed_login_types = [allowed_login_types]

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.authorization
            if not auth:
                return __authenticate()

            # Verwende die angepasste check_auth_and_get_type Funktion
            login_type = __check_auth_and_get_type(auth.username, auth.password)

            # Prüfe, ob der erfolgreiche Login-Typ in den erlaubten Typen für diese Route ist
            if login_type in allowed_login_types:
                return f(*args, **kwargs)
            else:
                # Wenn Authentifizierung fehlschlägt oder der Login-Typ nicht erlaubt ist
                return __authenticate()

        return decorated

    return decorator


####################################################################################################
def update_db():
    """Ändert die Datenbankstruktur
    update_db() muss in _initdb() nach STATE.db.create_all() ausgeführt werden
    """
    new_att = "raum"
    print(new_att)

    for cls in ["Berater"]:
        try:
            print(f"  --> {cls}")
            STATE.db.session.execute(text(f"ALTER TABLE {cls} ADD COLUMN {new_att} String"))
            # STATE.db.session.execute(text(f"UPDATE {cls} SET {new_att} = '123214152r43' "))  # is NULL
            STATE.db.session.commit()
        except Exception as e:
            print(f"ERROR: {e}")
            continue
    """
    schulform = STATE.schulformen.get_schulform("vollzeitschule")
    cls = schulform.model_cls
    rows = STATE.db.session.query(cls).all()
    # print(rows)
    for row in rows:
        print(row.schueler_id)
        if not row.schueler_id:
            row.schueler_id = token_urlsafe(12)
    STATE.db.session.commit()

    STATE.db.session.execute(
        text(
            f"UPDATE Vollzeitschueler SET {new_att} = 'SONST' WHERE {new_att} IS NULL",
        )
    )
    """
