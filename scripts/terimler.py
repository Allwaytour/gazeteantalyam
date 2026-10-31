#!/usr/bin/env python3
"""data/site.yaml içindeki kategori ve ilçeler için sayfa dosyalarını oluşturur (site derlenmeden önce çalışır)."""
import os, unicodedata, yaml

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hugo_slug(s):  # Hugo'nun urlize + removePathAccents davranışıyla aynı klasör adı
    s = s.lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.replace(" ", "-")


with open(os.path.join(KOK, "data", "site.yaml"), encoding="utf-8") as f:
    site = yaml.safe_load(f)
for tax, baslik in (("kategoriler", "Kategoriler"), ("ilceler", "İlçeler")):
    os.makedirs(os.path.join(KOK, "content", tax), exist_ok=True)
    with open(os.path.join(KOK, "content", tax, "_index.md"), "w", encoding="utf-8") as f:
        f.write(f'---\ntitle: "{baslik}"\n---\n')
    for t in site.get(tax) or []:
        klasor = os.path.join(KOK, "content", tax, hugo_slug(t))
        os.makedirs(klasor, exist_ok=True)
        with open(os.path.join(klasor, "_index.md"), "w", encoding="utf-8") as f:
            f.write(f'---\ntitle: "{t}"\nurl: "/{tax}/{hugo_slug(t).replace("ı", "i")}/"\n---\n')
print("Kategori ve ilçe sayfaları hazır.")
