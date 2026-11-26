"""Görsel hattı v2 (b1): haber brief'i → somut İngilizce sorgular → aday fotoğraflar → görüntüye bakan yapay zekâ puanı → eşik.

26.11.2026 incelemesinde bulunan uyumsuz görsellerin koddaki nedeni:
  - Yapay zekâ ile görüntüyü inceleyen hat (gorsel_hat h2) kabul testini geçemediği için yayın kapısı (kaynaklar.yml
    gorsel_yayin.surum) hiç açılmadı; yeni haberler fotoğrafsız yayımlandı.
  - Ardından çalışan gorsel_tamamla (t2) yapay zekâsız, KELİME/ETİKET eşleşmesiyle küçük bir arşivden görsel atadı:
    "bilim" → laboratuvar (mikroskop), "klasik müzik" → orkestra (kemanlar), "denizci/gemi" → polis, "enerji" → güneş paneli,
    "para" → dolar, "müteahhit" → inşaat, "seçim" → kâğıt oy sandığı, "yargı" → hâkim tokmağı, "Alanya" → Alanya limanı.
    Görüntünün kendisine hiç bakılmadı; arşiv kayıtlarının etiketi ile fotoğrafın gerçek içeriği de her zaman örtüşmüyordu
    ("Türk lirası" kaydı dolar gösteriyordu). Arşiv küçük olduğu için aynı kareler tekrar tekrar kullanıldı.

b1 akışı (istisna yok, her haber aynı adımlardan geçer):
  1. Brief: haberi yazan yapay zekâ ana konuyu, görselde olması gereken somut nesne/mekân/eylemi, adı geçen kişi/kurum/mekânları,
     kaçınılacakları ve 2–3 somut İngilizce arama sorgusunu çıkarır (gorsel_brief).
  2. Aday: (1) adı geçen varlık için Wikimedia Commons'ta lisansı uygun gerçek fotoğraf (dosya adı/açıklama/kategoride ad geçmeli),
     (2) Unsplash (ve anahtar tanımlıysa Pexels) aramaları; en çok 10 aday.
  3. Tekrar engeli: son 60 günde kullanılmış görsel kimliği ya da algısal özeti (pHash) çok yakın olan aday elenir.
  4. Puan: adayların küçük kopyaları tek bir Gemini isteğinde haberin başlığı, spotu ve brief'iyle birlikte incelenir; her aday
     0–10 puan, gerekçe, kesin ret kodları ve görselde gerçekte görünenin kısa tarifi (alt metin) alır.
  5. Eşik: puanı 7'nin altında ya da kesin ret kodu olan aday kullanılmaz. Hiçbiri geçmezse logolu başlık kartı kullanılır.
  6. Kayıt: seçilen görselin puanı/gerekçesi ve reddedilen adaylar data/gorsel_kayit.json'a yazılır.
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

SURUM = "b1"
ESIK = 7
TR = timezone(timedelta(hours=3))
KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KULLANIM_DOSYA = os.path.join(KOK, "data", "gorsel_kullanim.json")
KAYIT_DOSYA = os.path.join(KOK, "data", "gorsel_kayit.json")
UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
UA = "GazeteAntalyaBot/1.0 (https://www.gazeteantalyam.com; iletisim@gazeteantalyam.com)"
TEKRAR_GUN = 60
PHASH_ESIK = 10          # 64 bitlik pHash'te en çok bu kadar bit farkı "çok benzer" sayılır
EN_FAZLA_ADAY = 10
UNSPLASH_DURUM = {"kalan": None}   # saatlik Unsplash istek hakkı (X-Ratelimit-Remaining)
UNSPLASH_YEDEK = 0                 # toplu tarama bu kadar hakkı yeni haberlere bırakır

# Haberi yazan yapay zekânın JSON yanıtına eklenen alan (haber_cek.YZ_TALIMAT)
BRIEF_ALANI = """"gorsel_brief": Görsel seçimi için haberin brief'i:
 {"ana_konu": "haberin ana konusu, bir cümle",
  "olmasi_gereken": ["görselde görünmesi gereken SOMUT nesne/mekân/eylem; ör. teleskop, kuru yük gemisi, mahkeme binası, piyano"],
  "varliklar": [{"ad": "haberde adı geçen kişi/kurum/mekân/takım (tam adıyla)", "tur": "kisi|kurum|mekan|takim|etkinlik"}],
  "kacinilacaklar": ["görselde OLMAMASI gerekenler; ör. kâğıt oy sandığı (haber elektronik oy), keman (haber piyano)"],
  "sorgular_en": ["2-3 adet somut İngilizce fotoğraf arama sorgusu; ör. 'telescope stargazing night', 'piano concert stage'"],
  "hassas": false}
 "hassas": ölüm, intihar, cinayet, cinsel suç ya da çocuk mağdur içeren haberde true. Sorgular soyut kavram değil, fotoğrafta
 görülebilecek nesne/sahne olsun; haberdeki olguyla çelişen sahne (elektronik oy için kâğıt sandık) sorgulanmasın."""

HASSAS_RE = re.compile(r"(?i)(öldü|ölü |ölüm|hayatını kaybet|cinayet|öldür|intihar|ceset|cesed|cinsel|istismar|taciz|tecavüz|"
                       r"çocuk|bebek|kazada|yaralı|bıçak|silahl)")

PUAN_TALIMAT = """Sen bir Türk haber sitesinin deneyimli foto editörüsün. Aşağıdaki haber için aday fotoğrafları tek tek incele.

