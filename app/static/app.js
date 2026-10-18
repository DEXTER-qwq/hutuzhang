/* 糊涂账 — front-end */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    meta: null,
    month: null,
    monthBeforeSearch: null,
    view: 'overview',
    overview: null,
    ledger: { items: [], total: 0, offset: 0, loading: false },
    filters: { usage: '', status: '', category_id: '', account_id: '', q: '' },
    ledgerDate: '',
    ledgerRange: null,
    ledgerContinuous: false,
    selection: new Set(),
    importPreview: null,
    importFile: null,
    importMode: 'merge',
    routeParams: null,
    stats: { period: 'month', key: '', scope: 'all', metric: 'expense', catMetric: 'expense', scrollTo: '' },
  };

  /* ------------------------------------------------------------------ utils */

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  const fmt = (cents) => {
    const v = (cents || 0) / 100;
    return (v < 0 ? '-' : '') + Math.abs(v).toLocaleString('zh-CN', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  };
  const money = (cents) => (cents < 0 ? '-¥' : '¥') + fmt(Math.abs(cents));
  const signed = (cents, kind) => (kind === 'income' ? '+' : '') + '¥' + fmt(cents);

  const todayISO = () => {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  };
  const currentMonth = () => todayISO().slice(0, 7);

  const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  function dayLabel(iso) {
    const d = new Date(iso.slice(0, 10) + 'T00:00:00');
    const now = new Date();
    const t = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const diff = Math.round((t - d) / 86400000);
    const base = (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + WEEK[d.getDay()];
    if (diff === 0) return '今天 · ' + base;
    if (diff === 1) return '昨天 · ' + base;
    return base;
  }
  const monthLabel = (m) => {
    if (!m || m === 'all') return '全部时间';
    return m.slice(0, 4) + '年' + Number(m.slice(5, 7)) + '月';
  };
  const timeLabel = (iso) => (iso || '').slice(11, 16);

  const STATUS = { personal: '日常', work: '报销', income: '收入' };

  function setupChartTips() {
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    document.body.appendChild(tip);
    let current = null;
    document.addEventListener('mousemove', (e) => {
      const el = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
      if (!el) {
        if (current) { tip.classList.remove('show'); current = null; }
        return;
      }
      if (current !== el) {
        current = el;
        tip.textContent = el.getAttribute('data-tip');
        tip.classList.add('show');
      }
      const pad = 14;
      const rect = tip.getBoundingClientRect();
      let x = e.clientX + pad;
      let y = e.clientY + pad;
      if (x + rect.width > window.innerWidth - 10) x = e.clientX - rect.width - pad;
      if (y + rect.height > window.innerHeight - 10) y = e.clientY - rect.height - pad;
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    });
    document.addEventListener('mouseleave', () => { tip.classList.remove('show'); current = null; });
  }

  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 2200);
  }

  async function api(path, opts = {}) {
    const res = await fetch('/api' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
      body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
    });
    if (!res.ok) {
      let msg = '请求失败';
      try { const j = await res.json(); msg = j.detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.status === 204 ? null : res.json();
  }

  const get = (p) => api(p);
  const post = (p, body) => api(p, { method: 'POST', body });
  const patch = (p, body) => api(p, { method: 'PATCH', body });
  const del = (p) => api(p, { method: 'DELETE' });
  const put = (p, body) => api(p, { method: 'PUT', body });

  /* ------------------------------------------------------------------ boot */

  async function boot() {
    try {
      state.meta = await get('/meta');
    } catch (e) {
      document.body.innerHTML = '<div style="padding:40px">无法连接本地服务：' + esc(e.message) + '</div>';
      return;
    }
    const months = state.meta.months || [];
    if (months.length && !months.includes(currentMonth())) state.month = months[0];
    else state.month = currentMonth();
    state.monthBeforeSearch = state.month;

    bindChrome();
    setupChartTips();
    setSideCount();
    route();
    window.addEventListener('hashchange', route);
  }

  function route() {
    const raw = location.hash.replace('#/', '') || 'overview';
    const [path, qs] = raw.split('?');
    const view = path.split('/')[0] || 'overview';
    state.routeParams = new URLSearchParams(qs || '');
    const allowed = ['overview', 'stats', 'ledger', 'assets', 'budget', 'import', 'settings'];
    const v = allowed.includes(view) ? view : 'overview';
    state.view = v;
    document.body.className = 'view-' + v;
    $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === v));
    $$('.view').forEach((s) => s.classList.toggle('active', s.id === 'view-' + v));
    $('#month-nav').style.visibility = (v === 'overview' || v === 'ledger' || v === 'budget') ? 'visible' : 'hidden';
    $('#btn-add').style.display = (v === 'settings') ? 'none' : '';
    renderCurrent();
  }

  function renderCurrent() {
    if (state.view === 'overview') renderOverview();
    else if (state.view === 'stats') renderStats();
    else if (state.view === 'ledger') renderLedger();
    else if (state.view === 'assets') renderAssets();
    else if (state.view === 'budget') renderBudget();
    else if (state.view === 'import') renderImport();
    else renderSettings();
  }

  function refreshMonthLabel() {
    if (state.ledgerRange) {
      $('#month-label').textContent = state.ledgerRange.label;
      $('#month-prev').disabled = true;
      $('#month-next').disabled = true;
      return;
    }
    $('#month-label').textContent = monthLabel(state.month);
    $('#month-prev').disabled = state.month === 'all';
    $('#month-next').disabled = state.month === 'all';
  }

  function bindChrome() {
    $('#month-prev').onclick = () => { shiftMonth(-1); };
    $('#month-next').onclick = () => { shiftMonth(1); };
    $('#month-label').onclick = () => openMonthPicker();
    $('#btn-add').onclick = () => openEntryModal();

    let t = null;
    $('#global-search').addEventListener('input', (e) => {
      const q = e.target.value.trim();
      clearTimeout(t);
      t = setTimeout(() => {
        state.ledgerDate = '';
        state.ledgerRange = null;
        if (q && state.month !== 'all') { state.monthBeforeSearch = state.month; state.month = 'all'; }
        if (!q && state.month === 'all' && state.monthBeforeSearch) state.month = state.monthBeforeSearch;
        state.filters.q = q;
        refreshMonthLabel();
        if (state.view !== 'ledger') location.hash = '#/ledger';
        else { state.ledger.offset = 0; loadLedger(true); }
      }, 280);
    });

    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'n') { e.preventDefault(); openEntryModal(); }
      if (e.key === 'Escape') { if (!closeDialog(null)) closeModal(); }
    });
  }

  function openMonthPicker() {
    const nav = $('#month-nav');
    const existing = nav.querySelector('.month-pop');
    if (existing) { existing.remove(); return; }

    let year = state.month === 'all'
      ? Number(currentMonth().slice(0, 4))
      : Number(state.month.slice(0, 4));

    const pop = document.createElement('div');
    pop.className = 'month-pop';
    nav.appendChild(pop);

    const choose = (month) => {
      state.month = month;
      state.monthBeforeSearch = month;
      state.ledgerDate = '';
      state.ledgerRange = null;
      state.filters.q = '';
      $('#global-search').value = '';
      refreshMonthLabel();
      renderCurrent();
      pop.remove();
    };

    const paint = () => {
      const current = state.month === 'all' ? '' : state.month;
      let grid = '';
      for (let m = 1; m <= 12; m++) {
        const key = year + '-' + String(m).padStart(2, '0');
        grid += `<button type="button" data-month="${key}" class="${current === key ? 'sel' : ''}">${m}月</button>`;
      }
      pop.innerHTML = `
        <div class="month-pop-head">
          <button type="button" data-year="-1">‹</button>
          <span>${year}年</span>
          <button type="button" data-year="1">›</button>
        </div>
        <div class="month-grid">${grid}</div>
        <button type="button" class="month-all ${state.month === 'all' ? 'sel' : ''}" data-all>全部时间</button>`;
      pop.querySelectorAll('[data-year]').forEach((b) => {
        b.onclick = () => { year += Number(b.dataset.year); paint(); };
      });
      pop.querySelectorAll('[data-month]').forEach((b) => {
        b.onclick = () => choose(b.dataset.month);
      });
      pop.querySelector('[data-all]').onclick = () => choose('all');
    };

    paint();

    const onDoc = (e) => {
      if (!pop.contains(e.target) && e.target !== $('#month-label')) {
        pop.remove();
        document.removeEventListener('mousedown', onDoc);
      }
    };
    setTimeout(() => document.addEventListener('mousedown', onDoc), 0);
  }

  function shiftMonth(dir) {
    if (state.month === 'all') return;
    const [y, m] = state.month.split('-').map(Number);
    const d = new Date(y, m - 1 + dir, 1);
    state.month = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    state.monthBeforeSearch = state.month;
    state.ledgerDate = '';
    state.ledgerRange = null;
    state.filters.q = '';
    $('#global-search').value = '';
    refreshMonthLabel();
    renderCurrent();
  }

  /* ------------------------------------------------------------------ overview */

  async function renderOverview() {
    const el = $('#view-overview');
    refreshMonthLabel();
    el.innerHTML = '<div class="empty">加载中…</div>';
    const month = state.month === 'all' ? currentMonth() : state.month;
    let d, budget = null;
    try {
      const [ov, bg] = await Promise.all([
        get('/overview?month=' + encodeURIComponent(month)),
        get('/budget?month=' + encodeURIComponent(month)).catch(() => null),
      ]);
      d = ov;
      budget = bg;
    } catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }
    state.overview = d;

    const delta = d.prev_personal_cents
      ? (d.personal_cents - d.prev_personal_cents) / d.prev_personal_cents
      : (d.personal_cents ? 1 : 0);
    const deltaCls = !d.prev_personal_cents ? 'flat' : delta > 0.001 ? 'up' : delta < -0.001 ? 'down' : 'flat';
    const deltaTxt = !d.prev_personal_cents ? '上月无记录'
      : (delta > 0 ? '↑ ' : delta < 0 ? '↓ ' : '') + (Math.abs(delta) * 100).toFixed(1) + '%';
    const workShare = d.expense_cents ? Math.round(d.work_cents / d.expense_cents * 100) : 0;

    el.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">${monthLabel(state.month)}概览</h1>
          <div class="page-sub">日常消费已剔除报销、不计预算的大额支出和固定支出 · 本月收入 ${money(d.income_cents)}</div>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom:14px">
        <div class="card stat clickable" id="ov-personal">
          <div class="stat-label"><span class="dot" style="background:var(--accent)"></span>本月日常消费</div>
          <div class="stat-value"><small>¥</small>${fmt(d.personal_cents)}</div>
          <div class="stat-sub"><span class="delta ${deltaCls}">${deltaTxt}</span> 较上月 · ${d.personal_count} 笔</div>
        </div>
        <div class="card stat clickable" id="ov-work">
          <div class="stat-label"><span class="dot" style="background:var(--work)"></span>本月报销</div>
          <div class="stat-value"><small>¥</small>${fmt(d.work_cents)}</div>
          <div class="stat-sub">${d.work_count} 笔 · 占本月支出 ${workShare}%</div>
        </div>
        <div class="card stat clickable" id="ov-income">
          <div class="stat-label"><span class="dot" style="background:var(--reimb)"></span>本月总收入</div>
          <div class="stat-value"><small>¥</small>${fmt(d.income_cents)}</div>
          <div class="stat-sub">银行卡 / 支付宝等入账</div>
        </div>
        <div class="card stat clickable" id="ov-total">
          <div class="stat-label"><span class="dot" style="background:var(--muted)"></span>本月总支出</div>
          <div class="stat-value"><small>¥</small>${fmt(d.expense_cents)}</div>
          <div class="stat-sub">日常 ${money(d.personal_cents)} + 不计预算 ${money(d.nobudget_cents)} + 报销 ${money(d.work_cents)} + 固定 ${money(d.fixed_cents)}</div>
        </div>
      </div>

      ${budget ? overviewBudgetCard(budget) : ''}

      <div class="grid grid-2" style="margin-bottom:14px">
        <div class="card clickable" id="ov-trend">
          <div class="card-head">
            <div><div class="card-title">近 12 个月支出</div><div class="card-sub">日常 / 不计预算 / 报销 / 固定 · 截至本月</div></div>
          </div>
          <div class="card-body" style="padding-top:10px">
            <div class="trend-legend">
              <span><i style="background:var(--accent)"></i>日常消费</span>
              <span><i style="background:var(--nobudget)"></i>不计预算</span>
              <span><i style="background:var(--work)"></i>报销</span>
              <span><i style="background:var(--fixed)"></i>固定支出</span>
            </div>
            <div class="trend-wrap">${trendChart(d.trend)}</div>
          </div>
        </div>
        <div class="card clickable" id="ov-cat">
          <div class="card-head">
            <div><div class="card-title">日常消费构成</div><div class="card-sub">${monthLabel(state.month)} · 不含报销、不计预算和固定支出</div></div>
          </div>
          <div class="card-body">
            ${d.categories.length ? donutChart(d.categories, d.personal_cents) : '<div class="empty">本月还没有日常消费</div>'}
          </div>
        </div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-head">
            <div><div class="card-title">最近流水</div><div class="card-sub">最新 6 笔</div></div>
            <a class="card-link" href="#/ledger">全部 →</a>
          </div>
          <div class="card-body" style="padding-top:6px">
            ${d.recent.length ? '<div class="mini-list">' + d.recent.map(miniRow).join('') + '</div>' : '<div class="empty"><strong>还没有账目</strong>点右上角「记一笔」开始，或先导入账单。</div>'}
          </div>
        </div>
        <div class="card">
          <div class="card-head">
            <div><div class="card-title">本月报销明细</div><div class="card-sub">${d.work_count} 笔 · ${money(d.work_cents)}</div></div>
            <a class="card-link" href="#/ledger" id="to-work">去流水 →</a>
          </div>
          <div class="card-body" style="padding-top:6px">
            ${d.work_recent.length ? '<div class="mini-list">' + d.work_recent.map(miniRow).join('') + '</div>' : '<div class="empty">本月没有标记为报销的账目</div>'}
          </div>
        </div>
      </div>
    `;

    const toWork = el.querySelector('#to-work');
    if (toWork) toWork.onclick = () => { state.filters.usage = 'work'; };

    const monthKey = state.month === 'all' ? currentMonth() : state.month;
    const goStats = (params) => {
      location.hash = '#/stats?' + new URLSearchParams(params).toString();
    };
    el.querySelectorAll('#ov-trend [data-month]').forEach((node) => {
      node.onclick = (ev) => {
        ev.stopPropagation();
        location.hash = '#/stats?period=month&key=' + node.dataset.month + '&scope=all';
      };
    });
    const links = [
      ['#ov-personal', { section: 'flow', period: 'month', key: monthKey, scope: 'personal' }],
      ['#ov-work', { section: 'flow', period: 'month', key: monthKey, scope: 'work' }],
      ['#ov-income', { section: 'flow', period: 'month', key: monthKey, scope: 'personal', metric: 'income' }],
      ['#ov-total', { section: 'flow', period: 'month', key: monthKey, scope: 'all' }],
      ['#ov-trend', { section: 'flow', period: 'year', key: currentMonth().slice(0, 4), scope: 'all' }],
      ['#ov-cat', { section: 'cat', period: 'month', key: monthKey, scope: 'personal' }],
    ];
    links.forEach(([sel, params]) => {
      const node = el.querySelector(sel);
      if (node) node.onclick = () => goStats(params);
    });
    setSideCount();
  }

  function overviewBudgetCard(b) {
    const t = b.total;
    const daily = t.daily || {};
    if (!t.budget_cents) {
      return `<div class="card ov-budget">
        <div class="card-body ov-budget-empty">
          <div>
            <div class="card-title">本月预算</div>
            <div class="card-sub">还没有设置预算；设一个总数，每天能花多少就清楚了。</div>
          </div>
          <a class="btn sm" href="#/budget">去设置</a>
        </div>
      </div>`;
    }
    const pct = t.pct || 0;
    const over = t.spent_cents > t.budget_cents;
    const color = over ? 'var(--danger)' : pct > 0.8 ? 'var(--work)' : 'var(--accent)';
    return `<div class="card ov-budget">
      <div class="card-head">
        <div><div class="card-title">本月预算</div>
          <div class="card-sub">日常消费 · 已用 ${Math.round(pct * 100)}%</div></div>
        <a class="card-link" href="#/budget">预算详情 →</a>
      </div>
      <div class="card-body" style="padding-top:4px">
        <div class="ov-budget-top">
          <div class="budget-big"><span class="used">${money(t.spent_cents)}</span>
            <span class="of">/ ${money(t.budget_cents)}</span></div>
          <div class="ov-budget-remain ${over ? 'neg' : ''}">
            ${over ? '已超支 ' + money(-t.remaining_cents) : '剩余 ' + money(t.remaining_cents)}</div>
        </div>
        <div class="progress" style="height:7px;margin:10px 0 0">
          <i style="width:${Math.min(pct, 1) * 100}%;background:${color}"></i>
        </div>
        <div class="ov-budget-foot">
          <span>日均预算 <b>${money(daily.budget_cents || 0)}</b></span>
          <span>日均支出 <b>${money(daily.spent_cents || 0)}</b></span>
          ${daily.is_current ? `<span>今日剩余 <b class="${daily.today_remaining_cents < 0 ? 'neg' : ''}">${money(daily.today_remaining_cents || 0)}</b></span>` : ''}
        </div>
      </div>
    </div>`;
  }

  function miniRow(e) {
    const amtCls = e.kind === 'income' ? 'income' : e.usage === 'work' ? 'work' : '';
    return `
      <div class="mini-row">
        <div class="mini-icon" style="background:${hexA(e.category_color, .12)}">${e.category_icon}</div>
        <div class="mini-main">
          <div class="mini-title">${esc(e.category_label)}${e.note ? ' · ' + esc(e.note) : ''}</div>
          <div class="mini-sub">${esc(dayLabel(e.occurred_at))}${e.account_name ? ' · ' + esc(e.account_name) : ''}</div>
        </div>
        <div class="mini-amt ${amtCls}">${signed(e.amount_cents, e.kind)}</div>
      </div>`;
  }

  function hexA(hex, a) {
    if (!hex || hex[0] !== '#') return 'transparent';
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function setSideCount() {
    const m = state.meta;
    if (m) $('#side-count').textContent = m.entry_count + ' 笔账目 · 本地存储';
  }

  /* ------------------------------------------------------------------ charts */

  function niceMax(v) {
    if (v <= 0) return 100;
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / mag;
    const steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
    const step = steps.find((s) => n <= s + 1e-9) || 10;
    return step * mag;
  }

  function trendChart(trend) {
    const W = 720, H = 208, pl = 40, pr = 10, pt = 14, pb = 26;
    const vals = trend.map((t) => (t.personal_cents + (t.nobudget_cents || 0)
      + (t.fixed_cents || 0) + t.work_cents) / 100);
    const top = niceMax(Math.max(...vals, 0));
    const iw = W - pl - pr, ih = H - pt - pb;
    const bw = iw / trend.length;
    const barW = Math.min(26, bw * 0.52);

    let grid = '';
    for (let i = 0; i <= 2; i++) {
      const y = pt + ih - (ih * i) / 2;
      const v = (top * i) / 2;
      grid += `<line class="grid-line" x1="${pl}" y1="${y}" x2="${W - pr}" y2="${y}"/>`;
      grid += `<text class="axis-text" x="${pl - 7}" y="${y + 3}" text-anchor="end">${v >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + 'k' : v.toFixed(0)}</text>`;
    }

    let bars = '';
    trend.forEach((t, i) => {
      const x = pl + i * bw + (bw - barW) / 2;
      const p = t.personal_cents / 100, n = (t.nobudget_cents || 0) / 100;
      const w = t.work_cents / 100, f = (t.fixed_cents || 0) / 100;
      const hp = (p / top) * ih, hn = (n / top) * ih;
      const hw = (w / top) * ih, hf = (f / top) * ih;
      const yp = pt + ih - hp, yn = yp - hn, yw = yn - hw, yf = yw - hf;
      const label = t.month.slice(5) + '月';
      const showLabel = i % 2 === 0 || i === trend.length - 1;
      const total = p + n + w + f;
      const tipText = `${t.month.slice(0, 4)}年${label}\n日常 ¥${fmt(t.personal_cents)} · 不计预算 ¥${fmt(t.nobudget_cents || 0)} · 报销 ¥${fmt(t.work_cents)} · 固定 ¥${fmt(t.fixed_cents || 0)}`;
      bars += `<g class="bar-group" data-month="${t.month}">
        <rect x="${x - (bw - barW) / 2}" y="${pt}" width="${bw}" height="${ih}" fill="transparent" data-tip="${esc(tipText)}" data-month="${t.month}"/>
        ${hp > 0 ? `<rect class="bar-seg" x="${x}" y="${yp}" width="${barW}" height="${Math.max(hp, 1.5)}" rx="${(hn > 0 || hw > 0 || hf > 0) ? 0 : 3}" fill="var(--accent)" data-tip="${esc(tipText)}" data-month="${t.month}"/>` : ''}
        ${hn > 0 ? `<rect class="bar-seg" x="${x}" y="${yn}" width="${barW}" height="${Math.max(hn, 1.5)}" rx="${(hw > 0 || hf > 0) ? 0 : 3}" fill="var(--nobudget)" data-tip="${esc(tipText)}" data-month="${t.month}"/>` : ''}
        ${hw > 0 ? `<rect class="bar-seg" x="${x}" y="${yw}" width="${barW}" height="${Math.max(hw, 1.5)}" rx="${hf > 0 ? 0 : 3}" fill="var(--work)" data-tip="${esc(tipText)}" data-month="${t.month}"/>` : ''}
        ${hf > 0 ? `<rect class="bar-seg" x="${x}" y="${yf}" width="${barW}" height="${Math.max(hf, 1.5)}" rx="3" fill="var(--fixed)" data-tip="${esc(tipText)}" data-month="${t.month}"/>` : ''}
        ${total > 0 ? `<text class="axis-text" x="${x + barW / 2}" y="${pt + ih + 15}" text-anchor="middle">${showLabel ? label : ''}</text>` : `<text class="axis-text" x="${x + barW / 2}" y="${pt + ih + 15}" text-anchor="middle">${showLabel ? label : ''}</text>`}
      </g>`;
    });

    return `<svg class="trend-svg" viewBox="0 0 ${W} ${H}">${grid}${bars}</svg>`;
  }

  function donutChart(cats, total, opts = {}) {
    const r = 56, cx = 74, cy = 74, sw = 17;
    const C = 2 * Math.PI * r;
    let off = 0;
    const segs = cats.map((c) => {
      const len = (c.amount_cents / (total || 1)) * C;
      const s = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${c.color || '#999'}"
        stroke-width="${sw}" stroke-dasharray="${Math.max(len - 1.5, 0)} ${C - Math.max(len - 1.5, 0)}"
        stroke-dashoffset="${-off}" transform="rotate(-90 ${cx} ${cy})" stroke-linecap="butt"
        data-tip="${esc(`${c.name}\n¥${fmt(c.amount_cents)} · ${Math.round(c.share * 100)}%`)}"
        data-cat-id="${c.id || ''}"></circle>`;
      off += len;
      return s;
    }).join('');

    const legend = cats.slice(0, 6).map((c) => `
      <div class="legend-row" data-cat-id="${c.id || ''}" data-tip="${esc(`${c.name}\n¥${fmt(c.amount_cents)} · ${Math.round(c.share * 100)}%`)}">
        <span class="dot" style="background:${c.color}"></span>
        <span class="name">${esc(c.name)}</span>
        <span class="amt">${opts.showAmount ? money(c.amount_cents) : Math.round(c.share * 100) + '%'}</span>
      </div>`).join('');

    return `<div class="donut-wrap">
      <div style="position:relative">
        <svg class="donut" viewBox="0 0 148 148">
          <circle cx="74" cy="74" r="${r}" fill="none" stroke="var(--soft)" stroke-width="${sw}"/>
          ${segs}
        </svg>
        <div style="position:absolute;inset:0;display:grid;place-items:center;text-align:center;pointer-events:none">
          <div>
            <div style="font-size:10px;color:var(--muted)">合计</div>
            <div style="font-size:14px;font-weight:650;font-variant-numeric:tabular-nums">${money(total)}</div>
          </div>
        </div>
      </div>
      <div class="legend">${legend}</div>
    </div>`;
  }

  /* ------------------------------------------------------------------ ledger */

  async function renderLedger() {
    refreshMonthLabel();
    const el = $('#view-ledger');
    const hasFilter = !!(state.filters.usage || state.filters.category_id
      || state.filters.account_id || state.filters.q || state.ledgerDate || state.ledgerRange);
    el.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">流水</h1>
          <div class="page-sub" id="ledger-sub">—</div>
        </div>
        <button class="btn sm ${state.ledgerContinuous ? '' : 'ghost'}" id="toggle-continuous">
          ${state.ledgerContinuous ? '按月查看' : '连续流水'}
        </button>
      </div>
      <div class="toolbar">
        <div class="seg" id="seg-usage">
          ${[['', '全部'], ['personal', '日常'], ['work', '报销']].map(([v, l]) =>
            `<button data-v="${v}" class="${state.filters.usage === v ? 'active' : ''}">${l}</button>`).join('')}
        </div>
        <div class="date-field" id="ledger-date-field"></div>
        <select class="select" id="f-cat">
          <option value="">全部分类</option>
          ${state.meta.categories.filter((c) => !c.parent_id && !c.archived).map((c) =>
            `<option value="${c.id}" ${String(state.filters.category_id) === String(c.id) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
          ${(() => {
            const active = state.filters.category_id
              ? state.meta.categories.find((c) => String(c.id) === String(state.filters.category_id)) : null;
            if (!active || !active.parent_id) return '';
            const parent = state.meta.categories.find((c) => c.id === active.parent_id);
            return `<option value="${active.id}" selected>${esc(parent ? parent.name : '')} · ${esc(active.name)}</option>`;
          })()}
        </select>
        <select class="select" id="f-acc">
          <option value="">全部账户</option>
          ${state.meta.accounts.filter((a) => !a.archived).map((a) =>
            `<option value="${a.id}" ${String(state.filters.account_id) === String(a.id) ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
        </select>
        ${hasFilter ? '<button class="btn sm ghost" id="clear-filters">清除筛选 ✕</button>' : ''}
        <div class="spacer"></div>
        <div class="count" id="ledger-count"></div>
      </div>
      <div class="card ledger" id="ledger-list"><div class="empty">加载中…</div></div>
      <div class="load-more" id="load-more"></div>
      <div class="bulkbar" id="bulkbar">
        <span class="n" id="bulk-n">0</span>
        <button data-bulk="personal">标为日常</button>
        <button data-bulk="work">标为报销</button>
        <button data-bulk="budget-toggle" title="不计入月度预算，但仍计入日常消费和总支出">不计预算</button>
        <button data-bulk="delete" class="danger">删除</button>
        <button class="close" data-bulk="clear">✕</button>
      </div>
    `;

    $('#seg-usage').onclick = (e) => {
      const b = e.target.closest('button[data-v]');
      if (!b) return;
      state.filters.usage = b.dataset.v;
      $$('#seg-usage button').forEach((x) => x.classList.toggle('active', x === b));
      state.ledger.offset = 0;
      loadLedger(true);
    };
    $('#f-cat').onchange = (e) => { state.filters.category_id = e.target.value; loadLedger(true); };
    $('#f-acc').onchange = (e) => { state.filters.account_id = e.target.value; loadLedger(true); };

    attachDatePicker(el.querySelector('#ledger-date-field'), state.ledgerDate, {
      compact: true,
      allowClear: true,
      onChange: (v) => {
        state.ledgerDate = v;
        state.ledgerRange = null;
        if (v) {
          state.month = v.slice(0, 7);
          state.monthBeforeSearch = state.month;
        }
        state.ledger.offset = 0;
        renderLedger();
      },
    });

    const clearFilters = $('#clear-filters');
    if (clearFilters) {
      clearFilters.onclick = () => {
        state.filters = { usage: '', status: '', category_id: '', account_id: '', q: '' };
        state.ledgerDate = '';
        state.ledgerRange = null;
        $('#global-search').value = '';
        state.ledger.offset = 0;
        renderLedger();
      };
    }

    $('#toggle-continuous').onclick = () => {
      state.ledgerContinuous = !state.ledgerContinuous;
      state.ledgerDate = '';
      state.ledgerRange = null;
      state.ledger.offset = 0;
      renderLedger();
    };
    $('#ledger-list').onclick = onLedgerClick;
    $('#bulkbar').onclick = onBulkClick;
    $('#load-more').onclick = () => loadLedger(false);

    await loadLedger(true);
    bindLedgerScroll();
  }

  async function loadLedger(reset) {
    const box = $('#ledger-list');
    if (!box || state.ledger.loading) return;
    if (reset) { state.ledger.offset = 0; state.selection.clear(); }
    state.ledger.loading = true;
    const f = state.filters;
    const p = new URLSearchParams();
    if (state.ledgerRange) {
      p.set('date_from', state.ledgerRange.from);
      p.set('date_to', state.ledgerRange.to);
    } else {
      if (state.ledgerContinuous) {
        if (state.month !== 'all') p.set('date_to', monthEndDate(state.month));
      } else if (state.month !== 'all') {
        p.set('month', state.month);
      }
      if (state.ledgerDate) {
        p.set('date_from', state.ledgerDate);
        p.set('date_to', state.ledgerDate);
      }
    }
    if (f.usage) p.set('usage', f.usage);
    if (f.status) p.set('status', f.status);
    if (f.category_id) p.set('category_id', f.category_id);
    if (f.account_id) p.set('account_id', f.account_id);
    if (f.q) p.set('q', f.q);
    p.set('limit', '100');
    p.set('offset', String(state.ledger.offset));

    try {
      const d = await get('/entries?' + p.toString());
      state.ledger.items = reset ? d.items : state.ledger.items.concat(d.items);
      state.ledger.offset = state.ledger.items.length;
      state.ledger.total = d.total;
      const head = state.ledgerRange ? state.ledgerRange.label
        : state.ledgerDate ? dayLabel(state.ledgerDate)
        : state.ledgerContinuous
          ? (state.month === 'all' ? '全部流水' : monthLabel(state.month) + ' 起往前')
          : monthLabel(state.month);
      $('#ledger-sub').textContent = state.ledgerContinuous
        ? (state.month === 'all' ? '全部流水 · 连续滚动'
          : `${monthLabel(state.month)} 起往前 · 连续滚动`)
        : `${head} · 支出 ${money(d.expense_cents)} · 收入 ${money(d.income_cents)}`;
      $('#ledger-count').textContent = `共 ${d.total} 笔`;
      paintLedger();
    } catch (e) {
      box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
    } finally {
      state.ledger.loading = false;
      fillLedgerViewport();
    }
  }

  function monthEndDate(month) {
    const [y, m] = month.split('-').map(Number);
    return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;
  }

  function bindLedgerScroll() {
    const scroller = document.querySelector('.views');
    if (!scroller) return;
    scroller.onscroll = () => {
      if (state.view !== 'ledger' || !state.ledgerContinuous) return;
      if (state.ledger.loading || state.ledger.items.length >= state.ledger.total) return;
      if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 320) {
        loadLedger(false);
      }
    };
  }

  function fillLedgerViewport() {
    if (state.view !== 'ledger' || !state.ledgerContinuous) return;
    if (state.ledger.loading || state.ledger.items.length >= state.ledger.total) return;
    const scroller = document.querySelector('.views');
    if (!scroller) return;
    if (scroller.scrollHeight <= scroller.clientHeight + 100) {
      setTimeout(() => loadLedger(false), 60);
    }
  }

  function paintLedger() {
    const box = $('#ledger-list');
    const items = state.ledger.items;
    if (!items.length) {
      box.innerHTML = '<div class="empty"><strong>没有符合条件的账目</strong>换个筛选条件，或先记一笔。</div>';
      $('#load-more').innerHTML = '';
      updateBulkbar();
      return;
    }
    const groups = new Map();
    items.forEach((e) => {
      const d = e.date;
      if (!groups.has(d)) groups.set(d, []);
      groups.get(d).push(e);
    });

    let html = '';
    let lastMonth = '';
    for (const [day, list] of groups) {
      const month = day.slice(0, 7);
      if (state.ledgerContinuous && month !== lastMonth) {
        lastMonth = month;
        html += `<div class="month-sep">${month.slice(0, 4)}年${Number(month.slice(5))}月</div>`;
      }
      const spend = list.filter((x) => x.kind === 'expense').reduce((s, x) => s + x.amount_cents, 0);
      const inc = list.filter((x) => x.kind === 'income').reduce((s, x) => s + x.amount_cents, 0);
      html += `<div class="day-group"><div class="day-head"><strong>${dayLabel(day)}</strong>
        <span class="sums">${spend ? '支出 ' + money(spend) : ''}${spend && inc ? ' · ' : ''}${inc ? '收入 ' + money(inc) : ''}</span></div>`;
      html += list.map(entryRow).join('');
      html += '</div>';
    }
    box.innerHTML = html;
    const more = items.length < state.ledger.total;
    if (!more) {
      if (!state.ledgerContinuous && state.month !== 'all'
        && !state.ledgerDate && !state.ledgerRange) {
        $('#load-more').innerHTML = '<button class="btn ghost" id="continue-older">继续看更早的流水 →</button>';
        $('#continue-older').onclick = () => { state.ledgerContinuous = true; renderLedger(); };
      } else {
        $('#load-more').innerHTML = `<div class="ledger-end">已经到底了 · 共 ${items.length} 笔</div>`;
      }
    } else if (state.ledgerContinuous) {
      $('#load-more').innerHTML = `<div class="ledger-end">继续下滑加载更早的流水…</div>`;
    } else {
      $('#load-more').innerHTML =
        `<button class="btn ghost">加载更多（还有 ${state.ledger.total - items.length} 笔）</button>`;
    }
    updateBulkbar();
  }

  function entryRow(e) {
    const checked = state.selection.has(e.id);
    const amtCls = e.kind === 'income' ? 'income' : e.usage === 'work' ? 'work' : '';
    const chips = [];
    if (e.source === 'reconcile') chips.push('<span class="chip muted">糊涂账</span>');
    else if (e.kind !== 'income') {
      if (e.usage === 'work') chips.push('<span class="chip work">报销</span>');
      else {
        if (e.exclude_daily) chips.push('<span class="chip muted">不计日常</span>');
        if (e.exclude_budget) chips.push('<span class="chip nobudget">不计预算</span>');
      }
    }
    const chip = chips.join('');
    return `
      <div class="entry ${checked ? 'checked' : ''}" data-id="${e.id}">
        <button class="check" data-act="select" title="选择"></button>
        <div class="icon" style="background:${hexA(e.category_color, .13)}">${e.category_icon}</div>
        <div class="body">
          <div class="l1"><span class="cat">${esc(e.category_label)}</span>${chip}</div>
          <div class="note">${esc(e.note || '')}${e.exclude_stats ? ' · 不计入收支' : ''}</div>
        </div>
        <div class="right">
          <div style="text-align:right">
            <div class="amt ${amtCls}">${signed(e.amount_cents, e.kind)}</div>
            <div class="acct">${esc(e.account_name || '未指定')} · ${timeLabel(e.occurred_at)}</div>
          </div>
          <div class="row-actions">
            <button data-act="edit" title="编辑"><svg viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z"/></svg></button>
            <button data-act="more" title="更多">⋯</button>
          </div>
        </div>
      </div>`;
  }

  function onLedgerClick(ev) {
    const row = ev.target.closest('.entry');
    if (!row) return;
    const id = Number(row.dataset.id);
    const act = ev.target.closest('[data-act]')?.dataset.act;
    if (act === 'edit') { openEntryModal(state.ledger.items.find((x) => x.id === id)); return; }
    if (act === 'more') { openRowMenu(id); return; }
    if (act === 'select') {
      if (state.selection.has(id)) state.selection.delete(id); else state.selection.add(id);
      row.classList.toggle('checked', state.selection.has(id));
      updateBulkbar();
      return;
    }
  }

  function updateBulkbar() {
    const bar = $('#bulkbar');
    if (!bar) return;
    const n = state.selection.size;
    bar.classList.toggle('show', n > 0);
    $('#bulk-n').textContent = `已选 ${n} 笔`;
    const selected = state.ledger.items.filter((e) => state.selection.has(e.id));
    const allNoBudget = selected.length > 0 && selected.every((e) => e.exclude_budget);
    const budgetBtn = bar.querySelector('[data-bulk="budget-toggle"]');
    if (budgetBtn) budgetBtn.textContent = allNoBudget ? '计入预算' : '不计预算';
  }

  async function onBulkClick(ev) {
    const act = ev.target.closest('[data-bulk]')?.dataset.bulk;
    if (!act) return;
    const ids = Array.from(state.selection);
    if (act === 'clear') { state.selection.clear(); paintLedger(); updateBulkbar(); return; }
    if (!ids.length) return;
    if (act === 'delete') {
      if (!(await uiConfirm(`删除选中的 ${ids.length} 笔账目？此操作不可撤销。`, { danger: true, okText: '删除' }))) return;
      await post('/entries/delete', { ids });
      toast('已删除 ' + ids.length + ' 笔');
    } else if (act === 'personal') {
      await post('/entries/bulk', { ids, usage: 'personal' });
      toast('已标为日常消费');
    } else if (act === 'work') {
      await post('/entries/bulk', { ids, usage: 'work' });
      toast('已标为报销');
    } else if (act === 'budget-toggle') {
      const selected = ids.map((id) => state.ledger.items.find((e) => e.id === id)).filter(Boolean);
      const allNoBudget = selected.length > 0 && selected.every((e) => e.exclude_budget);
      await post('/entries/bulk', { ids, exclude_budget: !allNoBudget });
      toast(allNoBudget ? '已计入预算' : '已标为不计预算');
    }
    state.selection.clear();
    await reloadAll();
  }

  function openRowMenu(id) {
    const e = state.ledger.items.find((x) => x.id === id);
    if (!e) return;
    const m = openModal(`
      <div class="modal-head"><h3>${esc(e.note || e.category_label)}</h3>
        <button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body" style="gap:10px">
        <div class="kv"><span class="k">金额</span><span class="v">${money(e.amount_cents)}</span></div>
        <div class="kv"><span class="k">分类</span><span class="v">${esc(e.category_label)}</span></div>
        <div class="kv"><span class="k">账户</span><span class="v">${esc(e.account_name || '—')}</span></div>
        <div class="kv"><span class="k">用途</span><span class="v">${e.usage === 'work' ? '报销' : e.exclude_daily ? '日常（不计入）' : '日常'}</span></div>
        <div class="kv"><span class="k">标记</span><span class="v">${e.usage === 'work' ? '报销' : '日常'}</span></div>
        <div class="kv"><span class="k">预算</span><span class="v">${e.exclude_budget ? '不计预算' : '计入预算'}</span></div>
        <div class="kv"><span class="k">时间</span><span class="v">${esc(e.occurred_at)}</span></div>
      </div>
      <div class="modal-foot">
        <button class="btn danger" data-act="del">删除</button>
        <button class="btn" data-act="edit">编辑</button>
      </div>`);
    m.querySelector('[data-act="del"]').onclick = async () => {
      if (!(await uiConfirm('删除这笔账目？', { danger: true, okText: '删除' }))) return;
      await del('/entries/' + e.id);
      closeModal(); toast('已删除'); await reloadAll();
    };
    m.querySelector('[data-act="edit"]').onclick = () => { closeModal(); openEntryModal(e); };
  }

  /* ------------------------------------------------------------------ entry modal */

  function categoryOptions(selected, kind) {
    const cats = state.meta.categories.filter((c) => !c.archived);
    const parents = cats.filter((c) => !c.parent_id);
    const incomeParent = parents.find((p) => p.name === '收入');
    const visible = kind === 'income'
      ? (incomeParent ? [incomeParent] : [])
      : parents.filter((p) => p.name !== '收入');
    return visible.map((p) => {
      const kids = cats.filter((c) => c.parent_id === p.id);
      if (!kids.length) return `<option value="${p.id}" ${String(selected) === String(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`;
      return `<optgroup label="${esc(p.name)}">${kids.map((k) =>
        `<option value="${k.id}" ${String(selected) === String(k.id) ? 'selected' : ''}>${esc(p.name)} · ${esc(k.name)}</option>`).join('')}</optgroup>`;
    }).join('');
  }

  function catInfo(id) {
    const c = state.meta.categories.find((x) => x.id === id);
    if (!c) return null;
    const p = c.parent_id ? state.meta.categories.find((x) => x.id === c.parent_id) : null;
    return { parent: p ? p.name : c.name, child: c.parent_id ? c.name : '' };
  }

  function workRuleMatch(parent, child, text) {
    const wr = (state.meta.rules && state.meta.rules.work) || { keywords: [], exclude: [] };
    const body = [parent || '', child || '', text || ''].join(' ');
    if ((wr.exclude || []).some((w) => w && body.includes(w))) return false;
    return (wr.keywords || []).some((w) => w && body.includes(w));
  }

  function defaultCategoryId(kind) {
    const cats = state.meta.categories.filter((c) => !c.archived);
    if (kind === 'income') {
      const inc = cats.find((c) => !c.parent_id && c.name === '收入');
      return inc ? inc.id : null;
    }
    const food = cats.find((c) => !c.parent_id && c.name === '食品餐饮');
    const lunch = food && cats.find((c) => c.parent_id === food.id && c.name === '午餐');
    return (lunch || food || {}).id || null;
  }

  function normalizeTime(raw) {
    const s = String(raw || '').trim();
    if (!s) return '';
    const m = s.match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return null;
    const h = Number(m[1]), mi = Number(m[2]);
    if (h > 23 || mi > 59) return null;
    return String(h).padStart(2, '0') + ':' + m[2];
  }

  function attachDatePicker(wrap, initial, opts = {}) {
    let value = initial ? initial.slice(0, 10) : '';
    let view = (value || todayISO()).slice(0, 7);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'date-btn' + (opts.compact ? ' compact' : '');
    const cal = document.createElement('div');
    cal.className = 'cal';
    cal.hidden = true;
    wrap.append(btn, cal);

    const dateLabel = (iso) => {
      const [y, m, d] = iso.split('-').map(Number);
      return `${y}年${m}月${d}日 ${WEEK[new Date(y, m - 1, d).getDay()]}`;
    };
    const paint = () => {
      btn.textContent = value ? dateLabel(value) : '全部日期';
      btn.classList.toggle('empty', !value);
    };

    function render() {
      const [y, m] = view.split('-').map(Number);
      const offset = (new Date(y, m - 1, 1).getDay() + 6) % 7;
      const start = new Date(y, m - 1, 1 - offset);
      const today = todayISO();
      let cells = ['一', '二', '三', '四', '五', '六', '日']
        .map((d) => `<div class="cal-dow">${d}</div>`).join('');
      for (let i = 0; i < 42; i++) {
        const dt = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
        const iso = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' +
          String(dt.getDate()).padStart(2, '0');
        const cls = ['cal-day', dt.getMonth() !== m - 1 ? 'out' : '', iso === today ? 'today' : '',
          iso === value ? 'sel' : ''].filter(Boolean).join(' ');
        cells += `<button type="button" class="${cls}" data-d="${iso}">${dt.getDate()}</button>`;
      }
      cal.innerHTML = `
        <div class="cal-head">
          <button type="button" data-nav="-1">‹</button><span>${y}年${m}月</span>
          <button type="button" data-nav="1">›</button>
        </div>
        <div class="cal-grid">${cells}</div>
        <div class="cal-foot">
          <button type="button" data-today>今天</button>
          ${opts.allowClear ? '<button type="button" data-clear>整月</button>' : ''}
        </div>`;
      cal.querySelectorAll('[data-d]').forEach((el) => {
        el.onclick = () => {
          value = el.dataset.d;
          view = value.slice(0, 7);
          paint();
          cal.hidden = true;
          if (opts.onChange) opts.onChange(value);
        };
      });
      cal.querySelectorAll('[data-nav]').forEach((el) => {
        el.onclick = () => {
          const [yy, mm] = view.split('-').map(Number);
          const dt = new Date(yy, mm - 1 + Number(el.dataset.nav), 1);
          view = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0');
          render();
        };
      });
      cal.querySelector('[data-today]').onclick = () => {
        value = todayISO();
        view = value.slice(0, 7);
        paint();
        cal.hidden = true;
        if (opts.onChange) opts.onChange(value);
      };
      const clear = cal.querySelector('[data-clear]');
      if (clear) {
        clear.onclick = () => {
          value = '';
          view = todayISO().slice(0, 7);
          paint();
          cal.hidden = true;
          if (opts.onChange) opts.onChange('');
        };
      }
    }

    btn.onclick = (e) => {
      e.stopPropagation();
      cal.hidden = !cal.hidden;
      if (!cal.hidden) {
        view = (value || todayISO()).slice(0, 7);
        render();
      }
    };
    cal.onmousedown = (e) => e.stopPropagation();
    document.addEventListener('mousedown', (e) => { if (!wrap.contains(e.target)) cal.hidden = true; });
    paint();
    return { get: () => value };
  }

  function openEntryModal(entry) {
    const editing = !!entry;
    const now = new Date();
    const d = entry ? entry.occurred_at.slice(0, 10) : todayISO();
    const t = entry ? (entry.occurred_at.slice(11, 16) || '') : '';
    const kind = entry ? entry.kind : 'expense';
    const usage = entry ? entry.usage : 'personal';
    const catId = entry ? entry.category_id : defaultCategoryId(kind);

    const m = openModal(`
      <div class="modal-head"><h3>${editing ? '编辑账目' : '记一笔'}</h3>
        <button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body">
        <div class="amount-field"><span>¥</span>
          <input id="f-amount" inputmode="decimal" placeholder="0.00" value="${entry ? (entry.amount_cents / 100).toFixed(2) : ''}" autocomplete="off">
        </div>
        <div class="row-2">
          <div class="field"><label>日期</label>
            <div class="date-field" id="f-date-field"></div>
          </div>
          <div class="field"><label>时间（可选）</label>
            <input type="text" id="f-time" placeholder="HH:MM" value="${t}">
          </div>
        </div>
        <div class="field"><label>收支</label>
          <div class="seg" id="f-kind" style="align-self:flex-start">
            <button data-v="expense" class="${kind === 'expense' ? 'active' : ''}">支出</button>
            <button data-v="income" class="${kind === 'income' ? 'active' : ''}">收入</button>
          </div>
        </div>
        <div class="field"><label>分类</label><select id="f-cat">${categoryOptions(catId, kind)}</select></div>
        <div class="row-2">
          <div class="field"><label>账户</label><select id="f-acc">
            <option value="">未指定</option>
            ${state.meta.accounts.filter((a) => !a.archived).map((a) =>
              `<option value="${a.id}" ${entry && entry.account_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
          </select></div>
        </div>
        <div class="field" id="f-usage-wrap"><label>这笔钱算谁的</label>
          <div class="seg" id="f-usage" style="align-self:flex-start">
            <button data-v="personal" class="${usage === 'personal' ? 'active' : ''}">日常消费</button>
            <button data-v="work" class="${usage === 'work' ? 'active' : ''}">报销（不计日常）</button>
          </div>
          <div id="f-rule-hint" style="display:none;font-size:10.5px;color:var(--work)"></div>
        </div>
        <div class="field" id="f-project-wrap"><label>项目 / 事由（可选）</label>
          <input type="text" id="f-project" list="project-list" placeholder="例如：客户拜访、团建" value="${esc(entry ? entry.project : '')}">
          <datalist id="project-list">${state.meta.projects.map((p) => `<option value="${esc(p)}">`).join('')}</datalist>
        </div>
        <div class="field"><label>备注</label><input type="text" id="f-note" placeholder="可选" value="${esc(entry ? entry.note : '')}"></div>
        <details ${entry && (entry.exclude_stats || entry.exclude_budget) ? 'open' : ''}>
          <summary style="font-size:11.5px;color:var(--muted);cursor:pointer">更多选项</summary>
          <div class="checks" style="margin-top:10px">
            <label><input type="checkbox" id="f-xdaily" ${entry && entry.exclude_daily ? 'checked' : ''}> 不计入日常消费</label>
            <label><input type="checkbox" id="f-xstat" ${entry && entry.exclude_stats ? 'checked' : ''}> 不计入收支统计</label>
            <label><input type="checkbox" id="f-xbudget" ${entry && entry.exclude_budget ? 'checked' : ''}> 不计入预算</label>
          </div>
        </details>
      </div>
      <div class="modal-foot">
        ${editing ? '<button class="btn danger" id="f-del">删除</button>' : ''}
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="f-save">${editing ? '保存' : '记下'}</button>
      </div>`);

    const amount = m.querySelector('#f-amount');
    const datePick = attachDatePicker(m.querySelector('#f-date-field'), d);
    setTimeout(() => amount.focus(), 60);

    let curKind = kind, curUsage = usage;
    let usageTouched = editing;
    let dailyTouched = editing;

    m.querySelector('#f-kind').onclick = (ev) => {
      const b = ev.target.closest('button[data-v]');
      if (!b) return;
      curKind = b.dataset.v;
      if (curKind === 'income') curUsage = 'personal';
      $$('#f-kind button').forEach((x) => x.classList.toggle('active', x === b));
      m.querySelector('#f-cat').innerHTML = categoryOptions(defaultCategoryId(curKind), curKind);
      applyCategoryDefault();
    };
    m.querySelector('#f-usage').onclick = (ev) => {
      const b = ev.target.closest('button[data-v]');
      if (!b) return;
      usageTouched = true;
      curUsage = b.dataset.v;
      $$('#f-usage button').forEach((x) => x.classList.toggle('active', x === b));
      m.querySelector('#f-rule-hint').style.display = 'none';
      syncFields();
    };
    m.querySelector('#f-cat').addEventListener('change', applyCategoryDefault);

    function applyCategoryDefault() {
      if (curKind === 'income') { syncFields(); return; }
      const sel = m.querySelector('#f-cat');
      const info = catInfo(Number(sel.value));
      const hint = m.querySelector('#f-rule-hint');
      const matched = !!info && workRuleMatch(info.parent, info.child, '');
      const dailyBox = m.querySelector('#f-xdaily');
      if (dailyBox && !dailyTouched && ruleFixedHit(info)) dailyBox.checked = true;
      if (!usageTouched) {
        curUsage = matched ? 'work' : 'personal';
        $$('#f-usage button').forEach((x) => x.classList.toggle('active', x.dataset.v === curUsage));
      }
      if (matched && (!editing || curUsage !== 'work')) {
        hint.style.display = '';
        hint.textContent = curUsage === 'work'
          ? '已按当前报销规则标记，可手动改回'
          : '这类支出通常算报销';
      } else {
        hint.style.display = 'none';
      }
      syncFields();
    }

    function ruleFixedHit(info) {
      const fixed = (state.meta.rules && state.meta.rules.fixed) || { keywords: [] };
      const note = (m.querySelector('#f-note') || {}).value || '';
      return !!info && (fixed.keywords || []).some((w) =>
        w && (info.parent + ' ' + info.child + ' ' + note).includes(w));
    }

    function syncFields() {
      const income = curKind === 'income';
      const work = !income && curUsage === 'work';
      m.querySelector('#f-usage-wrap').style.display = income ? 'none' : '';
      m.querySelector('#f-project-wrap').style.display = work ? '' : 'none';
    }
    m.querySelector('#f-xdaily').addEventListener('change', () => { dailyTouched = true; });
    m.querySelector('#f-note').addEventListener('input', () => {
      if (editing) return;
      const sel = m.querySelector('#f-cat');
      const info = catInfo(Number(sel.value));
      if (!info) return;
      const matched = workRuleMatch(info.parent, info.child, m.querySelector('#f-note').value);
      const hint = m.querySelector('#f-rule-hint');
      if (!usageTouched) {
        curUsage = matched ? 'work' : 'personal';
        $$('#f-usage button').forEach((x) => x.classList.toggle('active', x.dataset.v === curUsage));
      }
      if (!dailyTouched) m.querySelector('#f-xdaily').checked = ruleFixedHit(info);
      if (matched) {
        hint.style.display = '';
        hint.textContent = curUsage === 'work'
          ? '已按当前报销规则标记，可手动改回'
          : '这类支出通常算报销';
      } else {
        hint.style.display = 'none';
      }
      syncFields();
    });

    syncFields();
    if (!editing) applyCategoryDefault();

    m.querySelector('#f-save').onclick = async () => {
      const val = parseFloat(String(amount.value).replace(/[^\d.]/g, ''));
      if (!val || val <= 0) { toast('请输入金额'); amount.focus(); return; }
      const time = normalizeTime(m.querySelector('#f-time').value);
      if (time === null) { toast('时间写成 HH:MM，例如 14:30'); return; }
      const body = {
        kind: curKind,
        occurred_at: datePick.get() + (time ? ' ' + time : ''),
        amount: Math.round(val * 100) / 100,
        category_id: Number(m.querySelector('#f-cat').value) || null,
        account_id: Number(m.querySelector('#f-acc').value) || null,
        note: m.querySelector('#f-note').value.trim(),
        usage: curKind === 'income' ? 'personal' : curUsage,
        project: m.querySelector('#f-project').value.trim(),
        exclude_stats: m.querySelector('#f-xstat').checked,
        exclude_daily: m.querySelector('#f-xdaily').checked,
        exclude_budget: m.querySelector('#f-xbudget').checked,
      };
      try {
        if (editing) await patch('/entries/' + entry.id, body);
        else await post('/entries', body);
        closeModal();
        toast(editing ? '已保存' : '已记下');
        await reloadAll();
      } catch (e) { toast(e.message); }
    };

    if (editing) {
      m.querySelector('#f-del').onclick = async () => {
        if (!(await uiConfirm('删除这笔账目？', { danger: true, okText: '删除' }))) return;
        await del('/entries/' + entry.id);
        closeModal(); toast('已删除'); await reloadAll();
      };
    }
  }

  /* ------------------------------------------------------------------ statistics */

  async function renderStats() {
    const el = $('#view-stats');
    const st = state.stats;
    if (state.routeParams) {
      const p = state.routeParams;
      if (p.get('period') === 'year' || p.get('period') === 'month') st.period = p.get('period');
      if (p.get('scope')) st.scope = p.get('scope');
      if (p.get('metric')) st.metric = p.get('metric');
      if (p.get('cat')) st.catMetric = p.get('cat');
      if (p.get('key')) st.key = p.get('key');
      const section = p.get('section') || p.get('tab');
      if (['flow', 'assets', 'cat', 'categories'].includes(section)) {
        st.scrollTo = section === 'categories' ? 'cat' : section;
      }
      state.routeParams = null;
    }
    if (st.catMetric !== 'income') st.catMetric = 'expense';
    if (st.scope === 'work' || st.scope === 'nobudget') st.metric = 'expense';
    if (!st.key) st.key = st.period === 'year' ? currentMonth().slice(0, 4) : currentMonth();

    el.innerHTML = '<div class="empty">加载中…</div>';
    let d;
    try {
      d = await get('/stats?period=' + st.period + '&key=' + encodeURIComponent(st.key) +
        '&scope=' + st.scope);
    } catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }

    const label = st.period === 'year' ? st.key + '年' : monthLabel(st.key);
    const scopeLabel = st.scope === 'work' ? '报销支出'
      : st.scope === 'nobudget' ? '不计预算'
      : st.scope === 'all' ? '全部支出' : '日常消费';
    const catIncome = st.catMetric === 'income';
    const chartCats = catIncome ? (d.income_categories || []) : d.categories;
    const chartTotal = catIncome ? d.summary.income_cents : d.summary.expense_cents;
    const lastAsset = d.assets.length ? d.assets[d.assets.length - 1]
      : { net_cents: 0, assets_cents: 0, liabilities_cents: 0, date: '' };
    const periodDays = Math.max(1, Math.round(
      (new Date(d.end + 'T00:00:00') - new Date(d.start + 'T00:00:00')) / 86400000
    ));
    const workAvg = d.summary.expense_count
      ? Math.round(d.summary.expense_cents / d.summary.expense_count) : 0;
    const pct = (a, b) => b ? Math.round((a - b) / b * 100) : null;
    const dExp = pct(d.summary.expense_cents, d.prev.expense_cents);
    const dInc = pct(d.summary.income_cents, d.prev.income_cents);

    el.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">统计</h1>
          <div class="page-sub">${label} · ${scopeLabel}</div>
        </div>
        <div class="seg">
          <button data-period="month" class="${st.period === 'month' ? 'active' : ''}">月统计</button>
          <button data-period="year" class="${st.period === 'year' ? 'active' : ''}">年统计</button>
        </div>
      </div>

      <div class="stat-period">
        <button class="icon-btn" id="st-prev">‹</button>
        <span>${label}</span>
        <button class="icon-btn" id="st-next">›</button>
        <div class="spacer"></div>
        <div class="seg" id="st-scope">
          <button data-scope="all" class="${st.scope === 'all' ? 'active' : ''}">全部</button>
          <button data-scope="personal" class="${st.scope === 'personal' ? 'active' : ''}">日常</button>
          <button data-scope="nobudget" class="${st.scope === 'nobudget' ? 'active' : ''}">不计预算</button>
          <button data-scope="work" class="${st.scope === 'work' ? 'active' : ''}">报销</button>
        </div>
      </div>

      <div class="stats-hero">
        ${(st.scope === 'work' || st.scope === 'nobudget') ? `
          <div class="item">
            <div class="label">${scopeLabel}</div>
            <div class="value">${fmt(d.summary.expense_cents)}</div>
            <div class="delta">${dExp === null ? '—' : (dExp >= 0 ? '↑ ' : '↓ ') + Math.abs(dExp) + '% 较上期'}</div>
          </div>
          <div class="item">
            <div class="label">笔数</div>
            <div class="value">${d.summary.expense_count}</div>
            <div class="delta">${st.scope === 'work' ? '报销账目' : '不计预算账目'}</div>
          </div>
          <div class="item">
            <div class="label">单笔平均</div>
            <div class="value">${fmt(workAvg)}</div>
            <div class="delta">日均 ${fmt(Math.round(d.summary.expense_cents / periodDays))}</div>
          </div>
        ` : `
          <div class="item">
            <div class="label">${scopeLabel}</div>
            <div class="value">${fmt(d.summary.expense_cents)}</div>
            <div class="delta">${dExp === null ? '—' : (dExp >= 0 ? '↑ ' : '↓ ') + Math.abs(dExp) + '% 较上期'}</div>
          </div>
          <div class="item">
            <div class="label">收入</div>
            <div class="value">${fmt(d.summary.income_cents)}</div>
            <div class="delta">${dInc === null ? '—' : (dInc >= 0 ? '↑ ' : '↓ ') + Math.abs(dInc) + '% 较上期'}</div>
          </div>
          <div class="item">
            <div class="label">结余</div>
            <div class="value">${fmt(d.summary.balance_cents)}</div>
            <div class="delta">收入 − 支出</div>
          </div>
        `}
      </div>

      <div class="card stats-section" id="st-flow">
        <div class="card-head">
          <div><div class="card-title">收支统计</div>
            <div class="card-sub">${st.period === 'year' ? '按月' : '按天'} · ${scopeLabel}${st.scope === 'personal' ? '（不含固定）' : ''}</div></div>
          ${(st.scope !== 'work' && st.scope !== 'nobudget') ? `<div class="seg">
            <button data-metric="expense" class="${st.metric === 'expense' ? 'active' : ''}">支出</button>
            <button data-metric="income" class="${st.metric === 'income' ? 'active' : ''}">收入</button>
          </div>` : ''}
        </div>
        <div class="card-body" style="padding-top:10px">
          ${statsBarChart(d.series, st.metric, st.period, st.key)}
        </div>
      </div>

      <div class="card stats-section" id="st-assets">
        <div class="card-head">
          <div><div class="card-title">资产走势</div>
            <div class="card-sub">按已记录流水与当前余额推算</div></div>
          <div class="card-sub">净资产 <b style="color:var(--ink)">${money(lastAsset.net_cents)}</b></div>
        </div>
        <div class="card-body" style="padding-top:10px">
          ${statsLineChart(d.assets, st.period)}
        </div>
      </div>

      <div class="card stats-section" id="st-cat">
        <div class="card-head">
          <div><div class="card-title">收支占比</div>
            <div class="card-sub">${catIncome ? '收入 · 按分类' : scopeLabel + (st.scope === 'personal' ? '（不含固定）' : '') + ' · 按大类'}</div></div>
          <div class="seg">
            <button data-catmetric="expense" class="${!catIncome ? 'active' : ''}">支出</button>
            <button data-catmetric="income" class="${catIncome ? 'active' : ''}">收入</button>
          </div>
        </div>
        <div class="card-body">
          ${chartCats.length
            ? donutChart(chartCats, chartTotal, { showAmount: true })
            : `<div class="empty">这段时间没有${catIncome ? '收入' : '支出'}</div>`}
        </div>
        ${chartCats.length ? `<div class="stats-detail">
          ${chartCats.map((c) => `<div class="stats-cat-row" data-cat-id="${c.id || ''}">
            <span class="icon" style="background:${hexA(c.color, .13)}">${c.icon || '🧾'}</span>
            <span class="name">${esc(c.name)}<em>${c.count} 笔</em></span>
            <span class="amt">${money(c.amount_cents)}</span>
            <span class="pct">${Math.round(c.share * 100)}%</span>
          </div>`).join('')}
        </div>` : ''}
      </div>
    `;

    el.querySelectorAll('[data-period]').forEach((b) => {
      b.onclick = () => {
        const next = b.dataset.period;
        if (next === st.period) return;
        st.period = next;
        st.key = next === 'year' ? st.key.slice(0, 4) : (st.key.length === 4 ? currentMonth() : st.key);
        renderStats();
      };
    });
    el.querySelectorAll('[data-scope]').forEach((b) => {
      b.onclick = () => { st.scope = b.dataset.scope; renderStats(); };
    });
    el.querySelectorAll('[data-metric]').forEach((b) => {
      b.onclick = () => { st.metric = b.dataset.metric; renderStats(); };
    });
    el.querySelectorAll('[data-catmetric]').forEach((b) => {
      b.onclick = () => { st.catMetric = b.dataset.catmetric; renderStats(); };
    });

    $('#st-prev').onclick = () => {
      st.key = st.period === 'year' ? String(Number(st.key) - 1) : shiftMonthKey(st.key, -1);
      renderStats();
    };
    $('#st-next').onclick = () => {
      st.key = st.period === 'year' ? String(Number(st.key) + 1) : shiftMonthKey(st.key, 1);
      renderStats();
    };

    el.querySelectorAll('[data-drill-date], [data-drill-month]').forEach((node) => {
      node.onclick = () => {
        if (node.dataset.drillDate) {
          drillToLedger({ month: node.dataset.drillDate.slice(0, 7), date: node.dataset.drillDate });
        } else {
          drillToLedger({ month: node.dataset.drillMonth });
        }
      };
    });
    el.querySelectorAll('[data-cat-id]').forEach((node) => {
      node.onclick = (ev) => {
        ev.stopPropagation();
        const id = Number(node.dataset.catId);
        if (!id) return;
        drillToLedger({
          categoryId: id,
          month: st.period === 'month' ? st.key : '',
          year: st.period === 'year' ? st.key : '',
        });
      };
    });

    wireLineChart(el.querySelector('.line-chart'), d.assets, st.period);

    if (st.scrollTo) {
      const target = document.getElementById('st-' + st.scrollTo);
      st.scrollTo = '';
      if (target) setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    }
  }

  function drillToLedger({ categoryId, date, month, year }) {
    state.filters.category_id = categoryId ? String(categoryId) : '';
    state.filters.q = '';
    state.ledgerDate = date || '';
    state.ledgerRange = null;
    state.ledgerContinuous = false;
    if (month) {
      state.month = month;
      state.monthBeforeSearch = month;
    } else if (year) {
      state.month = 'all';
      state.ledgerRange = {
        from: year + '-01-01',
        to: year + '-12-31',
        label: year + '年',
      };
    }
    state.ledger.offset = 0;
    if (state.view === 'ledger') renderLedger();
    else location.hash = '#/ledger';
  }

  function shiftMonthKey(key, delta) {
    const [y, m] = key.split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
  }

  function statsBarChart(series, metric, period, key) {
    const W = 900, H = 250, pl = 52, pr = 14, pt = 16, pb = 28;
    const vals = series.map((s) => (metric === 'income' ? s.income_cents : s.expense_cents) / 100);
    const max = niceMax(Math.max(...vals, 1));
    const iw = W - pl - pr, ih = H - pt - pb;
    const bw = iw / series.length;
    const barW = Math.min(30, bw * 0.55);
    const y = (v) => pt + ih - (v / max) * ih;

    let grid = '';
    for (let i = 0; i <= 2; i++) {
      const v = (max * i) / 2;
      const yy = y(v);
      grid += `<line class="grid-line" x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}"/>`;
      grid += `<text class="axis-text" x="${pl - 8}" y="${yy + 3}" text-anchor="end">${v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)}</text>`;
    }

    const step = series.length > 15 ? 4 : 1;
    let bars = '';
    series.forEach((s, i) => {
      const v = vals[i];
      const x = pl + i * bw + (bw - barW) / 2;
      const h = (v / max) * ih;
      const color = metric === 'income' ? 'var(--reimb)' : 'var(--accent)';
      const showLabel = i % step === 0 || i === series.length - 1;
      const amount = metric === 'income' ? s.income_cents : s.expense_cents;
      const when = period === 'year' ? s.label : parseInt(s.label, 10) + '日';
      const tipText = `${when}\n${metric === 'income' ? '收入' : '支出'} ¥${fmt(amount)}`;
      const mm = String(i + 1).padStart(2, '0');
      const drill = period === 'year'
        ? `data-drill-month="${key}-${mm}"`
        : `data-drill-date="${key}-${mm}"`;
      bars += `<g>
        <rect x="${pl + i * bw}" y="${pt}" width="${bw}" height="${ih}" fill="transparent" data-tip="${esc(tipText)}" ${drill}/>
        ${v > 0 ? `<rect x="${x}" y="${y(v)}" width="${barW}" height="${Math.max(h, 1.5)}" rx="2.5" fill="${color}" data-tip="${esc(tipText)}" ${drill}/>` : ''}
        ${showLabel ? `<text class="axis-text" x="${x + barW / 2}" y="${H - 8}" text-anchor="middle">${s.label}</text>` : ''}
      </g>`;
    });

    return `<svg class="trend-svg" viewBox="0 0 ${W} ${H}">${grid}${bars}</svg>`;
  }

  function smoothPath(pts) {
    const n = pts.length;
    if (n < 2) return n === 1 ? `M ${pts[0].x} ${pts[0].y}` : '';
    const dx = [], dy = [], slope = [];
    for (let i = 0; i < n - 1; i++) {
      dx[i] = pts[i + 1].x - pts[i].x;
      dy[i] = pts[i + 1].y - pts[i].y;
    }
    for (let i = 0; i < n; i++) {
      if (i === 0) slope[i] = dy[0] / dx[0];
      else if (i === n - 1) slope[i] = dy[n - 2] / dx[n - 2];
      else {
        const s0 = dy[i - 1] / dx[i - 1], s1 = dy[i] / dx[i];
        slope[i] = s0 * s1 <= 0 ? 0 : (s0 + s1) / 2;
        const limit = 3 * Math.min(Math.abs(s0), Math.abs(s1));
        if (Math.abs(slope[i]) > limit) slope[i] = Math.sign(slope[i]) * limit;
      }
    }
    let d = `M ${pts[0].x} ${pts[0].y}`;
    for (let i = 0; i < n - 1; i++) {
      const c1x = pts[i].x + dx[i] / 3;
      const c1y = pts[i].y + slope[i] * dx[i] / 3;
      const c2x = pts[i + 1].x - dx[i] / 3;
      const c2y = pts[i + 1].y - slope[i + 1] * dx[i] / 3;
      d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${pts[i + 1].x} ${pts[i + 1].y}`;
    }
    return d;
  }

  function statsLineChart(points, period) {
    if (!points.length) return '<div class="empty">还没有资产数据</div>';
    const W = 900, H = 250, pl = 56, pr = 14, pt = 18, pb = 28;
    const vals = points.map((p) => p.net_cents / 100);
    let max = Math.max(...vals);
    let min = Math.min(...vals);
    if (max === min) { max += 100; min -= 100; }
    const pad = Math.max((max - min) * 0.1, 1);
    max += pad; min -= pad;
    const iw = W - pl - pr, ih = H - pt - pb;
    const y = (v) => pt + ih - ((v - min) / (max - min)) * ih;
    const x = (i) => pl + (iw * i) / Math.max(points.length - 1, 1);
    const coords = vals.map((v, i) => ({ x: x(i), y: y(v) }));
    const line = smoothPath(coords);
    const area = `M ${coords[0].x} ${y(min)} L ${line.replace(/^M\s*/, '')} L ${coords[coords.length - 1].x} ${y(min)} Z`;

    let grid = '';
    for (let i = 0; i <= 2; i++) {
      const v = min + ((max - min) * i) / 2;
      const yy = y(v);
      grid += `<line class="grid-line" x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}"/>`;
      grid += `<text class="axis-text" x="${pl - 8}" y="${yy + 3}" text-anchor="end">${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)}</text>`;
    }

    const step = points.length > 20 ? 4 : 1;
    let labels = '';
    points.forEach((p, i) => {
      if (i % step === 0 || i === points.length - 1) {
        const text = period === 'year' ? Number(p.date.slice(5, 7)) + '月' : String(Number(p.date.slice(8, 10)));
        labels += `<text class="axis-text" x="${x(i)}" y="${H - 8}" text-anchor="middle">${text}</text>`;
      }
    });

    const dots = points.map((p, i) => {
      if (i > 0 && p.net_cents === points[i - 1].net_cents) return '';
      return `<circle cx="${coords[i].x}" cy="${coords[i].y}" r="2.4" fill="var(--accent)" pointer-events="none"/>`;
    }).join('');

    return `<div class="line-chart">
      <svg class="trend-svg" viewBox="0 0 ${W} ${H}">
        ${grid}
        ${min < 0 ? `<line x1="${pl}" y1="${y(0)}" x2="${W - pr}" y2="${y(0)}" stroke="var(--line)"/>` : ''}
        <path d="${area}" fill="rgba(31,81,54,.10)" stroke="none"/>
        <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        ${dots}
        <g class="crosshair" style="display:none">
          <line x1="0" y1="${pt}" x2="0" y2="${pt + ih}" stroke="var(--faint)" stroke-dasharray="3 3"/>
          <circle r="4.2" fill="var(--accent)" stroke="var(--panel)" stroke-width="2"/>
        </g>
        <rect class="hover-zone" x="${pl}" y="${pt}" width="${iw}" height="${ih}" fill="transparent" style="cursor:crosshair"/>
        ${labels}
      </svg>
      <div class="line-tip"></div>
    </div>`;
  }

  function wireLineChart(container, points, period) {
    if (!container || !points.length) return;
    const svg = container.querySelector('svg');
    const zone = svg.querySelector('.hover-zone');
    const cross = svg.querySelector('.crosshair');
    const line = cross.querySelector('line');
    const dot = cross.querySelector('circle');
    const tip = container.querySelector('.line-tip');
    const W = 900, H = 250, pl = 56, pr = 14, pt = 18, pb = 28;
    const iw = W - pl - pr, ih = H - pt - pb;
    const vals = points.map((p) => p.net_cents / 100);
    let max = Math.max(...vals), min = Math.min(...vals);
    if (max === min) { max += 100; min -= 100; }
    const pad = Math.max((max - min) * 0.1, 1);
    max += pad; min -= pad;
    const y = (v) => pt + ih - ((v - min) / (max - min)) * ih;
    const x = (i) => pl + (iw * i) / Math.max(points.length - 1, 1);

    zone.addEventListener('mousemove', (e) => {
      const rect = svg.getBoundingClientRect();
      const vx = (e.clientX - rect.left) * (W / rect.width);
      let idx = Math.round((vx - pl) / (iw / Math.max(points.length - 1, 1)));
      idx = Math.max(0, Math.min(points.length - 1, idx));
      const px = x(idx), py = y(vals[idx]);
      line.setAttribute('x1', px);
      line.setAttribute('x2', px);
      dot.setAttribute('cx', px);
      dot.setAttribute('cy', py);
      cross.style.display = '';
      const p = points[idx];
      const dateLabel = period === 'year'
        ? `${p.date.slice(0, 4)}年${Number(p.date.slice(5, 7))}月`
        : `${Number(p.date.slice(5, 7))}月${Number(p.date.slice(8, 10))}日`;
      tip.textContent = `${dateLabel}\n净资产 ¥${fmt(p.net_cents)}`;
      tip.style.left = (px / W) * rect.width + 'px';
      tip.style.top = (py / H) * rect.height + 'px';
      tip.classList.add('show');
    });
    zone.addEventListener('mouseleave', () => {
      cross.style.display = 'none';
      tip.classList.remove('show');
    });
  }

  /* ------------------------------------------------------------------ assets */

  function accountIcon(a) {
    if (a.type === 'investment') return '📈';
    if (a.type === 'liability') return '💳';
    if (a.kind === 'wechat') return '💬';
    if (a.kind === 'alipay') return '🔷';
    if (a.kind === 'cash') return '💵';
    return '🏦';
  }

  async function renderAssets() {
    const el = $('#view-assets');
    el.innerHTML = '<div class="empty">加载中…</div>';
    let d;
    try { d = await get('/networth'); }
    catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }

    const change = d.month_change_cents;
    const assetAccounts = d.accounts.filter((a) => a.type !== 'liability');
    const debtAccounts = d.accounts.filter((a) => a.type === 'liability');
    const assetsTotal = assetAccounts.reduce((s, a) => s + a.balance_cents, 0);
    const debtTotal = debtAccounts.reduce((s, a) => s - a.balance_cents, 0);
    const netTotal = assetsTotal - debtTotal;
    const accountSum = d.accounts.reduce((s, a) => s + a.balance_cents, 0);
    const groups = [
      ['asset', '资金账户'],
      ['investment', '投资'],
      ['liability', '负债'],
    ];
    const rowsFor = (type) => d.accounts.filter((a) => a.type === type);

    el.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">资产</h1>
          <div class="page-sub">净资产 = 资金 + 投资 − 负债</div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn" id="reconcile">糊涂账</button>
          <button class="btn" id="add-transfer">转账</button>
          <button class="btn primary" id="add-account">+ 账户</button>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom:14px">
        <div class="card stat">
          <div class="stat-label"><span class="dot" style="background:var(--accent)"></span>净资产</div>
          <div class="stat-value"><small>¥</small>${fmt(netTotal)}</div>
          <div class="stat-sub">资金 + 投资 − 负债</div>
        </div>
        <div class="card stat">
          <div class="stat-label"><span class="dot" style="background:var(--accent)"></span>总资产</div>
          <div class="stat-value"><small>¥</small>${fmt(assetsTotal)}</div>
          <div class="stat-sub">${assetAccounts.length} 个账户</div>
        </div>
        <div class="card stat">
          <div class="stat-label"><span class="dot" style="background:var(--danger)"></span>总负债</div>
          <div class="stat-value"><small>¥</small>${fmt(debtTotal)}</div>
          <div class="stat-sub">${debtAccounts.length} 个负债账户</div>
        </div>
        <div class="card stat">
          <div class="stat-label"><span class="dot" style="background:var(--muted)"></span>本月净变化</div>
          <div class="stat-value" style="color:${change >= 0 ? 'var(--accent)' : 'var(--danger)'}">${change >= 0 ? '+' : '−'}¥${fmt(Math.abs(change))}</div>
          <div class="stat-sub">本月收入 − 支出</div>
        </div>
      </div>

      <div class="card clickable" style="margin-bottom:14px" id="asset-trend">
        <div class="card-head">
          <div><div class="card-title">净资产每月变化</div><div class="card-sub">收入 − 支出 · 近 12 个月</div></div>
        </div>
        <div class="card-body" style="padding-top:8px">${changeChart(d.trend)}</div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-head"><div class="card-title">账户</div>
            <div class="card-sub">${d.accounts.length} 个</div></div>
          <div style="padding:4px 0">
            ${groups.map(([type, title]) => rowsFor(type).length ? `
              <div class="group-title">${title}</div>
              ${rowsFor(type).map(accRow).join('')}
            ` : '').join('') || '<div class="empty">还没有账户</div>'}
          </div>
          <div style="padding:10px 18px;border-top:1px solid var(--line);font-size:11px;color:var(--muted);display:flex;justify-content:space-between">
            <span>账户余额合计</span><span style="font-variant-numeric:tabular-nums">${money(accountSum)}</span>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><div class="card-title">最近转账</div>
            <div class="card-sub">账户之间挪钱，不算收支</div></div>
          <div style="padding:4px 0">
            ${d.transfers.length ? d.transfers.map(transferRow).join('') : '<div class="empty">还没有转账记录</div>'}
          </div>
        </div>
      </div>
    `;

    const trendCard = $('#asset-trend');
    if (trendCard) trendCard.onclick = () => {
      location.hash = '#/stats?section=assets&period=month&key=' + currentMonth() + '&scope=personal';
    };
    el.querySelectorAll('#asset-trend [data-month]').forEach((node) => {
      node.onclick = (ev) => {
        ev.stopPropagation();
        location.hash = '#/stats?period=month&key=' + node.dataset.month + '&scope=all';
      };
    });
    $('#add-account').onclick = () => openAccountModal();
    $('#reconcile').onclick = () => openReconcileModal(d.accounts);
    $('#add-transfer').onclick = () => openTransferModal();
    el.querySelectorAll('[data-acc]').forEach((row) => {
      row.onclick = () => {
        const acc = d.accounts.find((a) => a.id === Number(row.dataset.acc));
        if (acc) openAccountModal(acc);
      };
    });
  }

  function accRow(a) {
    const negative = a.type === 'liability';
    return `<div class="acc-row" data-acc="${a.id}">
      <div class="icon" style="background:${hexA(a.color, .13)}">${accountIcon(a)}</div>
      <div class="main">
        <div class="name">${esc(a.name)}</div>
        <div class="sub">${a.type === 'liability' ? '负债' : a.type === 'investment' ? '投资' : '资金'} · ${a.used} 笔流水</div>
      </div>
      <div class="bal ${negative ? 'neg' : ''}">${negative ? '欠 ' + money(-a.balance_cents) : money(a.balance_cents)}</div>
    </div>`;
  }

  function transferRow(t) {
    return `<div class="mini-row" style="padding:9px 16px">
      <div class="mini-icon">⇄</div>
      <div class="mini-main">
        <div class="mini-title">${esc(t.from_name || '—')} → ${esc(t.to_name || '—')}</div>
        <div class="mini-sub">${esc(t.date)}${t.note ? ' · ' + esc(t.note) : ''}</div>
      </div>
      <div class="mini-amt">${money(t.amount_cents)}</div>
    </div>`;
  }

  function changeChart(trend) {
    const W = 900, H = 220, pl = 52, pr = 14, pt = 16, pb = 30;
    const vals = trend.map((t) => t.change_cents / 100);
    const maxAbs = Math.max(...vals.map(Math.abs), 100);
    const top = maxAbs * 1.15;
    const iw = W - pl - pr, ih = H - pt - pb;
    const y = (v) => pt + ih / 2 - (v / top) * (ih / 2);
    const bw = iw / trend.length;
    const barW = Math.min(30, bw * 0.5);
    const zero = y(0);

    let grid = '';
    for (const v of [top, 0, -top]) {
      const yy = y(v);
      grid += `<line class="grid-line" x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}"/>`;
      grid += `<text class="axis-text" x="${pl - 8}" y="${yy + 3}" text-anchor="end">${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)}</text>`;
    }

    let bars = '';
    trend.forEach((t, i) => {
      const v = t.change_cents / 100;
      const x = pl + i * bw + (bw - barW) / 2;
      const yy = y(v);
      const h = Math.abs(yy - zero);
      const color = v >= 0 ? 'var(--accent)' : 'var(--danger)';
      const tipText = `${t.month}\n净变化 ${v >= 0 ? '+' : '−'}¥${fmt(Math.abs(t.change_cents))}`;
      bars += `<g>
        <rect x="${pl + i * bw}" y="${pt}" width="${bw}" height="${ih}" fill="transparent" data-tip="${esc(tipText)}" data-month="${t.month}"/>
        <rect x="${x}" y="${Math.min(yy, zero)}" width="${barW}" height="${Math.max(h, 1.5)}" rx="3" fill="${color}" data-tip="${esc(tipText)}" data-month="${t.month}"/>
        <text class="axis-text" x="${x + barW / 2}" y="${H - 8}" text-anchor="middle">${i % 2 === 0 ? t.month.slice(5) + '月' : ''}</text>
      </g>`;
    });

    return `<svg class="trend-svg" viewBox="0 0 ${W} ${H}">${grid}${bars}</svg>`;
  }

  function openAccountModal(acc) {
    const editing = !!acc;
    const type = acc ? acc.type : 'asset';
    const raw = acc ? Math.abs(acc.balance_cents) / 100 : '';
    const m = openModal(`
      <div class="modal-head"><h3>${editing ? '编辑账户' : '新增账户'}</h3>
        <button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body">
        <div class="field"><label>账户名称</label>
          <input type="text" id="acc-name" placeholder="招商银行 / 支付宝 / 花呗" value="${esc(acc ? acc.name : '')}"></div>
        <div class="row-2">
          <div class="field"><label>类型</label>
            <select id="acc-type">
              <option value="asset" ${type === 'asset' ? 'selected' : ''}>资金账户</option>
              <option value="investment" ${type === 'investment' ? 'selected' : ''}>投资</option>
              <option value="liability" ${type === 'liability' ? 'selected' : ''}>负债（信用卡 / 借款）</option>
            </select></div>
          <div class="field"><label id="acc-bal-label">${type === 'liability' ? '当前欠款' : '当前余额'}</label>
            <input type="number" step="0.01" id="acc-bal" value="${raw}" placeholder="0.00"></div>
        </div>
        <div id="acc-hint" style="font-size:11px;color:var(--muted);line-height:1.6">
          填当前实际余额即可，糊涂账会反推期初，后续记账自动跟着变。
        </div>
        <div id="acc-breakdown"></div>
      </div>
      <div class="modal-foot">
        ${editing ? '<button class="btn danger" id="acc-del">删除</button>' : ''}
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="acc-save">保存</button>
      </div>`);

    const typeSel = m.querySelector('#acc-type');
    const balInput = m.querySelector('#acc-bal');
    const hint = m.querySelector('#acc-hint');
    const syncHint = () => {
      const bal = parseFloat(balInput.value || '0');
      if (typeSel.value !== 'liability' && bal < 0) {
        hint.textContent = '余额是负数：信用卡 / 借款请把类型改成「负债」，否则会冲减总资产。';
        hint.style.color = 'var(--danger)';
      } else {
        hint.textContent = '填当前实际余额即可，糊涂账会反推期初，后续记账自动跟着变。';
        hint.style.color = 'var(--muted)';
      }
    };
    typeSel.onchange = () => {
      m.querySelector('#acc-bal-label').textContent = typeSel.value === 'liability' ? '当前欠款' : '当前余额';
      syncHint();
    };
    balInput.addEventListener('input', syncHint);

    if (editing) {
      get('/accounts/' + acc.id + '/detail').then((d) => {
        const box = m.querySelector('#acc-breakdown');
        if (!box) return;
        box.className = 'acc-breakdown';
        box.innerHTML = `
          <div class="kv"><span class="k">期初</span><span class="v">${money(d.opening_cents)}</span></div>
          <div class="kv"><span class="k">收入</span><span class="v">+${money(d.income_cents)}</span></div>
          <div class="kv"><span class="k">支出</span><span class="v">−${money(d.expense_cents)}</span></div>
          <div class="kv"><span class="k">转入 / 转出</span><span class="v">+${money(d.transfer_in_cents)} / −${money(d.transfer_out_cents)}</span></div>
          <div class="kv"><span class="k">当前余额</span><span class="v" style="font-weight:700">${money(d.balance_cents)}</span></div>
          <button type="button" class="btn sm ghost" style="margin-top:10px" id="acc-ledger">查看这个账户的 ${d.entry_count} 笔流水 →</button>`;
        box.querySelector('#acc-ledger').onclick = () => {
          closeModal();
          state.filters.account_id = String(acc.id);
          state.ledger.offset = 0;
          if (state.view === 'ledger') loadLedger(true);
          else location.hash = '#/ledger';
        };
      }).catch(() => {});
    }

    m.querySelector('#acc-save').onclick = async () => {
      const name = m.querySelector('#acc-name').value.trim();
      if (!name) { toast('填个账户名'); return; }
      const t = typeSel.value;
      let bal = parseFloat(m.querySelector('#acc-bal').value || '0');
      if (Number.isNaN(bal)) bal = 0;
      const target = t === 'liability' ? -Math.abs(bal) : bal;
      try {
        if (editing) {
          await patch('/accounts/' + acc.id, { name, type: t, current_balance: target });
        } else {
          await post('/accounts', { name, type: t, current_balance: target });
        }
        closeModal(); toast(editing ? '已保存' : '已添加');
        await reloadMeta();
        if (state.view === 'settings') renderSettings(); else renderAssets();
      } catch (e) { toast(e.message); }
    };
    if (editing) {
      m.querySelector('#acc-del').onclick = async () => {
        if (!(await uiConfirm('删除这个账户？有流水的会被归档。', { danger: true, okText: '删除' }))) return;
        await del('/accounts/' + acc.id);
        closeModal();
        if (state.view === 'settings') renderSettings(); else renderAssets();
      };
    }
  }

  function openReconcileModal(accounts) {
    const list = accounts.filter((a) => !a.archived);
    const typeLabel = (a) => a.type === 'liability' ? '负债' : a.type === 'investment' ? '投资' : '资金';
    const inputValue = (a) => (a.type === 'liability' ? Math.abs(a.balance_cents) : a.balance_cents) / 100;

    const rows = list.map((a) => `
      <div class="rec-row" data-id="${a.id}">
        <div class="rec-main">
          <div class="name">${esc(a.name)} <em>${typeLabel(a)}</em></div>
          <div class="cur">当前 ${a.type === 'liability' ? '欠 ' + money(-a.balance_cents) : money(a.balance_cents)}</div>
        </div>
        <input type="number" step="0.01" data-rec="${a.id}" value="${inputValue(a).toFixed(2)}">
        <div class="diff" data-diff="${a.id}"></div>
      </div>`).join('');

    const m = openModal(`
      <div class="modal-head"><h3>糊涂账</h3>
        <button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body">
        <div style="font-size:11.5px;color:var(--muted);line-height:1.7;background:var(--panel-2);border:1px solid var(--line);border-radius:10px;padding:10px 12px">
          流水对不上的时候用：直接填每个账户现在实际有多少钱。糊涂账会生成一笔「糊涂账调整」补差额，
          调整记录不计入收支统计、日常消费和预算，只影响账户余额和净资产。
        </div>
        <div class="rec-list">${rows}</div>
        <div class="field"><label>说明（可选）</label>
          <input type="text" id="rec-reason" placeholder="例如：8 月有几天没记账 / 某笔支出漏记">
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="rec-save">生成调整记录</button>
      </div>`);

    const byId = new Map(list.map((a) => [a.id, a]));
    const updateDiff = (id) => {
      const acc = byId.get(id);
      const input = m.querySelector(`[data-rec="${id}"]`);
      const box = m.querySelector(`[data-diff="${id}"]`);
      if (!acc || !input) return;
      let v = parseFloat(input.value || '0');
      if (!Number.isFinite(v)) v = 0;
      const target = Math.round((acc.type === 'liability' ? -Math.abs(v) : v) * 100);
      const diff = target - acc.balance_cents;
      if (diff === 0) {
        box.textContent = '';
        box.className = 'diff';
      } else {
        box.textContent = (diff > 0 ? '补 +' : '冲 −') + money(Math.abs(diff));
        box.className = 'diff ' + (diff > 0 ? 'up' : 'down');
      }
    };
    m.querySelectorAll('[data-rec]').forEach((input) => {
      input.addEventListener('input', () => updateDiff(Number(input.dataset.rec)));
      updateDiff(Number(input.dataset.rec));
    });

    m.querySelector('#rec-save').onclick = async () => {
      const balances = {};
      m.querySelectorAll('[data-rec]').forEach((input) => {
        const id = Number(input.dataset.rec);
        const acc = byId.get(id);
        let v = parseFloat(input.value || '0');
        if (!Number.isFinite(v)) v = 0;
        balances[id] = acc.type === 'liability' ? -Math.abs(v) : v;
      });
      try {
        const res = await post('/reconcile', {
          balances,
          reason: m.querySelector('#rec-reason').value.trim(),
        });
        closeModal();
        if (!res.adjusted) { toast('余额没有变化，不需要调整'); return; }
        toast(`已生成 ${res.adjusted} 笔糊涂账调整`);
        await reloadMeta();
        renderAssets();
      } catch (e) { toast(e.message); }
    };
  }

  function openTransferModal() {
    get('/accounts').then(({ items }) => {
      const accs = items.filter((a) => !a.archived);
      if (accs.length < 2) { toast('至少需要两个账户'); return; }
      const m = openModal(`
        <div class="modal-head"><h3>账户转账</h3><button class="icon-btn" data-close>✕</button></div>
        <div class="modal-body">
          <div class="row-2">
            <div class="field"><label>转出</label><select id="tr-from">
              ${accs.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}
            </select></div>
            <div class="field"><label>转入</label><select id="tr-to">
              ${accs.map((a, i) => `<option value="${a.id}" ${i === 1 ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
            </select></div>
          </div>
          <div class="row-2">
            <div class="field"><label>金额</label><input type="number" step="0.01" id="tr-amt" placeholder="0.00"></div>
            <div class="field"><label>日期</label><div class="date-field" id="tr-date-field"></div></div>
          </div>
          <div class="field"><label>备注（可选）</label><input type="text" id="tr-note" placeholder="还信用卡 / 提现"></div>
          <div style="font-size:11px;color:var(--muted)">转账只是账户间挪钱，不算支出也不算收入。</div>
        </div>
        <div class="modal-foot">
          <button class="btn" data-close>取消</button>
          <button class="btn primary" id="tr-save">转账</button>
        </div>`);
      const trDate = attachDatePicker(m.querySelector('#tr-date-field'), todayISO());
      m.querySelector('#tr-save').onclick = async () => {
        const amount = parseFloat(m.querySelector('#tr-amt').value || '0');
        const from = Number(m.querySelector('#tr-from').value);
        const to = Number(m.querySelector('#tr-to').value);
        if (!amount || amount <= 0) { toast('填写金额'); return; }
        if (from === to) { toast('转出和转入不能是同一个账户'); return; }
        try {
          await post('/transfers', {
            date: trDate.get(), from_account_id: from,
            to_account_id: to, amount, note: m.querySelector('#tr-note').value.trim(),
          });
          closeModal(); toast('已转账'); renderAssets();
        } catch (e) { toast(e.message); }
      };
    });
  }

  /* ------------------------------------------------------------------ budget */

  async function renderBudget() {
    if (state.month === 'all') state.month = currentMonth();
    const el = $('#view-budget');
    refreshMonthLabel();
    const month = state.month;
    el.innerHTML = '<div class="empty">加载中…</div>';
    let d;
    try { d = await get('/budget?month=' + month); }
    catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }

    const total = d.total;
    const pct = total.pct || 0;
    const over = total.budget_cents > 0 && total.spent_cents > total.budget_cents;
    const color = over ? 'var(--danger)' : pct > 0.8 ? 'var(--work)' : 'var(--accent)';
    const daily = total.daily || {};
    const allocated = total.allocated_cents || 0;
    const unallocated = total.unallocated_cents || 0;
    const allocLine = total.budget_cents
      ? (unallocated >= 0
        ? `分类预算合计 ${money(allocated)} · 未分配 ${money(unallocated)}`
        : `分类预算合计 ${money(allocated)} · <span class="neg">超出总预算 ${money(-unallocated)}</span>`)
      : (allocated ? `分类预算合计 ${money(allocated)} · 总预算未设置` : '');
    const dailyItems = [];
    if (total.budget_cents && daily.days_in_month) {
      dailyItems.push({ label: '日均预算', cents: daily.budget_cents });
      dailyItems.push({ label: '日均支出', cents: daily.spent_cents });
      if (daily.is_current) {
        dailyItems.push({ label: '今日已花', cents: daily.today_spent_cents });
        dailyItems.push({
          label: '今日剩余', cents: daily.today_remaining_cents,
          neg: daily.today_remaining_cents < 0,
        });
      } else if (daily.is_future) {
        dailyItems.push({ label: '剩余日均', cents: daily.remaining_daily_cents });
      }
    }

    el.innerHTML = `
      <div class="page-head">
        <div><h1 class="page-title">预算</h1>
          <div class="page-sub">${monthLabel(month)} · 只统计日常消费，报销、房租等固定支出和不计入预算的账目都不算</div></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <div class="card-head">
          <div><div class="card-title">本月总预算</div>
            <div class="card-sub">${total.budget_cents ? '已用 ' + Math.round(pct * 100) + '%' : '还没有设置总预算'}</div></div>
          <button class="btn sm ghost" id="edit-total">${total.budget_cents ? '修改' : '设置'}</button>
        </div>
        <div class="card-body">
          <div class="budget-big">
            <span class="used">${money(total.spent_cents)}</span>
            <span class="of">/ ${total.budget_cents ? money(total.budget_cents) : '未设置'}</span>
          </div>
          <div class="progress" style="height:8px;margin:12px 0 9px">
            <i style="width:${Math.min(pct, 1) * 100}%;background:${color}"></i>
          </div>
          <div class="budget-meta">
            ${total.budget_cents
              ? (over ? `<span style="color:var(--danger)">已超支 ${money(-total.remaining_cents)}</span>` : `剩余 ${money(total.remaining_cents)}`)
              : '设置一个总数，用来盯住整月开销'}
          </div>
          ${dailyItems.length ? `
            <div class="budget-daily" style="grid-template-columns:repeat(${dailyItems.length},1fr)">
              ${dailyItems.map((x) => `<div><span>${x.label}</span>
                <b class="${x.neg ? 'neg' : ''}">${money(x.cents)}</b></div>`).join('')}
            </div>
            <div class="budget-daily-hint">
              日均预算 = 总预算 ÷ 当月天数${daily.is_current ? ' · 日均支出 = 已花 ÷ 已过天数 · 今日剩余 = 日均预算 − 今日已花' : ''}
            </div>` : ''}
          ${allocLine ? `<div class="budget-alloc">${allocLine}</div>` : ''}
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><div class="card-title">分类预算</div><div class="card-sub">${d.items.length} 个分类 · 点击查看流水</div></div>
          <button class="btn sm ghost" id="add-budget">+ 添加</button>
        </div>
        <div style="padding:2px 0">
          ${d.items.length ? d.items.map(budgetRow).join('') : '<div class="empty"><strong>还没有分类预算</strong>给餐饮、购物这些大类设个上限，超了会变红。</div>'}
        </div>
      </div>

      ${d.suggestions.length ? `<div class="card" style="margin-top:14px">
        <div class="card-head"><div class="card-title">这些分类本月有支出但没设预算</div></div>
        <div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
          ${d.suggestions.map((s) => `<button class="btn sm" data-add-cat="${s.category_id}">${s.icon} ${esc(s.name)} · ${money(s.spent_cents)} +</button>`).join('')}
        </div>
      </div>` : ''}
    `;

    $('#edit-total').onclick = () => openBudgetModal(null, '本月总预算', total.budget_cents);
    $('#add-budget').onclick = () => openBudgetPicker(d);
    el.querySelectorAll('[data-budget]').forEach((row) => {
      row.onclick = () => {
        const item = d.items.find((i) => i.category_id === Number(row.dataset.budget));
        if (!item) return;
        state.filters.category_id = String(item.category_id);
        state.filters.usage = 'personal';
        state.filters.status = '';
        state.filters.q = '';
        state.ledgerDate = '';
        state.ledgerRange = null;
        state.ledgerContinuous = false;
        state.ledger.offset = 0;
        state.monthBeforeSearch = state.month;
        location.hash = '#/ledger';
      };
    });
    el.querySelectorAll('[data-budget-edit]').forEach((btn) => {
      btn.onclick = (ev) => {
        ev.stopPropagation();
        const item = d.items.find((i) => i.category_id === Number(btn.dataset.budgetEdit));
        if (item) openBudgetModal(item.category_id, item.name, item.budget_cents);
      };
    });
    el.querySelectorAll('[data-add-cat]').forEach((btn) => {
      btn.onclick = () => {
        const s = d.suggestions.find((x) => x.category_id === Number(btn.dataset.addCat));
        if (s) openBudgetModal(s.category_id, s.name, 0);
      };
    });
  }

  function budgetRow(b) {
    const pct = b.pct || 0;
    const over = b.spent_cents > b.budget_cents;
    const color = over ? 'var(--danger)' : pct > 0.8 ? 'var(--work)' : b.color;
    return `<div class="budget-row" data-budget="${b.category_id}">
      <div class="top">
        <span class="icon" style="background:${hexA(b.color, .13)}">${b.icon}</span>
        <span class="name">${esc(b.name)}</span>
        <span class="amt">${money(b.spent_cents)} <em>/ ${money(b.budget_cents)}</em></span>
        <button class="icon-btn" data-budget-edit="${b.category_id}" title="编辑预算">✎</button>
      </div>
      <div class="progress"><i style="width:${Math.min(pct, 1) * 100}%;background:${color}"></i></div>
      <div class="budget-meta">${over ? `<span style="color:var(--danger)">超支 ${money(b.spent_cents - b.budget_cents)}</span>` : `剩余 ${money(b.remaining_cents)}`} · ${Math.round(pct * 100)}%</div>
    </div>`;
  }

  function openBudgetPicker(d) {
    const m = openModal(`
      <div class="modal-head"><h3>添加分类预算</h3><button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body">
        <div class="field"><label>分类</label><select id="bd-cat">
          ${state.meta.categories.filter((c) => !c.parent_id && !c.archived && c.name !== '收入').map((c) =>
            `<option value="${c.id}">${esc(c.name)}</option>`).join('')}
        </select></div>
        <div class="field"><label>每月预算</label><input type="number" id="bd-amt" placeholder="1000"></div>
      </div>
      <div class="modal-foot">
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="bd-save">保存</button>
      </div>`);
    m.querySelector('#bd-save').onclick = async () => {
      const id = Number(m.querySelector('#bd-cat').value);
      const amount = parseFloat(m.querySelector('#bd-amt').value || '0');
      if (!amount || amount <= 0) { toast('填写预算金额'); return; }
      await put('/budgets', { category_id: id, amount });
      closeModal(); toast('已设置'); renderBudget();
    };
  }

  function openBudgetModal(categoryId, name, currentCents) {
    const m = openModal(`
      <div class="modal-head"><h3>${esc(name)}</h3><button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body">
        <div class="field"><label>每月预算金额</label>
          <input type="number" step="1" id="bm-amt" value="${currentCents ? Math.round(currentCents / 100) : ''}" placeholder="0">
        </div>
        <div style="font-size:11px;color:var(--muted)">填 0 并保存可以取消这个预算。预算按自然月重置。</div>
      </div>
      <div class="modal-foot">
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="bm-save">保存</button>
      </div>`);
    m.querySelector('#bm-save').onclick = async () => {
      const amount = parseFloat(m.querySelector('#bm-amt').value || '0');
      await put('/budgets', { category_id: categoryId, amount });
      closeModal(); toast(amount > 0 ? '已保存' : '已取消预算'); renderBudget();
    };
  }

  /* ------------------------------------------------------------------ import */

  async function renderImport() {
    const el = $('#view-import');
    let suggestions = [];
    try { suggestions = (await get('/import/suggestions')).files; } catch (_) {}
    el.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">导入</h1>
          <div class="page-sub">支持支付宝、微信支付账单、第三方记账 App 导出（.xlsx / .csv），以及恢复 .db 数据库备份</div>
        </div>
      </div>

      <div class="dropzone" id="dropzone">
        <svg viewBox="0 0 24 24"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v3a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-3"/></svg>
        <div class="big">把账单或 .db 备份拖进来</div>
        <div class="hint">或 <button class="btn sm" id="pick-file" style="margin-top:8px">选择文件</button>　·　支付宝 / 微信：账单 → 导出；其他记账 App：设置 → 导出账单；恢复：选 .db 备份</div>
        <input type="file" id="file-input" accept=".xlsx,.csv,.db,.sqlite,.sqlite3" hidden>
      </div>

      ${suggestions.length ? `
        <div class="card" style="margin-top:14px">
          <div class="card-head"><div class="card-title">本机找到的导出文件</div>
            <div class="card-sub">点击直接预览</div></div>
          <div class="card-body" style="padding-top:8px">
            ${suggestions.map((p) => `<div class="mini-row" data-path="${esc(p)}" style="cursor:pointer">
              <div class="mini-icon">📄</div>
              <div class="mini-main"><div class="mini-title">${esc(p.split('/').pop())}</div>
                <div class="mini-sub">${esc(p)}</div></div>
              <span class="card-link">预览 →</span>
            </div>`).join('')}
          </div>
        </div>` : ''}

      <div id="import-preview" style="margin-top:14px"></div>
    `;

    const dz = $('#dropzone');
    const input = $('#file-input');
    $('#pick-file').onclick = () => input.click();
    input.onchange = () => { if (input.files[0]) handleFile(input.files[0]); };
    ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => {
      e.preventDefault(); dz.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => {
      e.preventDefault(); dz.classList.remove('over');
    }));
    dz.addEventListener('drop', (e) => {
      const f = e.dataTransfer.files[0];
      if (f) handleFile(f);
    });
    el.querySelectorAll('[data-path]').forEach((row) => {
      row.onclick = () => previewFromPath(row.dataset.path);
    });
  }

  function isBackupFile(name) {
    return /\.(db|sqlite|sqlite3)$/i.test(name || '');
  }

  async function previewBackupFile(file) {
    const buf = await file.arrayBuffer();
    const res = await fetch('/api/import/backup/preview?filename=' + encodeURIComponent(file.name), {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf,
    });
    if (!res.ok) { toast((await res.json()).detail || '不是有效的备份'); return; }
    const data = await res.json();
    data._file = file;
    state.importPreview = data;
    paintBackupPreview(data);
  }

  async function handleFile(file) {
    if (isBackupFile(file.name)) return previewBackupFile(file);
    state.importFile = file;
    const buf = await file.arrayBuffer();
    const res = await fetch('/api/import/preview?filename=' + encodeURIComponent(file.name), {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf,
    });
    if (!res.ok) { toast((await res.json()).detail || '解析失败'); return; }
    state.importPreview = await res.json();
    state.importPreview._file = file;
    paintPreview();
  }

  async function previewFromPath(path) {
    if (isBackupFile(path)) { toast('请把 .db 备份拖进来，或点「选择文件」'); return; }
    const res = await fetch('/api/import/preview-path', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }),
    });
    if (!res.ok) { toast((await res.json()).detail || '解析失败'); return; }
    state.importPreview = await res.json();
    state.importPreview._path = path;
    paintPreview();
  }

  function paintBackupPreview(d) {
    const box = $('#import-preview');
    box.innerHTML = `
      <div class="card">
        <div class="card-head">
          <div><div class="card-title">预览 · ${esc(d.filename)}
              <span class="chip muted">数据库备份</span></div>
            <div class="card-sub">${(d.size / 1024).toFixed(0)} KB</div></div>
          <button class="btn sm ghost" id="clear-preview">取消</button>
        </div>
        <div class="card-body">
          <div class="preview-grid" style="margin-bottom:16px">
            <div class="preview-item"><div class="k">账目</div><div class="v">${d.entries} 笔</div></div>
            <div class="preview-item"><div class="k">账户</div><div class="v">${d.accounts}</div></div>
            <div class="preview-item"><div class="k">转账</div><div class="v">${d.transfers}</div></div>
            <div class="preview-item"><div class="k">预算</div><div class="v">${d.budgets}</div></div>
          </div>
          <div class="split-note" style="border:1px solid var(--line);border-radius:10px">
            <span style="font-size:13px">⚠️</span>
            <div>恢复会<b>完全覆盖</b>当前账目、账户、转账和预算。恢复前会自动把当前数据库备份到下载文件夹。</div>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px">
            <button class="btn danger" id="do-restore">恢复这个备份</button>
          </div>
        </div>
      </div>`;
    box.querySelector('#clear-preview').onclick = () => {
      state.importPreview = null;
      box.innerHTML = '';
    };
    box.querySelector('#do-restore').onclick = async () => {
      if (!(await uiConfirm('恢复后当前数据会被完全替换，确定吗？\n当前数据会先自动备份。',
        { danger: true, okText: '恢复' }))) return;
      const buf = await d._file.arrayBuffer();
      const res = await fetch('/api/import/backup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: buf,
      });
      if (!res.ok) {
        let msg = '恢复失败';
        try { msg = (await res.json()).detail || msg; } catch (_) {}
        toast(msg);
        return;
      }
      const out = await res.json();
      state.importPreview = null;
      await reloadMeta();
      state.month = (state.meta.months || [])[0] || currentMonth();
      state.ledgerDate = '';
      state.ledgerRange = null;
      state.filters = { usage: '', status: '', category_id: '', account_id: '', q: '' };
      $('#global-search').value = '';
      box.innerHTML = `
        <div class="card">
          <div class="card-head"><div class="card-title">恢复完成</div></div>
          <div class="card-body">
            <div class="preview-grid" style="margin-bottom:14px">
              <div class="preview-item"><div class="k">账目</div><div class="v">${out.counts.entries} 笔</div></div>
              <div class="preview-item"><div class="k">账户</div><div class="v">${out.counts.accounts}</div></div>
              <div class="preview-item"><div class="k">转账</div><div class="v">${out.counts.transfers}</div></div>
              <div class="preview-item"><div class="k">预算</div><div class="v">${out.counts.budgets}</div></div>
            </div>
            <div class="kv"><span class="k">恢复前已备份到</span>
              <span class="v mono" style="word-break:break-all;max-width:70%;text-align:right">${esc(out.backup_path)}</span></div>
            <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
              <button class="btn" id="reveal-restore">打开所在文件夹</button>
              <button class="btn primary" id="go-overview">去概览</button>
            </div>
          </div>
        </div>`;
      box.querySelector('#reveal-restore').onclick = async () => {
        try { await post('/export/reveal', { path: out.backup_path }); }
        catch (e) { toast(e.message); }
      };
      box.querySelector('#go-overview').onclick = () => { location.hash = '#/overview'; };
    };
  }

  function paintPreview() {
    const d = state.importPreview;
    if (!d) return;
    const box = $('#import-preview');
    box.innerHTML = `
      <div class="card">
        <div class="card-head">
          <div><div class="card-title">预览 · ${esc(d.filename || (d._path || '').split('/').pop())}
              <span class="chip muted">${d.source === 'alipay' ? '支付宝' : d.source === 'wechat' ? '微信支付' : '第三方记账'}</span></div>
            <div class="card-sub">${d.start} ~ ${d.end} · ${d.total} 行</div></div>
          <button class="btn sm ghost" id="clear-preview">取消</button>
        </div>
        <div class="card-body">
          <div class="preview-grid" style="margin-bottom:16px">
            <div class="preview-item"><div class="k">支出笔数</div><div class="v">${d.expense_count}</div></div>
            <div class="preview-item"><div class="k">支出合计</div><div class="v">${money(d.expense_cents)}</div></div>
            <div class="preview-item"><div class="k">收入笔数</div><div class="v">${d.income_count}</div></div>
            <div class="preview-item"><div class="k">收入合计</div><div class="v">${money(d.income_cents)}</div></div>
          </div>
          <div class="kv"><span class="k">账户</span><span class="v">${d.accounts.length ? esc(d.accounts.join('、')) : '未指定'}</span></div>
          <div class="kv"><span class="k">分类${d.source !== 'onewood' ? '（自动映射）' : ''}</span><span class="v">${d.categories} 个大类${d.category_pairs ? ' · ' + d.category_pairs + ' 个子类' : ''}</span></div>
          ${d.auto_work_count ? `<div class="kv"><span class="k">默认标为报销</span><span class="v">${d.auto_work_count} 笔 · 高铁 / 飞机 / 酒店 / 打车</span></div>` : ''}
          ${d.fixed_count ? `<div class="kv"><span class="k">默认不计日常</span><span class="v">${d.fixed_count} 笔 · 房租 / 租金</span></div>` : ''}
          ${d.statuses && Object.keys(d.statuses).length ? `<div class="kv"><span class="k">交易状态</span><span class="v">${Object.entries(d.statuses).map(([k, v]) => esc(k) + ' ' + v).join(' · ')}</span></div>` : ''}
          ${d.skipped_total ? `<div class="kv"><span class="k">已跳过</span><span class="v">${d.skipped_total} 行 · ${Object.entries(d.skipped || {}).map(([k, v]) => esc(k) + ' ' + v).join('、')}</span></div>` : ''}
          <div class="kv"><span class="k">标记「不计入收支」</span><span class="v">${d.exclude_stats_count} 行</span></div>
          <div class="kv"><span class="k">含退款</span><span class="v">${d.refund_count} 行</span></div>


          ${d.source !== 'onewood' ? `<div class="split-note" style="border:1px solid var(--line);border-radius:10px;margin-top:14px">
            <span style="font-size:13px">⚠️</span>
            <div>如果同一时间段已经从别的渠道导入过，同一笔消费会被重复统计。建议只保留一边，或先删掉另一边的同期记录。</div>
          </div>` : ''}

          <div style="margin:16px 0 8px;font-size:11.5px;color:var(--muted)">导入方式</div>
          <div class="radio-row">
            <div class="radio-card ${state.importMode === 'merge' ? 'sel' : ''}" data-mode="merge">
              <strong>合并导入</strong><span>保留现有数据，按行去重（推荐）</span></div>
            <div class="radio-card ${state.importMode === 'replace' ? 'sel' : ''}" data-mode="replace">
              <strong>清空后导入</strong><span>会删除现有账目和报销单</span></div>
          </div>

          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px">
            <button class="btn primary" id="do-import">导入 ${d.total} 行</button>
          </div>
        </div>
      </div>`;

    box.querySelectorAll('.radio-card').forEach((card) => {
      card.onclick = () => {
        state.importMode = card.dataset.mode;
        box.querySelectorAll('.radio-card').forEach((c) => c.classList.toggle('sel', c === card));
      };
    });
    box.querySelector('#clear-preview').onclick = () => { state.importPreview = null; box.innerHTML = ''; };
    box.querySelector('#do-import').onclick = doImport;
  }

  async function doImport() {
    const d = state.importPreview;
    if (!d) return;
    if (state.importMode === 'replace' && !(await uiConfirm('这会清空现有账目和报销单，确定吗？', { danger: true, okText: '清空并导入' }))) return;
    const btn = $('#do-import');
    btn.disabled = true; btn.textContent = '导入中…';
    try {
      let res;
      if (d._path) {
        res = await post('/import/path', { path: d._path, mode: state.importMode });
      } else {
        const buf = await d._file.arrayBuffer();
        const r = await fetch('/api/import?filename=' + encodeURIComponent(d._file.name) + '&mode=' + state.importMode, {
          method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf,
        });
        if (!r.ok) throw new Error((await r.json()).detail || '导入失败');
        res = await r.json();
      }
      toast(`导入完成：新增 ${res.imported} 笔，跳过 ${res.skipped} 笔`);
      state.importPreview = null;
      $('#import-preview').innerHTML = `
        <div class="card"><div class="card-body" style="text-align:center;padding:34px">
          <div style="font-size:30px">✅</div>
          <div style="font-size:16px;font-weight:650;margin:8px 0 4px">导入完成</div>
          <div style="font-size:12px;color:var(--muted)">新增 ${res.imported} 笔 · 跳过 ${res.skipped} 笔${res.auto_work ? ' · 其中 ' + res.auto_work + ' 笔标为报销' : ''}${res.fixed_rows ? ' · ' + res.fixed_rows + ' 笔不计日常' : ''}${res.work_rows ? ' · 含报销记录 ' + res.work_rows + ' 条' : ''}${res.source === 'alipay' ? ' · 来自支付宝' : res.source === 'wechat' ? ' · 来自微信支付' : ''}</div>
          <div style="margin-top:16px;display:flex;gap:8px;justify-content:center">
            <a class="btn" href="#/overview">去看概览</a>
            <a class="btn primary" href="#/ledger">打开流水</a>
          </div>
        </div></div>`;
      await reloadMeta();
      state.month = state.meta.months[0] || currentMonth();
      state.monthBeforeSearch = state.month;
      setSideCount();
    } catch (e) {
      toast(e.message);
      btn.disabled = false; btn.textContent = '重试';
    }
  }

  /* ------------------------------------------------------------------ settings */

  function openExportModal() {
    const now = currentMonth();
    let scope = 'all';
    const m = openModal(`
      <div class="modal-head"><h3>导出账目</h3>
        <button class="icon-btn" data-close>✕</button></div>
      <div class="modal-body">
        <div class="field"><label>范围</label>
          <div class="seg" id="export-scope">
            <button data-scope="all" class="active">全部账目</button>
            <button data-scope="year">今年</button>
            <button data-scope="month">本月</button>
          </div>
        </div>
        <div style="font-size:11.5px;color:var(--muted);line-height:1.7">
          CSV 带 BOM，Excel 可直接打开。包含日期、时间、收支、金额、分类、账户、用途、标记、项目、备注和排除标记。
          转账和预算不在 CSV 里，完整备份请用「备份数据库」。
        </div>
        <div id="export-result" style="margin-top:12px"></div>
      </div>
      <div class="modal-foot">
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="export-go">导出</button>
      </div>`);

    m.querySelectorAll('#export-scope button').forEach((b) => {
      b.onclick = () => {
        scope = b.dataset.scope;
        m.querySelectorAll('#export-scope button').forEach((x) => x.classList.toggle('active', x === b));
      };
    });
    m.querySelector('#export-go').onclick = async () => {
      const btn = m.querySelector('#export-go');
      btn.disabled = true;
      try {
        const res = await post('/export', {
          scope,
          month: now,
          year: now.slice(0, 4),
        });
        const box = m.querySelector('#export-result');
        box.innerHTML = `
          <div style="font-size:12px">已导出 ${res.count} 笔</div>
          <div class="mono" style="font-size:10.5px;color:var(--muted);word-break:break-all;margin:4px 0 8px">${esc(res.path)}</div>
          <button class="btn sm" id="export-reveal">打开所在文件夹</button>`;
        box.querySelector('#export-reveal').onclick = async () => {
          try { await post('/export/reveal', { path: res.path }); }
          catch (e) { toast(e.message); }
        };
      } catch (e) {
        toast(e.message);
      } finally {
        btn.disabled = false;
      }
    };
  }

  async function renderSettings() {
    const el = $('#view-settings');
    const [cats, accs, info] = await Promise.all([
      get('/categories'), get('/accounts'), get('/settings'),
    ]);
    const parents = cats.items.filter((c) => !c.parent_id);
    const rcfg = state.meta.rules || {};
    const wr = rcfg.work || { keywords: [], exclude: [], categories: [] };
    const fr = rcfg.fixed || { keywords: [], categories: [] };
    const ruleChip = (text, cls, section, value) =>
      `<span class="chip ${cls}">${esc(text)}<button type="button" class="chip-x" data-rule-remove="${section}" data-value="${esc(value)}">×</button></span>`;

    el.innerHTML = `
      <div class="page-head">
        <div><h1 class="page-title">设置</h1>
          <div class="page-sub">分类、账户与本地数据</div></div>
      </div>
      <div class="settings-grid">
        <div class="card">
          <div class="card-head"><div class="card-title">分类</div>
            <button class="btn sm ghost" id="add-cat">+ 新建</button></div>
          <div class="card-body" style="padding-top:8px">
            ${parents.map((p) => `
              <div class="cat-row">
                <div class="icon" style="background:${hexA(p.color, .13)}">${p.icon || '🧾'}</div>
                <div><div class="name">${esc(p.name)}</div>
                  <div class="sub">${p.used} 笔</div></div>
                <div class="right">
                  <button class="icon-btn" data-rename-cat="${p.id}">✎</button>
                  ${p.name !== '收入' ? `<button class="icon-btn" data-del-cat="${p.id}">🗑</button>` : ''}
                </div>
              </div>
              ${cats.items.filter((c) => c.parent_id === p.id).length ? `<div class="cat-children">
                ${cats.items.filter((c) => c.parent_id === p.id).map((c) => `<span class="cat-child">${esc(c.name)}</span>`).join('')}
              </div>` : ''}
            `).join('')}
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">账户</div>
            <button class="btn sm ghost" id="add-acc">+ 新建</button></div>
          <div class="card-body" style="padding-top:8px">
            ${accs.items.map((a) => `
              <div class="cat-row">
                <div class="icon" style="background:${hexA(a.color, .13)}">${a.kind === 'cash' ? '💵' : a.kind === 'credit' ? '💳' : a.kind === 'wechat' ? '💬' : a.kind === 'alipay' ? '🔷' : '🏦'}</div>
                <div><div class="name">${esc(a.name)}</div><div class="sub">${a.kind === 'wechat' ? '微信' : a.kind === 'alipay' ? '支付宝' : a.kind === 'credit' ? '信用卡' : a.kind === 'cash' ? '现金' : '银行 / 其他'} · ${a.used} 笔</div></div>
                <div class="right">
                  <button class="icon-btn" data-rename-acc="${a.id}">✎</button>
                  <button class="icon-btn" data-del-acc="${a.id}">🗑</button>
                </div>
              </div>`).join('')}
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">数据</div></div>
          <div class="card-body">
            <div class="kv"><span class="k">账目</span><span class="v">${info.counts.entries} 笔</span></div>
            <div class="kv"><span class="k">数据库大小</span><span class="v">${(info.db_size / 1024).toFixed(0)} KB</span></div>
            <div class="kv"><span class="k">存储路径</span><span class="v mono" style="max-width:60%;word-break:break-all;text-align:right">${esc(info.data_path)}</span></div>
            <div style="display:flex;gap:8px;margin-top:14px">
              <button class="btn" id="export-csv">导出 CSV</button>
              <button class="btn" id="backup-db">备份数据库</button>
              <button class="btn danger" id="reset-data">清空全部数据</button>
            </div>
            <div id="backup-result" style="font-size:11px;color:var(--muted);margin-top:10px;line-height:1.6">
              所有数据只保存在本机这个文件里，不上传、不联网。CSV 只导出账目明细；完整备份用「备份数据库」。
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">报销规则</div>
            <button class="btn sm ghost" id="reset-rules">恢复默认</button></div>
          <div class="card-body">
            <div style="font-size:12px;color:var(--muted);line-height:1.75">
              导入或手动记账时，备注 / 分类命中以下规则的支出默认标为<b style="color:var(--ink)">报销</b>，不计入日常消费：
            </div>
            <div class="rule-block">
              <div class="rule-label">关键词</div>
              <div class="rule-chips">${(wr.keywords || []).map((w) => ruleChip(w, 'work', 'work-keywords', w)).join('') || '<span class="rule-empty">还没有关键词</span>'}</div>
              <div class="rule-add">
                <input type="text" id="rule-work-kw" placeholder="添加关键词，如：过路费">
                <button class="btn sm" data-rule-add="work-keywords">添加</button>
              </div>
            </div>
            <div class="rule-block">
              <div class="rule-label">排除词（备注命中就不算报销）</div>
              <div class="rule-chips">${(wr.exclude || []).map((w) => ruleChip(w, 'muted', 'work-exclude', w)).join('') || '<span class="rule-empty">没有排除词</span>'}</div>
              <div class="rule-add">
                <input type="text" id="rule-work-ex" placeholder="添加排除词，如：地铁">
                <button class="btn sm" data-rule-add="work-exclude">添加</button>
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">固定支出</div></div>
          <div class="card-body">
            <div style="font-size:12px;color:var(--muted);line-height:1.75">
              房租、学费这类几个月交一次的大额支出，可以标为<b style="color:var(--ink)">不计入日常消费</b>：
              仍然计入总支出和净资产，但不影响日常消费、统计和预算。
            </div>
            <div class="rule-block">
              <div class="rule-label">关键词</div>
              <div class="rule-chips">${(fr.keywords || []).map((w) => ruleChip(w, 'personal', 'fixed-keywords', w)).join('') || '<span class="rule-empty">还没有关键词</span>'}</div>
              <div class="rule-add">
                <input type="text" id="rule-fixed-kw" placeholder="添加关键词，如：物业费">
                <button class="btn sm" data-rule-add="fixed-keywords">添加</button>
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">关于</div></div>
          <div class="card-body">
            <div style="font-size:12.5px;line-height:1.8;color:var(--muted)">
              <b style="color:var(--ink)">糊涂账</b> 是一个本地记账小工具，核心只有一件事：
              把报销账目从日常消费里摘出去，让“这个月到底花了多少”不再被报销污染。
              <br><br>
              版本 ${info.version} · 数据格式 v1
            </div>
          </div>
        </div>

        <div class="card" style="grid-column:1/-1">
          <div class="card-body" style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">
            <div style="flex:1;min-width:260px;font-size:12px;color:var(--muted);line-height:1.75">
              <b style="color:var(--ink)">规则修改默认只影响之后导入和新记的账目</b>，已经记下的不会自动变化。
              需要让历史账目也按当前规则统一时，应用一次即可；只处理「日常」支出，已报销的不会动。
            </div>
            <button class="btn" id="apply-all-rules">应用到全部历史账目</button>
          </div>
        </div>

      </div>`;

    $('#add-cat').onclick = async () => {
      const name = await uiPrompt('分类名称', '', { title: '新建分类' });
      if (!name) return;
      await post('/categories', { name });
      toast('已添加'); renderSettings();
    };
    $('#add-acc').onclick = () => openAccountModal();
    el.querySelectorAll('[data-rename-cat]').forEach((b) => {
      b.onclick = async () => {
        const c = cats.items.find((x) => x.id === Number(b.dataset.renameCat));
        const name = await uiPrompt('新的分类名称', c.name, { title: '重命名分类' });
        if (!name) return;
        await patch('/categories/' + c.id, { name });
        renderSettings();
      };
    });
    el.querySelectorAll('[data-del-cat]').forEach((b) => {
      b.onclick = async () => {
        if (!(await uiConfirm('删除这个分类？已使用的账目会归入「未分类」。', { danger: true, okText: '删除' }))) return;
        await del('/categories/' + b.dataset.delCat);
        renderSettings();
      };
    });
    el.querySelectorAll('[data-rename-acc]').forEach((b) => {
      b.onclick = async () => {
        const a = accs.items.find((x) => x.id === Number(b.dataset.renameAcc));
        const name = await uiPrompt('新的账户名称', a.name, { title: '重命名账户' });
        if (!name) return;
        await patch('/accounts/' + a.id, { name });
        renderSettings();
      };
    });
    el.querySelectorAll('[data-del-acc]').forEach((b) => {
      b.onclick = async () => {
        if (!(await uiConfirm('删除这个账户？已使用的账目会变成「未指定」。', { danger: true, okText: '删除' }))) return;
        await del('/accounts/' + b.dataset.delAcc);
        renderSettings();
      };
    });
    const saveRuleConfig = async (cfg) => {
      try {
        await put('/rules', cfg);
        await reloadMeta();
        renderSettings();
        toast('规则已更新');
      } catch (e) { toast(e.message); }
    };
    const cloneRules = () => JSON.parse(JSON.stringify(state.meta.rules));
    $('#reset-rules').onclick = async () => {
      if (!(await uiConfirm('恢复默认规则？你添加的关键词会丢失。', { danger: true, okText: '恢复默认' }))) return;
      await post('/rules/reset', {});
      await reloadMeta();
      renderSettings();
      toast('已恢复默认规则');
    };
    el.querySelectorAll('[data-rule-remove]').forEach((b) => {
      b.onclick = () => {
        const cfg = cloneRules();
        const section = b.dataset.ruleRemove;
        const value = b.dataset.value;
        if (section === 'work-keywords') cfg.work.keywords = (cfg.work.keywords || []).filter((x) => x !== value);
        else if (section === 'work-exclude') cfg.work.exclude = (cfg.work.exclude || []).filter((x) => x !== value);
        else if (section === 'fixed-keywords') cfg.fixed.keywords = (cfg.fixed.keywords || []).filter((x) => x !== value);
        saveRuleConfig(cfg);
      };
    });
    el.querySelectorAll('[data-rule-add]').forEach((b) => {
      b.onclick = () => {
        const type = b.dataset.ruleAdd;
        const cfg = cloneRules();
        if (type === 'work-keywords' || type === 'work-exclude' || type === 'fixed-keywords') {
          const input = el.querySelector(type === 'work-keywords' ? '#rule-work-kw'
            : type === 'work-exclude' ? '#rule-work-ex' : '#rule-fixed-kw');
          const value = input.value.trim();
          if (!value) { toast('先填一个词'); return; }
          const list = type === 'work-keywords' ? cfg.work.keywords
            : type === 'work-exclude' ? cfg.work.exclude : cfg.fixed.keywords;
          if (list.includes(value)) { toast('已经在规则里了'); return; }
          list.push(value);
        }
        saveRuleConfig(cfg);
      };
    });

    $('#apply-all-rules').onclick = async () => {
      try {
        const [wd, fd] = await Promise.all([
          post('/entries/apply-work-rules', { dry_run: true }),
          post('/entries/apply-daily-rules', { dry_run: true }),
        ]);
        if (!wd.matched && !fd.matched) { toast('没有匹配当前规则的账目'); return; }
        const msg = `把当前规则应用到全部历史账目？\n\n报销：${wd.matched} 笔\n固定支出：${fd.matched} 笔\n\n只处理「日常」支出，已报销的不会动。`;
        if (!(await uiConfirm(msg, { okText: '应用' }))) return;
        await post('/entries/apply-work-rules', { dry_run: false });
        await post('/entries/apply-daily-rules', { dry_run: false });
        toast(`已应用：报销 ${wd.matched} 笔，固定支出 ${fd.matched} 笔`);
        await reloadMeta();
        renderSettings();
      } catch (e) { toast(e.message); }
    };

    $('#export-csv').onclick = () => openExportModal();
    $('#backup-db').onclick = async () => {
      try {
        const res = await post('/backup', {});
        const box = $('#backup-result');
        box.innerHTML = `已备份 ${(res.size / 1024).toFixed(0)} KB
          <button class="btn sm" id="backup-reveal" style="margin-left:8px">打开所在文件夹</button>`;
        box.querySelector('#backup-reveal').onclick = async () => {
          try { await post('/export/reveal', { path: res.path }); }
          catch (e) { toast(e.message); }
        };
      } catch (e) { toast(e.message); }
    };

    $('#reset-data').onclick = async () => {
      if (!(await uiConfirm('这会删除全部账目，且无法撤销。确定吗？', { danger: true, okText: '继续' }))) return;
      if (!(await uiConfirm('再确认一次：真的清空？', { danger: true, okText: '清空' }))) return;
      await post('/settings/reset', {});
      toast('已清空');
      await reloadMeta();
      renderSettings();
    };
  }

  /* ------------------------------------------------------------------ modal plumbing */

  let modalCloseHandler = null;

  function openModal(inner, onClose) {
    const root = $('#modal-root');
    root.innerHTML = `<div class="modal-backdrop"><div class="modal">${inner}</div></div>`;
    modalCloseHandler = onClose || null;
    const backdrop = root.querySelector('.modal-backdrop');
    backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) closeModal(null); });
    root.querySelectorAll('[data-close]').forEach((b) => { b.onclick = () => closeModal(null); });
    return root.querySelector('.modal');
  }

  function closeModal(value = null) {
    const cb = modalCloseHandler;
    modalCloseHandler = null;
    $('#modal-root').innerHTML = '';
    if (cb) cb(value);
  }

  let dialogState = null;

  function closeDialog(value = null) {
    if (!dialogState) return false;
    const state = dialogState;
    dialogState = null;
    state.root.innerHTML = '';
    state.resolve(value);
    return true;
  }

  function openDialog(inner) {
    const root = $('#dialog-root');
    root.innerHTML = `<div class="modal-backdrop dialog-backdrop"><div class="modal dialog">${inner}</div></div>`;
    return root.querySelector('.modal');
  }

  function uiConfirm(message, opts = {}) {
    return new Promise((resolve) => {
      const m = openDialog(`
        <div class="modal-head"><h3>${esc(opts.title || '确认')}</h3>
          <button class="icon-btn" data-act="cancel">✕</button></div>
        <div class="modal-body">
          <div class="dialog-text">${esc(message)}</div>
        </div>
        <div class="modal-foot">
          <button class="btn" data-act="cancel">取消</button>
          <button class="btn ${opts.danger ? 'danger' : 'primary'}" data-act="ok">${esc(opts.okText || '确定')}</button>
        </div>`);
      const root = $('#dialog-root');
      dialogState = { root, resolve: (v) => resolve(v === true) };
      root.querySelector('.dialog-backdrop').addEventListener('mousedown', (e) => {
        if (e.target === e.currentTarget) closeDialog(false);
      });
      m.querySelectorAll('[data-act="cancel"]').forEach((b) => { b.onclick = () => closeDialog(false); });
      m.querySelector('[data-act="ok"]').onclick = () => closeDialog(true);
    });
  }

  function uiPrompt(message, defaultValue = '', opts = {}) {
    return new Promise((resolve) => {
      const m = openDialog(`
        <div class="modal-head"><h3>${esc(opts.title || '请输入')}</h3>
          <button class="icon-btn" data-act="cancel">✕</button></div>
        <div class="modal-body">
          <div class="dialog-text">${esc(message)}</div>
          <div class="field"><input type="text" id="dlg-input" value="${esc(defaultValue)}"></div>
        </div>
        <div class="modal-foot">
          <button class="btn" data-act="cancel">取消</button>
          <button class="btn primary" data-act="ok">${esc(opts.okText || '确定')}</button>
        </div>`);
      const root = $('#dialog-root');
      const input = m.querySelector('#dlg-input');
      dialogState = {
        root,
        resolve: (v) => resolve(v === true && input ? input.value.trim() : null),
      };
      root.querySelector('.dialog-backdrop').addEventListener('mousedown', (e) => {
        if (e.target === e.currentTarget) closeDialog(false);
      });
      setTimeout(() => { input.focus(); input.select(); }, 50);
      m.querySelectorAll('[data-act="cancel"]').forEach((b) => { b.onclick = () => closeDialog(false); });
      m.querySelector('[data-act="ok"]').onclick = () => closeDialog(true);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') closeDialog(true); });
    });
  }

  /* ------------------------------------------------------------------ reload */

  async function reloadMeta() {
    state.meta = await get('/meta');
    setSideCount();
  }

  async function reloadAll() {
    await reloadMeta();
    if (state.view === 'overview') await renderOverview();
    else if (state.view === 'stats') await renderStats();
    else if (state.view === 'ledger') await loadLedger(true);
    else if (state.view === 'assets') await renderAssets();
    else if (state.view === 'budget') await renderBudget();
    else if (state.view === 'settings') await renderSettings();
  }

  boot();
})();
