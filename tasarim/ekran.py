"""Tasarım düzeltmeleri için önce/sonra ekran görüntüleri ve ölçümler (iş akışı: .github/workflows/tasarim.yml).
Önce: sitenin şu anki hâli (http://localhost:8001). Sonra: tasarim/yeni ile derlenmiş hâli (http://localhost:8002).
Çıktı: static/images/kalite-testi/tasarim/*.jpg ve data/tasarim_test.json (/kalite-testi/ sayfasında gösterilir)."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIKTI = os.path.join(KOK, "static", "images", "kalite-testi", "tasarim")
WEB = "/images/kalite-testi/tasarim/"
ADRES = {"once": "http://localhost:8001", "sonra": "http://localhost:8002"}
HABER = os.environ.get("TASARIM_HABER", "/haber/20261126-manavgatta-engelli-calisanlarin-is-yasamindaki-talepleri-2d67c/")
EKRAN = {"m": {"viewport": {"width": 375, "height": 812}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True},
         "d": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 1}}

CLS_JS = """() => new Promise(r => { let t = 0; try { new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) t += e.value; })
  .observe({type: 'layout-shift', buffered: true}); } catch (e) {} setTimeout(() => r(Math.round(t * 1000) / 1000), 2500); })"""
KESIK_JS = """() => [...document.querySelectorAll('.hk-b,.yn-b,.slayt-b,.yazar-baslik,.one-metin-b,.manset-yazi-b')].filter(e => {
  const s = getComputedStyle(e); const c = s.webkitLineClamp || s.getPropertyValue('-webkit-line-clamp');
  return (c && c !== 'none') || (e.scrollHeight > e.clientHeight + 2 && s.overflow === 'hidden'); }).length"""


def kaydet(sayfa, ad, **kw):
    os.makedirs(CIKTI, exist_ok=True)
    sayfa.screenshot(path=os.path.join(CIKTI, ad), type="jpeg", quality=68, **kw)
    return WEB + ad + "?v=" + str(int(time.time()))


def kirp(sayfa, yuk):
    h = sayfa.evaluate("document.documentElement.scrollHeight")
    w = sayfa.viewport_size["width"]
    return {"clip": {"x": 0, "y": 0, "width": w, "height": min(h, yuk)}, "full_page": True}


def ac(tarayici, ekran, yol, durum, engelle_gorsel=False):
    bag = tarayici.new_context(**EKRAN[ekran], locale="tr-TR", timezone_id="Europe/Istanbul")
    sayfa = bag.new_page()
    if os.environ.get("TASARIM_DIS_ENGELLE"):      # yalnızca yerel deneme: dış adresler engellenir
        sayfa.route(lambda u: "localhost" not in u, lambda r: r.abort())
    if engelle_gorsel:            # yer tutucu: sitenin kendi görselleri hiç gelmez (istek bekletilir), zemin rengi görünür
        sayfa.route(lambda u: "localhost" in u and ("/img/" in u or "/images/" in u), lambda r: None)
    sayfa.goto(ADRES[durum] + yol, wait_until="domcontentloaded" if engelle_gorsel else "load", timeout=60000)
    sayfa.wait_for_timeout(1200)
    if not engelle_gorsel:        # tembel yüklenen görseller tam sayfa görüntüsünde görünsün diye sayfa bir kez baştan sona kaydırılır
        sayfa.evaluate("async () => { for (let y = 0; y < document.documentElement.scrollHeight; y += 600) { scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } scrollTo(0, 0); }")
        sayfa.wait_for_timeout(900)
    return bag, sayfa


def main():
    vakalar = []
    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="chrome")
        except Exception:
            tarayici = pw.chromium.launch()

        def vaka(kimlik, baslik, aciklama, cek):
            v = {"id": kimlik, "baslik": baslik, "aciklama": aciklama, "gorseller": {}, "olcum": {}}
            for durum in ("once", "sonra"):
                v["gorseller"][durum] = {}
                v["olcum"][durum] = {}
                for ekran in ("m", "d"):
                    try:
                        cek(v, durum, ekran)
                    except Exception as e:
                        v["olcum"][durum][f"hata_{ekran}"] = str(e)[:160]
                        print("HATA", kimlik, durum, ekran, e)
            vakalar.append(v)

        # 1) Mobil ana sayfa: tek sütunlu liste, başlık kesilmez
        def c1(v, durum, ekran):
            bag, s = ac(tarayici, ekran, "/", durum)
            v["olcum"][durum][f"kesik_baslik_{ekran}"] = s.evaluate(KESIK_JS)
            for _ in range(2):            # görseller yüklenip düzen oturduktan sonra yeniden konumlan
                s.evaluate("() => { const e = document.querySelector('#b-gundem'); if (e) window.scrollTo(0, e.getBoundingClientRect().top + scrollY - 70); }")
                s.wait_for_timeout(900)
            v["gorseller"][durum][ekran] = kaydet(s, f"1-liste-{durum}-{ekran}.jpg")
            bag.close()
        vaka("liste", "1. Mobil ana sayfa: tek sütunlu liste, başlıklar kesilmez",
             "Mobilde iki sütunlu ızgara yerine küçük görsel solda, başlık sağda tek sütunlu liste. Başlıklarda '...' kesmesi yok "
             "(ölçüm: kesilen başlık sayısı).", c1)

        # 2) WhatsApp düğmesi
        def c2(v, durum, ekran):
            if ekran == "d":
                bag, s = ac(tarayici, ekran, HABER, durum)
                v["gorseller"][durum][ekran] = kaydet(s, f"2-wa-{durum}-{ekran}.jpg")
                bag.close()
                return
            bag, s = ac(tarayici, ekran, HABER, durum)
            for _ in range(6):
                s.mouse.wheel(0, 250)
                s.wait_for_timeout(120)
            s.wait_for_timeout(600)
            v["gorseller"][durum]["m"] = kaydet(s, f"2-wa-{durum}-m.jpg")
            v["olcum"][durum]["asagida_gorunur"] = s.evaluate("() => { const w = document.querySelector('.wa-yuzen'); if (!w) return null; const r = w.getBoundingClientRect(); return getComputedStyle(w).opacity !== '0' && r.top < innerHeight; }")
            for _ in range(2):
                s.mouse.wheel(0, -250)
                s.wait_for_timeout(120)
            s.wait_for_timeout(600)
            v["gorseller"][durum]["m2"] = kaydet(s, f"2-wa-{durum}-m2.jpg")
            v["olcum"][durum]["yukarida_gorunur"] = s.evaluate("() => { const w = document.querySelector('.wa-yuzen'); if (!w) return null; return getComputedStyle(w).opacity !== '0'; }")
            v["olcum"][durum]["dugme_px"] = s.evaluate("() => { const w = document.querySelector('.wa-yuzen'); return w ? Math.round(w.getBoundingClientRect().width) : null; }")
            bag.close()
        vaka("wa", "2. WhatsApp yüzen düğmesi (mobil)",
             "Mobilde küçültüldü (44 px). Aşağı kaydırırken gizlenir (birinci görüntü), yukarı kaydırınca görünür (ikinci görüntü); "
             "sayfa sonunda içeriğin üstüne binmez. Masaüstünde yüzen düğme yok (üst çubuktaki düğme aynı).", c2)

        # 3) Reklam yerleşimi: ana sayfa ve haber sayfası, kayma ölçümü
        def c3(v, durum, ekran):
            bag, s = ac(tarayici, ekran, "/", durum)
            v["olcum"][durum][f"cls_ana_{ekran}"] = s.evaluate(CLS_JS)
            v["gorseller"][durum][ekran] = kaydet(s, f"3-reklam-ana-{durum}-{ekran}.jpg", **kirp(s, 2300 if ekran == "m" else 1500))
            bag.close()
            bag, s = ac(tarayici, ekran, HABER, durum)
            v["olcum"][durum][f"cls_haber_{ekran}"] = s.evaluate(CLS_JS)
            v["gorseller"][durum][ekran + "_haber"] = kaydet(s, f"3-reklam-haber-{durum}-{ekran}.jpg", **kirp(s, 2000 if ekran == "m" else 1500))
            bag.close()
        vaka("reklam", "3. Reklam yerleşimi ve sabit reklam alanı",
             "Ana sayfada reklam manşet bloğunun altında; haber sayfasında başlıktan önce değil, ilk paragraftan sonra. Reklam alanına "
             "görselin oranında sabit yer ayrılır (ölçüm: CLS, düzen kayması; 0'a yakın olmalı).", c3)

        # 4) Etiketler yazının sonunda
        def c4(v, durum, ekran):
            bag, s = ac(tarayici, ekran, HABER, durum)
            kutu = s.evaluate("() => { const a = document.querySelector('article.haber'); const r = a.getBoundingClientRect(); return {y: r.top + scrollY, h: r.height}; }")
            w = s.viewport_size["width"]
            v["gorseller"][durum][ekran] = kaydet(s, f"4-etiket-{durum}-{ekran}.jpg", full_page=True,
                                                   clip={"x": 0, "y": max(0, kutu["y"] - 140), "width": w, "height": min(kutu["h"] + 160, 3400)})
            bag.close()
        vaka("etiket", "4. Haber sayfası: etiketler yazının sonunda",
             "Etiketler başlık ile özet arasından alınıp yazının sonuna taşındı.", c4)

        # 5-6) Kartlar: portre kırpma, logo görsel sayılmaz, görselsiz kart
        def c5(v, durum, ekran):
            bag, s = ac(tarayici, ekran, "/tasarim-ornek/", durum)
            v["gorseller"][durum][ekran] = kaydet(s, f"5-kart-{durum}-{ekran}.jpg", full_page=True)
            v["olcum"][durum][f"logo_ya_da_bos_kutu_{ekran}"] = s.evaluate("() => document.querySelectorAll('.hk .yer, .hk-tipo, .hk-g img[src*=\"logo-\"], .hk-pg img[style*=\"contain\"]').length")
            bag.close()
        vaka("kart", "5-6. Portre kırpma, logo kuralı ve görselsiz kart",
             "Portre fotoğraflar yatay kartta kenarlarda boşluk kalmadan yüze göre kırpılır (object-fit: cover, object-position). "
             "Logo ve logolu başlık kartı haber görseli sayılmaz; bu haberler boş gri kutu olmadan kategori etiketi, başlık ve özetle "
             "gösterilir. Görsel seçim kurallarına 'logo' kesin ret kodu eklendi.", c5)

        # 6) Manşet ve slayt: yalnızca fotoğraflı haberler
        def c6(v, durum, ekran):
            bag, s = ac(tarayici, ekran, "/", durum)
            v["olcum"][durum][f"mansette_fotosuz_{ekran}"] = s.evaluate(
                "() => [...document.querySelectorAll('.slayt, .manset-yan .yn')].filter(e => e.querySelector('img[src*=\"logo-\"], .slayt-yazi.gizli, .yn-yazi.gizli') || ['yn-metin','slayt-metin','yn-tipo','yn-portre'].some(c => e.classList.contains(c))).length")
            v["gorseller"][durum][ekran] = kaydet(s, f"6-manset-{durum}-{ekran}.jpg")
            bag.close()
        vaka("manset", "6. Manşet ve slayt: yalnızca görseli olan haberler",
             "Manşet slaytında ve yanındaki kartlarda yalnızca fotoğraflı haberler yer alır (ölçüm: manşetteki fotoğrafsız/logolu kart sayısı).", c6)

        # 7) Görsel performansı: WebP + srcset, baskın renk yer tutucu
        def c7(v, durum, ekran):
            bag = tarayici.new_context(**EKRAN[ekran], locale="tr-TR")
            s = bag.new_page()
            toplam = {"bayt": 0, "adet": 0, "webp": 0}

            def yanit(r):
                u = r.url
                if "localhost" in u and r.request.resource_type == "image" and ("/images/" in u or "/img/" in u):
                    try:
                        toplam["bayt"] += len(r.body())
                        toplam["adet"] += 1
                        toplam["webp"] += 1 if u.split("?")[0].endswith(".webp") else 0
                    except Exception:
                        pass
            s.on("response", yanit)
            s.goto(ADRES[durum] + "/", wait_until="load", timeout=60000)
            s.wait_for_timeout(1500)
            v["olcum"][durum][f"yerel_gorsel_kb_{ekran}"] = round(toplam["bayt"] / 1024)
            v["olcum"][durum][f"yerel_gorsel_adet_{ekran}"] = toplam["adet"]
            v["olcum"][durum][f"webp_{ekran}"] = toplam["webp"]
            bag.close()
            bag, s = ac(tarayici, ekran, HABER, durum, engelle_gorsel=True)
            v["gorseller"][durum][ekran] = kaydet(s, f"7-yer-tutucu-{durum}-{ekran}.jpg")
            bag.close()
        vaka("performans", "7. Görsel performansı: WebP, srcset ve baskın renk yer tutucu",
             "Sitenin kendi sunucusundaki görseller Hugo ile WebP'ye çevrilir ve ekran genişliğine uygun boyutlarda (srcset) üretilir. "
             "Görüntülerde görseller bilerek yüklenmedi: gri kutu yerine görselin baskın rengi görünür. Ölçüm: ana sayfada indirilen "
             "yerel görsel boyutu (KB) ve WebP sayısı.", c7)
        tarayici.close()

    tr = timezone(timedelta(hours=3))
    with open(os.path.join(KOK, "data", "tasarim_test.json"), "w", encoding="utf-8") as f:
        json.dump({"guncelleme": datetime.now(tr).isoformat(timespec="seconds"), "haber": HABER, "vakalar": vakalar}, f, ensure_ascii=False, indent=1)
    print(json.dumps([{k: v["olcum"]} for k, v in [(x["id"], x) for x in vakalar]], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
