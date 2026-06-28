# ------------------------------------------------------------------------------
#     USER-BEREICH
# ------------------------------------------------------------------------------
import locale
import logging

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from markupsafe import Markup
from sqlalchemy.exc import SQLAlchemyError

from src.extensions import state
from src.forms import BeraterForm, BuchungForm, ConfigForm
from src.helpies import (
    _copy_model_attributes,
    _delete_old_orders,
    _export_to_pdf,
    _generiere_zeiten,
    _get_freie_zeiten_fuer_berater,
    _get_gebuchte_zeiten,
    _requires_auth,
    _send_anmeldung_mail_to_berater,
    _send_mail_to_berater,
    _send_mail_to_bucher,
)
from src.models import Berater, Buchung


logger = logging.getLogger(__name__)
bp = Blueprint("main", __name__)  # Name und optional url_prefix

# Ratenbegrenzung einrichten (10 Anfragen pro Minute pro IP-Adresse)
limiter = Limiter(
    get_remote_address,
    app=state.app,
    default_limits=["10 per minute"],
    storage_uri="memory://",
)


# +-----------------------------------------------
# + ROUTEN
# +-----------------------------------------------
@bp.route("/", methods=["GET", "POST"])
@limiter.limit("5 per minute")  # Strenges Limit für Mailversand
def index():
    """Startseite wird aufgerufen"""
    title = "Ausbildersprechtag der TSS"
    form = BuchungForm()

    # Alte Einträge, die nicht bestätigt wurden, löschen
    _delete_old_orders()

    # 1. Berater für das Dropdown laden
    stmt = state.db.select(Berater).order_by(Berater.berater_nachname, Berater.berater_vorname)
    berater_liste = state.db.session.execute(stmt).scalars().all()

    form.berater_id.choices = [
        ("", "Bitte wählen..."),
    ] + [(berater.berater_id, f"{berater.berater_nachname}, {berater.berater_vorname}") for berater in berater_liste]

    # 2. Standard-Fallback für Uhrzeiten (Sichert GET und fehlerhafte POSTs ab)
    form.uhrzeit_id.choices = [("", "Bitte wählen Sie zuerst eine Lehrkraft aus")]

    # 3. Wenn das Formular abgeschickt wird (POST)
    if request.method == "POST":
        selected_berater_id = request.form.get("berater_id")

        # Nur wenn eine ID da ist, überschreiben wir den Standard-Fallback von oben
        if selected_berater_id:
            valid_zeiten = _get_freie_zeiten_fuer_berater(selected_berater_id)
            form.uhrzeit_id.choices = [("", "Bitte Uhrzeit wählen...")] + [
                (zeit, f"{zeit} Uhr") for zeit in valid_zeiten
            ]

        if form.validate_on_submit():
            # Formular ist gültig, Daten verarbeiten
            berater_id = form.berater_id.data
            uhrzeit_id = form.uhrzeit_id.data

            # Überprüfung, ob Berater mit der ID existiert
            berater = state.db.session.get(Berater, berater_id)

            if not berater:
                flash("Es gibt keine Lehrkraft mit dieser ID", "error")
                return redirect(url_for("main.index"))

            # Überprüfung, ob Buchung wirklich frei ist
            stmt = state.db.select(Buchung).filter(
                Buchung.berater_id == berater_id,
                Buchung.uhrzeit_id == uhrzeit_id,
            )
            buchung = state.db.session.execute(stmt).scalars().first()
            if buchung:
                info = (
                    f"Der Termin um {uhrzeit_id}h bei {berater.berater_vorname} {berater.berater_nachname}"
                    "ist leider schon vergeben)"
                )
                return redirect(url_for("main.index"))

            # Speichern
            obj = Buchung()
            form.populate_obj(obj)
            state.db.session.add(obj)
            state.db.session.commit()

            info = f"Termin gebucht für: {berater.berater_vorname} {berater.berater_nachname} um {uhrzeit_id}h"
            flash(info, "success")

            # Mail an Betrieb
            info, result = _send_mail_to_bucher(obj)
            flash(info, result)

            return redirect(url_for("main.index"))

        else:
            # Formular ist ungültig, Fehler ausgeben und Formular erneut rendern
            info = form.errors
            flash(str(info), "error")
            logger.error(info)

    else:
        # Erster Aufruf
        form.uhrzeit_id.disabled = True

    return render_template(
        "index.html",
        title=title,
        berater_liste=berater_liste,
        sprechtag=state.sprechtag,
        form=form,
    )


