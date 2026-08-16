'use strict';

const $ = (id) => document.getElementById(id);

const form = $('search-form');
const queryInput = $('query');
const marketSelect = $('market');
const goButton = $('go');
const statusBox = $('status');
const recBox = $('recommendation');
const resultsBox = $('results');
const offersBody = $('offers-body');
const resultCount = $('result-count');
const sortSelect = $('sort');
const storeLog = $('store-log');
const storeLogBody = $('store-log-body');

let currentOffers = [];

// ---------- formatting helpers ----------

const aed = (value) =>
  value === null || value === undefined
    ? '—'
    : new Intl.NumberFormat('en-AE', { maximumFractionDigits: 0 }).format(value);

const escapeHtml = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

function ratingCell(offer) {
  if (offer.rating === null || offer.rating === undefined) return '<span class="muted">—</span>';
  const count = offer.review_count ? ` <span class="muted">(${aed(offer.review_count)})</span>` : '';
  return `★ ${offer.rating.toFixed(1)}${count}`;
}

function etaCell(offer) {
  if (!offer.delivery_days) return '<span class="muted">—</span>';
  return `${offer.delivery_days}d`;
}

function shippingCell(offer) {
  if (!offer.shipping_aed) return '<span class="muted">Free</span>';
  const mark = offer.shipping_is_estimate ? '<span class="muted">~</span>' : '';
  return `${mark}${aed(offer.shipping_aed)}`;
}

// ---------- rendering ----------

function setStatus(html, isError = false) {
  statusBox.hidden = false;
  statusBox.className = isError ? 'status error' : 'status';
  statusBox.innerHTML = html;
}

function renderRecommendation(rec) {
  if (!rec) {
    recBox.hidden = true;
    return;
  }
  const o = rec.offer;
  const image = o.image
    ? `<img class="rec-img" src="${escapeHtml(o.image)}" alt="" loading="lazy"
         onerror="this.style.display='none'">`
    : '';
  const badges = (o.badges || [])
    .map((b, i) => `<span class="badge${i === 0 ? ' best' : ''}">${escapeHtml(b)}</span>`)
    .join('');
  const breakdown = [
    o.shipping_aed ? `shipping AED ${aed(o.shipping_aed)}` : 'free shipping',
    o.import_fees_aed ? `duty/VAT AED ${aed(o.import_fees_aed)}` : null,
  ].filter(Boolean).join(' + ');

  const savings = rec.savings_vs_worst_aed
    ? `<p class="rec-meta">Saves about <strong>AED ${aed(rec.savings_vs_worst_aed)}</strong>
       versus the priciest listing found.</p>`
    : '';

  recBox.hidden = false;
  recBox.innerHTML = `
    <span class="rec-flag">Our pick</span>
    <div class="rec-body">
      ${image}
      <div class="rec-main">
        <h3><a href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer">
          ${escapeHtml(o.title)}</a></h3>
        <div>${badges}</div>
        <p class="rec-price">AED ${aed(o.landed_aed)}
          <small>landed &middot; item AED ${aed(o.price_aed)}${breakdown ? ' + ' + breakdown : ''}</small>
        </p>
        <p class="rec-meta">
          ${escapeHtml(o.store_label)} &middot; ${o.market === 'local' ? 'UAE stock' : 'ships internationally'}
          &middot; ${ratingCell(o)} &middot; ETA ${o.delivery_days || '?'} days
        </p>
        ${savings}
        <ul class="rec-why">
          ${rec.rationale.map((r) => `<li>${escapeHtml(r)}</li>`).join('')}
        </ul>
        <div class="rec-actions">
          <a class="btn-buy" href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer">
            View on ${escapeHtml(o.store_label)} →</a>
          <span class="confidence">Confidence: <strong>${escapeHtml(rec.confidence)}</strong>${
            rec.runner_up ? ` &middot; runner-up: ${escapeHtml(rec.runner_up.store_label)}
            at AED ${aed(rec.runner_up.landed_aed)}` : ''}</span>
        </div>
      </div>
    </div>`;
}

