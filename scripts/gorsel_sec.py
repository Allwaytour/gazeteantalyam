#!/usr/bin/env python3
"""Gazete Antalya – görsel seçim hattı (g7).

g7 ek sıra (kişi ve kurum haberleri):
  1. Haberdeki olayın gerçek fotoğrafı (kullanım hakkı doğrulanmış).
  2. Haberdeki KİŞİNİN benzer bir ziyaret/toplantı/etkinlikte çekilmiş arşiv fotoğrafı.
  3. Aynı kişinin genel fotoğrafı/portresi.
  4. Haberdeki kurum, takım veya mekânın fotoğrafı.
  5. Fotoğrafsız kategori kapağı.
Kişinin kimliği yüz benzerliğiyle DEĞİL, Wikidata kaydı (ad + açıklama eşleşmesi) ve o kişiye ait
Wikimedia Commons kategorisiyle doğrulanır; yapay zekâ yalnızca sahneyi (yanıltıcı unsur, etkinlik benzerliği) değerlendirir.
Onaylanan fotoğraflar kaynak ve lisansıyla kütüphaneye (data/foto_arsivi.json, "kut:" anahtarları) kaydedilir.

---- g6 açıklaması ----

Seçim sırası (başka hiçbir yol yoktur; genel stok fotoğraf KULLANILMAZ):
  1. Gerçek haber fotoğrafı: kullanım hakkı doğrulanmış kaynaktan, doğrudan aynı olaya ait
     (olay adı, yer ve çekim tarihi dosya açıklamasıyla doğrulanır).
  2. Arşiv fotoğrafı: kendi arşivimizden (data/gorsel_arsivi.yml) ya da kullanım hakkı doğrulanmış
     arşivden (Wikimedia Commons), haberde ADI GEÇEN gerçek yer/kurum/tesis/takıma ait fotoğraf.
     Adın kendisi dosya başlığı/açıklaması/kategorilerinde geçmelidir; "polis", "futbol", "okul"
     gibi geniş eşleşmeler kabul edilmez. Sitede "Arşiv" olarak belirtilir.
  3. Hiçbiri yoksa sade, fotoğrafsız haber kapağı (kategori görseli).

Gerçek ve arşiv adayları ayrıca Gemini ile görüntünün kendisine bakılarak kontrol edilir.
Kontrol başarısızsa, hata verirse ya da kota yüzünden yapılamazsa fotoğrafsız kapak kullanılır.
Elle seçilmiş, sabitlenmiş ya da kökeni belirsiz görsellere dokunulmaz.
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

SURUM = "g7"
KURAL_TARIHI = "2026-11-02"
KURAL_SURUMU = f"{SURUM}-{KURAL_TARIHI}"   # kurallar değişince artırılır → eski kararlar önbellekten silinir

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HABER_KLASOR = os.path.join(KOK, "content", "haber")
IZIN_DOSYA = os.path.join(KOK, "data", "gorsel_izinleri.yml")
ARSIV_DOSYA = os.path.join(KOK, "data", "gorsel_arsivi.yml")
TR = timezone(timedelta(hours=3))
UA_BOT = "GazeteAntalya/1.0 (https://www.gazeteantalyam.com; iletisim@gazeteantalyam.com)"

KATEGORI_DOSYA = {
    "Gündem": "gundem", "Asayiş": "asayis", "Ekonomi": "ekonomi", "Turizm": "turizm", "Spor": "spor",
    "Sağlık": "saglik", "Eğitim": "egitim", "Kültür-Sanat": "kultur-sanat", "Yaşam": "yasam",
}
HASSAS = {"suç", "sağlık", "kaza", "ölüm", "afet", "çocuk"}
HEDEF_TURLERI = {"yer", "yol", "tesis", "kurum", "takim", "olay"}
# Eski hatların (Unsplash/Openverse/Commons rastgele seçimi) ürettiği görseller: düzeltilecekler
OTOMATIK_HOST = re.compile(r"^https://(images\.unsplash\.com|(upload|thumb)\.wikimedia\.org|live\.staticflickr\.com|api\.openverse\.org)/")
SERBEST_LISANS = re.compile(r"^(CC0|CC BY(-SA)? \d\.\d|CC BY(-SA)?|Public domain|PD)", re.I)
ISTENMEYEN = re.compile(r"map|logo|flag|coat.of.arms|diagram|chart|plan\b|seal|emblem|icon|poster|signature|screenshot|harita|bayra|"
                        r"engraving|drawing|painting|illustration|postcard|\b1[6-8]\d\d\b|\b19[0-7]\d\b", re.I)
# Hedef adında tek başına yeterli sayılmayan genel kelimeler ("Antalya stadı" gibi genel adlar eşleşme sayılmaz)
GENEL_KELIME = set("antalya turkey turkiye türkiye the of and ve ile city şehir sehir merkez center centre stadium stadyum stadı stadi "
                   "spor sport kulübü kulubu club fk sk okul school hastane hospital belediye municipality road yol cadde street "
                   "polis police meydan square park plaj beach otel hotel".split())


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
    return s.translate(str.maketrans("çğıöşüâîû", "cgiosuaiu"))


def kelimeler(s):
    return [w for w in re.findall(r"[a-z0-9]+", kucult(s)) if len(w) >= 3 or w.isdigit()]


def kok(w):
    return w[:5]


def json_ayikla(metin):
    metin = re.sub(r"^```(?:json)?|```$", "", (metin or "").strip()).strip()
    return json.loads(metin)


# ----------------------------------------------------------------- bağlam: ayarlar, kota, önbellek
class Baglam:
    """Bir çalışma turunun ayarları, kota sayaçları ve önbelleği (data/otomatik_durum.json → 'gorsel')."""

    def __init__(self, ayar, durum, gemini_key="", model_fn=None, arsiv=None):
        self.ayar = ayar or {}
        self.gemini_key = gemini_key
        self.model_fn = model_fn or (lambda: "gemini-2.5-flash-lite")
        self.arsiv = arsiv if arsiv is not None else {}
        g = durum.setdefault("gorsel", {})
        bugun = simdi_tr().strftime("%Y-%m-%d")
        if g.get("gun") != bugun:
            g["gun"], g["yz_bugun"] = bugun, 0
        # Kural sürümü değiştiyse önceki sürümün kararları (kabul edilmiş yanlış seçimler dahil) silinir
        g["onbellek"] = {k: v for k, v in (g.get("onbellek") or {}).items() if isinstance(v, dict) and v.get("s") == KURAL_SURUMU}
        for eski in ("unsplash", "unsplash_kapali"):
            g.pop(eski, None)
        g.setdefault("yasakli", [])          # bir daha hiçbir habere seçilmeyecek görseller
        self.g = g
        self.yz_tur = 0
        self.son_yz = 0.0
        self.izinler = izinleri_oku()
        self.kendi_arsiv = kendi_arsivi_oku()
        self.notlar = []

    @property
    def yz_gunluk(self):
        return int(self.ayar.get("gorsel_yz_gunluk", 100))

    def yz_hakki(self, adet=1):
        return bool(self.gemini_key) and self.g["yz_bugun"] + adet <= self.yz_gunluk

    def yasakli_mi(self, url):
        return url in self.g["yasakli"]

    def yasakla(self, url):
        if url and url not in self.g["yasakli"]:
            self.g["yasakli"].append(url)
            self.g["yasakli"] = self.g["yasakli"][-3000:]

    def onbellek_al(self, anahtar):
        return self.g["onbellek"].get(anahtar)

    def onbellek_koy(self, anahtar, deger):
        ob = self.g["onbellek"]
        ob[anahtar] = dict(deger, s=KURAL_SURUMU)
        if len(ob) > 1500:
            for k in sorted(ob, key=lambda k: ob[k].get("t", ""))[: len(ob) - 1500]:
                ob.pop(k, None)

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
                return json_ayikla("".join(p.get("text", "") for p in aday["content"]["parts"]))
            except Exception as e:
                raise ServisHatasi(f"Gemini yanıtı okunamadı: {e}")
        raise KotaHatasi("Gemini kotası doldu")


def izinleri_oku():
    try:
        with open(IZIN_DOSYA, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("kaynaklar") or []
    except Exception:
        return []


def kendi_arsivi_oku():
    """Kendi fotoğraf arşivimiz: yalnızca kullanım dayanağı yazılı kayıtlar kullanılır."""
    try:
        with open(ARSIV_DOSYA, encoding="utf-8") as f:
            kayitlar = (yaml.safe_load(f) or {}).get("fotograflar") or []
    except Exception:
        return []
    return [k for k in kayitlar if isinstance(k, dict) and k.get("gorsel") and k.get("dayanak") and k.get("anahtarlar")]


def kaynak_izni(izinler, url):
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
KONU_ALANLARI = """{"ana_olay": "...", "odak": "olay | yer | kurum | kisi | takim | genel", "kisiler": ["..."], "kurumlar": ["..."],
 "yer": "...", "olay_tarihi": "YYYY-AA-GG veya boş", "hassas": "yok | suç | sağlık | kaza | ölüm | afet | çocuk",
 "gercek_arama": "...", "hedefler": [{"ad": "...", "ad_en": "...", "tur": "yer | yol | tesis | kurum | takim | olay"}],
 "ana_kisiler": [{"ad": "...", "rol": "..."}],
 "etkinlik": {"tur": "...", "anahtar_tr": ["..."], "anahtar_en": ["..."]}}"""

KONU_KURALLARI = """Alanlar:
- ana_olay: Haberde ne oldu? Tek cümle (Türkçe).
- odak: Haber en çok neyle ilgili? (belirli bir olay, yer, kurum, kişi, spor takımı ya da genel bir konu)
- kisiler / kurumlar: Haberde adı geçen kişi ve kurumlar (yoksa boş liste).
- yer: Olayın geçtiği yer (ilçe, mahalle, tesis vb.).
- olay_tarihi: Metinde açıkça geçiyorsa olayın tarihi; yoksa boş.
- hassas: Suç, sağlık durumu, kaza, ölüm, afet ya da çocuk içeriyorsa ilgili değer; değilse "yok".
- gercek_arama: Adı konmuş belirli bir olaysa (ör. "COP31 Antalya") aynı olayın gerçek fotoğrafını arşivde aramak için kısa ifade; değilse boş.
- hedefler: Haberde ADIYLA geçen ve fotoğrafı çekilebilecek gerçek yer, yol, tesis, kurum binası, spor takımı ya da adı konmuş olay (en fazla 3).
  "ad" haberde geçtiği gibi özel ad (ör. "Antalya Havalimanı", "Antiphellos Antik Tiyatrosu", "Kepezspor", "Konyaaltı Sahili", "D400 kara yolu").
  "ad_en" bilinen İngilizce/uluslararası adı (ör. "Antalya Airport", "Antiphellos theatre"); bilinmiyorsa "ad" ile aynı.
  Genel ifadeler (ör. "stadyum", "polis", "okul", "hastane", "otoyol", "Antalya") hedef DEĞİLDİR. Yoksa boş liste.
