#!/usr/bin/env python3
"""Yayımlanmış haberlerin tek seferlik kalite taraması (k1/b2). İki aşama:

  rapor   Haberler (yeniden eskiye) yeni kurallardan geçirilir. Haber dosyalarına DOKUNULMAZ; öneriler
          data/toplu_kalite/oneriler.json'a yazılır ve sitede /kalite-raporu/ sayfasında iki grupta görünür:
            toplu  : yalnızca mekanik düzeltmeler (Türkçe karakter, kesme işareti, başlık düzeni, görsel altındaki kaynak satırı,
                     kelime ortasından kesilmiş adres) — toplu onaylanabilir;
            tek    : görseli değişen ya da uygun görsel bulunamadığı için yayından kaldırılması önerilen haberler, karantina
                     önerileri, anlam düzeyinde yazım düzeltmeleri, kişi fotoğrafı içerenler; ölüm, suç, intihar ve çocuk haberleri.
          Mevcut görsel yalnızca yeni kurallara uyan bir kaynaktan geliyorsa (kurumun aynı habere ait fotoğrafı, Pixabay, CC0/kamu malı
          Commons) ve görüntü puanı eşiği geçiyorsa korunur; Unsplash, CC BY/BY-SA Commons, başlık kartı ve kelime eşleşmesiyle verilmiş
          arşiv görselleri yeniden aranır. Yapay zekâ kotası için çalışma başına bütçe vardır; kalan haberler sonraki çalışmalarda işlenir.
  uygula  Onaylanan öneriler haber dosyalarına yazılır: --onay toplu (toplu grubun tamamı) ya da dosya adları (virgülle).

Test: --dosyalar ile yalnızca verilen haberler; --hep-ara ile mevcut görsel uygun olsa da yeni görsel aranır (data/kalite_test.json).
"""
import argparse
import base64
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402

import gorsel_sec as gs  # noqa: E402
import gorsel_v2 as g2  # noqa: E402
import haber_cek as hc  # noqa: E402
import kalite  # noqa: E402

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = timezone(timedelta(hours=3))
CIKTI = os.path.join(KOK, "data", "toplu_kalite", "oneriler.json")
TOPLU_SURUM = "t2"
EDITOR_SORUN = ("ascii", "ascii_metin", "bitisik", "ek_uyum", "ek_ayri", "intihar_yontemi")

PUAN_MEVCUT = """Sen bir Türk haber sitesinin deneyimli foto editörüsün. Aşağıda birkaç haber ve her haberde KULLANILAN TEK fotoğraf var.
Her fotoğrafı yalnızca KENDİ haberine göre değerlendir (ölçüt ve kesin ret kodları aşağıda).
""" + g2.PUAN_TALIMAT.split("Her aday için:", 1)[1].split("Yalnızca şu biçimde", 1)[0] + """
Yalnızca şu biçimde JSON döndür: {"haberler": [{"no": 1, "puan": 0, "ihlaller": [], "donem": "guncel", "tahmini_yil": "", "taninir_yuz": false, "gorunen": "...", "gerekce": "..."}]}
"""


def simdi():
    return datetime.now(TR).isoformat(timespec="seconds")


def oku(yol=CIKTI):
    try:
        with open(yol, encoding="utf-8") as f:
            r = json.load(f)
        if r.get("surum") == TOPLU_SURUM:
            return r
    except Exception:
        pass
    return {"surum": TOPLU_SURUM, "oneriler": {}, "ozet": {}}


