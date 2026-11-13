"""Her habere konusuyla ilgili görsel (t2). Yapay zekâ isteği yapılmaz; deneysel arama sürümünün kapalı olması bu adımı engellemez.

Sıra: (1) haberin kendi onaylı fotoğrafı korunur; (2) doğrulanmış görsel arşivinden (data/gorsel_arsiv.yml) haberin kişisi,
kurumu/takımı/tesisi, somut konusu ya da gerçekten ilgili yeri kimlik ve kullanım bağlamıyla eşleştirilir; (3) hiçbiri yoksa haberin
somut başlığıyla bize ait tipografik haber görseli üretilir (son seçenek; gerçek kişi ya da olay fotoğrafı uydurulmaz).

Eşleştirme (t2): başlık (3 puan), spot + metnin girişi (2,5), yalnız spot (2), yalnız giriş (1). Kişi, kurum, takım, tesis ve logo
kayıtları haberin MERKEZİNDEYSE kullanılır: adı başlıkta ya da hem spotta hem metnin girişinde geçmeli; metinde ikincil geçmesi
yetmez. Konu kayıtları (somut nesne/etkinlik: ambulans, itfaiye, yağmur, avokado, voleybol…) başlık ya da spotla eşleşir; haberi yazan
yapay zekânın seçtiği konu etiketi (gorsel_sinif) ek sinyaldir. Yer kayıtları yalnızca haberin konusu o yerin kendisiyse kullanılır;
başlıkta şehir adı geçmesi tek başına yer fotoğrafı için yeterli değildir.

Güvenlik: ret kaydındaki (bilinen yanlış) fotoğraflar ve editör incelemesinde yanlış bulunanlar hiçbir habere verilmez. Hassas
haberlerde (kaza, suç, ölüm vb.) yalnızca (a) başlıkta adı geçen kişi/kurum kaydı ve (b) özellikle bu haberler için seçilmiş nötr
kayıtlar (hassas: true — ambulans, itfaiye aracı, adalet simgesi, kolluk aracı; başka bir olayın, kazanın ya da kişinin görüntüsü
değil) kullanılır.

Konu etiketi: haberi yazan yapay zekâ haberin ana konusunu arşivdeki konu etiketlerinden seçer (gorsel_sinif). Etiket varsa konu
görseli o etiketten seçilir; başka bir konu kaydı yalnızca kelime eşleşmesiyle öne geçemez (ör. "kritik randevu" → hastane olmaz).
Editör ataması (tanım dosyasında "atama": haber dosyası → kayıt/grup/konu ya da "tipografik") her kuralın önündedir.

Çeşitlilik: bir haberin görseli, yayın sırasında kendisine yakın haberlerde (12 haber, aynı kategoride 8 haber) kullanılan kareyle
aynı olmaz. Aynı konunun öteki görselleri, onlar da kullanılmışsa haberle eşleşen ikinci konu/yer kaydı denenir; seçenek kalmadıysa
en uzun süredir kullanılmayan kare verilir (haber görselsiz bırakılmaz)."""
import hashlib
import json
import math
import os
import re

from urllib.parse import quote

import requests
import yaml

import gorsel_sec as gs
import gorsel_hat as gh

TAMAMLA_SURUM = "t2"
PENCERE, PENCERE_KAT = 12, 8      # çeşitlilik penceresi: yayın sırasında yakın haber sayısı (genel, aynı kategori)
TIPO_SURUM = "t1"          # tipografik görselin tasarım sürümü (değişirse bütün tipografik görseller yeniden üretilir)
KOK = gs.KOK
ARSIV_DOSYA = os.path.join(KOK, "data", "gorsel_arsiv.yml")
TANIM_DOSYA = os.path.join(KOK, "data", "gorsel_arsiv_tanim.json")      # t2 kayıt tanımları (eşleştirme kuralları + aday anahtarları)
ADAY_DOSYA = {"c": os.path.join(KOK, "data", "gorsel_aday_commons.json"), "u": os.path.join(KOK, "data", "gorsel_aday_unsplash.json")}
INCELEME_DOSYA = os.path.join(KOK, "data", "gorsel_inceleme.yml")
CIKTI = os.path.join(KOK, "static", "images", "haber-gorsel")
WEB = "/images/haber-gorsel/"
FONT = os.path.join(KOK, "scripts", "Mulish.ttf")   # Mulish (SIL OFL 1.1), sitenin yazı tipi
LOGO = os.path.join(KOK, "static", "images", "logo-beyaz.png")
UA = {"User-Agent": "GazeteAntalyaBot/1.0 (https://www.gazeteantalyam.com/iletisim/)"}
HASSAS_RE = re.compile(r"kaza(?!n)|çarpış|carpis|devril|yaral|öldü|ölü bul|hayatını kaybet|hayatini kaybet|cinayet|bıçak|bicak|silah|tabanca|"
                       r"gözaltı|gozalti|tutuklan|istismar|taciz|intihar|boğul|bogul|yangın|yangin|dolandırıc|dolandiric|uyuşturucu|"
                       r"uyusturucu|operasyon|şüpheli|supheli|darp|kavga|cenaze|ceset|cesed|saldır|saldir|su altında|taşkın|sel felaket|selde|hortum")
