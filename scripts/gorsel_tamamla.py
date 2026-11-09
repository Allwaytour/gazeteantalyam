"""Her habere görsel (t1). Yapay zekâ isteği yapılmaz; deneysel arama sürümünün kapalı olması bu adımı engellemez.

Sıra: (1) haberin kendi onaylı fotoğrafı korunur; (2) doğrulanmış görsel arşivinden (data/gorsel_arsiv.yml) haberin kişisi,
kurumu/takımı, konusu ya da gerçekten ilgili yeri kimlik ve kullanım bağlamıyla eşleştirilir; (3) hiçbiri yoksa haberin somut
başlığıyla bize ait tipografik haber görseli üretilir (gerçek kişi ya da olay fotoğrafı uydurulmaz, yalnızca kategori adı yazan
genel kapak kullanılmaz). Her otomatik habere ayrıca tipografik yedek görsel (gorsel_yedek) üretilir: ana sayfada aynı fotoğraf
iki habere düşerse ikinci haber bu yedekle gösterilir, fotoğrafsız kart kalmaz.

Güvenlik: ret kaydındaki (bilinen yanlış) fotoğraflar ve editör incelemesinde yanlış bulunanlar arşivden hiçbir habere verilmez.
Hassas haberlerde (kaza, suç, ölüm vb.) yalnızca başlıkta adı geçen kişi/kurum/yer kaydı kullanılır; konu ve genel kent
fotoğrafı kullanılmaz (başka bir olay ya da kişiyle ilişkilendirme olmasın). Başlıkta şehir adı geçmesi tek başına yer fotoğrafı
için yeterli değildir: yer kayıtları haberin konusu o yerin kendisiyse (turizm, nüfus, kent ekonomisi…) kullanılır."""
import hashlib
import json
import math
import os
import re

import requests
import yaml

import gorsel_sec as gs
import gorsel_hat as gh

TAMAMLA_SURUM = "t1"
KOK = gs.KOK
ARSIV_DOSYA = os.path.join(KOK, "data", "gorsel_arsiv.yml")
INCELEME_DOSYA = os.path.join(KOK, "data", "gorsel_inceleme.yml")
CIKTI = os.path.join(KOK, "static", "images", "haber-gorsel")
WEB = "/images/haber-gorsel/"
FONT = os.path.join(KOK, "scripts", "Mulish.ttf")   # Mulish (SIL OFL 1.1), sitenin yazı tipi
LOGO = os.path.join(KOK, "static", "images", "logo-beyaz.png")
UA = {"User-Agent": "GazeteAntalyaBot/1.0 (https://www.gazeteantalyam.com/iletisim/)"}
HASSAS_RE = re.compile(r"kaza(?!n)|çarpış|carpis|devril|yaral|öldü|ölü bul|hayatını kaybet|hayatini kaybet|cinayet|bıçak|bicak|silah|tabanca|"
                       r"gözaltı|gozalti|tutuklan|istismar|taciz|intihar|boğul|bogul|yangın|yangin|dolandırıc|dolandiric|uyuşturucu|"
                       r"uyusturucu|operasyon|şüpheli|supheli|darp|kavga|cenaze|ceset|cesed|saldır|saldir|su altında|taşkın|sel felaket|selde|hortum")
_TR = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def kat(metin):
    """Karşılaştırma için küçük harf ve Türkçe karakter katlama (başlıkların bir kısmı ASCII yazılıyor)."""
    m = (metin or "").replace("İ", "i").replace("I", "ı").lower().replace("’", "'").replace("‘", "'")
    return m.translate(_TR)


def _gecer(anahtar, metin, tam=False):
    a = kat(anahtar)
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(a) + (r"(?![a-z0-9])" if tam else ""), metin))


# ----------------------------------------------------------------- arşiv
_ARSIV = None


def _yasakli_gorseller():
    """Bilinen yanlış fotoğraflar: ret kaydı (her düzey) ve editör incelemesinde 'yanlis' kararı verilenler."""
    ys = set()
    r = gh.ret_kaydi()
    for k in r.get("gorsel") or []:
        ys.add(k.get("gk") or "")
    for liste in (r.get("eslesme") or {}).values():
        for k in liste:
            ys.add(k.get("gk") or "")
    try:
        with open(INCELEME_DOSYA, encoding="utf-8") as f:
            for k in (yaml.safe_load(f) or {}).get("kararlar") or []:
                if k.get("karar") == "yanlis" and k.get("gorsel"):
                    ys.add(gh.gorsel_kimligi(k["gorsel"]))
    except Exception:
        pass
    ys.discard("")
    return ys


