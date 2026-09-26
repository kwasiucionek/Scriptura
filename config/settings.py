"""Ustawienia projektu Scriptura — RAG teologii biblijnej.

Wszystkie wartości środowiskowe czytane są z .env (patrz .env.example).
Domyślnie SQLite (dev), na produkcji PostgreSQL przez DATABASE_URL.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimalny loader .env bez zewnętrznych zależności."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv(BASE_DIR / ".env")


def env(key: str, default=None, cast=str):
    """Odczyt zmiennej środowiskowej z rzutowaniem typu."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    if cast is bool:
        return raw.lower() in {"1", "true", "yes", "on"}
    return cast(raw)


SECRET_KEY = env("SECRET_KEY", "dev-only-change-me")
DEBUG = env("DEBUG", True, bool)
ALLOWED_HOSTS = env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
# Za Cloudflare/nginx: TLS terminowany przed originem -> bez przekierowań na https po stronie Django
BEHIND_PROXY = env("BEHIND_PROXY", False, bool)
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corpus",
    "library",
    "rag",
    "ane",
    "patristics",
    "pages",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # statyki admina bez aliasu w nginx
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pages.context.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def _database_from_url(url: str) -> dict:
    """DATABASE_URL: sqlite:///path/db.sqlite3 lub postgres://user:pass@host:port/db"""
    if url.startswith("sqlite://"):
        # sqlite:///db.sqlite3 (względna do BASE_DIR) lub sqlite:////abs/path.db
        name = url[len("sqlite:///") :]
        if name != ":memory:" and not name.startswith("/"):
            name = str(BASE_DIR / name)
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
    }


DATABASES = {
    "default": _database_from_url(
        env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
    )
}

# FTS (lookup __search) wymaga django.contrib.postgres — tylko na PostgreSQL
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    INSTALLED_APPS.insert(0, "django.contrib.postgres")
elif DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    # WAL: czytelnicy nie czekają na piszących (historia rozmów, uploady, update_corpus z timera);
    # timeout: krótkie kolizje zapisów czekają zamiast rzucać „database is locked”;
    # IMMEDIATE: transakcje zapisu biorą blokadę od razu (bez deadlocku SQLite przy podniesieniu)
    DATABASES["default"]["OPTIONS"] = {
        "timeout": 20,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-65536;",
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]  # site.css — wspólne style podstron i paska
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --- Limity zapytań (chmura LLM kosztuje): liczba pytań na godzinę per IP (anonim) / per konto ---
RATE_LIMIT_ANON = env("RATE_LIMIT_ANON", 20, int)
RATE_LIMIT_USER = env("RATE_LIMIT_USER", 200, int)
QUESTION_MAX_CHARS = env("QUESTION_MAX_CHARS", 1000, int)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": env("CACHE_DIR", str(BASE_DIR / ".cache")),
        "TIMEOUT": 3600,
    }
}

# --- Dane źródłowe (katalog z pobranymi korpusami) ---
DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR / "data")))

# --- Wyszukiwanie ---
# "opensearch" (produkcja/dev z dockerem) | "db" (fallback: Postgres FTS / SQLite LIKE)
SEARCH_BACKEND = env("SEARCH_BACKEND", "db")
OPENSEARCH_URL = env("OPENSEARCH_URL", "http://localhost:9200")
OPENSEARCH_USER = env("OPENSEARCH_USER", "")
OPENSEARCH_PASSWORD = env("OPENSEARCH_PASSWORD", "")
OPENSEARCH_VERIFY_CERTS = env("OPENSEARCH_VERIFY_CERTS", True, bool)
OPENSEARCH_INDEX_PREFIX = env("OPENSEARCH_INDEX_PREFIX", "scriptura")