@bp.route("/bestaetigung.html", methods=["GET"])
def bestaetigung():
    """Buchung wird durch Aufruf bestätigt oder gelöscht"""
    title = "Bestätigung"
    token = request.args.get("token")
    action = request.args.get("action")

    if not token:
        flash("Kein Token angegeben.", "error")
        return render_template(
            "bestaetigung.html",
            buchung=None,
            berater=None,
            sprechtag=state.sprechtag,
        )

    if not action:
        flash("Keine Aktion angegeben.", "error")
        return render_template(
            "bestaetigung.html",
            buchung=None,
            berater=None,
            sprechtag=state.sprechtag,
        )

        buchung: Buchung | None = None

    try:
        # Buchung laden
        stmt = state.db.select(Buchung).where(Buchung.token == token)
        buchung = state.db.session.execute(stmt).scalars().first()

        if buchung is None:
            flash(f"Buchung mit token {token} existiert nicht.", "error")
            return render_template(
                "bestaetigung.html",
                buchung=None,
                berater=None,
                sprechtag=state.sprechtag,
            )
        match action:
            case "confirm":
                # Buchung bestätigen
                buchung.bestaetigt = True
                state.db.session.commit()

                # Mail an Berater
                _send_mail_to_berater(buchung)
                info = (
                    " <p>⚠️ Der Termin wurde bestätigt</p>"
                    " <p><b>Wichtig: </b> Wenn Sie Ihren Termin nicht wahrnehmen können, nutzen Sie den"
                    " Stornierungslink in Ihrer Bestätigungs-E-Mail "
                    "— so geben Sie den Platz für andere Betriebe frei.</p>"
                )

                flash(Markup(info), "warning")

            case "delete":
                # Attribute der Buchung kopieren (für die E-Mail)
                buchung_data = _copy_model_attributes(buchung)  # <<< Hier kopieren

                # Buchung löschen
                stmt = state.db.delete(Buchung).where(Buchung.token == token)
                state.db.session.execute(stmt)
                state.db.session.commit()  # Erst committen

                # E-Mail mit den kopierten Daten senden
                _send_mail_to_berater(buchung_data, True)  # Dann E-Mail senden
                flash("Der Termin wurde gelöscht und wieder frei gegeben", "warning")
                buchung = None

            case _:
                logger.warning(f"Ungültige Aktion: {action}")
                flash("Ungültige Aktion.", "error")

    except Exception as e:
        state.db.session.rollback()
        logger.error(f"Fehler im Modul route_bestaetigung (action: {action}) (Toen: {token}): {e}")
        flash("Ein Fehler ist aufgetreten. Bitte versuche es erneut.", "error")

    return render_template(
        "bestaetigung.html",
        title=title,
        buchung=buchung,
        sprechtag=state.sprechtag,
    )


@bp.route("/api/freie_zeiten/<int:berater_id>")
def freie_zeiten(berater_id):
    """Diese Route wird von JavaScript aufgerufen, um freie Zeiten für eine Person zu holen."""
    verfuegbare_zeiten = _get_freie_zeiten_fuer_berater(berater_id)
    return jsonify(verfuegbare_zeiten)


@bp.route("/api/gebuchte_zeiten/<int:berater_id>")
def gebuchte_zeiten(berater_id):
    """Diese Route wird von JavaScript aufgerufen, um freie Zeiten für eine Person zu holen."""
    # Welche Zeiten sind für diese spezifische Lehrkraft noch frei?
    return jsonify(_get_gebuchte_zeiten(berater_id))


@bp.route("/impressum.html", methods=["GET", "POST"])
def route_impressum():
    return render_template("impressum.html")
