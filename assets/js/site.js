(function () {
  "use strict";
  var TZ = "Europe/Istanbul";
  var $ = function (s, k) { return (k || document).querySelector(s); };
  var $$ = function (s, k) { return Array.prototype.slice.call((k || document).querySelectorAll(s)); };

  // Bugünün tarihi
  var bugun = $("#bugun");
  if (bugun) bugun.textContent = new Intl.DateTimeFormat("tr-TR", { timeZone: TZ, weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(new Date());

  // Zamanlar: "5 dk önce", "14:20", "12 Ekim"
  var saatF = new Intl.DateTimeFormat("tr-TR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
  var gunF = new Intl.DateTimeFormat("tr-TR", { timeZone: TZ, day: "numeric", month: "long" });
  var tamF = new Intl.DateTimeFormat("tr-TR", { timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  function goreli(d) {
    var fark = (Date.now() - d.getTime()) / 60000;
    if (fark < 1) return "Az önce";
    if (fark < 60) return Math.floor(fark) + " dk önce";
    if (fark < 24 * 60) return Math.floor(fark / 60) + " saat önce";
    if (fark < 48 * 60) return "Dün " + saatF.format(d);
    return gunF.format(d);
  }
  function zamanlariYaz(kok) {
    $$("time[data-zaman]", kok).forEach(function (t) { var d = new Date(t.getAttribute("datetime")); if (!isNaN(d)) t.textContent = goreli(d); });
    $$("time[data-saat]", kok).forEach(function (t) { var d = new Date(t.getAttribute("datetime")); if (!isNaN(d)) t.textContent = saatF.format(d); });
    $$("time[data-tam]", kok).forEach(function (t) { var d = new Date(t.getAttribute("datetime")); if (!isNaN(d)) t.textContent = tamF.format(d); });
  }
  zamanlariYaz(document);

  // Döviz ve altın (ücretsiz açık kaynak kur servisi; yedek: Avrupa Merkez Bankası)
  var sayi = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function kurYaz(oran) { $$("[data-kur]").forEach(function (b) { var x = oran[b.getAttribute("data-kur")]; b.textContent = x ? sayi.format(x) : "–"; }); }
  fetch("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/try.json")
    .then(function (r) { return r.json(); })
    .then(function (v) { var t = v["try"]; kurYaz({ USD: 1 / t.usd, EUR: 1 / t.eur, GBP: 1 / t.gbp, ALTIN: t.xau ? 1 / t.xau / 31.1035 : 0 }); })
    .catch(function () {
      fetch("https://api.frankfurter.dev/v1/latest?base=TRY&symbols=USD,EUR,GBP").then(function (r) { return r.json(); })
        .then(function (v) { kurYaz({ USD: 1 / v.rates.USD, EUR: 1 / v.rates.EUR, GBP: 1 / v.rates.GBP }); })
        .catch(function () { kurYaz({}); });
    });

  // Yüklenemeyen fotoğrafların yerine marka renkli yer tutucu koy
  function gorselHata(img) {
    var d = document.createElement("div"); d.className = "yer"; d.setAttribute("aria-hidden", "true");
    var fig = img.closest("figure.kapak"); if (fig) { fig.remove(); return; }
    img.replaceWith(d);
  }
  $$("img").forEach(function (img) { if (img.complete && img.naturalWidth === 0 && img.src) gorselHata(img); else img.addEventListener("error", function () { gorselHata(img); }); });

  // Hava durumu (Open-Meteo, ücretsiz, anahtar gerekmez)
  var KOD = { 0: ["Açık", "☀️"], 1: ["Az bulutlu", "🌤️"], 2: ["Parçalı bulutlu", "⛅"], 3: ["Bulutlu", "☁️"], 45: ["Sisli", "🌫️"], 48: ["Sisli", "🌫️"],
    51: ["Çisenti", "🌦️"], 53: ["Çisenti", "🌦️"], 55: ["Çisenti", "🌦️"], 61: ["Hafif yağmur", "🌦️"], 63: ["Yağmurlu", "🌧️"], 65: ["Kuvvetli yağmur", "🌧️"],
    66: ["Dondurucu yağmur", "🌧️"], 67: ["Dondurucu yağmur", "🌧️"], 71: ["Hafif kar", "🌨️"], 73: ["Karlı", "🌨️"], 75: ["Yoğun kar", "❄️"], 77: ["Kar", "🌨️"],
    80: ["Sağanak", "🌦️"], 81: ["Sağanak", "🌧️"], 82: ["Kuvvetli sağanak", "⛈️"], 85: ["Kar sağanağı", "🌨️"], 86: ["Kar sağanağı", "🌨️"],
    95: ["Gök gürültülü", "⛈️"], 96: ["Dolu", "⛈️"], 99: ["Dolu", "⛈️"] };
  var p = $("#piyasa");
  if (p) {
    var url = "https://api.open-meteo.com/v1/forecast?latitude=" + p.dataset.enlem + "&longitude=" + p.dataset.boylam +
      "&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=Europe%2FIstanbul&forecast_days=4";
    fetch(url).then(function (r) { return r.json(); }).then(function (h) {
      var c = h.current, k = KOD[c.weather_code] || ["", ""];
      var kisa = $("#hava-kisa"); if (kisa) kisa.textContent = k[1] + " " + Math.round(c.temperature_2m) + "°C";
      var der = $("#hava-der"); if (!der) return;
      der.textContent = Math.round(c.temperature_2m) + "°";
      $("#hava-durum").textContent = k[1] + " " + k[0];
      $("#hava-ek").textContent = "Nem %" + c.relative_humidity_2m + " · Rüzgâr " + Math.round(c.wind_speed_10m) + " km/sa";
      var gf = new Intl.DateTimeFormat("tr-TR", { weekday: "short", timeZone: TZ });
      $("#hava-gunler").innerHTML = h.daily.time.map(function (g, i) {
        var kk = KOD[h.daily.weather_code[i]] || ["", ""];
        return "<div>" + (i === 0 ? "Bugün" : gf.format(new Date(g + "T12:00:00+03:00"))) + "<b>" + kk[1] + "</b>" +
          Math.round(h.daily.temperature_2m_max[i]) + "° / " + Math.round(h.daily.temperature_2m_min[i]) + "°</div>";
      }).join("");
    }).catch(function () { var d = $("#hava-durum"); if (d) d.textContent = "Şu an alınamadı"; });
  }

  // Manşet slaytı
  $$("[data-slayt]").forEach(function (s) {
    var ic = $(".slayt-ic", s), noktalar = $$(".slayt-nok button", s), n = noktalar.length, i = 0, zaman;
    if (n < 2) { $$(".slayt-ok", s).forEach(function (b) { b.hidden = true; }); return; }
    function git(j) { i = (j + n) % n; ic.style.transform = "translateX(-" + i * 100 + "%)"; noktalar.forEach(function (b, x) { b.classList.toggle("aktif", x === i); }); }
    function baslat() { clearInterval(zaman); zaman = setInterval(function () { git(i + 1); }, 6000); }
    noktalar.forEach(function (b, x) { b.addEventListener("click", function () { git(x); baslat(); }); b.addEventListener("mouseenter", function () { git(x); }); });
    $(".slayt-ok.sol", s).addEventListener("click", function () { git(i - 1); baslat(); });
    $(".slayt-ok.sag", s).addEventListener("click", function () { git(i + 1); baslat(); });
    s.addEventListener("mouseenter", function () { clearInterval(zaman); });
    s.addEventListener("mouseleave", baslat);
    var x0 = null;
    s.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; }, { passive: true });
    s.addEventListener("touchend", function (e) { if (x0 === null) return; var dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 40) { git(dx < 0 ? i + 1 : i - 1); baslat(); } x0 = null; });
    git(0); baslat();
  });

  // "Daha fazla haber yükle"
  $$("[data-sayfala]").forEach(function (l) {
    var adim = +l.dataset.sayfala, ogeler = $$(".liste-oge", l), btn = l.nextElementSibling, goster = adim;
    if (!btn || !btn.hasAttribute("data-daha")) return;
    function yenile() { ogeler.forEach(function (o, x) { o.hidden = x >= goster; }); btn.hidden = goster >= ogeler.length; }
    btn.addEventListener("click", function () { goster += adim; yenile(); });
    yenile();
  });

  // Linki kopyala
  $$("[data-kopyala]").forEach(function (b) {
    b.addEventListener("click", function () {
      var y = b.textContent;
      (navigator.clipboard ? navigator.clipboard.writeText(b.dataset.kopyala) : Promise.reject()).then(function () { b.textContent = "Kopyalandı ✓"; }, function () { b.textContent = "Kopyalanamadı"; });
      setTimeout(function () { b.textContent = y; }, 2000);
    });
  });

  // Arama sayfası
  var sonuc = $("#arama-sonuc");
  if (sonuc) {
    var q = (new URLSearchParams(location.search).get("q") || "").trim();
    var kutu = $("#aq"); if (kutu) kutu.value = q;
    var durum = $("#arama-durum");
    if (q) {
      var norm = function (s) { return (s || "").toLocaleLowerCase("tr-TR").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ı/g, "i"); };
      var kel = norm(q).split(/\s+/).filter(Boolean);
      var esc = function (s) { return (s || "").replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
      durum.textContent = "Aranıyor…";
      fetch("/index.json").then(function (r) { return r.json(); }).then(function (veri) {
        var bul = veri.filter(function (h) { var m = norm(h.b + " " + h.o + " " + h.k + " " + h.i); return kel.every(function (k) { return m.indexOf(k) > -1; }); });
        durum.textContent = "“" + q + "” için " + bul.length + " sonuç bulundu.";
        sonuc.innerHTML = bul.slice(0, 100).map(function (h) {
          var hedef = h.d ? ' target="_blank" rel="noopener nofollow"' : "";
          var g = h.g ? '<img src="' + esc(h.g) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '<div class="yer"><span>' + esc(h.k) + "</span></div>";
          return '<article class="liste-oge"><a class="g" href="' + esc(h.l) + '"' + hedef + ">" + g + '</a><div><span class="etiket">' + esc(h.k) +
            '</span><h3><a href="' + esc(h.l) + '"' + hedef + ">" + esc(h.b) + "</a></h3><p>" + esc(h.o) + '</p><div class="meta"><time datetime="' + esc(h.t) +
            '" data-zaman></time>' + (h.i ? "<span>· " + esc(h.i) + "</span>" : "") + (h.d ? '<span class="kaynak dis">' + esc(h.s) + "</span>" : "") + "</div></div></article>";
        }).join("");
        if (!bul.length) sonuc.innerHTML = '<div class="bos">Sonuç bulunamadı. Farklı bir kelime deneyin.</div>';
        zamanlariYaz(sonuc);
      }).catch(function () { durum.textContent = "Arama şu an yapılamadı."; });
    } else { durum.textContent = "Aramak istediğiniz kelimeyi yazın."; }
  }
})();
