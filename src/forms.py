from datetime import datetime
import re

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    EmailField,
    Field,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, Regexp, ValidationError


def normalize_whitespace(value: str) -> str:
    """Normalisiert Werte, bevor sie in die Datenbank übernommen werden.

    Die Funktion löscht:
        - Leerzeichen, Tabs, Zeilenumbrüche

    Args:
        value (str): Wert, der bereinigt werden soll

    Returns:
        str: Bereinigter String
    """
    if value is None:
        return ""
    # ensure string
    s = str(value)
    # trim ends
    s = s.strip()
    # replace any sequence of whitespace (spaces, tabs, newlines) with a single space
    s = re.sub(r"\s+", " ", s)
    return s


def validate_time_format(form: FlaskForm, field: Field):
    """_suValidiert, ob ein gegebener String dem Uhrzeitformat "HH:MM" entspricht.mmary_

    Args:
        form (FlaskForm): Flaskform, von der aufgreufen wurde
        field (Field): flask_feld, das überprüft wird

    Raises:
        ValidationError: _description_

    """

    try:
        time_string = field.data.strip()
        datetime.strptime(time_string, "%H:%M")
    except ValueError as e:  # Den ValueError als 'e' abfangen
        raise ValidationError(
            f"'{field.label.text}' enthält keine Uhrzeit. Bitte prüfe das Format: (z.B.: 12:00)."
        ) from e  # 'from e' hinzufügen


# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
class ConfigForm(FlaskForm):
    # Admin
    admin_login = StringField("Admin Login", validators=[Optional(), Length(min=4, max=15)])
    admin_password = PasswordField("Admin Passwort", validators=[Optional(), Length(min=4, max=15)])
    # TSS-Lehrkräfte
    tss_login = StringField("Lehrkraft Login", validators=[Optional(), Length(min=4, max=15)])
    tss_password = PasswordField("Lehrkraft Passwort", validators=[Optional(), Length(min=4, max=15)])

    # Mail
    mail_server = StringField("Mail Server", validators=[Optional(), Length(max=255)])
    mail_port = IntegerField("Mail Port", validators=[Optional(), NumberRange(min=1, max=65535)])
    mail_use_ssl = BooleanField("Nutze SSL", validators=[Optional()])
    mail_use_tls = BooleanField("Nutze TLS", validators=[Optional()])
    mail_username = StringField("Mail Benutzername", validators=[Optional(), Length(max=255)])
    mail_password = PasswordField("Mail Passwort", validators=[Optional(), Length(max=255)])
    mail_default_sender = StringField("Standard Absender (E-Mail)", validators=[Optional(), Length(max=320)])

    # Sprechtag
    sprechtag_termin = DateField(
        "Termin",
        format="%Y-%m-%d",
        validators=[DataRequired(message="Bitte dem Termin des Aubildersprechtages eingeben.")],
        render_kw={
            "title": "An welchem Tag findet der Ausbildersprechtag statt?",
        },
    )
    sprechtag_beginn = StringField(
        "Uhrzeit, Anfang",
        filters=[normalize_whitespace],
        validators=[Optional(), validate_time_format],
        render_kw={
            "placeholder": "z.B.: 16:00h",
            "title": "Um welche Uhrzeit findet der erste Termin statt??",
        },
    )
    sprechtag_ende = StringField(
        "Uhrzeit, Ende",
        filters=[normalize_whitespace],
        validators=[Optional(), validate_time_format],
        render_kw={
            "placeholder": "z.B.: 16:00h",
            "title": "Um welche Uhrzeit findet der letzte Termin statt?",
        },
    )
    sprechtag_wartezeit = IntegerField(
        "Wartezeit bis zum Löschen",
        validators=[Optional(), NumberRange(min=15, max=24 * 60)],
        render_kw={
            "placeholder": "z.B.: 90 (min: 15; max:1440 -> 24h)",
            "title": "Anzahl an Minuten, nach denen eine nicht bestätigte Anmeldung gelöscht wird.",
        },
    )
    submit = SubmitField("Einstellungen speichern")


