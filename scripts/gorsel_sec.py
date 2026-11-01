#!/usr/bin/env python3
"""Gazete Antalya – görsel seçim hattı.

Sıra:
  1. Haberin konusu ayrıştırılır (ana olay, somut nesne/faaliyet, kişi, kurum, yer, tarih, hassasiyet).
  2. Gerçek haber fotoğrafı: yalnızca kullanım izni kayıtlı kaynaklardan (data/gorsel_izinleri.yml) ve
     görselin aynı olay, yer ve tarihle ilişkisi kaynak sayfası/açıklamasından doğrulanırsa.
  3. Stok: somut konuya göre birkaç aday getirilir; adayların görselleri Gemini ile başlık ve spotla
     birlikte gerçekten incelenir. Kontrol yapılamazsa stok fotoğraf yayımlanmaz.
  4. Hiçbiri geçmezse marka renkleriyle hazırlanmış sade "Kategori görseli" kullanılır.

Yalnızca otomatik seçilmiş görsellere dokunur; elle seçilmiş/sabitlenmiş ya da kökeni belirsiz görseller korunur.
"""
import base64
import hashlib
import html
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests
import yaml

SURUM = "g5"
KURAL_TARIHI = "2026-11-01"
KURAL_SURUMU = f"{SURUM}-{KURAL_TARIHI}"   # kontrol kuralları değişince artırılır → önbellek ve kayıtlar yeniden değerlendirilir

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HABER_KLASOR = os.path.join(KOK, "content", "haber")
IZIN_DOSYA = os.path.join(KOK, "data", "gorsel_izinleri.yml")
TR = timezone(timedelta(hours=3))
UA_BOT = "GazeteAntalya/1.0 (https://www.gazeteantalyam.com; iletisim@gazeteantalyam.com)"
UTM = "utm_source=gazete_antalya&utm_medium=referral"

KATEGORI_DOSYA = {
    "Gündem": "gundem", "Asayiş": "asayis", "Ekonomi": "ekonomi", "Turizm": "turizm", "Spor": "spor",
    "Sağlık": "saglik", "Eğitim": "egitim", "Kültür-Sanat": "kultur-sanat", "Yaşam": "yasam",
}
HASSAS = {"suç", "sağlık", "kaza", "ölüm", "afet", "çocuk"}
OTOMATIK_HOST = re.compile(r"^https://(images\.unsplash\.com|(upload|thumb)\.wikimedia\.org)/")
YABANCI_YER = re.compile(r"\b(india|indian|thailand|bangkok|new york|nyc|london|paris|usa|america|american|tokyo|china|chinese|"
                         r"dubai|mumbai|delhi|bali|vietnam|mexico|brazil|nigeria|kenya|philippines|indonesia|korea)\b", re.I)
SERBEST_LISANS = re.compile(r"^(CC0|CC BY(-SA)? \d\.\d|CC BY(-SA)?|Public domain|PD)", re.I)


class KotaHatasi(Exception):
    pass


class ServisHatasi(Exception):
    pass


def simdi_tr():
    return datetime.now(TR)


def imza(*parcalar):
    return hashlib.sha1("|".join(str(p) for p in parcalar).encode()).hexdigest()[:12]


def konu_imzasi(baslik, ozet, kategori):
    return imza(re.sub(r"\s+", " ", baslik or "").strip(), re.sub(r"\s+", " ", ozet or "").strip(), kategori or "")


def temiz(s, n=300):
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]


def kucult(s):
    s = (s or "").replace("I", "ı").replace("İ", "i").lower()
    return s.translate(str.maketrans("çğıöşüâî", "cgiosuai"))


def json_ayikla(metin):
    metin = re.sub(r"^```(?:json)?|```$", "", (metin or "").strip()).strip()
    return json.loads(metin)


