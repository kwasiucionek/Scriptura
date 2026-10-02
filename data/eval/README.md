# Ewaluacje Scriptury w MLflow

Dwie osobne ścieżki: **retrieval** mierzy jakość znalezionych chunków, **rag** uruchamia
istniejący pipeline odpowiedzi i mierzy jego raporty kontroli. To jawna komenda offline:
nie instrumentuje ruchu użytkowników ani nie zapisuje historii rozmów w Django.

## Instalacja i start lokalny

Z katalogu projektu, z aktywną `.venv`:

```bash
pip install -e ".[eval]"

# Najpierw walidacja istniejącego, ręcznie przejrzanego zbioru i etykiet:
python manage.py eval_mlflow data/eval/questions.jsonl --dry-run

# Sama hybryda; k=5 jak w dotychczasowym benchmarku:
python manage.py eval_mlflow data/eval/questions.jsonl \
  --task retrieval --k 5 --no-rerank --run-name retrieval-rrf

# Ta sama próbka z rerankerem skonfigurowanym w .env:
python manage.py eval_mlflow data/eval/questions.jsonl \
  --task retrieval --k 5 --run-name retrieval-reranked

# Pełna odpowiedź; --k nie zmienia RAG_TOP_K:
python manage.py eval_mlflow data/eval/questions.jsonl \
  --task rag --mode scientific --limit 5 --run-name rag-scientific

# W osobnym terminalu; UI jest dostępne tylko lokalnie:
mlflow ui --backend-store-uri sqlite:///.mlflow/mlflow.db --host 127.0.0.1 --port 5000
```

Otwórz `http://127.0.0.1:5000`, eksperyment `scriptura-evaluation`. Porównuj runy w widoku
Eksperymentów; widok GenAI/Traces pokazuje wyniki deterministycznych scorerów per przypadek.
Artefakt `cases.json` zawiera wiersze i metryki. UI wymaga osobnego procesu, komenda go nie
uruchamia. URI dla UI zakłada uruchomienie z tego samego katalogu projektu.

Domyślnie baza trackingu: `.mlflow/mlflow.db`, artefakty: `.mlflow/mlflow-artifacts/`.
`--tracking-uri` ma pierwszeństwo przed `MLFLOW_TRACKING_URI`; bez obu wybierana jest
lokalna baza SQLite (nie stary file store `mlruns`). Eksperyment można zmienić przez
`--experiment`. Nowe lokalne eksperymenty mają artefakty obok bazy; istniejące zachowują
swoją poprzednią lokalizację.

## Zbiór JSONL

Dotychczasowy format z `eval_bootstrap` pozostaje obsługiwany:

```json
{"id":"mk-ending","q":"Dlaczego Mk 16,9-20 uznaje się za dodatek?","type":"pl","relevant":["12:3","12:4"],"authors":["Mariusz Rosik"],"note":"Sprawdzone ręcznie"}
```

`12:3` oznacza **ID dokumentu 12 i order chunka 3** — zastąp przykładowe wartości
rzeczywistymi z własnej bazy. Etykiety są specyficzne dla wersji korpusu. W trybie retrieval
muszą istnieć w otwartym korpusie; nieaktualne lub zamknięte etykiety kończą komendę
przed wywołaniami modeli. `authors` to nazwy, nie ID ani slugi. `type` grupuje wyniki,
np. `pl`, `pl+orig`, `pl->en`; `all` jest zarezerwowane. Brak `id` oznacza stabilny hash
znormalizowanych pól przypadku. Duplikaty ID są odrzucane.

Dla RAG `relevant` jest opcjonalne i **nie jest obecnie używane do liczenia recall**.
Można dodać:

```json
{"id":"outside-domain","q":"Jaki jest dzisiejszy kurs euro?","type":"out-of-scope","expected_refused":true,"required_terms":[],"include_ane":false,"include_patristics":false}
```

Opcjonalne pola: `expected_refused` (bool), `required_terms` (lista niepustych tekstów),
`works` (kody dzieł; `[]` wyłącza dzieła), `include_ane` i `include_patristics`
(bool/null), `mode` (`scientific`/`popular`, nadpisuje tryb komendy dla przypadku), `note`.
Nieznane starsze metadane są ignorowane. Cały plik jest walidowany przed zastosowaniem
`--limit`; limit nie ukrywa błędnych rekordów znajdujących się dalej.

