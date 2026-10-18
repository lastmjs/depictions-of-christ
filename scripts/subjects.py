"""Classify an artwork title into Christ-related subjects.

classify(title) returns a list of subject labels (matching those used by
harvest.py) or an empty list when the title does not indicate that Christ is
depicted. Used to filter museum open-access records, which have no structured
"depicts" data.
"""
import re

# (subject label, regex). Order matters only for display.
RULES = [
    ("Madonna and Child", r"\b(madonna|virgin|our lady|mary|maria|vierge|theotokos|mother of god)\b.{0,40}\b(child|infant|bambino|enfant|kind|son)\b|\bmadonna\b|\bvirgen\b|\btheotokos\b|\bhodegetria\b|\beleousa\b|\bmaria lactans\b|\bvirgo lactans\b"),
    ("Christ Child", r"\b(christ|infant|holy) child\b|\binfant (christ|jesus)\b|\bchild jesus\b|\bchrist ?kind\b|\bsanto ni[nñ]o\b|\bbambino\b|\benfant j[ée]sus\b"),
    ("Nativity", r"\bnativity\b|\bbirth of (christ|jesus)\b|\bnatividad\b|\bnativit[àé]\b|\bcr[eè]che\b|\bgeburt christi\b"),
    ("Adoration of the Shepherds", r"\badoration of the shepherds\b|\bannunciation to the shepherds\b"),
    ("Adoration of the Magi", r"\badoration of the (magi|kings)\b|\bthree (kings|magi)\b|\bepiphany\b|\bjourney of the magi\b"),
    ("Circumcision", r"\bcircumcision\b"),
    ("Presentation in the Temple", r"\bpresentation (of (christ|jesus) )?in the temple\b|\bpresentation of (christ|jesus)\b|\bpurification of the virgin\b"),
    ("Flight into Egypt", r"\bflight into egypt\b|\breturn from egypt\b"),
    ("Rest on the Flight into Egypt", r"\brest on the flight\b"),
    ("Holy Family", r"\bholy family\b|\bsacra famiglia\b|\bsagrada familia\b|\bholy kinship\b"),
    ("Christ among the Doctors", r"\bamong the doctors\b|\bdisputing with the doctors\b|\bfinding (of jesus )?in the temple\b|\bchrist in the temple\b"),
    ("Mystic Marriage of St Catherine", r"\bmystic(al)? marriage\b"),
    ("Baptism", r"\bbaptism of (christ|jesus|our lord)\b|\bchrist'?s baptism\b"),
    ("Temptation of Christ", r"\btemptation of (christ|jesus)\b|\bchrist (tempted|in the wilderness|in the desert)\b"),
    ("Wedding at Cana", r"\b(wedding|marriage) (feast )?at cana\b"),
    ("Sermon on the Mount", r"\bsermon on the mount\b"),
    ("Miracles of Christ", r"\b(christ|jesus) heal(s|ing)\b|\bhealing of the\b|\braising of lazarus\b|\braising of the (widow|daughter of jairus)\b|\bmiraculous draught\b|\bwalking on (the )?water\b|\bfeeding of the (five|multitude)\b|\bloaves and fishes\b|\bpool of bethesda\b"),
    ("Christ and Followers", r"\bcalling of (st\.? |saint )?(matthew|peter|andrew|the apostles)\b|\bchrist and the (woman|adulteress|samaritan|canaanite|rich young|centurion|children)\b|\bsuffer the little children\b|\blet the little children\b|\bwoman taken in adultery\b|\bmary and martha\b|\bmartha and mary\b|\bhouse of (simon|levi|martha)\b|\bchrist blessing the children\b|\btribute money\b|\bchrist (preaching|teaching)\b"),
    ("Transfiguration", r"\btransfiguration\b"),
    ("Entry into Jerusalem", r"\bentry into jerusalem\b|\bpalm sunday\b"),
    ("Cleansing of the Temple", r"\b(cleansing|purification|expulsion).{0,25}temple\b|\bmoney ?changers\b"),
    ("Last Supper", r"\blast supper\b|\bcommunion of the apostles\b|\bwashing (of )?the (disciples'?|apostles'?) feet\b|\bchrist washing\b|\bcena\b|\babendmahl\b|\bcène\b"),
    ("Agony in the Garden", r"\bagony in the garden\b|\bgethsemane\b|\bmount of olives\b|\bchrist on the mount\b"),
    ("Betrayal & Arrest", r"\bbetrayal of (christ|jesus)\b|\bkiss of judas\b|\barrest of (christ|jesus)\b|\btaking of christ\b|\bcapture of christ\b"),
    ("Trial of Christ", r"\bchrist before (pilate|caiaphas|annas|herod|the high priest)\b|\bjesus before\b|\bpilate washing\b"),
    ("Flagellation", r"\bflagellation\b|\bscourging\b|\bchrist at the column\b|\bchrist bound\b"),
    ("Crowning with Thorns", r"\bcrown(ing|ed) with thorns\b|\bcrown of thorns\b"),
    ("Mocking of Christ", r"\bmocking of (christ|jesus)\b|\bchrist mocked\b"),
    ("Ecce Homo", r"\becce homo\b"),
    ("Carrying the Cross", r"\b(christ|jesus) (carrying|bearing) the cross\b|\bcross-?bearing\b|\broad to (calvary|golgotha)\b|\bway to calvary\b|\bstations? of the cross\b|\bvia crucis\b|\bkreuztragung\b|\b(saint |st\.? )?veronica.{0,20}(veil|sudarium|cloth|holding)|\bveil of veronica\b|\bsudarium\b|\bvernicle\b"),
    ("Crucifixion", r"\bcrucifixion\b|\bcrucified\b|\bcrucifix\b|\bchrist on the cross\b|\bcalvary\b|\bgolgotha\b|\bkreuzigung\b|\bcrucifixi[oó]n\b|\bcrocifiss|\bcristo crucificado\b"),
    ("Descent from the Cross", r"\bdescent from the cross\b|\bdeposition\b|\btaking down from the cross\b|\bkreuzabnahme\b"),
    ("Lamentation", r"\blamentation\b|\bbeweinung\b|\bmourning (over|for) the dead christ\b"),
    ("Pietà", r"\bpiet[àa]\b|\bvesperbild\b"),
    ("Entombment", r"\bentombment\b|\bburial of (christ|jesus)\b|\bdead christ\b|\bchrist in the tomb\b|\bgrablegung\b"),
    ("Man of Sorrows", r"\bman of sorrows\b|\bimago pietatis\b|\bschmerzensmann\b|\bchrist as the man of sorrows\b"),
    ("Harrowing of Hell", r"\bharrowing of hell\b|\bdescent into (limbo|hell)\b|\bchrist in limbo\b|\banastasis\b"),
    ("Resurrection", r"\bresurrection of (christ|jesus|our lord)\b|\b(the )?resurrection$|\brisen christ\b|\bchrist risen\b|\bauferstehung\b|\bresurrexit\b|\bholy women at the (tomb|sepulchre)\b|\bthree marys at the tomb\b"),
    ("Noli me tangere", r"\bnoli me tangere\b|\bchrist appearing to (mary|the magdalen)\b"),
    ("Supper at Emmaus", r"\bemmaus\b"),
    ("Appearances after the Resurrection", r"\bincredulity of (st\.? |saint )?thomas\b|\bdoubting thomas\b|\bchrist appearing to\b|\bappearance of christ\b|\bfeed my sheep\b"),
    ("Ascension", r"\bascension\b"),
    ("Last Judgment", r"\blast judg(e)?ment\b|\bday of judg(e)?ment\b|\bweltgericht\b|\bjuicio final\b|\bgiudizio universale\b"),
    ("Coronation of the Virgin", r"\bcoronation of the virgin\b"),
    ("Pantocrator", r"\bpantocrator\b|\bpantokrator\b|\bchrist in majesty\b|\bmaiestas domini\b|\bchrist enthroned\b|\bdeesis\b|\bdeësis\b"),
    ("Salvator Mundi", r"\bsalvator mundi\b|\bsaviour of the world\b|\bsavior of the world\b"),
    ("Holy Face", r"\bholy face\b|\bmandylion\b|\bnot made by hands\b|\bvolto santo\b|\bhead of christ\b|\bface of christ\b|\bbust of christ\b|\bportrait of (christ|jesus)\b"),
    ("Sacred Heart", r"\bsacred heart\b"),
    ("Good Shepherd", r"\bgood shepherd\b"),
    ("Throne of Mercy", r"\bthrone of mercy\b|\bgnadenstuhl\b|\bholy trinity\b|\btrinity\b"),
]
COMPILED = [(label, re.compile(rx, re.I)) for label, rx in RULES]