# ----------------------------------------------------------------- bağlam: ayarlar, kota, önbellek
class Baglam:
    """Bir çalışma turunun ayarları, kota sayaçları ve önbelleği. Durum, data/otomatik_durum.json içinde 'gorsel' anahtarında saklanır."""

    def __init__(self, ayar, durum, gemini_key="", unsplash_key="", model_fn=None, arsiv=None):
        self.ayar = ayar or {}
        self.gemini_key = gemini_key
        self.unsplash_key = unsplash_key
        self.model_fn = model_fn or (lambda: "gemini-2.5-flash-lite")
        self.arsiv = arsiv if arsiv is not None else {}
        g = durum.setdefault("gorsel", {})
        bugun = simdi_tr().strftime("%Y-%m-%d")
        if g.get("gun") != bugun:
            g["gun"], g["yz_bugun"] = bugun, 0
        g.setdefault("onbellek", {})
        saat_once = time.time() - 3600
        g["unsplash"] = [t for t in g.get("unsplash", []) if t > saat_once]
        self.g = g
        self.yz_tur = 0
        self.son_yz = 0.0
        self.izinler = izinleri_oku()
        self.notlar = []          # kota/hata nedeniyle atlanan kontroller (rapor için)

    # sınırlar
    @property
    def yz_gunluk(self):
        return int(self.ayar.get("gorsel_yz_gunluk", 100))

    @property
    def unsplash_saatlik(self):
        return int(self.ayar.get("unsplash_saatlik", 35))

    def yz_hakki(self, adet=1):
        return bool(self.gemini_key) and self.g["yz_bugun"] + adet <= self.yz_gunluk

    def unsplash_hakki(self):
        return bool(self.unsplash_key) and len(self.g["unsplash"]) < self.unsplash_saatlik

    def unsplash_say(self):
        self.g["unsplash"].append(time.time())

    # önbellek
    def onbellek_al(self, anahtar):
        return self.g["onbellek"].get(anahtar)

    def onbellek_koy(self, anahtar, deger):
        ob = self.g["onbellek"]
        ob[anahtar] = deger
        if len(ob) > 1500:
            for k in sorted(ob, key=lambda k: ob[k].get("t", ""))[: len(ob) - 1500]:
                ob.pop(k, None)

    # Gemini
    def gemini(self, parcalar, en_fazla=1024, sicaklik=0.1):
        if not self.gemini_key:
            raise ServisHatasi("GEMINI_API_KEY tanımlı değil")
        if not self.yz_hakki():
            raise KotaHatasi(f"görsel hattı günlük yapay zekâ sınırı ({self.yz_gunluk}) doldu")
        bekle = 4.5 - (time.time() - self.son_yz)   # ücretsiz kotada dakikalık sınırı aşmamak için
        if bekle > 0:
            time.sleep(bekle)
        istek = {"contents": [{"role": "user", "parts": parcalar}],
                 "generationConfig": {"temperature": sicaklik, "responseMimeType": "application/json", "maxOutputTokens": en_fazla}}
        for deneme in range(2):
            self.son_yz = time.time()
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_fn()}:generateContent",
                                  headers={"x-goog-api-key": self.gemini_key, "Content-Type": "application/json"}, json=istek, timeout=90)
            except requests.RequestException as e:
                raise ServisHatasi(f"bağlantı: {e}")
            if r.status_code == 429:
                if deneme == 0:
                    time.sleep(20)
                    continue
                raise KotaHatasi("Gemini kotası doldu (429)")
            if r.status_code >= 400:
                raise ServisHatasi(f"Gemini {r.status_code}: {r.text[:160]}")
            self.g["yz_bugun"] += 1
            self.yz_tur += 1
            try:
                aday = r.json()["candidates"][0]
                metin = "".join(p.get("text", "") for p in aday["content"]["parts"])
                return json_ayikla(metin)
            except Exception as e:
                raise ServisHatasi(f"Gemini yanıtı okunamadı: {e}")
        raise KotaHatasi("Gemini kotası doldu")


def izinleri_oku():
    try:
        with open(IZIN_DOSYA, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("kaynaklar") or []
    except Exception:
        return []


def kaynak_izni(izinler, url):
    """URL'nin alan adı için kayıtlı izin. Kayıt yoksa izin yok sayılır."""
    m = re.match(r"https?://([^/]+)", url or "")
    if not m:
        return None
    host = m.group(1).lower()
    for k in izinler:
        alan = str(k.get("alan", "")).lower()
        if alan and (host == alan or host.endswith("." + alan)):
            return k
    return None


# ----------------------------------------------------------------- 1) konu ayrıştırma
KONU_ALANLARI = """{"ana_olay": "...", "nesne_faaliyet": "...", "kisiler": ["..."], "kurumlar": ["..."], "yer": "...",
 "olay_tarihi": "YYYY-AA-GG veya boş", "hassas": "yok | suç | sağlık | kaza | ölüm | afet | çocuk",
 "gercek_arama": "...", "yer_arama": "...", "stok_sorgulari": ["...", "..."], "kacin": ["..."]}"""

KONU_KURALLARI = """Alanlar:
- ana_olay: Haberde ne oldu? Tek cümle (Türkçe).
- nesne_faaliyet: Haberi temsil eden SOMUT nesne veya faaliyet (Türkçe, ör. "zeytin fidanı dikimi", "yol asfaltlama", "devrilmiş otomobil", "çocuk korosu"). Genel kategori kelimesi (gündem, ekonomi, spor, şehir, haber) yazma.
- kisiler / kurumlar: Haberde adı geçen kişi ve kurumlar (yoksa boş liste).
- yer: Olayın geçtiği yer (ilçe, mahalle, tesis vb.).
- olay_tarihi: Metinde açıkça geçiyorsa olayın tarihi; yoksa boş.
- hassas: Suç, sağlık durumu, kaza, ölüm, afet ya da çocuk içeriyorsa ilgili değer; değilse "yok".
- gercek_arama: Aynı olayın gerçek fotoğrafını arşivlerde bulmak için kısa arama ifadesi (olayın adı, kurum, yer; ör. "COP31 Antalya"). Belirli, adı konmuş bir olay değilse boş.
- yer_arama: Haber doğrudan belirli bir mekânla ilgiliyse (antik kent, müze, plaj vb.) o mekânın adı; değilse boş.
- stok_sorgulari: Temsili fotoğraf aramak için 2-3 adet, 2-4 kelimelik İNGİLİZCE ifade. Her biri somut nesne veya faaliyeti anlatsın
  (ör. "olive sapling planting", "asphalt paving road", "overturned car roadside", "children choir singing").
  Kişi, takım, marka, kurum adı ve genel kategori kelimesi (news, city, sport, economy, business, meeting) kullanma.
  Ülkeye özgü görünen polis/ambulans/itfaiye araçlarını sorgulama; ülkeden bağımsız ayrıntılar seç (ör. "emergency lights night", "fire hose water").
  Hassas haberlerde insansız sahneler seç.
- kacin: Bu haberde yanıltıcı olabilecek görsel türleri (ör. "tanınabilir yüzler", "sınıf ortamı", "yabancı tabelalar")."""


def konu_dogrula(k):
    if not isinstance(k, dict):
        return None
    sonuc = {
        "ana_olay": temiz(k.get("ana_olay"), 200),
        "nesne_faaliyet": temiz(k.get("nesne_faaliyet"), 120),
        "kisiler": [temiz(x, 60) for x in (k.get("kisiler") or []) if temiz(x)][:6],
        "kurumlar": [temiz(x, 80) for x in (k.get("kurumlar") or []) if temiz(x)][:6],
        "yer": temiz(k.get("yer"), 80),
        "olay_tarihi": k.get("olay_tarihi") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k.get("olay_tarihi") or "")) else "",
        "hassas": str(k.get("hassas") or "yok").strip().lower(),
        "gercek_arama": temiz(k.get("gercek_arama"), 80),
        "yer_arama": temiz(k.get("yer_arama"), 60),
        "stok_sorgulari": [],
        "kacin": [temiz(x, 60) for x in (k.get("kacin") or []) if temiz(x)][:5],
    }
    if sonuc["hassas"] not in HASSAS:
        sonuc["hassas"] = "yok"
    genel = re.compile(r"^(news|city|sport|sports|economy|business|meeting|event|people|turkey|antalya|politics)$", re.I)
    for q in k.get("stok_sorgulari") or []:
        q = re.sub(r"[^A-Za-z \-]", "", str(q)).strip()
        if 2 <= len(q.split()) <= 5 and not genel.match(q):
            sonuc["stok_sorgulari"].append(q.lower())
    sonuc["stok_sorgulari"] = sonuc["stok_sorgulari"][:3]
    if not sonuc["nesne_faaliyet"] or not sonuc["stok_sorgulari"]:
        return None
    return sonuc


