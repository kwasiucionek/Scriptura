# Wdrożenie na Mikrusa (steve141) — pierwsze uruchomienie

Porty: subdomena `scriptura.cytr.us` → `steve141.mikrus.xyz:40021` (nginx; port 40021 przydzielony w panelu Mikrusa — porty 44xxx/4xxxx nadaje panel, nie wybiera się ich dowolnie) → `127.0.0.1:8534` (gunicorn).
Duże dane na `/cytrus/scriptura` (PDF-y, baza, cache), kod w `/opt/scriptura`.

## 0. Stan steve141 (2026-09-23)
OpenSearch: wspólny kontener `taxpilot-opensearch` (3.7, plugins stempel + icu + knn, heap 1 GB,
`127.0.0.1:9200`), indeksy Scriptury z prefiksem `scriptura-*`. Ollama lokalna (0.18, zalogowana do
chmury) z `snowflake-arctic-embed2`; czat przez tagi `-cloud`. Dane na `/cytrus/scriptura` (dysk `/` w 90 %).

## 0a. Zasoby (ogólnie)
OpenSearch potrzebuje ~1 GB heapu + ok. 0,5 GB na indeksy (5 tys. chunków z wektorami 1024-d ≈ 60 MB).
Embeddingi lokalnie (`snowflake-arctic-embed2` przez Ollamę na CPU) — 2 vCPU: ~1 s/zapytanie.
Jeśli RAM < 4 GB: Ollama z modelem embeddingów + OpenSearch się nie zmieszczą — wtedy
`EMBEDDINGS_BACKEND=tei` z endpointem HF (CPU x1, 0,033 $/h) albo bez kNN (`SEARCH_BACKEND=db`, tylko FTS).

## 1. Serwer
```bash
ssh -p 10141 root@steve141.mikrus.xyz
apt install -y python3-venv nginx
# OpenSearch 2.x (Ubuntu): repo opensearch.org, potem:
#   /etc/opensearch/opensearch.yml: discovery.type: single-node, network.host: 127.0.0.1, plugins.security.disabled: true
#   /etc/opensearch/jvm.options: -Xms1g -Xmx1g
#   /usr/share/opensearch/bin/opensearch-plugin install analysis-stempel analysis-icu   (knn jest w pakiecie)
systemctl enable --now opensearch
# Ollama (embeddingi lokalnie + proxy do chmury): curl -fsSL https://ollama.com/install.sh | sh; ollama pull snowflake-arctic-embed2; ollama signin
useradd -r -m -d /opt/scriptura -s /usr/sbin/nologin scriptura
mkdir -p /cytrus/scriptura/{data,cache} && chown -R scriptura:scriptura /cytrus/scriptura
chmod 755 /opt/scriptura
```

## 1a. Stan po sesji 2026-09-23 (zrobione przez MCP)
Użytkownik `scriptura`, katalogi `/opt/scriptura` i `/cytrus/scriptura/{data,cache}`, unity systemd
(`scriptura-web` enabled, `scriptura-update.timer` enabled), nginx `sites-enabled/scriptura` (40021),
`/opt/scriptura/.env` z wygenerowanym SECRET_KEY (**do uzupełnienia: OLLAMA_API_KEY, HARVEST_MAILTO**),
`analysis-icu` w kontenerze OpenSearch, `snowflake-arctic-embed2` w Ollamie, `/root/MCP_documentation.md`.
Pozostało: `./deploy/deploy.sh --data` z komputera (kod + PDF-y + manifesty + wektory + db.sqlite3),
potem na serwerze `bash /opt/scriptura/deploy/setup_after_rsync.sh`, panel Mikrusa (subdomena → 40021).

