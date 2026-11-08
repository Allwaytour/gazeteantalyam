#!/usr/bin/env python3
"""Gazete Antalya – görsel hattı h1/h2 (üç ayrı aşama).

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
(4) her haber için ayrıca sorulur; kütüphanede onaylı olmak yeni habere uygun olmak anlamına gelmez. h2'den itibaren (4)'ün yanıtı
da haber (konu imzası) + fotoğraf + soru sürümü (ILISKI_SURUM) için kaydedilir: haber metni, fotoğraf ya da soru değişmedikçe Gemini'ye
yeniden sorulmaz, kabul/ret kuralları aynı kayıtlı yanıta uygulanır (sonuç turdan tura değişmez). Servis/kota hatası içerik reddi
sayılmaz: haber "bekliyor" olarak sonra yeniden denenir, mevcut fotoğraf korunur.

h2 — aday bulma başarısı: ilk adaylar elenirse (A2) ret nedenlerine göre, ana özneyi koruyan yeni sorgularla bir tur daha aranır;
etkinliğin yapıldığı tesis (ve takım haberinde takımın kendi tesisi) olaysız tesis görünümü olarak aranır; kutudan ölçülen alan payı
ret nedeni değil, yalnızca yardımcı sinyaldir (yerine fotoğrafın çekim planı ve modelin anlamsal yanıtı); dikey portreler kartlarda
küçük portre varyantıyla gösterilir.
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

HAT_SURUM = "h2"         # kural sürümü (kayıtlara yazılır)
# Bu sürümlerin kabul ettiği kütüphane kayıtları aday olur. h1e kayıtları h2'de de geçerlidir (h2 yalnızca aday bulmayı
# genişletti); daha eski h1/h1b/h1c/h1d kayıtları yanlış kabuller içerdiği için kullanılmaz.
KUTUPHANE_GECERLI = {"h1e", "h2"}
ICERIK_SURUM = "i3"        # fotoğrafın kendisine ait inceleme (tür, dönem, kutular, çekim planı, sahne türü) – değişirse yeniden incelenir
ILISKI_SURUM = "r1"        # haber–fotoğraf ilişkisi sorusunun sürümü: aynı haber + aynı fotoğraf + aynı sürüm → kayıtlı yanıt kullanılır
TEKNIK_SURUM = "t1"        # teknik ölçüm yöntemi
YERLESIM_SURUM = "y3"      # y3: dikey portre için küçük kart varyantı (kart_portre)
KABUL_KURALI = "k3"        # kabul kurallarının sürümü: değişince onaylı fotoğraflar kayıtlı yanıtlarla (Gemini'siz) yeniden denetlenir
ARAMA_SURUM = "a2"         # aday arama yeteneği: değişince fotoğrafsız haberler (ikinci kuyruk) bir kez yeniden aranır
KUTUPHANE_DOSYA = os.path.join(gs.KOK, "data", "gorsel_kutuphanesi.json")

SINIF_ACIKLAMA = {
    "ayni_olay": "aynı olayın fotoğrafı (kullanım izni kayıtlı kaynaktan)",
    "ozne_kisi": "haberin ana kişisinin arşiv fotoğrafı (kimlik kaynak kaydından: Wikidata + Commons kategorisi)",
    "ozne_kurum": "haberin ana öznesi olan kurumun/takımın/tesisin fotoğrafı (kimlik kaynak kaydından)",
    "konu": "haberin konusunu gösteren temsilî fotoğraf (olayın kendisi değil)",
    "yer": "haberde geçen bir yerin/tesisin fotoğrafı (haberin ana öznesi değil)",
    "mekan": "haberdeki etkinliğin yapıldığı tesisin/mekânın arşiv fotoğrafı (etkinliğin kendisi değil; olaysız, nötr görünüm beklenir)",
    "yer_genel": "haberde geçen ilçenin/şehrin genel görünümü",
}
SINIF_SIRA = {"ayni_olay": 6, "ozne_kisi": 5, "ozne_kurum": 5, "konu": 3, "mekan": 3, "yer": 2, "yer_genel": 1}

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
    _KUT["kayitlar"] = {k: v for k, v in _KUT["kayitlar"].items() if str(v.get("dogrulama_surumu", "")).split("-")[0] in KUTUPHANE_GECERLI}
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
        if r.get("dogrulama_surumu", "").split("-")[0] not in KUTUPHANE_GECERLI:
            continue
        kurum_izinli = r.get("alt_tur") == "kurum_haber" or (r.get("kaynak") or {}).get("lisans") == "Kurum izniyle"
        if kurum_izinli and (r.get("izin_kapsami") != "genel" or r.get("sahne_turu") != "notr_yapi_tesis"):
            # Kurum izni kaydı yalnızca "aynı haber" kapsamındaysa (ya da kare bir olayın karesiyse) fotoğraf başka habere taşınmaz:
            # başka bir yerin/olayın gerçek karesi bu haberin görüntüsü sanılır. Kapsamı "genel" olan iznin olaysız bina/tesis
            # karesi ise kimliği tutan başka haberlerde aday olur (konu/temsilî olarak değil).
            continue
        if r.get("sinif") == "ayni_olay" and not kurum_izinli:
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
        kimlikli = any(kim.get(x) for x in ("kisiler", "kurumlar", "takimlar", "tesisler", "yerler"))
        if not neden and gerekli and not kurum_izinli and r.get("alt_tur") == "nesne" and not kimlikli and r.get("sahne_turu") != "portre":
            # Konu eşleşmesiyle yalnızca kimliği olmayan nesne/konu kareleri başka habere aday olur; belirli bir yerin (Manavgat'taki
            # cami, Alanya'nın genel görünümü), kurumun ya da kişinin karesi konu benzerliği yüzünden başka bir haberde kullanılmaz.
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


GENEL_TESIS_KELIME = {"stadı", "stadi", "stadyumu", "stadium", "stad", "arena", "spor", "kompleksi", "complex", "tesisleri", "tesisi",
                      "salonu", "merkezi", "center", "centre", "uluslararası", "international", "sports", "sporları", "plaj", "beach"}


def _takim_koku(ad):
    return {re.sub(r"(belediyespor|spor|sk|fk|kulübü|kulubu)$", "", w) for w in gs.kelimeler(ad) if len(w) >= 3} - {""}


def mekan_uygun(h, konu):
    """Hedef, haberdeki etkinliğin geçtiği tesis mi (konu.nerede)? Olay haberinde tesis fotoğrafı olaysız görünüm olarak
    kullanılabilir; takım haberinde yalnızca takımın kendi tesisi (adında takımın adı geçen, ör. Kepezspor → Kepez … Stadı)."""
    if h.get("tur") not in ("tesis", "yer"):
        return False
    yer = konu.get("nerede") or {}
    if not yer.get("ad") or not ad_ayni(h["ad"], yer["ad"]) or not re.search(r"(?i)stad|arena|kompleks|complex|tesis|salon|merkez|center|sahas", h["ad"]):
        return False
    tur = ana_ozne(konu).get("tur")
    if tur == "olay":
        return True
    if tur == "takim":
        tk = set().union(*[_takim_koku(t["ad"]) for t in takimlar(konu, "ana")] or [set()])
        hk = {w for w in gs.kelimeler(h["ad"]) if w not in GENEL_TESIS_KELIME}
        return bool(tk & hk) and not any(_takim_koku(t["ad"]) & hk for t in takimlar(konu, "rakip"))
    return False


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
    secili, mekanlar = [], []
    for h in konu.get("hedefler") or []:
        if rakip_mi(h["ad"], konu):
            notlar.append(f"'{h['ad']}' haberdeki rakip takım; fotoğrafı aranmadı")
            continue
        if gs.ad_eslesir(h["ad"], bs) or gs.ad_eslesir(h.get("ad_en") or h["ad"], bs) or ana_ozne_adi_mi(h["ad"], konu):
            secili.append(h)
        elif mekan_uygun(h, konu):
            # Etkinliğin yapıldığı tesis (ör. turnuvanın oynandığı spor kompleksi, takımın kendi stadı): etkinliğin kendisi değil,
            # olaysız tesis görünümü olarak aranır; rakibin tesisi ve şehirdeki rastgele bir tesis değil.
            secili.append(h)
            mekanlar.append(h["ad"])
            notlar.append(f"'{h['ad']}' etkinliğin yapıldığı tesis; tesis fotoğrafı arandı (olay karesi olarak değil)")
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
            if a["sinif"] in ("yer", "ozne_kurum") and any(ad_ayni(m, a.get("gosterim_ad") or re.sub(r"\s*\(.*$", "", a.get("hedef", "")))
                                                          for m in mekanlar) and not ana_ozne_adi_mi(a.get("gosterim_ad") or "", konu):
                a["sinif"] = "mekan"
            a["gk"] = gk
            hepsi.append(a)
    hepsi.sort(key=lambda a: (-SINIF_SIRA.get(a["sinif"], 0), 0 if a.get("kutuphane_id") else 1, -int(a.get("meta_puan") or 0)))
    return hepsi, notlar


# ----------------------------------------------------------------- B1) kaynak / lisans
def lisans_kontrol(bag, a):
    """Kullanım dayanağı yoksa aday elenir. İnternette ya da resmî sitede bulunmak izin sayılmaz."""
    lis = (a.get("lisans") or "").strip()
    if (a.get("alt_tur") == "kurum_haber" or lis == "Kurum izniyle") and a.get("sinif") != "ayni_olay":
        # İzin kapsamı izin kaydından okunur (data/gorsel_izinleri.yml → yeniden_kullanim). "ayni_haber" (varsayılan): kurumun
        # fotoğrafı yalnızca kurumun aynı haberinde kullanılır. "genel": olaysız bina/tesis karesi başka uygun haberlerde de
        # kullanılabilir (karar aşamasında sahne türü ayrıca denetlenir: bir olayın karesi başka olaymış gibi kullanılmaz).
        if a.get("izin_kapsami") != "genel":
            return False, "kurum izni yalnızca kurumun aynı haberi için (izin kapsamı: aynı haber); başka habere taşınmadı"
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
- cekim: "yakin" (konu kareyi dolduruyor) | "orta" (konu ve yakın çevresi) | "genis" (sokak/saha/salon ölçeğinde geniş kare) |
  "panorama" (havadan ya da yüksekten şehir/sahil/manzara görünümü; insanlar ve nesneler uzaktan küçük ayrıntı)
- sahne_turu: "notr_yapi_tesis" (olaysız bina, tesis, mekân görünümü) | "olay_etkinlik" (belirli bir etkinliğin, törenin, maçın,
  ziyaretin karesi) | "portre" | "manzara" | "nesne_urun" | "gunluk_yasam" | "diger"
- uygun_baglamlar / uygunsuz_baglamlar: bu fotoğrafın kullanılabileceği ve kullanılamayacağı haber türleri (en fazla 3'er, kısa)

"iliski" — bu fotoğraf BU haber için. İddiası "haberin ana kişisinin arşiv fotoğrafı" olan karelerde haberdeki olayın (toplantı,
ziyaret, buluşma) görülmesi BEKLENMEZ: kişinin nötr portresi uygundur; bu karelerde olay_gorunuyor false olabilir ve ilgisiz_yer_nesne
yalnızca karede kişiden başka, haberle çelişen bir yer/nesne öne çıkıyorsa true olur. Aynı şekilde "kurumun/takımın/tesisin fotoğrafı"
iddiasında binanın ya da tesisin kendisi yeterlidir. "Etkinliğin yapıldığı tesis" iddiasında da etkinliğin görülmesi beklenmez:
tesisin olaysız görünümü uygundur (gerekli unsurlar için tesisin kendisi yeterlidir); karede başka bir etkinlik varsa baska_olay_sanilir true olur.
- ana_ozne_gorunuyor: haberin ana öznesi fotoğrafta görülüyor mu? (kişi fotoğrafında: kaynak kaydındaki kişi tek başına ya da açıkça öne çıkıyorsa true)
- olay_gorunuyor: haberdeki olay/eylem fotoğrafta görülüyor mu?
- gerekli: her "GÖRSELDE OLMALI" unsuru için {{"unsur": "...", "var": true/false}} (liste boşsa []); unsurlar çoğunlukla
  seçeneklidir (ör. "musluk" ya da "su hattı çalışması"): karar bunlardan en az birinin açıkça görülmesine göre verilir
- yasak: fotoğrafta görülen "GÖRSELDE OLMAMALI" unsurları (yoksa [])
- yanlis_kisi: haberle ilgisi olmayan biri haberdeki kişi sanılacak biçimde öne çıkıyor mu?
- yanlis_takim: başka bir takımın forması, stadı ya da logosu haberdeki takımın yerine öne çıkıyor mu? (RAKİP takımın stadı/oyuncuları dahil)
- yanlis_spor: fotoğraftaki spor dalı haberdekinden farklı mı?
- yaniltici_donem: fotoğrafın dönemi haberle çelişiyor mu? (güncel bir haber için tarihî ya da siyah-beyaz kare → true)
- ilgisiz_yer_nesne: fotoğrafın asıl konusu haberin konusuyla ilgisiz bir yer/nesne mi? (ör. taksi haberinde uçak; plaj voleybolu
  haberinde yalnızca sahil manzarası; maç haberinde heykel)
- baska_olay_sanilir: başka bir olaya ait sahne bu haberin görüntüsü sanılır mı? ("aynı olayın fotoğrafı" iddiasında false)
- belirli_kisi_isletme: fotoğrafta okunabilir bir plaka, firma/durak/dükkân adı ya da tanınabilir bir kişi var ve haber olumsuz
  (şikâyet, tepki, suç, kaza, ceza) olduğu için o kişi, araç ya da işletme haberde suçlanıyormuş gibi mi görünür?
- yaniltici_yer: fotoğraftaki yabancı unsurlar (yabancı dilde tabela/yazı, plaka, forma, simge yapı) olayın Türkiye dışında
  geçtiği ya da başka bir ülkenin kurumuyla ilgili olduğu izlenimini verir mi? (yabancı unsur seçilmiyorsa ya da önemsizse false)
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
 "yuzler": [], "ulke": "belirsiz", "notr_portre": false, "cekim": "orta", "sahne_turu": "diger", "uygun_baglamlar": ["..."],
 "uygunsuz_baglamlar": ["..."]}},
 "iliski": {{"ana_ozne_gorunuyor": false, "olay_gorunuyor": false, "gerekli": [], "yasak": [], "yanlis_kisi": false, "yanlis_takim": false,
 "yanlis_spor": false, "yaniltici_donem": false, "ilgisiz_yer_nesne": false, "baska_olay_sanilir": false, "belirli_kisi_isletme": false,
 "yaniltici_yer": false, "konu_ana_unsur": true, "konu_kutusu": null,
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
        "cekim": str(d.get("cekim") or "orta") if str(d.get("cekim")) in ("yakin", "orta", "genis", "panorama") else "orta",
        "sahne_turu": str(d.get("sahne_turu") or "diger"),
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
        **{k: d.get(k) is True for k in ("yanlis_kisi", "yanlis_takim", "yanlis_spor", "yaniltici_donem", "ilgisiz_yer_nesne", "baska_olay_sanilir",
                                         "belirli_kisi_isletme", "yaniltici_yer")},
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
        elif not ic.get("notr_portre"):
            # kişi fotoğrafı ile olay fotoğrafı ayrıdır: arşivden yalnızca kişinin nötr portresi kullanılır; başka bir
            # olaydaki karesi (gol sevinci, tören, ziyaret) bu haberin olayı sanılabilir
            ned.append(("konu", "kişinin nötr portresi değil (başka bir olaydaki karesi)"))
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
        # aynı olay, konu, mekân, yer, yer genel
        gerekli = il.get("gerekli") or []
        gorulen = [g for g in gerekli if g.get("var")]
        if sinif != "mekan" and gerekli and not gorulen:
            # "görselde olmalı" unsurları seçeneklidir (musluk YA DA su hattı çalışması): en az biri açıkça görülmeli
            ned.append(("konu", "zorunlu unsurlardan hiçbiri görünmüyor: " + ", ".join(eksik[:3])))
        if sinif in ("ayni_olay", "konu") and not gerekli and not (il.get("olay_gorunuyor") or il.get("ana_ozne_gorunuyor")):
            ned.append(("konu", "ne haberin öznesi ne de olayı görünüyor"))
        # Konunun karede ana unsur olup olmadığına modelin anlamsal yanıtı ve fotoğrafın kendi çekim planı (fotoğraf başına
        # bir kez belirlenir, haberden habere değişmez) karar verir. Kutudan ölçülen alan payı tek başına ret nedeni değildir;
        # yalnızca yardımcı sinyaldir: konu küçük görünüyorsa habere uygunluk eşiği yükselir ve kırpma ona göre yapılır.
        olay_haberi = ana_ozne(konu).get("tur") == "olay" or bool(konu.get("spor_dali"))
        kk = il.get("konu_kutusu") or _anlamli(ic.get("ana_kutu"))
        alan_payi = ((kk[2] - kk[0]) * (kk[3] - kk[1]) / 1e6) if kk else None
        a["sinyal"] = {"konu_alani": round(alan_payi, 3) if alan_payi is not None else None, "cekim": ic.get("cekim")}
        if sinif in ("ayni_olay", "konu"):
            if il.get("konu_ana_unsur") is False:
                ned.append(("konu", "haberin konusu fotoğrafın ana konusu değil (geniş kare içinde ayrıntı)"))
            elif olay_haberi and ic.get("cekim") == "panorama":
                ned.append(("konu", "havadan/yüksekten manzara karesi: olayın kendisini değil manzarayı gösteriyor"))
        if sinif != "ayni_olay":
            if il.get("baska_olay_sanilir"):
                ned.append(("konu", "başka bir olayın karesi bu haberin görüntüsü sanılır"))
            if il.get("belirli_kisi_isletme"):
                ned.append(("konu", "olumsuz haberde belirli bir kişi, araç (plaka) ya da işletme suçlanıyormuş gibi görünüyor"))
            if sinif in ("yer", "yer_genel", "mekan") and ic.get("ulke") == "yabanci":
                ned.append(("konu", "Türkiye dışına ait yer (haberdeki yer değil)"))
            elif il.get("yaniltici_yer"):
                ned.append(("konu", "yabancı unsurlar olayın başka bir ülkede geçtiği izlenimini veriyor"))
            if konu.get("hassas", "yok") != "yok" and ic.get("yuz_belirgin"):
                ned.append(("konu", "hassas haberde temsilî karede tanınabilir kişi"))
            if sinif == "konu" and a.get("alt_tur") == "yer_konu" and a.get("gosterim_ad"):
                # belirli bir yerin (ilçe, mekân) karesi yalnızca haberin geçtiği yerde kullanılır: Serik'teki olaya Manavgat'taki cami değil
                yerler = [(konu.get("nerede") or {}).get("ad", ""), konu.get("yer", "")] + \
                    [h.get("ad", "") for h in konu.get("hedefler") or [] if h.get("tur") in ("yer", "tesis")]
                if _k(a["gosterim_ad"]) != "antalya" and not any(ad_ayni(a["gosterim_ad"], y) for y in yerler if y):
                    ned.append(("konu", f"fotoğraf başka bir yere ait ({a['gosterim_ad']}); haberin geçtiği yer değil"))
            if sinif == "konu" and ic.get("sahne_turu") == "olay_etkinlik" and ic.get("cekim") in ("genis", "panorama") \
                    and int(ic.get("kisi_sayisi") or 0) >= 15:
                # başka bir etkinliğin kalabalık/geniş karesi (tribün, sahne önü, kent silueti) haberdeki etkinliğin kendisi sanılır;
                # temsilî fotoğraf konuyu (top, file, kort, sahne) göstermeli, başka bir yerin etkinliğini değil
                ned.append(("konu", "başka bir etkinliğin kalabalık/geniş karesi haberdeki etkinlik sanılır"))
            if sinif == "konu" and ic.get("sahne_turu") == "portre":
                ned.append(("konu", "temsilî fotoğraf belirli bir kişinin portresi"))
            if sinif == "konu" and gs.TOPLANTI.search(f"{a.get('gosterim_ad') or ''} {a.get('hedef') or ''}"):
                # açıklama/toplantı/konuşma haberinde konuşma eyleminin genel karesi (mikrofon, kürsü) habere bilgi katmaz: dolgu görsel
                ned.append(("konu", "açıklama/toplantı eyleminin genel karesi (mikrofon, kürsü gibi dolgu görsel)"))
            adli = bool(konu.get("ana_kisiler") or konu.get("kisiler") or ana_ozne(konu).get("tur") == "kisi")
            if sinif == "konu" and adli and ic.get("yuz_belirgin") and ic.get("kisi_sayisi", 0) <= 3:
                ned.append(("konu", "temsilî fotoğrafta tanınabilir kişi (haberde adı geçen kişi sanılabilir)"))
    kurum_izinli = a.get("alt_tur") == "kurum_haber" or (a.get("lisans") or "").strip() == "Kurum izniyle"
    if kurum_izinli and sinif != "ayni_olay" and ic.get("sahne_turu") != "notr_yapi_tesis":
        ned.append(("lisans", "kurum izniyle alınan olay karesi başka haberde kullanılamaz (yalnızca olaysız bina/tesis karesi)"))
    # habere uygunluk eşiği: temsilî ve kurum fotoğrafında 7 (modelin "doğrudan yansıtmıyor" dediği 5-6 puanlık kareler dolgu sayılır)
    esik = {"yer_genel": 7, "yer": 7, "konu": 7, "ozne_kurum": 7, "mekan": 6}.get(sinif, 5)
    alan = (a.get("sinyal") or {}).get("konu_alani")
    if sinif in ("ayni_olay", "konu") and alan is not None and alan < 0.05:
        esik = max(esik, 8)             # konu karede küçükse fotoğrafın haberi doğrudan anlattığına dair daha güçlü yanıt aranır
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
    # dikey portre kartı: küçük 3:4 fotoğraf solda, başlık sağda (geniş kartta yüz büyütülmesin/kesilmesin diye)
    "kart_p": {"oran": 0.75, "en_az": 150, "yazi": 0.0, "yuz": 0.85},
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
    # Geniş kart/yan kart uymuyorsa (dikey portre) haber fotoğrafsız kalmaz: küçük portre varyantı kullanılır
    y["kart_portre"] = not y["kart"] and bool(sonuc["kart_p"])
    y["yan_portre"] = not y["yan"] and bool(sonuc["kart_p"])
    for ad, r in sonuc.items():
        if r:
            y["o_" + ad] = r["odak"]
    return y, neden


def yerlesim_puani(y):
    return ((3 if y.get("manset") == "kaplama" else 1 if y.get("manset") == "yan" else 0) + (1 if y.get("kart") else 0.5 if y.get("kart_portre") else 0)
            + (1 if y.get("yan") else 0.5 if y.get("yan_portre") else 0))


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
    s["kabul_kurali"] = KABUL_KURALI
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
    elif a["sinif"] == "ayni_olay" and a.get("alt_tur") == "kurum_haber" and a.get("kaynak"):
        kim["kurumlar"].append({"ad": a["kaynak"], "qid": ""})        # kurumun kendi yayımladığı fotoğraf (izin kapsamı ayrıca kayıtlı)
    elif a["sinif"] in ("yer", "yer_genel", "mekan") and a.get("hedef"):
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
        "kullanilabilir": ic.get("uygun_baglamlar") or [], "kullanilamaz": (ic.get("uygunsuz_baglamlar") or [])
        + (["başka haber ya da olay (yalnızca kendi haberindeki olayın fotoğrafı)"] if a["sinif"] == "ayni_olay" and a.get("izin_kapsami") != "genel" else []),
        "yalniz_ayni_olay": a["sinif"] == "ayni_olay" and a.get("izin_kapsami") != "genel",
        "izin_kapsami": a.get("izin_kapsami") or ("ayni_haber" if a.get("alt_tur") == "kurum_haber" else ""),
        "sahne_turu": ic.get("sahne_turu", ""), "cekim": ic.get("cekim", ""),
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


def kural_denetimi(v, konu):
    """Onaylı fotoğrafa güncel kabul kurallarını Gemini'ye sormadan, kayıtlı inceleme yanıtlarıyla uygular.
    Dönüş: nedenler listesi ([] → kurallara uyuyor) ya da None (kayıt eksik; tam yeniden değerlendirme gerekir)."""
    s = v.get("gorsel_secim") or {}
    ic = (kutuphane()["inceleme"].get(gorsel_kimligi(v.get("gorsel", ""))) or {}).get("icerik")
    inc = s.get("inceleme") or {}
    if not ic or not inc.get("iliski") or not s.get("sinif"):
        return None
    a = {"sinif": s["sinif"], "icerik": ic, "iliski": inc["iliski"], "teknik_bayrak": teknik_bayraklar(inc["teknik"]) if inc.get("teknik") else {},
         "alt_tur": s.get("alt_tur", ""), "lisans": s.get("lisans", ""), "gosterim_ad": s.get("gosterilen", ""), "hedef": s.get("gosterilen", ""),
         "izin_kapsami": s.get("izin_kapsami", ""), "mevcut": True}
    return karar(a, konu)


def mevcut_aday(eski, konu):
    """Haberin şu anki otomatik görseli de aynı kontrollerden geçer; kaynak/lisans bilgisi eski kayıttan okunur."""
    s = eski.get("secim") or {}
    a = {"tur": s.get("tur") or "arsiv", "alt_tur": s.get("alt_tur", ""), "kaynak": ("Wikimedia Commons" if "wikimedia.org" in eski["url"]
         else "Unsplash" if "unsplash" in eski["url"] else s.get("kredi", "")), "url": eski["url"], "kucuk": gs.kontrol_kucuk(eski["url"], 9999),
         "sayfa": s.get("kaynak_sayfa", ""), "fotografci": s.get("fotografci", ""), "lisans": s.get("lisans", ""),
         "lisans_dayanagi": s.get("lisans_dayanagi", ""), "kosul": s.get("kosul", ""), "hedef": s.get("gosterilen", "") or "haberin şu anki görseli",
         "gosterim_ad": s.get("gosterilen", ""), "qid": s.get("qid", ""), "izin_kontrolu": s.get("izin_kontrolu") or {},
         "kisi": s.get("gosterilen", "") if str(s.get("alt_tur", "")).startswith("kisi") else "",
         "cekim": s.get("cekim_yili", ""), "mevcut": True, "mevcut_secim": s, "mevcut_kredi": eski.get("kredi", ""),
         "izin_kapsami": s.get("izin_kapsami") or (s.get("izin_kontrolu") or {}).get("kapsam", "")}
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
        # Mevcut fotoğraf indirilemediyse bu geçici bir servis sorunudur (fotoğraf kaldırılmaz); yeni adayın dosyası
        # açılamıyorsa aday kullanılamaz (kaynak sorunu, içerik reddi değil).
        a["nedenler"] = [("servis" if a.get("mevcut") else "kaynak", f"görsel indirilemedi/ölçülemedi: {str(e)[:80]}")]
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


# ----------------------------------------------------------------- kayıtlı inceleme kararları (sabit sonuç)
def _iliski_anahtari(kimza, a):
    return f"{ILISKI_SURUM}:{kimza}:{a['sinif']}"


def iliski_kayitli(a, kimza):
    """Aynı haber (konu imzası: başlık + spot + kategori), aynı fotoğraf ve aynı soru sürümü için Gemini'nin önceki yanıtı.
    Haber metni, fotoğraf ya da değerlendirme sorusu değişmedikçe yeniden sorulmaz; kabul/ret kuralları (karar) bu kayıtlı
    yanıta her turda aynı biçimde uygulanır. Servis hatası olan yanıt kaydedilmez."""
    r = ((kutuphane()["inceleme"].get(a["gk"]) or {}).get("iliski") or {}).get(_iliski_anahtari(kimza, a))
    return r.get("d") if isinstance(r, dict) and isinstance(r.get("d"), dict) else None


def iliski_kaydet(a, kimza):
    kayit = kutuphane()["inceleme"].setdefault(a["gk"], {})
    il = kayit.setdefault("iliski", {})
    il[_iliski_anahtari(kimza, a)] = {"d": a["iliski"], "t": a.get("kontrol_tarih") or gs.simdi_tr().isoformat(timespec="seconds")}
    if len(il) > 12:
        for k in sorted(il, key=lambda k: il[k].get("t", ""))[: len(il) - 12]:
            il.pop(k, None)
    kayit.setdefault("url", a["url"])
    kayit["t"] = gs.simdi_tr().isoformat(timespec="seconds")


def incele_kayitli(bag, haber, konu, kimza, parti):
    """Önce kayıtlı yanıtlar; yalnızca kaydı olmayan fotoğraflar Gemini'ye sorulur. Dönüş: sorulan fotoğraf sayısı."""
    sorulacak = []
    for a in parti:
        k = iliski_kayitli(a, kimza) if a.get("icerik") else None
        if k:
            a["iliski"], a["onbellekten"] = k, True
        else:
            sorulacak.append(a)
    if not sorulacak:
        return 0
    if not bag.yz_hakki(1):
        raise gs.KotaHatasi("görsel inceleme hakkı (günlük sınır) doldu")
    incele(bag, haber, konu, sorulacak)
    for a in sorulacak:
        if a.get("iliski") and not a.get("_servis") and a.get("icerik"):
            iliski_kaydet(a, kimza)
    return len(sorulacak)


