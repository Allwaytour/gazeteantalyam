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

try:
    import kurum_foto as kf
except Exception:          # tarayıcı/Pillow yoksa kurum fotoğrafı katmanı devre dışı kalır
    kf = None

SURUM = "g9.4"
BASLANGIC = time.time()
KURAL_TARIHI = "2026-11-05"
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
OZNE_TURLERI = {"kisi", "kurum", "takim", "yer", "nesne", "olay"}
GIZLI_AD = re.compile(r"(^|\s)[A-ZÇĞİÖŞÜ]\.(\s|$)|^[A-ZÇĞİÖŞÜ]{1,2}\.?\s?[A-ZÇĞİÖŞÜ]{1,2}\.?$")   # "M.B.", "A. K." gibi gizlenmiş adlar
# Eski hatların (Unsplash/Openverse/Commons rastgele seçimi) ürettiği görseller: düzeltilecekler
OTOMATIK_HOST = re.compile(r"^https://(images\.unsplash\.com|(upload|thumb)\.wikimedia\.org|live\.staticflickr\.com|api\.openverse\.org)/")
SERBEST_LISANS = re.compile(r"^(CC0|CC[ -]BY([ -]SA)?([ -]\d\.\d)?|Public domain|PD\b|Public Domain Mark|No restrictions|Attribution\b)", re.I)
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


try:
    from zoneinfo import ZoneInfo
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:                      # saat dilimi veritabanı yoksa kış saati
    _PT = timezone(timedelta(hours=-8))
GUNLUK_KOTA_RE = re.compile(r"(?i)PerDay|per day")


def gemini_gunu():
    """Gemini ücretsiz kotası Pasifik saatiyle gece yarısı yenilenir; günlük yapay zekâ sayaçları bu güne göre tutulur."""
    return datetime.now(_PT).strftime("%Y-%m-%d")


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
    try:
        return json.loads(metin)
    except ValueError:
        pass
    # Modelin JSON'dan önce/sonra yazdığı fazlalıkları at; ilk tam nesneyi oku.
    bas = metin.find("{")
    if bas < 0:
        raise ValueError("yanıtta JSON nesnesi yok")
    try:
        nesne, _ = json.JSONDecoder().raw_decode(metin[bas:])
    except ValueError:
        # Modelin metne koyduğu geçersiz ters bölüler (ör. "\ş", yarım "\u12") JSON'u bozmasın
        temiz_metin = re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r"\\\\", metin[bas:])
        nesne, _ = json.JSONDecoder().raw_decode(temiz_metin)
    return nesne


# ----------------------------------------------------------------- bağlam: ayarlar, kota, önbellek
class Baglam:
    """Bir çalışma turunun ayarları, kota sayaçları ve önbelleği (data/otomatik_durum.json → 'gorsel')."""

    def __init__(self, ayar, durum, gemini_key="", model_fn=None, arsiv=None):
        self.ayar = ayar or {}
        self.gemini_key = gemini_key
        self.model_fn = model_fn or (lambda: "gemini-2.5-flash-lite")
        self.arsiv = arsiv if arsiv is not None else {}
        g = durum.setdefault("gorsel", {})
        bugun = gemini_gunu()
        if g.get("gun") != bugun:
            g["gun"], g["yz_bugun"] = bugun, 0
        # Kural sürümü değiştiyse önceki sürümün kararları (kabul edilmiş yanlış seçimler dahil) silinir
        g["onbellek"] = {k: v for k, v in (g.get("onbellek") or {}).items() if isinstance(v, dict) and v.get("s") == KURAL_SURUMU}
        for eski in ("unsplash", "unsplash_kapali"):
            g.pop(eski, None)
        g.setdefault("yasakli", [])          # bir daha hiçbir habere seçilmeyecek görseller
        if g.get("yasakli_surum") != "g9":
            # g9: önceki sürümler bir haberde değiştirilen HER görseli (ör. Devlet Bahçeli'nin doğrulanmış portresi) tüm haberler
            # için yasaklıyordu. Yalnızca eski hattın denetimsiz stok görselleri yasaklı kalır.
            g["yasakli"] = [u for u in g["yasakli"] if "wikimedia.org" not in u and not u.startswith("/images/kurum/")]
            g["yasakli_surum"] = "g9"
        self.g = g
        self.yz_tur = 0
        self.son_yz = 0.0
        self.izinler = izinleri_oku()
        self.kendi_arsiv = kendi_arsivi_oku()
        self.notlar = []
        self.son_model = ""
        self.yedek_model_fn = None          # 503'te bir kez denenecek ikinci model (haber_cek verir)
        self.model_bitti_fn = None          # günlük kotası dolan modelden sonraki modele geçiş (haber_cek verir)
        self.kalan_modeller_fn = None       # bugün kotası dolmamış modeller
        self.biten_modeller = set()         # bugün kotası dolan modeller (haber_cek ile ortak küme)
        self.yeni_secim()

    def yeni_secim(self):
        """Her haber için arama bütçesi sıfırlanır (sorgu sayısı sınırlı; bütçe dolması 'fotoğraf yok' sayılmaz)."""
        self.sorgu_sayac = 0
        self.sorgu_kaydi = []           # tanı: yapılan aramalar ve sonuç sayıları
        self.elenen = []                # tanı: görüntü kontrolüne gitmeden elenen dosyalar ve nedenleri
        self.butce_doldu = False
        self.arama_hatasi = False

    def sorgu_hakki(self):
        if self.sorgu_sayac >= int(self.ayar.get("gorsel_sorgu_butcesi", 12)):
            self.butce_doldu = True
            return False
        self.sorgu_sayac += 1
        return True

    @property
    def yz_gunluk(self):
        return int(self.ayar.get("gorsel_yz_gunluk", 100))

    def yz_hakki(self, adet=1):
        # Gemini günlük kotası gerçekten dolduysa (429 PerDay) o gün başka istek denenmez; kota yenilenince açılır.
        return (bool(self.gemini_key) and self.g.get("kota_bitti") != gemini_gunu()
                and self.g["yz_bugun"] + adet <= self.yz_gunluk)

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

    def gemini(self, parcalar, en_fazla=1024, sicaklik=0.1, model=None):
        model = model or self.model_fn()
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
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  headers={"x-goog-api-key": self.gemini_key, "Content-Type": "application/json"}, json=istek, timeout=90)
            except requests.RequestException as e:
                raise ServisHatasi(f"bağlantı: {e}")
            if r.status_code == 429:
                if GUNLUK_KOTA_RE.search(r.text or ""):
                    # Ücretsiz kota her model için ayrıdır: kotası dolan model bırakılır, kotası olan bir sonraki modelle denenir.
                    yeni = self.model_bitti_fn(model) if self.model_bitti_fn else ""
                    if yeni and yeni != model:
                        print(f"   {model} günlük kotası doldu; {yeni} ile devam ediliyor")
                        return self.gemini(parcalar, en_fazla, sicaklik, model=yeni)
                    self.g["kota_bitti"] = gemini_gunu()
                    raise KotaHatasi("Gemini günlük kotası doldu (429); kota yenilenince yeniden denenecek")
                if deneme == 0:
                    time.sleep(20)
                    continue
                raise KotaHatasi("Gemini kotası doldu (429)")
            if r.status_code in (500, 502, 503, 504) and deneme == 0:   # model geçici olarak yoğun
                time.sleep(12)
                continue
            if r.status_code in (500, 502, 503, 504) and self.yedek_model_fn and not getattr(self, "_yedekte", False):
                yedek = self.yedek_model_fn()
                if yedek and yedek != model:
                    print(f"   {model} yoğun ({r.status_code}); {yedek} ile bir kez deneniyor")
                    self._yedekte = True
                    try:
                        return self.gemini(parcalar, en_fazla, sicaklik, model=yedek)
                    finally:
                        self._yedekte = False
            if r.status_code >= 400:
                raise ServisHatasi(f"Gemini {r.status_code}: {r.text[:160]}")
            self.g["yz_bugun"] += 1
            self.yz_tur += 1
            self.son_model = model
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
 "ana_kisiler": [{"ad": "...", "rol": "...", "kamusal": true, "hassas_rol": ""}],
 "ana_ozne": {"tur": "kisi | kurum | takim | yer | nesne | olay", "ad": "...", "ad_en": "..."},
 "nerede": {"ad": "...", "ad_en": "..."}, "ne": {"ad": "...", "ad_en": "..."},
 "gorsel_ipucu_en": ["..."], "alternatif_sorgular_en": ["..."],
 "konu_gorseli": {"uygun": false, "sorgular_en": ["..."], "sorgular_tr": ["..."]},
 "gorselde_olmali": ["..."], "gorselde_olmamali": ["..."],
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
  "ad" tam ad ya da kamuoyunun bildiği sahne adı (ör. "Ersin Tatar", "Murat Kurum", "Tarkan"); "rol" haberdeki görevi/mesleği,
  Türkçe ve İngilizce (ör. "KKTC Cumhurbaşkanı / president", "Çevre Bakanı / minister", "şarkıcı / singer", "futbolcu / footballer").
  "kamusal": kişi kamuya mal olmuş biri mi (siyasetçi, belediye başkanı, bakan, vali, kamu yöneticisi, sanatçı, sporcu)?
  Kamuya mal olmuş kişi haberin ANA ÖZNESİYSE dava, soruşturma, gözaltı, sağlık gibi hassas haberlerde de yaz ve "hassas_rol"
  alanına durumunu yaz (ör. "sanık", "tutuklu", "şüpheli", "tanık", "hasta"); değilse boş bırak.
  Özel kişileri (kamuya mal olmamış vatandaşlar), çocukları ve adı kısaltılmış/gizlenmiş kişileri (ör. "M.B.", "A.K.") ASLA yazma.
  Yalnızca soyadı geçiyorsa tam adını metinden bul; bulamazsan yazma.
- ana_ozne: BAŞLIK ve SPOTA göre haberin asıl öznesi. "tur": kisi (bir kişi), kurum (belediye, bakanlık, oda, şirket),
  takim (spor kulübü), yer (belirli bir ilçe, mahalle, tesis, yol, plaj, antik kent), nesne (genel bir ürün/olgu: akaryakıt,
  ekmek, elektrik faturası, kira, hava durumu, trafik), olay (adı konmuş etkinlik). Haberde ikincil olarak geçen şehir ya da
  kişiyi ana özne yapma (ör. "Alanya'da LPG'ye zam" → nesne "LPG"; "Vali X'ten çağrı" → kisi X).
  "ad" Türkçe kısa ad; "ad_en" fotoğraf araması için İngilizce karşılığı (nesnede somut görüntü: "LPG" → "LPG gas pump",
  "akaryakıt" → "fuel pump", "elektrik faturası" → "electricity meter").
- nerede / ne: Haberin NEREDE olduğu ve NE olduğu ayrı ayrı. "nerede": olayın geçtiği belirli yer/tesis (ör. "Antalya Havalimanı",
  "Alanya", "Konyaaltı Sahili"); yoksa boş. "ne": olayın/konunun kendisi (ör. "yakıt sızıntısı", "otel doluluğu", "elektrik desteği").
- gorsel_ipucu_en: O yerde haberin konusuyla DOĞRUDAN ilişkili, fotoğrafta görülebilecek somut şeyler, İngilizce 1-3 ifade
  (ör. havalimanında yakıt sızıntısı → ["apron", "aircraft", "refueling"]; Alanya otel doluluğu → ["hotels", "hotel", "resort"];
  liman haberi → ["port", "harbour"]). Yalnızca aynı yerde bulunan ama konuyla ilgisiz şeyleri (tabela, otopark) yazma.
- alternatif_sorgular_en: Nesne/olgu için Türkiye'ye uygun 2-3 alternatif fotoğraf araması (ör. elektrik desteği →
  ["electricity meter Turkey", "electricity bill", "power distribution meter"]).
- konu_gorseli: Haberin KONUSUNU (spor dalı, ürün, faaliyet, sektör) doğrudan gösteren genel bir fotoğraf, olayın kendisi sanılmadan
  haberi anlatabilir mi? "uygun": true yalnızca hassas olmayan haberlerde ve fotoğraf belirli bir kişiyi/olayı göstermek zorunda değilse
  (ör. muaythai şampiyonası → muaythai müsabakası; benzin indirimi → benzin pompası/dolum; turizm rekoru → otel bölgesi, turistik sahil).
  Kişi açıklaması, siyaset, dava, suç, kaza, ölüm, sağlık haberlerinde false. "sorgular_en"/"sorgular_tr": 2-3 SOMUT arama
  (ör. ["muay thai fight", "muay thai ring"]); "spor", "ekonomi" gibi genel kelimeler yazma; benzer ama FARKLI şeyi yazma (muaythai ≠ boks/karate,
  LPG ≠ benzin).
- gorselde_olmali: Fotoğrafta MUTLAKA görülmesi gereken 1-3 somut unsur, Türkçe (ör. ["muaythai müsabakası"], ["havalimanı apronu", "uçak"],
  ["LPG pompası"]). gorselde_olmamali: Görülmemesi gereken, yanlış izlenim verecek 1-4 unsur (ör. ["boks", "karate"], ["benzin tabancası"],
  ["yabancı polis aracı", "ceset", "kan"], ["başka takımın forması"]).
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
        "ana_ozne": {"tur": "", "ad": "", "ad_en": ""},
        "etkinlik": {"tur": "", "anahtar_tr": [], "anahtar_en": []},
    }
    for alan in ("nerede", "ne"):
        x = k.get(alan) if isinstance(k.get(alan), dict) else {}
        sonuc[alan] = {"ad": temiz(x.get("ad"), 80), "ad_en": temiz(x.get("ad_en") or x.get("ad"), 80)}
    sonuc["gorsel_ipucu_en"] = [temiz(x, 30).lower() for x in (k.get("gorsel_ipucu_en") or []) if temiz(x)][:3]
    sonuc["alternatif_sorgular_en"] = [temiz(x, 50) for x in (k.get("alternatif_sorgular_en") or []) if temiz(x)][:3]
    kg = k.get("konu_gorseli") if isinstance(k.get("konu_gorseli"), dict) else {}
    sonuc["konu_gorseli"] = {"uygun": kg.get("uygun") is True,
                             "sorgular_en": [temiz(x, 50) for x in (kg.get("sorgular_en") or []) if temiz(x)][:3],
                             "sorgular_tr": [temiz(x, 50) for x in (kg.get("sorgular_tr") or []) if temiz(x)][:2]}
    sonuc["gorselde_olmali"] = [temiz(x, 40) for x in (k.get("gorselde_olmali") or []) if temiz(x)][:3]
    sonuc["gorselde_olmamali"] = [temiz(x, 40) for x in (k.get("gorselde_olmamali") or []) if temiz(x)][:4]
    oz = k.get("ana_ozne") if isinstance(k.get("ana_ozne"), dict) else {}
    oz_tur = str(oz.get("tur") or "").strip().lower()
    if oz_tur in OZNE_TURLERI and temiz(oz.get("ad")):
        sonuc["ana_ozne"] = {"tur": oz_tur, "ad": temiz(oz.get("ad"), 80), "ad_en": temiz(oz.get("ad_en") or oz.get("ad"), 80)}
    for kisi in (k.get("ana_kisiler") or [])[:3]:
        # Tek kelimelik sahne adları (Teoman, Tarkan) da alınır; kimlik Wikidata'da rol ipucuyla ayrıca doğrulanır.
        if isinstance(kisi, dict) and kelimeler(kisi.get("ad")) and len(" ".join(kelimeler(kisi.get("ad")))) >= 4 \
                and not GIZLI_AD.search(str(kisi.get("ad"))):
            sonuc["ana_kisiler"].append({"ad": temiz(kisi.get("ad"), 60), "rol": temiz(kisi.get("rol"), 80),
                                         "kamusal": kisi.get("kamusal") is not False, "hassas_rol": temiz(kisi.get("hassas_rol"), 30)})
    sonuc["ana_kisiler"] = sonuc["ana_kisiler"][:2]
    e = k.get("etkinlik") if isinstance(k.get("etkinlik"), dict) else {}
    sonuc["etkinlik"] = {"tur": temiz(e.get("tur"), 30).lower(),
                         "anahtar_tr": [temiz(x, 30).lower() for x in (e.get("anahtar_tr") or []) if temiz(x)][:4],
                         "anahtar_en": [temiz(x, 30).lower() for x in (e.get("anahtar_en") or []) if temiz(x)][:4]}
    if sonuc["hassas"] not in HASSAS:
        sonuc["hassas"] = "yok"
    kg = sonuc["konu_gorseli"]
    if not kg["sorgular_en"] and sonuc["ana_ozne"]["tur"] == "nesne":
        # eski biçimdeki konu kaydı: nesne haberinde nesnenin İngilizce adı ve alternatif sorgular kullanılır
        kg.update(uygun=True, sorgular_en=[sonuc["ana_ozne"]["ad_en"]] + sonuc["alternatif_sorgular_en"][:2])
    if sonuc["hassas"] in NESNE_HASSAS:
        kg["uygun"] = False
    for h in (k.get("hedefler") or [])[:4]:
        if not isinstance(h, dict):
            continue
        ad, ad_en = temiz(h.get("ad"), 80), temiz(h.get("ad_en") or h.get("ad"), 80)
        tur = str(h.get("tur") or "").strip().lower()
        if ad and ayirt_edici(ad) and tur in HEDEF_TURLERI:
            sonuc["hedefler"].append({"ad": ad, "ad_en": ad_en if ayirt_edici(ad_en) else ad, "tur": tur})
    oz = sonuc["ana_ozne"]
    if oz["tur"] in ("takim", "kurum", "yer", "olay") and ayirt_edici(oz["ad"]) \
            and not any(kucult(h["ad"]) == kucult(oz["ad"]) for h in sonuc["hedefler"]):
        # Ana özne bir takım/kurum/yer ise fotoğrafı her zaman doğrudan aranır (model hedef listesine yazmasa da)
        sonuc["hedefler"].insert(0, {"ad": oz["ad"], "ad_en": oz["ad_en"] if ayirt_edici(oz["ad_en"]) else oz["ad"], "tur": oz["tur"]})
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
        return konu_dogrula(ob["konu"]) or ob["konu"]     # önbellekteki konu da güncel kurallardan geçer
    talimat = ("Bir haberin görselini seçmek için haberin konusunu ayrıştır. Yalnızca metinde geçen bilgileri kullan, uydurma.\n"
               + KONU_KURALLARI + "\n\nYalnızca şu biçimde JSON döndür:\n" + KONU_ALANLARI
               + f"\n\nBAŞLIK: {baslik}\nSPOT: {spot}\n\nMETİN:\n{(metin or '')[:3500]}")
    konu = konu_dogrula(bag.gemini([{"text": talimat}], en_fazla=1600))
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
    if bag.arsiv.get(anahtar):            # boş sonuç saklanmaz; sonradan yüklenen dosyalar bulunabilsin
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
    if sonuc:
        bag.arsiv[anahtar] = sonuc
    return sonuc