def yaz(r, yol=CIKTI):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    o = list(r.get("oneriler", {}).values())
    gk = lambda x: (x.get("gorsel") or {}).get("karar")  # noqa: E731
    r["ozet"] = {"guncelleme": simdi(), "islenen": len(o), "toplu_grup": sum(1 for x in o if x.get("grup") == "toplu" and x.get("degisiklik_var")),
                 "tek_tek_grup": sum(1 for x in o if x.get("grup") == "tek"), "degisiklik_yok": sum(1 for x in o if not x.get("degisiklik_var")),
                 "gorsel_yenilenen": sum(1 for x in o if gk(x) == "yenilendi"), "yayindan_kaldirilacak": sum(1 for x in o if gk(x) == "kaldir"),
                 "gorsel_korunan": sum(1 for x in o if gk(x) == "korundu"), "karantina_onerisi": sum(1 for x in o if x.get("engeller")),
                 "adres_degisen": sum(1 for x in o if x.get("adres")), "bekleyen": sum(1 for x in o if x.get("bekliyor")),
                 "uygulanan": sum(1 for x in o if x.get("durum") == "uygulandi")}
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)


class Butce:
    def __init__(self, n):
        self.kalan = n

    def al(self, n=1):
        if self.kalan < n or hc.GUNLUK_KOTA["bitti"]:
            return False
        self.kalan -= n
        return True


def gemini(metin=None, parcalar=None, en_fazla=6144, sicaklik=0.0):
    return hc.gemini_json(metin, sicaklik, en_fazla, parcalar=parcalar)


# ----------------------------------------------------------------- metin
def _mekanik(a, b):
    """Değişiklik yalnızca harf işareti, büyük/küçük harf, kesme işareti ya da boşluk mu?"""
    n = lambda s: re.sub(r"[\s'’‘]", "", kalite.katla(s if isinstance(s, str) else " ".join(s or [])))  # noqa: E731
    return n(a) == n(b)


def metin_onerisi(v, govde, butce):
    alanlar = {"baslik": v.get("title", ""), "spot": v.get("ozet", ""), "metin": govde.strip(), "etiketler": list(v.get("etiketler") or [])}
    kalite.sozluk_hazirla()
    s0 = kalite.sorunlar(alanlar)
    editor_gerek = any(k in d for d in s0.values() for k in EDITOR_SORUN)
    fn = (lambda m: gemini(m)) if editor_gerek and butce.al() else None
    duz, kr = kalite.denetle(alanlar, fn)
    if editor_gerek and not fn:
        return None, {"bekliyor": "editör için yapay zekâ bütçesi kalmadı"}, True
    degisen, mekanik = {}, True
    for a, alan in (("baslik", "title"), ("spot", "ozet"), ("metin", "govde"), ("etiketler", "etiketler")):
        once = alanlar[a]
        if duz.get(a) != once:
            degisen[alan] = {"once": once, "sonra": duz[a]} if a != "metin" else {"sonra": duz[a]}
            mekanik = mekanik and _mekanik(once, duz[a])
    return degisen, kr, mekanik


def adres_onerisi(ad, v):
    if v.get("url") or not re.match(r"\d{8}-.+-[0-9a-f]{5}\.md$", ad):
        return None
    parca = ad[9:-9]
    tam = hc.slug(v.get("title", ""), 999)
    if tam.startswith(parca) and len(tam) > len(parca) and tam[len(parca)] != "-" and "-" in parca:
        return {"once": f"/haber/{ad[:-3]}/", "sonra": f"/haber/{ad[:9]}{parca.rsplit('-', 1)[0]}{ad[-9:-3]}/"}
    return None


# ----------------------------------------------------------------- görsel
def foto_mu(v):
    g = v.get("gorsel") or ""
    return bool(g) and v.get("gorsel_tur") not in ("tipografik", "kategori") and "/images/kategori/" not in g


