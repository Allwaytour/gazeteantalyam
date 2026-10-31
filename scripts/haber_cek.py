#!/usr/bin/env python3
"""Gazete Antalya - otomatik haber derleyici.

kaynaklar.yml içindeki RSS kaynaklarını okur, Antalya ile ilgili haberleri seçer,
kategorisini ve ilçesini bulur, data/otomatik.json ve static/otomatik.json dosyalarına yazar.
Yalnızca başlık, kısa özet ve kaynak bağlantısı saklanır; haberin tamamı kaynağında okunur.
"""
import calendar
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = timezone(timedelta(hours=3))
SITE = os.environ.get("SITE_URL", "https://www.gazeteantalyam.com").rstrip("/")
UA = "Mozilla/5.0 (compatible; GazeteAntalyaBot/1.0; +https://www.gazeteantalyam.com)"


def tr_kucuk(s):
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


# İlçe adları ve ilçeye bağlı bilinen yerler
ILCE_ESLER = {
    "Akseki": ["akseki"], "Aksu": ["aksu"], "Alanya": ["alanya", "mahmutlar", "kestel", "avsallar", "okurcalar"],
    "Demre": ["demre", "kale ilçesi"], "Döşemealtı": ["döşemealtı"], "Elmalı": ["elmalı"], "Finike": ["finike"],
    "Gazipaşa": ["gazipaşa"], "Gündoğmuş": ["gündoğmuş"], "İbradı": ["ibradı"], "Kaş": ["kaş", "kalkan"],
    "Kemer": ["kemer", "beldibi", "göynük", "tekirova", "çamyuva", "olimpos"], "Kepez": ["kepez", "varsak"],
    "Konyaaltı": ["konyaaltı", "hurma", "liman mahallesi"], "Korkuteli": ["korkuteli"], "Kumluca": ["kumluca"],
    "Manavgat": ["manavgat", "side", "kızılağaç", "kumköy", "evrenseki"], "Muratpaşa": ["muratpaşa", "lara", "kaleiçi", "güzeloba"],
    "Serik": ["serik", "belek", "kadriye", "boğazkent"],
}
ILCE_RE = {ilce: re.compile(r"(?<![\wçğıöşü])(" + "|".join(map(re.escape, adlar)) + r")(?![\wçğıöşü])") for ilce, adlar in ILCE_ESLER.items()}
ANTALYA_RE = re.compile(r"(?<![\wçğıöşü])antalya")

KATEGORI_KELIMELER = [
    ("Asayiş", ["kaza", "yangın", "gözaltı", "tutuklan", "cinayet", "öldür", "bıçak", "silah", "polis", "jandarma", "hırsız",
                "uyuşturucu", "operasyon", "yaralandı", "hayatını kaybetti", "boğul", "dolandır", "kavga", "firari", "şüpheli",
                "saldırı", "devrildi", "çarpış", "ceset", "kaçakçı", "sahil güvenlik", "itfaiye"]),
    ("Spor", ["antalyaspor", "alanyaspor", "maç", "futbol", "basketbol", "voleybol", "şampiyon", "gol", "süper lig", "turnuva",
              "sporcu", "teknik direktör", "transfer", "hentbol", "maraton", "olimpiyat", "milli takım"]),
    ("Turizm", ["turist", "turizm", "otel", "tatil", "sezon", "havalimanı", "kruvaziyer", "ziyaretçi", "plaj", "konaklama", "acenta", "tur operatör"]),
    ("Ekonomi", ["ekonomi", "ihracat", "ithalat", "fiyat", "zam", "esnaf", "tarım", "sera", "hasat", "üretici", "borsa", "yatırım",
                 "istihdam", "narenciye", "domates", "çiftçi", "ticaret", "kira", "konut", "enflasyon", "ihale", "fabrika"]),
    ("Sağlık", ["hastane", "sağlık", "doktor", "hasta", "ameliyat", "tedavi", "aşı", "hekim", "kanser", "organ", "ambulans", "salgın"]),
    ("Eğitim", ["okul", "öğrenci", "üniversite", "öğretmen", "eğitim", "sınav", "mezun", "akademisyen", "rektör", "kampüs"]),
    ("Kültür-Sanat", ["festival", "konser", "sergi", "tiyatro", "sanat", "film", "müze", "kültür", "opera", "bale", "altın portakal", "kazı", "antik kent"]),
    ("Yaşam", ["hava durumu", "yağış", "fırtına", "sağanak", "deprem", "sokak hayvan", "doğa", "çevre", "meteoroloji", "sıcaklık", "hortum", "sel", "orman"]),
]
KATEGORI_RE = [(k, [re.compile(r"(?<![\wçğıöşü])" + re.escape(w)) for w in ws]) for k, ws in KATEGORI_KELIMELER]


def temizle(s, uzunluk=300):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > uzunluk:
        s = s[:uzunluk].rsplit(" ", 1)[0] + "…"
    return s


