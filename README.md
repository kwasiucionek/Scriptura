# Scriptura — RAG teologii biblijnej

Asystent do pytań o Biblię, który odpowiada **wyłącznie na podstawie źródeł** i każde zdanie
podpiera przypisem: tekstem biblijnym w oryginale i przekładzie, literaturą biblistów (na razie
PWT Wrocław i UPJPII), leksykonem, tradycją Ojców Kościoła i tekstami starożytnego Bliskiego
Wschodu. Dwa tryby — naukowy (aparat, terminy oryginalne, hierarchia źródeł) i popularny
(prosty język, te same źródła) — i weryfikacja cytatów po stronie systemu.

Demo: https://scriptura.cytr.us · stan projektu i historia decyzji: [CHANGELOG.md](CHANGELOG.md)

> PoC zbudowany na otwartych źródłach jako podstawa do rozmów z autorami o włączeniu ich
> dorobku. Materiały bez wolnej licencji są w systemie widoczne tylko dla kont z dostępem
> (`licensed`) albo dla właściciela (`personal`) — patrz [Dostęp i licencje](#dostęp-i-licencje).

## Co jest w środku

| warstwa | źródło | licencja | co daje |
|---|---|---|---|
| tekst biblijny | WLC (OSHB), SBLGNT (MorphGNT), LXX Rahlfs (E. Wong), Biblia Gdańska 1632 | CC BY / domena publiczna | wersety po ordinalu, tokeny z lematami i Strongami, widok równoległy, konkordancja |
| leksykon | STEPBible TBESH/TBESG (Tyndale House) | CC BY 4.0 | 20 192 hasła: lemat, transliteracja, glosa, definicja; uzupełnia lematy WLC |
| odsyłacze | OpenBible.info cross-references | CC BY | „powiązane wersety” wg głosów |
| literatura | artykuły, monografie, skrypty, recenzje, wpisy blogowe, wykłady wideo (harvest z DSpace/OPEN, OpenAlex, Scholar, WordPress, YouTube) | wg pozycji: open / licensed | chunki z siglami, cytowanie [n], weryfikacja cytatów, strony; wykłady z linkiem do minuty |
| Ojcowie Kościoła | ANF/NPNF w ThML z CCEL (37 tomów, przekłady angielskie XIX w.) | domena publiczna | pasaże z odsyłaczami biblijnymi (`<scripRef>`) — „co Ojcowie mówią o tym wersecie” |
| ANE | eBL (LMU): Gilgamesz, Enuma Elisz… | CC BY-NC-SA 4.0 | teksty porównawcze, cytowane z tabliczką i linią |

Bazy Ojców i ANE są **powiązane, nie zmieszane** z literaturą: osobne indeksy, osobne sekcje
promptu z instrukcją, osobne oznaczenia w panelu źródeł ([P1], [A1]); włączane opcją albo
automatycznie, gdy pytanie o nie pyta.

## Jak działa odpowiedź

1. **Pytanie** → sigla (parser polskich skrótów, zakresy, wersety), terminy oryginalne
   (hebrajski/grecki, także w transliteracji: *hesed*, *szalom*, *logos*), tłumaczenie
   zapytania na angielski dla źródeł obcojęzycznych.
2. **Retrieval** czterokanałowy w OpenSearchu: sigla → terminy (lemat) → transliteracje
   (`text_fold`) → BM25 + kNN (`snowflake-arctic-embed2`), fuzja RRF, sąsiedzi kontekstu,
   limit chunków na dokument, miękka preferencja rejestru (naukowy/popularny).
3. **Reranker**: `llm` (model czatu ocenia trafność 0–3, odrzuca nietrafione), `tei`
   (bge-reranker-v2-m3 na GPU) albo `none`.
4. **Kontekst**: ŹRÓDŁA [n] · WERSETY (oryginał + przekład) · LEKSYKON · POWIĄZANE WERSETY ·
   TRADYCJA PATRYSTYCZNA [P] · ANE [A].
5. **Generacja** (Ollama, `gemma4:31b-cloud` lub lokalny model) ze strumieniem SSE; potem
   weryfikacja: czy cytaty [n] istnieją, czy sigla są w źródłach, czy cudzysłowy odpowiadają
   tekstowi (dosłownie; cytaty z [P]/[A] oznaczone jako przekład modelu).
6. **Panel źródeł**: zwijane grupy, fragmenty, linki do oryginałów, eksport cytowań
   BibTeX / RIS (tylko przywołane pozycje, ze stronami pasaży Ojców).

Ewaluacja retrievalu (30 pytań, `eval_retrieval`): recall@5 0,87, near±1 1,00, MRR 0,67
bez rerankera — szczegóły i decyzje w changelogu (sprint 10–11).

## Szybki start (lokalnie)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                      # OLLAMA_*, OPENSEARCH_URL, HARVEST_MAILTO
docker compose --profile opensearch up -d  # OpenSearch z pluginami stempel + icu + knn
ollama pull snowflake-arctic-embed2 && ollama signin   # modele -cloud po zalogowaniu

python manage.py migrate
python manage.py fetch_sources && python manage.py import_corpus oshb morphgnt lxx json
python manage.py fetch_sources step xref && python manage.py import_lexicon && python manage.py import_xref
python manage.py index_search --recreate            # wersety
python manage.py runserver                          # http://127.0.0.1:8000
```

Literatura (przykład dla jednego autora):

```bash
python manage.py harvest openalex --author-id A5073391228 --only-oa --out data/manifests/rosik.jsonl
python manage.py curate_manifest data/manifests/rosik.jsonl --author "Mariusz Rosik" --apply
python manage.py ingest_manifest data/manifests/rosik.jsonl --no-index
python manage.py audit_corpus --apply --delete
python manage.py translate_chunks                   # obcojęzyczne -> text_pl
python manage.py reindex_chunks --recreate
```

Bazy powiązane:

```bash
python manage.py import_patristics anf01 npnf110 && python manage.py index_patristics --recreate
python manage.py import_ane L/1/4 --name-pl Gilgamesz && python manage.py index_ane --recreate
python manage.py translate_patristics --volumes anf01 ; python manage.py translate_ane
```

Automatyczne aktualizacje: `data/authors.jsonl` (wzór: `data/authors.example.jsonl`) →
`python manage.py update_corpus` (harvest przyrostowy z DSpace/OpenAlex/Scholar/bloga → kurator
LLM → ingestia → audyt → przekład → reindeks → raport w `data/manifests/`).

## Najważniejsze komendy

| komenda | opis |
|---|---|
| `fetch_sources [oshb morphgnt bg lxx step xref]` | pobranie otwartych korpusów i leksykonów |
| `import_corpus`, `import_lexicon`, `import_xref` | tekst biblijny, leksykon STEP (+ lematy WLC), cross-references |
| `index_search --recreate` | indeks wersetów (BM25, greka/hebrajski przez ICU) |
| `harvest dspace\|openalex\|scholar\|bibtex\|blog\|youtube` | manifest JSONL publikacji / wpisów / wykładów (napisy + rozdziały przez yt-dlp) |
| `curate_manifest`, `ingest_manifest`, `audit_corpus` | kurator (reguły + LLM), pobieranie i chunkowanie, audyt po treści |
| `translate_chunks`, `translate_ane`, `translate_patristics` | przekłady maszynowe do wyszukiwania |
| `reindex_chunks --recreate [--reuse-vectors\|--vectors-file]` | indeks literatury; wektory z indeksu lub pliku |
| `export_vectors` | eksport embeddingów (chunks + ane + patristics) do przeniesienia na inny host |
| `import_patristics`, `index_patristics` | Ojcowie z CCEL |
| `import_ane`, `index_ane` | teksty ANE z eBL |
| `merge_authors --apply` | scalanie dubletów autorów |
| `eval_bootstrap`, `eval_retrieval` | ewaluacja retrievalu |
| `update_corpus [--dry-run]` | pełny pipeline aktualizacji |
| `warmup` | rozgrzewka embeddingów i rerankera po starcie |

## Dostęp i licencje

Poziomy dokumentu: `open` (wolna licencja / domena publiczna — dla wszystkich, także anonimów),
`licensed` (za zgodą autora — konta z grupą `scriptura-licensed`), `private` (demo dla
właściciela praw — `scriptura-private`), `personal` (materiał użytkownika wgrany przez konto —
widoczny tylko dla niego, także w RAG). Właściciel materiału osobistego może go **udostępnić**
do wspólnego korpusu: oświadczenie → `Consent` (kto, kiedy, treść) → `licensed`, z możliwością
wycofania. Licencja i poziom dostępu są ustalane deterministycznie przy ingestii (CC → open,
brak → licensed); kurator LLM może tylko oznaczać do przeglądu.

Anonimowi: tryb popularny, źródła `open`, limit `RATE_LIMIT_ANON` pytań/h.

## Konfiguracja (.env)

Pełna lista w `.env.example`; produkcja: `deploy/env.production.example`. Najważniejsze:

| zmienna | domyślnie | uwagi |
|---|---|---|
| `OLLAMA_BASE_URL`, `OLLAMA_CHAT_MODEL` | `http://localhost:11434`, `gemma4:31b-cloud` | modele `-cloud` po `ollama signin` |
| `OLLAMA_EMBED_MODEL`, `EMBEDDING_DIM` | `snowflake-arctic-embed2`, 1024 | zmiana wymaga `reindex_chunks --recreate` |
| `SEARCH_BACKEND`, `OPENSEARCH_URL`, `OPENSEARCH_INDEX_PREFIX` | opensearch | `db` = fallback bez kNN (testy, awaria) |
| `RERANKER_BACKEND` | none | `llm` (chmura, bez GPU), `tei` (GPU), `none` |
| `RAG_TOP_K`, `RAG_MAX_PER_DOC`, `RAG_CONTEXT_NEIGHBORS` | 8, 3, 1 | źródła, limit na dokument, sąsiedzi |
| `RAG_DEFAULT_MODE`, `RAG_REGISTER_MODE` | scientific, soft | soft: tryb = poziom odpowiedzi + premia rankingowa |
| `RAG_RELATED_VERSES`, `RAG_ANE_PASSAGES`, `RAG_PATRISTIC_PASSAGES` | 6, 3, 4 | 0 wyłącza warstwę |
| `ALLOW_ANONYMOUS`, `ANONYMOUS_POPULAR_ONLY`, `RATE_LIMIT_ANON` | true, true, 20 | |
| `PERSONAL_MAX_MB`, `PERSONAL_MAX_DOCS` | 25, 50 | materiały osobiste |
| `HARVEST_MAILTO`, `HARVEST_COOKIE`, `SERPAPI_KEY` | | OpenAlex polite pool; JWT zapory ICM (7 dni); Scholar |
| `TRANSLATE_MODEL`, `CURATE_MODEL` | = chat | przekłady i kurator |
| `BEHIND_PROXY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | | produkcja za Cloudflare/nginx |

## Wdrożenie

`deploy/` — unit systemd (`scriptura-web`: gunicorn gthread, SSE), timer tygodniowej
aktualizacji (`scriptura-update`), vhost nginx (SSE bez buforowania), `deploy.sh`
(rsync + migrate + collectstatic + restart; `--data` z PDF-ami, bazą i wektorami),
`setup_after_rsync.sh` (venv, indeksy z pliku wektorów), `backup-db.sh` (spójna kopia SQLite).
Kroki i realia serwera: [deploy/README.md](deploy/README.md). OpenSearch może być
współdzielony z innymi aplikacjami (prefiks `scriptura-*`); potrzebne pluginy:
`analysis-stempel`, `analysis-icu`, `opensearch-knn`.

## Układ repozytorium

```
config/       ustawienia, urls, wsgi
corpus/       tekst biblijny: modele, sigla, importery (oshb, morphgnt, lxx, json, step, xref), konkordancja, widok równoległy
library/      literatura: modele, harvest/, ingestia, chunkowanie, fonty legacy, search (OpenSearch), reranker, przekłady, kurator
rag/          serwis RAG, prompt, weryfikacja cytatów, konta, materiały osobiste, zgody, eksport cytowań, GUI (SSE)
ane/          baza powiązana: teksty ANE (eBL)
patristics/   baza powiązana: Ojcowie Kościoła (CCEL ThML)
deploy/       systemd, nginx, skrypty wdrożeniowe
services/     reranker ROCm/CUDA (FastAPI, API zgodne z TEI)
scripts/      narzędzia jednorazowe (fonty legacy, place_downloads, eval)
templates/    GUI: czat, konto, tekst i konkordancja
```

Testy: `pytest` (129, backendy zastępcze bez OpenSearcha i Ollamy); styl: `ruff format && ruff check`.

## Stan i plan

Działa: pełny łańcuch od harvestu po odpowiedź z weryfikacją, konta i materiały osobiste,
bazy powiązane, eksport cytowań, wdrożenie na VPS. Do zrobienia: zgody autorów na materiały
`licensed` (w toku), skrypty UPJPII Majewskiego, Postgres przed otwarciem kont dla wielu
użytkowników, więcej tomów Ojców z przekładem, ORACC dla inskrypcji i listów, warstwa
patrystyczna po polsku (gdy pojawi się źródło z otwartą licencją).
