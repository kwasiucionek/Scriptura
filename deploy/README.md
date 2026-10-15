# Wdrożenie na Mikrusa (roman233) — pierwsze uruchomienie

Porty: domena Cytrusa `scriptura.cytr.us` → `roman233.mikrus.xyz:20233` (nginx; port 20233 to drugi port
współdzielonego IPv4, tylko dla Scriptury — wejście bez nazwy domeny przekierowuje na `https://scriptura.cytr.us`)
→ `127.0.0.1:8534` (gunicorn). Portfolio ma osobny port 40000, Fakturownia 30233. Dane na `/srv/scriptura` (PDF-y, baza, cache, logi), kod w `/opt/scriptura`.
`/cytrus` to wolumen współdzielony z frontem WWW (zalecane < 3 GB) — Scriptura go nie używa.

## 0. Stan roman233 (2026-10-15, przeniesienie ze steve141)
Mikrus 4.2 (pro05): 16 GB RAM, 160 GB dysku. OpenSearch we własnym kontenerze `scriptura-opensearch`
(3.7.0 + stempel + icu + knn, heap 2 GB, `127.0.0.1:9200`, dane `/srv/opensearch/data`) z
`deploy/opensearch/`. Ollama lokalna (zalogowana do chmury) z `snowflake-arctic-embed2`; czat przez tagi `-cloud`.
Na steve141 zostaje MLflow (`mlflow.cytr.us`) i kopia danych sprzed przeniesienia.

## 0a. Zasoby (ogólnie)
OpenSearch potrzebuje ~1–2 GB heapu + ok. 0,5 GB na indeksy (60 tys. chunków z wektorami 1024-d ≈ 0,5 GB).
Embeddingi lokalnie (`snowflake-arctic-embed2` przez Ollamę na CPU) — ~1 s/zapytanie.
Jeśli RAM < 4 GB: Ollama z modelem embeddingów + OpenSearch się nie zmieszczą — wtedy
`EMBEDDINGS_BACKEND=tei` z endpointem HF (CPU x1, 0,033 $/h) albo bez kNN (`SEARCH_BACKEND=db`, tylko FTS).

## 1. Serwer
```bash
ssh -p 10233 root@roman233.mikrus.xyz
apt install -y python3-venv nginx docker.io docker-compose-v2
# OpenSearch (kontener, port tylko na 127.0.0.1 — Docker omija UFW):
cp -r /opt/scriptura/deploy/opensearch /srv/opensearch && cd /srv/opensearch
install -d -o 1000 -g 1000 data && docker compose up -d --build
# Ollama (embeddingi lokalnie + proxy do chmury): curl -fsSL https://ollama.com/install.sh | sh; ollama pull snowflake-arctic-embed2; ollama signin
useradd -r -m -d /opt/scriptura -s /usr/sbin/nologin scriptura
install -d -o scriptura -g scriptura /srv/scriptura /srv/scriptura/{data,cache,logs}
chmod 755 /opt/scriptura
```

## 1a. Co jest na serwerze
Użytkownik `scriptura`, katalogi `/opt/scriptura` (klon repo) i `/srv/scriptura/{data,cache,logs}`, unity systemd
(`scriptura-web` enabled; `scriptura-update.timer` zainstalowany, wyłączony), nginx `sites-enabled/scriptura` (20233),
`/opt/scriptura/.env` (sekrety tylko tam), kontener `scriptura-opensearch`, `snowflake-arctic-embed2` w Ollamie,
cron roota `30 2 * * * /opt/scriptura/deploy/backup-db.sh`.
Przeniesienie z innego serwera: snapshot bazy (`deploy/sqlite_snapshot.py --from-settings`), rsync `data/`,
`export_vectors` → `reindex_chunks/index_ane/index_patristics --recreate --vectors-file …` (bez przeliczania wektorów).

## 1b. Git zamiast rsync dla kodu (od sprintu 26)
Kod żyje w publicznym repo na GitHubie (bez danych i sekretów); serwer ma klon w `/opt/scriptura`
z kluczem deploy (tylko odczyt). Pierwsze ustawienie:
```bash
# lokalnie (w katalogu projektu):
git init && git add -A && git commit -m "Scriptura: sprint 26" && git remote add origin https://github.com/kwasiucionek/Scriptura.git && git push -u origin master
# na serwerze (jako root): klucz deploy dla użytkownika scriptura i klon
sudo -u scriptura ssh-keygen -t ed25519 -N "" -f /opt/scriptura/.ssh/id_ed25519 -C scriptura@roman233
cat /opt/scriptura/.ssh/id_ed25519.pub     # -> GitHub: repo -> Settings -> Deploy keys (read-only)
mv /opt/scriptura /opt/scriptura.rsync-backup && sudo -u scriptura git clone git@github.com:kwasiucionek/Scriptura.git /opt/scriptura
cp /opt/scriptura.rsync-backup/.env /opt/scriptura/.env && mv /opt/scriptura.rsync-backup/.venv /opt/scriptura/.venv && chown -R scriptura:scriptura /opt/scriptura
```
Potem każde wdrożenie to `./deploy/deploy.sh` (commit → push → pull na serwerze → migrate → restart);
`--data --replace-db` dokłada rsync danych i zastępuje bazę. Dane (`data/`, `db.sqlite3`, `.env`) nigdy nie są w repo (`.gitignore`).

