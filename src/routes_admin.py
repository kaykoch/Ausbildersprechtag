import logging
from typing import Optional

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from markupsafe import Markup
from sqlalchemy import asc, desc
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from src.extensions import state
from src.forms import BeraterShowForm, ConfigForm
from src.helpies import (
    _delete_berater,
    _get_berater_by_token_or_abort,
    _requires_auth,
    _send_anmeldung_mail_to_berater,
    _update_app,
)
from src.models import Berater, ConfigSetting


logger = logging.getLogger(__name__)
bp = Blueprint("admin", __name__)


@bp.route("/", methods=["GET", "POST"])
@_requires_auth("admin")
def route_admin() -> str:
    """zeigt alle administrativen Aufgaben auf einer Webseite"""
    title = "Administration - Ausbilderbetriebe WebUntis"
    # info = "<p>Hier finden Sie Links zu allen administrativen Aufgaben:</p>"
    # flash(Markup(info), "success")
    return render_template(
        "admin/admin.html",
        title=title,
        sprechtag=state.sprechtag,
    )


@bp.route("/config.html", methods=["GET", "POST"])
@_requires_auth("admin")
def route_config() -> str:
    """Zeigt die Webseite zur Eingabe der Konfigurtionsdaten an

    Returns:
        str: Webseite
    """
    title = "Einstellungen - Ausbilderbetriebe WebUntis"
    # Konfiguration laden (ersten Datensatz)
    stmt = state.db.select(ConfigSetting).limit(1)
    cfg = state.db.session.execute(stmt).scalar_one_or_none()

    try:
        form = ConfigForm(obj=cfg)
        if form.validate_on_submit():
            # Neu anlegen, falls noch keine Config vorhanden
            if cfg is None:
                cfg = ConfigSetting()
                state.db.session.add(cfg)

            # Liste der Passwortfelder
            password_fields = ["admin_password", "mail_password", "tss_password"]

            # 1) Alle Felder außer Passwörter und Systemfelder speichern
            for fieldname, value in form.data.items():
                if fieldname not in password_fields + ["csrf_token", "submit"]:
                    setattr(cfg, fieldname, value)

            # 2) Passwörter nur setzen, wenn sie einen Wert haben
            for field in password_fields:
                if form[field].data:  # Prüfe, ob das Feld einen Wert hat
                    setattr(cfg, field, form[field].data)  # TODO: ggf. hashen/verschlüsseln

            try:
                state.db.session.commit()
                _update_app()
                flash("Konfiguration erfolgreich gespeichert.", "success")

            except SQLAlchemyError:
                state.db.session.rollback()
                current_app.logger.exception("DB-Fehler beim Speichern der Config")
                flash("Datenbankfehler beim Speichern der Konfiguration.", "error")
                return render_template("config.html", title=title, form=form), 500

        elif request.method == "POST":
            logger.error(f"Formular-Fehler in route_config: {form.errors}")
            texts = [msg for messages in form.errors.values() for msg in messages]
            flash(Markup("<br>".join(texts)), "error")

        return render_template(
            "admin/config.html",
            title=title,
            form=form,
            sprechtag=state.sprechtag,
        )

    except Exception as e:
        current_app.logger.exception("Fehler im Modul config")
        logger.error(f"Fehler im Modul config: {e}")
        abort(make_response("Interner Serverfehler", 500))


@bp.route("/berater.html", methods=["GET", "POST"])
@_requires_auth("admin")
def route_berateranzeige() -> str:
    title = "Anzeige der Lehrkräfte"
    form = BeraterShowForm()
    ALLOWED_ACTIONS = ["update", "show", "send", "delete"]
    info = "Für weitere Informationen mit der Maus über die Kopfzeile fahren"
    result = "warning"

    if form.validate_on_submit() and form.action.data in ALLOWED_ACTIONS:
        berater_token: str | None = form.token.data
        action: str | None = form.action.data

        # Überprüfung, ob token und berater existieren, sonst Abbruch
        berater = _get_berater_by_token_or_abort(berater_token)

        match action:
            case "update":
                return redirect(url_for("tss.route_lehrkraftanmeldung", token=berater.token))
            case "show":
                return redirect(url_for("tss.route_buchungenanzeige", token=berater.token))
            case "send":
                info, result = _send_anmeldung_mail_to_berater(berater)
            case "delete":
                info, result = _delete_berater(berater)

    flash(info, result)
    # Beginn: Es wurde kein Button geklickt, sondern die Seite wurde normal aufgerufen
    try:
        stmt = state.db.select(Berater).order_by(Berater.berater_nachname, Berater.berater_vorname)
        berater_liste = state.db.session.execute(stmt).scalars().all()

        return render_template(
            "admin/berater_anzeige.html",
            title=title,
            berater_liste=berater_liste,
            form=form,
        )

    except Exception as e:
        current_app.logger.exception("Fehler im Modul config")
        logger.error(f"Fehler im Modul config: {e}")
        abort(make_response("Interner Serverfehler", 500))