function renderOffers(offers) {
  offersBody.innerHTML = offers.map((o, index) => {
    const badges = (o.badges || [])
      .map((b) => `<span class="badge${b === 'Best overall' ? ' best' : ''}">${escapeHtml(b)}</span>`)
      .join('');
    const stock = o.in_stock ? '' : ' <span class="oos">Out of stock</span>';
    return `
      <tr class="${index === 0 && sortSelect.value === 'score' ? 'winner' : ''}">
        <td class="title-cell">
          <a href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer">
            ${escapeHtml(o.title)}</a>${stock}
          <div>${badges}</div>
        </td>
        <td>${escapeHtml(o.store_label)}
          <span class="pill ${o.market === 'global' ? 'global' : ''}">${o.market}</span></td>
        <td class="num">${aed(o.price_aed)}</td>
        <td class="num">${shippingCell(o)}</td>
        <td class="num">${o.import_fees_aed ? aed(o.import_fees_aed) : '<span class="muted">—</span>'}</td>
        <td class="num landed">${aed(o.landed_aed)}</td>
        <td class="num">${ratingCell(o)}</td>
        <td class="num">${etaCell(o)}</td>
        <td class="num"><span class="score-chip${index === 0 ? ' top' : ''}">${o.score ?? '—'}</span></td>
      </tr>`;
  }).join('');

  resultCount.textContent = `(${offers.length})`;
  resultsBox.hidden = offers.length === 0;
}

function renderStores(stores) {
  storeLog.hidden = false;
  storeLogBody.innerHTML = stores.map((s) => `
    <div class="store-card ${s.ok ? (s.kept_count === 0 ? 'partial' : '') : 'failed'}">
      <strong>${escapeHtml(s.store_label)}</strong>
      <span class="muted">${s.market} &middot; ${(s.elapsed_ms / 1000).toFixed(1)}s${
        s.method ? ' &middot; ' + escapeHtml(s.method) : ''}</span>
      <div class="why">${s.ok
        ? (s.kept_count === null || s.kept_count === undefined || s.kept_count === s.offer_count
            ? `${s.offer_count} offer${s.offer_count === 1 ? '' : 's'}`
            : `${s.offer_count} fetched, <strong>${s.kept_count}</strong> matched`)
        : escapeHtml(s.error || 'no results')}</div>
    </div>`).join('');
}

function sortOffers(mode) {
  const sorted = [...currentOffers];
  if (mode === 'landed') sorted.sort((a, b) => (a.landed_aed ?? 1e9) - (b.landed_aed ?? 1e9));
  else if (mode === 'rating') sorted.sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
  else if (mode === 'delivery') sorted.sort((a, b) => (a.delivery_days ?? 999) - (b.delivery_days ?? 999));
  else sorted.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  renderOffers(sorted);
}

// ---------- tracked searches ----------

const historyBody = $('history-body');

const signed = (value) => (value > 0 ? '+' : '') + aed(value);

function deltaCell(delta) {
  if (delta === null || delta === undefined) return '<span class="delta flat">—</span>';
  if (delta < 0) return `<span class="delta down">${signed(delta)} AED</span>`;
  if (delta > 0) return `<span class="delta up">${signed(delta)} AED</span>`;
  return '<span class="delta flat">no change</span>';
}

async function loadHistory() {
  try {
    const response = await fetch('/api/history');
    const data = await response.json();
    const searches = data.searches || [];

    if (!searches.length) {
      historyBody.innerHTML =
        '<p class="muted">Searches you run are tracked here, so you can re-check '
        + 'them later and see what moved.</p>';
      return;
    }

    historyBody.innerHTML = searches.map((s) => `
      <div class="history-row" data-id="${s.id}">
        <span class="history-q">${escapeHtml(s.query)}</span>
        <span class="history-meta">${s.market} · ${s.checks} check${s.checks === 1 ? '' : 's'}
          · ${escapeHtml((s.last_checked_at || '').slice(0, 16).replace('T', ' '))}</span>
        <span class="history-price">${s.best_landed_aed === null ? '—'
          : 'AED ' + aed(s.best_landed_aed)}</span>
        ${deltaCell(s.best_delta_aed)}
        <button class="link-btn" data-recheck="${s.id}" type="button">Check now</button>
        <button class="link-btn" data-forget="${s.id}" type="button">Forget</button>
        <ul class="history-changes" hidden></ul>
      </div>`).join('');
  } catch (error) {
    historyBody.innerHTML =
      `<p class="muted">Could not load history: ${escapeHtml(error.message)}</p>`;
  }
}