- ana_kisiler: Haberin asıl konusu olan, kamuoyunca tanınan kişiler (siyasetçi, yönetici, sanatçı, sporcu, teknik direktör vb.; en fazla 2).
  "ad" tam ad (ör. "Ersin Tatar", "Murat Kurum"); "rol" haberdeki görevi (ör. "KKTC 5. Cumhurbaşkanı", "Çevre Bakanı").
  Suç şüphelisi, mağdur, hasta, çocuk ya da kamuya mal olmamış kişileri YAZMA. Yalnızca soyadı geçiyorsa tam adını metinden bul; bulamazsan yazma.
- etkinlik: Haberdeki etkinliğin türü. "tur" tek kelime Türkçe (ör. "ziyaret", "toplantı", "konser", "maç", "tören", "açılış", "basın toplantısı", "antrenman", "açıklama").
  "anahtar_tr" ve "anahtar_en": benzer etkinlik fotoğraflarını bulmak için 2-4 kelime (ör. ["üniversite","ziyaret"], ["university","visit","campus"])."""


def konu_dogrula(k):
    if not isinstance(k, dict):
        return None
    sonuc = {
        "ana_olay": temiz(k.get("ana_olay"), 200),
        "odak": str(k.get("odak") or "genel").strip().lower(),
        "kisiler": [temiz(x, 60) for x in (k.get("kisiler") or []) if temiz(x)][:6],
        "kurumlar": [temiz(x, 80) for x in (k.get("kurumlar") or []) if temiz(x)][:6],
        "yer": temiz(k.get("yer"), 80),
        "olay_tarihi": k.get("olay_tarihi") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k.get("olay_tarihi") or "")) else "",
        "hassas": str(k.get("hassas") or "yok").strip().lower(),
        "gercek_arama": temiz(k.get("gercek_arama"), 80),
        "hedefler": [],
        "ana_kisiler": [],
        "etkinlik": {"tur": "", "anahtar_tr": [], "anahtar_en": []},
    }
    for kisi in (k.get("ana_kisiler") or [])[:3]:
        if isinstance(kisi, dict) and len(kelimeler(kisi.get("ad"))) >= 2:
            sonuc["ana_kisiler"].append({"ad": temiz(kisi.get("ad"), 60), "rol": temiz(kisi.get("rol"), 80)})
    sonuc["ana_kisiler"] = sonuc["ana_kisiler"][:2]
    e = k.get("etkinlik") if isinstance(k.get("etkinlik"), dict) else {}
    sonuc["etkinlik"] = {"tur": temiz(e.get("tur"), 30).lower(),
                         "anahtar_tr": [temiz(x, 30).lower() for x in (e.get("anahtar_tr") or []) if temiz(x)][:4],
                         "anahtar_en": [temiz(x, 30).lower() for x in (e.get("anahtar_en") or []) if temiz(x)][:4]}
    if sonuc["hassas"] not in HASSAS:
        sonuc["hassas"] = "yok"
    for h in (k.get("hedefler") or [])[:4]:
        if not isinstance(h, dict):
            continue
        ad, ad_en = temiz(h.get("ad"), 80), temiz(h.get("ad_en") or h.get("ad"), 80)
        tur = str(h.get("tur") or "").strip().lower()
        if ad and ayirt_edici(ad) and tur in HEDEF_TURLERI:
            sonuc["hedefler"].append({"ad": ad, "ad_en": ad_en if ayirt_edici(ad_en) else ad, "tur": tur})
    sonuc["hedefler"] = sonuc["hedefler"][:3]
    if not sonuc["ana_olay"]:
        return None
    return sonuc


def ayirt_edici(ad):
    """Adın genel kelimeler dışındaki (ayırt edici) kelimeleri. Boşsa ad tek başına bir hedef sayılmaz."""
    genel = {kucult(g) for g in GENEL_KELIME}
    return [w for w in kelimeler(ad) if not any(w == g or (w.startswith(g) and len(w) - len(g) <= 3) for g in genel)]


def konu_ayristir(bag, baslik, spot, metin):
    anahtar = "konu:" + imza(KURAL_SURUMU, baslik, spot)
    ob = bag.onbellek_al(anahtar)
    if ob and ob.get("konu"):
        return ob["konu"]
    talimat = ("Bir haberin görselini seçmek için haberin konusunu ayrıştır. Yalnızca metinde geçen bilgileri kullan, uydurma.\n"
               + KONU_KURALLARI + "\n\nYalnızca şu biçimde JSON döndür:\n" + KONU_ALANLARI
               + f"\n\nBAŞLIK: {baslik}\nSPOT: {spot}\n\nMETİN:\n{(metin or '')[:3500]}")
    konu = konu_dogrula(bag.gemini([{"text": talimat}], en_fazla=1000))
    if konu:
        bag.onbellek_koy(anahtar, {"konu": konu, "t": simdi_tr().isoformat(timespec="seconds")})
    return konu


def tarih_oku(s):
    m = re.search(r"(\d{4})[-:/.](\d{2})[-:/.](\d{2})", str(s or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ----------------------------------------------------------------- Wikimedia Commons
def commons_dosyalar(bag, sorgu, adet=15):
    anahtar = f"c6:{sorgu.lower()}"
    if anahtar in bag.arsiv:
        return bag.arsiv[anahtar]
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers={"User-Agent": UA_BOT}, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": adet,
        "gsrsearch": f"{sorgu} filetype:bitmap", "prop": "imageinfo|categories", "cllimit": 40,
        "iiprop": "url|size|extmetadata", "iiurlwidth": 1280,
    }, timeout=25)
    r.raise_for_status()
    sonuc = []
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
        })
    bag.arsiv[anahtar] = sonuc
    return sonuc


def kontrol_kucuk(url):
    """Kontrol için küçük kopya. Commons yalnızca standart boyutları verir (500px)."""
    return re.sub(r"/\d+px-", "/500px-", url) if "/thumb/" in url else url


def commons_metni(d):
    return " ".join([d["baslik"], d["aciklama"], d["nesne"], d["kategoriler"]])


def commons_uygun(d):
    if not SERBEST_LISANS.match(d.get("lisans", "")):
        return False, f"lisans doğrulanamadı ({d.get('lisans') or 'belirtilmemiş'})"
    if ISTENMEYEN.search(d["baslik"]):
        return False, "fotoğraf değil ya da tarihî çizim/harita/logo"
    if d["genislik"] < 800 or d["genislik"] < d["yukseklik"]:
        return False, "yatay ve yeterli boyutta değil"
    cekim = tarih_oku(d.get("tarih"))
    if cekim and cekim.year < 1990:
        return False, f"çok eski çekim ({cekim.year})"
    return True, ""


def _kelime_tutar(t, w):
    """Ek toleransı: 'havalimani' ~ 'havalimaninda'. 'kepezspor' ile 'kepez' eşleşmez."""
    return w.startswith(t) or (t.startswith(w) and len(w) >= 4 and len(t) - len(w) <= 2)


def ad_eslesir(ad, metin):
    """Adın TÜM kelimeleri metinde geçiyor mu ve adın en az bir ayırt edici kelimesi var mı?
    ("polis", "futbol", "stadyum", "Antalya" gibi geniş eşleşmeler tek başına kabul edilmez.)"""
    if not ayirt_edici(ad):
        return False
    m = kelimeler(metin)
    return all(any(_kelime_tutar(t, w) for w in m) for t in kelimeler(ad))


# ----------------------------------------------------------------- 2) adaylar
def gercek_adaylar(bag, konu, haber):
    """Aynı olaya ait, kullanım hakkı doğrulanmış gerçek fotoğraflar."""
    adaylar, notlar = [], []
    kayit = kaynak_izni(bag.izinler, haber.get("kaynak_url", ""))
    if haber.get("kaynak_gorsel") and kayit and kayit.get("izin") is True and kayit.get("haber_gorseli") is True:
        adaylar.append({
            "tur": "gercek", "kaynak": kayit.get("ad"), "url": haber["kaynak_gorsel"], "kucuk": haber["kaynak_gorsel"],
            "sayfa": haber["kaynak_url"], "fotografci": kayit.get("kredi") or kayit.get("ad"), "lisans": kayit.get("lisans", "Kaynak izni"),
            "lisans_dayanagi": kayit.get("dayanak", ""), "kosul": kayit.get("kosul", ""), "hedef": konu.get("ana_olay", ""),
            "dogrulama": "haberin dayandığı kaynak sayfasının kendi görseli; kaynağın kullanım izni kayıtlı",
        })
    elif haber.get("kaynak_url"):
        notlar.append(f"kaynak görseli kullanılmadı: {(kayit or {}).get('ad') or 'kaynak'} için kayıtlı kullanım izni yok")
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if konu.get("gercek_arama") and ayirt_edici(konu["gercek_arama"]) and commons and commons.get("izin") is True:
        try:
            hedef_tarih = tarih_oku(konu.get("olay_tarihi")) or haber.get("tarih")
            yerler = {kok(w) for w in kelimeler(" ".join([konu.get("yer", ""), haber.get("ilce") or "", "Antalya"]))}
            for d in commons_dosyalar(bag, konu["gercek_arama"]):
                ok, neden = commons_uygun(d)
                metin = commons_metni(d)
                cekim = tarih_oku(d.get("tarih"))
                if not ok or not cekim or not hedef_tarih or abs((cekim - hedef_tarih).days) > 3:
                    continue
                if not (yerler & {kok(w) for w in kelimeler(metin)}) or not ad_eslesir(konu["gercek_arama"], metin):
                    continue
                if bag.yasakli_mi(d["url"]):
                    continue
                adaylar.append({
                    "tur": "gercek", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"]), "sayfa": d["sayfa"],
                    "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                    "kosul": commons.get("kosul", ""), "hedef": konu["gercek_arama"], "cekim": str(cekim),
                    "dogrulama": f"dosya açıklamasında olay adı ve yer geçiyor; çekim tarihi {cekim} (olay tarihiyle örtüşüyor)",
                })
                if len(adaylar) >= 2:
                    break
        except Exception as e:
            notlar.append(f"Commons gerçek görsel araması yapılamadı: {e}")
    return adaylar, notlar



# ----------------------------------------------------------------- Wikidata: kişi/kurum kimlik doğrulaması
WD_API = "https://www.wikidata.org/w/api.php"
CM_API = "https://commons.wikimedia.org/w/api.php"


def _wd_get(params):
    r = requests.get(WD_API, headers={"User-Agent": UA_BOT}, params=dict(params, format="json"), timeout=25)
    r.raise_for_status()
    return r.json()


def wikidata_varlik(bag, ad, insan=True, ipucu=""):
    """Adı birebir tutan Wikidata kaydı. Kişi için P31=insan şartı; birden çok aday varsa açıklama ipucuyla
    tekil olarak ayrışmalı, ayrışmıyorsa None (yanlış kişiyi seçmemek için)."""
    anahtar = f"wd:{int(insan)}:{kucult(ad)}:{kucult(ipucu)[:40]}"
    if anahtar in bag.arsiv:
        return bag.arsiv[anahtar]
    hedef = " ".join(kelimeler(ad))
    idler = []
    for dil in ("tr", "en"):
        for s in (_wd_get({"action": "wbsearchentities", "search": ad, "language": dil, "uselang": dil, "limit": 7}).get("search") or []):
            if s["id"] not in idler:
                idler.append(s["id"])
    sonuc = None
    if idler:
        ent = _wd_get({"action": "wbgetentities", "ids": "|".join(idler[:12]), "props": "claims|labels|aliases|descriptions",
                       "languages": "tr|en"}).get("entities", {})
        adaylar = []
        for e in ent.values():
            adlar = [v.get("value", "") for v in (e.get("labels") or {}).values()]
            adlar += [a.get("value", "") for v in (e.get("aliases") or {}).values() for a in v]
            if not any(" ".join(kelimeler(x)) == hedef for x in adlar):
                continue
            p31 = [c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") for c in (e.get("claims", {}).get("P31") or [])]
            if insan != ("Q5" in p31):
                continue
            acik = " ".join(v.get("value", "") for v in (e.get("descriptions") or {}).values())
            deger = lambda p: [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in (e.get("claims", {}).get(p) or [])]
            adaylar.append({"qid": e["id"], "ad": ad, "aciklama": temiz(acik, 200), "p18": [x for x in deger("P18") if isinstance(x, str)],
                            "kategori": next((x for x in deger("P373") if isinstance(x, str)), "")})
        if len(adaylar) == 1:
            sonuc = adaylar[0]
        elif len(adaylar) > 1 and ipucu:
            ip = {kok(w) for w in kelimeler(ipucu)}
            puan = sorted(((len(ip & {kok(w) for w in kelimeler(a["aciklama"])}), a) for a in adaylar), key=lambda x: -x[0])
            if puan[0][0] > 0 and puan[0][0] > puan[1][0]:
                sonuc = puan[0][1]
    bag.arsiv[anahtar] = sonuc
    return sonuc


def _cm_get(params):
    r = requests.get(CM_API, headers={"User-Agent": UA_BOT}, params=dict(params, format="json"), timeout=25)
    r.raise_for_status()
    return r.json()


def _dosya_bilgisi(bag, basliklar):
    """Commons dosya başlıklarından lisans/açıklama/tarih bilgisi (50'lik gruplar, önbellekli)."""
    sonuc, eksik = [], []
    for b in basliklar:
        k = "cf:" + b
        if k in bag.arsiv:
            if bag.arsiv[k]:
                sonuc.append(bag.arsiv[k])
        else:
            eksik.append(b)
    for i in range(0, len(eksik), 50):
        grup = eksik[i:i + 50]
        js = _cm_get({"action": "query", "titles": "|".join(grup), "prop": "imageinfo|categories", "cllimit": "max",
                      "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 1280})
        bulunan = set()
        for sayfa in (js.get("query", {}).get("pages", {}) or {}).values():
            ii = (sayfa.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata", {}) or {}
            val = lambda k: temiz((meta.get(k) or {}).get("value", ""), 500)
            d = None
            if str(ii.get("mime", "")).startswith("image/") and "svg" not in str(ii.get("mime", "")):
                d = {"baslik": sayfa.get("title", ""), "url": ii.get("thumburl") or ii.get("url", ""), "sayfa": ii.get("descriptionurl", ""),
                     "genislik": ii.get("width", 0), "yukseklik": ii.get("height", 0), "aciklama": val("ImageDescription"),
                     "nesne": val("ObjectName"), "tarih": val("DateTimeOriginal"),
                     "kategoriler": " | ".join(c.get("title", "").replace("Category:", "") for c in sayfa.get("categories") or []),
                     "lisans": val("LicenseShortName"), "lisans_url": val("LicenseUrl"), "yazar": temiz(val("Artist"), 80) or "Wikimedia Commons"}
            bag.arsiv["cf:" + sayfa.get("title", "")] = d
            bulunan.add(sayfa.get("title", ""))
            if d:
                sonuc.append(d)
        for b in grup:
            if b not in bulunan:
                bag.arsiv["cf:" + b] = None
    return sonuc


def kategori_dosyalari(bag, kategori, alt_kategori=12):
    """Kişiye/kuruma ait Commons kategorisindeki fotoğraflar (bir alt kategori seviyesiyle)."""
    anahtar = "kat:" + kategori
    if anahtar not in bag.arsiv:
        dosyalar, altlar = [], []
        js = _cm_get({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + kategori, "cmtype": "file|subcat", "cmlimit": 200})
        for m in js.get("query", {}).get("categorymembers", []):
            (dosyalar if m["title"].startswith("File:") else altlar).append(m["title"])
        for alt in altlar[:alt_kategori]:
            js = _cm_get({"action": "query", "list": "categorymembers", "cmtitle": alt, "cmtype": "file", "cmlimit": 60})
            dosyalar += [m["title"] for m in js.get("query", {}).get("categorymembers", [])]
        bag.arsiv[anahtar] = list(dict.fromkeys(dosyalar))[:260]
    return _dosya_bilgisi(bag, bag.arsiv[anahtar])


GRUP_IPUCU = re.compile(r"\b(summit|meeting of|heads of state|with|and|ile|ve|delegation|group|zirve|heyet|toplu)\b", re.I)
TEK_IPUCU = re.compile(r"\b(portrait|portre|cropped|official|resmi)\b", re.I)


def _ek_den(kelime):
    """Türkçe ayrılma hâli eki: ziyaret→ziyaretten, toplantı→toplantıdan, tören→törenden."""
    k = (kelime or "").strip()
    if not k:
        return ""
    unluler = [c for c in k.lower() if c in "aıoueiöü"]
    son = unluler[-1] if unluler else "e"
    unlu = "a" if son in "aıou" else "e"
    sert = k[-1].lower() in "fstkçşhp"
    return k + ("t" if sert else "d") + unlu + "n"


def kisi_adaylari(bag, konu, haber):
    """Haberdeki tanınmış kişinin doğrulanmış Commons kategorisinden adaylar: olay → benzer etkinlik → genel/portre."""
    adaylar, notlar = [], []
    if konu.get("hassas", "yok") != "yok":
        if konu.get("ana_kisiler"):
            notlar.append("hassas haber: kişi fotoğrafı kullanılmadı")
        return adaylar, notlar
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    anahtarlar = {kok(w) for w in kelimeler(" ".join(konu["etkinlik"]["anahtar_tr"] + konu["etkinlik"]["anahtar_en"] + [konu["etkinlik"]["tur"]]))}
    hedef_tarih = tarih_oku(konu.get("olay_tarihi")) or haber.get("tarih")
    yerler = {kok(w) for w in kelimeler(" ".join([konu.get("yer", ""), haber.get("ilce") or ""]))}
    for kisi in konu.get("ana_kisiler") or []:
        try:
            v = wikidata_varlik(bag, kisi["ad"], insan=True, ipucu=kisi.get("rol", ""))
        except Exception as e:
            notlar.append(f"Wikidata sorgusu yapılamadı ({kisi['ad']}): {e}")
            continue
        if not v:
            notlar.append(f"'{kisi['ad']}' için kimliği kesin doğrulanan Wikidata kaydı yok (ad tutmadı ya da birden çok aynı adlı kişi var)")
            continue
        try:
            dosyalar = kategori_dosyalari(bag, v["kategori"]) if v["kategori"] else []
            p18 = _dosya_bilgisi(bag, ["File:" + x for x in v["p18"]])
        except Exception as e:
            notlar.append(f"Commons kategorisi okunamadı ({kisi['ad']}): {e}")
            continue
        kut = bag.arsiv.get("kut:" + v["qid"], [])
        gorulen = set()
        for d in p18 + [x for x in kut if isinstance(x, dict)] + dosyalar:
            if not d or d["url"] in gorulen or bag.yasakli_mi(d["url"]):
                continue
            gorulen.add(d["url"])
            ok, _ = commons_uygun(dict(d, genislik=max(d["genislik"], d["yukseklik"]), yukseklik=0))   # dikey portrelere izin
            if not ok or min(d["genislik"], d["yukseklik"]) < 400:
                continue
            metin = commons_metni(d)
            mk = {kok(w) for w in kelimeler(metin)}
            cekim = tarih_oku(d.get("tarih"))
            olay = bool(cekim and hedef_tarih and abs((cekim - hedef_tarih).days) <= 3 and yerler & mk)
            benzer = len(anahtarlar & mk)
            tur = "gercek" if olay else ("kisi_benzer" if benzer else "kisi_genel")
            puan = {"gercek": 300, "kisi_benzer": 200, "kisi_genel": 100}[tur] + benzer * 10
            puan += 15 if d in p18 else 0
            puan += 8 if any(isinstance(x, dict) and x.get("url") == d["url"] for x in kut) else 0
            puan += 6 if TEK_IPUCU.search(d["baslik"]) else 0
            puan -= 12 if GRUP_IPUCU.search(d["baslik"] + " " + d["aciklama"][:120]) else 0
            puan += min(10, max(0, (cekim.year - 2012))) if cekim else 0
            puan += 4 if d["genislik"] >= d["yukseklik"] else 0
            adaylar.append({
                "tur": "gercek" if olay else "arsiv", "alt_tur": tur, "kaynak": "Wikimedia Commons", "url": d["url"],
                "kucuk": kontrol_kucuk(d["url"]), "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"],
                "lisans_dayanagi": d["lisans_url"] or d["sayfa"], "kosul": commons.get("kosul", ""), "hedef": kisi["ad"],
                "kisi": kisi["ad"], "qid": v["qid"], "cekim": str(cekim or ""), "meta_puan": puan, "dikey": d["yukseklik"] > d["genislik"],
                "kayit": d,
                "dogrulama": (f"'{kisi['ad']}' kimliği Wikidata {v['qid']} ({v['aciklama'][:60]}) ve bu kişiye ait Commons kategorisiyle doğrulandı"
                              + ("; olay tarihi ve yeriyle örtüşüyor" if olay else (f"; benzer etkinlik ({benzer} anahtar eşleşmesi)" if benzer else "; genel fotoğraf"))),
            })
    adaylar.sort(key=lambda a: -a["meta_puan"])
    return adaylar[:6], notlar


def kurum_wikidata_adaylari(bag, konu):
    """Haberdeki kurum/takım/mekânın Wikidata görseli ve Commons kategorisi (ad birebir eşleşmeli)."""
    adaylar, notlar = [], []
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    for h in (konu.get("hedefler") or [])[:3]:
        try:
            v = wikidata_varlik(bag, h["ad"], insan=False, ipucu="Antalya " + (konu.get("yer") or ""))
            if not v and h.get("ad_en") and h["ad_en"] != h["ad"]:
                v = wikidata_varlik(bag, h["ad_en"], insan=False, ipucu="Antalya")
        except Exception as e:
            notlar.append(f"Wikidata sorgusu yapılamadı ({h['ad']}): {e}")
            continue
        if not v:
            continue
        try:
            dosyalar = _dosya_bilgisi(bag, ["File:" + x for x in v["p18"]]) + (kategori_dosyalari(bag, v["kategori"], alt_kategori=4)[:30] if v["kategori"] else [])
        except Exception as e:
            notlar.append(f"Commons okunamadı ({h['ad']}): {e}")
            continue
        alinan = 0
        for d in dosyalar:
            ok, _ = commons_uygun(d)
            if not ok or bag.yasakli_mi(d["url"]):
                continue
            cekim = tarih_oku(d.get("tarih"))
            adaylar.append({
                "tur": "arsiv", "alt_tur": "kurum", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"]),
                "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                "kosul": commons.get("kosul", ""), "hedef": h["ad"], "qid": v["qid"], "cekim": str(cekim or ""), "kayit": d,
                "meta_puan": 50 + (10 if alinan == 0 else 0),
                "dogrulama": f"'{h['ad']}' Wikidata {v['qid']} kaydı ve Commons kategorisiyle doğrulandı",
            })
            alinan += 1
            if alinan >= 2:
                break
    return adaylar, notlar

def arsiv_adaylari(bag, konu):
    """Haberde adı geçen gerçek yer/kurum/tesis/takıma ait arşiv fotoğrafları (kendi arşivimiz, sonra Commons)."""
    adaylar, notlar = [], []
    hedefler = konu.get("hedefler") or []
    if not hedefler:
        notlar.append("haberde fotoğraflanabilir, adıyla geçen yer/kurum/tesis yok")
        return adaylar, notlar
    # a) kendi arşivimiz
    for h in hedefler:
        for k in bag.kendi_arsiv:
            if any(ad_eslesir(a, h["ad"]) or ad_eslesir(h["ad"], a) for a in k.get("anahtarlar") or []):
                if bag.yasakli_mi(k["gorsel"]):
                    continue
                adaylar.append({
                    "tur": "arsiv", "kaynak": "Gazete Antalya arşivi", "url": k["gorsel"], "kucuk": k["gorsel"], "sayfa": "",
                    "fotografci": k.get("kredi") or "Gazete Antalya", "lisans": k.get("lisans") or "Gazete Antalya",
                    "lisans_dayanagi": k["dayanak"], "kosul": k.get("kosul", ""), "hedef": h["ad"], "cekim": str(k.get("tarih") or ""),
                    "dogrulama": f"kendi arşivimizde '{h['ad']}' olarak kayıtlı fotoğraf",
                })
    # b) Commons: adın tamamı dosya başlığı/açıklaması/kategorilerinde geçmeli
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if commons and commons.get("izin") is True:
        for h in hedefler:
            if len([a for a in adaylar if a["hedef"] == h["ad"]]) >= 2:
                continue
            sorgular = list(dict.fromkeys([h["ad_en"], h["ad"]]))
            bulunan = 0
            for q in sorgular:
                try:
                    dosyalar = commons_dosyalar(bag, q)
                except Exception as e:
                    notlar.append(f"Commons araması yapılamadı ({q}): {e}")
                    continue
                for d in dosyalar:
                    ok, neden = commons_uygun(d)
                    metin = commons_metni(d)
                    if not ok or not (ad_eslesir(h["ad"], metin) or ad_eslesir(h["ad_en"], metin)):
                        continue
                    if bag.yasakli_mi(d["url"]) or any(a["url"] == d["url"] for a in adaylar):
                        continue
                    cekim = tarih_oku(d.get("tarih"))
                    adaylar.append({
                        "tur": "arsiv", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"]), "sayfa": d["sayfa"],
                        "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                        "kosul": commons.get("kosul", ""), "hedef": h["ad"], "cekim": str(cekim or ""),
                        "dogrulama": f"'{h['ad']}' adı dosya başlığı/açıklaması/kategorilerinde geçiyor" + (f"; çekim {cekim}" if cekim else ""),
                    })
                    bulunan += 1
                    if bulunan >= 2:
                        break
                if bulunan >= 2:
                    break
            if not bulunan and not any(a["hedef"] == h["ad"] for a in adaylar):
                notlar.append(f"'{h['ad']}' için kullanım hakkı doğrulanmış fotoğraf bulunamadı")
    return adaylar[:5], notlar


# ----------------------------------------------------------------- 3) görsel kontrol (görüntünün kendisi)
KONTROL_TALIMAT = """Antalya'da yayın yapan yerel bir haber sitesi için fotoğraf denetçisisin. Aşağıda bir haber ve numaralı fotoğraflar var.
Her fotoğrafın arşiv kaydında neyi gösterdiği yazıyor ("İDDİA"). Fotoğrafın KENDİSİNE bak; kayıttaki yazıya güvenme.
Her fotoğraf için değerlendir:
- fotograf_mi: Gerçek bir fotoğraf mı? (çizim, harita, logo, afiş, ekran görüntüsü, belge taraması değil)
- iddiayla_tutarli: Görüntü, iddia edilen yeri/tesisi/kurumu/takımı/olayı gösteriyor olabilir mi? Görüntüdeki unsurlar
  (manzara, mimari, tabelalar, forma renkleri, araç işaretleri vb.) iddiayla çelişiyorsa false.
- habere_uygun: Bu haberin görseli olarak, haberle ilgili doğru bir şey gösteriyor mu? (ör. haberde adı geçen yerin/tesisin fotoğrafı;
  kişi iddialarında: haberdeki kişiyi gösteren, sahnesi güncel haberle çelişmeyen bir arşiv fotoğrafı)
  Kişi iddialarında "iddiayla_tutarli" yalnızca sahnenin iddiayla çelişmemesi demektir (ör. iddia kişi fotoğrafı ama karede kimse yok,
  ya da tabela/yazı açıkça başka birini gösteriyor → false). Yüz karşılaştırması yapma.
- yaniltici: Haberdeki olayın ya da kişilerin görüntüsüymüş gibi yanlış izlenim yaratır mı? (başka bir olay, tören, kaza, kalabalık;
  haberde adı geçen kişi sanılabilecek biri; başka bir takım/kurum)
- yabanci_unsur: Türkiye dışına ait belirgin unsur var mı? (yabancı dilde tabela, yabancı plaka, "POLICE"/"911" gibi yabancı polis/ambulans işaretleri, bayrak)
- taninabilir_kisi: Yüzü tanınabilir biçimde görünen kişi var mı?
- etkinlik_benzerligi (0-10): Fotoğraftaki sahne türü, haberdeki etkinlik türüne ("{etkinlik}") ne kadar benziyor?
- tek_kisi_odakli: Fotoğrafta iddia edilen kişi tek başına ya da açıkça ön planda mı? (kalabalık zirve/toplu fotoğrafta false)
- gerekce: En fazla 20 kelimelik Türkçe açıklama.
ÖNEMLİ: Kişileri yüzlerinden tanımaya ÇALIŞMA ve kimlik hakkında yorum yapma; kişinin kimliği arşiv kaydıyla (Wikidata ve
Commons kategorisi) doğrulanmıştır. Yalnızca sahneyi değerlendir: başka bir etkinliğe ait pankart, yazı, tarih, bayrak veya
öne çıkan başka kişiler bu fotoğrafı güncel haberdeki etkinliğin görüntüsüymüş gibi gösterecekse "yaniltici": true.

HABER BAŞLIĞI: {baslik}
SPOT: {spot}
ANA OLAY: {olay}
KİŞİLER: {kisiler} | KURUMLAR: {kurumlar} | YER: {yer} | HASSASİYET: {hassas}

{iddialar}

Yalnızca şu biçimde JSON döndür:
{{"degerlendirmeler": [{{"no": 1, "fotograf_mi": false, "iddiayla_tutarli": false, "habere_uygun": false, "yaniltici": false,
 "yabanci_unsur": false, "taninabilir_kisi": false, "etkinlik_benzerligi": 0, "tek_kisi_odakli": false, "gerekce": "..."}}]}}"""


def gorsel_indir(url, en_fazla=900_000):
    if url.startswith("/"):   # kendi arşivimiz: dosya sitenin static klasöründe
        yol = os.path.join(KOK, "static", url.lstrip("/"))
        with open(yol, "rb") as f:
            veri = f.read()
        if len(veri) > 4_000_000:
            raise ServisHatasi("arşiv dosyası çok büyük")
        tip = "image/png" if yol.lower().endswith(".png") else "image/webp" if yol.lower().endswith(".webp") else "image/jpeg"
        return tip, base64.b64encode(veri).decode()
    r = requests.get(url, headers={"User-Agent": UA_BOT}, timeout=20)
    r.raise_for_status()
    tip = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not tip.startswith("image/") or len(r.content) > en_fazla:
        raise ServisHatasi(f"görsel uygun değil ({tip}, {len(r.content)} bayt)")
    return tip, base64.b64encode(r.content).decode()


def karar_ver(d, konu):
    """Yapay zekânın gözleminden kesin karar; kurallar kodda. Hepsi sağlanmazsa red."""
    nedenler = []
    if not d.get("fotograf_mi"):
        nedenler.append("gerçek fotoğraf değil")
    if not d.get("iddiayla_tutarli"):
        nedenler.append("görüntü, kaydın iddia ettiği yer/kurum/olayla tutarlı değil")
    if not d.get("habere_uygun"):
        nedenler.append("habere uygun değil")
    if d.get("yaniltici"):
        nedenler.append("yanıltıcı (başka olay/kişi/kurum izlenimi)")
    if d.get("yabanci_unsur") and not str(d.get("_alt_tur", "")).startswith("kisi"):
        nedenler.append("Türkiye dışına ait unsur var")
    if d.get("taninabilir_kisi") and not str(d.get("_alt_tur", "")).startswith("kisi") and (konu.get("hassas", "yok") != "yok" or konu.get("kisiler")):
        nedenler.append("tanınabilir kişi (hassas haber / adı geçen kişi sanılabilir)")
    return ("kabul" if not nedenler else "red"), nedenler


def gorsel_kontrol(bag, haber, konu, konu_imza, adaylar):
    """Adayları görüntüleriyle birlikte tek istekte inceler. Önbellekte kararı olanlar yeniden gönderilmez.
    Kontrol yapılamazsa KotaHatasi/ServisHatasi yükselir (çağıran fotoğrafsız kapağa geçer)."""
    gonderilecek, parcalar, iddialar = [], [], []
    for a in adaylar:
        ob = bag.onbellek_al(imza(KURAL_SURUMU, konu_imza, a["url"]))
        if ob:
            a.update(karar=ob["karar"], gerekce=ob.get("gerekce", ""), kontrol_tarih=ob.get("t", ""), onbellekten=True,
                     benzerlik=ob.get("benzerlik", 0), tek=ob.get("tek", False))
            continue
        try:
            tip, veri = gorsel_indir(a.get("kucuk") or a["url"])
        except Exception as e:
            a.update(karar="red", gerekce=f"görsel indirilemedi: {str(e)[:80]}", kontrol_tarih="")
            continue
        gonderilecek.append(a)
        n = len(gonderilecek)
        kayit = a.get("kayit") or {}
        iddialar.append(f"FOTOĞRAF {n} İDDİA: {('aynı olayın fotoğrafı: ' if a['tur'] == 'gercek' else 'arşiv fotoğrafı: ')}"
                        f"{a.get('hedef', '')}" + (f" (çekim {a['cekim']})" if a.get("cekim") else "")
                        + (f" | arşiv açıklaması: {temiz(kayit.get('baslik', '').replace('File:', ''), 90)} — {temiz(kayit.get('aciklama'), 140)}" if kayit else ""))
        parcalar += [{"text": f"FOTOĞRAF {n}:"}, {"inlineData": {"mimeType": tip, "data": veri}}]
    if not gonderilecek:
        return adaylar
    metin = KONTROL_TALIMAT.format(
        baslik=haber.get("baslik", ""), spot=haber.get("spot", ""), olay=konu.get("ana_olay", ""),
        kisiler=", ".join(konu.get("kisiler") or []) or "-", kurumlar=", ".join(konu.get("kurumlar") or []) or "-",
        yer=konu.get("yer") or "-", hassas=konu.get("hassas", "yok"), iddialar="\n".join(iddialar),
        etkinlik=(konu.get("etkinlik") or {}).get("tur") or konu.get("ana_olay", ""))
    yanit = bag.gemini([{"text": metin}] + parcalar, en_fazla=1500)
    liste = {int(d.get("no", 0)): d for d in (yanit.get("degerlendirmeler") or []) if isinstance(d, dict)}
    tarih = simdi_tr().isoformat(timespec="seconds")
    for i, a in enumerate(gonderilecek, 1):
        d = liste.get(i)
        if not d:
            a.update(karar="red", gerekce="değerlendirme yanıtında yok", kontrol_tarih=tarih)
            continue
        karar, nedenler = karar_ver(dict(d, _alt_tur=a.get("alt_tur", "")), konu)
        gerekce = temiz(d.get("gerekce"), 200)
        if nedenler:
            gerekce = "; ".join(nedenler) + (f" — {gerekce}" if gerekce else "")
        benz = int(d.get("etkinlik_benzerligi") or 0)
        tek = bool(d.get("tek_kisi_odakli"))
        a.update(karar=karar, gerekce=gerekce, kontrol_tarih=tarih, benzerlik=benz, tek=tek,
                 bulgular={k: d.get(k) for k in ("fotograf_mi", "iddiayla_tutarli", "habere_uygun", "yaniltici", "yabanci_unsur",
                                                  "taninabilir_kisi", "etkinlik_benzerligi", "tek_kisi_odakli")})
        bag.onbellek_koy(imza(KURAL_SURUMU, konu_imza, a["url"]), {"karar": karar, "gerekce": gerekce, "t": tarih, "benzerlik": benz, "tek": tek})
    return adaylar


# ----------------------------------------------------------------- seçim
def kapak_secimi(kategori, gerekce, kontrol, konu_imza):
    kat = kategori if kategori in KATEGORI_DOSYA else "Gündem"
    return {
        "tur": "kategori", "gorsel": f"/images/kategori/{KATEGORI_DOSYA[kat]}.png", "gorsel_kredi": "",
        "kaynak_sayfa": "", "fotografci": "", "kredi": "Gazete Antalya", "lisans": "Gazete Antalya'nın kendi tasarımı",
        "lisans_dayanagi": "Sitenin kendi grafik dosyası", "kosul": "", "gerekce": gerekce, "kontrol": kontrol,
        "surum": KURAL_SURUMU, "konu_imza": konu_imza,
    }


def alt_yazi(a, konu):
    """Arşiv fotoğrafının güncel haberle karıştırılmaması için açıklama."""
    yil = (a.get("cekim") or "")[:4]
    ek = f" ({yil})" if yil else ""
    alt = a.get("alt_tur", "")
    if alt == "kisi_benzer":
        etk = (konu.get("etkinlik") or {}).get("tur") or "etkinlik"
        return f"{a['kisi']}, önceki bir {_ek_den(etk)} — Arşiv{ek}"
    if alt == "kisi_genel":
        return f"{a['kisi']} — Arşiv fotoğrafı{ek}"
    if a["tur"] == "arsiv":
        return f"{a.get('hedef', '')} — Arşiv fotoğrafı{ek}"
    return ""


def aday_secimi(a, kontrol, konu_imza, konu=None):
    kredi = f"{a['fotografci']} / {a['kaynak']}" + (f" ({a['lisans']})" if a["kaynak"] == "Wikimedia Commons" else "")
    yil = (a.get("cekim") or "")[:4] if a["tur"] == "arsiv" else ""
    return {
        "tur": a["tur"], "gorsel": a["url"], "gorsel_kredi": ("Arşiv fotoğrafı: " if a["tur"] == "arsiv" else "Fotoğraf: ") + kredi,
        "kaynak_sayfa": a.get("sayfa", ""), "fotografci": a.get("fotografci", ""), "kredi": kredi, "lisans": a.get("lisans", ""),
        "lisans_dayanagi": a.get("lisans_dayanagi", ""), "kosul": a.get("kosul", ""), "gosterilen": a.get("hedef", ""),
        "cekim_yili": yil, "gerekce": f"{a.get('dogrulama', '')}; görsel kontrol: {a.get('gerekce', '')}",
        "kontrol": kontrol, "surum": KURAL_SURUMU, "konu_imza": konu_imza,
        "aciklama": alt_yazi(a, konu or {}), "alt_tur": a.get("alt_tur", ""), "qid": a.get("qid", ""),
        "konum": "50% 22%" if a.get("dikey") else "",
    }


def gorsel_sec(bag, haber, konu, eski=None):
    """haber: {baslik, spot, kategori, ilce, tarih(date), kaynak_url, kaynak_gorsel}
    Dönüş: (secim, rapor). Gerçek → arşiv → fotoğrafsız kapak. Başka seçenek yoktur."""
    kimza = konu_imzasi(haber.get("baslik"), haber.get("spot"), haber.get("kategori"))
    rapor = {"notlar": [], "adaylar": [], "eski": None}
    simdi = simdi_tr().isoformat(timespec="seconds")
    if not konu:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "konu ayrıştırılamadı"}
        s = kapak_secimi(haber.get("kategori"), "Haberin konusu ayrıştırılamadı; fotoğrafsız kapak.", k, kimza)
        s["bekliyor"] = True
        return s, rapor

    gercek, n1 = gercek_adaylar(bag, konu, haber)
    kisi, n3 = kisi_adaylari(bag, konu, haber)
    kurum_wd, n4 = kurum_wikidata_adaylari(bag, konu)
    arsiv, n2 = arsiv_adaylari(bag, konu)
    rapor["notlar"] += n1 + n3 + n4 + n2
    gercek += [a for a in kisi if a["tur"] == "gercek"]
    kisi = [a for a in kisi if a["tur"] != "gercek"]
    gorulen = {a["url"] for a in gercek + kisi}
    kurum = []
    for a in kurum_wd + arsiv:
        if a["url"] not in gorulen:
            gorulen.add(a["url"])
            kurum.append(dict(a, alt_tur=a.get("alt_tur") or "kurum"))
    kurum = kurum[:4]
    adaylar = gercek + kisi + kurum
    eski_aday = None
    if eski and eski.get("url"):
        eski_aday = {"tur": "eski", "kaynak": "mevcut", "url": eski["url"], "kucuk": kontrol_kucuk(eski["url"]),
                     "hedef": "haberin şu anki görseli (eski otomatik seçim)"}
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
        rapor["eski"] = {k: eski_aday.get(k) for k in ("karar", "gerekce", "bulgular")}
    rapor["adaylar"] = [{k: a.get(k) for k in ("tur", "alt_tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "hedef", "cekim", "karar",
                                               "gerekce", "dogrulama", "benzerlik", "tek", "meta_puan")} for a in adaylar]

    if not kontrol_yapildi and adaylar:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "Gemini görsel kontrol", "neden": hata}
        s = kapak_secimi(haber.get("kategori"), f"Adaylar görsel kontrolden geçirilemedi ({hata}); kontrol edilmemiş fotoğraf kullanılmadı.", k, kimza)
        s["bekliyor"] = True
        return s, rapor
    def sira(a):   # kişi/kurum doğruluğu (katman) + etkinlik benzerliği + tek kişi odağı + kayıt puanı
        katman = {"gercek": 4, "kisi_benzer": 3, "kisi_genel": 2}.get(a.get("alt_tur") or a["tur"], 1)
        if a["tur"] == "gercek":
            katman = 4
        return (katman, (a.get("benzerlik") or 0) + (3 if a.get("tek") else 0) + (a.get("meta_puan") or 0) / 100)
    kabul = sorted([a for a in gercek + kisi + kurum if a.get("karar") == "kabul"], key=sira, reverse=True)
    if kabul:
        a = kabul[0]
        kontrol = {"yapildi": True, "sonuc": "onaylandı", "tarih": a.get("kontrol_tarih") or simdi,
                   "yontem": "Wikidata/Commons kimlik doğrulaması + Gemini sahne kontrolü" if a.get("qid") else "kaynak/açıklama doğrulaması + Gemini görsel kontrol",
                   "model": bag.model_fn()}
        if a.get("qid") and a.get("kayit"):
            kut = bag.arsiv.setdefault("kut:" + a["qid"], [])
            if not any(isinstance(x, dict) and x.get("url") == a["url"] for x in kut):
                kut.append(dict(a["kayit"], onay=simdi[:10]))
                del kut[:-30]
        return aday_secimi(a, kontrol, kimza, konu), rapor
    if adaylar:
        k = {"yapildi": True, "sonuc": "adaylar reddedildi", "tarih": simdi, "yontem": "Gemini görsel kontrol", "model": bag.model_fn()}
        neden = f"{len(adaylar)} aday görsel kontrolden geçemedi"
    else:
        k = {"yapildi": False, "sonuc": "aday yok", "tarih": simdi, "yontem": "kaynak/arşiv taraması"}
        neden = "olaya ait ya da haberde adı geçen yere/kuruma ait kullanım hakkı doğrulanmış fotoğraf bulunamadı"
    return kapak_secimi(haber.get("kategori"), neden.capitalize() + "; fotoğrafsız kapak.", k, kimza), rapor


# ----------------------------------------------------------------- ön bilgi (front matter)
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
        return True, "otomatik (eski hat: denetimsiz stok/rastgele seçim)"
    return False, "kökeni belirsiz"


def secimi_uygula(v, secim):
    """Seçimi ön bilgiye yazar. Önceki görsel geri alınabilsin diye 'gorsel_onceki' alanında saklanır."""
    if "gorsel_onceki" not in v and (v.get("gorsel") or v.get("gorsel_kredi")):
        v["gorsel_onceki"] = {"gorsel": v.get("gorsel", ""), "gorsel_kredi": v.get("gorsel_kredi", "")}
    v["gorsel"] = secim["gorsel"]
    v["gorsel_kredi"] = secim["gorsel_kredi"]
    v["gorsel_tur"] = secim["tur"]
    kayit = {k: secim.get(k, "") for k in ("tur", "kaynak_sayfa", "fotografci", "kredi", "lisans", "lisans_dayanagi", "kosul",
                                           "gosterilen", "cekim_yili", "gerekce", "surum", "konu_imza")}
    kayit["gorsel_url"] = secim["gorsel"]
    kayit["kontrol"] = secim.get("kontrol", {})
    for k in ("aciklama", "alt_tur", "qid"):
        if secim.get(k):
            kayit[k] = secim[k]
    if secim.get("konum"):
        v["gorsel_konum"] = secim["konum"]
    else:
        v.pop("gorsel_konum", None)
    if secim.get("bekliyor"):
        kayit["bekliyor"] = True
    v["gorsel_secim"] = kayit
    v.pop("gorsel_v4", None)
    return v


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
            "ilce": (v.get("ilceler") or [""])[0], "tarih": tarih, "kaynak_url": v.get("kaynak_url", ""), "kaynak_gorsel": "", "metin": govde}


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