def kategori_bul(metin):
    puanlar = []
    for k, desenler in KATEGORI_RE:
        p = sum(1 for d in desenler if d.search(metin))
        if p:
            puanlar.append((p, -[x[0] for x in KATEGORI_RE].index(k), k))
    return max(puanlar)[2] if puanlar else "Gündem"


def ilce_bul(*metinler):
    """Önce başlıkta, sonra özette geçen ilk ilçeyi döndürür."""
    for metin in metinler:
        bulunan = [(m.start(), ilce) for ilce, d in ILCE_RE.items() for m in [d.search(metin)] if m]
        if bulunan:
            return min(bulunan)[1]
    return ""


def gorsel_bul(e):
    for alan in ("media_content", "media_thumbnail"):
        for m in e.get(alan) or []:
            if m.get("url"):
                return m["url"]
    for l in e.get("links") or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
    m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", "") or "")
    return m.group(1) if m else ""


def anahtar(baslik):
    return re.sub(r"[^\wçğıöşü]", "", tr_kucuk(baslik))[:70]


def onceki_haberler():
    """Önceki çalıştırmada derlenen haberleri yükler (önce yerel dosya, yoksa yayındaki site)."""
    yol = os.path.join(KOK, "data", "otomatik.json")
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f).get("haberler", [])
    except Exception:
        pass
    try:
        r = requests.get(SITE + "/otomatik.json", headers={"User-Agent": UA}, timeout=20)
        if r.ok:
            return r.json().get("haberler", [])
    except Exception as e:
        print("Önceki haberler alınamadı:", e)
    return []


def kaynak_oku(k, ayar, simdi):
    r = requests.get(k["url"], headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    akis = feedparser.parse(r.content)
    sonuc = []
    google = "news.google." in k["url"]
    for e in akis.entries:
        baslik = temizle(e.get("title"), 220)
        link = e.get("link")
        if not baslik or not link:
            continue
        kaynak_adi = k["ad"]
        ozet = temizle(e.get("summary") or e.get("description"), 320)
        if google:
            # Google Haberler başlıkları "Başlık - Kaynak" biçimindedir
            src = (e.get("source") or {}).get("title")
            if " - " in baslik:
                baslik, son = baslik.rsplit(" - ", 1)
                src = src or son
            kaynak_adi = src or "Google Haberler"
            ozet = ""
        if ozet and anahtar(ozet).startswith(anahtar(baslik)[:40]):
            ozet = ""  # özet başlığın tekrarıysa gösterme
        zaman = e.get("published_parsed") or e.get("updated_parsed")
        ts = calendar.timegm(zaman) if zaman else int(simdi.timestamp())
        ts = min(ts, int(simdi.timestamp()))
        metin = tr_kucuk(baslik + " " + ozet)
        ilce = ilce_bul(tr_kucuk(baslik), tr_kucuk(ozet))
        if k.get("antalya_filtresi") and not (ANTALYA_RE.search(metin) or ilce):
            continue
        sonuc.append({
            "baslik": baslik,
            "ozet": ozet,
            "link": link,
            "kaynak": kaynak_adi,
            "ts": ts,
            "iso": datetime.fromtimestamp(ts, TR).isoformat(),
            "kategori": k.get("kategori") or kategori_bul(metin),
            "ilce": ilce,
            "gorsel": gorsel_bul(e) if ayar.get("gorsel_kullan") else "",
        })
    return sonuc


def main():
    with open(os.path.join(KOK, "kaynaklar.yml"), encoding="utf-8") as f:
        yap = yaml.safe_load(f) or {}
    ayar = yap.get("ayarlar") or {}
    simdi = datetime.now(TR)
    sinir = int((simdi - timedelta(days=int(ayar.get("en_eski_gun", 5)))).timestamp())

    yeni, basarili = [], 0
    for k in yap.get("kaynaklar") or []:
        try:
            h = kaynak_oku(k, ayar, simdi)
            basarili += 1
            print(f"✓ {k['ad']}: {len(h)} haber  ({k['url'][:70]})")
            yeni.extend(h)
        except Exception as e:
            print(f"✗ {k['ad']}: {e}")

    tum, gorulen, gorulen_link = [], set(), set()
    for h in sorted(yeni, key=lambda x: x["ts"]) + onceki_haberler():
        a = anahtar(h["baslik"])
        if h["ts"] < sinir or a in gorulen or h["link"] in gorulen_link:
            continue
        gorulen.add(a)
        gorulen_link.add(h["link"])
        if not ayar.get("gorsel_kullan"):
            h["gorsel"] = ""
        tum.append(h)
    tum.sort(key=lambda x: x["ts"], reverse=True)
    tum = tum[: int(ayar.get("en_fazla_haber", 300))]

    cikti = {"guncelleme": simdi.isoformat(), "haberler": tum}
    for yol in ("data/otomatik.json", "static/otomatik.json"):
        p = os.path.join(KOK, yol)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cikti, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n{basarili}/{len(yap.get('kaynaklar') or [])} kaynak okundu, sitede {len(tum)} otomatik haber var.")


if __name__ == "__main__":
    sys.exit(main())