Her aday için:
- "puan": 0-10 tam sayı. 9-10: haberin olayını ya da haberde adı geçen kişi/kurum/mekânı birebir gösteriyor. 7-8: brief'teki somut
  nesne/mekân/eylemi açıkça gösteriyor, okuru yanıltmıyor. 4-6: genel ya da dolaylı ilgili (konuyu anlatmıyor). 0-3: ilgisiz ya da yanıltıcı.
- "ihlaller": aşağıdaki KESİN RET kodlarından uyanlar (yoksa boş liste):
  yanlis_kisi: haberde adı geçen bir kişinin yerine başka/kimliği bilinmeyen tanınabilir bir kişinin yüzü öne çıkıyor
  baska_sehir: Antalya/Türkiye haberinde başka bir şehrin tanınır mekânı (ör. İstanbul Boğazı, Galata Kulesi, yabancı şehir silueti)
  dini_yapi: haberle ilgisi olmayan cami, kilise, tapınak ya da dinî simge
  yabanci_bayrak: Türk bayrağı dışında bayrak (haber o ülkeyle ilgili değilse)
  yabanci_para: Türk lirası dışında para birimi (dolar, euro vb.)
  yabanci_polis: Türk olmayan polis aracı, üniforması ya da yabancı plaka
  hakim_tokmagi: Amerikan tarzı hâkim tokmağı (Türk yargısında kullanılmaz)
  celiski: haberdeki olguyla çelişen sahne (ör. elektronik oy haberi ↔ kâğıt oy sandığı; tren haberi ↔ liman; piyano haberi ↔ keman)
  hassas: ölüm/intihar/cinayet/çocuk haberinde sansasyonel görüntü ya da tanınabilir kişi
  kacinilacak: brief'teki "kaçınılacaklar" listesinden bir unsur görünüyor
  yazi_logo: görselde baskın yazı, filigran ya da ilgisiz marka logosu var
- "taninir_yuz": görselde yüzü tanınabilir bir insan öne çıkıyorsa true
- "gorunen": görselde GERÇEKTE görünenin kısa Türkçe tarifi (alt metin; en çok 110 karakter; haberin başlığını tekrar etme)
- "gerekce": puanın kısa gerekçesi (en çok 25 kelime)