_TR = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def kat(metin):
    """Karşılaştırma için küçük harf ve Türkçe karakter katlama (başlıkların bir kısmı ASCII yazılıyor)."""
    m = (metin or "").replace("İ", "i").replace("I", "ı").lower().replace("’", "'").replace("‘", "'")
    return m.translate(_TR)


def _gecer(anahtar, metin, tam=False):
    """Anahtar metinde kelime başından itibaren geçiyor mu? 're:' ile başlayan anahtar, katlanmış (küçük harf, işaretsiz) metinde
    kelime başından aranan düzenli ifadedir (ör. 're:kaza(?!n)' → kaza, kazada; kazandı değil)."""
    if anahtar.startswith("re:"):
        return bool(re.search(r"(?<![a-z0-9])" + anahtar[3:], metin))
    a = kat(anahtar)
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(a) + (r"(?![a-z0-9])" if tam else ""), metin))


# ----------------------------------------------------------------- arşiv
_ARSIV = None


_YASAK = None


def _yasakli_gorseller():
    """Bilinen yanlış fotoğraflar. Dönüş: (her haber için yasak görseller, {konu imzası ya da dosya adı: o haber için yasak görseller}).
    Ret kaydında 'gorsel' düzeyi fotoğrafın kendisinin (kalite, lisans) reddidir: hiçbir habere verilmez. 'eslesme' düzeyi ve editör
    incelemesindeki 'yanlis' kararı o haber–fotoğraf eşleşmesinin reddidir: aynı fotoğraf o habere bir daha verilmez."""
    global _YASAK
    if _YASAK is not None:
        return _YASAK
    genel, habere = set(), {}
    r = gh.ret_kaydi()
    for k in r.get("gorsel") or []:
        genel.add(k.get("gk") or "")
    for kimza, liste in (r.get("eslesme") or {}).items():
        for k in liste:
            habere.setdefault(kimza, set()).add(k.get("gk") or "")
    try:
        with open(INCELEME_DOSYA, encoding="utf-8") as f:
            for k in (yaml.safe_load(f) or {}).get("kararlar") or []:
                if k.get("karar") == "yanlis" and k.get("gorsel"):
                    habere.setdefault(k.get("dosya") or "", set()).add(gh.gorsel_kimligi(k["gorsel"]))
    except Exception:
        pass
    genel.discard("")
    _YASAK = (genel, habere)
    return _YASAK


def _tanim_kayitlari():
    """data/gorsel_arsiv_tanim.json tanımlarını aday kayıtlarındaki (Commons/Unsplash) adres, lisans ve atıf bilgisiyle birleştirir."""
    try:
        with open(TANIM_DOSYA, encoding="utf-8") as f:
            tanim = json.load(f)
    except Exception as e:
        print("Görsel arşivi tanımları okunamadı:", e)
        return []
    aday = {}
    for tur, yol in ADAY_DOSYA.items():
        try:
            with open(yol, encoding="utf-8") as f:
                aday[tur] = {x["k"]: x for x in json.load(f)}
        except Exception as e:
            print("Aday kayıtları okunamadı:", yol, e)
            aday[tur] = {}
        for x in (tanim.get("aday") or {}).get(tur) or []:
            aday[tur][x["k"]] = x
    out = []
    for e in tanim.get("kayitlar") or []:
        ortak = {k: v for k, v in e.items() if k != "kaynak"}
        for kid, kay in e.get("kaynak") or []:
            d = dict(ortak, id=kid, t2=True)
            tur, anahtar = kay.split(":", 1)
            if tur == "c":
                c = aday["c"].get(anahtar)
                if not c:
                    continue
                ad = re.sub(r"\s+", " ", c.get("a") or "").strip()
                ad = re.sub(r"\s*\(talk.*$| from .*$", "", re.sub(r"^User:", "", ad))
                d.update(url=c["u"].split("?")[0], sayfa="https://commons.wikimedia.org/wiki/File:" + quote(c["t"].replace(" ", "_")),
                         lisans=c.get("l", ""), lisans_dayanagi=c.get("lu", ""), boyut=[c["w"], c["h"]],
                         fotografci="bilinmiyor" if (not ad or ad.lower().startswith("unknown")) else ad)
                if not d.get("yil") and re.match(r"(19|20)\d\d", c.get("y") or ""):
                    d["yil"] = int(c["y"][:4])
            elif tur == "u":
                u = aday["u"].get(anahtar)
                if not u:
                    continue
                d.update(url=u["raw"] + "?fm=jpg&q=80&w=1600&fit=max", sayfa="https://unsplash.com/photos/" + u["id"], lisans="Unsplash Lisansı",
                         lisans_dayanagi="https://unsplash.com/license", fotografci=u.get("a", ""), boyut=[u["w"], u["h"]])
            else:
                d["kutuphane"] = anahtar          # doğrulanmış görsel kütüphanesi kimliğinin başı
            out.append(d)
    return out


_ATAMA = None


def atamalar():
    """Editör ataması: {haber dosyası: kayıt kimliği | grup | konu etiketi | 'tipografik'} (tanım dosyasının "atama" bölümü)."""
    global _ATAMA
    if _ATAMA is None:
        try:
            with open(TANIM_DOSYA, encoding="utf-8") as f:
                _ATAMA = dict(json.load(f).get("atama") or {})
        except Exception:
            _ATAMA = {}
    return _ATAMA


