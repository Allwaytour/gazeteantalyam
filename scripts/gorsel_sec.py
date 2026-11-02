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

SURUM = "g8.1"
BASLANGIC = time.time()
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
OZNE_TURLERI = {"kisi", "kurum", "takim", "yer", "nesne", "olay"}
GIZLI_AD = re.compile(r"(^|\s)[A-ZÇĞİÖŞÜ]\.(\s|$)|^[A-ZÇĞİÖŞÜ]{1,2}\.?\s?[A-ZÇĞİÖŞÜ]{1,2}\.?$")   # "M.B.", "A. K." gibi gizlenmiş adlar
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
            if r.status_code in (500, 502, 503, 504) and deneme == 0:   # model geçici olarak yoğun
                time.sleep(12)
                continue
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
 "ana_kisiler": [{"ad": "...", "rol": "...", "kamusal": true, "hassas_rol": ""}],
 "ana_ozne": {"tur": "kisi | kurum | takim | yer | nesne | olay", "ad": "...", "ad_en": "..."},
 "nerede": {"ad": "...", "ad_en": "..."}, "ne": {"ad": "...", "ad_en": "..."},
 "gorsel_ipucu_en": ["..."], "alternatif_sorgular_en": ["..."],
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


def commons_uygun(d):
    if not SERBEST_LISANS.match(d.get("lisans", "")):
        LISANS_RED.append(d.get("baslik", ""))
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
                            "mekan": guncel_mekan(e.get("claims", {}).get("P115") or [])})
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
                "kayit": d,
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
            notlar.append(f"Wikidata sorgusu yapılamadı ({h['ad']}): {e}")
            continue
        if not v:
            continue
        try:
            dosyalar = _dosya_bilgisi(bag, ["File:" + x for x in v["p18"]]) + (kategori_dosyalari(bag, v["kategori"], alt_kategori=4)[:30] if v["kategori"] else [])
        except Exception as e:
            notlar.append(f"Commons okunamadı ({h['ad']}): {e}")
            continue
        try:
            stat = mekan_gorselleri(bag, v.get("mekan") or [], v["qid"] if h.get("tur") == "takim" else "")
        except Exception as e:
            stat = []
            notlar.append(f"Stat kaydı okunamadı ({h['ad']}): {e}")
        for d, mekan_ad in stat:
            ok, _ = commons_uygun(d)
            if ok and not bag.yasakli_mi(d["url"]):
                adaylar.append({
                    "tur": "arsiv", "alt_tur": "kurum", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]),
                    "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
                    "kosul": commons.get("kosul", ""), "hedef": f"{mekan_ad} ({h['ad']} kulübünün iç saha stadı)", "qid": v["qid"],
                    "cekim": str(tarih_oku(d.get("tarih")) or ""), "kayit": d, "meta_puan": 58, "gosterim_ad": mekan_ad,
                    "dogrulama": f"'{h['ad']}' Wikidata {v['qid']} kaydındaki iç saha (P115) bilgisiyle doğrulandı: {mekan_ad}",
                })
        alinan = 0
        for d in dosyalar:
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
                "meta_puan": 50 + (10 if alinan == 0 else 0),
                "dogrulama": f"'{h['ad']}' Wikidata {v['qid']} kaydı ve Commons kategorisiyle doğrulandı",
            })
            alinan += 1
            if alinan >= 2:
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
        ok, _ = commons_uygun(d)
        metin = d["baslik"] + " " + d["aciklama"][:160]
        # Yalnızca yerin GENEL görünümü: belirli tesis/bina kareleri (havalimanı, stat, hastane…) başka bir yer sanılır.
        if not ok or YER_ISTENMEYEN.search(metin) or not (d in p18 or GENEL_GORUNUM.search(metin)):
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
                        "tur": "arsiv", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]), "sayfa": d["sayfa"],
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
- konu_iliskisi (0-10): Fotoğraf, haberin KONUSUYLA ("{ne}") doğrudan ilişkili olanı mı gösteriyor? Yer/tesis/temsilî fotoğraflarda
  aynı yerde bulunan ama konuyla ilgisiz unsur (şirket tabelası, otopark, rastgele bina, kule) düşük puan alır; konuyla ilgili alan
  (yakıt sızıntısı haberinde apron/uçak operasyonu, otel doluluğu haberinde oteller/otel bölgesi) yüksek puan alır.
