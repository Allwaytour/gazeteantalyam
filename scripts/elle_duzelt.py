"""Panelden (Sveltia CMS) elle eklenen haberlerin yayın öncesi düzeltmeleri. Hızlı çalışır (yapay zekâ, ağ isteği yok); panelden
kaydedilen her haberde ve her otomatik turda çalıştırılır.

1) Yayın tarihi boş bırakıldıysa kaydedildiği an yazılır (haber beklemeden yayımlanır).
2) Sayfa adresi: panel, adresi başlıktan Türkçe harfleri bozarak üretiyor ("ü" → "ue", "İ" → "ı": "...tenısı-mıllı-duenya...").
   Doğru adres başlıktan Türkçe kurallarla üretilir ("...tenisi-milli-dunya..."), haberin 'url' alanına yazılır; eski adres
   'aliases' ile yeni adrese yönlendirilir (paylaşılmış bağlantılar çalışmaya devam eder). Bir kez yazılan adres, başlık sonradan
   değişse de değişmez. Haber metnine dokunulmaz.
"""
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import yaml

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HABER = os.path.join(KOK, "content", "haber")
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
    """Ön bilgi (front matter) metin olarak düzenlenir: yalnızca eksik satırlar eklenir, öteki alanların yazımı değişmez."""
    simdi = datetime.now(TR).replace(microsecond=0)
    adlar = sorted(a for a in os.listdir(HABER) if a.endswith(".md"))
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
                ek.append(f"url: /haber/{aday}/")
                ek.append("aliases:\n" + "".join(f"- '{x}'\n" for x in sorted(set(eski + [f'/haber/{kok}/']))).rstrip("\n"))
                notlar.append(f"adres: /haber/{aday}/")
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