# ----------------------------------------------------------------- A2) ret nedenine göre yeni aday arama
ALT_SORGU_TALIMAT = """Bir haber sitesinin fotoğraf editörüsün. Aşağıdaki haber için ücretsiz arşivlerde (Wikimedia Commons, Unsplash)
fotoğraf aranıyor. İlk aramada bulunan adaylar aşağıdaki nedenlerle elendi. Haberin ANA ÖZNESİNİ ve konusunu koruyarak, bu elenme
nedenlerinden kaçınan yeni arama sorguları yaz.

Kurallar:
- 3 ya da 4 sorgu, her biri 2-5 kelime, İngilizce.
- Kişi adı, kurum adı ya da etkinlik adı yazma; haberi DOĞRUDAN anlatan somut, fotoğraflanabilir konuyu ara (nesne, araç, faaliyet,
  spor dalının oyun anı, mekân türü). Haberin konusundan uzaklaşma: başka bir konunun fotoğrafı istenmez.
- Elenme nedenine göre sorguyu değiştir: "tanınabilir kişi/plaka/işletme" → kişisiz ya da yakın plan nesne (ör. araç üstü tabela);
  "manzara/küçük ayrıntı" → konunun yakın ya da orta plan karesi; "yanlış spor" → spor dalının kendine özgü unsuru (file, kum, top);
  "Türkiye dışı" → gerekirse "Turkey" ya da "Turkish" ekle; "tarihî/siyah-beyaz" → güncel çekim ifadesi.
- Toplantı, konferans, basın açıklaması, kalabalık, konuşmacı gibi insan odaklı sahne ARAMA.
- Daha önce denenen sorguları tekrar etme.
Örnek (başka bir haber için): ekmek fiyatı haberinde ilk aramada fırın vitrininde çalışan biri elendiyse → "bread loaves bakery shelf",
"fresh bread close up".

HABER BAŞLIĞI: {baslik}
SPOT: {spot}
ANA ÖZNE: {ozne} | KONU: {ne}
GÖRSELDE OLMALI: {olmali}
GÖRSELDE OLMAMALI: {olmamali}
DENENEN SORGULAR: {denenen}
ELENEN ADAYLAR:
{elenen}

Yalnızca şu biçimde JSON döndür: {{"sorgular": ["...", "..."]}}"""