def arsiv():
    """data/gorsel_arsiv.yml kayıtları; 'kutuphane' alanı olan kayıtların görsel ve kaynak bilgisi doğrulanmış görsel kütüphanesinden okunur."""
    global _ARSIV
    if _ARSIV is not None:
        return _ARSIV
    try:
        with open(ARSIV_DOSYA, encoding="utf-8") as f:
            ham = (yaml.safe_load(f) or {}).get("kayitlar") or []
    except Exception as e:
        print("Görsel arşivi okunamadı:", e)
        ham = []
    kut = gh.kutuphane()
    yasak = _yasakli_gorseller()
    out = []
    for k in ham:
        k = dict(k)
        if k.get("kutuphane"):
            kk = (kut.get("kayitlar") or {}).get(k["kutuphane"])
            if not kk:
                continue
            kay = kk.get("kaynak") or {}
            k.setdefault("url", kk.get("url"))
            k.setdefault("boyut", kk.get("boyut"))
            k.setdefault("sayfa", kay.get("sayfa", ""))
            k.setdefault("lisans", kay.get("lisans", ""))
            k.setdefault("lisans_dayanagi", kay.get("dayanak", ""))
            k.setdefault("fotografci", kay.get("sahibi") or kay.get("atif", ""))
            k.setdefault("yil", (kk.get("cekim_tarihi") or "")[:4])
            ic = (kut.get("inceleme") or {}).get(gh.gorsel_kimligi(k["url"])) or {}
            k.setdefault("icerik", {x: (ic.get("icerik") or {}).get(x) for x in ("ana_kutu", "yuzler")})
        if not k.get("url") or gh.gorsel_kimligi(k["url"]) in yasak:
            continue
        out.append(k)
    _ARSIV = out
    return out


def hassas_mi(v):
    m = (v.get("title", "") + " " + (v.get("ozet") or "")).replace("İ", "i").replace("I", "ı").lower()
    return (v.get("kategoriler") or [""])[0] == "Asayiş" or bool(HASSAS_RE.search(m))


def esle(v, govde=""):
    """Haberle arşiv kaydı eşleştirmesi. Dönüş: (kayıt, açıklama) ya da (None, neden)."""
    baslik, spot = kat(v.get("title", "")), kat(v.get("ozet") or "")
    metin = baslik + " " + spot
    kategori = (v.get("kategoriler") or [""])[0]
    hassas = hassas_mi(v)
    adaylar = []
    for k in arsiv():
        if k.get("kategoriler") and kategori not in k["kategoriler"]:
            continue
        if kategori in (k.get("yasak_kategoriler") or []):
            continue
        if k.get("yerel") and any(_gecer(il, baslik, True) for il in gh.TR_ILLERI if il not in ("Antalya", "Burdur", "Isparta")):
            continue          # Antalya'ya özgü görsel (ör. Antalya piyazı, Antalya seraları) başka ilin haberine verilmez
        # Kişi, kurum, takım, tesis ve yer kayıtları için ad başlıkta geçmeli (haberin ana öznesi); konu kayıtları spotta da olabilir
        baslikta = k.get("baslikta", k.get("sinif") not in ("konu",))
        if any(_gecer(y, metin) for y in k.get("yasak") or []):
            continue
        tam = bool(k.get("tam"))
        skor = 0
        for a in k.get("anahtar") or []:
            if _gecer(a, baslik, tam):
                skor = max(skor, 3)
            elif _gecer(a, spot, tam):
                skor = max(skor, 2)
        if skor < (3 if baslikta else 2):
            continue
        if k.get("gerekli") and not any(_gecer(g, baslik if baslikta else metin) for g in k["gerekli"]):
            continue
        if hassas and (k.get("sinif") not in ("kisi", "kurum", "takim", "logo", "tesis") or skor < 3 or not k.get("hassas_uygun", True)):
            continue
        if k.get("genel"):
            skor -= 0.5
        adaylar.append((-skor, float(k.get("oncelik", 6)), k))
    if not adaylar:
        return None, "arşivde eşleşen kayıt yok" + (" (hassas haber: yalnızca başlıktaki kişi/kurum/yer kaydı)" if hassas else "")
    adaylar.sort(key=lambda x: (x[0], x[1]))
    en = adaylar[0][2]
    grup = [k for s, o, k in adaylar if (s, o) == adaylar[0][:2] and k.get("grup") and k.get("grup") == en.get("grup")] or [en]
    secilen = grup[int(hashlib.md5((v.get("title", "")).encode()).hexdigest(), 16) % len(grup)]
    return secilen, f"arşiv kaydı '{secilen.get('id')}' ({secilen.get('kimlik', '')}) başlık/spot ile eşleşti"


