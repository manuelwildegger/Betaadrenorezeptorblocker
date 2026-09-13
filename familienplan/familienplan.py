#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Familien-Dienstplaner
=====================
Schicht- und Betreuungsplanung fuer die Familie - reines Konsolenprogramm,
nur Python-Standardbibliothek, laeuft out of the box auf macOS.

    ./familienplan.py               interaktives Menue
    ./familienplan.py --help        Kommandouebersicht
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta
from itertools import combinations, permutations, product

# --------------------------------------------------------------------------
# Grundwerte
# --------------------------------------------------------------------------
APP_DIR = os.path.expanduser(os.environ.get("FAMILIENPLAN_DIR", "~/.familienplan"))
PLAN_FILE = os.path.join(APP_DIR, "plan.json")

SLOT = 15                     # Zeitraster in Minuten
SPD = 24 * 60 // SLOT         # Slots pro Tag = 96
FENSTER = 8                   # Planungsfenster je Woche in Tagen (7 + Nacht-Ueberhang)

WDK = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
WDL = ("Montag", "Dienstag", "Mittwoch", "Donnerstag",
       "Freitag", "Samstag", "Sonntag")

DEFAULT_CONFIG = {
    "start": "2026-11-01",
    "wochen": 4,
    "kind": {"name": "Tochter", "betreuung": "24/7"},
    "externe_hilfe": {
        "namen": ["Familie (z. B. Oma/Opa)"],
        "max_h_pro_woche": 8.0,
    },
    "kita": {
        "aktiv": False,
        "wochentage": [0, 1, 2, 3, 4],
        "von": "08:00",
        "bis": "14:00",
    },
    "personen": {
        "Manuel": {
            "kuerzel": "Man",
            "vertrag_h_woche": 20.0,
            "dienste_pro_woche": 3,
            "pflicht_schichten": ["f", "s", "n"],
            "min_ruhe_h": 12.0,
            "wochenenddienste_pro_4_wochen": 2,
            "wunschfrei": [],
            "schichten": {
                "f": {"name": "Fruehdienst", "von": "05:00", "bis": "14:30",
                      "netto_h": 6.5, "erholung_vor_h": 0, "erholung_nach_h": 0},
                "s": {"name": "Spaetdienst", "von": "12:00", "bis": "23:00",
                      "netto_h": 7.0, "erholung_vor_h": 0, "erholung_nach_h": 9},
                "n": {"name": "Nachtdienst", "von": "20:00", "bis": "08:00",
                      "netto_h": 6.5, "erholung_vor_h": 3, "erholung_nach_h": 6},
            },
        },
        "Maria": {
            "kuerzel": "Mar",
            "vertrag_h_woche": 15.0,
            "dienste_pro_woche": 3,
            "pflicht_schichten": [],
            "min_ruhe_h": 11.0,
            "wochenenddienste_pro_4_wochen": 0,
            "wunschfrei": [],
            "schichten": {
                "f": {"name": "Fruehdienst", "von": "07:30", "bis": "13:00",
                      "netto_h": 5.0, "erholung_vor_h": 0, "erholung_nach_h": 0},
            },
        },
    },
}

# --------------------------------------------------------------------------
# Kleinkram
# --------------------------------------------------------------------------
FARBE = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text, farbe):
    if not FARBE:
        return text
    codes = {"rot": "31", "gruen": "32", "gelb": "33", "blau": "34",
             "magenta": "35", "cyan": "36", "grau": "90", "fett": "1"}
    return "\033[%sm%s\033[0m" % (codes[farbe], text)


