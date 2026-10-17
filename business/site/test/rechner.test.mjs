import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { parseDE, berechne } = require("../src/assets/rechner.js");

const base = {
  nettoWunsch: 3000, abgabenquote: 30, fixkosten: 400, puffer: 10,
  tageWoche: 5, stundenTag: 8, urlaub: 25, feiertage: 10, krank: 8,
  abrechenbar: 60, ustSatz: 19, kleinunternehmer: false,
};
const close = (a, b, eps = 0.01) => assert.ok(Math.abs(a - b) <= eps, `${a} ≉ ${b}`);

test("parseDE: deutsche und englische Schreibweisen", () => {
  assert.equal(parseDE("3000"), 3000);
  assert.equal(parseDE("3.000"), 3000);
  assert.equal(parseDE("3.000,50"), 3000.5);
  assert.equal(parseDE("1,234.56"), 1234.56);
  assert.equal(parseDE("2,5"), 2.5);
  assert.equal(parseDE("2.5"), 2.5);
  assert.equal(parseDE("1.250.000"), 1250000);
  assert.equal(parseDE(" 30 % "), 30);
  assert.equal(parseDE("1 500 €"), 1500);
  assert.ok(Number.isNaN(parseDE("")));
  assert.ok(Number.isNaN(parseDE("abc")));
  assert.ok(Number.isNaN(parseDE("1,2,3")));
  assert.ok(Number.isNaN(parseDE(null)));
});

test("Beispielwerte entsprechen der Excel-Tabelle (59,38 €/h)", () => {
  const r = berechne(base);
  assert.equal(r.ok, true);
  assert.equal(r.arbeitstage, 217);
  assert.equal(r.verfuegbar, 1736);
  close(r.abrechenbar, 1041.6);
  close(r.gewinn, 51428.57);
  close(r.kosten, 4800);
  close(r.ziel, 61851.43);
  close(r.stundensatz, 59.38);
  close(r.tagessatz, 475.05);
  close(r.stundensatzBrutto, 70.66);
});

test("Zweiter Referenzfall: 4.000 € Wunsch-Netto → 77,49 €/h (unabhängig in Python nachgerechnet)", () => {
  close(berechne({ ...base, nettoWunsch: 4000 }).stundensatz, 77.4852, 0.001);
});

test("Kleinunternehmer: brutto = netto", () => {
  const r = berechne({ ...base, kleinunternehmer: true });
  close(r.stundensatzBrutto, r.stundensatz, 1e-9);
});

test("Sensitivität: 60 % entspricht dem Hauptergebnis, weniger Anteil = teurer", () => {
  const r = berechne(base);
  const s = Object.fromEntries(r.sens.map((x) => [x.anteil, x.satz]));
  close(s[60], r.stundensatz, 1e-9);
  assert.ok(s[50] > s[60] && s[60] > s[70] && s[70] > s[80]);
});

test("Plausibilität: mehr Wunsch-Netto, Kosten oder Puffer erhöhen den Satz", () => {
  const r0 = berechne(base).stundensatz;
  assert.ok(berechne({ ...base, nettoWunsch: 4000 }).stundensatz > r0);
  assert.ok(berechne({ ...base, fixkosten: 800 }).stundensatz > r0);
  assert.ok(berechne({ ...base, puffer: 20 }).stundensatz > r0);
  assert.ok(berechne({ ...base, abrechenbar: 80 }).stundensatz < r0);
});

test("Nullwerte sind erlaubt (keine Kosten, kein Puffer)", () => {
  const r = berechne({ ...base, fixkosten: 0, puffer: 0, urlaub: 0, feiertage: 0, krank: 0 });
  assert.equal(r.ok, true);
  close(r.stundensatz, (3000 * 12 / 0.7) / (260 * 8 * 0.6));
});

test("Ungültige Eingaben werden mit Feldnamen gemeldet", () => {
  let r = berechne({ ...base, nettoWunsch: NaN });
  assert.equal(r.ok, false);
  assert.ok(r.errors.nettoWunsch);
  r = berechne({ ...base, abrechenbar: 0 });
  assert.equal(r.ok, false);
  assert.ok(r.errors.abrechenbar);
  r = berechne({ ...base, abgabenquote: 100 });
  assert.equal(r.ok, false);
  assert.ok(r.errors.abgabenquote);
  r = berechne({ ...base, nettoWunsch: -1 });
  assert.equal(r.ok, false);
});

test("Mehr Ausfalltage als Arbeitstage: Fehler statt Division durch null", () => {
  const r = berechne({ ...base, tageWoche: 1, urlaub: 30, feiertage: 15, krank: 10 });
  assert.equal(r.ok, false);
  assert.ok(r.errors.urlaub);
});

test("Warnungen bei unrealistischen Annahmen", () => {
  assert.equal(berechne(base).warnungen.length, 0);
  assert.ok(berechne({ ...base, abrechenbar: 95 }).warnungen.length >= 1);
  assert.ok(berechne({ ...base, stundenTag: 12 }).warnungen.length >= 1);
  assert.ok(berechne({ ...base, abrechenbar: 20 }).warnungen.length >= 1);
});