Yalnızca şu biçimde JSON döndür: {"adaylar": [{"no": 1, "puan": 0, "ihlaller": [], "taninir_yuz": false, "gorunen": "...", "gerekce": "..."}]}

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


def brief_duzelt(b, hb=None):
    """Brief'i güvenli biçime getirir. Eksik sorgular haber başlığından türetilmez (Türkçe sorgu stok aramada isabetsiz):
    sorgu yoksa yalnızca Commons varlık araması yapılır."""
    b = b if isinstance(b, dict) else {}
    out = {"ana_konu": temiz(b.get("ana_konu"), 200),
           "olmasi_gereken": [temiz(x, 60) for x in (b.get("olmasi_gereken") or []) if temiz(x, 60)][:5],
           "varliklar": [], "kacinilacaklar": [temiz(x, 80) for x in (b.get("kacinilacaklar") or []) if temiz(x, 80)][:6],
           "sorgular_en": [], "hassas": bool(b.get("hassas"))}
    for v in b.get("varliklar") or []:
        if isinstance(v, dict) and temiz(v.get("ad"), 80):
            out["varliklar"].append({"ad": temiz(v.get("ad"), 80), "tur": temiz(v.get("tur"), 20).lower() or "kurum"})
        elif isinstance(v, str) and temiz(v, 80):
            out["varliklar"].append({"ad": temiz(v, 80), "tur": "kurum"})
    out["varliklar"] = out["varliklar"][:5]
    for q in b.get("sorgular_en") or []:
        q = re.sub(r"[^A-Za-z0-9 '\-]", " ", str(q or ""))
        q = re.sub(r"\s+", " ", q).strip()
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
    """Toplu (eski haberler için) brief isteği. haberler: [{baslik, spot, metin}]."""
    satir = [f"{i}. BAŞLIK: {h.get('baslik', '')}\n   SPOT: {h.get('spot', '')}\n   METİN (giriş): {temiz(h.get('metin'), 600)}"
             for i, h in enumerate(haberler, 1)]
    return BRIEF_TOPLU.replace("<BRIEF>", BRIEF_ALANI.split(":", 1)[1].strip()) + "\n\n".join(satir)


# ----------------------------------------------------------------- görsel kimliği ve tekrar engeli
def gorsel_id(url):
    u = str(url or "")
    m = re.search(r"images\.unsplash\.com/(photo-[A-Za-z0-9_-]+)", u)
    if m:
        return "unsplash:" + m.group(1)
    m = re.search(r"pexels\.com/photos/(\d+)", u)
    if m:
        return "pexels:" + m.group(1)
    m = re.search(r"upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/?#]+)", u)
    if m:
        return "commons:" + requests.utils.unquote(m.group(1))
    return u.split("?")[0]


def phash(veri):
    """64 bitlik algısal özet (pHash; imagehash yoksa Pillow ile fark özeti)."""
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
    """Kullanılmış görsellerin kimlik + pHash kaydı (data/gorsel_kullanim.json). Ayrıca sitedeki haberlerin görselleri okunur."""

    def __init__(self, haber_klasor=None):
        try:
            with open(KULLANIM_DOSYA, encoding="utf-8") as f:
                self.veri = json.load(f)
        except Exception:
            self.veri = {"kayitlar": []}
        self.site = {}                                   # görsel kimliği → en yeni kullanım tarihi (sitedeki haberler)
        if haber_klasor:
            self._site_oku(haber_klasor)

    def _site_oku(self, klasor):
        sinir = (simdi() - timedelta(days=TEKRAR_GUN)).strftime("%Y%m%d")
        for ad in os.listdir(klasor):
            if ad[:8] < sinir:
                continue
            try:
                with open(os.path.join(klasor, ad), encoding="utf-8") as f:
                    bas = f.read(6000)
                m = re.search(r"(?m)^gorsel: ['\"]?([^'\"\n]+)", bas)
                if m and m.group(1).strip() and "/images/kategori/" not in m.group(1) and "/haber-gorsel/" not in m.group(1):
                    self.site.setdefault(gorsel_id(m.group(1).strip()), []).append(ad)
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


