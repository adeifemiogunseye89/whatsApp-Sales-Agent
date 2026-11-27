# DEMO DATA DESIGN: believable, deterministic, checked

> **Status:** DESIGN FOR REVIEW. No generator and no data have been written.
> **Date:** 2026-11-24. **Scope:** the 23-branch, 11-state demo chain (see `PLATFORM_FOUNDATION_DESIGN.md`, section 9) and the guardrails that keep synthetic data trustworthy.
> **Built in:** Stage 5B (the generator and checks), loaded through the 5B importer.

---

## 1. Principles

1. **Synthetic and labelled.** Every file says it is demo data: not real prices, stock or coordinates.
2. **Deterministic.** The same inputs always produce the same files (fixed seed, version stamp), so a demo can be rebuilt exactly.
3. **Designed scenarios are pinned.** The generator fills the catalogue; a short list of "hero" values is fixed by hand so the live story always works.
4. **Checked automatically.** A bad file cannot be produced or imported without a loud message.
5. **Separate from tests.** Tests keep their own small seed (3 branches, 6 products). The demo dataset never feeds the test suites.

---

## 2. Inputs

| Input | Content |
|---|---|
| **Master catalogue** | About 160 products: name, category, unit, base price, class (`staple`, `household`, `packaged`, `fresh`, `premium`), `pay_now_eligible`, aliases |
| **Branch table** | The 23 branches: id, name, city, state, area, coordinates, size tier, contact phone (empty) |
| **Region table** | Price multiplier, assortment additions and exclusions, and starter vocabulary per region |
| **Place gazetteer** | Place names, aliases, state, coordinates for the demo's cities and main neighbourhoods |
| **Hero scenarios** | The pinned stock and availability values behind the 4-city script (section 5) |

**Branch size tiers (proposal):** A = flagship (Lekki, Ikeja, Victoria Island, Wuse, Port Harcourt GRA, Ibadan Bodija, Kano Nassarawa GRA); B = mid (Festac Town, Ajah, Garki, Gwarinpa, Rumuokoro, Enugu GRA, Ibadan Challenge, Kaduna Barnawa, Abeokuta, Onitsha, Akure); C = small (Ibeju-Lekki, Sangotedo, Sango-Ota, Awka, Asaba). Seven, eleven and five branches.

---

## 3. Generation rules

**Prices.** Price = master price x region multiplier x tier adjustment x a small jitter (within 3%), rounded to the nearest ₦50. It must stay within 25% of the master price.

**Assortment.** Every branch stocks the "core 60" items. Others are stocked at 85% to 100% by tier, with regional additions and exclusions (for example ofada rice in the south-west, millet and tuwo-style items in the north).

**Stock** is scaled by item class and branch tier, so quantities look like a real shop and the fairness limits behave sensibly.

| Class | Tier A | Tier B | Tier C |
|---|---|---|---|
| Staples (rice, beans, garri, oil, sugar, flour) | 200 to 600 | 120 to 360 | 60 to 180 |
| Packaged goods | 80 to 300 | 50 to 180 | 25 to 90 |
| Household | 60 to 200 | 40 to 120 | 20 to 60 |
| Fresh (reserve-only) | 20 to 80 | 12 to 50 | 8 to 30 |
| Premium or imported | 8 to 40 | 5 to 25 | 3 to 12 |

On top of that, **5% to 8% of rows are deliberately low (12 or fewer)** to show "Low: N left" and the limits, and **no more than 2% are zero**.

**Aliases.** A starter list per region (for example rice is "shinkafa" in Hausa, "iresi" in Yoruba, "osikapa" in Igbo; red oil is also "epo pupa" and "mmanu nkwu"; "maggi" for seasoning cubes), flagged "to be checked with native speakers".

**Contact phones are empty.** Addresses are generic ("Demo address, Lekki Phase 1").

---

## 4. The guardrails (automatic checks)

Each check is a **blocker** (generation or import stops) or a **warning** (reported, not fatal). They run at generation, at import dry-run, and as automated tests.

