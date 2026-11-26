"""Görsel hattı v2 (b2): haber brief'i → kurum fotoğrafı / Pixabay / Wikimedia Commons (kamu malı, CC0) → görüntü puanı → eşik.

26.11.2026 incelemesinde bulunan uyumsuz görsellerin koddaki nedeni:
  - Görüntüye bakan yapay zekâ hattı (gorsel_hat h2) kabul testini geçemediği için yayın kapısı hiç açılmadı.
  - Ardından çalışan gorsel_tamamla (t2) yapay zekâsız, KELİME/ETİKET eşleşmesiyle küçük bir arşivden görsel atadı ("bilim" →
    mikroskop, "klasik müzik" → keman, "para" → dolar, "seçim" → kâğıt sandık…); görüntünün kendisine bakılmadı, arşiv küçük olduğu
    için aynı kareler tekrar tekrar kullanıldı.

b2 kuralları (site sahibinin 26.11.2026 kararları):
  - Kaynaklar, öncelik sırasıyla: (a) haberin kurumunun kendi resmî sitesinde AYNI habere ait yayımladığı fotoğraf (kurum_foto.py;
    ajans kredisi/filigranı olan reddedilir), (b) Pixabay (API; görsel sitenin klasörüne indirilir, kalıcı hotlink yok; API yanıtları
    24 saat önbellekte; 60 saniyede 100 istek sınırı), (c) Wikimedia Commons yalnızca CC0 ve dünya genelinde/Türkiye'de geçerli
    kamu malı şablonlarıyla (dosya sayfasındaki lisans şablonu API'den okunur; ad/açıklamaya bakılmaz). Unsplash/Pexels kullanılmaz.
  - Her aday görüntüsüne bakılarak 0–10 puanlanır (dönem kontrolü dahil); 7'nin altı ya da kesin ret kodu olan kullanılmaz.
  - Uygun görsel yoksa haber YAYIMLANMAZ (haber_cek kuyruğa alır: 2, 6, 12 saat sonra yeniden aranır, sonra iptal).
  - Görsel altında fotoğrafçı/kaynak gösterilmez ("Temsilî fotoğraf" / "Arşiv fotoğrafı, [yıl]" kalır); kaynak sayfa, sahip, lisans ve
    alınma tarihi ön bilgide gorsel_kanit alanında saklanır (telif talebinde kanıt).
  - Son 60 günde kullanılmış ya da algısal özeti (pHash) çok yakın görsel kullanılmaz; her kararın günlüğü tutulur.
"""
import base64
import hashlib
import html
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

SURUM = "b2"
ESIK = 7
TR = timezone(timedelta(hours=3))
KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KULLANIM_DOSYA = os.path.join(KOK, "data", "gorsel_kullanim.json")
KAYIT_DOSYA = os.path.join(KOK, "data", "gorsel_kayit.json")
PIXABAY_ONBELLEK = os.path.join(KOK, "data", "pixabay_onbellek.json")
CIKTI = os.path.join(KOK, "static", "images", "haber-gorsel")
WEB = "/images/haber-gorsel/"
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
UA = "GazeteAntalyaBot/1.0 (https://www.gazeteantalyam.com; iletisim@gazeteantalyam.com)"
TEKRAR_GUN = 60
PHASH_ESIK = 10
EN_FAZLA_ADAY = 10
PIXABAY_ISTEK = []          # son 60 saniyedeki istek zamanları (100/60 sn sınırı)

BRIEF_ALANI = """"gorsel_brief": Görsel seçimi için haberin brief'i:
 {"ana_konu": "haberin ana konusu, bir cümle",
  "olmasi_gereken": ["görselde görünmesi gereken SOMUT nesne/mekân/eylem; ör. teleskop, kuru yük gemisi, mahkeme binası, piyano"],
  "varliklar": [{"ad": "haberde adı geçen kişi/kurum/mekân/takım (tam adıyla)", "tur": "kisi|kurum|mekan|takim|etkinlik"}],
  "kacinilacaklar": ["görselde OLMAMASI gerekenler; ör. kâğıt oy sandığı (haber elektronik oy), keman (haber piyano)"],
  "sorgular_en": ["Pixabay için 2-3 somut İngilizce fotoğraf sorgusu; ör. 'telescope night sky', 'grand piano concert'"],
  "sorgular_tr": ["Wikimedia Commons için 2-3 Türkçe sorgu; özel adlarla; ör. 'Antalya Bilim Merkezi', 'Alanya Kalesi'"],
  "tarihi_haber": false, "hassas": false}
 "tarihi_haber": haber tarih, kültür mirası ya da yıldönümü haberiyse true (yalnızca o zaman eski/arşiv fotoğrafı kullanılabilir).
 "hassas": ölüm, intihar, cinayet, cinsel suç ya da çocuk mağdur içeren haberde true. Sorgular soyut kavram değil, fotoğrafta
 görülebilecek nesne/sahne olsun; haberdeki olguyla çelişen sahne (elektronik oy için kâğıt sandık) sorgulanmasın."""

