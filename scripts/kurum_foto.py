"""Resmî kurumların (belediye, valilik, kaymakamlık, bakanlık, devlet üniversitesi) AYNI HABERE ait kendi fotoğrafları.

Akış:
  1. Google Haberler aramasıyla haberin kurum sitesindeki karşılığı bulunur (yalnızca izin kaydındaki alan adları).
  2. Kurum sayfası açılır (gerekirse tarayıcıyla çizilir), sayfa metni bizim haberle karşılaştırılır.
     Aynı haber değilse fotoğraf alınmaz.
  3. Sayfanın haber fotoğrafı (paylaşım görseli ya da içerikteki en büyük fotoğraf) küçültülüp sitenin kendi
     klasörüne kaydedilir; ardından gorsel_sec içinde yapay zekâ ile görüntü kontrolünden geçer.
Kullanım dayanağı: data/gorsel_izinleri.yml içindeki "Resmî kurumlar" kaydı.
"""
import hashlib
import io
import os
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote, urljoin, urlparse

import requests

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAYIT_KLASOR = os.path.join(KOK, "static", "images", "kurum")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KURUM_SURUM = "k1"

# İzin verilen resmî alan adları (izin kaydıyla birlikte kullanılır)
KURUM_ALAN = re.compile(r"(?i)(^|\.)([a-z0-9\-]+\.bel\.tr|[a-z0-9\-]+-bld\.gov\.tr|[a-z0-9\-]+\.gov\.tr|akdeniz\.edu\.tr|alanya\.edu\.tr)$")
DISLA = re.compile(r"(?i)(^|\.)(turkiye\.gov\.tr|resmigazete\.gov\.tr|ekap\.kik\.gov\.tr|e-devlet)$")
ISTENMEYEN_GORSEL = re.compile(r"(?i)logo|banner|icon|ikon|favicon|sprite|placeholder|baskan|president|avatar|qr|whatsapp|"
                               r"facebook|twitter|instagram|youtube|app-?store|google-?play|bayrak|flag|svg")
DUR = set("ve ile da de ki bu bir için icin olarak olan oldu yeni ilk son çok daha en gibi kadar sonra önce "
          "antalya antalyada antalyanın belediye belediyesi büyükşehir buyuksehir başkan baskan başkanı haber".split())


def _kucult(s):
    s = (s or "").replace("I", "ı").replace("İ", "i").lower()
    return s.translate(str.maketrans("çğıöşüâîû", "cgiosuaiu"))


def anahtar_kelimeler(metin):
    return [w for w in re.findall(r"[a-z0-9]+", _kucult(metin)) if len(w) >= 4 and w not in {_kucult(x) for x in DUR}]


def kok(w):
    return w[:5]


def benzerlik(bizim, onlarin):
    """Bizim başlık+spot anahtar kelimelerinin kaçı kurum sayfasında geçiyor (0-1)."""
    a = {kok(w) for w in anahtar_kelimeler(bizim)}
    if not a:
        return 0.0
    b = {kok(w) for w in anahtar_kelimeler(onlarin)}
    return len(a & b) / len(a)


def izinli_alan(url):
    host = (urlparse(url).hostname or "").lower()
    return bool(host and KURUM_ALAN.search(host) and not DISLA.search(host))


def arama_ifadeleri(haber, konu):
    """Google Haberler için kısa aramalar (önce dar, sonra geniş): özel adlar, sonra başlığın ayırt edici kelimeleri."""
    ozel = " ".join([h["ad"] for h in (konu.get("hedefler") or []) if h.get("ad")][:1])
    baslik = re.sub(r"[\"'’‘“”:;,.!?()]", " ", haber.get("baslik", ""))
    dur = {_kucult(x) for x in DUR}
    kelimeler = [w for w in baslik.split() if len(w) >= 4 and _kucult(w) not in dur and _kucult(w) not in _kucult(ozel)]
    ifadeler = []
    if ozel:
        ifadeler += [" ".join([ozel] + kelimeler[:2]), ozel]
    ifadeler += [" ".join(kelimeler[:4]), " ".join(kelimeler[:3])]
    return [i for i in dict.fromkeys(x.strip() for x in ifadeler) if len(i.split()) >= 2]