# ----------------------------------------------------------------- tipografik görsel
def _font(boy, kalinlik):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(FONT, boy)
        try:
            f.set_variation_by_axes([kalinlik])
        except Exception:
            pass
        return f
    except Exception:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", boy)


def _sar(d, metin, f, gen):
    sat, cur = [], ""
    for k in metin.split():
        t = (cur + " " + k).strip()
        if d.textlength(t, font=f) <= gen:
            cur = t
        else:
            if cur:
                sat.append(cur)
            cur = k
    if cur:
        sat.append(cur)
    return sat


def tipografik_uret(baslik, kategori, ilce, yol):
    """1200×750 marka görseli: kategori · ilçe, haberin başlığı ve logomuz. Metin, kart/manşet/yan kart kırpmalarında görünür kalan
    güvenli alandadır (x 110–1090, y 150–630)."""
    from PIL import Image, ImageDraw
    W, H = 1200, 750
    im = Image.new("RGBA", (W, H), "#22343C")
    k = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dk = ImageDraw.Draw(k)
    cx, cy = 1110, 60
    for i in range(9):
        a = math.radians(100 + i * 18)
        dk.line([(cx + 70 * math.cos(a), cy + 70 * math.sin(a)), (cx + 150 * math.cos(a), cy + 150 * math.sin(a))], fill=(208, 86, 43, 150), width=12)
    dk.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=(208, 86, 43, 150))
    dk.rectangle([0, H - 14, W, H], fill=(208, 86, 43, 255))
    im = Image.alpha_composite(im, k).convert("RGB")
    d = ImageDraw.Draw(im)
    x0, x1 = 110, 1090
    d.rectangle([x0, 150, x0 + 8, 188], fill="#D0562B")
    ust = (kategori or "").upper().replace("i", "İ") + (("  ·  " + ilce.upper().replace("i", "İ")) if ilce else "")
    d.text((x0 + 24, 150), ust, font=_font(30, 850), fill="#F08A5D")
    for boy in (84, 78, 72, 66, 60, 56, 52, 48, 44):
        f = _font(boy, 950)
        sat = _sar(d, baslik, f, x1 - x0)
        lh = int(boy * 1.15)
        if len(sat) <= 4 and len(sat) * lh <= 318:
            break
    sat = sat[:4]
    y = 208 + (318 - len(sat) * lh) // 2
    for s in sat:
        d.text((x0, y), s, font=f, fill="#FFFFFF")
        y += lh
    try:
        logo = Image.open(LOGO).convert("RGBA")
        lh_ = 86
        logo = logo.resize((int(logo.width * lh_ / logo.height), lh_), Image.LANCZOS)
        im.paste(logo, (x0, 630 - lh_), logo)
    except Exception as e:
        print("Logo eklenemedi:", e)
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    im.save(yol, "JPEG", quality=82, optimize=True, progressive=True)


