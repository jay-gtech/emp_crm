/**
 * Employee CRM — UX Intelligence Layer
 * =====================================
 * Progressive enhancement only — every feature degrades gracefully.
 * This file NEVER modifies backend behaviour.
 *
 * Features:
 *  1. Dark mode toggle (persisted to localStorage)
 *  2. Rich empty-state injection for all tables
 *  3. Form UX — inline validation, Enter-to-submit, loading states
 *  4. Flash-message → toast conversion
 *  5. Keyboard shortcuts (g d → Dashboard, g t → Tasks, etc.)
 *  6. Clickable stat cards
 *  7. Confirm-dialog UX polish (replace ugly browser confirm)
 *  8. Auto-dismiss flash alerts
 */

(function () {
  'use strict';

  /* ─── Utility ─────────────────────────────────────────────────────────── */
  const $ = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  /* ═══════════════════════════════════════════════════════════════════════
     1. DARK MODE TOGGLE
     ═══════════════════════════════════════════════════════════════════════ */
  function initDarkMode() {
    const PREF_KEY = 'crm-theme';
    const saved    = localStorage.getItem(PREF_KEY);
    if (saved === 'dark') document.documentElement.setAttribute('data-theme', 'dark');

    const toggle = document.getElementById('darkModeToggle');
    if (!toggle) return;

    // Reflect saved state
    toggle.checked = saved === 'dark';

    toggle.addEventListener('change', () => {
      const isDark = toggle.checked;
      document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
      localStorage.setItem(PREF_KEY, isDark ? 'dark' : 'light');
      if (window.showToast) {
        showToast(isDark ? '🌙 Dark mode on' : '☀️ Light mode on', 'info', 2000);
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     2. RICH EMPTY STATES
     ═══════════════════════════════════════════════════════════════════════ */
  const EMPTY_CONFIGS = {
    // Keyed by a substring of the page path or table id
    tasks: {
      icon: '✅',
      title: 'No tasks yet',
      body: 'Tasks assigned to you will appear here.',
      cta: { label: 'View Tasks', href: '/tasks/' },
    },
    leaves: {
      icon: '📅',
      title: 'No leave requests',
      body: 'You haven\'t submitted any leave requests yet.',
      cta: { label: 'Apply for Leave', href: '/leaves/' },
    },
    employees: {
      icon: '👥',
      title: 'No employees found',
      body: 'Try adjusting your search filters.',
      cta: null,
    },
    attendance: {
      icon: '🕐',
      title: 'No attendance records',
      body: 'Your attendance history will appear here once you clock in.',
      cta: { label: 'Go to Attendance', href: '/attendance/' },
    },
    default: {
      icon: '📭',
      title: 'Nothing here yet',
      body: 'Data will appear here once available.',
      cta: null,
    },
  };

  function buildEmptyState(cfg) {
    const wrap = document.createElement('tr');
    wrap.className = 'ux-empty-row';
    const td = document.createElement('td');
    td.colSpan = 99;
    td.innerHTML = `
      <div class="ux-empty">
        <div class="ux-empty-icon">${cfg.icon}</div>
        <div class="ux-empty-title">${cfg.title}</div>
        <div class="ux-empty-body">${cfg.body}</div>
        ${cfg.cta ? `<a href="${cfg.cta.href}" class="btn btn-outline btn-sm" style="margin-top:8px">${cfg.cta.label}</a>` : ''}
      </div>`;
    wrap.appendChild(td);
    return wrap;
  }

  function upgradeEmptyStates() {
    // Find all plain "no data" rows and replace with rich empty states
    $$('tbody tr').forEach(tr => {
      const td = tr.querySelector('td');
      if (!td) return;

      // Detect plain empty-state cells (colspan + blank-ish text)
      const txt = td.textContent.trim().toLowerCase();
      const isPlain = td.colSpan > 2 && (
        txt.includes('no ') || txt.includes('nothing') || txt === '—'
      );
      if (!isPlain) return;
      if (tr.classList.contains('ux-empty-row')) return; // already upgraded

      // Pick config from path or table id
      const path   = location.pathname;
      const tblId  = tr.closest('table')?.id || '';
      let key = 'default';
      if (path.includes('/tasks') || tblId.includes('task'))       key = 'tasks';
      else if (path.includes('/leaves'))                            key = 'leaves';
      else if (path.includes('/employees') || tblId.includes('emp')) key = 'employees';
      else if (path.includes('/attendance'))                        key = 'attendance';

      tr.replaceWith(buildEmptyState(EMPTY_CONFIGS[key]));
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     3. FORM UX — validation, loading states, Enter-to-submit
     ═══════════════════════════════════════════════════════════════════════ */
  function initFormUX() {
    // ── Enter-to-submit on text/date inputs (skip textarea) ──────────────
    $$('input[type=text], input[type=email], input[type=date], input[type=search]')
      .forEach(input => {
        input.addEventListener('keydown', e => {
          if (e.key !== 'Enter') return;
          const form = input.closest('form');
          if (!form) return;
          // Don't auto-submit forms that have a textarea (multi-step intent)
          if (form.querySelector('textarea')) return;
          e.preventDefault();
          // Find the primary submit button
          const btn = form.querySelector('[type=submit], button:not([type=button])');
          if (btn) btn.click();
        });
      });

    // ── Loading state on submit ───────────────────────────────────────────
    $$('form').forEach(form => {
      form.addEventListener('submit', () => {
        const btn = form.querySelector('[type=submit], button:not([type=button]):not(.btn-danger)');
        if (!btn || btn.disabled) return;
        const original = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="ux-spinner"></span> ' + (original.replace(/<[^>]+>/g, '').trim() || 'Processing…');
        // Restore after 8 s fallback (page usually reloads)
        setTimeout(() => {
          btn.disabled = false;
          btn.innerHTML = original;
        }, 8000);
      });
    });

    // ── Inline validation feedback ───────────────────────────────────────
    $$('.form-control[required]').forEach(input => {
      input.addEventListener('blur', () => validateField(input));
      input.addEventListener('input', () => {
        if (input.classList.contains('ux-invalid')) validateField(input);
      });
    });

    // Date range validation (start_date / end_date pairs)
    const startDate = $('[name=start_date]');
    const endDate   = $('[name=end_date]');
    if (startDate && endDate) {
      endDate.addEventListener('change', () => {
        if (startDate.value && endDate.value < startDate.value) {
          markInvalid(endDate, 'End date must be after start date');
        } else {
          markValid(endDate);
        }
      });
    }
  }

  function validateField(input) {
    if (!input.value.trim()) {
      markInvalid(input, input.placeholder ? `${input.placeholder} is required` : 'This field is required');
    } else {
      markValid(input);
    }
  }

  function markInvalid(input, msg) {
    input.classList.add('ux-invalid');
    input.classList.remove('ux-valid');
    let hint = input.nextElementSibling;
    if (!hint || !hint.classList.contains('ux-hint')) {
      hint = document.createElement('span');
      hint.className = 'ux-hint ux-hint-error';
      input.parentNode.insertBefore(hint, input.nextSibling);
    }
    hint.textContent = '⚠ ' + msg;
    hint.style.display = 'block';
  }

  function markValid(input) {
    input.classList.remove('ux-invalid');
    input.classList.add('ux-valid');
    const hint = input.nextElementSibling;
    if (hint && hint.classList.contains('ux-hint')) {
      hint.style.display = 'none';
    }
  }

  /* ═══════════════════════════════════════════════════════════════════════
     4. AUTO-DISMISS FLASH ALERTS & TOAST CONVERSION
     ═══════════════════════════════════════════════════════════════════════ */
  function initAlerts() {
    $$('.alert').forEach(alert => {
      // Map alert class to toast type
      let type = 'info';
      if (alert.classList.contains('alert-success')) type = 'success';
      else if (alert.classList.contains('alert-danger'))  type = 'danger';
      else if (alert.classList.contains('alert-warning')) type = 'warning';

      // Show as toast if showToast is available and alert is brief
      const text = alert.textContent.trim();
      if (window.showToast && text.length < 200) {
        showToast(text, type, 5000);
        // Optionally keep the inline alert for accessibility
      }

      // Auto-dismiss inline alert after 6 s
      setTimeout(() => {
        alert.style.transition = 'opacity .4s';
        alert.style.opacity    = '0';
        setTimeout(() => alert.remove(), 450);
      }, 6000);
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     5. KEYBOARD SHORTCUTS
     ═══════════════════════════════════════════════════════════════════════ */
  const SHORTCUTS = {
    'gd': '/dashboard/',
    'gt': '/tasks/',
    'gl': '/leaves/',
    'ga': '/attendance/',
    'ge': '/employees/',
    'gn': '/analytics/',
  };

  function initKeyboardShortcuts() {
    let buf = '';
    let timer;
    document.addEventListener('keydown', e => {
      // Ignore when user is typing in an input / textarea
      if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      buf += e.key.toLowerCase();
      clearTimeout(timer);
      timer = setTimeout(() => { buf = ''; }, 800);

      if (SHORTCUTS[buf]) {
        window.location.href = SHORTCUTS[buf];
        buf = '';
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     6. CLICKABLE STAT CARDS
     ═══════════════════════════════════════════════════════════════════════ */
  const CARD_LINKS = [
    { textMatch: /today.s attendance/i,      href: '/attendance/' },
    { textMatch: /leave days left/i,         href: '/leaves/' },
    { textMatch: /my tasks|assigned tasks/i, href: '/tasks/' },
    { textMatch: /pending tasks/i,           href: '/tasks/' },
    { textMatch: /total tasks/i,             href: '/tasks/' },
    { textMatch: /present today/i,           href: '/attendance/' },
  ];

  function initClickableCards() {
    $$('.stat-card').forEach(card => {
      if (card.dataset.linked) return;
      const label = card.querySelector('.stat-label')?.textContent || '';
      const match = CARD_LINKS.find(c => c.textMatch.test(label));
      if (!match) return;

      card.style.cursor = 'pointer';
      card.setAttribute('role', 'link');
      card.setAttribute('tabindex', '0');
      card.dataset.linked = '1';

      card.addEventListener('click', () => { window.location.href = match.href; });
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') window.location.href = match.href;
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     7. POLISHED CONFIRM DIALOGS
     ═══════════════════════════════════════════════════════════════════════ */
  function initConfirmDialogs() {
    // Event delegation: catch clicks on elements that have confirm in their onclick
    // Or prefer data-confirm attribute for new code.
    document.addEventListener('click', e => {
      const btn = e.target.closest('[onclick*="confirm("], [data-confirm]');
      if (!btn) return;

      const onclickVal = btn.getAttribute('onclick') || '';
      const match = onclickVal.match(/confirm\(['"](.+?)['"]\)/) || btn.dataset.confirm;
      const msg = match ? (typeof match === 'string' ? match : match[1]) : 'Are you sure?';

      // Only intercept if we haven't already handled it or if it's a raw confirm
      if (onclickVal.includes('return confirm(') || btn.dataset.confirm) {
        e.preventDefault();
        e.stopImmediatePropagation();
        
        // Remove the inline onclick if present to avoid dual triggers
        if (onclickVal) {
          btn.dataset.originalOnclick = onclickVal;
          btn.removeAttribute('onclick');
        }

        showConfirmModal(msg, () => {
          const form = btn.closest('form');
          if (form) {
            form.submit();
          } else if (btn.href) {
            window.location.href = btn.href;
          }
        });
      }
    }, true); // Capture phase to intercept before inline handlers if possible? 
    // Actually, inline onclick runs before addEventListener on the element, 
    // but delegation on document with capture might be tricky.
    // Let's stick to standard and assume we strip onclick where we find it.
  }

  function showConfirmModal(message, onConfirm) {
    // Remove any existing modal
    $('#ux-confirm-modal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'ux-confirm-modal';
    modal.className = 'ux-modal-backdrop';
    modal.innerHTML = `
      <div class="ux-modal-card" role="dialog" aria-modal="true">
        <div class="ux-modal-icon">❓</div>
        <div class="ux-modal-msg">${message}</div>
        <div class="ux-modal-actions">
          <button class="btn btn-outline" id="uxCancelBtn">Cancel</button>
          <button class="btn btn-primary" id="uxConfirmBtn">Confirm</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    requestAnimationFrame(() => modal.querySelector('.ux-modal-card').classList.add('ux-modal-in'));

    modal.querySelector('#uxConfirmBtn').addEventListener('click', () => {
      modal.remove();
      if (onConfirm) onConfirm();
    });
    modal.querySelector('#uxCancelBtn').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', e => {
      if (e.target === e.currentTarget) modal.remove();
    });

    // Focus confirm button for keyboard users
    setTimeout(() => {
      const btn = modal.querySelector('#uxConfirmBtn');
      if (btn) btn.focus();
    }, 50);
  }
  // Expose
  window.showConfirmModal = showConfirmModal;

  /* ═══════════════════════════════════════════════════════════════════════
     8. QUICK ACTION DOCK (injected into dashboard)
     ═══════════════════════════════════════════════════════════════════════ */
  function initQuickActions() {
    const isDashboard = location.pathname.startsWith('/dashboard');
    if (!isDashboard) return;

    // Only inject once
    if ($('#ux-quick-actions')) return;

    const actions = [
      { icon: '✅', label: 'New Task',    href: '/tasks/',      role: ['admin','manager','team_lead'] },
      { icon: '🕐', label: 'Clock In',    href: '/attendance/', role: ['admin','manager','team_lead','employee'] },
      { icon: '📅', label: 'Apply Leave', href: '/leaves/',     role: ['admin','manager','team_lead','employee'] },
      { icon: '📈', label: 'Analytics',   href: '/analytics/',  role: ['admin','manager','team_lead','employee'] },
    ];

    const dock = document.createElement('div');
    dock.id = 'ux-quick-actions';
    dock.className = 'ux-quick-dock';
    dock.innerHTML = `
      <div class="ux-quick-label">Quick Actions</div>
      <div class="ux-quick-btns">
        ${actions.map(a => `
          <a href="${a.href}" class="ux-quick-btn" title="${a.label}">
            <span class="ux-quick-icon">${a.icon}</span>
            <span class="ux-quick-text">${a.label}</span>
          </a>`).join('')}
      </div>`;

    // Insert before the first section of the dashboard
    const firstChild = $('.page-body')?.firstElementChild;
    if (firstChild) firstChild.parentNode.insertBefore(dock, firstChild);
  }

  /* ═══════════════════════════════════════════════════════════════════════
     9. SMOOTH SCROLL & FOCUS MANAGEMENT
     ═══════════════════════════════════════════════════════════════════════ */
  function initSmoothFocus() {
    // Auto-focus first input in visible forms (skip hidden/readonly)
    const firstInput = $('form .form-control:not([type=hidden]):not([readonly]):not([disabled])');
    if (firstInput && !location.search.includes('error')) {
      // Slight delay so animations complete first
      setTimeout(() => firstInput.focus(), 350);
    }
  }

  /* ═══════════════════════════════════════════════════════════════════════
     10. TOOLTIP ENHANCEMENT
     ═══════════════════════════════════════════════════════════════════════ */
  function initTooltips() {
    $$('[title]').forEach(el => {
      if (el.dataset.tooltipInit) return;
      el.dataset.tooltipInit = '1';
      const title = el.getAttribute('title');
      if (!title) return;
      
      // Convert browser title to custom CSS tooltip
      el.setAttribute('data-tooltip', title);
      el.removeAttribute('title');
      el.classList.add('ux-tooltip-trigger');
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     12. COPY TO CLIPBOARD FEEDBACK
     ═══════════════════════════════════════════════════════════════════════ */
  function initCopyFeedback() {
    // Find text that looks like IDs or emails, wrapping them to be clickable 
    // OR just rely on existing copy buttons if added. We will target table cells
    // that contain IDs (e.g., "#123")
    $$('td.text-muted').forEach(td => {
      const text = td.textContent.trim();
      if (text.startsWith('#') && text.length < 10 && !td.querySelector('a')) {
        td.style.cursor = 'pointer';
        td.setAttribute('data-tooltip', 'Click to copy');
        td.classList.add('ux-tooltip-trigger');
        td.addEventListener('click', (e) => {
          e.stopPropagation();
          navigator.clipboard.writeText(text).then(() => {
            if (window.showToast) showToast(`Copied ${text}`, 'success', 2000);
          }).catch(() => {});
        });
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     13. COMMAND PALETTE (CTRL+K)
     ═══════════════════════════════════════════════════════════════════════ */
  const PALETTE_LINKS = [
    { label: 'Dashboard', url: '/dashboard/', icon: '🏠' },
    { label: 'My Tasks', url: '/tasks/', icon: '✅' },
    { label: 'Leave Requests', url: '/leaves/', icon: '📅' },
    { label: 'Attendance', url: '/attendance/', icon: '🕐' },
    { label: 'Employees', url: '/employees/', icon: '👥' },
    { label: 'Analytics', url: '/analytics/', icon: '📈' },
    { label: 'Profile Settings', url: '#', icon: '⚙️' },
    { label: 'Logout', url: '/auth/logout', icon: '🚪' }
  ];

  function initCommandPalette() {
    document.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        showPalette();
      }
    });
  }

  function showPalette() {
    $('#ux-palette')?.remove();
    
    const modal = document.createElement('div');
    modal.id = 'ux-palette';
    modal.className = 'ux-palette-backdrop';
    modal.innerHTML = `
      <div class="ux-palette-box">
        <input type="text" id="ux-palette-input" placeholder="Search for pages..." autocomplete="off" />
        <div class="ux-palette-list" id="ux-palette-list"></div>
      </div>
    `;
    document.body.appendChild(modal);

    const input = modal.querySelector('#ux-palette-input');
    const list = modal.querySelector('#ux-palette-list');

    function render(query = '') {
      const q = query.toLowerCase();
      const filtered = PALETTE_LINKS.filter(l => l.label.toLowerCase().includes(q));
      if (!filtered.length) {
        list.innerHTML = `<div class="ux-palette-item" style="color:var(--gray-400)">No results found</div>`;
        return;
      }
      list.innerHTML = filtered.map((l, i) => `
        <a href="${l.url}" class="ux-palette-item ${i===0?'selected':''}" data-index="${i}">
          <span class="ux-palette-icon">${l.icon}</span>
          ${l.label}
        </a>
      `).join('');
    }

    render();
    setTimeout(() => input.focus(), 50);

    // Filter and navigate
    input.addEventListener('input', e => render(e.target.value));
    
    // Keyboard navigation
    input.addEventListener('keydown', e => {
      const items = $$('.ux-palette-item:not([style])', list);
      if (!items.length) return;
      
      let curr = items.findIndex(el => el.classList.contains('selected'));
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        items[curr]?.classList.remove('selected');
        curr = (curr + 1) % items.length;
        items[curr].classList.add('selected');
        items[curr].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        items[curr]?.classList.remove('selected');
        curr = (curr - 1 + items.length) % items.length;
        items[curr].classList.add('selected');
        items[curr].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (curr >= 0) window.location.href = items[curr].href;
      } else if (e.key === 'Escape') {
        e.preventDefault();
        modal.remove();
      }
    });

    // Close on backdrop click
    modal.addEventListener('click', e => {
      if (e.target === modal) modal.remove();
    });
  }
  // Expose
  window.showPalette = showPalette;

  /* ═══════════════════════════════════════════════════════════════════════
     14. PAGE LOAD TRANSITION
     ═══════════════════════════════════════════════════════════════════════ */
  function initPageTransitions() {
    // Add class to body to trigger CSS fade-in
    document.body.classList.add('ux-page-loaded');
  }

  /* ═══════════════════════════════════════════════════════════════════════
     11. TABLE ROW HOVER — show action buttons on hover for cleaner look
     ═══════════════════════════════════════════════════════════════════════ */
  function initTableHoverActions() {
    $$('tbody tr').forEach(tr => {
      if (tr.classList.contains('ux-empty-row')) return;
      const actions = tr.querySelector('td:last-child .btn, td:last-child form');
      if (!actions) return;
      tr.classList.add('ux-has-actions');
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════════ */
  ready(() => {
    try {
      // ── Page Level Control ──────────────────────────────────────────
      const path = document.body.dataset.page || '';
      const isAuthPage = path.includes('/auth/login');

      // ── Core Features (Always Run) ──────────────────────────────────
      try { initDarkMode(); } catch(e) { console.warn('darkMode:', e); }
      try { requestAnimationFrame(initPageTransitions); } catch(e) {}

      // ── App Features (Skip on Login) ────────────────────────────────
      if (isAuthPage) {
        console.log('Skipping app features on login page.');
        // Still run form UX for the login form if present
        try { initFormUX(); } catch(e) {}
        document.documentElement.dataset.uxReady = "true";
        return;
      }

      try { upgradeEmptyStates(); }    catch(e) { console.warn('emptyStates:', e); }
      try { initFormUX(); }            catch(e) { console.warn('formUX:', e); }
      try { initAlerts(); }            catch(e) { console.warn('alerts:', e); }
      try { initKeyboardShortcuts(); } catch(e) { console.warn('shortcuts:', e); }
      try { initClickableCards(); }    catch(e) { console.warn('clickCards:', e); }
      try { initConfirmDialogs(); }    catch(e) { console.warn('confirmDialog:', e); }
      try { initQuickActions(); }      catch(e) { console.warn('quickActions:', e); }
      try { initSmoothFocus(); }       catch(e) { console.warn('smoothFocus:', e); }
      try { initTooltips(); }          catch(e) { console.warn('tooltips:', e); }
      try { initTableHoverActions(); } catch(e) { console.warn('tableHover:', e); }
      try { initCopyFeedback(); }      catch(e) { console.warn('copy:', e); }
      try { initCommandPalette(); }    catch(e) { console.warn('cmdPalette:', e); }

      document.documentElement.dataset.uxReady = "true";
    } catch (err) {
      console.error('UX Intelligence Layer Error:', err);
      // Ensure page is still revealed even on total failure
      document.body.classList.add('ux-page-loaded');
    }
  });
})();