`mlflow-rag.example.jsonl` to przykłady schematu, **nie zweryfikowany benchmark jakości**.
Do testu schematu bez modeli:

```bash
python manage.py eval_mlflow data/eval/mlflow-rag.example.jsonl --task rag --dry-run
```

Realne zbiory `.jsonl`, pytania syntetyczne i dane MLflow pozostają poza Git.
`eval_bootstrap` może przygotować materiał, ale przed porównaniami przejrzyj pytania,
relevance labels i oczekiwania. Nie buduj oceny końcowej wyłącznie z pytań tego samego
modelu, który oceniasz. Zachowaj oddzielny, ręcznie sprawdzony zbiór testowy.

## Metryki i ich ograniczenia

| Tryb | Metryka | Znaczenie |
|---|---|---|
| retrieval | `recall_at_k` | udział oznaczonych właściwych chunków znalezionych w top-k |
| retrieval | `mrr_at_k` | odwrotność pozycji pierwszego właściwego trafienia, 0 przy braku |
| retrieval | `hit_at_1` | czy pierwsze trafienie jest właściwe |
| retrieval | `near_at_k` | czy znaleziono właściwy chunk lub jego sąsiada ±1; to hit, nie recall |
| rag | `quote_verification_rate` | udział wykrytych cytatów zgodnych według istniejącego weryfikatora |
| rag | `citation_validity_rate` | udział odsyłaczy wskazujących dostępne źródła w kontekście |
| rag | `refusal_accuracy` | zgodność odmowy z `expected_refused`, tylko gdy jest etykieta |
| rag | `required_terms_coverage` | udział wymaganych terminów w odpowiedzi, dopasowanie podciągów casefold |
| rag | `answer_nonempty`, `refused` | niepusta odpowiedź i częstotliwość odmowy |
| oba | `success`, `latency_ms` | ukończenie pipeline'u i czas wall-clock |

Zapisujemy też liczniki cytatów/odsyłaczy, czasy etapów oraz dostępne liczniki tokenów RAG.
`success=1` nie oznacza poprawnej merytorycznie odpowiedzi. Istnienie przypisu nie dowodzi
poparcia tezy, a leksykalne wystąpienie terminu nie dowodzi poprawnego objaśnienia.
Weryfikacja cytatów jest heurystyczna; tłumaczenia mogą wymagać kontroli eksperta.
Nie dodano automatycznego LLM-as-judge ani ocen semantycznej prawdziwości/faithfulness.

Przy braku cytatów lub odsyłaczy wskaźnik nie występuje — nie jest ustawiany na 1.
Średnie są liczone makro po przypadkach, tylko dla metryk mających zastosowanie.
`metrics.<nazwa>.mean` i `.count` pokazują średnią i mianownik; podobnie
`types.<zakodowany_typ>.metrics.<nazwa>.*`. W etykietach typów znaki specjalne kodujemy
bezkolizyjnie, np. `pl+orig` → `pl_2b_orig`. Surowe etykiety pozostają w artefaktach.
Oprócz tych agregatów natywne scorery MLflow tworzą własne oceny/średnie w widoku GenAI.

Awaria backendu daje `success=0`, czas i `error=evaluation_failed`, bez pozornych wyników
jakości ani surowego wyjątku. Pozostałe przypadki są wykonywane, wyniki są zapisywane,
a komenda kończy się błędem (kod 1). Zawsze czytaj `cases.failed` oraz mianowniki obok
średnich: pominięte awarie nie mogą być interpretowane jako lepsza jakość modelu.

Retrieval jest zgodny z `scripts/eval_retrieval.py`: bez dodawania sąsiadów i dywersyfikacji
RAG. Pełny RAG ma te etapy oraz przycięcie kontekstu; jego sources nie zawierają `order`,
więc nie wyprowadzamy pozornego recall z samych ID dokumentów.

## Powtarzalność

Każdy run rejestruje:
- konfigurację z jawnej allowlisty (backendy, modele, k, reranker, budżet, parametry RAG);
- hash efektywnych przypadków benchmarku (z uwzględnieniem limitu; bez notatek);
- commit Git i informację o niezatwierdzonych zmianach;
- hash promptu i instrukcji obu trybów;
- liczbę otwartych dokumentów/chunków i fingerprint metadanych dokumentów.

