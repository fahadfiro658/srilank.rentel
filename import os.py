import os
import re
import logging
import traceback
import random
import string
from datetime import datetime, date, time, timedelta
from functools import wraps

from flask import Flask, render_template_string, request, redirect, url_for, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired
from wtforms import StringField, SubmitField, SelectField, DateField, TimeField, IntegerField, BooleanField, TextAreaField, FloatField
from wtforms.validators import DataRequired, Length, ValidationError, Email, NumberRange, Optional
from werkzeug.utils import secure_filename
from twilio.rest import Client
from dotenv import load_dotenv

# Optional Firebase (Firestore + Storage) sync
try:
    import firebase_admin
    from firebase_admin import credentials as firebase_credentials
    from firebase_admin import firestore as firebase_firestore
except Exception:  # pragma: no cover
    firebase_admin = None
    firebase_credentials = None
    firebase_firestore = None

try:
    from sqlalchemy import event as sqlalchemy_event
except Exception:  # pragma: no cover
    sqlalchemy_event = None

# Load environment variables
load_dotenv()

# Vercel/serverless: only /tmp is writable, repo is read-only at runtime
IS_VERCEL = os.environ.get("VERCEL") == "1" or bool(os.environ.get("VERCEL_ENV"))
BASE_DIR = "/tmp" if IS_VERCEL else os.path.dirname(os.path.abspath(__file__))
DEFAULT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
# Use SQLite in /tmp on Vercel, and an absolute instance/premium_rental.db path locally.
LOCAL_DB_FILE = os.path.join(BASE_DIR, "instance", "premium_rental.db")
DEFAULT_DB_URL = (
    "sqlite:////tmp/premium_rental.db"
    if IS_VERCEL
    else f"sqlite:///{LOCAL_DB_FILE}"
)

# ==============================
# CONFIGURATION (Sri Lankan Version)
# ==============================
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', DEFAULT_DB_URL)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', DEFAULT_UPLOAD_FOLDER)
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    BANNER_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    HERO_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    
    # Cloudinary (recommended for Vercel; enables large direct-to-cloud uploads)
    CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    # Create an UNSIGNED upload preset in Cloudinary dashboard and put it here.
    CLOUDINARY_UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "")
    CLOUDINARY_FOLDER = os.environ.get("CLOUDINARY_FOLDER", "premium-rentals")
    
    # Currency settings - SRI LANKAN RUPEES
    CURRENCY_SYMBOL = 'Rs.'
    CURRENCY_CODE = 'LKR'
    
    # Bank details (Sri Lanka)
    BANK_NAME = os.environ.get('BANK_NAME', 'Bank of Ceylon')
    ACCOUNT_NAME = os.environ.get('ACCOUNT_NAME', 'Fahad Rental Service')
    ACCOUNT_NUMBER = os.environ.get('ACCOUNT_NUMBER', '123456789012')
    BRANCH_CODE = os.environ.get('BRANCH_CODE', '123')
    ACCOUNT_TYPE = os.environ.get('ACCOUNT_TYPE', 'Savings')
    
    # Twilio (optional)
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
    TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
    ADMIN_WHATSAPP = os.environ.get('ADMIN_WHATSAPP', 'whatsapp:+94756656862')
    
    # WhatsApp Business link (Sri Lanka number)
    WHATSAPP_NUMBER = os.environ.get('WHATSAPP_NUMBER', '94756656862')  # Without + for wa.me
    
    # Admin
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
    
    # Brand name - Centralized for easy updates
    BRAND_NAME = "Fahad Premium Rentals"
    BRAND_SHORT_NAME = "Fahad Rentals"
    BRAND_SLOGAN = "Luxury Car Rental in Sri Lanka"

    # Firebase (optional): when enabled, SQL data is mirrored to Firestore
    # Provide either FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT_PATH.
    FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
    FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    FIREBASE_FIRESTORE_PREFIX = os.environ.get("FIREBASE_FIRESTORE_PREFIX", "premium_rentals")
    _DEFAULT_FIREBASE_SA_PATH = os.path.join(
        BASE_DIR,
        ".env",
        "fahad-61bab-firebase-adminsdk-fbsvc-c43c17dc92.json",
    )
    FIREBASE_SERVICE_ACCOUNT_PATH = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        _DEFAULT_FIREBASE_SA_PATH if os.path.exists(_DEFAULT_FIREBASE_SA_PATH) else "",
    )

    # Accept common truthy values: "1", "true", "TRUE", "yes", etc.
    _FIREBASE_ENABLED_FROM_ENV = os.environ.get("FIREBASE_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    FIREBASE_ENABLED = bool(
        _FIREBASE_ENABLED_FROM_ENV
        or (FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_SERVICE_ACCOUNT_JSON)
    )

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Normalize UPLOAD_FOLDER: if env provides a relative path (e.g. "uploads"),
# make it absolute relative to this file's directory so saving/serving matches
# regardless of the process working directory.
_upload_folder = str(app.config.get('UPLOAD_FOLDER') or DEFAULT_UPLOAD_FOLDER)
if not os.path.isabs(_upload_folder):
    _upload_folder = os.path.join(BASE_DIR, _upload_folder)
app.config['UPLOAD_FOLDER'] = _upload_folder

# Ensure upload folders exist with proper permissions
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'banners'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'cars'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'hero'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'temp'), exist_ok=True)

# Ensure SQLite directory exists for local database file
if not IS_VERCEL:
    os.makedirs(os.path.dirname(LOCAL_DB_FILE), exist_ok=True)

# Initialize database
db = SQLAlchemy(app)

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# ==============================
# FIREBASE INITIALIZATION (optional)
# ==============================
_firebase_app = None
_firestore_client = None


def _get_firestore_client():
    global _firebase_app, _firestore_client
    if _firestore_client is not None:
        return _firestore_client

    if not app.config.get("FIREBASE_ENABLED"):
        return None
    if firebase_admin is None or firebase_credentials is None or firebase_firestore is None:
        logger.warning("Firebase is enabled but firebase-admin is not installed/importable.")
        return None

    try:
        if not firebase_admin._apps:
            sa_json = (app.config.get("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
            sa_path = (app.config.get("FIREBASE_SERVICE_ACCOUNT_PATH") or "").strip()
            if sa_json:
                import json

                cred = firebase_credentials.Certificate(json.loads(sa_json))
            elif sa_path:
                cred = firebase_credentials.Certificate(sa_path)
            else:
                logger.warning("Firebase enabled but no service account provided.")
                return None

            options = {}
            if app.config.get("FIREBASE_PROJECT_ID"):
                options["projectId"] = app.config["FIREBASE_PROJECT_ID"]
            _firebase_app = firebase_admin.initialize_app(cred, options=options or None)

        _firestore_client = firebase_firestore.client()
        logger.info("Firebase Firestore client initialized.")
        return _firestore_client
    except Exception:
        logger.exception("Failed to initialize Firebase Firestore client.")
        return None


def _sa_model_to_dict(model_obj):
    """Serialize a SQLAlchemy model instance into Firestore-safe dict."""
    data = {}
    for col in model_obj.__table__.columns:
        key = col.name
        value = getattr(model_obj, key)
        if isinstance(value, (datetime, date, time)):
            try:
                data[key] = value.isoformat()
            except Exception:
                data[key] = str(value)
        else:
            data[key] = value
    return data


def _firestore_collection(name: str) -> str:
    prefix = (app.config.get("FIREBASE_FIRESTORE_PREFIX") or "").strip()
    return f"{prefix}_{name}" if prefix else name


def _firestore_upsert(collection: str, doc_id: str, payload: dict):
    client = _get_firestore_client()
    if client is None:
        return
    client.collection(_firestore_collection(collection)).document(str(doc_id)).set(payload, merge=True)


def _firestore_delete(collection: str, doc_id: str):
    client = _get_firestore_client()
    if client is None:
        return
    client.collection(_firestore_collection(collection)).document(str(doc_id)).delete()

# ==============================
# HELPER FUNCTIONS FOR SRI LANKAN FORMAT
# ==============================
def format_currency(amount):
    """Format amount in Sri Lankan currency style: Rs. 5,000/="""
    if amount is None:
        amount = 0
    # Format with commas for thousands
    formatted_amount = f"{int(amount):,}"
    return f"Rs. {formatted_amount}/="

def format_currency_simple(amount):
    """Simple currency format without /="""
    if amount is None:
        amount = 0
    return f"Rs. {int(amount):,}"

def generate_tracking_number():
    """Generate a unique tracking number in format FR00001, FR00002, etc. (FR = Fahad Rentals)"""
    last_booking = Booking.query.order_by(Booking.id.desc()).first()
    
    if last_booking and last_booking.tracking_number:
        # Extract number from last tracking number (e.g., FR00001 -> 1)
        try:
            last_num = int(last_booking.tracking_number[2:])  # Remove 'FR' prefix
            new_num = last_num + 1
        except:
            new_num = 1
    else:
        new_num = 1
    
    # Format with leading zeros (5 digits)
    return f"FR{new_num:05d}"


# ==============================
# MEDIA URL HELPERS (local or full URL)
# ==============================
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_http_url(value):
    return bool(value) and bool(_HTTP_URL_RE.match(str(value)))


def media_url(value, kind: str) -> str:
    """
    Returns a usable URL for either:
    - full http(s) URL stored in DB (Cloudinary/CDN/etc)
    - local filename stored in DB (served via our Flask routes)
    """
    if not value:
        return ""
    if is_http_url(value):
        return str(value)

    if kind == "car":
        return url_for("car_image", filename=value)
    if kind == "hero":
        return url_for("hero_image", filename=value)
    if kind == "banner":
        return url_for("banner_image", filename=value)
    if kind == "background":
        return url_for("background_image")
    if kind == "upload":
        return url_for("uploaded_file", filename=value)
    return str(value)


app.jinja_env.globals["media_url"] = media_url

# ==============================
# DATABASE MODELS
# ==============================
class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_number = db.Column(db.String(20), unique=True, nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    car_model = db.Column(db.String(50), nullable=False)
    car_price_per_day = db.Column(db.Integer, nullable=False, default=0)
    pickup_date = db.Column(db.String(20), nullable=False)
    pickup_time = db.Column(db.String(10), nullable=False, default='10:00')
    return_date = db.Column(db.String(20), nullable=False)
    return_time = db.Column(db.String(10), nullable=False, default='18:00')
    total_days = db.Column(db.Integer, nullable=False, default=1)
    total_price = db.Column(db.Integer, nullable=False, default=0)
    amount_paid = db.Column(db.Integer, nullable=False, default=0)
    balance_due = db.Column(db.Integer, nullable=False, default=0)
    payment_status = db.Column(db.String(20), default='pending')  # pending, partial, paid
    id_front = db.Column(db.String(200), nullable=False)
    id_back = db.Column(db.String(200), nullable=False)
    license_file = db.Column(db.String(200), nullable=False)
    payment_screenshot = db.Column(db.String(200), nullable=True)
    payment_method = db.Column(db.String(50), nullable=True)  # bank_transfer, cash
    payment_reference = db.Column(db.String(100), nullable=True)  # Reference number
    notes = db.Column(db.Text, nullable=True)  # Admin notes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = db.Column(db.String(20), default='pending')  # pending, active, completed, cancelled
    
    def __repr__(self):
        return f'<Booking {self.tracking_number} - {self.customer_name}>'

class Car(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    price_per_day = db.Column(db.Integer, nullable=False)
    km_per_day = db.Column(db.Integer, nullable=False, default=100)
    image = db.Column(db.String(200), default='default_car.jpg')
    available = db.Column(db.Boolean, default=True)
    category = db.Column(db.String(50), default='luxury')
    transmission = db.Column(db.String(20), default='automatic')
    seats = db.Column(db.Integer, default=5)
    fuel_type = db.Column(db.String(20), default='petrol')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Banner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    subtitle = db.Column(db.String(200), nullable=True)
    image = db.Column(db.String(200), nullable=False)
    offer_text = db.Column(db.String(100), nullable=True)
    km_offer = db.Column(db.Integer, nullable=True)
    price_offer = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Background(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image = db.Column(db.String(200), nullable=False, default='default_bg.jpg')
    title = db.Column(db.String(100), nullable=True)
    description = db.Column(db.String(200), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Hero(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image = db.Column(db.String(200), nullable=False, default='default_hero.jpg')
    title = db.Column(db.String(100), nullable=True)
    subtitle = db.Column(db.String(200), nullable=True)
    overlay_opacity = db.Column(db.Float, default=0.6)
    overlay_color = db.Column(db.String(20), default='0,0,0')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Expense(db.Model):
    """Track business expenses"""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(50), nullable=False)  # maintenance, fuel, insurance, tax, salary, other
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    payment_method = db.Column(db.String(50), nullable=True)
    receipt = db.Column(db.String(200), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AccountTransaction(db.Model):
    """Track all financial transactions"""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    type = db.Column(db.String(20), nullable=False)  # income, expense, transfer
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    reference_id = db.Column(db.String(50), nullable=True)  # Booking ID or Expense ID
    payment_method = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ==============================
# FIRESTORE MIRRORING (optional)
# ==============================
_firestore_sync_registered = False


def _register_firestore_sync():
    """
    Mirrors SQLAlchemy row changes into Firestore collections.
    This keeps the existing app logic intact while also storing data in Firebase.
    """
    global _firestore_sync_registered
    if _firestore_sync_registered:
        return
    if sqlalchemy_event is None:
        logger.warning("SQLAlchemy event system unavailable; Firestore mirroring disabled.")
        return

    model_to_collection = {
        Booking: "bookings",
        Car: "cars",
        Banner: "banners",
        Background: "backgrounds",
        Hero: "heroes",
        Expense: "expenses",
        AccountTransaction: "account_transactions",
    }

    def _after_change(mapper, connection, target):  # noqa: ARG001
        try:
            payload = _sa_model_to_dict(target)
            payload["_model"] = target.__class__.__name__
            payload["_mirrored_at"] = datetime.utcnow().isoformat()
            collection = model_to_collection.get(target.__class__)
            if not collection:
                return
            doc_id = getattr(target, "id", None)
            if doc_id is None:
                return
            _firestore_upsert(collection, str(doc_id), payload)
        except Exception:
            logger.exception("Failed to mirror %s to Firestore.", target.__class__.__name__)

    def _after_delete(mapper, connection, target):  # noqa: ARG001
        try:
            collection = model_to_collection.get(target.__class__)
            if not collection:
                return
            doc_id = getattr(target, "id", None)
            if doc_id is None:
                return
            _firestore_delete(collection, str(doc_id))
        except Exception:
            logger.exception("Failed to delete %s from Firestore.", target.__class__.__name__)

    for model_cls in model_to_collection.keys():
        sqlalchemy_event.listen(model_cls, "after_insert", _after_change)
        sqlalchemy_event.listen(model_cls, "after_update", _after_change)
        sqlalchemy_event.listen(model_cls, "after_delete", _after_delete)

    _firestore_sync_registered = True
    logger.info("Firestore mirroring hooks registered.")


def _restore_from_firestore_if_empty():
    """
    When running on Vercel (ephemeral filesystem), rebuild the local SQLite
    tables from Firestore if they're empty. This makes Firestore the true
    source of truth, and SQLAlchemy just a cache for each instance.
    """
    client = _get_firestore_client()
    if client is None:
        logger.info("Firestore client not available; skipping restore.")
        return

    mappings = [
        (Booking, "bookings"),
        (Car, "cars"),
        (Banner, "banners"),
        (Background, "backgrounds"),
        (Hero, "heroes"),
        (Expense, "expenses"),
        (AccountTransaction, "account_transactions"),
    ]

    for model_cls, coll_name in mappings:
        # If table already has rows, don't touch it.
        if model_cls.query.count() > 0:
            continue

        try:
            docs = client.collection(_firestore_collection(coll_name)).stream()
        except Exception:
            logger.exception("Failed to read collection %s from Firestore.", coll_name)
            continue

        instances = []
        for doc in docs:
            data = doc.to_dict() or {}

            # Try to preserve the same numeric ID as the Firestore document ID
            try:
                doc_id_int = int(doc.id)
            except (TypeError, ValueError):
                doc_id_int = None

            if doc_id_int is not None:
                # If a row with this ID already exists, skip it.
                if model_cls.query.get(doc_id_int):
                    continue
                data["id"] = doc_id_int

            try:
                instances.append(model_cls(**data))
            except Exception:
                logger.exception(
                    "Failed to deserialize Firestore doc %s in %s.", doc.id, coll_name
                )

        if instances:
            db.session.add_all(instances)
            db.session.commit()
            logger.info(
                "Restored %s %s record(s) from Firestore.", len(instances), coll_name
            )


_register_firestore_sync()


def _init_db_if_possible():
    """
    Serverless-safe DB init.

    - Always runs inside app context.
    - Uses SQLite locally (or /tmp on Vercel) only as a cache.
    - Real persistence lives in Firestore; on a fresh instance, we restore
      the tables from Firestore so deletes / edits do not "come back".
    """
    try:
        with app.app_context():
            db.create_all()

            # First, if Firestore is enabled, try to restore all tables
            # from Firestore when they are empty (fresh Vercel instance).
            _restore_from_firestore_if_empty()

            # Now seed minimal defaults ONLY if still empty AND
            # nothing existed in Firestore for those tables.

            if Banner.query.count() == 0:
                default_banner = Banner(
                    title=f'{app.config["BRAND_NAME"]}',
                    subtitle='Experience luxury with our premium fleet. Best prices, guaranteed!',
                    image='default_banner.jpg',
                    offer_text='SPECIAL OFFER',
                    km_offer=300,
                    price_offer='Rs. 15,000/=',
                    is_active=True
                )
                db.session.add(default_banner)
                db.session.commit()
                logger.info("Default banner added to database")

            if Background.query.count() == 0:
                default_background = Background(
                    image='default_bg.jpg',
                    title=app.config["BRAND_NAME"],
                    description=app.config["BRAND_SLOGAN"]
                )
                db.session.add(default_background)
                db.session.commit()
                logger.info("Default background added to database")

            if Hero.query.count() == 0:
                default_hero = Hero(
                    image='default_hero.jpg',
                    title=app.config["BRAND_NAME"],
                    subtitle='Experience luxury with our premium fleet. Best prices, guaranteed!',
                    overlay_opacity=0.6,
                    overlay_color='0,0,0',
                    is_active=True
                )
                db.session.add(default_hero)
                db.session.commit()
                logger.info("Default hero added to database")
    except Exception:
        logger.exception("Database initialization skipped (config/driver/runtime issue).")


_init_db_if_possible()

# ==============================
# FORM VALIDATION (Sri Lankan Phone Number)
# ==============================
def phone_number_check(form, field):
    """Validate Sri Lankan phone numbers"""
    # Remove spaces and special characters
    phone = re.sub(r'[\s\-\(\)]', '', field.data)
    
    # Sri Lankan phone number patterns:
    patterns = [
        r'^07[0-9]{8}$',  # 0771234567
        r'^7[0-9]{8}$',    # 771234567
        r'^\+947[0-9]{8}$',  # +94771234567
        r'^00947[0-9]{8}$',  # 0094771234567
        r'^0[1-9][0-9]{8}$',  # Landline: 0111234567, 0211234567, etc.
        r'^[1-9][0-9]{8}$'    # Landline without 0: 111234567
    ]
    
    valid = False
    for pattern in patterns:
        if re.match(pattern, phone):
            valid = True
            break
    
    if not valid:
        raise ValidationError('Invalid phone number. Use Sri Lankan format (e.g., 0771234567, 771234567, or +94771234567)')

class BookingForm(FlaskForm):
    customer_name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email Address', validators=[DataRequired(), Email()])
    phone = StringField('Phone Number', validators=[DataRequired(), phone_number_check])
    
    car_model = SelectField('Select Car', choices=[], validators=[DataRequired()])
    
    pickup_date = DateField('Pickup Date', validators=[DataRequired()])
    pickup_time = SelectField('Pickup Time', choices=[
        ('08:00', '08:00 AM'),
        ('09:00', '09:00 AM'),
        ('10:00', '10:00 AM'),
        ('11:00', '11:00 AM'),
        ('12:00', '12:00 PM'),
        ('13:00', '01:00 PM'),
        ('14:00', '02:00 PM'),
        ('15:00', '03:00 PM'),
        ('16:00', '04:00 PM'),
        ('17:00', '05:00 PM'),
        ('18:00', '06:00 PM'),
    ], validators=[DataRequired()], default='10:00')
    
    return_date = DateField('Return Date', validators=[DataRequired()])
    return_time = SelectField('Return Time', choices=[
        ('08:00', '08:00 AM'),
        ('09:00', '09:00 AM'),
        ('10:00', '10:00 AM'),
        ('11:00', '11:00 AM'),
        ('12:00', '12:00 PM'),
        ('13:00', '01:00 PM'),
        ('14:00', '02:00 PM'),
        ('15:00', '03:00 PM'),
        ('16:00', '04:00 PM'),
        ('17:00', '05:00 PM'),
        ('18:00', '06:00 PM'),
    ], validators=[DataRequired()], default='18:00')
    
    id_front = FileField('ID Card (Front)', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Images or PDF only!')
    ])
    id_back = FileField('ID Card (Back)', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Images or PDF only!')
    ])
    license_file = FileField('Driving License', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Images or PDF only!')
    ])
    
    submit = SubmitField('Book Now')

class TrackingSearchForm(FlaskForm):
    tracking_number = StringField('Tracking Number', validators=[DataRequired(), Length(min=6, max=20)])
    submit = SubmitField('Check Status')

class PaymentForm(FlaskForm):
    amount = IntegerField(f'Amount to Pay (Rs.)', validators=[DataRequired(), NumberRange(min=1)])
    payment_method = SelectField('Payment Method', choices=[
        ('bank_transfer', 'Bank Transfer'),
        ('cash', 'Cash')
    ], validators=[DataRequired()])
    payment_reference = StringField('Reference Number', validators=[Optional(), Length(max=100)])
    payment_screenshot = FileField('Payment Screenshot', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png'], 'Images only!')
    ])
    submit = SubmitField('Submit Payment')

class CarForm(FlaskForm):
    name = StringField('Car Name', validators=[DataRequired(), Length(max=50)])
    model = StringField('Model Year', validators=[DataRequired(), Length(max=50)])
    price_per_day = IntegerField(f'Price per Day (Rs.)', validators=[DataRequired(), NumberRange(min=1)])
    km_per_day = IntegerField('KM allowed per day', validators=[DataRequired(), NumberRange(min=1)], default=100)
    category = SelectField('Category', choices=[
        ('sedan', 'Sedan'),
        ('suv', 'SUV'),
        ('luxury', 'Luxury'),
        ('sports', 'Sports'),
        ('economy', 'Economy')
    ], validators=[DataRequired()])
    transmission = SelectField('Transmission', choices=[
        ('automatic', 'Automatic'),
        ('manual', 'Manual')
    ], validators=[DataRequired()])
    seats = IntegerField('Number of Seats', validators=[DataRequired(), NumberRange(min=2, max=10)])
    fuel_type = SelectField('Fuel Type', choices=[
        ('petrol', 'Petrol'),
        ('diesel', 'Diesel'),
        ('electric', 'Electric'),
        ('hybrid', 'Hybrid')
    ], validators=[DataRequired()])
    available = BooleanField('Available for rent', default=True)
    image = FileField('Car Image', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png'], 'Images only!')
    ])
    submit = SubmitField('Save Car')

class BannerForm(FlaskForm):
    title = StringField('Banner Title', validators=[DataRequired(), Length(max=100)])
    subtitle = StringField('Subtitle (Optional)', validators=[Optional(), Length(max=200)])
    offer_text = StringField('Offer Text (e.g., "SPECIAL OFFER")', validators=[Optional(), Length(max=100)])
    km_offer = IntegerField('KM Offer', validators=[Optional(), NumberRange(min=1)], default=300)
    price_offer = StringField('Price Offer (e.g., "Rs. 15,000/=")', validators=[Optional(), Length(max=50)])
    image = FileField('Banner Image', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')
    ])
    is_active = BooleanField('Active Banner', default=True)
    submit = SubmitField('Save Banner')

class BackgroundForm(FlaskForm):
    image = FileField('Website Background Image', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')
    ])
    title = StringField('Background Title (Optional)', validators=[Optional(), Length(max=100)])
    description = StringField('Background Description (Optional)', validators=[Optional(), Length(max=200)])
    submit = SubmitField('Update Background')

