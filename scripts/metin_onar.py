"""Türkçe karakter denetimi ve onarımı.

Sorunun kaynağı (2026-11-09 incelemesi): kaynak sayfalar Türkçe karakterlidir (ör. Cumhuriyet "Kaş'ta dehşet…"); haber metnini yazan
yapay zekâ bazı yanıtlarda bütün metni ç/ğ/ı/ö/ş/ü olmadan (ASCII) döndürdü. Başlık TAMAMI BÜYÜK HARFLE ve ASCII gelince
başlık düzeltmesi "AILE"yi "Aıle" yaptı (büyük I'nın Türkçede küçüğü ı'dır; asıl kelime "AİLE" idi). Font ve görsel üretimi sorun
değildi: tipografik görsel, başlığı ne ise onu yazar.

Onarım kuralı: yalnızca harf işaretleri değişir; her kelimenin katlanmış (işaretsiz) bicimi aynı kalmalıdır. Cümle içindeki büyük
harfli kelimeler (özel adlar) yalnızca kaynak metinde aynı bicimde geçiyorsa değiştirilir; kaynakta yoksa olduğu gibi bırakılır
(tahminle ad değiştirilmez)."""
import re

TR_HARF = set("çğıöşüÇĞİÖŞÜ")
_KAT = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")
KELIME = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû]+")