def google_ara(ifade, gun=7):
    q = f"{ifade} (site:bel.tr OR site:gov.tr OR site:edu.tr) when:{gun}d"
    r = requests.get("https://news.google.com/rss/search?q=" + quote(q) + "&hl=tr&gl=TR&ceid=TR:tr",
                     headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    sonuc = []
    for m in re.finditer(r"<item>(.*?)</item>", r.text, re.S):
        it = m.group(1)
        link = re.search(r"<link>([^<]+)</link>", it)
        kaynak = re.search(r'<source url="([^"]+)">([^<]*)</source>', it)
        tarih = re.search(r"<pubDate>([^<]+)</pubDate>", it)
        if not (link and kaynak) or not izinli_alan(kaynak.group(1)):
            continue
        try:
            t = parsedate_to_datetime(tarih.group(1)) if tarih else None
        except Exception:
            t = None
        sonuc.append({"link": link.group(1), "kurum": unescape(kaynak.group(2)).strip(), "alan": kaynak.group(1), "tarih": t})
    return sonuc


def cozumle(link):
    if "news.google.com" not in link:
        return link
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(link, interval=1)
        if r.get("success"):
            return r["decoded_url"]
    except Exception:
        pass
    return ""


def _statik_oku(url):
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "tr"}, timeout=20)
    r.raise_for_status()
    html = r.text
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', html, re.I)
    metin = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    metin = unescape(re.sub(r"<[^>]+>", " ", metin))
    return {"metin": re.sub(r"\s+", " ", metin), "og": urljoin(url, og.group(1)) if og else "", "gorseller": []}


_tarayici = None
_pw = None


def _tarayici_al():
    global _tarayici, _pw
    if _tarayici is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        try:
            _tarayici = _pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            _tarayici = _pw.chromium.launch(headless=True)
    return _tarayici


def _cizerek_oku(url):
    """JavaScript ile içerik yükleyen kurum siteleri için: sayfayı çizer, içerikteki büyük fotoğrafları toplar."""
    sayfa = _tarayici_al().new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
    try:
        sayfa.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            sayfa.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        sayfa.mouse.wheel(0, 900)
        time.sleep(1.5)
        veri = sayfa.evaluate("""() => {
            const og = (document.querySelector('meta[property="og:image"]')||{}).content || "";
            const g = [...document.images].map(i => { const r = i.getBoundingClientRect();
                return {src: i.currentSrc || i.src, w: r.width, h: r.height, nw: i.naturalWidth, nh: i.naturalHeight,
                        top: r.top + window.scrollY, alt: i.alt || "", sinif: (i.className || "") + " " + ((i.closest('[class]')||{}).className || "")}; });
            return {og, metin: document.body.innerText || "", gorseller: g};
        }""")
        return veri
    finally:
        sayfa.close()


def kapat():
    global _tarayici, _pw
    for k in (_tarayici, _pw):
        try:
            if k:
                k.close() if k is _tarayici else k.stop()
        except Exception:
            pass
    _tarayici = _pw = None


def haber_fotografi(veri, url):
    """Sayfanın haber fotoğrafı: içerikteki en büyük gerçek fotoğraf; yoksa paylaşım görseli."""
    adaylar = []
    for g in veri.get("gorseller") or []:
        src = g.get("src") or ""
        if not src.startswith("http") or ISTENMEYEN_GORSEL.search(src + " " + str(g.get("sinif")) + " " + str(g.get("alt"))):
            continue
        if g.get("w", 0) < 420 or g.get("h", 0) < 220 or g.get("nw", 0) < 600 or g.get("top", 0) > 2600:
            continue
        adaylar.append((g["w"] * g["h"], src))
    if adaylar:
        return sorted(adaylar, reverse=True)[0][1]
    og = veri.get("og") or ""
    if og and not ISTENMEYEN_GORSEL.search(og):
        return og
    return ""