class HeroForm(FlaskForm):
    image = FileField('Hero Background Image', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')
    ])
    title = StringField('Hero Title', validators=[Optional(), Length(max=100)])
    subtitle = StringField('Hero Subtitle', validators=[Optional(), Length(max=200)])
    overlay_opacity = StringField('Overlay Opacity (0.0 - 1.0)', validators=[Optional()], default='0.6')
    overlay_color = StringField('Overlay Color (RGB - e.g., 0,0,0 for black)', validators=[Optional()], default='0,0,0')
    is_active = BooleanField('Active Hero', default=True)
    submit = SubmitField('Update Hero')

class AdminEditBookingForm(FlaskForm):
    amount_paid = IntegerField('Amount Paid (Rs.)', validators=[Optional(), NumberRange(min=0)])
    payment_status = SelectField('Payment Status', choices=[
        ('pending', 'Pending'),
        ('partial', 'Partial'),
        ('paid', 'Paid')
    ], validators=[Optional()])
    status = SelectField('Booking Status', choices=[
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ], validators=[Optional()])
    payment_method = StringField('Payment Method', validators=[Optional(), Length(max=50)])
    payment_reference = StringField('Reference Number', validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Admin Notes', validators=[Optional()])
    submit = SubmitField('Update Booking')

class ExpenseForm(FlaskForm):
    date = DateField('Date', validators=[DataRequired()], default=date.today)
    category = SelectField('Category', choices=[
        ('maintenance', 'Maintenance/Repair'),
        ('fuel', 'Fuel'),
        ('insurance', 'Insurance'),
        ('tax', 'Tax'),
        ('salary', 'Salary'),
        ('rent', 'Rent'),
        ('electricity', 'Electricity'),
        ('water', 'Water'),
        ('internet', 'Internet'),
        ('marketing', 'Marketing'),
        ('other', 'Other')
    ], validators=[DataRequired()])
    description = StringField('Description', validators=[DataRequired(), Length(max=200)])
    amount = IntegerField('Amount (Rs.)', validators=[DataRequired(), NumberRange(min=1)])
    payment_method = SelectField('Payment Method', choices=[
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer')
    ], validators=[Optional()])
    receipt = FileField('Receipt (Optional)', validators=[
        Optional(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Images or PDF only!')
    ])
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Add Expense')

# ==============================
# HELPER FUNCTIONS
# ==============================
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please login first', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def update_account_balance():
    """Calculate current account balance from all transactions"""
    last_transaction = AccountTransaction.query.order_by(AccountTransaction.id.desc()).first()
    if last_transaction:
        return last_transaction.balance_after
    return 0

def add_transaction(transaction_type, category, description, amount, reference_id=None, payment_method=None, notes=None):
    """Add a transaction and update balance"""
    current_balance = update_account_balance()
    
    if transaction_type == 'income':
        new_balance = current_balance + amount
    elif transaction_type == 'expense':
        new_balance = current_balance - amount
    else:
        new_balance = current_balance
    
    transaction = AccountTransaction(
        type=transaction_type,
        category=category,
        description=description,
        amount=amount,
        balance_after=new_balance,
        reference_id=reference_id,
        payment_method=payment_method,
        notes=notes
    )
    db.session.add(transaction)
    return transaction

def send_whatsapp_notification(booking):
    """Send WhatsApp message to admin about new booking."""
    if not all([app.config['TWILIO_ACCOUNT_SID'], 
                app.config['TWILIO_AUTH_TOKEN'], 
                app.config['ADMIN_WHATSAPP']]):
        logger.warning("Twilio credentials not fully configured. Skipping WhatsApp.")
        return False
    
    try:
        client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        message_body = f"""
🔔 *NEW BOOKING* 🔔
━━━━━━━━━━━━━━━━━━━
📋 *Tracking:* {booking.tracking_number}
👤 *Customer:* {booking.customer_name}
📧 *Email:* {booking.email}
📞 *Phone:* {booking.phone}
🚗 *Car:* {booking.car_model}
💰 *Total Price:* {format_currency(booking.total_price)}
💵 *Amount Paid:* {format_currency(booking.amount_paid)}
💳 *Balance:* {format_currency(booking.balance_due)}
📅 *Pickup:* {booking.pickup_date} at {booking.pickup_time}
📅 *Return:* {booking.return_date} at {booking.return_time}
⏰ *Booked at:* {current_time}
━━━━━━━━━━━━━━━━━━━
✅ Documents uploaded. Check admin panel.
        """
        client.messages.create(
            body=message_body,
            from_=app.config['TWILIO_WHATSAPP_NUMBER'],
            to=app.config['ADMIN_WHATSAPP']
        )
        logger.info(f"WhatsApp sent for booking {booking.tracking_number}")
        return True
    except Exception as e:
        logger.error(f"Error sending WhatsApp: {e}")
        return False

def send_customer_confirmation(booking):
    """Send confirmation WhatsApp to customer."""
    if not all([app.config['TWILIO_ACCOUNT_SID'], 
                app.config['TWILIO_AUTH_TOKEN']]):
        return False
    
    try:
        client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if booking.status == 'active':
            message_body = f"""
✅ *BOOKING CONFIRMED!* ✅
━━━━━━━━━━━━━━━━━━━
Dear {booking.customer_name},

Your booking has been confirmed and is now ACTIVE!
━━━━━━━━━━━━━━━━━━━
📋 *Tracking:* {booking.tracking_number}
🚗 *Car:* {booking.car_model}
💰 *Total Price:* {format_currency(booking.total_price)}
💵 *Paid:* {format_currency(booking.amount_paid)}
💳 *Balance:* {format_currency(booking.balance_due)}
📅 *Pickup:* {booking.pickup_date} at {booking.pickup_time}
📅 *Return:* {booking.return_date} at {booking.return_time}
⏰ *Confirmed at:* {current_time}
━━━━━━━━━━━━━━━━━━━
📱 Contact us: +94 756656862
Thank you for choosing {app.config['BRAND_NAME']}!
        """
        elif booking.status == 'completed':
            message_body = f"""
✅ *RENTAL COMPLETED* ✅
━━━━━━━━━━━━━━━━━━━
Dear {booking.customer_name},

Thank you for renting with us!
━━━━━━━━━━━━━━━━━━━
📋 *Tracking:* {booking.tracking_number}
🚗 *Car:* {booking.car_model}
💰 *Total Paid:* {format_currency(booking.amount_paid)}
📅 *Returned:* {booking.return_date} at {booking.return_time}
⏰ *Completed at:* {current_time}
━━━━━━━━━━━━━━━━━━━
We hope to see you again soon!
        """
        else:
            return False
        
        # Format phone number for WhatsApp
        phone_digits = re.sub(r'\D', '', booking.phone)
        if phone_digits.startswith('94'):
            to_number = f"whatsapp:+{phone_digits}"
        elif phone_digits.startswith('0'):
            to_number = f"whatsapp:+94{phone_digits[1:]}"
        else:
            to_number = f"whatsapp:+94{phone_digits}"
        
        client.messages.create(
            body=message_body,
            from_=app.config['TWILIO_WHATSAPP_NUMBER'],
            to=to_number
        )
        return True
    except Exception as e:
        logger.error(f"Error sending customer confirmation: {e}")
        return False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def banner_allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['BANNER_ALLOWED_EXTENSIONS']

def hero_allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['HERO_ALLOWED_EXTENSIONS']

def check_file_size(file):
    """Check if file size exceeds limit"""
    if file:
        # Try to get content_length first
        if hasattr(file, 'content_length') and file.content_length:
            return file.content_length <= app.config['MAX_CONTENT_LENGTH']
        
        # Otherwise check by seeking
        try:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)  # Reset file pointer
            return size <= app.config['MAX_CONTENT_LENGTH']
        except:
            return True  # If we can't check, assume it's ok
    return True

# ==============================
# ROUTES
# ==============================
@app.route('/')
def index():
    try:
        form = BookingForm()
        cars = Car.query.filter_by(available=True).all()
        banners = Banner.query.filter_by(is_active=True).all()
        background = Background.query.first() or Background(image='default_bg.jpg')
        hero = Hero.query.filter_by(is_active=True).first() or Hero(image='default_hero.jpg')
        
        # Get the most recent active banner or None
        active_banner = banners[0] if banners else None
        
        # Update car choices with Rs. format
        form.car_model.choices = [(f"{car.name} {car.model}", f"{car.name} {car.model} - {format_currency_simple(car.price_per_day)}/day") for car in cars]
        
        current_date = date.today().strftime('%Y-%m-%d')
        
        html = '''
        <div class="row justify-content-center">
            <div class="col-lg-10">
                <!-- Hero Section with Dynamic Background -->
                <div class="glass-card mb-5" style="position: relative; overflow: hidden; background-image: url('{{ media_url(hero.image, 'hero') }}'); background-size: cover; background-position: center;">
                    <div style="background: rgba({{ hero.overlay_color }}, {{ hero.overlay_opacity }}); padding: 60px 30px; border-radius: 20px;">
                        <div class="text-center text-white">
                            {% if active_banner %}
                                <h1 class="display-4 mb-4" style="text-shadow: 2px 2px 4px rgba(0,0,0,0.5);">{{ active_banner.title }}</h1>
                                <p class="lead" style="text-shadow: 1px 1px 2px rgba(0,0,0,0.5);">{{ active_banner.subtitle }}</p>
                                
                                {% if active_banner.offer_text and active_banner.km_offer and active_banner.price_offer %}
                                <!-- Dynamic Special Offer Banner -->
                                <div class="offer-banner mb-4" style="background: linear-gradient(135deg, #ffd700 0%, #ffa500 100%); color: #000; padding: 20px; border-radius: 15px; max-width: 400px; margin: 0 auto;">
                                    <h2 class="mb-2">🔥 {{ active_banner.offer_text }}</h2>
                                    <div class="d-flex justify-content-center align-items-center">
                                        <div style="font-size: 24px; font-weight: bold;">{{ active_banner.km_offer }} KM</div>
                                        <div style="font-size: 36px; font-weight: 800; margin: 0 20px;">{{ active_banner.price_offer }}</div>
                                    </div>
                                </div>
                                {% else %}
                                <!-- Default Special Offer Banner -->
                                <div class="offer-banner mb-4" style="background: linear-gradient(135deg, #ffd700 0%, #ffa500 100%); color: #000; padding: 20px; border-radius: 15px; max-width: 400px; margin: 0 auto;">
                                    <h2 class="mb-2">🔥 SPECIAL OFFER</h2>
                                    <div class="d-flex justify-content-center align-items-center">
                                        <div style="font-size: 24px; font-weight: bold;">300 KM</div>
                                        <div style="font-size: 36px; font-weight: 800; margin: 0 20px;">Rs. 15,000/=</div>
                                    </div>
                                </div>
                                {% endif %}
                            {% else %}
                                <h1 class="display-4 mb-4" style="text-shadow: 2px 2px 4px rgba(0,0,0,0.5);">{{ hero.title or brand_name }}</h1>
                                <p class="lead" style="text-shadow: 1px 1px 2px rgba(0,0,0,0.5);">{{ hero.subtitle or brand_slogan }}</p>
                                
                                <!-- Default Special Offer Banner -->
                                <div class="offer-banner mb-4" style="background: linear-gradient(135deg, #ffd700 0%, #ffa500 100%); color: #000; padding: 20px; border-radius: 15px; max-width: 400px; margin: 0 auto;">
                                    <h2 class="mb-2">🔥 SPECIAL OFFER</h2>
                                    <div class="d-flex justify-content-center align-items-center">
                                        <div style="font-size: 24px; font-weight: bold;">300 KM</div>
                                        <div style="font-size: 36px; font-weight: 800; margin: 0 20px;">Rs. 15,000/=</div>
                                    </div>
                                </div>
                            {% endif %}
                            
                            <div class="mt-4">
                                <a href="#booking-form" class="btn-gradient btn-lg me-3">
                                    <i class="fas fa-calendar-check"></i> Book Now
                                </a>
                                <a href="https://wa.me/{{ whatsapp_number }}?text=Hello%20{{ brand_name|urlencode }}%2C%20I%27m%20interested%20in%20renting%20a%20car.%20Can%20you%20help%20me%3F" target="_blank" class="btn-success btn-lg" style="background: #25D366; color: white; padding: 15px 30px; border-radius: 50px; text-decoration: none; display: inline-block; font-weight: 600;">
                                    <i class="fab fa-whatsapp"></i> Call Now: 0753394996 / 0756656862
                                </a>
                            </div>
                            
                            <div class="mt-3 text-white">
                                <i class="fas fa-phone"></i> 0753394996 | 0756656862
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Cars Section -->
                <div class="glass-card mb-5" id="cars">
                    <div class="card-header-gradient">
                        <h3>
                            <i class="fas fa-car"></i>
                            Our Premium Fleet
                        </h3>
                    </div>
                    <div class="card-body p-4">
                        <div class="row">
                            {% for car in cars %}
                            <div class="col-md-4 mb-4">
                                <div class="car-card" onclick="selectCar('{{ car.name }} {{ car.model }}', {{ car.price_per_day }})">
                                    <div class="car-image">
                                        {% if car.image and car.image != 'default_car.jpg' %}
                                            <img src="{{ media_url(car.image, 'car') }}" style="width: 100%; height: 100%; object-fit: cover;" alt="{{ car.name }}">
                                        {% else %}
                                            <i class="fas fa-car"></i>
                                        {% endif %}
                                    </div>
                                    <div class="car-details">
                                        <h4>{{ car.name }}</h4>
                                        <p class="text-muted">{{ car.model }} • {{ car.transmission }} • {{ car.seats }} Seats • {{ car.fuel_type }}</p>
                                        <div class="car-price">{{ format_currency_simple(car.price_per_day) }}/day</div>
                                        <small class="text-info">{{ car.km_per_day }} km/day included</small>
                                    </div>
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                </div>
                
                <!-- Booking Form -->
                <div class="glass-card" id="booking-form">
                    <div class="card-header-gradient">
                        <h3>
                            <i class="fas fa-calendar-check"></i>
                            Book Your Car
                        </h3>
                    </div>
                    <div class="card-body p-4">
                        <!-- File Upload Guidelines -->
                        <div class="alert alert-info mb-3">
                            <i class="fas fa-info-circle"></i>
                            <strong>File Upload Guidelines:</strong>
                            <ul class="mb-0 mt-2">
                                <li>Maximum file size: <strong>50MB per file</strong></li>
                                <li>Allowed formats: JPG, PNG, PDF</li>
                                <li>If your files are too large, please compress them before uploading</li>
                                <li>You can use online tools like <a href="https://tinyjpg.com/" target="_blank">TinyJPG</a> or <a href="https://www.ilovepdf.com/compress_pdf" target="_blank">iLovePDF</a> to compress files</li>
                            </ul>
                        </div>
                        
                        <form method="POST" action="{{ url_for('book') }}" enctype="multipart/form-data" id="bookingForm">
                            {{ form.hidden_tag() }}
                            <!-- Cloudinary URLs (used on Vercel to avoid large request bodies) -->
                            <input type="hidden" name="id_front_url" id="id_front_url">
                            <input type="hidden" name="id_back_url" id="id_back_url">
                            <input type="hidden" name="license_url" id="license_url">
                            
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-user"></i> {{ form.customer_name.label.text }}
                                    </label>
                                    {{ form.customer_name(class="form-control" + (' is-invalid' if form.customer_name.errors else ''), placeholder="Enter your full name") }}
                                    {% for error in form.customer_name.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                                
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-envelope"></i> {{ form.email.label.text }}
                                    </label>
                                    {{ form.email(class="form-control" + (' is-invalid' if form.email.errors else ''), placeholder="your@email.com") }}
                                    {% for error in form.email.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                            </div>
                            
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-phone"></i> {{ form.phone.label.text }}
                                    </label>
                                    {{ form.phone(class="form-control" + (' is-invalid' if form.phone.errors else ''), placeholder="0771234567") }}
                                    {% for error in form.phone.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                    <small class="text-muted">Sri Lankan format: 0771234567 or +94771234567</small>
                                </div>
                                
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-car"></i> {{ form.car_model.label.text }}
                                    </label>
                                    <select class="form-select" id="car_model" name="car_model" required>
                                        <option value="">Select a car</option>
                                        {% for car in cars %}
                                        <option value="{{ car.name }} {{ car.model }}" data-price="{{ car.price_per_day }}" data-km="{{ car.km_per_day }}">
                                            {{ car.name }} {{ car.model }} - {{ format_currency_simple(car.price_per_day) }}/day ({{ car.km_per_day }} km/day)
                                        </option>
                                        {% endfor %}
                                    </select>
                                    {% for error in form.car_model.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                            </div>
                            
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-calendar"></i> {{ form.pickup_date.label.text }}
                                    </label>
                                    {{ form.pickup_date(class="form-control", type="date", min=current_date, id="pickup_date") }}
                                    {% for error in form.pickup_date.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                                
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-clock"></i> {{ form.pickup_time.label.text }}
                                    </label>
                                    {{ form.pickup_time(class="form-select", id="pickup_time") }}
                                    {% for error in form.pickup_time.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                            </div>
                            
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-calendar-check"></i> {{ form.return_date.label.text }}
                                    </label>
                                    {{ form.return_date(class="form-control", type="date", min=current_date, id="return_date") }}
                                    {% for error in form.return_date.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                                
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-clock"></i> {{ form.return_time.label.text }}
                                    </label>
                                    {{ form.return_time(class="form-select", id="return_time") }}
                                    {% for error in form.return_time.errors %}
                                        <div class="invalid-feedback">{{ error }}</div>
                                    {% endfor %}
                                </div>
                            </div>
                            
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-id-card"></i> {{ form.id_front.label.text }}
                                    </label>
                                    <div class="file-upload">
                                        {{ form.id_front(class="form-control", id="id_front", onchange="validateFileSize(this)") }}
                                        {% for error in form.id_front.errors %}
                                            <div class="invalid-feedback">{{ error }}</div>
                                        {% endfor %}
                                    </div>
                                    <small class="text-muted">JPG, PNG or PDF (Max 50MB)</small>
                                </div>
                                
                                <div class="col-md-6 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-id-card"></i> {{ form.id_back.label.text }}
                                    </label>
                                    <div class="file-upload">
                                        {{ form.id_back(class="form-control", id="id_back", onchange="validateFileSize(this)") }}
                                        {% for error in form.id_back.errors %}
                                            <div class="invalid-feedback">{{ error }}</div>
                                        {% endfor %}
                                    </div>
                                    <small class="text-muted">JPG, PNG or PDF (Max 50MB)</small>
                                </div>
                            </div>
                            
                            <div class="row">
                                <div class="col-md-12 mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-id-card"></i> {{ form.license_file.label.text }}
                                    </label>
                                    <div class="file-upload">
                                        {{ form.license_file(class="form-control", id="license_file", onchange="validateFileSize(this)") }}
                                        {% for error in form.license_file.errors %}
                                            <div class="invalid-feedback">{{ error }}</div>
                                        {% endfor %}
                                    </div>
                                    <small class="text-muted">JPG, PNG or PDF (Max 50MB)</small>
                                </div>
                            </div>
                            
                            <!-- Price Summary with Immediate Calculation -->
                            <div class="alert alert-info mt-4" id="price-summary" style="display: none;">
                                <div class="row">
                                    <div class="col-md-12">
                                        <h5 class="mb-3"><i class="fas fa-calculator"></i> Price Summary</h5>
                                        <div class="row">
                                            <div class="col-md-6">
                                                <p class="mb-1"><strong>Car:</strong> <span id="selectedCarName"></span></p>
                                                <p class="mb-1"><strong>Price per day:</strong> <span id="pricePerDay">0</span></p>
                                                <p class="mb-1"><strong>Pickup:</strong> <span id="pickupDisplay"></span></p>
                                                <p class="mb-1"><strong>Return:</strong> <span id="returnDisplay"></span></p>
                                                <p class="mb-1"><strong>Total Days:</strong> <span id="totalDays">0</span></p>
                                            </div>
                                            <div class="col-md-6 text-end">
                                                <h4 class="text-primary"><strong>TOTAL: <span id="totalPrice">0</span></strong></h4>
                                                <small id="priceBreakdown" class="text-muted"></small>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Required Payment Notice -->
                            <div class="alert alert-warning mt-3" id="payment-notice" style="display: none;">
                                <i class="fas fa-info-circle"></i>
                                <strong>Payment Required:</strong> After booking, you will need to pay the total amount to confirm your reservation.
                            </div>
                            
                            <div class="text-center mt-4">
                                <button type="submit" class="btn-gradient btn-lg" id="submitBtn" disabled>
                                    <i class="fas fa-check-circle"></i> {{ form.submit.label.text }}
                                </button>
                                <button type="reset" class="btn-outline-gradient btn-lg ms-2">
                                    <i class="fas fa-redo"></i> Reset
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            // File size validation
            function validateFileSize(input) {
                const maxSize = 50 * 1024 * 1024; // 50MB in bytes
                
                if (input.files && input.files[0]) {
                    const fileSize = input.files[0].size;
                    if (fileSize > maxSize) {
                        const sizeInMB = (fileSize / (1024 * 1024)).toFixed(2);
                        const maxSizeInMB = (maxSize / (1024 * 1024)).toFixed(0);
                        
                        // Create or update error message
                        let errorDiv = input.parentElement.querySelector('.file-size-error');
                        if (!errorDiv) {
                            errorDiv = document.createElement('div');
                            errorDiv.className = 'invalid-feedback d-block file-size-error';
                            input.parentElement.appendChild(errorDiv);
                        }
                        errorDiv.textContent = '❌ File size (' + sizeInMB + 'MB) exceeds maximum allowed (' + maxSizeInMB + 'MB). Please compress your file.';
                        
                        // Clear the file input
                        input.value = '';
                        return false;
                    } else {
                        // Remove error message if exists
                        const errorDiv = input.parentElement.querySelector('.file-size-error');
                        if (errorDiv) {
                            errorDiv.remove();
                        }
                    }
                }
                return true;
            }
            
            // Store cars data for price calculation
            const carsData = {
                {% for car in cars %}
                "{{ car.name }} {{ car.model }}": {
                    price: {{ car.price_per_day }},
                    km: {{ car.km_per_day }}
                },
                {% endfor %}
            };
            
            // Car selection function
            function selectCar(carName, price) {
                const select = document.getElementById('car_model');
                for (let option of select.options) {
                    if (option.text.includes(carName)) {
                        option.selected = true;
                        break;
                    }
                }
                calculateTotal();
                
                // Scroll to booking form
                document.getElementById('booking-form').scrollIntoView({ behavior: 'smooth' });
            }
            
            // Price calculator
            function calculateTotal() {
                const pickupDate = document.getElementById('pickup_date');
                const returnDate = document.getElementById('return_date');
                const carSelect = document.getElementById('car_model');
                
                const priceSummary = document.getElementById('price-summary');
                const paymentNotice = document.getElementById('payment-notice');
                const submitBtn = document.getElementById('submitBtn');
                
                const totalDaysEl = document.getElementById('totalDays');
                const pricePerDayEl = document.getElementById('pricePerDay');
                const totalPriceEl = document.getElementById('totalPrice');
                const priceBreakdown = document.getElementById('priceBreakdown');
                const selectedCarName = document.getElementById('selectedCarName');
                const pickupDisplay = document.getElementById('pickupDisplay');
                const returnDisplay = document.getElementById('returnDisplay');
                
                if (!pickupDate || !returnDate || !carSelect) return;
                
                if (pickupDate.value && returnDate.value && carSelect.value) {
                    const pickup = new Date(pickupDate.value);
                    const ret = new Date(returnDate.value);
                    const diffTime = ret - pickup;
                    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
                    
                    if (diffDays > 0) {
                        const selectedOption = carSelect.options[carSelect.selectedIndex];
                        const pricePerDay = parseFloat(selectedOption.dataset.price) || 0;
                        
                        // Calculate total
                        const total = diffDays * pricePerDay;
                        
                        // Update display with proper formatting
                        selectedCarName.textContent = carSelect.value;
                        pricePerDayEl.textContent = 'Rs. ' + pricePerDay.toLocaleString('en-US') + '/=';
                        totalDaysEl.textContent = diffDays;
                        totalPriceEl.innerHTML = '<strong>Rs. ' + total.toLocaleString('en-US') + '/=</strong>';
                        
                        // Format dates for display
                        const pickupDateObj = new Date(pickupDate.value);
                        const returnDateObj = new Date(returnDate.value);
                        pickupDisplay.textContent = pickupDateObj.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
                        returnDisplay.textContent = returnDateObj.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
                        
                        priceBreakdown.textContent = diffDays + ' days × Rs. ' + pricePerDay.toLocaleString('en-US') + '/= = Rs. ' + total.toLocaleString('en-US') + '/=';
                        
                        // Show summary and enable submit
                        priceSummary.style.display = 'block';
                        paymentNotice.style.display = 'block';
                        submitBtn.disabled = false;
                        
                        // Store total for validation
                        document.getElementById('total_price_hidden')?.remove();
                        const hiddenInput = document.createElement('input');
                        hiddenInput.type = 'hidden';
                        hiddenInput.name = 'total_price';
                        hiddenInput.id = 'total_price_hidden';
                        hiddenInput.value = total;
                        document.querySelector('form').appendChild(hiddenInput);
                    } else if (diffDays === 0) {
                        alert('Return date must be after pickup date');
                        priceSummary.style.display = 'none';
                        paymentNotice.style.display = 'none';
                        submitBtn.disabled = true;
                    } else {
                        priceSummary.style.display = 'none';
                        paymentNotice.style.display = 'none';
                        submitBtn.disabled = true;
                    }
                } else {
                    priceSummary.style.display = 'none';
                    paymentNotice.style.display = 'none';
                    submitBtn.disabled = true;
                }
            }
            
            // Event listeners
            document.addEventListener('DOMContentLoaded', function() {
                const pickupDate = document.getElementById('pickup_date');
                const returnDate = document.getElementById('return_date');
                const carSelect = document.getElementById('car_model');
                const pickupTime = document.getElementById('pickup_time');
                const returnTime = document.getElementById('return_time');
                
                // Set min dates
                const today = new Date().toISOString().split('T')[0];
                if (pickupDate) pickupDate.min = today;
                if (returnDate) returnDate.min = today;
                
                // Add event listeners
                if (pickupDate) pickupDate.addEventListener('change', calculateTotal);
                if (returnDate) returnDate.addEventListener('change', calculateTotal);
                if (carSelect) carSelect.addEventListener('change', calculateTotal);
                if (pickupTime) pickupTime.addEventListener('change', calculateTotal);
                if (returnTime) returnTime.addEventListener('change', calculateTotal);
                
                // Update return date min when pickup changes
                if (pickupDate) {
                    pickupDate.addEventListener('change', function() {
                        if (returnDate) {
                            returnDate.min = this.value;
                            if (returnDate.value && new Date(returnDate.value) <= new Date(this.value)) {
                                returnDate.value = '';
                            }
                        }
                        calculateTotal();
                    });
                }
                
                // Add file input event listeners
                const fileInputs = ['id_front', 'id_back', 'license_file'];
                fileInputs.forEach(function(inputId) {
                    const input = document.getElementById(inputId);
                    if (input) {
                        input.addEventListener('change', function() {
                            validateFileSize(this);
                        });
                    }
                });
                
                // Initial calculation if values are pre-selected
                if (pickupDate && returnDate && carSelect && pickupDate.value && returnDate.value && carSelect.value) {
                    calculateTotal();
                }

                // Direct-to-Cloudinary upload (recommended on Vercel)
                const CLOUDINARY_CLOUD_NAME = {{ cloudinary_cloud_name|tojson }};
                const CLOUDINARY_UPLOAD_PRESET = {{ cloudinary_upload_preset|tojson }};
                const CLOUDINARY_FOLDER = {{ cloudinary_folder|tojson }};

                async function uploadToCloudinary(file) {
                    const endpoint = `https://api.cloudinary.com/v1_1/${CLOUDINARY_CLOUD_NAME}/auto/upload`;
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('upload_preset', CLOUDINARY_UPLOAD_PRESET);
                    if (CLOUDINARY_FOLDER) formData.append('folder', CLOUDINARY_FOLDER);
                    const res = await fetch(endpoint, { method: 'POST', body: formData });
                    if (!res.ok) throw new Error('Cloud upload failed');
                    const data = await res.json();
                    if (!data.secure_url) throw new Error('Cloud upload missing URL');
                    return data.secure_url;
                }

                async function maybeUploadAndSetUrl(fileInputId, urlInputId) {
                    if (!CLOUDINARY_CLOUD_NAME || !CLOUDINARY_UPLOAD_PRESET) return;
                    const fileInput = document.getElementById(fileInputId);
                    const urlInput = document.getElementById(urlInputId);
                    if (!fileInput || !urlInput) return;
                    if (!fileInput.files || fileInput.files.length === 0) return;
                    const url = await uploadToCloudinary(fileInput.files[0]);
                    urlInput.value = url;
                    // Clear file so Vercel doesn't receive big body
                    fileInput.value = '';
                }

                const bookingForm = document.getElementById('bookingForm');
                if (bookingForm) {
                    bookingForm.addEventListener('submit', async function (e) {
                        if (!CLOUDINARY_CLOUD_NAME || !CLOUDINARY_UPLOAD_PRESET) return;
                        e.preventDefault();
                        try {
                            await maybeUploadAndSetUrl('id_front', 'id_front_url');
                            await maybeUploadAndSetUrl('id_back', 'id_back_url');
                            await maybeUploadAndSetUrl('license_file', 'license_url');
                            bookingForm.submit();
                        } catch (err) {
                            alert('Upload failed. Please try again (or compress your files).');
                        }
                    });
                }
            });
        </script>
        '''
        
        content = render_template_string(html, 
                                        form=form, 
                                        cars=cars, 
                                        current_date=current_date, 
                                        whatsapp_number=app.config['WHATSAPP_NUMBER'], 
                                        active_banner=active_banner, 
                                        hero=hero,
                                        brand_name=app.config['BRAND_NAME'],
                                        brand_slogan=app.config['BRAND_SLOGAN'],
                                        format_currency_simple=format_currency_simple,
                                        cloudinary_cloud_name=app.config.get("CLOUDINARY_CLOUD_NAME", ""),
                                        cloudinary_upload_preset=app.config.get("CLOUDINARY_UPLOAD_PRESET", ""),
                                        cloudinary_folder=app.config.get("CLOUDINARY_FOLDER", ""))
        return render_template_string(BASE_TEMPLATE, content=content, background=background, brand_name=app.config['BRAND_NAME'], format_currency=format_currency, format_currency_simple=format_currency_simple)
    except Exception as e:
        logger.error(f"Error in index route: {str(e)}")
        logger.error(traceback.format_exc())
        flash('An error occurred loading the page. Please try again.', 'danger')
        return redirect(url_for('simple_index'))

@app.route('/car-image/<filename>')
def car_image(filename):
    """Serve car images from the uploads/cars folder"""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'cars'), filename)

