"""Skąd pochodzą czasopisma i wydawnictwa korpusu: wydawca i, gdy nie wynika z nazwy, środowisko.

Strona Korpus pokazuje to przy liście czasopism zamiast etykiet przy każdym tekście — większość
literatury jest neutralna metodologicznie, a czytelnik widzi, z jakiej uczelni lub środowiska
pochodzi źródło. Ujęcie (Document.perspective) zostaje tylko dla dokumentów Kościoła i tekstów,
które wprost bronią stanowiska wyznaniowego.

Klucz = Document.journal bez kropki na końcu; warianty tej samej nazwy w ALIASES. Brak wpisu =
wydawca nieustalony (nie zgadujemy).
"""

from dataclasses import dataclass

KUL = "Katolicki Uniwersytet Lubelski Jana Pawła II"
UPJP2 = "Uniwersytet Papieski Jana Pawła II w Krakowie"
UKSW = "Uniwersytet Kardynała Stefana Wyszyńskiego w Warszawie"


@dataclass(frozen=True)
class Venue:
    publisher: str
    note: str = ""  # środowisko, gdy nie wynika z nazwy wydawcy


VENUES: dict[str, Venue] = {
    "The Biblical Annals": Venue(KUL),
    "Verbum Vitae": Venue(KUL),
    "Vox Patrum": Venue(KUL),
    "Roczniki Teologiczne": Venue("Towarzystwo Naukowe KUL"),
    "majewskimarcin.pl": Venue("strona autora (ks. Marcin Majewski)"),
    "Wrocławski Przegląd Teologiczny": Venue(
        "Papieski Wydział Teologiczny we Wrocławiu"
    ),
    "Collectanea Theologica": Venue(UKSW),
    "Teologia w Polsce": Venue(UKSW),
    "Studia Theologica Varsaviensia": Venue(UKSW),
    "Ruch Biblijny i Liturgiczny": Venue("Polskie Towarzystwo Teologiczne, Kraków"),
    "Biblica et Patristica Thoruniensia": Venue(
        "Uniwersytet Mikołaja Kopernika w Toruniu, Wydział Teologiczny"
    ),
    "Tarnowskie Studia Teologiczne": Venue(f"{UPJP2}, Wydział Teologiczny w Tarnowie"),
    "Analecta Cracoviensia": Venue(UPJP2),
    "Polonia Sacra": Venue(UPJP2),
    "The Person and the Challenges": Venue(UPJP2),
    "Wydawnictwo Naukowe UPJP II": Venue(UPJP2),
    "Zagadnienia Filozoficzne w Nauce": Venue(UPJP2),
    "Wydawnictwo Naukowe PAT": Venue(
        "Papieska Akademia Teologiczna w Krakowie (dziś UPJPII)"
    ),
    "Śląskie Studia Historyczno-Teologiczne": Venue(
        "Uniwersytet Śląski, Wydział Teologiczny"
    ),
    "Scriptura Sacra": Venue("Uniwersytet Opolski, Wydział Teologiczny"),
    "Liturgia Sacra": Venue("Uniwersytet Opolski, Wydział Teologiczny"),
    "Poznańskie Studia Teologiczne": Venue(
        "Uniwersytet im. Adama Mickiewicza w Poznaniu, Wydział Teologiczny"
    ),
    "Poznańskie Studia Polonistyczne Seria Językoznawcza": Venue(
        "Uniwersytet im. Adama Mickiewicza w Poznaniu"
    ),
    "Colloquia Theologica Ottoniana": Venue("Uniwersytet Szczeciński"),
    "Studia Koszalińsko-Kołobrzeskie": Venue("Uniwersytet Szczeciński"),
    "Studia Warmińskie": Venue(
        "Uniwersytet Warmińsko-Mazurski w Olsztynie, Wydział Teologii"
    ),
    "Studia Pelplińskie": Venue("Wyższe Seminarium Duchowne w Pelplinie"),
    "Sympozjum": Venue("Wyższe Seminarium Misyjne Księży Sercanów"),
    "Perspectiva": Venue("Wyższe Seminarium Duchowne w Legnicy"),
    "Seminare": Venue("Towarzystwo Naukowe Franciszka Salezego"),
    "Studia Nauk Teologicznych PAN": Venue(
        "Polska Akademia Nauk, Komitet Nauk Teologicznych"
    ),
    "Stowarzyszenie Biblistów Polskich": Venue("Stowarzyszenie Biblistów Polskich"),
    "Wydawnictwo bł. Jakuba Strzemię Archidiecezji Lwowskiej obrządku łacińskiego": Venue(
        "Archidiecezja Lwowska obrządku łacińskiego"
    ),
    "Wydawnictwo eSPe": Venue("Wydawnictwo eSPe, Kraków"),
    "CeON Repository (Centre for Evaluation in Education and Science)": Venue(
        "repozytorium CeON (ICM Uniwersytetu Warszawskiego)"
    ),
    "Elpis": Venue("Uniwersytet w Białymstoku", "teologia prawosławna"),
    "Rocznik Teologiczny": Venue(
        "Chrześcijańska Akademia Teologiczna w Warszawie",
        "uczelnia ekumeniczna: teologia ewangelicka, prawosławna i starokatolicka",
    ),
}

ALIASES: dict[str, str] = {
    "Biblical Annals": "The Biblical Annals",
    "Marcin Majewski": "majewskimarcin.pl",
    "Uniwersytet Papieski Jana Pawła II w Krakowie": "Wydawnictwo Naukowe UPJP II",
    "Seminare Poszukiwania naukowe": "Seminare",
    "Liturgia Sacra Liturgia - Musica – Ars": "Liturgia Sacra",
    "Stowarzyszenie LIST": "LIST",
    "Sympozjum/Sympozjum - Wyższe Seminarium Misyjne Zgromadzenia Księży Najświętszego Serca Jezusowego": "Sympozjum",
}


def canonical(journal: str) -> str:
    """Nazwa czasopisma bez kropki na końcu i w wariancie kanonicznym."""
    name = " ".join(journal.split()).rstrip(".")
    if name.startswith("The Person and the Challenges"):
        return "The Person and the Challenges"
    return ALIASES.get(name, name)


def journal_rows(rows: list[dict]) -> list[dict]:
    """[{journal, n}] -> scalone warianty nazw z wydawcą i środowiskiem, malejąco po liczbie."""
    merged: dict[str, int] = {}
    for r in rows:
        key = canonical(r["journal"])
        merged[key] = merged.get(key, 0) + r["n"]
    out = []
    for name, n in merged.items():
        v = VENUES.get(name)
        out.append(
            {
                "journal": name,
                "n": n,
                "publisher": v.publisher if v else "",
                "note": v.note if v else "",
            }
        )
    return sorted(out, key=lambda r: (-r["n"], r["journal"]))