def kaynak_uygun(v, oturum, notlar):
    """Mevcut görselin kaynağı yeni kurallara uyuyor mu? Dönüş: (uygun, kaynak türü, açıklama)."""
    g = v.get("gorsel") or ""
    s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    if s.get("hat") == g2.SURUM:
        return True, (v.get("gorsel_kanit") or {}).get("kaynak", "b2"), "b2 kurallarıyla seçilmiş"
    if g.startswith("/images/kurum/") and v.get("gorsel_tur") == "gercek":
        return True, "kurum", "kurumun aynı habere ait fotoğrafı"
    if "unsplash.com" in g:
        return False, "unsplash", "Unsplash (kullanılmıyor)"
    if "pexels.com" in g:
        return False, "pexels", "Pexels (kullanılmıyor)"
    m = re.search(r"upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/?#]+)", g)
    if m:
        ad = requests.utils.unquote(m.group(1))
        try:
            j = g2._cm(oturum, {"action": "query", "titles": "File:" + ad, "prop": "templates", "tlnamespace": 10, "tllimit": "max"})
            sab = [t.get("title", "") for p in (j.get("query") or {}).get("pages") or [] for t in p.get("templates") or []]
            ok, neden = g2.commons_lisans(sab)
            return ok, "commons", neden
        except Exception as e:
            notlar.append(f"Commons lisansı okunamadı: {e}"[:120])
            return False, "commons", "lisans okunamadı"
    if "/haber-gorsel/logo-" in g:
        return False, "logo", "kurum logosu (kaynağı/izni ayrıca belirlenmemiş)"
    return False, "diger", "kaynağı ya da kullanım izni yeni kurallara göre belirlenemedi"


def mevcut_indir(oturum, url):
    if url.startswith("/"):
        with open(os.path.join(KOK, "static", url.lstrip("/")), "rb") as f:
            veri = f.read()
    else:
        u = re.sub(r"/\d+px-", "/480px-", url) if "/thumb/" in url else url
        r = oturum.get(u, timeout=25)
        r.raise_for_status()
        veri = r.content
    from PIL import Image
    im = Image.open(io.BytesIO(veri)).convert("RGB")
    im.thumbnail((512, 512))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


def mevcut_puanla(gruplar, butce):
    sonuc = {}
    for i in range(0, len(gruplar), 6):
        parti = gruplar[i:i + 6]
        if not butce.al():
            break
        parcalar = [{"text": PUAN_MEVCUT}]
        for n, (ad, v, veri) in enumerate(parti, 1):
            parcalar.append({"text": f"\nHABER {n}\nBAŞLIK: {v.get('title', '')}\nSPOT: {v.get('ozet', '')}\nKATEGORİ: {(v.get('kategoriler') or [''])[0]}\nFOTOĞRAF {n}:"})
            parcalar.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(veri).decode()}})
        try:
            y = gemini(parcalar=parcalar)
        except Exception as e:
            print("Mevcut görsel puanlanamadı:", e)
            continue
        for p in (y or {}).get("haberler") or []:
            try:
                sonuc[parti[int(p.get("no")) - 1][0]] = p
            except Exception:
                pass
    return sonuc


def brief_al(haberler, butce):
    out = {}
    for i in range(0, len(haberler), 8):
        parti = haberler[i:i + 8]
        if not butce.al():
            break
        try:
            y = gemini(g2.brief_istegi([{"baslik": v.get("title", ""), "spot": v.get("ozet", ""), "metin": g} for _, v, g in parti]))
        except Exception as e:
            print("Brief alınamadı:", e)
            continue
        for n, (ad, v, _) in enumerate(parti, 1):
            out[ad] = g2.brief_duzelt((y or {}).get(str(n)), {"baslik": v.get("title", ""), "spot": v.get("ozet", "")})
    return out


def tekrar_haritasi():
    h = {}
    for ad in sorted(os.listdir(gs.HABER_KLASOR)):
        try:
            v, _ = gs.md_oku(os.path.join(gs.HABER_KLASOR, ad))
        except Exception:
            continue
        if foto_mu(v):
            h.setdefault(g2.gorsel_id(v["gorsel"]), []).append(ad)
    return h


def _tarih(v):
    try:
        return datetime.fromisoformat(str(v.get("date"))).date()
    except Exception:
        return None