GENERAL = re.compile(r"\b(christ|jesus|jesu|cristo|christus|j[ée]sus|gesù|salvator|saviour|savior|redeemer|our lord|agnus dei|lamb of god|ihs)\b", re.I)
FALSE = re.compile(r"\bchrist(opher|ina|ine|ian|ians|ianity|mas|ie|church)\b|\bchrist church\b|\bcorpus christi college\b|\bjesus college\b|\bsociety of jesus\b|\bjesuit\b|\bchristie'?s\b", re.I)

# Titles that match a rule but where Christ himself is usually absent.
EXCLUDE = re.compile(r"\b(annunciation|visitation|assumption|immaculate|dormition|birth of the virgin|education of the virgin|presentation of the virgin|marriage of the virgin|pentecost)\b", re.I)


def classify(title, extra=""):
    """Return Christ-related subject labels for a title (plus optional extra text)."""
    text = f"{title} {extra}".strip()
    if not text:
        return []
    found = [label for label, rx in COMPILED if rx.search(text)]
    if found:
        if EXCLUDE.search(title) and not re.search(r"\b(christ|jesus|child|infant)\b", title, re.I):
            return []
        return found
    cleaned = FALSE.sub(" ", text)
    if GENERAL.search(cleaned):
        return ["Jesus Christ"]
    return []


if __name__ == "__main__":
    import sys
    for t in sys.argv[1:]:
        print(t, "->", classify(t))
