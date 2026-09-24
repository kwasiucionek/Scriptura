from corpus.spoken import normalize_spoken_sigla as n


def test_spoken_sigla():
    assert (
        n("Powtórzonego Prawa 32 8 i Septuaginta")
        == "Powtórzonego Prawa 32 8 (Pwt 32,8) i Septuaginta"
    )
    assert "(Pwt 32,8-9)" in n("piąta Księga Mojżeszowa 32 wers 8 do 9")
    assert "(Mk 16,8)" in n("w Ewangelii Marka rozdział 16 werset 8")
    assert "(1 Kor 15,3)" in n("pierwszy list do Koryntian 15 3")
    assert "(Ps 82,6)" in n("Psalm 82 werset 6")
    assert "(Rdz 1,26)" in n("Księga Rodzaju 1 26 uczyńmy") and "(Ap 21,1)" in n(
        "Apokalipsa 21 1"
    )
    assert n("Pwt 32,8 kanoniczne") == "Pwt 32,8 kanoniczne"
    assert n("miał 32 lata i 8 dzieci") == "miał 32 lata i 8 dzieci"
    assert n("Powtórzonego Prawa 32 8 (Pwt 32,8) już dopisane").count("(Pwt 32,8)") == 1