def konu_ayristir(bag, baslik, spot, metin):
    anahtar = "konu:" + imza(KURAL_SURUMU, baslik, spot)
    ob = bag.onbellek_al(anahtar)
    if ob and ob.get("konu"):
        return ob["konu"]
    talimat = ("Bir haberin görselini seçmek için haberin konusunu ayrıştır. Yalnızca metinde geçen bilgileri kullan, uydurma.\n"
               + KONU_KURALLARI + "\n\nYalnızca şu biçimde JSON döndür:\n" + KONU_ALANLARI
               + f"\n\nBAŞLIK: {baslik}\nSPOT: {spot}\n\nMETİN:\n{(metin or '')[:3500]}")
    konu = konu_dogrula(bag.gemini([{"text": talimat}], en_fazla=700))
    if konu:
        bag.onbellek_koy(anahtar, {"konu": konu, "t": simdi_tr().isoformat(timespec="seconds")})
    return konu


# ----------------------------------------------------------------- yardımcı: tarih
def tarih_oku(s):
    m = re.search(r"(\d{4})[-:/.](\d{2})[-:/.](\d{2})", str(s or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ----------------------------------------------------------------- 2) gerçek haber görseli
def commons_dosyalar(bag, sorgu, adet=12, genislik=1200):
    """Wikimedia Commons dosya araması (lisans ve açıklama bilgileriyle), önbellekli."""
    anahtar = f"c5:{genislik}:{sorgu.lower()}"
    if anahtar in bag.arsiv:
        return bag.arsiv[anahtar]
    sonuc = []
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers={"User-Agent": UA_BOT}, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": adet,
        "gsrsearch": f"{sorgu} filetype:bitmap", "prop": "imageinfo|categories", "cllimit": 30,
        "iiprop": "url|size|extmetadata", "iiurlwidth": genislik,
    }, timeout=25)
    r.raise_for_status()
    for s in sorted((r.json().get("query", {}).get("pages", {}) or {}).values(), key=lambda p: p.get("index", 99)):
        ii = (s.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {}) or {}
        val = lambda k: temiz((meta.get(k) or {}).get("value", ""), 500)
        sonuc.append({
            "baslik": s.get("title", ""), "url": ii.get("thumburl") or ii.get("url", ""), "sayfa": ii.get("descriptionurl", ""),
            "genislik": ii.get("width", 0), "yukseklik": ii.get("height", 0),
            "aciklama": val("ImageDescription"), "nesne": val("ObjectName"), "tarih": val("DateTimeOriginal"),
            "kategoriler": " | ".join(c.get("title", "").replace("Category:", "") for c in s.get("categories") or []),
            "lisans": val("LicenseShortName"), "lisans_url": val("LicenseUrl"), "yazar": temiz(val("Artist"), 80) or "Wikimedia Commons",
            "atif_gerekli": val("AttributionRequired"),
        })
    bag.arsiv[anahtar] = sonuc
    return sonuc


def kelime_kok(s):
    return {w[:5] for w in re.findall(r"[a-z0-9]{3,}", kucult(s)) if w not in {"the", "and", "ile", "icin", "with", "from", "olan", "gibi"}}


def gercek_dogrula(dosya, konu, haber_tarihi, ilce):
    """Commons dosyasının aynı olay, yer ve tarihle ilişkisini açıklama/kategori/çekim tarihinden doğrular (yapay zekâ kullanmadan)."""
    metin = " ".join([dosya["baslik"], dosya["aciklama"], dosya["nesne"], dosya["kategoriler"]])
    mk = kelime_kok(metin)
    if not SERBEST_LISANS.match(dosya.get("lisans", "")):
        return False, f"lisans uygun değil ({dosya.get('lisans') or 'belirtilmemiş'})"
    hedef = tarih_oku(konu.get("olay_tarihi")) or haber_tarihi
    cekim = tarih_oku(dosya.get("tarih"))
    if not cekim or not hedef or abs((cekim - hedef).days) > 3:
        return False, f"çekim tarihi olayla örtüşmüyor ({dosya.get('tarih') or 'tarih yok'})"
    yerler = kelime_kok(" ".join([konu.get("yer", ""), ilce or "", "Antalya"]))
    if not (yerler & mk):
        return False, "açıklamada olayın yeri geçmiyor"
    olay = kelime_kok(" ".join([konu.get("gercek_arama", "")] + konu.get("kurumlar", []))) - yerler
    ortak = olay & mk
    if len(ortak) < min(2, len(olay)) or not olay:
        return False, "açıklama aynı olayı anlatmıyor"
    return True, f"çekim tarihi {cekim}, yer ve olay adı ({', '.join(sorted(ortak))}) dosya açıklamasında geçiyor"


