#!/usr/bin/env python3
"""Gazete Antalya – görsel hattı h1 (üç ayrı aşama).

Eski hat (g1–g9, c1–c8) her yeni hatada bir istisna ekleyerek büyüdü: "ana öznenin kendi fotoğrafıysa konu ilişkisi aranmaz",
"önceden onaylanmış portre yeniden incelenmez", "maç orada oynanıyorsa rakibin stadı olur" gibi. Bu istisnalar yanlış konu
eşleşmelerinin (taksi haberine uçak, Kepezspor haberine rakibin karanlık stadı) doğrudan nedeniydi. h1'de istisna yoktur;
her aday aynı üç aşamadan geçer:

  A) ADAY BULMA      Önce doğrulanmış görsel kütüphanesi (data/gorsel_kutuphanesi.json), sonra ücretsiz kaynaklar
                     (kurumun aynı haberdeki izinli fotoğrafı, Wikidata/Commons kimlik kayıtları, Commons/Unsplash konu araması).
                     Aday bulmak kabul etmek değildir; burada yalnızca kaynak ve kimlik bilgisi toplanır.
  B) YAYIN UYGUNLUĞU (1) kaynak/lisans dayanağı, (2) teknik kalite ölçümü (çözünürlük, karanlık, bulanıklık, siyah-beyaz; Pillow),
                     (3) fotoğrafın kendisinin incelenmesi (tür, dönem, filigran, sahne, ana konu ve yüz kutuları; Gemini),
                     (4) BU haberle ilişkisi (ana özne, olay, gerekli/yasak unsurlar; Gemini). Kesin eleme nedenleri (yanlış kişi,
                     yanlış takım, yanlış spor, yanıltıcı dönem, ilgisiz yer/nesne, eksik zorunlu unsur, düşük kalite) başka hiçbir
                     puanla telafi edilmez.
  C) YERLEŞİM        Kabul edilen fotoğrafın manşet (kaplama ya da yan düzen), manşet yanı kartı ve haber kartı için ayrı ayrı uygun
                     olup olmadığı ve odak noktası; sayfadaki gerçek oranlar (masaüstü/tablet/mobil) ve başlık bandı hesaba katılır.
                     Yüz kırpılıyorsa, aşırı yakınsa ya da başlık yüzün üzerine geliyorsa o alan kullanılmaz.

Önbellek: (1)–(3) fotoğrafın kendisine aittir ve görsel kimliğiyle bir kez yapılır (kütüphanenin "inceleme" bölümü).
(4) ve (C) her haber için ayrıca yapılır; kütüphanede onaylı olmak yeni habere uygun olmak anlamına gelmez.
"""
import base64
import hashlib
import io
import json
import os
import re
import time
from urllib.parse import unquote

import requests

import gorsel_sec as gs

HAT_SURUM = "h1d"        # kural sürümü: değişince kütüphanenin eski kayıtları aday olarak kullanılmaz
ICERIK_SURUM = "i2"        # fotoğrafın kendisine ait inceleme (tür, dönem, kutular) – değişirse yeniden incelenir
TEKNIK_SURUM = "t1"        # teknik ölçüm yöntemi
YERLESIM_SURUM = "y2"
KUTUPHANE_DOSYA = os.path.join(gs.KOK, "data", "gorsel_kutuphanesi.json")

SINIF_ACIKLAMA = {
    "ayni_olay": "aynı olayın fotoğrafı (kullanım izni kayıtlı kaynaktan)",
    "ozne_kisi": "haberin ana kişisinin arşiv fotoğrafı (kimlik kaynak kaydından: Wikidata + Commons kategorisi)",
    "ozne_kurum": "haberin ana öznesi olan kurumun/takımın/tesisin fotoğrafı (kimlik kaynak kaydından)",
    "konu": "haberin konusunu gösteren temsilî fotoğraf (olayın kendisi değil)",
    "yer": "haberde geçen bir yerin/tesisin fotoğrafı (haberin ana öznesi değil)",
    "yer_genel": "haberde geçen ilçenin/şehrin genel görünümü",
}
SINIF_SIRA = {"ayni_olay": 6, "ozne_kisi": 5, "ozne_kurum": 5, "konu": 3, "yer": 2, "yer_genel": 1}

# ----------------------------------------------------------------- kütüphane (doğrulanmış görsel arşivi)
_KUT = None


def kutuphane():
    global _KUT
    if _KUT is None:
        try:
            with open(KUTUPHANE_DOSYA, encoding="utf-8") as f:
                _KUT = json.load(f) or {}
        except Exception:
            _KUT = {}
        _KUT.setdefault("aciklama", "Doğrulanmış görsel kütüphanesi (görsel hattı h1). 'kayitlar': kaynağı, lisansı, kimliği ve içeriği "
                                    "doğrulanmış, en az bir haberde tüm kontrollerden geçmiş fotoğraflar. 'inceleme': incelenen her "
                                    "fotoğrafın (elenenler dahil) teknik ölçümü ve içerik incelemesi; aynı fotoğraf her turda yeniden incelenmez.")
        _KUT.setdefault("kayitlar", {})
        _KUT.setdefault("inceleme", {})
    return _KUT