def kontrol_kucuk(url, genislik=0):
    """Kontrol için küçük kopya. Commons yalnızca standart boyutları verir (500px)."""
    if "/thumb/" in url:
        return re.sub(r"/\d+px-", "/500px-", url)
    m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2}/)([^/?]+)", url)
    if m and genislik > 500:   # özgün boyutlu dosya: 500px küçük kopyası istenir
        return f"{m.group(1)}thumb/{m.group(2)}{m.group(3)}/500px-{m.group(3)}"
    return url


def commons_metni(d):
    return " ".join([d["baslik"], d["aciklama"], d["nesne"], d["kategoriler"]])


KISITLI_LISANS = re.compile(r"(?i)(^|[\s-])(NC|ND)([\s-]|$)|non.?commercial|no.?deriv|fair use|copyright(ed)?\b(?! free)")
LISANS_OKUNAMADI = []  # lisans alanı boş/okunamayan dosyalar (gerçek lisans reddinden ayrı tutulur)


def commons_uygun(d):
    lisans = (d.get("lisans") or "").strip()
    if not lisans:
        LISANS_OKUNAMADI.append(d.get("baslik", ""))
        return False, "lisans bilgisi okunamadı"
    if KISITLI_LISANS.search(lisans) or not SERBEST_LISANS.match(lisans):
        LISANS_RED.append(d.get("baslik", ""))
        return False, f"lisans site kullanımına uygun değil ({lisans})"
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
                metin = commons_metni(d)
                cekim = tarih_oku(d.get("tarih"))
                if not cekim or not hedef_tarih or abs((cekim - hedef_tarih).days) > 3:
                    continue
                if not (yerler & {kok(w) for w in kelimeler(metin)}) or not ad_eslesir(konu["gercek_arama"], metin):
                    continue
                if not commons_uygun(d)[0]:
                    continue
                if bag.yasakli_mi(d["url"]):
                    continue
                adaylar.append({
                    "tur": "gercek", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]), "sayfa": d["sayfa"],
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


def guncel_mekan(iddialar):
    """Takımın BUGÜNKÜ iç saha stadı: bitiş tarihi (P582) olan ya da geçersiz sayılan kayıtlar atlanır; 'tercih edilen' önce."""
    gecerli = [c for c in iddialar if c.get("rank") != "deprecated" and "P582" not in (c.get("qualifiers") or {})]
    tercih = [c for c in gecerli if c.get("rank") == "preferred"] or gecerli
    idler = [c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") for c in tercih]
    return [i for i in idler if i][:1]


# Takım fotoğrafında belirli bir maç/rakip öne çıkıyorsa başka bir karşılaşmanın görüntüsü sanılır.
MAC_IPUCU = re.compile(r"(?i)\b(vs\.?|v\.|match|maçı?|derbi|derby|ceremony|seremoni)\b|\w+spor\s*[-–]\s*\w+|\w+\s*[-–]\s*\w+spor|\d+\s*[-–]\s*\d+")


OYUNCU_IPUCU = re.compile(r"(?i)\b(players?|defenders?|forwards?|goalkeepers?|squad|line-?up|team photo|futbolcu\w*|oyuncu\w*|kadro\w*|"
                          r"kaleci\w*|fans?|supporters?|taraftar\w*|celebrat\w*|training session)\b")
TAKIM_TURLERI = {"Q476028", "Q847017", "Q12973014", "Q1194951", "Q20639856"}   # futbol kulübü, spor kulübü, spor takımı, ulusal futbol takımı, kadın futbol takımı


def wikidata_varlik(bag, ad, insan=True, ipucu="", turler=None):
    """Adı birebir tutan Wikidata kaydı. Kişi için P31=insan şartı; birden çok aday varsa açıklama ipucuyla
    tekil olarak ayrışmalı, ayrışmıyorsa None (yanlış kişiyi seçmemek için)."""
    anahtar = f"wd:v4:{int(insan)}:{kucult(ad)}:{kucult(ipucu)[:40]}" + (":takim" if turler else "")
    ob = bag.arsiv.get(anahtar)
    if isinstance(ob, dict) and ob.get("qid"):
        return ob
    if isinstance(ob, dict) and ob.get("_yok") and ob["_yok"] >= (simdi_tr() - timedelta(days=2)).strftime("%Y-%m-%d"):
        return None                       # "kayıt yok" sonucu yalnızca 2 gün geçerli
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
            if turler and not (set(p31) & turler):   # ör. "Kasımpaşa" takımı ≠ Kasımpaşa semti
                continue
            acik = " ".join(v.get("value", "") for v in (e.get("descriptions") or {}).values())
            deger = lambda p: [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in (e.get("claims", {}).get(p) or [])]
            adaylar.append({"qid": e["id"], "ad": ad, "aciklama": temiz(acik, 200), "p18": [x for x in deger("P18") if isinstance(x, str)],
                            "kategori": next((x for x in deger("P373") if isinstance(x, str)), ""),
                            "mekan": guncel_mekan(e.get("claims", {}).get("P115") or []), "p31": [x for x in p31 if x]})
        tek_kelime = insan and len(hedef.split()) < 2
        if len(adaylar) == 1 and not tek_kelime:
            sonuc = adaylar[0]
        elif adaylar and ipucu:
            # Tek kelimelik adlarda tek aday olsa bile açıklama rolle örtüşmeli (yanlış kişiyi seçmemek için).
            ip = {kok(w) for w in kelimeler(ipucu)}
            puan = sorted((len(ip & {kok(w) for w in kelimeler(a["aciklama"])}) for a in adaylar), reverse=True) + [0]
            en_iyi = max(adaylar, key=lambda a: len(ip & {kok(w) for w in kelimeler(a["aciklama"])}))
            if puan[0] > 0 and puan[0] > puan[1]:
                sonuc = en_iyi
    bag.arsiv[anahtar] = sonuc or {"_yok": simdi_tr().strftime("%Y-%m-%d")}
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
        liste = list(dict.fromkeys(dosyalar))[:260]
        if not liste:
            return []
        bag.arsiv[anahtar] = liste
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


ETKINLIK_EN = {"ziyaret": "visit visits visiting", "toplanti": "meeting", "konser": "concert", "mac": "match game", "toren": "ceremony",
               "acilis": "opening inauguration", "basin toplantisi": "press conference", "antrenman": "training", "aciklama": "statement",
               "konusma": "speech", "zirve": "summit", "kongre": "congress", "festival": "festival", "gorusme": "talks meeting"}


def kisi_adaylari(bag, konu, haber):
    """Haberdeki tanınmış kişinin doğrulanmış Commons kategorisinden adaylar: olay → benzer etkinlik → genel/portre."""
    adaylar, notlar = [], []
    hassas = konu.get("hassas", "yok") != "yok"
    if konu.get("hassas") == "çocuk":
        if konu.get("ana_kisiler"):
            notlar.append("çocuk içeren haber: kişi fotoğrafı aranmadı")
        return adaylar, notlar
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    anahtarlar = {kok(w) for w in kelimeler(" ".join(konu["etkinlik"]["anahtar_tr"] + konu["etkinlik"]["anahtar_en"] + [konu["etkinlik"]["tur"]]))}
    # Alt yazıda "önceki bir konserden" denebilmesi için fotoğrafın KENDİ kaydında etkinlik türü (ör. konser/concert) geçmeli.
    tur = konu["etkinlik"]["tur"]
    tur_kokleri = {kok(w) for w in kelimeler(" ".join([tur, ETKINLIK_EN.get(kucult(tur), "")]))}
    hedef_tarih = tarih_oku(konu.get("olay_tarihi")) or haber.get("tarih")
    yerler = {kok(w) for w in kelimeler(" ".join([konu.get("yer", ""), haber.get("ilce") or ""]))}
    baslik_k = set(kelimeler(haber.get("baslik", "") + " " + haber.get("spot", "")))
    oz = konu.get("ana_ozne") or {}
    for sira, kisi in enumerate(konu.get("ana_kisiler") or []):
        adi_basta = bool(set(kelimeler(kisi["ad"])) & baslik_k)
        ana_kisi = oz.get("tur") == "kisi" and bool(set(kelimeler(oz.get("ad", ""))) & set(kelimeler(kisi["ad"])))
        if not adi_basta and not ana_kisi:
            notlar.append(f"'{kisi['ad']}' başlıkta/spotta geçmiyor ve haberin ana öznesi değil; fotoğrafı kullanılmadı")
            continue
        if sira > 0 and not adi_basta:
            # İkinci kişi başlıkta yoksa haberin öznesi değildir; onun fotoğrafı haberi başka biri hakkındaymış gibi gösterir.
            notlar.append(f"'{kisi['ad']}' haberin asıl öznesi değil (başlıkta geçmiyor); fotoğrafı kullanılmadı")
            continue
        if hassas:
            # Hassas haberde yalnızca: başlık/spotta açıkça adı geçen, haberin ANA ÖZNESİ olan, kamuya mal olmuş kişi.
            ana = oz.get("tur") == "kisi" and bool(set(kelimeler(oz.get("ad", ""))) & set(kelimeler(kisi["ad"])))
            if sira > 0 or not adi_basta or not kisi.get("kamusal", True) or not ana:
                notlar.append(f"hassas haber: '{kisi['ad']}' haberin ana öznesi olan kamuya mal olmuş kişi değil; fotoğrafı aranmadı")
                continue
        try:
            v = wikidata_varlik(bag, kisi["ad"], insan=True, ipucu=kisi.get("rol", ""))
        except Exception as e:
            bag.arama_hatasi = True
            notlar.append(f"Wikidata sorgusu yapılamadı ({kisi['ad']}): {e}")
            continue
        if not v:
            notlar.append(f"'{kisi['ad']}' için kimliği kesin doğrulanan Wikidata kaydı yok (ad tutmadı ya da birden çok aynı adlı kişi var)")
            continue
        try:
            dosyalar = kategori_dosyalari(bag, v["kategori"]) if v["kategori"] else []
            p18 = _dosya_bilgisi(bag, ["File:" + x for x in v["p18"]])
        except Exception as e:
            bag.arama_hatasi = True
            notlar.append(f"Commons kategorisi okunamadı ({kisi['ad']}): {e}")
            continue
        kut = bag.arsiv.get("kut:" + v["qid"], [])
        gorulen = set()
        for d in p18 + [x for x in kut if isinstance(x, dict)] + dosyalar:
            if not d or d["url"] in gorulen or bag.yasakli_mi(d["url"]):
                continue
            gorulen.add(d["url"])
            resmi = d in p18
            ok, _ = commons_uygun(dict(d, genislik=max(800, d["genislik"], d["yukseklik"]) if resmi else max(d["genislik"], d["yukseklik"]), yukseklik=0))
            # dikey portrelere izin; Wikidata'daki resmî portre daha küçük olabilir (en az 380 px)
            if not ok or min(d["genislik"], d["yukseklik"]) < (380 if resmi else 400):
                continue
            metin = commons_metni(d)
            mk = {kok(w) for w in kelimeler(metin)}
            cekim = tarih_oku(d.get("tarih"))
            benzer = len(anahtarlar & mk)
            # Aynı olay: çekim tarihi + yer + kaynak açıklamasında olayın kendisi (etkinlik türü) birlikte doğrulanmalı.
            olay = bool(not hassas and cekim and hedef_tarih and abs((cekim - hedef_tarih).days) <= 3 and yerler & mk
                        and tur_kokleri & mk and benzer >= 2)
            if hassas:            # hassas haberde yalnızca genel/arşiv fotoğrafı; güncel olay ya da benzer etkinlik gibi sunulmaz
                benzer = 0
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
                "kucuk": kontrol_kucuk(d["url"], d["genislik"]), "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"],
                "lisans_dayanagi": d["lisans_url"] or d["sayfa"], "kosul": commons.get("kosul", ""), "hedef": kisi["ad"],
                "kisi": kisi["ad"], "qid": v["qid"], "cekim": str(cekim or ""), "meta_puan": puan, "dikey": d["yukseklik"] > d["genislik"],
                "portre": d in p18, "etkinlik_eslesti": bool(tur_kokleri & mk), "hassas_kip": hassas,
                "onayli": any(isinstance(x, dict) and x.get("url") == d["url"] for x in kut),
                "kayit": next((x for x in kut if isinstance(x, dict) and x.get("url") == d["url"]), d),
                "dogrulama": (f"'{kisi['ad']}' kimliği Wikidata {v['qid']} ({v['aciklama'][:60]}) ve bu kişiye ait Commons kategorisiyle doğrulandı"
                              + ("; olay tarihi ve yeriyle örtüşüyor" if olay else (f"; benzer etkinlik ({benzer} anahtar eşleşmesi)" if benzer else "; genel fotoğraf"))),
            })
    adaylar.sort(key=lambda a: -a["meta_puan"])
    secilen = adaylar[:5]
    # Wikidata'daki resmî portre (P18) her zaman adaylar arasında kalır: kalabalık/tören kareleri elenirse yedek olur.
    for a in adaylar[5:]:
        if a.get("portre") and not any(x.get("portre") and x.get("kisi") == a.get("kisi") for x in secilen):
            secilen = secilen[:4] + [a]
    return secilen, notlar