@app.route('/hero-image/<filename>')
def hero_image(filename):
    """Serve hero background images"""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'hero'), filename)

@app.route('/background-image')
def background_image():
    """Serve the current background image from database"""
    background = Background.query.first()
    if background and background.image:
        filename = background.image
    else:
        filename = 'default_bg.jpg'
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds'), filename)

@app.route('/banner-image/<filename>')
def banner_image(filename):
    """Serve banner images"""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'banners'), filename)

@app.route('/simple')
def simple_index():
    """Simple fallback page if main index fails"""
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-car"></i>
                        {{ brand_name }}
                    </h3>
                </div>
                <div class="card-body p-5 text-center">
                    <i class="fas fa-check-circle text-success fa-5x mb-4"></i>
                    <h2>System is running!</h2>
                    <p class="lead">Please use the navigation menu to access:</p>
                    <div class="row mt-4">
                        <div class="col-md-4">
                            <a href="/" class="btn-gradient w-100">Home</a>
                        </div>
                        <div class="col-md-4">
                            <a href="/booking/status" class="btn-gradient w-100">Check Status</a>
                        </div>
                        <div class="col-md-4">
                            <a href="/admin/login" class="btn-gradient w-100">Admin</a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/book', methods=['POST'])
def book():
    try:
        logger.info("Booking form submitted")
        
        form = BookingForm()
        cars = Car.query.filter_by(available=True).all()
        form.car_model.choices = [(f"{car.name} {car.model}", f"{car.name} {car.model} - {format_currency_simple(car.price_per_day)}/day") for car in cars]
        
        if form.validate_on_submit():
            logger.info("Form validation passed")

            # Prefer Cloudinary direct-upload URLs (for Vercel). Fallback to local files.
            id_front_url = (request.form.get("id_front_url") or "").strip()
            id_back_url = (request.form.get("id_back_url") or "").strip()
            license_url = (request.form.get("license_url") or "").strip()

            if id_front_url and id_back_url and license_url:
                id_front_value = id_front_url
                id_back_value = id_back_url
                license_value = license_url
            else:
                # Get files (local fallback)
                id_front_file = request.files['id_front']
                id_back_file = request.files['id_back']
                license_file = request.files['license_file']

                logger.info(f"Files received: {id_front_file.filename}, {id_back_file.filename}, {license_file.filename}")

                # Check if files are empty
                if id_front_file.filename == '' or id_back_file.filename == '' or license_file.filename == '':
                    flash('All files are required', 'danger')
                    return redirect(url_for('index'))

                # Check file extensions
                if not (allowed_file(id_front_file.filename) and allowed_file(id_back_file.filename) and allowed_file(license_file.filename)):
                    flash('Invalid file type. Allowed: jpg, jpeg, png, pdf', 'danger')
                    return redirect(url_for('index'))

                # Check file sizes
                max_size = app.config['MAX_CONTENT_LENGTH']
                for file, name in [(id_front_file, 'ID Front'), (id_back_file, 'ID Back'), (license_file, 'License')]:
                    file.seek(0, os.SEEK_END)
                    size = file.tell()
                    file.seek(0)
                    if size > max_size:
                        flash(f'❌ {name} file too large. Maximum size is {max_size // (1024*1024)}MB', 'danger')
                        return redirect(url_for('index'))

                # Secure filenames
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                id_front_filename = secure_filename(f"{timestamp}_front_{id_front_file.filename}")
                id_back_filename = secure_filename(f"{timestamp}_back_{id_back_file.filename}")
                license_filename = secure_filename(f"{timestamp}_license_{license_file.filename}")

                # Save files
                id_front_path = os.path.join(app.config['UPLOAD_FOLDER'], id_front_filename)
                id_back_path = os.path.join(app.config['UPLOAD_FOLDER'], id_back_filename)
                license_path = os.path.join(app.config['UPLOAD_FOLDER'], license_filename)

                id_front_file.save(id_front_path)
                id_back_file.save(id_back_path)
                license_file.save(license_path)

                logger.info(f"Files saved: {id_front_path}, {id_back_path}, {license_path}")

                id_front_value = id_front_filename
                id_back_value = id_back_filename
                license_value = license_filename
            
            # Get car price (robust parsing of name + model)
            car_model_value = form.car_model.data  # e.g. "Toyota Innova 2024"
            parts = car_model_value.split()
            car = None
            if len(parts) >= 2:
                # Assume last part is model/year, the rest is the name
                selected_model = parts[-1]
                selected_name = " ".join(parts[:-1])
                car = Car.query.filter_by(name=selected_name, model=selected_model).first()
            price_per_day = car.price_per_day if car else 0
            
            # Check if this car is already booked for the selected dates
            pickup = form.pickup_date.data
            ret = form.return_date.data
            overlapping = (
                Booking.query.filter(
                    Booking.car_model == form.car_model.data,
                    Booking.status != 'cancelled',
                    Booking.pickup_date <= ret.strftime('%Y-%m-%d'),
                    Booking.return_date >= pickup.strftime('%Y-%m-%d'),
                )
                .first()
            )
            if overlapping:
                flash(
                    f'❌ This car is already booked between {overlapping.pickup_date} and {overlapping.return_date}. Please choose another car or different dates.',
                    'danger',
                )
                return redirect(url_for('index'))
            
            # Calculate total days and price
            total_days = (ret - pickup).days
            
            # Calculate total
            total_price = total_days * price_per_day
            
            # Generate tracking number
            tracking_number = generate_tracking_number()
            
            # Create booking with time fields and price
            booking = Booking(
                tracking_number=tracking_number,
                customer_name=form.customer_name.data,
                email=form.email.data,
                phone=form.phone.data,
                car_model=form.car_model.data,
                car_price_per_day=price_per_day,
                pickup_date=form.pickup_date.data.strftime('%Y-%m-%d'),
                pickup_time=form.pickup_time.data,
                return_date=form.return_date.data.strftime('%Y-%m-%d'),
                return_time=form.return_time.data,
                total_days=total_days,
                total_price=int(total_price),
                amount_paid=0,
                balance_due=int(total_price),
                payment_status='pending',
                id_front=id_front_value,
                id_back=id_back_value,
                license_file=license_value,
                status='pending'
            )
            
            db.session.add(booking)
            db.session.commit()
            logger.info(f"Booking saved with Tracking: {booking.tracking_number}")
            
            # Send notifications (don't fail if Twilio not configured)
            try:
                send_whatsapp_notification(booking)
            except Exception as e:
                logger.warning(f"WhatsApp notification failed: {e}")
            
            flash(f'✨ Booking successful! Your tracking number is: {tracking_number}. Total amount: {format_currency(total_price)}. Please complete payment.', 'success')
            return redirect(url_for('payment', booking_id=booking.id))
            
        else:
            # Form validation failed
            logger.warning(f"Form validation failed: {form.errors}")
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f'{getattr(form, field).label.text if hasattr(form, field) else field}: {error}', 'danger')
            
    except Exception as e:
        logger.error(f"Error creating booking: {str(e)}")
        logger.error(traceback.format_exc())
        db.session.rollback()
        flash(f'❌ An error occurred: {str(e)}', 'danger')
    
    return redirect(url_for('index'))