Fingerprint metadanych **nie jest hashem pełnego tekstu, wersji modeli ani wektorów
OpenSearch**. Przy zmianach danych dodaj `--corpus-version`, np. własną etykietę snapshotu;
`--dataset-version` opisuje ręcznie zatwierdzony benchmark. Wyniki dotyczą bieżącej bazy
i indeksu; komenda nie zamraża ich na czas ewaluacji. Nie uruchamiaj ingestii równolegle.
Porównuj ten sam zbiór i korpus, zmieniając jedną konfigurację naraz.

## Prywatność, koszty i serwer

Ewaluator jawnie korzysta tylko z `access=open`, bez konta i materiałów osobistych,
niezależnie od `RAG_ACCESS` ustawionego dla aplikacji. Domyślnie do MLflow trafiają
identyfikatory przypadków, etykiety typów, metryki, hashe i konfiguracja — **bez pytań,
odpowiedzi, notatek i fragmentów**. Nie umieszczaj danych wrażliwych w ID/typach/etykietach.
Zapis contentu wymaga `--log-content`; obejmuje pytania, odpowiedzi i źródła, więc używaj
zaufanego trackingu. Notatki nie są eksportowane. Nie zapisujemy adresów usług i kluczy
API; również content opt-in redaguje strukturalne sekrety/URL-e.

Na czas sekwencyjnego wykonania backendów komenda wycisza ich logi: wyjątki dostawców
potrafią zawierać prompty lub sekrety. Po wykonaniu przywraca poprzedni stan logowania;
nie zmienia logowania aplikacji WWW. Wyniki awarii pozostają w tabeli przypadków.

Sam MLflow z deterministycznymi scorerami nie wywołuje modeli oceniających.
**Pipeline może jednak korzystać z płatnego/chmurowego LLM** do tłumaczenia, rerankingu
lub generacji, zgodnie z `.env`. `--no-rerank` nie wyłącza tłumaczenia; `--limit` ogranicza
liczbę wywołań. `LLM_BACKEND=echo` daje test techniczny, nie benchmark jakości modelu.
MLflow jest importowany leniwie, bez autologowania ruchu produkcyjnego; telemetria jest
domyślnie wyłączana, jeśli użytkownik nie ustawił jej inaczej.

Na VPS dodaj MLflow **tylko jeśli tam uruchamiasz ewaluacje**: lokalnie
`./deploy/deploy.sh --eval` albo po transferze danych na serwerze
`bash /opt/scriptura/deploy/setup_after_rsync.sh --eval`. Wspólny instalator wybiera
`.[prod,harvest,eval]`, sprawdza zależności i wersję MLflow. Zwykły deploy
`.[prod,harvest]` nie dodaje MLflow; nie usuwa też wcześniejszej instalacji. Nie publikuj portu 5000 bez uwierzytelniania i polityki
dostępu. Możesz korzystać z lokalnego UI przez tunel SSH albo wskazać zaufany serwer
przez `MLFLOW_TRACKING_URI`/`--tracking-uri`. Remote tracking wymaga sieci; preflight
sprawdza konfigurację i zależność, ale nie sprawdza osiągalności serwera.

## Sędziowie LLM (RAGAS, DeepEval)

Metryki deterministyczne nie mówią, czy odpowiedź wynika ze źródeł. Do tego służą
sędziowie LLM uruchamiani jako scorery MLflow (`mlflow.genai.scorers.ragas`,
`mlflow.genai.scorers.deepeval`) w tym samym runie co metryki deterministyczne.

```bash
# osobny venv — RAGAS/DeepEval ciągną langchain, openai, instructor; nie w venv WWW
python -m venv /cytrus/scriptura-eval/venv
/cytrus/scriptura-eval/venv/bin/pip install --no-cache-dir -e ".[prod,eval,judges]"

python manage.py eval_mlflow data/eval/questions.jsonl --task rag --mode popular \
  --limit 5 --log-content --judges default \
  --tracking-uri http://127.0.0.1:8535 --run-name rag-judged
```

- `--judges`: `default` = `ragas:Faithfulness`, `deepeval:Faithfulness`,
  `deepeval:AnswerRelevancy`; można dopisać `ragas:ResponseGroundedness`,
  `ragas:ContextRelevance`, `ragas:ContextUtilization`, `deepeval:ContextualRelevancy`.
  Tylko metryki bez odpowiedzi wzorcowej i bez embeddingów (żadnych domyślnych wywołań OpenAI).
