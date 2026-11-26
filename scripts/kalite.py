"""Yayın öncesi metin kalite katmanı (k1).

26.11.2026 incelemesinde bulunan yazım sorunlarının koddaki nedenleri:
  1. haber_cek.yz_yaz Türkçe karaktersiz yanıtı yalnızca KAYNAK metin Türkçe karakterliyse denetliyordu
     (kaynak_tr koşulu). Kaynak ASCII yazılmışsa (ör. "Akdeniz Universitesinden…") yanıt hiç denetlenmeden yayımlandı.
  2. Bütün metnin karakter oranına bakan denetim (metin_onar.ascii_turkce, %2,5 eşiği) birkaç kelimesi işaretli,
     çoğu işaretsiz metni ("ısık", "uyarisi") yakalamıyordu; kelime düzeyinde denetim yoktu.
  3. Etiketler, alt metin ve özet hiçbir denetimden geçmiyordu.
  4. Kesme işareti (' ve ’), başlık stili (Her Kelime Büyük / cümle düzeni) ve özel ad ekleri için kural yoktu.

Bu katman her yeni haberde sırayla:
  a) kural tabanlı düzeltmeler: kesme işaretini tek karaktere (') indirir, özel addan ayrı yazılmış eki birleştirir
     ("Çetin den" → "Çetin'den"), başlığı cümle düzenine getirir (tamamı büyük harf yasak);
  b) sorunları işaretler: Türkçe karakter kaybı (kelime düzeyinde, Türkçe çözümleyiciyle), ek uyumsuzluğu ("Antalya'nin"),
     sözlükte olmayan ve bitişik yazılmış kelimeler, intihar haberinde yöntem;
  c) ayrı bir "editör" yapay zekâ çağrısı metni YALNIZCA yazım/dilbilgisi yönünden düzeltir ve değişiklik listesi döndürür.
     Her değişiklik ayrıca doğrulanır: sayı, yeni ad, anlam değişikliği içeren ya da çok büyük değişiklik uygulanmaz;
  d) son denetimden geçmeyen haber yayımlanmaz (karantina).
"""
import difflib
import json
import os
import re
from datetime import datetime, timedelta, timezone

import metin_onar as mo
import yazim

KALITE_SURUM = "k1"
TR = timezone(timedelta(hours=3))
KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KARANTINA = os.path.join(KOK, "karantina", "haber")
RAPOR_DOSYA = os.path.join(KOK, "data", "kalite_raporu.json")

HARF = "A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû"
KELIME = re.compile(rf"[{HARF}]+")
APOS = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "´": "'", "`": "'", "′": "'"})

KISALTMALAR = set("""TL ABD AB AK CHP MHP DEM TBMM TFF KKTC ÇED TOKİ SGK DSİ ATSO ALTSO ATB ATİB TÜRSAK BAİB AKUT AFAD UEFA FIFA NATO BM
AA İHA DHA TRT PTT TCDD YKS LGS KPSS ALES ÖSYM MEB TÜİK TÜBİTAK KDV ÖTV LPG CNG AVM OSB GES HES COP COP31 ASAT MTA TEMA TSK MİT EGM
HSK YSK TÜSİAD MÜSİAD TOBB TESK KOAH MR BT AŞ FK SK BB İŞKUR KOSGEB TMMOB TTB TDK AKM ATM SMS GSM PKK FETÖ DEAŞ IŞİD YÖK
İYİ ODTÜ İTÜ YTÜ ALKÜ AÜ AKÜ EXPO NBA FIBA EHF TVF THF TBF UNESCO WHO UNICEF FAO IMF OECD G20 AKP DSÖ ASKİ AGC ABB""".split())
OZEL_SABIT = """antalya türkiye akdeniz ege karadeniz anadolu ankara istanbul izmir akseki aksu alanya demre döşemealtı elmalı finike
gazipaşa gündoğmuş ibradı kaş kemer kepez konyaaltı korkuteli kumluca manavgat muratpaşa serik kaleiçi lara belek side olimpos
türk avrupa almanya rusya ukrayna ingiltere amerika atatürk cumhurbaşkanı""".split()