def hhmm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def mmhh(m):
    m %= 1440
    return "%02d:%02d" % (m // 60, m % 60)


def hm(minuten):
    minuten = int(round(minuten))
    vz = "-" if minuten < 0 else ""
    minuten = abs(minuten)
    return "%s%d:%02d" % (vz, minuten // 60, minuten % 60)


def parse_datum(s):
    s = str(s).strip()
    for f in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise ValueError("Datum nicht lesbar: %r (erlaubt: 01.11.2026 / 2026-11-01)" % s)


def d_kurz(d):
    return "%s %02d.%02d." % (WDK[d.weekday()], d.day, d.month)


def rmask(a, b, n):
    """Bitmaske fuer Slots [a, b) innerhalb von n Slots."""
    a = max(0, int(a))
    b = min(n, int(b))
    return 0 if b <= a else ((1 << (b - a)) - 1) << a


def bits(x):
    return bin(x).count("1")


# --------------------------------------------------------------------------
# Plan-Objekt
# --------------------------------------------------------------------------
class Plan(object):
    def __init__(self, cfg, start, wochen, dienste=None):
        self.cfg = cfg
        self.start = start
        self.wochen = wochen
        self.dienste = dienste if dienste is not None else \
            dict((p, {}) for p in cfg["personen"])
        for p in cfg["personen"]:
            self.dienste.setdefault(p, {})
        self.reduziert = {}          # Person -> Wochen mit weniger Diensten

    # ---- Struktur -------------------------------------------------------
    @property
    def personen(self):
        return list(self.cfg["personen"].keys())

    @property
    def tage(self):
        return self.wochen * 7

    def datum(self, di):
        return self.start + timedelta(days=di)

    def index(self, d):
        return (d - self.start).days

    def wunschfrei(self, person):
        """Wunschfrei-/Urlaubstage als Menge von Tagindizes."""
        out = set()
        for s in self.cfg["personen"][person].get("wunschfrei") or []:
            try:
                out.add(self.index(parse_datum(s)))
            except ValueError:
                pass
        return out

    def ist_frei_gewuenscht(self, person, di):
        return di in self.wunschfrei(person)

    def urlaubswoche(self, person, w):
        return bool(self.wunschfrei(person) & set(range(w * 7, (w + 1) * 7)))

    def schicht(self, person, code):
        return self.cfg["personen"][person]["schichten"][code]

    def code(self, person, di):
        if di < 0 or di >= self.tage:
            return None
        return self.dienste[person].get(self.datum(di).isoformat())

    def setze(self, person, di, code):
        key = self.datum(di).isoformat()
        if code is None:
            self.dienste[person].pop(key, None)
        else:
            self.dienste[person][key] = code

    # ---- Zeitintervalle -------------------------------------------------
    def intervalle(self, person):
        """Liste (start_min, ende_min, code, tagindex), absolut ab Planbeginn."""
        out = []
        for di in range(self.tage):
            code = self.code(person, di)
            if not code:
                continue
            sh = self.schicht(person, code)
            s = di * 1440 + hhmm(sh["von"])
            e = di * 1440 + hhmm(sh["bis"])
            if e <= s:
                e += 1440
            out.append((s, e, code, di))
        out.sort()
        return out

    def abdeckung(self):
        """Anzahl anwesender Erwachsener je 15-Minuten-Slot (inkl. Pufftag)."""
        n = (self.tage + 1) * SPD
        arr = [len(self.personen)] * n
        for p in self.personen:
            for (s, e, _code, _di) in self.intervalle(p):
                for sl in range(max(0, s // SLOT), min(n, e // SLOT)):
                    arr[sl] -= 1
        if self.cfg.get("kita", {}).get("aktiv"):
            k = self.cfg["kita"]
            for di in range(self.tage):
                if self.datum(di).weekday() in k["wochentage"]:
                    a = di * SPD + hhmm(k["von"]) // SLOT
                    b = di * SPD + hhmm(k["bis"]) // SLOT
                    for sl in range(a, min(n, b)):
                        arr[sl] += 1
        return arr

    def luecken(self, arr=None):
        """Betreuungsluecken als Liste (start_min, ende_min) absolut."""
        arr = arr if arr is not None else self.abdeckung()
        grenze = self.tage * SPD
        out, i = [], 0
        while i < grenze:
            if arr[i] <= 0:
                j = i
                while j < grenze and arr[j] <= 0:
                    j += 1
                out.append((i * SLOT, j * SLOT))
                i = j
            else:
                i += 1
        return out

    # ---- Kennzahlen -----------------------------------------------------
    def wochenstat(self, person, w):
        a, b = w * 7, (w + 1) * 7
        codes, ausser, netto = [], 0, 0.0
        for di in range(a, b):
            code = self.code(person, di)
            if not code:
                continue
            sh = self.schicht(person, code)
            s, e = hhmm(sh["von"]), hhmm(sh["bis"])
            if e <= s:
                e += 1440
            codes.append(code)
            ausser += e - s
            netto += float(sh.get("netto_h", (e - s) / 60.0))
        return {"codes": codes, "ausser_min": ausser, "netto_h": netto,
                "dienste": len(codes), "frei": 7 - len(codes)}

    def luecken_woche(self, w, arr=None):
        lk = self.luecken(arr)
        a, b = w * 7 * 1440, (w + 1) * 7 * 1440
        out = []
        for (s, e) in lk:
            s2, e2 = max(s, a), min(e, b)
            if e2 > s2:
                out.append((s2, e2))
        return out

    # ---- Persistenz -----------------------------------------------------
    def speichern(self, pfad=PLAN_FILE):
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        with open(pfad, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "start": self.start.isoformat(),
                       "wochen": self.wochen, "config": self.cfg,
                       "dienste": self.dienste}, fh, indent=2, ensure_ascii=False)

    @staticmethod
    def laden(pfad=PLAN_FILE):
        if not os.path.exists(pfad):
            return None
        with open(pfad, encoding="utf-8") as fh:
            d = json.load(fh)
        return Plan(d["config"], parse_datum(d["start"]), int(d["wochen"]),
                    d.get("dienste"))


# --------------------------------------------------------------------------
# Planerzeugung (regelbasierte Suche)
# --------------------------------------------------------------------------
def kandidaten(cfg, person, k=None, pflicht=None):
    """Alle regelkonformen Wochenmuster einer Person.

    Ein Muster ist {wochentag_offset: schichtkuerzel} plus vorberechnete
    Bitmasken fuer Abwesenheit und Erholungsbedarf.  k und pflicht koennen
    reduziert werden - das wird fuer Urlaubswochen gebraucht.
    """
    pc = cfg["personen"][person]
    k = int(pc["dienste_pro_woche"]) if k is None else int(k)
    pflicht = list(pc.get("pflicht_schichten") or []) if pflicht is None \
        else list(pflicht)
    if k == 0:
        return [{"tage": {}, "abw": 0, "erh": 0, "first": None, "last": None,
                 "tageliste": []}]
    erlaubt = list(pc["schichten"].keys())
    ruhe = int(float(pc["min_ruhe_h"]) * 60)
    n = FENSTER * SPD
    res = []

    for tage in combinations(range(7), k):
        if pflicht and len(pflicht) == k:
            varianten = sorted(set(permutations(pflicht)))
        else:
            varianten = [v for v in product(erlaubt, repeat=k)
                         if set(pflicht) <= set(v)]
        for codes in varianten:
            zuord = dict(zip(tage, codes))
            iv = []
            for d in tage:
                sh = pc["schichten"][zuord[d]]
                s = d * 1440 + hhmm(sh["von"])
                e = d * 1440 + hhmm(sh["bis"])
                if e <= s:
                    e += 1440
                iv.append((s, e, zuord[d]))
            iv.sort()
            # Ruhezeit innerhalb der Woche
            if any(iv[i + 1][0] - iv[i][1] < ruhe for i in range(len(iv) - 1)):
                continue
            abw = erh = 0
            for (s, e, code) in iv:
                sh = pc["schichten"][code]
                abw |= rmask(s // SLOT, e // SLOT, n)
                vh = int(float(sh.get("erholung_vor_h", 0)) * 60)
                nh = int(float(sh.get("erholung_nach_h", 0)) * 60)
                if vh:
                    erh |= rmask((s - vh) // SLOT, s // SLOT, n)
                if nh:
                    erh |= rmask(e // SLOT, (e + nh) // SLOT, n)
            res.append({"tage": zuord, "abw": abw, "erh": erh,
                        "first": iv[0][0], "last": iv[-1][1],
                        "tageliste": list(tage)})
    return res


VARIANTEN = {
    "lueckenfrei": {
        "beschreibung": "Betreuungsluecken strikt vermeiden",
        "gap": 10.0, "konflikt": 3.0, "we": 60.0, "we_maria": 150.0,
        "block": 8.0, "stabil": -40.0, "famtag": -20.0,
    },
    "familienzeit": {
        "beschreibung": "moeglichst viele gemeinsame freie Tage",
        "gap": 10.0, "konflikt": 3.0, "we": 60.0, "we_maria": 150.0,
        "block": 8.0, "stabil": -20.0, "famtag": -220.0,
    },
    "rotierend": {
        "beschreibung": "Wochenenden rotieren, kleine Luecken erlaubt (Standard)",
        "gap": 4.0, "konflikt": 3.0, "we": 500.0, "we_maria": 150.0,
        "block": 8.0, "stabil": 0.0, "famtag": -20.0,
    },
}


def generiere(plan, variante="rotierend", report=None):
    """Erzeugt Woche fuer Woche das beste zulaessige Muster.

    Harte Regeln: Dienste/Woche, Pflichtschichten, Ruhezeit, Wunschfrei und
    das Wochenbudget fuer Fremdbetreuung. Alles andere wird gewichtet.
    """
    cfg = plan.cfg
    g = VARIANTEN.get(variante, VARIANTEN["rotierend"])
    budget = int(float(cfg["externe_hilfe"]["max_h_pro_woche"]) * 60)
    personen = plan.personen
    cache = {}

    def kand_fuer(p, k, mit_pflicht):
        schl = (p, k, mit_pflicht)
        if schl not in cache:
            cache[schl] = kandidaten(cfg, p, k,
                                     None if mit_pflicht else [])
        return cache[schl]

    for p in personen:
        if not kand_fuer(p, None, True):
            raise SystemExit("Keine zulaessige Wochenkombination fuer %s - "
                             "Regeln pruefen (Ruhezeit/Dienste pro Woche)." % p)

    wochenmaske = rmask(0, 7 * SPD, FENSTER * SPD)
    letztes_ende = dict((p, None) for p in personen)   # absolut in Minuten
    we_ist = dict((p, 0) for p in personen)
    vorwoche = dict((p, None) for p in personen)
    carry = dict((p, 0) for p in personen)             # Nacht-Ueberhang
    carry_erh = dict((p, 0) for p in personen)
    warnungen = []

    for p in personen:
        plan.dienste[p] = {}

    for w in range(plan.wochen):
        basis = w * 7
        wtag = [plan.datum(basis + d).weekday() for d in range(7)]
        we_tage = set(d for d in range(7) if wtag[d] >= 5)
        gesperrt = {}
        for p in personen:
            frei = set()
            for s in cfg["personen"][p].get("wunschfrei") or []:
                try:
                    di = plan.index(parse_datum(s))
                except ValueError:
                    continue
                if basis <= di < basis + 7:
                    frei.add(di - basis)
            gesperrt[p] = frei

        # zulaessige Kandidaten der Woche je Person; sind zu viele Tage
        # gesperrt (Urlaub), wird die Dienstzahl schrittweise reduziert
        moeglich = {}
        for p in personen:
            ruhe = int(float(cfg["personen"][p]["min_ruhe_h"]) * 60)
            voll = int(cfg["personen"][p]["dienste_pro_woche"])
            liste = []
            for stufe in range(voll, -1, -1):
                roh = kand_fuer(p, stufe, stufe == voll)
                liste = []
                for kd in roh:
                    if gesperrt[p] & set(kd["tageliste"]):
                        continue
                    if letztes_ende[p] is not None and kd["first"] is not None:
                        if basis * 1440 + kd["first"] - letztes_ende[p] < ruhe:
                            continue
                    liste.append(kd)
                if liste:
                    if stufe < voll:
                        plan.reduziert.setdefault(p, set()).add(w)
                    break
            if not liste:
                raise SystemExit("Woche %d: keine zulaessige Loesung fuer %s."
                                 % (w + 1, p))
            moeglich[p] = liste

        bestes = bestwert = None
        notloesung = notwert = None
        for kombi in product(*[moeglich[p] for p in personen]):
            zu = dict(zip(personen, kombi))
            # Betreuungsluecke = alle Erwachsenen gleichzeitig abwesend
            luecke = wochenmaske
            for p in personen:
                luecke &= (zu[p]["abw"] | carry[p])
            gap_min = bits(luecke) * SLOT

            # Erholungskonflikt: jemand ist allein zustaendig, obwohl er
            # nach Nacht-/Spaetdienst eigentlich schlafen muesste
            konf_min = 0
            for p in personen:
                andere = 0
                for q in personen:
                    if q != p:
                        andere |= (zu[q]["abw"] | carry[q])
                eigen = zu[p]["abw"] | carry[p]
                konf = (zu[p]["erh"] | carry_erh[p]) & andere & ~eigen & wochenmaske
                konf_min += bits(konf) * SLOT

            # gemeinsame freie Tage (niemand arbeitet, keine Nacht laeuft aus)
            belegt = 0
            for p in personen:
                belegt |= (zu[p]["abw"] | carry[p])
            famtage = sum(1 for d in range(7)
                          if not (belegt & rmask(d * SPD, (d + 1) * SPD,
                                                 FENSTER * SPD)))

            wert = gap_min * g["gap"] + konf_min * g["konflikt"] \
                + famtage * g["famtag"]
            for p in personen:
                pc = cfg["personen"][p]
                soll = float(pc.get("wochenenddienste_pro_4_wochen", 0)) / 4.0
                ist = we_ist[p] + len(we_tage & set(zu[p]["tageliste"]))
                gew = g["we_maria"] if soll == 0 else g["we"]
                wert += abs(ist - soll * (w + 1)) * gew
                tl = zu[p]["tageliste"]
                wert += ((tl[-1] - tl[0] + 1) - len(tl)) * g["block"]
                if vorwoche[p] == zu[p]["tage"]:
                    wert += g["stabil"]
            if notwert is None or wert < notwert:
                notwert, notloesung = wert, zu
            if gap_min > budget:          # harte Budgetgrenze
                continue
            if bestwert is None or wert < bestwert:
                bestwert, bestes = wert, zu

        if bestes is None:
            bestes, bestwert = notloesung, notwert
            warnungen.append("Woche %d: Fremdbetreuungsbudget nicht einhaltbar."
                             % (w + 1))

        for p in personen:
            kd = bestes[p]
            for d, code in kd["tage"].items():
                plan.setze(p, basis + d, code)
            if kd["last"] is not None:
                letztes_ende[p] = basis * 1440 + kd["last"]
            we_ist[p] += len(we_tage & set(kd["tageliste"]))
            vorwoche[p] = kd["tage"]
            carry[p] = kd["abw"] >> (7 * SPD)
            carry_erh[p] = kd["erh"] >> (7 * SPD)
        if report is not None:
            report.append((w, bestwert))
    for t in warnungen:
        print("  ! " + t)
    return plan


def neuer_plan(start=None, wochen=None, cfg=None, variante="rotierend"):
    cfg = json.loads(json.dumps(cfg or DEFAULT_CONFIG))
    s = parse_datum(start or cfg["start"])
    w = int(wochen or cfg["wochen"])
    cfg["start"], cfg["wochen"] = s.isoformat(), w
    cfg["variante"] = variante
    return generiere(Plan(cfg, s, w), variante)


# --------------------------------------------------------------------------
# Darstellung
# --------------------------------------------------------------------------
def kasten(titel, breite=86):
    print(c("╭─ " + titel + " " + "─" * max(0, breite - len(titel) - 4)
            + "╮", "blau"))


def linie(breite=86):
    print(c("╰" + "─" * (breite - 2) + "╯", "blau"))


def balken(arr, di, farbig=True):
    """24 Zeichen - je Zeichen eine Stunde, schlechtester Wert der Stunde."""
    out = []
    for h in range(24):
        m = min(arr[di * SPD + h * 4: di * SPD + h * 4 + 4])
        if m <= 0:
            out.append(c("!", "rot") if farbig else "!")
        elif m == 1:
            out.append(c("▒", "gelb") if farbig else "-")
        else:
            out.append(c("█", "gruen") if farbig else "#")
    return "".join(out)


def stundenlineal():
    zeile = [" "] * 26
    for h in range(0, 24, 3):
        for i, ch in enumerate(str(h)):
            zeile[h + i] = ch
    return "".join(zeile)[:24]


def zelle(plan, p, di, breite):
    code = plan.code(p, di)
    teile = []
    vor = plan.code(p, di - 1) if di > 0 else None
    if vor:
        sh = plan.schicht(p, vor)
        if hhmm(sh["bis"]) <= hhmm(sh["von"]):
            teile.append("(%s bis %s)" % (vor, sh["bis"]))
    if code:
        sh = plan.schicht(p, code)
        ue = "+1" if hhmm(sh["bis"]) <= hhmm(sh["von"]) else ""
        teile.append("%s %s–%s%s" % (code, sh["von"], sh["bis"], ue))
    if not teile and plan.ist_frei_gewuenscht(p, di):
        teile.append("Urlaub / frei")
    txt = "  ".join(teile) if teile else "–"
    return txt.ljust(breite)[:breite]


def zeige_plan(plan, wochen=None, farbig=True):
    arr = plan.abdeckung()
    maxh = float(plan.cfg["externe_hilfe"]["max_h_pro_woche"])
    ws = range(plan.wochen) if wochen is None else wochen
    for w in ws:
        a = w * 7
        kasten("WOCHE %d   %s – %s   (KW %d)"
               % (w + 1, d_kurz(plan.datum(a)), d_kurz(plan.datum(a + 6)),
                  plan.datum(a).isocalendar()[1]))
        print("  %-10s %-21s %-17s  %-24s %s"
              % ("Datum", "Manuel", "Maria", "Betreuung rund um die Uhr",
                 "Lücke"))
        print(" " * 54 + c(stundenlineal(), "grau") + " Uhr")
        for di in range(a, a + 7):
            d = plan.datum(di)
            tag = d_kurz(d)
            if d.weekday() >= 5:
                tag = c(tag, "magenta") if farbig else tag
            tagluecke = sum(SLOT for sl in range(di * SPD, (di + 1) * SPD)
                            if arr[sl] <= 0)
            lt = c(hm(tagluecke), "rot") if (tagluecke and farbig) else \
                (hm(tagluecke) if tagluecke else c("–", "grau") if farbig else "-")
            print("  %-10s %s %s  %s %s"
                  % (tag + " " * max(0, 10 - len(d_kurz(d))),
                     zelle(plan, "Manuel", di, 21),
                     zelle(plan, "Maria", di, 17),
                     balken(arr, di, farbig), lt))
        sm = plan.wochenstat("Manuel", w)
        sf = plan.wochenstat("Maria", w)
        lk = plan.luecken_woche(w, arr)
        lkh = sum(e - s for s, e in lk)
        print("  " + c("Manuel", "cyan") +
              ": %d Dienste (%s) · %s h außer Haus · %.1f h Arbeitszeit · %d freie Tage"
              % (sm["dienste"], "".join(sm["codes"]) or "–",
                 hm(sm["ausser_min"]), sm["netto_h"], sm["frei"]))
        print("  " + c("Maria ", "cyan") +
              ": %d Dienste (%s) · %s h außer Haus · %.1f h Arbeitszeit · %d freie Tage"
              % (sf["dienste"], "".join(sf["codes"]) or "–",
                 hm(sf["ausser_min"]), sf["netto_h"], sf["frei"]))
        farbe = "gruen" if lkh <= maxh * 60 else "rot"
        print("  " + c("Betreuung", "cyan") +
              ": %s h Fremdbetreuung nötig (Budget %s h) %s"
              % (hm(lkh), hm(maxh * 60),
                 c("✓", farbe) if farbig else ""))
        linie()
        print()
    print("  Legende:  " + (c("█", "gruen") if farbig else "#") +
          " beide zuhause   " + (c("▒", "gelb") if farbig else "-") +
          " eine/r zuhause   " + (c("!", "rot") if farbig else "!") +
          " Betreuungslücke")
    print("            f = Frühdienst   s = Spätdienst   n = Nachtdienst   U = Urlaub   – = frei")


def kompakt_zeichen(plan, p, di):
    code = plan.code(p, di)
    if code:
        return code
    if plan.ist_frei_gewuenscht(p, di):
        return "U"
    vor = plan.code(p, di - 1) if di > 0 else None
    if vor:
        sh = plan.schicht(p, vor)
        if hhmm(sh["bis"]) <= hhmm(sh["von"]):
            return ">"        # Nachtdienst laeuft in den Vormittag hinein
    return "·"


def zeige_kompakt(plan, stil="tabelle", farbig=True):
    """Sehr kompakte Darstellung des Dienstplans."""
    arr = plan.abdeckung()
    kopf = [WDK[plan.datum(d).weekday()] for d in range(7)]

    if stil == "zeile":
        for w in range(plan.wochen):
            a = w * 7
            teile = []
            for p in plan.personen:
                s = "".join(kompakt_zeichen(plan, p, di) for di in range(a, a + 7))
                teile.append("%s:%s" % (plan.cfg["personen"][p]["kuerzel"], s))
            lkh = sum(e - s for s, e in plan.luecken_woche(w, arr))
            print("W%-2d %s  %s  | Lücke %s h"
                  % (w + 1, plan.datum(a).strftime("%d.%m."), "  ".join(teile),
                     hm(lkh)))
        print("(%s = Reihenfolge %s ab Startdatum, · = frei, > = Nacht läuft aus)"
              % ("Spalten", "/".join(kopf)))
        return

    if stil != "plain":
        kasten("KOMPAKTPLAN   %s – %s"
               % (d_kurz(plan.start), d_kurz(plan.datum(plan.tage - 1))))
    print("  %-6s %-14s %-5s %s   %-9s %s"
          % ("Woche", "Zeitraum", "Wer", " ".join("%-2s" % k for k in kopf),
             "kompakt", "Lücke"))
    for w in range(plan.wochen):
        a = w * 7
        zeit = "%02d.%02d.–%02d.%02d." % (plan.datum(a).day, plan.datum(a).month,
                                          plan.datum(a + 6).day, plan.datum(a + 6).month)
        lkh = sum(e - s for s, e in plan.luecken_woche(w, arr))
        for i, p in enumerate(plan.personen):
            zeichen = [kompakt_zeichen(plan, p, di) for di in range(a, a + 7)]
            spalten = " ".join("%-2s" % z for z in zeichen)
            komp = "".join(zeichen)
            print("  %-6s %-14s %-5s %s   %-9s %s"
                  % ("W%d" % (w + 1) if i == 0 else "",
                     zeit if i == 0 else "",
                     plan.cfg["personen"][p]["kuerzel"],
                     spalten, komp,
                     (hm(lkh) if lkh else "–") if i == 0 else ""))
        if w < plan.wochen - 1:
            print()
    if stil != "plain":
        linie()
    print("  f Frühdienst · s Spätdienst · n Nachtdienst · U Urlaub · "
          "· frei · > Nacht läuft aus")


def zeige_betreuung(plan, farbig=True):
    arr = plan.abdeckung()
    maxh = float(plan.cfg["externe_hilfe"]["max_h_pro_woche"])
    kasten("BETREUUNG – wer springt wann ein?")
    gesamt = 0
    for w in range(plan.wochen):
        lk = plan.luecken_woche(w, arr)
        summe = sum(e - s for s, e in lk)
        gesamt += summe
        anteil = summe / (maxh * 60) if maxh else 0
        voll = int(round(min(anteil, 1.0) * 20))
        bal = "█" * voll + "░" * (20 - voll)
        bal = c(bal, "gruen" if summe <= maxh * 60 else "rot") if farbig else bal
        print("  Woche %-2d %s  %5s h von %s h Fremdbetreuung"
              % (w + 1, bal, hm(summe), hm(maxh * 60)))
        if not lk:
            print("           %s" % (c("keine Lücke – komplett selbst abgedeckt", "grau")
                                     if farbig else "keine Luecke"))
        for (s, e) in lk:
            d = plan.datum(s // 1440)
            print("           %s  %s–%s  (%s h)  → %s"
                  % (d_kurz(d), mmhh(s), mmhh(e), hm(e - s),
                     ", ".join(plan.cfg["externe_hilfe"]["namen"])))
    linie()
    print("  Gesamt über %d Wochen: %s h Fremdbetreuung "
          "(Ø %s h/Woche, Budget %s h/Woche)"
          % (plan.wochen, hm(gesamt), hm(gesamt / float(plan.wochen)), hm(maxh * 60)))


def regelpruefung(plan):
    """Liefert Liste (status, text); status in ok/warn/fail."""
    res = []
    arr = plan.abdeckung()
    maxh = float(plan.cfg["externe_hilfe"]["max_h_pro_woche"])
    for p in plan.personen:
        pc = plan.cfg["personen"][p]
        soll = int(pc["dienste_pro_woche"])
        ruhe = float(pc["min_ruhe_h"])
        pflicht = pc.get("pflicht_schichten") or []
        vertrag = float(pc["vertrag_h_woche"])
        urlaub = [w for w in range(plan.wochen)
                  if plan.urlaubswoche(p, w)
                  and plan.wochenstat(p, w)["dienste"] < soll]
        normal = [w for w in range(plan.wochen) if w not in urlaub]
        fehl = []
        for w in normal:
            st = plan.wochenstat(p, w)
            if st["dienste"] != soll:
                fehl.append("W%d: %d statt %d Dienste" % (w + 1, st["dienste"], soll))
        res.append(("ok" if not fehl else "fail",
                    "%s: %d Dienste pro Woche  %s" % (p, soll, "; ".join(fehl))))
        if pflicht:
            fehl = [("W%d: %s fehlt" % (w + 1, "/".join(
                sorted(set(pflicht) - set(plan.wochenstat(p, w)["codes"])))))
                for w in normal
                if not set(pflicht) <= set(plan.wochenstat(p, w)["codes"])]
            res.append(("ok" if not fehl else "fail",
                        "%s: jede Schicht (%s) mindestens 1x pro Woche  %s"
                        % (p, "/".join(pflicht), "; ".join(fehl))))
        if urlaub:
            res.append(("warn",
                        "%s: %d Urlaubswoche(n) mit weniger Diensten (%s) – "
                        "oben nicht mitgerechnet"
                        % (p, len(urlaub),
                           ", ".join("W%d" % (w + 1) for w in urlaub))))
        iv = plan.intervalle(p)
        verst = []
        for i in range(len(iv) - 1):
            pause = iv[i + 1][0] - iv[i][1]
            if pause < ruhe * 60:
                verst.append("%s → %s: nur %s h"
                             % (d_kurz(plan.datum(iv[i][3])),
                                d_kurz(plan.datum(iv[i + 1][3])), hm(pause)))
        mini = min([iv[i + 1][0] - iv[i][1] for i in range(len(iv) - 1)] or [0])
        res.append(("ok" if not verst else "fail",
                    "%s: Ruhezeit ≥ %.0f h  (kürzeste Pause %s h)  %s"
                    % (p, ruhe, hm(mini), "; ".join(verst))))
        abw = [abs(plan.wochenstat(p, w)["netto_h"] - vertrag) for w in normal] or [0.0]
        res.append(("ok" if max(abw) <= 0.1 else "warn",
                    "%s: Vertrag %.1f h/Woche  (Ist %.1f h, größte Abweichung %.1f h)"
                    % (p, vertrag,
                       plan.wochenstat(p, normal[0] if normal else 0)["netto_h"],
                       max(abw))))
    lkges = []
    for w in range(plan.wochen):
        lkges.append(sum(e - s for s, e in plan.luecken_woche(w, arr)))
    schlimm = max(lkges) if lkges else 0
    res.append(("ok" if schlimm <= maxh * 60 else "fail",
                "Tochter: 24-h-Betreuung sichergestellt "
                "(max. %s h Fremdbetreuung/Woche, Budget %s h)"
                % (hm(schlimm), hm(maxh * 60))))
    for p in plan.personen:
        wf = plan.cfg["personen"][p].get("wunschfrei") or []
        if wf:
            verletzt = [s for s in wf
                        if plan.code(p, plan.index(parse_datum(s))) is not None]
            res.append(("ok" if not verletzt else "fail",
                        "%s: Urlaub/Wunschfrei eingehalten (%d Tage)%s"
                        % (p, len(wf), "" if not verletzt else " – " + ", ".join(verletzt))))
    return res


def zeige_check(plan, farbig=True):
    kasten("REGELPRÜFUNG")
    for status, text in regelpruefung(plan):
        sym = {"ok": ("✓", "gruen"), "warn": ("!", "gelb"), "fail": ("✗", "rot")}[status]
        print("  %s  %s" % (c(sym[0], sym[1]) if farbig else sym[0], text.rstrip()))
    linie()


def zeige_tag(plan, d, farbig=True):
    di = plan.index(d)
    if di < 0 or di >= plan.tage:
        print("  %s liegt außerhalb des Plans (%s – %s)."
              % (d_kurz(d), d_kurz(plan.start), d_kurz(plan.datum(plan.tage - 1))))
        return
    arr = plan.abdeckung()
    kasten("%s, %02d.%02d.%d – Tagesansicht"
           % (WDL[d.weekday()], d.day, d.month, d.year))
    print("       " + "".join("%-4s" % ("%02d" % h) for h in range(0, 24, 2)))
    print("       " + "".join("|   " for _ in range(12)))
    for p in plan.personen:
        zeile = []
        for sl in range(di * SPD, (di + 1) * SPD, 2):
            weg = False
            for (s, e, _c, _d) in plan.intervalle(p):
                if s // SLOT <= sl < e // SLOT:
                    weg = True
            zeile.append((c("░", "grau") if farbig else ".") if weg
                         else (c("█", "cyan") if farbig else "#"))
        code = plan.code(p, di)
        info = ""
        if code:
            sh = plan.schicht(p, code)
            info = " %s %s–%s" % (code, sh["von"], sh["bis"])
        print("  %-5s" % plan.cfg["personen"][p]["kuerzel"] + "".join(zeile) + info)
    zeile = []
    for sl in range(di * SPD, (di + 1) * SPD, 2):
        m = min(arr[sl], arr[sl + 1])
        zeile.append((c("!", "rot") if farbig else "!") if m <= 0
                     else (c("█", "gruen") if farbig else "#") if m >= 2
                     else (c("▒", "gelb") if farbig else "-"))
    print("  %-5s" % "Kind" + "".join(zeile))
    linie()
    print("  █ zuhause / abgedeckt   ░ außer Haus   ! Betreuungslücke "
          "(48 Zeichen = je 30 Minuten)")


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def export_csv(plan, pfad):
    arr = plan.abdeckung()
    with open(pfad, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh, delimiter=";")
        kopf = ["Datum", "Wochentag", "Woche"]
        for p in plan.personen:
            kopf += [p + "_Code", p + "_Von", p + "_Bis"]
        kopf += ["Betreuungsluecke_h"]
        wr.writerow(kopf)
        for di in range(plan.tage):
            d = plan.datum(di)
            zeile = [d.strftime("%d.%m.%Y"), WDL[d.weekday()], "W%d" % (di // 7 + 1)]
            for p in plan.personen:
                code = plan.code(p, di)
                if code:
                    sh = plan.schicht(p, code)
                    zeile += [code, sh["von"], sh["bis"]]
                else:
                    zeile += ["", "", ""]
            lk = sum(SLOT for sl in range(di * SPD, (di + 1) * SPD) if arr[sl] <= 0)
            zeile.append(hm(lk))
            wr.writerow(zeile)
    return pfad


def export_ics(plan, pfad):
    def stamp(di, minute):
        d = plan.datum(di) + timedelta(days=minute // 1440)
        return "%sT%02d%02d00" % (d.strftime("%Y%m%d"),
                                  (minute % 1440) // 60, (minute % 1440) % 60)
    zeilen = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Familienplan//DE"]
    nr = 0
    for p in plan.personen:
        for (s, e, code, di) in plan.intervalle(p):
            nr += 1
            sh = plan.schicht(p, code)
            zeilen += ["BEGIN:VEVENT",
                       "UID:fp-%d@familienplan" % nr,
                       "DTSTART:" + stamp(0, s),
                       "DTEND:" + stamp(0, e),
                       "SUMMARY:%s %s (%s)" % (p, sh["name"], code),
                       "END:VEVENT"]
    arr = plan.abdeckung()
    for (s, e) in plan.luecken(arr):
        nr += 1
        zeilen += ["BEGIN:VEVENT",
                   "UID:fp-l-%d@familienplan" % nr,
                   "DTSTART:" + stamp(0, s),
                   "DTEND:" + stamp(0, e),
                   "SUMMARY:BETREUUNG GESUCHT (%s h)" % hm(e - s),
                   "END:VEVENT"]
    zeilen.append("END:VCALENDAR")
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write("\r\n".join(zeilen) + "\r\n")
    return pfad


# --------------------------------------------------------------------------
# Kommandos
# --------------------------------------------------------------------------
def hole_plan(pflicht=True):
    plan = Plan.laden()
    if plan is None and pflicht:
        print("  Noch kein Plan vorhanden – erzeuge Standardplan ...")
        plan = neuer_plan()
        plan.speichern()
    return plan


def cmd_neu(args):
    plan = neuer_plan(args.start, args.wochen, variante=args.variante)
    plan.speichern()
    print("  Neuer Plan erzeugt: %s – %s (%d Wochen), gespeichert unter %s\n"
          % (d_kurz(plan.start), d_kurz(plan.datum(plan.tage - 1)),
             plan.wochen, PLAN_FILE))
    zeige_kompakt(plan)
    print()
    zeige_check(plan)


def cmd_plan(args):
    plan = hole_plan()
    wochen = None
    if getattr(args, "woche", None):
        wochen = [args.woche - 1]
    zeige_plan(plan, wochen)


def cmd_kompakt(args):
    plan = hole_plan()
    zeige_kompakt(plan, "zeile" if args.zeile else ("plain" if args.plain else "tabelle"))


def cmd_betreuung(args):
    zeige_betreuung(hole_plan())


def cmd_check(args):
    zeige_check(hole_plan())


def cmd_tag(args):
    zeige_tag(hole_plan(), parse_datum(args.datum))


def cmd_setze(args):
    plan = hole_plan()
    d = parse_datum(args.datum)
    di = plan.index(d)
    if di < 0 or di >= plan.tage:
        raise SystemExit("  Datum liegt außerhalb des Plans.")
    if args.person not in plan.personen:
        raise SystemExit("  Unbekannte Person. Möglich: %s" % ", ".join(plan.personen))
    code = None if args.schicht in ("frei", "-", "x") else args.schicht
    if code and code not in plan.cfg["personen"][args.person]["schichten"]:
        raise SystemExit("  Unbekannte Schicht %r für %s." % (code, args.person))
    plan.setze(args.person, di, code)
    plan.speichern()
    print("  %s am %s: %s\n" % (args.person, d_kurz(d), code or "frei"))
    zeige_check(plan)


def cmd_vergleich(args):
    alt = Plan.laden()
    start = args.start or (alt.start.isoformat() if alt else None)
    wochen = args.wochen or (alt.wochen if alt else None)
    kasten("SZENARIO-VERGLEICH")
    for name in sorted(VARIANTEN.keys()):
        pl = neuer_plan(start, wochen, variante=name)
        arr = pl.abdeckung()
        luecke = sum(e - s for s, e in pl.luecken(arr))
        we = sum(1 for p in pl.personen for di in range(pl.tage)
                 if pl.code(p, di) and pl.datum(di).weekday() >= 5)
        belegt = set()
        for p in pl.personen:
            for (s, e, _c, _d) in pl.intervalle(p):
                for t in range(s // 1440, (e - 1) // 1440 + 1):
                    belegt.add(t)
        famtage = sum(1 for t in range(pl.tage) if t not in belegt)
        print("  " + c("%-13s" % name, "cyan") + VARIANTEN[name]["beschreibung"])
        print("     Fremdbetreuung %s h gesamt (%s h/Woche) \u00b7 "
              "%d Wochenenddienste \u00b7 %d gemeinsame freie Tage"
              % (hm(luecke), hm(luecke / float(pl.wochen)), we, famtage))
        for w in range(pl.wochen):
            a = w * 7
            teile = ["%s:%s" % (pl.cfg["personen"][p]["kuerzel"],
                                "".join(kompakt_zeichen(pl, p, di)
                                        for di in range(a, a + 7)))
                     for p in pl.personen]
            print("     W%-2d %s" % (w + 1, "  ".join(teile)))
        if args.uebernehmen == name:
            pl.speichern()
            print("     " + c("\u2192 als aktueller Plan gespeichert", "gruen"))
        print()
    linie()
    print("  \u00dcbernehmen mit:  familienplan.py vergleich --uebernehmen <variante>")


def cmd_bilanz(args):
    """Monatsuebersicht - sinnvoll bei langen Planungszeitraeumen."""
    plan = hole_plan()
    arr = plan.abdeckung()
    monate = []
    daten = {}
    for di in range(plan.tage):
        d = plan.datum(di)
        s = (d.year, d.month)
        if s not in daten:
            monate.append(s)
            daten[s] = {"luecke": 0}
            for p in plan.personen:
                daten[s][p] = {"dienste": 0, "we": 0, "h": 0.0}
        z = daten[s]
        z["luecke"] += sum(SLOT for sl in range(di * SPD, (di + 1) * SPD)
                           if arr[sl] <= 0)
        for p in plan.personen:
            code = plan.code(p, di)
            if code:
                z[p]["dienste"] += 1
                z[p]["h"] += float(plan.schicht(p, code).get("netto_h", 0))
                if d.weekday() >= 5:
                    z[p]["we"] += 1
    kasten("BILANZ  %s – %s" % (d_kurz(plan.start), d_kurz(plan.datum(plan.tage - 1))))
    print("  %-10s %s %s" % ("Monat",
                             " ".join("%-24s" % p for p in plan.personen),
                             "Fremdbetreuung"))
    print("  %-10s %s" % ("", " ".join(
        "%-24s" % "Dienste / WE / Stunden" for _ in plan.personen)))
    gesamt = dict((p, {"dienste": 0, "we": 0, "h": 0.0}) for p in plan.personen)
    gl = 0
    for s in monate:
        z = daten[s]
        gl += z["luecke"]
        zellen = []
        for p in plan.personen:
            zellen.append("%-24s" % ("%3d  /  %2d  /  %6.1f h"
                                     % (z[p]["dienste"], z[p]["we"], z[p]["h"])))
            for k in gesamt[p]:
                gesamt[p][k] += z[p][k]
        print("  %-10s %s %s" % ("%02d/%d" % (s[1], s[0]), " ".join(zellen),
                                 hm(z["luecke"]) + " h"))
    zellen = ["%-24s" % ("%3d  /  %2d  /  %6.1f h"
                         % (gesamt[p]["dienste"], gesamt[p]["we"], gesamt[p]["h"]))
              for p in plan.personen]
    print("  " + c("%-10s %s %s" % ("gesamt", " ".join(zellen), hm(gl) + " h"), "fett"))
    linie()
    print("  WE = Dienste an Samstagen und Sonntagen · Stunden = bezahlte Arbeitszeit")


def cmd_urlaub(args):
    plan = hole_plan()
    if args.liste or not args.person:
        kasten("URLAUB / WUNSCHFREI")
        leer = True
        for p in plan.personen:
            tage = sorted(plan.cfg["personen"][p].get("wunschfrei") or [])
            if tage:
                leer = False
            print("  %-8s %s" % (p, ", ".join(
                parse_datum(t).strftime("%d.%m.%Y") for t in tage) or "–"))
        linie()
        if leer:
            print("  Eintragen mit:  familienplan.py urlaub Manuel 24.12.2026 03.01.2027")
        return

    if args.person not in plan.personen:
        raise SystemExit("  Unbekannte Person. Möglich: %s" % ", ".join(plan.personen))
    if not args.von:
        raise SystemExit("  Bitte Datum angeben, z. B. urlaub Manuel 24.12.2026 03.01.2027")
    von = parse_datum(args.von)
    bis = parse_datum(args.bis) if args.bis else von
    if bis < von:
        von, bis = bis, von
    tage = []
    d = von
    while d <= bis:
        tage.append(d.isoformat())
        d += timedelta(days=1)

    pc = plan.cfg["personen"][args.person]
    vorhanden = set(pc.get("wunschfrei") or [])
    if args.loeschen:
        vorhanden -= set(tage)
        wort = "ausgetragen"
    else:
        vorhanden |= set(tage)
        wort = "eingetragen"
    pc["wunschfrei"] = sorted(vorhanden)

    print("  %s: %d Tag(e) %s (%s – %s)"
          % (args.person, len(tage), wort,
             von.strftime("%d.%m.%Y"), bis.strftime("%d.%m.%Y")))
    print("  Plan wird neu berechnet – manuelle Einzeländerungen gehen dabei verloren.\n")
    generiere(plan, plan.cfg.get("variante", "rotierend"))
    plan.speichern()
    zeige_kompakt(plan)
    print()
    zeige_check(plan)


def cmd_export(args):
    plan = hole_plan()
    ziel = args.datei or os.path.join(os.getcwd(), "dienstplan." + args.format)
    (export_csv if args.format == "csv" else export_ics)(plan, ziel)
    print("  Export geschrieben: %s" % ziel)


def cmd_config(args):
    plan = hole_plan()
    if args.bearbeiten:
        pfad = os.path.join(APP_DIR, "config.json")
        with open(pfad, "w", encoding="utf-8") as fh:
            json.dump(plan.cfg, fh, indent=2, ensure_ascii=False)
        print("  Einstellungen exportiert nach %s" % pfad)
        print("  Datei anpassen, dann:  familienplan.py neu --config %s" % pfad)
        return
    print(json.dumps(plan.cfg, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------
# Interaktives Menue
# --------------------------------------------------------------------------
MENUE = [
    ("1", "Dienstplan (große Tabelle)"),
    ("2", "Kompaktansicht"),
    ("3", "Kompakt als Einzeiler (zum Kopieren)"),
    ("4", "Betreuung & Lücken"),
    ("5", "Regelprüfung"),
    ("6", "Tag im Detail"),
    ("7", "Dienst ändern"),
    ("8", "Plan neu berechnen"),
    ("9", "Export (CSV / Kalender)"),
    ("b", "Bilanz pro Monat"),
    ("u", "Urlaub / Wunschfrei"),
    ("v", "Szenarien vergleichen"),
    ("0", "Beenden"),
]


def menue():
    plan = hole_plan()
    while True:
        print()
        kasten("FAMILIEN-DIENSTPLANER   %s – %s   (%d Wochen)"
               % (d_kurz(plan.start), d_kurz(plan.datum(plan.tage - 1)), plan.wochen))
        for k, t in MENUE:
            print("   %s  %s" % (c(k, "cyan"), t))
        linie()
        try:
            wahl = input("  Auswahl: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        print()
        if wahl == "0":
            return
        elif wahl == "1":
            zeige_plan(plan)
        elif wahl == "2":
            zeige_kompakt(plan)
        elif wahl == "3":
            zeige_kompakt(plan, "zeile")
        elif wahl == "4":
            zeige_betreuung(plan)
        elif wahl == "5":
            zeige_check(plan)
        elif wahl == "6":
            zeige_tag(plan, parse_datum(input("  Datum (TT.MM.JJJJ): ")))
        elif wahl == "7":
            p = input("  Person (%s): " % "/".join(plan.personen)).strip()
            d = parse_datum(input("  Datum (TT.MM.JJJJ): "))
            s = input("  Schicht (f/s/n oder 'frei'): ").strip()
            try:
                cmd_setze(argparse.Namespace(person=p, datum=d.isoformat(), schicht=s))
                plan = hole_plan()
            except SystemExit as e:
                print(e)
        elif wahl == "8":
            st = input("  Startdatum [%s]: " % plan.start.strftime("%d.%m.%Y")).strip()
            wo = input("  Anzahl Wochen [%d]: " % plan.wochen).strip()
            va = input("  Variante (%s) [rotierend]: "
                       % "/".join(sorted(VARIANTEN))).strip() or "rotierend"
            plan = neuer_plan(st or plan.start.isoformat(),
                              int(wo) if wo else plan.wochen, variante=va)
            plan.speichern()
            zeige_kompakt(plan)
        elif wahl == "b":
            cmd_bilanz(None)
        elif wahl == "u":
            cmd_urlaub(argparse.Namespace(person=None, von=None, bis=None,
                                          loeschen=False, liste=True))
            p = input("  Person (leer = zurück): ").strip()
            if p:
                v = input("  Von (TT.MM.JJJJ): ").strip()
                b = input("  Bis (leer = ein Tag): ").strip() or None
                lo = input("  Eintragen oder löschen? [e/l]: ").strip().lower() == "l"
                cmd_urlaub(argparse.Namespace(person=p, von=v, bis=b,
                                              loeschen=lo, liste=False))
                plan = hole_plan()
        elif wahl == "v":
            cmd_vergleich(argparse.Namespace(start=plan.start.isoformat(),
                                             wochen=plan.wochen, uebernehmen=None))
        elif wahl == "9":
            f = input("  Format (csv/ics) [csv]: ").strip() or "csv"
            ziel = input("  Datei [./dienstplan.%s]: " % f).strip() or \
                os.path.join(os.getcwd(), "dienstplan." + f)
            (export_csv if f == "csv" else export_ics)(plan, ziel)
            print("  Geschrieben: %s" % ziel)
        else:
            print("  Unbekannte Auswahl.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="familienplan.py",
        description="Familien-Dienstplaner – Schicht- und Betreuungsplanung.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ohne Unterbefehl startet das interaktive Menü.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("neu", help="Plan neu berechnen")
    p.add_argument("--start", help="Startdatum, z. B. 01.11.2026")
    p.add_argument("--wochen", type=int, help="Anzahl Wochen")
    p.add_argument("--variante", choices=sorted(VARIANTEN.keys()),
                   default="rotierend", help="Planungsstrategie")
    p.set_defaults(func=cmd_neu)

    p = sub.add_parser("vergleich", help="Szenarien nebeneinander vergleichen")
    p.add_argument("--start")
    p.add_argument("--wochen", type=int)
    p.add_argument("--uebernehmen", help="eine Variante als Plan speichern")
    p.set_defaults(func=cmd_vergleich)

    p = sub.add_parser("plan", help="große Tabelle anzeigen")
    p.add_argument("--woche", type=int, help="nur diese Woche")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("kompakt", help="kompakte Darstellung")
    p.add_argument("--zeile", action="store_true", help="eine Zeile pro Woche")
    p.add_argument("--plain", action="store_true", help="ohne Rahmen/Farben")
    p.set_defaults(func=cmd_kompakt)

    p = sub.add_parser("betreuung", help="Betreuungslücken und Budget")
    p.set_defaults(func=cmd_betreuung)

    p = sub.add_parser("check", help="Regelprüfung")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("tag", help="einzelnen Tag im Detail")
    p.add_argument("datum")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("setze", help="Dienst manuell setzen")
    p.add_argument("person")
    p.add_argument("datum")
    p.add_argument("schicht", help="f, s, n oder frei")
    p.set_defaults(func=cmd_setze)

    p = sub.add_parser("bilanz", help="Monatsuebersicht (Dienste, Stunden, Luecken)")
    p.set_defaults(func=cmd_bilanz)

    p = sub.add_parser("urlaub", help="Urlaub / Wunschfrei eintragen")
    p.add_argument("person", nargs="?", help="Manuel oder Maria")
    p.add_argument("von", nargs="?", help="Datum oder Beginn des Zeitraums")
    p.add_argument("bis", nargs="?", help="Ende des Zeitraums (optional)")
    p.add_argument("--loeschen", action="store_true", help="Tage wieder austragen")
    p.add_argument("--liste", action="store_true", help="nur anzeigen")
    p.set_defaults(func=cmd_urlaub)

    p = sub.add_parser("export", help="CSV- oder Kalenderdatei schreiben")
    p.add_argument("--format", choices=["csv", "ics"], default="csv")
    p.add_argument("--datei")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("config", help="Einstellungen anzeigen/exportieren")
    p.add_argument("--bearbeiten", action="store_true")
    p.set_defaults(func=cmd_config)

    args = ap.parse_args(argv)
    global FARBE
    if os.environ.get("FP_NOCOLOR"):
        FARBE = False
    if not args.cmd:
        return menue()
    return args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
    except BrokenPipeError:          # z. B. beim Weiterleiten an "head"
        try:
            sys.stdout.close()
        except Exception:
            pass
