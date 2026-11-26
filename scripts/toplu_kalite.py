#!/usr/bin/env python3
"""Yayımlanmış haberlerin tek seferlik kalite taraması (k1/b1). İki aşama:

  rapor   Haberler (yeniden eskiye) kalite katmanından geçirilir, görseller yeniden puanlanır. Haber dosyalarına DOKUNULMAZ;
          öneriler data/toplu_kalite/oneriler.json'a yazılır (önce/sonra: başlık, değişen kelimeler, eski/yeni görsel ve puanı,
          adres düzeltmesi). Rapor sitede /kalite-raporu/ sayfasında görünür (arama motorlarına kapalı). Yapay zekâ ve Unsplash
          kotası için çalışma başına bütçe vardır; kalan haberler sonraki çalışmalarda işlenir.
  uygula  Onaylanan öneriler haber dosyalarına yazılır (--onay hepsi | dosya adları virgülle).

Test: --dosyalar ile yalnızca verilen haberler işlenir; --hep-ara ile mevcut görsel eşiği geçse de yeni görsel aranır
(26.11.2026 incelemesindeki hatalı örneklerin karşılaştırması: data/kalite_test.json).
"""
import argparse
import json
import os
import re
import sys
import time
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
TOPLU_SURUM = "t1"
EDITOR_SORUN = ("ascii", "ascii_metin", "bitisik", "ek_uyum", "ek_ayri", "intihar_yontemi")

PUAN_MEVCUT = """Sen bir Türk haber sitesinin deneyimli foto editörüsün. Aşağıda birkaç haber ve her haberde KULLANILAN TEK fotoğraf var.
Her fotoğrafı yalnızca KENDİ haberine göre değerlendir (ölçüt ve kesin ret kodları aşağıda).
""" + g2.PUAN_TALIMAT.split("Her aday için:", 1)[1].split("Yalnızca şu biçimde", 1)[0] + """
Yalnızca şu biçimde JSON döndür: {"haberler": [{"no": 1, "puan": 0, "ihlaller": [], "taninir_yuz": false, "gorunen": "...", "gerekce": "..."}]}
"""


def simdi():
    return datetime.now(TR).isoformat(timespec="seconds")


def oku():
    try:
        with open(CIKTI, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"surum": TOPLU_SURUM, "oneriler": {}, "ozet": {}}


def yaz(r, yol=CIKTI):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    o = r.get("oneriler", {}).values()
    r["ozet"] = {"guncelleme": simdi(), "islenen": len(o),
                 "metin_degisen": sum(1 for x in o if x.get("metin")), "gorsel_yenilenen": sum(1 for x in o if (x.get("gorsel") or {}).get("karar") == "yenilendi"),
                 "baslik_karti": sum(1 for x in o if (x.get("gorsel") or {}).get("karar") == "baslik_karti"),
                 "gorsel_korunan": sum(1 for x in o if (x.get("gorsel") or {}).get("karar") == "korundu"),
                 "adres_degisen": sum(1 for x in o if x.get("adres")), "karantina_onerisi": sum(1 for x in o if x.get("engeller")),
                 "onay_bekleyen": sum(1 for x in o if x.get("durum") == "bekliyor" and x.get("degisiklik_var")),
                 "uygulanan": sum(1 for x in o if x.get("durum") == "uygulandi")}
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------- yapay zekâ bütçesi
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
def metin_onerisi(v, govde, butce):
    alanlar = {"baslik": v.get("title", ""), "spot": v.get("ozet", ""), "metin": govde.strip(), "etiketler": list(v.get("etiketler") or [])}
    kalite.sozluk_hazirla()
    s0 = kalite.sorunlar(alanlar)
    editor_gerek = any(k in d for d in s0.values() for k in EDITOR_SORUN)
    fn = (lambda m: gemini(m)) if editor_gerek and butce.al() else None
    duz, kr = kalite.denetle(alanlar, fn)
    if editor_gerek and not fn:
        return None, {"bekliyor": "editör için yapay zekâ bütçesi kalmadı"}
    degisen = {}
    for a, alan in (("baslik", "title"), ("spot", "ozet"), ("metin", "govde"), ("etiketler", "etiketler")):
        once = alanlar[a]
        if duz.get(a) != once:
            degisen[alan] = {"once": once, "sonra": duz[a]} if a != "metin" else {"sonra": duz[a]}
    return degisen, kr