def mekan_gorselleri(bag, qidler, takim_qid=""):
    """Takımın BUGÜNKÜ iç saha stadının resmî görseli (P18). [(dosya, ad)]
    Wikidata'da takımın P115 kaydı eski kalabilir; bu yüzden takımı "kullanıcı" (P466) olarak gösteren statlar da toplanır,
    yıkılmış/kapanmış olanlar atlanır ve açılışı (P1619/P571) en yeni olan seçilir."""
    anahtar = "wd:v3:mekan:" + takim_qid + ":" + "|".join(qidler)
    if anahtar not in bag.arsiv:
        idler = list(qidler)
        if takim_qid:
            for x in (_wd_get({"action": "query", "list": "search", "srsearch": f"haswbstatement:P466={takim_qid}", "srlimit": 6})
                      .get("query", {}).get("search") or []):
                if x.get("title", "").startswith("Q") and x["title"] not in idler:
                    idler.append(x["title"])
        kayitlar = []
        if idler:
            ent = _wd_get({"action": "wbgetentities", "ids": "|".join(idler[:8]), "props": "claims|labels", "languages": "tr|en"}).get("entities", {})
            for e in ent.values():
                cl = e.get("claims", {})
                if cl.get("P576") or cl.get("P582"):          # yıkılmış / kapanmış
                    continue
                if takim_qid and not any(c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") == takim_qid
                                         and "P582" not in (c.get("qualifiers") or {}) for c in (cl.get("P466") or [])) \
                        and e.get("id") not in qidler:
                    continue
                acilis = ""
                for prop in ("P1619", "P571"):
                    for c in cl.get(prop) or []:
                        t = str(c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time", ""))
                        acilis = max(acilis, t.lstrip("+")[:10])
                kayitlar.append({"qid": e.get("id"), "acilis": acilis,
                                 "ad": ((e.get("labels") or {}).get("tr") or (e.get("labels") or {}).get("en") or {}).get("value", ""),
                                 "p18": [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in (cl.get("P18") or [])][:1]})
        kayitlar.sort(key=lambda m: m["acilis"], reverse=True)
        bag.arsiv[anahtar] = kayitlar[:1]
    sonuc = []
    for m in bag.arsiv[anahtar] or []:
        for d in _dosya_bilgisi(bag, ["File:" + x for x in m["p18"] if isinstance(x, str)]):
            sonuc.append((d, m["ad"]))
    return sonuc


def kurum_wikidata_adaylari(bag, konu):
    """Haberdeki kurum/takım/mekânın Wikidata görseli ve Commons kategorisi (ad birebir eşleşmeli)."""
    adaylar, notlar = [], []
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    for h in (konu.get("hedefler") or [])[:3]:
        try:
            tl = TAKIM_TURLERI if h.get("tur") == "takim" else None
            v = wikidata_varlik(bag, h["ad"], insan=False, ipucu="Antalya " + (konu.get("yer") or ""), turler=tl)
            if not v and h.get("ad_en") and h["ad_en"] != h["ad"]:
                v = wikidata_varlik(bag, h["ad_en"], insan=False, ipucu="Antalya", turler=tl)
        except Exception as e:
            bag.arama_hatasi = True
            notlar.append(f"Wikidata sorgusu yapılamadı ({h['ad']}): {e}")
            continue
        if not v:
            continue
        try:
            onayli = [x for x in bag.arsiv.get("kut:" + v["qid"], []) if isinstance(x, dict)]   # bu kuruma daha önce onaylananlar
            dosyalar = onayli + _dosya_bilgisi(bag, ["File:" + x for x in v["p18"]]) \
                + (kategori_dosyalari(bag, v["kategori"], alt_kategori=4)[:30] if v["kategori"] else [])
        except Exception as e:
            bag.arama_hatasi = True
            notlar.append(f"Commons okunamadı ({h['ad']}): {e}")
            continue
        try:
            stat = mekan_gorselleri(bag, v.get("mekan") or [], v["qid"] if h.get("tur") == "takim" else "")
        except Exception as e:
            stat = []
            notlar.append(f"Stat kaydı okunamadı ({h['ad']}): {e}")
        oz = konu.get("ana_ozne") or {}
        nerede = (konu.get("nerede") or {}).get("ad", "")
        ana_takim = kucult(oz.get("ad", "")) == kucult(h["ad"]) or ad_eslesir(h["ad"], oz.get("ad", ""))
        for d, mekan_ad in stat:
            # Stat yalnızca haberin ana öznesi olan takımın sahasıysa ya da olayın geçtiği yer bu statsa kullanılır:
            # rakip takımın stadı, maç orada oynanmıyorsa haberi başka bir yerde geçiyormuş gibi gösterir.
            if not ana_takim and not (nerede and (ad_eslesir(nerede, mekan_ad) or ad_eslesir(mekan_ad, nerede))):
                notlar.append(f"'{mekan_ad}' ({h['ad']} stadı) haberin ana takımına ait değil ve olay yeri olarak geçmiyor; kullanılmadı")
                continue
            ok, _ = commons_uygun(d)
            if ok and not bag.yasakli_mi(d["url"]):
                adaylar.append({
                    "tur": "arsiv", "alt_tur": "kurum", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]),
                    "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                    "kosul": commons.get("kosul", ""), "hedef": f"{mekan_ad} ({h['ad']} kulübünün iç saha stadı)", "qid": v["qid"],
                    "ana_ozne": bool(ana_takim), "hedef_tur": h.get("tur", ""),
                    "cekim": str(tarih_oku(d.get("tarih")) or ""), "kayit": d, "meta_puan": 58, "gosterim_ad": mekan_ad,
                    "dogrulama": f"'{h['ad']}' Wikidata {v['qid']} kaydındaki iç saha (P115) bilgisiyle doğrulandı: {mekan_ad}",
                })
        alinan, gorulen_url = 0, set()
        for d in dosyalar:
            if not d or d["url"] in gorulen_url:
                continue
            gorulen_url.add(d["url"])
            rakip = h.get("tur") == "takim" and not ana_takim and (oz.get("tur") == "takim"
                                                                   or re.search(r"(?i)stad|stadium|\bpark\b|arena", commons_metni(d)))
            if rakip and not (nerede and ad_eslesir(nerede, commons_metni(d))):
                continue      # haberin ana takımı başka bir takımsa rakibin kareleri/stadı kullanılmaz (maç orada oynanmıyorsa)
            if h.get("tur") == "takim" and OYUNCU_IPUCU.search(d["baslik"].replace("File:", "") + " " + d["aciklama"][:160]):
                continue      # oyuncu/kadro/maç karesi: başka dönemin kadrosu bugünkü haber sanılır
            ok, _ = commons_uygun(d)
            if not ok or bag.yasakli_mi(d["url"]):
                continue
            if h.get("tur") == "takim" and MAC_IPUCU.search(d["baslik"].replace("File:", "") + " " + d["aciklama"][:160]):
                continue
            cekim = tarih_oku(d.get("tarih"))
            adaylar.append({
                "tur": "arsiv", "alt_tur": "kurum", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]),
                "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                "kosul": commons.get("kosul", ""), "hedef": h["ad"], "qid": v["qid"], "cekim": str(cekim or ""), "kayit": d,
                "hedef_tur": h.get("tur", ""), "ana_ozne": bool(ana_takim),
                "meta_puan": 50 + (10 if alinan == 0 else 0) - (6 * int(d.get("kullanim", 0))), "onayli": "onay" in d,
                "dogrulama": f"'{h['ad']}' Wikidata {v['qid']} kaydı ve Commons kategorisiyle doğrulandı",
            })
            alinan += 1
            if alinan >= 3:
                break
    adaylar.sort(key=lambda a: -a["meta_puan"])
    return adaylar, notlar

# Haberin geçtiği ilçe/şehir: Wikidata kaydı ve Commons kategorisi (doğrulanmış eşleşme; ad benzerliğine güvenilmez)
YERLER = {
    "Antalya": ("Q6487", "Antalya"), "Akseki": ("Q421462", "Akseki"), "Aksu": ("Q421618", "Aksu, Antalya"),
    "Alanya": ("Q171810", "Alanya"), "Demre": ("Q34801304", "Demre (town)"), "Döşemealtı": ("Q1027553", "Döşemealtı"),
    "Elmalı": ("Q1013462", "Elmalı District"), "Finike": ("Q1012894", "Finike"), "Gazipaşa": ("Q844779", "Gazipaşa"),
    "Gündoğmuş": ("Q327508", "Gündoğmuş"), "İbradı": ("Q343180", "İbradı"), "Kaş": ("Q1004438", "Kaş (town)"),
    "Kemer": ("Q217047", "Kemer"), "Kepez": ("Q727065", "Kepez, Antalya"), "Konyaaltı": ("Q1783599", "Konyaaltı"),
    "Korkuteli": ("Q1004462", "Korkuteli"), "Kumluca": ("Q1004471", "Kumluca"), "Manavgat": ("Q81643", "Manavgat"),
    "Muratpaşa": ("Q1503514", "Muratpaşa"), "Serik": ("Q1012849", "Serik"),
}
GENEL_GORUNUM = re.compile(r"(?i)view|panorama|skyline|from|harbou?r|port\b|marina|beach|coast|bay\b|aerial|cliff|old.town|kaleiçi|"
                           r"görünüm|manzara|sahil|liman|plaj|genel|city|town|şehir|merkez|centre|center")
YER_ISTENMEYEN = re.compile(r"(?i)hotel|resort|otel|museum|müze|interior|inside|detail|sign|tabela|bus\b|car\b|accident|kaza|fire|yangın|"
                            r"protest|festival|concert|match|maç|wedding|portrait|people|crowd|menu|food|yemek|room|lobby|shop|store|"
                            r"airport|havaliman|tower|kule|terminal|stadium|stadyum|stadı|university|üniversite|hospital|hastane|"
                            r"school|okul|station|istasyon|tram|metro|plane|aircraft|uçak")
HASSAS_YERSIZ = {"ölüm", "çocuk"}


def haber_yeri(konu, haber):
    """Fotoğrafı gösterilecek ilçe/şehir: yalnızca haberin ANA ÖZNESİ o yerin kendisiyse (ör. "Kemer'de sahil sezonu").
    Haberde il/ilçe adı geçiyor diye herhangi bir şehir fotoğrafı kullanılmaz."""
    oz = konu.get("ana_ozne") or {}
    if oz.get("tur") != "yer":
        # Turizm haberinde (ör. "Alanya Avrupa pazarlarında yükselişini sürdürüyor") ilçenin genel görünümü konuyu doğrudan anlatır.
        if haber.get("kategori") == "Turizm" and konu.get("hassas", "yok") == "yok":
            for aday in ((konu.get("nerede") or {}).get("ad", ""), haber.get("ilce") or ""):
                for ad in YERLER:
                    if aday and kucult(ad) == kucult(aday):
                        return ad
        return ""
    ad_k = kucult(oz.get("ad", ""))
    for ad in YERLER:
        if kucult(ad) == ad_k or ad_k in (kucult(ad) + " ilcesi", kucult(ad) + " sehri", kucult(ad) + " merkezi"):
            return ad
    return ""


def yer_adaylari(bag, konu, haber):
    """Son fotoğraf katmanı: haberin geçtiği ilçenin/şehrin genel görünümü (arşiv). Kişi/kurum/olay fotoğrafı yoksa kullanılır."""
    adaylar, notlar = [], []
    if konu.get("hassas", "yok") in HASSAS_YERSIZ:
        return adaylar, notlar
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    ad = haber_yeri(konu, haber)
    if not ad:
        return adaylar, notlar
    qid, kategori = YERLER[ad]
    try:
        anahtar = "wd:v3:yer:" + qid
        if anahtar not in bag.arsiv:
            ent = _wd_get({"action": "wbgetentities", "ids": qid, "props": "claims"}).get("entities", {}).get(qid, {})
            bag.arsiv[anahtar] = [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in (ent.get("claims", {}).get("P18") or [])][:2]
        p18 = _dosya_bilgisi(bag, ["File:" + x for x in bag.arsiv[anahtar] if isinstance(x, str)])
        dosyalar = p18 + kategori_dosyalari(bag, kategori, alt_kategori=3)
    except Exception as e:
        notlar.append(f"'{ad}' yer fotoğrafları okunamadı: {e}")
        return adaylar, notlar
    kut = [x for x in bag.arsiv.get("kut:" + qid, []) if isinstance(x, dict)]
    gorulen, puanli = set(), []
    for d in kut + dosyalar:
        if not d or d["url"] in gorulen or bag.yasakli_mi(d["url"]):
            continue
        gorulen.add(d["url"])
        metin = d["baslik"] + " " + d["aciklama"][:160]
        # Yalnızca yerin GENEL görünümü: belirli tesis/bina kareleri (havalimanı, stat, hastane…) başka bir yer sanılır.
        if YER_ISTENMEYEN.search(metin) or not (d in p18 or GENEL_GORUNUM.search(metin)) or not commons_uygun(d)[0]:
            continue
        cekim = tarih_oku(d.get("tarih"))
        kullanim = next((x.get("kullanim", 1) for x in kut if x.get("url") == d["url"]), 0)
        # Aynı ilçe fotoğrafı ana sayfada tekrar tekrar görünmesin: kullanıldıkça sırası düşer.
        puan = (20 if d in p18 else 0) + (12 if kullanim else 0) + (8 if GENEL_GORUNUM.search(metin) else 0) \
            + (min(10, max(0, cekim.year - 2010)) if cekim else 0) - 18 * kullanim
        puanli.append((puan, d, cekim))
    puanli.sort(key=lambda x: -x[0])
    for puan, d, cekim in puanli[:3]:
        adaylar.append({
            "tur": "arsiv", "alt_tur": "yer", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]),
            "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
            "kosul": commons.get("kosul", ""), "hedef": f"{ad} ({'şehrin' if ad == 'Antalya' else 'ilçenin'} genel görünümü)",
            "gosterim_ad": ad, "qid": qid, "cekim": str(cekim or ""), "kayit": d, "meta_puan": puan,
            "dogrulama": f"'{ad}' Wikidata {qid} kaydı ve Commons kategorisi ({kategori}) ile doğrulandı; haberde geçen yer",
        })
    if not adaylar:
        notlar.append(f"'{ad}' için uygun genel görünüm fotoğrafı bulunamadı")
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
            if h.get("tur") == "takim":
                # Takım adları çoğu zaman semt/şehir adıyla aynıdır (Kasımpaşa, Kepez): serbest metin araması yapılmaz,
                # yalnızca Wikidata'da spor kulübü olarak doğrulanan kaydın görselleri kullanılır.
                continue
            if h.get("tur") == "olay":
                # Olay adıyla serbest arşiv araması yapılmaz: "iklim konferansı" gibi bir adla bulunan BAŞKA bir etkinliğin
                # konuşmacısı/salonu haberdeki olayın görüntüsü sanılır. Olay fotoğrafı yalnızca tarih+yer doğrulamalı katmanda aranır.
                notlar.append(f"'{h['ad']}' bir olay: başka bir etkinliğin arşiv fotoğrafı kullanılmaz (yalnızca aynı olay aranır)")
                continue
            for x in bag.arsiv.get("kut:ad:" + kucult(h["ad"]), []):     # bu ada daha önce onaylanmış arşiv fotoğrafları
                if isinstance(x, dict) and not bag.yasakli_mi(x.get("url", "")) and not any(a["url"] == x.get("url") for a in adaylar):
                    adaylar.append({
                        "tur": "arsiv", "kaynak": "Wikimedia Commons", "url": x["url"], "kucuk": kontrol_kucuk(x["url"], x.get("genislik", 0)),
                        "sayfa": x.get("sayfa", ""), "fotografci": x.get("yazar", ""), "lisans": x.get("lisans", ""),
                        "lisans_dayanagi": x.get("lisans_url") or x.get("sayfa", ""), "kosul": commons.get("kosul", ""), "hedef": h["ad"],
                        "cekim": str(tarih_oku(x.get("tarih")) or ""), "kayit": x, "onayli": True,
                        "meta_puan": 30 - 6 * int(x.get("kullanim", 1)),
                        "dogrulama": f"'{h['ad']}' için daha önce onaylanmış arşiv fotoğrafı ({x.get('onay', '')}); lisans dosya sayfasından"})
            sorgular = list(dict.fromkeys([h["ad_en"], h["ad"]]))
            bulunan = 0
            for q in sorgular:
                if not bag.sorgu_hakki():
                    break
                try:
                    dosyalar = commons_dosyalar(bag, q)
                except Exception as e:
                    bag.arama_hatasi = True
                    notlar.append(f"Commons araması yapılamadı ({q}): {e}")
                    continue
                for d in dosyalar:
                    metin = commons_metni(d)
                    if not (ad_eslesir(h["ad"], metin) or ad_eslesir(h["ad_en"], metin)) or not commons_uygun(d)[0]:
                        continue
                    if bag.yasakli_mi(d["url"]) or any(a["url"] == d["url"] for a in adaylar):
                        continue
                    cekim = tarih_oku(d.get("tarih"))
                    adaylar.append({
                        "tur": "arsiv", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]), "sayfa": d["sayfa"],
                        "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                        "kosul": commons.get("kosul", ""), "hedef": h["ad"], "cekim": str(cekim or ""), "kayit": d,
                        "meta_puan": 20 + min(10, max(0, (cekim.year - 2012))) if cekim else 20,
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
- konu_iliskisi (0-10): Fotoğraf, haberin KONUSUYLA ("{ne}") doğrudan ilişkili olanı mı gösteriyor? Yer/tesis/temsilî fotoğraflarda
  aynı yerde bulunan ama konuyla ilgisiz unsur (şirket tabelası, otopark, rastgele bina, kule) düşük puan alır; konuyla ilgili alan
  (yakıt sızıntısı haberinde apron/uçak operasyonu, otel doluluğu haberinde oteller/otel bölgesi) yüksek puan alır.
- yabanci_unsur: Türkiye dışına ait belirgin unsur var mı? (yabancı dilde tabela, yabancı plaka, "POLICE"/"911" gibi yabancı polis/ambulans işaretleri, bayrak)
- taninabilir_kisi: Yüzü YAKIN ve net biçimde, kimliği öne çıkacak şekilde görünen kişi var mı? (uzaktan çekilmiş kalabalık,
  yandan/arkadan görünen ya da hareket hâlindeki, yüzü belirgin olmayan sporcular false)
- etkinlik_benzerligi (0-10): Fotoğraftaki sahne türü, haberdeki etkinlik türüne ("{etkinlik}") ne kadar benziyor?
- tek_kisi_odakli: Fotoğrafta iddia edilen kişi tek başına ya da açıkça ön planda mı? (kalabalık zirve/toplu fotoğrafta false)
- gerekli_unsur_var: Fotoğrafta "GÖRSELDE OLMALI" listesindeki unsur(lar) gerçekten görülüyor mu? (liste boşsa true)
- yasak_unsur_var: Fotoğrafta "GÖRSELDE OLMAMALI" listesindeki bir unsur görülüyor mu? (ör. muaythai haberinde boks/karate
  müsabakası, LPG haberinde benzin tabancası, ölüm haberinde ceset/kan, yabancı polis aracı)
- gerekce: En fazla 20 kelimelik Türkçe açıklama.
ARŞİV KURALI: "arşiv fotoğrafı" iddiası olan kareler haberde fotoğrafın altında arşiv olduğu ve çekim yılı belirtilerek
(ör. "X, önceki bir ziyaretten — Arşiv fotoğrafı (2023)") yayımlanır. Fotoğrafın geçmiş bir tarihe ait olması TEK BAŞINA yanıltıcı ya da habere uygunsuz sayılmaz. Kişi iddialarında kişiyi
nötr biçimde gösteren portre, kürsü, görüşme ya da etkinlik kareleri habere uygundur; yalnızca karedeki pankart, yazı, başka öne çıkan
kişiler ya da belirgin başka bir olay güncel haber hakkında yanlış izlenim yaratıyorsa yanıltıcıdır. Takım/mekân iddialarında
stadın, tesisin ya da binanın genel görünümü uygundur; haberdekinden farklı belirli bir rakip/maç öne çıkıyorsa yanıltıcıdır.
İlçe/şehir ("genel görünümü") iddialarında: o yerin nötr genel görünümü (şehir, sahil, liman, meydan, manzara) habere uygundur.
Karede belirgin bir kişi, belirli bir otel/işletme tabelası ya da başka bir olay (kaza, yangın, tören, gösteri) öne çıkıyorsa
ya da haberin tonuyla açıkça çelişiyorsa (ör. ölüm/kaza haberi için şenlikli bir kare) yanıltıcıdır.
HASSAS HABER (dava, soruşturma, sağlık vb.) kişi iddialarında: kişinin nötr portresi ya da genel arşiv karesi uygundur ve
"Arşiv" olarak sunulur; kelepçe, adliye, gözaltı, hastane gibi güncel olay izlenimi veren ya da kişiyi küçük düşüren kareler yanıltıcıdır.
"temsilî fotoğraf" iddialarında: fotoğraf haberin konusunu doğrudan göstermeli (ör. akaryakıt haberinde pompa, muaythai haberinde
muaythai müsabakası); benzer ama farklı bir şey (boks, karate; LPG yerine benzin) habere uygun değildir. Karede belirli bir organizasyonun/kurumun
pankartı, afişi, logosu, tabelası ya da öne çıkan tanınabilir yüzler varsa haberdeki olayın kendisi sanılabilir → yaniltici: true.
Yabancı tabela, plaka, para birimi, polis/ambulans aracı ya da tanınır bir yabancı şehir/simge yapı varsa yabanci_unsur: true.
Doğal manzara, tarla, mera, deniz, ürün, hayvan ya da iç mekân gibi ülkesi belli olmayan sahneler tek başına yabancı unsur sayılmaz.
ÖNEMLİ: Kişileri yüzlerinden tanımaya ÇALIŞMA ve kimlik hakkında yorum yapma; kişinin kimliği arşiv kaydıyla (Wikidata ve
Commons kategorisi) doğrulanmıştır. Yalnızca sahneyi değerlendir: başka bir etkinliğe ait pankart, yazı, tarih, bayrak veya
öne çıkan başka kişiler bu fotoğrafı güncel haberdeki etkinliğin görüntüsüymüş gibi gösterecekse "yaniltici": true.

