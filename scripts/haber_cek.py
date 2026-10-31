#!/usr/bin/env python3
"""Gazete Antalya - otomatik haber hazırlayıcı.

1. kaynaklar.yml içindeki RSS kaynaklarından Antalya haberlerini toplar.
2. Daha önce yayımlanmış ya da benzeri yayımlanmış haberleri ayıklar.
3. Her yeni haber için kaynağın sayfasını okur; GEMINI_API_KEY tanımlıysa
   yapay zekâ ile özgün bir haber metni yazdırır, değilse kısa özet sayfası oluşturur.
4. Fotoğraf: kaynağın kendi fotoğrafı, yoksa Wikimedia Commons'tan özgür lisanslı Antalya fotoğrafı.
5. Sonucu content/haber/ altına Markdown dosyası olarak yazar (panelden düzenlenebilir).
"""
import calendar
import difflib
import hashlib
import html
import json
import os
import random
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = timezone(timedelta(hours=3))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HABER_KLASOR = os.path.join(KOK, "content", "haber")
DURUM_DOSYA = os.path.join(KOK, "data", "otomatik_durum.json")
ARSIV_DOSYA = os.path.join(KOK, "data", "foto_arsivi.json")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()

KOSE_RE = re.compile(r"/(yazar|yazarlar|kose-?yazi|kose|koseyazisi|makale|makaleler|yorum|column|columnist|author|gorus)[/-]|köşe yazısı|yazının devamı")

KATEGORILER = ["Gündem", "Asayiş", "Ekonomi", "Turizm", "Spor", "Sağlık", "Eğitim", "Kültür-Sanat", "Yaşam"]


# ----------------------------------------------------------------- yardımcılar
def tr_kucuk(s):
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


def slug(s, uzunluk=70):
    s = re.sub(r"['’‘`]", "", tr_kucuk(s)).replace("ı", "i")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:uzunluk].rstrip("-")


def temizle(s, uzunluk=400):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > uzunluk:
        s = s[:uzunluk].rsplit(" ", 1)[0] + "…"
    return s


def anahtar(baslik):
    return re.sub(r"[^\wçğıöşü]", "", tr_kucuk(baslik))[:70]


ONEMSIZ = set("ve ile için da de bir bu şu o en çok daha olarak oldu olan gibi kadar sonra önce yeni antalya antalyada antalyanın ın in un ün".split())


def kelimeler(s):
    s = re.sub(r"['’‘]\w*", "", tr_kucuk(s))
    return {k[:6] for k in re.findall(r"[\wçğıöşü]+", s) if len(k) > 2 and k not in ONEMSIZ}


def benzer(a, b):
    """İki başlığın aynı olayı anlatıp anlatmadığını tahmin eder (0-1)."""
    oran = difflib.SequenceMatcher(None, anahtar(a), anahtar(b)).ratio()
    ka, kb = kelimeler(a), kelimeler(b)
    ortak = len(ka & kb) / max(1, min(len(ka), len(kb))) if ka and kb else 0
    return max(oran, ortak * 0.9)


# İlçe adları ve ilçeye bağlı bilinen yerler
ILCE_ESLER = {
    "Akseki": ["akseki"], "Aksu": ["aksu"], "Alanya": ["alanya", "mahmutlar", "kestel", "avsallar", "okurcalar"],
    "Demre": ["demre"], "Döşemealtı": ["döşemealtı"], "Elmalı": ["elmalı"], "Finike": ["finike"],
    "Gazipaşa": ["gazipaşa"], "Gündoğmuş": ["gündoğmuş"], "İbradı": ["ibradı"], "Kaş": ["kaş", "kalkan"],
    "Kemer": ["kemer", "beldibi", "göynük", "tekirova", "çamyuva", "olimpos"], "Kepez": ["kepez", "varsak"],
    "Konyaaltı": ["konyaaltı", "hurma", "liman mahallesi"], "Korkuteli": ["korkuteli"], "Kumluca": ["kumluca"],
    "Manavgat": ["manavgat", "side", "kızılağaç", "kumköy", "evrenseki"], "Muratpaşa": ["muratpaşa", "lara", "kaleiçi", "güzeloba"],
    "Serik": ["serik", "belek", "kadriye", "boğazkent"],
}
SINIR = r"(?<![\wçğıöşü])"
ILCE_RE = {i: re.compile(SINIR + "(" + "|".join(map(re.escape, a)) + r")(?![\wçğıöşü])") for i, a in ILCE_ESLER.items()}
ANTALYA_RE = re.compile(SINIR + "antalya")

