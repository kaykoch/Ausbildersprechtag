import logging

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from markupsafe import Markup

from src.extensions import state
from src.forms import BeraterForm, BuchungShowForm
from src.helpies import (
    _export_to_pdf,
    _get_berater_by_token_or_abort,
    _requires_auth,
    _send_anmeldung_mail_to_berater,
)
from src.models import Berater, Buchung


logger = logging.getLogger(__name__)
bp = Blueprint("tss", __name__)


@bp.route("/", methods=["GET", "POST"])
@_requires_auth(["admin", "tss"])
def route_lehrkraft() -> str:
    """zeigt alle administrativen Aufgaben auf einer Webseite"""
    berater_token: str | None = request.values.get("token")
    berater: Berater | None = None
    title = "Administration - Lehrkräfte"

    # Aufruf durch existierenden Berater oder Admin
    if berater_token:
        # Überprüfung, ob token und berater existieren, sonst Abbruch
        berater = _get_berater_by_token_or_abort(berater_token)
        return render_template(
            "tss/lehrkraft.html",
            title=title,
            sprechtag=state.sprechtag,
            berater=berater,
        )
    else:
        # Erster Aufruf einer Lehrkraft wird zur Anmeldung weitergeleitet
        return redirect(url_for("tss.route_lehrkraftanmeldung"))


@bp.route("/lehrkraft_anmeldung.html", methods=["GET", "POST"])
@_requires_auth(["admin", "tss"])
def route_lehrkraftanmeldung() -> str:
    """zeigt die Anmeldeseite für Lehrkräfte und, wenn sie schon angemeldet sind,
    deren Einstellungen

    Returns:
        str: Webseite
    """
    title = "Lehrkräfte"
    berater_token: str | None = request.values.get("token")
    berater: Berater | None = None

    # Aufruf mit token (Berater sollte existieren)
    if berater_token:
        # Überprüfung, ob token und berater existieren, sonst Abbruch
        berater = _get_berater_by_token_or_abort(berater_token)

    # Formular initialisieren
    form = BeraterForm(obj=berater) if berater else BeraterForm()

    # Formular wurde abgeschickt
    if form.validate_on_submit():
        # Speichern oder aktualisieren der Lehrkraftdaten
        info = f"Berater: {berater.berater_vorname} {berater.berater_nachname} wurde erfolgreich"
        try:
            if berater:
                # Lehrkraft existiert -> Update
                form.populate_obj(berater)
                state.db.session.commit()
                info += " aktualisiert"
                flash(Markup(info), "success")

            else:
                berater = Berater()
                # Lehrkraft ist neu -> Anlegen
                form.populate_obj(berater)
                state.db.session.add(berater)
                state.db.session.commit()
                info += " eingefügt"
                flash(Markup(info), "success")

                # Bestätigungsmail an neue Lehrkraft
                mail_info, mail_result = _send_anmeldung_mail_to_berater(berater)
                flash(mail_info, mail_result)

            redirect_url = url_for("tss.route_lehrkraftanmeldung", token=berater.token)
            return redirect(redirect_url)

        except Exception as e:
            state.db.session.rollback()
            logger.error(f"Datenbankfehler beim Speichern des Beraters: {e}")
            flash("Fehler beim Speichern. Bitte versuche es erneut.", "error")
            return render_template(
                "tss/lehrkraft_anmeldung.html",
                title=title,
                form=form,
            )

    elif request.method == "POST":
        logger.error(f"Formular-Fehler in route_anmeldung: {form.errors}")
        texts = [msg for messages in form.errors.values() for msg in messages]
        flash(Markup("<br>".join(texts)), "error")

    return render_template(
        "tss/lehrkraft_anmeldung.html",
        title=title,
        form=form,
        sprechtag=state.sprechtag,
    )


@bp.route("/buchungen.html", methods=["GET", "POST"])
@_requires_auth(["admin", "tss"])
def route_buchungenanzeige() -> str:
    """zeigt alle Buchungen einer Lehrkraft an

    Returns:
        str: Webseite
    """
    title = "Buchungen"
    berater_token: str | None = request.values.get("token")
    berater: Berater | None = None

    # Überprüfung, ob token und berater existieren, sonst Abbruch
    berater = _get_berater_by_token_or_abort(berater_token)
    form = BuchungShowForm()

    # Formular wurde abgeschickt
    if form.validate_on_submit():
        match form.buchung_action.data:
            # Download aller Buchungen
            case "download":
                file_io = _export_to_pdf(berater)
                return send_file(
                    file_io,
                    mimetype="application/pdf",
                    as_attachment=True,
                    download_name=f"{berater.berater_nachname}_{berater.berater_vorname}.pdf",
                    conditional=False,
                )

            # Löschen einer einzelnen Buchung
            case "delete":
                if not form.buchung_token.data:
                    flash("Keine Buchungs-ID angegeben.", "error")
                    return redirect(url_for("tss.route_buchungenanzeige", token=berater_token))

                stmt = state.db.select(Buchung).where(Buchung.token == form.buchung_token.data)
                buchung = state.db.session.execute(stmt).scalars().first()

                if buchung:
                    info = f"{buchung.betrieb_name} um {buchung.uhrzeit_id}h"
                    try:
                        state.db.session.delete(buchung)
                        state.db.session.commit()
                        flash(f"Buchung: {info} wurde erfolgreich gelöscht", "success")

                    except Exception as e:
                        state.db.session.rollback()
                        logger.error(f"Fehler beim Löschen der Buchung ({info}): {e}")
                        flash("Fehler beim Löschen. Bitte versuchen Sie es erneut.", "error")
                else:
                    flash("Buchung existiert nicht", "error")

                return redirect(url_for("tss.route_buchungenanzeige", token=berater_token))

    return render_template(
        "tss/lehrkraft_buchungen.html",
        title=title,
        form=form,
        sprechtag=state.sprechtag,
        berater=berater,
    )