HABER BAŞLIĞI: {baslik}
SPOT: {spot}
ANA OLAY: {olay}
KİŞİLER: {kisiler} | KURUMLAR: {kurumlar} | YER: {yer} | HASSASİYET: {hassas}
GÖRSELDE OLMALI: {olmali} | GÖRSELDE OLMAMALI: {olmamali}

{iddialar}

Yalnızca şu biçimde JSON döndür:
{{"degerlendirmeler": [{{"no": 1, "fotograf_mi": false, "iddiayla_tutarli": false, "habere_uygun": false, "yaniltici": false,
 "yabanci_unsur": false, "taninabilir_kisi": false, "etkinlik_benzerligi": 0, "tek_kisi_odakli": false, "konu_iliskisi": 0,
 "gerekli_unsur_var": true, "yasak_unsur_var": false, "gerekce": "..."}}]}}"""


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


YABANCI_SOMUT = re.compile(r"(?i)tabela|yazı|plaka|polis|ambulans|bayrak|para birimi|sign|plate|flag|police|landmark|simge|şehir")


def gerekli_unsurlar(konu):
    """'Görselde olmalı' listesinden kişi adları çıkarılır: temsilî ya da yer fotoğrafında haberdeki kişinin kendisi bulunamaz
    (ör. satranç haberinde "Elif Cemre Uysal" şartı her satranç fotoğrafını eler)."""
    adlar = [k.get("ad", "") for k in konu.get("ana_kisiler") or [] if isinstance(k, dict)] + list(konu.get("kisiler") or [])
    oz = konu.get("ana_ozne") or {}
    if oz.get("tur") == "kisi":
        adlar.append(oz.get("ad", ""))
    kisi_k = {w for ad in adlar for w in kelimeler(ad) if len(w) >= 3}
    return [u for u in konu.get("gorselde_olmali") or [] if not (set(kelimeler(u)) & kisi_k)]


def eski_red_yeniden(a, ob, konu=None):
    """Önbellekteki red kararı bugünkü kurallara göre geçersiz mi? 'kabul' → yalnızca konu ilişkisi yüzünden elenmiş, ana öznenin
    kimliği doğrulanmış kendi fotoğrafı; 'tekrar' → yalnızca 'Türkiye dışına ait unsur' (somut tabela/plaka/bayrak/simge
    belirtilmeden) yüzünden elenmiş kare yeni kuralla bir kez daha görüntü kontrolüne gönderilir; '' → red geçerli."""
    if not ob or ob.get("karar") != "red":
        return ""
    g = ob.get("gerekce", "")
    ned = [n.strip() for n in g.split(" — ")[0].split(";") if n.strip()]
    if not ned:
        return ""
    if a.get("alt_tur") == "kurum" and a.get("ana_ozne") and all(
            n.startswith(("haberin konusuyla doğrudan ilişkili değil", "haberin gerektirdiği unsur görünmüyor")) for n in ned):
        return "kabul"
    aciklama = g.split(" — ", 1)[1] if " — " in g else ""
    if (all(n.startswith("haberin gerektirdiği unsur görünmüyor") for n in ned) and konu \
            and len(gerekli_unsurlar(konu)) < len(konu.get("gorselde_olmali") or [])
            and not ob.get("yeniden_bakildi")):
        return "tekrar"               # şart listesinde kişi adı vardı (artık çıkarılıyor): yeniden bakılır
    if (all(n.startswith("Türkiye dışına ait unsur var") for n in ned) and not YABANCI_SOMUT.search(aciklama)
            and not ob.get("yeniden_bakildi")):
        return "tekrar"
    return ""


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
    # Haberin ANA ÖZNESİ olan kurumun/takımın/tesisin kimliği doğrulanmış kendi fotoğrafı (ör. Antalyaspor haberinde kendi stadı)
    # aynı olayda çekilmiş olmak zorunda değildir: konu ilişkisi eşiği uygulanmaz (yanıltıcılık ve tutarlılık yine aranır).
    ana_ozne_kurum = d.get("_alt_tur") == "kurum" and d.get("_ana_ozne")
    if d.get("_alt_tur") in KONU_ILISKISI_SART and not ana_ozne_kurum and int(d.get("konu_iliskisi") or 0) < KONU_ILISKISI_ESIK:
        nedenler.append(f"haberin konusuyla doğrudan ilişkili değil (konu ilişkisi {int(d.get('konu_iliskisi') or 0)}/10)")
    if d.get("yasak_unsur_var") is True:
        nedenler.append("görselde olmaması gereken unsur var (" + ", ".join((konu.get("gorselde_olmamali") or [])[:3]) + ")")
    if (gerekli_unsurlar(konu) and d.get("_alt_tur") in KONU_ILISKISI_SART and not ana_ozne_kurum
            and d.get("gerekli_unsur_var") is False):
        nedenler.append("haberin gerektirdiği unsur görünmüyor (" + ", ".join(gerekli_unsurlar(konu)[:2]) + ")")
    if d.get("_alt_tur") == "nesne" and d.get("taninabilir_kisi"):
        nedenler.append("temsilî fotoğrafta tanınabilir kişi var")
    if d.get("_hedef_tur") == "takim" and d.get("taninabilir_kisi"):
        nedenler.append("takım arşiv karesinde tanınabilir oyuncular var (başka dönemin kadrosu/maçı bugünkü haber sanılır)")
    kurum_h = d.get("_alt_tur") == "kurum_haber"   # kurumun aynı habere ait kendi fotoğrafı: karedeki kişiler olayın kendisidir
    if str(d.get("_alt_tur", "")).startswith("kisi") and not d.get("tek_kisi_odakli"):
        nedenler.append("kişi tek başına ya da açıkça ön planda değil (karedeki başka kişiler/tören yanlış izlenim yaratabilir)")
    if d.get("yabanci_unsur") and not str(d.get("_alt_tur", "")).startswith("kisi") and not kurum_h:
        nedenler.append("Türkiye dışına ait unsur var")
    if d.get("taninabilir_kisi") and not str(d.get("_alt_tur", "")).startswith("kisi") and not kurum_h and (konu.get("hassas", "yok") != "yok" or konu.get("kisiler")):
        nedenler.append("tanınabilir kişi (hassas haber / adı geçen kişi sanılabilir)")
    return ("kabul" if not nedenler else "red"), nedenler


def gorsel_kontrol(bag, haber, konu, konu_imza, adaylar):
    """Adayları görüntüleriyle birlikte tek istekte inceler. Önbellekte kararı olanlar yeniden gönderilmez.
    Kontrol yapılamazsa KotaHatasi/ServisHatasi yükselir (çağıran fotoğrafsız kapağa geçer)."""
    gonderilecek, parcalar, iddialar = [], [], []
    for a in adaylar:
        ob = bag.onbellek_al(imza(KURAL_SURUMU, konu_imza, a["url"]))
        if ob and eski_red_yeniden(a, ob, konu) == "tekrar":
            ob = None                     # kural değişti: bu kare bir kez daha görüntü kontrolüne gönderilir
            a["yeniden_bakildi"] = True
        if ob:
            a.update(karar=ob["karar"], gerekce=ob.get("gerekce", ""), kontrol_tarih=ob.get("t", ""), onbellekten=True,
                     benzerlik=ob.get("benzerlik", 0), tek=ob.get("tek", False), iliski=ob.get("iliski", 0))
            if a["karar"] == "red" and a.get("alt_tur") == "kurum" and a.get("ana_ozne"):
                # Ana özne kurumun/takımın kendi fotoğrafı yalnızca "konu ilişkisi" yüzünden elenmişse (kural değişti) kabul edilir.
                ned = [n.strip() for n in a["gerekce"].split(" — ")[0].split(";") if n.strip()]
                if ned and all(n.startswith(("haberin konusuyla doğrudan ilişkili değil", "haberin gerektirdiği unsur görünmüyor")) for n in ned):
                    a.update(karar="kabul", gerekce="ana öznenin kimliği doğrulanmış kendi fotoğrafı (aynı olay şartı yok) — "
                                                    + (a["gerekce"].split(" — ", 1)[1] if " — " in a["gerekce"] else ""))
            if (a["karar"] == "kabul" and a.get("alt_tur") in KONU_ILISKISI_SART and not a.get("ana_ozne")
                    and int(a.get("iliski") or 0) < KONU_ILISKISI_ESIK):   # eşik sonradan yükseldiyse eski kabul geçersiz
                a.update(karar="red", gerekce=f"haberin konusuyla doğrudan ilişkili değil (konu ilişkisi {a['iliski']}/10, "
                                              f"eşik {KONU_ILISKISI_ESIK}) — {a['gerekce']}")
            continue
        try:
            tip, veri = gorsel_indir(a.get("kucuk") or a["url"])
        except Exception as e:
            a.update(karar="red", gerekce=f"görsel indirilemedi: {str(e)[:80]}", kontrol_tarih="")
            continue
        gonderilecek.append(a)
        n = len(gonderilecek)
        kayit = a.get("kayit") or {}
        on_ek = {"gercek": "aynı olayın fotoğrafı: ", "stok": "temsilî fotoğraf: "}.get(
            "stok" if a.get("alt_tur") == "nesne" else a["tur"], "arşiv fotoğrafı: ")
        iddialar.append(f"FOTOĞRAF {n} İDDİA: {on_ek}"
                        f"{a.get('hedef', '')}" + (f" (çekim {a['cekim']})" if a.get("cekim") else "")
                        + (f" | arşiv açıklaması: {temiz(kayit.get('baslik', '').replace('File:', ''), 90)} — {temiz(kayit.get('aciklama'), 140)}" if kayit else ""))
        parcalar += [{"text": f"FOTOĞRAF {n}:"}, {"inlineData": {"mimeType": tip, "data": veri}}]
    if not gonderilecek:
        return adaylar
    metin = KONTROL_TALIMAT.format(
        baslik=haber.get("baslik", ""), spot=haber.get("spot", ""), olay=konu.get("ana_olay", ""),
        kisiler=", ".join(konu.get("kisiler") or []) or "-", kurumlar=", ".join(konu.get("kurumlar") or []) or "-",
        yer=konu.get("yer") or "-", hassas=konu.get("hassas", "yok"), iddialar="\n".join(iddialar),
        etkinlik=(konu.get("etkinlik") or {}).get("tur") or konu.get("ana_olay", ""),
        ne=(konu.get("ne") or {}).get("ad") or konu.get("ana_olay", ""),
        olmali=", ".join(gerekli_unsurlar(konu)) or "-", olmamali=", ".join(konu.get("gorselde_olmamali") or []) or "-")
    yanit = bag.gemini([{"text": metin}] + parcalar, en_fazla=3500)
    liste = {int(d.get("no", 0)): d for d in (yanit.get("degerlendirmeler") or []) if isinstance(d, dict)}
    tarih = simdi_tr().isoformat(timespec="seconds")
    for i, a in enumerate(gonderilecek, 1):
        d = liste.get(i)
        if not d:
            a.update(karar="red", gerekce="değerlendirme yanıtında yok", kontrol_tarih=tarih)
            continue
        karar, nedenler = karar_ver(dict(d, _alt_tur=a.get("alt_tur", ""), _hedef_tur=a.get("hedef_tur", ""),
                                         _ana_ozne=bool(a.get("ana_ozne"))), konu)
        gerekce = temiz(d.get("gerekce"), 200)
        if nedenler:
            gerekce = "; ".join(nedenler) + (f" — {gerekce}" if gerekce else "")
        benz = int(d.get("etkinlik_benzerligi") or 0)
        tek = bool(d.get("tek_kisi_odakli"))
        ilis = int(d.get("konu_iliskisi") or 0)
        a.update(karar=karar, gerekce=gerekce, kontrol_tarih=tarih, benzerlik=benz, tek=tek, iliski=ilis,
                 bulgular={k: d.get(k) for k in ("fotograf_mi", "iddiayla_tutarli", "habere_uygun", "yaniltici", "yabanci_unsur",
                                                  "taninabilir_kisi", "etkinlik_benzerligi", "tek_kisi_odakli", "konu_iliskisi",
                                                  "gerekli_unsur_var", "yasak_unsur_var")})
        bag.onbellek_koy(imza(KURAL_SURUMU, konu_imza, a["url"]), {"karar": karar, "gerekce": gerekce, "t": tarih, "benzerlik": benz, "tek": tek,
                                                                 "iliski": ilis, "yeniden_bakildi": bool(a.get("yeniden_bakildi"))})
    return adaylar


# ----------------------------------------------------------------- seçim
# Kapak kalan haberler, aday kuralları genişletildiğinde (tüm kararlar sıfırlanmadan) bir kez yeniden denenir.
# c2: kurum haberinde konu fotoğrafı (kurumun binası/kadrosu değil, konunun kendisi) ve ilçe/şehir haberinde konu fotoğrafı.
# c3: turizm haberinde ilçenin genel görünümü; ülkesi belli olmayan doğal sahne/ürün kareleri "yabancı unsur" sayılmaz.
# c4: yer+konu aramasında "Antalya" gibi geniş yer adı da tutar; ana öznenin kendi fotoğrafı ve "yabancı sahne" redleri yeniden
#     değerlendirilir; hassas olmayan kişi haberinde konuyu anlatan temsilî fotoğraf.
# c5: "görselde olmalı" şartından kişi adları çıkarılır (temsilî fotoğrafta haberdeki kişi bulunamaz).
KAPAK_SURUM = "c5"


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
    if alt == "kisi_benzer" and a.get("etkinlik_eslesti", True):
        etk = (konu.get("etkinlik") or {}).get("tur") or "etkinlik"
        return f"{a['kisi']}, önceki bir {_ek_den(etk)} — Arşiv{ek}"
    if alt in ("kisi_genel", "kisi_benzer"):
        return f"{a['kisi']} — Arşiv fotoğrafı{ek}"
    if a["tur"] == "arsiv":
        return f"{a.get('gosterim_ad') or a.get('hedef', '')} — Arşiv fotoğrafı{ek}"
    return ""


def aday_secimi(a, kontrol, konu_imza, konu=None):
    kredi = (a["kaynak"] if a.get("fotografci") == a["kaynak"] else f"{a['fotografci']} / {a['kaynak']}") \
        + (f" ({a['lisans']})" if a["kaynak"] == "Wikimedia Commons" else "")
    yil = (a.get("cekim") or "")[:4] if a["tur"] == "arsiv" else ""
    return {
        "tur": a["tur"], "gorsel": a["url"], "gorsel_kredi": ("Arşiv fotoğrafı: " if a["tur"] == "arsiv" else "Fotoğraf: ") + kredi,
        "kaynak_sayfa": a.get("sayfa", ""), "fotografci": a.get("fotografci", ""), "kredi": kredi, "lisans": a.get("lisans", ""),
        "lisans_dayanagi": a.get("lisans_dayanagi", ""), "kosul": a.get("kosul", ""), "gosterilen": a.get("gosterim_ad") or a.get("hedef", ""),
        "cekim_yili": yil, "gerekce": f"{a.get('dogrulama', '')}; görsel kontrol: {a.get('gerekce', '')}",
        "kontrol": kontrol, "surum": KURAL_SURUMU, "konu_imza": konu_imza,
        "aciklama": alt_yazi(a, konu or {}) if a["tur"] != "stok" else "Temsilî fotoğraf",
        "izin_kontrolu": a.get("izin_kontrolu") or {},
        "alt_tur": a.get("alt_tur", ""), "qid": a.get("qid", ""),
        "kaynak_aciklama": temiz(((a.get("kayit") or {}).get("aciklama") or ""), 160),
        "konum": "50% 22%" if a.get("dikey") else "",
    }


KURUM_HASSAS = {"suç", "çocuk", "ölüm"}
KONU_ILISKISI_ESIK = 7
KONU_ILISKISI_SART = {"kurum", "yer", "yer_konu", "nesne"}   # aynı yerde/kurumda olmak yetmez; konuyla doğrudan ilişki gerekir
NESNE_HASSAS = {"suç", "kaza", "ölüm", "afet", "çocuk"}   # bu haberlerde temsilî fotoğraf olayın görüntüsü sanılır
UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
LISANS_RED = []       # bir seçim turunda lisans nedeniyle elenen dosyalar (kapak nedeni için)


def unsplash_ara(bag, sorgu, adet=6):
    anahtar = "c6:us:" + sorgu.lower()
    if bag.arsiv.get(anahtar):
        return bag.arsiv[anahtar]
    r = requests.get("https://api.unsplash.com/search/photos", headers={"Authorization": f"Client-ID {UNSPLASH_KEY}", "Accept-Version": "v1"},
                     params={"query": sorgu, "per_page": adet, "orientation": "landscape", "content_filter": "high"}, timeout=20)
    r.raise_for_status()
    sonuc = [{"url": f["urls"]["regular"], "kucuk": f["urls"]["small"], "sayfa": f["links"]["html"],
              "indirme": f["links"].get("download_location", ""), "yazar": (f.get("user") or {}).get("name", "Unsplash"),
              "aciklama": temiz(f.get("description") or f.get("alt_description") or "", 160)}
             for f in r.json().get("results") or [] if f.get("urls")]
    if sonuc:
        bag.arsiv[anahtar] = sonuc
    return sonuc


# İnsan odaklı etkinlikler: genel bir "konferansta konuşan biri" fotoğrafı haberdeki etkinliğin ya da konuşmacının kendisi sanılır.
TOPLANTI = re.compile(r"(?i)\b(konferans\w*|conference|toplantı\w*|meeting|zirve\w*|summit|sempozyum\w*|symposium|kongre\w*|congress|panel\w*|"
                      r"forum\w*|tören\w*|ceremony|basın|press|açıklama\w*|presentation|sunum\w*|semin\w*|workshop|çalıştay\w*|ziyaret\w*|"
                      r"visit\w*|görüşme\w*|lecture|speech|konuşma\w*)\b")
# Manzara/yer türü konular: rastgele bir otel/sahil fotoğrafı başka bir yeri haberdeki yer gibi gösterir; yalnızca yer adı doğrulanmış arşiv.
MANZARA = re.compile(r"(?i)\b(hotels?|otel\w*|resorts?|beach(es)?|plaj\w*|sahil\w*|coast\w*|city|şehir\w*|skyline|panorama|harbou?r|liman\w*|marina|old town|kaleiçi)\b")


# Kurumun KENDİSİNİ (binası, kampüsü, kadrosu, kulübü) anlatan konu sorguları: rastgele bir fotoğraf o kurum sanılır.
KURUM_YAPI = re.compile(r"(?i)\b(building|headquarters?|office|offices|campus|universit\w*|school|hospital|club|staff|employees?|"
                        r"team|facility|facilities|stadium|town hall|city hall|municipality|ministry|directorate|bina\w*|kampüs\w*)\b")


def konu_gorseli(konu):
    """Konu fotoğrafı ayarı. Genel konu fotoğrafı yalnızca haberin öznesi genel bir nesne/olgu ya da (insan odaklı olmayan) bir etkinlik
    türüyse kullanılır: belirli bir kişi, kurum, takım ya da yer haberinde rastgele bir fotoğraf o kişi/kurum/tesis/yer sanılır."""
    kg = dict(konu.get("konu_gorseli") or {})
    oz = konu.get("ana_ozne") or {}
    if not kg.get("sorgular_en") and oz.get("tur") == "nesne" and oz.get("ad_en"):
        kg.update(uygun=True, sorgular_en=[oz["ad_en"]] + (konu.get("alternatif_sorgular_en") or [])[:2])
    if konu.get("hassas", "yok") != "yok" or oz.get("tur") == "takim":
        kg["uygun"] = False
    elif oz.get("tur") == "kisi":
        # Kişi haberinde temsilî fotoğraf yalnızca konuyu (satranç, sahne, ürün) gösterir; görüntü kontrolünde tanınabilir kişi
        # bulunan kare elenir (fotoğraftaki kişi haberin kişisi sanılmasın). Kişinin doğrulanmış portresi her zaman önce gelir.
        kg["sorgular_en"] = [q for q in kg.get("sorgular_en") or [] if not INSAN_IPUCU.search(q)]
        kg["sorgular_tr"] = [q for q in kg.get("sorgular_tr") or [] if not INSAN_IPUCU.search(q)]
    elif oz.get("tur") == "kurum":
        # Kurum haberinde konu fotoğrafı yalnızca KONUYU (organ bağışı, doğal taş, bilim projesi) anlatan sorgularla aranır;
        # kurumun binası/kadrosu/kulübü gibi sorgular çıkarılır. Görüntü kontrolünde kurumun kendisi sanılabilecek kare elenir.
        kg["sorgular_en"] = [q for q in kg.get("sorgular_en") or [] if not KURUM_YAPI.search(q)]
        kg["sorgular_tr"] = [q for q in kg.get("sorgular_tr") or [] if not KURUM_YAPI.search(q)]
    elif oz.get("tur") == "yer" and not (oz.get("ad") in YERLER and not TESIS_TURU.search(oz.get("ad", ""))):
        kg["uygun"] = False      # belirli bir tesis/mekân haberinde (ör. sosyal tesis) başka bir mekânın karesi o tesis sanılır
    etkinlik = " ".join([(konu.get("etkinlik") or {}).get("tur", ""), (konu.get("ne") or {}).get("ad", ""), oz.get("ad", "")])
    if oz.get("tur") == "olay" and TOPLANTI.search(etkinlik):
        kg["uygun"] = False
    kg["sorgular_en"] = [q for q in kg.get("sorgular_en") or [] if not TOPLANTI.search(q)]
    kg["sorgular_tr"] = [q for q in kg.get("sorgular_tr") or [] if not TOPLANTI.search(q)]
    return kg


def konu_sorgulari(konu, alternatif=False):
    """Konu fotoğrafı aramaları: önce konuyu somut anlatan sorgular, ikinci turda alternatifler (tekrarsız)."""
    kg = konu_gorseli(konu)
    ana = kg.get("sorgular_en") or []
    alt = (kg.get("sorgular_tr") or []) + [q for q in (konu.get("alternatif_sorgular_en") or []) if not TOPLANTI.search(q)]
    liste = alt if alternatif else ana[:2]
    if alternatif:
        oz = konu.get("ana_ozne") or {}
        liste = ana[2:] + liste + [x for x in ((konu.get("ne") or {}).get("ad_en", ""), oz.get("ad_en", "")) if x and not TOPLANTI.search(x)]
    return [q for q in dict.fromkeys(liste) if ayirt_edici(q)]


ESANLAM = {"gasoline": {"gas", "petrol", "fuel"}, "petrol": {"gas", "gasoline", "fuel"}, "fuel": {"gas", "gasoline", "petrol"},
           "gas": {"autogas", "gasoline", "petrol", "fuel"}, "muaythai": {"muay"}, "lpg": {"autogas", "propane"}, "autogas": {"lpg"}}


STOK_GENEL = set("fight fighting match game championship competition tournament pump station sign price photo picture image ring people "
                 "event events festival workshop activity activities kids children child "
                 "men women man woman training facility field building street view close up closeup detail scene the of and in at with "
                 "a an for on".split())


def stok_metni_uygun(sorgu, metin):
    """Stok fotoğrafın kendi açıklamasında sorgunun bütün kelimeleri (eş anlamlılarıyla) geçmeli: "LPG gas pump" araması
    benzin pompası döndürdüğünde ("a gas pump next to a wall") LPG geçmediği için elenir; görsel model buna tek başına bırakılmaz."""
    mk = set(kelimeler(metin))
    ozgul = [w for w in kelimeler(sorgu) if w not in STOK_GENEL]
    for w in ozgul or kelimeler(sorgu):
        adaylar = {w} | ESANLAM.get(w, set())
        if not any(_kelime_tutar(a, m) or _kelime_tutar(m, a) for a in adaylar for m in mk):
            return False
    return True


def yasak_metin(konu, metin):
    """Dosya kaydında 'görselde olmamalı' unsurlardan biri açıkça geçiyor mu? (ör. muaythai haberinde 'boxing')"""
    yasak = " ".join(konu.get("gorselde_olmamali") or [])
    esler = {"boks": "boxing", "karate": "karate", "benzin": "gasoline petrol", "ceset": "corpse body", "polis": "police"}
    ek = " ".join(v for k, v in esler.items() if k in kucult(yasak))
    mk = {kok(w) for w in kelimeler(metin)}
    return bool({kok(w) for w in kelimeler(yasak + " " + ek) if len(w) >= 4} & mk)


def nesne_adaylari(bag, konu, haber, sorgular=None, gorulen=()):
    """KONU fotoğrafı (spor dalı, ürün, faaliyet, sektör): konuyu doğrudan gösteren ücretsiz fotoğraf. Yalnızca konu ayrıştırması
    "konu_gorseli.uygun" dediğinde ve hassas olmayan haberlerde aranır (ana öznenin tek etiketine bakılmaz: "muaythai şampiyonası"
    bir olay olsa da muaythai müsabakası fotoğrafı aranır). Önce Wikimedia Commons (dosya lisansı), sonra Unsplash (Unsplash Lisansı).
    Görüntü kontrolünde yabancı unsur, öne çıkan tanınabilir kişi ve 'görselde olmamalı' unsurlar elenir; sitede "Temsilî fotoğraf" denir."""
    kg = konu_gorseli(konu)
    adaylar, notlar = [], []
    if not kg.get("uygun"):
        return adaylar, notlar
    sorgular = sorgular or konu_sorgulari(konu)
    if not sorgular:
        return adaylar, notlar
    oz = konu.get("ana_ozne") or {}
    konu_ad = (konu.get("ne") or {}).get("ad") or oz.get("ad") or sorgular[0]
    # Çekirdek terim: konunun ilk (en somut) sorgusundaki özgül kelimeler (ör. "LPG", "muay thai"). Alternatif sorgular genelleşse de
    # ("fuel station") her aday kendi kaydında bu terimi taşımalı; yoksa LPG haberine benzin pompası gelir.
    # (yalnızca ilk özgül kelime zorunlu: "lpg", "muay", "gasoline"; diğerleri sorgunun kendi eşleşmesine bırakılır)
    cekirdek = " ".join([w for w in kelimeler((kg.get("sorgular_en") or sorgular)[0]) if w not in STOK_GENEL][:1])
    gorulen = set(gorulen)
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    us = kaynak_izni(bag.izinler, "https://unsplash.com/")
    yer = konu.get("nerede") or {}
    for sorgu in sorgular[:2]:
        manzara = bool(MANZARA.search(sorgu))
        if manzara and not yer.get("ad"):
            continue
        if commons and commons.get("izin") is True and bag.sorgu_hakki():
            try:
                alinan = 0
                for d in commons_dosyalar(bag, sorgu):
                    metin = commons_metni(d)
                    # önce ilgi (sorgunun ayırt edici kelimeleri dosyanın kendi kaydında geçmeli), sonra lisans
                    if bag.yasakli_mi(d["url"]) or d["url"] in gorulen:
                        continue
                    if not ad_eslesir(sorgu, metin):
                        ele(bag, d, f"konu: '{sorgu}' kayıtta geçmiyor"); continue
                    if yasak_metin(konu, metin):
                        ele(bag, d, "konu: görselde olmaması gereken unsur kayıtta geçiyor"); continue
                    if cekirdek and not stok_metni_uygun(cekirdek, metin):
                        ele(bag, d, f"konu: çekirdek terim '{cekirdek}' kayıtta yok"); continue
                    if manzara and not (ad_eslesir(yer["ad"], metin) or ad_eslesir(yer.get("ad_en") or yer["ad"], metin)):
                        continue          # otel/sahil gibi yer fotoğrafı: haberdeki yerin adı dosya kaydında doğrulanmalı
                    ok, neden = commons_uygun(d)
                    if not ok:
                        continue
                    gorulen.add(d["url"])
                    adaylar.append({"tur": "stok", "alt_tur": "nesne", "kaynak": "Wikimedia Commons", "url": d["url"],
                                    "kucuk": kontrol_kucuk(d["url"], d["genislik"]), "sayfa": d["sayfa"], "fotografci": d["yazar"],
                                    "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"], "kosul": commons.get("kosul", ""),
                                    "hedef": f"{konu_ad} (temsilî fotoğraf, olayın kendisi değil)", "gosterim_ad": konu_ad, "cekim": str(tarih_oku(d.get("tarih")) or ""),
                                    "meta_puan": 20 + min(10, d["genislik"] // 400), "kayit": d,
                                    "dogrulama": f"Commons dosya kaydında '{sorgu}' geçiyor; lisans dosya sayfasından okundu"})
                    alinan += 1
                    if alinan >= 2:
                        break
            except Exception as e:
                bag.arama_hatasi = True
                notlar.append(f"Commons konu araması yapılamadı ({sorgu}): {str(e)[:80]}")
        if UNSPLASH_KEY and us and us.get("izin") is True and not manzara and bag.sorgu_hakki():
            try:
                alinan = 0
                for f in unsplash_ara(bag, sorgu):
                    if bag.yasakli_mi(f["url"]) or f["url"] in gorulen or yasak_metin(konu, f["aciklama"]) \
                            or not stok_metni_uygun(sorgu, f["aciklama"]) or (cekirdek and not stok_metni_uygun(cekirdek, f["aciklama"])):
                        continue
                    gorulen.add(f["url"])
                    adaylar.append({"tur": "stok", "alt_tur": "nesne", "kaynak": "Unsplash", "url": f["url"], "kucuk": f["kucuk"],
                                    "sayfa": f["sayfa"], "fotografci": f["yazar"], "lisans": "Unsplash Lisansı",
                                    "lisans_dayanagi": "https://unsplash.com/license", "kosul": us.get("kosul", ""), "indirme": f["indirme"],
                                    "hedef": f"{konu_ad} (temsilî fotoğraf, olayın kendisi değil)", "gosterim_ad": konu_ad, "cekim": "", "meta_puan": 10,
                                    "kayit": {"baslik": f["aciklama"], "aciklama": f["aciklama"]},
                                    "dogrulama": f"Unsplash araması '{sorgu}'; kullanım hakkı Unsplash Lisansı (fotoğraf sayfası)"})
                    alinan += 1
                    if alinan >= 2:
                        break
            except Exception as e:
                bag.arama_hatasi = True
                notlar.append(f"Unsplash araması yapılamadı ({sorgu}): {str(e)[:80]}")
    if not adaylar:
        notlar.append(f"'{konu_ad}' konusunu gösteren lisanslı fotoğraf bulunamadı (sorgular: {', '.join(sorgular[:2])})")
    return adaylar, notlar


# Belirli tesis türleri: yer yalnızca bir şehir/ilçe ise "İstanbul + stadium" gibi aramalar rastgele bir tesisi getirir.
TESIS_TURU = re.compile(r"(?i)stadium|stadyum|stadı|arena|airport|havaliman|hospital|hastane|university|üniversite|campus|kampüs|"
                        r"court|adliye|school|okul|terminal|station|istasyon|prison|cezaevi|factory|fabrika")


INSAN_IPUCU = re.compile(r"(?i)\b(officials?|people|crowd|lawyers?|staff|members?|delegation|participants?|audience|students?|"
                         r"workers?|police officers?|politicians?|speakers?|guests?|visitors?|team|players?)\b")


def yer_eslesir(ad, metin):
    """Yer adının bütün kelimeleri dosya kaydında geçiyor mu? (ad_eslesir'den farkı: "Antalya" gibi geniş bir ad da YER olarak
    tutar; dosyanın konuyla ilişkisi ayrıca konu ipucunun tam ifadesiyle aranır.)"""
    k = kelimeler(ad)
    if not k or kucult(ad) in ULKE_ADLARI:
        return False
    m = kelimeler(metin)
    return all(any(_kelime_tutar(t, w) for w in m) for t in k)


def ele(bag, d, neden):
    """Görüntü kontrolüne gönderilmeden elenen dosyanın tanı kaydı (en çok 40)."""
    if isinstance(getattr(bag, "elenen", None), list) and len(bag.elenen) < 40:
        bag.elenen.append(f"{(d.get('baslik') or d.get('aciklama') or d.get('url', ''))[:70].replace('File:', '')}: {neden}")


def yerel_gorsel_kullaniliyor(url):
    """/images/kurum/... dosyası herhangi bir haberin mevcut görseli ya da geri alma kaydında (gorsel_gecmis) geçiyor mu?
    Geçiyorsa silinmez: aynı kurum fotoğrafı başka bir haberde onaylı kullanılıyor olabilir, eski görsel de geri alınabilir kalmalı."""
    try:
        for ad in os.listdir(HABER_KLASOR):
            with open(os.path.join(HABER_KLASOR, ad), encoding="utf-8") as f:
                if url in f.read():
                    return True
    except OSError:
        return True
    return False


def yerel_gorsel_sil(url):
    if url.startswith("/images/kurum/") and not yerel_gorsel_kullaniliyor(url):
        try:
            os.remove(os.path.join(KOK, "static", url.lstrip("/")))
        except OSError:
            pass


def yerel_gorsel_eksik(url):
    return bool(url) and url.startswith("/images/") and not url.startswith("/images/kategori/") \
        and not os.path.exists(os.path.join(KOK, "static", url.lstrip("/")))


# Ülke ve kıta düzeyindeki yerler: "Çin + doğal taş" gibi bir aramada ülkenin herhangi bir taşı haberdeki konu değildir.
ULKE_TURLERI = {"Q6256", "Q3624078", "Q5107", "Q112099", "Q7275", "Q3336843"}
ULKE_ADLARI = {kucult(x) for x in (
    "Çin China Japonya Japan Kore Korea Hindistan India Rusya Russia Ukrayna Ukraine Almanya Germany Fransa France İngiltere England "
    "Birleşik Krallık United Kingdom Britanya Britain İtalya Italy İspanya Spain Portekiz Portugal Hollanda Netherlands Belçika Belgium "
    "Avusturya Austria İsviçre Switzerland Polonya Poland Yunanistan Greece Bulgaristan Bulgaria Romanya Romania Sırbistan Serbia "
    "Macaristan Hungary Çekya Czechia İsveç Sweden Norveç Norway Danimarka Denmark Finlandiya Finland İrlanda Ireland "
    "Amerika ABD USA America Kanada Canada Meksika Mexico Brezilya Brazil Arjantin Argentina Avustralya Australia Mısır Egypt "
    "İran Iran Irak Iraq Suriye Syria İsrail Israel Filistin Palestine Lübnan Lebanon Ürdün Jordan Suudi Arabistan Saudi Arabia "
    "Katar Qatar Kuveyt Kuwait Azerbaycan Azerbaijan Gürcistan Georgia Ermenistan Armenia Kazakistan Kazakhstan Özbekistan Uzbekistan "
    "Türkmenistan Turkmenistan Kırgızistan Kyrgyzstan Pakistan Afganistan Afghanistan Endonezya Indonesia Malezya Malaysia Tayland Thailand "
    "Vietnam Filipinler Philippines Libya Tunus Tunisia Cezayir Algeria Fas Morocco Nijerya Nigeria Etiyopya Ethiopia Somali Somalia "
    "Kıbrıs Cyprus KKTC Avrupa Europe Asya Asia Afrika Africa Ortadoğu Türkiye Turkey Türkiye'de").split()}


def yer_konu_adaylari(bag, konu, sorgular=None, en_fazla=2, gorulen=()):
    """NEREDE + NE: haberin geçtiği yerde, konuyla doğrudan ilişkili alanın arşiv fotoğrafı
    (ör. Antalya Havalimanı + yakıt sızıntısı → apron/uçak; Alanya + otel doluluğu → oteller/otel bölgesi).
    Dosyanın kendi kaydında hem YER adı hem KONU ipucu geçmeli; yalnızca şehir/yer eşleşmesi yetmez."""
    yer = konu.get("nerede") or {}
    # Toplantı/insan ipuçları ("meeting", "officials", "lawyer") başka bir etkinliğin insanlarını gösterir: yer+konu için kullanılmaz.
    ipuclari = [ip for ip in (konu.get("gorsel_ipucu_en") or []) if not TOPLANTI.search(ip) and not INSAN_IPUCU.search(ip)]
    adaylar, notlar = [], []
    if not (yer.get("ad") and ipuclari) or konu.get("hassas", "yok") in ("ölüm", "çocuk"):
        return adaylar, notlar
    if (konu.get("ana_ozne") or {}).get("tur") == "takim":
        # Takım haberinde yalnızca takımın kendi doğrulanmış kayıtları kullanılır: şehirdeki rastgele bir maç/stat karesi
        # başka bir takımı ya da başka bir tesisi gösterir.
        notlar.append(f"takım haberi: '{yer['ad']}' konumundaki genel spor fotoğrafları kullanılmadı (takımın kendi kaydı gerekir)")
        return adaylar, notlar
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    yer_en = yer.get("ad_en") or yer["ad"]
    tesis_adi = bool(TESIS_TURU.search(yer["ad"] + " " + yer_en))
    if not tesis_adi:
        # Yer bir şehir/ilçe adıysa tesis türü ipuçları (stadyum, havalimanı, hastane…) kullanılmaz: o şehirdeki herhangi bir
        # stadyum haberdeki tesis değildir. Tesisin kendisi adıyla (ör. "Antalya Havalimanı") verilmişse kullanılır.
        ipuclari = [ip for ip in ipuclari if not TESIS_TURU.search(ip)]
        sorgular = [q for q in (sorgular or []) if not TESIS_TURU.search(q)] or None
        if not ipuclari:
            notlar.append(f"'{yer['ad']}' bir şehir/ilçe; tesis türü ipucuyla rastgele tesis fotoğrafı aranmadı")
            return adaylar, notlar
    sorgular = sorgular or [f"{yer_en} {ip}" for ip in ipuclari[:2]]
    ip_kok = {kok(w) for ip in ipuclari for w in kelimeler(ip)}
    dosyalar = []
    try:
        v = wikidata_varlik(bag, yer["ad"], insan=False, ipucu="Antalya") or (wikidata_varlik(bag, yer_en, insan=False, ipucu="Antalya")
                                                                              if yer_en != yer["ad"] else None)
        if (v and set(v.get("p31") or []) & ULKE_TURLERI) or kucult(yer["ad"]) in ULKE_ADLARI or kucult(yer_en) in ULKE_ADLARI:
            notlar.append(f"'{yer['ad']}' bir ülke/bölge; ülke genelinden konu fotoğrafı aranmadı (haberdeki yer değil)")
            return adaylar, notlar
        if v and v.get("kategori"):
            dosyalar += kategori_dosyalari(bag, v["kategori"], alt_kategori=6)
    except Exception as e:
        notlar.append(f"'{yer['ad']}' Wikidata/Commons kategorisi okunamadı: {str(e)[:60]}")
    dosyalar = [x for x in bag.arsiv.get("kut:ad:" + kucult(yer["ad"]), []) if isinstance(x, dict)] + dosyalar
    for q in sorgular[:3]:
        if not bag.sorgu_hakki():
            break
        try:
            dosyalar += commons_dosyalar(bag, q)
        except Exception as e:
            bag.arama_hatasi = True
            notlar.append(f"Commons araması yapılamadı ({q}): {str(e)[:60]}")
    seen = set(gorulen)
    for d in dosyalar:
        if not d or d["url"] in seen or bag.yasakli_mi(d["url"]):
            continue
        seen.add(d["url"])
        metin = commons_metni(d)
        mk = {kok(w) for w in kelimeler(metin)}
        yer_tutar = yer_eslesir(yer["ad"], metin) or yer_eslesir(yer_en, metin)
        # konu ipucu tek kelimeyle değil, ifadenin bütün özgül kelimeleriyle tutmalı ("natural stone" ≠ "tektite stone structure")
        if not yer_tutar:
            ele(bag, d, f"yer/konu: kayıtta '{yer['ad']}' geçmiyor"); continue
        if not any(stok_metni_uygun(ip, metin) for ip in ipuclari):
            ele(bag, d, f"yer/konu: konu ipucu ({', '.join(ipuclari[:2])}) kayıtta yok"); continue
        if yasak_metin(konu, metin) or (not tesis_adi and TESIS_TURU.search(d["baslik"])):
            ele(bag, d, "yer/konu: yasak unsur ya da başka tesis"); continue
        if MAC_IPUCU.search(d["baslik"].replace("File:", "") + " " + d["aciklama"][:160]):
            continue              # belirli bir karşılaşmanın (A vs B) karesi başka takımları gösterir
        if not commons_uygun(d)[0]:
            continue
        cekim = tarih_oku(d.get("tarih"))
        adaylar.append({
            "tur": "arsiv", "alt_tur": "yer_konu", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]),
            "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
            "kosul": commons.get("kosul", ""), "hedef": f"{yer['ad']} — {', '.join(ipuclari[:2])} (konuyla ilişkili alan, arşiv)",
            "gosterim_ad": yer["ad"], "cekim": str(cekim or ""), "kayit": d, "onayli": "onay" in d,
            "meta_puan": 60 + (min(10, cekim.year - 2010) if cekim else 0) - 6 * int(d.get("kullanim", 0)),
            "dogrulama": f"dosya kaydında yer ('{yer['ad']}') ve konu ipucu ({', '.join(ipuclari[:2])}) birlikte geçiyor",
        })
    adaylar.sort(key=lambda a: -a["meta_puan"])
    if not adaylar:
        notlar.append(f"'{yer['ad']}' için konuyla ({', '.join(ipuclari)}) ilişkili lisanslı fotoğraf bulunamadı")
    return adaylar[:en_fazla], notlar


# Arama servisinin kendisi (Wikidata, Commons, Unsplash, kurum haber araması) yanıt vermediyse; tek bir kurum sayfasının okunamaması değil.
ARAMA_HATA_RE = re.compile(r"(araması|sorgusu) yapılamadı|Commons okunamadı|kategorisi okunamadı|yer fotoğrafları okunamadı|fotoğrafı aranamadı|"
                           r"kategorisi okunamadı|kurum fotoğrafı araması yapılamadı")


def kapak_nedeni(adaylar, notlar, kontrol_hatasi="", konu=None, kalan=0, butce=False, arama_hatasi=False):
    """Kategori kapağı neden kullanıldı? Kota/servis hatası, bütçe dolması ve arama hatası "fotoğraf yok" sayılmaz (ayrı kodlar;
    bunlarda haber sonra yeniden denenir). 'notlar' alanı her katmanın arama sonucunu tanı için saklar."""
    metin = " ".join(notlar)
    ozet = "; ".join(dict.fromkeys(notlar))[:700]
    red = [a for a in adaylar if a.get("karar") == "red"]
    nedenler = "; ".join(sorted({(a.get("gerekce") or "")[:90] for a in red}))[:300]
    if kontrol_hatasi:
        return {"kod": "kota_veya_servis_hatasi", "aciklama": kontrol_hatasi[:200], "notlar": ozet}
    if butce and kalan:
        return {"kod": "butce_doldu", "aciklama": f"{len(red)} aday elendi; {kalan} aday bu turun bütçesi nedeniyle kontrol edilmedi "
                                                  "(sonraki turda denenecek)", "notlar": ozet}
    if red:
        return {"kod": "gorsel_reddedildi", "aciklama": f"{len(red)} aday görüntü kontrolünde elendi: {nedenler}", "notlar": ozet,
                "elenenler": [{"sayfa": a.get("sayfa") or a.get("url", ""), "katman": a.get("alt_tur", ""), "neden": (a.get("gerekce") or "")[:160]}
                              for a in red[:6]]}
    servis = [n for n in notlar if ARAMA_HATA_RE.search(n)]
    if arama_hatasi or servis:
        return {"kod": "arama_hatasi", "aciklama": (servis[0] if servis else "arama servisi yanıt vermedi")[:200], "notlar": ozet}
    oz = (konu or {}).get("ana_ozne") or {}
    if oz.get("tur") == "kisi" and any("kimliği kesin doğrulanan" in n and set(kelimeler(oz.get("ad", ""))) & set(kelimeler(n)) for n in notlar):
        return {"kod": "kimlik_eslesmedi", "aciklama": next(n for n in notlar if "kimliği kesin doğrulanan" in n)[:200], "notlar": ozet}
    if LISANS_RED:
        return {"kod": "lisans_uygun_degil", "aciklama": f"konuyla eşleşen {len(LISANS_RED)} dosyanın lisansı serbest değil ya da okunamadı "
                                                         f"(ör. {LISANS_RED[0][:80]})", "notlar": ozet}
    if LISANS_OKUNAMADI:
        return {"kod": "lisans_okunamadi", "aciklama": f"konuyla eşleşen {len(LISANS_OKUNAMADI)} dosyanın lisans bilgisi okunamadı "
                                                       f"(ör. {LISANS_OKUNAMADI[0][:80]})", "notlar": ozet}
    if not notlar:
        return {"kod": "arama_yapilmadi", "aciklama": "haberde fotoğrafı aranabilecek kişi, kurum, yer ya da konu bulunamadı", "notlar": ozet}
    return {"kod": "aday_bulunamadi", "aciklama": ozet[:300], "notlar": ozet}


def kurum_haber_adaylari(bag, konu, haber):
    """En üst katman: aynı haberin resmî kurum (belediye, valilik, bakanlık…) sitesindeki kendi fotoğrafı."""
    izin = kaynak_izni(bag.izinler, "https://www.antalya.bel.tr/")
    if kf is None or not (izin and izin.get("izin") is True) or konu.get("hassas", "yok") in KURUM_HASSAS:
        return [], []
    if not bag.ayar.get("kurum_fotografi", True):
        return [], []
    try:
        bulunan, notlar = kf.kurum_adaylari(haber, konu, bag.arsiv)
    except Exception as e:
        print(f"   kurum fotoğrafı aranamadı: {str(e)[:100]}")
        return [], [f"kurum fotoğrafı aranamadı: {str(e)[:100]}"]
    adaylar = []
    for b in bulunan:
        # KONTROL 2 — yeniden kullanım izni: o alan adı için izin kaydı ve dayanağı olmadan kullanılmaz.
        kayit = kaynak_izni(bag.izinler, b["sayfa"])
        if not (kayit and kayit.get("izin") is True and kayit.get("haber_gorseli") is True and str(kayit.get("dayanak") or "").strip()):
            notlar.append(f"{b['kurum']}: yeniden kullanım izni kaydı/dayanağı yok; kurumun fotoğrafı kullanılmadı")
            yerel_gorsel_sil(b["url"])
            continue
        adaylar.append({
            "izin_kontrolu": {"kendi_fotografi": b.get("kendi_fotografi", ""), "yeniden_kullanim": kayit.get("dayanak", ""),
                              "izin_kaydi": kayit.get("ad", ""), "kosul": kayit.get("kosul", "")},
            "tur": "gercek", "alt_tur": "kurum_haber", "kaynak": b["kurum"], "url": b["url"], "kucuk": b["kucuk"], "sayfa": b["sayfa"],
            "fotografci": b["kurum"], "lisans": "Kurum izniyle", "lisans_dayanagi": kayit.get("dayanak", ""), "kosul": kayit.get("kosul", ""),
            "hedef": f"{b['kurum']} sitesinde yayımlanan aynı haberin fotoğrafı", "cekim": "",
            "dogrulama": f"Haber {b['kurum']} sitesinde bulundu (metin benzerliği %{int(b['oran'] * 100)}); fotoğraf kurumun kendi sayfasından",
            "meta_puan": 400,
        })
    bag.son_kurum_notu = (f"{adaylar[0]['kaynak']} sayfasında bulundu (kendi fotoğrafı + izin kaydı doğrulandı)" if adaylar
                          else "; ".join(notlar))[:300]
    print("   kurum fotoğrafı: " + bag.son_kurum_notu)
    return adaylar, notlar


def aday_sira_anahtari(a, oz_tur=""):
    """Önce katman (kişi/kurum doğruluğu), sonra UYGUNLUK (etkinlik benzerliği, tek kişi, konu ilişkisi; 2'şer puanlık
    dilimler), uygunluğu benzer adaylarda daha YAKIN TARİHLİ çekim, en son kayıt puanı. Yeni olduğu için zayıf kare seçilmez."""
    katman = {"gercek": 4, "kisi_benzer": 3, "kisi_genel": 2, "yer_konu": 1.5, "yer": 0, "nesne": -1}.get(a.get("alt_tur") or a["tur"], 1)
    if oz_tur in ("kurum", "takim", "yer") and str(a.get("alt_tur", "")).startswith("kisi"):
        katman = 0.5          # ana özne kişi değilse kişi fotoğrafı kurum/yer fotoğrafının önüne geçmez
    if a["tur"] == "gercek":
        katman = 4
    uygunluk = (a.get("benzerlik") or 0) + (3 if a.get("tek") else 0) + (a.get("iliski") or 0)
    cekim = (a.get("cekim") or "")[:10]
    return (katman, uygunluk // 2, cekim, (a.get("meta_puan") or 0))


def eski_gecerli(eski, konu, haber):
    """Mevcut otomatik görsel bugünkü kod kurallarını (kimlik/konu/yer) hâlâ sağlıyor mu? Sağlamıyorsa korunmaz.
    (ör. takım haberinde başka takımların maç karesi, insan odaklı etkinlikte genel stok, olay adıyla bulunmuş başka etkinlik karesi)"""
    s = eski.get("secim") or {}
    alt, tur = s.get("alt_tur") or eski.get("alt_tur", ""), s.get("tur", "")
    oz = (konu.get("ana_ozne") or {}).get("tur", "")
    from urllib.parse import unquote
    metin = unquote(s.get("kaynak_sayfa") or "").replace("_", " ") + " " + (s.get("gosterilen") or "")
    if tur == "gercek":
        return True
    if alt == "nesne" or tur == "stok" or "unsplash" in eski.get("url", ""):
        kg = konu_gorseli(konu)
        if not kg.get("uygun"):
            return False
        cekirdek = " ".join([w for w in kelimeler((kg.get("sorgular_en") or [""])[0]) if w not in STOK_GENEL][:1])
        kaynak = unquote(s.get("kaynak_sayfa") or "").replace("-", " ").replace("_", " ") + " " + (s.get("kaynak_aciklama") or "")
        return not cekirdek or stok_metni_uygun(cekirdek, kaynak)
    if alt == "yer_konu":
        ipl = [ip for ip in (konu.get("gorsel_ipucu_en") or []) if not TESIS_TURU.search(ip)] or (konu.get("gorsel_ipucu_en") or [])
        kaynak = metin + " " + (s.get("kaynak_aciklama") or "")
        if ipl and not any(stok_metni_uygun(ip, kaynak) for ip in ipl):
            return False
        if kucult(s.get("gosterilen") or "") in ULKE_ADLARI:
            return False
        return oz != "takim" and not MAC_IPUCU.search(metin) and (bool(TESIS_TURU.search(s.get("gosterilen") or "")) or not TESIS_TURU.search(metin))
    if alt == "yer":
        return bool(haber_yeri(konu, haber))
    if alt.startswith("kisi"):
        ad = s.get("gosterilen") or ""
        return any(set(kelimeler(ad)) & set(kelimeler(k["ad"])) for k in konu.get("ana_kisiler") or [])
    if alt == "kurum":
        ad = s.get("gosterilen") or ""
        return any(h.get("tur") != "olay" and (ad_eslesir(h["ad"], ad) or ad_eslesir(ad, h["ad"])) for h in konu.get("hedefler") or []) \
            and not (oz == "takim" and MAC_IPUCU.search(metin))
    return False


def gorsel_sec(bag, haber, konu, eski=None):
    """haber: {baslik, spot, kategori, ilce, tarih(date), kaynak_url, kaynak_gorsel}
    Dönüş: (secim, rapor). Gerçek → arşiv → fotoğrafsız kapak. Başka seçenek yoktur."""
    kimza = konu_imzasi(haber.get("baslik"), haber.get("spot"), haber.get("kategori"))
    rapor = {"notlar": [], "adaylar": [], "eski": None}
    simdi = simdi_tr().isoformat(timespec="seconds")
    del LISANS_RED[:]
    del LISANS_OKUNAMADI[:]
    if not konu:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "konu ayrıştırılamadı"}
        s = kapak_secimi(haber.get("kategori"), "Haberin konusu ayrıştırılamadı; fotoğrafsız kapak.", k, kimza)
        s["bekliyor"] = True
        s["kapak_nedeni"] = {"kod": "arama_yapilmadi", "aciklama": "haberin konusu ayrıştırılamadı (yapay zekâ kotası ya da servis hatası)"}
        return s, rapor

    bag.yeni_secim()
    t0 = time.time()
    gercek, n1 = gercek_adaylar(bag, konu, haber)
    kurum_h, n6 = kurum_haber_adaylari(bag, konu, haber)
    kisi, n3 = kisi_adaylari(bag, konu, haber)
    # Kurum/tesis/takım fotoğrafı yalnızca haberin öznesi olan hedefler için aranır: başlıkta ya da spotta adı geçen, ana özne olan
    # ya da olayın geçtiği yer olan. Metnin içinde ikincil olarak geçen bir liman/havalimanı (ör. turizm istatistiği haberinde)
    # haberin konusunu anlatmaz.
    bs = f"{haber.get('baslik', '')} {haber.get('spot', '')}"
    oz_ad = (konu.get("ana_ozne") or {}).get("ad", "")
    ner = (konu.get("nerede") or {}).get("ad", "")
    secili, ikincil = [], []
    for h in konu.get("hedefler") or []:
        ana = (ad_eslesir(h["ad"], bs) or ad_eslesir(h.get("ad_en") or h["ad"], bs) or kucult(h["ad"]) == kucult(oz_ad)
               or (ner and (ad_eslesir(h["ad"], ner) or ad_eslesir(ner, h["ad"]))))
        (secili if ana else ikincil).append(h)
    if ikincil:
        rapor["notlar"].append("ikincil hedefler (başlık/spotta yok) aranmadı: " + ", ".join(h["ad"] for h in ikincil))
    konu_h = dict(konu, hedefler=secili)
    kurum_wd, n4 = kurum_wikidata_adaylari(bag, konu_h)
    arsiv, n2 = arsiv_adaylari(bag, konu_h)
    yer, n5 = yer_adaylari(bag, konu, haber)
    yk, n8 = yer_konu_adaylari(bag, konu)
    nesne, n7 = nesne_adaylari(bag, konu, haber)
    rapor["notlar"] += n6 + n1 + n3 + n4 + n2 + n5 + n8 + n7
    gercek = kurum_h + gercek + [a for a in kisi if a["tur"] == "gercek"]
    kisi = [a for a in kisi if a["tur"] != "gercek"]
    kurum = [dict(a, alt_tur=a.get("alt_tur") or "kurum") for a in kurum_wd + arsiv]
    oz_tur = (konu.get("ana_ozne") or {}).get("tur", "")
    # Katmanlar (öncelik sırası): aynı olay → ana özne (kişi ya da kurum/takım/tesis) → yer + konu → konu fotoğrafı → yer genel görünümü.
    # Hiçbir katman tek adaya indirilmez; görüntü kontrolüne her turda bütçe kadar aday, katmanlar arasında dengeli gönderilir.
    ozne = [("kurum", kurum), ("kisi", kisi)] if oz_tur in ("kurum", "takim", "yer", "olay") else [("kisi", kisi), ("kurum", kurum)]
    sira_k = [("gercek", gercek)] + ozne + [("yer_konu", yk), ("konu", nesne), ("yer", yer)]
    gorulen, havuz = set(), {}
    for ad, liste in sira_k:
        havuz[ad] = []
        for a in liste:
            if a["url"] not in gorulen:
                gorulen.add(a["url"])
                havuz[ad].append(a)
    rapor["havuz"] = {ad: len(l) for ad, l in havuz.items()}
    butce = max(1, int(bag.ayar.get("gorsel_aday_butcesi", 5)))
    tur_sayisi = max(1, int(bag.ayar.get("gorsel_kontrol_turu", 2)))

    def onceki_karar(a):
        return bag.onbellek_al(imza(KURAL_SURUMU, kimza, a["url"]))

    def parti():
        """Bütçe kadar aday: önce her katmandan en çok 2, sonra kalan bütçe sırayla. Bu haber için önceden elenmiş
        adaylar bütçeyi harcamaz (sonraki denemede yeni adaylara geçilir)."""
        secilen, eski_red = [], []
        for sinir in (2, butce):
            for ad, _ in sira_k:
                al = 0
                while havuz[ad] and len(secilen) < butce and al < sinir:
                    a = havuz[ad].pop(0)
                    ob = onceki_karar(a)
                    if ob and ob.get("karar") == "red" and not eski_red_yeniden(a, ob, konu):
                        eski_red.append(a)
                        continue
                    secilen.append(a)
                    al += 1
        return secilen, eski_red

    eski_aday = None
    if eski and eski.get("url") and eski.get("secim") is not None and not eski_gecerli(eski, konu, haber):
        rapor["notlar"].append("mevcut görsel bugünkü kimlik/konu kurallarını sağlamıyor; korunmadı")
        eski = None
    if eski and eski.get("url"):
        eski_aday = {"tur": "eski", "kaynak": "mevcut", "url": eski["url"], "kucuk": kontrol_kucuk(eski["url"]),
                     "alt_tur": eski.get("alt_tur") or ("nesne" if "unsplash" in eski["url"] else "kurum"),
                     "hedef": "haberin şu anki görseli (eski otomatik seçim)"}
    gonderilen, kabul, kontrol_yapildi, hata = [], [], False, ""
    t1 = time.time()
    # Haberin ana öznesi olan kişinin, bu bağlam (genel/hassas) için daha önce onaylanmış nötr arşiv portresi varsa ve aynı olaya ait
    # fotoğraf yoksa, aynı portre her haberde yeniden aranıp görsel değerlendirmeye gönderilmez: onay kaydı ve bağlam uygunluğu
    # (kimlik + lisans + nötr portre + hassas/genel bağlam) arşiv kaydından okunur.
    baglam = "genel" if konu.get("hassas", "yok") == "yok" else "hassas"
    hazir = [a for a in havuz["kisi"] if oz_tur == "kisi" and a.get("onayli") and a.get("alt_tur") == "kisi_genel"
             and baglam in ((a.get("kayit") or {}).get("arsiv_kaydi") or {}).get("uygun_baglamlar", [])
             and ((a.get("kayit") or {}).get("arsiv_kaydi") or {}).get("kontrol", {}).get("tek")]
    if hazir and not havuz["gercek"]:
        a = sorted(hazir, key=lambda x: aday_sira_anahtari(x, oz_tur), reverse=True)[0]
        ak = a["kayit"]["arsiv_kaydi"]
        a.update(karar="kabul", tek=True, kontrol_tarih=ak.get("dogrulama_tarihi", ""), onbellekten=True,
                 gerekce=f"önceden onaylanmış nötr arşiv portresi ({ak.get('dogrulama_tarihi', '')}); '{baglam}' bağlamına uygun olarak kayıtlı")
        kabul, tur_sayisi = [a], 0
        gonderilen.append(a)
        rapor["notlar"].append("onaylı arşiv portresi kullanıldı; görsel değerlendirmeye yeniden gönderilmedi")
    for tur_no in range(tur_sayisi):
        if tur_no > 0:
            # Reddedilen adaylardan sonra arama hemen bitmez: alternatif sorgularla SINIRLI bir tur daha (aynı kurallarla).
            hepsi = gorulen | {a["url"] for a in gonderilen}
            alt = konu_sorgulari(konu, alternatif=True)
            if alt:
                e, n = nesne_adaylari(bag, konu, haber, sorgular=alt[:2], gorulen=hepsi)
                havuz["konu"] += e
                rapor["notlar"] += n
                hepsi |= {a["url"] for a in e}
            ipl = konu.get("gorsel_ipucu_en") or []
            if (konu.get("nerede") or {}).get("ad") and len(ipl) > 2:
                e, n = yer_konu_adaylari(bag, konu, sorgular=[f"{(konu['nerede'].get('ad_en') or konu['nerede']['ad'])} {ipl[2]}"], gorulen=hepsi)
                havuz["yer_konu"] += e
        secilen, eski_red = parti()
        for a in eski_red:
            ob = onceki_karar(a)
            a.update(karar="red", gerekce=ob.get("gerekce", ""), iliski=ob.get("iliski", 0), onbellekten=True)
        gonderilen += eski_red
        if not secilen and not (eski_aday and tur_no == 0):
            break
        if tur_no > 0:
            rapor["notlar"].append(f"ilk adaylar elendi; {tur_no + 1}. turda {len(secilen)} aday daha denendi")
        if not bag.yz_hakki(1):
            havuz["kontrol_bekleyen"] = secilen
            bag.butce_doldu = True
            rapor["notlar"].append("görsel kontrol hakkı (günlük sınır) doldu; kalan adaylar sonraki turda denenecek")
            break
        liste = secilen + ([eski_aday] if eski_aday and tur_no == 0 else [])
        try:
            gorsel_kontrol(bag, haber, konu, kimza, liste)
            kontrol_yapildi = True
        except (KotaHatasi, ServisHatasi) as e:
            hata = str(e)
            rapor["notlar"].append("görsel kontrol yapılamadı: " + hata)
            bag.notlar.append(f"{haber.get('baslik', '')[:60]}: {hata}")
            havuz["kontrol_bekleyen"] = secilen
            break
        gonderilen += secilen

        def sira(a):
            return aday_sira_anahtari(a, oz_tur)
        kabul = sorted([a for a in gonderilen if a.get("karar") == "kabul"], key=sira, reverse=True)
        if kabul:
            break
    kalan = sum(len(l) for l in havuz.values())
    print(f"   görsel: havuz {rapor['havuz']}, kontrol {len(gonderilen)} aday, {time.time() - t1:.0f} sn"
          + (f" ({hata[:80]})" if hata else ""))
    if eski_aday:
        rapor["eski"] = {k: eski_aday.get(k) for k in ("karar", "gerekce", "bulgular")}
    rapor["sorgular"] = list(getattr(bag, "sorgu_kaydi", []))[:40]
    rapor["on_elenen"] = list(getattr(bag, "elenen", []))[:40]
    rapor["adaylar"] = [{k: a.get(k) for k in ("tur", "alt_tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "hedef", "cekim", "karar",
                                               "gerekce", "dogrulama", "benzerlik", "tek", "iliski", "meta_puan", "onayli")} for a in gonderilen]
    for a in kurum_h:                # görüntü kontrolünden geçmeyen kurum fotoğrafı sitede tutulmaz
        if a.get("karar") != "kabul" and a["url"].startswith("/images/kurum/"):
            yerel_gorsel_sil(a["url"])

    if kabul:
        a = kabul[0]
        if a.get("indirme") and UNSPLASH_KEY:        # Unsplash kullanım koşulu: seçilen fotoğraf için indirme bildirimi
            try:
                requests.get(a["indirme"], headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"}, timeout=10)
            except Exception:
                pass
        kontrol = {"yapildi": True, "sonuc": "onaylandı", "tarih": a.get("kontrol_tarih") or simdi,
                   "yontem": "Wikidata/Commons kimlik doğrulaması + Gemini sahne kontrolü" if a.get("qid") else "kaynak/açıklama doğrulaması + Gemini görsel kontrol",
                   "model": bag.son_model or bag.model_fn()}
        if a.get("onbellekten") and "önceden onaylanmış nötr arşiv portresi" in a.get("gerekce", ""):
            kontrol.update(sonuc="onaylı arşiv kaydından", yontem="Wikidata kimliği + lisans + önceki görsel onayı (arşiv kaydı)", model="")
        onayli_kaydet(bag, a, simdi, konu)
        return aday_secimi(a, kontrol, kimza, konu), rapor

    if not kontrol_yapildi and hata:
        # Görüntü kontrolü yapılamadı: doğrulanmamış aday onaylanmış gibi yayımlanmaz. Bu kişi/kurum/yer için DAHA ÖNCE
        # görüntü kontrolünden geçmiş arşiv fotoğrafı varsa o kullanılır (kontrol yapılmadığı açıkça kaydedilir, sonra yeniden denenir).
        yedek = [a for a in kisi + kurum + yk + yer if a.get("onayli") and a.get("alt_tur") in ("kisi_genel", "kurum", "yer", "yer_konu")
                 and (konu.get("hassas", "yok") == "yok" or a.get("portre"))]
        if yedek:
            a = sorted(yedek, key=lambda x: aday_sira_anahtari(x, oz_tur), reverse=True)[0]
            k = {"yapildi": False, "sonuc": "önceden onaylanmış arşiv görseli (bu haber için görüntü kontrolü yapılamadı)", "tarih": simdi,
                 "yontem": "kimlik ve lisans kaydı daha önce doğrulanmış arşiv", "neden": hata[:160]}
            s = aday_secimi(a, k, kimza, konu)
            s["bekliyor"] = True
            return s, rapor
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "Gemini görsel kontrol", "neden": hata}
        s = kapak_secimi(haber.get("kategori"), f"Adaylar görsel kontrolden geçirilemedi ({hata[:160]}); kontrol edilmemiş fotoğraf kullanılmadı.", k, kimza)
        s["bekliyor"] = True
        s["kapak_nedeni"] = kapak_nedeni(gonderilen, rapor["notlar"], hata, konu=konu, kalan=kalan)
        return s, rapor

    if (eski_aday and eski_aday.get("karar") == "kabul" and eski.get("koru")
            and oz_tur not in ("kisi", "kurum", "takim", "yer", "olay") and konu.get("hassas", "yok") == "yok"):
        k = {"yapildi": True, "sonuc": "mevcut görsel onaylandı", "tarih": eski_aday.get("kontrol_tarih") or simdi,
             "yontem": "Gemini görsel kontrol", "model": bag.son_model or bag.model_fn()}
        return {"tur": "stok", "gorsel": eski["url"], "gorsel_kredi": eski.get("kredi", ""), "kaynak_sayfa": "", "fotografci": "",
                "kredi": eski.get("kredi", ""), "lisans": "Unsplash Lisansı", "lisans_dayanagi": "https://unsplash.com/license", "kosul": "",
                "gosterilen": "Temsilî görsel", "aciklama": "Temsilî fotoğraf", "cekim_yili": "", "kontrol": k, "surum": KURAL_SURUMU,
                "konu_imza": kimza,
                "gerekce": "Doğrulanmış gerçek fotoğraf bulunamadı; mevcut temsilî görsel görüntü kontrolünden geçtiği için korundu: "
                           f"{eski_aday.get('gerekce', '')}"}, rapor
    if gonderilen:
        k = {"yapildi": kontrol_yapildi, "sonuc": "adaylar reddedildi", "tarih": simdi, "yontem": "Gemini görsel kontrol",
             "model": bag.son_model or bag.model_fn()}
        neden = f"{len(gonderilen)} aday görsel kontrolden geçemedi"
    else:
        k = {"yapildi": False, "sonuc": "aday yok", "tarih": simdi, "yontem": "kaynak/arşiv taraması"}
        neden = "aynı olaya, haberin öznesine ya da konusuna ait kullanım hakkı doğrulanmış fotoğraf bulunamadı"
    s = kapak_secimi(haber.get("kategori"), neden[0].upper() + neden[1:] + "; fotoğrafsız kapak.", k, kimza)
    s["kapak_nedeni"] = kapak_nedeni(gonderilen, rapor["notlar"], konu=konu, kalan=kalan, butce=bag.butce_doldu,
                                     arama_hatasi=bag.arama_hatasi)
    if s["kapak_nedeni"]["kod"] in ("arama_hatasi", "butce_doldu"):
        s["bekliyor"] = True              # "fotoğraf yok" değil: sonraki turda yeniden denenir
    return s, rapor


def onayli_kaydet(bag, a, simdi, konu=None):
    """Onaylanan arşiv fotoğrafı, kimliğiyle (Wikidata QID ya da tür+ad) yeniden kullanılabilir arşive yazılır
    (data/foto_arsivi.json, "kut:" anahtarları). Kayıt: görsel ve kaynak sayfası, fotoğraf sahibi ve kullanım dayanağı, atıf metni,
    kimlik, doğrulanmış çekim tarihi (yoksa boş; yükleme tarihi kullanılmaz), görsel türü, uygun olduğu/olmadığı bağlamlar,
    seçim gerekçesi, doğrulama tarihi ve kural sürümü. Aynı fotoğraf sık kullanıldıkça sırası düşer."""
    if a.get("tur") != "arsiv" or not a.get("kayit"):
        return
    anahtar = "kut:" + a["qid"] if a.get("qid") else ("kut:ad:" + kucult(a.get("gosterim_ad") or a.get("hedef", ""))
                                                    if a.get("alt_tur") in ("kurum", "yer_konu", "yer") else "")
    if not anahtar or anahtar.endswith(":"):
        return
    konu = konu or {}
    alt = a.get("alt_tur", "")
    notr = alt == "kisi_genel" and bool(a.get("portre") or a.get("hassas_kip"))     # resmî portre ya da hassas haberde onaylanmış
    if alt.startswith("kisi"):
        uygun = ["genel"] + (["hassas"] if notr else [])
        degil = [] if notr else ["hassas (nötr portre olduğu doğrulanmadı)"]
    else:
        uygun = [f"konu: {(konu.get('ne') or {}).get('ad') or konu.get('ana_olay', '')[:60]}"]
        degil = ["konusu farklı haberler (her haberde ayrıca görüntü kontrolü gerekir)"]
    kredi = (a["kaynak"] if a.get("fotografci") == a.get("kaynak") else f"{a.get('fotografci', '')} / {a.get('kaynak', '')}") \
        + (f" ({a['lisans']})" if a.get("lisans") else "")
    kayit_bilgisi = {
        "gorsel": a["url"], "kaynak_sayfa": a.get("sayfa", ""), "fotografci": a.get("fotografci", ""), "lisans": a.get("lisans", ""),
        "dayanak": a.get("lisans_dayanagi", ""), "atif": "Fotoğraf: " + kredi,
        "kimlik": {"tur": alt, "ad": a.get("kisi") or a.get("gosterim_ad") or a.get("hedef", ""), "qid": a.get("qid", "")},
        "cekim": a.get("cekim", ""), "gorsel_turu": alt, "uygun_baglamlar": uygun, "uygun_olmayan_baglamlar": degil,
        "gerekce": f"{a.get('dogrulama', '')}; görsel kontrol: {a.get('gerekce', '')}"[:300], "dogrulama_tarihi": simdi[:10],
        "kural_surumu": KURAL_SURUMU, "kontrol": {"tek": bool(a.get("tek")), "benzerlik": a.get("benzerlik", 0), "iliski": a.get("iliski", 0)},
    }
    kut = bag.arsiv.setdefault(anahtar, [])
    mevcut = next((x for x in kut if isinstance(x, dict) and x.get("url") == a["url"]), None)
    if mevcut:
        mevcut["kullanim"] = mevcut.get("kullanim", 1) + 1
        mevcut["son"] = simdi[:10]
        eski = mevcut.get("arsiv_kaydi") or {}
        if not a.get("onbellekten"):      # yeni bir görsel onayı: bağlamlar birleştirilir
            kayit_bilgisi["uygun_baglamlar"] = sorted(set(eski.get("uygun_baglamlar", [])) | set(uygun))
            kayit_bilgisi["uygun_olmayan_baglamlar"] = [b for b in degil if not any(b.startswith(u) for u in kayit_bilgisi["uygun_baglamlar"])]
            mevcut["arsiv_kaydi"] = kayit_bilgisi
    else:
        kayit = {k: v for k, v in a["kayit"].items() if k not in ("arsiv_kaydi", "onay", "son", "kullanim", "kimlik")}
        kut.append(dict(kayit, onay=simdi[:10], son=simdi[:10], kullanim=1, kimlik=kayit_bilgisi["kimlik"], arsiv_kaydi=kayit_bilgisi))
        del kut[:-30]


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
    if v.get("gorsel") and v.get("gorsel") != secim["gorsel"]:
        # Geri alınabilirlik: değiştirilen görsel ve kaydı (son 3) saklanır
        gecmis = v.get("gorsel_gecmis") if isinstance(v.get("gorsel_gecmis"), list) else []
        gecmis.append({"gorsel": v.get("gorsel", ""), "gorsel_kredi": v.get("gorsel_kredi", ""), "gorsel_tur": v.get("gorsel_tur", ""),
                       "gorsel_konum": v.get("gorsel_konum", ""), "gorsel_secim": v.get("gorsel_secim") or {},
                       "degisim": simdi_tr().isoformat(timespec="seconds")})
        v["gorsel_gecmis"] = gecmis[-3:]
    v["gorsel"] = secim["gorsel"]
    v["gorsel_kredi"] = secim["gorsel_kredi"]
    v["gorsel_tur"] = secim["tur"]
    kayit = {k: secim.get(k, "") for k in ("tur", "kaynak_sayfa", "fotografci", "kredi", "lisans", "lisans_dayanagi", "kosul",
                                           "gosterilen", "cekim_yili", "gerekce", "surum", "konu_imza")}
    kayit["gorsel_url"] = secim["gorsel"]
    kayit["kontrol"] = secim.get("kontrol", {})
    for k in ("aciklama", "alt_tur", "qid", "izin_kontrolu", "kaynak_aciklama"):
        if secim.get(k):
            kayit[k] = secim[k]
    if kf:
        kayit["kurum"] = kf.KURUM_SURUM
        if secim.get("kurum_notu"):
            kayit["kurum_notu"] = secim["kurum_notu"]
    if secim.get("konum"):
        v["gorsel_konum"] = secim["konum"]
    else:
        v.pop("gorsel_konum", None)
    if secim.get("bekliyor"):
        kayit["bekliyor"] = True
        kayit["bekleme_sayisi"] = secim.get("bekleme_sayisi", 1)
    if secim.get("kapak_nedeni"):
        kayit["kapak_nedeni"] = secim["kapak_nedeni"]
    if kayit.get("tur") == "kategori":
        kayit["kapak_surum"] = KAPAK_SURUM
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
    if yerel_gorsel_eksik(v.get("gorsel") or ""):
        return True
    if s.get("tur") == "kategori" and s.get("kapak_surum") != KAPAK_SURUM and not s.get("bekliyor") \
            and (s.get("kapak_nedeni") or {}).get("kod") in ("aday_bulunamadi", "gorsel_reddedildi", "arama_yapilmadi"):
        return True
    if s.get("surum") != KURAL_SURUMU:
        return True
    if s.get("alt_tur") == "yer" and YER_ISTENMEYEN.search(s.get("kaynak_sayfa", "")):   # sonradan daraltılan yer kuralı
        return True
    # Kurum fotoğrafı katmanı eklendi: kapak, temsilî ya da genel yer/kurum fotoğrafı olan haberler bir kez yeniden denenir.
    if kf and s.get("kurum") != kf.KURUM_SURUM and (s.get("tur") in ("kategori", "stok") or s.get("alt_tur") in ("yer", "kurum")):
        return True
    if s.get("konu_imza") != konu_imzasi(v.get("title"), v.get("ozet"), (v.get("kategoriler") or ["Gündem"])[0]):
        return True
    if s.get("bekliyor"):
        if int(s.get("bekleme_sayisi") or 0) >= 4:     # dört denemeden sonra kural sürümü değişene kadar bırakılır
            return False
        son = (s.get("kontrol") or {}).get("tarih", "")
        try:
            return datetime.fromisoformat(son) < simdi_tr() - timedelta(hours=3)
        except Exception:
            return True
    return False


def bekleyen_deneme_yakin(v):
    """Mevcut fotoğrafı korunan haberde (kontrol/arama yapılamadı) son deneme 3 saatten yeniyse ya da 4 kez denendiyse atlanır."""
    b = v.get("gorsel_bekleyen")
    if not isinstance(b, dict) or b.get("surum") != KURAL_SURUMU:
        return False
    if int(b.get("sayi") or 0) >= 4:
        return True
    try:
        return datetime.fromisoformat(b.get("tarih", "")) > simdi_tr() - timedelta(hours=3)
    except Exception:
        return False


def haber_isle(bag, yol, zorla=""):
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
    eski = v.get("gorsel") or ""
    eski_secim = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    eski_bilgi = None
    if yerel_gorsel_eksik(eski):
        print(f"   mevcut görsel dosyası yok ({eski}); yeniden seçilecek")
        eski = ""
    if eski and not eski.startswith("/images/kategori/"):
        # Mevcut fotoğraf da aynı görüntü kontrolünden geçer: yeni fotoğraf doğrulanmadıkça mevcut fotoğraf değiştirilmez;
        # mevcut fotoğraf açıkça uygunsuz bulunursa (red) kaldırılır.
        eski_bilgi = {"url": eski, "kredi": v.get("gorsel_kredi", ""), "alt_tur": eski_secim.get("alt_tur", ""), "secim": eski_secim,
                      "koru": bool(bag.ayar.get("gorsel_stok_koru", True) and eski.startswith("https://images.unsplash.com/"))}
    bag.son_kurum_notu = ""
    secim, rapor = gorsel_sec(bag, h, konu, eski=eski_bilgi)
    eski_karar = (rapor.get("eski") or {}).get("karar")
    if getattr(bag, "son_kurum_notu", ""):
        secim["kurum_notu"] = bag.son_kurum_notu
    if secim["tur"] == "kategori" and not secim.get("bekliyor") and eski_bilgi and eski_karar == "kabul":
        # Yeni fotoğraf bulunamadı ama mevcut fotoğraf bu haber için görüntü kontrolünden geçti: korunur (kapakla değiştirilmez).
        eski_secim = dict(eski_secim, surum=KURAL_SURUMU, konu_imza=secim.get("konu_imza", eski_secim.get("konu_imza", "")),
                          yeniden_kontrol={"tarih": simdi_tr().isoformat(timespec="seconds"), "sonuc": "mevcut fotoğraf onaylandı; korundu",
                                           "gerekce": ((rapor.get("eski") or {}).get("gerekce") or "")[:160],
                                           "yeni_aday_sonucu": (secim.get("kapak_nedeni") or {}).get("kod", "")})
        if not eski_secim.get("gorsel_url"):
            eski_secim["gorsel_url"] = eski
        if zorla:
            eski_secim["zorla"] = zorla
        v["gorsel_secim"] = eski_secim
        v.pop("gorsel_bekleyen", None)
        md_yaz(yol, v, govde)
        return {"tur": "korundu", "gorsel": eski, "alt_tur": eski_secim.get("alt_tur", ""), "aciklama": eski_secim.get("aciklama", ""),
                "kapak_nedeni": secim.get("kapak_nedeni")}, "mevcut fotoğraf yeniden kontrolden geçti; korundu"
    gecersiz = bool(konu and eski_bilgi and not eski_gecerli(eski_bilgi, konu, h))
    if secim.get("bekliyor") and eski and not eski.startswith("/images/kategori/") and secim["tur"] == "kategori" and not gecersiz:
        # Konu ayrıştırılamadı, görsel kontrolü ya da arama yapılamadı (kota/servis): mevcut fotoğraf kategori kapağıyla
        # değiştirilmez. Yalnızca deneme kaydı tutulur; 3 saat sonra (en çok 4 kez) yeniden denenir.
        b = v.get("gorsel_bekleyen") if isinstance(v.get("gorsel_bekleyen"), dict) else {}
        v["gorsel_bekleyen"] = {"surum": KURAL_SURUMU, "tarih": simdi_tr().isoformat(timespec="seconds"),
                                "sayi": (int(b.get("sayi") or 0) + 1) if b.get("surum") == KURAL_SURUMU else 1,
                                "neden": ((secim.get("kapak_nedeni") or {}).get("aciklama") or secim.get("gerekce", ""))[:200]}
        if zorla and isinstance(v.get("gorsel_secim"), dict):
            v["gorsel_secim"]["zorla"] = zorla
        md_yaz(yol, v, govde)
        return None, "kontrol yapılamadı; mevcut görsel korundu"
    # Eski görsel yalnızca eski hattın denetimsiz stok seçimiyse bir daha kullanılmamak üzere yasaklanır. Kimliği doğrulanmış
    # arşiv fotoğrafları (ör. bir kişinin portresi) bir haberde değiştirildi diye başka haberler için yasaklanmaz.
    if (eski and eski != secim["gorsel"] and not isinstance(v.get("gorsel_secim"), dict) and OTOMATIK_HOST.match(eski)
            and "wikimedia.org" not in eski):
        bag.yasakla(eski)
    if secim.get("bekliyor"):
        onceki = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
        secim["bekleme_sayisi"] = (int(onceki.get("bekleme_sayisi") or 0) + 1) if onceki.get("bekliyor") and onceki.get("surum") == KURAL_SURUMU else 1
    v.pop("gorsel_bekleyen", None)
    secimi_uygula(v, secim)
    if zorla:
        v["gorsel_secim"]["zorla"] = zorla       # elle istenen yeniden işleme bir kez yapılır
    md_yaz(yol, v, govde)
    return secim, "düzeltildi"


def toplu_duzelt(bag, limit=3, yalniz_bekleyen=False):
    """Otomatik görselleri küçük gruplar hâlinde düzeltir (yenilerden eskiye).
    yalniz_bekleyen=True: yalnızca kontrolü kota yüzünden ertelenmiş yeni hat kayıtlarını yeniden dener."""
    duzeltilen = 0
    # Kademeli uygulama: bu kural sürümüyle toplam en fazla N haber düzeltilir (0 = sınırsız); sonuçlar incelenip sınır artırılır.
    sinir = int(bag.ayar.get("gorsel_toplu_sinir", 0) or 0)
    sayac = bag.g.setdefault("toplu_sayac", {})
    sure = int(bag.ayar.get("gorsel_sure_dk", 8)) * 60   # bir turun 30 dakikayı aşmaması için toplu düzeltmeye ayrılan süre
    baslangic = time.time()
    tum = sorted(os.listdir(HABER_KLASOR), reverse=True)
    yeniden = [a for a in (bag.ayar.get("gorsel_yeniden") or []) if isinstance(a, str) and a in tum]
    oncelikli = yeniden + [a for a in (bag.ayar.get("gorsel_oncelikli") or []) if isinstance(a, str) and a in tum and a not in yeniden]
    for ad in oncelikli + [a for a in tum if a not in oncelikli]:
        # Yeni gelen haberlerin görsel kontrolü için günlük hakkın bir kısmı her zaman ayrılır (toplu düzeltme onu tüketmez).
        if duzeltilen >= limit or not bag.yz_hakki(2 + int(bag.ayar.get("gorsel_yeni_haber_payi", bag.yz_gunluk // 4))):
            break
        if time.time() - baslangic > sure:
            print("Görsel düzeltme süre sınırına ulaştı; kalan haberler sonraki turda.")
            break
        if not yalniz_bekleyen and bag.kalan_modeller_fn and ad not in yeniden:
            # Eski haberlerin düzeltilmesi, kotası dolmamış SON modeli tüketmez: o model gün sonuna kadar yeni haberlere kalır.
            try:
                kalan_m = bag.kalan_modeller_fn()
            except Exception:
                kalan_m = ["?", "?"]
            if len(kalan_m) <= 1 and getattr(bag, "biten_modeller", None):
                print("Toplu düzeltme durdu: kotası kalan son yapay zekâ modeli yeni haberlere ayrıldı.")
                break
        if not yalniz_bekleyen and sinir and sayac.get(KURAL_SURUMU, 0) >= sinir and ad not in yeniden:
            if not yeniden or ad not in oncelikli:
                print(f"Toplu düzeltme bu sürümde {sinir} haber sınırına ulaştı; inceleme sonrası sınır artırılmalı.")
                break
            continue
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            v, _ = md_oku(yol)
            oto, _ = otomatik_gorsel_mi(v)
            zorla = ad in (bag.ayar.get("gorsel_yeniden") or []) and (v.get("gorsel_secim") or {}).get("zorla") != \
                f"{KURAL_SURUMU}:{bag.ayar.get('gorsel_yeniden_no', 1)}"
            if not oto or (not zorla and (not yeniden_gerekli(v) or bekleyen_deneme_yakin(v))):
                continue
            if yalniz_bekleyen and not (isinstance(v.get("gorsel_secim"), dict) and v["gorsel_secim"].get("bekliyor")):
                continue
            secim, neden = haber_isle(bag, yol, zorla=f"{KURAL_SURUMU}:{bag.ayar.get('gorsel_yeniden_no', 1)}" if zorla else "")
            if secim is None:
                print(f"Görsel düzeltilmedi: {ad} → {neden}")
                if neden.startswith("kontrol yapılamadı") and not bag.yz_hakki(2):
                    print("Yapay zekâ kotası dolu; toplu düzeltme bu tur durduruldu (sayaç artmadı).")
                    break
                continue
            duzeltilen += 1
            if not yalniz_bekleyen:
                if not secim.get("bekliyor"):    # arama/kontrol hatası sınırdan düşmez (sonra yeniden denenir)
                    sayac[KURAL_SURUMU] = sayac.get(KURAL_SURUMU, 0) + 1
                son = bag.g.setdefault("toplu_son", [])
                son.append({"dosya": ad, "tur": (secim or {}).get("tur", neden), "alt_tur": (secim or {}).get("alt_tur", ""),
                            "aciklama": (secim or {}).get("aciklama", ""), "gorsel": (secim or {}).get("gorsel", ""),
                            "kapak_nedeni": ((secim or {}).get("kapak_nedeni") or {}).get("kod", ""), "surum": KURAL_SURUMU,
                            "bekliyor": bool(secim.get("bekliyor")), "tarih": simdi_tr().isoformat(timespec="seconds")})
                del son[:-60]
            print(f"Görsel düzeltildi: {ad} → {secim['tur']} {secim.get('alt_tur', '')}")
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
            satir.update(yeni=secim, eski_degerlendirme=rapor["eski"], adaylar=rapor["adaylar"], notlar=rapor["notlar"],
                         havuz=rapor.get("havuz") or {}, sorgular=rapor.get("sorgular") or [], on_elenen=rapor.get("on_elenen") or [])
            print(f"Deneme: {ad[:60]} → {secim['tur']}")
        except Exception as e:
            satir["hata"] = str(e)[:200]
        satirlar.append(satir)
    return {"tarih": simdi_tr().isoformat(timespec="seconds"), "surum": KURAL_SURUMU, "model": bag.model_fn() if bag.gemini_key else "",
            "dosyalar": dosyalar, "satirlar": satirlar,
            "eksik": sum(1 for x in satirlar if (x.get("yeni") or {}).get("bekliyor")),
            "atlanan_kontroller": bag.notlar[:], "yz_kullanilan": bag.yz_tur}


def _sorgu_kaydet(bag, metin):
    if isinstance(getattr(bag, "sorgu_kaydi", None), list) and len(bag.sorgu_kaydi) < 60:
        bag.sorgu_kaydi.append(metin[:160])


def _kayitli(fn, etiket):
    """Tanı için arama kaydı: hangi kaynakta hangi sorgu yapıldı, kaç sonuç geldi ya da hata oldu."""
    def sar(bag, *a, **k):
        ne = str(a[0]) if a else ""
        try:
            r = fn(bag, *a, **k)
        except Exception as e:
            _sorgu_kaydet(bag, f"{etiket}: {ne} → HATA {str(e)[:70]}")
            raise
        if isinstance(r, dict):
            sonuc = r.get("qid") or "kayıt"
        elif isinstance(r, (list, tuple)):
            sonuc = f"{len(r)} sonuç"
        else:
            sonuc = "yok" if not r else "var"
        _sorgu_kaydet(bag, f"{etiket}: {ne} → {sonuc}")
        return r
    sar.__wrapped__ = fn
    return sar


commons_dosyalar = _kayitli(commons_dosyalar, "Commons arama")
unsplash_ara = _kayitli(unsplash_ara, "Unsplash arama")
kategori_dosyalari = _kayitli(kategori_dosyalari, "Commons kategori")
wikidata_varlik = _kayitli(wikidata_varlik, "Wikidata")