def adres_onerisi(ad, v):
    """Kelime ortasından kesilmiş dosya adı (ör. '...-kayit-sistemle') → kelime sınırında kesilmiş adres + eski adrese yönlendirme."""
    if v.get("url") or not re.match(r"\d{8}-.+-[0-9a-f]{5}\.md$", ad):
        return None
    parca = ad[9:-9]
    tam = hc.slug(v.get("title", ""), 999)
    if tam.startswith(parca) and len(tam) > len(parca) and tam[len(parca)] != "-" and "-" in parca:
        yeni = f"{ad[:9]}{parca.rsplit('-', 1)[0]}{ad[-9:-3]}"
        return {"once": f"/haber/{ad[:-3]}/", "sonra": f"/haber/{yeni}/"}
    return None


# ----------------------------------------------------------------- görsel
def mevcut_puanla(gruplar, butce):
    """[(ad, v, veri)] → {ad: puan kaydı}. Tek istekte en çok 6 haberin mevcut fotoğrafı puanlanır."""
    sonuc = {}
    for i in range(0, len(gruplar), 6):
        parti = gruplar[i:i + 6]
        if not butce.al():
            break
        parcalar = [{"text": PUAN_MEVCUT}]
        for n, (ad, v, veri) in enumerate(parti, 1):
            parcalar.append({"text": f"\nHABER {n}\nBAŞLIK: {v.get('title', '')}\nSPOT: {v.get('ozet', '')}\nKATEGORİ: {(v.get('kategoriler') or [''])[0]}"
                                     f"\nFOTOĞRAF {n} (dosya: {(v.get('gorsel_secim') or {}).get('gosterilen', '')[:80]}):"})
            parcalar.append({"inline_data": {"mime_type": "image/jpeg", "data": __import__("base64").b64encode(veri).decode()}})
        try:
            y = gemini(parcalar=parcalar)
        except Exception as e:
            print("Mevcut görsel puanlanamadı:", e)
            continue
        for p in (y or {}).get("haberler") or []:
            try:
                ad = parti[int(p.get("no")) - 1][0]
                sonuc[ad] = p
            except Exception:
                pass
    return sonuc


def brief_al(haberler, butce):
    """[(ad, v, govde)] → {ad: brief}. Tek istekte 8 haber."""
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
            hb = {"baslik": v.get("title", ""), "spot": v.get("ozet", "")}
            out[ad] = g2.brief_duzelt((y or {}).get(str(n)), hb)
    return out


def tekrar_haritasi():
    """görsel kimliği → onu kullanan haberler (eskiden yeniye). Aynı kare birden çok haberdeyse yalnızca ilki korunabilir."""
    h = {}
    for ad in sorted(os.listdir(gs.HABER_KLASOR)):
        try:
            v, _ = gs.md_oku(os.path.join(gs.HABER_KLASOR, ad))
        except Exception:
            continue
        g = v.get("gorsel") or ""
        if g and v.get("gorsel_tur") not in ("tipografik", "kategori") and "/images/kategori/" not in g:
            h.setdefault(g2.gorsel_id(g), []).append(ad)
    return h


def foto_mu(v):
    g = v.get("gorsel") or ""
    return bool(g) and v.get("gorsel_tur") not in ("tipografik", "kategori") and "/images/kategori/" not in g and "/haber-gorsel/" not in g


def mevcut_indir(oturum, url):
    u = url
    if u.startswith("/"):
        yol = os.path.join(KOK, "static", u.lstrip("/"))
        with open(yol, "rb") as f:
            veri = f.read()
    else:
        if "images.unsplash.com" in u:
            u = re.sub(r"[?&]w=\d+", "", u) + ("&" if "?" in u else "?") + "w=480"
        elif "/thumb/" in u:
            u = re.sub(r"/\d+px-", "/480px-", u)
        r = oturum.get(u, timeout=25)
        r.raise_for_status()
        veri = r.content
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(veri)).convert("RGB")
    im.thumbnail((512, 512))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