# ----------------------------------------------------------------- aday kaynakları
LISANS_UYGUN = re.compile(r"(?i)^(cc0|public domain|pd|cc[ -]by(-sa)?[ -]?\d(\.\d)?|cc[ -]by(-sa)?$)")
COMMONS_DISLA = re.compile(r"(?i)\b(logo|map|harita|seal|coat of arms|arma|flag of|diagram|chart|graph|plan|svg|icon|signature|imza)\b")


def _cm(params, oturum):
    params = dict(params, format="json", formatversion=2)
    r = oturum.get("https://commons.wikimedia.org/w/api.php", params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def commons_varlik(oturum, ad, tur, adet=3):
    """Haberde adı geçen varlığın Commons fotoğrafları. Adın bütün kelimeleri dosya adı/açıklama/kategorilerde geçmelidir."""
    sorgu = f'"{ad}"' if tur == "kisi" else ad
    j = _cm({"action": "query", "generator": "search", "gsrsearch": f"{sorgu} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 12,
             "prop": "imageinfo|categories", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1280, "cllimit": 30}, oturum)
    ad_k = [w for w in re.findall(r"\w+", katla(ad)) if len(w) >= 3 and w not in ("ve", "ile", "the", "of")]
    out = []
    for p in sorted((j.get("query") or {}).get("pages") or [], key=lambda x: x.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        lisans = temiz((md.get("LicenseShortName") or {}).get("value"), 60)
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp") or not LISANS_UYGUN.search(lisans):
            continue
        if (ii.get("width") or 0) < 800 or COMMONS_DISLA.search(p.get("title", "")):
            continue
        kat = " ".join(c.get("title", "") for c in p.get("categories") or [])
        metin = katla(f"{p.get('title', '')} {temiz((md.get('ImageDescription') or {}).get('value'), 500)} {kat}")
        if not ad_k or not all(re.search(rf"\b{re.escape(w)}", metin) for w in ad_k):
            continue
        url = ii.get("thumburl") or ii.get("url")
        out.append({"kaynak": "commons", "id": "commons:" + p["title"].split(":", 1)[-1].replace(" ", "_"), "url": url,
                    "kucuk": re.sub(r"/\d+px-", "/480px-", url) if "/thumb/" in url else url,
                    "sayfa": ii.get("descriptionurl", ""), "fotografci": temiz((md.get("Artist") or {}).get("value"), 80) or "Wikimedia Commons",
                    "lisans": lisans, "aciklama": temiz(f"{p.get('title', '')} — {(md.get('ImageDescription') or {}).get('value', '')}", 250),
                    "varlik": ad, "varlik_tur": tur, "sorgu": f"Commons: {ad}"})
        if len(out) >= adet:
            break
    return out


def unsplash_ara(oturum, sorgu, adet=4):
    if not UNSPLASH_KEY:
        return []
    if UNSPLASH_DURUM["kalan"] is not None and UNSPLASH_DURUM["kalan"] <= UNSPLASH_YEDEK:
        raise RuntimeError(f"Unsplash saatlik hakkı azaldı ({UNSPLASH_DURUM['kalan']})")
    r = oturum.get("https://api.unsplash.com/search/photos", params={"query": sorgu, "per_page": adet, "orientation": "landscape",
                                                                     "content_filter": "high"},
                   headers={"Authorization": f"Client-ID {UNSPLASH_KEY}", "Accept-Version": "v1"}, timeout=20)
    try:
        UNSPLASH_DURUM["kalan"] = int(r.headers.get("X-Ratelimit-Remaining"))
    except (TypeError, ValueError):
        pass
    r.raise_for_status()
    out = []
    for f in r.json().get("results") or []:
        u = f.get("urls") or {}
        if not u.get("regular"):
            continue
        out.append({"kaynak": "unsplash", "id": "unsplash:" + (re.search(r"(photo-[A-Za-z0-9_-]+)", u["regular"]) or re.search(r"(.*)", f.get("id", ""))).group(1),
                    "url": u["regular"], "kucuk": u.get("small") or u["regular"], "sayfa": (f.get("links") or {}).get("html", ""),
                    "indirme": (f.get("links") or {}).get("download_location", ""), "fotografci": (f.get("user") or {}).get("name", "Unsplash"),
                    "lisans": "Unsplash Lisansı", "aciklama": temiz(f"{f.get('description') or ''} {f.get('alt_description') or ''}", 200),
                    "sorgu": sorgu})
    return out


def pexels_ara(oturum, sorgu, adet=4):
    if not PEXELS_KEY:
        return []
    r = oturum.get("https://api.pexels.com/v1/search", params={"query": sorgu, "per_page": adet, "orientation": "landscape"},
                   headers={"Authorization": PEXELS_KEY}, timeout=20)
    r.raise_for_status()
    return [{"kaynak": "pexels", "id": f"pexels:{f['id']}", "url": (f.get("src") or {}).get("large2x") or (f.get("src") or {}).get("large"),
             "kucuk": (f.get("src") or {}).get("medium"), "sayfa": f.get("url", ""), "fotografci": f.get("photographer", "Pexels"),
             "lisans": "Pexels Lisansı", "aciklama": temiz(f.get("alt"), 200), "sorgu": sorgu}
            for f in r.json().get("photos") or [] if (f.get("src") or {}).get("medium")]


def adaylari_topla(oturum, brief, notlar):
    """Kaynak önceliği: (1) adı geçen varlığın Commons fotoğrafı, (2) Unsplash/Pexels konu sorguları. Sorgular dönüşümlü sıralanır."""
    out, gorulen = [], set()

    def ekle(l):
        for a in l:
            if a["id"] not in gorulen and a.get("url") and a.get("kucuk"):
                gorulen.add(a["id"])
                out.append(a)
    for v in brief.get("varliklar", [])[:3]:
        if v["tur"] in ("kisi", "kurum", "mekan", "takim"):
            try:
                ekle(commons_varlik(oturum, v["ad"], v["tur"], 3))
            except Exception as e:
                notlar.append(f"Commons araması yapılamadı ({v['ad']}): {e}"[:160])
    stok = []
    for i, q in enumerate(brief.get("sorgular_en", [])[:3]):
        l = []
        # Unsplash saatlik kotası düşük (50/saat): ilk iki sorgu Unsplash'te, hepsi Pexels'te (anahtar tanımlıysa) aranır
        for fn in ((unsplash_ara, pexels_ara) if i < 2 else (pexels_ara,)):
            try:
                l += fn(oturum, q, 4)
            except Exception as e:
                notlar.append(f"{fn.__name__} hatası ({q}): {e}"[:160])
        stok.append(l)
    for i in range(8):                                   # sorgular arasında dönüşümlü (her sorgudan adaylar)
        for l in stok:
            if i < len(l):
                ekle([l[i]])
    return out


def kucuk_indir(oturum, a):
    r = oturum.get(a["kucuk"], timeout=25, headers={"User-Agent": UA})
    r.raise_for_status()
    from PIL import Image
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    a["boyut_kucuk"] = list(im.size)
    im.thumbnail((512, 512))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


# ----------------------------------------------------------------- görüntü puanlama
def puan_istegi(hb, brief, adaylar):
    bilgi = (f"BAŞLIK: {hb.get('baslik', '')}\nSPOT: {hb.get('spot', '')}\nKATEGORİ: {hb.get('kategori', '')}  İLÇE: {hb.get('ilce', '') or '-'}\n"
             f"BRIEF: {json.dumps(brief, ensure_ascii=False)}\n\nADAYLAR (sırayla):")
    parcalar = [{"text": PUAN_TALIMAT + bilgi}]
    for i, (a, veri) in enumerate(adaylar, 1):
        kim = f"; Commons dosyası, '{a.get('varlik')}' adıyla eşleşti" if a["kaynak"] == "commons" else ""
        parcalar.append({"text": f"Aday {i} (kaynak: {a['kaynak']}{kim}; dosya açıklaması: {a.get('aciklama', '')[:160]})"})
        parcalar.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(veri).decode()}})
    return parcalar