KATEGORI_KELIMELER = [
    ("Asayiş", ["kaza", "yangın", "gözaltı", "tutuklan", "cinayet", "öldür", "bıçak", "silah", "polis", "jandarma", "hırsız",
                "uyuşturucu", "operasyon", "yaralandı", "hayatını kaybetti", "boğul", "dolandır", "kavga", "firari", "şüpheli",
                "saldırı", "devrildi", "çarpış", "ceset", "kaçakçı", "sahil güvenlik", "itfaiye"]),
    ("Spor", ["antalyaspor", "alanyaspor", "maç", "futbol", "basketbol", "voleybol", "şampiyon", "gol", "süper lig", "turnuva",
              "sporcu", "teknik direktör", "transfer", "hentbol", "maraton", "olimpiyat", "milli takım"]),
    ("Turizm", ["turist", "turizm", "otel", "tatil", "sezon", "havalimanı", "kruvaziyer", "ziyaretçi", "plaj", "konaklama", "acenta"]),
    ("Ekonomi", ["ekonomi", "ihracat", "ithalat", "fiyat", "zam", "esnaf", "tarım", "sera", "hasat", "üretici", "borsa", "yatırım",
                 "istihdam", "narenciye", "domates", "çiftçi", "ticaret", "kira", "konut", "enflasyon", "ihale", "fabrika"]),
    ("Sağlık", ["hastane", "sağlık", "doktor", "hasta", "ameliyat", "tedavi", "aşı", "hekim", "kanser", "organ", "ambulans", "salgın"]),
    ("Eğitim", ["okul", "öğrenci", "üniversite", "öğretmen", "eğitim", "sınav", "mezun", "akademisyen", "rektör", "kampüs"]),
    ("Kültür-Sanat", ["festival", "konser", "sergi", "tiyatro", "sanat", "film", "müze", "kültür", "opera", "bale", "altın portakal", "kazı", "antik kent"]),
    ("Yaşam", ["hava durumu", "yağış", "fırtına", "sağanak", "deprem", "sokak hayvan", "doğa", "çevre", "meteoroloji", "sıcaklık", "hortum", "sel", "orman"]),
]
KATEGORI_RE = [(k, [re.compile(SINIR + re.escape(w)) for w in ws]) for k, ws in KATEGORI_KELIMELER]


def kategori_bul(metin):
    puan = [(sum(1 for d in ds if d.search(metin)), -i, k) for i, (k, ds) in enumerate(KATEGORI_RE)]
    puan = [p for p in puan if p[0]]
    return max(puan)[2] if puan else "Gündem"


def ilce_bul(*metinler):
    for metin in metinler:
        bulunan = [(m.start(), i) for i, d in ILCE_RE.items() for m in [d.search(metin)] if m]
        if bulunan:
            return min(bulunan)[1]
    return ""


def json_oku(yol, varsayilan):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return varsayilan