async function recheck(id, row) {
  const list = row.querySelector('.history-changes');
  list.hidden = false;
  list.innerHTML = '<li><span class="spinner"></span>Re-checking…</li>';

  try {
    const response = await fetch(`/api/history/${id}/recheck`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);

    const changes = data.changes;
    const rows = [
      ...(changes.changed || []).map((o) =>
        `<li>${deltaCell(o.delta_aed)} ${aed(o.previous_landed_aed)} → ${aed(o.landed_aed)}
         · ${escapeHtml(o.store_label)} · ${escapeHtml(o.title.slice(0, 60))}</li>`),
      ...(changes.appeared || []).map((o) =>
        `<li><span class="delta flat">new</span> AED ${aed(o.landed_aed)}
         · ${escapeHtml(o.store_label)} · ${escapeHtml(o.title.slice(0, 60))}</li>`),
      ...(changes.disappeared || []).map((o) =>
        `<li><span class="delta flat">gone</span> AED ${aed(o.landed_aed)}
         · ${escapeHtml(o.store_label)} · ${escapeHtml(o.title.slice(0, 60))}</li>`),
    ];

    list.innerHTML = `<li><strong>${escapeHtml(changes.summary)}</strong></li>` + rows.join('');
    loadHistory();

    if (data.result) {
      currentOffers = data.result.offers || [];
      renderStores(data.result.stores || []);
      renderRecommendation(data.result.recommendation);
      sortOffers(sortSelect.value);
      queryInput.value = changes.query;
    }
  } catch (error) {
    list.innerHTML = `<li>Re-check failed: ${escapeHtml(error.message)}</li>`;
  }
}

historyBody.addEventListener('click', async (event) => {
  const recheckId = event.target.dataset?.recheck;
  const forgetId = event.target.dataset?.forget;
  if (recheckId) {
    await recheck(recheckId, event.target.closest('.history-row'));
  } else if (forgetId) {
    await fetch(`/api/history/${forgetId}`, { method: 'DELETE' });
    loadHistory();
  }
});

$('history-refresh').addEventListener('click', loadHistory);

// ---------- search ----------

async function runSearch(event) {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (query.length < 2) return;

  goButton.disabled = true;
  recBox.hidden = true;
  resultsBox.hidden = true;
  storeLog.hidden = true;
  setStatus(`<span class="spinner"></span>Searching UAE and global stores for
    <strong>${escapeHtml(query)}</strong>… usually 15–30 seconds, up to a minute if a store is slow.`);

  const params = new URLSearchParams({ q: query, market: marketSelect.value });

  try {
    const response = await fetch(`/api/search?${params}`);
    const data = await response.json();

    if (!response.ok) {
      setStatus(`Search failed: ${escapeHtml(data.detail || response.statusText)}`, true);
      return;
    }

    currentOffers = data.offers || [];
    renderStores(data.stores || []);

    if (currentOffers.length === 0) {
      setStatus(`No offers found for <strong>${escapeHtml(query)}</strong>.
        ${(data.notes || []).map((n) => `<div class="why">${escapeHtml(n)}</div>`).join('')}`, true);
      return;
    }

    const notes = (data.notes || []).length
      ? `<ul>${data.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>`
      : '';
    setStatus(`Compared <strong>${currentOffers.length}</strong> offers from
      <strong>${(data.stores || []).filter((s) => s.ok).length}</strong> stores in
      ${(data.elapsed_ms / 1000).toFixed(1)}s.${notes}`);

    renderRecommendation(data.recommendation);
    sortSelect.value = 'score';
    sortOffers('score');
    loadHistory();
  } catch (error) {
    setStatus(`Could not reach the API: ${escapeHtml(error.message)}`, true);
  } finally {
    goButton.disabled = false;
  }
}

form.addEventListener('submit', runSearch);
sortSelect.addEventListener('change', () => sortOffers(sortSelect.value));

loadHistory();

// Deep-link support: /?q=airpods runs the search on load.
const initial = new URLSearchParams(location.search).get('q');
if (initial) {
  queryInput.value = initial;
  form.requestSubmit();
}