HASSAS_RE = re.compile(r"(?i)(öldü|ölü |ölüm|hayatını kaybet|cinayet|öldür|intihar|ceset|cesed|cinsel|istismar|taciz|tecavüz|"
                       r"çocuk|bebek|kazada|yaralı|bıçak|silahl)")
AJANS_RE = re.compile(r"(?i)\b(aa|anadolu ajans[ıi]|[iİ]ha|ihlas haber|dha|demirören haber|anka|getty|reuters|afp|ap photo|"
                      r"shutterstock|associated press|epa)\b")

PUAN_TALIMAT = """Sen bir Türk haber sitesinin deneyimli foto editörüsün. Aşağıdaki haber için aday fotoğrafları tek tek incele.

Her aday için:
- "puan": 0-10 tam sayı. 9-10: haberin olayını ya da haberde adı geçen kişi/kurum/mekânı birebir gösteriyor. 7-8: brief'teki somut
  nesne/mekân/eylemi açıkça gösteriyor, okuru yanıltmıyor. 4-6: genel ya da dolaylı ilgili. 0-3: ilgisiz ya da yanıltıcı.
- "ihlaller": aşağıdaki KESİN RET kodlarından uyanlar (yoksa boş liste):
  yanlis_kisi: haberde adı geçen bir kişinin yerine başka/kimliği bilinmeyen tanınabilir bir kişinin yüzü öne çıkıyor
  baska_sehir: Antalya/Türkiye haberinde başka bir şehrin tanınır mekânı (ör. İstanbul Boğazı, Galata Kulesi, yabancı şehir silueti)
  dini_yapi: haberle ilgisi olmayan cami, kilise, tapınak ya da dinî simge
  yabanci_bayrak: Türk bayrağı dışında bayrak (haber o ülkeyle ilgili değilse)
  yabanci_para: Türk lirası dışında para birimi (dolar, euro vb.)
  yabanci_polis: Türk olmayan polis aracı, üniforması ya da yabancı plaka
  hakim_tokmagi: Amerikan tarzı hâkim tokmağı (Türk yargısında kullanılmaz)
  yabanci_kurum: Türkiye haberinde tanınır biçimde yabancı bir meclis/parlamento salonu, hükümet binası, mahkeme ya da resmî kurum (ör. Avrupa Parlamentosu, ABD Kongresi)
  celiski: haberdeki olguyla çelişen sahne (ör. elektronik oy ↔ kâğıt oy sandığı; tren ↔ liman; piyano ↔ keman)
  hassas: ölüm/intihar/cinayet/çocuk haberinde sansasyonel görüntü ya da tanınabilir kişi
  kacinilacak: brief'teki "kaçınılacaklar" listesinden bir unsur görünüyor
  ajans: haber ajansı (AA, İHA, DHA, Getty, Reuters, AFP vb.) filigranı, logosu ya da fotoğrafçı kredisi var
  yazi_logo: görselde baskın yazı, filigran ya da ilgisiz marka logosu var
- "donem": "guncel" (son yıllarda çekilmiş görünüyor) ya da "eski" (siyah-beyaz, eski araç/giyim, arşiv baskısı vb.)
- "tahmini_yil": eski ise tahmini çekim yılı (bilmiyorsan boş)
- "taninir_yuz": görselde yüzü tanınabilir bir insan öne çıkıyorsa true
- "gorunen": görselde GERÇEKTE görünenin kısa Türkçe tarifi (alt metin; en çok 110 karakter; haberin başlığını tekrar etme)
- "gerekce": puanın kısa gerekçesi (en çok 25 kelime)

Yalnızca şu biçimde JSON döndür: {"adaylar": [{"no": 1, "puan": 0, "ihlaller": [], "donem": "guncel", "tahmini_yil": "", "taninir_yuz": false, "gorunen": "...", "gerekce": "..."}]}

HABER
"""


# ----------------------------------------------------------------- yardımcılar
def simdi():
    return datetime.now(TR)


def temiz(s, n=300):
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    return re.sub(r"\s+", " ", s).strip()[:n]


_KAT = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def katla(s):
    return (s or "").replace("İ", "i").replace("I", "i").lower().translate(_KAT).replace("ı", "i")


def _liste(b, k, n, en=60):
    return [temiz(x, en) for x in (b.get(k) or []) if isinstance(x, str) and temiz(x, en)][:n]