# ----------------------------------------------------------------- rapor
def rapor(args):
    r = oku() if not args.cikti else ({"surum": TOPLU_SURUM, "oneriler": {}, "ozet": {}} if not os.path.exists(args.cikti)
                                     else json.load(open(args.cikti, encoding="utf-8")))
    yol = args.cikti or CIKTI
    butce = Butce(args.butce)
    g2.UNSPLASH_YEDEK = args.unsplash_yedek
    oturum = requests.Session()
    oturum.headers["User-Agent"] = g2.UA
    kullanim = g2.Kullanim(gs.HABER_KLASOR)
    tekrar = tekrar_haritasi()
    if args.dosyalar:
        sira = [d.strip() for d in args.dosyalar.split(",") if d.strip()]
    else:
        sira = sorted(os.listdir(gs.HABER_KLASOR), reverse=True)
        sira = [a for a in sira if (r["oneriler"].get(a) or {}).get("surum") != TOPLU_SURUM or (r["oneriler"].get(a) or {}).get("bekliyor")]
    sira = sira[:args.sinir]
    print(f"Toplu kalite raporu: {len(sira)} haber, yapay zekâ bütçesi {args.butce}")
    yeni = {}
    # 1) metin + adres
    for ad in sira:
        yol_h = os.path.join(gs.HABER_KLASOR, ad)
        if not os.path.exists(yol_h):
            print("Bulunamadı:", ad)
            continue
        v, govde = gs.md_oku(yol_h)
        metin, kr = metin_onerisi(v, govde, butce)
        o = {"surum": TOPLU_SURUM, "tarih": simdi(), "baslik": v.get("title", ""), "durum": "bekliyor", "metin": metin or {},
             "metin_degisiklikleri": (kr.get("degisiklikler") or [])[:40] + (kr.get("kurallar") or [])[:20],
             "engeller": kr.get("engeller") or [], "reddedilen_duzeltmeler": (kr.get("reddedilen") or [])[:20]}
        if kr.get("bekliyor"):
            o["bekliyor"] = kr["bekliyor"]
        a = adres_onerisi(ad, v)
        if a:
            o["adres"] = a
        yeni[ad] = (o, v, govde)
    # 2) mevcut görselin puanı (tekrar kullanılan kare: yalnızca en eski haberde korunabilir)
    puanlanacak, ara = [], []
    for ad, (o, v, govde) in yeni.items():
        if not foto_mu(v):
            o["gorsel"] = {"once": {"url": v.get("gorsel", ""), "tur": v.get("gorsel_tur", "")}, "karar": "baslik_karti_kaldi"}
            continue
        gid = g2.gorsel_id(v["gorsel"])
        kullananlar = tekrar.get(gid, [])
        if len(kullananlar) > 1 and kullananlar[0] != ad:
            o["gorsel"] = {"once": {"url": v["gorsel"], "tur": v.get("gorsel_tur", ""), "neden": f"aynı kare {len(kullananlar)} haberde kullanılmış"}}
            ara.append(ad)
            continue
        try:
            puanlanacak.append((ad, v, mevcut_indir(oturum, v["gorsel"])))
        except Exception as e:
            o["gorsel"] = {"once": {"url": v["gorsel"], "neden": f"indirilemedi: {e}"[:100]}}
            ara.append(ad)
    puanlar = mevcut_puanla(puanlanacak, butce)
    for ad, v, _ in puanlanacak:
        o = yeni[ad][0]
        p = puanlar.get(ad)
        if not p:
            o["bekliyor"] = "mevcut görsel puanlanamadı (bütçe/kota)"
            o["gorsel"] = {"once": {"url": v["gorsel"]}}
            continue
        hb = {"baslik": v.get("title", ""), "spot": v.get("ozet", "")}
        puan, nedenler = g2.karar({"kaynak": "commons" if "wikimedia" in v["gorsel"] else "unsplash"}, p, {"varliklar": []}, hb)
        o["gorsel"] = {"once": {"url": v["gorsel"], "tur": v.get("gorsel_tur", ""), "puan": puan, "gerekce": g2.temiz(p.get("gerekce"), 200),
                                "gorunen": g2.temiz(p.get("gorunen"), 120), "ihlaller": p.get("ihlaller") or []}}
        if nedenler or args.hep_ara:
            o["gorsel"]["once"]["neden"] = "; ".join(nedenler) or "test: yeni görsel karşılaştırması"
            ara.append(ad)
        else:
            o["gorsel"]["karar"] = "korundu"
            o["gorsel"]["alt_sonra"] = g2.temiz(p.get("gorunen"), 120)
    # 3) eşik altındakiler için brief + yeni arama
    briefler = brief_al([(ad, yeni[ad][1], yeni[ad][2]) for ad in ara], butce)
    for ad in ara:
        o, v, govde = yeni[ad]
        brief = briefler.get(ad)
        if not brief or not butce.al():
            o["bekliyor"] = "brief/görsel araması için bütçe kalmadı"
            continue
        hb = {"baslik": (o["metin"].get("title") or {}).get("sonra") or v.get("title", ""), "spot": v.get("ozet", ""),
              "kategori": (v.get("kategoriler") or [""])[0], "ilce": (v.get("ilceler") or [""])[0]}
        try:
            a, gk = g2.sec(hb, brief, lambda parts: gemini(parcalar=parts), kullanim, ad, oturum)
        except Exception as e:
            a, gk = None, {"sonuc": f"hata: {e}"[:200], "hata": True}
        if gk.get("hata"):
            o["bekliyor"] = gk.get("sonuc")
            continue
        o["gorsel"]["brief"] = brief
        o["gorsel"]["reddedilen"] = gk.get("reddedilen", [])[:12]
        if a:
            o["gorsel"]["karar"] = "yenilendi"
            o["gorsel"]["sonra"] = {k: a.get(k) for k in ("url", "kaynak", "puan", "gerekce", "gorunen", "fotografci", "lisans", "sayfa", "sorgu", "id", "phash")}
            o["gorsel"]["secim"] = g2.secim_kaydi(a, gs.konu_imzasi(hb["baslik"], hb["spot"], hb["kategori"]))
            o["gorsel"]["indirme"] = a.get("indirme", "")
            kullanim.ekle(a["id"], a.get("phash", ""), ad)          # aynı rapordaki başka habere aynı kare önerilmesin
        else:
            o["gorsel"]["karar"] = "baslik_karti"
            o["gorsel"]["sonuc"] = gk.get("sonuc")
    for ad, (o, v, govde) in yeni.items():
        g = o.get("gorsel") or {}
        o["degisiklik_var"] = bool(o.get("metin") or o.get("adres") or g.get("karar") in ("yenilendi", "baslik_karti")
                                   or (g.get("karar") == "korundu" and g.get("alt_sonra")))
        r["oneriler"][ad] = o
    if not args.dosyalar:
        # kullanım kaydı yalnızca gerçek raporda tutulur (önerilen kareler başka haberlere önerilmesin); uygulanmazsa 90 günde düşer
        kullanim.kaydet()
    yaz(r, yol)
    print("Özet:", json.dumps(r["ozet"], ensure_ascii=False))
    print(f"Kalan yapay zekâ bütçesi: {butce.kalan}")