def kaydet():
    if _KUT is None:
        return
    # Bugünkü kurallarla kabul edilmemiş kütüphane kayıtları (önceki kural sürümü) aday olarak kullanılmaz ve silinir
    _KUT["kayitlar"] = {k: v for k, v in _KUT["kayitlar"].items() if str(v.get("dogrulama_surumu", "")).split("-")[0] == HAT_SURUM}
    inc = _KUT["inceleme"]
    if len(inc) > 3000:
        for k in sorted(inc, key=lambda k: inc[k].get("t", ""))[: len(inc) - 3000]:
            inc.pop(k, None)
    with open(KUTUPHANE_DOSYA, "w", encoding="utf-8") as f:
        json.dump(_KUT, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def commons_dosya_adi(url):
    m = re.search(r"/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/?#]+)", url or "")
    return unquote(m.group(1)) if m else ""


def gorsel_kimligi(url):
    """Aynı fotoğrafın farklı boyut/sorgu adresleri tek kimliğe iner (Commons: dosya adı; diğerleri: sorgusuz adres)."""
    ad = commons_dosya_adi(url)
    anahtar = ("commons:" + ad) if ad else ("url:" + re.sub(r"[?#].*$", "", url or ""))
    return hashlib.sha1(anahtar.encode()).hexdigest()[:16]


# ----------------------------------------------------------------- konu yardımcıları
def _k(s):
    return gs.kucult(s or "")


def ana_ozne(konu):
    return konu.get("ana_ozne") or {}


def takimlar(konu, rol):
    return [t for t in konu.get("takimlar") or [] if isinstance(t, dict) and t.get("rol") == rol and t.get("ad")]


def ad_ayni(a, b):
    return bool(a and b and (gs.ad_eslesir(a, b) or gs.ad_eslesir(b, a)))


def rakip_mi(ad, konu):
    return any(ad_ayni(ad, t["ad"]) for t in takimlar(konu, "rakip"))


def ana_ozne_adi_mi(ad, konu):
    oz = ana_ozne(konu)
    adlar = [oz.get("ad", ""), oz.get("ad_en", "")] + [t["ad"] for t in takimlar(konu, "ana")]
    return any(ad_ayni(ad, x) for x in adlar if x)


def sinif_belirle(a, konu):
    alt, tur = a.get("alt_tur", ""), a.get("tur", "")
    if a.get("sinif"):
        return a["sinif"]
    if tur == "gercek" or alt == "kurum_haber":
        return "ayni_olay"
    if alt.startswith("kisi"):
        return "ozne_kisi"
    if alt == "kurum":
        hedef = a.get("gosterim_ad") or a.get("hedef", "")
        hedef_ad = re.sub(r"\s*\(.*$", "", hedef)
        sahibi = re.search(r"\(([^)]+?) kulübünün", a.get("hedef", ""))
        if ana_ozne_adi_mi(hedef_ad, konu) or (sahibi and ana_ozne_adi_mi(sahibi.group(1), konu)):
            return "ozne_kurum"
        return "yer"
    if alt == "yer":
        return "yer_genel"
    return "konu"


# ----------------------------------------------------------------- A) aday bulma
def kutuphane_adaylari(konu):
    """Kütüphanede kimliği ya da somut konusu bu haberle örtüşen kayıtlar. Örtüşme yalnızca aday yapar; kabul B aşamasındadır."""
    kut = kutuphane()["kayitlar"]
    kisiler = [k.get("ad", "") for k in konu.get("ana_kisiler") or []]
    oz = ana_ozne(konu)
    if oz.get("tur") == "kisi":
        kisiler.append(oz.get("ad", ""))
    kurumlar = [oz.get("ad", "")] if oz.get("tur") in ("kurum", "takim", "yer", "olay") else []
    kurumlar += [t["ad"] for t in takimlar(konu, "ana")]
    gerekli = gs.gerekli_unsurlar(konu)
    adaylar = []
    for kid, r in kut.items():
        if r.get("dogrulama_surumu", "").split("-")[0] != HAT_SURUM:
            continue
        kim = r.get("kimlikler") or {}
        neden, sinif = "", ""
        for ad in kisiler:
            if ad and any(ad_ayni(ad, x.get("ad", "")) for x in kim.get("kisiler") or []):
                neden, sinif = f"kütüphane: kişi kimliği '{ad}'", "ozne_kisi"
        if not neden:
            for ad in kurumlar:
                for alan in ("kurumlar", "takimlar", "tesisler", "yerler"):
                    if ad and any(ad_ayni(ad, x.get("ad", "")) for x in kim.get(alan) or []):
                        neden, sinif = f"kütüphane: {alan[:-3]} kimliği '{ad}'", "ozne_kurum"
        if not neden and gerekli:
            konular = " | ".join(r.get("konular") or [])
            if all(any(gs.kok(w) in {gs.kok(x) for x in gs.kelimeler(konular)} for w in gs.kelimeler(u) if len(w) > 3) for u in gerekli):
                neden, sinif = "kütüphane: somut konu (" + ", ".join(gerekli[:2]) + ")", "konu"
        if not neden:
            continue
        k = r.get("kaynak") or {}
        adaylar.append({
            "tur": "arsiv" if sinif != "konu" else "stok", "alt_tur": r.get("alt_tur") or ("nesne" if sinif == "konu" else "kurum"),
            "sinif": sinif, "kaynak": k.get("kaynak", ""), "url": r["url"], "kucuk": r.get("kucuk") or r["url"],
            "sayfa": k.get("sayfa", ""), "fotografci": k.get("sahibi", ""), "lisans": k.get("lisans", ""),
            "lisans_dayanagi": k.get("dayanak", ""), "kosul": k.get("atif_kosulu", ""), "hedef": r.get("gosterdigi", ""),
            "gosterim_ad": r.get("gosterim_ad", ""), "qid": r.get("qid", ""), "kisi": (kim.get("kisiler") or [{}])[0].get("ad", ""),
            "cekim": r.get("cekim_tarihi", ""), "kutuphane_id": kid, "genislik": (r.get("boyut") or [0, 0])[0],
            "yukseklik": (r.get("boyut") or [0, 0])[1], "dogrulama": neden + f" (doğrulama {r.get('dogrulama_surumu', '')})",
        })
    return adaylar


def aday_bul(bag, haber, konu):
    """A aşaması. Dönüş: (adaylar, notlar). Her adayın 'sinif' alanı ilişki kurallarını belirler."""
    notlar = []
    kut = kutuphane_adaylari(konu)
    if kut:
        notlar.append(f"kütüphanede {len(kut)} aday")
    gercek, n1 = gs.gercek_adaylar(bag, konu, haber)
    kurum_h, n6 = gs.kurum_haber_adaylari(bag, konu, haber)
    kisi, n3 = gs.kisi_adaylari(bag, konu, haber)
    # Kurum/tesis/takım fotoğrafı yalnızca başlıkta/spotta geçen ya da ana özne olan hedefler için; rakip takım hiçbir zaman.
    bs = f"{haber.get('baslik', '')} {haber.get('spot', '')}"
    secili = []
    for h in konu.get("hedefler") or []:
        if rakip_mi(h["ad"], konu):
            notlar.append(f"'{h['ad']}' haberdeki rakip takım; fotoğrafı aranmadı")
            continue
        if gs.ad_eslesir(h["ad"], bs) or gs.ad_eslesir(h.get("ad_en") or h["ad"], bs) or ana_ozne_adi_mi(h["ad"], konu):
            secili.append(h)
        else:
            notlar.append(f"'{h['ad']}' başlık/spotta geçmiyor ve ana özne değil; fotoğrafı aranmadı")
    konu_h = dict(konu, hedefler=secili)
    kurum_wd, n4 = gs.kurum_wikidata_adaylari(bag, konu_h)
    arsiv, n2 = gs.arsiv_adaylari(bag, konu_h)
    yk, n8 = gs.yer_konu_adaylari(bag, konu)
    nesne, n7 = gs.nesne_adaylari(bag, konu, haber)
    yer, n5 = gs.yer_adaylari(bag, konu, haber)
    notlar += n6 + n1 + n3 + n4 + n2 + n8 + n7 + n5
    hepsi, gorulen = [], set()
    for liste in (kut, kurum_h, gercek, kisi, [dict(a, alt_tur=a.get("alt_tur") or "kurum") for a in kurum_wd + arsiv], yk, nesne, yer):
        for a in liste:
            gk = gorsel_kimligi(a["url"])
            if gk in gorulen:
                continue
            hedef_metin = f"{a.get('gosterim_ad', '')} {a.get('hedef', '')}"
            if any(gs.ad_eslesir(t["ad"], hedef_metin) for t in takimlar(konu, "rakip")):
                notlar.append(f"rakip takıma ait aday elendi: {hedef_metin.strip()[:80]}")
                continue
            gorulen.add(gk)
            a = dict(a)
            a["sinif"] = sinif_belirle(a, konu)
            a["gk"] = gk
            hepsi.append(a)
    hepsi.sort(key=lambda a: (-SINIF_SIRA.get(a["sinif"], 0), 0 if a.get("kutuphane_id") else 1, -int(a.get("meta_puan") or 0)))
    return hepsi, notlar


# ----------------------------------------------------------------- B1) kaynak / lisans
def lisans_kontrol(bag, a):
    """Kullanım dayanağı yoksa aday elenir. İnternette ya da resmî sitede bulunmak izin sayılmaz."""
    lis = (a.get("lisans") or "").strip()
    if a.get("sinif") == "ayni_olay" and a.get("alt_tur") == "kurum_haber":
        ik = a.get("izin_kontrolu") or {}
        return (True, "") if (a.get("lisans_dayanagi") or ik.get("yeniden_kullanim")) else (False, "kurum fotoğrafı için izin dayanağı yok")
    if a.get("kaynak") == "Gazete Antalya arşivi":
        return (True, "") if a.get("lisans_dayanagi") else (False, "kendi arşiv kaydında kullanım dayanağı yok")
    if a.get("kaynak") == "Unsplash" or "unsplash.com" in (a.get("url") or ""):
        return (True, "") if lis.startswith("Unsplash") else (False, "Unsplash lisans kaydı yok")
    if "wikimedia.org" in (a.get("url") or ""):
        if not lis:
            return False, "Commons lisansı okunamadı"
        if gs.KISITLI_LISANS.search(lis) or not gs.SERBEST_LISANS.match(lis):
            return False, f"lisans site kullanımına uygun değil ({lis})"
        if not a.get("fotografci"):
            return False, "fotoğraf sahibi (atıf) bilgisi yok"
        return True, ""
    if lis == "Kurum izniyle" and a.get("lisans_dayanagi"):
        return True, ""
    kayit = gs.kaynak_izni(bag.izinler, a.get("sayfa") or a.get("url", ""))
    if kayit and kayit.get("izin") is True and str(kayit.get("dayanak") or "").strip():
        return True, ""
    return False, "kullanım izni/lisans dayanağı kayıtlı değil"


# ----------------------------------------------------------------- B2) teknik kalite
def indir(url, en_fazla=4_000_000):
    if url.startswith("/"):
        yol = os.path.join(gs.KOK, "static", url.lstrip("/"))
        with open(yol, "rb") as f:
            veri = f.read()
        tip = "image/png" if yol.lower().endswith(".png") else "image/webp" if yol.lower().endswith(".webp") else "image/jpeg"
        return tip, veri
    r = requests.get(url, headers={"User-Agent": gs.UA_BOT}, timeout=25)
    r.raise_for_status()
    tip = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not tip.startswith("image/") or len(r.content) > en_fazla:
        raise gs.ServisHatasi(f"görsel uygun değil ({tip}, {len(r.content)} bayt)")
    return tip, r.content


def teknik_olc(veri):
    """Fotoğrafın kendisinden teknik ölçüm (genişliği 400 piksele indirilmiş kopya üzerinde, Pillow)."""
    from PIL import Image, ImageFilter, ImageOps, ImageStat
    im = Image.open(io.BytesIO(veri))
    im = ImageOps.exif_transpose(im).convert("RGB")
    w0, h0 = im.size
    k = im.resize((400, max(1, round(400 * h0 / w0))))
    g = k.convert("L")
    hist = g.histogram()
    n = sum(hist) or 1
    ort = sum(i * c for i, c in enumerate(hist)) / n
    birikim, medyan = 0, 0
    for i, c in enumerate(hist):
        birikim += c
        if birikim >= n / 2:
            medyan = i
            break
    lap = g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return {"inen": [w0, h0], "parlaklik": round(ort), "medyan": medyan, "karanlik_oran": round(sum(hist[:50]) / n, 3),
            "kontrast": round(ImageStat.Stat(g).stddev[0]), "doygunluk": round(ImageStat.Stat(k.convert("HSV")).mean[1] / 255 * 100, 1),
            "keskinlik": round(ImageStat.Stat(lap).var[0]), "s": TEKNIK_SURUM}


def teknik_bayraklar(t):
    """Eşikler hatalı örneklerin gerçek ölçümleriyle belirlendi (Kepezspor haberindeki stat: medyan 49, %52'si karanlık;
    genç milliler karesi: doygunluk 0)."""
    return {
        "siyah_beyaz": t["doygunluk"] < 5,
        "cok_karanlik": t["karanlik_oran"] >= 0.45 or t["medyan"] < 55,
        "karanlik": t["karanlik_oran"] >= 0.33 or t["medyan"] < 75,
        "cok_bulanik": t["keskinlik"] < 60,
        "bulanik": t["keskinlik"] < 140,
    }


def asil_boyut(bag, a, inen):
    """Fotoğrafın özgün boyutu: kaynak kaydından; yoksa Commons'tan sorgulanır; yerel dosyada dosyanın kendisi."""
    k = a.get("kayit") or {}
    w, h = int(k.get("genislik") or a.get("genislik") or 0), int(k.get("yukseklik") or a.get("yukseklik") or 0)
    if w and h:
        return w, h
    ad = commons_dosya_adi(a["url"])
    if ad:
        try:
            for d in gs._dosya_bilgisi(bag, ["File:" + ad.replace("_", " ")]):
                if d.get("genislik"):
                    a.setdefault("kayit", d)
                    return int(d["genislik"]), int(d["yukseklik"])
        except Exception:
            pass
    if a["url"].startswith("/") or not re.search(r"/\d+px-|[?&]w=\d+", a.get("kucuk") or a["url"]):
        return inen
    return inen          # yalnızca küçük kopya bilindiğinde ihtiyatlı: indirilen boyut


# ----------------------------------------------------------------- B3 + B4) Gemini: fotoğrafın kendisi ve haberle ilişkisi
INCELEME_TALIMAT = """Bir haber sitesinin fotoğraf editörüsün. Aşağıda bir haber ve numaralı fotoğraflar var. Her fotoğrafın yanında
kaynak kaydının iddiası yazıyor. Fotoğrafın KENDİSİNE bak. Kişileri yüzlerinden tanımaya çalışma; kişinin kimliği kaynak kaydıyla
belirlenir, sen yalnızca sahneyi değerlendirirsin.

Her fotoğraf için iki bölüm doldur:

"icerik" — yalnızca "İÇERİK İNCELENECEK" yazan fotoğraflarda doldur, diğerlerinde null yaz. Haberden bağımsız, fotoğrafın kendisi:
- tur: "fotograf" | "cizim" | "oyun_goruntusu" | "yapay_gorunumlu" | "harita_logo_belge" | "ekran_goruntusu" | "kolaj"
- siyah_beyaz: true/false
- donem: fotoğrafın ÇEKİLDİĞİ dönem (konusunun tarihî olması değil: antik kent, heykel ya da müze eserinin bugün çekilmiş fotoğrafı
  "guncel"dir): "guncel" (son ~20 yılın çekimi) | "eski" (eski araçlar, giysiler, film dokusu, solmuş renkler, arşiv karesi)
- karanlik: ana konu karanlıkta seçilemiyor mu? bulanik: belirgin bulanık mı?
- yazi_filigran: "yok" | "kucuk" (köşede küçük imza/logo) | "buyuk" (görüntüyü kaplayan filigran, gömülü başlık, afiş/poster yazısı)
- sahne: en fazla 15 kelime, ne görünüyor (Türkçe)
- nesneler: görülen somut unsurlar, en fazla 8 (Türkçe, ör. "plaj voleybolu filesi", "taksi", "yolcu uçağı", "stadyum tribünü")
- kisi_sayisi: tahmini sayı; yuz_belirgin: yakın ve net bir yüz var mı
- ana_kutu: fotoğrafın ana konusunu (ör. uçak, bina, taksi sırası, kişi) çevreleyen DAR kutu [ymin, xmin, ymax, xmax], 0-1000 arası;
  tüm kareyi değil, yalnızca ana konunun kapladığı bölgeyi ver
- yuzler: belirgin yüzlerin kutuları (en fazla 4), saç tepesinden çeneye: [[ymin, xmin, ymax, xmax], ...]; yoksa []
- ulke: "turkiye" | "yabanci" | "belirsiz" (tabela, plaka, yazı dili, simge yapılar)
- notr_portre: tek bir kişinin olaysız, nötr portresi mi
- uygun_baglamlar / uygunsuz_baglamlar: bu fotoğrafın kullanılabileceği ve kullanılamayacağı haber türleri (en fazla 3'er, kısa)

"iliski" — bu fotoğraf BU haber için. İddiası "haberin ana kişisinin arşiv fotoğrafı" olan karelerde haberdeki olayın (toplantı,
ziyaret, buluşma) görülmesi BEKLENMEZ: kişinin nötr portresi uygundur; bu karelerde olay_gorunuyor false olabilir ve ilgisiz_yer_nesne
yalnızca karede kişiden başka, haberle çelişen bir yer/nesne öne çıkıyorsa true olur. Aynı şekilde "kurumun/takımın/tesisin fotoğrafı"
iddiasında binanın ya da tesisin kendisi yeterlidir.
- ana_ozne_gorunuyor: haberin ana öznesi fotoğrafta görülüyor mu? (kişi fotoğrafında: kaynak kaydındaki kişi tek başına ya da açıkça öne çıkıyorsa true)
- olay_gorunuyor: haberdeki olay/eylem fotoğrafta görülüyor mu?
- gerekli: her "GÖRSELDE OLMALI" unsuru için {{"unsur": "...", "var": true/false}} (liste boşsa [])
- yasak: fotoğrafta görülen "GÖRSELDE OLMAMALI" unsurları (yoksa [])
- yanlis_kisi: haberle ilgisi olmayan biri haberdeki kişi sanılacak biçimde öne çıkıyor mu?
- yanlis_takim: başka bir takımın forması, stadı ya da logosu haberdeki takımın yerine öne çıkıyor mu? (RAKİP takımın stadı/oyuncuları dahil)
- yanlis_spor: fotoğraftaki spor dalı haberdekinden farklı mı?
- yaniltici_donem: fotoğrafın dönemi haberle çelişiyor mu? (güncel bir haber için tarihî ya da siyah-beyaz kare → true)
- ilgisiz_yer_nesne: fotoğrafın asıl konusu haberin konusuyla ilgisiz bir yer/nesne mi? (ör. taksi haberinde uçak; plaj voleybolu
  haberinde yalnızca sahil manzarası; maç haberinde heykel)
- baska_olay_sanilir: başka bir olaya ait sahne bu haberin görüntüsü sanılır mı? ("aynı olayın fotoğrafı" iddiasında false)
- konu_ana_unsur: haberin konusu (olay, özne ya da nesne) fotoğrafın ANA konusu mu? Geniş bir manzara, şehir ya da kalabalık içinde
  zor seçilen küçük bir ayrıntıysa false (ör. gün batımı sahil manzarasında uzaktan görülen voleybol sahası → false)
- konu_kutusu: fotoğrafı BU haberle ilişkili kılan unsurun dar kutusu [ymin, xmin, ymax, xmax] (ör. taksi haberinde taksiler,
  maç haberinde sahadaki oyuncular, kişi haberinde kişinin yüzü ve gövdesi); böyle bir unsur yoksa null
- uygunluk: 0-10, bu fotoğraf bu haberin görseli olarak ne kadar doğru
- gerekce: en fazla 20 kelime (Türkçe)

HABER BAŞLIĞI: {baslik}
SPOT: {spot}
ANA ÖZNE: {ozne} | ANA OLAY/EYLEM: {eylem} | YER: {yer} | ZAMAN: {zaman}
TAKIMLAR: {takimlar} | SPOR DALI: {spor} | HASSASİYET: {hassas}
GÖRSELDE OLMALI: {olmali}
GÖRSELDE OLMAMALI: {olmamali}

{fotolar}

Yalnızca şu biçimde JSON döndür:
{{"fotograflar": [{{"no": 1, "icerik": {{"tur": "fotograf", "siyah_beyaz": false, "donem": "guncel", "karanlik": false, "bulanik": false,
 "yazi_filigran": "yok", "sahne": "...", "nesneler": ["..."], "kisi_sayisi": 0, "yuz_belirgin": false, "ana_kutu": [0, 0, 1000, 1000],
 "yuzler": [], "ulke": "belirsiz", "notr_portre": false, "uygun_baglamlar": ["..."], "uygunsuz_baglamlar": ["..."]}},
 "iliski": {{"ana_ozne_gorunuyor": false, "olay_gorunuyor": false, "gerekli": [], "yasak": [], "yanlis_kisi": false, "yanlis_takim": false,
 "yanlis_spor": false, "yaniltici_donem": false, "ilgisiz_yer_nesne": false, "baska_olay_sanilir": false, "konu_ana_unsur": true, "konu_kutusu": null,
 "uygunluk": 0, "gerekce": "..."}}}}]}}"""


def _kutu(x):
    try:
        k = [max(0, min(1000, int(v))) for v in x][:4]
        if len(k) == 4 and k[2] > k[0] and k[3] > k[1]:
            return k
    except Exception:
        pass
    return None


def icerik_dogrula(d):
    if not isinstance(d, dict):
        return None
    return {
        "tur": str(d.get("tur") or "fotograf"), "siyah_beyaz": d.get("siyah_beyaz") is True,
        "donem": "eski" if str(d.get("donem")) == "eski" else "guncel", "karanlik": d.get("karanlik") is True,
        "bulanik": d.get("bulanik") is True, "yazi_filigran": str(d.get("yazi_filigran") or "yok"),
        "sahne": gs.temiz(d.get("sahne"), 160), "nesneler": [gs.temiz(x, 40) for x in (d.get("nesneler") or []) if gs.temiz(x)][:8],
        "kisi_sayisi": int(d.get("kisi_sayisi") or 0) if str(d.get("kisi_sayisi") or "0").isdigit() else 0,
        "yuz_belirgin": d.get("yuz_belirgin") is True, "ana_kutu": _kutu(d.get("ana_kutu") or []),
        "yuzler": [k for k in (_kutu(y) for y in (d.get("yuzler") or [])[:4]) if k],
        "ulke": str(d.get("ulke") or "belirsiz"), "notr_portre": d.get("notr_portre") is True,
        "uygun_baglamlar": [gs.temiz(x, 60) for x in (d.get("uygun_baglamlar") or [])][:3],
        "uygunsuz_baglamlar": [gs.temiz(x, 60) for x in (d.get("uygunsuz_baglamlar") or [])][:3],
        "s": ICERIK_SURUM,
    }


def iliski_dogrula(d):
    d = d if isinstance(d, dict) else {}
    try:
        uyg = max(0, min(10, int(d.get("uygunluk") or 0)))
    except Exception:
        uyg = 0
    return {
        "ana_ozne_gorunuyor": d.get("ana_ozne_gorunuyor") is True, "olay_gorunuyor": d.get("olay_gorunuyor") is True,
        "gerekli": [{"unsur": gs.temiz(x.get("unsur"), 60), "var": x.get("var") is True} for x in (d.get("gerekli") or []) if isinstance(x, dict)],
        "yasak": [gs.temiz(x.get("unsur") if isinstance(x, dict) else x, 60) for x in (d.get("yasak") or [])
                  if (not isinstance(x, dict) or x.get("var") is not False) and gs.temiz(x.get("unsur") if isinstance(x, dict) else x)],
        "konu_ana_unsur": d.get("konu_ana_unsur") is not False,
        **{k: d.get(k) is True for k in ("yanlis_kisi", "yanlis_takim", "yanlis_spor", "yaniltici_donem", "ilgisiz_yer_nesne", "baska_olay_sanilir")},
        "konu_kutusu": _kutu(d.get("konu_kutusu") or []), "uygunluk": uyg, "gerekce": gs.temiz(d.get("gerekce"), 200),
    }


def iddia_metni(a):
    k = a.get("kayit") or {}
    ek = []
    if a.get("hedef"):
        ek.append(f"gösterdiği iddia edilen: {re.sub(chr(10), ' ', str(a['hedef']))[:90]}")
    if a.get("kisi"):
        ek.append(f"kişi: {a['kisi']}" + (f" (Wikidata {a['qid']})" if a.get("qid") else ""))
    if a.get("cekim"):
        ek.append(f"çekim: {a['cekim']}")
    if k.get("baslik") or k.get("aciklama"):
        ek.append(f"kaynak açıklaması: {gs.temiz(str(k.get('baslik', '')).replace('File:', ''), 90)} — {gs.temiz(k.get('aciklama'), 140)}")
    return SINIF_ACIKLAMA.get(a["sinif"], a["sinif"]) + ("; " + "; ".join(ek) if ek else "")


def incele(bag, haber, konu, adaylar):
    """Adayları tek istekte inceler. İçeriği önbellekte olan fotoğraf için yalnızca haber ilişkisi sorulur."""
    parcalar, fotolar = [], []
    for n, a in enumerate(adaylar, 1):
        fotolar.append(f"FOTOĞRAF {n} — {'İÇERİK ÖNBELLEKTE (icerik: null)' if a.get('icerik') else 'İÇERİK İNCELENECEK'} — İDDİA: {iddia_metni(a)}")
        parcalar += [{"text": f"FOTOĞRAF {n}:"}, {"inlineData": {"mimeType": a["_mime"], "data": a["_b64"]}}]
    oz = ana_ozne(konu)
    metin = INCELEME_TALIMAT.format(
        baslik=haber.get("baslik", ""), spot=haber.get("spot", ""),
        ozne=f"{oz.get('tur', '')} «{oz.get('ad', '')}»" if oz.get("ad") else "-",
        eylem=konu.get("eylem") or konu.get("ana_olay", "") or "-", yer=(konu.get("nerede") or {}).get("ad") or konu.get("yer") or "-",
        zaman=konu.get("zaman") or "guncel",
        takimlar=", ".join(f"{t['ad']} ({t['rol']})" for t in konu.get("takimlar") or []) or "-",
        spor=konu.get("spor_dali") or "-", hassas=konu.get("hassas", "yok"),
        olmali=", ".join(gs.gerekli_unsurlar(konu)) or "-", olmamali=", ".join(konu.get("gorselde_olmamali") or []) or "-",
        fotolar="\n".join(fotolar))
    try:
        yanit = bag.gemini([{"text": metin}] + parcalar, en_fazla=1200 * len(adaylar) + 800)
    except gs.ServisHatasi as e:
        # Yanıt yarım/bozuk JSON geldiyse parti ikiye bölünüp yeniden sorulur (tek fotoğrafta bir kez daha denenir)
        if "okunamadı" not in str(e) or getattr(bag, "_bolundu", 0) >= 3:
            raise
        bag._bolundu = getattr(bag, "_bolundu", 0) + 1
        try:
            if len(adaylar) == 1:
                return incele(bag, haber, konu, adaylar)
            yari = len(adaylar) // 2
            incele(bag, haber, konu, adaylar[:yari])
            incele(bag, haber, konu, adaylar[yari:])
            return adaylar
        finally:
            bag._bolundu -= 1
    liste = {}
    for d in yanit.get("fotograflar") or []:
        if isinstance(d, dict):
            try:
                liste[int(d.get("no", 0))] = d
            except Exception:
                pass
    tarih = gs.simdi_tr().isoformat(timespec="seconds")
    for n, a in enumerate(adaylar, 1):
        d = liste.get(n)
        if not d:
            a["_servis"] = "inceleme yanıtında bu fotoğraf yok"
            continue
        if not a.get("icerik"):
            a["icerik"] = icerik_dogrula(d.get("icerik"))
            if a["icerik"]:
                kayit = kutuphane()["inceleme"].setdefault(a["gk"], {})
                kayit.update(icerik=a["icerik"], t=tarih, url=a["url"])
        a["iliski"] = iliski_dogrula(d.get("iliski"))
        a["kontrol_tarih"] = tarih
    return adaylar


# ----------------------------------------------------------------- B) karar: kesin eleme nedenleri
def karar(a, konu):
    """Kesin eleme nedenleri (kategori, açıklama). Hiçbiri puanla telafi edilmez. Boş liste → kabul."""
    ned = []
    ic, il, tb = a.get("icerik") or {}, a.get("iliski") or {}, a.get("teknik_bayrak") or {}
    tarihi_haber = konu.get("zaman") == "tarihi"
    sinif = a["sinif"]
    # kalite (fotoğrafın kendisi)
    if ic.get("tur", "fotograf") != "fotograf":
        ned.append(("kalite", f"gerçek fotoğraf değil ({ic.get('tur')})"))
    if (tb.get("siyah_beyaz") or ic.get("siyah_beyaz") or ic.get("donem") == "eski") and not tarihi_haber:
        ned.append(("kalite", "tarihî/siyah-beyaz görüntü güncel haberle çelişiyor"))
    if ic.get("yazi_filigran") == "buyuk":
        ned.append(("kalite", "büyük filigran ya da görüntüye gömülü yazı"))
    if tb.get("cok_karanlik") or (ic.get("karanlik") and tb.get("karanlik")):
        ned.append(("kalite", "aşırı karanlık"))
    if tb.get("cok_bulanik") or (ic.get("bulanik") and tb.get("bulanik")):
        ned.append(("kalite", "bulanık"))
    # konu (bu haberle ilişkisi)
    for alan, metin in (("yanlis_kisi", "yanlış kişi öne çıkıyor"), ("yanlis_takim", "yanlış takım (rakibin stadı/forması dahil)"),
                        ("yanlis_spor", "yanlış spor dalı"), ("yaniltici_donem", "yanıltıcı dönem"),
                        ("ilgisiz_yer_nesne", "haberin konusuyla ilgisiz yer/nesne")):
        if il.get(alan):
            ned.append(("konu", metin))
    if il.get("yasak"):
        ned.append(("konu", "görselde olmaması gereken unsur: " + ", ".join(il["yasak"][:3])))
    eksik = [g["unsur"] for g in il.get("gerekli") or [] if not g.get("var")]
    if sinif == "ozne_kisi":
        if not il.get("ana_ozne_gorunuyor"):
            ned.append(("konu", "haberin kişisi fotoğrafta tek başına/öne çıkmış görünmüyor"))
        if ic.get("kisi_sayisi", 0) > 3 and not ic.get("notr_portre"):
            ned.append(("konu", "kalabalık kare: başka kişiler haberin kişisi sanılabilir"))
        if il.get("baska_olay_sanilir"):
            ned.append(("konu", "başka bir etkinliğin karesi güncel olayın görüntüsü sanılır"))
        if konu.get("hassas", "yok") != "yok" and not ic.get("notr_portre"):
            ned.append(("konu", "hassas haberde yalnızca nötr portre kullanılır"))
    elif sinif == "ozne_kurum":
        if not il.get("ana_ozne_gorunuyor"):
            ned.append(("konu", "haberin ana öznesi (kurum/takım/tesis) fotoğrafta görünmüyor"))
        if il.get("baska_olay_sanilir"):
            ned.append(("konu", "başka bir olayın karesi güncel olayın görüntüsü sanılır"))
    else:
        # aynı olay, konu, yer, yer genel: zorunlu unsurlar görünmeli
        if eksik:
            ned.append(("konu", "zorunlu unsur görünmüyor: " + ", ".join(eksik[:3])))
        if not gs.gerekli_unsurlar(konu) and not (il.get("olay_gorunuyor") or il.get("ana_ozne_gorunuyor")):
            ned.append(("konu", "ne haberin öznesi ne de olayı görünüyor"))
        kk = il.get("konu_kutusu")
        alan_payi = ((kk[2] - kk[0]) * (kk[3] - kk[1]) / 1e6) if kk else None
        # Konunun karedeki payı ölçüyle denetlenir (modelin "ana unsur" yorumu tek başına yeterli sayılmaz; aynı kare için
        # farklı haberlerde farklı yanıt verebiliyor). Olay/spor haberinde olayı gösterdiği söylenen kare (aynı olay ya da
        # temsilî) olayı karenin en az %15'inde göstermeli: gün batımı manzarasında uzaktan görülen saha (≈%6-10) elenir.
        # Yer fotoğraflarında (yer/yer_genel) yerin kendisi konudur; yalnızca %3 altı ayrıntılar elenir.
        olay_haberi = (konu.get("ana_ozne") or {}).get("tur") == "olay" or bool(konu.get("spor_dali"))
        esik_alan = 0.15 if (olay_haberi and sinif in ("ayni_olay", "konu")) else 0.03
        if alan_payi is None and esik_alan > 0.03 and _kutu(ic.get("ana_kutu") or []):
            ak = ic["ana_kutu"]
            alan_payi = (ak[2] - ak[0]) * (ak[3] - ak[1]) / 1e6
        if il and ((alan_payi is not None and alan_payi < esik_alan)
                   or (il.get("konu_ana_unsur") is False and (alan_payi is None or alan_payi < 0.10))):
            ned.append(("konu", "haberin konusu fotoğrafta yalnızca küçük bir ayrıntı (ör. manzara içinde uzaktan görülen saha)"))
        if sinif != "ayni_olay":
            if il.get("baska_olay_sanilir"):
                ned.append(("konu", "başka bir olayın karesi bu haberin görüntüsü sanılır"))
            if ic.get("ulke") == "yabanci":
                ned.append(("konu", "Türkiye dışına ait unsurlar (yanıltıcı yer)"))
            if konu.get("hassas", "yok") != "yok" and ic.get("yuz_belirgin"):
                ned.append(("konu", "hassas haberde temsilî karede tanınabilir kişi"))
            if sinif == "konu" and ic.get("yuz_belirgin") and ic.get("kisi_sayisi", 0) <= 3:
                ned.append(("konu", "temsilî fotoğrafta tanınabilir kişi (haberdeki kişi sanılabilir)"))
    esik = {"yer_genel": 7, "yer": 7, "konu": 6}.get(sinif, 5)
    if il and il.get("uygunluk", 0) < esik:
        ned.append(("konu", f"habere uygunluk düşük ({il.get('uygunluk', 0)}/10, en az {esik})"))
    return ned


# ----------------------------------------------------------------- C) yerleşim
# Sayfadaki gerçek alanlar (assets/css/site.css): oran = genişlik/yükseklik; en_az = görünen bölgenin kaynak pikselde en az
# genişliği; yazi = başlık bandının alttan kapladığı pay (yüz bu bandın üstünde kalmalı); yuz = yüz yüksekliğinin görünen
# yüksekliğe en fazla oranı (aşırı yakın kırpmayı engeller).
ALANLAR = {
    # yazi: üç satırlık başlığın ve koyu geçişin kapladığı alt pay (masaüstü 26 px alt boşluk + 3 × 38 px satır + geçiş ≈ %36)
    "manset_d": {"oran": 1.6, "en_az": 760, "yazi": 0.36, "yuz": 0.40},       # masaüstü/tablet manşet, 16:10, başlık altta
    "manset_m": {"oran": 4 / 3, "en_az": 380, "yazi": 0.40, "yuz": 0.42},     # mobil manşet, 4:3
    # yan düzen: başlığın yanındaki dar portre sütunu; vesikalık yakın portre burada uygundur (tepe ve çene payı yine aranır)
    "mansetyan_d": {"oran": 0.66, "en_az": 330, "yazi": 0.0, "yuz": 0.80},    # yan düzen: fotoğraf başlığın yanında (masaüstü)
    "mansetyan_m": {"oran": 0.67, "en_az": 190, "yazi": 0.0, "yuz": 0.80},    # yan düzen, mobil (sütun %50, 4:3 pencere)
    # manşet yanı kartında iki satırlık başlık ve geçiş ≈401×176 kutunun alttan %58'ini kaplar (canlı sayfada ölçüldü)
    "yan_d": {"oran": 2.3, "en_az": 390, "yazi": 0.58, "yuz": 0.40},          # manşet yanı kartı, masaüstü (≈401×176)
    "yan_t": {"oran": 4 / 3, "en_az": 300, "yazi": 0.45, "yuz": 0.45},        # manşet yanı kartı, tablet
    "yan_m": {"oran": 16 / 9, "en_az": 360, "yazi": 0.50, "yuz": 0.45},       # manşet yanı kartı, mobil
    "kart": {"oran": 1.6, "en_az": 290, "yazi": 0.0, "yuz": 0.55},            # haber kartı
}


def _px(kutu, w, h):
    return [kutu[1] * w / 1000, kutu[0] * h / 1000, kutu[3] * w / 1000, kutu[2] * h / 1000]   # x1, y1, x2, y2


def kirp(w, h, alan, ana_kutu, yuzler):
    """Bir alan için object-fit: cover kırpması. Dönüş: (sonuc|None, neden). sonuc: {"odak": "x% y%", ...}"""
    A = alan["oran"]
    if w <= 0 or h <= 0:
        return None, "boyut bilinmiyor"
    if w / h >= A:
        vw, vh = h * A, float(h)
    else:
        vw, vh = float(w), w / A
    if vw < alan["en_az"]:
        return None, f"çözünürlük yetersiz (görünen bölge {int(vw)} px, en az {alan['en_az']})"
    if vw * vh / (w * h) < 0.38:
        return None, "fotoğrafın çok küçük bir parçası görünür (aşırı yakın kırpma)"
    F = None
    for y in yuzler:
        x1, y1, x2, y2 = _px(y, w, h)
        fh = y2 - y1
        e = [x1 - 0.12 * (x2 - x1), y1 - 0.25 * fh, x2 + 0.12 * (x2 - x1), y2 + 0.12 * fh]   # saç tepesi ve çene payı
        F = e if F is None else [min(F[0], e[0]), min(F[1], e[1]), max(F[2], e[2]), max(F[3], e[3])]
        if fh / vh > alan["yuz"]:
            return None, f"yüz alanı çok büyük kaplıyor (%{int(100 * fh / vh)})"
    if F:
        F = [max(0, F[0]), max(0, F[1]), min(w, F[2]), min(h, F[3])]
        if F[2] - F[0] > vw or F[3] - F[1] > vh * (1 - alan["yazi"]):
            return None, "yüzler aynı anda tam görünemiyor ya da başlık bandına giriyor"
    bilinen = ana_kutu is not None
    ak = _px(ana_kutu or [150, 150, 850, 850], w, h)
    cx, cy = (ak[0] + ak[2]) / 2, (ak[1] + ak[3]) / 2

    def aralik(bas_ideal, boy, toplam, z1=None, z2=None, ust_sinir=None):
        lo, hi = 0.0, toplam - boy
        if z1 is not None:
            lo, hi = max(lo, z2 - (ust_sinir if ust_sinir is not None else boy)), min(hi, z1)
        if lo > hi + 0.5:
            return None
        return min(max(bas_ideal, lo), max(lo, hi))

    vx = aralik(cx - vw / 2, vw, w, F[0] if F else None, F[2] if F else None)
    # yüz varsa yüzler görünen alanın üst üçte birine yakın; yoksa ana konu başlık bandının dışında kalan alanın ortasına
    ideal_y = ((F[1] + F[3]) / 2 - 0.38 * vh) if F else (cy - vh * (1 - alan["yazi"]) / 2 if (alan["yazi"] and bilinen) else cy - vh / 2)
    vy = aralik(ideal_y, vh, h, F[1] if F else None, F[3] if F else None, vh * (1 - alan["yazi"]) if F else None)
    if vx is None or vy is None:
        return None, "zorunlu bölge (yüzler) bu oranda kırpılmadan sığmıyor"
    gx = max(0, min(ak[2], vx + vw) - max(ak[0], vx)) / max(1, ak[2] - ak[0])
    gy = max(0, min(ak[3], vy + vh) - max(ak[1], vy)) / max(1, ak[3] - ak[1])
    if gx * gy < 0.55:
        return None, f"ana konunun ancak %{int(100 * gx * gy)}'i görünür"
    if alan["yazi"] and not F and bilinen:
        # başlık bandı ana konuyu örtmesin: görünen ana konunun en az üçte biri bandın üstünde kalmalı
        ust = vy + vh * (1 - alan["yazi"])
        if (min(ak[3], ust) - max(ak[1], vy)) / max(1, min(ak[3], vy + vh) - max(ak[1], vy)) < 0.33:
            return None, "başlık bandı ana konunun büyük kısmını örtüyor"
    px = 50 if w - vw < 1 else round(100 * vx / (w - vw))
    py = 50 if h - vh < 1 else round(100 * vy / (h - vh))
    return {"odak": f"{px}% {py}%", "gorunen": [round(vx), round(vy), round(vw), round(vh)]}, ""


def _anlamli(k):
    """Karenin neredeyse tamamını kaplayan kutu odak bilgisi taşımaz."""
    return k if k and (k[2] - k[0]) * (k[3] - k[1]) < 0.8 * 1000 * 1000 else None


def yerlesim(w, h, icerik, iliski=None):
    """Her alan için uygunluk ve odak noktası. Öncelik: fotoğrafı bu haberle ilişkili kılan unsurun kutusu (haber başına),
    yoksa fotoğrafın ana konusunun kutusu. Dönüş: (yerlesim sözlüğü, alan bazında nedenler)."""
    ak = _anlamli((iliski or {}).get("konu_kutusu")) or _anlamli((icerik or {}).get("ana_kutu"))
    yz = (icerik or {}).get("yuzler") or []
    sonuc, neden = {}, {}
    for ad, alan in ALANLAR.items():
        r, n = kirp(w, h, alan, ak, yz)
        sonuc[ad] = r
        if not r:
            neden[ad] = n
    y = {"boyut": [w, h], "dikey": h > w * 1.05, "surum": YERLESIM_SURUM,
         "manset": "kaplama" if sonuc["manset_d"] and sonuc["manset_m"] else ("yan" if sonuc["mansetyan_d"] and sonuc["mansetyan_m"] else ""),
         "yan": bool(sonuc["yan_d"] and sonuc["yan_t"] and sonuc["yan_m"]), "kart": bool(sonuc["kart"]),
         "detay": min(w, h) >= 300 and w >= 480}
    for ad, r in sonuc.items():
        if r:
            y["o_" + ad] = r["odak"]
    return y, neden


def yerlesim_puani(y):
    return (3 if y.get("manset") == "kaplama" else 1 if y.get("manset") == "yan" else 0) + (1 if y.get("kart") else 0) + (1 if y.get("yan") else 0)


# ----------------------------------------------------------------- seçim çıktısı ve kütüphane kaydı
def secim_yap(bag, a, kimza, konu):
    kontrol = {"yapildi": True, "sonuc": "onaylandı", "tarih": a.get("kontrol_tarih") or gs.simdi_tr().isoformat(timespec="seconds"),
               "yontem": "h1: kaynak/lisans + teknik ölçüm + Gemini içerik ve haber ilişkisi + yerleşim", "model": bag.son_model or bag.model_fn()}
    if a.get("mevcut"):
        s = dict(a["mevcut_secim"])
        s.update(kontrol=kontrol, surum=gs.KURAL_SURUMU, konu_imza=kimza, gorsel_url=a["url"], gorsel=a["url"],
                 gorsel_kredi=a.get("mevcut_kredi", ""))
        for k in ("bekliyor", "kapak_nedeni", "zorla", "yeniden_kontrol", "bekleme_sayisi"):
            s.pop(k, None)
        s["gerekce"] = "mevcut görsel h1 kontrollerinden geçti; korundu — " + (a.get("iliski") or {}).get("gerekce", "")
    else:
        a.setdefault("tur", "arsiv")
        a.setdefault("dogrulama", "")
        a["gerekce"] = (a.get("iliski") or {}).get("gerekce", "")
        s = gs.aday_secimi(a, kontrol, kimza, konu)
        s.pop("konum", None)
    s["sinif"] = a["sinif"]
    s["hat"] = HAT_SURUM
    s["yerlesim"] = a["yerlesim"]
    s["inceleme"] = {"teknik": a.get("teknik"), "icerik": {k: (a.get("icerik") or {}).get(k) for k in ("sahne", "nesneler", "tur", "donem")},
                     "iliski": a.get("iliski")}
    if a.get("indirme") and gs.UNSPLASH_KEY:        # Unsplash kullanım koşulu: seçilen fotoğraf için indirme bildirimi
        try:
            requests.get(a["indirme"], headers={"Authorization": f"Client-ID {gs.UNSPLASH_KEY}"}, timeout=10)
        except Exception:
            pass
    kutuphaneye_ekle(a, konu, s)
    return s


def kutuphaneye_ekle(a, konu, s):
    """Tüm kontrollerden geçen fotoğraf kimlik, kaynak, lisans, içerik ve yerleşim bilgisiyle kütüphaneye yazılır.
    Kayıt yeni bir habere otomatik uygunluk vermez: yalnızca aday olur, haber ilişkisi her seferinde yeniden kontrol edilir."""
    kut = kutuphane()["kayitlar"]
    ic = a.get("icerik") or {}
    kim = {"kisiler": [], "kurumlar": [], "takimlar": [], "tesisler": [], "yerler": []}
    if a["sinif"] == "ozne_kisi" and a.get("kisi"):
        kim["kisiler"].append({"ad": a["kisi"], "qid": a.get("qid", "")})
    elif a["sinif"] == "ozne_kurum":
        ad = re.sub(r"\s*\(.*$", "", a.get("gosterim_ad") or a.get("hedef", ""))
        alan = "takimlar" if a.get("hedef_tur") == "takim" else "tesisler" if a.get("gosterim_ad") else "kurumlar"
        if ad:
            kim[alan].append({"ad": ad, "qid": a.get("qid", "")})
    elif a["sinif"] in ("yer", "yer_genel") and a.get("hedef"):
        kim["yerler"].append({"ad": re.sub(r"\s*\(.*$", "", a.get("gosterim_ad") or a["hedef"]), "qid": a.get("qid", "")})
    gorulen = [g["unsur"] for g in (a.get("iliski") or {}).get("gerekli") or [] if g.get("var")]
    eski = kut.get(a["gk"]) or {}
    for alan, liste in (eski.get("kimlikler") or {}).items():
        for x in liste:
            if x not in kim.setdefault(alan, []):
                kim[alan].append(x)
    kut[a["gk"]] = {
        "url": a["url"], "kucuk": a.get("kucuk") or a["url"], "alt_tur": a.get("alt_tur", ""), "sinif": a["sinif"],
        "kaynak": {"kaynak": a.get("kaynak", ""), "sayfa": s.get("kaynak_sayfa", ""), "sahibi": s.get("fotografci", ""),
                   "lisans": s.get("lisans", ""), "dayanak": s.get("lisans_dayanagi", ""), "atif": s.get("kredi", ""),
                   "atif_kosulu": s.get("kosul", "")},
        "kimlikler": kim, "qid": a.get("qid", ""), "gosterim_ad": a.get("gosterim_ad", ""),
        "cekim_tarihi": a.get("cekim", "") or eski.get("cekim_tarihi", ""),
        "gosterdigi": ic.get("sahne") or a.get("hedef", ""), "konular": sorted(set((eski.get("konular") or []) + gorulen + ic.get("nesneler", [])))[:16],
        "kullanilabilir": ic.get("uygun_baglamlar") or [], "kullanilamaz": ic.get("uygunsuz_baglamlar") or [],
        "boyut": a["yerlesim"]["boyut"],
        "uygun_yerlesimler": [k for k in ("manset", "yan", "kart", "detay") if a["yerlesim"].get(k)],
        "notr_portre": ic.get("notr_portre", False), "dogrulama_surumu": f"{HAT_SURUM}-{ICERIK_SURUM}-{YERLESIM_SURUM}",
        "ilk_kabul": eski.get("ilk_kabul") or gs.simdi_tr().isoformat(timespec="seconds"),
        "son_kabul": gs.simdi_tr().isoformat(timespec="seconds"), "kabul_sayisi": int(eski.get("kabul_sayisi") or 0) + 1,
    }


def kapak(haber, kimza, gerekce, kontrol, kod, aciklama, bekliyor=False):
    s = gs.kapak_secimi(haber.get("kategori"), gerekce, kontrol, kimza)
    s["hat"] = HAT_SURUM
    s["kapak_nedeni"] = {"kod": kod, "aciklama": aciklama[:400]}
    if bekliyor:
        s["bekliyor"] = True
    return s


def mevcut_aday(eski, konu):
    """Haberin şu anki otomatik görseli de aynı kontrollerden geçer; kaynak/lisans bilgisi eski kayıttan okunur."""
    s = eski.get("secim") or {}
    a = {"tur": s.get("tur") or "arsiv", "alt_tur": s.get("alt_tur", ""), "kaynak": ("Wikimedia Commons" if "wikimedia.org" in eski["url"]
         else "Unsplash" if "unsplash" in eski["url"] else s.get("kredi", "")), "url": eski["url"], "kucuk": gs.kontrol_kucuk(eski["url"], 9999),
         "sayfa": s.get("kaynak_sayfa", ""), "fotografci": s.get("fotografci", ""), "lisans": s.get("lisans", ""),
         "lisans_dayanagi": s.get("lisans_dayanagi", ""), "kosul": s.get("kosul", ""), "hedef": s.get("gosterilen", "") or "haberin şu anki görseli",
         "gosterim_ad": s.get("gosterilen", ""), "qid": s.get("qid", ""), "izin_kontrolu": s.get("izin_kontrolu") or {},
         "kisi": s.get("gosterilen", "") if str(s.get("alt_tur", "")).startswith("kisi") else "",
         "cekim": s.get("cekim_yili", ""), "mevcut": True, "mevcut_secim": s, "mevcut_kredi": eski.get("kredi", "")}
    if s.get("sinif"):
        a["sinif"] = s["sinif"]
    a["sinif"] = sinif_belirle(a, konu)
    a["gk"] = gorsel_kimligi(a["url"])
    return a


def hazirla(bag, a):
    """B1 + B2: lisans ve teknik ölçüm (görsel kimliğine göre önbellekli). Eleme olursa a['nedenler'] dolar."""
    ok, n = lisans_kontrol(bag, a)
    if not ok:
        a["nedenler"] = [("lisans", n)]
        return False
    inc = kutuphane()["inceleme"].get(a["gk"]) or {}
    tek = inc.get("teknik") if (inc.get("teknik") or {}).get("s") == TEKNIK_SURUM else None
    if (inc.get("icerik") or {}).get("s") == ICERIK_SURUM:
        a["icerik"] = inc["icerik"]
    try:
        try:
            mime, veri = indir(a.get("kucuk") or a["url"])
        except Exception:
            if not a.get("kucuk") or a["kucuk"] == a["url"]:
                raise
            mime, veri = indir(a["url"], en_fazla=6_000_000)   # küçük kopya yoksa (özgün boyut 500 px'ten küçük) özgün dosya
        if not tek:
            tek = teknik_olc(veri)
            kutuphane()["inceleme"].setdefault(a["gk"], {}).update(teknik=tek, url=a["url"], t=gs.simdi_tr().isoformat(timespec="seconds"))
    except Exception as e:
        a["nedenler"] = [("servis", f"görsel indirilemedi/ölçülemedi: {str(e)[:80]}")]
        return False
    if len(veri) > 1_200_000:          # Gemini'ye gönderilecek kopya küçük olsun
        try:
            from PIL import Image, ImageOps
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(veri))).convert("RGB")
            im.thumbnail((900, 900))
            b = io.BytesIO()
            im.save(b, "JPEG", quality=85)
            mime, veri = "image/jpeg", b.getvalue()
        except Exception:
            pass
    a["_mime"], a["_b64"] = mime, base64.b64encode(veri).decode()
    a["teknik"] = tek
    a["teknik_bayrak"] = teknik_bayraklar(tek)
    w, h = asil_boyut(bag, a, tek["inen"])
    a["boyut"] = [w, h]
    on = []
    tb = a["teknik_bayrak"]
    if tb["cok_karanlik"]:
        on.append(("kalite", f"aşırı karanlık (medyan parlaklık {tek['medyan']}, karanlık pay %{int(100 * tek['karanlik_oran'])})"))
    if tb["cok_bulanik"]:
        on.append(("kalite", f"bulanık (keskinlik {tek['keskinlik']})"))
    if min(w, h) < 300 or w < 480:
        on.append(("kalite", f"çözünürlük yetersiz ({w}×{h})"))
    if on:
        a["nedenler"] = on                 # Gemini isteği harcanmaz
        return False
    return True