def haber_isle(bag, yol):
    """Tek bir haberin görselini yeni yöntemle seçer ve dosyaya yazar. Eski görsel yasaklı listeye girer."""
    v, govde = md_oku(yol)
    oto, neden = otomatik_gorsel_mi(v)
    if not oto:
        return None, neden
    h = haber_bilgisi(v, govde)
    try:
        konu = konu_ayristir(bag, h["baslik"], h["spot"], govde)
    except (KotaHatasi, ServisHatasi) as e:
        konu = None
        bag.notlar.append(f"{h['baslik'][:60]}: konu ayrıştırılamadı ({e})")
    secim, _ = gorsel_sec(bag, h, konu)
    eski = v.get("gorsel") or ""
    if eski and eski != secim["gorsel"] and not eski.startswith("/images/kategori/"):
        bag.yasakla(eski)
    secimi_uygula(v, secim)
    md_yaz(yol, v, govde)
    return secim, "düzeltildi"


def toplu_duzelt(bag, limit=3, yalniz_bekleyen=False):
    """Otomatik görselleri küçük gruplar hâlinde düzeltir (yenilerden eskiye).
    yalniz_bekleyen=True: yalnızca kontrolü kota yüzünden ertelenmiş yeni hat kayıtlarını yeniden dener."""
    duzeltilen = 0
    for ad in sorted(os.listdir(HABER_KLASOR), reverse=True):
        if duzeltilen >= limit or not bag.yz_hakki(2):
            break
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            v, _ = md_oku(yol)
            oto, _ = otomatik_gorsel_mi(v)
            if not oto or not yeniden_gerekli(v):
                continue
            if yalniz_bekleyen and not (isinstance(v.get("gorsel_secim"), dict) and v["gorsel_secim"].get("bekliyor")):
                continue
            secim, _ = haber_isle(bag, yol)
            duzeltilen += 1
            print(f"Görsel düzeltildi: {ad} → {secim['tur']}")
        except Exception as e:
            print("Görsel düzeltilemedi:", ad, e)
    return duzeltilen


def deneme_calistir(bag, dosyalar):
    """Seçilen haberler için karşılaştırmalı deneme. Haber dosyalarına DOKUNMAZ."""
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
            satir.update(yeni=secim, eski_degerlendirme=rapor["eski"], adaylar=rapor["adaylar"], notlar=rapor["notlar"])
            print(f"Deneme: {ad[:60]} → {secim['tur']}")
        except Exception as e:
            satir["hata"] = str(e)[:200]
        satirlar.append(satir)
    return {"tarih": simdi_tr().isoformat(timespec="seconds"), "surum": KURAL_SURUMU, "model": bag.model_fn() if bag.gemini_key else "",
            "dosyalar": dosyalar, "satirlar": satirlar,
            "eksik": sum(1 for x in satirlar if (x.get("yeni") or {}).get("bekliyor")),
            "atlanan_kontroller": bag.notlar[:], "yz_kullanilan": bag.yz_tur}
