"""Polskie etykiety typów miejsc z OpenBible (settlement -> miejscowość).

Typów jest kilkadziesiąt i są stałe — słownik zamiast modelu; nieznany typ zostaje bez zmian.
"""

TYPES_PL = {
    "altar": "ołtarz",
    "body of water": "zbiornik wodny",
    "campsite": "obozowisko",
    "canal": "kanał",
    "cliff": "urwisko",
    "district in settlement": "dzielnica",
    "field": "pole",
    "ford": "bród",
    "forest": "las",
    "fortification": "twierdza",
    "garden": "ogród",
    "gate": "brama",
    "hall": "sala",
    "hill": "wzgórze",
    "island": "wyspa",
    "mine": "kopalnia",
    "mountain": "góra",
    "mountain pass": "przełęcz",
    "mountain range": "pasmo górskie",
    "mountain ridge": "grzbiet górski",
    "natural area": "obszar naturalny",
    "people group": "lud",
    "pool": "sadzawka",
    "promontory": "przylądek",
    "region": "kraina",
    "river": "rzeka",
    "road": "droga",
    "rock": "skała",
    "room": "pomieszczenie",
    "settlement": "miejscowość",
    "special": "inne",
    "spring": "źródło",
    "stone heap": "stos kamieni",
    "structure": "budowla",
    "tree": "drzewo",
    "valley": "dolina",
    "wadi": "wadi",
    "well": "studnia",
}


def type_label(value: str) -> str:
    """„settlement” -> „miejscowość”; nieznany typ bez zmian."""
    return TYPES_PL.get((value or "").strip().lower(), value or "")