- yabanci_unsur: Türkiye dışına ait belirgin unsur var mı? (yabancı dilde tabela, yabancı plaka, "POLICE"/"911" gibi yabancı polis/ambulans işaretleri, bayrak)
- taninabilir_kisi: Yüzü tanınabilir biçimde görünen kişi var mı?
- etkinlik_benzerligi (0-10): Fotoğraftaki sahne türü, haberdeki etkinlik türüne ("{etkinlik}") ne kadar benziyor?
- tek_kisi_odakli: Fotoğrafta iddia edilen kişi tek başına ya da açıkça ön planda mı? (kalabalık zirve/toplu fotoğrafta false)
- gerekce: En fazla 20 kelimelik Türkçe açıklama.
ARŞİV KURALI: "arşiv fotoğrafı" iddiası olan kareler haberde "Arşiv" etiketi ve doğru alt yazıyla (ör. "X, önceki bir ziyaretten — Arşiv")
yayımlanır. Fotoğrafın geçmiş bir tarihe ait olması TEK BAŞINA yanıltıcı ya da habere uygunsuz sayılmaz. Kişi iddialarında kişiyi
nötr biçimde gösteren portre, kürsü, görüşme ya da etkinlik kareleri habere uygundur; yalnızca karedeki pankart, yazı, başka öne çıkan
kişiler ya da belirgin başka bir olay güncel haber hakkında yanlış izlenim yaratıyorsa yanıltıcıdır. Takım/mekân iddialarında
stadın, tesisin ya da binanın genel görünümü uygundur; haberdekinden farklı belirli bir rakip/maç öne çıkıyorsa yanıltıcıdır.
İlçe/şehir ("genel görünümü") iddialarında: o yerin nötr genel görünümü (şehir, sahil, liman, meydan, manzara) habere uygundur.
Karede belirgin bir kişi, belirli bir otel/işletme tabelası ya da başka bir olay (kaza, yangın, tören, gösteri) öne çıkıyorsa
ya da haberin tonuyla açıkça çelişiyorsa (ör. ölüm/kaza haberi için şenlikli bir kare) yanıltıcıdır.
HASSAS HABER (dava, soruşturma, sağlık vb.) kişi iddialarında: kişinin nötr portresi ya da genel arşiv karesi uygundur ve
"Arşiv" olarak sunulur; kelepçe, adliye, gözaltı, hastane gibi güncel olay izlenimi veren ya da kişiyi küçük düşüren kareler yanıltıcıdır.
"temsilî fotoğraf" iddialarında: fotoğraf haberdeki nesneyi/olguyu doğrudan göstermeli (ör. akaryakıt haberinde pompa).
Yabancı tabela, plaka, para birimi, polis/ambulans aracı ya da Türkiye'de olmayan bir sahne varsa yabanci_unsur: true.
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
 "yabanci_unsur": false, "taninabilir_kisi": false, "etkinlik_benzerligi": 0, "tek_kisi_odakli": false, "konu_iliskisi": 0,
 "gerekce": "..."}}]}}"""


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
    if d.get("_alt_tur") in KONU_ILISKISI_SART and int(d.get("konu_iliskisi") or 0) < 6:
        nedenler.append(f"haberin konusuyla doğrudan ilişkili değil (konu ilişkisi {int(d.get('konu_iliskisi') or 0)}/10)")
    if d.get("_alt_tur") == "nesne" and d.get("taninabilir_kisi"):
        nedenler.append("temsilî fotoğrafta tanınabilir kişi var")
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
        if ob:
            a.update(karar=ob["karar"], gerekce=ob.get("gerekce", ""), kontrol_tarih=ob.get("t", ""), onbellekten=True,
                     benzerlik=ob.get("benzerlik", 0), tek=ob.get("tek", False), iliski=ob.get("iliski", 0))
            continue
        try:
            tip, veri = gorsel_indir(a.get("kucuk") or a["url"])
        except Exception as e:
            a.update(karar="red", gerekce=f"görsel indirilemedi: {str(e)[:80]}", kontrol_tarih="")
            continue
        gonderilecek.append(a)
        n = len(gonderilecek)
        kayit = a.get("kayit") or {}
        on_ek = {"gercek": "aynı olayın fotoğrafı: ", "stok": "temsilî fotoğraf: "}.get(a["tur"], "arşiv fotoğrafı: ")
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
        ne=(konu.get("ne") or {}).get("ad") or konu.get("ana_olay", ""))
    yanit = bag.gemini([{"text": metin}] + parcalar, en_fazla=3500)
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
        ilis = int(d.get("konu_iliskisi") or 0)
        a.update(karar=karar, gerekce=gerekce, kontrol_tarih=tarih, benzerlik=benz, tek=tek, iliski=ilis,
                 bulgular={k: d.get(k) for k in ("fotograf_mi", "iddiayla_tutarli", "habere_uygun", "yaniltici", "yabanci_unsur",
                                                  "taninabilir_kisi", "etkinlik_benzerligi", "tek_kisi_odakli", "konu_iliskisi")})
        bag.onbellek_koy(imza(KURAL_SURUMU, konu_imza, a["url"]), {"karar": karar, "gerekce": gerekce, "t": tarih, "benzerlik": benz, "tek": tek,
                                                                 "iliski": ilis})
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
        "konum": "50% 22%" if a.get("dikey") else "",
    }


KURUM_HASSAS = {"suç", "çocuk", "ölüm"}
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


def nesne_adaylari(bag, konu, haber, sorgular=None, gorulen=()):
    """Nesne odaklı genel haberler (akaryakıt, ekmek, elektrik faturası…): konuya doğrudan uygun ücretsiz temsilî fotoğraf.
    Önce Wikimedia Commons (dosya lisansı), sonra Unsplash (Unsplash Lisansı). Görüntü kontrolünde yabancı unsur ve
    tanınabilir kişi içeren kareler elenir; sitede "Temsilî görsel" olarak belirtilir."""
    oz = konu.get("ana_ozne") or {}
    adaylar, notlar = [], []
    if oz.get("tur") != "nesne" or konu.get("hassas", "yok") in NESNE_HASSAS:
        return adaylar, notlar
    sorgu = (sorgular or [oz.get("ad_en") or oz.get("ad")])[0]
    if not sorgu:
        return adaylar, notlar
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if commons and commons.get("izin") is True:
        try:
            for d in commons_dosyalar(bag, sorgu):
                ok, neden = commons_uygun(d)
                if not ok:
                    continue
                if bag.yasakli_mi(d["url"]) or d["url"] in gorulen or not ad_eslesir(sorgu, commons_metni(d)):
                    continue
                adaylar.append({"tur": "stok", "alt_tur": "nesne", "kaynak": "Wikimedia Commons", "url": d["url"],
                                "kucuk": kontrol_kucuk(d["url"], d["genislik"]), "sayfa": d["sayfa"], "fotografci": d["yazar"],
                                "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"], "kosul": commons.get("kosul", ""),
                                "hedef": f"{oz['ad']} (temsilî fotoğraf, olayın kendisi değil)", "cekim": "", "meta_puan": 20, "kayit": d,
                                "dogrulama": f"Commons dosya açıklamasında '{sorgu}' geçiyor; lisans dosya sayfasından okundu"})
                if len(adaylar) >= 1:
                    break
        except Exception as e:
            notlar.append(f"Commons nesne araması yapılamadı: {str(e)[:80]}")
    us = kaynak_izni(bag.izinler, "https://unsplash.com/")
    if UNSPLASH_KEY and us and us.get("izin") is True:
        try:
            for f in unsplash_ara(bag, sorgu):
                if bag.yasakli_mi(f["url"]) or f["url"] in gorulen:
                    continue
                adaylar.append({"tur": "stok", "alt_tur": "nesne", "kaynak": "Unsplash", "url": f["url"], "kucuk": f["kucuk"],
                                "sayfa": f["sayfa"], "fotografci": f["yazar"], "lisans": "Unsplash Lisansı",
                                "lisans_dayanagi": "https://unsplash.com/license", "kosul": us.get("kosul", ""), "indirme": f["indirme"],
                                "hedef": f"{oz['ad']} (temsilî fotoğraf, olayın kendisi değil)", "cekim": "", "meta_puan": 10,
                                "kayit": {"baslik": f["aciklama"], "aciklama": f["aciklama"]},
                                "dogrulama": f"Unsplash araması '{sorgu}'; kullanım hakkı Unsplash Lisansı (fotoğraf sayfası)"})
                if len([a for a in adaylar if a["kaynak"] == "Unsplash"]) >= 2:
                    break
        except Exception as e:
            notlar.append(f"Unsplash araması yapılamadı: {str(e)[:80]}")
    if not adaylar:
        notlar.append(f"'{oz.get('ad')}' için uygun lisanslı temsilî fotoğraf bulunamadı")
    return adaylar, notlar


def yer_konu_adaylari(bag, konu, sorgular=None, en_fazla=2, gorulen=()):
    """NEREDE + NE: haberin geçtiği yerde, konuyla doğrudan ilişkili alanın arşiv fotoğrafı
    (ör. Antalya Havalimanı + yakıt sızıntısı → apron/uçak; Alanya + otel doluluğu → oteller/otel bölgesi).
    Dosyanın kendi kaydında hem YER adı hem KONU ipucu geçmeli; yalnızca şehir/yer eşleşmesi yetmez."""
    yer = konu.get("nerede") or {}
    ipuclari = konu.get("gorsel_ipucu_en") or []
    adaylar, notlar = [], []
    if not (yer.get("ad") and ipuclari) or konu.get("hassas", "yok") in ("ölüm", "çocuk"):
        return adaylar, notlar
    commons = kaynak_izni(bag.izinler, "https://commons.wikimedia.org/")
    if not (commons and commons.get("izin") is True):
        return adaylar, notlar
    yer_en = yer.get("ad_en") or yer["ad"]
    sorgular = sorgular or [f"{yer_en} {ip}" for ip in ipuclari[:2]]
    ip_kok = {kok(w) for ip in ipuclari for w in kelimeler(ip)}
    dosyalar = []
    try:
        v = wikidata_varlik(bag, yer["ad"], insan=False, ipucu="Antalya") or (wikidata_varlik(bag, yer_en, insan=False, ipucu="Antalya")
                                                                              if yer_en != yer["ad"] else None)
        if v and v.get("kategori"):
            dosyalar += kategori_dosyalari(bag, v["kategori"], alt_kategori=6)
    except Exception as e:
        notlar.append(f"'{yer['ad']}' Wikidata/Commons kategorisi okunamadı: {str(e)[:60]}")
    for q in sorgular[:3]:
        try:
            dosyalar += commons_dosyalar(bag, q)
        except Exception as e:
            notlar.append(f"Commons araması yapılamadı ({q}): {str(e)[:60]}")
    seen = set(gorulen)
    for d in dosyalar:
        if not d or d["url"] in seen or bag.yasakli_mi(d["url"]):
            continue
        seen.add(d["url"])
        ok, _ = commons_uygun(d)
        metin = commons_metni(d)
        mk = {kok(w) for w in kelimeler(metin)}
        yer_tutar = ad_eslesir(yer["ad"], metin) or ad_eslesir(yer_en, metin)
        if not ok or not yer_tutar or not (ip_kok & mk):
            continue
        cekim = tarih_oku(d.get("tarih"))
        adaylar.append({
            "tur": "arsiv", "alt_tur": "yer_konu", "kaynak": "Wikimedia Commons", "url": d["url"], "kucuk": kontrol_kucuk(d["url"], d["genislik"]),
            "sayfa": d["sayfa"], "fotografci": d["yazar"], "lisans": d["lisans"], "lisans_dayanagi": d["lisans_url"] or d["sayfa"],
            "kosul": commons.get("kosul", ""), "hedef": f"{yer['ad']} — {', '.join(ipuclari[:2])} (konuyla ilişkili alan, arşiv)",
            "gosterim_ad": yer["ad"], "cekim": str(cekim or ""), "kayit": d, "meta_puan": 60 + (min(10, cekim.year - 2010) if cekim else 0),
            "dogrulama": f"dosya kaydında yer ('{yer['ad']}') ve konu ipucu ({', '.join(ipuclari[:2])}) birlikte geçiyor",
        })
    adaylar.sort(key=lambda a: -a["meta_puan"])
    if not adaylar:
        notlar.append(f"'{yer['ad']}' için konuyla ({', '.join(ipuclari)}) ilişkili lisanslı fotoğraf bulunamadı")
    return adaylar[:en_fazla], notlar


def kapak_nedeni(adaylar, notlar, kontrol_hatasi=""):
    """Kategori kapağı neden kullanıldı? (arama yapılmadı / aday yok / lisans / kimlik / görsel reddedildi / kota-servis)"""
    metin = " ".join(notlar)
    if kontrol_hatasi:
        return {"kod": "kota_veya_servis_hatasi", "aciklama": kontrol_hatasi[:200]}
    if adaylar:
        nedenler = "; ".join(sorted({(a.get("gerekce") or "")[:90] for a in adaylar if a.get("karar") == "red"}))[:300]
        return {"kod": "gorsel_reddedildi", "aciklama": f"{len(adaylar)} aday görüntü kontrolünde elendi: {nedenler}"}
    if "kimliği kesin doğrulanan" in metin:
        return {"kod": "kimlik_eslesmedi", "aciklama": next(n for n in notlar if "kimliği kesin doğrulanan" in n)[:200]}
    if LISANS_RED:
        return {"kod": "lisans_uygun_degil", "aciklama": f"{len(LISANS_RED)} dosyanın lisansı doğrulanamadı (ör. {LISANS_RED[0][:80]})"}
    if "yapılamadı" in metin or "okunamadı" in metin:
        return {"kod": "kota_veya_servis_hatasi", "aciklama": next(n for n in notlar if "yapılamadı" in n or "okunamadı" in n)[:200]}
    if not notlar:
        return {"kod": "arama_yapilmadi", "aciklama": "haberde fotoğrafı aranabilecek kişi, kurum, yer ya da nesne bulunamadı"}
    return {"kod": "aday_bulunamadi", "aciklama": "; ".join(notlar)[:300]}


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
            try:
                os.remove(os.path.join(KOK, "static", b["url"].lstrip("/")))
            except OSError:
                pass
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


def gorsel_sec(bag, haber, konu, eski=None):
    """haber: {baslik, spot, kategori, ilce, tarih(date), kaynak_url, kaynak_gorsel}
    Dönüş: (secim, rapor). Gerçek → arşiv → fotoğrafsız kapak. Başka seçenek yoktur."""
    kimza = konu_imzasi(haber.get("baslik"), haber.get("spot"), haber.get("kategori"))
    rapor = {"notlar": [], "adaylar": [], "eski": None}
    simdi = simdi_tr().isoformat(timespec="seconds")
    del LISANS_RED[:]
    if not konu:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "konu ayrıştırılamadı"}
        s = kapak_secimi(haber.get("kategori"), "Haberin konusu ayrıştırılamadı; fotoğrafsız kapak.", k, kimza)
        s["bekliyor"] = True
        s["kapak_nedeni"] = {"kod": "arama_yapilmadi", "aciklama": "haberin konusu ayrıştırılamadı (yapay zekâ kotası ya da servis hatası)"}
        return s, rapor

    t0 = time.time()
    gercek, n1 = gercek_adaylar(bag, konu, haber)
    kurum_h, n6 = kurum_haber_adaylari(bag, konu, haber)
    gercek = kurum_h + gercek
    n1 = n6 + n1
    kisi, n3 = kisi_adaylari(bag, konu, haber)
    kurum_wd, n4 = kurum_wikidata_adaylari(bag, konu)
    arsiv, n2 = arsiv_adaylari(bag, konu)
    yer, n5 = yer_adaylari(bag, konu, haber)
    nesne, n7 = nesne_adaylari(bag, konu, haber)
    yk, n8 = yer_konu_adaylari(bag, konu)
    rapor["notlar"] += n1 + n3 + n4 + n2 + n5 + n7 + n8
    gercek += [a for a in kisi if a["tur"] == "gercek"]
    kisi = [a for a in kisi if a["tur"] != "gercek"]
    gorulen = {a["url"] for a in gercek + kisi}
    kurum = []
    for a in kurum_wd + arsiv:
        if a["url"] not in gorulen:
            gorulen.add(a["url"])
            kurum.append(dict(a, alt_tur=a.get("alt_tur") or "kurum"))
    kurum = kurum[:3]
    yer = [a for a in yer if a["url"] not in gorulen]
    # Görüntü kontrolü tek istekte yapılır; çok görsel modeli yavaşlatıp 503'e düşürüyor. Katman başına en iyi adaylar gönderilir.
    portre = [a for a in kisi[2:] if a.get("portre")][:1]          # resmî portre her zaman yedek olarak kalır
    gercek, kisi, kurum, yer = gercek[:2], kisi[:2] + portre, kurum[:1], yer[:1]
    yk = [a for a in yk if a["url"] not in gorulen][:2]
    gorulen |= {a["url"] for a in yk}
    nesne = [a for a in nesne if a["url"] not in gorulen][:2] if not (gercek or kisi or kurum or yk) else []
    kurum = kurum + yk
    adaylar = gercek + kisi + kurum + yer + nesne
    eski_aday = None
    if eski and eski.get("url"):
        eski_aday = {"tur": "eski", "kaynak": "mevcut", "url": eski["url"], "kucuk": kontrol_kucuk(eski["url"]),
                     "hedef": "haberin şu anki görseli (eski otomatik seçim)"}
    kontrol_listesi = adaylar + ([eski_aday] if eski_aday else [])

    kontrol_yapildi, hata = False, ""
    t1 = time.time()
    if kontrol_listesi:
        try:
            gorsel_kontrol(bag, haber, konu, kimza, kontrol_listesi)
            kontrol_yapildi = True
        except (KotaHatasi, ServisHatasi) as e:
            hata = str(e)
            rapor["notlar"].append("görsel kontrol yapılamadı: " + hata)
            bag.notlar.append(f"{haber.get('baslik', '')[:60]}: {hata}")
    print(f"   görsel süre: aday toplama {t1 - t0:.0f} sn, kontrol {time.time() - t1:.0f} sn, {len(kontrol_listesi)} görsel"
          + (f" ({hata[:80]})" if hata else ""))
    if eski_aday:
        rapor["eski"] = {k: eski_aday.get(k) for k in ("karar", "gerekce", "bulgular")}
    rapor["adaylar"] = [{k: a.get(k) for k in ("tur", "alt_tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "hedef", "cekim", "karar",
                                               "gerekce", "dogrulama", "benzerlik", "tek", "iliski", "meta_puan")} for a in adaylar]

    if not kontrol_yapildi and adaylar:
        k = {"yapildi": False, "sonuc": "yapılamadı", "tarih": simdi, "yontem": "Gemini görsel kontrol", "neden": hata}
        s = kapak_secimi(haber.get("kategori"), f"Adaylar görsel kontrolden geçirilemedi ({hata}); kontrol edilmemiş fotoğraf kullanılmadı.", k, kimza)
        s["bekliyor"] = True
        s["kapak_nedeni"] = kapak_nedeni(adaylar, rapor["notlar"], hata)
        return s, rapor
    oz_tur = (konu.get("ana_ozne") or {}).get("tur", "")

    def sira(a):
        return aday_sira_anahtari(a, oz_tur)
    kabul = sorted([a for a in gercek + kisi + kurum + yer + nesne if a.get("karar") == "kabul"], key=sira, reverse=True)
    if not kabul and kontrol_yapildi and bag.yz_hakki(1):
        # Reddedilen adaylardan sonra arama hemen bitmez: alternatif sorgularla SINIRLI bir tur daha (aynı kurallarla).
        gorulen |= {a["url"] for a in adaylar}
        ek = []
        alt = konu.get("alternatif_sorgular_en") or []
        if (konu.get("ana_ozne") or {}).get("tur") == "nesne" and alt:
            for q in alt[:2]:
                e, n = nesne_adaylari(bag, konu, haber, sorgular=[q], gorulen=gorulen)
                ek += e[:1]
                gorulen |= {a["url"] for a in e}
        ipl = konu.get("gorsel_ipucu_en") or []
        if (konu.get("nerede") or {}).get("ad") and len(ipl) > 2:
            e, n = yer_konu_adaylari(bag, konu, sorgular=[f"{(konu['nerede'].get('ad_en') or konu['nerede']['ad'])} {ipl[2]}"], gorulen=gorulen)
            ek += e[:1]
        if ek:
            rapor["notlar"].append(f"ilk adaylar elendi; alternatif aramayla {len(ek)} aday daha denendi")
            try:
                gorsel_kontrol(bag, haber, konu, kimza, ek)
                rapor["adaylar"] += [{k: a.get(k) for k in ("tur", "alt_tur", "kaynak", "url", "sayfa", "fotografci", "lisans", "hedef", "cekim",
                                                            "karar", "gerekce", "dogrulama", "benzerlik", "tek", "meta_puan")} for a in ek]
                adaylar = adaylar + ek
                nesne = nesne + [a for a in ek if a.get("alt_tur") == "nesne"]
                kurum = kurum + [a for a in ek if a.get("alt_tur") == "yer_konu"]
                kabul = sorted([a for a in ek if a.get("karar") == "kabul"], key=sira, reverse=True)
            except (KotaHatasi, ServisHatasi) as e:
                rapor["notlar"].append("alternatif arama kontrolü yapılamadı: " + str(e)[:80])
    for a in kurum_h:                # görüntü kontrolünden geçmeyen kurum fotoğrafı sitede tutulmaz
        if a.get("karar") != "kabul" and a["url"].startswith("/images/kurum/"):
            try:
                os.remove(os.path.join(KOK, "static", a["url"].lstrip("/")))
            except OSError:
                pass
    if kabul:
        a = kabul[0]
        if a.get("indirme") and UNSPLASH_KEY:        # Unsplash kullanım koşulu: seçilen fotoğraf için indirme bildirimi
            try:
                requests.get(a["indirme"], headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"}, timeout=10)
            except Exception:
                pass
        kontrol = {"yapildi": True, "sonuc": "onaylandı", "tarih": a.get("kontrol_tarih") or simdi,
                   "yontem": "Wikidata/Commons kimlik doğrulaması + Gemini sahne kontrolü" if a.get("qid") else "kaynak/açıklama doğrulaması + Gemini görsel kontrol",
                   "model": bag.model_fn()}
        if a.get("qid") and a.get("kayit"):
            kut = bag.arsiv.setdefault("kut:" + a["qid"], [])
            mevcut = next((x for x in kut if isinstance(x, dict) and x.get("url") == a["url"]), None)
            if mevcut:
                mevcut["kullanim"] = mevcut.get("kullanim", 1) + 1
            else:
                kut.append(dict(a["kayit"], onay=simdi[:10], kullanim=1))
                del kut[:-30]
        return aday_secimi(a, kontrol, kimza, konu), rapor
    if (eski_aday and eski_aday.get("karar") == "kabul" and eski.get("koru")
            and (konu.get("ana_ozne") or {}).get("tur", konu.get("odak")) not in ("kisi", "kurum", "takim", "yer", "olay")
            and konu.get("hassas", "yok") == "yok"):
        k = {"yapildi": True, "sonuc": "mevcut görsel onaylandı", "tarih": eski_aday.get("kontrol_tarih") or simdi,
             "yontem": "Gemini görsel kontrol", "model": bag.model_fn()}
        return {"tur": "stok", "gorsel": eski["url"], "gorsel_kredi": eski.get("kredi", ""), "kaynak_sayfa": "", "fotografci": "",
                "kredi": eski.get("kredi", ""), "lisans": "Unsplash Lisansı", "lisans_dayanagi": "https://unsplash.com/license", "kosul": "",
                "gosterilen": "Temsilî görsel", "cekim_yili": "", "kontrol": k, "surum": KURAL_SURUMU, "konu_imza": kimza,
                "gerekce": "Haberde kişi/kurum/yer odağı yok ve doğrulanmış gerçek fotoğraf bulunamadı; mevcut temsilî görsel "
                           f"görüntü kontrolünden geçtiği için korundu: {eski_aday.get('gerekce', '')}"}, rapor
    if adaylar:
        k = {"yapildi": True, "sonuc": "adaylar reddedildi", "tarih": simdi, "yontem": "Gemini görsel kontrol", "model": bag.model_fn()}
        neden = f"{len(adaylar)} aday görsel kontrolden geçemedi"
    else:
        k = {"yapildi": False, "sonuc": "aday yok", "tarih": simdi, "yontem": "kaynak/arşiv taraması"}
        neden = "olaya ait ya da haberde adı geçen yere/kuruma ait kullanım hakkı doğrulanmış fotoğraf bulunamadı"
    s = kapak_secimi(haber.get("kategori"), neden.capitalize() + "; fotoğrafsız kapak.", k, kimza)
    s["kapak_nedeni"] = kapak_nedeni(adaylar, rapor["notlar"])
    return s, rapor


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
    for k in ("aciklama", "alt_tur", "qid", "izin_kontrolu"):
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
    if secim.get("kapak_nedeni"):
        kayit["kapak_nedeni"] = secim["kapak_nedeni"]
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
    if s.get("alt_tur") == "yer" and YER_ISTENMEYEN.search(s.get("kaynak_sayfa", "")):   # sonradan daraltılan yer kuralı
        return True
    # Kurum fotoğrafı katmanı eklendi: kapak, temsilî ya da genel yer/kurum fotoğrafı olan haberler bir kez yeniden denenir.
    if kf and s.get("kurum") != kf.KURUM_SURUM and (s.get("tur") in ("kategori", "stok") or s.get("alt_tur") in ("yer", "kurum")):
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
    eski = v.get("gorsel") or ""
    eski_bilgi = None
    if bag.ayar.get("gorsel_stok_koru", True) and eski.startswith("https://images.unsplash.com/"):
        eski_bilgi = {"url": eski, "kredi": v.get("gorsel_kredi", ""), "koru": True}
    bag.son_kurum_notu = ""
    secim, _ = gorsel_sec(bag, h, konu, eski=eski_bilgi)
    if getattr(bag, "son_kurum_notu", ""):
        secim["kurum_notu"] = bag.son_kurum_notu
    if secim.get("bekliyor") and eski_bilgi:
        return None, "kontrol ertelendi; mevcut görsel şimdilik korundu"
    if eski and eski != secim["gorsel"] and not eski.startswith("/images/kategori/") and secim["tur"] != "stok":
        bag.yasakla(eski)
    secimi_uygula(v, secim)
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
    for ad in sorted(os.listdir(HABER_KLASOR), reverse=True):
        # Yeni gelen haberlerin görsel kontrolü için günlük hakkın bir kısmı her zaman ayrılır (toplu düzeltme onu tüketmez).
        if duzeltilen >= limit or not bag.yz_hakki(2 + int(bag.ayar.get("gorsel_yeni_haber_payi", bag.yz_gunluk // 4))):
            break
        if time.time() - baslangic > sure:
            print("Görsel düzeltme süre sınırına ulaştı; kalan haberler sonraki turda.")
            break
        if not yalniz_bekleyen and sinir and sayac.get(KURAL_SURUMU, 0) >= sinir:
            print(f"Toplu düzeltme bu sürümde {sinir} haber sınırına ulaştı; inceleme sonrası sınır artırılmalı.")
            break
        yol = os.path.join(HABER_KLASOR, ad)
        try:
            v, _ = md_oku(yol)
            oto, _ = otomatik_gorsel_mi(v)
            if not oto or not yeniden_gerekli(v):
                continue
            if yalniz_bekleyen and not (isinstance(v.get("gorsel_secim"), dict) and v["gorsel_secim"].get("bekliyor")):
                continue
            secim, neden = haber_isle(bag, yol)
            duzeltilen += 1
            if not yalniz_bekleyen:
                sayac[KURAL_SURUMU] = sayac.get(KURAL_SURUMU, 0) + 1
                son = bag.g.setdefault("toplu_son", [])
                son.append({"dosya": ad, "tur": (secim or {}).get("tur", neden), "alt_tur": (secim or {}).get("alt_tur", ""),
                            "aciklama": (secim or {}).get("aciklama", ""), "gorsel": (secim or {}).get("gorsel", ""),
                            "kapak_nedeni": ((secim or {}).get("kapak_nedeni") or {}).get("kod", ""), "surum": KURAL_SURUMU})
                del son[:-60]
            print(f"Görsel düzeltildi: {ad} → {secim['tur'] if secim else neden}")
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
