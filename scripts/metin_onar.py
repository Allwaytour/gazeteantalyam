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
    """Türkçe bir metin Türkçe karaktersiz (ya da büyük ölçüde karaktersiz) mi yazılmış? Türkçe metinlerde bu harflerin payı
    ~%6-9'dur; %2,5'in altı, karakterlerin çoğunun düşürüldüğünü gösterir (ör. yalnızca birkaç kelimesi işaretli metin)."""
    harf = sum(1 for c in (metin or "") if c.isalpha())
    return harf >= en_az and tr_orani(metin) < 0.025


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


def _ozel_ad_mi(kelimeler, ayraclar, i):
    """Cümle içinde büyük harfle başlayan kelime (özel ad olabilir)? Başlıkta her kelime büyük harfle başlayabildiği için başlıkta
    yalnızca cümle başı sayılmayan ve kaynakta/metinde küçük harfle geçmeyen kelimeler özel ad sayılır (çağıran karar verir)."""
    a = kelimeler[i]
    if not a[:1].isupper() or (a.isupper() and len(a) > 1):
        return False
    onceki = ayraclar[i].strip()
    return not (i == 0 or onceki.endswith((".", "!", "?", ":")) or "\n" in ayraclar[i])


def dogrula(eski, yeni, kaynak, sozluk_ek="", baslik=False):
    """Yapay zekânın onardığı metni denetler ve yalnızca güvenli değişiklikleri alır. Kelimeler katlanmış (işaretsiz) biçimleriyle
    hizalanır: işaretsiz biçimi aynı kalan kelimede yapay zekânın yazımı alınır, değişen/eklenen/çıkarılan kelimede özgün kelime
    korunur (yapay zekâ kelime değiştiremez). Cümle içindeki büyük harfli kelimede (özel ad) değişiklik yalnızca kaynak metinde ya da
    onarılmış haber metninde (sozluk_ek) aynı biçim geçiyorsa alınır. Dönüş: (metin, not)."""
    import difflib
    e, y = KELIME.findall(eski or ""), KELIME.findall(yeni or "")
    ayraclar = KELIME.split(eski or "")
    sz = kaynak_sozlugu((kaynak or "") + "\n" + (sozluk_ek or ""))
    sonuc = list(e)
    korunan = alinmayan = 0
    sm = difflib.SequenceMatcher(None, [katla(x) for x in e], [katla(x) for x in y], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            alinmayan += i2 - i1
            continue
        for i, j in zip(range(i1, i2), range(j1, j2)):
            a, b = e[i], y[j]
            if a == b:
                continue
            if a.isupper() and len(a) > 1:
                # tamamı büyük harfli (başlık) kelime: kaynakta tek biçimi varsa o, yoksa yalnızca işaret düzeltmesi
                bicim = sz.get(katla(b)) or set()
                if len(bicim) == 1:
                    b = _harf_duzeni(b, next(iter(bicim)))
            elif _ozel_ad_mi(e, ayraclar, i) and kucuk(b) not in (sz.get(katla(b)) or set()):
                if baslik and not sz.get(katla(b)):
                    pass                          # başlıktaki büyük harfli kelime metinde/kaynakta hiç yok: özel ad değil (ör. "Öldürdü")
                else:
                    b = kaynakla_onar(a, (kaynak or "") + "\n" + (sozluk_ek or ""))   # özel ad: kaynaktaki/metindeki yazımı
                    korunan += 1
            sonuc[i] = b
    out = []
    for i, k in enumerate(sonuc):
        out.append(ayraclar[i])
        out.append(k)
    out.append(ayraclar[-1])
    notlar = []
    if korunan:
        notlar.append(f"{korunan} özel ad kaynakta bulunamadığı için değiştirilmedi")
    if alinmayan:
        notlar.append(f"yapay zekânın değiştirdiği {alinmayan} kelime özgün haliyle bırakıldı")
    return "".join(out), "; ".join(notlar)


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
    """eski/yanit: {'baslik','spot','metin'}. Önce kaynak metinle yapay zekâsız onarım, sonra yapay zekânın yanıtından yalnızca
    harf işareti değişiklikleri alınır (metin → spot → başlık; başlıktaki kelimeler onarılmış metinle de doğrulanır).
    Sonuç yeterince Türkçe karakterli değilse (None, neden)."""
    out, notlar = {}, []
    ek = ""
    for alan in ("metin", "spot", "baslik"):
        e = str((eski or {}).get(alan) or "")
        if not e:
            out[alan] = e
            continue
        e2 = kaynakla_onar(e, kaynak)
        y = str((yanit or {}).get(alan) or "")
        m, n = dogrula(e2, y, kaynak, ek, baslik=alan == "baslik") if y else (e2, "yapay zekâ yanıtı yok")
        out[alan] = m
        ek += " " + m
        if n:
            notlar.append(f"{alan}: {n}")
    govde = f"{out.get('spot', '')} {out.get('metin', '')}"
    if sum(1 for c in govde if c.isalpha()) >= 200 and tr_orani(govde) < 0.035:
        return None, "onarılan metin hâlâ yeterince Türkçe karakterli değil (" + "; ".join(notlar)[:120] + ")"
    out["baslik"] = baslik_esle(out["baslik"], govde)
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
