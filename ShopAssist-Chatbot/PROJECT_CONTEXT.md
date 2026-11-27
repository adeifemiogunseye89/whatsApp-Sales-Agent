# PROJECT_CONTEXT.md: ShopAssist WhatsApp Sales/Reservation Bot

> **Purpose.** This is the single hand-off file for any new chat session (or new developer).
> Read it fully before suggesting anything. It records the product, the rules we work by, every
> stage built, every decision (including reversals), every live-chat incident and its fix, what is
> pending, and what is risky. If something here contradicts the code, the code wins; then fix this file.
>
> **Last updated:** 2026-11-24. Stage 4.2.1 was delivered on 2026-11-21 and pushed, but its commit missed the one-line isolation change in the nine older test suites; **a fix-up (Stage 4.2.2, tests only) was delivered on 2026-11-24**. On 2026-11-22 to 24 the owner approved the shop-pilot decisions (section 3.6) and confirmed the branch list, ambiguity rule, distance rule, paging and place gazetteer; the Stage 5A design and the demo data design are written (design only, no code).
> **Repo:** `adeifemiogunseye89/whatsApp-Sales-Agent`, project folder `ShopAssist-Chatbot/`.
> **Keep this file alive:** after every stage, update sections 1, 6, 8, 9, 13 and the change log (section 17).

---

## 0. How to start a new chat (paste this first)

```
I'm Adebanjo, continuing my WhatsApp sales/reservation bot (repo adeifemiogunseye89/whatsApp-Sales-Agent,
folder ShopAssist-Chatbot). I'm attaching PROJECT_CONTEXT.md: read it fully and follow section 2
(working agreement) exactly. Current state: see section 1. Today I want to: <your goal>.
My latest `git log --oneline` and test results are below: <paste them>.
```

**First things a new session must do (do not skip):**
1. Read sections 1, 2, 13 and 14 of this file.
2. Ask for (or fetch) the repo's current `git log --oneline` and confirm which stages are applied. Stage 4.2 may not be applied yet.
3. Ask for the output of all nine test suites (section 10). Trust only what was actually run.
4. Never ask the owner to paste an API key into chat. Keys live only in his local `.env`.
5. Do not start building until he says "go". He often wants a deliberation first (see section 2).

---

## 1. State of the project (TL;DR)

- **What it is:** a WhatsApp bot, built as a **demo "gate pass"** to show malls and supermarkets in Nigeria a reserve-for-pickup service they could add. **No pilot shop exists yet.**
- **What works today (all tested):** branch-aware catalogue; code-owned branch selection; code-owned quotes and reservations; hold and grace expiry with automatic release; 3 reservations per phone per rolling 24 hours; cancel and amend by YES/NO; human handoff; per-turn diagnostics; **model lanes** (hosted, backup, local), spend budget, abuse limits, limited mode; lane health check tool.
- **Stages complete:** 1, 2, 2.5, 2.5.1, 3, 3.1, 4, 4.1, 4.2 and the repo-hygiene commit are **pushed**. **Stage 4.2.1 (fixes only) is built and verified; the owner still has to apply it** (`stage4_2_1.patch`).
- **Tests:** 10 suites, **224 tests**, all passing on a fresh clone of the pushed repo with the 4.2.1 patch applied, **and** with a realistic private `.env` planted in the folder (tests are isolated from `.env` since 4.2.1). 31 deliberate breaks were caught in 4.2 and 17 in 4.2.1.
- **Brain status:** the local model (qwen2.5:14b) takes **5 to 8 minutes per model turn** on the owner's CPU-only PC (2 GB GPU), so it is development-only. Hosted lanes answer in **0.8 to 2.8 seconds**. Live-tested on Groq's free tier with `openai/gpt-oss-120b`: worked well, but the free tier allows only about 1 to 2 customer messages a minute. His free Anthropic key has **no API credit**. A paid or higher-limit lane is needed for the demo.
- **Next steps:** apply the Stage 4.2.2 tests fix-up first (`tests_isolation.patch`, then run all ten suites: expect 224); the owner buys Claude (Anthropic) credit and runs a 10-minute test. **No code exists for 4.3 or 5A, by instruction: build only on "go".** Plan: 4.3 (small), then 5A, 5B, 5C, then 6 (section 13). Scope is **one shop with branches**.

---

## 2. The owner and the working agreement (non-negotiable)

**Who:** Adebanjo N. Ogunseye, solo founder and entry-level developer in Nigeria (Lagos, focus on Ibeju-Lekki). Background: sales management, market analysis, strategic planning. JavaScript beginner; Python and PowerShell tasks need exact commands. Windows + PowerShell + Ollama.

**How he wants us to work:**
1. **Brutal candor, no sycophancy, no hallucination.** Say what is weak, what you did not test, and what you are unsure of. Never claim a test, search or run that did not happen.
2. **Situational context first.** Establish the real situation before judging any idea. Give directional expert views with context **before** a decision is taken.
3. **Council of opposing views and loophole-finding.** For each decision present competing approaches, the strongest objections, and how the idea could be abused or fail. He also wants global alternative opinions.
4. **Deliberate before building.** When he says "let's deliberate, do not build, let's see what it will be", give the design, options, risks and decisions needed, then **wait for "go"**.
5. **Stage-by-stage development** with line-by-line explanation, exact placement in the codebase, and verification against each stage's stated needs before moving on.
6. **A recommended GitHub commit message for every stage** (subject of 72 characters or fewer; then why, what, verified, known issues). Past messages are in `outputs/commits/`.
7. **Code references for every function block:** file and line, what calls it, what it leads to.
8. **Deliver whole files or whole functions plus a `.patch`.** Never ask him to edit single lines or use line numbers: pasted indentation and line-number edits have repeatedly failed him.
9. **Show him what to run and what to send back** (exact PowerShell commands, expected output).
10. **He pastes live-chat transcripts for review.** Respond with observations, pushback, root causes, and a fix proposal; do not just praise.
11. **Spend discipline:** hosted model spend must be the barest minimum (he can pay in dollars, roughly $5 prepaid at a time).
12. **Verify before delivery:** run the full suite on a **fresh clone of his pushed repo with the patch applied**; mutation-test every new guard; say plainly what could not be tested (for example real provider calls without a key).

---

## 3. Product vision, scope and decisions

### 3.1 What the bot does
- Customers chat on WhatsApp in plain language (typos, Pidgin-friendly). The bot finds the nearest **branch**, shows branch-specific prices and stock, builds a **quote**, and on the customer's **YES** creates a **reservation** (stock held; pay at the counter at pickup with a pickup code).
- Customers can ask for order status, **amend** quantities, or **cancel** (each needs a YES).
- Anything outside its remit (refunds, complaints) goes to a human via `escalate_to_human`.

### 3.2 Demo-first strategy
- The demo is a **gate pass** to shop owners; there is no pilot shop yet. The pilot would use a **dedicated WhatsApp number** (API-only).
- Shops' own till/stock files are the source of prices and stock (hence the Stage 5 CSV importer).
- "POS" here means **card terminals used for payment only**; in the reserve path the bot **never takes money**.

### 3.3 Reserve alerts to shops (decided, not yet built)
- A plain notification to a **shared customer-care phone per branch** (reps may call customers). One shared number per branch; **no pickup code in the alert**; the alert includes the customer's full number; customer sees: "The shop may call or message you about this reservation."
- Alerts to staff outside the 24-hour WhatsApp window need **approved message templates**.
- No staff-side cancel yet. **Nothing marks orders `COLLECTED` yet**, so reports can show reserved, cancelled and expired but not collected. (The `READY` and `COLLECTED` statuses exist in the schema but no code sets them.)
- Parking reservations: only if customers ask.