def karar(a, p, brief, hb):
    """Kesin kurallar: eşik, ihlal kodları, hassas haberde tanınabilir kişi, kişi haberinde stok fotoğrafta tanınabilir yüz."""
    nedenler = []
    puan = int(p.get("puan") or 0) if str(p.get("puan", "")).strip().lstrip("-").isdigit() else 0
    ihlal = [str(x) for x in (p.get("ihlaller") or []) if str(x).strip()]
    if puan < ESIK:
        nedenler.append(f"puan {puan} < {ESIK}")
    if ihlal:
        nedenler.append("kesin ret: " + ", ".join(ihlal))
    if (brief.get("hassas") or HASSAS_RE.search(f"{hb.get('baslik', '')} {hb.get('spot', '')}")) and p.get("taninir_yuz"):
        nedenler.append("hassas haberde tanınabilir kişi")
    kisi_haberi = any(v["tur"] == "kisi" for v in brief.get("varliklar", []))
    if a["kaynak"] != "commons" and p.get("taninir_yuz") and kisi_haberi:
        nedenler.append("kişi haberinde stok fotoğrafta tanınabilir yüz (adı geçen kişi sanılır)")
    return puan, nedenler


def sec(hb, brief, gemini_fn, kullanim, haber_adi="", oturum=None):
    """Haber için görsel seçer. gemini_fn(parcalar) → JSON. Dönüş: (seçilen aday ya da None, kayıt).
    kayit: {surum, brief, secilen{...puan, gerekce}, reddedilen[...], notlar, sonuc}."""
    oturum = oturum or requests.Session()
    oturum.headers.setdefault("User-Agent", UA)
    notlar = []
    kayit = {"surum": SURUM, "tarih": simdi().isoformat(timespec="seconds"), "haber": haber_adi, "baslik": hb.get("baslik", ""),
             "brief": brief, "reddedilen": [], "notlar": notlar}
    adaylar = adaylari_topla(oturum, brief, notlar)
    kayit["aday_sayisi"] = len(adaylar)
    incelenecek = []
    for a in adaylar:
        if len(incelenecek) >= EN_FAZLA_ADAY:
            break
        try:
            veri = kucuk_indir(oturum, a)
            a["phash"] = phash(veri)
        except Exception as e:
            kayit["reddedilen"].append({"id": a["id"], "url": a["url"], "neden": f"indirilemedi: {e}"[:120]})
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
    oncelik = {"commons": 2, "unsplash": 1, "pexels": 0}
    for i, (a, _) in enumerate(incelenecek, 1):
        p = puanlar.get(i) or {}
        puan, nedenler = karar(a, p, brief, hb)
        a.update(puan=puan, gerekce=temiz(p.get("gerekce"), 200), gorunen=temiz(p.get("gorunen"), 120),
                 ihlaller=p.get("ihlaller") or [], taninir_yuz=bool(p.get("taninir_yuz")))
        if not p:
            nedenler.append("puanlanmadı")
        if nedenler:
            kayit["reddedilen"].append({"id": a["id"], "url": a["url"], "kaynak": a["kaynak"], "sorgu": a.get("sorgu", ""), "puan": puan,
                                        "gerekce": a["gerekce"], "neden": "; ".join(nedenler)})
        elif not en_iyi or (puan, oncelik[a["kaynak"]]) > (en_iyi["puan"], oncelik[en_iyi["kaynak"]]):
            if en_iyi:
                kayit["reddedilen"].append({"id": en_iyi["id"], "url": en_iyi["url"], "kaynak": en_iyi["kaynak"], "puan": en_iyi["puan"],
                                            "gerekce": en_iyi["gerekce"], "neden": "daha yüksek puanlı aday seçildi"})
            en_iyi = a
        else:
            kayit["reddedilen"].append({"id": a["id"], "url": a["url"], "kaynak": a["kaynak"], "puan": puan, "gerekce": a["gerekce"],
                                        "neden": "daha yüksek puanlı aday seçildi"})
    if en_iyi:
        kayit["secilen"] = {k: en_iyi.get(k) for k in ("id", "url", "kaynak", "sorgu", "puan", "gerekce", "gorunen", "fotografci", "lisans", "sayfa")}
        kayit["sonuc"] = f"seçildi ({en_iyi['puan']}/10)"
    else:
        kayit["sonuc"] = f"eşiği ({ESIK}) geçen aday yok → başlık kartı"
    return en_iyi, kayit


