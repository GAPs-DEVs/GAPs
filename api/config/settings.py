"""Minimal settings for Django and PostgreSQL connectivity checks."""
import os
from django.core.exceptions import ImproperlyConfigured


def required_value(name):
    # Reject missing or example secrets without exposing their values in errors.
    value = os.environ.get(name, "")
    if not value or value.startswith("replace-with-"):
        raise ImproperlyConfigured(f"Set {name} in the API environment")
    return value


# Compose supplies these variables from api/.env.local at runtime.
SECRET_KEY = required_value("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = [
    item.strip()
    for item in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if item.strip()
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
# No auth, admin, sessions, or models yet. No Django migrations are needed.
INSTALLED_APPS = []
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "postgres"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": os.environ.get("DB_NAME", "guitar_practice"),
        "USER": os.environ.get("DB_USER", "guitar_api"),
        "PASSWORD": required_value("DB_PASSWORD"),
        # Bound connection/query waits when PostgreSQL is unavailable.
        "OPTIONS": {"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    }
}
USE_TZ = True
TIME_ZONE = "Asia/Seoul"