# ----------------------------------------------------------------- uygula
def uygula(args):
    import gorsel_tamamla as gt
    r = oku()
    onay = None if args.onay == "hepsi" else {x.strip() for x in args.onay.split(",") if x.strip()}
    kullanim = g2.Kullanim()
    n = 0
    for ad, o in r["oneriler"].items():
        if o.get("durum") != "bekliyor" or not o.get("degisiklik_var") or (onay is not None and ad not in onay) or o.get("bekliyor"):
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
            v.pop("gorsel_alternatif", None)
            kullanim.ekle(g["sonra"]["id"], g["sonra"].get("phash", ""), ad)
            g2.unsplash_indirme_bildir({"kaynak": g["sonra"].get("kaynak"), "indirme": g.get("indirme")})
        elif g.get("karar") == "baslik_karti":
            web = gt.tipo_hazirla(v, ad)
            gt.tipo_uygula(v, web, gs.konu_imzasi(v.get("title"), v.get("ozet"), (v.get("kategoriler") or ["Gündem"])[0]),
                           f"Toplu kalite taraması: {g.get('sonuc', '')}")
            v.pop("gorsel_alternatif", None)
        elif g.get("karar") == "korundu" and g.get("alt_sonra") and isinstance(v.get("gorsel_secim"), dict):
            v["gorsel_secim"]["gosterilen"] = g["alt_sonra"]
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
    p.add_argument("--butce", type=int, default=40, help="bu çalışmada en fazla yapay zekâ isteği")
    p.add_argument("--hep-ara", action="store_true")
    p.add_argument("--unsplash-yedek", type=int, default=20, help="saatlik Unsplash hakkından yeni haberlere bırakılacak pay")
    p.add_argument("--cikti", default="")
    p.add_argument("--onay", default="")
    a = p.parse_args()
    if not hc.GEMINI_KEY and a.islem == "rapor":
        print("GEMINI_API_KEY tanımlı değil")
        return
    rapor(a) if a.islem == "rapor" else uygula(a)


if __name__ == "__main__":
    main()
