"""Türkçe yazım (harf işareti) denetimi: Türkçe biçimbilim çözümleyicisiyle (zeyrek, Zemberek'in Python sürümü) sözlükte
bulunmayan kelimenin ç/ğ/ı/ö/ş/ü ile yazılmış biçimlerinden yalnızca biri geçerliyse o biçime getirilir."""
import contextlib, io, itertools, logging, re

KELIME = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû]+")
ESLER = {"c": "cç", "g": "gğ", "i": "iı", "ı": "ıi", "o": "oö", "s": "sş", "u": "uü"}
_A = None
_ON = {}


def _cozumleyici():
    global _A
    if _A is None:
        try:
            logging.disable(logging.CRITICAL)
            import zeyrek
            with contextlib.redirect_stdout(io.StringIO()):
                _A = zeyrek.MorphAnalyzer()
        except Exception as e:
            print("Yazım denetimi çözümleyicisi yüklenemedi:", e)
            _A = False
    return _A


def kucuk(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def cozum(k):
    """Kelimenin çözümlemeleri: [(kök, tür)] (küçük harfli kelime)."""
    if k in _ON:
        return _ON[k]
    a = _cozumleyici()
    r = []
    if a:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                for p in a._parse(k):
                    if p is not None:
                        r.append((p.dict_item.lemma, p.pos.value, getattr(p.dict_item, "secondary_pos", None) and p.dict_item.secondary_pos.value))
        except Exception:
            r = []
    _ON[k] = r
    return r


def gecerli(k):
    return bool(cozum(k))


def ozel_ad(k):
    return any(str(x[2]) == "ProperNoun" or "Prop" in str(x[2]) for x in cozum(k))


def _harf_duzeni(ornek, yeni):
    if ornek.isupper() and len(ornek) > 1:
        return yeni.replace("i", "İ").replace("ı", "I").upper()
    if ornek[:1].isupper():
        return {"i": "İ", "ı": "I"}.get(yeni[:1], yeni[:1].upper()) + yeni[1:]
    return yeni


def adaylar(k, sinir=4096):
    """k (küçük harf) kelimesinin harf işareti değişkeleri (en fazla 'sinir' tane)."""
    yer = [i for i, c in enumerate(k) if c in ESLER]
    if len(yer) > 12:
        return []
    out = []
    for secim in itertools.product(*[ESLER[k[i]] for i in yer]):
        y = list(k)
        for i, c in zip(yer, secim):
            y[i] = c
        y = "".join(y)
        if y != k:
            out.append(y)
        if len(out) >= sinir:
            break
    return out


SOZLUK = {}          # sitedeki doğru yazılmış haberlerden kelime sıklıkları (küçük harf) — çözümleyicinin bilmediği kelimeler için


def sozluk_kur(metinler):
    """Türkçe karakter oranı normal olan metinlerden kelime sıklığı sözlüğü."""
    import collections
    c = collections.Counter()
    for m in metinler:
        harf = [x for x in m if x.isalpha()]
        if len(harf) < 200 or sum(1 for x in harf if x in "çğıöşüÇĞİÖŞÜ") / len(harf) < 0.045:
            continue
        c.update(kucuk(w) for w in KELIME.findall(m))
    SOZLUK.clear()
    SOZLUK.update(c)
    return len(c)


def bilinen(k):
    return SOZLUK.get(k, 0) >= 2 or gecerli(k)


def kelime_onar(w, ozel=False, kaynak=None):
    """Bilinmeyen kelimenin harf işareti EKLENMİŞ (ç, ğ, ı, ö, ş, ü; işaret kaldırılmaz) değişkelerinden biri seçilir:
    önce kaynak metinde geçen, sonra sitedeki doğru yazılmış haberlerde sık geçen, sonra çözümleyicinin tek geçerli bulduğu.
    ozel: cümle içinde büyük harfle başlayan kelime (özel ad): yalnızca kaynakta ya da sitede geçen biçim kabul edilir."""
    k = kucuk(w)
    if len(k) < 3 or (w.isupper() and len(w) > 1) or bilinen(k):
        return None
    ady = [y for y in adaylar(k) if all(a == b or (a, b) in (("c", "ç"), ("g", "ğ"), ("i", "ı"), ("o", "ö"), ("s", "ş"), ("u", "ü"))
                                        for a, b in zip(k, y))]
    if kaynak:
        kk = [y for y in ady if y in kaynak]
        if len(kk) == 1:
            return _harf_duzeni(w, kk[0])
    sik = sorted(((SOZLUK.get(y, 0), y) for y in ady if SOZLUK.get(y, 0) >= 2), reverse=True)
    if sik and (len(sik) == 1 or sik[0][0] >= 3 * sik[1][0]):
        return _harf_duzeni(w, sik[0][1])
    return None             # sitede ya da kaynakta geçmeyen biçim tahmin edilmez (yazım hatası olabilir: yazım denetimine kalır)


def metin_onar(metin, kaynak_metin="", baslik=False):
    """Metindeki işaretsiz/yanlış işaretli kelimeleri onarır. Dönüş: (yeni metin, [(eski, yeni)])."""
    if not _cozumleyici():
        return metin, []
    kaynak = set(kucuk(x) for x in KELIME.findall(kaynak_metin or ""))
    degisen = []

    def degis(m):
        w = m.group(0)
        once = m.string[max(0, m.start() - 3):m.start()]
        if m.start() > 0 and m.string[m.start() - 1] in "'’":
            return w                       # kesme işaretinden sonraki ek: önceki kelimeyle birlikte değerlendirilmez
        if w.isupper() and len(w) <= 5:
            return w                       # kısaltma (TBMM, ASAT)
        cumle_basi = m.start() == 0 or re.search(r"[.!?:\n\"“]\s*$", once) is not None
        ozel = w[:1].isupper() and not cumle_basi and not baslik
        y = kelime_onar(w, ozel, kaynak)
        if y and y != w:
            degisen.append((w, y))
            return y
        return w
    return KELIME.sub(degis, metin or ""), degisen


def bilinmeyenler(metin, baslik=False):
    """Sözlükte ve sitede geçmeyen, cümle içinde küçük harfle başlayan kelimeler (olası yazım hataları)."""
    out = []
    for m in KELIME.finditer(metin or ""):
        w = m.group(0)
        if len(w) < 4 or (m.start() > 0 and m.string[m.start() - 1] in "'’") or (w.isupper() and len(w) > 1):
            continue
        once = m.string[max(0, m.start() - 3):m.start()]
        cumle_basi = m.start() == 0 or re.search(r"[.!?:\n\"“]\s*$", once) is not None
        if w[:1].isupper() and not cumle_basi and not baslik:
            continue
        if not bilinen(kucuk(w)):
            out.append(w)
    return out


# ----------------------------------------------------------------- yazım hatası (yapay zekâ önerisi + sözlük doğrulaması)
_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def katla(s):
    return kucuk(s).translate(_TR)


def _uzaklik(a, b):
    """Levenshtein uzaklığı."""
    if a == b:
        return 0
    onceki = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        simdiki = [i]
        for j, y in enumerate(b, 1):
            simdiki.append(min(onceki[j] + 1, simdiki[j - 1] + 1, onceki[j - 1] + (x != y)))
        onceki = simdiki
    return onceki[-1]


DUZELT_TALIMAT = """Aşağıdaki kelimeler bir Türkçe haber metninde geçiyor ve yazım hatası olabilir (eksik, fazla ya da yanlış harf;
bitişik yazılmış iki kelime; Türkçe karakter eksikliği). Her kelimenin, verilen cümledeki doğru yazımını ver.
Kurallar: kelimeyi başka bir kelimeyle DEĞİŞTİRME, yalnızca yazımını düzelt; kelime zaten doğruysa, bir özel ad, yabancı kelime
ya da kısaltmaysa aynen yaz. Yalnızca şu biçimde JSON döndür: {"kelime": "doğru yazım", ...}

"""


def duzelt_istegi(kelimeler, metin):
    """kelimeler: şüpheli kelimeler; metin: bağlam için haber metni. Her kelime geçtiği cümleyle birlikte verilir."""
    satir = []
    for w in kelimeler:
        m = re.search(r"[^.!?\n]*(?<![A-Za-zÇĞİÖŞÜçğıöşü])" + re.escape(w) + r"(?![A-Za-zÇĞİÖŞÜçğıöşü])[^.!?\n]*", metin or "")
        satir.append(f'- "{w}" — cümle: {(m.group(0).strip() if m else "")[:220]}')
    return DUZELT_TALIMAT + "\n".join(satir)


def oneri_dogrula(w, o):
    """Yapay zekânın önerisi kabul edilir mi? Öneri sözlükte/sitede bilinen kelime(ler)den oluşmalı ve yazım olarak yakın olmalı
    (harf işaretleri sayılmadan en fazla 2 harf farkı; uzun kelimede 1/6 oranı). Dönüş: kabul edilen biçim ya da None."""
    o = re.sub(r"\s+", " ", str(o or "")).strip()
    if not o or o == w or not re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû]+(?: [A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû]+)?", o):
        return None
    if bilinen(kucuk(w)):
        return None
    if any(not bilinen(kucuk(p)) for p in o.split(" ")):
        return None
    if katla(o.replace(" ", ""))[:1] != katla(w)[:1]:
        return None
    if _uzaklik(katla(w), katla(o.replace(" ", ""))) > max(2, len(w) // 6):
        return None
    if len(o.replace(" ", "")) < len(w) - 1:
        return None                        # ek düşürme (ör. "yaptığını" → "yaptı") yazım düzeltmesi değildir
    return _harf_duzeni(w, kucuk(o)) if not w.isupper() else o.upper()


def oneri_uygula(metin, duzeltme):
    """{eski: yeni} düzeltmelerini kelime sınırlarında uygular."""
    for a, b in duzeltme.items():
        metin = re.sub(r"(?<![A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû])" + re.escape(a) + r"(?![A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû])", b, metin)
    return metin