def brief_duzelt(b, hb=None):
    b = b if isinstance(b, dict) else {}
    out = {"ana_konu": temiz(b.get("ana_konu"), 200), "olmasi_gereken": _liste(b, "olmasi_gereken", 5),
           "varliklar": [], "kacinilacaklar": _liste(b, "kacinilacaklar", 6, 80), "sorgular_en": [], "sorgular_tr": _liste(b, "sorgular_tr", 3, 80),
           "tarihi_haber": bool(b.get("tarihi_haber")), "hassas": bool(b.get("hassas"))}
    for v in b.get("varliklar") or []:
        if isinstance(v, dict) and temiz(v.get("ad"), 80):
            out["varliklar"].append({"ad": temiz(v.get("ad"), 80), "tur": temiz(v.get("tur"), 20).lower() or "kurum"})
        elif isinstance(v, str) and temiz(v, 80):
            out["varliklar"].append({"ad": temiz(v, 80), "tur": "kurum"})
    out["varliklar"] = out["varliklar"][:5]
    for q in b.get("sorgular_en") or []:
        q = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 '\-]", " ", str(q or ""))).strip()
        if 3 <= len(q) <= 80 and q.lower() not in [x.lower() for x in out["sorgular_en"]]:
            out["sorgular_en"].append(q)
    out["sorgular_en"] = out["sorgular_en"][:3]
    if hb and HASSAS_RE.search(f"{hb.get('baslik', '')} {hb.get('spot', '')}"):
        out["hassas"] = True
    return out


BRIEF_TOPLU = """Aşağıdaki her haber için görsel seçiminde kullanılacak brief'i çıkar.
<BRIEF>
Yalnızca şu biçimde JSON döndür: {"1": {brief}, "2": {brief}, ...}

HABERLER
"""


def brief_istegi(haberler):
    satir = [f"{i}. BAŞLIK: {h.get('baslik', '')}\n   SPOT: {h.get('spot', '')}\n   METİN (giriş): {temiz(h.get('metin'), 600)}"
             for i, h in enumerate(haberler, 1)]
    return BRIEF_TOPLU.replace("<BRIEF>", BRIEF_ALANI.split(":", 1)[1].strip()) + "\n\n".join(satir)


# ----------------------------------------------------------------- görsel kimliği ve tekrar engeli
def gorsel_id(url):
    u = str(url or "")
    m = re.search(r"images\.unsplash\.com/(photo-[A-Za-z0-9_-]+)", u)
    if m:
        return "unsplash:" + m.group(1)
    m = re.search(r"pixabay[-_:](\d+)", u)
    if m:
        return "pixabay:" + m.group(1)
    m = re.search(r"upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/?#]+)", u)
    if m:
        return "commons:" + requests.utils.unquote(m.group(1))
    return u.split("?")[0]


