# +-----------------------------------------------
# + DATENBANK-MODELLE
# +-----------------------------------------------
from datetime import date, datetime
from secrets import token_urlsafe

from src.extensions import db


token = db.Column(db.String(32), default=lambda: token_urlsafe(12))


# Daten, wie Adminpassword. -login, Mailzugang
class ConfigSetting(db.Model):
    __tablename__ = "ConfigSetting"

    id = db.Column(db.Integer, primary_key=True)

    # Admin Credentials
    admin_login = db.Column(db.String(100), nullable=False, default="admin")
    admin_password = db.Column(db.String(255), nullable=False, default="admin")
    # TSS Credentials
    tss_login = db.Column(db.String(100), nullable=False, default="tssbit")
    tss_password = db.Column(db.String(255), nullable=False, default="tssbit")

    # Mail Server Einstellungen
    mail_server = db.Column(db.String(255), default="smtp.office365.com")
    mail_port = db.Column(db.Integer, default=587)
    mail_use_tls = db.Column(db.Boolean, default=True)
    mail_use_ssl = db.Column(db.Boolean, default=False)
    mail_username = db.Column(db.String(255), default="john@beatles.com")
    mail_password = db.Column(db.String(255), default="yellosubmarine")
    mail_default_sender = db.Column(db.String(255), default="paul@beatles.com")

    # Sprechtag Einstallungen
    sprechtag_termin = db.Column(db.Date, default=date.today)
    sprechtag_beginn = db.Column(db.String(6), default="16:00")
    sprechtag_ende = db.Column(db.String(6), default="19:00")
    sprechtag_wartezeit = db.Column(db.Integer(), default="90")


class Berater(db.Model):
    """Die Personen, mit denen man einen Termin buchen kann."""

    berater_id = db.Column(db.Integer, primary_key=True)
    berater_nachname = db.Column(db.String(100), nullable=False)
    berater_vorname = db.Column(db.String(100), nullable=False)
    berater_mail = db.Column(db.String(100), nullable=False)
    berater_dauer = db.Column(db.Integer, default=15)
    berater_raum = db.Column(db.String(100), nullable=True)
    berater_will_mail = db.Column(db.Boolean, default=False)
    token = db.Column(db.String(32), default=lambda: token_urlsafe(12))
    # Verknüpfung zur Buchungstabelle (Eins-zu-Viele)
    buchungen = db.relationship("Buchung", backref="berater", lazy=True)

    def __repr__(self):
        return f"<Berater: {self.berater_nachname}, {self.berater_vorname}>"


class Buchung(db.Model):
    """Die eigentlichen Termine."""

    buchung_id = db.Column(db.Integer, primary_key=True)
    betrieb_name = db.Column(db.String(100), nullable=False)
    uhrzeit_id = db.Column(db.String(5), nullable=False)
    betrieb_mail = db.Column(db.String(100), nullable=True)
    bestaetigt = db.Column(db.Boolean, nullable=False, default=False)
    token = db.Column(db.String(32), default=lambda: token_urlsafe(12))
    erstellt_um = db.Column(db.DateTime, default=datetime.now)

    # Fremdschlüssel: Welche Person wurde gebucht?
    berater_id = db.Column(db.Integer, db.ForeignKey("berater.berater_id"), nullable=False)

    # Verhindert, dass derselbe Berater zur selben Zeit doppelt gebucht wird
    __table_args__ = (db.UniqueConstraint("berater_id", "uhrzeit_id", name="_berater_zeit_uc"),)

    def __repr__(self):
        return f"<Buchung: {self.betrieb_name}>"