def gercek_adaylar(bag, konu, haber):
    """İzinli kaynaklardan aynı olaya ait doğrulanmış gerçek fotoğraf adayları."""
    adaylar, notlar = [], []
    # a) Haberin kendi kaynağı: yalnızca kaynağın izni kayıtlıysa ve izin gerçek haber görselini kapsıyorsa
    kayit = kaynak_izni(bag.izinler, haber.get("kaynak_url", ""))
    if haber.get("kaynak_gorsel") and kayit and kayit.get("izin") is True and kayit.get("haber_gorseli") is True:
        adaylar.append({
            "tur": "gercek", "kaynak": kayit.get("ad"), "url": haber["kaynak_gorsel"], "kucuk": haber["kaynak_gorsel"],
            "sayfa": haber["kaynak_url"], "fotografci": kayit.get("kredi") or kayit.get("ad"), "fotografci_sayfa": "",
            "lisans": kayit.get("lisans", "Kaynak izni"), "lisans_dayanagi": kayit.get("dayanak", ""), "kosul": kayit.get("kosul", ""),
            "dogrulama": "görsel, haberin dayandığı kaynak sayfasının kendi paylaşım görseli (aynı haber sayfası)",
        })
    elif haber.get("kaynak_url"):
        notlar.append(f"kaynak görseli kullanılmadı: {(kayit or {}).get('ad') or 'alan adı'} için kayıtlı kullanım izni yok")
    # b) Wikimedia Commons: dosya bazında özgür lisans + açıklama/tarih/yer doğrulaması
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if konu.get("gercek_arama") and commons and commons.get("izin") is True:
        try:
            for d in commons_dosyalar(bag, konu["gercek_arama"]):
                ok, neden = gercek_dogrula(d, konu, haber.get("tarih"), haber.get("ilce"))
                if not ok:
                    continue
                if d["genislik"] < 800 or d["genislik"] < d["yukseklik"]:
                    continue
                adaylar.append({
                    "tur": "gercek", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": re.sub(r"/\d+px-", "/640px-", d["url"]),
                    "sayfa": d["sayfa"], "fotografci": d["yazar"], "fotografci_sayfa": "", "lisans": d["lisans"],
                    "lisans_dayanagi": d["lisans_url"] or d["sayfa"], "kosul": commons.get("kosul", ""), "dogrulama": neden,
                    "aciklama": d["aciklama"][:200],
                })
                if len(adaylar) >= 2:
                    break
        except Exception as e:
            notlar.append(f"Commons gerçek görsel araması yapılamadı: {e}")
    return adaylar, notlar


# ----------------------------------------------------------------- 3) stok adayları
def unsplash_ara(bag, sorgu):
    anahtar = "u5:" + sorgu.lower()
    if anahtar in bag.arsiv:
        return bag.arsiv[anahtar]
    if not bag.unsplash_hakki():
        raise KotaHatasi(f"Unsplash saatlik sınırı ({bag.unsplash_saatlik}) doldu")
    bag.unsplash_say()
    r = requests.get("https://api.unsplash.com/search/photos", headers={"Authorization": f"Client-ID {bag.unsplash_key}", "Accept-Version": "v1"},
                     params={"query": sorgu, "orientation": "landscape", "per_page": 10, "content_filter": "high"}, timeout=20)
    if r.status_code in (403, 429):
        raise KotaHatasi(f"Unsplash kotası ({r.status_code})")
    r.raise_for_status()
    sonuc = []
    for f in r.json().get("results", []):
        sonuc.append({
            "tur": "stok", "kaynak": "Unsplash", "id": f.get("id"), "url": f["urls"]["regular"], "kucuk": f["urls"]["small"],
            "sayfa": f.get("links", {}).get("html", ""), "indir": f.get("links", {}).get("download_location", ""),
            "fotografci": f["user"]["name"], "fotografci_sayfa": f["user"].get("links", {}).get("html", ""),
            "aciklama": temiz(f.get("alt_description") or f.get("description") or "", 200),
            "lisans": "Unsplash Lisansı", "lisans_dayanagi": "https://unsplash.com/license",
            "kosul": "Ücretsiz kullanım; fotoğrafçı ve Unsplash adı belirtilir, görsel Unsplash adresinden gösterilir, kullanımda indirme bildirimi gönderilir.",
        })
    bag.arsiv[anahtar] = sonuc
    return sonuc