@app.route('/payment/<int:booking_id>', methods=['GET', 'POST'])
def payment(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    form = PaymentForm()
    
    if form.validate_on_submit():
        # Handle payment screenshot
        payment_screenshot_filename = None
        if form.payment_screenshot.data and form.payment_screenshot.data.filename:
            payment_file = form.payment_screenshot.data
            if allowed_file(payment_file.filename):
                # Check file size
                if check_file_size(payment_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    payment_screenshot_filename = secure_filename(f"payment_{timestamp}_{payment_file.filename}")
                    payment_path = os.path.join(app.config['UPLOAD_FOLDER'], payment_screenshot_filename)
                    payment_file.save(payment_path)
                else:
                    flash(f'❌ Payment screenshot too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
                    return redirect(url_for('payment', booking_id=booking.id))
        
        # Update payment information
        amount_paid = form.amount.data
        booking.amount_paid += amount_paid
        booking.balance_due = booking.total_price - booking.amount_paid
        booking.payment_method = form.payment_method.data
        booking.payment_reference = form.payment_reference.data
        
        if booking.balance_due <= 0:
            booking.payment_status = 'paid'
            booking.balance_due = 0
        elif booking.amount_paid > 0:
            booking.payment_status = 'partial'
        
        if payment_screenshot_filename:
            booking.payment_screenshot = payment_screenshot_filename
        
        # Add transaction record
        add_transaction(
            transaction_type='income',
            category='booking_payment',
            description=f"Payment for booking {booking.tracking_number}",
            amount=amount_paid,
            reference_id=booking.tracking_number,
            payment_method=form.payment_method.data,
            notes=f"Payment received from {booking.customer_name}"
        )
        
        db.session.commit()
        
        flash(f'Payment of {format_currency(amount_paid)} recorded! Balance due: {format_currency(booking.balance_due)}', 'success')
        return redirect(url_for('payment', booking_id=booking.id))
    
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);">
                    <h3>
                        <i class="fas fa-check-circle"></i>
                        Booking Successful!
                    </h3>
                </div>
                <div class="card-body p-5">
                    <div class="text-center mb-4">
                        <i class="fas fa-check-circle text-success" style="font-size: 80px;"></i>
                    </div>
                    
                    <h2 class="text-center mb-4">Thank You, {{ booking.customer_name }}!</h2>
                    
                    <!-- Tracking Number Highlight -->
                    <div class="alert alert-info text-center p-4 mb-4">
                        <h4 class="mb-2"><i class="fas fa-qrcode"></i> Your Tracking Number:</h4>
                        <div style="font-size: 48px; font-weight: 800; letter-spacing: 5px; background: #f8f9fa; padding: 20px; border-radius: 15px; border: 3px dashed #667eea;">
                            {{ booking.tracking_number }}
                        </div>
                        <p class="mt-3 mb-0"><i class="fas fa-save"></i> Save this number to check your booking status</p>
                    </div>
                    
                    <!-- Price Summary Card -->
                    <div class="card border-0 shadow mb-4">
                        <div class="card-header bg-primary text-white">
                            <h5 class="mb-0"><i class="fas fa-money-bill-wave"></i> Price Summary</h5>
                        </div>
                        <div class="card-body">
                            <div class="row">
                                <div class="col-md-6">
                                    <p><strong>Car:</strong> {{ booking.car_model }}</p>
                                    <p><strong>Price per day:</strong> {{ format_currency(booking.car_price_per_day) }}</p>
                                    <p><strong>Duration:</strong> {{ booking.total_days }} days</p>
                                </div>
                                <div class="col-md-6">
                                    <h3 class="text-primary">Total: {{ format_currency(booking.total_price) }}</h3>
                                    <hr>
                                    <p><strong>Amount Paid:</strong> {{ format_currency(booking.amount_paid) }}</p>
                                    <p><strong>Balance Due:</strong> {{ format_currency(booking.balance_due) }}</p>
                                    <p><strong>Payment Status:</strong> 
                                        <span class="badge {% if booking.payment_status == 'paid' %}bg-success{% elif booking.payment_status == 'partial' %}bg-warning{% else %}bg-danger{% endif %}">
                                            {{ booking.payment_status|upper }}
                                        </span>
                                    </p>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Payment Form -->
                    {% if booking.balance_due > 0 %}
                    <div class="card border-0 shadow mb-4">
                        <div class="card-header bg-warning">
                            <h5 class="mb-0"><i class="fas fa-credit-card"></i> Make Payment</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST" enctype="multipart/form-data">
                                {{ form.hidden_tag() }}
                                
                                <div class="mb-3">
                                    <label class="form-label">Amount to Pay (Rs.)</label>
                                    {{ form.amount(class="form-control", value=booking.balance_due, min=1, max=booking.balance_due) }}
                                    <small class="text-muted">Balance due: {{ format_currency(booking.balance_due) }}</small>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">Payment Method</label>
                                    {{ form.payment_method(class="form-select") }}
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">Reference Number (Optional)</label>
                                    {{ form.payment_reference(class="form-control", placeholder="Enter reference number") }}
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">Payment Screenshot (Optional)</label>
                                    {{ form.payment_screenshot(class="form-control") }}
                                    <small class="text-muted">Max file size: 50MB</small>
                                </div>
                                
                                <button type="submit" class="btn-gradient">
                                    <i class="fas fa-check"></i> Submit Payment
                                </button>
                            </form>
                        </div>
                    </div>
                    {% endif %}
                    
                    <!-- Bank Details (Sri Lanka) -->
                    <div class="card border-0 shadow mb-4">
                        <div class="card-header bg-info text-white">
                            <h5 class="mb-0"><i class="fas fa-university"></i> Bank Transfer Details</h5>
                        </div>
                        <div class="card-body">
                            <div class="alert alert-info">
                                <strong>Bank Name:</strong> {{ bank_name }}<br>
                                <strong>Account Name:</strong> {{ account_name }}<br>
                                <strong>Account Number:</strong> {{ account_number }}<br>
                                <strong>Branch Code:</strong> {{ branch_code }}<br>
                                <strong>Account Type:</strong> {{ account_type }}<br>
                                <strong>Amount to Pay:</strong> {{ format_currency(booking.balance_due) }}
                            </div>
                        </div>
                    </div>
                    
                    <!-- WhatsApp Contact -->
                    <div class="text-center mt-4">
                        <a href="https://wa.me/{{ whatsapp_number }}?text=Hello%20{{ brand_name|urlencode }}%2C%0A%0AI%20just%20made%20a%20booking%20with%20tracking%20number%3A%20*{{ booking.tracking_number }}*%0A%0ABooking%20Details%3A%0A•%20Customer%3A%20{{ booking.customer_name|urlencode }}%0A•%20Car%3A%20{{ booking.car_model|urlencode }}%0A•%20Total%3A%20{{ format_currency(booking.total_price) }}%0A•%20Paid%3A%20{{ format_currency(booking.amount_paid) }}%0A•%20Balance%3A%20{{ format_currency(booking.balance_due) }}%0A•%20Pickup%3A%20{{ booking.pickup_date }}%20at%20{{ booking.pickup_time }}%0A•%20Return%3A%20{{ booking.return_date }}%20at%20{{ booking.return_time }}%0A•%20Booked%20at%3A%20{{ current_time|urlencode }}%0A%0APlease%20confirm%20my%20booking.%20Thank%20you!" target="_blank" class="btn-success btn-lg" style="background: #25D366; color: white; padding: 15px 40px; border-radius: 50px; text-decoration: none; display: inline-block; font-weight: 600;">
                            <i class="fab fa-whatsapp" style="font-size: 24px; margin-right: 10px;"></i>
                            Share on WhatsApp
                        </a>
                    </div>
                    
                    <div class="text-center mt-4">
                        <a href="{{ url_for('index') }}" class="btn-gradient">
                            <i class="fas fa-home"></i> Back to Home
                        </a>
                        <a href="{{ url_for('booking_status') }}" class="btn-outline-gradient">
                            <i class="fas fa-search"></i> Check Status
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(html, 
                                    booking=booking,
                                    form=form,
                                    bank_name=app.config['BANK_NAME'],
                                    account_name=app.config['ACCOUNT_NAME'],
                                    account_number=app.config['ACCOUNT_NUMBER'],
                                    branch_code=app.config['BRANCH_CODE'],
                                    account_type=app.config['ACCOUNT_TYPE'],
                                    whatsapp_number=app.config['WHATSAPP_NUMBER'],
                                    brand_name=app.config['BRAND_NAME'],
                                    format_currency=format_currency,
                                    current_time=current_time)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

# ==============================
# BOOKING STATUS ROUTE
# ==============================
@app.route('/booking/status', methods=['GET', 'POST'])
def booking_status():
    form = TrackingSearchForm()
    
    if request.method == 'POST' and form.validate_on_submit():
        tracking_number = form.tracking_number.data.strip().upper()
        
        # Search for booking by tracking number
        booking = Booking.query.filter_by(tracking_number=tracking_number).first()
        
        if booking:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            html = '''
            <div class="row justify-content-center">
                <div class="col-md-8">
                    <div class="glass-card">
                        <div class="card-header-gradient" style="background: {% if booking.status == 'pending' %}linear-gradient(135deg, #f093fb 0%, #f5576c 100%)
                                                                    {% elif booking.status == 'active' %}linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%)
                                                                    {% elif booking.status == 'completed' %}linear-gradient(135deg, #11998e 0%, #38ef7d 100%)
                                                                    {% else %}linear-gradient(135deg, #ff416c 0%, #ff4b2b 100%){% endif %};">
                            <h3 class="mb-0">
                                <i class="fas fa-info-circle"></i>
                                Booking Details - {{ booking.tracking_number }}
                            </h3>
                        </div>
                        <div class="card-body p-5">
                            <!-- Status Banner -->
                            <div class="text-center mb-4">
                                <span class="status-badge status-{{ booking.status }}" style="font-size: 18px; padding: 12px 30px;">
                                    <i class="fas {% if booking.status == 'pending' %}fa-clock{% elif booking.status == 'active' %}fa-play{% elif booking.status == 'completed' %}fa-check-circle{% else %}fa-times-circle{% endif %}"></i>
                                    {{ booking.status|upper }}
                                </span>
                                <br><small class="text-muted">Last checked: {{ current_time }}</small>
                            </div>
                            
                            <!-- Tracking Number Display -->
                            <div class="alert alert-info text-center p-4 mb-4">
                                <h5><i class="fas fa-qrcode"></i> Tracking Number</h5>
                                <div style="font-size: 32px; font-weight: 700; letter-spacing: 3px;">{{ booking.tracking_number }}</div>
                            </div>
                            
                            <!-- Customer Info Card -->
                            <div class="card border-0 shadow-sm mb-4">
                                <div class="card-body">
                                    <h5 class="card-title text-primary mb-3">
                                        <i class="fas fa-user-circle"></i> Customer Information
                                    </h5>
                                    <div class="row">
                                        <div class="col-md-6">
                                            <p><strong>Name:</strong> {{ booking.customer_name }}</p>
                                            <p><strong>Phone:</strong> {{ booking.phone }}</p>
                                        </div>
                                        <div class="col-md-6">
                                            <p><strong>Email:</strong> {{ booking.email }}</p>
                                            <p><strong>Booking Date:</strong> {{ booking.created_at.strftime('%Y-%m-%d %H:%M') }}</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Rental Details Card -->
                            <div class="card border-0 shadow-sm mb-4">
                                <div class="card-body">
                                    <h5 class="card-title text-primary mb-3">
                                        <i class="fas fa-car"></i> Rental Details
                                    </h5>
                                    <div class="row">
                                        <div class="col-md-6">
                                            <p><strong>Car Model:</strong> {{ booking.car_model }}</p>
                                            <p><strong>Price per day:</strong> {{ format_currency(booking.car_price_per_day) }}</p>
                                            <p><strong>Duration:</strong> {{ booking.total_days }} days</p>
                                            <p><strong>Pickup:</strong> {{ booking.pickup_date }} at {{ booking.pickup_time }}</p>
                                        </div>
                                        <div class="col-md-6">
                                            <p><strong>Return:</strong> {{ booking.return_date }} at {{ booking.return_time }}</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Price Summary Card -->
                            <div class="card border-0 shadow-sm mb-4">
                                <div class="card-body">
                                    <h5 class="card-title text-primary mb-3">
                                        <i class="fas fa-money-bill-wave"></i> Payment Summary
                                    </h5>
                                    <div class="row">
                                        <div class="col-md-6">
                                            <p><strong>Total Price:</strong> {{ format_currency(booking.total_price) }}</p>
                                            <p><strong>Amount Paid:</strong> {{ format_currency(booking.amount_paid) }}</p>
                                            <p><strong>Balance Due:</strong> {{ format_currency(booking.balance_due) }}</p>
                                        </div>
                                        <div class="col-md-6">
                                            <p><strong>Payment Status:</strong> 
                                                <span class="badge {% if booking.payment_status == 'paid' %}bg-success{% elif booking.payment_status == 'partial' %}bg-warning{% else %}bg-danger{% endif %}">
                                                    {{ booking.payment_status|upper }}
                                                </span>
                                            </p>
                                            {% if booking.payment_method %}
                                            <p><strong>Payment Method:</strong> {{ booking.payment_method }}</p>
                                            {% endif %}
                                            {% if booking.payment_reference %}
                                            <p><strong>Reference:</strong> {{ booking.payment_reference }}</p>
                                            {% endif %}
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Status Message -->
                            <div class="alert {% if booking.status == 'pending' %}alert-warning
                                              {% elif booking.status == 'active' %}alert-success
                                              {% elif booking.status == 'completed' %}alert-info
                                              {% else %}alert-danger{% endif %} mt-4">
                                <i class="fas {% if booking.status == 'pending' %}fa-exclamation-circle
                                              {% elif booking.status == 'active' %}fa-check-circle
                                              {% elif booking.status == 'completed' %}fa-flag-checkered
                                              {% else %}fa-times-circle{% endif %}"></i>
                                {% if booking.status == 'pending' %}
                                    Your booking is pending payment confirmation. Balance due: {{ format_currency(booking.balance_due) }}. Please complete the payment and contact us on WhatsApp.
                                {% elif booking.status == 'active' %}
                                    Your rental is currently ACTIVE! Enjoy your ride. Please return the car on {{ booking.return_date }} at {{ booking.return_time }}.
                                {% elif booking.status == 'completed' %}
                                    Your rental has been COMPLETED. Thank you for choosing {{ brand_name }}! We hope to see you again.
                                {% elif booking.status == 'cancelled' %}
                                    This booking has been CANCELLED. Please contact support for more information.
                                {% endif %}
                            </div>
                            
                            <!-- Action Buttons -->
                            <div class="text-center mt-4">
                                {% if booking.balance_due > 0 %}
                                <a href="{{ url_for('payment', booking_id=booking.id) }}" class="btn-gradient btn-lg me-2">
                                    <i class="fas fa-credit-card"></i> Pay Now ({{ format_currency(booking.balance_due) }})
                                </a>
                                {% endif %}
                                
                                <a href="https://wa.me/{{ whatsapp_number }}?text=Hi%2C%20I%20have%20a%20question%20about%20booking%20{{ booking.tracking_number }}" target="_blank" class="btn-outline-gradient btn-lg">
                                    <i class="fab fa-whatsapp"></i> Contact Support
                                </a>
                                
                                <a href="{{ url_for('index') }}" class="btn-gradient btn-lg">
                                    <i class="fas fa-home"></i> Back to Home
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            '''
            content = render_template_string(html, 
                                           booking=booking, 
                                           whatsapp_number=app.config['WHATSAPP_NUMBER'], 
                                           brand_name=app.config['BRAND_NAME'],
                                           format_currency=format_currency,
                                           current_time=current_time)
            return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)
        else:
            flash(f'❌ No booking found with tracking number: {tracking_number}. Please check and try again.', 'danger')
            return redirect(url_for('booking_status'))
    
    # GET request - show search form
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-search"></i>
                        Check Booking Status
                    </h3>
                </div>
                <div class="card-body p-5">
                    <p class="text-muted text-center mb-4">
                        <i class="fas fa-info-circle"></i>
                        Enter your tracking number to check your booking status
                    </p>
                    
                    <form method="POST">
                        {{ form.hidden_tag() }}
                        
                        <div class="mb-4">
                            <label class="form-label">
                                <i class="fas fa-qrcode"></i> Tracking Number
                            </label>
                            {{ form.tracking_number(class="form-control form-control-lg" + (' is-invalid' if form.tracking_number.errors else ''), placeholder="e.g., FR00001") }}
                            {% for error in form.tracking_number.errors %}
                                <div class="invalid-feedback">{{ error }}</div>
                            {% endfor %}
                            <small class="text-muted">Enter your tracking number (format: FR00001)</small>
                        </div>
                        
                        <div class="d-grid gap-2">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-search"></i> Check Status
                            </button>
                        </div>
                    </form>
                    
                    <hr class="my-4">
                    
                    <div class="text-center">
                        <p class="text-muted mb-2">Don't have a booking yet?</p>
                        <a href="{{ url_for('index') }}#booking-form" class="btn-outline-gradient">
                            <i class="fas fa-calendar-plus"></i> Make a Booking
                        </a>
                    </div>
                </div>
            </div>
            
            <!-- Quick Tips -->
            <div class="glass-card mt-4">
                <div class="card-body p-4">
                    <h5><i class="fas fa-lightbulb text-warning"></i> Quick Tips:</h5>
                    <ul class="text-muted mb-0">
                        <li>Your tracking number was provided after booking (format: FR00001)</li>
                        <li>Check your booking status anytime, 24/7</li>
                        <li>Contact us on WhatsApp for immediate assistance</li>
                        <li>Have your tracking number ready when contacting support</li>
                    </ul>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, form=form)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