def arsiv():
    """data/gorsel_arsiv.yml kayıtları; 'kutuphane' alanı olan kayıtların görsel ve kaynak bilgisi doğrulanmış görsel kütüphanesinden okunur."""
    global _ARSIV
    if _ARSIV is not None:
        return _ARSIV
    try:
        with open(ARSIV_DOSYA, encoding="utf-8") as f:
            ham = (yaml.safe_load(f) or {}).get("kayitlar") or []
    except Exception as e:
        print("Görsel arşivi okunamadı:", e)
        ham = []
    ham = list(ham) + _tanim_kayitlari()
    kut = gh.kutuphane()
    yasak, _ = _yasakli_gorseller()
    out = []
    for k in ham:
        k = dict(k)
        if k.get("kutuphane"):
            kk = (kut.get("kayitlar") or {}).get(k["kutuphane"])
            if not kk:      # kimliğin başı verilmişse tam kimliğe tamamlanır
                tam = [x for x in (kut.get("kayitlar") or {}) if x.startswith(k["kutuphane"])]
                if len(tam) == 1:
                    k["kutuphane"], kk = tam[0], kut["kayitlar"][tam[0]]
            if not kk:
                continue
            kay = kk.get("kaynak") or {}
            k.setdefault("url", kk.get("url"))
            k.setdefault("boyut", kk.get("boyut"))
            k.setdefault("sayfa", kay.get("sayfa", ""))
            k.setdefault("lisans", kay.get("lisans", ""))
            k.setdefault("lisans_dayanagi", kay.get("dayanak", ""))
            k.setdefault("fotografci", kay.get("sahibi") or kay.get("atif", ""))
            k.setdefault("yil", (kk.get("cekim_tarihi") or "")[:4])
            ic = (kut.get("inceleme") or {}).get(gh.gorsel_kimligi(k["url"])) or {}
            k.setdefault("icerik", {x: (ic.get("icerik") or {}).get(x) for x in ("ana_kutu", "yuzler")})
        if not k.get("url"):
            continue
        k["_gk"] = gh.gorsel_kimligi(k["url"])
        if k["_gk"] in yasak:
            continue
        out.append(k)
    _ARSIV = out
    return out


def konu_etiketleri():
    """Haberi yazan yapay zekânın seçebileceği konu etiketleri (arşivdeki konu kayıtlarının 'konu' alanı)."""
    return sorted({k.get("konu") for k in arsiv() if k.get("sinif") == "konu" and k.get("konu")})


def hassas_mi(v, yalniz_baslik=False):
    """Hassas haber (kaza, suç, ölüm, afet): kategori Asayiş ya da başlıkta/spotta hassas ifade. yalniz_baslik: spot sayılmaz."""
    m = (v.get("title", "") + " " + ("" if yalniz_baslik else (v.get("ozet") or ""))).replace("İ", "i").replace("I", "ı").lower()
    return (v.get("kategoriler") or [""])[0] == "Asayiş" or bool(HASSAS_RE.search(m))


KIMLIK_SINIF = ("kisi", "kurum", "takim", "logo", "tesis")
# Haber o yerin kendi işleriyle ilgiliyse (belediye hizmeti, mahalle, altyapı, yerel ekonomi, nüfus, imar…) ilçe fotoğrafı anlamlıdır
YEREL_RE = re.compile(r"belediye|muhtar|mahalle|kaymakam|ilce|sakin|vatandas|esnaf|altyapi|imar|nufus|meclis|cadde|sokak|meydan|"
                      r"hizmet|yatirim|proje|kalkinma|turizm|sezon|ziyaretci|kent|sehir|koy|yayla|sahil|carsi|pazar")


def bolgeler(v, govde=""):
    """Eşleştirme bölgeleri: başlık, spot ve metnin girişi (ilk ~450 karakter)."""
    return kat(v.get("title", "")), kat(v.get("ozet") or ""), kat(re.sub(r"\s+", " ", govde or "")[:450])


def _puan(k, b, s, g):
    """Başlıkta 3; spotta ve metnin girişinde (kaydın aynı ya da farklı anahtarlarıyla, ör. "COP31" + "Taraflar Konferansı") 2,5;
    yalnız spotta 2; yalnız girişte 1."""
    tam = bool(k.get("tam"))
    an = k.get("anahtar") or []
    if any(_gecer(a, b, tam) for a in an):
        return 3
    is_, ig = any(_gecer(a, s, tam) for a in an), any(_gecer(a, g, tam) for a in an)
    return 2.5 if (is_ and ig) else 2 if is_ else 1 if ig else 0


def _esik(k):
    sinif = k.get("sinif")
    if k.get("esik"):
        return float(k["esik"])
    if "baslikta" in k:
        return 3 if k["baslikta"] else 2
    # kişi: adı başlıkta geçmeli (spottaki ikinci bir kişi haberin kişisi sanılmasın); kurum/takım/tesis/logo: başlık ya da spot + giriş
    return 3 if sinif == "kisi" else 2.5 if sinif in KIMLIK_SINIF else 2 if sinif == "konu" else 3


