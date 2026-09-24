"""Mapping indeksu `verses`: dokument = werset × dzieło.

Pola tekstowe per język (tylko jedno wypełnione, wg Work.language):
  text_pl   — Stempel (stemming polski) + lowercase
  text_grc  — ICU tokenizer + icu_folding (zdejmuje akcenty, przydechy) + lowercase
  text_hbo  — ICU tokenizer + icu_folding (zdejmuje nikud i te'amim)
  text      — forma dosłowna, lowercase bez stemmingu (frazy „jak w druku")

Pola denormalizowane PER WERSET (ze wszystkich dzieł, nie tylko tego dokumentu):
  lemmas[], strongs[], forms[] — keyword; dzięki temu „H430 niebo" to jeden bool:
  term na strongs (z WLC) + match na text_pl (z BG) w tym samym dokumencie BG.
"""

ANALYSIS = {
    "analyzer": {
        "pl_stem": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "polish_stem"],
        },
        "grc_fold": {
            "type": "custom",
            "tokenizer": "icu_tokenizer",
            "filter": ["icu_folding", "lowercase", "final_sigma"],
        },
        "hbo_fold": {
            "type": "custom",
            "tokenizer": "icu_tokenizer",
            "filter": ["icu_folding", "strip_hebrew_punct"],
        },
        "exact": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase"],
        },
    },
    "filter": {
        "final_sigma": {"type": "pattern_replace", "pattern": "ς", "replacement": "σ"},
        "strip_hebrew_punct": {
            "type": "pattern_replace",
            "pattern": "[\u05be\u05c0\u05c3\u05c6\u05f3\u05f4׃]",
            "replacement": "",
        },
    },
}

VERSES_INDEX = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0},
        "analysis": ANALYSIS,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "osis_id": {"type": "keyword"},
            "ordinal": {"type": "long"},
            "book": {"type": "keyword"},
            "book_order": {"type": "short"},
            "chapter": {"type": "short"},
            "verse": {"type": "short"},
            "ref": {"type": "keyword"},  # "Mk 1,1" do wyświetlania
            "work": {"type": "keyword"},
            "language": {"type": "keyword"},
            "access": {"type": "keyword"},
            "text": {
                "type": "text",
                "analyzer": "exact",
                "term_vector": "with_positions_offsets",
            },
            "text_pl": {
                "type": "text",
                "analyzer": "pl_stem",
                "term_vector": "with_positions_offsets",
            },
            "text_grc": {
                "type": "text",
                "analyzer": "grc_fold",
                "term_vector": "with_positions_offsets",
            },
            "text_hbo": {
                "type": "text",
                "analyzer": "hbo_fold",
                "term_vector": "with_positions_offsets",
            },
            "lemmas": {"type": "keyword"},
            "strongs": {"type": "keyword"},
            "forms": {"type": "keyword"},
        },
    },
}

# Pole tekstowe z analizatorem dla danego kodu języka (fallback: 'text')
LANGUAGE_FIELD = {"pl": "text_pl", "grc": "text_grc", "hbo": "text_hbo"}
STEM_FIELDS = ["text_pl", "text_grc", "text_hbo"]