def kucuk(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def katla(s):
    """Karşılaştırma bicimi: küçük harf, işaretsiz. Büyük I hem I hem İ olabileceği için i'ye katlanır."""
    return (s or "").replace("İ", "i").replace("I", "i").lower().translate(_KAT).replace("ı", "i")


def tr_orani(metin):
    harfler = [c for c in (metin or "") if c.isalpha()]
    if not harfler:
        return 0.0
    return sum(1 for c in harfler if c in TR_HARF) / len(harfler)


def ascii_turkce(metin, en_az=200):
    """Türkçe bir metin Türkçe karaktersiz mi yazılmış? (Türkçe metinlerde bu harflerin payı ~%6-9'dur.)"""
    harf = sum(1 for c in (metin or "") if c.isalpha())
    return harf >= en_az and tr_orani(metin) < 0.006


def yz_metni(yz):
    yz = yz or {}
    return " ".join(str(yz.get(k) or "") for k in ("baslik", "spot", "metin"))


def kaynak_sozlugu(kaynak):
    """Kaynak metindeki kelimelerin katlanmış bicimi → küçük harfli özgün bicim(ler)."""
    sz = {}
    for k in KELIME.findall(kaynak or ""):
        sz.setdefault(katla(k), set()).add(kucuk(k))
    return sz


def _harf_duzeni(ornek, yeni):
    """yeni (küçük harfli) kelimeyi ornek'in büyük/küçük düzenine uydurur."""
    if ornek.isupper() and len(ornek) > 1:
        return yeni.replace("i", "İ").replace("ı", "I").upper()
    if ornek[:1].isupper():
        ilk = {"i": "İ", "ı": "I"}.get(yeni[:1], yeni[:1].upper())
        return ilk + yeni[1:]
    return yeni


def kaynakla_onar(metin, kaynak):
    """Yapay zekâsız onarım: kelimenin kaynak metinde tek bir özgün bicimi varsa o kullanılır (yalnızca harf işaretleri)."""
    sz = kaynak_sozlugu(kaynak)

    def degis(m):
        k = m.group(0)
        if any(c in TR_HARF for c in k):
            return k
        adaylar = sz.get(katla(k)) or set()
        if len(adaylar) != 1:
            return k
        return _harf_duzeni(k, next(iter(adaylar)))
    return KELIME.sub(degis, metin or "")


def dogrula(eski, yeni, kaynak):
    """Yapay zekânın onardığı metni denetler. Kelime sayısı ve her kelimenin katlanmış bicimi aynı olmalı; cümle içindeki büyük
    harfli kelimede (özel ad) değişiklik yalnızca kaynakta aynı bicim geçiyorsa kabul edilir. Dönüş: (metin, sorun) — sorun varsa
    metin None."""
    e, y = KELIME.findall(eski or ""), KELIME.findall(yeni or "")
    if len(e) != len(y):
        return None, f"kelime sayısı değişti ({len(e)} → {len(y)})"
    ayraclar = KELIME.split(eski or "")
    sz = kaynak_sozlugu(kaynak)
    sonuc, korunan = [], 0
    for i, (a, b) in enumerate(zip(e, y)):
        if katla(a) != katla(b):
            return None, f"kelime değişti: {a} → {b}"
        if a != b and a.isupper() and len(a) > 1:
            # tamamı büyük harfli (başlık) kelime: kaynakta tek bicimi varsa o, yoksa yalnızca işaret düzeltmesi
            bicim = sz.get(katla(b)) or set()
            if len(bicim) == 1:
                b = _harf_duzeni(b, next(iter(bicim)))
        elif a != b and a[:1].isupper():
            onceki = ayraclar[i].strip()
            cumle_basi = i == 0 or onceki.endswith((".", "!", "?", ":")) or "\n" in ayraclar[i]
            if not cumle_basi and kucuk(b) not in (sz.get(katla(b)) or set()):
                b = kaynakla_onar(a, kaynak)      # özel ad kaynakta bu bicimde yok: tahmin edilmez (kaynakta tek bicim varsa o)
                korunan += 1
        sonuc.append(b)
    out = []
    for i, k in enumerate(sonuc):
        out.append(ayraclar[i])
        out.append(k)
    out.append(ayraclar[-1])
    return "".join(out), (f"{korunan} özel ad kaynakta bulunamadığı için değiştirilmedi" if korunan else "")


ONAR_TALIMAT = """Aşağıdaki Türkçe haber metni Türkçe karakterler (ç, ğ, ı, İ, ö, ş, ü) kullanılmadan yazılmış. Metni YALNIZCA harf
işaretlerini düzelterek yeniden yaz: hiçbir kelimeyi ekleme, çıkarma, değiştirme ya da yerini değiştirme; noktalama ve paragraflar
aynı kalsın. Kişi, yer ve kurum adlarını KAYNAK METİN'de nasıl yazılmışsa öyle yaz; kaynakta olmayan bir adı olduğu gibi bırak.
Başlık tamamen büyük harfliyse yalnızca ilk harfleri büyük olacak bicimde yaz (kısaltmalar büyük kalır).

Yalnızca şu bicimde JSON döndür: {"baslik": "...", "spot": "...", "metin": "..."}

BAŞLIK: {baslik}
SPOT: {spot}
METİN:
{metin}

KAYNAK METİN (yalnızca ad yazımı için):
{kaynak}"""


def onar_istegi(baslik, spot, metin, kaynak):
    return (ONAR_TALIMAT.replace("{baslik}", baslik or "").replace("{spot}", spot or "").replace("{metin}", metin or "")
            .replace("{kaynak}", (kaynak or "")[:6000]))


def yaniti_dogrula(eski, yanit, kaynak):
    """eski/yanit: {'baslik','spot','metin'}. Üç alan da doğrulanırsa onarılmış alanlar; değilse (None, neden)."""
    out, notlar = {}, []
    for alan in ("baslik", "spot", "metin"):
        e = str((eski or {}).get(alan) or "")
        y = str((yanit or {}).get(alan) or "")
        if not e:
            out[alan] = e
            continue
        m, n = dogrula(e, y, kaynak)
        if m is None:
            return None, f"{alan}: {n}"
        out[alan] = m
        if n:
            notlar.append(f"{alan}: {n}")
    if ascii_turkce(yz_metni(out)):
        return None, "onarılan metin hâlâ Türkçe karaktersiz"
    return out, "; ".join(notlar)


def baslik_esle(baslik, referans):
    """Başlıktaki kelimenin yazımı, haberin kendi spot ve metninde (Türkçe karakterli yazılmış olmalı) tek bir biçimde geçiyorsa
    o biçime getirilir: 'Sarigul Sorusturmasinda' → 'Sarıgül Soruşturmasında', büyük harften yanlış çevrilen 'Pereıra' → 'Pereira'.
    Yalnızca harf işaretleri değişir; kısa ekler (kesme işaretinden sonraki 'nin/nın') ve metinde geçmeyen kelimeler olduğu gibi kalır."""
    if tr_orani(referans) < 0.03:
        return baslik
    sz = kaynak_sozlugu(referans)

    def degis(m):
        k = m.group(0)
        if len(k) < 3 or k.isupper() or m.start() > 0 and m.string[m.start() - 1] in "'’":
            return k                      # kısa kelime, kısaltma ya da kesme işaretinden sonraki ek (Antalya'nın)
        f = sz.get(katla(k)) or set()
        if len(f) != 1:
            return k
        yeni = next(iter(f))
        return k if kucuk(k) == yeni else _harf_duzeni(k, yeni)
    return KELIME.sub(degis, baslik or "")