**Uwaga: zastąpienie bazy nadpisuje produkcyjne konta, zgody i historię rozmów.**
Flaga `--data` bez `--replace-db` jest odrzucana przed transferem. Dla przyrostowych
aktualizacji źródeł korzystaj z `manage.py update_corpus` na serwerze, zamiast podmieniać bazę.

Transfer tworzy lokalny snapshot przez SQLite Backup API (uwzględnia WAL i `DATABASE_URL`),
zatrzymuje web oraz timer/usługę aktualizacji, a przed zastąpieniem tworzy spójną kopię
produkcyjnej bazy w `/srv/scriptura/backups/`. Przy błędzie nie startuje usług na
częściowo przeniesionych danych. Po sprawdzeniu transferu uruchom `setup_after_rsync.sh`:
przebuduje indeksy i uruchomi web oraz wcześniej włączony timer. Inne procesy zapisujące
bazę trzeba zatrzymać samodzielnie; skrypt zna tylko unity Scriptury.

Backupy sprzed wdrożeń są przechowywane bez automatycznego usuwania. Zaplanuj ich
retencję zgodnie z dostępnym dyskiem, osobną kopię poza VPS i próbę odtworzenia.

## 2. Kod, środowisko, .env
```bash
# z komputera: ./deploy/deploy.sh --data --replace-db (świadome zastąpienie bazy)
# na serwerze:
cd /opt/scriptura && sudo -u scriptura python3 -m venv .venv && sudo -u scriptura .venv/bin/pip install -e ".[prod]"
cp deploy/env.production.example .env && nano .env      # SECRET_KEY, OLLAMA_API_KEY, HARVEST_MAILTO
chown scriptura:scriptura .env && chmod 600 .env
```

## 3. Dane
Baza SQLite z komputera deweloperskiego obejmuje korpus, konta, zgody i historię.
Nie kopiuj samego `db.sqlite3` przy WAL i nie usuwaj plików WAL istniejącej bazy.
Bezpieczny transfer z backupem produkcji: `./deploy/deploy.sh --data --replace-db`.
Sam snapshot, bez transferu:
```bash
.venv/bin/python deploy/sqlite_snapshot.py --from-settings --output /tmp/scriptura-snapshot.sqlite3
```
Narzędzie odrzuca nadpisanie istniejącego pliku docelowego.

(albo od zera: `fetch_sources && import_corpus oshb/morphgnt/lxx/json`, `import_lexicon`, `import_xref`, `ingest_manifest` …).
Geografia (niezależna od bazy z komputera, ok. 2 s + tłumaczenie nazw modelem czatu):
`fetch_sources places && import_places && translate_places`.
Indeksy: `index_search --recreate` (wersety, bez wektorów, ~2 min) i `reindex_chunks --recreate --vectors-file /srv/scriptura/data/vectors.jsonl.gz`
(wektory z pliku — bez liczenia; 60 tys. chunków wczytuje się ok. 7 min). ANE: `index_ane --recreate --vectors-file …`.

Duża ingestia (całe czasopisma: `harvest openalex --journal-id S… --only-oa` → `curate_manifest … --apply`)
idzie skryptem `deploy/bulk-ingest.sh` w trzech etapach: `ingest` (PDF → chunki w bazie, bez indeksu;
`--delay` 1 s między pobraniami), `translate` (chunki nie-pl, model w chmurze) i `embed` (wektory na GPU
laptopa przez tunel SSH `ssh -p 10233 -N -R 127.0.0.1:11435:127.0.0.1:11434 root@roman233.mikrus.xyz`,
potem `reindex_chunks --missing`). Na CPU serwera ten sam etap trwałby dni i spowalniał stronę.
`EMBEDDINGS_BASE_URL` ustawia skrypt tylko dla tego procesu — strona dalej liczy wektory pytań lokalnie.

## 4. systemd + nginx
```bash
cp deploy/scriptura-web.service deploy/scriptura-update.service deploy/scriptura-update.timer /etc/systemd/system/
cp deploy/nginx-scriptura.conf /etc/nginx/sites-available/scriptura && ln -s /etc/nginx/sites-available/scriptura /etc/nginx/sites-enabled/
systemctl daemon-reload && systemctl enable --now scriptura-web   # timer: systemctl enable --now scriptura-update.timer
nginx -t && systemctl reload nginx
```
Panel Mikrusa (Cytrus): `scriptura.cytr.us` → `roman233.mikrus.xyz:20233`.