def logo_uret(kayit, yol):
    """Logo kırpılmaz ve esnetilmez: sade zeminde, kenarlarda boşluk bırakılarak 1200×750 karenin ortasına yerleştirilir."""
    from PIL import Image
    import io
    url = kayit["url"]
    m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2}/)([^/?]+\.svg)$", url, re.I)
    if m:      # SVG logo: Commons'un PNG'ye çevrilmiş 1000 px kopyası
        url = f"{m.group(1)}thumb/{m.group(2)}{m.group(3)}/1000px-{m.group(3)}.png"
    r = requests.get(url, headers=UA, timeout=40)
    r.raise_for_status()
    lg = Image.open(io.BytesIO(r.content)).convert("RGBA")
    W, H = 1200, 750
    zemin = Image.new("RGBA", (W, H), kayit.get("zemin") or "#FFFFFF")
    oran = min(760 / lg.width, 360 / lg.height)
    lg = lg.resize((max(1, int(lg.width * oran)), max(1, int(lg.height * oran))), Image.LANCZOS)
    zemin.alpha_composite(lg, ((W - lg.width) // 2, (H - lg.height) // 2 - 10))
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    zemin.convert("RGB").save(yol, "JPEG", quality=88, optimize=True)


def _tipo_yerlesim(url):
    o = "50% 50%"
    return {"boyut": [1200, 750], "dikey": False, "surum": gh.YERLESIM_SURUM, "manset": "kaplama", "yan": True, "kart": True, "detay": True,
            "kart_portre": False, "yan_portre": False, "tipografik": True, "url": url,
            **{"o_" + a: o for a in ("manset_d", "manset_m", "yan_d", "yan_t", "yan_m", "kart")}}


def tipo_yolu(v, dosya):
    imza = hashlib.md5(((v.get("title") or "") + "|" + ((v.get("kategoriler") or [""])[0]) + "|" + ((v.get("ilceler") or [""])[0]) + "|" + TAMAMLA_SURUM).encode()).hexdigest()[:8]
    ad = re.sub(r"[^a-z0-9-]", "", dosya[:-3])[:70] + "-" + imza + ".jpg"
    return os.path.join(CIKTI, ad), WEB + ad


def tipo_hazirla(v, dosya):
    yol, web = tipo_yolu(v, dosya)
    if not os.path.exists(yol):
        tipografik_uret(v.get("title", ""), (v.get("kategoriler") or ["Gündem"])[0], (v.get("ilceler") or [""])[0], yol)
    return web


# ----------------------------------------------------------------- uygulama
def _foto_var(v):
    g = v.get("gorsel") or ""
    s = v.get("gorsel_secim") or {}
    return bool(g) and not g.startswith("/images/kategori/") and s.get("tur") not in ("tipografik",) and not g.startswith(WEB)


def _arsiv_yerlesim(k):
    w, h = (k.get("boyut") or [0, 0])[:2]
    if k.get("sahne") == "logo":
        return None
    ic = dict(k.get("icerik") or {})
    ic.setdefault("ana_kutu", k.get("ana_kutu"))
    ic.setdefault("yuzler", k.get("yuzler") or [])
    y, _ = gh.yerlesim(int(w), int(h), ic, None)
    return y


def arsiv_uygula(v, k, kimza, neden):
    """Arşiv kaydını habere yazar (onaylı; kaynak, lisans ve arşiv bağlamı haber detayında fotoğrafın altında)."""
    if k.get("sahne") == "logo":
        ad = "logo-" + re.sub(r"[^a-z0-9-]", "", k["id"]) + ".jpg"
        yol = os.path.join(CIKTI, ad)
        if not os.path.exists(yol):
            logo_uret(k, yol)
        gorsel = WEB + ad
        y = _tipo_yerlesim(gorsel)
        y["tipografik"] = False
        y["logo"] = True
    else:
        gorsel = k["url"]
        y = _arsiv_yerlesim(k)
        if not y or not (y.get("kart") or y.get("kart_portre")):
            return False
        y = dict(y, url=gorsel)
    kredi = f"Arşiv fotoğrafı: {k.get('fotografci', '')} / Wikimedia Commons ({k.get('lisans', '')})" if "wikimedia" in k["url"] else \
        f"Fotoğraf: {k.get('fotografci', '')}"
    if k.get("sahne") == "logo":
        kredi = f"Logo: {k.get('fotografci') or k.get('kimlik', '')} / Wikimedia Commons ({k.get('lisans', '')})"
    eski = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    gecmis = v.get("gorsel_gecmis") if isinstance(v.get("gorsel_gecmis"), list) else []
    if _foto_var(v):
        return False
    v["gorsel"], v["gorsel_kredi"], v["gorsel_tur"] = gorsel, kredi, "arsiv"
    v["gorsel_secim"] = {
        "tur": "arsiv", "alt_tur": "arsiv_eslesme", "sinif": k.get("sinif", ""), "gorsel_url": gorsel, "konu_imza": kimza,
        "kaynak_sayfa": k.get("sayfa", ""), "fotografci": k.get("fotografci", ""), "lisans": k.get("lisans", ""),
        "lisans_dayanagi": k.get("lisans_dayanagi", ""), "gosterilen": k.get("kimlik", ""), "cekim_yili": str(k.get("yil") or ""),
        "aciklama": k.get("aciklama", ""), "gerekce": neden, "durum": "onaylandi", "hat": "t1",
        "onay": {"yontem": "doğrulanmış görsel arşivi (kimlik ve kullanım bağlamı eşleşmesi)", "arsiv_id": k.get("id"),
                 "tarih": gs.simdi_tr().isoformat(timespec="seconds")},
        "kural_denetim": gh.KABUL_KURALI, "kabul_kurali": gh.KABUL_KURALI, "surum": gs.KURAL_SURUMU,
        "onceki_durum": {x: eski.get(x) for x in ("tur", "durum", "kapak_nedeni") if eski.get(x)}}
    v["gorsel_yerlesim"] = y
    v.pop("gorsel_konum", None)
    v["gorsel_gecmis"] = gecmis[-4:]
    return True


def tipo_uygula(v, web, kimza, neden):
    eski = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    v["gorsel"], v["gorsel_kredi"], v["gorsel_tur"] = web, "Görsel: Gazete Antalya", "tipografik"
    v["gorsel_secim"] = {"tur": "tipografik", "gorsel_url": web, "konu_imza": kimza, "durum": "onaylandi", "hat": "t1",
                         "gerekce": neden, "aciklama": "", "kural_denetim": gh.KABUL_KURALI, "surum": gs.KURAL_SURUMU,
                         "onceki_durum": {x: eski.get(x) for x in ("tur", "durum", "kapak_nedeni") if eski.get(x)}}
    v["gorsel_yerlesim"] = _tipo_yerlesim(web)
    v.pop("gorsel_konum", None)


def tamamla(dosyalar=None, oncelikli=(), sinir=10000):
    """Otomatik haberlerin görselini tamamlar. Elle seçilmiş görsellere dokunulmaz; onaylı fotoğraf korunur.
    Dönüş: {'arsiv': n, 'tipografik': n, 'yedek': n, 'korundu': n}."""
    tum = dosyalar or sorted(os.listdir(gs.HABER_KLASOR), reverse=True)
    sira = [a for a in oncelikli if a in tum] + [a for a in tum if a not in oncelikli]
    say = {"arsiv": 0, "tipografik": 0, "yedek": 0, "korundu": 0, "arsive_gecti": 0}
    for ad in sira[:sinir]:
        yol = os.path.join(gs.HABER_KLASOR, ad)
        try:
            v, govde = gs.md_oku(yol)
            oto, _ = gs.otomatik_gorsel_mi(v)
            if not oto:
                continue
            degisti = False
            web = tipo_hazirla(v, ad)
            if v.get("gorsel_yedek") != web:
                eski_yedek = v.get("gorsel_yedek")
                v["gorsel_yedek"] = web
                degisti = True
                say["yedek"] += 1
                if eski_yedek and eski_yedek.startswith(WEB) and eski_yedek != web:
                    try:
                        os.remove(os.path.join(KOK, "static", eski_yedek.lstrip("/")))
                    except OSError:
                        pass
            s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
            kimza = s.get("konu_imza") or gs.konu_imzasi(v.get("title"), v.get("ozet"), (v.get("kategoriler") or ["Gündem"])[0])
            if _foto_var(v):
                say["korundu"] += 1
            else:
                k, neden = esle(v, govde)
                if k and s.get("tur") == "arsiv" and (s.get("onay") or {}).get("arsiv_id") == k.get("id"):
                    pass                                   # aynı arşiv kaydı zaten uygulanmış
                else:
                    uygulandi = False
                    if k:
                        try:
                            uygulandi = arsiv_uygula(v, k, kimza, neden)
                        except Exception as e:             # logo indirilemedi vb.: haber görselsiz kalmaz
                            print("   Arşiv görseli uygulanamadı:", ad, k.get("id"), e)
                            neden = f"arşiv kaydı '{k.get('id')}' uygulanamadı"
                        if not uygulandi and not neden.startswith("arşiv kaydı '"):
                            neden = f"arşiv kaydı '{k.get('id')}' bu alanlara yerleşmiyor"
                    if uygulandi:
                        say["arsiv" if s.get("tur") != "tipografik" else "arsive_gecti"] += 1
                        degisti = True
                    elif v.get("gorsel") != web:
                        tipo_uygula(v, web, kimza, "Uygun doğrulanmış fotoğraf ya da logo yok (" + neden + "); bize ait tipografik haber görseli.")
                        say["tipografik"] += 1
                        degisti = True
            if degisti:
                gs.md_yaz(yol, v, govde)
        except Exception as e:
            print("Görsel tamamlanamadı:", ad, e)
    print(f"Görsel tamamlama ({TAMAMLA_SURUM}): arşivden {say['arsiv']} (+{say['arsive_gecti']} tipografikten arşive), tipografik "
          f"{say['tipografik']}, yedek üretilen {say['yedek']}, kendi fotoğrafı korunan {say['korundu']}")
    return say
