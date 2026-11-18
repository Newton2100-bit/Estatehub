from pathlib import Path
import os
from urllib.parse import urlsplit
BASE_DIR=Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    # Keep the local Django site runnable when the optional .env loader has not
    # been installed in the active Python environment. Deployment variables
    # can still be supplied through the process environment.
    pass
else:
    load_dotenv(BASE_DIR / ".env")
PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").strip()
SECRET_KEY="estatehub-dev-secret-change-me"
DEBUG=True
ALLOWED_HOSTS=["127.0.0.1","localhost"]
callback_host=urlsplit(PUBLIC_BASE_URL).hostname
if callback_host and callback_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(callback_host)
INSTALLED_APPS=["django.contrib.admin","django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","core"]
MIDDLEWARE=["django.middleware.security.SecurityMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","core.middleware.RequirePasswordChangeMiddleware","django.contrib.messages.middleware.MessageMiddleware","django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF="estatehub.urls"
TEMPLATES=[{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[BASE_DIR/"templates"],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages","core.context_processors.notification_context"]}}]
WSGI_APPLICATION="estatehub.wsgi.application"
DATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":BASE_DIR/"db.sqlite3"}}
AUTH_PASSWORD_VALIDATORS=[]
LANGUAGE_CODE="en-us"
TIME_ZONE="Africa/Nairobi"
USE_I18N=True
USE_TZ=True
STATIC_URL="static/"
STATICFILES_DIRS=[BASE_DIR/"static"]
MEDIA_URL="media/"
MEDIA_ROOT=BASE_DIR/"media"
DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"
LOGIN_URL="/login/"
DARAJA_ENV=os.getenv("DARAJA_ENV", "sandbox").strip().lower()
if DARAJA_ENV not in {"sandbox", "production"}:
    DARAJA_ENV = "sandbox"
DARAJA_CONSUMER_KEY=os.getenv("DARAJA_CONSUMER_KEY", "").strip()
DARAJA_CONSUMER_SECRET=os.getenv("DARAJA_CONSUMER_SECRET", "").strip()
DARAJA_SHORTCODE=os.getenv("DARAJA_SHORTCODE", "").strip()
DARAJA_PASSKEY=os.getenv("DARAJA_PASSKEY", "").strip()
DARAJA_STK_TRANSACTION_TYPE=os.getenv("DARAJA_STK_TRANSACTION_TYPE", "CustomerPayBillOnline").strip()
DARAJA_B2C_ENABLED=os.getenv("DARAJA_B2C_ENABLED", "false").strip().lower() == "true"
DARAJA_B2C_SHORTCODE=os.getenv("DARAJA_B2C_SHORTCODE", "").strip()
DARAJA_B2C_INITIATOR_NAME=os.getenv("DARAJA_B2C_INITIATOR_NAME", "").strip()
DARAJA_B2C_SECURITY_CREDENTIAL=os.getenv("DARAJA_B2C_SECURITY_CREDENTIAL", "").strip()
DARAJA_CALLBACK_TOKEN=os.getenv("DARAJA_CALLBACK_TOKEN", "").strip()
