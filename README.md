# 🛒 Shopping Scout

Type in a product. It searches the **UAE local market** and the **global
market** at the same time, shows you every offer with its URL, price and
reviews, works out what each one *actually* costs delivered to the UAE, and
tells you which one to buy — and why.

```
$ python cli.py "sony wh-1000xm5"

#  STORE               LANDED    RATING    ETA   SCORE  PRODUCT
1  Amazon.ae            1,299  4.6★(2847)     2d    87.6  Sony WH-1000XM5 Wireless Noise Cancel…
2  Noon UAE             1,249  4.5★(312)      2d    83.7  Sony WH-1000XM5 Wireless Noise Cancel…
3  AliExpress           1,101  4.7★(2100)    20d    82.2  Sony WH-1000XM5 Wireless Headphones …
4  eBay                 1,301  4.8★(1842)    12d    81.6  Sony WH-1000XM5 Wireless Noise Cancel…

▶ RECOMMENDED  Sony WH-1000XM5 Wireless Noise Cancelling Headphones, Black
  Amazon.ae — AED 1,299 landed
  https://www.amazon.ae/dp/B09XS7JWHH
  Confidence: high
    • At AED 1,299 landed it costs AED 198 (18%) more than the outright cheapest
      listing, but wins on reviews, delivery and seller reliability.
    • Rated 4.6/5 across 2,847 reviews — enough volume for that score to mean something.
    • Buying locally from Amazon.ae means no customs surprises, local warranty,
      and delivery in about 2 days.
```

## Why "landed cost" and not just price

The cheapest sticker price is usually the wrong answer. A USD 180 item from the
US can land dearer than an AED 720 item bought in Dubai once you add shipping,
5% customs duty and 5% VAT. Every offer is therefore converted to AED and
charged the fees it would really attract, and **ranking happens on that landed
number** — never on the sticker price.

| | |
|---|---|
| Item price | converted to AED at live FX (USD is pegged at 3.6725) |
| Shipping | read from the listing where the store states it, estimated otherwise |
| Customs duty | 5% of goods value, global sellers only |
| VAT | 5% of (goods + freight + duty), global sellers only |
| De-minimis | consignments at or below **AED 300** clear free of both |

## Stores covered

**UAE (local):** Amazon.ae · Noon UAE · Sharaf DG · Carrefour UAE
**Global:** Amazon.com · eBay · AliExpress · Newegg

Adding another store is one module in `app/providers/` plus one line each in
`app/config.py` and `app/providers/__init__.py`. Nothing else changes.

## How the recommendation is decided

Each offer scores 0–100 on five weighted components (`app/scoring.py`):

| Component | Weight | What it captures |
|---|---:|---|
| Price | 45% | landed cost relative to the best available |
| Rating | 22% | star rating, Bayesian-shrunk toward the mean |
| Review volume | 13% | log-scaled confidence in that rating |
| Delivery | 12% | days to your door |
| Seller trust | 8% | returns, warranty, dispute history |

Two deliberate judgement calls:

- **A 5.0 from two reviewers loses to a 4.6 from eight thousand.** Ratings are
  shrunk toward a prior, so thin evidence cannot win on a fluke.
- **The cheapest listing is not automatically the pick.** When the winner is not
  the cheapest, the app says so explicitly and quantifies the premium, so you
  can overrule it.

Weights are environment variables — set `W_PRICE=0.6` if you care about price
more than the defaults assume.

## Running it

### macOS / Linux

```bash
git clone https://github.com/Buafra/shopping.git
cd shopping
pip install -r requirements.txt
playwright install chromium      # skip if Chromium is already provisioned

uvicorn app.main:app --reload    # web UI at http://127.0.0.1:8000
python cli.py "airfryer" --market local
python cli.py "rtx 4070" --json
```

### Windows (PowerShell)

Windows PowerShell 5.1 has no `&&`, so run one command per line. Every command
must be run from inside the cloned folder — `python cli.py` from `C:\` will
just report that it cannot find the file.

```powershell
git clone https://github.com/Buafra/shopping.git
cd shopping
python -m pip install -r requirements.txt
python -m playwright install chromium

python cli.py "sony wh-1000xm5"
python run.py                     # web UI; picks a port that will bind
```

If `uvicorn app.main:app` fails with **`WinError 10013`**, the port is not in
use — Windows has reserved it for Hyper-V/WSL and refuses to bind it. Either
run `python run.py`, which probes and moves to a free port automatically, or
pass a higher one yourself (`--port 8600`). To see the reserved ranges:
`netsh interface ipv4 show excludedportrange protocol=tcp`.

The CLI detects a console that cannot render `★ ▶ —`, falls back to ASCII, and
drops colour codes when output is piped to a file — so a cp1252 console gets
readable output rather than a `UnicodeEncodeError` halfway through.

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/search?q=…` | full comparison; `market=all\|local\|global`, `stores=ebay,noon` |
| `GET /api/stores` | store registry and current scoring weights |
| `GET /api/health` | FX source and whether the browser fallback is working |

### Configuration