# ----------------------------------------------------------------- ana akış
def sec(bag, haber, konu, eski=None):
    """gorsel_sec.gorsel_sec yerine. Dönüş: (secim, rapor)."""
    kimza = gs.konu_imzasi(haber.get("baslik"), haber.get("spot"), haber.get("kategori"))
    simdi = gs.simdi_tr().isoformat(timespec="seconds")
    rapor = {"notlar": [], "adaylar": [], "eski": None, "hat": HAT_SURUM}
    del gs.LISANS_RED[:]
    del gs.LISANS_OKUNAMADI[:]
    if not konu:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "konu ayrıştırılamadı"}
        return kapak(haber, kimza, "Haberin konusu ayrıştırılamadı; fotoğrafsız.", k, "arama_yapilmadi",
                     "haberin konusu ayrıştırılamadı (yapay zekâ kotası ya da servis hatası)", bekliyor=True), rapor
    bag.yeni_secim()
    t0 = time.time()
    try:
        adaylar, notlar = aday_bul(bag, haber, konu)
    except Exception as e:
        adaylar, notlar = [], [f"aday araması hata verdi: {str(e)[:120]}"]
        bag.arama_hatasi = True
    rapor["notlar"] += notlar
    mevcut = mevcut_aday(eski, konu) if eski and eski.get("url") and not eski["url"].startswith("/images/kategori/") else None
    if mevcut:
        adaylar = [mevcut] + [a for a in adaylar if a["gk"] != mevcut["gk"]]
    butce = max(1, int(bag.ayar.get("gorsel_aday_butcesi", 5)))
    incelenen, kabul, hata = [], [], ""
    kuyruk = list(adaylar)
    for tur_no in range(max(1, int(bag.ayar.get("gorsel_kontrol_turu", 2)))):
        parti = []
        while kuyruk and len(parti) < butce:
            a = kuyruk.pop(0)
            if hazirla(bag, a):
                parti.append(a)
            else:
                incelenen.append(a)
        if not parti:
            break
        if not bag.yz_hakki(1):
            hata = "görsel inceleme hakkı (günlük sınır) doldu"
            bag.butce_doldu = True
            break
        try:
            incele(bag, haber, konu, parti)
        except (gs.KotaHatasi, gs.ServisHatasi) as e:
            hata = str(e)
            bag.notlar.append(f"{haber.get('baslik', '')[:60]}: {hata}")
            break
        for a in parti:
            if a.get("_servis"):
                a["nedenler"] = [("servis", a["_servis"])]
            else:
                a["nedenler"] = karar(a, konu)
                if not a["nedenler"]:
                    a["yerlesim"], a["yerlesim_neden"] = yerlesim(a["boyut"][0], a["boyut"][1], a.get("icerik"), a.get("iliski"))
                    if not (a["yerlesim"]["kart"] or a["yerlesim"]["manset"] or a["yerlesim"]["detay"]):
                        a["nedenler"] = [("yerlesim", "hiçbir alan için uygun değil: " + "; ".join(sorted(set(a["yerlesim_neden"].values())))[:200])]
            incelenen.append(a)
        kabul = [a for a in incelenen if not a.get("nedenler")]
        if kabul:
            break
    for a in incelenen:
        a.pop("_b64", None)
    rapor["adaylar"] = [{k: a.get(k) for k in ("sinif", "tur", "alt_tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "hedef", "cekim",
                                               "nedenler", "teknik", "teknik_bayrak", "icerik", "iliski", "yerlesim", "yerlesim_neden", "boyut",
                                               "mevcut", "kutuphane_id", "dogrulama")} for a in incelenen]
    rapor["sorgular"] = list(getattr(bag, "sorgu_kaydi", []))[:40]
    rapor["havuz"] = {s: sum(1 for a in adaylar if a["sinif"] == s) for s in SINIF_SIRA}
    if mevcut:
        rapor["eski"] = {"karar": "kabul" if not mevcut.get("nedenler") and mevcut in kabul else ("red" if mevcut.get("nedenler") else ""),
                         "gerekce": "; ".join(n for _, n in mevcut.get("nedenler") or []) or (mevcut.get("iliski") or {}).get("gerekce", ""),
                         "nedenler": mevcut.get("nedenler")}
    print(f"   görsel (h1): {len(adaylar)} aday, {len(incelenen)} incelendi, {len(kabul)} kabul, {time.time() - t0:.0f} sn"
          + (f" ({hata[:80]})" if hata else ""))
    for a in incelenen:      # kabul edilmeyen kurum fotoğrafı sitede tutulmaz
        if a.get("nedenler") and a["url"].startswith("/images/kurum/") and not a.get("mevcut"):
            gs.yerel_gorsel_sil(a["url"])
    if kabul:
        kabul.sort(key=lambda a: (SINIF_SIRA.get(a["sinif"], 0), yerlesim_puani(a["yerlesim"]), (a.get("iliski") or {}).get("uygunluk", 0),
                                  1 if a.get("mevcut") else 0), reverse=True)
        return secim_yap(bag, kabul[0], kimza, konu), rapor
    k = {"yapildi": bool(incelenen) and not hata, "sonuc": "adaylar reddedildi" if incelenen else "aday yok", "tarih": simdi,
         "yontem": "h1", "model": bag.son_model or ""}
    if hata:
        k.update(yapildi=False, sonuc="yapılamadı", neden=hata[:160])
        return kapak(haber, kimza, f"İnceleme yapılamadı ({hata[:120]}); kontrol edilmemiş fotoğraf kullanılmadı.", k, "kontrol_yapilamadi",
                     hata, bekliyor=True), rapor
    if bag.arama_hatasi and not incelenen:
        return kapak(haber, kimza, "Fotoğraf araması hata verdi; sonra yeniden denenecek.", k, "arama_hatasi",
                     "; ".join(rapor["notlar"])[:300], bekliyor=True), rapor
    if incelenen:
        ozet = "; ".join(sorted({n for a in incelenen for _, n in (a.get("nedenler") or [])}))
        return kapak(haber, kimza, f"{len(incelenen)} aday kontrolden geçemedi; fotoğrafsız.", k, "gorsel_reddedildi", ozet), rapor
    return kapak(haber, kimza, "Haberin öznesine ya da konusuna ait kullanım hakkı doğrulanmış fotoğraf bulunamadı; fotoğrafsız.", k,
                 "aday_bulunamadi", "; ".join(rapor["notlar"])[:300]), rapor
