# Scriptura — dziennik sprintów (changelog)

Nazwa robocza. Cel: teologia biblijna na poziomie naukowym dla każdego (etap 1),
z możliwością rozszerzenia o korpus naukowy po uzyskaniu praw (etap 2).

## Sprint 1 (ten) — warstwa tekstu kanonicznego

Co jest:

- **`corpus.books`** — tabela 73 ksiąg (kanon katolicki) z OSIS id, skrótami BT
  i aliasami (BW, BG, „1 Mojż", „Obj", „Kazn"…), mapowaniem na źródła.
- **`corpus.sigla`** — parser polskich sigli: `parse()` dla ciągów sigli,
  `extract()` do skanowania artykułów; zakresy przeliczane na `ordinal`
  bez odwołania do bazy. 24 testy.
- **`corpus.normalize`** — normalizacja greki (bez akcentów), hebrajskiego
  (bez nikud), polskiego — jedna funkcja dla indeksu i dla zapytania.
- **Modele**: `Book`, `Work` (dzieło: oryginał/przekład, licencja, `access`),
  `Verse` (adres kanoniczny, `ordinal`), `VerseText`, `Token` (lemat, Strong,
  morfologia). Klucz łączący korpusy to **lemat**; Strong jest opcjonalny
  (LXX ma tysiące lematów bez Stronga).
- **Importery** z otwartych źródeł, sprawdzone na prawdziwych danych:
  - OSHB / WLC z morfologią OSHM (CC BY 4.0) — `import_corpus oshb`
  - MorphGNT / SBLGNT (CC BY 4.0 + CC BY-SA 3.0) — `import_corpus morphgnt`
  - Biblia Gdańska 1632 JSON (MIT-0, tekst PD) — `import_corpus json`
- **Admin** dla wszystkich modeli.

## Start (pełny przebieg)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                    # uzupełnij: OLLAMA_API_KEY, SEARCH_BACKEND, RERANKER_BACKEND…
python manage.py migrate
python manage.py createsuperuser

# korpus tekstu kanonicznego (otwarte źródła z GitHuba)
python manage.py fetch_sources                 # OSHB + MorphGNT + BG + LXX do ./data
python manage.py import_corpus oshb            # WLC: ~23 000 wersetów
python manage.py import_corpus morphgnt        # SBLGNT: ~8 000 wersetów
python manage.py import_corpus lxx             # Septuaginta: ~27 000 wersetów
python manage.py import_corpus json --file data/translations/bg.json --code BG1632 \
    --license "MIT-0 (bible-api-io); tekst 1632 w domenie publicznej"
python manage.py index_search --recreate       # indeks wersetów w OpenSearch

# literatura
python manage.py harvest dspace --browse-author "Majewski, Marcin" --out data/manifests/majewski-open.jsonl
python manage.py ingest_manifest data/manifests/majewski-open.jsonl --dry-run
python manage.py ingest_manifest data/manifests/majewski-open.jsonl

# usługi i uruchomienie
docker compose up -d                           # reranker (opcjonalnie)
python manage.py warmup                        # rozgrzewka embeddingów/rerankera
python manage.py runserver
```

Import fragmentu korpusu: `fetch_sources oshb --books Gen Isa Ps`, potem
`import_corpus oshb --books Gen Isa Ps`. Po zmianie modelu embeddingów:
`reindex_chunks --recreate`; po zmianie mappingu bez zmiany modelu:
`reindex_chunks --recreate --reuse-vectors`; po zmianie chunkowania:
`reingest_documents` + `reindex_chunks --recreate`.

## Zapytania, które już działają

```python
from corpus.sigla import parse, ordinal_range
from corpus.models import VerseText, Token

s, e = ordinal_range(parse("Mk 1,1-8")[0])
VerseText.objects.filter(verse__ordinal__range=(s, e)).select_related("work")

Token.objects.filter(lemma_norm="αρχη")  # konkordancja po lemacie greckim
Token.objects.filter(strong="H430")  # po Strongu hebrajskim
Token.objects.filter(surface_norm="בראשית")
```

## Zmienne .env

| zmienna | domyślnie | uwagi |
|---|---|---|
| `SECRET_KEY` | dev-only | |
| `DEBUG` | true | |
| `ALLOWED_HOSTS` | localhost,127.0.0.1 | |
| `CSRF_TRUSTED_ORIGINS` | (puste) | wymagane za Cloudflare/nginx |
| `DATABASE_URL` | sqlite:///db.sqlite3 | prod: `postgres://user:pass@host:5432/scriptura` |
| `DATA_DIR` | ./data | katalog pobranych korpusów |
| `SEARCH_BACKEND` | db | `opensearch` na dev z dockerem i na prod |
| `OPENSEARCH_URL` | http://localhost:9200 | |
| `OPENSEARCH_USER` / `OPENSEARCH_PASSWORD` | (puste) | dev bez security plugin |
| `OPENSEARCH_VERIFY_CERTS` | true | |
| `OPENSEARCH_INDEX_PREFIX` | scriptura | indeksy `scriptura-verses`, `scriptura-chunks` |
| `MANIFESTS_DIR` | ./data/manifests | |
| `HARVEST_MAILTO` | (puste) | e-mail do nagłówków (OpenAlex polite pool, UA dla DSpace) |
| `LLM_BACKEND` | echo | `ollama` na dev/prod |
| `OLLAMA_BASE_URL` | http://localhost:11434 | |
| `OLLAMA_API_KEY` | (puste) | Ollama Cloud |
| `OLLAMA_CHAT_MODEL` | qwen3.5:122b-cloud | |
| `OLLAMA_EMBED_MODEL` | snowflake-arctic-embed2 | |
| `EMBEDDING_DIM` | 1024 | wymiar `knn_vector`; zmiana = `reindex_chunks --recreate` |
| `EMBEDDING_QUERY_PREFIX` | `query: ` | prefiks tylko do zapytań (Qwen3-Emb.: `Instruct: …\nQuery: `, bge-m3: pusty) |
| `RAG_THINK` | false | tryb myślenia modelu czatu |
| `RERANKER_BACKEND` | none | `tei` na dev/prod |
| `TEI_RERANK_URL` | http://localhost:8081 | |
| `RERANK_TOP_N` | 20 | kandydaci z RRF do rerankera |
| `RERANK_MAX_CHARS` | 1800 | przycięcie tekstu pary |
| `RAG_TOP_K` | 8 | chunków w kontekście |
| `RAG_CONTEXT_NEIGHBORS` | 1 | chunki order±N doklejane do trafień (0 = wyłączone) |
| `RAG_TRANSLATE_QUERY` | true | tłumaczenie pytania na angielski do BM25/kNN po dokumentach en |
| `RAG_TRANSLATE_MODEL` | (puste) | model do tłumaczeń zapytań; pusty = `OLLAMA_CHAT_MODEL` |
| `RAG_RELATED_VERSES` | 6 | powiązane wersety w prompcie (0 = wyłączone) |
| `RAG_ANE_PASSAGES` | 3 | pasaże ANE w prompcie (0 = baza ANE wyłączona) |
| `RAG_PATRISTIC_PASSAGES` | 4 | pasaże Ojców w prompcie (0 = wyłączone) |
| `RERANK_LLM_MIN_SCORE` / `RERANK_LLM_MAX_CHARS` | 1.0 / 700 | reranker `llm`: próg odrzucenia (0–3) i długość fragmentu |
| `RAG_MAX_PER_DOC` | 3 | max chunków z jednego dokumentu w top-k (0 = bez limitu) |
| `RAG_REGISTER_MODE` | soft | `soft`: tryb = poziom odpowiedzi + premia rankingowa; `hard`: filtr rejestru w trybie naukowym |
| `TRANSLATE_MODEL` | translategemma:27b | przekład chunków obcych (`translate_chunks`), lokalnie |
| `TRANSLATE_BASE_URL` | (puste) | Ollama do przekładów; pusty = `OLLAMA_BASE_URL` |
| `CURATE_MODEL` | (puste) | model kuratora; pusty = `OLLAMA_CHAT_MODEL` |
| `CURATE_REVIEW_CONFIDENCE` | 0.5 | model niepewny → `review` (manifest) / pominięcie usunięcia (audyt) |
| `RAG_DEFAULT_MODE` | scientific | `popular` dla instancji publicznej |
| `RAG_ACCESS` | open | poziomy dostępu widoczne dla retrievalu, np. `open,licensed` |
| `RAG_VERSE_WORKS` | BG1632,SBLGNT,WLC,LXX | dzieła, z których wersety trafiają do promptu |
| `RAG_TEMPERATURE` / `RAG_NUM_CTX` | 0.2 / 16384 | |
| `DEMO_TOKEN` | (puste) | jeśli ustawiony, `/ask/stream` wymaga `X-Demo-Token` |
| `ALLOW_ANONYMOUS` | true | `false` = wymagane logowanie |
| `ANONYMOUS_POPULAR_ONLY` | true | anonimowi tylko w trybie popularnym |
| `PERSONAL_MAX_MB` | 25 | limit rozmiaru pliku w materiałach osobistych |
| `PERSONAL_MAX_DOCS` | 50 | limit liczby materiałów na konto |
| `BEHIND_PROXY` | false | `true` za Cloudflare/nginx |
| `RATE_LIMIT_ANON` / `RATE_LIMIT_USER` | 20 / 200 | pytań na godzinę (IP / konto); 0 = bez limitu |
| `QUESTION_MAX_CHARS` | 1000 | maksymalna długość pytania |
| `CACHE_DIR` | (puste) | katalog cache plikowego (limity); pusty = `.cache` w projekcie |
| `LOG_LEVEL` | INFO | |

## Sprint 2 — widok równoległy, konkordancja, wyszukiwanie

- **`/ref/?q=Mk 1,1-8&works=WLC,SBLGNT,BG1632`** — wersety × dzieła w tabeli;
  tokeny oryginałów są klikalne (lemat → konkordancja; WLC: Strong),
  tooltip pokazuje lemat i kod morfologiczny. Checkboxy dzieł przeładowują
  panel przez HTMX.
- **`/concordance/?lemma=λόγος | ?strong=H430 | ?form=בראשית`** — wystąpienia
  z rozkładem po księgach, paginacja 50/str., podświetlenie formy.
- **`/search/?q=…`** — składnia: słowa (AND), `"fraza"`, `-wykluczone`,
  `H430`/`G746`, `lemma:λόγος`. Strong/lemat zawęża do *wersetów* (w dowolnym
  dziele), słowa filtrują teksty w wybranych dziełach, więc `H430 niebo`
  znajduje wersety z אלהים po hebrajsku i „niebo" po polsku.
- Backend: na PostgreSQL lookup `__search` (config `simple`) z indeksem GIN
  (migracja 0002, no-op na SQLite); na SQLite fallback `contains`.
- Warstwa serwisowa `corpus/services/text.py` (parallel / concordance /
  lexical) — widoki są cienkie, testy trafiają w serwisy.
- Front: Django templates + HTMX (cdnjs), bez frameworka CSS; hebrajski RTL.

## Sprint 2b — OpenSearch jako backend wyszukiwania

Decyzja: jeden silnik dla wersetów (teraz) i chunków literatury z wektorami
(sprint 3) — OpenSearch zamiast Qdrant + Postgres FTS. Mikrus 4.2 PRO (16 GB)
to udźwignie; heap 2 GB w `docker-compose.yml`.

- `docker compose up -d --build` — OpenSearch 2.19 z pluginami `analysis-stempel`
  (polski stemmer) i `analysis-icu` (folding akcentów greki i nikud hebrajskiego).
- `python manage.py index_search --recreate` — indeks `<prefix>-verses`,
  dokument = werset × dzieło, pola `text_pl` / `text_grc` / `text_hbo` z właściwym
  analizatorem + `text` dosłowne; `lemmas[]`, `strongs[]`, `forms[]`
  zdenormalizowane per werset ze wszystkich dzieł (zapytania międzykorpusowe
  jednym `bool`).
- Gramatyka (`corpus/search/grammar.py`) wspólna dla obu backendów; nowość:
  `a NEAR/3 b` → `intervals` z `max_gaps`, `ordered: false`.
- `SEARCH_BACKEND=db` zostaje jako fallback (testy, awaria klastra): NEAR
  degraduje do AND, bez fleksji.
- `scripts/opensearch_smoke.py` — test na żywym klastrze (pluginy, analizatory,
  zapytania). **Ścieżka OpenSearch była testowana tylko jednostkowo (DSL, mapping,
  build_docs) — uruchom smoke po pierwszym `docker compose up`.**

## Sprint 3 — literatura + RAG + GUI

- App **`library`**: `Author`, `Document` (typ, czasopismo, DOI, licencja,
  `access`), `Chunk` (sekcja, strony, `sigla` jako zakresy ordinal).
  `manage.py ingest_document --file x.pdf --title … --author … --type article
  --journal … --year … --license "CC BY 4.0" --access open` — PDF (PyMuPDF) /
  .md / .txt → sekcje po nagłówkach → chunki ~1400 zn. z nakładką → sigla
  (`corpus.sigla.extract`) → indeks `chunks` w OpenSearch (`knn_vector` bge-m3
  przez Ollamę, engine lucene) i BM25; **hybryda = RRF w Pythonie**.
  Retrieval filtruje po `access`, autorach i przecięciu sigli (`long_range`).
  Fallback `SEARCH_BACKEND=db`: prefiksowe trafienia słów + sigla, bez wektorów.
- App **`rag`**: `service.ask()` — chunki + wersety z sigli w pytaniu → prompt
  (tekst / egzegeza / interpretacja, cytuj [n], nie wymyślaj wersetów) →
  strumień z Ollamy (`/api/chat`, lokalnie lub Cloud przez `OLLAMA_API_KEY`).
  Po odpowiedzi: cytowane [n], sigla zweryfikowane z korpusem (`unverified_refs`
  = wymyślone). `LLM_BACKEND=echo` — bez modelu, do testów i demo offline.
- **GUI** `/` — wątek rozmowy, panel źródeł (literatura [n] + wersety, hebrajski
  RTL), kompozytor z filtrem autora i wyborem dzieł, SSE (`POST /ask/stream`),
  opcjonalny `DEMO_TOKEN` (nagłówek `X-Demo-Token`). Widoki tekstu przeniesione
  pod `/text/`.
- 44 testy; ścieżka db+echo przetestowana end-to-end na przykładowym artykule.

## Sprint 4 — modele wg PIRB, reranker, terminy oryginalne, ewaluacja

Decyzje na podstawie tablicy PIRB (NDCG@10 / Recall@100 po polsku):
- **Embeddingi: `snowflake-arctic-embed2`** (arctic-embed-l-v2.0, 568M, 1024-d,
  8k ctx, Apache 2.0) przez Ollamę — kompromis polsko-angielski: 59,2 NDCG po
  polsku (jak Qwen3-Embedding-4B przy 7× mniejszym modelu), angielski bez strat,
  tokenizator XLM-R zna grekę i hebrajski. Prefiks zapytania `query: `, dokumenty
  bez. Alternatywy do A/B: `sdadas/stella-pl-retrieval-8k` (62,7, dwujęzyczny
  z destylacji, 1,5B — ciężki na CPU), `OPI-PIB/PolDense-400M` (63,2, tylko polski).
  Odrzucone: `qwen3-embedding:0.6b` (50,7 ≈ BM25), `bge-m3` (56,0).
- **Reranker: `BAAI/bge-reranker-v2-m3`** przez TEI (usługa `reranker` w
  `docker-compose.yml`, port 8081). Dla korpusu czysto polskiego podmień na
  `sdadas/polish-reranker-roberta-v3` (66,2 vs 61,7 po polsku) jedną linią
  w composie. Reranker działa nad pulą `RERANK_TOP_N` z RRF; przy awarii usługi
  zostaje kolejność RRF (log warning, nie błąd).
- **Chat: `qwen3.5:122b-cloud`** (Ollama Cloud, `OLLAMA_API_KEY`), `RAG_THINK=false`
  wyłącza tryb myślenia; do porównania `gemma4:31b-cloud`.
- **Terminy oryginalne**: `terms_grc[]` / `terms_hbo[]` w indeksie `chunks`
  (słowa greckie/hebrajskie z chunka, znormalizowane jak w korpusie); grecki lub
  hebrajski wyraz w pytaniu daje `term` z boostem 3.0 — deterministycznie, obok
  BM25 i kNN.
- `manage.py reindex_chunks --recreate [--suffix arctic]` — przebudowa indeksu
  po zmianie modelu (wymiar `knn_vector` jest zamrożony w mappingu); `--suffix`
  tworzy osobny indeks do porównań.
- `scripts/eval_retrieval.py data/eval/questions.jsonl [--no-rerank]` —
  recall@k / MRR / hit@1 per typ pytania (`pl`, `pl+orig`, `pl->en`);
  format w `data/eval/questions.example.jsonl`. Uruchom z i bez `--no-rerank`,
  żeby zobaczyć wkład rerankera.

## Sprint 5 — harvest źródeł otwartych

Scholar mówi, *co istnieje*; o tym, *co wolno*, mówią repozytoria i wydawcy.
Manifest JSONL (`data/manifests/*.jsonl`) jest miejscem ręcznej decyzji
licencyjnej przed pobraniem czegokolwiek (`access`: open / licensed / private / skip).

- `manage.py harvest dspace --query 'author:"Majewski, Marcin"'` — DSpace 7 REST
  (domyślnie OPEN ICM UW, `open.icm.edu.pl`, gdzie autorzy sami deponują teksty
  i wybierają licencję — leżą tam m.in. całe monografie); czyta `dc.rights`
  i `dc.rights.uri`, znajduje PDF w bundle ORIGINAL.
- `manage.py harvest dspace --browse-author "Majewski, Marcin"` — po kanonicznej
  wartości autora (jak `/browse/author?value=…`), dokładne dopasowanie; `--query`
  zostaje do wyszukiwania pełnotekstowego.
- `manage.py harvest bibtex --file citations.bib` — eksport z profilu Google
  Scholar (zaznacz wszystkie → Eksportuj → BibTeX): lista „co istnieje", bez
  plików i licencji (`access=licensed`). `scripts/manifest_diff.py scholar.jsonl
  open.jsonl openalex.jsonl` wypisuje pozycje bez odpowiednika w źródłach
  otwartych — to gotowa lista do rozmowy z autorem.
- `manage.py harvest openalex --author "Marcin Majewski"` — kandydaci z afiliacją;
  potem `--author-id A… --only-oa` — prace z `open_access`, licencją i `pdf_url`.
- `manage.py ingest_manifest data/manifests/x.jsonl [--access open licensed] [--dry-run]`
  — pobiera PDF-y do `DATA_DIR/library`, woła `ingest_document` z metadanymi
  z manifestu, pomija już zaindeksowane (po `source_id` w `source_path`).
- Heurystyka licencji (`normalize_license`): CC-* i CC0 → `open`; inny zapis praw →
  `licensed`; brak → `licensed` (do decyzji). Dalsze źródła do dodania tym samym
  wzorcem: OAI-PMH czasopism na OJS (RBL, WPT, Biblical Annals, Verbum Vitae),
  Crossref (`license[]`), ORCID.

Testy harvestu działają na utrwalonych kształtach odpowiedzi API (sandbox bez
dostępu do sieci); pierwsze uruchomienie na żywo zweryfikuje nazwy pól DSpace
w konkretnej instancji.

## Sprint 6 — jakość korpusu: fonty legacy, przypisy, cytaty

Wnioski z pierwszego realnego korpusu (30 tekstów Majewskiego, 3 monografie):
- **Hebrajski w fontach BibleWorks** (`Bwhebb`, składy 2008–2012) nie jest Unicode:
  ekstrakcja daje ciąg klawiszy od lewej (`dApae` = אֵפֹד). `library/legacy_fonts.py`
  dekoduje spany w tym foncie (tabela wyprowadzona z próbek, 24 testy): odwrócenie,
  samogłoski dolne przed spółgłoską, cholem `A` za nią, NFC. Efekt: chunków z hebrajskim
  202 → 432, Mieszkanie Chwały 0 → 119. Diagnostyka fontów: `scripts/legacy_fonts.py
  plik.pdf [--dump bwhebb]`.
- **Transliteracje**: pole `text_fold` w indeksie chunków (filtr `translit`: š→sz, ḥ→ch,
  ṣ→c…, potem ICU folding) — *miszkan* w pytaniu trafia w *miškān* w tekście.
- **Przypisy i żywe paginy**: nagłówek numerowany wymaga kropki po numerze, linie
  z markerami bibliograficznymi nie są sekcjami, linie powtarzające się na ≥25 % stron
  (po usunięciu cyfr) są usuwane; przyklejone numery przypisów w siglach odrzucane
  limitami rozdział ≤ 150 / werset ≤ 176 i odwróconych zakresów.
- **Weryfikacja treści cytatów** (`rag/quotes.py`): cytaty w cudzysłowach i po siglum
  porównywane z wersetami i chunkami; badge „cytaty zgodne z korpusem N/M" i osobno
  „cytat zmieniony" (parafraza/przekład z pamięci modelu podany jako cytat).
- Komendy: `reingest_documents` (nowe chunkowanie z plików), `recompute_sigla`,
  `reindex_chunks --recreate --reuse-vectors` (zmiana mappingu bez liczenia wektorów).
- `conftest.py` wymusza backendy zastępcze w testach niezależnie od `.env`.

## Sprint 7 — Septuaginta

- `fetch_sources lxx` + `import_corpus lxx` — Rahlfs 1935 z repozytorium Elirana Wonga
  (CC BY-NC-SA 4.0; tekst bazowy CCAT/CATSS wymaga wysłania deklaracji użytkownika —
  link w README repozytorium). Tokeny z lematem (mapa lexid→lemat z plików OSSP),
  Strongiem greckim (gdzie istnieje) i morfologią CATSS.
- Wersyfikacja: Psalmy przeliczone na numerację masorecką (LXX 9 = MT 9+10, 113 =
  114+115, 114+115 = 116, 146+147 = 147, przesunięcia +1 pomiędzy); List Jeremiasza
  = Ba 6, Zuzanna = Dn 13 (OG zaczyna od w. 6), Bel = Dn 14; 1 Ezd, 3–4 Mch, PsSal,
  Ody pominięte. Jr, Wj 36–40, Prz, Hi — kolejność LXX bez mapowania
  (`Work.versification = "lxx"`).
- `RAG_VERSE_WORKS` i widok równoległy domyślnie z LXX.

## Sprint 8 — dwa tryby: naukowy i popularnonaukowy

Jeden korpus, jedno pole `Document.register` (scientific / popular / mixed; domyślnie wg
`doc_type`: artykuł i książka → naukowy, skrypt → mieszany, blog i wideo → popularny;
`ingest_document --register`, kolumna `register` w manifeście), dwa tryby odpowiedzi:
- **naukowy** — retrieval filtruje źródła naukowe i mieszane; prompt: terminologia,
  oryginał z transliteracją, stan dyskusji i rozbieżności, 2–5 akapitów;
- **popularnonaukowy** — wszystkie źródła (popularne oznaczone w panelu); prompt: bez
  żargonu, transliteracje, spory tylko na żądanie, 1–3 akapity, nadal wyłącznie ze
  źródeł i z cytowaniami.
Przełącznik w GUI (`mode` w `POST /ask/stream`), domyślny tryb `RAG_DEFAULT_MODE`.
Instancja publiczna = `popular` + `RAG_ACCESS=open`; instancja dla PWT = `scientific`
+ `open,licensed` — ten sam kod, dwa `.env`. Zmiana mappingu (`register` w indeksie):
`reindex_chunks --recreate --reuse-vectors`.

## Sprint 9 — konta, uprawnienia, historia rozmów

- Konta na Django auth (`/login/`, `/logout/`, tworzenie w adminie lub `createsuperuser`).
  Poziomy dostępu do źródeł to **grupy**: `scriptura-licensed` (dokumenty za zgodą
  autorów), `scriptura-private` (demo prywatne); każdy zalogowany widzi `open`,
  superużytkownik wszystko. Retrieval filtruje po poziomach użytkownika, nie po
  `RAG_ACCESS` z `.env` (ten zostaje dla skryptów/ewaluacji).
- Anonimowi: `ALLOW_ANONYMOUS=true` → mogą pytać, tylko `open` i tylko tryb popularny
  (`ANONYMOUS_POPULAR_ONLY`); `false` → przekierowanie do logowania.
- Historia: `Conversation` → `Message` (pytanie z filtrami; odpowiedź z pełnym JSON-em
  wyniku i źródłami). Zapis po zdarzeniu `done`; `conversation_id` wraca w `done`
  i GUI dokleja kolejne pytania do tej samej rozmowy; „Nowa rozmowa" zaczyna nową.
  Endpointy: `GET /conversations/`, `GET /conversations/<id>/`,
  `POST /conversations/<id>/delete/` (tylko właściciel). W GUI: „Historia" w nagłówku
  wczytuje rozmowę z odpowiedziami, badge'ami i źródłami; admin: rozmowy z inline
  wiadomości.

## Sprint 10 — ewaluacja i decyzja o rerankerze

Pierwszy pomiar (24 pytania syntetyczne + `pl+orig`, korpus Majewskiego):
RRF recall@5 0,875 / MRR 0,654; z bge-reranker-v2-m3 0,958 / 0,699 — przy koszcie ~15 s
na pytanie na 16 rdzeniach CPU. Wszystkie pudła to **sąsiednie chunki** właściwego
dokumentu (błąd granicy, nie rankingu). Stąd:
- `RAG_CONTEXT_NEIGHBORS=1` — do trafień doklejane są chunki order±1 z bazy (jeden
  ciągły fragment w prompcie, bez dodatkowych pozycji [n]);
- `eval_retrieval.py` raportuje `near±1` obok recall@k;
- `eval_bootstrap` generuje pytania z losowych chunków (`--documents`, `--per-document`);
- reranker na produkcji (Mikrus, 2 vCPU) wyłączony (`RERANKER_BACKEND=none`); do dema
  z maszyny z GPU: `services/reranker_rocm/` — FastAPI + sentence-transformers z API
  zgodnym z TEI (`/rerank`), na ROCm/CUDA ~0,5 s na 20 par.

## Sprint 11 — kanał polsko-angielski

Pomiar na 30 pytaniach: `pl->en` bez rerankera 0,33 recall@5, z rerankerem 0,67 — angielskie
chunki są w puli, ale przegrywają ranking z polskimi o tym samym temacie. Przyczyny: angielskie
teksty szły przez polski stemmer, a polskie pytanie nie ma po co trafiać w angielski BM25.
- pole `text_en` (analizator `english`) dla dokumentów `language=en`;
- tłumaczenie zapytania (`library/translate.py`, jedno wywołanie modelu czatu z cache,
  `RAG_TRANSLATE_QUERY`, opcjonalnie lżejszy `RAG_TRANSLATE_MODEL`), tylko gdy korpus ma
  dokumenty angielskie i pytanie wygląda na polskie;
- BM25 po polsku i po angielsku w jednym zapytaniu (`minimum_should_match: 1`), drugi kNN
  na wektorze angielskiej wersji pytania — trzy listy do RRF; `translate_ms` w timings.
Zmiana mappingu: `reindex_chunks --recreate --reuse-vectors`.

## Sprint 12 — przekład chunków obcych na polski (TranslateGemma)

Zamiast tłumaczyć zapytanie w locie: angielskie (i inne obce) chunki są raz, przy ingestii,
tłumaczone lokalnie modelem TranslateGemma (`translate_chunks`, `translategemma:27b` przez Ollamę,
prompt w formacie wymaganym przez model). Przekład ląduje w `Chunk.text_pl` i służy **wyłącznie
wyszukiwaniu**: pola `text`/`text_fold` i embedding liczone z polskiego, oryginał w `text_exact`
i `text_en`, w prompcie, panelu źródeł i weryfikacji cytatów. Powody: wiarygodność (użytkownik
widzi, co napisał autor) i licencje (CC BY-ND zabrania rozpowszechniania utworów zależnych —
wewnętrzny indeks nim nie jest). Tłumaczenie zapytania (`RAG_TRANSLATE_QUERY`) zostaje jako
fallback dla chunków bez przekładu.
Kolejność: `translate_chunks` → `reindex_chunks --recreate` (embeddingi z tekstu polskiego).

## Sprint 13 — kurator i aktualizacje automatyczne

- `library/curate.py` — ocena wstępna pozycji manifestu: reguły (sprawozdania, wstępniaki,
  dublety po DOI/tytule) + model (`format: json`): czy to biblistyka (imiennik?), typ
  (artykuł/recenzja/sprawozdanie/wstępniak/książka), rejestr, język. **Model nie decyduje
  o licencji ani dostępie** — może tylko dać `skip` (pewny szum, `CURATE_AUTO_CONFIDENCE`)
  lub `review` (niepewne, `CURATE_REVIEW_CONFIDENCE`); reszta zostaje wg licencji.
  `curate_manifest plik --author … [--apply]`.
- Kurator dostaje abstrakt (OpenAlex `abstract_inverted_index`, DSpace `dc.description.abstract`),
  sam poprawia błędne czasopisma (`journal_fix`) i typ dokumentu; `skip` przy `relevant=false`,
  `review` tylko gdy model zgłasza niepewność (`confidence < CURATE_REVIEW_CONFIDENCE`).
- `audit_corpus [--documents …] [--apply] [--delete]` — ocena **zaindeksowanych** dokumentów
  po treści (tytuł + początek tekstu): istotność, rejestr, język, typ; `--apply` poprawia
  metadane, `--delete` usuwa nieistotne (imienników) i reindeksuje; raport `audit-<data>.md`.
  `update_corpus` uruchamia audyt na nowych dokumentach po ingestii — druga kontrola po treści.
- `update_corpus` — dla autorów z `data/authors.jsonl` (wzór: `authors.example.jsonl`):
  przyrostowy harvest (OpenAlex/DSpace/Scholar; nowe = nieznane `source_id`), kurator,
  `ingest_manifest --access open --no-index`, `translate_chunks`, `hash_documents`,
  `reindex_chunks --documents <nowe>`, raport Markdown w `data/manifests/report-<data>.md`
  (✔ zaindeksowane, ? do decyzji, ! wymaga zgody, – pominięte) + podpowiedź `eval_bootstrap`.
  `--dry-run` = harvest + kurator + raport. Do timera systemd/crona.

## Sprint 15 — konto i materiały osobiste

- `/account/`: dane (imię, nazwisko, e-mail), zmiana hasła, „Moje materiały".
- Materiały osobiste: `Document.owner` + `access=personal`; plik (PDF/TXT/MD) trafia do
  `DATA_DIR/library/users/<id>/`, chunki/sigla/indeks tym samym pipeline'em, bez przekładu
  i bez kuratora (to prywatny obszar). Widoczne **wyłącznie** dla właściciela — w retrievalu
  (filtr `access ∈ poziomy` LUB `personal ∧ owner_id`), w panelu źródeł (badge „moje")
  i na stronie konta; anonimowi i inni użytkownicy nigdy ich nie dostają. Dedup po MD5
  w obrębie konta, limity `PERSONAL_MAX_MB` / `PERSONAL_MAX_DOCS`. W czacie opcja
  „tylko moje materiały" (`personal_only` w `POST /ask/stream`).
- Usunięcie materiału usuwa plik, chunki i wpisy z indeksu (`delete_document_from_index`).
Przeznaczenie: materiały z licencją osobistą (kursy, webinary, notatki), których nie wolno
włączyć do wspólnego korpusu.

## Sprint 16 — zgody na udostępnienie i leksykon STEPBible

- **Udostępnienie materiału osobistego do wspólnego korpusu**: na stronie konta „Udostępnij"
  → oświadczenie (autorstwo/prawo do dysponowania, zakres cytowania, możliwość wycofania),
  rejestr, licencja → `Consent` (kto, kiedy, treść, licencja) i `access: personal → licensed`;
  dokument zostaje przy właścicielu, „Wycofaj zgodę" cofa do `personal` i oznacza zgodę
  jako wycofaną. Admin: `Zgody`. To mechanizm, którym autor sam włącza swoje teksty.
- **Leksykon STEPBible** (Tyndale House, CC BY 4.0): `fetch_sources step` + `import_lexicon`
  → tabela `Lexeme` (20 192 haseł: Strong → lemat, transliteracja, morph, glosa, skrócona
  definicja; TBESH hebrajski/aramejski, TBESG grecki) i **uzupełnienie pustych lematów WLC**
  po Strongu (OSHB daje same Strongi; na Rdz 97 % tokenów dostaje lemat). Glosa w nagłówku
  konkordancji; w prompcie sekcja LEKSYKON dla terminów oryginalnych/Strongów z pytania.
  Strongi normalizowane jak w OSHB (`H0001 → H1`, `H1254a` bez zmian).

## Sprint 17 — powiązane wersety (OpenBible cross-references)

- `fetch_sources xref` (cross-references.zip z openbible.info, CC BY; rdzeń: Treasury of
  Scripture Knowledge) → `import_xref [--min-votes N]` → tabela `VerseLink`
  (`from_ordinal → [to_start, to_end], votes`).
- Użycie: w widoku tekstu sekcja „Powiązane wersety" (klikalne sigla), w RAG sekcja
  POWIĄZANE WERSETY w prompcie (top `RAG_RELATED_VERSES` wg głosów, brzmienie z pierwszego
  polskiego dzieła; instrukcja: tylko trop, cytować wyłącznie z WERSETY) i w panelu źródeł.
  Retrieval literatury bez zmian (powiązania nie rozszerzają filtra sigli — to celowo, żeby
  nie dociągać literatury o innych miejscach).

## Sprint 18 — baza powiązana ANE (teksty starożytnego Bliskiego Wschodu)

Osobna aplikacja `ane` — **nie miesza się** z Biblią ani literaturą: własne modele
(`AneText` → `AneChapter` → `AneLine`, pasaże `AnePassage` po ~12 linii), własny indeks
OpenSearch (`ane`), własny retrieval, osobna sekcja promptu („TEKSTY PORÓWNAWCZE ANE — nie są
tekstem biblijnym ani literaturą; cytuj z oznaczeniem tekstu i linii") i osobny typ źródła
w GUI (badge ANE). Włączane **jawnie** (opcja „teksty ANE" w kompozytorze, `include_ane`
w API) albo **automatycznie**, gdy pytanie wymienia teksty/postaci z `ANE_TRIGGERS`
(Gilgamesz, Utnapisztim, Enuma Elisz, Tiamat, Marduk, Atrahasis, Ugarit, Baal, Aqhat…).
- Źródło pierwsze: **eBL** (electronic Babylonian Library, LMU; CC BY-NC-SA 4.0 — użytek
  niekomercyjny). `import_ane L/1/4 --name-pl Gilgamesz [--stages "Standard Babylonian"]`
  pobiera tekst i rozdziały z API (cache JSON w `data/ane/<id>/`, `--from-dir` bez sieci),
  parsuje `variants[0].reconstruction` (normalizacja) i `translation` (`#tr.en:`).
  Odniesienia w stylu „Gilgamesz SB XI 11–22".
- `translate_ane` — przekład pasaży na polski do wyszukiwania (ten sam model co
  `translate_chunks`); `index_ane --recreate`.
- ORACC (CC0/BY-SA; JSON bez przekładów) — do dołożenia dla inskrypcji i listów, gdy będzie
  potrzeba; ETCSL/COS bez otwartej licencji — nie.

## Sprint 19 — przygotowanie do wdrożenia (Mikrus)

- Produkcja: `BEHIND_PROXY` (X-Forwarded-Proto za Cloudflare/nginx, bezpieczne ciasteczka, bez
  redirectu na https), WhiteNoise dla statyk admina, cache plikowy.
- Limity: `RATE_LIMIT_ANON` (per IP, na godzinę), `RATE_LIMIT_USER` (per konto),
  `QUESTION_MAX_CHARS`; przekroczenie → 429/400 przed jakimkolwiek wywołaniem chmury.
- `export_vectors` → `reindex_chunks --recreate --vectors-file …` / `index_ane --vectors-file …`:
  przenoszenie embeddingów między hostami bez liczenia (5 tys. chunków na 2 vCPU ≈ 1,5 h).
- `deploy/`: unit `scriptura-web` (gunicorn gthread, timeout 300 dla SSE, warmup po starcie),
  `scriptura-update` + timer (cotygodniowy `update_corpus`), nginx (port 40021 → 8534,
  `proxy_buffering off`, `read_timeout 300`), `env.production.example`, `deploy.sh`
  (rsync + migrate + collectstatic + restart; `--data` z PDF-ami i wektorami), README krok po kroku.

## Sprint 20 — harvest bloga autora (WordPress)

- `harvest blog --site https://majewskimarcin.pl --author "Marcin Majewski" [--since YYYY-MM-DD]`
  → REST API `/wp-json/wp/v2/posts`, treść HTML → tekst ze strukturą (nagłówki `##`, cytaty `>`,
  listy) w `data/library/blog/<host>/<slug>.md`, manifest z `local_file` (ingestia bez pobierania),
  `doc_type=blog`, `register=popular`, `access=licensed` (blog publiczny, ale „wszelkie prawa
  zastrzeżone” — do korpusu po zgodzie; wtedy `ingest_manifest … --access licensed` albo
  zmiana `access` w manifeście na `open`).
- `update_corpus`: pole `blog` (+ `blog_access`) w `data/authors.jsonl` dociąga nowe wpisy
  przyrostowo razem z publikacjami.
- Lista frekwencyjna BH Majewskiego (BibleWorks 10) odtwarza się z naszej bazy (OSHB + STEP):
  מִשְׁכָּן 139 = 139, רֵאשִׁית 51 = 51, אֵת 11 871 vs 11 873; różnice = konwencje tagowania
  (H3068/H3069, rozdzielone prefiksy).

## Sprint 21 — Ojcowie Kościoła (baza powiązana) i eksport cytowań

- Aplikacja `patristics`: ANF/NPNF (37 tomów, przekłady angielskie XIX w., domena publiczna)
  z CCEL w ThML. `import_patristics anf01 …` / `--all` (cache XML w `data/patristics/`):
  tom → dzieła (`div1`) → pasaże (~1500 zn., sekcja „Book III / Chapter XXI”, strona z `<pb>`)
  → **odsyłacze biblijne z `<scripRef osisRef>`** jako `PatRef` (zakresy ordinali). Autor z mapy
  tomów + heurystyka tytułu; polskie formy nazwisk (`author_pl`, edytowalne w adminie).
- Retrieval dwukanałowy: (1) **po wersecie** — pasaże, których odsyłacze pokrywają sigla
  z pytania („Ojcowie o Rdz 1,26”; badge „o tym wersecie”), (2) semantyczny BM25 en/pl + kNN
  (osobny indeks `patristics`, `index_patristics --recreate`, wektory przenośne przez
  `export_vectors`). Włączane opcją „Ojcowie Kościoła” lub automatycznie przy wyzwalaczach
  (Ojcowie, tradycja, patrystyka, nazwiska). W prompcie osobna sekcja TRADYCJA PATRYSTYCZNA
  z instrukcją odróżniania interpretacji Ojców od egzegezy współczesnej.
- **Eksport cytowań**: przyciski BibTeX / RIS w panelu źródeł (`POST /export/citations`):
  tylko źródła przywołane w odpowiedzi jako [n] (gdy brak — wszystkie), pola z bazy (journal,
  volume, pages, doi, url), Ojcowie jako `@book` z `series = {ANF 1}`, ANE jako `@misc`;
  klucze `majewski2010przybytek`.

## Sprint 22 — jakość cytowań i panel źródeł

- Eksport: autorzy normalizowani do „Nazwisko, Imię” niezależnie od formy w bazie; Ojcowie jako
  `@incollection` w *Ante-Nicene Fathers*, vol. N (red. Roberts–Donaldson / Schaff, rok serii)
  z **listą stron przywołanych pasaży** (`pages = {451, 521}`), dzieła anonimowe bez autora
  (opis w `note`); filtrowanie po `[n]`, `[Pn]`, `[An]`; RIS `CHAP` z `A2` redaktorami.
- `merge_authors [--apply]` — scalanie dubletów autorów („Marcin Majewski” / „Majewski, Marcin”,
  różnice diakrytyków) z przepięciem dokumentów.
- GUI: grupy źródeł (Literatura, Wersety, Ojcowie, ANE, Powiązane) jako zwijane sekcje
  z licznikiem; stan zwinięcia pamiętany między odpowiedziami; klik w [n]/[P1] otwiera grupę.
- Ojcowie w prompcie jako [P1]…, ANE jako [A1]…; cytaty przy nich poza weryfikacją dosłowną.
- `translate_patristics [--volumes …]` — maszynowy przekład pasaży na polski (do BM25 `text_pl`
  i podglądu w panelu źródeł z oznaczeniem „przekład maszynowy”, oryginał EN rozwijany);
  model w prompcie nadal dostaje oryginał. Retrieval bez przekładu i tak działa: kanał po
  wersecie jest niezależny od języka, semantyczny tłumaczy zapytanie na angielski, a
  `snowflake-arctic-embed2` jest wielojęzyczny.

## Sprint 23 — tryb = poziom odpowiedzi, nie zbiór źródeł

- `RAG_REGISTER_MODE=soft` (domyślnie): oba tryby widzą wszystkie źródła, do których użytkownik
  ma dostęp; rejestr działa jako **premia rankingowa** (naukowy: popular ×0,8; popularny:
  scientific ×0,9) i jako **hierarchia w prompcie** (naukowy: „gdy to samo mówi publikacja
  recenzowana i tekst popularyzatorski, cytuj publikację; popularyzatorskie jako uzupełnienie
  z oznaczeniem charakteru”; popularny: „przystępny tekst tego samego autora w pierwszej
  kolejności”). Retrieval pobiera `RAG_TOP_K + 4` kandydatów i przycina po ważeniu.
- `RAG_REGISTER_MODE=hard`: dawne zachowanie (naukowy tylko scientific/mixed).
- Uzasadnienie: forma (przypisy) nie jest miarą wartości — wpis blogowy biblisty jest tekstem
  naukowym w treści; twardy filtr ukrywał źródła zamiast je hierarchizować.

## Sprint 24 — SQLite w trybie WAL

- `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `timeout=20`, `transaction_mode=IMMEDIATE`
  (ustawiane automatycznie, gdy `DATABASE_URL` wskazuje SQLite): czytelnicy nie czekają na
  piszących, kolizje zapisów czekają zamiast błędu „database is locked”. Rozmiar bazy
  (~300 MB) nie jest problemem; problemem byłby wielodostęp — Postgres (na steve141 jest
  `taxpilot-postgres`) dopiero przed otwarciem kont dla wielu użytkowników:
  `dumpdata`/`loaddata` lub `pgloader`, zmiana `DATABASE_URL`, kod gotowy (`psycopg` w `[prod]`).
- `deploy/backup-db.sh` — spójna kopia przez `sqlite3 .backup` (bezpieczna przy WAL), 14 kopii.

## Sprint 25 — jakość retrievalu

Diagnoza na 4 pytaniach (serwer, anonim): (1) bez rerankera RRF wypycha nietrafione źródła przy
pytaniach spoza korpusu, (2) 6/8 chunków z jednej pracy i dublety wydań, (3) pytania leksykalne
(„hesed”) nie sięgały do leksykonu, bo transliteracja nie była rozpoznawana jako termin.
- `RERANKER_BACKEND=llm`: model czatu (chmura) ocenia trafność kandydatów 0–3 jednym
  wywołaniem JSON; kandydaci < `RERANK_LLM_MIN_SCORE` odpadają → mniej szumu w panelu,
  wcześniejsza uczciwa odmowa. Bez GPU; ~1 wywołanie/pytanie. `RERANK_LLM_MAX_CHARS`.
- `RAG_MAX_PER_DOC=3` + `diversify()`: max chunków z jednego dokumentu w top-k; sąsiadujące
  chunki liczone jako jeden (sklejane przez sąsiadów kontekstu).
- Kanał leksykalny: `corpus/translit.py` (`canonical_translit`: hesed/chesed/ḥesed/che.sed →
  jedno), pole `Lexeme.translit_fold` (migracja `corpus 0006`, wymaga `import_lexicon`);
  transliterowany termin w pytaniu → hasło STEP z liczbą wystąpień i rozkładem po księgach
  w LEKSYKON, a gdy pytanie nie ma sigli — pierwsze wystąpienia jako WERSETY (pl + oryginał).
  Prompt: znaczenie słowa wolno objaśnić z LEKSYKON/WERSETY, gdy literatura milczy.

## Sprint 26 — wykłady z YouTube

- `harvest youtube --video <url|id>… --author "…"` (`pip install -e ".[harvest]"` → yt-dlp):
  metadane, rozdziały ze znaczników czasu w opisie, napisy wgrane albo automatyczne (pl → en),
  tekst z sekcjami „## [mm:ss] Rozdział” i akapitami co 60 s z „[mm:ss]”; manifest
  `doc_type=video`, `register=popular`, `access=licensed`, `local_file`.
- Panel źródeł: chunk z nagrania linkuje do minuty (`&t=<s>`, najdokładniejszy znacznik
  akapitu), badge „wykład” / „blog”.
- Przykłady w GUI zależne od dostępu (`access` w EXAMPLES): 9 dla anonimów na korpusie
  open, 12 dla kont `licensed`; wyzwalacze Ojców + Barnaba, Hermas, Didache, Kodeks Synajski.

## Plan kolejnych sprintów

5. **Dalsze korpusy**: LXX Rahlfs (Eliran Wong, CC BY-NC-SA), UBG, Wujek,
   Sefaria-Export (komentarze rabiniczne), BT po zgodzie Pallottinum.

## Uwagi o danych

- Wersyfikacja: wszystkie trzy źródła są w numeracji masoreckiej/protestanckiej;
  LXX będzie wymagała tabel mapujących (Ps, Dn, 1–4 Krl, 2 Ezd).
- `Token.strong` w SBLGNT jest pusty — Strongi dla NT dojdą z OpenGNT.
- `Token.lemma` w WLC jest pusty (OSHB podaje tylko Stronga) — lemat hebrajski
  dojdzie z leksykonu OSHB (`HebrewLexicon`).