# ----------------------------------------------------------------- rapor
def rapor(args):
    yol = args.cikti or CIKTI
    r = oku(yol)
    butce = Butce(args.butce)
    oturum = requests.Session()
    oturum.headers["User-Agent"] = g2.UA
    kullanim = g2.Kullanim(gs.HABER_KLASOR)
    tekrar = tekrar_haritasi()
    onbellek = {}
    if args.dosyalar:
        sira = [d.strip() for d in args.dosyalar.split(",") if d.strip()]
    else:
        sira = [a for a in sorted(os.listdir(gs.HABER_KLASOR), reverse=True)
                if (r["oneriler"].get(a) or {}).get("durum") != "uygulandi" and
                (a not in r["oneriler"] or (r["oneriler"].get(a) or {}).get("bekliyor"))]
    sira = sira[:args.sinir]
    print(f"Toplu kalite raporu ({TOPLU_SURUM}): {len(sira)} haber, yapay zekâ bütçesi {args.butce}")
    yeni = {}
    for ad in sira:                                                # 1) metin + adres
        yol_h = os.path.join(gs.HABER_KLASOR, ad)
        if not os.path.exists(yol_h):
            print("Bulunamadı:", ad)
            continue
        v, govde = gs.md_oku(yol_h)
        metin, kr, mekanik = metin_onerisi(v, govde, butce)
        o = {"surum": TOPLU_SURUM, "tarih": simdi(), "baslik": v.get("title", ""), "durum": "bekliyor", "metin": metin or {},
             "metin_mekanik": mekanik, "metin_degisiklikleri": (kr.get("degisiklikler") or [])[:40] + (kr.get("kurallar") or [])[:20],
             "engeller": kr.get("engeller") or [], "reddedilen_duzeltmeler": (kr.get("reddedilen") or [])[:20], "notlar": [],
             "hassas": bool(g2.HASSAS_RE.search(f"{v.get('title', '')} {v.get('ozet', '')}"))}
        if kr.get("bekliyor"):
            o["bekliyor"] = kr["bekliyor"]
        a = adres_onerisi(ad, v)
        if a:
            o["adres"] = a
        yeni[ad] = (o, v, govde)
    puanlanacak, ara = [], []                                      # 2) mevcut görsel: kaynak kuralı, tekrar, görüntü puanı
    for ad, (o, v, govde) in yeni.items():
        once = {"url": v.get("gorsel", ""), "tur": v.get("gorsel_tur", ""), "kredi": v.get("gorsel_kredi", "")}
        o["gorsel"] = {"once": once}
        if not foto_mu(v):
            once["neden"] = "başlık kartı/fotoğrafsız (artık kullanılmıyor)"
            ara.append(ad)
            continue
        uygun, kaynak, neden = kaynak_uygun(v, oturum, o["notlar"])
        once["kaynak"], once["kaynak_karari"] = kaynak, neden
        kullananlar = tekrar.get(g2.gorsel_id(v["gorsel"]), [])
        if not uygun:
            once["neden"] = f"kaynak/izin yeni kurallara uymuyor: {neden}"
            ara.append(ad)
        elif len(kullananlar) > 1 and kullananlar[0] != ad:
            once["neden"] = f"aynı kare {len(kullananlar)} haberde kullanılmış"
            ara.append(ad)
        else:
            try:
                puanlanacak.append((ad, v, mevcut_indir(oturum, v["gorsel"])))
            except Exception as e:
                once["neden"] = f"indirilemedi: {e}"[:100]
                ara.append(ad)
    puanlar = mevcut_puanla(puanlanacak, butce)
    for ad, v, _ in puanlanacak:
        o = yeni[ad][0]
        p = puanlar.get(ad)
        if not p:
            o["bekliyor"] = "mevcut görsel puanlanamadı (bütçe/kota)"
            continue
        once = o["gorsel"]["once"]
        puan, nedenler = g2.karar({"kaynak": once.get("kaynak")}, p, {"varliklar": []}, {"baslik": v.get("title", ""), "spot": v.get("ozet", "")})
        once.update(puan=puan, gerekce=g2.temiz(p.get("gerekce"), 200), gorunen=g2.temiz(p.get("gorunen"), 120), ihlaller=p.get("ihlaller") or [],
                    taninir_yuz=bool(p.get("taninir_yuz")))
        if nedenler or args.hep_ara:
            once["neden"] = "; ".join(nedenler) or "test: yeni görsel karşılaştırması"
            ara.append(ad)
        else:
            o["gorsel"]["karar"] = "korundu"
            o["gorsel"]["alt_sonra"] = once["gorunen"]
    briefler = brief_al([(ad, yeni[ad][1], yeni[ad][2]) for ad in ara], butce)   # 3) yeni görsel araması
    for ad in ara:
        o, v, govde = yeni[ad]
        brief = briefler.get(ad)
        if not brief or not butce.al():
            o["bekliyor"] = "brief/görsel araması için bütçe kalmadı"
            continue
        hb = {"baslik": (o["metin"].get("title") or {}).get("sonra") or v.get("title", ""), "spot": v.get("ozet", ""),
              "kategori": (v.get("kategoriler") or [""])[0], "ilce": (v.get("ilceler") or [""])[0], "tarih": _tarih(v)}
        try:
            a, gk = g2.sec(hb, brief, lambda parts: gemini(parcalar=parts), kullanim, ad, oturum, onbellek)
        except Exception as e:
            a, gk = None, {"sonuc": f"hata: {e}"[:200], "hata": True}
        if gk.get("hata"):
            o["bekliyor"] = gk.get("sonuc")
            continue
        o["gorsel"].update(brief=brief, reddedilen=gk.get("reddedilen", [])[:12], notlar=gk.get("notlar", [])[:6])
        if a:
            o["gorsel"]["karar"] = "yenilendi"
            o["gorsel"]["sonra"] = dict(gk.get("secilen") or {})
            o["gorsel"]["secim"] = g2.secim_kaydi(a, gs.konu_imzasi(hb["baslik"], hb["spot"], hb["kategori"]), brief.get("tarihi_haber"))
            kullanim.ekle(a["id"], a.get("phash", ""), ad)
        else:
            o["gorsel"]["karar"] = "kaldir"
            o["gorsel"]["sonuc"] = gk.get("sonuc")
    for ad, (o, v, govde) in yeni.items():                         # 4) gruplar
        g = o.get("gorsel") or {}
        kredi_degisir = g.get("karar") == "korundu" and (v.get("gorsel_kredi") or "").startswith(("Fotoğraf", "Arşiv fotoğrafı:", "Görsel:", "Temsili fotoğraf:"))
        if kredi_degisir:
            g["kredi_sonra"] = "Temsilî fotoğraf" if v.get("gorsel_tur") == "stok" else ""
        o["degisiklik_var"] = bool(o.get("metin") or o.get("adres") or g.get("karar") in ("yenilendi", "kaldir") or kredi_degisir
                                   or (g.get("karar") == "korundu" and g.get("alt_sonra")))
        kisi = bool((g.get("sonra") or {}).get("taninir_yuz") or (g.get("once") or {}).get("taninir_yuz") and g.get("karar") == "korundu")
        tek = g.get("karar") in ("yenilendi", "kaldir") or bool(o.get("engeller")) or not o.get("metin_mekanik", True) or kisi or o["hassas"]
        o["grup"] = "tek" if tek else "toplu"
        o["grup_nedeni"] = [x for x, k in (("görsel değişiyor", g.get("karar") == "yenilendi"), ("uygun görsel yok: yayından kaldırma önerisi", g.get("karar") == "kaldir"),
                                           ("karantina önerisi", bool(o.get("engeller"))), ("anlam düzeyinde yazım düzeltmesi", not o.get("metin_mekanik", True)),
                                           ("kişi fotoğrafı", kisi), ("ölüm/suç/intihar/çocuk haberi", o["hassas"])) if k]
        r["oneriler"][ad] = o
    if not args.dosyalar:
        kullanim.kaydet()
    yaz(r, yol)
    print("Özet:", json.dumps(r["ozet"], ensure_ascii=False))
    print(f"Kalan yapay zekâ bütçesi: {butce.kalan}")


