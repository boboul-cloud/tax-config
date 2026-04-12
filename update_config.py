#!/usr/bin/env python3
"""
Scrapes the official French tax brackets from service-public.gouv.fr
and updates tax_config.json automatically.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://www.service-public.gouv.fr/particuliers/vosdroits/F1419"
CONFIG_PATH = Path(__file__).parent / "tax_config.json"


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TaxConfigBot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_year(html: str) -> tuple[int, int]:
    """Extract declaration year and revenue year from page."""
    m = re.search(r"d[ée]claration\s+(\d{4})\s+des\s+revenus\s+de\s+(\d{4})", html, re.IGNORECASE)
    if not m:
        raise ValueError("Could not find year info on page")
    return int(m.group(1)), int(m.group(2))


def parse_brackets(html: str) -> list[dict]:
    """Extract tax brackets from the infographic text."""
    brackets = []

    # Isolate the "Tranches pour 1 part" section to avoid duplicates from examples
    section_match = re.search(r"Tranches pour 1 part.*?Exemple de calcul", html, re.DOTALL)
    section = section_match.group(0) if section_match else html

    # Pattern: "Jusqu'à XX XXX € (tranche N) : taux d'imposition de 0 %"
    m = re.search(
        r"Jusqu['\u2019]à\s+([\d\s\xa0\u202f]+)\s*€\s*\(tranche\s*\d+\)\s*:\s*taux.*?(\d+)\s*%",
        section,
    )
    if m:
        val = int(re.sub(r"[\s\xa0\u202f]", "", m.group(1)))
        brackets.append({"upperBound": val, "rate": int(m.group(2)) / 100})

    # Pattern: "De XX XXX € à YY YYY € (tranche N) : taux d'imposition de NN %"
    for m in re.finditer(
        r"De\s+[\d\s\xa0\u202f]+€\s+à\s+([\d\s\xa0\u202f]+)\s*€\s*\(tranche\s*\d+\)\s*:\s*taux.*?(\d+)\s*%",
        section,
    ):
        val = int(re.sub(r"[\s\xa0\u202f]", "", m.group(1)))
        rate = int(m.group(2))
        brackets.append({"upperBound": val, "rate": rate / 100})

    # Pattern: "Plus de XX XXX € (tranche N) : taux d'imposition de NN %"
    m = re.search(
        r"Plus\s+de\s+[\d\s\xa0\u202f]+€\s*\(tranche\s*\d+\)\s*:\s*taux.*?(\d+)\s*%",
        section,
    )
    if m:
        rate = int(m.group(1))
        brackets.append({"upperBound": None, "rate": rate / 100})

    if len(brackets) < 4:
        raise ValueError(f"Only found {len(brackets)} brackets, expected at least 5")

    return brackets


def parse_plafonnement(html: str) -> tuple[float, float]:
    """Extract plafonnement du quotient familial values."""
    # Standard ceiling per half-part (from couple+1 child examples)
    m = re.search(
        r"avantage\s+fiscal\s+maximal\s+de\s+([\d\s\u202f\xa0]+)\s*€\s+pour\s+son\s+enfant",
        html,
    )
    ceiling = 1807.0  # fallback
    if m:
        ceiling = float(m.group(1).replace(" ", "").replace("\u202f", "").replace("\xa0", ""))

    # Parent isolé ceiling (from parent isolé examples)
    m = re.search(
        r"parent\s+isol[ée]+.*?avantage\s+fiscal\s+maximal\s+de\s+([\d\s\u202f\xa0]+)\s*€",
        html,
        re.DOTALL | re.IGNORECASE,
    )
    ceiling_pi = 4262.0  # fallback
    if m:
        ceiling_pi = float(m.group(1).replace(" ", "").replace("\u202f", "").replace("\xa0", ""))

    return ceiling, ceiling_pi


def parse_legal_reference(html: str) -> str:
    """Extract legal reference (loi de finances)."""
    # Look for "loi de finances pour YYYY" with a nearby date
    m = re.search(r"loi\s+de\s+finances\s+pour\s+\d{4}.*?Journal\s+officiel\s+du\s+(\d{1,2}\s+\w+)", html, re.IGNORECASE | re.DOTALL)
    if m:
        return f"Loi de finances du {m.group(1)}"
    # Try "Vérifié le" date as fallback
    m = re.search(r"V[ée]rifi[ée]\s+le\s+(\d{1,2}\s+\w+\s+\d{4})", html, re.IGNORECASE)
    if m:
        return f"Loi de finances (vérifié le {m.group(1)})"
    return "Loi de finances"


def main():
    print(f"Fetching {SOURCE_URL}...")
    html = fetch_page(SOURCE_URL)

    print("Parsing year...")
    decl_year, rev_year = parse_year(html)
    print(f"  Declaration {decl_year}, revenus {rev_year}")

    print("Parsing brackets...")
    brackets = parse_brackets(html)
    for b in brackets:
        ub = b["upperBound"] or "∞"
        print(f"  → {ub}: {b['rate']*100:.0f}%")

    print("Parsing plafonnement...")
    ceiling, ceiling_pi = parse_plafonnement(html)
    print(f"  Per half-part: {ceiling} €, Parent isolé: {ceiling_pi} €")

    print("Parsing legal reference...")
    legal_ref = parse_legal_reference(html)
    print(f"  {legal_ref}")

    # Load existing config to preserve values we can't scrape (décote, crédits)
    existing = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            existing = json.load(f)

    # Build new config, preserving manually-set values
    new_config = {
        "year": decl_year,
        "revenueYear": rev_year,
        "label": f"Barème {decl_year} — Revenus {rev_year}",
        "legalReference": legal_ref,
        "brackets": brackets,
        "ceilingPerHalfPart": ceiling,
        "ceilingParentIsole": ceiling_pi,
        # Preserve existing décote/deduction/credits if present (not on scraped page)
        "decote": existing.get("decote", {
            "singleThreshold": 1982,
            "coupleThreshold": 3277,
            "singleForfait": 897,
            "coupleForfait": 1483,
            "coefficient": 0.4525,
        }),
        "deduction": existing.get("deduction", {
            "rate": 0.10,
            "min": 504,
            "max": 14426,
        }),
        "credits": existing.get("credits", {
            "emploiDomicile": {"rate": 0.50, "baseCap": 12000, "perChildBonus": 1500, "maxCap": 15000},
            "donsAide": {"rate": 0.75, "cap": 1000},
            "donsAutres": {"rate": 0.66, "incomePercentCap": 0.20},
        }),
    }

    # Check if anything changed
    if existing == new_config:
        print("\n✅ Config is already up to date, no changes needed.")
        return 0

    # Write updated config
    with open(CONFIG_PATH, "w") as f:
        json.dump(new_config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n✅ Updated {CONFIG_PATH}")
    if existing.get("year") and existing["year"] != decl_year:
        print(f"   Year changed: {existing['year']} → {decl_year}")
    if existing.get("brackets") and existing["brackets"] != brackets:
        print("   Brackets changed!")

    return 1  # signal that changes were made


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(2)