def temsili_izinli(konu):
    """Ret sonrası temsilî (konu) fotoğraf aranabilir mi? Hassas haberde (ürün haberi dışında), takım haberinde (rastgele bir maç
    karesi başka takımı gösterir) ve belirli bir tesis/mekân haberinde (başka mekân o mekân sanılır) aranmaz."""
    oz = ana_ozne(konu)
    hassas = konu.get("hassas", "yok")
    if hassas != "yok" and not (hassas == "çocuk" and oz.get("tur") == "nesne"):
        return False, "hassas haber: temsilî fotoğraf aranmaz"
    if oz.get("tur") == "takim":
        return False, "takım haberi: genel maç/oyuncu karesi başka takımı gösterir; yalnızca takımın kendi kaydı ya da kendi tesisi"
    if oz.get("tur") == "yer" and oz.get("ad") not in gs.YERLER:
        return False, "belirli bir mekân haberi: başka mekânın karesi o mekân sanılır"
    return True, ""


def alternatif_sorgular(bag, haber, konu, kimza, incelenen):
    """Elenen adayların nedenlerine göre yeni sorgular (konu imzası başına bir kez üretilir ve kaydedilir)."""
    anahtar = "altsorgu:" + gs.imza(ILISKI_SURUM, kimza)
    ob = bag.onbellek_al(anahtar)
    if isinstance(ob, dict) and isinstance(ob.get("sorgular"), list):
        return ob["sorgular"], "kayıtlı"
    denenen = [re.sub(r"^[^:]+: (.*?) → .*$", r"\1", x) for x in getattr(bag, "sorgu_kaydi", []) if "arama:" in x]
    elenen = "\n".join(f"- {(a.get('icerik') or {}).get('sahne') or a.get('hedef', '')[:60]} → "
                       + "; ".join(n for _, n in (a.get("nedenler") or [])[:3]) for a in incelenen[:8]) or "- (aday bulunamadı)"
    oz = ana_ozne(konu)
    sorgular, kaynak = [], "kural"
    if bag.yz_hakki(2):
        try:
            y = bag.gemini([{"text": ALT_SORGU_TALIMAT.format(
                baslik=haber.get("baslik", ""), spot=haber.get("spot", ""), ozne=f"{oz.get('tur', '')} «{oz.get('ad', '')}»",
                ne=(konu.get("ne") or {}).get("ad", "") or "-", olmali=", ".join(gs.gerekli_unsurlar(konu)) or "-",
                olmamali=", ".join(konu.get("gorselde_olmamali") or []) or "-", denenen=", ".join(denenen[:12]) or "-", elenen=elenen)}],
                en_fazla=300)
            sorgular = [gs.temiz(q, 60) for q in (y.get("sorgular") or []) if isinstance(q, str) and gs.temiz(q)]
            kaynak = "yapay zekâ"
        except (gs.KotaHatasi, gs.ServisHatasi):
            sorgular = []
    if not sorgular:      # yedek: konu ayrıştırmasının alternatif sorguları ve somut ipuçları
        sorgular = list(konu.get("alternatif_sorgular_en") or []) + list(konu.get("gorsel_ipucu_en") or [])
    tekrar = {gs.kucult(x) for x in denenen}
    sorgular = [q for q in dict.fromkeys(sorgular) if gs.kucult(q) not in tekrar and not gs.TOPLANTI.search(q)
                and not (oz.get("tur") == "kisi" and gs.INSAN_IPUCU.search(q)) and gs.ayirt_edici(q)][:4]
    if kaynak == "yapay zekâ":
        bag.onbellek_koy(anahtar, {"sorgular": sorgular, "t": gs.simdi_tr().isoformat(timespec="seconds")})
    return sorgular, kaynak