def stok_adaylari(bag, konu, konu_imza, en_fazla=5):
    """Aynı konu için adaylar her seferinde aynı sırayla gelir; önceki kararlar önbellekten okunur (yeniden yapay zekâ isteği yok)."""
    adaylar, notlar = [], []
    sorgular = konu.get("stok_sorgulari", [])[:2]
    pay = [3, 2] if len(sorgular) > 1 else [5]
    if bag.unsplash_key:
        for q, n in zip(sorgular, pay):
            try:
                alinan = 0
                for a in unsplash_ara(bag, q):
                    if alinan >= n:
                        break
                    if YABANCI_YER.search(a.get("aciklama", "")) or any(x["url"] == a["url"] for x in adaylar):
                        continue
                    adaylar.append(dict(a, sorgu=q))
                    alinan += 1
            except KotaHatasi as e:
                notlar.append(str(e))
                break
            except Exception as e:
                notlar.append(f"Unsplash araması yapılamadı ({q}): {e}")
    else:
        notlar.append("UNSPLASH_ACCESS_KEY tanımlı değil")
    # Haber belirli bir mekânla ilgiliyse o mekânın özgür lisanslı fotoğrafı da aday olur (yine görsel kontrolden geçer)
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if konu.get("yer_arama") and commons and commons.get("izin") is True and len(adaylar) < en_fazla + 1:
        try:
            for d in commons_dosyalar(bag, konu["yer_arama"], adet=10):
                if not SERBEST_LISANS.match(d["lisans"]) or d["genislik"] < 1000 or d["genislik"] < d["yukseklik"] * 1.2:
                    continue
                if re.search(r"map|logo|flag|coat|diagram|plan|seal|emblem|poster|harita|\b1[89]\d\d\b", d["baslik"], re.I):
                    continue
                adaylar.append({
                    "tur": "stok", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": re.sub(r"/\d+px-", "/640px-", d["url"]),
                    "sayfa": d["sayfa"], "fotografci": d["yazar"], "fotografci_sayfa": "", "aciklama": d["aciklama"][:200],
                    "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"], "sorgu": konu["yer_arama"],
                    "kosul": commons.get("kosul", ""),
                })
                if sum(1 for a in adaylar if a["kaynak"] == "Wikimedia Commons") >= 2:
                    break
        except Exception as e:
            notlar.append(f"Commons mekân araması yapılamadı: {e}")
    return adaylar[: en_fazla + 1], notlar


# ----------------------------------------------------------------- 4) görsel kontrol (Gemini, görüntünün kendisiyle)
KONTROL_TALIMAT = """Antalya'da yayın yapan yerel bir haber sitesi için fotoğraf denetçisisin. Aşağıda bir haber ve numaralı fotoğraflar var.
Fotoğrafların kendisine bak; dosya adı veya açıklama benzerliğine güvenme. Her fotoğraf için değerlendir:
- konu_puani (0-10): Fotoğraf haberdeki SOMUT nesne/faaliyeti ("{nesne}") ne kadar gösteriyor? Yalnızca genel kategori benzerliği (ör. herhangi bir spor, herhangi bir toplantı) en fazla 4 alır.
- somut_konu: Fotoğraf bu somut konuyu açıkça gösteriyor mu?
- yaniltici: Fotoğraf yanlış kişi, kurum, yer veya olay izlenimi yaratır mı? (Başka yerdeki belirli bir sınıf, toplantı, etkinlik,
  tören, maç veya kalabalığı Antalya'daki bu olayın görüntüsü gibi gösterecekse; rastgele bir kişi, bina veya logo haberde adı geçen
  kişi/kurum sanılabilecekse true.)
- yabanci_unsur: Türkiye dışını gösteren belirgin unsur var mı? (yabancı dilde tabela/yazı, yabancı plaka, bayrak, "POLICE", "911"
  gibi yabancı polis/ambulans işaretleri, Türkiye dışındaki tanınır yapı/manzara)
- taninabilir_kisi: Yüzü tanınabilir biçimde görünen kişi var mı?
- temsili_uygun: "Temsili görsel" etiketiyle bu haberde kullanılması uygun mu? Hassas haberlerde (suç, sağlık, kaza, ölüm, çocuk)
  tanınabilir ilgisiz kişileri gösteren fotoğraflar uygun değildir.
- gerekce: En fazla 20 kelimelik Türkçe açıklama.

HABER BAŞLIĞI: {baslik}
SPOT: {spot}
ANA OLAY: {olay}
SOMUT NESNE/FAALİYET: {nesne}
KİŞİLER: {kisiler} | KURUMLAR: {kurumlar} | YER: {yer} | HASSASİYET: {hassas}
KAÇINILACAKLAR: {kacin}

Yalnızca şu biçimde JSON döndür:
{{"degerlendirmeler": [{{"no": 1, "konu_puani": 0, "somut_konu": false, "yaniltici": false, "yabanci_unsur": false,
 "taninabilir_kisi": false, "temsili_uygun": false, "gerekce": "..."}}]}}"""


def gorsel_indir(url, en_fazla=900_000):
    r = requests.get(url, headers={"User-Agent": UA_BOT}, timeout=20)
    r.raise_for_status()
    tip = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not tip.startswith("image/") or len(r.content) > en_fazla:
        raise ServisHatasi(f"görsel uygun değil ({tip}, {len(r.content)} bayt)")
    return tip, base64.b64encode(r.content).decode()


def karar_ver(d, konu, gercek=False):
    """Yapay zekâ değerlendirmesinden kesin karar: kurallar kodda uygulanır."""
    hassas = konu.get("hassas", "yok") != "yok"
    esik = 8 if hassas else 7
    nedenler = []
    if not d.get("somut_konu") and not gercek:
        nedenler.append("somut konuyu göstermiyor")
    if d.get("yaniltici"):
        nedenler.append("yanıltıcı (yanlış kişi/kurum/yer/olay izlenimi)")
    if d.get("yabanci_unsur") and not gercek:
        nedenler.append("Türkiye dışını gösteren unsur var")
    if not d.get("temsili_uygun") and not gercek:
        nedenler.append("temsili kullanım için uygun bulunmadı")
    puan = int(d.get("konu_puani") or 0)
    if puan < esik and not gercek:
        nedenler.append(f"konu puanı düşük ({puan}/10, en az {esik})")
    if d.get("taninabilir_kisi") and not gercek and (hassas or konu.get("kisiler") or konu.get("kurumlar")):
        nedenler.append("tanınabilir ilgisiz kişi (haberde adı geçen kişi sanılabilir / hassas haber)")
    return ("kabul" if not nedenler else "red"), nedenler