def phash(veri):
    from PIL import Image
    im = Image.open(io.BytesIO(veri)).convert("RGB")
    try:
        import imagehash
        return str(imagehash.phash(im))
    except ImportError:
        g = im.convert("L").resize((9, 8))
        p = list(g.getdata())
        bits = "".join("1" if p[r * 9 + c] > p[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
        return f"{int(bits, 2):016x}"


def hash_uzaklik(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


class Kullanim:
    """Kullanılmış görsellerin kimlik + pHash kaydı (data/gorsel_kullanim.json) ve sitedeki haberlerin görselleri."""

    def __init__(self, haber_klasor=None):
        try:
            with open(KULLANIM_DOSYA, encoding="utf-8") as f:
                self.veri = json.load(f)
        except Exception:
            self.veri = {"kayitlar": []}
        self.site = {}
        if haber_klasor:
            self._site_oku(haber_klasor)

    def _site_oku(self, klasor):
        sinir = (simdi() - timedelta(days=TEKRAR_GUN)).strftime("%Y%m%d")
        for ad in os.listdir(klasor):
            if ad[:8] < sinir:
                continue
            try:
                with open(os.path.join(klasor, ad), encoding="utf-8") as f:
                    bas = f.read(8000)
                for m in re.finditer(r"(?m)^(?:gorsel|  kimlik): ['\"]?([^'\"\n]+)", bas):
                    g = m.group(1).strip()
                    if g and "/images/kategori/" not in g:
                        self.site.setdefault(gorsel_id(g), []).append(ad)
            except Exception:
                pass

    def yakin(self, gun=TEKRAR_GUN):
        sinir = (simdi() - timedelta(days=gun)).isoformat()
        return [k for k in self.veri.get("kayitlar", []) if k.get("tarih", "") >= sinir]

    def tekrar_mi(self, gid, ph, haric_haber=""):
        for ad in self.site.get(gid, []):
            if ad != haric_haber:
                return f"aynı görsel son {TEKRAR_GUN} günde kullanıldı ({ad[:40]})"
        for k in self.yakin():
            if k.get("haber") == haric_haber:
                continue
            if k.get("id") == gid:
                return f"aynı görsel son {TEKRAR_GUN} günde kullanıldı ({str(k.get('haber'))[:40]})"
            if ph and k.get("phash") and hash_uzaklik(ph, k["phash"]) <= PHASH_ESIK:
                return f"son {TEKRAR_GUN} günde kullanılan bir görsele çok benzer ({str(k.get('haber'))[:40]}, pHash farkı {hash_uzaklik(ph, k['phash'])})"
        return ""

    def ekle(self, gid, ph, haber):
        self.veri.setdefault("kayitlar", []).append({"id": gid, "phash": ph, "tarih": simdi().isoformat(timespec="seconds"), "haber": haber})
        self.site.setdefault(gid, []).append(haber)
        sinir = (simdi() - timedelta(days=TEKRAR_GUN + 30)).isoformat()
        self.veri["kayitlar"] = [k for k in self.veri["kayitlar"] if k.get("tarih", "") >= sinir]

    def kaydet(self):
        os.makedirs(os.path.dirname(KULLANIM_DOSYA), exist_ok=True)
        with open(KULLANIM_DOSYA, "w", encoding="utf-8") as f:
            json.dump(self.veri, f, ensure_ascii=False, indent=0)


# ----------------------------------------------------------------- (a) kurum fotoğrafı
def kurum_adaylari(hb, brief, notlar, onbellek):
    try:
        import kurum_foto as kf
    except Exception as e:
        notlar.append(f"kurum fotoğrafı modülü yüklenemedi: {e}"[:120])
        return []
    hedef = [{"ad": v["ad"]} for v in brief.get("varliklar", []) if v["tur"] in ("kurum", "takim", "mekan")]
    try:
        l, n = kf.kurum_adaylari({"baslik": hb.get("baslik", ""), "spot": hb.get("spot", ""), "tarih": hb.get("tarih")},
                                 {"hedefler": hedef}, onbellek)
    except Exception as e:
        notlar.append(f"kurum fotoğrafı araması hatası: {e}"[:160])
        return []
    notlar += [str(x)[:160] for x in n][:4]
    out = []
    for a in l:
        yol = os.path.join(KOK, "static", a["url"].lstrip("/"))
        out.append({"kaynak": "kurum", "id": "kurum:" + a["url"], "url": a["url"], "yerel": yol, "sayfa": a.get("sayfa", ""),
                    "sahip": a.get("kurum", ""), "lisans": "Kurumun kendi resmî yayını (data/gorsel_izinleri.yml kaydı)",
                    "orijinal": a.get("kaynak_gorsel", ""), "aciklama": f"{a.get('kurum', '')} sayfasındaki haber fotoğrafı",
                    "sorgu": "kurum sitesi", "lisans_kaniti": a.get("kendi_fotografi", "")})
    return out


# ----------------------------------------------------------------- (b) Pixabay
def _pixabay_onbellek():
    try:
        with open(PIXABAY_ONBELLEK, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _pixabay_hiz():
    """60 saniyede en çok 100 istek (güvenlik payıyla 90)."""
    t = time.time()
    while PIXABAY_ISTEK and PIXABAY_ISTEK[0] < t - 60:
        PIXABAY_ISTEK.pop(0)
    if len(PIXABAY_ISTEK) >= 90:
        time.sleep(61 - (t - PIXABAY_ISTEK[0]))
    PIXABAY_ISTEK.append(time.time())


def pixabay_ara(oturum, sorgu, adet=5):
    if not PIXABAY_KEY:
        return []
    ob = _pixabay_onbellek()
    k = sorgu.lower().strip()
    kayit = ob.get(k)
    if kayit and kayit.get("ts", 0) > time.time() - 86400:          # API yanıtı 24 saat önbellekte
        sonuc = kayit["sonuc"]
    else:
        _pixabay_hiz()
        r = oturum.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": sorgu[:100], "image_type": "photo",
                                                           "orientation": "horizontal", "safesearch": "true", "per_page": 10,
                                                           "min_width": 1000, "lang": "en"}, timeout=20)
        r.raise_for_status()
        sonuc = [{"id": h["id"], "buyuk": h.get("largeImageURL"), "kucuk": h.get("webformatURL"), "sayfa": h.get("pageURL"),
                  "sahip": h.get("user", ""), "etiket": h.get("tags", "")} for h in r.json().get("hits") or []]
        ob = {x: y for x, y in ob.items() if y.get("ts", 0) > time.time() - 86400}
        ob[k] = {"ts": int(time.time()), "sonuc": sonuc}
        os.makedirs(os.path.dirname(PIXABAY_ONBELLEK), exist_ok=True)
        with open(PIXABAY_ONBELLEK, "w", encoding="utf-8") as f:
            json.dump(ob, f, ensure_ascii=False)
    return [{"kaynak": "pixabay", "id": f"pixabay:{h['id']}", "url": h["buyuk"], "kucuk": h["kucuk"], "sayfa": h["sayfa"],
             "sahip": h["sahip"], "lisans": "Pixabay İçerik Lisansı", "orijinal": h["buyuk"], "aciklama": temiz(h.get("etiket"), 120),
             "sorgu": sorgu, "lisans_kaniti": "Pixabay API kaydı (pixabay.com/service/license-summary/)"}
            for h in sonuc[:adet] if h.get("buyuk") and h.get("kucuk")]


# ----------------------------------------------------------------- (c) Wikimedia Commons (yalnızca CC0 / kamu malı)
IZINLI_SABLON = re.compile(r"(?i)^(template:)?(cc-zero|cc0(-[\w.]+)?|pd-self|pd-author|pd-old(-\d+)?(-[\w-]+)?|pd-turkey|pd-trgov|"
                           r"pd-tr(-\w+)?|pd-gov-turkey|pd-ineligible|pd-shape|pd-textlogo|pd-user(-\w+)?|pd-release)$")
YASAK_SABLON = re.compile(r"(?i)^(template:)?(cc-by(-sa)?(-[\w.]+)?|gfdl[\w-]*|pd-us[\w-]*|pd-usgov[\w-]*|fal|attribution[\w-]*|"
                          r"cc-sa[\w.-]*|copyrighted[\w-]*|non-free[\w-]*)$")
COMMONS_DISLA = re.compile(r"(?i)\b(logo|map|harita|seal|coat of arms|arma|flag of|diagram|chart|graph|plan|svg|icon|signature|imza)\b")


def commons_lisans(sablonlar):
    """Dosya sayfasındaki şablonlardan lisans kararı. Dönüş: (uygun mu, gerekçe)."""
    ad = [str(s).split(":", 1)[-1] for s in sablonlar]
    yasak = [s for s in ad if YASAK_SABLON.match(s)]
    izinli = [s for s in ad if IZINLI_SABLON.match(s)]
    if not izinli:
        return False, "CC0/kamu malı (dünya geneli ya da Türkiye) şablonu yok" + (f"; şablonlar: {', '.join(yasak[:3])}" if yasak else "")
    if any(re.match(r"(?i)cc-by", s) or re.match(r"(?i)gfdl", s) for s in yasak):
        return False, f"atıf isteyen lisans da var ({', '.join(yasak[:3])})"
    return True, "lisans şablonu: " + ", ".join(izinli[:3])


def _cm(oturum, params):
    params = dict(params, format="json", formatversion=2)
    r = oturum.get("https://commons.wikimedia.org/w/api.php", params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def commons_ara(oturum, sorgu, notlar, adet=4, ad_sart=""):
    j = _cm(oturum, {"action": "query", "generator": "search", "gsrsearch": f"{sorgu} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 15,
                     "prop": "imageinfo|templates", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1280, "tlnamespace": 10,
                     "tllimit": "max"})
    ad_k = [w for w in re.findall(r"\w+", katla(ad_sart)) if len(w) >= 3 and w not in ("ve", "ile", "the", "of")]
    out, red = [], 0
    for p in sorted((j.get("query") or {}).get("pages") or [], key=lambda x: x.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp") or (ii.get("width") or 0) < 800 or COMMONS_DISLA.search(p.get("title", "")):
            continue
        ok, neden = commons_lisans([t.get("title", "") for t in p.get("templates") or []])
        if not ok:
            red += 1
            continue
        metin = katla(f"{p.get('title', '')} {temiz((md.get('ImageDescription') or {}).get('value'), 500)}")
        if ad_k and not all(re.search(rf"\b{re.escape(w)}", metin) for w in ad_k):
            continue
        tarih = temiz((md.get("DateTimeOriginal") or {}).get("value"), 40)
        yil = (re.search(r"(1[89]\d\d|20\d\d)", tarih) or re.search(r"(.*)", "")).group(1)
        url = ii.get("thumburl") or ii.get("url")
        out.append({"kaynak": "commons", "id": "commons:" + p["title"].split(":", 1)[-1].replace(" ", "_"), "url": url,
                    "kucuk": re.sub(r"/\d+px-", "/500px-", url) if "/thumb/" in url else url, "sayfa": ii.get("descriptionurl", ""),
                    "sahip": temiz((md.get("Artist") or {}).get("value"), 80) or "Wikimedia Commons",
                    "lisans": temiz((md.get("LicenseShortName") or {}).get("value"), 60) or "Kamu malı", "lisans_kaniti": neden,
                    "orijinal": ii.get("url", ""), "aciklama": temiz(f"{p.get('title', '')} — {(md.get('ImageDescription') or {}).get('value', '')}", 250),
                    "cekim_yili": yil, "sorgu": f"Commons: {sorgu}"})
        if len(out) >= adet:
            break
    if red:
        notlar.append(f"Commons '{sorgu}': {red} dosya lisansı uygun olmadığı için elendi (CC BY/BY-SA, ABD kamu malı vb.)")
    return out


def adaylari_topla(oturum, hb, brief, notlar, onbellek):
    out, gorulen = [], set()

    def ekle(l):
        for a in l:
            if a["id"] not in gorulen and a.get("url"):
                gorulen.add(a["id"])
                out.append(a)
    ekle(kurum_adaylari(hb, brief, notlar, onbellek))                          # (a) kurum
    px = []
    for q in brief.get("sorgular_en", [])[:3]:                                  # (b) Pixabay
        try:
            px.append(pixabay_ara(oturum, q, 5))
        except Exception as e:
            notlar.append(f"Pixabay hatası ({q}): {e}"[:160])
    for i in range(5):
        for l in px:
            if i < len(l):
                ekle([l[i]])
    cm = []
    for v in brief.get("varliklar", [])[:2]:                                    # (c) Commons: adı geçen varlık, sonra Türkçe sorgular
        if v["tur"] in ("kurum", "mekan", "takim", "kisi"):
            try:
                cm.append(commons_ara(oturum, v["ad"], notlar, 3, ad_sart=v["ad"]))
            except Exception as e:
                notlar.append(f"Commons hatası ({v['ad']}): {e}"[:160])
    for q in brief.get("sorgular_tr", [])[:2]:
        try:
            cm.append(commons_ara(oturum, q, notlar, 3))
        except Exception as e:
            notlar.append(f"Commons hatası ({q}): {e}"[:160])
    for i in range(3):
        for l in cm:
            if i < len(l):
                ekle([l[i]])
    return out


def kucuk_indir(oturum, a):
    if a["kaynak"] == "kurum":
        with open(a["yerel"], "rb") as f:
            veri = f.read()
    else:
        r = oturum.get(a["kucuk"], timeout=25, headers={"User-Agent": UA})
        r.raise_for_status()
        veri = r.content
    from PIL import Image
    im = Image.open(io.BytesIO(veri))
    exif = ""
    try:
        e = im.getexif()
        exif = " ".join(str(e.get(k, "")) for k in (315, 33432, 270))       # Artist, Copyright, ImageDescription
    except Exception:
        pass
    im = im.convert("RGB")
    im.thumbnail((512, 512))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue(), exif


def kalici_kaydet(oturum, a):
    """Seçilen görsel sitenin kendi klasörüne indirilir (Pixabay kalıcı hotlink yasağı; Commons da yerelde tutulur)."""
    if a["kaynak"] == "kurum":
        return a["url"]
    from PIL import Image
    r = oturum.get(a["url"], timeout=40, headers={"User-Agent": UA})
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((1600, 1600))
    ad = re.sub(r"[^a-z0-9]+", "-", a["id"].lower())[:60].strip("-") + "-" + hashlib.sha1(a["url"].encode()).hexdigest()[:8] + ".jpg"
    os.makedirs(CIKTI, exist_ok=True)
    im.save(os.path.join(CIKTI, ad), "JPEG", quality=84, optimize=True, progressive=True)
    return WEB + ad


# ----------------------------------------------------------------- görüntü puanlama ve karar
def puan_istegi(hb, brief, adaylar):
    bilgi = (f"BAŞLIK: {hb.get('baslik', '')}\nSPOT: {hb.get('spot', '')}\nKATEGORİ: {hb.get('kategori', '')}  İLÇE: {hb.get('ilce', '') or '-'}\n"
             f"BRIEF: {json.dumps(brief, ensure_ascii=False)}\n\nADAYLAR (sırayla):")
    parcalar = [{"text": PUAN_TALIMAT + bilgi}]
    for i, (a, veri) in enumerate(adaylar, 1):
        ek = {"kurum": f"; {a.get('sahip')} resmî sitesindeki aynı haberin fotoğrafı", "commons": f"; Commons dosyası, çekim yılı {a.get('cekim_yili') or '?'}",
              "pixabay": "; Pixabay stok fotoğrafı"}[a["kaynak"]]
        parcalar.append({"text": f"Aday {i} (kaynak: {a['kaynak']}{ek}; açıklama: {a.get('aciklama', '')[:160]})"})
        parcalar.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(veri).decode()}})
    return parcalar


# Başka şehrin tanınır yeri (§3): görsel tarifinde, Pixabay etiketlerinde ya da sayfa adresinde geçen ve haberde geçmeyen il/şehir/simge adı.
# Ad, soyad ya da sıradan kelimeyle karışan iller (Aydın, Karaman, Van, Batman, Ordu, Ağrı) listede yok.
BASKA_YER = """istanbul boğaz boğaziçi bosphorus galata haliç golden-horn ayasofya hagia sultanahmet kız-kulesi maiden's-tower taksim eminönü ortaköy
karaköy kadıköy üsküdar beşiktaş kapadokya cappadocia göreme pamukkale efes ephesus anıtkabir nemrut sümela ankara izmir bursa
eskişehir konya kayseri adana mersin hatay antakya gaziantep şanlıurfa diyarbakır mardin trabzon rize samsun erzurum malatya
elazığ sivas tokat çorum yozgat kastamonu zonguldak sakarya kocaeli izmit çanakkale tekirdağ edirne kırklareli balıkesir manisa
denizli muğla bodrum marmaris fethiye datça çeşme isparta burdur afyon afyonkarahisar kütahya uşak bilecik niğde nevşehir kırşehir
kırıkkale aksaray kahramanmaraş osmaniye adıyaman erzincan gümüşhane giresun artvin kars ardahan iğdır bitlis muş siirt şırnak
hakkari tunceli bingöl bayburt sinop amasya çankırı karabük bartın düzce bolu yalova kilis
paris london londra new-york manhattan roma rome venedik venice berlin dubai tokyo moskova moscow washington eiffel big-ben prag
prague amsterdam barselona barcelona madrid lizbon lisbon atina athens budapeşte budapest viyana vienna santorini bali singapur
singapore hong-kong şanghay shanghai los-angeles san-francisco las-vegas chicago miami brüksel brussels strazburg strasbourg
cenevre geneva zürih zurich münih munich milano milan floransa florence napoli naples kahire cairo tahran tehran bakü baku tiflis
tbilisi kiev kyiv varşova warsaw stockholm oslo kopenhag copenhagen dublin edinburgh""".split()


def _yer_katla(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", katla(s)).strip() + " "


BASKA_YER_K = sorted({_yer_katla(x.replace("-", " ")) for x in BASKA_YER}, key=len, reverse=True)


def baska_yer(a, p, brief, hb):
    """Görselde başka bir şehrin/ülkenin tanınır yeri görünüyor mu? Haberde (başlık, spot, metin, brief) geçen yer adları hariç."""
    gorsel = _yer_katla(" ".join(str(x or "") for x in (p.get("gorunen"), p.get("gerekce"), a.get("aciklama"), a.get("sayfa"), a.get("baslik"))))
    haber = _yer_katla(" ".join([str(hb.get(k) or "") for k in ("baslik", "spot", "metin")] +
                                [str(brief.get("ana_konu") or "")] + [str(v.get("ad") or "") for v in brief.get("varliklar") or []] +
                                [str(x) for x in (brief.get("sorgular_tr") or [])]))
    out = []
    for y in BASKA_YER_K:
        if y in gorsel and y not in haber and not any(y in o for o in out):
            out.append(y)
    return [y.strip() for y in out]


def karar(a, p, brief, hb):
    nedenler = []
    puan = int(p.get("puan") or 0) if str(p.get("puan", "")).strip().lstrip("-").isdigit() else 0
    ihlal = [str(x) for x in (p.get("ihlaller") or []) if str(x).strip()]
    if puan < ESIK:
        nedenler.append(f"puan {puan} < {ESIK}")
    if ihlal:
        nedenler.append("kesin ret: " + ", ".join(ihlal))
    yer = baska_yer(a, p, brief, hb)
    if yer:
        nedenler.append("kesin ret: baska_sehir (" + ", ".join(yer) + ")")
    if (brief.get("hassas") or HASSAS_RE.search(f"{hb.get('baslik', '')} {hb.get('spot', '')}")) and p.get("taninir_yuz"):
        nedenler.append("hassas haberde tanınabilir kişi")
    if a.get("kaynak") != "kurum" and p.get("taninir_yuz") and any(v["tur"] == "kisi" for v in brief.get("varliklar", [])):
        nedenler.append("kişi haberinde stok/arşiv fotoğrafta tanınabilir yüz (adı geçen kişi sanılır)")
    eski = str(p.get("donem", "")).lower().startswith("eski") or (a.get("cekim_yili") and a["cekim_yili"] < str(simdi().year - 15))
    if eski and not brief.get("tarihi_haber"):
        nedenler.append("güncel haberde eski/arşiv fotoğrafı")
    if AJANS_RE.search(a.get("exif", "")):
        nedenler.append(f"görsel üst verisinde ajans kredisi ({temiz(a['exif'], 60)})")
    return puan, nedenler


def sec(hb, brief, gemini_fn, kullanim, haber_adi="", oturum=None, onbellek=None):
    """Haber için görsel seçer. Dönüş: (seçilen aday ya da None, kayıt)."""
    oturum = oturum or requests.Session()
    oturum.headers.setdefault("User-Agent", UA)
    notlar = []
    kayit = {"surum": SURUM, "tarih": simdi().isoformat(timespec="seconds"), "haber": haber_adi, "baslik": hb.get("baslik", ""),
             "brief": brief, "reddedilen": [], "notlar": notlar}
    adaylar = adaylari_topla(oturum, hb, brief, notlar, onbellek if onbellek is not None else {})
    kayit["aday_sayisi"] = len(adaylar)
    incelenecek = []
    for a in adaylar:
        if len(incelenecek) >= EN_FAZLA_ADAY:
            break
        try:
            veri, a["exif"] = kucuk_indir(oturum, a)
            a["phash"] = phash(veri)
        except Exception as e:
            kayit["reddedilen"].append({"id": a["id"], "url": a["url"], "kaynak": a["kaynak"], "neden": f"indirilemedi: {e}"[:120]})
            continue
        t = kullanim.tekrar_mi(a["id"], a["phash"], haber_adi)
        if t:
            kayit["reddedilen"].append({"id": a["id"], "url": a["url"], "kaynak": a["kaynak"], "neden": t})
            continue
        incelenecek.append((a, veri))
    if not incelenecek:
        kayit["sonuc"] = "aday yok" if not adaylar else "adayların hepsi elendi (tekrar/indirme)"
        return None, kayit
    try:
        yanit = gemini_fn(puan_istegi(hb, brief, incelenecek))
    except Exception as e:
        kayit["sonuc"] = f"görüntü puanlaması yapılamadı: {e}"[:200]
        kayit["hata"] = True
        return None, kayit
    puanlar = {int(p.get("no", 0)): p for p in (yanit or {}).get("adaylar") or [] if str(p.get("no", "")).isdigit()}
    en_iyi = None
    oncelik = {"kurum": 2, "pixabay": 1, "commons": 0}
    for i, (a, _) in enumerate(incelenecek, 1):
        p = puanlar.get(i) or {}
        puan, nedenler = karar(a, p, brief, hb)
        a.update(puan=puan, gerekce=temiz(p.get("gerekce"), 200), gorunen=temiz(p.get("gorunen"), 120), ihlaller=p.get("ihlaller") or [],
                 taninir_yuz=bool(p.get("taninir_yuz")), donem=temiz(p.get("donem"), 10),
                 cekim_yili=a.get("cekim_yili") or temiz(p.get("tahmini_yil"), 10))
        if not p:
            nedenler.append("puanlanmadı")
        satir = {"id": a["id"], "url": a.get("kucuk") if a["kaynak"] != "kurum" else a["url"], "kaynak": a["kaynak"], "sorgu": a.get("sorgu", ""),
                 "puan": puan, "gerekce": a["gerekce"]}
        if nedenler:
            kayit["reddedilen"].append(dict(satir, neden="; ".join(nedenler)))
        elif not en_iyi or (puan, oncelik[a["kaynak"]]) > (en_iyi["puan"], oncelik[en_iyi["kaynak"]]):
            if en_iyi:
                kayit["reddedilen"].append({"id": en_iyi["id"], "url": en_iyi.get("kucuk") or en_iyi["url"], "kaynak": en_iyi["kaynak"],
                                            "puan": en_iyi["puan"], "gerekce": en_iyi["gerekce"], "neden": "daha yüksek puanlı aday seçildi"})
            en_iyi = a
        else:
            kayit["reddedilen"].append(dict(satir, neden="daha yüksek puanlı aday seçildi"))
    if en_iyi:
        try:
            en_iyi["yerel_url"] = kalici_kaydet(oturum, en_iyi)
        except Exception as e:
            kayit["sonuc"] = f"seçilen görsel indirilemedi: {e}"[:200]
            return None, kayit
        kayit["secilen"] = {k: en_iyi.get(k) for k in ("id", "yerel_url", "kaynak", "sorgu", "puan", "gerekce", "gorunen", "sahip", "lisans", "sayfa",
                                                       "taninir_yuz", "cekim_yili")}
        kayit["secilen"]["onizleme"] = en_iyi.get("kucuk") if en_iyi["kaynak"] != "kurum" else en_iyi["url"]
        kayit["sonuc"] = f"seçildi ({en_iyi['puan']}/10, {en_iyi['kaynak']})"
    else:
        kayit["sonuc"] = f"eşiği ({ESIK}) geçen uygun görsel yok"
    return en_iyi, kayit


def secim_kaydi(a, konu_imza="", tarihi=False):
    """Seçilen adayı gorsel_sec.secimi_uygula biçimine çevirir. Sitede kaynak/fotoğrafçı gösterilmez: görsel altında yalnızca
    "Temsilî fotoğraf" ya da "Arşiv fotoğrafı, [yıl]" yazar (kurumun aynı habere ait fotoğrafında hiçbir şey yazmaz).
    Kanıt (kaynak sayfa, sahip, lisans, alınma tarihi) 'kanit' alanında ön bilgiye yazılır."""
    if a["kaynak"] == "kurum":
        tur, alt_yazi = "gercek", ""
    elif tarihi and str(a.get("donem", "")).startswith("eski"):
        tur, alt_yazi = "arsiv", "Arşiv fotoğrafı" + (f", {a['cekim_yili']}" if a.get("cekim_yili") else "")
    else:
        tur, alt_yazi = "stok", "Temsilî fotoğraf"
    simdi_ = simdi().isoformat(timespec="seconds")
    return {"tur": tur, "gorsel": a.get("yerel_url") or a["url"], "gorsel_kredi": alt_yazi, "durum": "onaylandi",
            "gosterilen": a.get("gorunen") or a.get("aciklama", "")[:110], "aciklama": "", "fotografci": "", "kaynak_sayfa": "",
            "gerekce": f"Görsel puanı {a.get('puan')}/10: {a.get('gerekce', '')}", "hat": SURUM, "surum": SURUM, "konu_imza": konu_imza,
            "kontrol": {"yapildi": True, "sonuc": "uygun", "puan": a.get("puan"), "esik": ESIK, "gerekce": a.get("gerekce", ""), "sorgu": a.get("sorgu", ""),
                        "tarih": simdi_},
            "kanit": {"kaynak": a["kaynak"], "sayfa": a.get("sayfa", ""), "sahip": a.get("sahip", ""), "lisans": a.get("lisans", ""),
                      "lisans_kaniti": a.get("lisans_kaniti", ""), "orijinal_url": a.get("orijinal") or a["url"], "alinma_tarihi": simdi_,
                      "kimlik": a["id"], "phash": a.get("phash", ""), "puan": a.get("puan")}}


def kayit_yaz(kayitlar):
    try:
        with open(KAYIT_DOSYA, encoding="utf-8") as f:
            eski = json.load(f)
    except Exception:
        eski = []
    eski = (eski if isinstance(eski, list) else []) + kayitlar
    with open(KAYIT_DOSYA, "w", encoding="utf-8") as f:
        json.dump(eski[-600:], f, ensure_ascii=False, indent=1)