All optional, all environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `REQUEST_TIMEOUT` | `20` | per-request timeout, seconds |
| `HTTP_PHASE_TIMEOUT` | `20` | cap on the whole HTTP phase, retries included |
| `BROWSER_PHASE_TIMEOUT` | `35` | cap on the browser fallback |
| `DISABLED_STORES` | – | comma-separated keys to skip, e.g. `sharaf_dg,carrefour_ae` |
| `MAX_RETRIES` | `2` | retries on timeout/5xx/429 |
| `PER_STORE_RESULTS` | `6` | listings kept per store |
| `USE_BROWSER_FALLBACK` | `true` | allow Playwright when plain HTTP is blocked |
| `CHROMIUM_PATH` | auto | explicit Chromium binary |
| `SCRAPER_PROXY` | – | outbound proxy, e.g. `http://user:pass@host:port` |
| `UAE_VAT_RATE` / `UAE_CUSTOMS_RATE` | `0.05` | landed-cost assumptions |
| `UAE_DUTY_FREE_THRESHOLD_AED` | `300` | de-minimis |
| `W_PRICE`, `W_RATING`, `W_REVIEWS`, `W_DELIVERY`, `W_TRUST` | see above | must sum to 1.0 |

## What can go wrong (read this)

**Scraping is inherently fragile, and this app is honest about it rather than
pretending otherwise.**

- **Stores change their markup.** When a store redesigns, its parser returns
  zero results. Every parser is written to degrade to "no offers" rather than
  return wrong data, and each store's outcome — including the error — is shown
  in the response and in the UI's *Store coverage* panel. A comparison built
  from 5 of 8 stores says so.
- **Failures tell you which thing to fix.** Each store failure is classified
  (`error_kind` in the API), because these need opposite responses:

  | Kind | Means | What to do |
  |---|---|---|
  | `unreachable` | no route, DNS or proxy refusal | fix your network / `SCRAPER_PROXY` |
  | `blocked` | store refused automated traffic (403/429) | use a residential IP |
  | `timeout` | store too slow to answer | raise `REQUEST_TIMEOUT` |
  | `parse` | page loaded fine, nothing parsed out | the store redesigned — update that provider's selectors |
  | `no_results` | store genuinely has no match | broaden the query |

  If every store fails the same way, the summary note says so outright rather
  than leaving you to guess from a list of red rows.
- **Stores block bots.** Amazon in particular blocks datacentre IPs hard. Each
  provider tries plain HTTP first and falls back to a real headless browser.
  From a residential IP most stores answer; from a cloud VM expect Amazon and
  AliExpress to fail often. Set `SCRAPER_PROXY` if you need to route around it.
- **Prices go stale immediately.** Always confirm on the store page before
  buying. Every row links straight to the listing.
- **Shipping is sometimes an estimate.** Rows show `~` where the figure is
  assumed rather than quoted, and the assumption comes from `app/config.py`.
- **Respect the stores.** Search endpoints only, one query per user action, no
  bulk crawling. Several of these sites prohibit automated access in their
  terms — that is a real constraint, and running this at volume is your call to
  make, not the code's.

## Store reachability, measured

Live results from a UAE residential connection (August 2026). This is the part
that decides whether the app is useful, and it is not something the code can
fix on its own:

| Store | Result |
|---|---|
| **Amazon.ae** | works — 6 offers over plain HTTP in ~1s |
| Noon UAE | connection refused at protocol level (`ERR_HTTP2_PROTOCOL_ERROR`); HTTP/1.1 fallback added, unverified |
| Sharaf DG | serves a **CAPTCHA** to headless browsers; page loads with zero prices in it |
| Carrefour UAE | search API retired (404), but the **search page returns 200 with prices** — now parsed structurally |

Carrefour is recoverable: only its private API died, and the ordinary search
page still serves products over plain HTTP. That page is built with
utility-first CSS — `class="relative gap-2xs md:gap-1.5xs pl-md"` describes
appearance and nothing else — so there is no class worth selecting on and
`app/structural.py` locates cards by shape instead: a block containing exactly
one product link, a price and a plausible title. A block holding several
product links is skipped, because one price shared between them belongs to
none of them.

Sharaf DG is different. A CAPTCHA is a deliberate "no", and working around it
is out of scope here — the honest fixes are an official/affiliate feed or a
commercial scraping proxy via `SCRAPER_PROXY`. Until then, turn it off so it
stops costing every search the full timeout budget:

```bash
DISABLED_STORES=sharaf_dg
```

## When a store breaks

Stores redesign, and a `parse` failure means that store's selectors are stale.
`diagnose.py` captures what the store really returns so the fix is based on
evidence:

```bash
python diagnose.py sharaf_dg              # one store
python diagnose.py --all --query "airfryer"
```

It runs the HTTP and browser paths separately and reports the page title, any
bot-wall markers, whether the page ships `__NEXT_DATA__` or JSON-LD, how many
elements each current selector still matches, how many offers parse out, and
which repeated CSS classes look like product cards. Raw HTML is written to
`captures/` (git-ignored) so nothing leaves your machine.

## Tests

```bash
python -m pytest -q      # 161 tests
```

The suite never touches the network. Provider parsers run against fixtures in
`tests/fixtures/` that mirror each store's real markup — including the cases
that actually bite: sponsored Amazon cards, eBay's "Shop on eBay" placeholder,
free-shipping wording, out-of-stock flags, duplicate listings, EU-style decimal
commas, and pages that have been redesigned into unparseable junk.

## Layout

```
app/
  models.py      Offer, StoreStatus, Recommendation
  config.py      settings, store registry, scoring weights
  net.py         HTTP client, retries, price/rating parsing
  browser.py     Playwright fallback + Chromium discovery
  fx.py          currency conversion, live with static fallback
  pricing.py     landed cost — shipping, duty, VAT
  matching.py    relevance filtering (drops accessories)
  structural.py  class-free card extraction for utility-CSS storefronts
  scoring.py     ranking and the written rationale
  aggregator.py  concurrent fan-out across stores
  main.py        FastAPI app
  providers/     one module per store
  static/        web UI
cli.py           terminal interface
run.py           web launcher with automatic port selection
diagnose.py      capture a store's real markup when its parser breaks
```