# --- Harvest (manifesty prac ze źródeł otwartych) ---
MANIFESTS_DIR = Path(env("MANIFESTS_DIR", str(DATA_DIR / "manifests")))
HARVEST_MAILTO = env(
    "HARVEST_MAILTO", ""
)  # e-mail do nagłówków (OpenAlex polite pool, DSpace UA)
HARVEST_COOKIE = env(
    "HARVEST_COOKIE", ""
)  # np. token Anubis z przeglądarki (ważny ~7 dni)
HARVEST_USER_AGENT = env(
    "HARVEST_USER_AGENT", "scriptura-harvest/0.1"
)  # UA, dla którego wydano token
HARVEST_ACCEPT_LANGUAGE = env("HARVEST_ACCEPT_LANGUAGE", "pl,en;q=0.8")
# Szablon Referera przy pobieraniu plików; {origin} i {path} z URL-a pliku.
# OPEN ICM: "{origin}/captcha.html?protected={path}%3f"; pusty = bez Referera
HARVEST_REFERER_TEMPLATE = env("HARVEST_REFERER_TEMPLATE", "")
HARVEST_REFERER_HOSTS = [
    h for h in env("HARVEST_REFERER_HOSTS", "open.icm.edu.pl").split(",") if h
]  # tylko te hosty dostają Referer z szablonu
SERPAPI_KEY = env(
    "SERPAPI_KEY", ""
)  # profil Google Scholar przez SerpApi (opcjonalnie)

# --- LLM / embeddingi ---
# "ollama" | "echo" (bez modelu: odpowiedź zastępcza z listą źródeł — testy, demo offline)
LLM_BACKEND = env("LLM_BACKEND", "echo")
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_API_KEY = env("OLLAMA_API_KEY", "")  # Ollama Cloud
OLLAMA_CHAT_MODEL = env("OLLAMA_CHAT_MODEL", "qwen3.5:122b-cloud")
OLLAMA_EMBED_MODEL = env("OLLAMA_EMBED_MODEL", "snowflake-arctic-embed2")
EMBEDDING_DIM = env("EMBEDDING_DIM", 1024, int)
# Prefiks doklejany do ZAPYTANIA (nie do dokumentów): arctic-embed v2 -> "query: ",
# Qwen3-Embedding -> "Instruct: ...\nQuery: ", bge-m3 -> pusty
EMBEDDING_QUERY_PREFIX = env("EMBEDDING_QUERY_PREFIX", "query: ")
RAG_THINK = env("RAG_THINK", False, bool)  # tryb "thinking" modelu czatu

# --- Embeddingi: "ollama" (lokalnie / OLLAMA_BASE_URL) | "tei" (serwer TEI: lokalny lub HF Inference Endpoint) ---
EMBEDDINGS_BACKEND = env("EMBEDDINGS_BACKEND", "ollama")
TEI_EMBED_URL = env("TEI_EMBED_URL", "http://localhost:8080")
TEI_TOKEN = env(
    "TEI_TOKEN", ""
)  # token HF dla endpointów w chmurze; pusty lokalnie (dla embed i rerank)

# --- Reranker (TEI: text-embeddings-inference z modelem klasyfikacji par; lokalnie lub HF Endpoint) ---
RERANKER_BACKEND = env("RERANKER_BACKEND", "none")  # "tei" | "llm" | "none"
RERANK_LLM_MIN_SCORE = env(
    "RERANK_LLM_MIN_SCORE", 1.0, float
)  # llm: kandydaci poniżej odpadają (0–3)
RERANK_LLM_MAX_CHARS = env(
    "RERANK_LLM_MAX_CHARS", 700, int
)  # llm: ile znaków chunku pokazać modelowi
RAG_MAX_PER_DOC = env(
    "RAG_MAX_PER_DOC", 3, int
)  # dywersyfikacja: max chunków z jednego dokumentu w top-k
TEI_RERANK_URL = env("TEI_RERANK_URL", "http://localhost:8081")
RERANK_TOP_N = env("RERANK_TOP_N", 20, int)  # ilu kandydatów z RRF trafia do rerankera
RERANK_MAX_CHARS = env("RERANK_MAX_CHARS", 1800, int)  # przycięcie tekstu pary