## 1b. Git zamiast rsync dla kodu (od sprintu 26)
Kod żyje w repo (GitHub, prywatne); serwer ma klon w `/opt/scriptura` z kluczem deploy (tylko odczyt).
Pierwsze ustawienie:
```bash
# lokalnie (w katalogu projektu):
git init -b main && git add -A && git commit -m "Scriptura: sprint 26" && gh repo create scriptura --private --source . --push
# na serwerze (jako root): klucz deploy dla użytkownika scriptura i klon
sudo -u scriptura ssh-keygen -t ed25519 -N "" -f /opt/scriptura/.ssh/id_ed25519 -C scriptura@steve141
cat /opt/scriptura/.ssh/id_ed25519.pub     # -> GitHub: repo -> Settings -> Deploy keys (read-only)
mv /opt/scriptura /opt/scriptura.rsync-backup && sudo -u scriptura git clone git@github.com:kwasiucionek/scriptura.git /opt/scriptura
cp /opt/scriptura.rsync-backup/.env /opt/scriptura/.env && mv /opt/scriptura.rsync-backup/.venv /opt/scriptura/.venv && chown -R scriptura:scriptura /opt/scriptura
```
Potem każde wdrożenie to `./deploy/deploy.sh` (commit → push → pull na serwerze → migrate → restart);
`--data` dokłada rsync danych. Dane (`data/`, `db.sqlite3`, `.env`) nigdy nie są w repo (`.gitignore`).

## 2. Kod, środowisko, .env
```bash
# z komputera: ./deploy/deploy.sh --data   (pierwszy raz: kod + PDF-y + manifesty + wektory)
# na serwerze:
cd /opt/scriptura && sudo -u scriptura python3 -m venv .venv && sudo -u scriptura .venv/bin/pip install -e ".[prod]"
cp deploy/env.production.example .env && nano .env      # SECRET_KEY, OLLAMA_API_KEY, HARVEST_MAILTO
chown scriptura:scriptura .env && chmod 600 .env
```

## 3. Dane
Baza SQLite z komputera deweloperskiego (korpus kanoniczny + literatura + historia) — najszybciej skopiować plik:
```bash
rsync -av -e "ssh -p 10141" db.sqlite3 root@steve141.mikrus.xyz:/cytrus/scriptura/db.sqlite3
```
(albo od zera: `fetch_sources && import_corpus oshb/morphgnt/lxx/json`, `import_lexicon`, `import_xref`, `ingest_manifest` …).
Indeksy: `index_search --recreate` (wersety, bez wektorów, ~2 min) i `reindex_chunks --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz`
(wektory z pliku — bez liczenia; bez pliku 5 tys. chunków na 2 vCPU to ~1,5 h). ANE: `index_ane --recreate --vectors-file …`.

## 4. systemd + nginx
```bash
cp deploy/scriptura-web.service deploy/scriptura-update.service deploy/scriptura-update.timer /etc/systemd/system/
cp deploy/nginx-scriptura.conf /etc/nginx/sites-available/scriptura && ln -s /etc/nginx/sites-available/scriptura /etc/nginx/sites-enabled/
systemctl daemon-reload && systemctl enable --now scriptura-web scriptura-update.timer
nginx -t && systemctl reload nginx
```
Panel Mikrusa: „Zarządzanie stronami WWW" → `scriptura.cytr.us` → `steve141.mikrus.xyz:40021`.

## 5. Testy
```bash
curl -sI http://127.0.0.1:8534/ | head -1       # 200
curl -sI http://127.0.0.1:40021/ | head -1      # 200
curl -sI https://scriptura.cytr.us/ | head -1   # 200
curl -N -X POST https://scriptura.cytr.us/ask/stream -H 'Content-Type: application/json' -d '{"question":"Co znaczy miszkan?"}' | head -5
journalctl -u scriptura-web -f
```
Aktualizacje: `./deploy/deploy.sh` (kod) lub `./deploy/deploy.sh --data` (nowe źródła z komputera);
`update_corpus` z timera dociąga nowe publikacje autorów z `data/authors.jsonl` i pisze raport w `data/manifests/`.
