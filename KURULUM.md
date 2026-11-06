# Gazete Antalya – Kurulum ve Kullanım

Site ücretsiz GitHub altyapısında çalışır:

- **GitHub Pages:** Siteyi yayınlar.
- **GitHub Actions:** Her 30 dakikada bir haberleri günceller.
- **Yönetim paneli (Sveltia CMS):** `gazeteantalyam.com/admin` adresinde, elle haber eklemek için.

## 1. Depoyu oluştur
1. github.com → sağ üstte **+** → **New repository**
2. Repository name: `gazeteantalyam`. **Public** seçili olmalı.
3. **Create repository** düğmesine tıkla.

## 2. Dosyaları yükle
1. Depo sayfasında **uploading an existing file** bağlantısına tıkla.
2. Zip'ten çıkan klasörün **içindekileri** sürükleyip bırak.
3. **Commit changes** düğmesine tıkla.
4. `.github` ve `.gitignore` gizli olduğu için yüklenmemiş olabilir. Depo listesinde `.github` klasörü görünmüyorsa:
   **Add file → Create new file** yolunu izle. Dosya adına `.github/workflows/yayinla.yml` yaz, içeriği zip'teki aynı dosyadan yapıştır ve kaydet.
   Aynısını `.github/workflows/canli-tut.yml` için de yap.

## 3. Yayını aç
1. Depoda **Settings → Pages** sayfasına git.
2. **Source** seçeneğini **GitHub Actions** yap.
3. **Settings → Actions → General** sayfasında **Workflow permissions** ayarını **Read and write** yap (canlı tutma görevi için).
4. **Actions** sekmesine git, **Haberleri güncelle ve yayınla** görevini seç ve **Run workflow** düğmesine tıkla.

## 4. Alan adını bağla
1. **Settings → Pages → Custom domain** alanına `www.gazeteantalyam.com` yaz ve **Save** düğmesine tıkla.
2. GoDaddy'de **Alan Adı → DNS** sayfasına git:
   - `@` için 4 adet **A** kaydı: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
     (varsa GoDaddy'nin eski "Parked" A kaydını sil)
   - `www` için **CNAME** kaydı: `allwaytour.github.io`
3. DNS'in yayılmasını bekle (genelde 15 dk – birkaç saat). Ardından Pages ayarlarında **Enforce HTTPS** kutusunu işaretle.

## 5. Yönetim paneli
1. github.com → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token** yolunu izle.
   - Repository access: yalnızca `gazeteantalyam`
   - Permissions: **Contents → Read and write**
2. `www.gazeteantalyam.com/admin` adresine git, **Sign In with Token** seçeneğine tıkla ve token'ı yapıştır.
3. **Haberler → Yeni** ile haber ekle. Kaydettikten 1-2 dakika sonra haber sitede görünür.
4. Köşe yazısı için **Köşe Yazıları → Yeni**; yeni yazar için önce **Yazarlar → Yeni**.
5. Girilmiş bir haberi düzeltmek için **Haberler** listesinde habere tıkla, değiştir, **Kaydet**. Kapak fotoğrafını
   değiştirirsen "Fotoğraf kredisi" alanını da güncelle. Panelden konan fotoğrafa otomatik görsel sistemi dokunmaz.
   Yüklenen fotoğraflar tarayıcıda küçültülür (en uzun kenar 1920 px, WebP).

Başka muhabirler eklemek için: Her muhabir kendi GitHub hesabını açar. Sen depo **Settings → Collaborators** sayfasından onu eklersin, o da kendi token'ıyla giriş yapar.

## Sık yapılacaklar
| İş | Nerede |
|---|---|
| Haber ekle / düzenle / sil | Panel → Haberler |
| Manşete almak | Haberde "Manşete al" seçeneğini aç |
| WhatsApp ihbar hattı, sosyal medya | Panel → Ayarlar → Site Bilgileri |
| Otomatik haber kaynağı ekle / çıkar | Panel → Ayarlar → Haber Kaynakları |
| Künye bilgileri | Panel → Sayfalar → Künye |
| Otomatik güncelleme çalışıyor mu? | GitHub → depo → Actions sekmesi (yeşil tik = sorun yok) |

## Notlar
- Otomatik haberlerde yalnızca başlık ve kısa özet gösterilir. Tıklayan okur haberin kaynağına gider.
- GitHub zamanlanmış görevleri yoğunlukta birkaç dakika geciktirebilir.
- Bir kaynak bozulursa diğerleri çalışmaya devam eder. Hangi kaynağın kaç haber verdiği Actions kayıtlarında görünür.