## 5. Testy
```bash
curl -sI http://127.0.0.1:8534/ | head -1       # 200
curl -sI -H 'Host: scriptura.cytr.us' http://127.0.0.1:20233/ | head -1   # 200
curl -sI https://scriptura.cytr.us/ | head -1   # 200
# POST /ask/stream oraz /export/citations wymagają cookie sesji CSRF i nagłówka
# X-CSRFToken; czat w przeglądarce ustawia je automatycznie. Sam curl POST bez nich: 403.
journalctl -u scriptura-web -f
```
Aktualizacje: `./deploy/deploy.sh` (kod) lub `./deploy/deploy.sh --data --replace-db` (świadoma podmiana danych);
`update_corpus` z timera dociąga nowe publikacje autorów z `data/authors.jsonl` i pisze raport w `data/manifests/`.

## 6. MLflow — opcjonalne zależności na serwerze

```bash
# Lokalnie: wdrożenie kodu oraz instalacja MLflow na VPS, bez podmiany danych:
./deploy/deploy.sh --eval

# Po świadomym przeniesieniu bazy, na VPS:
bash /opt/scriptura/deploy/setup_after_rsync.sh --eval
```

Oba skrypty używają `deploy/install_dependencies.sh`: standardowo `.[prod,harvest]`,
z `--eval` — `.[prod,harvest,eval]`, następnie `pip check` i sprawdzenie wersji MLflow.
Flaga nie uruchamia benchmarku ani publicznego panelu. Zwykły deploy pozostaje lekki;
nie usuwa jednak MLflow, jeśli był już zainstalowany. Aktualizacja kodu odmawia działania
przy lokalnych zmianach na serwerze i wymaga fast-forward, bez `git reset --hard`.

Ewaluację uruchamiaj jako użytkownik `scriptura`, z katalogu `/opt/scriptura`:

```bash
sudo -u scriptura .venv/bin/python manage.py eval_mlflow \
  data/eval/mlflow-rag.example.jsonl --task rag --dry-run
```

Pełna instrukcja: [../data/eval/README.md](../data/eval/README.md).
Rzeczywiste benchmarki JSONL muszą być dostępne na serwerze osobno; zwykły deploy kodu
nie kopiuje prywatnych zbiorów ewaluacyjnych.

Serwer śledzenia MLflow działa osobno od aplikacji i został na steve141: `mlflow.service`
(127.0.0.1:8535, baza i artefakty w `/cytrus/mlflow/`), wystawiony jako `https://mlflow.cytr.us`
przez nginx na porcie 40021 **wyłącznie z basic auth** (`/etc/nginx/auth/mlflow.htpasswd`).
Klient: `MLFLOW_TRACKING_URI=https://mlflow.cytr.us` + `MLFLOW_TRACKING_USERNAME/PASSWORD`.

## 7. Backup i kontrola dostępu

`deploy/backup-db.sh` tworzy prywatne snapshoty w `/backup/DB/scriptura/` (14 ostatnich).
Katalog należy do użytkownika `scriptura`, więc proces może zapisać backup bez zmiany
właściciela plików WAL bazy. Skrypt nie usuwa historycznych kopii z `/backup/DB/`.

Nginx nadpisuje `X-Forwarded-For`, żeby klient nie mógł wybierać klucza limitu IP.
Jeśli nginx stoi za ingress/Cloudflare, skonfiguruj `real_ip_header` i `set_real_ip_from`
wyłącznie dla rzeczywiście zaufanych adresów tej infrastruktury. Bez tego limit anonimowy
może być wspólny dla użytkowników za jednym proxy. Nie ufaj dowolnemu nagłówkowi od klienta.
Limiter file cache obsługuje wiele procesów na jednym hoście, nie wdrożenia wielohostowe.

Przed wdrożeniem uruchom `manage.py check --deploy` z produkcyjnym środowiskiem.
`DEBUG=false`, silny `SECRET_KEY`, ciasteczka Secure i wymuszanie HTTPS na zaufanym
proxy są wymaganiami konfiguracji, nie wynikają z zielonych testów lokalnych.

Po aktualizacji kodu wymagana jest migracja `library/0009_personal_upload_state`.
Materiały oznaczone jako `pending` można ponowić ze strony konta. Indeks bez `text_exact`
przebuduj (`reindex_chunks --recreate --reuse-vectors`), aby cytaty korzystały z oryginałów.
`constraints.txt` jest surowym pip freeze środowiska lokalnego, zawiera editable projekt;
nie jest bezpośrednio używalnym plikiem `pip -c` ani gwarancją zgodności wersji Pythona.