def adaylar_bul(v, govde="", dosya="", haric=()):
    """Haberle eşleşen arşiv kayıtları, en iyiden başlayarak: [(puan, öncelik, kayıt)]."""
    b, s, g = bolgeler(v, govde)
    metin = b + " " + s
    kategori = (v.get("kategoriler") or [""])[0]
    ilce = (v.get("ilceler") or [""])[0]
    hassas, hassas_baslik = hassas_mi(v), hassas_mi(v, True)
    etiket = str(v.get("gorsel_sinif") or "")
    _, habere = _yasakli_gorseller()
    sec = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    kimza = sec.get("konu_imza") or gs.konu_imzasi(v.get("title"), v.get("ozet"), kategori or "Gündem")
    yasak_gk = (habere.get(kimza) or set()) | (habere.get(dosya) or set())
    out = []
    hedef = atamalar().get(dosya)
    if hedef:                 # editör ataması: yalnızca atanan kayıt/grup/konu
        for k in arsiv():
            if k.get("ilceler") is not None and ilce not in k["ilceler"] and k.get("id") != hedef:
                continue      # başka ilçenin görseli (ör. Alanya'daki konutlar) Antalya geneli haberine verilmez
            if hedef in (k.get("id"), k.get("grup"), k.get("konu")) and k.get("_gk") not in yasak_gk and k.get("id") not in haric:
                out.append((9, float(k.get("oncelik", 6)), k))
        out.sort(key=lambda x: (0 if x[2].get("id") == hedef else 1, x[1]))
        return out
    etiketler = set(konu_etiketleri())
    etiket = etiket if etiket in etiketler else ""
    for k in arsiv():
        if k.get("yedek_yer") or k.get("_gk") in yasak_gk or k.get("id") in haric:
            continue
        if k.get("kategoriler") and kategori not in k["kategoriler"]:
            continue
        if kategori in (k.get("yasak_kategoriler") or []):
            continue
        if k.get("ilceler") is not None and ilce not in k["ilceler"]:
            continue          # belirli bir yerin tesisi (ör. Antalya Adliyesi) başka ilçenin haberine verilmez
        if k.get("yerel") and any(_gecer(il, b, True) for il in gh.TR_ILLERI if il not in ("Antalya", "Burdur", "Isparta")):
            continue          # Antalya'ya özgü görsel başka ilin haberine verilmez
        if any(_gecer(y, metin) for y in k.get("yasak") or []):
            continue
        puan = _puan(k, b, s, g)
        if etiket and k.get("sinif") == "konu" and k.get("konu"):
            if etiket == k["konu"]:
                puan = max(puan, 3)       # haberi yazan yapay zekânın seçtiği konu etiketi: başlık eşleşmesi değerinde
            elif puan:
                puan = min(puan, 3) - 1.2  # başka konunun kaydı yalnızca kelimeyle eşleşti: etiketin önüne geçemez
        esik = 3 if kategori in (k.get("baslik_kategoriler") or []) else _esik(k)
        if puan < esik:
            continue
        if k.get("gerekli") and not any(_gecer(x, b if esik >= 3 else metin) for x in k["gerekli"]):
            continue
        if hassas:
            kimlik = k.get("sinif") in KIMLIK_SINIF and puan >= 3 and k.get("hassas_uygun", True)
            # hassas ifade yalnızca spotta geçiyorsa (haberin konusu başka) başlıkla eşleşen konu kaydı da kullanılabilir
            baslik_konusu = not hassas_baslik and puan >= 3 and k.get("sinif") == "konu"
            if not (kimlik or baslik_konusu or k.get("hassas") is True):
                continue
        elif k.get("yalniz_hassas"):
            continue
        if k.get("genel"):
            puan -= 0.5
        onc = float(k.get("oncelik", 2 if k.get("sinif") == "kisi" else 6)) - (0.3 if k.get("ilce") and k.get("ilce") == ilce else 0)
        out.append((puan, onc, k))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def yer_yedegi(v, govde=""):
    """Konu eşleşmesi olmayan, hassas olmayan yerel haber için ilçenin kendi fotoğrafı (yalnızca haber o yerin işleriyle ilgiliyse)."""
    if hassas_mi(v) or (v.get("kategoriler") or [""])[0] in ("Spor", "Asayiş"):
        return []
    b, s, _ = bolgeler(v, govde)
    ilce = (v.get("ilceler") or [""])[0]
    if not YEREL_RE.search(b + " " + s):
        return []
    if not ilce:          # ilçesi belirtilmemiş, kentin geneliyle ilgili haber: Antalya'nın kendi fotoğrafı
        return [k for k in arsiv() if k.get("yedek_yer") and k.get("ilce") == "Muratpaşa"] if _gecer("antalya", b) else []
    return [k for k in arsiv() if k.get("yedek_yer") and k.get("ilce") == ilce and _gecer(ilce, b)]