# ----------------------------------------------------------------- uygula
def uygula(args):
    r = oku()
    secili = None if args.onay == "toplu" else {x.strip() for x in args.onay.split(",") if x.strip()}
    if not args.onay:
        print("Onay verilmedi (--onay toplu | dosya adları)")
        return
    kullanim = g2.Kullanim()
    n = 0
    for ad, o in r["oneriler"].items():
        if o.get("durum") != "bekliyor" or not o.get("degisiklik_var") or o.get("bekliyor"):
            continue
        if secili is None and o.get("grup") != "toplu":
            continue
        if secili is not None and ad not in secili:
            continue
        yol = os.path.join(gs.HABER_KLASOR, ad)
        if not os.path.exists(yol):
            continue
        v, govde = gs.md_oku(yol)
        m = o.get("metin") or {}
        if m.get("title"):
            v.setdefault("baslik_onceki", v.get("title"))
            v["title"] = m["title"]["sonra"]
        if m.get("ozet"):
            v["ozet"] = m["ozet"]["sonra"]
        if m.get("etiketler"):
            v["etiketler"] = m["etiketler"]["sonra"]
        if m.get("govde"):
            govde = m["govde"]["sonra"] + "\n"
        g = o.get("gorsel") or {}
        if g.get("karar") == "yenilendi" and g.get("secim"):
            gs.secimi_uygula(v, g["secim"])
            v["gorsel_kanit"] = g["secim"].get("kanit")
            for k in ("gorsel_alternatif", "gorsel_yedek"):
                v.pop(k, None)
            kullanim.ekle(g["secim"]["kanit"]["kimlik"], g["secim"]["kanit"].get("phash", ""), ad)
        elif g.get("karar") == "kaldir":
            v["draft"] = True
            v["kaldirma"] = {"tarih": simdi(), "neden": "yeni görsel kurallarına uygun görsel bulunamadı (toplu kalite taraması)"}
        elif g.get("karar") == "korundu":
            s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
            if "kredi_sonra" in g:
                v.setdefault("gorsel_kanit", {"kaynak": (g.get("once") or {}).get("kaynak", ""), "sayfa": s.get("kaynak_sayfa", ""),
                                              "sahip": s.get("fotografci", ""), "lisans": s.get("lisans", ""), "eski_kredi": v.get("gorsel_kredi", ""),
                                              "alinma_tarihi": (s.get("onay") or {}).get("tarih", "")})
                v["gorsel_kredi"] = g["kredi_sonra"]
                if s:
                    s.update(fotografci="", kaynak_sayfa="", aciklama="")
            if g.get("alt_sonra") and s:
                s["gosterilen"] = g["alt_sonra"]
        if o.get("adres"):
            v["url"] = o["adres"]["sonra"]
            v["aliases"] = sorted(set(list(v.get("aliases") or []) + [o["adres"]["once"]]))
        v["kalite_denetim"] = kalite.KALITE_SURUM
        gs.md_yaz(yol, v, govde)
        o["durum"] = "uygulandi"
        o["uygulama"] = simdi()
        n += 1
    kullanim.kaydet()
    yaz(r)
    print(f"Uygulanan öneri: {n}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("islem", choices=["rapor", "uygula"])
    p.add_argument("--dosyalar", default="")
    p.add_argument("--sinir", type=int, default=40)
    p.add_argument("--butce", type=int, default=40)
    p.add_argument("--hep-ara", action="store_true")
    p.add_argument("--cikti", default="")
    p.add_argument("--onay", default="")
    a = p.parse_args()
    if not hc.GEMINI_KEY and a.islem == "rapor":
        print("GEMINI_API_KEY tanımlı değil")
        return
    rapor(a) if a.islem == "rapor" else uygula(a)


if __name__ == "__main__":
    main()