# ==============================
# ACCOUNT MANAGEMENT ROUTES
# ==============================
@app.route('/admin/account')
@admin_required
def admin_account():
    """Account overview page with balance and transactions"""
    current_balance = update_account_balance()
    transactions = AccountTransaction.query.order_by(AccountTransaction.date.desc()).limit(50).all()
    expenses = Expense.query.order_by(Expense.date.desc()).all()
    
    # Calculate totals
    total_income = db.session.query(db.func.sum(AccountTransaction.amount)).filter(AccountTransaction.type == 'income').scalar() or 0
    total_expenses = db.session.query(db.func.sum(AccountTransaction.amount)).filter(AccountTransaction.type == 'expense').scalar() or 0
    
    html = '''
    <div class="row">
        <div class="col-12">
            <div class="glass-card mb-4">
                <div class="card-header-gradient">
                    <h3 class="mb-0">
                        <i class="fas fa-wallet"></i>
                        {{ brand_name }} - Account Management
                    </h3>
                </div>
            </div>
            
            <!-- Balance Cards -->
            <div class="row mb-4">
                <div class="col-md-4">
                    <div class="stats-card" style="border-left: 5px solid #28a745;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Current Balance</h6>
                                <h2 class="mb-0 text-success">{{ format_currency(current_balance) }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%);">
                                <i class="fas fa-rupee-sign"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-4">
                    <div class="stats-card" style="border-left: 5px solid #007bff;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Total Income</h6>
                                <h2 class="mb-0 text-primary">{{ format_currency(total_income) }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #007bff 0%, #6610f2 100%);">
                                <i class="fas fa-arrow-up"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-4">
                    <div class="stats-card" style="border-left: 5px solid #dc3545;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Total Expenses</h6>
                                <h2 class="mb-0 text-danger">{{ format_currency(total_expenses) }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);">
                                <i class="fas fa-arrow-down"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Action Buttons -->
            <div class="row mb-4">
                <div class="col-12">
                    <div class="glass-card">
                        <div class="card-body p-3">
                            <a href="{{ url_for('admin_add_expense') }}" class="btn-gradient me-2" style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);">
                                <i class="fas fa-minus-circle"></i> Add Expense
                            </a>
                            <a href="{{ url_for('admin_dashboard') }}" class="btn-outline-gradient">
                                <i class="fas fa-arrow-left"></i> Back to Dashboard
                            </a>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Recent Transactions -->
            <div class="glass-card mb-4">
                <div class="card-header-gradient">
                    <h4 class="mb-0">
                        <i class="fas fa-history"></i>
                        Recent Transactions
                    </h4>
                </div>
                <div class="card-body p-4">
                    <div class="table-responsive">
                        <table class="table table-hover">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Type</th>
                                    <th>Category</th>
                                    <th>Description</th>
                                    <th>Amount</th>
                                    <th>Balance After</th>
                                    <th>Reference</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for transaction in transactions %}
                                <tr>
                                    <td>{{ transaction.date.strftime('%Y-%m-%d %H:%M') }}</td>
                                    <td>
                                        <span class="badge {% if transaction.type == 'income' %}bg-success{% else %}bg-danger{% endif %}">
                                            {{ transaction.type|upper }}
                                        </span>
                                    </td>
                                    <td>{{ transaction.category }}</td>
                                    <td>{{ transaction.description }}</td>
                                    <td class="{% if transaction.type == 'income' %}text-success{% else %}text-danger{% endif %}">
                                        {{ format_currency(transaction.amount) }}
                                    </td>
                                    <td>{{ format_currency(transaction.balance_after) }}</td>
                                    <td>{{ transaction.reference_id or '-' }}</td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="7" class="text-center py-4">
                                        <i class="fas fa-exchange-alt fa-3x text-muted mb-3"></i>
                                        <h5>No transactions yet</h5>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Expenses List -->
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);">
                    <h4 class="mb-0">
                        <i class="fas fa-file-invoice"></i>
                        Expenses
                    </h4>
                </div>
                <div class="card-body p-4">
                    <div class="table-responsive">
                        <table class="table table-hover">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Category</th>
                                    <th>Description</th>
                                    <th>Amount</th>
                                    <th>Payment Method</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for expense in expenses %}
                                <tr>
                                    <td>{{ expense.date.strftime('%Y-%m-%d') }}</td>
                                    <td>{{ expense.category }}</td>
                                    <td>{{ expense.description }}</td>
                                    <td class="text-danger">{{ format_currency(expense.amount) }}</td>
                                    <td>{{ expense.payment_method or '-' }}</td>
                                    <td>
                                        <a href="{{ url_for('admin_delete_expense', expense_id=expense.id) }}" class="btn btn-sm btn-danger" onclick="return confirm('Delete this expense?')">
                                            <i class="fas fa-trash"></i>
                                        </a>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="6" class="text-center py-4">
                                        <i class="fas fa-receipt fa-3x text-muted mb-3"></i>
                                        <h5>No expenses recorded</h5>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(html,
                                   current_balance=current_balance,
                                   transactions=transactions,
                                   expenses=expenses,
                                   total_income=total_income,
                                   total_expenses=total_expenses,
                                   brand_name=app.config['BRAND_NAME'],
                                   format_currency=format_currency)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/expense/add', methods=['GET', 'POST'])
@admin_required
def admin_add_expense():
    form = ExpenseForm()
    
    if form.validate_on_submit():
        # Handle receipt upload
        receipt_filename = None
        if form.receipt.data and form.receipt.data.filename:
            receipt_file = form.receipt.data
            if allowed_file(receipt_file.filename):
                if check_file_size(receipt_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    receipt_filename = secure_filename(f"receipt_{timestamp}_{receipt_file.filename}")
                    receipt_path = os.path.join(app.config['UPLOAD_FOLDER'], receipt_filename)
                    receipt_file.save(receipt_path)
                else:
                    flash(f'❌ Receipt too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
                    return redirect(url_for('admin_add_expense'))
        
        # Create expense
        expense = Expense(
            date=form.date.data,
            category=form.category.data,
            description=form.description.data,
            amount=form.amount.data,
            payment_method=form.payment_method.data,
            receipt=receipt_filename,
            notes=form.notes.data
        )
        db.session.add(expense)
        
        # Add transaction
        add_transaction(
            transaction_type='expense',
            category=form.category.data,
            description=form.description.data,
            amount=form.amount.data,
            payment_method=form.payment_method.data,
            notes=form.notes.data
        )
        
        db.session.commit()
        flash(f'Expense of {format_currency(form.amount.data)} added successfully!', 'success')
        return redirect(url_for('admin_account'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);">
                    <h3>
                        <i class="fas fa-minus-circle"></i>
                        {{ brand_name }} - Add Expense
                    </h3>
                </div>
                <div class="card-body p-4">
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.date.label }}</label>
                                {{ form.date(class="form-control", type="date") }}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.category.label }}</label>
                                {{ form.category(class="form-select") }}
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.description.label }}</label>
                            {{ form.description(class="form-control") }}
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.amount.label }}</label>
                                {{ form.amount(class="form-control", type="number") }}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.payment_method.label }}</label>
                                {{ form.payment_method(class="form-select") }}
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.receipt.label }}</label>
                            {{ form.receipt(class="form-control") }}
                            <small class="text-muted">Upload receipt (JPG, PNG, PDF) - Max 50MB</small>
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.notes.label }}</label>
                            {{ form.notes(class="form-control", rows=3) }}
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> Add Expense
                            </button>
                            <a href="{{ url_for('admin_account') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(html, form=form, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/expense/delete/<int:expense_id>')
@admin_required
def admin_delete_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    db.session.delete(expense)
    db.session.commit()
    flash('Expense deleted', 'success')
    return redirect(url_for('admin_account'))

# ==============================
# ADMIN ROUTES (Enhanced with better balance tracking)
# ==============================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == app.config['ADMIN_PASSWORD']:
            session['admin_logged_in'] = True
            flash(f'Welcome back to {app.config["BRAND_NAME"]} Admin!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid password', 'danger')
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-5">
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #333 0%, #000 100%);">
                    <h3>
                        <i class="fas fa-lock"></i>
                        {{ brand_name }} Admin Access
                    </h3>
                </div>
                <div class="card-body p-5">
                    <form method="POST">
                        <div class="mb-4">
                            <label class="form-label">
                                <i class="fas fa-key"></i> Admin Password
                            </label>
                            <input type="password" class="form-control form-control-lg" name="password" required placeholder="Enter admin password">
                        </div>
                        <button type="submit" class="btn-gradient w-100 btn-lg">
                            <i class="fas fa-sign-in-alt"></i> Login
                        </button>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    # Show only non-cancelled bookings in main dashboard
    bookings = Booking.query.filter(Booking.status != 'cancelled').order_by(Booking.created_at.desc()).all()
    banners = Banner.query.order_by(Banner.created_at.desc()).all()
    
    # Calculate stats with enhanced balance tracking
    pending_count = Booking.query.filter_by(status='pending').count()
    active_count = Booking.query.filter_by(status='active').count()
    completed_count = Booking.query.filter_by(status='completed').count()
    total_cars = Car.query.count()
    active_banners = Banner.query.filter_by(is_active=True).count()
    
    # Calculate detailed financial metrics (exclude cancelled bookings)
    total_revenue = (
        db.session.query(db.func.sum(Booking.amount_paid))
        .filter(Booking.status != 'cancelled')
        .scalar()
        or 0
    )
    total_expected = (
        db.session.query(db.func.sum(Booking.total_price))
        .filter(Booking.status != 'cancelled')
        .scalar()
        or 0
    )
    total_pending_payments = (
        db.session.query(db.func.sum(Booking.balance_due))
        .filter(Booking.status != 'cancelled')
        .scalar()
        or 0
    )
    
    # Payment status breakdown (exclude cancelled bookings)
    paid_count = (
        Booking.query.filter(Booking.payment_status == 'paid', Booking.status != 'cancelled')
        .count()
    )
    partial_count = (
        Booking.query.filter(Booking.payment_status == 'partial', Booking.status != 'cancelled')
        .count()
    )
    pending_payment_count = (
        Booking.query.filter(Booking.payment_status == 'pending', Booking.status != 'cancelled')
        .count()
    )
    
    # Balance due breakdown by status
    pending_balance = db.session.query(db.func.sum(Booking.balance_due)).filter(Booking.status == 'pending').scalar() or 0
    active_balance = db.session.query(db.func.sum(Booking.balance_due)).filter(Booking.status == 'active').scalar() or 0
    completed_balance = db.session.query(db.func.sum(Booking.balance_due)).filter(Booking.status == 'completed').scalar() or 0
    
    # Calculate collection rate
    collection_rate = (total_revenue / total_expected * 100) if total_expected > 0 else 0
    
    # Get current account balance
    current_balance = update_account_balance()
    
    # Today overview (simple daily tracking)
    today = date.today()
    # Net income for today: booking payments minus booking refunds
    today_income_payments = (
        db.session.query(db.func.sum(AccountTransaction.amount))
        .filter(
            AccountTransaction.type == 'income',
            AccountTransaction.category == 'booking_payment',
            db.func.date(AccountTransaction.date) == today,
        )
        .scalar()
        or 0
    )
    today_income_refunds = (
        db.session.query(db.func.sum(AccountTransaction.amount))
        .filter(
            AccountTransaction.type == 'expense',
            AccountTransaction.category == 'booking_refund',
            db.func.date(AccountTransaction.date) == today,
        )
        .scalar()
        or 0
    )
    today_income = today_income_payments - today_income_refunds

    # Only count non-cancelled bookings for today
    today_new_bookings = Booking.query.filter(
        db.func.date(Booking.created_at) == today, Booking.status != 'cancelled'
    ).count()
    
    html = '''
    <div class="row">
        <div class="col-12">
            <!-- Business Overview Header -->
            <div class="glass-card mb-4">
                <div class="card-header-gradient d-flex justify-content-between align-items-center">
                    <h3 class="mb-0">
                        <i class="fas fa-chart-line"></i>
                        {{ brand_name }} - Business Dashboard
                    </h3>
                    <span class="badge bg-light text-dark">Last Updated: {{ now().strftime('%Y-%m-%d %H:%M') }}</span>
                </div>
            </div>
            
            <!-- Key Business Metrics -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="stats-card" style="border-left: 5px solid #28a745;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Current Balance</h6>
                                <h2 class="mb-0 text-success">{{ format_currency(current_balance) }}</h2>
                                <small class="text-muted">In account</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%);">
                                <i class="fas fa-rupee-sign"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card" style="border-left: 5px solid #ffc107;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Pending Payments</h6>
                                <h2 class="mb-0 text-warning">{{ format_currency(total_pending_payments) }}</h2>
                                <small class="text-muted">Balance due</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #ffc107 0%, #fd7e14 100%);">
                                <i class="fas fa-clock"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card" style="border-left: 5px solid #17a2b8;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Total Expected</h6>
                                <h2 class="mb-0 text-info">{{ format_currency(total_expected) }}</h2>
                                <small class="text-muted">All bookings</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #17a2b8 0%, #6f42c1 100%);">
                                <i class="fas fa-chart-line"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card" style="border-left: 5px solid #007bff;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Collection Rate</h6>
                                <h2 class="mb-0 text-primary">{{ '{:.1f}'.format(collection_rate) }}%</h2>
                                <small class="text-muted">Revenue / Expected</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #007bff 0%, #6610f2 100%);">
                                <i class="fas fa-percent"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Today Summary (simple view) -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="stats-card" style="border-left: 5px solid #17a2b8;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Today Income</h6>
                                <h2 class="mb-0 text-info">{{ format_currency(today_income) }}</h2>
                                <small class="text-muted">All payments today</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #17a2b8 0%, #6610f2 100%);">
                                <i class="fas fa-calendar-day"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card" style="border-left: 5px solid #6f42c1;">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Today Bookings</h6>
                                <h2 class="mb-0 text-primary">{{ today_new_bookings }}</h2>
                                <small class="text-muted">New customers today</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #6f42c1 0%, #e83e8c 100%);">
                                <i class="fas fa-user-check"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Payment Status Breakdown -->
            <div class="row mb-4">
                <div class="col-md-4">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Paid in Full</h6>
                                <h2 class="mb-0 text-success">{{ paid_count }}</h2>
                                <small>Bookings completed</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%);">
                                <i class="fas fa-check-circle"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-4">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Partial Payments</h6>
                                <h2 class="mb-0 text-warning">{{ partial_count }}</h2>
                                <small>Balance due</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #ffc107 0%, #fd7e14 100%);">
                                <i class="fas fa-clock"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-4">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Pending Payment</h6>
                                <h2 class="mb-0 text-danger">{{ pending_payment_count }}</h2>
                                <small>No payment yet</small>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);">
                                <i class="fas fa-exclamation-circle"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Balance Due by Status -->
            <div class="row mb-4">
                <div class="col-md-12">
                    <div class="glass-card">
                        <div class="card-header-gradient">
                            <h4 class="mb-0">
                                <i class="fas fa-money-bill-wave"></i>
                                Balance Due Analysis
                            </h4>
                        </div>
                        <div class="card-body">
                            <div class="row">
                                <div class="col-md-4">
                                    <div class="text-center p-3">
                                        <h6 class="text-muted">Pending Bookings Balance</h6>
                                        <h3 class="text-warning">{{ format_currency(pending_balance) }}</h3>
                                    </div>
                                </div>
                                <div class="col-md-4">
                                    <div class="text-center p-3">
                                        <h6 class="text-muted">Active Bookings Balance</h6>
                                        <h3 class="text-info">{{ format_currency(active_balance) }}</h3>
                                    </div>
                                </div>
                                <div class="col-md-4">
                                    <div class="text-center p-3">
                                        <h6 class="text-muted">Completed Balance</h6>
                                        <h3 class="text-success">{{ format_currency(completed_balance) }}</h3>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Booking Stats -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Total Bookings</h6>
                                <h2 class="mb-0">{{ bookings|length }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                                <i class="fas fa-car"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Pending</h6>
                                <h2 class="mb-0">{{ pending_count }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
                                <i class="fas fa-clock"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Active</h6>
                                <h2 class="mb-0">{{ active_count }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%);">
                                <i class="fas fa-play"></i>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-3">
                    <div class="stats-card">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted">Completed</h6>
                                <h2 class="mb-0">{{ completed_count }}</h2>
                            </div>
                            <div class="stats-icon" style="background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);">
                                <i class="fas fa-check-circle"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Admin Navigation Buttons -->
            <div class="row mb-4">
                <div class="col-12">
                    <div class="glass-card">
                        <div class="card-body p-3">
                            <a href="{{ url_for('admin_cars') }}" class="btn-gradient me-2">
                                <i class="fas fa-car"></i> Manage Cars ({{ total_cars }})
                            </a>
                            <a href="{{ url_for('admin_banners') }}" class="btn-gradient me-2" style="background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%);">
                                <i class="fas fa-images"></i> Manage Banners ({{ active_banners }} Active)
                            </a>
                            <a href="{{ url_for('admin_background') }}" class="btn-gradient me-2" style="background: linear-gradient(135deg, #36d1dc 0%, #5b86e5 100%);">
                                <i class="fas fa-image"></i> Website Background
                            </a>
                            <a href="{{ url_for('admin_hero') }}" class="btn-gradient me-2" style="background: linear-gradient(135deg, #f12711 0%, #f5af19 100%);">
                                <i class="fas fa-photo-video"></i> Hero Background
                            </a>
                            <a href="{{ url_for('admin_account') }}" class="btn-gradient me-2" style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%);">
                                <i class="fas fa-wallet"></i> Account ({{ format_currency(current_balance) }})
                            </a>
                            <a href="{{ url_for('admin_dashboard') }}" class="btn-outline-gradient">
                                <i class="fas fa-sync-alt"></i> Refresh
                            </a>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Main Dashboard Card -->
            <div class="glass-card">
                <div class="card-header-gradient d-flex justify-content-between align-items-center">
                    <h3 class="mb-0">
                        <i class="fas fa-chart-line"></i>
                        {{ brand_name }} - Booking Management
                    </h3>
                    <div>
                        <span class="badge bg-light text-dark me-2">Total: {{ format_currency(total_expected) }}</span>
                        <span class="badge bg-success me-2">Paid: {{ format_currency(total_revenue) }}</span>
                        <span class="badge bg-warning">Pending: {{ format_currency(total_pending_payments) }}</span>
                    </div>
                </div>
                <div class="card-body p-4">
                    <div class="table-responsive">
                        <table class="table table-hover">
                            <thead>
                                <tr>
                                    <th>Tracking #</th>
                                    <th>Customer</th>
                                    <th>Car</th>
                                    <th>Dates</th>
                                    <th>Total</th>
                                    <th>Paid</th>
                                    <th>Balance</th>
                                    <th>Payment Status</th>
                                    <th>Booking Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for booking in bookings %}
                                <tr>
                                    <td><span class="badge bg-secondary">{{ booking.tracking_number }}</span></td>
                                    <td>
                                        <strong>{{ booking.customer_name }}</strong><br>
                                        <small>{{ booking.phone }}</small>
                                    </td>
                                    <td>{{ booking.car_model }}</td>
                                    <td>
                                        <small>P: {{ booking.pickup_date }}</small><br>
                                        <small>R: {{ booking.return_date }}</small>
                                    </td>
                                    <td><strong>{{ format_currency(booking.total_price) }}</strong></td>
                                    <td class="text-success">{{ format_currency(booking.amount_paid) }}</td>
                                    <td class="{% if booking.balance_due > 0 %}text-warning{% else %}text-success{% endif %}">
                                        {{ format_currency(booking.balance_due) }}
                                    </td>
                                    <td>
                                        <span class="badge {% if booking.payment_status == 'paid' %}bg-success{% elif booking.payment_status == 'partial' %}bg-warning{% else %}bg-danger{% endif %}">
                                            {{ booking.payment_status|upper }}
                                        </span>
                                    </td>
                                    <td>
                                        <span class="status-badge status-{{ booking.status }}">{{ booking.status|upper }}</span>
                                    </td>
                                    <td>
                                        <div class="btn-group" role="group">
                                            <a href="{{ url_for('admin_view_booking', booking_id=booking.id) }}" class="btn btn-sm btn-info" title="View">
                                                <i class="fas fa-eye"></i>
                                            </a>
                                            
                                            <a href="{{ url_for('admin_edit_booking', booking_id=booking.id) }}" class="btn btn-sm btn-warning" title="Edit Payment">
                                                <i class="fas fa-edit"></i>
                                            </a>
                                            
                                            {% if booking.status == 'pending' %}
                                            <a href="{{ url_for('admin_update_status', booking_id=booking.id, status='active') }}" class="btn btn-sm btn-success" title="Activate" onclick="return confirm('Activate this booking?')">
                                                <i class="fas fa-check"></i>
                                            </a>
                                            {% endif %}
                                            
                                            {% if booking.status == 'active' %}
                                            <a href="{{ url_for('admin_update_status', booking_id=booking.id, status='completed') }}" class="btn btn-sm btn-primary" title="Complete" onclick="return confirm('Mark as completed?')">
                                                <i class="fas fa-flag-checkered"></i>
                                            </a>
                                            {% endif %}
                                            
                                            <a href="{{ url_for('admin_update_status', booking_id=booking.id, status='cancelled') }}" class="btn btn-sm btn-danger" title="Cancel" onclick="return confirm('Cancel this booking?')">
                                                <i class="fas fa-times"></i>
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="10" class="text-center py-4">
                                        <i class="fas fa-inbox fa-3x text-muted mb-3"></i>
                                        <h5>No bookings found</h5>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(html,
                                    bookings=bookings,
                                    banners=banners,
                                    pending_count=pending_count,
                                    active_count=active_count,
                                    completed_count=completed_count,
                                    total_cars=total_cars,
                                    active_banners=active_banners,
                                    total_revenue=total_revenue,
                                    total_pending_payments=total_pending_payments,
                                    total_expected=total_expected,
                                    paid_count=paid_count,
                                    partial_count=partial_count,
                                    pending_payment_count=pending_payment_count,
                                    pending_balance=pending_balance,
                                    active_balance=active_balance,
                                    completed_balance=completed_balance,
                                    collection_rate=collection_rate,
                                    current_balance=current_balance,
                                    today_income=today_income,
                                    today_new_bookings=today_new_bookings,
                                    brand_name=app.config['BRAND_NAME'],
                                    format_currency=format_currency,
                                    now=datetime.now)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)