def unsplash_indirme_bildir(a):
    """Unsplash API kuralı: kullanılan fotoğraf için indirme uç noktası tetiklenir."""
    if a.get("kaynak") == "unsplash" and a.get("indirme") and UNSPLASH_KEY:
        try:
            requests.get(a["indirme"], headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"}, timeout=15)
        except Exception:
            pass


def secim_kaydi(a, konu_imza=""):
    """Seçilen adayı gorsel_sec.secimi_uygula biçimine çevirir (onaylı; alt metin = görselde görünen)."""
    kaynak_ad = {"commons": "Wikimedia Commons", "unsplash": "Unsplash", "pexels": "Pexels"}[a["kaynak"]]
    if a["kaynak"] == "commons":
        kredi = f"Arşiv fotoğrafı: {a.get('fotografci', '')} / Wikimedia Commons ({a.get('lisans', '')})"
        tur, aciklama = "arsiv", f"{a.get('varlik', '')} — Arşiv fotoğrafı"
    else:
        kredi = f"Temsili fotoğraf: {a.get('fotografci', '')} / {kaynak_ad}"
        tur, aciklama = "stok", "Temsilî fotoğraf"
    return {"tur": tur, "gorsel": a["url"], "gorsel_kredi": kredi, "durum": "onaylandi", "kaynak_sayfa": a.get("sayfa", ""),
            "fotografci": a.get("fotografci", ""), "lisans": a.get("lisans", ""),
            "lisans_dayanagi": f"{kaynak_ad} lisansı (API kaydı)", "gosterilen": a.get("gorunen") or a.get("aciklama", "")[:110],
            "aciklama": aciklama, "gerekce": f"Görsel puanı {a.get('puan')}/10: {a.get('gerekce', '')}", "hat": SURUM, "surum": SURUM,
            "konu_imza": konu_imza, "kredi_etiket": "Fotoğraf",
            "kontrol": {"yapildi": True, "sonuc": "uygun", "puan": a.get("puan"), "esik": ESIK, "gerekce": a.get("gerekce", ""),
                        "sorgu": a.get("sorgu", ""), "phash": a.get("phash", ""), "tarih": simdi().isoformat(timespec="seconds")},
            "onay": {"yontem": f"görüntü incelemesi (Gemini), puan ≥ {ESIK}, kesin ret kuralları", "tarih": simdi().isoformat(timespec="seconds")}}


def kayit_yaz(kayitlar):
    """Haber bazında görsel seçim günlüğü (son 600 haber)."""
    try:
        with open(KAYIT_DOSYA, encoding="utf-8") as f:
            eski = json.load(f)
    except Exception:
        eski = []
    eski = (eski if isinstance(eski, list) else []) + kayitlar
    with open(KAYIT_DOSYA, "w", encoding="utf-8") as f:
        json.dump(eski[-600:], f, ensure_ascii=False, indent=1)
