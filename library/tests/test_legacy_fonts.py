import pytest

from library.legacy_fonts import decode_bwhebb, is_legacy_hebrew_font


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("~s;q'", "קָסַם"),
        ("~s,q,", "קֶסֶם"),
        ('hw"hy>', "יְהוָה"),
        ("~aun>", "נְאֻם"),
        ("~yMituw>", "וְתֻמִּים"),
        ("~yrIWa", "אוּרִים"),
        ("l[;B;", "בַּעַל"),
        ("tl;[]B;", "בַּעֲלַת"),
        ("tyrIB.", "בְּרִית"),
        ("lb;G>", "גְּבַל"),
        ("lm,r>K;", "כַּרְמֶל"),
        ("!Amr>x,", "חֶרְמֹן"),
        ("!Adyci", "צִידֹן"),
        ("la;v'", "שָׁאַל"),
        ("af'n\"", "נָשָׂא"),
        ("rm'T'", "תָּמָר"),
        ("rA[P.", "פְּעֹר"),
        ("bWbz>", "זְבוּב"),
        ("!v,xo", "חֹשֶׁן"),
        ("dApae", "אֵפֹד"),
        ("lr'AG", "גֹּרָל"),
        ("btk", "כתב"),
        ("$na", "אנך"),
    ],
)
def test_decode_bwhebb(raw, expected):
    assert decode_bwhebb(raw) == expected


def test_font_detection():
    assert is_legacy_hebrew_font("Bwhebb")
    assert is_legacy_hebrew_font("ABCDEF+BWHEBB")
    assert not is_legacy_hebrew_font("ACaslonPro-Regular")
