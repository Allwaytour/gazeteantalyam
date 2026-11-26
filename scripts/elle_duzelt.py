"""Panelden (Sveltia CMS) elle eklenen haberlerin yayın öncesi düzeltmeleri. Hızlı çalışır (yapay zekâ, ağ isteği yok); panelden
kaydedilen her haberde ve her otomatik turda çalıştırılır.

1) Yayın tarihi boş bırakıldıysa kaydedildiği an yazılır (haber beklemeden yayımlanır).
2) Sayfa adresi: panel, adresi başlıktan Türkçe harfleri bozarak üretiyor ("ü" → "ue", "İ" → "ı": "...tenısı-mıllı-duenya...").
   Doğru adres başlıktan Türkçe kurallarla üretilir ("...tenisi-milli-dunya..."), haberin 'url' alanına yazılır; eski adres
   'aliases' ile yeni adrese yönlendirilir (paylaşılmış bağlantılar çalışmaya devam eder). Bir kez yazılan adres, başlık sonradan
   değişse de değişmez. Haber metnine dokunulmaz.
3) Yayından kaldırılan haber (Düzeltme Politikası: haberler sessizce silinmez): panelde "Yayından kaldır" tarihi (kaldirildi) dolu olan
   haber ve köşe yazısı listelerden, RSS'ten ve site haritasından çıkarılır (build.list: never, sitemap.disable, noindex); adresi
   404 vermez, orada "Bu haber [tarih] tarihinde yayından kaldırılmıştır" notu durur. Kaldırılanların listesi data/kaldirilan.json
   dosyasına yazılır ve /duzeltmeler/ sayfasında gösterilir.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import yaml

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HABER = os.path.join(KOK, "content", "haber")
KLASORLER = ("haber", "kose")          # köşe yazıları da panelden eklenir; adresleri aynı kuralla düzeltilir
TR = timezone(timedelta(hours=3))


def tr_kucuk(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def slug(s, uzunluk=80):
    s = re.sub(r"['’‘`]", "", tr_kucuk(s)).replace("ı", "i")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > uzunluk:
        s = s[:uzunluk].rsplit("-", 1)[0]            # kelime ortasından kesilmez
    return s


ON = re.compile(r"\A---\n(.*?\n)---(\n|\Z)", re.S)


def duzelt():
    degisen = []
    for k in KLASORLER:
        degisen += _duzelt(k)
    try:
        kaldirilanlar()
    except Exception as e:
        print("Kaldırılan haberler işlenemedi:", e)
    return degisen


KALDIRMA_SATIRLARI = (("build:", "build:\n  list: never\n  render: always"), ("sitemap:", "sitemap:\n  disable: true"), ("noindex:", "noindex: true"))


def kaldirilanlar():
    """'kaldirildi' tarihi dolu haber/köşe yazılarını listelerden çıkarır ve data/kaldirilan.json listesini yeniden yazar."""
    liste = []
    for bolum in KLASORLER:
        d = os.path.join(KOK, "content", bolum)
        if not os.path.isdir(d):
            continue
        for ad in sorted(a for a in os.listdir(d) if a.endswith(".md") and not a.startswith("_")):
            yol = os.path.join(d, ad)
            try:
                with open(yol, encoding="utf-8") as f:
                    t = f.read()
                m = ON.match(t)
                if not m or not re.search(r"(?m)^kaldirildi:\s*\S", m.group(1)):
                    continue
                fm = m.group(1)
                v = yaml.safe_load(fm) or {}
            except Exception as e:
                print("Okunamadı:", ad, e)
                continue
            if not v.get("kaldirildi"):
                continue
            ek = [satir for anahtar, satir in KALDIRMA_SATIRLARI if not re.search(rf"(?m)^{anahtar}", fm)]
            if ek:
                yeni = "---\n" + fm + "\n".join(ek) + "\n---" + m.group(2) + t[m.end():]
                yaml.safe_load(ON.match(yeni).group(1))
                with open(yol, "w", encoding="utf-8") as f:
                    f.write(yeni)
                print("Yayından kaldırılan haber listelerden çıkarıldı:", ad)
            tarih = v["kaldirildi"]
            tarih = tarih.isoformat() if hasattr(tarih, "isoformat") else str(tarih)
            liste.append({"baslik": v.get("title", ""), "adres": v.get("url") or f"/{bolum}/{ad[:-3]}/", "bolum": bolum,
                          "yayin": str(v.get("date", "")), "kaldirildi": tarih, "neden": str(v.get("kaldirma_nedeni") or "")})
    liste.sort(key=lambda x: x["kaldirildi"], reverse=True)
    yol = os.path.join(KOK, "data", "kaldirilan.json")
    eski = None
    if os.path.exists(yol):
        with open(yol, encoding="utf-8") as f:
            eski = f.read()
    yeni = json.dumps(liste, ensure_ascii=False, indent=1)
    if yeni != eski:
        with open(yol, "w", encoding="utf-8") as f:
            f.write(yeni)
    return liste


def _duzelt(bolum):
    """Ön bilgi (front matter) metin olarak düzenlenir: yalnızca eksik satırlar eklenir, öteki alanların yazımı değişmez."""
    HABER = os.path.join(KOK, "content", bolum)
    if not os.path.isdir(HABER):
        return []
    simdi = datetime.now(TR).replace(microsecond=0)
    adlar = sorted(a for a in os.listdir(HABER) if a.endswith(".md") and not a.startswith("_"))
    kullanilan = {a[:-3] for a in adlar}
    degisen = []
    for ad in adlar:
        yol = os.path.join(HABER, ad)
        try:
            with open(yol, encoding="utf-8") as f:
                t = f.read()
            m = ON.match(t)
            if not m:
                continue
            fm = m.group(1)
            v = yaml.safe_load(fm) or {}
        except Exception as e:
            print("Okunamadı:", ad, e)
            continue
        if v.get("otomatik"):
            continue
        ek, notlar = [], []
        tarih_gecersiz = isinstance(v.get("date"), str) and not re.match(r"\d{4}-\d{2}-\d{2}", v["date"])
        if not v.get("date") or tarih_gecersiz:
            fm = re.sub(r"(?m)^date:.*\n", "", fm)
            ek.append(f"date: '{simdi.isoformat()}'")
            notlar.append("yayın tarihi yazıldı")
        kok = ad[:-3]
        if not v.get("url") and v.get("title"):
            on = re.match(r"(\d{8})-", kok)
            tarih = on.group(1) if on else simdi.strftime("%Y%m%d")
            dogru = f"{tarih}-{slug(v['title'])}"
            bozuk = slug(kok) != kok or (re.search(r"ue|oe", kok) and not re.search(r"ue|oe", dogru))
            if dogru != kok and bozuk:
                aday, i = dogru, 2
                while aday in kullanilan and aday != kok:
                    aday, i = f"{dogru}-{i}", i + 1
                kullanilan.add(aday)
                eski = [x for x in (v.get("aliases") or []) if x]
                fm = re.sub(r"(?m)^aliases:.*\n(?:\s*-.*\n)*", "", fm)
                ek.append(f"url: /{bolum}/{aday}/")
                ek.append("aliases:\n" + "".join(f"- '{x}'\n" for x in sorted(set(eski + [f'/{bolum}/{kok}/']))).rstrip("\n"))
                notlar.append(f"adres: /{bolum}/{aday}/")
        if ek:
            yeni = "---\n" + fm + "\n".join(ek) + "\n---" + m.group(2) + t[m.end():]
            yaml.safe_load(ON.match(yeni).group(1))          # geçerli YAML olduğu doğrulanır
            with open(yol, "w", encoding="utf-8") as f:
                f.write(yeni)
            degisen.append((ad, notlar))
    for ad, n in degisen:
        print("Elle eklenen haber düzeltildi:", ad, "—", "; ".join(n))
    return degisen


if __name__ == "__main__":
    duzelt()
    sys.exit(0)
