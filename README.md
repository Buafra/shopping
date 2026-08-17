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

**PC-component specialists**, added automatically when the search is for a part
(see [PC parts](#pc-parts)):

- *UAE:* Microless · UAEGAMERS · GCC Gamers · DXB Gamers · PCDubai ·
  Gear-up.me · Emax · Jumbo
- *Global:* B&H Photo *(US)* · Overclockers UK · Scan UK · Alternate *(DE)*

### Adding a store

Most shops need **no code at all** — an entry in `STORES` is the whole change:

```python
"my_shop": StoreSpec(
    key="my_shop", label="My Shop", market=Market.LOCAL, country="AE",
    currency="AED", trust=0.8, default_delivery_days=3,
    origin="https://www.myshop.ae",     # the only required field
),
```

`app/providers/generic.py` then finds the search page (trying the Shopify,
Magento, WooCommerce and plain `?q=` conventions in turn) and reads product
cards by shape. Product URLs are detected from the page itself, so you do not
need to know the store's markup — `structural.guess_product_path` looks at
which link pattern repeats next to a price.

Override any of that when the defaults miss:

| Field | Use when |
|---|---|
| `search_urls=("https://…/search?query={q}",)` | the search page is somewhere unusual |
| `product_path="/c/product/"` | detection picks the wrong link pattern |
| `tags=("pc_parts",)` | the shop only stocks one category |

A store that ships a JSON API worth reading still earns a module in
`app/providers/` — that is why Carrefour, Noon and Amazon have one.

## How the recommendation is decided

Each offer scores 0–100 on five weighted components (`app/scoring.py`):

| Component | Weight | What it captures |
|---|---:|---|
| Price | 45% | landed cost relative to the best available |
| Rating | 22% | star rating, Bayesian-shrunk toward the mean |
| Review volume | 13% | log-scaled confidence in that rating |
| Delivery | 12% | days to your door |
| Seller trust | 8% | returns, warranty, dispute history |

Before anything is scored, listings that are not the product you asked for are
removed:

- **A variant suffix is part of the model.** An RTX 4070 Ti is not an RTX
  4070, and a 4070 Super is neither — different cards at different prices. A
  suffix the query did not ask for (`Ti`, `Super`, `XT`, `Pro`, `Max`) is a
  mismatch, and so is dropping one it did ask for. Factory-overclock marks
  like `OC` are not variants — that is the same chip.
- **Currency is read from the page, never assumed.** AliExpress localises by
  IP and quotes a UAE visitor in AED on a store the registry calls USD;
  assuming the store default multiplied every price by 3.67.
- **A model number is identity, not description.** A title missing the model
  you searched for scores zero, not partial credit. `WH-1000XM5` and
  `WF-1000XM5` differ by one letter and are different products (over-ear
  headphones vs earbuds); `WH-1000XM4` is a different generation. Loose
  spellings still match, so `WH-1000XM5`, `WH 1000XM5` and `WH1000XM5` are one
  product.
- **Refurbished stock is hidden by default.** A renewed unit undercuts new
  stock on price without being the same purchase — different condition,
  different warranty. Pass `--include-used` (CLI) or `include_used=true` (API)
  to see them.
- **A listing selling many products is an offer for none of them.**
  Marketplaces put six GPUs on one page — "3060TI 3050 3070 RTX 4070 4060TI" —
  and advertise the cheapest variant's price. That surfaced as an RTX 4070 for
  AED 959 when the 959 buys a 3050.
- **A prebuilt PC is not a graphics card.** A system containing the part you
  searched for is a different purchase, unless you asked for a system.
- **Accessories are dropped.** Otherwise a AED 19 case wins a phone search.
  This happens *inside each provider*, before its results are truncated —
  otherwise the cheap accessories fill the per-store quota and the real
  product never reaches the comparison at all.

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

## Tracking prices over time

Every search is recorded, so re-running it later answers the question that
actually matters — *has it moved?*

```bash
python cli.py "rtx 4070"          # searched, and now tracked
python cli.py --history           # what you are watching, and how it has moved
python cli.py --recheck 1         # re-run search 1 and show the difference
python cli.py --recheck all       # re-check everything
python cli.py --forget 1          # stop tracking
python cli.py "rtx 4070" --no-save   # search without recording
```

```
Since last check  (search #1)
  1 cheaper, 1 dearer, 2 gone
    -98 (-12.0%)  819 → 721  Amazon.ae  Sony WH-1000XM5 Wireless Industry…
    +78 (+6.0%)  1,299 → 1,377  Amazon.ae  Sony WH-1000XM5 Noise Cancelling…
    gone                 1,199  Noon UAE  Sony WH-1000XM5 Out Of Stock Variant
```

Listings are matched between runs by **URL path**, ignoring query strings —
stores append campaign parameters that change on every fetch, and matching on
the raw URL would report every listing as vanished and replaced. Searches
limited to specific stores are not tracked, since those are diagnostics.

History lives in `history.db` (SQLite, no extra dependency); set `HISTORY_DB`
to move it. The web UI shows the same list with *Check now* and *Forget*.

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/search?q=…` | full comparison; `market=all\|local\|global`, `stores=ebay,noon`, `include_used`, `save_history` |
| `GET /api/history` | tracked searches and how their best price has moved |
| `GET /api/history/{id}` | what changed at the last check |
| `POST /api/history/{id}/recheck` | re-run a tracked search and diff it |
| `DELETE /api/history/{id}` | stop tracking |
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
| `HISTORY_DB` | `history.db` | where tracked searches are stored |
| `MAX_RETRIES` | `2` | retries on timeout/5xx/429 |
| `PER_STORE_RESULTS` | `6` | listings kept per store |
| `USE_BROWSER_FALLBACK` | `true` | allow Playwright when plain HTTP is blocked |
| `CHROMIUM_PATH` | auto | explicit Chromium binary |
| `SCRAPER_PROXY` | – | outbound proxy, e.g. `http://user:pass@host:port` |
| `UAE_VAT_RATE` / `UAE_CUSTOMS_RATE` | `0.05` | landed-cost assumptions |
| `UAE_DUTY_FREE_THRESHOLD_AED` | `300` | de-minimis |
| `W_PRICE`, `W_RATING`, `W_REVIEWS`, `W_DELIVERY`, `W_TRUST` | see above | must sum to 1.0 |

`GET /api/search` also takes `include_used=true` to show refurbished listings.

## What can go wrong (read this)

**Scraping is inherently fragile, and this app is honest about it rather than
pretending otherwise.**

- **Stores change their markup.** When a store redesigns, its parser returns
  zero results. Every parser is written to degrade to "no offers" rather than
  return wrong data, and each store's outcome — including the error — is shown
  in the response and in the UI's *Store coverage* panel. A comparison built
  from 5 of 8 stores says so. Coverage reports fetched *and* matched counts
  separately (`offer_count` / `kept_count`) — a store can return six listings
  and contribute none, and showing only the fetched number makes that look
  like a success.
- **Failures tell you which thing to fix.** Each store failure is classified
  (`error_kind` in the API), because these need opposite responses:

  | Kind | Means | What to do |
  |---|---|---|
  | `unreachable` | no route, DNS or proxy refusal | fix your network / `SCRAPER_PROXY` |
  | `blocked` | refused as automated traffic — a 403/429, **or a CAPTCHA or interstitial served as a normal 200** | use a residential IP or `SCRAPER_PROXY`; no parser change helps |
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
| **Amazon.ae** | works — offers with ratings and review counts, over plain HTTP in ~1s |
| **Newegg** | works |
| **Amazon.com** | works |
| AliExpress | serves a **CAPTCHA** ("Captcha Interception"). Worked for the first few runs, then rate-limited into a permanent challenge |
| eBay | serves an **Imperva interstitial** ("Pardon Our Interruption"). Session priming got past the 403 only to land here |
| Noon UAE | no response at all, and the browser navigation is aborted — blocking at connection level, not by status code. Given a short 8s leash so it cannot dominate a search; disable it or use `SCRAPER_PROXY` |
| Sharaf DG | serves a **CAPTCHA** to headless browsers; page loads with zero prices in it |
| **Carrefour UAE** | works — 6 offers in ~2.5s by parsing the search page (its JSON API is retired) |

Carrefour is recoverable: only its private API died, and the ordinary search
page still serves products over plain HTTP. That page is built with
utility-first CSS — `class="relative gap-2xs md:gap-1.5xs pl-md"` describes
appearance and nothing else — so there is no class worth selecting on and
`app/structural.py` locates cards by shape instead: a block containing exactly
one product link, a price and a plausible title. A block holding several
product links is skipped, because one price shared between them belongs to
none of them.

Four of the eight stores now answer with a challenge rather than results:
Noon (connection-level), Sharaf DG, AliExpress and eBay (CAPTCHA or
interstitial). These arrive as ordinary HTTP 200 responses with well-formed
HTML, so they are detected by what the page *says* — the app reports them as
`blocked`, not as a markup change, because no selector fixes a CAPTCHA.

Repeated searching is what triggers most of them: AliExpress answered
normally for the first few runs and only then began challenging. Space out
`--recheck all` rather than polling.

Sharaf DG is the same story. A CAPTCHA is a deliberate "no", and working around it
is out of scope here — the honest fixes are an official/affiliate feed or a
commercial scraping proxy via `SCRAPER_PROXY`. Until then, turn it off so it
stops costing every search the full timeout budget:

```bash
DISABLED_STORES=sharaf_dg
```

### PC parts

Same search, wider net. General retailers carry a thin and expensive slice of
this category, so a query recognised as a component pulls in twelve specialist
shops on top of the usual eight stores — twenty in total:

```bash
python cli.py "rtx 4070"
python cli.py "ryzen 7 7800x3d" --market global
python cli.py "samsung 990 pro 2tb nvme"
```

Recognition (`app/category.py`) keys off product families rather than a fixed
list — `rtx 4070`, `7800x3d`, `b650 motherboard`, `ddr5`, `850w power supply`
and `nvme` all qualify; `air fryer` and `iphone 15 pro` do not, and those
searches run against exactly the same eight stores as before. Detection is
deliberately generous: a false positive costs a few seconds against shops that
return nothing, while a false negative costs you the cheapest listing.

The specialists span three currencies (AED, GBP, EUR), which matters more than
it sounds — a £549 card from the UK and a €599 card from Germany are ranked on
**landed** cost in AED, including shipping, 5% customs and 5% VAT, so the
sticker price never decides the winner on its own.

To search them without the category check, name them:

```bash
python cli.py "thermal paste" --stores microless,scan_uk
```

When a store reports "N offers, 0 matched", ask it why:

```bash
python cli.py "rtx 4070" --explain
```

Every filtered listing is printed under the rule that removed it — a wrong
variant, a prebuilt system, an accessory, refurbished stock. A store with no
stock and a filter that is too strict look identical without this, and they
need opposite fixes.

These twelve are config-only entries and unverified from a UAE connection — the
same blocking that affects eBay and AliExpress may apply. `python diagnose.py
microless` reports what any one of them actually returns, and the fix for a
store that has moved its search page is a URL in `app/config.py`, not code.

Two known gaps worth picking up:

- **[Pricena](https://ae.pricena.com)** aggregates a large number of UAE
  retailers. One integration there is worth many store entries, but its rows
  are other shops' offers, so attributing them to "Pricena" would misreport who
  you are buying from — it needs a seller-per-row model first.
- **Official APIs** beat scraping wherever they exist. Amazon's Product
  Advertising API is free for Associates, and eBay's Browse API allows a
  generous free tier — both would replace a blocked scraper with a supported
  feed. eBay is currently blocked here and would benefit most.

Landed cost matters more here than anywhere: a GPU that looks cheap on a US
site attracts 5% duty and 5% VAT on a high-value item, which routinely erases
the gap against local stock.

## Why stores block, and the free things that help

It is not load — a search sends about eight requests, fewer than opening eight
tabs. It is **identification**. Python's TLS handshake has a different shape
from Chrome's (cipher order, extensions, ALPN, HTTP/2 settings), and that
fingerprint — JA3 — identifies the client before a single byte of HTTP is
sent. No header can fix it, because it happens before headers exist. Noon
accepting the connection and then never answering is the classic signature.

Two free mitigations, both on by default:

**TLS impersonation.** With `curl_cffi` installed, the handshake is performed
with Chrome's fingerprint instead. Set `USE_TLS_IMPERSONATION=false` to
disable. If the package is absent the app runs exactly as before — nothing
depends on it.

**Skipping stores that refuse.** A CAPTCHA is not a transient fault; the same
store answers the same way an hour later, and retrying costs the full timeout
budget each search. A store reporting `blocked` is remembered and skipped for
24 hours (`SKIP_BLOCKED_HOURS`), then retried automatically.

`DISABLED_STORES` and the blocked list are separate things: the first is your
decision, the second is the store's. Asking for a store that is switched off
now says so, rather than reporting that no stores matched.

```bash
python cli.py --blocked          # what is being skipped, and why
python cli.py --unblock noon     # try one again right now
python cli.py --unblock all
python cli.py "rtx 4070" --stores noon   # naming a store always tries it
```

What neither can do is answer a CAPTCHA. Impersonation is about not being
challenged in the first place; once a store has decided to challenge you, only
a different IP changes the answer.

## Using a proxy to reach blocked stores

Four stores answer a UAE home connection with a challenge rather than
results. That is an IP-reputation decision, so the fix is to make requests
from an address that does not look automated — a **residential** or
**mobile** proxy. A datacentre proxy will not help; those ranges are exactly
what the stores already block.

### Choosing one

Three kinds are sold, and only two are any use here:

| Type | Roughly | Works? |
|---|---|---|
| **Datacentre** | $1–3 per IP/month | **No.** These ranges are precisely what the stores already block |
| **Residential** | $2–9 per GB | Yes — real home IPs, what you want |
| **Mobile** | $8–20 per GB | Yes, and hardest to block, but overkill for this |

Providers in this market include IPRoyal, Webshare, Decodo (formerly
Smartproxy), Proxy-Cheap, Oxylabs and Bright Data. The first four are easier
to sign up for; the last two are larger but often require business
verification, which is friction for a personal tool. Prices move — check
current rates rather than trusting this table.

**Size the plan by the right number.** One search fetches roughly 1–2 MB per
store, so about 10 MB all-in — around **100 searches per GB**. Traffic is not
your cost; a monthly minimum is. Prefer pay-as-you-go with credit that does
not expire over a subscription, and buy the smallest amount that lets you
test. Most providers include a small trial.

What you are given is a **gateway host, a port, a username and a password** —
which is exactly what goes into `SCRAPER_PROXY`.

```powershell
# this session only
$env:SCRAPER_PROXY = "http://<user>:<pass>@<your-proxy-host>:<port>"
python cli.py "rtx 4070"

# permanently, for your account
setx SCRAPER_PROXY "http://<user>:<pass>@<your-proxy-host>:<port>"
```

```bash
# macOS / Linux
export SCRAPER_PROXY="http://<user>:<pass>@<your-proxy-host>:<port>"
```

Substitute your provider's real values — a placeholder host resolves to
nothing and fails exactly like having no internet, so the app detects the
documented placeholders and goes direct rather than breaking every store at
once. On Windows, `setx` persists the value for **future** shells; to undo it:

```powershell
setx SCRAPER_PROXY ""
Remove-Item Env:\SCRAPER_PROXY     # clears the current shell too
```

If the password contains `@`, `:` or `/`, percent-encode it — `p@ss` becomes
`p%40ss`. Check it is live before searching:

```bash
curl http://127.0.0.1:8000/api/health     # proxy.configured, proxy.server, proxy.used_by
python diagnose.py amazon_ae              # should still return offers through the proxy
```

Both the HTTP client and the headless browser route through it. What it does
and does not buy you:

| | |
|---|---|
| Fixes | being blocked *because of your IP* — Noon's silent drop, eBay's interstitial, AliExpress's rate-limit |
| Does not fix | a CAPTCHA already being shown. A proxy avoids being challenged; it does not answer a challenge |
| Costs | residential proxies are billed per GB, typically a few dollars per month at this volume |
| Slows | every request by 100–400ms |

Requests are one search per user action, so bandwidth is small. Keep it that
way: polling `--recheck all` on a tight loop is what earned the blocks in the
first place.

## When a store breaks

Stores redesign, and a `parse` failure means that store's selectors are stale.
`diagnose.py` captures what the store really returns so the fix is based on
evidence:

```bash
python diagnose.py sharaf_dg              # one store
python diagnose.py --all --query "airfryer"
```

It uses short timeouts (8s, no retries — override with `--timeout`) so a
blocked store reports quickly instead of stalling the run. It runs the HTTP
and browser paths separately and reports the page title, any
bot-wall markers, whether the page ships `__NEXT_DATA__` or JSON-LD, how many
elements each current selector still matches, how many offers parse out, and
which repeated CSS classes look like product cards. Raw HTML is written to
`captures/` (git-ignored) so nothing leaves your machine.

## Tests

```bash
python -m pytest -q      # 395 tests
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
  history.py     search history and price-change tracking (SQLite)
  scoring.py     ranking and the written rationale
  aggregator.py  concurrent fan-out across stores
  main.py        FastAPI app
  providers/     one module per store
  static/        web UI
cli.py           terminal interface
run.py           web launcher with automatic port selection
diagnose.py      capture a store's real markup when its parser breaks
```