def esle(v, govde="", kullanim=None, dosya="", haric=(), yakin=()):
    """Haberle arşiv kaydı eşleştirmesi. yakin: yayın sırasında bu habere yakın haberlerin görsel kimlikleri (en yakından uzağa);
    bunlarla aynı kare seçilmez. Dönüş: (kayıt, açıklama, uygun seçenekler) ya da (None, neden, [])."""
    ad = adaylar_bul(v, govde, dosya, haric)
    if hedef_tipo(dosya):
        return None, "editör kararı: tipografik görsel", []
    if ad:
        en = ad[0]
        esdeger = [k for p, o, k in ad if (p, o) == en[:2] and k.get("grup") and k.get("grup") == en[2].get("grup")] or [en[2]]
        neden = "başlık/spot/giriş ile eşleşti"
        # Yalnızca yer fotoğrafı (ör. COP31'in yapıldığı alan) yakın haberlerde tükenmişse: başlıkta açıkça geçen ikinci konu
        # (ör. "iklim krizi"). Kişi/kurum/takım/tesis ve konu görselleri başka konuyla değiştirilmez; aynı grubun en uzun süredir
        # kullanılmayan karesi verilir.
        ek = [k for p, o, k in ad if p >= 3 and k not in esdeger and k.get("sinif") not in ("kisi", "yer")] \
            if en[2].get("sinif") == "yer" else []
    else:
        esdeger = [k for k in yer_yedegi(v, govde) if k.get("id") not in haric]
        neden, ek = "yerel haber: ilçenin kendi fotoğrafı", []
        if not esdeger:
            return None, "arşivde eşleşen kayıt yok" + (" (hassas haber: yalnızca başlıktaki kişi/kurum ya da nötr kayıt)" if hassas_mi(v) else ""), []
    kullanim = kullanim or {}
    yakin = list(yakin)
    h = int(hashlib.md5((v.get("title", "")).encode()).hexdigest(), 16)

    def sirala(liste):
        return sorted(range(len(liste)), key=lambda i: (kullanim.get(liste[i].get("id"), 0), (i - h) % len(liste)))
    bos = [k for k in esdeger if k.get("_gk") not in yakin]
    if bos:
        secilen = bos[sirala(bos)[0]]
    else:
        bos_ek = [k for k in ek if k.get("_gk") not in yakin]
        if bos_ek:
            secilen = bos_ek[0]
            neden += " (aynı konunun öteki kareleri yakın haberlerde kullanıldığı için haberin ikinci konusu)"
        else:                 # seçenek kalmadı: en uzun süredir kullanılmayan kare
            secilen = max(esdeger, key=lambda k: yakin.index(k.get("_gk")) if k.get("_gk") in yakin else 10 ** 6)
    return secilen, f"arşiv kaydı '{secilen.get('id')}' ({secilen.get('kimlik', '')}) {neden}", esdeger + ek


def hedef_tipo(dosya):
    return atamalar().get(dosya) in ("tipografik", "tipo")


# ----------------------------------------------------------------- tipografik görsel
def _font(boy, kalinlik):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(FONT, boy)
        try:
            f.set_variation_by_axes([kalinlik])
        except Exception:
            pass
        return f
    except Exception:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", boy)


def _sar(d, metin, f, gen):
    sat, cur = [], ""
    for k in metin.split():
        t = (cur + " " + k).strip()
        if d.textlength(t, font=f) <= gen:
            cur = t
        else:
            if cur:
                sat.append(cur)
            cur = k
    if cur:
        sat.append(cur)
    return sat


def tipografik_uret(baslik, kategori, ilce, yol):
    """1200×750 marka görseli: kategori · ilçe, haberin başlığı ve logomuz. Metin, kart/manşet/yan kart kırpmalarında görünür kalan
    güvenli alandadır (x 110–1090, y 150–630)."""
    from PIL import Image, ImageDraw
    W, H = 1200, 750
    im = Image.new("RGBA", (W, H), "#22343C")
    k = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dk = ImageDraw.Draw(k)
    cx, cy = 1110, 60
    for i in range(9):
        a = math.radians(100 + i * 18)
        dk.line([(cx + 70 * math.cos(a), cy + 70 * math.sin(a)), (cx + 150 * math.cos(a), cy + 150 * math.sin(a))], fill=(208, 86, 43, 150), width=12)
    dk.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=(208, 86, 43, 150))
    dk.rectangle([0, H - 14, W, H], fill=(208, 86, 43, 255))
    im = Image.alpha_composite(im, k).convert("RGB")
    d = ImageDraw.Draw(im)
    x0, x1 = 110, 1090
    d.rectangle([x0, 150, x0 + 8, 188], fill="#D0562B")
    ust = (kategori or "").upper().replace("i", "İ") + (("  ·  " + ilce.upper().replace("i", "İ")) if ilce else "")
    d.text((x0 + 24, 150), ust, font=_font(30, 850), fill="#F08A5D")
    for boy in (84, 78, 72, 66, 60, 56, 52, 48, 44):
        f = _font(boy, 950)
        sat = _sar(d, baslik, f, x1 - x0)
        lh = int(boy * 1.15)
        if len(sat) <= 4 and len(sat) * lh <= 318:
            break
    sat = sat[:4]
    y = 208 + (318 - len(sat) * lh) // 2
    for s in sat:
        d.text((x0, y), s, font=f, fill="#FFFFFF")
        y += lh
    try:
        logo = Image.open(LOGO).convert("RGBA")
        lh_ = 86
        logo = logo.resize((int(logo.width * lh_ / logo.height), lh_), Image.LANCZOS)
        im.paste(logo, (x0, 630 - lh_), logo)
    except Exception as e:
        print("Logo eklenemedi:", e)
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    im.save(yol, "JPEG", quality=82, optimize=True, progressive=True)