| # | Check | Rule | Severity |
|---|---|---|---|
| G1 | Schema | Required columns present; numbers parse; no blank required cells | Blocker |
| G2 | Identity | Unique branch ids and names; a SKU appears once per branch; the same SKU means the same product everywhere | Blocker |
| G3 | Money | Whole naira, at least ₦50, multiples of ₦50; price within 25% of master times the regional multiplier | Blocker |
| G4 | Stock ranges | Whole numbers, never negative; inside the class-and-tier ranges; low-stock rows 5% to 8%; zero-stock rows 2% or fewer | Blocker |
| G5 | Coverage | Every branch has all core items; assortment 85% to 100%; every category present at every branch | Blocker |
| G6 | Geography | Every branch has state, city, area, coordinates; coordinates fall inside the state's rough bounding box; no two branches share a point; same-city branches are 1 to 60 km apart | Blocker |
| G7 | Cross-state pairs | The pairs used in the story behave as intended: Onitsha to Asaba is well under 50 km; Sango-Ota to Ikeja is checked against 50 km (the scenario is adjusted if the numbers disagree) | Blocker |
| G8 | Flags | `pay_now_eligible` is "no" for every fresh or weighed item and "yes" otherwise | Blocker |
| G9 | Aliases | Each alias points to one product per shop; no alias equals another product's real name; regional lists marked unreviewed until checked | Warning |
| G10 | Hero scenarios (golden tests) | Each pinned scenario is run through the real limit and discovery code and must produce the expected answer (section 5) | Blocker |
| G11 | Provenance | Every file carries a header: "DEMO DATA: synthetic", generator version, seed, date | Blocker |
| G12 | Reproducibility | Regenerating gives identical files (checksums match); any rule change bumps the version | Blocker |
| G13 | Importer dry-run | The 5B importer's dry-run reports zero errors and the same row counts as the files | Blocker |
| G14 | Privacy | No real phone numbers, emails or street addresses anywhere | Blocker |
| G15 | Brand names | Product names are generic or clearly fictional; no real brand with an implied price | Warning |

Every run writes a **data report** (row counts, stock and price distributions, check results) so a person can read it in two minutes.

---

## 5. Hero scenarios for the 4-city script (pinned values; limits at 20% share and 60% ceiling)

| City | Pinned values | Expected result |
|---|---|---|
| **Lagos** | Royal Basmati Rice 5kg: Lekki 30, Ikeja 240, Victoria Island 8 | "8 bags" at Lekki becomes 6 (share 6, ceiling 18). Victoria Island shows "Low: 8 left" |
| **Lagos** | "lekki" typed by the customer | The bot asks "Lekki or Ibeju-Lekki?" (ambiguity rule) |
| **Lagos** | Any common item with 5 or more branches in stock | The list shows 4 with "Reply 5 to see more"; the next page shows the rest (7 branches) |
| **Abuja** | Long Grain Rice 2kg: Wuse 40, Garki 4, Gwarinpa 150 | Ranking by availability then closeness; Garki shows "Low: 4 left"; two phones can fill a branch's ceiling, a third is refused |
| **Kano** | One branch; rice requested as "shinkafa" | The alias finds the product |
| **Port Harcourt** | A south-west item (for example ofada rice) not stocked in either local branch | The bot says it is not available near the customer and names the nearest branch with its distance |

Optional: Onitsha and Asaba, or Ota and Lagos, to show the "own state or about 50 km" rule crossing state lines.

---

## 6. Decision: stock levels versus the per-phone share limit

**Question (owner):** with small stocks the 20% share makes the demo look strict ("8 bags becomes 2"). Increase stocks, or remove the share limit for now and add it per shop later?

**Decision: increase and vary the stocks; keep the share limit in the logic, on by default.**

| Option | Verdict | Why |
|---|---|---|
| **Scale stocks up and vary them** (this design) | **Chosen** | The small numbers came from my earlier demo catalogues, which gave staples only about 12 units. A real branch holds tens to hundreds. With realistic stock the share is generous for staples (20% of 240 is 48) and tight only where stock is genuinely low, which is the point |
| Remove the share limit for now | Rejected | The aggregate ceiling is the core defence, but without a per-phone share one phone can fill the whole 60% ceiling alone. That contradicts the owner's own aim ("more phones needed to fill the ceiling") and the requirement to prevent one person hoarding |
| Make the share limit optional per shop | **Already true** | Limits are policy data per shop (and per branch): a shop can raise or lower `share_percent`, or set it to 100 to switch it off. Default stays ON, as decided ("safe by default") |

**What the numbers look like:**
| On-hand | Share per phone (20%, minimum 2) | Ceiling (60%) |
|---|---|---|
| 12 | 2 | 7 |
| 30 | 6 | 18 |
| 120 | 24 | 72 |
| 240 | 48 | 144 |

**Clarification on tests:** the tests' small seed (3 branches, 6 products) is separate and unaffected. But the default development database (`shop.db`) also starts from that tiny seed, so **limit demos must not be run on it**. Until the 5B dataset exists, the 5A live-test script will include a one-line command that sets a few stocks to realistic values.

---

## 7. Outputs

`demo_data/` (outside the test suites): `branches.csv`, one catalogue CSV per branch (23), `places.csv`, `aliases.csv`, `DATA_REPORT.md`, `CHECKSUMS.txt`. The generator is a command (for example `python -m demo_data build`) that fails loudly if any blocker check fails.

---

## 8. Open points for the owner

1. **Tier assignments** (section 2): fine as proposed?
2. **Brand names:** use generic or fictional product names (my recommendation, check G15) rather than real brands?
3. **Scarcity quota:** 5% to 8% low-stock rows and at most 2% zero: fine?
4. **Coordinates** are my approximations from general knowledge, labelled demo estimates. Acceptable for the demo?
5. **Abuja's third branch:** I will use **Gwarinpa** unless you prefer Maitama.
