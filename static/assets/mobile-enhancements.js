(() => {
  'use strict';

  const MOBILE_QUERY = '(max-width: 640px)';
  const state = {
    stats: null,
    friends: null,
    statsPromise: null,
    friendsPromise: null,
    latency: null,
    latencyPromise: null,
    latencyPollTimer: null,
    toastTimer: null,
    credentialHistoryExpanded: false,
  };

  const textOf = (node) => (node?.textContent || '').replace(/\s+/g, ' ').trim();

  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const currentTitle = () => textOf(document.querySelector('main .page-head h2'));

  const showToast = (message) => {
    let toast = document.querySelector('.mobile-copy-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'mobile-copy-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('is-visible');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 1800);
  };

  const copyText = async (value, label) => {
    if (!value) {
      showToast('订阅链接暂不可用');
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const area = document.createElement('textarea');
      area.value = value;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    showToast(`${label}订阅链接已复制`);
  };

  const jsonFetch = async (url) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };

  const loadStats = async () => {
    if (state.stats) return state.stats;
    if (!state.statsPromise) {
      state.statsPromise = jsonFetch('/api/admin/stats')
        .then((data) => { state.stats = data; return data; })
        .catch(() => null)
        .finally(() => { state.statsPromise = null; });
    }
    return state.statsPromise;
  };

  const loadFriends = async () => {
    if (state.friends) return state.friends;
    if (!state.friendsPromise) {
      state.friendsPromise = jsonFetch('/api/admin/friends')
        .then((data) => {
          state.friends = new Map((Array.isArray(data) ? data : []).map((row) => [String(row.uid), row]));
          return state.friends;
        })
        .catch(() => new Map())
        .finally(() => { state.friendsPromise = null; });
    }
    return state.friendsPromise;
  };

  const renderActivity = (rows) => {
    const list = document.querySelector('.mobile-activity-list');
    if (!list) return;
    const normalized = (Array.isArray(rows) ? rows : [])
      .map((row) => ({ uid: String(row?.uid ?? ''), fetches: Number(row?.fetches ?? 0) }))
      .filter((row) => row.uid)
      .sort((a, b) => b.fetches - a.fetches || a.uid.localeCompare(b.uid));
    const max = Math.max(1, ...normalized.map((row) => row.fetches));
    list.replaceChildren();
    if (!normalized.length) {
      const empty = document.createElement('p');
      empty.className = 'panel-note';
      empty.textContent = '暂无本周订阅拉取记录';
      list.appendChild(empty);
      return;
    }
    normalized.forEach((row) => {
      const item = document.createElement('div');
      item.className = 'mobile-activity-row';
      const head = document.createElement('div');
      head.className = 'mobile-activity-row-head';
      const label = document.createElement('span');
      label.className = 'mobile-activity-label';
      label.textContent = row.uid;
      const value = document.createElement('strong');
      value.className = 'mobile-activity-value';
      value.textContent = `${row.fetches} 次`;
      head.append(label, value);
      const bar = document.createElement('div');
      bar.className = 'mobile-activity-bar';
      const fill = document.createElement('i');
      fill.style.width = `${Math.max(3, Math.round((row.fetches / max) * 100))}%`;
      bar.appendChild(fill);
      item.append(head, bar);
      list.appendChild(item);
    });
  };

  const enhanceActivity = () => {
    const panel = Array.from(document.querySelectorAll('main article.panel'))
      .find((article) => textOf(article.querySelector('.panel-head')).includes('用户拉取活跃度'));
    if (!panel) return;
    panel.classList.add('mobile-activity-panel');
    let list = panel.querySelector('.mobile-activity-list');
    if (!list) {
      list = document.createElement('div');
      list.className = 'mobile-activity-list';
      panel.querySelector('.chart')?.after(list);
    }
    if (state.stats?.per_friend_week) renderActivity(state.stats.per_friend_week);
    else loadStats().then((data) => renderActivity(data?.per_friend_week || []));
  };

  const latencyPanel = () => Array.from(document.querySelectorAll('main article.panel'))
    .find((article) => textOf(article.querySelector('.panel-head')).includes('延迟探针'));

  const latencyValue = (result, fallback = '未测试') => {
    if (!result) return fallback;
    if (result.state === 'ok') return result.ms == null ? '可达' : `${result.ms} ms`;
    if (result.state === 'unsupported') return '无';
    if (result.state === 'pending') return '探测中';
    return result.value || '不可达';
  };

  const latencyClass = (result) => {
    if (!result) return 'pending';
    if (result.state === 'ok') return 'ok';
    if (result.state === 'unsupported') return 'muted';
    if (result.state === 'pending') return 'pending';
    return 'bad';
  };

  const summaryValue = (summary, okKey, avgKey, running) => {
    if (running) return '探测中…';
    const total = Number(summary?.nodes_total || 0);
    const ok = Number(summary?.[okKey] || 0);
    if (!total) return '未测试';
    const avg = Number(summary?.[avgKey]);
    return `${ok}/${total} 可达${Number.isFinite(avg) ? ` · ${avg} ms` : ''}`;
  };

  const setLatencyRow = (row, label, value, stateName) => {
    if (!row) return;
    const labelNode = row.querySelector(':scope > span');
    const valueNode = row.querySelector(':scope > strong');
    if (labelNode && labelNode.textContent !== label) labelNode.textContent = label;
    if (valueNode) {
      if (valueNode.textContent !== value) valueNode.textContent = value;
      valueNode.className = `state-text ${stateName}`;
    }
  };

  const renderLatencyDetails = (panel, status) => {
    let toggle = panel.querySelector('.latency-details-toggle');
    let details = panel.querySelector('.latency-node-details');
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'latency-details-toggle';
      toggle.dataset.latencyDetails = 'toggle';
      panel.querySelector('.latency-list')?.after(toggle);
    }
    if (!details) {
      details = document.createElement('div');
      details.className = 'latency-node-details';
      details.hidden = true;
      toggle.after(details);
    }
    const nodes = Array.isArray(status?.nodes) ? status.nodes : [];
    const label = nodes.length ? `查看 ${nodes.length} 个节点探测结果` : '查看节点探测结果';
    if (toggle.textContent !== label) toggle.textContent = label;
    const signature = nodes.map((node) => [
      node.node_id,
      node.name,
      node.protocol,
      node.entry?.state,
      node.entry?.value,
      node.proxy?.state,
      node.proxy?.value,
    ].map((value) => String(value ?? '')).join(':')).join('|');
    if (details.dataset.signature === signature) return;
    details.dataset.signature = signature;
    details.replaceChildren();
    if (!nodes.length) return;
    const list = document.createElement('div');
    list.className = 'latency-node-list';
    nodes.forEach((node) => {
      const item = document.createElement('div');
      item.className = 'latency-node-item';
      const name = document.createElement('strong');
      name.textContent = node.name || `节点 ${node.node_id}`;
      const meta = document.createElement('span');
      meta.textContent = `${String(node.protocol || '').toUpperCase()} · 入口 ${latencyValue(node.entry, '—')} · 出口 ${latencyValue(node.proxy, '—')}`;
      item.append(name, meta);
      list.appendChild(item);
    });
    details.appendChild(list);
  };

  const renderLatency = (status) => {
    const panel = latencyPanel();
    if (!panel) return;
    panel.classList.add('latency-enhanced');
    const rows = panel.querySelectorAll('.latency-list .latency-row');
    const running = status?.status === 'running';
    const summary = status?.summary || {};
    const control = status?.control || null;
    setLatencyRow(rows[0], '控制面', running ? '探测中…' : latencyValue(control), latencyClass(control));
    setLatencyRow(rows[1], '节点入口', summaryValue(summary, 'entry_ok', 'entry_avg_ms', running), running ? 'pending' : summary.entry_ok ? 'ok' : 'bad');
    setLatencyRow(rows[2], '代理出口', summaryValue(summary, 'proxy_ok', 'proxy_avg_ms', running), running ? 'pending' : summary.proxy_ok ? 'ok' : 'bad');
    const note = panel.querySelector('.panel-note');
    const targetHost = status?.target?.url ? (() => { try { return new URL(status.target.url).hostname; } catch { return '公共目标'; } })() : '公共目标';
    const noteText = running
      ? '正在从美国 VPS 逐节点验证入口和真实代理出口，请稍候。'
      : `入口为节点端口握手，出口为通过节点访问 ${targetHost}；不使用普通网页延迟冒充代理延迟。`;
    if (note && note.textContent !== noteText) note.textContent = noteText;
    renderLatencyDetails(panel, status || {});
  };

  const loadLatency = async () => {
    if (state.latency) { renderLatency(state.latency); return state.latency; }
    if (!state.latencyPromise) {
      state.latencyPromise = jsonFetch(`/api/admin/latency?ts=${Date.now()}`)
        .then((data) => { state.latency = data; renderLatency(data); return data; })
        .catch(() => {
          state.latency = { status: 'error', summary: {}, control: { state: 'bad', value: '不可用' }, nodes: [] };
          renderLatency(state.latency);
          return state.latency;
        })
        .finally(() => { state.latencyPromise = null; });
    }
    return state.latencyPromise;
  };

  const pollLatency = (attempt = 0) => {
    clearTimeout(state.latencyPollTimer);
    if (attempt >= 60) return;
    state.latencyPollTimer = setTimeout(async () => {
      try {
        const data = await jsonFetch(`/api/admin/latency?ts=${Date.now()}`);
        state.latency = data;
        renderLatency(data);
        if (data.status === 'running') pollLatency(attempt + 1);
      } catch { /* keep the running state visible until the next manual probe */ }
    }, 1000);
  };

  const triggerLatency = async () => {
    state.latency = { status: 'running', summary: {}, control: { state: 'pending' }, nodes: [] };
    renderLatency(state.latency);
    try {
      const response = await fetch('/api/admin/latency/probe', {
        method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error('probe failed');
      const data = await response.json();
      if (data.status !== 'running') { state.latency = data; renderLatency(data); }
      pollLatency();
      showToast(data.started === false ? '探测任务正在运行' : '已开始真实节点探测');
    } catch {
      state.latency = { status: 'error', summary: {}, control: { state: 'bad', value: '不可用' }, nodes: [] };
      renderLatency(state.latency);
      showToast('延迟探测启动失败');
    }
  };

  const enhanceLatency = () => {
    if (!latencyPanel()) return;
    loadLatency();
  };

  const setCellLabels = (table, labels) => {
    table.querySelectorAll('tbody tr').forEach((row) => {
      row.querySelectorAll(':scope > td').forEach((cell, index) => {
        cell.dataset.label = labels[index] || '信息';
      });
    });
  };

  const addCopyButtons = (table) => {
    table.querySelectorAll('tbody tr').forEach((row) => {
      const uid = textOf(row.querySelector(':scope > td:first-child strong'));
      const actions = row.querySelector(':scope > td:last-child');
      if (!uid || !actions) return;
      let controls = actions.querySelector('.mobile-subscription-copy');
      if (!controls) {
        controls = document.createElement('span');
        controls.className = 'mobile-subscription-copy';
        ['clash', 'v2ray'].forEach((kind) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'button ghost compact';
          button.dataset.mobileCopy = kind;
          button.dataset.mobileCopyUid = uid;
          button.textContent = kind === 'clash' ? '复制 Clash' : '复制 V2Ray';
          controls.appendChild(button);
        });
        actions.appendChild(controls);
      }
      const friend = state.friends?.get(uid);
      controls.querySelectorAll('[data-mobile-copy]').forEach((button) => {
        const url = friend?.links?.[button.dataset.mobileCopy];
        button.disabled = !url;
        button.title = url ? `复制 ${button.dataset.mobileCopy === 'clash' ? 'Clash' : 'V2Ray'} 订阅` : '订阅链接暂不可用';
      });
    });
  };

  const enhanceTables = () => {
    const title = currentTitle();
    const table = document.querySelector('main .table-panel table');
    const panel = table?.closest('.table-panel');
    if (!table || !panel) return;
    if (title === '用户管理') {
      panel.classList.add('mobile-user-panel');
      setCellLabels(table, ['用户', '节点', '认证', '流量配额', '设备', '状态', '操作']);
      addCopyButtons(table);
      loadFriends().then(() => addCopyButtons(table));
    } else if (title === '设备审计') {
      panel.classList.add('mobile-device-panel');
      setCellLabels(table, ['用户', '备注', '来源', '客户端', '最近 IP', '拉取', '状态', '操作']);
    }
  };

  const enhanceCredentials = () => {
    // The subscriber editor is rendered in a modal outside <main>.
    const section = document.querySelector('#app .credential-section');
    if (!section) return;
    if (state.credentialSection !== section) {
      state.credentialSection = section;
      state.credentialHistoryExpanded = false;
    }
    section.classList.add('credential-section-enhanced');
    const rows = Array.from(section.querySelectorAll(':scope > .credential-row'));
    if (!rows.length) return;

    rows.forEach((row) => {
      const protocol = row.querySelector(':scope > .table-muted:not(.error-ellipsis)');
      if (protocol) {
        const normalized = protocol.textContent.trim().toLowerCase();
        const compact = normalized === 'hysteria2' ? 'HY2' : normalized === 'vless' ? 'VLESS' : normalized.toUpperCase();
        if (protocol.textContent !== compact) protocol.textContent = compact;
      }
    });

    const historyRows = rows.filter((row) => {
      const badge = row.querySelector(':scope > .status-badge');
      return badge?.classList.contains('muted') || textOf(badge) === '已撤销';
    });
    let toggle = section.querySelector(':scope > .credential-history-toggle');
    if (!historyRows.length) {
      toggle?.remove();
      return;
    }
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'credential-history-toggle';
      toggle.dataset.credentialHistoryToggle = 'true';
      section.querySelector(':scope > .section-head')?.after(toggle);
    }
    const expanded = state.credentialHistoryExpanded;
    historyRows.forEach((row) => {
      row.classList.add('credential-history-row');
      row.hidden = !expanded;
    });
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.textContent = expanded ? `收起 ${historyRows.length} 条旧记录` : `显示 ${historyRows.length} 条旧记录`;
  };

  const enhance = () => {
    enhanceActivity();
    enhanceLatency();
    enhanceTables();
    enhanceCredentials();
  };

  document.addEventListener('click', (event) => {
    const latencyButton = event.target.closest('.latency-panel .icon-button');
    if (latencyButton) {
      event.preventDefault();
      event.stopImmediatePropagation();
      triggerLatency();
      return;
    }
    const detailsToggle = event.target.closest('[data-latency-details="toggle"]');
    if (detailsToggle) {
      event.preventDefault();
      const details = detailsToggle.parentElement?.querySelector('.latency-node-details');
      if (details) {
        details.hidden = !details.hidden;
        detailsToggle.setAttribute('aria-expanded', String(!details.hidden));
      }
      return;
    }
    const credentialToggle = event.target.closest('[data-credential-history-toggle="true"]');
    if (credentialToggle) {
      event.preventDefault();
      state.credentialHistoryExpanded = credentialToggle.getAttribute('aria-expanded') !== 'true';
      enhanceCredentials();
      return;
    }
    const button = event.target.closest('[data-mobile-copy]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    const friend = state.friends?.get(button.dataset.mobileCopyUid);
    copyText(friend?.links?.[button.dataset.mobileCopy], button.dataset.mobileCopy === 'clash' ? 'Clash' : 'V2Ray');
  }, true);

  const observer = new MutationObserver(() => {
    clearTimeout(observer.timer);
    observer.timer = setTimeout(enhance, 60);
  });

  const boot = () => {
    enhance();
    observer.observe(document.querySelector('#app') || document.body, { childList: true, subtree: true });
    if (window.matchMedia) {
      window.matchMedia(MOBILE_QUERY).addEventListener?.('change', enhance);
    }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();


/* Compact, expandable node allocation for the subscriber editor. */
(() => {
  'use strict';

  const state = { section: null, open: false, timer: null };

  const textOf = (node) => (node?.textContent || '').replace(/\s+/g, ' ').trim();

  const schedule = () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(refresh, 40);
  };

  const refresh = () => {
    const section = Array.from(document.querySelectorAll('#app .form-section'))
      .find((node) => textOf(node.querySelector(':scope > .section-head')).startsWith('分配节点'));
    if (!section) {
      state.section = null;
      return;
    }

    const head = section.querySelector(':scope > .section-head');
    const list = section.querySelector(':scope > .node-check-list');
    if (!head || !list) return;

    section.classList.add('node-allocation-section');
    if (state.section !== section) {
      state.section = section;
      state.open = false;
    }

    let toggle = section.querySelector(':scope > [data-node-allocation-toggle]');
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'node-allocation-toggle';
      toggle.dataset.nodeAllocationToggle = 'true';
      toggle.setAttribute('aria-label', '展开或收起节点选择');
      const summary = document.createElement('span');
      summary.className = 'node-allocation-summary';
      const chevron = document.createElement('span');
      chevron.className = 'node-allocation-chevron';
      chevron.setAttribute('aria-hidden', 'true');
      chevron.textContent = '⌄';
      toggle.append(summary, chevron);
      list.before(toggle);
      if (!list.id) list.id = 'node-allocation-list-' + Math.random().toString(36).slice(2);
      toggle.setAttribute('aria-controls', list.id);
    }

    const rows = Array.from(list.querySelectorAll(':scope > label'));
    const selected = rows.filter((row) => row.querySelector('input')?.checked);
    const names = selected
      .map((row) => textOf(row.querySelector(':scope > span:nth-of-type(1)')))
      .filter(Boolean);
    const summary = toggle.querySelector('.node-allocation-summary');
    if (summary) {
      const preview = names.slice(0, 2).join('、');
      const suffix = names.length > 2 ? ' +' + (names.length - 2) : '';
      summary.textContent = selected.length
        ? selected.length + ' 个节点' + (preview ? ' · ' + preview + suffix : '')
        : '未选择节点';
    }

    toggle.setAttribute('aria-expanded', String(state.open));
    toggle.classList.toggle('is-open', state.open);
    list.hidden = !state.open;
    const allButton = head.querySelector(':scope > .text-link');
    if (allButton) allButton.hidden = !state.open;
  };

  document.addEventListener('click', (event) => {
    const toggle = event.target.closest('[data-node-allocation-toggle="true"]');
    if (!toggle) return;
    event.preventDefault();
    state.open = !state.open;
    refresh();
  }, true);

  document.addEventListener('change', (event) => {
    if (event.target.closest('.node-check-list input')) schedule();
  }, true);

  const observer = new MutationObserver(schedule);
  const boot = () => {
    refresh();
    observer.observe(document.querySelector('#app') || document.body, { childList: true, subtree: true });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();


/* Deterministic, font-independent product mark for login/sidebar/mobile header. */
(() => {
  'use strict';

  const SELECTOR = '#app .brand-mark, #app .mobile-brand';
  const SVG = `
    <svg class="brand-mark-glyph" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <path d="M11 14.5 20 9l9 5.5-9 5.5-9-5.5Z M11 20l9 5.5 9-5.5 M11 25.5 20 31l9-5.5"
        fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="20" cy="20" r="2.2" fill="currentColor" />
    </svg>`;

  const apply = () => {
    document.querySelectorAll(SELECTOR).forEach((mark) => {
      if (mark.dataset.brandReady === 'true' && mark.querySelector('.brand-mark-glyph')) return;
      mark.replaceChildren();
      mark.insertAdjacentHTML('afterbegin', SVG);
      mark.dataset.brandReady = 'true';
      mark.setAttribute('aria-hidden', 'true');
    });
  };

  let frame = 0;
  const schedule = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(apply);
  };
  const observer = new MutationObserver(schedule);
  const boot = () => {
    apply();
    observer.observe(document.querySelector('#app') || document.body, { childList: true, subtree: true });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
