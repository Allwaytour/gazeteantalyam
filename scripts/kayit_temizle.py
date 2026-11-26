"""Eski kayıtlardan görselin kaynağı, sahibi, lisansı ve adresi silinir (yayın sorumlusu kararı, 26.11.2026: görsel kaynağı yazılmaz,
kayıt tutulmaz). Kalite taramasının her çalışmasında başta çalışır; tekrar çalıştırılması zararsızdır.
Dokunduğu dosyalar: data/gorsel_kayit.json, data/kalite_raporu.json, data/gorsel_kullanim.json, data/kalite_test.json,
data/toplu_kalite/oneriler.json. Haber dosyalarına dokunmaz (onlar onaydan sonra toplu_kalite.py uygula ile temizlenir)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gorsel_sec as gs  # noqa: E402
import gorsel_v2 as g2  # noqa: E402
import toplu_kalite as tk  # noqa: E402

D = os.path.join(g2.KOK, "data")


def oku(ad):
    try:
        with open(os.path.join(D, ad), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def yaz(ad, veri, girinti=1):
    with open(os.path.join(D, ad), "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=girinti)


def rapor_temizle(ad):
    d = oku(ad)
    if not isinstance(d, dict):
        return
    for dosya, o in (d.get("oneriler") or {}).items():
        g = o.get("gorsel") or {}
        if g.get("karar") == "gorselsiz":
            g["karar"] = "kaldir"
        once = g.get("once")
        if isinstance(once, dict):
            for k in ("kredi", "kaynak", "kaynak_karari"):
                once.pop(k, None)
            if str(once.get("neden", "")).startswith("kaynak/izin"):
                once["neden"] = "kaynak/izin yeni kurallara uymuyor"
        if isinstance(g.get("sonra"), dict):
            g["sonra"] = g2.kaynaksiz(g["sonra"], onizleme=True)
            g["sonra"].pop("kaynak", None)
        if isinstance(g.get("secim"), dict):
            g["secim"].pop("kanit", None)
        if g.get("reddedilen"):
            g["reddedilen"] = [g2.kaynaksiz(x, onizleme=True) for x in g["reddedilen"]]
            for x in g["reddedilen"]:
                x.pop("kaynak", None)
        g.pop("notlar", None)
        if isinstance(g.get("brief"), dict):
            g["brief"].pop("sorgular_en", None)
            g["brief"].pop("sorgular_tr", None)
        o["notlar"] = []
        yol = os.path.join(gs.HABER_KLASOR, dosya)
        if o.get("durum") != "uygulandi" and os.path.exists(yol):
            tk.grupla(o, gs.md_oku(yol)[0])
    tk.yaz(d, os.path.join(D, ad))


def main():
    for ad in ("toplu_kalite/oneriler.json", "kalite_test.json"):
        rapor_temizle(ad)
    k = oku("gorsel_kayit.json")
    if isinstance(k, list):
        yaz("gorsel_kayit.json", [g2.kalici_kayit(x) for x in k])
    r = oku("kalite_raporu.json")
    if isinstance(r, dict):
        for h in r.get("haberler") or []:
            if isinstance(h.get("gorsel"), dict):
                h["gorsel"] = g2.kaynaksiz(h["gorsel"])
                h["gorsel"].pop("kaynak", None)
        yaz("kalite_raporu.json", r)
    u = oku("gorsel_kullanim.json")
    if isinstance(u, dict):
        for x in u.get("kayitlar") or []:
            x["id"] = g2.iz(x.get("id"))
        yaz("gorsel_kullanim.json", u, 0)
    print("Kayıtlardan görsel kaynak bilgileri silindi.")


if __name__ == "__main__":
    main()