def logo_uret(kayit, yol):
    """Logo kırpılmaz ve esnetilmez: sade zeminde, kenarlarda boşluk bırakılarak 1200×750 karenin ortasına yerleştirilir."""
    from PIL import Image
    import io
    url = kayit["url"]
    m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2}/)([^/?]+\.svg)$", url, re.I)
    if m:      # SVG logo: Commons'un standart 960 px PNG kopyası
        url = f"{m.group(1)}thumb/{m.group(2)}{m.group(3)}/960px-{m.group(3)}.png"
    r = requests.get(url, headers=UA, timeout=40)
    r.raise_for_status()
    lg = Image.open(io.BytesIO(r.content)).convert("RGBA")
    W, H = 1200, 750
    zemin = Image.new("RGBA", (W, H), kayit.get("zemin") or "#FFFFFF")
    oran = min(760 / lg.width, 360 / lg.height)
    lg = lg.resize((max(1, int(lg.width * oran)), max(1, int(lg.height * oran))), Image.LANCZOS)
    zemin.alpha_composite(lg, ((W - lg.width) // 2, (H - lg.height) // 2 - 10))
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    zemin.convert("RGB").save(yol, "JPEG", quality=88, optimize=True)


def _tipo_yerlesim(url):
    o = "50% 50%"
    return {"boyut": [1200, 750], "dikey": False, "surum": gh.YERLESIM_SURUM, "manset": "kaplama", "yan": True, "kart": True, "detay": True,
            "kart_portre": False, "yan_portre": False, "tipografik": True, "url": url,
            **{"o_" + a: o for a in ("manset_d", "manset_m", "yan_d", "yan_t", "yan_m", "kart")}}


def tipo_yolu(v, dosya):
    imza = hashlib.md5(((v.get("title") or "") + "|" + ((v.get("kategoriler") or [""])[0]) + "|" + ((v.get("ilceler") or [""])[0]) + "|" + TIPO_SURUM).encode()).hexdigest()[:8]
    ad = re.sub(r"[^a-z0-9-]", "", dosya[:-3])[:70] + "-" + imza + ".jpg"
    return os.path.join(CIKTI, ad), WEB + ad


def tipo_hazirla(v, dosya):
    yol, web = tipo_yolu(v, dosya)
    if not os.path.exists(yol):
        tipografik_uret(v.get("title", ""), (v.get("kategoriler") or ["Gündem"])[0], (v.get("ilceler") or [""])[0], yol)
    return web


# ----------------------------------------------------------------- uygulama
def _arsivden(v):
    """Görsel bu adımın (t1/t2) arşiv eşleşmesi mi? Öyleyse güncel kurallarla yeniden değerlendirilir."""
    s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    return str(s.get("hat", "")).startswith("t") and s.get("tur") == "arsiv" and (s.get("gorsel_url") or "") == (v.get("gorsel") or "")


def _foto_var(v):
    """Haberin bu adımın dışında seçilmiş (kaynak, elle ya da görsel hattının onayladığı) kendi fotoğrafı var mı?"""
    g = v.get("gorsel") or ""
    s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    return bool(g) and not g.startswith("/images/kategori/") and s.get("tur") not in ("tipografik",) and not g.startswith(WEB) \
        and not _arsivden(v)


def _arsiv_yerlesim(k):
    w, h = (k.get("boyut") or [0, 0])[:2]
    if k.get("sahne") == "logo":
        return None
    ic = dict(k.get("icerik") or {})
    ic.setdefault("ana_kutu", k.get("ana_kutu"))
    ic.setdefault("yuzler", k.get("yuzler") or [])
    y, _ = gh.yerlesim(int(w), int(h), ic, None)
    return y


def arsiv_uygula(v, k, kimza, neden):
    """Arşiv kaydını habere yazar (onaylı; kaynak, lisans ve arşiv bağlamı haber detayında fotoğrafın altında)."""
    if k.get("sahne") == "logo":
        ad = "logo-" + re.sub(r"[^a-z0-9-]", "", k["id"]) + ".jpg"
        yol = os.path.join(CIKTI, ad)
        if not os.path.exists(yol):
            logo_uret(k, yol)
        gorsel = WEB + ad
        y = _tipo_yerlesim(gorsel)
        y["tipografik"] = False
        y["logo"] = True
    else:
        gorsel = k["url"]
        y = _arsiv_yerlesim(k)
        if not y or not (y.get("kart") or y.get("kart_portre")):
            return False
        y = dict(y, url=gorsel)
    temsili = k.get("sinif") == "konu"
    if "unsplash.com" in k["url"]:
        kredi = f"Fotoğraf: {k.get('fotografci', '')} / Unsplash"
    elif "wikimedia" in k["url"]:
        kredi = f"{'Fotoğraf' if temsili else 'Arşiv fotoğrafı'}: {k.get('fotografci', '')} / Wikimedia Commons ({k.get('lisans', '')})"
    else:
        kredi = f"Fotoğraf: {k.get('fotografci', '')}"
    if k.get("sahne") == "logo":
        kredi = f"Görsel: {k.get('fotografci') or k.get('kimlik', '')} / Wikimedia Commons ({k.get('lisans', '')})"
    aciklama = k.get("aciklama") or ("Temsilî fotoğraf" if temsili else f"{k.get('kimlik', '')} logosu" if k.get("sahne") == "logo"
                                     else f"{k.get('kimlik', '')} — Arşiv fotoğrafı" + (f" ({k.get('yil')})" if k.get("yil") else ""))
    eski = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    gecmis = v.get("gorsel_gecmis") if isinstance(v.get("gorsel_gecmis"), list) else []
    if _foto_var(v):
        return False
    v["gorsel"], v["gorsel_kredi"], v["gorsel_tur"] = gorsel, kredi, "arsiv"
    v["gorsel_secim"] = {
        "tur": "arsiv", "alt_tur": "arsiv_eslesme", "sinif": k.get("sinif", ""), "gorsel_url": gorsel, "konu_imza": kimza,
        "kaynak_sayfa": k.get("sayfa", ""), "fotografci": k.get("fotografci", ""), "lisans": k.get("lisans", ""),
        "lisans_dayanagi": k.get("lisans_dayanagi", ""), "gosterilen": k.get("kimlik", ""), "cekim_yili": str(k.get("yil") or ""),
        "aciklama": aciklama, "gerekce": neden, "durum": "onaylandi", "hat": TAMAMLA_SURUM,
        "kredi_etiket": "Görsel" if k.get("sahne") == "logo" else "Fotoğraf",
        "onay": {"yontem": "doğrulanmış görsel arşivi (kimlik ve kullanım bağlamı eşleşmesi)", "arsiv_id": k.get("id"),
                 "tarih": gs.simdi_tr().isoformat(timespec="seconds")},
        "kural_denetim": gh.KABUL_KURALI, "kabul_kurali": gh.KABUL_KURALI, "surum": gs.KURAL_SURUMU,
        "onceki_durum": {x: eski.get(x) for x in ("tur", "durum", "kapak_nedeni") if eski.get(x)}}
    v["gorsel_yerlesim"] = y
    v.pop("gorsel_konum", None)
    v["gorsel_gecmis"] = gecmis[-4:]
    return True


def tipo_uygula(v, web, kimza, neden):
    eski = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    v["gorsel"], v["gorsel_kredi"], v["gorsel_tur"] = web, "Görsel: Gazete Antalya", "tipografik"
    v["gorsel_secim"] = {"tur": "tipografik", "gorsel_url": web, "konu_imza": kimza, "durum": "onaylandi", "hat": TAMAMLA_SURUM,
                         "gerekce": neden, "aciklama": "", "kural_denetim": gh.KABUL_KURALI, "surum": gs.KURAL_SURUMU,
                         "onceki_durum": {x: eski.get(x) for x in ("tur", "durum", "kapak_nedeni") if eski.get(x)}}
    v["gorsel_yerlesim"] = _tipo_yerlesim(web)
    v.pop("gorsel_konum", None)


def _arsiv_id(v):
    s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
    return (s.get("onay") or {}).get("arsiv_id") if _arsivden(v) or s.get("hat") in ("t1", "t2") and s.get("tur") == "arsiv" else None


def _gk(v):
    g = v.get("gorsel") or ""
    return gh.gorsel_kimligi(g) if g else ""


def tamamla(dosyalar=None, oncelikli=(), sinir=10000):
    """Otomatik haberlerin görselini tamamlar. Elle seçilmiş görsellere dokunulmaz; görsel hattının onayladığı fotoğraf korunur.
    Arşivden verilmiş ve tipografik görseller her turda güncel arşiv ve kurallarla yeniden değerlendirilir: daha uygun kayıt varsa
    onunla değişir, eşleşme kalmadıysa tipografik görsele döner. Haberler yayın sırasıyla (yeniden eskiye) işlenir; görseli yakın
    haberlerle aynı olan haberin görseli değiştirilir (çeşitlilik). Dönüş: sayılar."""
    tum = dosyalar or os.listdir(gs.HABER_KLASOR)
    bilgi = {}
    for ad in os.listdir(gs.HABER_KLASOR):
        try:
            v0, _ = gs.md_oku(os.path.join(gs.HABER_KLASOR, ad))
            bilgi[ad] = {"tarih": str(v0.get("date") or ""), "kat": (v0.get("kategoriler") or [""])[0], "gk": _gk(v0), "aid": _arsiv_id(v0)}
        except Exception:
            pass
    yayin = sorted(bilgi, key=lambda a: (bilgi[a]["tarih"], a), reverse=True)        # yayın sırası: yeniden eskiye
    konum = {a: i for i, a in enumerate(yayin)}
    kat_sira = {}
    for a in yayin:
        kat_sira.setdefault(bilgi[a]["kat"], []).append(a)
    kat_konum = {a: i for l in kat_sira.values() for i, a in enumerate(l)}

    def pencere(ad, yon):
        """ad'a yakın haberlerin görsel kimlikleri, en yakından uzağa. yon=-1: daha yeni haberler; +1: daha eski haberler."""
        i, l, ki = konum[ad], kat_sira[bilgi[ad]["kat"]], kat_konum[ad]
        genel = yayin[max(0, i - PENCERE):i][::-1] if yon < 0 else yayin[i + 1:i + 1 + PENCERE]
        kat_ = l[max(0, ki - PENCERE_KAT):ki][::-1] if yon < 0 else l[ki + 1:ki + 1 + PENCERE_KAT]
        out = []
        for x in genel + kat_:
            g = bilgi[x]["gk"]
            if g and g not in out:
                out.append(g)
        return out
    sira = [a for a in yayin if a in set(tum)][:sinir]
    say = {"arsiv": 0, "tipografik": 0, "yedek": 0, "korundu": 0, "arsive_gecti": 0, "degisti": 0, "ayni": 0, "cesit": 0}
    # son haberlerde hangi arşiv görseli kaç kez kullanıldı (aynı konunun seçenekleri arasında en az kullanılan seçilir)
    kullanim = {}
    for ad in yayin[:60]:
        a0 = bilgi[ad]["aid"]
        if a0:
            kullanim[a0] = kullanim.get(a0, 0) + 1
    for ad in sira:
        yol = os.path.join(gs.HABER_KLASOR, ad)
        try:
            v, govde = gs.md_oku(yol)
            oto, _ = gs.otomatik_gorsel_mi(v)
            if not oto:
                continue
            degisti = False
            web = tipo_hazirla(v, ad)
            if v.get("gorsel_yedek") != web:
                eski_yedek = v.get("gorsel_yedek")
                v["gorsel_yedek"] = web
                degisti = True
                say["yedek"] += 1
                if eski_yedek and eski_yedek.startswith(WEB) and eski_yedek != web:
                    try:
                        os.remove(os.path.join(KOK, "static", eski_yedek.lstrip("/")))
                    except OSError:
                        pass
            s = v.get("gorsel_secim") if isinstance(v.get("gorsel_secim"), dict) else {}
            kimza = s.get("konu_imza") or gs.konu_imzasi(v.get("title"), v.get("ozet"), (v.get("kategoriler") or ["Gündem"])[0])
            if _foto_var(v):
                say["korundu"] += 1
            else:
                mevcut = _arsiv_id(v)
                yeni_p = pencere(ad, -1)
                yakin = yeni_p + [g for g in pencere(ad, 1) if g not in yeni_p]
                k, neden, esdeger = esle(v, govde, kullanim, ad, yakin=yakin)
                tekrar = bool(mevcut) and bilgi[ad]["gk"] in yeni_p        # daha yeni bir yakın haberle aynı kare
                if k and mevcut and mevcut in [x.get("id") for x in esdeger] and s.get("hat") == TAMAMLA_SURUM and \
                        not (tekrar and k.get("_gk") != bilgi[ad]["gk"]):
                    say["ayni"] += 1                       # aynı konunun görseli zaten uygulanmış: değiştirilmez
                else:
                    uygulandi, haric = False, set()
                    onceki_tur = s.get("tur")
                    yedek_v = {x: v.get(x) for x in ("gorsel", "gorsel_kredi", "gorsel_tur", "gorsel_secim", "gorsel_yerlesim")}
                    for _ in range(4):                     # kayıt bu alanlara yerleşmiyorsa ya da indirilemiyorsa sıradaki aday denenir
                        if not k:
                            break
                        if _arsivden(v):
                            v["gorsel"] = ""               # arşiv görseli yeniden değerlendiriliyor
                        try:
                            uygulandi = arsiv_uygula(v, k, kimza, neden)
                        except Exception as e:             # logo indirilemedi vb.: haber görselsiz kalmaz
                            print("   Arşiv görseli uygulanamadı:", ad, k.get("id"), e)
                        if uygulandi:
                            break
                        v.update({x: y for x, y in yedek_v.items() if y is not None})
                        haric.add(k.get("id"))
                        neden_ = f"arşiv kaydı '{k.get('id')}' uygulanamadı"
                        k, neden, esdeger = esle(v, govde, kullanim, ad, haric, yakin=yakin)
                        if not k:
                            neden = neden_
                    if uygulandi:
                        kullanim[k.get("id")] = kullanim.get(k.get("id"), 0) + 1
                        say["arsive_gecti" if onceki_tur == "tipografik" else "degisti" if mevcut else "arsiv"] += 1
                        say["cesit"] += 1 if tekrar else 0
                        degisti = True
                    elif v.get("gorsel") != web:
                        tipo_uygula(v, web, kimza, "Uygun doğrulanmış fotoğraf ya da logo yok (" + neden + "); bize ait tipografik haber görseli.")
                        say["tipografik"] += 1
                        degisti = True
            bilgi[ad]["gk"], bilgi[ad]["aid"] = _gk(v), _arsiv_id(v)
            if degisti:
                gs.md_yaz(yol, v, govde)
        except Exception as e:
            print("Görsel tamamlanamadı:", ad, e)
    print(f"Görsel tamamlama ({TAMAMLA_SURUM}): arşivden {say['arsiv']} (+{say['arsive_gecti']} tipografikten arşive, {say['degisti']} daha uygun "
          f"kayıtla değişti [{say['cesit']} yakın haberle aynı kare olduğu için], {say['ayni']} aynı kaldı), tipografik {say['tipografik']}, "
          f"yedek üretilen {say['yedek']}, kendi fotoğrafı korunan {say['korundu']}")
    return say