@app.route('/admin/simple-dashboard')
@admin_required
def admin_simple_dashboard():
    """Very simple dashboard: daily income and who still has to pay."""
    today = date.today()
    
    # Today income (net: payments - refunds, same logic as main dashboard)
    today_income_payments = (
        db.session.query(db.func.sum(AccountTransaction.amount))
        .filter(
            AccountTransaction.type == 'income',
            AccountTransaction.category == 'booking_payment',
            db.func.date(AccountTransaction.date) == today,
        )
        .scalar()
        or 0
    )
    today_income_refunds = (
        db.session.query(db.func.sum(AccountTransaction.amount))
        .filter(
            AccountTransaction.type == 'expense',
            AccountTransaction.category == 'booking_refund',
            db.func.date(AccountTransaction.date) == today,
        )
        .scalar()
        or 0
    )
    today_income = today_income_payments - today_income_refunds
    
    # All customers who still have balance to pay (exclude cancelled)
    pending_bookings = (
        Booking.query.filter(Booking.balance_due > 0, Booking.status != 'cancelled')
        .order_by(Booking.pickup_date.asc())
        .all()
    )

    # Overall amounts from all non-cancelled bookings
    total_expected = (
        db.session.query(db.func.sum(Booking.total_price))
        .filter(Booking.status != 'cancelled')
        .scalar()
        or 0
    )
    total_paid = (
        db.session.query(db.func.sum(Booking.amount_paid))
        .filter(Booking.status != 'cancelled')
        .scalar()
        or 0
    )
    total_pending_payments = (
        db.session.query(db.func.sum(Booking.balance_due))
        .filter(Booking.status != 'cancelled')
        .scalar()
        or 0
    )
    
    html = '''
    <div class="row">
        <div class="col-12">
            <div class="glass-card mb-4">
                <div class="card-header-gradient d-flex justify-content-between align-items-center">
                    <h3 class="mb-0">
                        <i class="fas fa-tachometer-alt"></i>
                        Simple Money Dashboard
                    </h3>
                    <small>Easy view for daily income and pending balances</small>
                </div>
            </div>
        </div>
    </div>
    
    <div class="row mb-4">
        <div class="col-md-3">
            <div class="stats-card" style="border-left: 5px solid #28a745;">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="text-muted">Today Income</h6>
                        <h2 class="mb-0 text-success">{{ format_currency(today_income) }}</h2>
                        <small class="text-muted">All payments today</small>
                    </div>
                    <div class="stats-icon" style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%);">
                        <i class="fas fa-calendar-day"></i>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="col-md-3">
            <div class="stats-card" style="border-left: 5px solid #ffc107;">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="text-muted">Customers with Balance</h6>
                        <h2 class="mb-0 text-warning">{{ pending_bookings|length }}</h2>
                        <small class="text-muted">Still need to pay</small>
                    </div>
                    <div class="stats-icon" style="background: linear-gradient(135deg, #ffc107 0%, #fd7e14 100%);">
                        <i class="fas fa-user-clock"></i>
                    </div>
                </div>
            </div>
        </div>

        <div class="col-md-3">
            <div class="stats-card" style="border-left: 5px solid #17a2b8;">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="text-muted">Pending Payments</h6>
                        <h2 class="mb-0 text-info">{{ format_currency(total_pending_payments) }}</h2>
                        <small class="text-muted">Balance to collect</small>
                    </div>
                    <div class="stats-icon" style="background: linear-gradient(135deg, #17a2b8 0%, #6610f2 100%);">
                        <i class="fas fa-clock"></i>
                    </div>
                </div>
            </div>
        </div>

        <div class="col-md-3">
            <div class="stats-card" style="border-left: 5px solid #007bff;">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="text-muted">Total Expected</h6>
                        <h2 class="mb-0 text-primary">{{ format_currency(total_expected) }}</h2>
                        <small class="text-muted">All bookings total</small>
                    </div>
                    <div class="stats-icon" style="background: linear-gradient(135deg, #007bff 0%, #6610f2 100%);">
                        <i class="fas fa-chart-line"></i>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="row">
        <div class="col-12">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h4 class="mb-0">
                        <i class="fas fa-users"></i>
                        Customers Who Still Have to Pay
                    </h4>
                </div>
                <div class="card-body p-3">
                    <div class="table-responsive">
                        <table class="table table-hover table-sm">
                            <thead>
                                <tr>
                                    <th>Tracking #</th>
                                    <th>Customer</th>
                                    <th>Phone</th>
                                    <th>Total</th>
                                    <th>Paid</th>
                                    <th>Balance</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for booking in pending_bookings %}
                                <tr>
                                    <td><span class="badge bg-secondary">{{ booking.tracking_number }}</span></td>
                                    <td>{{ booking.customer_name }}</td>
                                    <td>{{ booking.phone }}</td>
                                    <td>{{ format_currency(booking.total_price) }}</td>
                                    <td class="text-success">{{ format_currency(booking.amount_paid) }}</td>
                                    <td class="text-danger">{{ format_currency(booking.balance_due) }}</td>
                                    <td>
                                        <span class="badge {% if booking.payment_status == 'partial' %}bg-warning{% else %}bg-danger{% endif %}">
                                            {{ booking.payment_status|upper }}
                                        </span>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="7" class="text-center py-4">
                                        <i class="fas fa-smile-beam fa-2x text-success mb-2"></i>
                                        <div>No customers with balance. All paid!</div>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(
        html,
        today_income=today_income,
        pending_bookings=pending_bookings,
        total_expected=total_expected,
        total_paid=total_paid,
        total_pending_payments=total_pending_payments,
        brand_name=app.config['BRAND_NAME'],
        format_currency=format_currency,
    )
    return render_template_string(
        BASE_TEMPLATE,
        content=content,
        brand_name=app.config['BRAND_NAME'],
        format_currency=format_currency,
    )

@app.route('/admin/booking/edit/<int:booking_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    form = AdminEditBookingForm(obj=booking)
    
    if form.validate_on_submit():
        old_amount_paid = booking.amount_paid
        
        # Update amount paid if provided
        if form.amount_paid.data is not None:
            booking.amount_paid = form.amount_paid.data
            booking.balance_due = booking.total_price - booking.amount_paid
            
            # Update payment status based on balance
            if booking.balance_due <= 0:
                booking.payment_status = 'paid'
                booking.balance_due = 0
            elif booking.amount_paid > 0:
                booking.payment_status = 'partial'
            else:
                booking.payment_status = 'pending'
        
        # Update other fields
        if form.payment_status.data:
            booking.payment_status = form.payment_status.data
        if form.status.data:
            booking.status = form.status.data
        if form.payment_method.data:
            booking.payment_method = form.payment_method.data
        if form.payment_reference.data:
            booking.payment_reference = form.payment_reference.data
        if form.notes.data:
            booking.notes = form.notes.data
        
        # If amount paid changed, add transaction
        if form.amount_paid.data and form.amount_paid.data != old_amount_paid:
            add_transaction(
                transaction_type='income',
                category='booking_payment',
                description=f"Payment adjustment for booking {booking.tracking_number}",
                amount=form.amount_paid.data - old_amount_paid,
                reference_id=booking.tracking_number,
                payment_method=booking.payment_method,
                notes=form.notes.data
            )
        
        db.session.commit()
        flash(f'Booking {booking.tracking_number} updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%);">
                    <h3>
                        <i class="fas fa-edit"></i>
                        Edit Booking - {{ booking.tracking_number }}
                    </h3>
                </div>
                <div class="card-body p-5">
                    <!-- Current Booking Info -->
                    <div class="alert alert-info mb-4">
                        <div class="row">
                            <div class="col-md-6">
                                <p><strong>Customer:</strong> {{ booking.customer_name }}</p>
                                <p><strong>Car:</strong> {{ booking.car_model }}</p>
                                <p><strong>Total Price:</strong> {{ format_currency(booking.total_price) }}</p>
                            </div>
                            <div class="col-md-6">
                                <p><strong>Current Paid:</strong> {{ format_currency(booking.amount_paid) }}</p>
                                <p><strong>Current Balance:</strong> {{ format_currency(booking.balance_due) }}</p>
                                <p><strong>Current Payment Status:</strong> 
                                    <span class="badge {% if booking.payment_status == 'paid' %}bg-success{% elif booking.payment_status == 'partial' %}bg-warning{% else %}bg-danger{% endif %}">
                                        {{ booking.payment_status|upper }}
                                    </span>
                                </p>
                            </div>
                        </div>
                    </div>
                    
                    <form method="POST">
                        {{ form.hidden_tag() }}
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.amount_paid.label }}</label>
                                {{ form.amount_paid(class="form-control", value=booking.amount_paid) }}
                                <small class="text-muted">Update the amount paid by customer</small>
                            </div>
                            
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.payment_status.label }}</label>
                                {{ form.payment_status(class="form-select") }}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.status.label }}</label>
                                {{ form.status(class="form-select") }}
                            </div>
                            
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.payment_method.label }}</label>
                                {{ form.payment_method(class="form-control", value=booking.payment_method or '') }}
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.payment_reference.label }}</label>
                            {{ form.payment_reference(class="form-control", value=booking.payment_reference or '') }}
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.notes.label }}</label>
                            {{ form.notes(class="form-control", rows=3) }}
                        </div>
                        
                        <div class="alert alert-warning">
                            <i class="fas fa-info-circle"></i>
                            <strong>Note:</strong> Balance due will be automatically recalculated when you update the amount paid.
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> Update Booking
                            </button>
                            <a href="{{ url_for('admin_dashboard') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(html, form=form, booking=booking, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/hero', methods=['GET', 'POST'])
@admin_required
def admin_hero():
    hero = Hero.query.first()
    if not hero:
        hero = Hero(image='default_hero.jpg')
        db.session.add(hero)
        db.session.commit()
    
    form = HeroForm()
    
    if form.validate_on_submit():
        # Handle image upload
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if hero_allowed_file(image_file.filename):
                if check_file_size(image_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(f"hero_{timestamp}_{image_file.filename}")
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'hero', filename)
                    image_file.save(image_path)
                    hero.image = filename
                    flash('Hero background image updated successfully!', 'success')
                else:
                    flash(f'❌ Hero image too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
        
        if form.title.data:
            hero.title = form.title.data
        if form.subtitle.data:
            hero.subtitle = form.subtitle.data
        if form.overlay_opacity.data:
            try:
                hero.overlay_opacity = float(form.overlay_opacity.data)
            except:
                hero.overlay_opacity = 0.6
        if form.overlay_color.data:
            hero.overlay_color = form.overlay_color.data
        hero.is_active = form.is_active.data
            
        db.session.commit()
        flash('Hero section settings saved!', 'success')
        return redirect(url_for('admin_hero'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #f12711 0%, #f5af19 100%);">
                    <h3>
                        <i class="fas fa-photo-video"></i>
                        {{ brand_name }} - Customize Hero Section Background
                    </h3>
                </div>
                <div class="card-body p-4">
                    <!-- Current Hero Preview -->
                    <div class="text-center mb-4">
                        <h5>Current Hero Background:</h5>
                        <div style="width: 100%; height: 200px; background: url('{{ media_url(hero.image, 'hero') }}') no-repeat center center; background-size: cover; border-radius: 10px; border: 3px solid #fff; box-shadow: 0 5px 15px rgba(0,0,0,0.3); position: relative;">
                            <div style="background: rgba({{ hero.overlay_color }}, {{ hero.overlay_opacity }}); width: 100%; height: 100%; border-radius: 10px; display: flex; align-items: center; justify-content: center;">
                                <div style="color: white; text-shadow: 2px 2px 4px rgba(0,0,0,0.5);">
                                    <h3>{{ hero.title or brand_name }}</h3>
                                    <p>{{ hero.subtitle or brand_slogan }}</p>
                                </div>
                            </div>
                        </div>
                        <p class="mt-2 text-muted">Filename: {{ hero.image }}</p>
                    </div>
                    
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="mb-4">
                            <label class="form-label">{{ form.image.label }}</label>
                            {{ form.image(class="form-control" + (' is-invalid' if form.image.errors else '')) }}
                            <small class="text-muted">Upload a new hero background image. Recommended size: 1920x1080px. Max size: 50MB</small>
                            {% for error in form.image.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.title.label }}</label>
                                {{ form.title(class="form-control", value=hero.title or '') }}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.subtitle.label }}</label>
                                {{ form.subtitle(class="form-control", value=hero.subtitle or '') }}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.overlay_opacity.label }}</label>
                                {{ form.overlay_opacity(class="form-control", value=hero.overlay_opacity) }}
                                <small class="text-muted">0.0 = transparent, 1.0 = solid</small>
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.overlay_color.label }}</label>
                                {{ form.overlay_color(class="form-control", value=hero.overlay_color) }}
                                <small class="text-muted">RGB values (e.g., 0,0,0 for black)</small>
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <div class="form-check">
                                {{ form.is_active(class="form-check-input", checked=hero.is_active) }}
                                <label class="form-check-label">{{ form.is_active.label }}</label>
                            </div>
                        </div>
                        
                        <div class="alert alert-info">
                            <i class="fas fa-info-circle"></i>
                            <strong>Tip:</strong> The hero section is the first thing customers see. Use high-quality images with good contrast for text readability.
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> Update Hero
                            </button>
                            <a href="{{ url_for('admin_dashboard') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-arrow-left"></i> Back to Dashboard
                            </a>
                            <a href="{{ url_for('index') }}" target="_blank" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-eye"></i> Preview
                            </a>
                        </div>
                    </form>
                </div>
            </div>
            
            <!-- Hero Gallery -->
            <div class="glass-card mt-4">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #f12711 0%, #f5af19 100%);">
                    <h4>
                        <i class="fas fa-images"></i>
                        {{ brand_name }} - Hero Background Gallery
                    </h4>
                </div>
                <div class="card-body p-4">
                    <div class="row">
                        {% for file in hero_files %}
                        <div class="col-md-3 mb-3">
                            <div style="height: 100px; background: url('{{ url_for('hero_gallery', filename=file) }}') no-repeat center center; background-size: cover; border-radius: 5px; cursor: pointer; border: 2px solid #fff;" 
                                 onclick="setHero('{{ file }}')"
                                 title="Click to use this background">
                            </div>
                            <p class="small text-center mt-1">{{ file[:20] }}...</p>
                        </div>
                        {% else %}
                        <div class="col-12 text-center py-4">
                            <i class="fas fa-images fa-3x text-muted mb-3"></i>
                            <p>No hero images uploaded yet. Upload your first image above.</p>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        function setHero(filename) {
            if(confirm('Set this as the hero background?')) {
                fetch('/admin/set-hero/' + filename, {
                    method: 'POST',
                }).then(response => {
                    if(response.ok) {
                        location.reload();
                    }
                });
            }
        }
    </script>
    '''
    
    # Get list of hero images
    hero_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'hero')
    hero_files = []
    if os.path.exists(hero_folder):
        hero_files = [f for f in os.listdir(hero_folder) 
                     if os.path.isfile(os.path.join(hero_folder, f)) 
                     and f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
    
    content = render_template_string(html, 
                                   form=form, 
                                   hero=hero, 
                                   hero_files=hero_files,
                                   brand_name=app.config['BRAND_NAME'],
                                   brand_slogan=app.config['BRAND_SLOGAN'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/set-hero/<filename>', methods=['POST'])
@admin_required
def admin_set_hero(filename):
    """Set a specific file as the hero background"""
    hero = Hero.query.first()
    if not hero:
        hero = Hero()
        db.session.add(hero)
    
    hero.image = filename
    db.session.commit()
    flash(f'{app.config["BRAND_NAME"]} hero background set to {filename}', 'success')
    return '', 200

@app.route('/hero-gallery/<filename>')
def hero_gallery(filename):
    """Serve hero images for gallery preview"""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'hero'), filename)

@app.route('/admin/background', methods=['GET', 'POST'])
@admin_required
def admin_background():
    background = Background.query.first()
    if not background:
        background = Background(image='default_bg.jpg')
        db.session.add(background)
        db.session.commit()
    
    form = BackgroundForm()
    
    if form.validate_on_submit():
        # Handle image upload
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if allowed_file(image_file.filename):
                if check_file_size(image_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(f"bg_{timestamp}_{image_file.filename}")
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds', filename)
                    image_file.save(image_path)
                    background.image = filename
                    flash('Background image updated successfully!', 'success')
                else:
                    flash(f'❌ Background image too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
        
        if form.title.data:
            background.title = form.title.data
        if form.description.data:
            background.description = form.description.data
            
        db.session.commit()
        flash('Background settings saved!', 'success')
        return redirect(url_for('admin_background'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #36d1dc 0%, #5b86e5 100%);">
                    <h3>
                        <i class="fas fa-image"></i>
                        {{ brand_name }} - Change Website Background
                    </h3>
                </div>
                <div class="card-body p-4">
                    <!-- Current Background Preview -->
                    <div class="text-center mb-4">
                        <h5>Current Background:</h5>
                        <div style="width: 100%; height: 200px; background: url('{{ url_for('background_image') }}') no-repeat center center; background-size: cover; border-radius: 10px; border: 3px solid #fff; box-shadow: 0 5px 15px rgba(0,0,0,0.3);">
                        </div>
                        <p class="mt-2 text-muted">Filename: {{ background.image }}</p>
                    </div>
                    
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="mb-4">
                            <label class="form-label">{{ form.image.label }}</label>
                            {{ form.image(class="form-control" + (' is-invalid' if form.image.errors else '')) }}
                            <small class="text-muted">Upload a new background image. Allowed: JPG, PNG, GIF. Max size: 50MB</small>
                            {% for error in form.image.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.title.label }}</label>
                            {{ form.title(class="form-control", value=background.title or '') }}
                            <small class="text-muted">Optional: Title for the background</small>
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.description.label }}</label>
                            {{ form.description(class="form-control", value=background.description or '') }}
                            <small class="text-muted">Optional: Description for the background</small>
                        </div>
                        
                        <div class="alert alert-info">
                            <i class="fas fa-info-circle"></i>
                            <strong>Tip:</strong> For best results, use high-quality images with dark or muted tones so that white text remains readable. Recommended size: 1920x1080px or larger.
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> Update Background
                            </button>
                            <a href="{{ url_for('admin_dashboard') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-arrow-left"></i> Back to Dashboard
                            </a>
                            <a href="{{ url_for('index') }}" target="_blank" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-eye"></i> Preview
                            </a>
                        </div>
                    </form>
                </div>
            </div>
            
            <!-- Background Gallery -->
            <div class="glass-card mt-4">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #36d1dc 0%, #5b86e5 100%);">
                    <h4>
                        <i class="fas fa-images"></i>
                        {{ brand_name }} - Background Gallery
                    </h4>
                </div>
                <div class="card-body p-4">
                    <div class="row">
                        {% for file in background_files %}
                        <div class="col-md-3 mb-3">
                            <div style="height: 100px; background: url('{{ url_for('background_gallery', filename=file) }}') no-repeat center center; background-size: cover; border-radius: 5px; cursor: pointer; border: 2px solid #fff;" 
                                 onclick="setBackground('{{ file }}')"
                                 title="Click to use this background">
                            </div>
                            <p class="small text-center mt-1">{{ file[:20] }}...</p>
                        </div>
                        {% else %}
                        <div class="col-12 text-center py-4">
                            <i class="fas fa-images fa-3x text-muted mb-3"></i>
                            <p>No background images uploaded yet. Upload your first image above.</p>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        function setBackground(filename) {
            if(confirm('Set this as the website background?')) {
                fetch('/admin/set-background/' + filename, {
                    method: 'POST',
                }).then(response => {
                    if(response.ok) {
                        location.reload();
                    }
                });
            }
        }
    </script>
    '''
    
    # Get list of background images
    background_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds')
    background_files = []
    if os.path.exists(background_folder):
        background_files = [f for f in os.listdir(background_folder) 
                           if os.path.isfile(os.path.join(background_folder, f)) 
                           and f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
    
    content = render_template_string(html, 
                                   form=form, 
                                   background=background, 
                                   background_files=background_files,
                                   brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/set-background/<filename>', methods=['POST'])
@admin_required
def admin_set_background(filename):
    """Set a specific file as the background"""
    background = Background.query.first()
    if not background:
        background = Background()
        db.session.add(background)
    
    background.image = filename
    db.session.commit()
    flash(f'{app.config["BRAND_NAME"]} background set to {filename}', 'success')
    return '', 200

@app.route('/background-gallery/<filename>')
def background_gallery(filename):
    """Serve background images for gallery preview"""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds'), filename)

@app.route('/admin/banners')
@admin_required
def admin_banners():
    banners = Banner.query.order_by(Banner.created_at.desc()).all()
    
    html = '''
    <div class="row">
        <div class="col-12">
            <div class="glass-card">
                <div class="card-header-gradient d-flex justify-content-between align-items-center">
                    <h3 class="mb-0">
                        <i class="fas fa-images"></i>
                        {{ brand_name }} - Manage Homepage Banners
                    </h3>
                    <a href="{{ url_for('admin_add_banner') }}" class="btn btn-light">
                        <i class="fas fa-plus"></i> Add New Banner
                    </a>
                </div>
                <div class="card-body p-4">
                    <div class="alert alert-info">
                        <i class="fas fa-info-circle"></i>
                        The active banner will automatically display on the homepage. You can create multiple banners and activate only one at a time.
                    </div>
                    
                    <div class="table-responsive">
                        <table class="table table-hover">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Preview</th>
                                    <th>Title</th>
                                    <th>Offer</th>
                                    <th>Status</th>
                                    <th>Created</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for banner in banners %}
                                <tr>
                                    <td>{{ banner.id }}</td>
                                    <td>
                                        {% if banner.image and banner.image != 'default_banner.jpg' %}
                                            <img src="{{ media_url(banner.image, 'banner') }}" style="width: 60px; height: 40px; object-fit: cover; border-radius: 5px;">
                                        {% else %}
                                            <i class="fas fa-image fa-2x text-muted"></i>
                                        {% endif %}
                                    </td>
                                    <td>
                                        <strong>{{ banner.title }}</strong><br>
                                        <small>{{ banner.subtitle }}</small>
                                    </td>
                                    <td>
                                        {% if banner.offer_text %}
                                            <span class="badge bg-warning">{{ banner.offer_text }}</span><br>
                                            <small>{{ banner.km_offer }} KM for {{ banner.price_offer }}</small>
                                        {% else %}
                                            <span class="badge bg-secondary">No Offer</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if banner.is_active %}
                                            <span class="status-badge status-active">ACTIVE</span>
                                        {% else %}
                                            <span class="status-badge status-cancelled">INACTIVE</span>
                                        {% endif %}
                                    </td>
                                    <td>{{ banner.created_at.strftime('%Y-%m-%d') }}</td>
                                    <td>
                                        <div class="btn-group" role="group">
                                            <a href="{{ url_for('admin_edit_banner', banner_id=banner.id) }}" class="btn btn-sm btn-warning" title="Edit">
                                                <i class="fas fa-edit"></i>
                                            </a>
                                            <a href="{{ url_for('admin_toggle_banner', banner_id=banner.id) }}" class="btn btn-sm btn-info" title="Toggle Active/Inactive">
                                                <i class="fas fa-sync-alt"></i>
                                            </a>
                                            <a href="{{ url_for('admin_delete_banner', banner_id=banner.id) }}" class="btn btn-sm btn-danger" title="Delete" onclick="return confirm('Are you sure you want to delete this banner?')">
                                                <i class="fas fa-trash"></i>
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="7" class="text-center py-4">
                                        <i class="fas fa-images fa-3x text-muted mb-3"></i>
                                        <h5>No banners found. Add your first banner!</h5>
                                        <a href="{{ url_for('admin_add_banner') }}" class="btn-gradient mt-3">
                                            <i class="fas fa-plus"></i> Add Banner
                                        </a>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, banners=banners, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/banner/add', methods=['GET', 'POST'])