def gorsel_kontrol(bag, haber, konu, konu_imza, adaylar):
    """Adayları görüntüleriyle birlikte tek istekte değerlendirir. Önbellekte kararı olanlar yeniden gönderilmez.
    Dönen her adaya 'karar', 'puan', 'gerekce', 'kontrol' alanları eklenir. Kontrol yapılamazsa KotaHatasi/ServisHatasi yükselir."""
    gonderilecek, parcalar = [], []
    for a in adaylar:
        ob = bag.onbellek_al(imza(KURAL_SURUMU, konu_imza, a["url"]))
        if ob:
            a.update(karar=ob["karar"], puan=ob.get("puan", 0), gerekce=ob.get("gerekce", ""), kontrol_tarih=ob.get("t", ""), onbellekten=True)
            continue
        try:
            tip, veri = gorsel_indir(a.get("kucuk") or a["url"])
        except Exception as e:
            a.update(karar="red", puan=0, gerekce=f"görsel indirilemedi: {e}", kontrol_tarih="")
            continue
        gonderilecek.append(a)
        parcalar += [{"text": f"FOTOĞRAF {len(gonderilecek)}:"}, {"inlineData": {"mimeType": tip, "data": veri}}]
    if not gonderilecek:
        return adaylar
    metin = KONTROL_TALIMAT.format(
        baslik=haber.get("baslik", ""), spot=haber.get("spot", ""), olay=konu.get("ana_olay", ""), nesne=konu.get("nesne_faaliyet", ""),
        kisiler=", ".join(konu.get("kisiler") or []) or "-", kurumlar=", ".join(konu.get("kurumlar") or []) or "-",
        yer=konu.get("yer") or "-", hassas=konu.get("hassas", "yok"), kacin=", ".join(konu.get("kacin") or []) or "-")
    yanit = bag.gemini([{"text": metin}] + parcalar, en_fazla=1500)
    liste = {int(d.get("no", 0)): d for d in (yanit.get("degerlendirmeler") or []) if isinstance(d, dict)}
    tarih = simdi_tr().isoformat(timespec="seconds")
    for i, a in enumerate(gonderilecek, 1):
        d = liste.get(i)
        if not d:
            a.update(karar="red", puan=0, gerekce="değerlendirme yanıtında yok", kontrol_tarih=tarih)
            continue
        karar, nedenler = karar_ver(d, konu, gercek=a["tur"] == "gercek")
        gerekce = temiz(d.get("gerekce"), 200)
        if nedenler:
            gerekce = "; ".join(nedenler) + (f" — {gerekce}" if gerekce else "")
        a.update(karar=karar, puan=int(d.get("konu_puani") or 0), gerekce=gerekce, kontrol_tarih=tarih,
                 bulgular={k: d.get(k) for k in ("somut_konu", "yaniltici", "yabanci_unsur", "taninabilir_kisi", "temsili_uygun")})
        bag.onbellek_koy(imza(KURAL_SURUMU, konu_imza, a["url"]),
                         {"karar": karar, "puan": a["puan"], "gerekce": gerekce, "t": tarih})
    return adaylar


# ----------------------------------------------------------------- seçim
def kategori_secimi(kategori, gerekce, kontrol, konu_imza):
    kat = kategori if kategori in KATEGORI_DOSYA else "Gündem"
    return {
        "tur": "kategori", "gorsel": f"/images/kategori/{KATEGORI_DOSYA[kat]}.png", "gorsel_kredi": "",
        "kaynak_sayfa": "", "fotografci": "", "kredi": "Gazete Antalya", "lisans": "Gazete Antalya'nın kendi tasarımı",
        "lisans_dayanagi": "Sitenin kendi grafik dosyası", "kosul": "", "gerekce": gerekce, "kontrol": kontrol,
        "surum": KURAL_SURUMU, "konu_imza": konu_imza,
    }


def aday_secimi(a, kontrol, konu_imza):
    kredi = f"{a['fotografci']} / {a['kaynak']}" + (f" ({a['lisans']})" if a["kaynak"] == "Wikimedia Commons" else "")
    return {
        "tur": a["tur"], "gorsel": a["url"], "gorsel_kredi": "Fotoğraf: " + kredi,
        "kaynak_sayfa": a.get("sayfa", ""), "fotografci": a.get("fotografci", ""), "fotografci_sayfa": a.get("fotografci_sayfa", ""),
        "kredi": kredi, "lisans": a.get("lisans", ""), "lisans_dayanagi": a.get("lisans_dayanagi", ""), "kosul": a.get("kosul", ""),
        "gerekce": (a.get("dogrulama") + "; " if a.get("dogrulama") else "") + (a.get("gerekce") or ""),
        "kontrol": kontrol, "surum": KURAL_SURUMU, "konu_imza": konu_imza, "indir": a.get("indir", ""),
    }


