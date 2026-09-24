from corpus.books import BOOKS
from patristics.thml import parse_osis_ref, parse_volume

ORD = {b.osis: b.order for b in BOOKS}

THML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ThML>
<ThML.head><title>ANF01. The Apostolic Fathers</title></ThML.head>
<ThML.body>
<div1 title="Title Page"><p>Ante-Nicene Fathers Vol. I</p></div1>
<div1 title="IREN&#198;US" id="ii" progress="proofed">
  <div2 title="Against Heresies">
  <div3 title="Book III">
    <div3 title="Chapter XXI.&#8212;A vindication of the prophecy in Isaiah">
      <pb n="451"/>
      <p>God, then, was made man, and the Lord did Himself save us, giving us the token of the Virgin
      <scripRef passage="Isa. vii. 14" osisRef="Bible:Isa.7.14">Isa. vii. 14</scripRef>. But not as some allege<note place="foot"><p>Footnote text to skip; cf. <scripRef passage="Rom. v. 12" parsed="|Rom|5|12|5|12|">Rom. v. 12</scripRef>.</p></note>, among those now presuming to expound the Scripture.</p>
      <p>For let us make man after our image and likeness, <scripRef osisRef="Bible:Gen.1.26-Gen.1.27">Gen. i. 26</scripRef> says the Word to the Son and the Spirit.</p>
      <pb n="452"/>
      <p>Second page paragraph long enough to be kept in the passage stream, referencing <scripRef osisRef="Bible:Matt.1">Matt. i</scripRef> as a whole chapter, and <scripRef passage="1 Cor. xv. 3" parsed="|1Cor|15|3|15|3|">1 Cor. xv. 3</scripRef> plus <scripRef passage="Ps. cx. 1">Ps. cx. 1</scripRef> without parsed.</p>
    </div3>
  </div3>
  </div2>
</div1>
</ThML.body>
</ThML>"""


def test_parse_osis_ref():
    g = ORD["Gen"] * 10**6
    assert parse_osis_ref("Bible:Gen.1.26") == (g + 1026, g + 1026)
    assert parse_osis_ref("Gen.1.26-Gen.1.27") == (g + 1026, g + 1027)
    assert parse_osis_ref("Bible:Gen.1.26-2.3") == (g + 1026, g + 2003)
    assert parse_osis_ref("Bible:Matt.1") == (
        ORD["Matt"] * 10**6 + 1001,
        ORD["Matt"] * 10**6 + 1999,
    )
    assert parse_osis_ref("Bible:Foo.1.1") is None


def test_parse_volume_passages_refs_pages():
    works = parse_volume(THML, default_author="", max_chars=200)
    assert [(w.author, w.title) for w in works] == [("Irenaeus", "Against Heresies")]
    w = works[0]
    assert len(w.passages) >= 2
    p0 = w.passages[0]
    assert p0.section.startswith("Book III / Chapter XXI")
    assert "Footnote" not in p0.text
    assert "Isa. vii. 14" in p0.text and p0.page == "451"
    assert (ORD["Isa"] * 10**6 + 7014, ORD["Isa"] * 10**6 + 7014) in p0.refs
    assert (
        ORD["Rom"] * 10**6 + 5012,
        ORD["Rom"] * 10**6 + 5012,
    ) in p0.refs  # odsyłacz z przypisu
    assert any(
        (ORD["Gen"] * 10**6 + 1026, ORD["Gen"] * 10**6 + 1027) in p.refs
        for p in w.passages
    )
    assert w.passages[-1].page == "452"
    last = w.passages[-1].refs
    assert (
        ORD["1Cor"] * 10**6 + 15003,
        ORD["1Cor"] * 10**6 + 15003,
    ) in last  # z parsed
    assert (
        ORD["Ps"] * 10**6 + 110001,
        ORD["Ps"] * 10**6 + 110001,
    ) in last  # z tekstu, rzymskie cx


def test_parse_passage_text():
    from patristics.thml import parse_parsed_attr, parse_passage_text

    g = ORD["Gen"] * 10**6
    assert parse_passage_text("Gen. i. 26") == (g + 1026, g + 1026)
    assert parse_passage_text("Gen. i. 26-28") == (g + 1026, g + 1028)
    assert parse_passage_text("Matt. v") == (
        ORD["Matt"] * 10**6 + 5001,
        ORD["Matt"] * 10**6 + 5999,
    )
    assert parse_passage_text("1 Cor. xv. 3") == (ORD["1Cor"] * 10**6 + 15003,) * 2
    assert parse_passage_text("Cant. ii. 1") == (ORD["Song"] * 10**6 + 2001,) * 2
    assert parse_passage_text("Ecclus. xxiv. 5") == (ORD["Sir"] * 10**6 + 24005,) * 2
    assert parse_passage_text("Josephus, Ant. ii. 3") is None
    assert parse_parsed_attr("|Gen|1|26|1|27|") == (g + 1026, g + 1027)
    assert parse_parsed_attr("|Rev|21|1|21|1|") == (ORD["Rev"] * 10**6 + 21001,) * 2