# Özel addan sonra boşlukla ayrılmış ek ("Çetin den"). "de/da" bağlacı olabileceği için kendiliğinden düzeltilmez, yalnızca işaretlenir.
EK_GUVENLI = "den dan ten tan nin nın nun nün in ın un ün ye ya yi yı yu yü le la yle yla nde nda ndan nden e a i ı u ü".split()
EK_RE = re.compile(rf"(?<![{HARF}'])([A-ZÇĞİÖŞÜ][a-zçğıöşüâîû]{{2,}}) ({'|'.join(sorted(EK_GUVENLI, key=len, reverse=True))})(?![{HARF}])")

INTIHAR = re.compile(r"(?i)intihar")
YONTEM = re.compile(r"(?i)\b(silah\w*|tabanca\w*|tüfe\w*|kurşun\w*|ateş ederek|as(arak|ılı|tı|mış)\w*|kendini as\w*|urgan\w*|ilaç\w*|"
                    r"zehir\w*|hap\w*|atla(yarak|dı|mış)\w*|köprüden|balkondan|yüksekten|çatıdan|pencereden|tren önüne|raylar\w*|"
                    r"bıçak\w*|jilet\w*|kesici|yakarak|kendini yak\w*|boğ\w*|gaz\w*)\b")

# ------------------------------------------------------------------ yardımcılar


