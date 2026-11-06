#!/usr/bin/env python3
"""Gazete Antalya - otomatik haber hazırlayıcı.

1. kaynaklar.yml içindeki RSS kaynaklarından Antalya haberlerini toplar.
2. Daha önce yayımlanmış ya da benzeri yayımlanmış haberleri ayıklar.
3. Her yeni haber için kaynağın sayfasını okur; GEMINI_API_KEY tanımlıysa
   yapay zekâ ile özgün bir haber metni yazdırır, değilse kısa özet sayfası oluşturur.
4. Görsel: scripts/gorsel_sec.py (izinli gerçek fotoğraf → adı geçen yerin arşiv fotoğrafı → fotoğrafsız kapak).
5. Sonucu content/haber/ altına Markdown dosyası olarak yazar (panelden düzenlenebilir).
"""
import calendar
import difflib
import hashlib
import html
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml

import gorsel_sec as gs

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = timezone(timedelta(hours=3))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HABER_KLASOR = os.path.join(KOK, "content", "haber")
DURUM_DOSYA = os.path.join(KOK, "data", "otomatik_durum.json")
ARSIV_DOSYA = os.path.join(KOK, "data", "foto_arsivi.json")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GUNLUK_KOTA = {"bitti": False}   # Gemini günlük kotası (429 PerDay) dolduysa bu turda yeni istek denenmez
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


_modeller = []
_biten = set()          # bugün (Pasifik günü) günlük kotası dolan modeller; ücretsiz kota her model için ayrıdır


def _surum(ad):
    s = re.findall(r"(\d+(?:\.\d+)?)", ad)
    return float(s[0]) if s else 0.0


def yedek_modeller():
    """Kotası dolmamış, aynı işe uygun 'lite' modeller (düşünme kipi kapalı; JSON yanıtı kısa). Sıra: model listesindeki sıra."""
    model_sec()
    return [m for m in _modeller if "lite" in m and _surum(m) >= 2.5 and m not in _biten]


def yedek_model():
    """Seçili model yoğunken (503) bir kez denenecek ikinci model: listedeki bir sonraki uygun model."""
    model_sec()
    return next((m for m in yedek_modeller() if m != _model), "")


def model_bitti(model):
    """Bir modelin günlük kotası doldu (429 PerDay): o gün kotası dolmamış bir sonraki modele geçilir. Yoksa '' döner
    (o zaman bütün modellerin kotası dolmuştur)."""
    global _model
    model_sec()
    _biten.add(model)
    kalan = yedek_modeller()
    if kalan:
        if _model in _biten:
            print(f"   {model} günlük kotası doldu; {kalan[0]} modeline geçildi")
            _model = kalan[0]
        return _model
    return ""


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
            _modeller[:] = [a[3] for a in adaylar]
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
{"uygun": true, "baslik": "...", "spot": "...", "metin": "...", "kategori": "<KATEGORILER>", "ilce": "<ILCELER veya boş>", "onem": 3,
 "gorsel_konu": <GORSEL_KONU>}

"onem": Haberin Antalya okuru için önemi, 1-5 tam sayı. 5: il genelini etkileyen çok önemli gelişme (can kaybı çok olan kaza/afet,
il çapında karar, Antalya'da yapılan büyük ulusal/uluslararası olay). 4: önemli il/ilçe gündemi (büyük yatırım, önemli karar, takımın
kritik maçı). 3: olağan haber. 2: küçük yerel haber, duyuru. 1: magazin, tanıtım, rutin etkinlik.