def gorsel_sec(bag, haber, konu, eski=None):
    """haber: {baslik, spot, kategori, ilce, tarih(date), kaynak_url, kaynak_gorsel}
    Dönüş: (secim, rapor). secim['bekliyor'] True ise kontrol yapılamadı; sonraki turda yeniden denenir."""
    kimza = konu_imzasi(haber.get("baslik"), haber.get("spot"), haber.get("kategori"))
    rapor = {"notlar": [], "adaylar": [], "eski": None}
    simdi = simdi_tr().isoformat(timespec="seconds")
    if not konu:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "konu ayrıştırılamadı"}
        s = kategori_secimi(haber.get("kategori"), "Haberin konusu ayrıştırılamadığı için kontrol edilmemiş fotoğraf yerine kategori görseli.", k, kimza)
        s["bekliyor"] = True
        return s, rapor

    gercek, n1 = gercek_adaylar(bag, konu, haber)
    stok, n2 = stok_adaylari(bag, konu, kimza)
    rapor["notlar"] += n1 + n2
    adaylar = gercek + stok
    eski_aday = None
    if eski and eski.get("url"):
        eski_aday = {"tur": "eski", "kaynak": "mevcut", "url": eski["url"], "kucuk": eski["url"], "fotografci": "", "lisans": ""}
    kontrol_listesi = adaylar + ([eski_aday] if eski_aday else [])

    kontrol_yapildi, hata = False, ""
    if kontrol_listesi:
        try:
            gorsel_kontrol(bag, haber, konu, kimza, kontrol_listesi)
            kontrol_yapildi = True
        except (KotaHatasi, ServisHatasi) as e:
            hata = str(e)
            rapor["notlar"].append("görsel kontrol yapılamadı: " + hata)
            bag.notlar.append(f"{haber.get('baslik', '')[:60]}: {hata}")
    if eski_aday:
        rapor["eski"] = {k: eski_aday.get(k) for k in ("karar", "puan", "gerekce", "bulgular")}
    rapor["adaylar"] = [{k: a.get(k) for k in ("tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "sorgu", "karar", "puan", "gerekce", "dogrulama")}
                        for a in adaylar]

    # Gerçek haber fotoğrafı: meta doğrulaması geçmiş olmalı; hassas haberlerde görsel kontrol de şart
    for a in gercek:
        if a.get("karar") == "red":
            continue
        if not kontrol_yapildi and konu.get("hassas") != "yok":
            continue
        kontrol = {"yapildi": kontrol_yapildi and a.get("karar") == "kabul", "sonuc": "onaylandı", "tarih": a.get("kontrol_tarih") or simdi,
                   "yontem": "kaynak/açıklama/tarih doğrulaması" + (" + Gemini görsel kontrol" if kontrol_yapildi else ""),
                   "model": bag.model_fn() if kontrol_yapildi else "", "puan": a.get("puan", 0)}
        return aday_secimi(a, kontrol, kimza), rapor

    if not kontrol_yapildi:
        neden = hata or "aday bulunamadı"
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "Gemini görsel kontrol", "neden": neden}
        s = kategori_secimi(haber.get("kategori"), f"Stok adaylar kontrol edilemedi ({neden}); kontrol edilmemiş fotoğraf yayımlanmadı.", k, kimza)
        s["bekliyor"] = bool(hata) or any(re.search(r"kota|sınır", n, re.I) for n in n2)
        return s, rapor

    kabul = sorted([a for a in stok if a.get("karar") == "kabul"], key=lambda a: -a.get("puan", 0))
    if kabul:
        a = kabul[0]
        kontrol = {"yapildi": True, "sonuc": "onaylandı", "tarih": a.get("kontrol_tarih") or simdi, "yontem": "Gemini görsel kontrol",
                   "model": bag.model_fn(), "puan": a.get("puan", 0)}
        return aday_secimi(a, kontrol, kimza), rapor
    k = {"yapildi": True, "sonuc": "adaylar reddedildi", "tarih": simdi, "yontem": "Gemini görsel kontrol", "model": bag.model_fn()}
    s = kategori_secimi(haber.get("kategori"), f"{len(stok)} stok aday görsel kontrolden geçemedi; ilgisiz fotoğraf yerine kategori görseli.", k, kimza)
    return s, rapor


# ----------------------------------------------------------------- ön bilgi (front matter) işlemleri
def otomatik_gorsel_mi(v):
    """Görsel otomatik seçilmişse True. Elle seçilmiş, sabitlenmiş ya da kökeni belirsizse False (dokunulmaz)."""
    if v.get("otomatik") is not True or v.get("gorsel_kilit"):
        return False, "elle eklenen haber" if v.get("otomatik") is not True else "görsel sabitlenmiş"
    secim = v.get("gorsel_secim")
    gorsel = v.get("gorsel") or ""
    if isinstance(secim, dict):
        if gorsel == secim.get("gorsel_url"):
            return True, "otomatik (kayıtlı seçim)"
        return False, "görsel kayıtlı otomatik seçimden farklı (editör değiştirmiş)"
    if not v.get("gorsel_v4"):
        return False, "kökeni belirsiz"
    if not gorsel and not v.get("gorsel_kredi"):
        return True, "otomatik (görselsiz)"
    if OTOMATIK_HOST.match(gorsel) and str(v.get("gorsel_kredi", "")).startswith("Temsili fotoğraf:"):
        return True, "otomatik (eski hat)"
    return False, "kökeni belirsiz"


def secimi_uygula(v, secim):
    """Seçimi ön bilgiye yazar. Önceki görsel geri alınabilsin diye 'gorsel_onceki' alanında saklanır."""
    if "gorsel_onceki" not in v and (v.get("gorsel") or v.get("gorsel_kredi")):
        v["gorsel_onceki"] = {"gorsel": v.get("gorsel", ""), "gorsel_kredi": v.get("gorsel_kredi", "")}
    v["gorsel"] = secim["gorsel"]
    v["gorsel_kredi"] = secim["gorsel_kredi"]
    v["gorsel_tur"] = secim["tur"]
    kayit = {k: secim.get(k, "") for k in ("tur", "kaynak_sayfa", "fotografci", "fotografci_sayfa", "kredi", "lisans",
                                           "lisans_dayanagi", "kosul", "gerekce", "surum", "konu_imza")}
    kayit["gorsel_url"] = secim["gorsel"]
    kayit["kontrol"] = secim.get("kontrol", {})
    if secim.get("bekliyor"):
        kayit["bekliyor"] = True
    v["gorsel_secim"] = kayit
    v.pop("gorsel_v4", None)
    return v


def unsplash_bildir(bag, secim):
    """Unsplash API kuralı: kullanılan fotoğraf için indirme bildirimi."""
    if secim.get("indir") and bag.unsplash_key:
        try:
            requests.get(secim["indir"], headers={"Authorization": f"Client-ID {bag.unsplash_key}"}, timeout=10)
        except Exception:
            pass


def md_oku(yol):
    with open(yol, encoding="utf-8") as f:
        icerik = f.read()
    if not icerik.startswith("---\n"):
        raise ValueError("ön bilgi yok")
    on, govde = icerik[4:].split("\n---\n", 1)
    return yaml.safe_load(on) or {}, govde


def md_yaz(yol, v, govde):
    with open(yol, "w", encoding="utf-8") as f:
        f.write("---\n" + yaml.safe_dump(v, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + govde)


def haber_bilgisi(v, govde=""):
    tarih = v.get("date")
    if isinstance(tarih, str):
        tarih = tarih_oku(tarih)
    elif isinstance(tarih, datetime):
        tarih = tarih.date()
    return {"baslik": v.get("title", ""), "spot": v.get("ozet", ""), "kategori": (v.get("kategoriler") or ["Gündem"])[0],
            "ilce": (v.get("ilceler") or [""])[0], "tarih": tarih, "kaynak_url": v.get("kaynak_url", ""), "kaynak_gorsel": "",
            "metin": govde}


def yeniden_gerekli(v):
    s = v.get("gorsel_secim")
    if not isinstance(s, dict):
        return True
    if s.get("surum") != KURAL_SURUMU:
        return True
    if s.get("konu_imza") != konu_imzasi(v.get("title"), v.get("ozet"), (v.get("kategoriler") or ["Gündem"])[0]):
        return True
    if s.get("bekliyor"):
        son = (s.get("kontrol") or {}).get("tarih", "")
        try:
            return datetime.fromisoformat(son) < simdi_tr() - timedelta(hours=3)
        except Exception:
            return True
    return False


# ----------------------------------------------------------------- toplu düzeltme ve deneme
def toplu_duzelt(bag, limit=3):
    """Eski haberlerdeki otomatik görselleri küçük gruplar hâlinde yeni yöntemle düzeltir (yenilerden eskiye)."""
    duzeltilen = 0
    for ad in sorted(os.listdir(HABER_KLASOR), reverse=True):
        if duzeltilen >= limit or not bag.yz_hakki(2):
            break
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            v, govde = md_oku(yol)
            oto, _ = otomatik_gorsel_mi(v)
            if not oto or not yeniden_gerekli(v):
                continue
            h = haber_bilgisi(v, govde)
            konu = konu_ayristir(bag, h["baslik"], h["spot"], govde)
            secim, _ = gorsel_sec(bag, h, konu)
            if secim.get("bekliyor") and v.get("gorsel") and not isinstance(v.get("gorsel_secim"), dict):
                # kontrol yapılamadıysa eski görseli hemen kaldırma; yalnızca kaydı güncelle, sonra yeniden dene
                print(f"Görsel kontrolü ertelendi: {ad} ({secim['kontrol'].get('neden', '')})")
                continue
            secimi_uygula(v, secim)
            md_yaz(yol, v, govde)
            if secim["tur"] == "stok":
                unsplash_bildir(bag, secim)
            duzeltilen += 1
            print(f"Görsel düzeltildi: {ad} → {secim['tur']}")
        except Exception as e:
            print("Görsel düzeltilemedi:", ad, e)
    return duzeltilen


def deneme_calistir(bag, dosyalar):
    """Seçilen haberler için karşılaştırmalı deneme. İçerik dosyalarına DOKUNMAZ; sonuç durum dosyasına yazılır."""
    satirlar = []
    for ad in dosyalar:
        yol = os.path.join(HABER_KLASOR, ad)
        satir = {"dosya": ad}
        try:
            v, govde = md_oku(yol)
            satir.update(baslik=v.get("title", ""), link="/haber/" + ad[:-3] + "/", kategori=(v.get("kategoriler") or [""])[0],
                         eski={"gorsel": v.get("gorsel", ""), "kredi": v.get("gorsel_kredi", "")})
            oto, neden = otomatik_gorsel_mi(v)
            satir["otomatik"] = neden
            if not oto:
                satir["sonuc"] = "atlandı: " + neden
                satirlar.append(satir)
                continue
            h = haber_bilgisi(v, govde)
            try:
                konu = konu_ayristir(bag, h["baslik"], h["spot"], govde)
            except (KotaHatasi, ServisHatasi) as e:
                konu = None
                bag.notlar.append(f"{h['baslik'][:60]}: konu ayrıştırılamadı ({e})")
            satir["konu"] = konu
            secim, rapor = gorsel_sec(bag, h, konu, eski={"url": v.get("gorsel", "")} if v.get("gorsel") else None)
            secim.pop("indir", None)
            satir.update(yeni=secim, eski_degerlendirme=rapor["eski"], adaylar=rapor["adaylar"], notlar=rapor["notlar"])
            print(f"Deneme: {ad[:60]} → {secim['tur']}")
        except Exception as e:
            satir["hata"] = str(e)[:200]
        satirlar.append(satir)
    return {"tarih": simdi_tr().isoformat(timespec="seconds"), "surum": KURAL_SURUMU, "model": bag.model_fn() if bag.gemini_key else "",
            "dosyalar": dosyalar, "satirlar": satirlar, "atlanan_kontroller": bag.notlar[:], "yz_kullanilan": bag.yz_tur}