def kucuk(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def buyuk_ilk(s):
    return ({"i": "İ", "ı": "I"}.get(s[:1], s[:1].upper()) + s[1:]) if s else s


def katla(s):
    return mo.katla(s)


def kesme_normalize(s):
    return (s or "").translate(APOS)


def _unluler(s):
    return [c for c in kucuk(s) if c in "aeıioöuü"]


ON_UNLU, ARKA_UNLU = set("eiöü"), set("aıou")


def ek_uyumsuz(kok, ek):
    """Kesme işaretli ekte ünlü uyumu bozuk mu ("Antalya'nin", "Kemer'da")? Kısaltmalar (okunuşu belirsiz) atlanır."""
    if kok.isupper() or not _unluler(kok) or not _unluler(ek):
        return False
    son = _unluler(kok)[-1]
    eku = _unluler(ek)
    if son in "aıou" and any(u in ON_UNLU for u in eku):
        return True
    if son in "eiöü" and any(u in ARKA_UNLU for u in eku):
        return True
    return False


def _cumle_basi(m):
    once = m.string[max(0, m.start() - 3):m.start()]
    return m.start() == 0 or re.search(r"[.!?:\n\"“]\s*$", once) is not None


# ------------------------------------------------------------------ kural tabanlı düzeltmeler


def ek_birlestir(metin):
    """'Çetin den' → 'Çetin'den' (yalnızca cümle içindeki büyük harfli kelimeden sonra, güvenli eklerde). Dönüş: (metin, değişiklikler)."""
    degisen = []

    def f(m):
        if m.group(2) in ("ya", "ye") and re.match(r" d[ae](?![a-zçğıöşü])", m.string[m.end():]):
            return m.group(0)                      # "ya da" bağlacı
        if _cumle_basi(m) and not yazim.ozel_ad(kucuk(m.group(1))):
            return m.group(0)
        yeni = f"{m.group(1)}'{m.group(2)}"
        degisen.append((m.group(0), yeni))
        return yeni
    return EK_RE.sub(f, metin or ""), degisen


def ozel_adlar(referans):
    """Referans metinde (spot + metin) özel ad olarak kullanılan kelimeler (katlanmış): cümle içinde büyük harfle geçişi, küçük harfle
    geçişinden az olmayanlar ("Kadın Kooperatifi" bir kez büyük, "kadın" on kez küçük geçiyorsa "kadın" özel ad sayılmaz)."""
    buyuk, kucuk_ = {}, {}
    for m in KELIME.finditer(referans or ""):
        w, k = m.group(0), katla(m.group(0))
        if w[:1].isupper():
            if not _cumle_basi(m):
                buyuk[k] = buyuk.get(k, 0) + 1
        else:
            kucuk_[k] = kucuk_.get(k, 0) + 1
    return {katla(x) for x in OZEL_SABIT} | {k for k, n in buyuk.items() if n >= kucuk_.get(k, 0)}


def _ozel_mi(k, ozel):
    """Kelime (katlanmış) özel ad mı? Ekli biçimler de ("universitesinden" ← "universitesi") kökle eşleşir."""
    if k in ozel:
        return True
    return any(len(w) >= 5 and k.startswith(w) and len(k) - len(w) <= 6 for w in ozel)


def cumle_duzeni(baslik, referans=""):
    """Başlık tek kurala getirilir: cümle düzeni (yalnızca ilk harf, özel adlar ve kısaltmalar büyük). Tamamı büyük harf yasaktır."""
    b = re.sub(r"\s+", " ", kesme_normalize(baslik or "")).strip()
    if not b:
        return b
    harf = [c for c in b if c.isalpha()]
    tamami_buyuk = len(harf) >= 6 and sum(c.isupper() for c in harf) / len(harf) > 0.8
    if tamami_buyuk and referans:
        b = mo.kaynakla_onar(b, referans)            # "AILE" → referanstaki yazımla "AİLE"
    ozel = ozel_adlar(referans)
    parcalar = re.split(r"(\s+)", b)
    out, ilk = [], True
    for p in parcalar:
        if not p or p.isspace():
            out.append(p)
            continue
        m = re.match(rf"^([^{HARF}0-9]*)([{HARF}0-9.\-]*)(.*)$", p)
        on, kok, son = m.group(1), m.group(2), m.group(3)
        if not kok or not re.search(rf"[{HARF}]", kok):
            out.append(p)
            ilk = ilk and not kok
            continue
        if son.startswith("'"):
            son = "'" + kucuk(son[1:])
        kisaltma = bool(re.search(r"\d", kok)) or kok.upper() in KISALTMALAR and (kok.isupper() or tamami_buyuk) \
            or (not tamami_buyuk and kok.isupper() and 2 <= len(kok) <= 5)
        if kisaltma:
            yeni = kok.upper() if tamami_buyuk and kok.upper() in KISALTMALAR else kok
        elif not ilk and not tamami_buyuk and kok[:1].islower():
            yeni = kok                                  # küçük yazılmış kelime büyütülmez (yalnızca gereksiz büyük harf düzeltilir)
        else:
            parcalar_ = kucuk(kok).split("-")
            yeni = "-".join(buyuk_ilk(x) if _ozel_mi(katla(x), ozel) else x for x in parcalar_)
            if ilk:
                yeni = buyuk_ilk(yeni)
        out.append(on + yeni + son)
        ilk = False
    return "".join(out)


def kurallari_uygula(alanlar, referans="", baslik_duzeni=True):
    """Kesme işareti, ayrı yazılmış özel ad eki, başlık düzeni. alanlar: baslik/spot/metin/etiketler/alt. Dönüş: (alanlar, notlar)."""
    out, notlar = dict(alanlar), []
    for a in ("baslik", "spot", "metin", "alt"):
        if out.get(a):
            yeni = kesme_normalize(out[a])
            if yeni != out[a]:
                notlar.append(f"{a}: kesme işareti tek karaktere indirildi")
            yeni, d = ek_birlestir(yeni)
            notlar += [f"{a}: {x}→{y}" for x, y in d]
            out[a] = yeni
    if out.get("etiketler"):
        out["etiketler"] = [kesme_normalize(e) for e in out["etiketler"]]
    if out.get("baslik") and baslik_duzeni:
        b = cumle_duzeni(out["baslik"], referans or f"{out.get('spot', '')} {out.get('metin', '')}")
        if b != out["baslik"]:
            notlar.append(f"başlık cümle düzenine getirildi: {out['baslik']} → {b}")
            out["baslik"] = b
    return out, notlar


# ------------------------------------------------------------------ işaretleme


def ascii_supheli(metin):
    """Türkçe karakteri düşmüş olması muhtemel kelimeler. Türkçe çözümleyici işaretsiz biçimleri de çoğu zaman geçerli saydığı için
    ("aldi", "basarisi") asıl ölçüt sitedeki doğru yazılmış haberlerdeki sıklıktır: harf işareti eklenmiş biçim çok daha sık
    geçiyorsa kelime şüphelidir. Sitede hiç geçmeyen kelimede çözümleyiciye bakılır (kendisi geçersiz, işaretli biçimi geçerli)."""
    out = []
    for m in KELIME.finditer(metin or ""):
        w = m.group(0)
        if len(w) < 3 or (w.isupper() and len(w) > 1) or (m.start() > 0 and m.string[m.start() - 1] == "'"):
            continue
        k = kucuk(w)
        fw = yazim.SOZLUK.get(k, 0)
        ady = [y for y in yazim.adaylar(k, 512) if all(a == b or (a, b) in (("c", "ç"), ("g", "ğ"), ("i", "ı"), ("o", "ö"), ("s", "ş"), ("u", "ü"))
                                                      for a, b in zip(k, y))]
        if not ady:
            continue
        fy, y = max((yazim.SOZLUK.get(x, 0), x) for x in ady)
        if fy >= 3 and fy >= 5 * max(fw, 1):
            out.append(w)
        elif fw < 2 and fy == 0 and not yazim.gecerli(k) and any(yazim.gecerli(x) for x in ady[:64]):
            out.append(w)
    return list(dict.fromkeys(out))


def ek_uyum_supheli(metin):
    """Kesme işaretli ekte ünlü uyumu bozuk yerler ("Antalya'nin"). Yabancı adlarda (okunuşa göre ek alan) yanlış alarm olabilir:
    yalnızca editöre ipucu olarak verilir, yayını tek başına engellemez."""
    return list(dict.fromkeys(m.group(0) for m in re.finditer(rf"([{HARF}]+)'([a-zçğıöşü]+)", metin or "")
                              if ek_uyumsuz(m.group(1), m.group(2))))


def bitisik_supheli(metin):
    """Sözlükte olmayan ve iki bilinen kelimeye bölünebilen kelimeler ("Baziulkelerde" → "Bazi ulkelerde")."""
    out = []
    for w in yazim.bilinmeyenler(metin or ""):
        k = kucuk(w)
        if len(k) < 7:
            continue
        for i in range(3, len(k) - 2):
            a, b = k[:i], k[i:]
            if (yazim.bilinen(a) or [y for y in yazim.adaylar(a, 64) if yazim.bilinen(y)]) and \
                    (yazim.bilinen(b) or [y for y in yazim.adaylar(b, 64) if yazim.bilinen(y)]):
                out.append(w)
                break
    return out


def intihar_yontemi(alanlar):
    """İntihar haberinde başlıkta ya da spotta yöntem geçiyor mu? Dönüş: geçen ifadeler."""
    tum = " ".join(str(alanlar.get(a) or "") for a in ("baslik", "spot", "metin"))
    if not INTIHAR.search(tum):
        return []
    return [m.group(0) for a in ("baslik", "spot") for m in YONTEM.finditer(str(alanlar.get(a) or ""))]


def sorunlar(alanlar):
    """Alan bazında sorun listesi: {alan: {ascii: [...], bitisik: [...], bilinmeyen: [...], ek_ayri: [...]}} ve genel sorunlar."""
    s = {}
    for a in ("baslik", "spot", "metin", "alt"):
        m = alanlar.get(a) or ""
        if not m:
            continue
        d = {"ascii": ascii_supheli(m), "ek_uyum": ek_uyum_supheli(m), "bitisik": bitisik_supheli(m) if a != "alt" else [],
             "bilinmeyen": [w for w in yazim.bilinmeyenler(m, a == "baslik")][:30],
             "ek_ayri": [x.group(0) for x in re.finditer(rf"(?<![{HARF}'])[A-ZÇĞİÖŞÜ][a-zçğıöşü]+ (de|da)(?![{HARF}])", m)]}
        if mo.ascii_turkce(m, en_az=200 if a == "metin" else 60):
            d["ascii_metin"] = True
        if a == "baslik":
            harf = [c for c in m if c.isalpha()]
            if harf and sum(c.isupper() for c in harf) / len(harf) > 0.8:
                d["tamami_buyuk"] = True
        if "’" in m or "‘" in m:
            d["kesme"] = True
        s[a] = {k: v for k, v in d.items() if v}
    et = []
    for e in alanlar.get("etiketler") or []:
        et += ascii_supheli(e)
    if et:
        s["etiketler"] = {"ascii": et}
    yontem = intihar_yontemi(alanlar)
    if yontem:
        s["genel"] = {"intihar_yontemi": yontem}
    return {k: v for k, v in s.items() if v}


# ------------------------------------------------------------------ editör (yapay zekâ) ve doğrulama

EDITOR_TALIMAT = """Sen bir Türkçe haber sitesinin yazım editörüsün. Aşağıdaki haberin YALNIZCA yazım, noktalama ve dilbilgisi hatalarını düzelt.
Kurallar:
- Anlamı, olguları, sayıları, tarihleri, adları ve cümle yapısını DEĞİŞTİRME; yeni bilgi ekleme, cümle silme, yeniden yazma.
- Kaynakta Türkçe karakterler eksik olabilir: tüm ç, ğ, ı, İ, ö, ş, ü harflerini doğru kullan (Universitesi → Üniversitesi, Ayse → Ayşe, aldi → aldı).
- Bitişik yazılmış kelimeleri ayır (Baziulkelerde → Bazı ülkelerde), bozuk kelimeleri düzelt (ulaşırmışken → ulaşırken).
- Özel adlara gelen ekleri kesme işaretiyle ayır (Çetin den → Çetin'den). Kesme işareti olarak yalnızca ' kullan.
- Ek ünlü uyumunu düzelt (Antalya'nin → Antalya'nın).
- Başlık cümle düzeninde olsun: yalnızca ilk harf ve özel adlar büyük; tamamı büyük harf yasak.
- Etiketler kişi/kurum/yer adlarıdır: yalnızca Türkçe karakterlerini ve yazımını düzelt.
- İntihar haberinde intihar yöntemi/aracı başlıkta ve spotta geçmesin (ör. "Silahlı intihar" → "İntihar"); yalnızca o ifadeyi çıkar.
- Düzeltilecek bir şey yoksa metni aynen döndür.
<IPUCU>
Yalnızca şu biçimde JSON döndür:
{"baslik": "...", "spot": "...", "metin": "...", "etiketler": ["..."], "degisiklikler": [{"once": "...", "sonra": "...", "neden": "..."}]}

HABER:
"""


def editor_istegi(alanlar, sorun):
    ipucu = []
    for a, d in sorun.items():
        for t, l in d.items():
            if isinstance(l, list) and l:
                ipucu.append(f"- {a} / {t}: {', '.join(map(str, l[:25]))}")
    ip = ("Otomatik denetimin işaretlediği şüpheli yerler (kontrol et; doğruysa dokunma):\n" + "\n".join(ipucu)) if ipucu else ""
    veri = {"baslik": alanlar.get("baslik", ""), "spot": alanlar.get("spot", ""), "metin": alanlar.get("metin", ""),
            "etiketler": list(alanlar.get("etiketler") or [])}
    return EDITOR_TALIMAT.replace("<IPUCU>", ip) + json.dumps(veri, ensure_ascii=False, indent=1)


TOKEN = re.compile(rf"[{HARF}0-9]+|\s+|[^\s{HARF}0-9]")


def _kucuk_degisiklik(a, b):
    """Editörün bir değişikliği kabul edilebilir mi? (yazım düzeltmesi mi, anlam değişikliği mi)"""
    aa, bb = "".join(a), "".join(b)
    if re.findall(r"\d+", aa) != re.findall(r"\d+", bb):
        return False, "sayı değişti"
    ka, kb = katla(aa).replace(" ", "").replace("'", ""), katla(bb).replace(" ", "").replace("'", "")
    if ka == kb:
        return True, "harf işareti/boşluk/kesme"
    # büyük harfle başlayan yeni kelime (yeni ad) eklenmesin
    yeni_ad = {katla(w) for w in KELIME.findall(bb) if w[:1].isupper()} - {katla(w) for w in KELIME.findall(aa) if w[:1].isupper()}
    if yeni_ad and not all(any(katla(x).startswith(y[:4]) for x in KELIME.findall(aa)) for y in yeni_ad):
        return False, "yeni ad"
    uz = yazim._uzaklik(ka, kb)
    if uz <= max(2, len(ka) // 4) and abs(len(ka) - len(kb)) <= max(3, len(ka) // 4):
        return True, "küçük yazım düzeltmesi"
    return False, f"büyük değişiklik ({uz} harf)"


def birlestir(eski, yeni):
    """Editörün metnini kelime/parça düzeyinde karşılaştırır; yalnızca küçük (yazım) değişiklikleri uygular.
    Dönüş: (birleşik metin, uygulanan [(önce, sonra)], reddedilen [(önce, sonra, neden)])."""
    A, B = TOKEN.findall(eski or ""), TOKEN.findall(yeni or "")
    sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
    out, uyg, red = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out += A[i1:i2]
            continue
        a, b = A[i1:i2], B[j1:j2]
        ok, neden = _kucuk_degisiklik(a, b)
        if tag in ("delete", "insert") and not "".join(a + b).strip(" '-,") == "":
            # yalnızca boşluk/kesme/noktalama eklenip silinebilir; kelime eklenip silinemez
            ok = ok and not KELIME.search("".join(a + b))
        if ok:
            out += b
            if "".join(a) != "".join(b):
                uyg.append(("".join(a), "".join(b)))
        else:
            out += a
            red.append(("".join(a), "".join(b), neden))
    return "".join(out), uyg, red


def editor_uygula(alanlar, yanit):
    """Editör yanıtını alan alan doğrulayarak uygular. Dönüş: (alanlar, değişiklik listesi, reddedilenler)."""
    out, degis, red = dict(alanlar), [], []
    if not isinstance(yanit, dict):
        return out, degis, [("", "", "editör yanıtı okunamadı")]
    for a in ("baslik", "spot", "metin"):
        if isinstance(yanit.get(a), str) and yanit[a].strip():
            yeni_metin = yanit[a].strip()
            if a == "metin":
                yeni_metin = yeni_metin.replace("\\n", "\n")
            # editörün metni çok farklıysa (yeniden yazılmışsa) hiç uygulanmaz
            oran = difflib.SequenceMatcher(None, katla(out.get(a) or ""), katla(yeni_metin), autojunk=False).ratio()
            if oran < 0.85:
                red.append((a, "", f"metin çok değişti (benzerlik {oran:.2f})"))
                continue
            m, u, r = birlestir(out.get(a) or "", yeni_metin)
            out[a] = m
            degis += [(a, x, y) for x, y in u]
            red += [(a, f"{x}→{y}", n) for x, y, n in r]
    et = yanit.get("etiketler")
    if isinstance(et, list) and len(et) == len(out.get("etiketler") or []):
        yeni = []
        for e0, e1 in zip(out.get("etiketler") or [], et):
            e1 = kesme_normalize(str(e1 or "")).strip()
            if e1 and katla(e0).replace(" ", "") == katla(e1).replace(" ", ""):
                yeni.append(e1)
                if e1 != e0:
                    degis.append(("etiket", e0, e1))
            else:
                yeni.append(e0)
        out["etiketler"] = yeni
    return out, degis, red


def etiket_normalize(etiketler):
    """Etiketlerin kesme işareti ve Türkçe karakterleri düzeltilir; aynı etiketin işaretli/işaretsiz biçimleri tekilleştirilir
    (Türkçe karakteri çok olan biçim kalır)."""
    secilen = {}
    for e in etiketler or []:
        e = re.sub(r"\s+", " ", kesme_normalize(str(e or ""))).strip(" .,-#")
        if not e:
            continue
        kelimeler = []
        for w in e.split(" "):
            y = yazim.kelime_onar(w, ozel=True) if not any(c in "çğıöşüÇĞİÖŞÜ" for c in w) else None
            kelimeler.append(y or w)
        e = " ".join(kelimeler)
        k = katla(e)
        onceki = secilen.get(k)
        if not onceki or mo.tr_orani(e) > mo.tr_orani(onceki):
            secilen[k] = e
    return list(secilen.values())


# ------------------------------------------------------------------ ana giriş
_SOZLUK = {"ok": False}


def sozluk_hazirla():
    """Sitedeki doğru yazılmış haberlerden kelime sıklığı sözlüğü (yazim.bilinen için; bir kez)."""
    if _SOZLUK["ok"] or yazim.SOZLUK:
        _SOZLUK["ok"] = True
        return
    _SOZLUK["ok"] = True
    import yaml
    metinler = []
    for klasor in ("haber", "kose"):
        d = os.path.join(KOK, "content", klasor)
        for ad in os.listdir(d) if os.path.isdir(d) else []:
            try:
                with open(os.path.join(d, ad), encoding="utf-8") as f:
                    metinler.append(f.read())
            except Exception:
                pass
    yazim.sozluk_kur(metinler)


def kalan_sorunlar(alanlar):
    """Yayını engelleyen sorunlar (editörden sonra hâlâ varsa haber karantinaya düşer)."""
    s = sorunlar(alanlar)
    engel = []
    for a, d in s.items():
        if d.get("ascii_metin"):
            engel.append(f"{a}: metin Türkçe karaktersiz")
        if len(d.get("ascii", [])) >= (1 if a in ("baslik", "etiketler") else 2):
            engel.append(f"{a}: Türkçe karakteri eksik kelimeler: {', '.join(d['ascii'][:8])}")
        if len(d.get("bitisik", [])) >= 2:
            engel.append(f"{a}: bitişik/bozuk kelimeler: {', '.join(d['bitisik'][:8])}")
        if d.get("tamami_buyuk"):
            engel.append("başlık tamamı büyük harf")
        if d.get("intihar_yontemi"):
            engel.append(f"intihar yöntemi başlık/spotta: {', '.join(d['intihar_yontemi'])}")
    return engel, s


def denetle(alanlar, gemini_fn=None, referans=""):
    """Yayın öncesi kalite denetimi. alanlar: baslik, spot, metin, etiketler (ve isteğe bağlı alt).
    gemini_fn(metin) → JSON (editör çağrısı). Dönüş: (düzeltilmiş alanlar, rapor{gecti, engeller, degisiklikler, reddedilen, sorunlar})."""
    sozluk_hazirla()
    out, notlar = kurallari_uygula(alanlar, referans, baslik_duzeni=not gemini_fn)
    out["etiketler"] = etiket_normalize(out.get("etiketler"))
    s0 = sorunlar(out)
    rapor = {"surum": KALITE_SURUM, "kurallar": notlar, "sorunlar_once": s0, "degisiklikler": [], "reddedilen": [], "editor": "yapılmadı"}
    if gemini_fn:
        try:
            yanit = gemini_fn(editor_istegi(out, s0))
            out, degis, red = editor_uygula(out, yanit)
            rapor["degisiklikler"] = [f"{a}: {x} → {y}" for a, x, y in degis]
            rapor["reddedilen"] = [f"{a}: {x} ({n})" for a, x, n in red][:40]
            rapor["editor"] = "yapıldı"
            out, n2 = kurallari_uygula(out, referans)      # editör sonrası başlık düzeni ve kesme işareti yeniden
            rapor["kurallar"] += n2
            out["etiketler"] = etiket_normalize(out.get("etiketler"))
        except Exception as e:
            rapor["editor"] = f"hata: {e}"[:200]
    engel, s1 = kalan_sorunlar(out)
    if gemini_fn and rapor["editor"] != "yapıldı":
        engel.append("editör denetimi yapılamadı")
    rapor["sorunlar_sonra"] = s1
    rapor["engeller"] = engel
    rapor["gecti"] = not engel
    return out, rapor


# ------------------------------------------------------------------ karantina ve kalite raporu


def karantinaya_al(dosya, on, govde, rapor):
    """Denetimden geçmeyen haber yayımlanmaz: karantina/haber/ klasörüne (Hugo'nun içeriği dışında) yazılır."""
    import yaml
    os.makedirs(KARANTINA, exist_ok=True)
    on = dict(on, karantina={"tarih": datetime.now(TR).isoformat(timespec="seconds"), "nedenler": rapor.get("engeller", []),
                             "degisiklikler": rapor.get("degisiklikler", [])[:30]})
    with open(os.path.join(KARANTINA, dosya), "w", encoding="utf-8") as f:
        f.write("---\n" + yaml.safe_dump(on, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + (govde or "").strip() + "\n")
    return os.path.join("karantina", "haber", dosya)


def rapor_oku():
    try:
        with open(RAPOR_DOSYA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"turlar": [], "haberler": []}


def rapor_yaz(tur, haberler):
    """Her otomatik çalıştırmanın kalite raporu: yayımlanan, karantina, başlık kartı, ortalama görsel puanı + haber bazında kayıt."""
    r = rapor_oku()
    r.setdefault("turlar", []).append(tur)
    r["turlar"] = r["turlar"][-500:]
    r.setdefault("haberler", []).extend(haberler)
    r["haberler"] = r["haberler"][-400:]
    os.makedirs(os.path.dirname(RAPOR_DOSYA), exist_ok=True)
    with open(RAPOR_DOSYA, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