"gorsel_konu": Görsel seçimi için yazdığın haberin konusunun ayrıştırması.
<GORSEL_KURAL>
"""


def yz_yaz(h, kaynak_metin):
    talimat = YZ_TALIMAT.replace("<KATEGORILER>", " | ".join(KATEGORILER)).replace("<ILCELER veya boş>", " | ".join(ILCE_ESLER) + " | (boş)")
    talimat = talimat.replace("<GORSEL_KONU>", gs.KONU_ALANLARI).replace("<GORSEL_KURAL>", gs.KONU_KURALLARI)
    istek = {
        "contents": [{"role": "user", "parts": [{"text": f"{talimat}\n\nKAYNAK BAŞLIK: {h['baslik']}\nKAYNAK: {h['kaynak']}\n\nKAYNAK METİN:\n{kaynak_metin}"}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "maxOutputTokens": 3072},
    }
    if GUNLUK_KOTA["bitti"]:
        raise RuntimeError("Gemini günlük kotası dolu")
    for deneme in range(3):
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model_sec()}:generateContent",
                          headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}, json=istek, timeout=60)
        if r.status_code == 429:
            if gs.GUNLUK_KOTA_RE.search(r.text or ""):
                onceki_model = model_sec()
                if model_bitti(onceki_model) and model_sec() != onceki_model:
                    continue                  # başka bir modelin günlük kotası var
                GUNLUK_KOTA["bitti"] = True
                raise RuntimeError("Gemini günlük kotası doldu; kota yenilenince yeniden denenecek")
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


# Fotoğraf seçimi scripts/gorsel_sec.py içindedir (gerçek → arşiv → fotoğrafsız kapak).
# Eski hattın genel stok/rastgele fotoğraf araması (Unsplash/Openverse/Commons) kaldırıldı.


# ----------------------------------------------------------------- yazma
# ----------------------------------------------------------------- başlık ve etiketler
# Kaynaktan TAMAMI BÜYÜK HARFLE gelen başlıklar sitenin başlık yazımına (her kelimenin ilk harfi büyük) çevrilir.
KISALTMALAR = set("""TL ABD AB AK CHP MHP DEM TBMM TFF KKTC ÇED TOKİ SGK DSİ ATSO ALTSO ATB ATİB TÜRSAK BAİB AKUT AFAD UEFA FIFA NATO BM
AA İHA DHA TRT PTT TCDD YKS LGS KPSS ALES ÖSYM MEB TÜİK TÜBİTAK KDV ÖTV LPG CNG AVM OSB GES HES COP COP31 ASAT MTA TEMA TSK MİT EGM
HSK YSK TÜSİAD MÜSİAD TOBB TESK KOAH MR BT AŞ FK SK BB İŞKUR KOSGEB TMMOB TTB TDK AKM ATM SMS GSM PKK FETÖ DEAŞ IŞİD
İYİ ODTÜ İTÜ YTÜ ALKÜ AÜ AKÜ EXPO NBA FIBA EHF TVF THF TBF UNESCO WHO UNICEF FAO IMF OECD G20""".split())
KUCUK_KALAN = {"ve", "ile", "ya", "veya", "de", "da", "ki", "mi", "mı", "mu", "mü"}


def _tr_kucuk(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def _tr_buyuk_ilk(s):
    if not s:
        return s
    ilk = {"i": "İ", "ı": "I"}.get(s[0], s[0].upper())
    return ilk + s[1:]


def _kelime_duzelt(k, ilk):
    on = re.match(r"^[^\wÇĞİÖŞÜçğıöşü]*", k).group(0)
    son = re.search(r"[^\wÇĞİÖŞÜçğıöşü.]*$", k[len(on):]).group(0)
    govde = k[len(on):len(k) - len(son)] if son else k[len(on):]
    if not govde:
        return k
    if "-" in govde and not re.search(r"\d", govde):
        return on + "-".join(_kelime_duzelt(p, True) for p in govde.split("-")) + son
    parca = re.split(r"(['’])", govde, maxsplit=1)
    kok, ayrac, ek = (parca + ["", ""])[:3]
    if kok in KISALTMALAR or kok.rstrip(".") in KISALTMALAR or (re.search(r"\d", kok) and re.search(r"[A-ZÇĞİÖŞÜ]", kok)):
        yeni = kok
    elif not ilk and _tr_kucuk(kok) in KUCUK_KALAN:
        yeni = _tr_kucuk(kok)
    else:
        yeni = _tr_buyuk_ilk(_tr_kucuk(kok))
    return on + yeni + ayrac + _tr_kucuk(ek) + son


def baslik_duzelt(baslik):
    """'ALANYA'DA MUTFAK TÜPÜNE ZAM: FİYAT 1.855 TL'YE YÜKSELDİ' → 'Alanya'da Mutfak Tüpüne Zam: Fiyat 1.855 TL'ye Yükseldi'.
    Yalnızca harflerinin çoğu büyük olan başlıklar değiştirilir; kısaltmalar (TL, CHP, COP31…) korunur."""
    harf = [c for c in baslik if c.isalpha()]
    if len(harf) < 8 or sum(c.isupper() for c in harf) / len(harf) < 0.8:
        return baslik
    kelimeler = baslik.split(" ")
    return " ".join(_kelime_duzelt(k, i == 0 or kelimeler[i - 1].endswith((":", "!", "?", "."))) for i, k in enumerate(kelimeler))


GENEL_ETIKET = {"antalya", "türkiye", "turkiye", "belediye", "valilik", "polis", "jandarma", "hastane", "okul"}


def etiketler_cikar(konu):
    """Haberin kişi ve kurum etiketleri: kamuoyunca tanınan ana kişiler, kurumlar ve ana özne (takım/kurum); en çok 5.
    Hassas haberlerde yalnızca kamusal kişiler ve kurumlar etiketlenir (özel kişiler etiketlenmez)."""
    if not isinstance(konu, dict):
        return []
    adlar = [k.get("ad", "") for k in konu.get("ana_kisiler") or [] if isinstance(k, dict) and k.get("kamusal") and not k.get("hassas_rol")]
    oz = konu.get("ana_ozne") or {}
    if oz.get("tur") in ("takim", "kurum"):
        adlar.append(oz.get("ad", ""))
    adlar += list(konu.get("kurumlar") or [])[:3]
    sonuc, gorulen = [], set()
    for a in adlar:
        a = re.sub(r"\s+", " ", str(a or "")).strip(" .,-")
        k = _tr_kucuk(a)
        if len(a) < 3 or len(a) > 60 or k in GENEL_ETIKET or k in gorulen:
            continue
        gorulen.add(k)
        sonuc.append(a)
    return sonuc[:5]


def gecmis_haberleri_duzelt(bag):
    """Bir kerelik ve tekrarlanabilir: büyük harfli eski başlıklar düzeltilir; etiketi olmayan haberlere, konu ayrıştırması
    önbellekte varsa etiket eklenir. Metin, adres (URL), tarih ve görsel değişmez. Değişiklik yoksa dosyaya yazılmaz."""
    duzeltilen = 0
    for ad in sorted(os.listdir(HABER_KLASOR)):
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            v, govde = gs.md_oku(yol)
        except Exception:
            continue
        if not v.get("otomatik"):
            continue
        degisti = False
        if "etiketler" not in v:
            ob = bag.onbellek_al("konu:" + gs.imza(gs.KURAL_SURUMU, v.get("title", ""), v.get("ozet", "")))
            et = etiketler_cikar((ob or {}).get("konu"))
            if et:
                v["etiketler"] = et
                degisti = True
        yeni = baslik_duzelt(v.get("title", ""))
        if yeni != v.get("title", ""):
            v.setdefault("baslik_ozgun", v["title"])
            v["title"] = yeni
            degisti = True
        if degisti:
            gs.md_yaz(yol, v, govde)
            duzeltilen += 1
    if duzeltilen:
        print(f"Başlığı/etiketi düzeltilen eski haber: {duzeltilen}")


def md_yaz(h, yz, kaynak_url, secim, etiketler=None):
    tarih = datetime.fromtimestamp(h["ts"], TR)
    baslik = baslik_duzelt(re.sub(r"\s+", " ", (yz or {}).get("baslik") or h["baslik"]).strip())
    dosya = f"{tarih:%Y%m%d}-{slug(baslik, 60)}-{hashlib.md5(h['link'].encode()).hexdigest()[:5]}.md"
    on = {
        "title": baslik,
        "date": tarih.isoformat(),
        "ozet": re.sub(r"\s+", " ", (yz or {}).get("spot") or h["ozet"] or "").strip(),
        "gorsel": "",
        "gorsel_kredi": "",
        "kategoriler": [(yz or {}).get("kategori") if (yz or {}).get("kategori") in KATEGORILER else h["kategori"]],
        "ilceler": [x for x in [(yz or {}).get("ilce") if (yz or {}).get("ilce") in ILCE_ESLER else h["ilce"]] if x],
        "etiketler": list(etiketler or []),
        "onem": max(1, min(5, int((yz or {}).get("onem") or 3))) if str((yz or {}).get("onem") or "3").strip().isdigit() else 3,
        "manset": False,
        "son_dakika": False,
        "yazar": "Gazete Antalya",
        "otomatik": True,
        "yz": bool(yz),
        "kaynak": h["kaynak"],
        "kaynak_url": kaynak_url or h["link"],
    }
    gs.secimi_uygula(on, secim)
    if True:
        for alan in ("manset", "son_dakika", "yazar", "otomatik", "yz", "kaynak", "kaynak_url"):
            on[alan] = on.pop(alan)
    govde = (yz or {}).get("metin", "").strip()
    with open(os.path.join(HABER_KLASOR, dosya), "w", encoding="utf-8") as f:
        f.write("---\n" + yaml.safe_dump(on, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + govde + "\n")
    return dosya


def duzenle_eskiler():
    """Yapay zekâsız özet sayfalarını siler. (Fotoğraf yenileme gorsel_sec.toplu_duzelt ile yapılır.)"""
    for ad in sorted(os.listdir(HABER_KLASOR), reverse=True):
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            with open(yol, encoding="utf-8") as f:
                icerik = f.read()
            if "\notomatik: true" in icerik and "\nyz: false" in icerik:
                os.remove(yol)
                print("Özet sayfası silindi:", ad)
        except Exception as e:
            print("Düzenlenemedi:", ad, e)


def eski_otomatikleri_sil(gun):
    """gun <= 0: hiçbir haber silinmez (arşiv). Arama motorlarının dizine aldığı sayfalar kalıcı kalır."""
    if gun <= 0:
        return
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
    bugun = gs.gemini_gunu()   # Gemini kotası Pasifik saatiyle yenilenir

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

    # Önbellekte yalnızca yeni hattın Commons arama sonuçları tutulur; eski hattın stok sorgu önbelleği silinir
    arsiv = {k: v for k, v in json_oku(ARSIV_DOSYA, {}).items() if k.startswith(("c6:", "wd:", "kat:", "cf:", "kut:", "kur:"))}
    bag = gs.Baglam(ayar, durum, GEMINI_KEY if ayar.get("yapay_zeka", True) else "", model_sec, arsiv)
    bag.yedek_model_fn = yedek_model
    bag.model_bitti_fn = model_bitti
    bag.kalan_modeller_fn = yedek_modeller
    bag.biten_modeller = _biten
    # Günlük kotası dolan modeller (Pasifik günü). Eski kayıtta yalnızca "kota_bitti" varsa o gün yalnızca ana model dolmuş sayılır;
    # diğer modeller denenir (her modelin ücretsiz kotası ayrıdır).
    km = durum["gorsel"].get("kota_modeller") or {}
    if km.get("gun") == gs.gemini_gunu():
        _biten.update(km.get("modeller") or [])
    elif durum["gorsel"].get("kota_bitti") == gs.gemini_gunu() and GEMINI_KEY:
        _biten.add(model_sec())
    if GEMINI_KEY and _biten:
        if model_sec() in _biten:
            model_bitti(model_sec())          # kotası olan bir sonraki modele geçer
        if yedek_modeller():
            durum["gorsel"].pop("kota_bitti", None)
    gorsel_tur_limit = int(ayar.get("gorsel_tur_limit", 6))
    print("Görsel hattı:", gs.KURAL_SURUMU, "(gerçek → arşiv → fotoğrafsız kapak)")
    yz_limit = int(ayar.get("yz_gunluk_limit", 150))
    yz_acik = bool(GEMINI_KEY) and ayar.get("yapay_zeka", True)
    if not yz_acik:
        print("Yapay zekâ kapalı (GEMINI_API_KEY tanımlı değil) → bu turda haber eklenmeyecek.")

    # Karşılaştırmalı görsel denemesi (haber dosyalarını değiştirmez). Aynı liste ve kural sürümü için bir kez tamamlanır;
    # kota nedeniyle eksik kalan satırlar sonraki turlarda (önbellekteki kararlar korunarak) yeniden denenir.
    deneme = [d for d in (ayar.get("gorsel_deneme") or []) if isinstance(d, str)]
    deneme_imza = gs.imza(gs.KURAL_SURUMU, ayar.get("gorsel_deneme_no", 1), *deneme)
    onceki = durum["gorsel"].get("deneme") or {}
    if deneme and onceki.get("imza") != deneme_imza and onceki.get("deneme_sayisi_imza") != f"{deneme_imza}:8":
        print(f"\nGörsel denemesi: {len(deneme)} haber")
        rapor = gs.deneme_calistir(bag, deneme)
        sayi = (onceki.get("deneme_sayisi", 0) + 1) if onceki.get("hedef") == deneme_imza else 1
        rapor.update(hedef=deneme_imza, deneme_sayisi=sayi, imza=deneme_imza if not rapor["eksik"] else None,
                     deneme_sayisi_imza=f"{deneme_imza}:{sayi}")
        durum["gorsel"]["deneme"] = rapor
        print(f"Görsel denemesi: {rapor['eksik']} satır kota nedeniyle eksik" if rapor["eksik"] else "Görsel denemesi tamamlandı")

    # 3) İşle
    if durum["gorsel"].get("kota_bitti") == gs.gemini_gunu():
        GUNLUK_KOTA["bitti"] = True
        print("Gemini günlük kotası dolu; kota yenilenene kadar yapay zekâ istekleri denenmeyecek.")
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
        kaynak_gorsel = h["gorsel"] or og
        kat = yz.get("kategori") if yz.get("kategori") in KATEGORILER else h["kategori"]
        ilce = yz.get("ilce") if yz.get("ilce") in ILCE_ESLER else h["ilce"]
        hb = {"baslik": yz.get("baslik", ""), "spot": yz.get("spot", ""), "kategori": kat, "ilce": ilce,
              "tarih": datetime.fromtimestamp(h["ts"], TR).date(), "kaynak_url": url or "", "kaynak_gorsel": kaynak_gorsel}
        konu = gs.konu_dogrula(yz.get("gorsel_konu"))
        if gorsel_tur_limit <= 0:
            secim = gs.kapak_secimi(kat, "Bu turun görsel kontrol sınırı doldu; fotoğrafsız kapak, sonraki turda yeniden değerlendirilecek.",
                                    {"yapildi": False, "sonuc": "yapılamadı", "tarih": datetime.now(TR).isoformat(timespec="seconds"),
                                     "yontem": "tur sınırı"}, gs.konu_imzasi(hb["baslik"], hb["spot"], kat))
            secim["bekliyor"] = True
        else:
            gorsel_tur_limit -= 1
            try:
                konu = konu or gs.konu_ayristir(bag, hb["baslik"], hb["spot"], yz.get("metin", ""))
            except Exception as e:
                print("   Konu ayrıştırılamadı:", e)
            secim, _ = gs.gorsel_sec(bag, hb, konu)
        print(f"   Görsel: {secim['tur']} – {secim['gerekce'][:100]}")
        dosya = md_yaz(h, yz, url, secim, etiketler_cikar(konu))
        print(f"   → {dosya} ({'yapay zekâ' if yz else 'özet'})")
        gorulen.append({"link": h["link"], "baslik": h["baslik"], "ts": simdi})
        if yz:
            gorulen.append({"link": h["link"] + "#yz", "baslik": yz.get("baslik", ""), "ts": simdi})
        yazilan += 1

    duzenle_eskiler()
    try:
        gecmis_haberleri_duzelt(bag)
    except Exception as e:
        print("Eski haber başlık/etiket düzeltmesi yapılamadı:", e)
    if GUNLUK_KOTA["bitti"]:
        bag.g["kota_bitti"] = gs.gemini_gunu()

    # Otomatik görsellerin düzeltilmesi: onaydan sonra eski haberler küçük gruplar hâlinde;
    # onaydan önce yalnızca kota yüzünden ertelenmiş yeni hat kayıtları yeniden denenir.
    toplu = bool(ayar.get("gorsel_toplu_duzeltme", False))
    n = gs.toplu_duzelt(bag, int(ayar.get("gorsel_duzeltme_tur", 3)), yalniz_bekleyen=not toplu)
    if n:
        print(f"Görseli düzeltilen haber: {n}")
    if gs.kf:
        gs.kf.kapat()
    if bag.notlar:
        print("Kota/hata nedeniyle atlanan görsel kontrolleri:", *bag.notlar, sep="\n  ")
    print(f"Görsel hattı yapay zekâ kullanımı: bu tur {bag.yz_tur}, bugün {bag.g['yz_bugun']}/{bag.yz_gunluk}")
    cf = [k for k in arsiv if k.startswith("cf:")]
    for k in cf[:-2500]:          # Commons dosya bilgisi önbelleği büyümesin (en eski kayıtlar silinir)
        arsiv.pop(k, None)
    json_yaz(ARSIV_DOSYA, arsiv)
    eski_otomatikleri_sil(int(ayar.get("en_eski_gun", 0)))
    durum["gorulen"] = gorulen[-3000:]
    durum["son_calisma"] = datetime.now(TR).isoformat()
    durum["gorsel"]["kota_modeller"] = {"gun": gs.gemini_gunu(), "modeller": sorted(_biten)}
    json_yaz(DURUM_DOSYA, durum)
    print(f"\nBu turda {yazilan} haber eklendi. Bugün yapay zekâ ile yazılan: {durum['yz_bugun']}/{yz_limit}")


if __name__ == "__main__":
    main()
