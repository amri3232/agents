/* Stundensatz-Rechner. Läuft vollständig im Browser: keine Netzwerkaufrufe, keine Speicherung.
   Die Rechenlogik ist als reine Funktion exportiert und wird mit node getestet. */
(function (root) {
  "use strict";

  // Deutsche Zahleneingabe: "3.000", "3000", "3.000,50", "2,5" ...
  function parseDE(raw) {
    if (typeof raw === "number") return Number.isFinite(raw) ? raw : NaN;
    var s = String(raw == null ? "" : raw).trim().replace(/\s+/g, "").replace(/[€%]/g, "");
    if (s === "") return NaN;
    var hasComma = s.indexOf(",") !== -1;
    var hasDot = s.indexOf(".") !== -1;
    if (hasComma && hasDot) {
      // Das letzte Trennzeichen ist das Dezimaltrennzeichen.
      if (s.lastIndexOf(",") > s.lastIndexOf(".")) s = s.replace(/\./g, "").replace(",", ".");
      else s = s.replace(/,/g, "");
    } else if (hasComma) {
      s = s.replace(",", ".");
    } else if (hasDot && /^\d{1,3}(\.\d{3})+$/.test(s)) {
      s = s.replace(/\./g, ""); // "3.000" = dreitausend
    }
    if (!/^-?\d*\.?\d+$/.test(s)) return NaN;
    return parseFloat(s);
  }

  var FIELDS = [
    // key, label, min, max
    ["nettoWunsch", "Wunsch-Netto pro Monat", 0, 1000000],
    ["abgabenquote", "Abgabenquote", 0, 80],
    ["fixkosten", "Geschäftskosten pro Monat", 0, 1000000],
    ["puffer", "Puffer", 0, 100],
    ["tageWoche", "Arbeitstage pro Woche", 1, 7],
    ["stundenTag", "Stunden pro Arbeitstag", 1, 16],
    ["urlaub", "Urlaubstage", 0, 365],
    ["feiertage", "Feiertage", 0, 60],
    ["krank", "Ausfalltage", 0, 365],
    ["abrechenbar", "Anteil abrechenbar", 1, 100],
    ["ustSatz", "Umsatzsteuersatz", 0, 30],
  ];

  /**
   * @param {object} i  Eingaben (Prozentwerte als 0–100)
   * @returns {{ok:boolean, errors?:object, ...}}
   */
  function berechne(i) {
    var errors = {};
    FIELDS.forEach(function (f) {
      var v = i[f[0]];
      if (typeof v !== "number" || !Number.isFinite(v)) errors[f[0]] = f[1] + ": bitte eine Zahl eingeben.";
      else if (v < f[2] || v > f[3]) errors[f[0]] = f[1] + ": bitte einen Wert zwischen " + f[2] + " und " + f[3] + " eingeben.";
    });
    if (Object.keys(errors).length) return { ok: false, errors: errors };

    var arbeitstage = 52 * i.tageWoche - i.urlaub - i.feiertage - i.krank;
    if (arbeitstage <= 0) {
      return { ok: false, errors: { urlaub: "Urlaub, Feier- und Ausfalltage übersteigen die Arbeitstage des Jahres." } };
    }
    var verfuegbar = arbeitstage * i.stundenTag;
    var anteil = i.abrechenbar / 100;
    var abrechenbar = verfuegbar * anteil;
    var gewinn = (i.nettoWunsch * 12) / (1 - i.abgabenquote / 100);
    var kosten = i.fixkosten * 12;
    var bedarf = gewinn + kosten;
    var ziel = bedarf * (1 + i.puffer / 100);
    var satz = ziel / abrechenbar;

    var sens = [50, 60, 70, 80].map(function (q) {
      return { anteil: q, satz: ziel / (verfuegbar * (q / 100)) };
    });
    var warnungen = [];
    if (i.abrechenbar > 85) warnungen.push("Mehr als 85 % abrechenbare Stunden sind selten realistisch. Akquise, Verwaltung und Weiterbildung brauchen Zeit.");
    if (i.abrechenbar < 40) warnungen.push("Unter 40 % abrechenbar ergibt einen sehr hohen Stundensatz. Prüfe, ob du Verwaltungszeit wirklich so hoch ansetzt.");
    if (i.stundenTag > 10) warnungen.push("Mehr als 10 Arbeitsstunden pro Tag sind auf Dauer kaum durchzuhalten.");

    return {
      ok: true,
      arbeitstage: arbeitstage,
      verfuegbar: verfuegbar,
      abrechenbar: abrechenbar,
      gewinn: gewinn,
      kosten: kosten,
      bedarf: bedarf,
      ziel: ziel,
      stundensatz: satz,
      tagessatz: satz * i.stundenTag,
      kleinunternehmer: !!i.kleinunternehmer,
      stundensatzBrutto: i.kleinunternehmer ? satz : satz * (1 + i.ustSatz / 100),
      sens: sens,
      warnungen: warnungen,
    };
  }

  var nf0 = null, nf2 = null;
  function fmt(n, digits) {
    var f = digits === 0 ? nf0 : nf2;
    if (!f) {
      f = new Intl.NumberFormat("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
      if (digits === 0) nf0 = f; else nf2 = f;
    }
    return f.format(n);
  }
  function eur(n, digits) { return fmt(n, digits === undefined ? 2 : digits) + " €"; }

  var api = { parseDE: parseDE, berechne: berechne, FIELDS: FIELDS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Stundensatz = api;

  // ---- UI -----------------------------------------------------------------
  if (typeof document === "undefined") return;

  function $(id) { return document.getElementById(id); }
  var form = $("rechner-form");
  if (!form) return;

  function read() {
    var vals = {};
    FIELDS.forEach(function (f) { vals[f[0]] = parseDE($(f[0]).value); });
    vals.kleinunternehmer = $("kleinunternehmer").value === "ja";
    return vals;
  }

  function render() {
    var res = berechne(read());
    var out = $("ergebnis");
    var errs = $("fehler");
    FIELDS.forEach(function (f) {
      var el = $(f[0]);
      var bad = !res.ok && res.errors && res.errors[f[0]];
      if (bad) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
    });
    if (!res.ok) {
      out.hidden = true;
      errs.hidden = false;
      errs.innerHTML = "";
      Object.keys(res.errors).forEach(function (k) {
        var p = document.createElement("p");
        p.className = "error-msg";
        p.textContent = res.errors[k];
        errs.appendChild(p);
      });
      return;
    }
    errs.hidden = true;
    out.hidden = false;
    $("r-satz").textContent = eur(res.stundensatz);
    $("r-tag").textContent = eur(res.tagessatz);
    $("r-brutto").textContent = res.kleinunternehmer ? "keine USt" : eur(res.stundensatzBrutto);
    $("r-gewinn").textContent = eur(res.gewinn, 0);
    $("r-kosten").textContent = eur(res.kosten, 0);
    $("r-ziel").textContent = eur(res.ziel, 0);
    $("r-stunden").textContent = fmt(res.abrechenbar, 0) + " h";
    var body = $("r-sens");
    body.innerHTML = "";
    res.sens.forEach(function (s) {
      var tr = document.createElement("tr");
      var a = document.createElement("td");
      a.textContent = s.anteil + " %";
      var b = document.createElement("td");
      b.textContent = eur(s.satz);
      tr.appendChild(a);
      tr.appendChild(b);
      body.appendChild(tr);
    });
    var w = $("r-warn");
    w.innerHTML = "";
    w.hidden = res.warnungen.length === 0;
    res.warnungen.forEach(function (t) {
      var p = document.createElement("p");
      p.textContent = t;
      p.style.margin = "0 0 .5em";
      w.appendChild(p);
    });
  }

  form.addEventListener("input", render);
  form.addEventListener("change", render);
  form.addEventListener("submit", function (e) { e.preventDefault(); render(); });
  render();
})(typeof window !== "undefined" ? window : globalThis);