- `--judge-model` (albo `SCRIPTURA_JUDGE_MODEL`): URI MLflow `<dostawca>:/<model>`,
  domyślnie `ollama:/deepseek-v4-pro:cloud` przez lokalną Ollamę — inna rodzina niż
  model odpowiedzi (gemma4), żeby sędzia nie oceniał sam siebie.
- `--judge-workers` (domyślnie 2): równoległe przypadki i scorery; więcej = szybciej,
  ale też więcej równoczesnych zapytań do modelu sędziego.
- `--judge-timeout` (domyślnie 300 s): limit jednego wywołania sędziego. Domyślne 60 s
  MLflow nie wystarcza — przy ~10 tys. tokenów kontekstu i modelu z rozumowaniem
  (deepseek-v4-pro) część ocen faithfulness kończyła się `ReadTimeout`.
- Wymaga `--log-content` i `--task rag`; walidacja przed wywołaniem jakiegokolwiek modelu.
- Średnia sędziego liczy tylko udane oceny — zawsze sprawdzaj w śladach, ile ocen ma błąd.

Jak to działa: zapisane odpowiedzi są odtwarzane przez `predict_fn` (bez ponownego
generowania) — każdy przypadek dostaje ślad ze spanem `RETRIEVER` z fragmentami kontekstu
w tej postaci, w jakiej widział je model (pełny tekst fragmentów, wersety, powiązane
wersety, ANE, Ojcowie; leksykon pominięty). Scorery RAGAS/DeepEval czytają kontekst z tego
spanu. Oceny mają nazwy `ragas/…` i `deepeval/…`, średnie trafiają do metryk runu
(`ragas/Faithfulness/mean` itd.), uzasadnienia sędziów — do śladów (Traces).
Przypadki z błędem lub pustą odpowiedzią nie są oceniane (`judges.cases` = liczba ocenionych).

Ograniczenia: sędzia też się myli — przed wnioskami przejrzyj ręcznie kilka ocen
i uzasadnień, zwłaszcza przy greckim i hebrajskim. Prompty RAGAS/DeepEval są angielskie,
treść polska. Adresy URL w treści są wycinane (`[redacted]`), reszta fragmentu zostaje.
Treść przypadków trafia do modelu sędziego i do śladów MLflow; telemetria RAGAS/DeepEval
jest domyślnie wyłączona (`DEEPEVAL_TELEMETRY_OPT_OUT`, `RAGAS_DO_NOT_TRACK`).

### Sędziowie w UI MLflow (strona „Judges”)

Sędziowie z `make_judge` (instrukcje + model) mogą być zarejestrowani w eksperymencie
i uruchamiani przez serwer MLflow — z UI na wybranych śladach albo automatycznie na nowych
śladach (`scorer.start(sampling_config=…)`). Kryteria specyficzne dla Scriptury są w
`scripts/mlflow_judges.py` (jedno kryterium na sędziego, wynik `yes`/`no`):

- `tradycja_vs_egzegeza` — Ojcowie [P…] i teksty ANE [A…] nie są podawane jako ustalenia
  współczesnej egzegezy ani tekst biblijny;
- `zrozumiala_dla_laika` — w trybie popularnym odpowiedź pada na początku, a terminy
  hebrajskie, greckie i fachowe są objaśnione.

```bash
python scripts/mlflow_judges.py register --experiment scriptura-evaluation
python scripts/mlflow_judges.py run --experiment scriptura-evaluation --run-id <RUN_ID>
```

Serwer MLflow wywołuje model sędziego sam (`ollama:/…` przez Ollamę na tym samym hoście).
Scorery RAGAS/DeepEval z `--judges` tak nie działają: serwer odrzuca scorery z kodem
(`@scorer`), więc zostają w komendzie. Ślady z `--judges` mają span główny `scriptura_rag`
(wejście: pytanie, wynik: odpowiedź) i podrzędny `retrieval` (RETRIEVER, fragmenty) —
dzięki temu `{{ inputs }}`/`{{ outputs }}` w sędziach z UI to pytanie i odpowiedź,
a wbudowane `RetrievalGroundedness`/`RetrievalRelevance` widzą kontekst.

## Bramka AI MLflow (AI Gateway)

Serwer MLflow 3.x ma bramkę zgodną z API OpenAI: `/gateway/mlflow/v1/chat/completions`,
gdzie `model` to nazwa endpointu. Endpointy, modele zapasowe i guardraile Scriptury są
zapisane jako kod w `scripts/mlflow_gateway.py` (`setup` idempotentnie, `status`, `check`
z oczekiwanym wynikiem każdego przypadku). Klucz dostawcy zostaje w sekrecie bramki
(UI: AI Gateway → API Keys); skrypt wskazuje go po nazwie.