### 3.4 Pay-now design (agreed on paper; design doc: https://claude.ai/artifact/1A19VMPe4zsuBXgbYXEkfU)
- Customer chooses **Pay now** or **Reserve**. Pay now uses a **one-time account number per order**, confirmed automatically by a gateway (Paystack, Flutterwave or Monnify, the shop's own account). Fees reported between roughly 0.1% and 1.5% with caps; **sources disagree**.
- Staff manual confirmation only as a fallback, using the provider's real reference, **never a screenshot**.
- Customer bears the transfer fee, shown in the quote with consent saved.
- **No pay now for fresh or weighed items** (catalogue flag `pay_now_eligible`).
- Refunds: customer cancel = minus actual fees; shop-caused failure = full refund; late payment or uncollected after 3 days = minus fees. Prepaid orders cannot be amended.
- Handover verified at the desk (pickup code plus last 4 digits of the phone).
- Pack alert on shared branch phones with quick-reply buttons [Pack now] [Pack in 1 hour] [Can't pack]; backup phones and re-alert after 5 minutes.
- Proposed numbers: **10 pay-now orders per 24 h, at most 2 awaiting payment, about 20-minute unpaid window, 5-minute re-alert, 3-day uncollected refund.** (He confirmed the open design points; confirm these numbers again at Stage 5 start.)
- In the demo, pay now shows in **Preview mode only** (per-shop switch Off / Preview / Sandbox).
- **Needs before real money:** legal review of refund wording; verification of Meta template button limits.

### 3.5 WhatsApp costs (researched; treat as estimates)
- Since 1 October 2026 service replies and in-window utility templates are chargeable after **1,000 free per number per month**; estimates for Nigeria about **₦9 to ₦14 per chargeable message** (sources disagree; billed in USD). Verify before relying on it.
- WhatsApp "coexistence" (using the shop's existing number plus the API) had conflicting vendor claims for Nigeria, so **it is not promised**; plan on a new dedicated number.

### 3.6 Approved decisions (2026-11-22, shop-pilot level only)
**Scope.** Stay at the private-shop pilot level, **narrow to a shop with branches** (owner, 2026-11-22). A *shop* is a multi-branch retailer; a mall run as one retailer counts as one shop, with its stores as branches. **Separate-tenant malls, brand aggregation and a marketplace are out of scope for this product**; the owner may build them later as a separate extension platform with its own rules. WhatsApp numbers are per shop or branch, not per brand. Community/NGO programmes and any government partnership wait for the success of the shop pilot. Keep the engine policy-driven and tenant-aware so those remain possible later, but build nothing special for them now. Vision on record: the product may grow to national relevance, including palliative or festive-season redistribution of food and household items to average-income citizens at lower prices, which makes **preventing hoarding a core requirement**.

**Vision (owner, 2026-11-22):** the original inspiration is multi-tenancy: a shop with branches in many places serving customers where they are. The owner first described a platform where customers choose among many shops, then **narrowed this product to a shop with branches**; a platform for brands, malls or a marketplace may come later as a separate extension with different rules (see `docs/PLATFORM_FOUNDATION_DESIGN.md`, section 12). **Core behavioural hypothesis from the early framework: customers name an item first and want to find which branches have it**, so need-first (item-first) discovery inside a shop is a design requirement, not an extra. The aim is a strong narrow demo on a foundation that does not need rebuilding.

**Approved:** D1 to D10.
- D1 Reserve or Pay-now choice in the bot's own conversation, code-owned, numbered replies (1 Reserve, 2 Pay now), Pay now shown as "coming soon" in the demo (Preview mode), taps logged as a demand signal, fresh or weighed items reserve-only, the bot never takes money.
- D2 Layered anti-hoarding (below). D3 Extend-once: +60 minutes, once, only while active, confirmed by YES, no extra slot.
- D4 Shop alerts and staff actions (collected, no-show, cancel) in Stage 5C. D5 Hold 2 h plus 30 min grace as a per-shop setting.
- D6 **Paid lane: Claude (Anthropic) Haiku 5.5 as primary, with no restriction to one model type** (owner, 2026-11-22: an earlier Haiku-only rule was withdrawn because future needs may outgrow any single model); Stage 4.3 adds **spend-safe flexibility** instead (a price-ceiling guard and a visible cost per turn, see the roadmap); backup Gemini 3.1 Flash-Lite pending a test; local lane off for the demo.
- D7 `check_lanes --models` upgrade now (Stage 4.3); `compare_lanes` deferred until before the pilot.
- D8 Catalogue importer with aliases and fuzzy search in code (5B). D9 A `shops` layer above branches and policies stored as data. D10 Numbered choices now, voice notes and more languages later.

**Anti-hoarding layers (starting values, to be tuned with a real shop):** per phone 3 reservations per 24 h (built); **share** 20% of on-hand per phone per item (owner's choice, smaller than the proposed 40%), counted **cumulatively per phone** (owner's amendment: per-phone fairness, no queue), minimum 2 units; **aggregate ceiling** 60% of on-hand per item per branch across all phones (**the ceiling must hold**); value cap about ₦150,000 per phone; optional absolute per-item cap (`max_per_customer`); no-show memory (5C: after 2 no-shows in 30 days, 1 open reservation until a collection clears it); velocity alert (3 or more phones on one item in 30 minutes); **paid orders bypass share and value caps** (money committed).
**Owner notes:** bulk buyers (for example restaurants) contact the shop or mall; extend-once stays as proposed and is watched; the limits are ON by default in code.

**Policy hierarchy:** settings resolve item, then branch, then shop, then global default. The value cap and the daily reservation limit are counted across the whole shop (all its branches); the share and the ceiling are per branch and item. **Owner's answers (2026-11-22):** over-limit baskets are adjusted with a visible notice; the per-item cap is named `max_per_customer`; the last unit is walk-in only by default; customers see the ₦ value cap but not which share or ceiling rule applied; events store a hashed phone only; limits are ON by default in code with older tests adjusted; a numbered pick from a code-written shortlist confirms the branch; stock is shown as availability bands and options are ranked by availability then closeness (the owner said to keep this logic; recorded as approved). **Narrowing:** no venues and no marketplace in this product. Need-first discovery is designed in `docs/PLATFORM_FOUNDATION_DESIGN.md` and built in Stage 5B.

**Design:** `docs/STAGE_5A_DESIGN.md` (10 mechanisms that make the ceiling hold: one source of truth, atomic check-and-write, re-check at confirm, aggregate not per-phone, whole-basket all-or-nothing, exact release, stock drops never cancel reservations, explicit payment status, self-checks, adversarial tests).

---

## 4. Architecture and principles

### 4.1 Shape
```
WhatsApp user -> Meta Cloud API -> FastAPI /webhook (app/main.py)
   -> run_agent(phone, text) (app/agent.py)
        1. CODE PATHS FIRST (no model, instant): YES/NO to a branch, to a cancel/amend, or to a quote
        2. per-phone limit check (30 model turns per hour)
        3. MODEL PATH: LaneRouter -> primary -> backup -> local (app/llm.py), tools in app/tools.py
        4. GUARDS check the model's wording; code-written text (the "outbox") replaces model text
        5. state saved (SQLite), diagnostics line written (metadata only)
   -> app/whatsapp.py sends the reply (prints to console when no token is set)
```

### 4.2 Principle: **code owns high-stakes steps; the model only chooses tools and phrases answers**
High-stakes = which branch, quotes, creating/cancelling/amending orders, money, expiry, limits. The model can propose; code decides and writes the text the customer sees.

### 4.3 Rules we learned the hard way (apply to all future work)
1. **Never trust model claims** ("branch set", "order created"). Check against state; a reply that fails a guard is rejected and never saved to history.
2. **No examples in prompts or tool descriptions.** Models copy them (the 14B copied "(price) (6 in stock)"; gpt-oss passed "city or area" and "Lekki" as arguments).
3. **Validate model tool arguments against what the customer actually said** (grounding).
4. **Undo side effects when a turn fails** (snapshot/restore). A half-failed turn must not leave a proposal, quote or reply behind.
5. **Classify provider errors carefully.** Rate limit is not billing, even if the message contains the word "billing".
6. **Money is integer kobo** (₦14,500 = 1450000). Spend is integer micro-dollars.
7. **Diagnostics never contain customer text or raw phone numbers.**
8. **Spend and abuse are capped by code**, with an operator alert once a day, not just by a provider setting.
9. **Name the branch once** after it is locked; quotes, receipts and alerts always name it. WhatsApp has no superscript; bold is single-asterisk `*text*`.
10. **Everything stateful is persisted** (sessions in SQLite, idle TTL), so restarts do not lose branch locks or pending confirmations.

---

## 5. Codebase map

### 5.1 Files (`ShopAssist-Chatbot/`)
| File | Role |
|---|---|
| `app/main.py` | FastAPI: webhook verify and receive; startup lists lanes, loads them, starts the hold-release sweeper (every `SWEEP_MINUTES`) |
| `app/agent.py` | System prompt, `run_agent` / `_run_agent` pipeline, guards wiring, limited mode, diagnostics capture |
| `app/llm.py` | **Model lanes**: lane parsing, provider objects, classify and retry, circuit breaker, state-note folding, `prepare()`, `warm_up()` |
| `app/budget.py` | Spend counting (micro-dollars), daily cap, operator alerts once a day, Lagos-day logic |
| `app/tools.py` | LLM-callable tools (below), grounding of `set_pickup_location` |
| `app/orders.py` | Code-owned ordering: quote building and texts, YES/NO parsing, confirm, cancel, amend, receipts |
| `app/branches.py` | Code-owned branch step: ask, list, propose, confirm, reject texts |
| `app/guards.py` | Reality checks on the model's wording about branches |
| `app/session.py` | Per-phone state persisted in SQLite: branch lock, proposals, pending quote/action, history, outbox, handoff, snapshot/restore |
| `app/db.py` | Schema, seed data, branch-aware queries, safe order writes, release logic, usage and alert tables |
| `app/diagnostics.py` | Per-turn record to `logs/turns.jsonl` |
| `app/textfmt.py` | Shared helpers (kobo to naira text, WhatsApp bold, `WAT` timezone) |
| `app/whatsapp.py` | Meta send client with message chunking |
| `app/config.py` | Env-driven settings; reads `.env` itself (no export needed) |
| `chat.py` | Dev terminal chat (`python -m chat`, `--quiet` hides developer lines) |
| `check_lanes.py` | Lane health check (`python -m check_lanes [--models]`) |
| `eval/test_queries.py` | **Legacy** canned-queries harness; needs a running model; not part of the suites |
| `tests/test_stage*.py` | Ten suites (section 10); every one sets `SHOPASSIST_NO_DOTENV=1` before importing the app |
| `.env.example` / `.env` | Template (committed) vs your real private settings (never committed) |
| `README.md`, `CUSTOMIZE.md`, `PROJECT_CONTEXT.md` | Overview, settings guide, this file |

### 5.2 Tools the model may call
`set_pickup_location` (grounded; the system, not the model, shows the result), `search_products`, `get_product_details`, `check_inventory`, `get_price_quote` (prepares a quote; code shows it), `amend_order` and `cancel_order` (prepare an action; the customer must reply YES), `track_order`, `escalate_to_human`. There is **no** `create_order` tool and **no** `confirm_pickup_branch` tool: they were removed on purpose (Stages 3 and 3.1).

### 5.3 Database (SQLite `shop.db`)
`branches` (branch_id, name, city, address, active), `products` (product_id, name, description, category), `branch_inventory` (branch_id, product_id, price_kobo, stock_qty), `orders` (order_id, idempotency_key, branch_id, customer_phone, items_json, subtotal/tax/delivery/total kobo, pickup_code, status, created_at, expires_at, release_at, closed_at), `sessions` (phone, state_json, updated_at), `llm_usage` (day, lane, calls, tokens, cost_micro_usd), `llm_alerts` (day, kind).
Order statuses: `RESERVED`, `READY`, `COLLECTED`, `CANCELLED`, `EXPIRED` (the last two plus `RESERVED` are the only ones code sets today).
Seed data in `db.py`: 3 branches (Lekki, Ikeja, both Lagos; Wuse, Abuja), 6 products, 15 inventory rows. In-place migration keeps older databases working.

### 5.4 Settings (all in `.env`; defaults in `app/config.py`; documented in `.env.example` and `CUSTOMIZE.md`)
- **Business:** `DATABASE_PATH`, `CURRENCY`, `TAX_RATE=0`, `DELIVERY_FEE_KOBO=0`, `RESERVATION_HOURS=2` (owner changed it from 5 on 2026-11-21; whole hours only; the code default and `.env.example` are 2 since 4.2.1), `QUOTE_TTL_MINUTES=10`, `GRACE_MINUTES=30`, `DAILY_RESERVATION_LIMIT=3`, `SWEEP_MINUTES=5`, `LOG_DIR=logs`, `SESSION_TTL_HOURS=24`, `HANDOFF_HOURS=4`, `HISTORY_CAP=20`, `HUMAN_ESCALATION_WEBHOOK`.
- **WhatsApp:** `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `META_API_VERSION`.
- **Local model:** `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (default `llama3.1:8b`), `OLLAMA_NUM_CTX=6144`, `OLLAMA_KEEP_ALIVE=1h`, `LLM_WARM_UP` (off).
- **Brain lanes:** `LLM_PRIMARY`, `LLM_BACKUP`, `LLM_LOCAL` (default `ollama:<OLLAMA_MODEL>`; empty switches the local lane off), keys `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `CEREBRAS_API_KEY`, optional `LLM_PRICE_<SLOT>=input,output` (USD per million tokens).
- **Test isolation:** `SHOPASSIST_NO_DOTENV=1` makes `config.py` skip `.env`. The suites set it; never set it in production.
- **Guards:** `DAILY_BUDGET_USD=0.50` (0 = no cap), `BUDGET_ALERT_PERCENT=80`, `MODEL_TURNS_PER_HOUR=30`, `MAX_MESSAGE_CHARS=600`, `LLM_TIMEOUT_HOSTED=20`, `LLM_TIMEOUT_LOCAL=300`, `LLM_MAX_OUTPUT_TOKENS=300`, `LIMITED_MODE_MESSAGE`.

---

## 6. Stage history

| Stage | Commit (date) | What it added and why |
|---|---|---|
| 0 | `5654512` to `107b766` (Oct 10 to 23) | Original skeleton, first branch recognition, order tracking and pickup codes |
| 1 | `7a2776d` (Oct 24) | Branch-aware data model (branches, per-branch inventory, kobo money, order with pickup code); `set_pickup_location`; branch lock in session. 15 tests |
| 2 | `ad6b324` (Oct 25) | Sessions persisted in SQLite; safe history trimming; real human handoff (`escalate_to_human`, webhook, `HANDOFF_HOURS`); idle TTL. 17 tests |
| 2.5 | `3d0ddb1` (Oct 25) | System-state note shown to the model every turn (branch, proposals, quote). 8 tests |
| 2.5.1 | `05139d3` (Oct 25) | A "proposed" branch equal to the confirmed one is never asked about again. 5 tests |
| 3 | `0ae90d3` (Oct 25) | **Orders created by code** from a customer-confirmed quote; outbox mechanism (code-written text overrides model text); YES/NO parsed by code; `create_order` tool removed; re-order allowed; WhatsApp bold conversion; `QUOTE_TTL_MINUTES=10`. 22 tests |
| 3.1 | `8fb5c15` (Oct 28) | **Branch step code-owned** (`branches.py`); `confirm_pickup_branch` tool removed; `guards.py` rejects invented branches or false "branch set" claims; rejected replies never saved; prompt example conversation removed; `RESERVATION_HOURS` default 5. 19 tests |
| 4 | `7959fce` (Oct 31) | Hold (5 h by default then; **2 h since 2026-11-21**) plus 30 min grace, then release (`release_at`, background sweeper); **3 reservations per phone per rolling 24 h** (cancelled and expired ones still count; the slot is never returned); `cancel_order` and `amend_order` (code-confirmed YES; amend edits in place keeping id, code and hold clock, and uses no extra slot); open reservations in the state note; per-turn diagnostics; in-place DB migration. 35 tests, 17 mutation breaks caught |
| docs | `18f59fc` (Nov 3) | CUSTOMIZE.md matched to Stage 4 |
| 4.1 | `fac8b4c` (Nov 14) | **Model lanes** (primary, backup, local), timeouts, retry, circuit breaker, spend budget in micro-dollars, operator alerts once a day, per-phone turn limit, message length cap, **limited mode**, branch named once, local-lane tuning (context, keep-alive), `chat --quiet`. 43 tests, 24 breaks caught |
| 4.2 | `028f301` (Nov 21; hygiene `09f3329`, docs `8fac66d`, check_lanes path fix `694cbe7`) | Rate limits never mistaken for billing, provider wait honoured, snapshot/restore of failed turns, **grounded `set_pickup_location`**, lane list pick by "2", `check_lanes` tool, lanes loaded at start, failed attempts timed, docs fixed. 42 tests, 31 breaks caught. Patch also fixes a stray `cl` on line 1 of `tests/test_stage3_1.py` |
| 4.2.1 | **delivered 2026-11-21, owner to apply** | Fixes found by running 4.2 on the owner's machine: **tests isolated from the private `.env`** (`SHOPASSIST_NO_DOTENV`; 3 failures there, 7 more in 4.1); **empty model reply retried, never shown** (the literal text `[no reply]` used to reach customers); **look-alike hyphens in order IDs** normalised in tools, limited mode and before the reality check (closes a loophole: an invented ID written with a non-breaking hyphen bypassed the check); clear message for a bad number in `.env`; "1 hour" wording; code default and template hold = 2 h. 18 tests, 17 breaks caught |
| 4.2.2 | **delivered 2026-11-24, owner to apply** | Tests-only fix-up: the one-line `SHOPASSIST_NO_DOTENV` change in the nine older suites that the 4.2.1 commit missed. Verified 224/224 on a brand-new clone of the pushed repo, clean and with a planted `.env` |

Commit messages for every stage are in `outputs/commits/` (`0_combined...` through `14_stage4_2_1_commit.txt`).

---

## 7. The brain: lanes, budget, limits (read before touching `llm.py` or `agent.py`)

### 7.1 Why lanes exist
The owner's PC is CPU-only (2 GB GPU). Measured: qwen2.5:14b about 1.3 tokens/s reading prompts and 0.9 writing; **5 to 8 minutes per model turn**. llama3.1:8b about 2 to 4 minutes; qwen2.5:3b about 1 to 1.5 minutes. None is usable for customers; code-answered turns take milliseconds on any model. A hosted small model answers in seconds.

### 7.2 How lanes work (`app/llm.py`)
- Up to three lanes, each `provider:model`, tried in order **primary, backup, local**. Providers: anthropic, openai, groq, gemini, openrouter, mistral, cerebras, ollama. Model names containing `:` are fine (the split is on the first colon).
- Hosted lanes: short timeout, **one retry**, then the next lane. Errors are classified: `auth`, `billing`, `rate_limit`, `transient`, `other`.
  - `billing` only from HTTP 402 or specific phrases (such as "credit balance is too low" or "insufficient_quota"); **never** from the word "billing" alone.
  - `rate_limit`: if the provider asks for 6 seconds or less, wait and retry once in the same turn; a longer wait rests the lane for that time (never over 2 minutes; 60 s if none given).
  - `auth` and `billing` pause a lane 30 minutes and alert the operator once a day. Three transient failures in a row pause it 60 s.
- The per-turn state note is **folded into the customer's message** for hosted lanes (some providers reject a mid-conversation system message).
- `prepare()` loads every available lane at start (printed by `chat` and server startup). Warm-up of a local model is opt-in (`LLM_WARM_UP=1`) because it can take minutes and must not hang tests.
- `before_retry` hook: `agent.py` passes an undo function so tools' side effects from a failed attempt are reverted before the next attempt or lane.
- If every lane fails or the paid budget is used up: **limited mode** shows `LIMITED_MODE_MESSAGE` ("We're a bit busy; the team will reply shortly."). Code still answers YES/NO, quotes in progress, and naming an order ID. The failed turn's side effects are restored, and the unanswered message is dropped from history.

### 7.3 Spend and abuse (`app/budget.py`, `agent.py`)
- Every call is counted in integer micro-dollars (price per million tokens x tokens) per Lagos day and lane. **Claude Haiku 5.5 is built in at $0.10 in / $0.50 out per million tokens.** An unknown hosted price is assumed pessimistically ($1 / $3) until `LLM_PRICE_<SLOT>` is set (`0,0` for a free tier).
- At `DAILY_BUDGET_USD` (default $0.50) paid lanes pause until tomorrow; the local lane still works. Alerts at `BUDGET_ALERT_PERCENT` and at the cap go to the console and to `HUMAN_ESCALATION_WEBHOOK`.
- Per phone: `MODEL_TURNS_PER_HOUR` (30) and `MAX_MESSAGE_CHARS` (600). Code paths (YES/NO) are never rate limited. This does not stop an attacker with many numbers; the daily budget is the backstop.
- Cost math (assumption: about 3,000 tokens in and 100 out per model call): about $0.00035 per turn, roughly ₦0.47 at ₦1,340 per dollar; **$5 buys about 14,000 turns**. WhatsApp fees (section 3.5) dwarf this.

### 7.4 Provider facts learned (as of Nov 2026)
- **Anthropic API credit is separate from a Claude chat plan.** His "free" key returned HTTP 400 "credit balance is too low": it needs credit bought in the Console (Plans & Billing).
- **Groq retired `llama-3.3-70b-versatile` on 16 Aug 2026.** His key sees: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `allam-2-7b`, plus speech and guard models. Use `python -m check_lanes --models` instead of guessing names.
- **Groq free tier:** 8,000 tokens per minute for `gpt-oss-120b`; one of our model calls is about 2,400 tokens (a tool-using turn makes two calls), so about **1 to 2 customer messages a minute in total**. Free tiers are for building and testing only.
- Free tiers can use data for training and change limits without notice. Use fake data on them. Hosted lanes send chat text (no phone numbers) to the provider.
- OpenAI-compatible base URLs in `llm.py` were written from memory; verify against each provider's docs.

### 7.5 What a good live run looks like
On Groq `gpt-oss-120b`: model times 0.8 to 2.8 s (first call after restart 9 s before Stage 4.2 loaded lanes at start), zero retries, zero guard rejections, correct tool choice, exact prices and stock copied from tool output.

---

## 8. Live-chat findings log (the turns and curves)

| # | What happened | Root cause | Fix / status |
|---|---|---|---|
| 1 | Model invented a "Victoria Island Branch" and three addresses; said "we've set Victoria Island Branch" with no branch set; looped on invented text | 14B model plus a prompt that let it confirm branches | Stage 3.1: branch step in code, branch guard, invented replies never saved |
| 2 | Model echoed prompt examples ("(price)", "(6 in stock)", "Assistant: (call ...)") | Example conversation in the prompt | Example removed (Stage 3.1); rule 4.3.2 |
| 3 | Reservations never expired (stock showed 1 instead of 12) | No hold expiry | Stage 4: hold, grace, release sweeper, in-place migration |
| 4 | Duplicate confirmation sentence after branch YES | Code text plus model text | Open (cosmetic); watch in transcripts |
| 5 | First chat turn 155,920 ms; later 5 to 8 minutes per model turn | CPU-only PC, 12 GB model footprint, 16,384 context, paging | Stage 4.1 lanes; hosted brain recommended |
| 6 | `test_stage3_1` "silent" run | Typing `test_stage3_1.py` alone launches it via file association; PowerShell needs `python tests\test_stage3_1.py` | Documented (section 11) |
| 7 | Anthropic lane: "credit balance is too low" | Key has no API credit | Needs credit; limited mode and alert verified live |
| 8 | Groq lane: 404 model not found | Model retired Aug 2026 | Names updated; `check_lanes --models` |
| 9 | gpt-oss run: customer saw "I couldn't find a branch for 'city or area'" | Model passed words from the prompt as the place | 4.2: grounded branch tool |
| 10 | After the customer confirmed Ikeja, the model proposed Lekki | Example place names in the tool description; model called the tool again | 4.2: examples removed, tool refused right after a code-confirmed branch |
| 11 | Groq 429 "try again in 2.5s" labelled billing; lane locked 30 min | Classifier matched the word "billing" in the upgrade link | 4.2: classification order fixed; short waits served in-turn |
| 12 | A failed turn left `proposed=BR-LKI` nobody saw | Tools had already run before the model call failed | 4.2: snapshot/restore |
| 13 | 9 s first message; 19 to 60 s on failed first turns | Provider packages and graph built on the customer's time | 4.2: `prepare()` at start |
| 14 | Pushed repo contained `shop.db-shm` / `shop.db-wal`, a `requirements` file without `.txt`, and a stray `cl` on line 1 of `tests/test_stage3_1.py` | `git add -A` swept up working files and an accidental keystroke | Hygiene commit and 4.2 patch (owner to apply). No secrets found in any commit |
| 15 | Amend and cancel after a reservation exists never seen on a live hosted model | Not yet run | **Seen working** on gpt-oss-120b (2026-11-21): amend added 3 palm oil, same code, correct totals |
| 16 | Stage 4.2 showed 3 test failures on the owner's PC (4.1 would show 7) | Tests read his private `.env` (price `0,0`, real Groq lane, no local lane); one subprocess test made a real Groq call | 4.2.1: `SHOPASSIST_NO_DOTENV`, every suite isolated, a test that plants a `.env` and re-runs suites |
| 17 | A customer-facing reply was literally `[no reply]` after "yes i need help with groceries" | `agent.py` used it when the model returned empty text (reasoning models can; `LLM_MAX_OUTPUT_TOKENS=300` may be consumed by hidden reasoning) | 4.2.1: empty reply is retried (`rejected=empty_reply`), friendly fallback after 4 tries. Consider raising `LLM_MAX_OUTPUT_TOKENS` to about 800 for reasoning models |
| 18 | The model wrote order IDs with a non-breaking hyphen (U+2011); lookups with it found nothing, and an invented ID written that way bypassed the order check | The check and the tools expected a plain hyphen | 4.2.1: normalise in tools, limited mode and before the check |
| 19 | `RESERVATION_HOURS=1.5` stopped start-up with a bare ValueError; quote said "1 hours"; pushed `config.py` and `.env.example` still said 5 after the owner chose 2 | Plain `int()` parsing; defaults not synced | 4.2.1: clear error message, `hours_phrase`, defaults 2 |
| 20 | "do you have maggic cube" answered as "magic cube"; "red oil" worked once and failed once for Palm Oil; the model asked to confirm instead of preparing the amendment | Search is keyword-only and model-driven; no synonyms or fuzzy matching | **Open**: Stage 5 (importer `aliases` column, fuzzy and synonym search done in code) |
| 21 | "ok thanks" cost a model call and the model restated the pickup code and total (correct, but unnecessary risk) | No fast path for pleasantries | **Open**: code fast path for thanks, ok, bye |
| 22 | The bot treated "clear" as maybe cancelling the reservation and asked first | Safe behaviour; the dev chat command is `reset` | None needed |
| 23 | `test_stage4_2_1` failed on the owner's PC: the "every suite isolates itself" check and the planted-`.env` check (Stage 3.1 went 16/19) | The Stage 4.2.1 commit contained the code and the new suite but **not** the one-line isolation change in the nine older suites; with the planted `.env` (`MAX_MESSAGE_CHARS=7`) Stage 3.1's branch-name tests failed. Reproduced on a clean clone; the new tests did their job | 4.2.2: the nine one-line changes, verified 224/224 clean and with a `.env`. Lesson: after applying a patch, run all ten suites and check `git diff --stat` to confirm every file in the patch landed |

Earlier model comparisons: qwen2.5:14b (original dev model, picked tools reasonably but too slow), llama3.1:8b, qwen3:14b and gpt-oss:20b exist locally but are too heavy for the CPU-only machine.

---

## 9. Decision log (including reversals)

| Decision | Why | Alternatives rejected |
|---|---|---|
| Code owns branch, quote, orders, expiry, limits | Small models lie and drift | Letting the model confirm or create orders (Stages 1 to 2 era) |
| Customer YES/NO is parsed by code | Reliable, testable | Asking the model to interpret confirmations |
| Reservations: hold + 30 min grace; 3 per rolling 24 h; cancelled and expired still count | Prevents hoarding stock | Returning the slot on cancel |
| **Scope: private-shop pilot only** (2026-11-22, owner) | Evidence from a real shop must come before programme or government work | Building allocation features for a partner who does not exist yet |
| **Layered anti-hoarding with an aggregate ceiling** (2026-11-22) | Per-phone limits are bypassed by many SIM cards; only an item-level ceiling bounds total exposure | Per-phone limits alone; a reservation queue (rejected for now) |
| **Commitment-proportional limits: paid orders bypass share and value caps** (2026-11-22) | Money committed removes the hoarding risk and rewards pay-now | The same limits for paid and unpaid orders |
| **Per-phone fairness: the share is cumulative per phone across open reservations** (2026-11-22, owner) | Fairer than per-reservation caps | Per-reservation share; smaller number kept as a tuning option |
| **Claude Haiku 5.5 as the paid primary lane** (2026-11-22, owner's choice; the assistant is Claude, so the endorsement was disclosed as possibly biased) | Price already built in; a 10-minute test confirms tool use | Gemini or Groq as primary |
| **One scope-based engine, narrowed to a shop with branches** (2026-11-22, owner) | A channel serves a shop (all its branches); the scope idea stays so a future extension platform can reuse it | Building two products now; a marketplace inside this product |
| **Need-first (item-first) discovery is a design requirement; a numbered pick from a code-written shortlist is the branch confirmation** (2026-11-22, owner's behavioural hypothesis) | People name a need before a store; fewer turns; the model never writes stores, prices or stock | Branch-first only; letting the model write the shortlist |
| **Venues, marketplace mode and cross-shop discovery dropped from this product** (2026-11-22, owner: keep it narrow to a shop; a separate extension platform may accommodate brands later) | Shops resist being compared; a narrower scope ships sooner; numbers stay per shop or branch | A `venues` concept now; a marketplace stage (the former 5D) |
| **Availability bands instead of exact stock counts by default; options ranked by availability then closeness** (2026-11-22, owner: keep this logic) | Protects shops from competitors and limits gaming of caps | Exact counts everywhere |
| **Foundation columns created in 5A (`products.shop_id`, branch coordinates, contact phone), features built by stage** (2026-11-22) | Tenant ownership is costly to retrofit onto live data; empty columns now are cheap. No `venues` table (narrowed scope) | Adding tenant columns later |
| **No restriction to a single model type; spend-safe flexibility instead** (2026-11-22, owner reversed the earlier Haiku-only rule) | Future needs may outgrow any one model; the lanes already accept any provider and model. The real danger is silent cost (a typo picking a costly model, or a price override hiding it), so any model is allowed but its price must be known or set, a price ceiling blocks lanes above the owner's limit (the owner can raise or disable it), and cost per turn is shown | A Haiku-only allowlist (withdrawn); trusting the `.env` model name with no cost visibility |
| **"Shop" = a shop with branches (a mall run as one retailer counts as one shop); policies cascade global, shop, branch, item** (2026-11-22, owner) | One retailer's branches need differences under one rule set | Single-level policy only |
| **Discovery distance rule** (2026-11-22, owner: "the best approach"): show branches in the customer's own state or within about 50 km; if none is near, name the nearest branch that has the item with its distance; customers may ask to see branches elsewhere | Pickup needs travel; a branch hundreds of kilometres away is not a real option | Listing every branch that has the item |
| **Branch list confirmed** (2026-11-24, owner): the proposed names stand (Abuja's third branch defaults to Gwarinpa); Lagos paging (4 per page) and the small place gazetteer confirmed | Settles the demo geography | Reopening names later |
| **Share limit stays on; demo stocks are scaled up and varied** (2026-11-24, owner aligned with the assistant) | The tiny demo stocks (about 12 units) made 20% look strict; real branches hold tens to hundreds. Removing the share would let one phone fill the whole ceiling. A shop can still tune or disable it through policy | Removing the per-phone share for now |
| **Ambiguous place names are confirmed with the customer, never guessed** (2026-11-22, owner) | Wrong guesses waste a turn later and erode trust; asked only when places genuinely differ | Silent best guess |
| **Demo: one chain with 23 branches in 11 states, live script on 4 cities** (2026-11-22, owner; replaces the earlier five- and 16-branch plans): 7 in Lagos (Lekki, Ikeja, Victoria Island, Ibeju-Lekki, Festac Town, Ajah, Sangotedo), 3 in Abuja, 2 in Port Harcourt, 2 in Ibadan, 2 in Anambra (Onitsha and Awka), 2 in Ogun, and 1 each in Enugu, Kaduna, Kano, Ondo and Delta; the script covers Lagos, Kano, Abuja and Port Harcourt | A national-looking chain makes need-first discovery and the branch step realistic | Five or 16 branches |
| **Share limit 20% per phone per item (cumulative); ceiling 60%; over-limit quotes adjusted with a visible notice** (2026-11-22, owner) | A smaller share means more phones are needed to fill the ceiling; adjusting needs fewer messages than refusing | 40% share; refusing over-limit baskets |
| **Limits ON by default in code** (2026-11-22) | A new shop must be protected from day one | Off by default and enabled per shop (test churn is the cost of the safe default) |
| **Tests never read the developer's `.env`** (2026-11-21) | A private `.env` changed results and let a test call a real provider | Trusting developers to keep `.env` clean |
| **Normalise model output before safety checks** (2026-11-21) | A look-alike character defeated a reality check | Making each check Unicode-aware separately |
| **Hold shortened from 5 h to 2 h** (owner, 2026-11-21; stock is really locked 2.5 h with the grace) | Cut the window a competitor could use to lock stock by about 55% per reservation (5.5 h to 2.5 h), and return unsold stock faster | Keeping 5 h. Concerns raised: Lagos traffic and after-work collection; expired reservations still burn one of the 3 daily slots; no cap on quantity per reservation (one reservation can lock all of an item: 12 of 12 was reserved in a live chat) |
| Amend edits the order in place (same id, code, hold clock, no extra slot) | Customer-friendly and abuse-safe | Cancel and re-reserve |
| Money in integer kobo; spend in integer micro-dollars | No float errors | Floats |
| Sessions in SQLite JSON with idle TTL (Redis later) | Survive restarts without new infrastructure | In-memory dict |
| Diagnostics log metadata only | Privacy | Logging chats |
| Hosted brain for demo, local only for development | Measured 5 to 8 min local turns | Buying a GPU now; waiting on local |
| Provider-agnostic lanes | Models are retired and prices change (it already happened) | Hard-wiring one vendor |
| **Plan changed:** prompt/tool-description diet **dropped** from 4.1 | Saves a fraction of a naira per turn on paid lanes and could change behaviour; revisit after the comparison | Doing it anyway |
| Local warm-up opt-in | It can take minutes and hung-test risk | On by default |
| For demo and pilot keep `LLM_LOCAL=` empty | "We're a bit busy" beats a 5-minute wait | Slow local fallback for customers |
| Free tiers: build and test only | 1 to 2 messages a minute; data-use terms; churn | Using them for the demo |
| One API key per provider | Simplicity | Key pools |
| Pay-now: gateway auto-confirm, customer bears transfer fee, no screenshots, Preview mode in demo | Fraud and support cost | Manual screenshot approval |
| Reserve alerts: one shared phone per branch, no pickup code in alert | Privacy and simplicity | Per-staff alerts |
| New dedicated WhatsApp number for the pilot | Coexistence claims conflict | Reusing a shop's number |

---

## 10. Testing and verification method

**Suites (run from `ShopAssist-Chatbot/`; PowerShell needs the `python` prefix and backslash):**
```powershell
python tests\test_stage1.py     # 15
python tests\test_stage2.py     # 17
python tests\test_stage2_5.py   # 8
python tests\test_stage2_5_1.py # 5
python tests\test_stage3.py     # 22
python tests\test_stage3_1.py   # 19
python tests\test_stage4.py     # 35
python tests\test_stage4_1.py   # 43
python tests\test_stage4_2.py   # 42
python tests\test_stage4_2_1.py # 18   -> total 224
```
- No Ollama, no network, no keys needed: models are faked (`FakeAgent` / fake graphs). Each suite prints `N/N passed` on its last line.
- Tests are organised as **NEEDS** (what the stage must achieve) with named tests per need; a docstring at the top of each suite lists them.
- **Mutation testing:** after writing tests, break the code on purpose (one change at a time on a copy) and confirm a test fails. Weak tests found this way are fixed. Stage 4.2: 31 of 31 caught; Stage 4.2.1: 17 of 17.
- **Isolation rule:** every suite sets `SHOPASSIST_NO_DOTENV=1` before importing the app, so the developer's `.env` (real keys, own lanes, prices, limits) cannot change a result or trigger a real network call. `test_stage4_2_1.py` checks every suite does this and re-runs four suites against a planted `.env`.
- **Delivery check:** clone the owner's pushed repo fresh, apply the patch, run all nine suites. Build patches with `git add -A` then `git diff --cached --binary` (plain `git diff` silently omits new files; this once dropped two files).
- Real providers are never called by tests. `python -m check_lanes` is the manual real-call check. It was run once against Anthropic's real server with a fake key and correctly reported an auth failure.
- `tools/verify_project.py` (in outputs, not the repo) compared his files to the **Stage 4** reference; it is stale for later stages.

---

## 11. Environment, gotchas and repo hygiene

- **Windows + PowerShell.** Prompts starting `PS C:\...>` are PowerShell; `C:\Users\...>` is the old Command Prompt (PowerShell commands fail there with "was unexpected at this time").
- Run tests as `python tests\test_stageX.py`. Typing the file name alone launches it through file association and shows nothing.
- **`.env` vs `.env.example`:** `.env.example` is the committed template (patches update it); `.env` is his private file (patches never touch it). Settings not in `.env` use defaults from `config.py`. In `.env`, active lines must **not** start with `#`; no quotes; spaces around `=` are tolerated; the file must be named `.env` (not `.env.txt`) and sit in `ShopAssist-Chatbot/`.
- **Never put a real key in `.env.example`** (it is committed). If a key was ever shared, rotate it.
- **Packages:** `pip install -r requirements.txt` (the file must be named `requirements.txt`); hosted lanes also need `pip install langchain-anthropic` and/or `pip install langchain-openai` (Groq, Gemini and other OpenAI-compatible providers use the latter).
- **Git hygiene (pending fix):** do not commit `shop.db*`, `logs/`, `.env`. Add `*.db-shm`, `*.db-wal`, `*.db-journal` to `.gitignore`; remove tracked ones with `git rm --cached`. Older commits hold test data from his own chats (no real customers); consider making the repo private rather than rewriting history. Use `git status` before `git add -A`.
- Keystroke accidents have happened (a stray `cl` at the top of a test file). After pasting files, run the suites.

---

## 12. Assets and documents

- **Demo catalogues** (`outputs/demo_catalogue/`): `demo_catalogue_lekki.csv` (150 products), `demo_catalogue_ikeja.csv` (144), `demo_catalogue_wuse.csv` (140). Columns: `sku, name, category, price` (whole naira), `stock, active, pay_now_eligible` ("no" for 15 / 13 / 11 fresh or weighed items). Staples anchored to NBS price figures; branded prices are estimates. `price_calibration_sheet.csv` (15 items) was offered for real shelf prices; **not returned yet**. **Not yet loadable:** do not paste products into `db.py`; the Stage 5 importer will load them. **Planned (Stage 5B):** the demo grows to **23 branches in 11 states**; 20 more catalogues are generated from one master list with regional rules.
- **Design doc, pay-now:** https://claude.ai/artifact/1A19VMPe4zsuBXgbYXEkfU
- **Latency and branch-display analysis:** https://claude.ai/artifact/LKGraFqBww5KgMrZFbzJ23
- **Patches and files per stage:** `outputs/stage1` through `outputs/stage4_2`; commit messages in `outputs/commits/`.
- **Full earlier transcript** (Stages 1 to 4, may not be available in a new session): `/mnt/transcripts/2026-11-20-11-49-43-whatsapp-sales-bot-stages-1-to-4.txt`.
- **Stage 5A design (for review, no code yet):** `docs/STAGE_5A_DESIGN.md`.
- **Platform foundation design (revision 2, for review, no code yet):** `docs/PLATFORM_FOUNDATION_DESIGN.md` (one shop with branches, need-first discovery, behaviour principles, staging, a note on a future extension platform).
- **Demo data design (for review, no code yet):** `docs/DEMO_DATA_DESIGN.md` (generation rules, 15 guardrails, hero scenarios for the 4-city script, the stock-versus-share decision).

---

## 13. Roadmap and open decisions

**Owner actions pending (do these before new feature work):**
1. (Done) hygiene commit, Stage 4.2, docs, hold decision.
2. Apply `stage4_2_1.patch` (`git status` first; discard any local-only edit to `config.py`), run all ten suites (expect 224), commit with `14_stage4_2_1_commit.txt`; refresh the three docs.
3. Re-run the live chat script (section 14); send the transcript and 3 lines of `logs\turns.jsonl`. Watch for `rejected=empty_reply`.
4. Decide the hold length question (section 9) and whether to add a per-reservation quantity cap.
5. Choose the demo lane: buy small Anthropic credit (or another paid tier). Free Groq is dev-only.

**Earlier proposal (superseded by the approved plan below):**
- **Stage 5:** CSV catalogue importer (dry-run, all-or-nothing, row-level errors, Excel quirks, respects open holds, includes `pay_now_eligible` **and an `aliases` column** so "red oil", "maggi", "garri" and common typos find the right product; search by name, aliases and fuzzy match is done **in code**, not left to the model; later URL or Google-Sheet source, then a per-shop adapter); **per-shop settings** (pay-now mode Off/Preview/Sandbox, ordered alert phone list, timers); **reserve alert** to the branch's primary phone via template; customer notice line.
- **Stage 5b:** Preview choice screen (Pay now / Reserve).
- **Stage 6:** webhook hardening (Meta signature check, duplicate message handling, voice notes and media), **numbers guard** (every ₦ and stock figure in a reply must appear in a tool result), concurrency (the server currently blocks the event loop during a model call).
- **A short model comparison** (same 30 scripted chats on the local 8B, a hosted small model, a second vendor; score first-try tool accuracy, rejections, speed, cost per order).
- **Small candidates:** code fast path for thanks/ok/bye; per-reservation quantity or value cap; extend-once for late customers.
- **Later:** sandbox pay now, pack-alert buttons, refunds, terms, staff-side actions (mark READY/COLLECTED, cancel), code fast paths for greetings/thanks, prompt diet if needed, Redis/Postgres swap.
- **Four pay-now numbers to re-confirm at Stage 5 start:** 10 per 24 h, 20-minute unpaid window, 5-minute re-alert, 3-day uncollected refund.

### Approved plan (2026-11-22)
1. **Now (owner):** buy small Claude (Anthropic Console, Plans & Billing) credit; run the 10-minute test script on Haiku 5.5; test Gemini 3.1 Flash-Lite as the backup. Set `LLM_PRIMARY=anthropic:claude-haiku-5-5`, keep `LLM_LOCAL=` empty for the demo, keep `DAILY_BUDGET_USD` on.
2. **Stage 4.3 (small):** `check_lanes --models` shows only chat-capable models, drops the `models/` prefix and shows all names; **plus spend-safe flexibility** (replaces the withdrawn Haiku-only guard): any model is allowed, but every hosted lane needs a known or explicit price, a configurable **price ceiling** (proposed default $3 input / $15 output per million tokens; the owner can raise it or set 0 to disable) blocks lanes priced above it with a clear message and one operator alert a day, and `check_lanes` and the chat banner show an **estimated cost per turn** for each lane.
3. **Stage 5A, quote step v2:** shop settings, share (20%), ceiling (60%) and value limits, Reserve or Pay-now Preview choice with interest logging, extend-once, event log, `shop_report` and the foundation columns. **Design complete with the owner's answers; awaiting "go" (after 4.3).**
4. **Stage 5B, real catalogues and need-first discovery:** CSV importer (aliases, `max_per_customer`, `pay_now_eligible`, dry-run), code-side fuzzy and synonym search, **need-first discovery across a shop's branches** with a code-written shortlist (a numbered pick confirms the branch), availability bands, location (area, and a WhatsApp pin if approved), discovery events. **The demo grows to 23 branches in 11 states** (20 more branches and catalogues are generated and loaded through the importer); region and distance limits (own state or about 50 km), place aliases (VI, PH, Festac, Akwa), a small place gazetteer for nearest-branch search, and confirmation of ambiguous place names are part of 5B. Demo data is generated deterministically and checked by 15 automatic guardrails with pinned hero scenarios (`docs/DEMO_DATA_DESIGN.md`).
5. **Stage 5C, shop side:** alerts (new, large, velocity), staff actions (collected, no-show, cancel), no-show memory.
6. ~~Stage 5D, marketplace mode~~ **Dropped from this product** (2026-11-22, owner): venues, cross-shop discovery and a marketplace would be a separate extension platform with its own rules.
7. **Stage 6:** webhook hardening, numbers guard, voice notes; **`compare_lanes` before any pilot**.
8. **Deferred until the shop pilot succeeds:** community or NGO programme, state or government partnership, allocation (quota) mode, identity tiers, USSD or voice access.

---

## 14. Live test script (hosted lane)

```powershell
python tests\test_stage4_2.py        # expect 42/42
python -m check_lanes --models       # plain-English health check; lists model names
python -m chat
```
In the chat:
1. `reset`, then "do u have basmati rice" -> expect the **standard city question** (no "city or area" leak).
2. "lagos" -> branch list; "2" -> **Lekki proposal**; "yes" -> branch confirmed **plus** the rice answer (no second proposal).
3. "7 units of basmati" -> quote; "make it 4 instead" -> new quote; "yes" -> receipt.
4. **Amend:** "make it 2 instead" -> "Change reservation ORD-...?"; "yes" -> updated, same code.
5. **Cancel:** "cancel my order"; "yes".
6. Reserve until the **3-per-24h limit** message appears; then "where is my order".
7. Send several quick messages to trigger a rate limit: the `[llm]` line must say `rate_limit` (not `billing`) and the bot should recover after a short wait.
Send back: the transcript with the `[turn: ...]` lines and 3 lines of `logs\turns.jsonl` (`in_tokens`, `out_tokens`, `cost_micro_usd`).

---

## 15. Known issues and risk register

| Risk | Severity | Notes |
|---|---|---|
| Prices and stock figures are written by the model | Medium | Copied correctly in live chats, but unverified. Numbers guard planned (Stage 6) |
| Free-tier rate ceiling | High for the demo | Need a paid or higher-limit lane |
| Guards might over-reject generic wording ("pickup code", "was updated") | Low to medium | Diagnostics (`rejected=`) will show it |
| Branch grounding looks only at the last 4 customer messages | Low | Worst case: one extra "which city?" question |
| Server blocks during a model call | Medium for scale | Fine for a demo; fix in Stage 6 |
| Webhook has no signature check, duplicate handling or media handling | High before real traffic | Stage 6 |
| Nothing marks orders READY/COLLECTED; no staff-side cancel | Medium | Reports cannot show "collected" |
| Duplicate confirmation sentence after branch YES | Low (cosmetic) | Watch |
| Model sometimes repeats the branch name despite the prompt | Low | Cosmetic |
| Hosted lanes send chat text to a third party | Medium | Add a consent line; read provider data terms before real customers |
| Hold of 2 h may be too short for genuine customers (traffic, after-work pickup); late ones burn a daily slot | Medium | Watch expiry counts once real use starts; options: extend-once, per-shop hold, longer hold for low-value staples |
| No cap on quantity or value per reservation; the 3-per-24h limit is per phone, so many numbers can still hoard | Medium to high with competitors | Candidate safeguards: max share of a branch's stock per reservation, max value per reservation and per phone, alert the shop on large reservations, no-show tracking once COLLECTED is recorded |
| `RESERVATION_HOURS` accepts whole numbers only (1.5 crashes start-up with a ValueError); the quote text says "1 hours" if set to 1 | Low | Small fix pending |
| Discovery exposes stock and prices to anyone who messages the bot, including a shop's competitors | Medium | Availability bands, per-phone search limits, shortlist size cap (platform design section 6) |
| The 23-branch national demo needs 20 more branches and catalogues (synthetic data, approximate coordinates) | Low to medium | Generate from one master list with regional rules and automatic checks in 5B; label as demo; the owner confirms the proposed branch names and areas |
| The behaviour hypotheses (item-first, 4 options, bands) may be wrong | Medium | Event log and real chats decide; revise the design |
| Anti-hoarding relies on accurate stock numbers; the first people to reserve can use up the ceiling | Medium | 5A: per-phone share, atomic ceiling, 3-per-day; importer and reminders in 5B/5C; tune with real data |
| Legal wording for refunds; Meta template button limits; fee estimates; WhatsApp coexistence | Medium | Verify before real money |
| `eval/test_queries.py` and `tools/verify_project.py` are stale | Low | Not part of the suites |
| Past commits hold his own test chats (WAL files) | Low | No real customers; consider a private repo |
| No successful hosted call has been exercised by automated tests | Medium | Providers are faked; live chats are the proof |

---

## 16. Glossary

**Shop:** a retailer with branches that is our customer (a mall run as one retailer counts as one shop). **Branch:** one collection point with its own stock and prices. **Lock:** the confirmed branch for a chat. **Proposal:** a branch suggested but not yet confirmed by YES. **Quote:** priced basket waiting for YES (valid `QUOTE_TTL_MINUTES`). **Hold:** the reservation period (`RESERVATION_HOURS`); **grace:** extra minutes to collect (`GRACE_MINUTES`); **release:** stock returned to inventory. **Outbox:** code-written text that overrides the model's reply. **Lead:** the code-confirmed branch line shown before the model resumes the customer's earlier question. **Lane:** one provider and model the router can use. **Limited mode:** the busy message when no lane can answer. **Kobo:** 1/100 naira. **Micro-dollar:** 1/1,000,000 dollar. **Grounding:** checking a model's tool argument against what the customer actually typed. **Snapshot/restore:** saving and reverting the state a model turn's tools may change. **Channel:** a WhatsApp number that belongs to one shop (optionally pinned to one branch). **Scope:** the part of the catalogue a channel's customers may discover (in this product, one shop's branches). **Discovery:** finding where an item or basket can be had before choosing where to collect. **Shortlist:** the code-written list of up to 4 branches; a numbered pick confirms the branch.

---

## 17. Change log of this file
- 2026-11-21: first full version, written after Stage 4.2 delivery (covers Stages 1 to 4.2, brain lanes, live-chat findings, decisions, roadmap, risks).
- 2026-11-21: reservation hold changed from 5 h to 2 h by the owner (config.py and .env); decision, risks and settings entry updated.
- 2026-11-24: Stage 4.2.2 added (tests-only fix-up: isolation line missing from nine older suites in the pushed repo; found by the owner's run of `test_stage4_2_1`); owner confirmed the proposed branch names, ambiguity rule, distance rule, Lagos paging and place gazetteer; `docs/DEMO_DATA_DESIGN.md` written (guardrails, scaled stocks, hero scenarios); decision recorded to keep the share limit and scale demo stocks; finding 23 added.
- 2026-11-22: owner changed the demo again to 23 branches in 11 states (7 in Lagos (Lekki, Ikeja, Victoria Island, Ibeju-Lekki, Festac Town, Ajah, Sangotedo), 3 in Abuja, 2 in Port Harcourt, 2 in Ibadan, 2 in Anambra (Onitsha and Awka), 2 in Ogun, and 1 each in Enugu, Kaduna, Kano, Ondo and Delta), approved the distance rule (own state or about 50 km, nearest far branch named with its distance), asked that ambiguous names such as Lekki versus Ibeju-Lekki be confirmed with the customer, and set the live script to 4 cities (Lagos, Kano, Abuja, Port Harcourt); Ogun and Delta branches proposed for confirmation; the design gains a place gazetteer. **This supersedes an earlier same-day entry** (Ajah replacing Ibeju-Lekki, Awka only in Anambra) that came from an earlier draft of the owner's answer.
- 2026-11-22: owner changed the demo to 16 branches in 9 states (5 in Lagos (Lekki, Ikeja, Victoria Island, Ibeju-Lekki, Festac Town), 2 in Abuja, 2 in Port Harcourt, 1 in Enugu, 2 in Ibadan, 1 in Kaduna, 1 in Kano, 1 in Anambra and 1 in Ondo); the design gains `branches.state`, place aliases, region and distance limits and a "more" option on the shortlist.
- 2026-11-22: owner answered the Stage 5A questions (share 20%, adjust with notice, `max_per_customer`, last unit walk-in only, value cap per shop, hashed phone, safe by default), narrowed the product to a shop with branches (no venues, no marketplace), withdrew the Haiku-only rule, and set the demo to five branches; the design documents and roadmap were updated (the former 5D is dropped).
- 2026-11-22: owner set the long-run vision (later narrowed, see above)
- 2026-11-22: owner clarified that "shop" means a mall or multi-branch retailer, and that the paid lane is restricted to Haiku models only; scope, D6, policy hierarchy, glossary, roadmap 4.3, decision log and risks updated; design gains branch-level policies and question 9.
- 2026-11-22: owner approved D1 to D10 and the shop-pilot scope; section 3.6, decision log, roadmap (approved plan) and risks updated; `docs/STAGE_5A_DESIGN.md` written (design only).
- 2026-11-21: Stage 4.2.1 added (isolated tests, empty-reply retry, look-alike order-ID hyphens, setting errors); findings 16 to 22; Stage 4.2 and hygiene marked pushed; roadmap gains aliases/fuzzy search.