def json_yaz(yol, veri):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------- RSS
def kaynak_oku(k):
    r = requests.get(k["url"], headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    akis = feedparser.parse(r.content)
    google = "news.google." in k["url"]
    sonuc = []
    for e in akis.entries:
        baslik = temizle(e.get("title"), 220)
        link = e.get("link")
        if not baslik or not link:
            continue
        kaynak_adi = k["ad"]
        ozet = temizle(e.get("summary") or e.get("description"), 400)
        if google:
            src = (e.get("source") or {}).get("title")
            if " - " in baslik:
                baslik, son = baslik.rsplit(" - ", 1)
                src = src or son
            kaynak_adi = src or "Google Haberler"
            ozet = ""
        if ozet and anahtar(ozet).startswith(anahtar(baslik)[:40]):
            ozet = ""
        zaman = e.get("published_parsed") or e.get("updated_parsed")
        ts = min(calendar.timegm(zaman) if zaman else int(time.time()), int(time.time()))
        metin = tr_kucuk(baslik + " " + ozet)
        ilce = ilce_bul(tr_kucuk(baslik), tr_kucuk(ozet))
        if k.get("antalya_filtresi") and not (ANTALYA_RE.search(metin) or ilce):
            continue
        gorsel = ""
        for alan in ("media_content", "media_thumbnail"):
            for m in e.get(alan) or []:
                gorsel = gorsel or m.get("url", "")
        for l in e.get("links") or []:
            if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
                gorsel = gorsel or l.get("href", "")
        sonuc.append({"baslik": baslik, "ozet": ozet, "link": link, "kaynak": kaynak_adi, "ts": ts, "google": google,
                      "kategori": k.get("kategori") or kategori_bul(metin), "ilce": ilce, "gorsel": gorsel})
    return sonuc


# ----------------------------------------------------------------- kaynak sayfası
def gercek_url(h):
    if not h["google"]:
        return h["link"]
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(h["link"], interval=1)
        if r.get("success"):
            return r["decoded_url"]
        print("   Google bağlantısı çözülemedi:", r.get("message"))
    except Exception as e:
        print("   Google bağlantısı çözülemedi:", e)
    return ""


def sayfa_oku(url):
    """Kaynağın metnini ve paylaşım görselini döndürür."""
    if not url:
        return "", ""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "tr-TR,tr"}, timeout=20)
        r.raise_for_status()
        sayfa = r.text
    except Exception as e:
        print("   Sayfa okunamadı:", e)
        return "", ""
    gorsel = ""
    m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)', sayfa, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)["\']', sayfa, re.I)
    if m:
        gorsel = html.unescape(m.group(1))
        if gorsel.startswith("//"):
            gorsel = "https:" + gorsel
        if not gorsel.startswith("http") or re.search(r"logo|default|placeholder", gorsel, re.I):
            gorsel = ""
    metin = ""
    try:
        import trafilatura
        metin = trafilatura.extract(sayfa, include_comments=False, include_tables=False, favor_precision=True) or ""
    except Exception as e:
        print("   Metin çıkarılamadı:", e)
    return metin.strip()[:7000], gorsel


# ----------------------------------------------------------------- yapay zekâ
_model = None


def model_sec():
    global _model
    if _model is not None:
        return _model
    _model = GEMINI_MODEL
    if not _model:
        try:
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200},
                             headers={"x-goog-api-key": GEMINI_KEY}, timeout=20)
            r.raise_for_status()
            adaylar = []
            for m in r.json().get("models", []):
                ad = m["name"].split("/")[-1]
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                if "flash" not in ad or re.search(r"tts|image|live|audio|embed|thinking|exp", ad):
                    continue
                surum = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", ad)[:1]] or [0]
                adaylar.append((("preview" not in ad), ("lite" in ad), surum[0], ad))
            adaylar.sort(reverse=True)
            _model = adaylar[0][3] if adaylar else "gemini-2.5-flash-lite"
        except Exception as e:
            print("Model listesi alınamadı:", e)
            _model = "gemini-2.5-flash-lite"
    print("Yapay zekâ modeli:", _model)
    return _model


YZ_TALIMAT = """Sen Antalya'da yayın yapan "Gazete Antalya" sitesinin deneyimli bir haber editörüsün.
Aşağıdaki kaynak metindeki bilgilere dayanarak sitemiz için ÖZGÜN bir haber yaz.

Kurallar:
- Kaynak metindeki cümleleri kopyalama; aynı bilgileri kendi cümlelerinle, akıcı ve tarafsız haber diliyle anlat.
- Kaynakta olmayan hiçbir bilgi, isim, rakam, tarih veya yorum ekleme. Emin olmadığın şeyi yazma.
- Kişi adlarını kaynakta nasıl geçiyorsa öyle yaz; suç haberlerinde şüphelileri kaynakta baş harfleriyle verilmişse öyle bırak.
- Başlık en fazla 90 karakter, dikkat çekici ama abartısız olsun (tamamı büyük harf, ünlem ve tık tuzağı yok).
- Spot 1-2 cümle olsun.
- Metin 3-6 paragraf, toplam 150-350 kelime olsun. Paragrafları boş satırla ayır. Başlık/alt başlık, madde işareti kullanma.
- Metinde hiçbir gazete, ajans, site ya da muhabir adını kaynak olarak anma ("... haberine göre", "... muhabirinin aktardığı" gibi ifadeler kullanma).
- Kaynak metin bir haber değilse "uygun": false döndür. Şunlar haber DEĞİLDİR: köşe yazısı, makale, yorum/görüş yazısı, deneme, anı, röportaj-söyleşi, reklam/tanıtım içeriği, liste, video/galeri sayfası, çok kısa metinler. Antalya ile ilgisi yoksa da "uygun": false döndür.

Yalnızca şu biçimde JSON döndür:
{"uygun": true, "baslik": "...", "spot": "...", "metin": "...", "kategori": "<KATEGORILER>", "ilce": "<ILCELER veya boş>"}
"""