def alternatif_adaylar(bag, haber, konu, kimza, incelenen, gorulen_gk):
    notlar = []
    izin, neden = temsili_izinli(konu)
    if not izin:
        return [], [f"ret sonrası yeni arama yapılmadı: {neden}"]
    sorgular, kaynak = alternatif_sorgular(bag, haber, konu, kimza, incelenen)
    if not sorgular:
        return [], ["ret sonrası denenecek yeni sorgu kalmadı"]
    notlar.append(f"ret sonrası yeni sorgular ({kaynak}): " + ", ".join(sorgular))
    # Bu aramaya ayrı ve sınırlı bir sorgu payı (ücretsiz arama servisleri; Gemini kotası yalnızca incelemede harcanır)
    pay = int(bag.ayar.get("gorsel_alternatif_sorgu", 6))
    bag.sorgu_sayac = min(bag.sorgu_sayac, max(0, int(bag.ayar.get("gorsel_sorgu_butcesi", 12)) - pay))
    goruldu_url = {a["url"] for a in incelenen}
    bulunan = []
    for q in sorgular:
        try:
            liste, n = gs.nesne_adaylari(bag, konu, haber, sorgular=[q], gorulen=goruldu_url, zorla_uygun=True)
        except Exception as e:
            notlar.append(f"yeni arama yapılamadı ({q}): {str(e)[:80]}")
            continue
        bulunan += liste
        goruldu_url |= {a["url"] for a in liste}
        if len(bulunan) >= 8:
            break
    if (konu.get("nerede") or {}).get("ad") and any(a.get("alt_tur") == "yer_konu" for a in incelenen):
        # aynı yer + konu aramasının ilk turda incelenmeyen diğer sonuçları
        liste, n = gs.yer_konu_adaylari(bag, konu, en_fazla=4, gorulen=goruldu_url)
        bulunan = liste + bulunan
    hepsi = []
    for a in bulunan:
        gk = gorsel_kimligi(a["url"])
        if gk in gorulen_gk:
            continue
        gorulen_gk.add(gk)
        a = dict(a, alternatif=True)
        a["sinif"] = sinif_belirle(a, konu)
        a["gk"] = gk
        hepsi.append(a)
    if not hepsi:
        notlar.append("yeni sorgularla incelenmemiş uygun kayıtlı aday bulunamadı")
    return hepsi, notlar