def kaydet(gorsel_url, sayfa_url):
    """Fotoğrafı indirir, 1280 piksele küçültüp sitenin klasörüne yazar. Dönüş: (site yolu, jpeg bayt)."""
    from PIL import Image
    r = requests.get(gorsel_url, headers={"User-Agent": UA, "Referer": sayfa_url}, timeout=30)
    r.raise_for_status()
    if not r.headers.get("Content-Type", "image/").startswith("image/") or len(r.content) > 25_000_000:
        raise ValueError("görsel değil ya da çok büyük")
    im = Image.open(io.BytesIO(r.content))
    im = im.convert("RGB")
    if im.width < 600 or im.width < im.height * 0.9:
        raise ValueError(f"yatay ve yeterli boyutta değil ({im.width}x{im.height})")
    im.thumbnail((1280, 1280))
    cikti = io.BytesIO()
    im.save(cikti, "JPEG", quality=82, optimize=True, progressive=True)
    veri = cikti.getvalue()
    ad = hashlib.sha1(gorsel_url.encode()).hexdigest()[:16] + ".jpg"
    os.makedirs(KAYIT_KLASOR, exist_ok=True)
    with open(os.path.join(KAYIT_KLASOR, ad), "wb") as f:
        f.write(veri)
    return "/images/kurum/" + ad, veri


def kurum_adaylari(haber, konu, onbellek, en_fazla_sayfa=3):
    """Aynı haberin resmî kurum sayfasındaki fotoğrafı. Dönüş: (aday listesi, notlar)."""
    notlar = []
    ifadeler = arama_ifadeleri(haber, konu)
    if not ifadeler:
        return [], ["kurum fotoğrafı: arama ifadesi çıkarılamadı"]
    anahtar = "kur:" + hashlib.sha1((KURUM_SURUM + haber.get("baslik", "")).encode()).hexdigest()[:12]
    if anahtar in onbellek:
        return [a for a in onbellek[anahtar] if os.path.exists(os.path.join(KOK, "static", a["url"].lstrip("/")))], ["kurum fotoğrafı önbellekten"]
    yas = (datetime.now().date() - haber["tarih"]).days if haber.get("tarih") else 0
    sonuclar, gorulen = [], set()
    for ifade in ifadeler[:3]:
        try:
            for x in google_ara(ifade, gun=min(40, max(7, yas + 4))):
                if x["link"] not in gorulen:
                    gorulen.add(x["link"])
                    sonuclar.append(x)
        except Exception as e:
            notlar.append(f"kurum fotoğrafı araması yapılamadı: {str(e)[:80]}")
        if len(sonuclar) >= 3:
            break
    hedef_tarih = haber.get("tarih")
    bizim = " ".join([haber.get("baslik", ""), haber.get("spot", "")])
    adaylar, bakilan = [], 0
    for s in sonuclar:
        if bakilan >= en_fazla_sayfa or adaylar:
            break
        if hedef_tarih and s["tarih"] and abs((s["tarih"].date() - hedef_tarih).days) > 4:
            continue
        url = cozumle(s["link"])
        if not url or not izinli_alan(url):
            continue
        bakilan += 1
        try:
            veri = _statik_oku(url)
            if benzerlik(bizim, veri["metin"]) < 0.5 or not veri["og"]:
                veri = _cizerek_oku(url)
        except Exception as e:
            notlar.append(f"kurum sayfası okunamadı ({s['kurum']}): {str(e)[:80]}")
            continue
        oran = benzerlik(bizim, veri.get("metin", ""))
        ozel_ok = all(kok(w) in {kok(x) for x in anahtar_kelimeler(veri.get("metin", ""))}
                      for h in (konu.get("hedefler") or [])[:1] for w in anahtar_kelimeler(h.get("ad", "")))
        if oran < 0.5 or (oran < 0.65 and not ozel_ok):
            notlar.append(f"{s['kurum']} sayfası aynı haber değil (benzerlik %{int(oran * 100)})")
            continue
        foto = haber_fotografi(veri, url)
        if not foto:
            notlar.append(f"{s['kurum']} sayfasında haber fotoğrafı yok")
            continue
        try:
            yol, _ = kaydet(foto, url)
        except Exception as e:
            notlar.append(f"{s['kurum']} fotoğrafı alınamadı: {str(e)[:80]}")
            continue
        adaylar.append({"url": yol, "kucuk": yol, "sayfa": url, "kurum": s["kurum"], "alan": s["alan"], "oran": round(oran, 2),
                        "kaynak_gorsel": foto})
    if adaylar:                      # olumsuz sonuç saklanmaz: kurum haberi sonradan yayımlayabilir
        onbellek[anahtar] = adaylar
    if not adaylar and not notlar:
        notlar.append("kurum sitelerinde aynı haber bulunamadı")
    return adaylar, notlar