| Endpoint | Model główny → zapasowy | Rola |
| --- | --- | --- |
| `scriptura-chat` | gemma4:31b-cloud → qwen3.5:397b-cloud | odpowiedź RAG; guardraile `scriptura-pii`, `scriptura-injection` |
| `scriptura-utility` | gemma4:31b-cloud → qwen3.5:397b-cloud | tłumaczenie zapytania, reranker LLM; bez guardraili |
| `scriptura-guard` | deepseek-v4-flash:cloud → gpt-oss:120b-cloud | model guardraili |
| `scriptura-judge` | deepseek-v4-pro:cloud, bez zapasowego | sędziowie ewaluacji |
| `demo-fallback` | qwen3-coder:480b-cloud (wycofany) → gemma4:31b-cloud | pokaz przełączenia |

Ewaluacja przez bramkę (produkcja dalej woła Ollamę bezpośrednio):

```bash
LLM_BACKEND=openai OPENAI_BASE_URL=http://127.0.0.1:8535/gateway/mlflow/v1 \
OPENAI_CHAT_MODEL=scriptura-chat RAG_TRANSLATE_MODEL=scriptura-utility \
CURATE_MODEL=scriptura-utility OPENAI_API_KEY=unused \
python manage.py eval_mlflow ../questions.jsonl --task rag --mode popular --limit 5 \
  --log-content --judges default --judge-model gateway:/scriptura-judge \
  --tracking-uri http://127.0.0.1:8535 --run-name rag-gateway
```

`OPENAI_API_KEY=unused` — żeby klucz NIM z `.env` nie trafiał do bramki.

Co wyszło w praktyce (MLflow 3.16.1):

- Guardrail to sędzia LLM (`make_judge`, `yes`/`no`) na osobnym endpoincie. Etap BEFORE
  widzi całe żądanie z fragmentami źródeł, więc instrukcje każą oceniać tylko tekst po
  „PYTANIE:”; nazwiska autorów, Ojców i postaci biblijnych nie są danymi osobowymi
  (szablon PII z UI blokowałby takie pytania).
- Każdy guardrail to dodatkowe wywołanie modelu przed odpowiedzią, wykonywane po kolei.
  Na 5 pytaniach (tryb popular) dwa guardraile wydłużyły odpowiedź z ok. 6,0 do 9,0 s;
  oceny sędziów bez zmian.
- Guardraile AFTER nie działają przy strumieniowaniu, a Scriptura strumieniuje odpowiedź.
- Zablokowane żądanie i tak trafia do śladu bramki, razem z danymi osobowymi — guardrail
  chroni model, nie logi. Potrzebna krótka retencja albo maskowanie śladów.
- Gdy model guardraili nie odpowiada, żądanie czeka na ponowienia (minuty) i kończy się
  błędem — dlatego `scriptura-guard` ma model zapasowy.
- Bramka nie wysyła `data: [DONE]`; `library/llm.py` uznaje strumień za kompletny także po
  `finish_reason`.
- `--judge-model gateway:/…` wymaga `--tracking-uri http(s)://…`; komenda ustawia wtedy
  `MLFLOW_GATEWAY_URI`, bez którego scorery RAGAS/DeepEval szukają litellm.
- Sędzia bez modelu zapasowego celowo: ocena innego modelu nie byłaby porównywalna
  z wcześniejszymi runami.
- Bez `MLFLOW_CRYPTO_KEK_PASSPHRASE` w środowisku serwera MLflow klucze w bramce są
  szyfrowane domyślnym hasłem (serwer to loguje) — ustaw je przed dodaniem kluczy
  i ogranicz dostęp do pliku bazy MLflow.

## Testy implementacji

Testy metryk i komendy są offline, z atrapami usług. Natywny smoke MLflow jest opcjonalny:

```bash
RUN_MLFLOW_SMOKE=1 pytest library/tests/test_mlflow_eval.py library/tests/test_eval_mlflow_native.py
```

Używa tymczasowej bazy SQLite, blokuje sieć i nie wywołuje rzeczywistych modeli.
Nie dodano CI ani testów integracyjnych OpenSearch/PostgreSQL.