# ----------------------------------------------------------------- ana akış
def _inceleme_turu(bag, haber, konu, kimza, kuyruk, butce, incelenen):
    """Kuyruktan en çok 'butce' adayı hazırlar, inceler ve karar verir. Dönüş: hata metni ('' → sorun yok)."""
    parti = []
    while kuyruk and len(parti) < butce:
        a = kuyruk.pop(0)
        if hazirla(bag, a):
            parti.append(a)
        else:
            incelenen.append(a)
    if not parti:
        return ""
    try:
        bag.sorulan = getattr(bag, "sorulan", 0) + incele_kayitli(bag, haber, konu, kimza, parti)
    except (gs.KotaHatasi, gs.ServisHatasi) as e:
        if isinstance(e, gs.KotaHatasi):
            bag.butce_doldu = True
        for a in parti:
            if not a.get("iliski"):
                a["nedenler"] = [("servis", f"inceleme yapılamadı: {str(e)[:100]}")]
                incelenen.append(a)
        bag.notlar.append(f"{haber.get('baslik', '')[:60]}: {e}")
        parti = [a for a in parti if a.get("iliski")]
        if not parti:
            return str(e)
    for a in parti:
        if a.get("_servis"):
            a["nedenler"] = [("servis", a["_servis"])]
        else:
            a["nedenler"] = karar(a, konu)
            if not a["nedenler"]:
                a["yerlesim"], a["yerlesim_neden"] = yerlesim(a["boyut"][0], a["boyut"][1], a.get("icerik"), a.get("iliski"))
                if not (a["yerlesim"]["kart"] or a["yerlesim"].get("kart_portre") or a["yerlesim"]["manset"] or a["yerlesim"]["detay"]):
                    a["nedenler"] = [("yerlesim", "hiçbir alan için uygun değil: " + "; ".join(sorted(set(a["yerlesim_neden"].values())))[:200])]
        incelenen.append(a)
    return ""


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
    bag.sorulan = 0
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
    gorulen_gk = {a["gk"] for a in adaylar}
    butce = max(1, int(bag.ayar.get("gorsel_aday_butcesi", 5)))
    incelenen, kabul, hata = [], [], ""
    kuyruk = list(adaylar)
    for _ in range(max(1, int(bag.ayar.get("gorsel_kontrol_turu", 2)))):
        if not kuyruk:
            break
        hata = _inceleme_turu(bag, haber, konu, kimza, kuyruk, butce, incelenen)
        kabul = [a for a in incelenen if not a.get("nedenler")]
        if kabul or hata:
            break
    alt_sayi = 0
    if not kabul and not hata and bag.ayar.get("gorsel_alternatif_arama", True):
        # A2: ilk adaylar elendiyse (ya da hiç aday yoksa) ret nedenlerine göre, ana özneyi koruyan yeni sorgularla bir tur daha
        yeni, n2 = alternatif_adaylar(bag, haber, konu, kimza, incelenen, gorulen_gk)
        rapor["notlar"] += n2
        alt_sayi = len(yeni)
        if yeni:
            hata = _inceleme_turu(bag, haber, konu, kimza, yeni, butce, incelenen)
            kabul = [a for a in incelenen if not a.get("nedenler")]
    for a in incelenen:
        a.pop("_b64", None)
    rapor["adaylar"] = [{k: a.get(k) for k in ("sinif", "tur", "alt_tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "hedef", "cekim",
                                               "nedenler", "teknik", "teknik_bayrak", "icerik", "iliski", "yerlesim", "yerlesim_neden", "boyut",
                                               "mevcut", "kutuphane_id", "dogrulama", "alternatif", "onbellekten", "sinyal")} for a in incelenen]
    rapor["sorgular"] = list(getattr(bag, "sorgu_kaydi", []))[:60]
    rapor["elenen_kayitlar"] = list(getattr(bag, "elenen", []))[:30]
    rapor["havuz"] = {s: sum(1 for a in adaylar if a["sinif"] == s) for s in SINIF_SIRA}
    rapor["havuz"]["ret_sonrasi"] = alt_sayi
    rapor["gemini_sorulan"] = bag.sorulan
    if mevcut:
        rapor["eski"] = {"karar": "kabul" if not mevcut.get("nedenler") and mevcut in kabul else ("red" if mevcut.get("nedenler") else ""),
                         "gerekce": "; ".join(n for _, n in mevcut.get("nedenler") or []) or (mevcut.get("iliski") or {}).get("gerekce", ""),
                         "nedenler": mevcut.get("nedenler")}
    print(f"   görsel ({HAT_SURUM}): {len(adaylar)}+{alt_sayi} aday, {len(incelenen)} incelendi ({bag.sorulan} yeni soru), {len(kabul)} kabul, "
          f"{time.time() - t0:.0f} sn" + (f" ({hata[:80]})" if hata else ""))
    for a in incelenen:      # kabul edilmeyen kurum fotoğrafı sitede tutulmaz
        if a.get("nedenler") and a["url"].startswith("/images/kurum/") and not a.get("mevcut"):
            gs.yerel_gorsel_sil(a["url"])
    if kabul:
        kabul.sort(key=lambda a: (SINIF_SIRA.get(a["sinif"], 0), yerlesim_puani(a["yerlesim"]), (a.get("iliski") or {}).get("uygunluk", 0),
                                  1 if a.get("mevcut") else 0), reverse=True)
        s = secim_yap(bag, kabul[0], kimza, konu)
        s["arama_surum"] = ARAMA_SURUM
        return s, rapor
    k = {"yapildi": bool(incelenen) and not hata, "sonuc": "adaylar reddedildi" if incelenen else "aday yok", "tarih": simdi,
         "yontem": HAT_SURUM, "model": bag.son_model or ""}
    servis = [a for a in incelenen if any(kat == "servis" for kat, _ in (a.get("nedenler") or []))]
    if hata or servis:
        # Servis/kota hatası içerik reddi değildir: kontrol edilemeyen aday reddedilmiş sayılmaz, haber sonra yeniden denenir
        # ve mevcut fotoğraf korunur.
        neden = hata or "; ".join(sorted({n for a in servis for kat, n in a["nedenler"] if kat == "servis"}))
        k.update(yapildi=False, sonuc="yapılamadı", neden=neden[:160])
        return kapak(haber, kimza, f"İnceleme tamamlanamadı ({neden[:120]}); kontrol edilmemiş fotoğraf kullanılmadı.", k, "kontrol_yapilamadi",
                     neden, bekliyor=True), rapor
    if bag.arama_hatasi and not incelenen:
        return kapak(haber, kimza, "Fotoğraf araması hata verdi; sonra yeniden denenecek.", k, "arama_hatasi",
                     "; ".join(rapor["notlar"])[:300], bekliyor=True), rapor
    if incelenen:
        ozet = "; ".join(sorted({n for a in incelenen for _, n in (a.get("nedenler") or [])}))
        s = kapak(haber, kimza, f"{len(incelenen)} aday kontrolden geçemedi; fotoğrafsız.", k, "gorsel_reddedildi", ozet)
    else:
        s = kapak(haber, kimza, "Haberin öznesine ya da konusuna ait kullanım hakkı doğrulanmış fotoğraf bulunamadı; fotoğrafsız.", k,
                  "aday_bulunamadi", "; ".join(rapor["notlar"])[:300])
    s["arama_surum"] = ARAMA_SURUM
    return s, rapor