class BuchungShowForm(FlaskForm):
    buchung_token = HiddenField(
        "buchung_token",
        validators=[Optional()],
        render_kw={"id": "buchung_token"},
    )
    buchung_action = HiddenField(
        "buchung_action",
        validators=[DataRequired()],
        render_kw={"id": "buchung_action"},
    )


class BeraterForm(FlaskForm):
    """WTF_Form zur Benutzung in folgenden Routen:
    @bp.route("/anmeldung.html")
    """

    berater_id = HiddenField("ID", validators=[Optional(), Length(max=36), Regexp(r"^\d+$")])

    berater_vorname = StringField(
        "Vorname",
        filters=[normalize_whitespace],
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "z.B.: John"},
    )

    berater_nachname = StringField(
        "Nachname",
        filters=[normalize_whitespace],
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "z.B.: Lennon"},
    )

    berater_raum = StringField(
        "Raum",
        filters=[normalize_whitespace],
        validators=[DataRequired(), Length(max=20)],
        render_kw={"placeholder": "z.B.: R109", "title": "Der Raum, in dem Sie den/die Ausbilder:In erwarten."},
    )

    berater_mail = EmailField(
        "E-Mail",
        filters=[normalize_whitespace],
        validators=[Optional(), Email(message="Bitte gieb eine gültige E-Mail-Adresse ein."), Length(max=100)],
        render_kw={"placeholder": "z.B.: john@beatles.de"},
    )

    berater_will_mail = BooleanField(
        "Benachrichtigung per Mail",
        default=False,
        render_kw={
            "title": "Ich bin damit einverstanden, dass eine Benachrichtigung an mich "
            "gesendet wird, sobald eine Anmeldung erfolgt"
        },
    )

    berater_dauer = IntegerField(
        "Dauer eines Termins",
        validators=[Optional(), NumberRange(min=10, max=45)],
        render_kw={
            "placeholder": "z.B.: 15 (min:10 - max:45)",
            "title": "Die Dauer in Minuten, für die ein Termin gebucht werden kann",
        },
    )
    berater_token = HiddenField(
        "beratberater_tokener_token",
        validators=[Optional()],
        render_kw={"id": "berater_token"},
    )

    submit_berater = SubmitField("Lehrkraft erstellen")

    def __repr__(self):
        return "<BeraterForm:>"


class BeraterShowForm(FlaskForm):
    action = HiddenField(
        "Action",
        validators=[DataRequired()],
        render_kw={"id": "form_action"},
    )
    token = HiddenField(
        "token",
        validators=[DataRequired()],
        render_kw={"id": "form_token"},
    )


class BuchungForm(FlaskForm):
    """WTF_Form zur Benutzung in folgenden Routen:
    @app.route("/")
    @app.route("/vollzeitschule.html"
    """

    buchung_id = HiddenField("buchug_id", validators=[Optional(), Length(max=36), Regexp(r"^\d+$")])

    berater_id = SelectField(
        "Mit wem möchten Sie sprechen?",
        choices=[("", "Bitte wählen...")],
        validators=[DataRequired()],
        render_kw={"title": "Bitte wählen Sie den Berater aus."},
    )
    uhrzeit_id = SelectField(
        "Wann möchten Sie mit der Lehrkraft sprechen?",
        validators=[DataRequired("Bitte wählen Sie eine Uhrzeit.")],  # Optional() durch DataRequired ersetzen
        render_kw={"title": "Bitte wählen Sie eine Uhrzeit aus."},
    )

    betrieb_name = StringField(
        "Betrieb (Ausbilder) / Erziehungsberechtigte",
        filters=[normalize_whitespace],
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "z.B.: Apple Records Ltd. (George Martin)"},
    )
    """
    buchung_vorname = StringField(
        "Vorname",
        filters=[normalize_whitespace],
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "z.B.: John"},
    )

    buchung_nachname = StringField(
        "Nachname",
        filters=[normalize_whitespace],
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "z.B.: Lennon"},
    )
    """

    betrieb_mail = EmailField(
        "E-Mail",
        filters=[normalize_whitespace],
        validators=[Optional(), Email(message="Bitte geben Sie eine gültige E-Mail-Adresse ein."), Length(max=100)],
        render_kw={"placeholder": "z.B.: john@beatles.de"},
    )
    submit = SubmitField("Termin buchen")