@admin_required
def admin_add_banner():
    form = BannerForm()
    
    if form.validate_on_submit():
        # Handle image upload
        filename = 'default_banner.jpg'
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if banner_allowed_file(image_file.filename):
                if check_file_size(image_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(f"banner_{timestamp}_{image_file.filename}")
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'banners', filename)
                    image_file.save(image_path)
                else:
                    flash(f'❌ Banner image too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
                    return redirect(url_for('admin_add_banner'))
        
        # If this banner is active, deactivate all other banners
        if form.is_active.data:
            Banner.query.update({Banner.is_active: False})
        
        banner = Banner(
            title=form.title.data,
            subtitle=form.subtitle.data,
            image=filename,
            offer_text=form.offer_text.data,
            km_offer=form.km_offer.data,
            price_offer=form.price_offer.data,
            is_active=form.is_active.data
        )
        db.session.add(banner)
        db.session.commit()
        
        flash('Banner added successfully!', 'success')
        return redirect(url_for('admin_banners'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-plus-circle"></i>
                        {{ brand_name }} - Add New Banner
                    </h3>
                </div>
                <div class="card-body p-4">
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.title.label }}</label>
                            {{ form.title(class="form-control" + (' is-invalid' if form.title.errors else ''), placeholder="e.g., Premium Car Rental in Sri Lanka") }}
                            {% for error in form.title.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.subtitle.label }}</label>
                            {{ form.subtitle(class="form-control" + (' is-invalid' if form.subtitle.errors else ''), placeholder="e.g., Experience luxury with our premium fleet") }}
                            {% for error in form.subtitle.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.image.label }}</label>
                            {{ form.image(class="form-control" + (' is-invalid' if form.image.errors else '')) }}
                            <small class="text-muted">Leave empty to use default banner. Allowed: JPG, PNG, GIF. Max size: 50MB</small>
                            {% for error in form.image.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <hr>
                        <h5>Offer Section (Optional)</h5>
                        
                        <div class="row">
                            <div class="col-md-4 mb-3">
                                <label class="form-label">{{ form.offer_text.label }}</label>
                                {{ form.offer_text(class="form-control" + (' is-invalid' if form.offer_text.errors else ''), placeholder="SPECIAL OFFER") }}
                                {% for error in form.offer_text.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-4 mb-3">
                                <label class="form-label">{{ form.km_offer.label }}</label>
                                {{ form.km_offer(class="form-control" + (' is-invalid' if form.km_offer.errors else ''), type="number", placeholder="300") }}
                                {% for error in form.km_offer.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-4 mb-3">
                                <label class="form-label">{{ form.price_offer.label }}</label>
                                {{ form.price_offer(class="form-control" + (' is-invalid' if form.price_offer.errors else ''), placeholder="Rs. 15,000/=") }}
                                {% for error in form.price_offer.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <div class="form-check">
                                {{ form.is_active(class="form-check-input") }}
                                <label class="form-check-label">{{ form.is_active.label }}</label>
                                <small class="text-muted d-block">If checked, this banner will be active and all others will be deactivated.</small>
                            </div>
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> {{ form.submit.label.text }}
                            </button>
                            <a href="{{ url_for('admin_banners') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, form=form, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/banner/edit/<int:banner_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_banner(banner_id):
    banner = Banner.query.get_or_404(banner_id)
    form = BannerForm(obj=banner)
    
    if form.validate_on_submit():
        # If this banner is being set to active, deactivate all others
        if form.is_active.data and not banner.is_active:
            Banner.query.update({Banner.is_active: False})
        
        banner.title = form.title.data
        banner.subtitle = form.subtitle.data
        banner.offer_text = form.offer_text.data
        banner.km_offer = form.km_offer.data
        banner.price_offer = form.price_offer.data
        banner.is_active = form.is_active.data
        
        # Handle image upload if new image provided
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if banner_allowed_file(image_file.filename):
                if check_file_size(image_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(f"banner_{timestamp}_{image_file.filename}")
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'banners', filename)
                    image_file.save(image_path)
                    banner.image = filename
                else:
                    flash(f'❌ Banner image too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
                    return redirect(url_for('admin_edit_banner', banner_id=banner_id))
        
        db.session.commit()
        flash('Banner updated successfully!', 'success')
        return redirect(url_for('admin_banners'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-edit"></i>
                        {{ brand_name }} - Edit Banner: {{ banner.title }}
                    </h3>
                </div>
                <div class="card-body p-4">
                    {% if banner.image and banner.image != 'default_banner.jpg' %}
                    <div class="text-center mb-4">
                        <img src="{{ media_url(banner.image, 'banner') }}" style="max-width: 100%; max-height: 200px; border-radius: 10px;">
                    </div>
                    {% endif %}
                    
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.title.label }}</label>
                            {{ form.title(class="form-control" + (' is-invalid' if form.title.errors else '')) }}
                            {% for error in form.title.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.subtitle.label }}</label>
                            {{ form.subtitle(class="form-control" + (' is-invalid' if form.subtitle.errors else '')) }}
                            {% for error in form.subtitle.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">{{ form.image.label }}</label>
                            {{ form.image(class="form-control" + (' is-invalid' if form.image.errors else '')) }}
                            <small class="text-muted">Leave empty to keep current image. Max size: 50MB</small>
                            {% for error in form.image.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                        </div>
                        
                        <hr>
                        <h5>Offer Section (Optional)</h5>
                        
                        <div class="row">
                            <div class="col-md-4 mb-3">
                                <label class="form-label">{{ form.offer_text.label }}</label>
                                {{ form.offer_text(class="form-control" + (' is-invalid' if form.offer_text.errors else '')) }}
                                {% for error in form.offer_text.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-4 mb-3">
                                <label class="form-label">{{ form.km_offer.label }}</label>
                                {{ form.km_offer(class="form-control" + (' is-invalid' if form.km_offer.errors else ''), type="number") }}
                                {% for error in form.km_offer.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-4 mb-3">
                                <label class="form-label">{{ form.price_offer.label }}</label>
                                {{ form.price_offer(class="form-control" + (' is-invalid' if form.price_offer.errors else '')) }}
                                {% for error in form.price_offer.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <div class="form-check">
                                {{ form.is_active(class="form-check-input") }}
                                <label class="form-check-label">{{ form.is_active.label }}</label>
                                <small class="text-muted d-block">If checked, this banner will be active and all others will be deactivated.</small>
                            </div>
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> Update Banner
                            </button>
                            <a href="{{ url_for('admin_banners') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, form=form, banner=banner, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/banner/toggle/<int:banner_id>')
@admin_required
def admin_toggle_banner(banner_id):
    banner = Banner.query.get_or_404(banner_id)
    
    if not banner.is_active:
        # Activating this banner - deactivate all others
        Banner.query.update({Banner.is_active: False})
        banner.is_active = True
        flash(f'Banner "{banner.title}" is now active!', 'success')
    else:
        # Deactivating this banner
        banner.is_active = False
        flash(f'Banner "{banner.title}" deactivated.', 'warning')
    
    db.session.commit()
    return redirect(url_for('admin_banners'))

@app.route('/admin/banner/delete/<int:banner_id>')
@admin_required
def admin_delete_banner(banner_id):
    banner = Banner.query.get_or_404(banner_id)
    db.session.delete(banner)
    db.session.commit()
    flash(f'Banner "{banner.title}" deleted.', 'success')
    return redirect(url_for('admin_banners'))

@app.route('/admin/cars')
@admin_required
def admin_cars():
    cars = Car.query.order_by(Car.name).all()
    html = '''
    <div class="row">
        <div class="col-12">
            <div class="glass-card">
                <div class="card-header-gradient d-flex justify-content-between align-items-center">
                    <h3 class="mb-0">
                        <i class="fas fa-car"></i>
                        {{ brand_name }} - Manage Cars
                    </h3>
                    <a href="{{ url_for('admin_add_car') }}" class="btn btn-light">
                        <i class="fas fa-plus"></i> Add New Car
                    </a>
                </div>
                <div class="card-body p-4">
                    <div class="alert alert-info">
                        <i class="fas fa-info-circle"></i>
                        Car images are stored in the uploads/cars folder. Upload images when adding or editing cars. Max file size: 50MB
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Image</th>
                                    <th>Name</th>
                                    <th>Model</th>
                                    <th>Price/Day</th>
                                    <th>KM/Day</th>
                                    <th>Category</th>
                                    <th>Seats</th>
                                    <th>Transmission</th>
                                    <th>Available</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for car in cars %}
                                <tr>
                                    <td>{{ car.id }}</td>
                                    <td>
                                        {% if car.image and car.image != 'default_car.jpg' %}
                                            <img src="{{ media_url(car.image, 'car') }}" style="width: 60px; height: 40px; object-fit: cover; border-radius: 5px;">
                                        {% else %}
                                            <i class="fas fa-car fa-2x text-muted"></i>
                                        {% endif %}
                                    </td>
                                    <td>{{ car.name }}</td>
                                    <td>{{ car.model }}</td>
                                    <td>{{ format_currency_simple(car.price_per_day) }}</td>
                                    <td>{{ car.km_per_day }} km</td>
                                    <td>{{ car.category }}</td>
                                    <td>{{ car.seats }}</td>
                                    <td>{{ car.transmission }}</td>
                                    <td>
                                        {% if car.available %}
                                            <span class="badge bg-success">Yes</span>
                                        {% else %}
                                            <span class="badge bg-danger">No</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        <a href="{{ url_for('admin_edit_car', car_id=car.id) }}" class="btn btn-sm btn-warning">
                                            <i class="fas fa-edit"></i>
                                        </a>
                                        <a href="{{ url_for('admin_delete_car', car_id=car.id) }}" class="btn btn-sm btn-danger" onclick="return confirm('Are you sure you want to delete this car?')">
                                            <i class="fas fa-trash"></i>
                                        </a>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="11" class="text-center py-4">
                                        <i class="fas fa-car-side fa-3x text-muted mb-3"></i>
                                        <h5>No cars found. Add your first car!</h5>
                                        <a href="{{ url_for('admin_add_car') }}" class="btn-gradient mt-3">
                                            <i class="fas fa-plus"></i> Add Car
                                        </a>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, cars=cars, brand_name=app.config['BRAND_NAME'], format_currency_simple=format_currency_simple)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/car/add', methods=['GET', 'POST'])
@admin_required
def admin_add_car():
    form = CarForm()
    if form.validate_on_submit():
        # Handle image upload if present
        filename = 'default_car.jpg'
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if image_file.filename:
                # Check file extension
                if allowed_file(image_file.filename):
                    if check_file_size(image_file):
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        filename = secure_filename(f"car_{timestamp}_{image_file.filename}")
                        # Save to cars subfolder
                        image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'cars', filename)
                        image_file.save(image_path)
                        logger.info(f"Car image saved: {image_path}")
                    else:
                        flash(f'❌ Car image too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
                        return redirect(url_for('admin_add_car'))
        
        car = Car(
            name=form.name.data,
            model=form.model.data,
            price_per_day=form.price_per_day.data,
            km_per_day=form.km_per_day.data,
            category=form.category.data,
            transmission=form.transmission.data,
            seats=form.seats.data,
            fuel_type=form.fuel_type.data,
            available=form.available.data,
            image=filename
        )
        db.session.add(car)
        db.session.commit()
        flash(f'Car {car.name} {car.model} added successfully!', 'success')
        return redirect(url_for('admin_cars'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-plus-circle"></i>
                        {{ brand_name }} - Add New Car
                    </h3>
                </div>
                <div class="card-body p-4">
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.name.label }}</label>
                                {{ form.name(class="form-control" + (' is-invalid' if form.name.errors else '')) }}
                                {% for error in form.name.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.model.label }}</label>
                                {{ form.model(class="form-control" + (' is-invalid' if form.model.errors else '')) }}
                                {% for error in form.model.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.price_per_day.label }}</label>
                                {{ form.price_per_day(class="form-control" + (' is-invalid' if form.price_per_day.errors else ''), type="number") }}
                                {% for error in form.price_per_day.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.km_per_day.label }}</label>
                                {{ form.km_per_day(class="form-control" + (' is-invalid' if form.km_per_day.errors else ''), type="number") }}
                                {% for error in form.km_per_day.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.category.label }}</label>
                                {{ form.category(class="form-select" + (' is-invalid' if form.category.errors else '')) }}
                                {% for error in form.category.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.transmission.label }}</label>
                                {{ form.transmission(class="form-select" + (' is-invalid' if form.transmission.errors else '')) }}
                                {% for error in form.transmission.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.seats.label }}</label>
                                {{ form.seats(class="form-control" + (' is-invalid' if form.seats.errors else ''), type="number") }}
                                {% for error in form.seats.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.fuel_type.label }}</label>
                                {{ form.fuel_type(class="form-select" + (' is-invalid' if form.fuel_type.errors else '')) }}
                                {% for error in form.fuel_type.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <div class="form-check">
                                    {{ form.available(class="form-check-input") }}
                                    <label class="form-check-label">{{ form.available.label }}</label>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.image.label }}</label>
                                {{ form.image(class="form-control" + (' is-invalid' if form.image.errors else '')) }}
                                <small class="text-muted">Recommended size: 800x600px. Allowed: JPG, PNG. Max size: 50MB</small>
                                {% for error in form.image.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> {{ form.submit.label.text }}
                            </button>
                            <a href="{{ url_for('admin_cars') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, form=form, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/car/edit/<int:car_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_car(car_id):
    car = Car.query.get_or_404(car_id)
    form = CarForm(obj=car)
    
    if form.validate_on_submit():
        # Update car fields
        car.name = form.name.data
        car.model = form.model.data
        car.price_per_day = form.price_per_day.data
        car.km_per_day = form.km_per_day.data
        car.category = form.category.data
        car.transmission = form.transmission.data
        car.seats = form.seats.data
        car.fuel_type = form.fuel_type.data
        car.available = form.available.data
        
        # Handle image upload if new image provided
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if allowed_file(image_file.filename):
                if check_file_size(image_file):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(f"car_{timestamp}_{image_file.filename}")
                    # Save to cars subfolder
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'cars', filename)
                    image_file.save(image_path)
                    car.image = filename
                    logger.info(f"Car image updated: {image_path}")
                else:
                    flash(f'❌ Car image too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] // (1024*1024)}MB', 'danger')
                    return redirect(url_for('admin_edit_car', car_id=car_id))
        
        db.session.commit()
        flash(f'Car {car.name} {car.model} updated successfully!', 'success')
        return redirect(url_for('admin_cars'))
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-edit"></i>
                        {{ brand_name }} - Edit Car: {{ car.name }} {{ car.model }}
                    </h3>
                </div>
                <div class="card-body p-4">
                    {% if car.image and car.image != 'default_car.jpg' %}
                    <div class="text-center mb-4">
                        <img src="{{ media_url(car.image, 'car') }}" style="max-width: 100%; max-height: 200px; border-radius: 10px;">
                    </div>
                    {% endif %}
                    
                    <form method="POST" enctype="multipart/form-data">
                        {{ form.hidden_tag() }}
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.name.label }}</label>
                                {{ form.name(class="form-control" + (' is-invalid' if form.name.errors else '')) }}
                                {% for error in form.name.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.model.label }}</label>
                                {{ form.model(class="form-control" + (' is-invalid' if form.model.errors else '')) }}
                                {% for error in form.model.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.price_per_day.label }}</label>
                                {{ form.price_per_day(class="form-control" + (' is-invalid' if form.price_per_day.errors else ''), type="number") }}
                                {% for error in form.price_per_day.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.km_per_day.label }}</label>
                                {{ form.km_per_day(class="form-control" + (' is-invalid' if form.km_per_day.errors else ''), type="number") }}
                                {% for error in form.km_per_day.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.category.label }}</label>
                                {{ form.category(class="form-select" + (' is-invalid' if form.category.errors else '')) }}
                                {% for error in form.category.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.transmission.label }}</label>
                                {{ form.transmission(class="form-select" + (' is-invalid' if form.transmission.errors else '')) }}
                                {% for error in form.transmission.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.seats.label }}</label>
                                {{ form.seats(class="form-control" + (' is-invalid' if form.seats.errors else ''), type="number") }}
                                {% for error in form.seats.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.fuel_type.label }}</label>
                                {{ form.fuel_type(class="form-select" + (' is-invalid' if form.fuel_type.errors else '')) }}
                                {% for error in form.fuel_type.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <div class="form-check">
                                    {{ form.available(class="form-check-input") }}
                                    <label class="form-check-label">{{ form.available.label }}</label>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">{{ form.image.label }}</label>
                                {{ form.image(class="form-control" + (' is-invalid' if form.image.errors else '')) }}
                                <small class="text-muted">Leave empty to keep current image. Upload new image to replace. Max size: 50MB</small>
                                {% for error in form.image.errors %}<div class="invalid-feedback">{{ error }}</div>{% endfor %}
                            </div>
                        </div>
                        
                        <div class="text-center mt-4">
                            <button type="submit" class="btn-gradient btn-lg">
                                <i class="fas fa-save"></i> Update Car
                            </button>
                            <a href="{{ url_for('admin_cars') }}" class="btn-outline-gradient btn-lg ms-2">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, form=form, car=car, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/car/delete/<int:car_id>')
@admin_required
def admin_delete_car(car_id):
    car = Car.query.get_or_404(car_id)
    db.session.delete(car)
    db.session.commit()
    flash(f'Car {car.name} {car.model} deleted.', 'success')
    return redirect(url_for('admin_cars'))


@app.route('/admin/dev/clear_cars')
@admin_required
def admin_clear_all_cars():
    """
    Danger: one-time utility to clear ALL cars.
    This will delete all Car rows in SQLite and, via Firestore mirroring,
    also clear the corresponding documents in Firestore.
    """
    cars = Car.query.all()
    for car in cars:
        db.session.delete(car)
    db.session.commit()
    flash('All cars have been deleted from the database (and Firestore).', 'warning')
    return redirect(url_for('admin_cars'))


@app.route('/admin/debug/firebase')
@admin_required
def admin_debug_firebase():
    """
    Simple debug endpoint to verify whether Firebase mirroring
    is enabled on this deployment.
    """
    return f"FIREBASE_ENABLED={app.config.get('FIREBASE_ENABLED')}"

@app.route('/admin/booking/<int:booking_id>')
@admin_required
def admin_view_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="glass-card">
                <div class="card-header-gradient">
                    <h3>
                        <i class="fas fa-info-circle"></i>
                        {{ brand_name }} - Booking #{{ booking.tracking_number }}
                    </h3>
                </div>
                <div class="card-body p-5">
                    <div class="text-center mb-4">
                        <span class="status-badge status-{{ booking.status }}">{{ booking.status|upper }}</span>
                        <span class="badge {% if booking.payment_status == 'paid' %}bg-success{% elif booking.payment_status == 'partial' %}bg-warning{% else %}bg-danger{% endif %} ms-2">
                            {{ booking.payment_status|upper }}
                        </span>
                    </div>
                    
                    <!-- Tracking Number Display -->
                    <div class="alert alert-info text-center p-4 mb-4">
                        <h5><i class="fas fa-qrcode"></i> Tracking Number</h5>
                        <div style="font-size: 32px; font-weight: 700; letter-spacing: 3px;">{{ booking.tracking_number }}</div>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <strong>Customer Name:</strong> {{ booking.customer_name }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Email:</strong> {{ booking.email }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Phone:</strong> {{ booking.phone }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Car Model:</strong> {{ booking.car_model }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Pickup:</strong> {{ booking.pickup_date }} at {{ booking.pickup_time }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Return:</strong> {{ booking.return_date }} at {{ booking.return_time }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Duration:</strong> {{ booking.total_days }} days
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Price per day:</strong> {{ format_currency(booking.car_price_per_day) }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Total Price:</strong> {{ format_currency(booking.total_price) }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Amount Paid:</strong> {{ format_currency(booking.amount_paid) }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Balance Due:</strong> {{ format_currency(booking.balance_due) }}
                        </div>
                        <div class="col-md-6 mb-3">
                            <strong>Booking Date:</strong> {{ booking.created_at.strftime('%Y-%m-%d %H:%M') }}
                        </div>
                        {% if booking.payment_method %}
                        <div class="col-md-6 mb-3">
                            <strong>Payment Method:</strong> {{ booking.payment_method }}
                        </div>
                        {% endif %}
                        {% if booking.payment_reference %}
                        <div class="col-md-6 mb-3">
                            <strong>Payment Reference:</strong> {{ booking.payment_reference }}
                        </div>
                        {% endif %}
                        {% if booking.notes %}
                        <div class="col-12 mb-3">
                            <strong>Notes:</strong> {{ booking.notes }}
                        </div>
                        {% endif %}
                    </div>
                    
                    <hr>
                    
                    <h5 class="mb-3">Documents:</h5>
                    <div class="row">
                        <div class="col-md-3 mb-2">
                            <a href="{{ media_url(booking.id_front, 'upload') }}" target="_blank" class="btn btn-outline-primary w-100">
                                <i class="fas fa-id-card"></i> ID Front
                            </a>
                        </div>
                        <div class="col-md-3 mb-2">
                            <a href="{{ media_url(booking.id_back, 'upload') }}" target="_blank" class="btn btn-outline-primary w-100">
                                <i class="fas fa-id-card"></i> ID Back
                            </a>
                        </div>
                        <div class="col-md-3 mb-2">
                            <a href="{{ media_url(booking.license_file, 'upload') }}" target="_blank" class="btn btn-outline-primary w-100">
                                <i class="fas fa-id-card"></i> License
                            </a>
                        </div>
                        {% if booking.payment_screenshot %}
                        <div class="col-md-3 mb-2">
                            <a href="{{ media_url(booking.payment_screenshot, 'upload') }}" target="_blank" class="btn btn-outline-success w-100">
                                <i class="fas fa-receipt"></i> Payment
                            </a>
                        </div>
                        {% endif %}
                    </div>
                    
                    <div class="text-center mt-4">
                        <a href="{{ url_for('admin_edit_booking', booking_id=booking.id) }}" class="btn-warning btn-lg me-2" style="background: #ffc107; color: #000; padding: 12px 30px; border-radius: 50px; text-decoration: none; display: inline-block; font-weight: 600;">
                            <i class="fas fa-edit"></i> Edit Payment
                        </a>
                        <a href="{{ url_for('admin_dashboard') }}" class="btn-gradient">
                            <i class="fas fa-arrow-left"></i> Back to Dashboard
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''
    
    content = render_template_string(html, booking=booking, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency)

@app.route('/admin/booking/<int:booking_id>/status/<string:status>')
@admin_required
def admin_update_status(booking_id, status):
    booking = Booking.query.get_or_404(booking_id)
    
    if status in ['pending', 'active', 'completed', 'cancelled']:
        old_status = booking.status
        booking.status = status

        # If booking is cancelled, auto-adjust money balance
        if status == 'cancelled':
            # If customer had already paid, record refund as expense and clear booking amounts
            if booking.amount_paid and booking.amount_paid > 0:
                refund_amount = booking.amount_paid
                add_transaction(
                    transaction_type='expense',
                    category='booking_refund',
                    description=f"Refund for cancelled booking {booking.tracking_number}",
                    amount=refund_amount,
                    reference_id=booking.tracking_number,
                    payment_method=booking.payment_method,
                    notes="Auto refund on cancel",
                )
                booking.amount_paid = 0
                booking.balance_due = 0
                booking.payment_status = 'cancelled'

        db.session.commit()
        
        # Send notification if Twilio configured
        try:
            send_customer_confirmation(booking)
        except:
            pass
        
        flash(f'Booking {booking.tracking_number} status updated from {old_status} to {status}', 'success')
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Logged out successfully', 'info')
    return redirect(url_for('index'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/test')
def test():
    """Simple test route to check if app is working"""
    return f"App is working! <a href='/'>Go to {app.config['BRAND_NAME']} Home</a>"

# ==============================
# ERROR HANDLERS
# ==============================
@app.errorhandler(413)
def too_large_error(error):
    """Handle file too large error"""
    flash('❌ File too large! Maximum file size is 50MB. Please compress your images or PDFs and try again.', 'danger')
    logger.error(f"413 Error: File too large - {error}")
    
    # Get the referring page or go to index
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('index'))

@app.errorhandler(404)
def not_found_error(error):
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="glass-card text-center">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #ff416c 0%, #ff4b2b 100%);">
                    <h3>
                        <i class="fas fa-exclamation-triangle"></i>
                        Error 404
                    </h3>
                </div>
                <div class="card-body p-5">
                    <i class="fas fa-frown-open text-danger" style="font-size: 80px;"></i>
                    <h4 class="mt-4">Page not found</h4>
                    <p class="text-muted mt-3">The page you're looking for doesn't exist.</p>
                    <a href="{{ url_for('index') }}" class="btn-gradient mt-4">
                        <i class="fas fa-home"></i> Return to {{ brand_name }}
                    </a>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    html = '''
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="glass-card text-center">
                <div class="card-header-gradient" style="background: linear-gradient(135deg, #ff416c 0%, #ff4b2b 100%);">
                    <h3>
                        <i class="fas fa-exclamation-triangle"></i>
                        Error 500
                    </h3>
                </div>
                <div class="card-body p-5">
                    <i class="fas fa-frown-open text-danger" style="font-size: 80px;"></i>
                    <h4 class="mt-4">Internal server error</h4>
                    <p class="text-muted mt-3">Something went wrong. Please try again.</p>
                    <a href="{{ url_for('index') }}" class="btn-gradient mt-4">
                        <i class="fas fa-home"></i> Return to {{ brand_name }}
                    </a>
                </div>
            </div>
        </div>
    </div>
    '''
    content = render_template_string(html, brand_name=app.config['BRAND_NAME'])
    return render_template_string(BASE_TEMPLATE, content=content, brand_name=app.config['BRAND_NAME'], format_currency=format_currency), 500

# BASE_TEMPLATE with updated navigation and currency display
BASE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ brand_name }}</title>
    
    <!-- Font Awesome 6 -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <!-- Bootstrap 5 -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    
    <!-- Google Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Poppins', sans-serif;
        }
        
        body {
            background: url('{{ url_for('background_image') }}') no-repeat center center fixed;
            background-size: cover;
            min-height: 100vh;
            position: relative;
            overflow-x: hidden;
        }
        
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            z-index: 0;
        }
        
        .bg-bubbles {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 1;
            overflow: hidden;
            pointer-events: none;
        }
        
        .bg-bubbles li {
            position: absolute;
            list-style: none;
            display: block;
            width: 40px;
            height: 40px;
            background-color: rgba(255, 255, 255, 0.1);
            bottom: -160px;
            animation: square 25s infinite;
            transition-timing-function: linear;
            border-radius: 50%;
        }
        
        @keyframes square {
            0% { transform: translateY(0) rotate(0deg); opacity: 1; }
            100% { transform: translateY(-1000px) rotate(720deg); opacity: 0; }
        }
        
        .navbar {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            padding: 15px 0;
            position: relative;
            z-index: 1000;
        }
        
        .navbar-brand {
            font-size: 24px;
            font-weight: 800;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .nav-link {
            font-weight: 500;
            color: #333 !important;
            margin: 0 10px;
        }
        
        .main-content {
            position: relative;
            z-index: 10;
            padding: 40px 0;
        }
        
        .glass-card {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }
        
        .card-header-gradient {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 25px 30px;
            border: none;
        }
        
        .card-header-gradient h3 {
            color: white;
            font-weight: 700;
            margin: 0;
        }
        
        .btn-gradient {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            font-weight: 600;
            border-radius: 50px;
            transition: all 0.3s ease;
            display: inline-block;
            text-decoration: none;
        }
        
        .btn-gradient:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
            color: white;
        }
        
        .btn-outline-gradient {
            background: transparent;
            border: 2px solid #667eea;
            color: #667eea;
            font-weight: 600;
            padding: 10px 25px;
            border-radius: 50px;
            transition: all 0.3s ease;
            display: inline-block;
            text-decoration: none;
        }
        
        .btn-outline-gradient:hover {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: transparent;
        }
        
        .status-badge {
            padding: 8px 16px;
            border-radius: 50px;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            display: inline-block;
        }
        
        .status-pending {
            background: #fff3cd;
            color: #856404;
        }
        
        .status-active {
            background: #cce5ff;
            color: #004085;
        }
        
        .status-completed {
            background: #d4edda;
            color: #155724;
        }
        
        .status-cancelled {
            background: #f8d7da;
            color: #721c24;
        }
        
        .form-control, .form-select {
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            padding: 12px 15px;
        }
        
        .form-control:focus, .form-select:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 0.2rem rgba(102, 126, 234, 0.25);
        }
        
        .file-upload input[type="file"] {
            padding: 15px;
            background: #f8f9fa;
            border: 2px dashed #667eea;
            border-radius: 12px;
            width: 100%;
        }
        
        .alert {
            border-radius: 12px;
            border: none;
            padding: 15px 20px;
        }
        
        .table {
            background: white;
            border-radius: 15px;
            overflow: hidden;
        }
        
        .table thead {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .stats-card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
        }
        
        .stats-icon {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            color: white;
        }
        
        .car-card {
            background: white;
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
            cursor: pointer;
        }
        
        .car-card:hover {
            transform: translateY(-10px);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
        }
        
        .car-image {
            height: 150px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 48px;
            overflow: hidden;
        }
        
        .car-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .car-details {
            padding: 15px;
        }
        
        .car-price {
            color: #667eea;
            font-weight: 800;
            font-size: 24px;
        }
        
        .footer {
            position: relative;
            z-index: 10;
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(10px);
            color: white;
            padding: 20px 0;
            margin-top: 50px;
            text-align: center;
        }
        
        .bg-bubbles li:nth-child(1) { left: 10%; width: 80px; height: 80px; animation-duration: 12s; }
        .bg-bubbles li:nth-child(2) { left: 20%; width: 40px; height: 40px; animation-duration: 10s; }
        .bg-bubbles li:nth-child(3) { left: 25%; width: 110px; height: 110px; }
        .bg-bubbles li:nth-child(4) { left: 40%; width: 60px; height: 60px; animation-duration: 18s; }
        .bg-bubbles li:nth-child(5) { left: 70%; width: 50px; height: 50px; }
        .bg-bubbles li:nth-child(6) { left: 80%; width: 120px; height: 120px; }
        .bg-bubbles li:nth-child(7) { left: 32%; width: 160px; height: 160px; }
        .bg-bubbles li:nth-child(8) { left: 55%; width: 45px; height: 45px; animation-duration: 45s; }
        .bg-bubbles li:nth-child(9) { left: 15%; width: 35px; height: 35px; animation-duration: 35s; }
        .bg-bubbles li:nth-child(10) { left: 90%; width: 150px; height: 150px; animation-duration: 11s; }
        
        .offer-banner {
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0% { transform: scale(1); }
            50% { transform: scale(1.05); }
            100% { transform: scale(1); }
        }
    </style>
</head>
<body>
    <ul class="bg-bubbles">
        <li></li><li></li><li></li><li></li><li></li>
        <li></li><li></li><li></li><li></li><li></li>
    </ul>
    
    <nav class="navbar navbar-expand-lg">
        <div class="container">
            <a class="navbar-brand" href="{{ url_for('index') }}">
                <i class="fas fa-car"></i> {{ brand_name }}
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('index') }}"><i class="fas fa-home"></i> Home</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('booking_status') }}"><i class="fas fa-search"></i> Track Booking</a>
                    </li>
                    {% if session.get('admin_logged_in') %}
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_dashboard') }}"><i class="fas fa-dashboard"></i> Dashboard</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_cars') }}"><i class="fas fa-car"></i> Cars</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_banners') }}"><i class="fas fa-images"></i> Banners</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_background') }}"><i class="fas fa-image"></i> Website BG</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_hero') }}"><i class="fas fa-photo-video"></i> Hero BG</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_account') }}"><i class="fas fa-wallet"></i> Account</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_logout') }}"><i class="fas fa-sign-out-alt"></i> Logout</a>
                    </li>
                    {% else %}
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_login') }}"><i class="fas fa-lock"></i> Admin</a>
                    </li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>
    
    <div class="main-content">
        <div class="container">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                            <i class="fas fa-{{ 'check-circle' if category == 'success' else 'exclamation-circle' }}"></i>
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            {{ content|safe }}
        </div>
    </div>
    
    <footer class="footer">
        <div class="container">
            <p class="mb-0">
                <i class="fas fa-copyright"></i> 2024 {{ brand_name }}. 
                All rights reserved. | <i class="fas fa-phone"></i> +94 0753394996 | 0756656862
            </p>
        </div>
    </footer>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
'''

# ==============================
# MAIN ENTRY POINT
# ==============================
if __name__ == '__main__':
    print("="*70)
    print(f"🚗 {app.config['BRAND_NAME']} SYSTEM - SRI LANKAN VERSION")
    print("="*70)
    print(f"✨ Brand Name: {app.config['BRAND_NAME']}")
    print(f"✨ Brand Slogan: {app.config['BRAND_SLOGAN']}")
    print(f"✨ Currency: Rs. (LKR) - Format: Rs. 5,000/=")
    print(f"✨ Upload folder: {app.config['UPLOAD_FOLDER']}")
    print(f"✨ Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print(f"✨ Admin login: http://127.0.0.1:5000/admin/login")
    print(f"✨ Admin password: {app.config['ADMIN_PASSWORD']}")
    print(f"✨ Track booking: http://127.0.0.1:5000/booking/status")
    print(f"✨ Manage cars: http://127.0.0.1:5000/admin/cars")
    print(f"✨ Manage banners: http://127.0.0.1:5000/admin/banners")
    print(f"✨ Change website BG: http://127.0.0.1:5000/admin/background")
    print(f"✨ Change hero BG: http://127.0.0.1:5000/admin/hero")
    print(f"✨ Account management: http://127.0.0.1:5000/admin/account")
    print(f"✨ Test page: http://127.0.0.1:5000/test")
    print(f"✨ WhatsApp number: wa.me/{app.config['WHATSAPP_NUMBER']}")
    print("="*70)
    print(f"🌐 Server running at http://127.0.0.1:5000")
    print("="*70)
    print(f"📱 {app.config['BRAND_NAME']} FEATURES:")
    print("📱 1. REAL-TIME PRICE CALCULATION (SRI LANKAN RUPEES)")
    print("📱 2. IMMEDIATE PRICE SUMMARY while booking")
    print("📱 3. Payment tracking (paid, partial, pending)")
    print("📱 4. Balance due calculation")
    print("📱 5. Payment screenshot upload")
    print("📱 6. Revenue tracking in admin panel")
    print("📱 7. Pending payments monitoring")
    print("📱 8. Total expected revenue")
    print("📱 9. WhatsApp sharing with payment details")
    print("📱 10. TRACKING NUMBER SYSTEM (FR00001, etc.)")
    print("📱 11. EDITABLE PAYMENT FIELDS in admin dashboard")
    print("📱 12. BALANCE DUE BREAKDOWN by booking status")
    print("📱 13. PAYMENT STATUS BREAKDOWN (Paid, Partial, Pending)")
    print("📱 14. COLLECTION RATE CALCULATION")
    print("📱 15. COMPLETE ACCOUNT MANAGEMENT with balance tracking")
    print("📱 16. EXPENSE TRACKING with categories")
    print("📱 17. TRANSACTION HISTORY with running balance")
    print("📱 18. SRI LANKAN BANK DETAILS (Bank of Ceylon)")
    print("📱 19. SRI LANKAN PHONE NUMBER VALIDATION (077, +94, etc.)")
    print("📱 20. FILE SIZE LIMIT: 50MB with client-side validation")
    print("="*70)
    
    # Create default folders and placeholder images
    folders = ['cars', 'banners', 'backgrounds', 'hero', 'temp']
    for folder in folders:
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], folder), exist_ok=True)
    
    # Run app
    app.run(debug=True, host='0.0.0.0', port=5000)