def yz_yaz(h, kaynak_metin):
    talimat = YZ_TALIMAT.replace("<KATEGORILER>", " | ".join(KATEGORILER)).replace("<ILCELER veya boş>", " | ".join(ILCE_ESLER) + " | (boş)")
    istek = {
        "contents": [{"role": "user", "parts": [{"text": f"{talimat}\n\nKAYNAK BAŞLIK: {h['baslik']}\nKAYNAK: {h['kaynak']}\n\nKAYNAK METİN:\n{kaynak_metin}"}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "maxOutputTokens": 2048},
    }
    for deneme in range(3):
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model_sec()}:generateContent",
                          headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}, json=istek, timeout=60)
        if r.status_code == 429:
            print("   Yapay zekâ kotası doldu, bekleniyor…")
            time.sleep(20 * (deneme + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
        parcalar = r.json()["candidates"][0]["content"]["parts"]
        metin = "".join(p.get("text", "") for p in parcalar).strip()
        metin = re.sub(r"^```(?:json)?|```$", "", metin).strip()
        return json.loads(metin)
    raise RuntimeError("Kota nedeniyle yazılamadı")


# ----------------------------------------------------------------- fotoğraf arşivi
ARSIV_SORGULARI = {
    "Antalya": "Antalya Konyaaltı beach", "Muratpaşa": "Kaleiçi Antalya", "Konyaaltı": "Konyaaltı Antalya",
    "Kepez": "Kepez Antalya", "Alanya": "Alanya castle", "Manavgat": "Manavgat", "Side": "Side Antalya",
    "Kemer": "Kemer Antalya", "Kaş": "Kaş Antalya", "Serik": "Belek Antalya", "Kumluca": "Kumluca", "Finike": "Finike",
    "Demre": "Demre Myra", "Gazipaşa": "Gazipaşa", "Elmalı": "Elmalı", "Korkuteli": "Korkuteli", "Aksu": "Perge",
    "Döşemealtı": "Döşemealtı", "Akseki": "Akseki", "İbradı": "İbradı", "Gündoğmuş": "Gündoğmuş",
    "Spor": "Antalya Stadium", "Turizm": "Antalya hotel beach", "Kültür-Sanat": "Aspendos theatre",
    "Sağlık": "Turkey ambulance", "Eğitim": "Akdeniz University", "Ekonomi": "Antalya greenhouse",
    "Yaşam": "Antalya Düden waterfall", "Asayiş": "Turkish police car", "Gündem": "Antalya Hadrian Gate",
}


def arsiv_doldur(arsiv):
    """Eksik konular için Wikimedia Commons'tan özgür lisanslı fotoğraf bulur (yalnızca bir kez)."""
    for konu, sorgu in ARSIV_SORGULARI.items():
        if arsiv.get(konu):
            continue
        try:
            r = requests.get("https://commons.wikimedia.org/w/api.php", headers={"User-Agent": "GazeteAntalya/1.0 (iletisim@gazeteantalyam.com)"}, params={
                "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 25,
                "gsrsearch": f"{sorgu} filetype:bitmap", "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1200,
            }, timeout=25)
            sonuc = []
            for s in (r.json().get("query", {}).get("pages", {}) or {}).values():
                ii = (s.get("imageinfo") or [{}])[0]
                meta = ii.get("extmetadata", {})
                lisans = (meta.get("LicenseShortName", {}) or {}).get("value", "")
                if ii.get("width", 0) < 1000 or ii.get("width", 0) < ii.get("height", 1) * 1.2:
                    continue
                if not re.search(r"CC BY|CC0|Public domain", lisans, re.I):
                    continue
                yazar = temizle((meta.get("Artist", {}) or {}).get("value", ""), 60) or "Wikimedia Commons"
                sonuc.append({"url": ii.get("thumburl") or ii.get("url"), "kredi": f"{yazar} / Wikimedia Commons ({lisans})",
                              "sayfa": ii.get("descriptionurl", "")})
            arsiv[konu] = sonuc[:6]
            print(f"Arşiv: {konu}: {len(arsiv[konu])} fotoğraf")
        except Exception as e:
            print(f"Arşiv: {konu} alınamadı: {e}")
    return arsiv


def arsiv_foto(arsiv, ilce, kategori, tohum):
    for konu in ([kategori] if kategori in ("Spor", "Asayiş", "Sağlık", "Eğitim") else []) + [ilce, kategori, "Antalya"]:
        if konu and arsiv.get(konu):
            return random.Random(tohum).choice(arsiv[konu])
    return None


# ----------------------------------------------------------------- yazma
def md_yaz(h, yz, foto, kaynak_url):
    tarih = datetime.fromtimestamp(h["ts"], TR)
    baslik = re.sub(r"\s+", " ", (yz or {}).get("baslik") or h["baslik"]).strip()
    dosya = f"{tarih:%Y%m%d}-{slug(baslik, 60)}-{hashlib.md5(h['link'].encode()).hexdigest()[:5]}.md"
    on = {
        "title": baslik,
        "date": tarih.isoformat(),
        "ozet": re.sub(r"\s+", " ", (yz or {}).get("spot") or h["ozet"] or "").strip(),
        "gorsel": foto["url"] if foto else "",
        "gorsel_kredi": foto["kredi"] if foto else "",
        "kategoriler": [(yz or {}).get("kategori") if (yz or {}).get("kategori") in KATEGORILER else h["kategori"]],
        "ilceler": [x for x in [(yz or {}).get("ilce") if (yz or {}).get("ilce") in ILCE_ESLER else h["ilce"]] if x],
        "manset": False,
        "son_dakika": False,
        "yazar": "Gazete Antalya",
        "otomatik": True,
        "yz": bool(yz),
        "kaynak": h["kaynak"],
        "kaynak_url": kaynak_url or h["link"],
    }
    govde = (yz or {}).get("metin", "").strip()
    with open(os.path.join(HABER_KLASOR, dosya), "w", encoding="utf-8") as f:
        f.write("---\n" + yaml.safe_dump(on, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + govde + "\n")
    return dosya


def duzenle_eskiler(arsiv):
    """Yapay zekâsız özet sayfalarını siler; kaynak fotoğraflarını arşiv fotoğraflarıyla değiştirir."""
    for ad in os.listdir(HABER_KLASOR):
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            with open(yol, encoding="utf-8") as f:
                icerik = f.read()
            if "\notomatik: true" not in icerik:
                continue
            if "\nyz: false" in icerik:
                os.remove(yol)
                print("Özet sayfası silindi:", ad)
                continue
            if re.search(r"\ngorsel_kredi: '?Fotoğraf: ", icerik):
                on, govde = icerik[4:].split("\n---\n", 1)
                v = yaml.safe_load(on)
                a = arsiv_foto(arsiv, (v.get("ilceler") or [""])[0], (v.get("kategoriler") or ["Gündem"])[0], v.get("kaynak_url", ad))
                v["gorsel"], v["gorsel_kredi"] = (a["url"], "Temsili fotoğraf: " + a["kredi"]) if a else ("", "")
                with open(yol, "w", encoding="utf-8") as f:
                    f.write("---\n" + yaml.safe_dump(v, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + govde)
                print("Fotoğraf değiştirildi:", ad)
        except Exception as e:
            print("Düzenlenemedi:", ad, e)


def eski_otomatikleri_sil(gun):
    sinir = datetime.now(TR) - timedelta(days=gun)
    silinen = 0
    for ad in os.listdir(HABER_KLASOR):
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            with open(yol, encoding="utf-8") as f:
                bas = f.read(3000)
            if "\notomatik: true" not in bas:
                continue
            m = re.search(r"\ndate: '?([0-9T:+\-.]+)", bas)
            if m and datetime.fromisoformat(m.group(1)) < sinir:
                os.remove(yol)
                silinen += 1
        except Exception:
            pass
    if silinen:
        print(f"{silinen} eski otomatik haber silindi.")


# ----------------------------------------------------------------- ana akış
def main():
    with open(os.path.join(KOK, "kaynaklar.yml"), encoding="utf-8") as f:
        yap = yaml.safe_load(f) or {}
    ayar = yap.get("ayarlar") or {}
    os.makedirs(HABER_KLASOR, exist_ok=True)
    simdi = int(time.time())
    bugun = datetime.now(TR).strftime("%Y-%m-%d")

    durum = json_oku(DURUM_DOSYA, {})
    gorulen = [g for g in durum.get("gorulen", []) if g["ts"] > simdi - 10 * 86400]
    if durum.get("gun") != bugun:
        durum["gun"], durum["yz_bugun"] = bugun, 0
    gorulen_link = {g["link"] for g in gorulen}

    # 1) Topla
    adaylar = []
    for k in yap.get("kaynaklar") or []:
        try:
            h = kaynak_oku(k)
            print(f"✓ {k['ad']}: {len(h)} haber")
            adaylar.extend(h)
        except Exception as e:
            print(f"✗ {k['ad']}: {e}")
    en_eski = simdi - int(ayar.get("en_eski_saat", 24)) * 3600
    adaylar = [h for h in adaylar if h["ts"] >= en_eski and h["link"] not in gorulen_link]
    adaylar.sort(key=lambda h: h["ts"], reverse=True)

    # 2) Benzerleri ayıkla
    secilen = []
    for h in adaylar:
        if any(benzer(h["baslik"], g["baslik"]) > 0.6 for g in [x for x in gorulen if x["ts"] > simdi - 2 * 86400] + secilen):
            gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
            continue
        secilen.append(h)
    limit = int(ayar.get("tur_basina_haber", 6))
    print(f"\n{len(adaylar)} yeni aday, {len(secilen)} tekil haber; bu turda en fazla {limit} tanesi işlenecek.\n")

    arsiv = arsiv_doldur(json_oku(ARSIV_DOSYA, {}))
    json_yaz(ARSIV_DOSYA, arsiv)
    yz_limit = int(ayar.get("yz_gunluk_limit", 150))
    yz_acik = bool(GEMINI_KEY) and ayar.get("yapay_zeka", True)
    if not yz_acik:
        print("Yapay zekâ kapalı (GEMINI_API_KEY tanımlı değil) → bu turda haber eklenmeyecek.")

    # 3) İşle
    yazilan = 0
    for h in secilen[:limit]:
        print(f"• {h['baslik'][:80]}  [{h['kaynak']}]")
        url = gercek_url(h)
        if KOSE_RE.search(tr_kucuk(url)) or KOSE_RE.search(tr_kucuk(h["baslik"])):
            print("   Köşe yazısı/makale, atlandı.")
            gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
            continue
        metin, og = sayfa_oku(url)
        yz = None
        if yz_acik and durum["yz_bugun"] < yz_limit and len(metin) > 400:
            try:
                yz = yz_yaz(h, metin)
                durum["yz_bugun"] += 1
                if not yz.get("uygun", True) or not yz.get("metin"):
                    print("   Yapay zekâ uygun bulmadı, atlandı.")
                    gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
                    continue
            except Exception as e:
                print("   Yapay zekâ hatası:", e)
                yz = None
        if yz and any(benzer(yz.get("baslik", ""), g["baslik"]) > 0.6 for g in gorulen if g["ts"] > simdi - 2 * 86400):
            print("   Aynı olay daha önce yazılmış, atlandı.")
            gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
            continue
        if not yz:
            print("   Yapay zekâ metni yok, yayımlanmadı.")
            if yz_acik and len(metin) <= 400:
                gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
            continue
        foto = None
        kaynak_gorsel = h["gorsel"] or og
        if kaynak_gorsel and ayar.get("kaynak_gorseli", False):
            foto = {"url": kaynak_gorsel, "kredi": ""}
        if not foto:
            a = arsiv_foto(arsiv, h["ilce"], h["kategori"], h["link"])
            if a:
                foto = {"url": a["url"], "kredi": f"Temsili fotoğraf: {a['kredi']}"}
        dosya = md_yaz(h, yz, foto, url)
        print(f"   → {dosya} ({'yapay zekâ' if yz else 'özet'})")
        gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
        if yz:
            gorulen.append({"link": h["link"] + "#yz", "baslik": yz.get("baslik", ""), "ts": simdi})
        yazilan += 1

    duzenle_eskiler(arsiv)
    eski_otomatikleri_sil(int(ayar.get("en_eski_gun", 30)))
    durum["gorulen"] = gorulen[-3000:]
    durum["son_calisma"] = datetime.now(TR).isoformat()
    json_yaz(DURUM_DOSYA, durum)
    print(f"\nBu turda {yazilan} haber eklendi. Bugün yapay zekâ ile yazılan: {durum['yz_bugun']}/{yz_limit}")


if __name__ == "__main__":
    main()