# --- RAG ---
RAG_TOP_K = env("RAG_TOP_K", 8, int)
RAG_CONTEXT_NEIGHBORS = env(
    "RAG_CONTEXT_NEIGHBORS", 1, int
)  # chunki order±N doklejane do trafień
# Tłumaczenie zapytania na angielski (BM25 + kNN po dokumentach en), tylko gdy korpus ma takie dokumenty
RAG_TRANSLATE_QUERY = env("RAG_TRANSLATE_QUERY", True, bool)
RAG_TRANSLATE_MODEL = env("RAG_TRANSLATE_MODEL", "")  # pusty = OLLAMA_CHAT_MODEL
RAG_RELATED_VERSES = env(
    "RAG_RELATED_VERSES", 6, int
)  # powiązane wersety (OpenBible); 0 = wyłączone
# Baza powiązana ANE (eBL): liczba pasaży w prompcie; 0 = wyłączone. Włączane opcją w pytaniu
# lub automatycznie, gdy pytanie wymienia teksty/bóstwa z ANE_TRIGGERS (ane/service.py)
RAG_ANE_PASSAGES = env("RAG_ANE_PASSAGES", 3, int)
# Baza powiązana Ojców Kościoła (ANF/NPNF z CCEL): pasaże w prompcie; 0 = wyłączone
RAG_PATRISTIC_PASSAGES = env("RAG_PATRISTIC_PASSAGES", 4, int)
# Przekład chunków obcojęzycznych na polski przy ingestii (translate_chunks): TranslateGemma lokalnie
TRANSLATE_MODEL = env("TRANSLATE_MODEL", "translategemma:27b")
TRANSLATE_BASE_URL = env("TRANSLATE_BASE_URL", "") or OLLAMA_BASE_URL
# Kurator manifestów (update_corpus): model do oceny wstępnej; progi pewności dla decyzji automatycznych
CURATE_MODEL = env("CURATE_MODEL", "")  # pusty = OLLAMA_CHAT_MODEL
CURATE_REVIEW_CONFIDENCE = env(
    "CURATE_REVIEW_CONFIDENCE", 0.5, float
)  # model niepewny -> access=review
RAG_DEFAULT_MODE = env("RAG_DEFAULT_MODE", "scientific")  # "scientific" | "popular"
# soft (domyślnie): tryb zmienia poziom odpowiedzi i kolejność źródeł (premia wg rejestru),
# hard: tryb naukowy widzi tylko źródła scientific/mixed
RAG_REGISTER_MODE = env("RAG_REGISTER_MODE", "soft")
RAG_ACCESS = env("RAG_ACCESS", "open").split(
    ","
)  # które poziomy dostępu retrieval widzi
RAG_VERSE_WORKS = env("RAG_VERSE_WORKS", "BG1632,SBLGNT,WLC,LXX").split(",")
RAG_TEMPERATURE = env("RAG_TEMPERATURE", 0.2, float)
RAG_NUM_CTX = env("RAG_NUM_CTX", 16384, int)
DEMO_TOKEN = env(
    "DEMO_TOKEN", ""
)  # jeśli ustawiony, /ask/stream wymaga nagłówka X-Demo-Token
# Konta: czy anonimowi mogą pytać (tylko open + tryb popularny), czy wymagane logowanie
ALLOW_ANONYMOUS = env("ALLOW_ANONYMOUS", True, bool)
ANONYMOUS_POPULAR_ONLY = env("ANONYMOUS_POPULAR_ONLY", True, bool)
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
# Materiały osobiste użytkowników (konto -> „Moje materiały")
PERSONAL_MAX_MB = env("PERSONAL_MAX_MB", 25, int)
PERSONAL_MAX_DOCS = env("PERSONAL_MAX_DOCS", 50, int)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# --- Strony informacyjne (stopka, kontakt) ---
SITE_GITHUB_URL = env("SITE_GITHUB_URL", "https://github.com/kwasiucionek/Scriptura")
SITE_CONTACT_EMAIL = env("SITE_CONTACT_EMAIL", "")  # pusty = bez adresu na stronie „Autor”
