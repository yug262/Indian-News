// =========================================
// CryptoWire — Frontend Logic (Production)
// =========================================

const DEFAULT_LOCAL_API = (() => {
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocalHost = host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0';
    const isLanHost = /^10\.|^192\.168\.|^172\.(1[6-9]|2\d|3[0-1])\./.test(host);
    if (port === '3000' || isLocalHost || isLanHost) {
        const targetHost = host === '0.0.0.0' ? 'localhost' : host;
        return `${window.location.protocol}//${targetHost}:8000`;
    }
    return '';
})();
const API_BASE = (window.APP_CONFIG && typeof window.APP_CONFIG.BACKEND_URL === 'string' && window.APP_CONFIG.BACKEND_URL.trim())
    ? window.APP_CONFIG.BACKEND_URL.trim()
    : DEFAULT_LOCAL_API;

// Wrapper around fetch() that injects the ngrok-skip-browser-warning header
// to bypass the ngrok free-tier interstitial page (ERR_NGROK_6024).
function apiFetch(url, options = {}) {
    const headers = options.headers instanceof Headers
        ? options.headers
        : new Headers(options.headers || {});
    headers.set('ngrok-skip-browser-warning', '1');
    return fetch(url, { ...options, headers });
}

const REFRESH_INTERVAL = 30_000; // 30 seconds
const SEARCH_DEBOUNCE = 300;
const SCROLL_TOP_THRESHOLD = 400;
const CONNECTION_FAIL_THRESHOLD = 2;

// ---- Debug Mode ----
// Set to true to enable verbose fetch/filter/WS logging in browser console.
const DEBUG = false;
function dbg(...args) { if (DEBUG) console.log('[DBG]', ...args); }

let currentSource = 'all';
let searchQuery = '';
let currentEventId = null;
let newsData = [];
let sourceFilters = [];
const analyzingArticles = new Set();
let eventsData = [];
let currentDashboardView = 'feed';
let eventsBoardSearch = '';
let eventsBoardSortBy = 'latest';

let showOnlyAnalyzed = false;
let currentRelevance = 'all';

// ---- Request State Machine ----
// 'idle' | 'loading' | 'background' | 'loadmore'
// Replaces the fragile isFetching + isLoadingMore booleans for explicit state tracking.
let requestState = 'idle';

let consecutiveFailures = 0;    // Network-level failures (no response at all)
let consecutiveBackendFailures = 0; // Backend-level failures (HTTP 200 but status='error')
let searchDebounceTimer = null;

// ---- Pagination & Smart Refresh ----

let totalDbArticles = 0;
let currentPage = 0;
const articlesPerPage = 20;
let hasMoreArticles = true;
const seenArticleIds = new Set();
let _fetchAbortController = null; // Abort stale fetch requests
let _fetchGeneration = 0;         // Monotonic counter — identify the "owning" fetchNews call

// Canonical map: pill data-relevance (lowercase) → DB-stored value (title case)
// This is the ONLY place the mapping lives. All filter logic reads from this.
const RELEVANCE_CANONICAL = {
    'all':        null,          // "all" means no filter (do not send to backend)
    'high useful': 'High Useful',
    'useful':      'Useful',
    'medium':      'Medium',
    'neutral':     'Neutral',
    'noisy':       'Noisy'
};

// ---- DOM Elements ----
const newsGrid = document.getElementById('newsGrid');
const featuredGrid = document.getElementById('featuredGrid');
const featuredSection = document.getElementById('featuredSection');
const allNewsHeader = document.getElementById('allNewsHeader');
const emptyState = document.getElementById('emptyState');
const emptyStateTitle = document.getElementById('emptyStateTitle');
const emptyStateMsg = document.getElementById('emptyStateMsg');
const filtersContainer = document.getElementById('filtersContainer');
const articleCount = document.getElementById('articleCount');
const clockEl = document.getElementById('clock');
const refreshIndicator = document.getElementById('refreshIndicator');
const searchInput = document.getElementById('searchInput');
const searchClear = document.getElementById('searchClear');
const scrollTopBtn = document.getElementById('scrollTopBtn');
const connectionBanner = document.getElementById('connectionBanner');
const toastContainer = document.getElementById('toastContainer');
const themeToggle = document.getElementById('themeToggle');
const filtersSection = document.getElementById('filtersSection');
const feedView = document.getElementById('feedView');
const eventsBoardView = document.getElementById('eventsBoardView');
const eventsBoardGrid = document.getElementById('eventsBoardGrid');
const eventsBoardEmpty = document.getElementById('eventsBoardEmpty');
const eventsBoardSearchInput = document.getElementById('eventsBoardSearchInput');
const eventsBoardSort = document.getElementById('eventsBoardSort');
const eventsBoardTotal = document.getElementById('eventsBoardTotal');
const eventsBoardArticles = document.getElementById('eventsBoardArticles');
const eventsBoardLastUpdate = document.getElementById('eventsBoardLastUpdate');
const relevanceView = document.getElementById('relevanceView');
const relevanceTitle = document.getElementById('relevanceTitle');
const relevanceSubtitle = document.getElementById('relevanceSubtitle');
const relevanceKicker = document.getElementById('relevanceKicker');

// ---- Mobile Menu Elements ----
const mobileMenuTrigger = document.getElementById('mobileMenuTrigger');
const mobileDrawer = document.getElementById('mobileDrawer');
const drawerOverlay = document.getElementById('drawerOverlay');
const drawerClock = document.getElementById('drawerClock');
const drawerCount = document.getElementById('drawerCount');

let isDrawerOpen = false;

// ---- Theme Toggle ----
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cw-theme', theme);

    // Re-render chart if it's active so it picks up the new theme colors
    if (typeof renderLWChart === 'function' && window.chartCandleData) {
        renderLWChart(window.chartCandleData);
    }
}

// Load saved theme (default: dark)
const savedTheme = localStorage.getItem('cw-theme') || 'dark';
applyTheme(savedTheme);

themeToggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
});

// ---- Clock ----
function updateClock() {
    const now = new Date();
    const options = {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: true, timeZone: 'Asia/Kolkata'
    };
    const timeStr = now.toLocaleTimeString('en-US', options);
    clockEl.textContent = timeStr;
    if (drawerClock) drawerClock.textContent = timeStr;
}

setInterval(updateClock, 1000);
updateClock();

// ---- Time Ago ----
function timeAgo(dateStr) {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return '--';
    const now = new Date();
    const diffMs = now - date;
    if (diffMs < 0) return 'Just now'; // Handle clock drift gracefully
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        timeZone: 'Asia/Kolkata'
    });
}

function formatTime(dateStr) {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return '--:--';
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: true,
        timeZone: 'Asia/Kolkata'
    });
}

function formatUtcTime(dateStr) {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
        timeZone: 'UTC'
    });
}

function formatUtcDateTime(dateStr) {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleString('en-US', {
        month: '2-digit',
        day: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZone: 'UTC'
    }) + ' UTC';
}

function formatDateTimeIST(dateStr) {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleString('en-IN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
        timeZone: 'Asia/Kolkata'
    });
}

// ---- HTML Escaping ----
function escapeHtml(text) {
    if (text == null) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Specifically for Javascript arguments injected into HTML attribute wrappers (like onclick="foo('...')")
function escapeForInlineJsAttr(text) {
    if (text == null) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '');
}

// ---- Impact Helpers ----
function getScoreClass(score) {
    if (score <= 3) return 'low';
    if (score <= 6) return 'medium';
    return 'high';
}

function getScoreLabel(score) {
    if (score <= 3) return 'Low Impact';
    if (score <= 6) return 'Medium Impact';
    return 'High Impact';
}

function parseAffectedMarkets(article) {
    if (!article.affected_markets) return {};
    let markets = article.affected_markets;
    if (typeof markets === 'string') {
        try { markets = JSON.parse(markets); } catch { markets = {}; }
    }
    return markets;
}

function parseJsonField(value) {
    if (!value) return null;
    if (typeof value === 'object') return value;
    if (typeof value === 'string') {
        try { return JSON.parse(value); } catch { return null; }
    }
    return null;
}

function getMarketModeInfo(mode) {
    if (!mode) return { label: 'N/A', cssClass: 'mode-neutral', icon: '⚖️' };
    const m = mode.toLowerCase();
    if (m.includes('risk-on') || m.includes('risk on')) return { label: 'Risk-On', cssClass: 'mode-risk-on', icon: '🟢' };
    if (m.includes('risk-off') || m.includes('risk off')) return { label: 'Risk-Off', cssClass: 'mode-risk-off', icon: '🔴' };
    return { label: 'Neutral', cssClass: 'mode-neutral', icon: '⚖️' };
}

// ═══════════════════════════════════════════════
// AI ANALYSIS TOOLTIP SYSTEM
// ═══════════════════════════════════════════════
const AI_TOOLTIPS = {
    signal_bucket: {
        _title: 'Signal Strength',
        _desc: 'How directly this news affects Indian markets.',
        DIRECT: { color: '#00d4aa', desc: 'A named Indian company or sector is the direct subject of a confirmed event. Strongest signal.' },
        AMBIGUOUS: { color: '#f0c040', desc: 'A real event exists, but the direction, impact size, or which company is affected is unclear.' },
        WEAK_PROXY: { color: '#ff9f43', desc: 'The event is real but the connection to India is indirect — like a global event that may ripple into Indian markets.' },
        NOISE: { color: '#888', desc: 'No meaningful market signal. Opinion pieces, recaps, lifestyle news, or daily wraps with nothing new.' }
    },
    market_bias: {
        _title: 'Market Direction',
        _desc: 'Which way the AI thinks this news pushes the market.',
        bullish: { color: '#00d4aa', desc: 'The news is positive — likely to push prices up for the affected stocks or sectors.' },
        bearish: { color: '#ff4757', desc: 'The news is negative — likely to push prices down for the affected stocks or sectors.' },
        mixed: { color: '#f0c040', desc: 'The news has both positive and negative elements, or the price is moving opposite to the event.' },
        neutral: { color: '#888', desc: 'No clear directional push. The news is either too weak or already reflected in prices.' },
        unclear: { color: '#666', desc: 'Not enough information to determine a direction. More data needed.' }
    },
    tradeability: {
        _title: 'Can You Trade This?',
        _desc: 'Whether this news creates a tradeable opportunity right now.',
        actionable_now: { color: '#00d4aa', desc: 'Strong signal, market is open, price hasn\'t fully reacted yet. There may be an opportunity to act on this right now.' },
        wait_for_confirmation: { color: '#f0c040', desc: 'Real event, but either the market is closed, the price already moved a lot, or more clarity is needed before trading.' },
        no_edge: { color: '#888', desc: 'No trading opportunity. The signal is too weak, already priced in, or there\'s no clear connection to any tradeable asset.' }
    },
    horizon: {
        _title: 'Time Horizon',
        _desc: 'How long the AI expects this news to influence the market.',
        intraday: { color: '#6C63FF', desc: 'Impact expected within today\'s trading session only. Fast-moving, short-lived effect.' },
        short_term: { color: '#00c8b4', desc: 'Impact expected over the next few days to a week. The market will digest this over multiple sessions.' },
        medium_term: { color: '#f0c040', desc: 'Impact expected over weeks to a couple of months. A structural shift that takes time to play out.' },
        long_term: { color: '#ff9f43', desc: 'Impact over months or longer. Significant economic or policy change with lasting effects.' }
    },
    category: {
        _title: 'News Category',
        _desc: 'What type of event this news represents.',
        corporate_event: { desc: 'Company-specific action — earnings, deals, orders, management changes, plant events, or filings.' },
        government_policy: { desc: 'Government or regulator decision — new rules, tax changes, policy announcements, or compliance actions.' },
        macro_data: { desc: 'Economic data release — inflation (CPI), GDP, PMI, industrial production, or RBI data.' },
        global_macro_impact: { desc: 'A global event that clearly affects India through trade, capital flows, risk sentiment, or interest rates.' },
        commodity_macro: { desc: 'Oil, gas, metals, or commodity price/supply changes with meaningful impact on Indian companies.' },
        sector_trend: { desc: 'A real shift affecting multiple companies across an entire industry — not just one stock.' },
        institutional_activity: { desc: 'Large money movements — FII/DII flows, big stake sales/purchases, or institutional allocation changes.' },
        sentiment_indicator: { desc: 'Market mood signals — surveys, positioning data, confidence indicators, or sentiment metrics.' },
        price_action_noise: { desc: 'Headline mainly describes a stock or index moving without any real new trigger behind it.' },
        routine_market_update: { desc: 'Daily wrap, recap, or summary of already-known information. Nothing new here.' },
        other: { desc: 'Doesn\'t fit neatly into any category. A catch-all for unusual or rare event types.' }
    },
    relevance: {
        _title: 'Trading Relevance',
        _desc: 'How useful is this news for making trading decisions.',
        'high useful': { color: '#ff6b6b', desc: 'Confirmed, new, directly relevant — the strongest signal. Think: major earnings surprise, RBI rate decision, big policy change.' },
        useful: { color: '#00d4aa', desc: 'Confirmed event with clear economic relevance. Worth paying attention to and may present trading ideas.' },
        medium: { color: '#f0c040', desc: 'Market-relevant but indirect, partial, or routine. Good context but not a strong standalone trade signal.' },
        neutral: { color: '#888', desc: 'Market-related but weak. Informational only — no strong economic change expected.' },
        noisy: { color: '#ff4757', desc: 'Speculation, commentary, recap, or price-only movement. Safe to ignore for trading purposes.' }
    },
    event_type: {
        _title: 'Event Type',
        _desc: 'What kind of business event triggered this news.',
        earnings: { desc: 'Quarterly or annual results, profit/loss reports, revenue figures, or guidance updates.' },
        policy: { desc: 'Government or central bank policy action — rates, regulation, taxes, subsidies, or approvals.' },
        order_win: { desc: 'A company won a new contract, order, or deal that affects its future revenue.' },
        macro: { desc: 'Macroeconomic data or event — GDP, inflation, trade data, employment, or fiscal numbers.' },
        regulation: { desc: 'New rules, compliance requirements, bans, or regulatory approvals affecting industries.' },
        disruption: { desc: 'Supply chain disruption, natural disaster, plant shutdown, or logistics interruption.' },
        corporate_action: { desc: 'Mergers, acquisitions, buybacks, stock splits, delistings, or major restructuring.' },
        other: { desc: 'An event that doesn\'t fit standard categories.' }
    },
    event_status: {
        _title: 'Confirmation Status',
        _desc: 'How confirmed is this event.',
        confirmed: { color: '#00d4aa', desc: 'Officially announced, verified data, or published by a reliable source. You can trust this happened.' },
        developing: { color: '#f0c040', desc: 'Partially confirmed — details are still emerging. The story might change as more information comes in.' },
        rumor: { color: '#ff9f43', desc: 'Unverified, unnamed sources, or speculative language. Take with a grain of salt.' },
        noise: { color: '#888', desc: 'No real event. Opinion, commentary, or recap of old information.' }
    },
    event_scope: {
        _title: 'Impact Scope',
        _desc: 'How wide is the impact of this event.',
        single_stock: { color: '#6C63FF', desc: 'Affects only one specific company. The stock moved independently from its peers.' },
        sector: { color: '#00c8b4', desc: 'Affects an entire sector or industry. Multiple companies in the same space are impacted.' },
        broad_market: { color: '#ff9f43', desc: 'Affects the overall market — indices, broad sentiment, or macro conditions for all stocks.' }
    },
    impact_score: {
        _title: 'Impact Score (0-10)',
        _desc: 'How strong is the actual economic change from this news.',
        '0-1': { color: '#555', desc: 'No meaningful economic change. Noise.' },
        '2-3': { color: '#888', desc: 'Minor change — only one of: revenue, confirmation, scale, or timing is present.' },
        '4-5': { color: '#f0c040', desc: 'Moderate — two factors confirmed. Starts becoming potentially tradeable.' },
        '6-7': { color: '#ff9f43', desc: 'Significant — three factors confirmed. Clear company/sector affected, near-term impact expected.' },
        '8-10': { color: '#ff4757', desc: 'Major — all four factors present: changes economics, confirmed, significant scale, near-term effect.' }
    },
    remaining_impact: {
        _title: 'Remaining Edge',
        _desc: 'How much of this news is already reflected in the stock price.',
        untouched: { color: '#00d4aa', desc: 'Market barely reacted yet. Most of the potential price move is still ahead.' },
        early: { color: '#26a69a', desc: 'Reaction just started. Price moved a little, but there\'s likely more to come.' },
        partially_absorbed: { color: '#f0c040', desc: 'Some move happened already. There might be follow-through, but the easy part is done.' },
        mostly_absorbed: { color: '#ff9f43', desc: 'Most of the obvious reaction is over. Limited upside from chasing this now.' },
        exhausted: { color: '#ff4757', desc: 'Fully reflected in price. The market already digested this news completely. No edge left.' }
    },
    event_activity_status: {
        _title: 'Event Status',
        _desc: 'Shows how actively this event is moving right now.',
        live: { color: '#00d464', desc: 'Live means this story is actively moving and has fresh momentum in the market.' },
        tracking: { color: '#7f8ca8', desc: 'Tracking means this story is still important, and we are monitoring it for the next meaningful update.' }
    },
    event_attention_level: {
        _title: 'Attention Level',
        _desc: 'Shows how strongly the market is focused on this event.',
        high_attention: { color: '#6C63FF', desc: 'High Attention means this event is getting strong market focus and can influence sentiment quickly.' },
        medium_attention: { color: '#00c8b4', desc: 'Medium Attention means the event matters and is being watched, but market urgency is moderate.' },
        emerging: { color: '#f0c040', desc: 'Emerging means this story is early and may become more important as new details come in.' }
    }
};

/**
 * Wraps badge HTML with a data-ai-tip attribute for the floating tooltip system.
 * No inline tooltip HTML is embedded — the single floating tooltip reads these attributes.
 * @param {string} innerHtml - The visible badge HTML
 * @param {string} field - Key into AI_TOOLTIPS (e.g. 'signal_bucket')
 * @param {string} [currentValue] - The current value to highlight
 * @returns {string} HTML string with data attributes
 */
function wrapTooltip(innerHtml, field, currentValue) {
    const fieldData = AI_TOOLTIPS[field];
    if (!fieldData) return innerHtml;
    const safeVal = (currentValue || '').replace(/"/g, '&quot;');
    return `<span data-ai-tip="${field}" data-ai-val="${safeVal}">${innerHtml}</span>`;
}

// ---- Floating Tooltip Engine ----
(function initAiTooltip() {
    // Create the single floating tooltip container
    const tip = document.createElement('div');
    tip.id = 'aiTooltipFloat';
    document.body.appendChild(tip);

    let hideTimer = null;
    let activeTrigger = null;
    const isMobile = () => window.innerWidth <= 768;

    function buildTooltipContent(field, currentValue) {
        const data = AI_TOOLTIPS[field];
        if (!data) return '';

        const title = data._title || field;
        const desc = data._desc || '';
        const valueKeys = Object.keys(data).filter(k => !k.startsWith('_'));

        let html = `<div class="tt-title">\u2139\uFE0F ${title}</div>`;
        html += `<div class="tt-desc">${desc}</div>`;

        if (valueKeys.length > 0) {
            html += '<div class="tt-divider"></div><div class="tt-values">';
            valueKeys.forEach(key => {
                const v = data[key];
                const isActive = currentValue && key.toLowerCase() === currentValue.toLowerCase();
                const dotColor = v.color || '#6C63FF';
                const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                html += `<div class="tt-row">
                    <span class="tt-dot${isActive ? ' active' : ''}" style="background:${dotColor};${isActive ? 'color:' + dotColor : ''}"></span>
                    <span><span class="tt-vlabel${isActive ? ' active' : ''}">${label}:</span> <span class="tt-vdesc">${v.desc}</span></span>
                </div>`;
            });
            html += '</div>';
        }
        return html;
    }

    function showTooltip(trigger) {
        if (activeTrigger === trigger && tip.classList.contains('visible')) return;

        clearTimeout(hideTimer);
        const field = trigger.getAttribute('data-ai-tip');
        const val = trigger.getAttribute('data-ai-val') || '';
        const content = buildTooltipContent(field, val);
        if (!content) return;

        tip.innerHTML = content;
        activeTrigger = trigger;

        if (isMobile()) {
            tip.style.top = '';
            tip.style.left = '';
            tip.classList.add('visible');
            setTimeout(() => {
                document.addEventListener('touchstart', closeMobileTooltip, { once: true });
            }, 50);
        } else {
            tip.classList.add('visible');
            const rect = trigger.getBoundingClientRect();
            const tipRect = tip.getBoundingClientRect();

            // Try to show above - Reduced gap to 6px
            let top = rect.top - tipRect.height - 6;
            let left = rect.left + rect.width / 2 - tipRect.width / 2;

            if (top < 8) {
                top = rect.bottom + 6;
            }
            if (left < 8) left = 8;
            if (left + tipRect.width > window.innerWidth - 8) {
                left = window.innerWidth - tipRect.width - 8;
            }

            tip.style.top = top + 'px';
            tip.style.left = left + 'px';
        }
    }

    function hideTooltip() {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => {
            tip.classList.remove('visible');
            activeTrigger = null;
        }, 200);
    }

    function closeMobileTooltip(e) {
        if (!tip.contains(e.target)) {
            tip.classList.remove('visible');
            activeTrigger = null;
        }
    }

    // Expert Event Delegation: Robust mouseover/mouseout
    document.addEventListener('mouseover', (e) => {
        const trigger = e.target.closest('[data-ai-tip]');
        const isTooltip = e.target.closest('#aiTooltipFloat');

        if (trigger || isTooltip) {
            clearTimeout(hideTimer);
            if (trigger) {
                showTooltip(trigger);
            }
        }
    });

    document.addEventListener('mouseout', (e) => {
        const trigger = e.target.closest('[data-ai-tip]');
        const isTooltip = e.target.closest('#aiTooltipFloat');
        const related = e.relatedTarget;

        // Only trigger hide if moving to an element that is NOT part of the trigger or tooltip
        const movingToSafe = related && (related.closest('[data-ai-tip]') || related.closest('#aiTooltipFloat'));

        if (!movingToSafe) {
            hideTooltip();
        }
    });

    // Touch support (mobile) remains largely the same but simplified
    document.addEventListener('touchstart', (e) => {
        const trigger = e.target.closest('[data-ai-tip]');
        if (trigger) {
            e.preventDefault();
            if (activeTrigger === trigger && tip.classList.contains('visible')) {
                tip.classList.remove('visible');
                activeTrigger = null;
            } else {
                showTooltip(trigger);
            }
        }
    }, { passive: false });

    document.addEventListener('scroll', (e) => {
        if (!isMobile() && !tip.contains(e.target)) hideTooltip();
    }, true);
})();

function renderRelevanceBadge(relevance, useRichTooltip = true) {
    if (!relevance) return '';
    const rel = relevance.toLowerCase();
    let cssClass = 'rel-neutral';

    if (rel.includes('very high')) cssClass = 'rel-very-high';
    else if (rel.includes('high')) cssClass = 'rel-high';
    else if (rel.includes('useful')) cssClass = 'rel-useful';
    else if (rel.includes('medium')) cssClass = 'rel-medium';
    else if (rel.includes('noisy')) cssClass = 'rel-noisy';

    const label = relevance.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

    // Get short description for title attribute
    const tipData = AI_TOOLTIPS.relevance[rel] || {};
    const titleAttr = tipData.desc ? ` title="${label}: ${tipData.desc}"` : '';

    const badgeHtml = `<span class="relevance-badge ${cssClass}"${titleAttr}>${escapeHtml(label)}</span>`;

    if (useRichTooltip) {
        return wrapTooltip(badgeHtml, 'relevance', rel);
    }
    return badgeHtml;
}

function renderCategoryBadge(category, useRichTooltip = true) {
    if (!category || category.toLowerCase() === 'none') return '';
    const label = category.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

    // Get short description for title attribute
    const tipData = AI_TOOLTIPS.category[category.toLowerCase()] || {};
    const titleAttr = tipData.desc ? ` title="${label}: ${tipData.desc}"` : '';

    const badgeHtml = `<span class="category-badge"${titleAttr}>${escapeHtml(label)}</span>`;

    if (useRichTooltip) {
        return wrapTooltip(badgeHtml, 'category', category.toLowerCase());
    }
    return badgeHtml;
}

function renderAllSymbolsBadge(symbols) {
    if (!symbols) return '';
    let symbolArr = symbols;
    if (typeof symbols === 'string') {
        try { symbolArr = JSON.parse(symbols); } catch { return ''; }
    }
    if (!Array.isArray(symbolArr) || symbolArr.length === 0) return '';
    return symbolArr.map(sym => `
        <span class="category-badge clickable-symbol" 
              style="background: rgba(255, 193, 7, 0.15); color: #ffca28; border-color: rgba(255, 193, 7, 0.3); font-weight: bold; letter-spacing: 0.5px; cursor: pointer; transition: all 0.2s;"
              onclick="event.stopPropagation(); selectChartPair('${escapeHtml(sym)}')">
            ${escapeHtml(sym)}
        </span>
    `).join('');
}

function getBiasInfo(bias) {
    if (!bias) return { label: 'N/A', cssClass: 'bias-neutral', arrow: '➖' };
    const b = bias.toLowerCase();
    if (b.includes('bullish')) return { label: 'Bullish', cssClass: 'bias-bullish', arrow: '▲' };
    if (b.includes('bearish')) return { label: 'Bearish', cssClass: 'bias-bearish', arrow: '▼' };
    return { label: 'Neutral', cssClass: 'bias-neutral', arrow: '➖' };
}

function getConfidenceInfo(conf) {
    if (conf === null || conf === undefined || conf === '') {
        return { label: 'N/A', cssClass: 'conf-medium', icon: '◐' };
    }

    if (typeof conf === 'number') {
        if (conf >= 8) return { label: 'High', cssClass: 'conf-high', icon: '●' };
        if (conf <= 4) return { label: 'Low', cssClass: 'conf-low', icon: '○' };
        return { label: 'Medium', cssClass: 'conf-medium', icon: '◐' };
    }

    const c = String(conf).toLowerCase();
    if (c === 'high') return { label: 'High', cssClass: 'conf-high', icon: '●' };
    if (c === 'low') return { label: 'Low', cssClass: 'conf-low', icon: '○' };
    return { label: 'Medium', cssClass: 'conf-medium', icon: '◐' };
}

function renderMarketBar(label, value, cssClass) {
    const pct = (value / 10) * 100;
    return `
        <div class="market-bar-item">
            <div class="market-bar-label"><span>${label}</span><span>${value}/10</span></div>
            <div class="market-bar-track">
                <div class="market-bar-fill ${cssClass}" style="width: ${pct}%"></div>
            </div>
        </div>
    `;
}

function formatResearchText(text) {
    if (!text) return '';
    const lines = text.split('\n');
    let html = '';
    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        // Separator lines
        if (/^-{3,}$/.test(trimmed)) {
            html += '<hr class="research-hr">';
            continue;
        }

        // Section headers: ALL CAPS lines or lines ending with ":"
        const isHeader = /^[A-Z][A-Z\s\/\-—()&]{4,}$/.test(trimmed) ||
            (/:\s*$/.test(trimmed) && trimmed.length < 80 && !trimmed.startsWith('-') && !trimmed.startsWith('•'));
        if (isHeader) {
            const headerText = escapeHtml(trimmed.replace(/:\s*$/, ''));
            html += `<h4 class="research-heading">${headerText}</h4>`;
            continue;
        }

        // Numbered items like "1)" or "1."
        const numMatch = trimmed.match(/^(\d+)\)\s*(.+)/);
        if (numMatch) {
            const content = formatInlineMd(escapeHtml(numMatch[2]));
            html += `<div class="research-numbered"><span class="research-num">${numMatch[1]}</span><span>${content}</span></div>`;
            continue;
        }

        // Bullet points (-, •, or *)
        if (/^[-•*]\s/.test(trimmed)) {
            const bulletText = formatInlineMd(escapeHtml(trimmed.replace(/^[-•*]\s*/, '')));
            html += `<div class="research-bullet"><span class="research-bullet-dot">•</span><span>${bulletText}</span></div>`;
            continue;
        }

        // Regular paragraph
        html += `<p class="research-para">${formatInlineMd(escapeHtml(trimmed))}</p>`;
    }
    return html;
}

function formatInlineMd(text) {
    // **bold** → <strong>
    return text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

function renderTradeActions(actions) {
    const data = parseJsonField(actions);
    if (!data) return '';
    const buyList = data.buy || [];
    const sellList = data.sell || [];
    const watchList = data.watch || [];
    if (buyList.length === 0 && sellList.length === 0 && watchList.length === 0) return '';

    function renderTradeCard(item, type) {
        if (typeof item === 'string') {
            return `<div class="trade-card trade-card-${type}">
                <div class="trade-card-body">${escapeHtml(item)}</div>
            </div>`;
        }
        if (typeof item !== 'object' || item === null) {
            return `<div class="trade-card trade-card-${type}">
                <div class="trade-card-body">${escapeHtml(String(item))}</div>
            </div>`;
        }

        const asset = item.pair || item.asset || item.symbol || item.name || '';
        const strength = item.strength;
        const reason = item.reason || '';
        const direction = item.direction || '';
        const confidence = item.confidence || '';

        // Expected move range (buy/sell)
        const moveRange = item.expected_move_percent;
        let moveHtml = '';
        if (moveRange && typeof moveRange === 'object') {
            moveHtml = `<span class="trade-move-range">${moveRange.min ?? '?'}% – ${moveRange.max ?? '?'}%</span>`;
        }

        // Probability (buy/sell)
        const probability = item.probability_percent;
        let probHtml = '';
        if (probability != null) {
            probHtml = `<div class="trade-prob-row">
                <span class="trade-prob-label">Probability</span>
                <div class="trade-prob-track"><div class="trade-prob-fill trade-prob-fill-${type}" style="width:${Math.min(probability, 100)}%"></div></div>
                <span class="trade-prob-val">${probability}%</span>
            </div>`;
        }

        // Per-trade confidence
        let confHtml = '';
        if (confidence) {
            const confInfo = getConfidenceInfo(confidence);
            confHtml = `<span class="trade-conf-badge ${confInfo.cssClass}">${confInfo.icon} ${confInfo.label}</span>`;
        }

        // Strength bar
        let strengthHtml = '';
        if (strength && typeof strength === 'number') {
            strengthHtml = `<div class="trade-card-strength">
                <span class="trade-card-strength-label">Strength</span>
                <div class="trade-card-strength-track"><div class="trade-card-strength-fill trade-card-strength-${type}" style="width:${strength * 10}%"></div></div>
                <span class="trade-card-strength-val">${strength}/10</span>
            </div>`;
        }

        // Watch-specific: trigger_condition, breakout_probability, expected_move_if_triggered
        const triggerCondition = item.trigger_condition || item.trigger || '';
        const breakoutProb = item.breakout_probability_percent;
        const moveIfTriggered = item.expected_move_if_triggered;
        let watchExtras = '';
        if (type === 'watch') {
            if (triggerCondition) {
                watchExtras += `<div class="trade-card-trigger"><span class="trade-card-trigger-label">Trigger:</span> ${escapeHtml(triggerCondition)}</div>`;
            }
            if (breakoutProb != null) {
                watchExtras += `<div class="trade-prob-row">
                    <span class="trade-prob-label">Breakout Prob.</span>
                    <div class="trade-prob-track"><div class="trade-prob-fill trade-prob-fill-watch" style="width:${Math.min(breakoutProb, 100)}%"></div></div>
                    <span class="trade-prob-val">${breakoutProb}%</span>
                </div>`;
            }
            if (moveIfTriggered && typeof moveIfTriggered === 'object') {
                watchExtras += `<div class="trade-move-triggered"><span class="trade-move-triggered-label">Move if triggered:</span> ${moveIfTriggered.min ?? '?'}% – ${moveIfTriggered.max ?? '?'}%</div>`;
            }
        }

        return `<div class="trade-card trade-card-${type}">
            <div class="trade-card-header">
                <span class="trade-card-asset">${escapeHtml(asset)}</span>
                ${moveHtml}
                ${direction ? `<span class="trade-card-dir trade-card-dir-${direction.toLowerCase()}">${escapeHtml(direction)}</span>` : ''}
                ${confHtml}
            </div>
            ${strengthHtml}
            ${probHtml}
            ${watchExtras}
            ${reason ? `<div class="trade-card-reason">${escapeHtml(reason)}</div>` : ''}
        </div>`;
    }

    let html = '<div class="trade-actions-section">';
    html += '<div class="analysis-bars-title">Trade Actions</div>';
    html += '<div class="trade-actions-list">';

    for (const item of buyList) {
        html += `<div class="trade-group-row">
            <span class="trade-group-badge badge-buy">BUY</span>
            ${renderTradeCard(item, 'buy')}
        </div>`;
    }
    for (const item of sellList) {
        html += `<div class="trade-group-row">
            <span class="trade-group-badge badge-sell">SELL</span>
            ${renderTradeCard(item, 'sell')}
        </div>`;
    }
    for (const item of watchList) {
        html += `<div class="trade-group-row">
            <span class="trade-group-badge badge-watch">WATCH</span>
            ${renderTradeCard(item, 'watch')}
        </div>`;
    }
    html += '</div></div>';
    return html;
}

function renderForexPairs(article) {
    return '';
}

function renderNewInfoBadge(article) {
    if (!article.is_new_information) return '';
    if (article.is_new_information === true) {
        return `<span class="new-info-badge new-info-new">🆕 NEW INFO</span>`;
    }
    return `<span class="new-info-badge new-info-priced">📊 PRICED IN</span>`;
}


function renderImpactBadge(article) {
    let badges = '';
    const isAnalyzed = !!article.analyzed;
    const analysis = parseJsonField(article.analysis_data);

    if (isAnalyzed) {
        // Show Impact Score
        if (article.impact_score != null) {
            const scoreClass = getScoreClass(article.impact_score);
            const scoreRange = article.impact_score <= 1 ? '0-1' : article.impact_score <= 3 ? '2-3' : article.impact_score <= 5 ? '4-5' : article.impact_score <= 7 ? '6-7' : '8-10';
            const badgeHtml = `<span class="impact-score-badge ${scoreClass}">⚡ ${article.impact_score}/10</span>`;
            badges += wrapTooltip(badgeHtml, 'impact_score', scoreRange);
        }
    } else {
        // Fallback or legacy impact displays
        if (article.news_impact_level && article.news_impact_level !== 'None' && article.news_impact_level !== 'Neutral') {
            const imp = article.news_impact_level.toLowerCase();
            let css = 'impact-neutral';
            if (imp === 'positive') css = 'impact-positive';
            if (imp === 'negative') css = 'impact-negative';
            badges += `<span class="impact-tag ${css}" style="margin-right:6px">📊 ${article.news_impact_level}</span>`;
        }
    }
    return badges;
}

function renderAnalyzeButton(article) {
    const isAnalyzing = analyzingArticles.has(article.id);
    const btnState = isAnalyzing ? 'disabled' : '';
    const btnClass = isAnalyzing ? 'analyzing' : '';
    
    let btnText = 'Analyze';
    if (isAnalyzing) {
        btnText = '<div class="analyzing-spinner-sm"></div> Analyzing…';
    }

    return `
        <button class="analyze-btn analyze-btn-sm ${btnClass}" data-id="${article.id}" ${btnState} onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">
             ${btnText}
        </button>
    `;
}

function renderPredictionBadge(article) {
    if (!article.prediction_count) return '';
    const status = (article.prediction_status || 'pending').toLowerCase();

    // Status emoji mapping
    const emojis = {
        hit: '✅',
        overperformed: '🚀',
        underperformed: '⚠️',
        wrong: '❌',
        pending: '⏱️',
        error: '⚠️'
    };

    const emoji = emojis[status] || '⏱️';
    const statusCap = status.charAt(0).toUpperCase() + status.slice(1);

    // Show single best prediction or generic mult-count
    if (article.prediction_count === 1 && article.prediction_asset) {
        const moveTxt = article.predicted_move_pct ? ` (${article.predicted_move_pct}%)` : '';
        const displayAsset = article.prediction_asset_display_name || formatSymbol(article.prediction_asset);
        return `<span class="pred-summary-badge pred-status-${status}">
            ${emoji} ${escapeHtml(displayAsset)}${moveTxt} · ${statusCap}
        </span>`;
    } else {
        return `<span class="pred-summary-badge pred-status-${status}">
            ${emoji} ${article.prediction_count} Predictions · ${statusCap}
        </span>`;
    }
}

function renderSuggestionsTab(article) {
    let suggestions = null;

    // Try new JSONB structure
    if (article.analysis_data && typeof article.analysis_data === 'object' && article.analysis_data.suggestions) {
        suggestions = article.analysis_data.suggestions;
    } else if (typeof article.analysis_data === 'string') {
        try {
            const parsed = JSON.parse(article.analysis_data);
            if (parsed.suggestions) suggestions = parsed.suggestions;
        } catch (e) { }
    }

    // Try fallback structure from flat DB
    if (!suggestions && article.suggestions_data) {
        if (typeof article.suggestions_data === 'object') {
            suggestions = article.suggestions_data;
        } else if (typeof article.suggestions_data === 'string') {
            try { suggestions = JSON.parse(article.suggestions_data); } catch (e) { }
        }
    }

    // If still nothing, but flat status exists, build it
    if (!suggestions && article.suggestions_status) {
        suggestions = {
            status: article.suggestions_status,
            summary: article.suggestions_summary || '',
            buy: [], sell: [], watch: [], avoid: []
        };
    }

    if (!suggestions) {
        return '<p style="color:var(--text-muted); font-size:0.85rem; padding:12px 0">Suggestions unavailable or still analyzing.</p>';
    }

    const st = suggestions.status || 'failed';

    if (st === 'failed') {
        return '<p style="color:var(--text-muted); font-size:0.85rem; padding:12px 0">Suggestions unavailable (analysis failed).</p>';
    }

    if (st === 'no_clean_setup') {
        return `
            <div style="background:var(--bg-card); border:1px solid var(--border-color); border-radius:8px; padding:16px; margin-top:12px;">
                <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                    <span style="font-size:1.2rem;">⚖️</span>
                    <strong style="color:var(--text-main); font-size:1rem;">No Clean Setup</strong>
                </div>
                <p style="color:var(--text-secondary); font-size:0.9rem; line-height:1.5;">${escapeHtml(suggestions.summary || 'No high-conviction trade idea based on this event.')}</p>
            </div>
        `;
    }

    let html = '';

    if (suggestions.summary) {
        html += `<p style="color:var(--text-secondary); font-size:0.9rem; margin-bottom:16px; line-height:1.5; padding:0 4px;">${escapeHtml(suggestions.summary)}</p>`;
    }

    const groups = [
        { key: 'buy', label: 'Buy / Long', color: 'var(--bullish)', bg: 'rgba(0, 212, 170, 0.1)', border: 'rgba(0, 212, 170, 0.4)' },
        { key: 'sell', label: 'Sell / Short', color: 'var(--bearish)', bg: 'rgba(255, 71, 87, 0.1)', border: 'rgba(255, 71, 87, 0.4)' },
        { key: 'watch', label: 'Watchlist', color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.1)', border: 'rgba(59, 130, 246, 0.4)' },
        { key: 'avoid', label: 'Avoid / Danger', color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.4)' }
    ];

    let hasItems = false;

    for (const g of groups) {
        const items = suggestions[g.key];
        if (Array.isArray(items) && items.length > 0) {
            hasItems = true;
            html += `
                <div class="analysis-sub-section" style="margin-bottom:20px;">
                    <div class="analysis-bars-title" style="color:${g.color}; border-bottom:1px solid ${g.border}; padding-bottom:4px;">
                        ${g.label.toUpperCase()}
                    </div>
                    <div style="display:flex; flex-direction:column; gap:12px; margin-top:12px;">
            `;

            items.forEach(item => {
                let asset = 'Unknown Asset';
                let reason = '';
                let logic = '';
                let invalid = '';
                let time_val = '';
                let exp_move = '';
                let confidence = '';

                if (typeof item === 'string') {
                    asset = 'Trade Idea';
                    reason = item;
                } else if (typeof item === 'object' && item !== null) {
                    asset = item.asset || item.pair || item.index || item.symbol || item.name || 'Unknown Asset';
                    reason = item.reasoning || item.reason || '';
                    logic = item.market_logic || '';
                    invalid = item.invalidation || '';
                    time_val = item.time_window || '';
                    exp_move = item.expected_move_pct || '';
                    confidence = item.confidence || '';
                }

                const move = exp_move ? `<span style="background:${g.bg}; color:${g.color}; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:4px;">🎯 Target: ${escapeHtml(String(exp_move))}</span>` : '';
                const time = time_val ? `<span style="font-size:0.75rem; color:var(--text-muted); display:flex; align-items:center; gap:4px;">⏱️ ${escapeHtml(time_val)}</span>` : '';
                const conf = confidence ? `<span style="font-size:0.7rem; text-transform:uppercase; border:1px solid var(--border-color); padding:1px 6px; border-radius:4px; color:var(--text-secondary);">Conf: ${escapeHtml(confidence)}</span>` : '';

                html += `
                    <div style="border-left:3px solid ${g.border}; padding-left:12px; background:var(--bg-main); padding:12px; border-radius:0 6px 6px 0; border-top:1px solid var(--border-color); border-right:1px solid var(--border-color); border-bottom:1px solid var(--border-color);">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
                            <div style="font-weight:700; font-size:1.05rem; color:var(--text-main);">${escapeHtml(asset)}</div>
                            <div style="display:flex; gap:6px; align-items:center;">${conf} ${move}</div>
                        </div>
                        ${time}
                        
                        <div style="margin-top:10px; font-size:0.85rem; line-height:1.5;">
                            ${reason ? `<p style="color:var(--text-secondary); margin-bottom:8px;"><strong style="color:var(--text-main);">Reasoning:</strong> ${escapeHtml(reason)}</p>` : ''}
                            ${logic ? `<p style="color:var(--text-secondary); margin-bottom:8px;"><strong style="color:var(--text-main);">Market Logic:</strong> ${escapeHtml(logic)}</p>` : ''}
                            ${invalid ? `<p style="color:#f59e0b; margin-top:8px; border-top:1px dashed var(--border-color); padding-top:8px;"><strong>⚠️ Invalidation:</strong> ${escapeHtml(invalid)}</p>` : ''}
                        </div>
                    </div>
                `;
            });

            html += `</div></div>`;
        }
    }

    if (!hasItems) {
        return '<p style="color:var(--text-muted); font-size:0.85rem; padding:12px 0">No specific trade suggestions found.</p>';
    }

    return html;
}

function renderCardAnalysis(article) {
    if (!article.impact_score) return '';

    const analysis = parseJsonField(article.analysis_data);
    const coreView = (analysis && analysis.core_view) ? analysis.core_view : {};

    const scoreClass = getScoreClass(article.impact_score);
    const scoreLabel = getScoreLabel(article.impact_score);

    // For Indian News, show signal bucket clearly
    const rawBucket = article.signal_bucket || (analysis && analysis.signal_bucket);
    let bucketHtml = '';
    if (rawBucket) {
        const bucket = rawBucket.toUpperCase();
        const bucketCls = `bucket-${rawBucket.toLowerCase().replace('_', '-')}`;
        bucketHtml = `<span class="signal-bucket-badge ${bucketCls}" style="transform:scale(0.85); transform-origin:left center;">${bucket}</span>`;
    }

    const bias = (coreView.market_bias || 'Neutral').toLowerCase();
    const biasInfo = getBiasInfo(bias);
    const horizon = coreView.horizon || 'N/A';

    return `
        <div class="card-analysis">
            <div class="card-analysis-header">
                ${bucketHtml}
                <span class="impact-score-badge ${scoreClass}" style="margin-left:auto">⚡ ${article.impact_score}/10</span>
            </div>
            <div class="card-analysis-meta">
                <span class="bias-pill ${biasInfo.cssClass}">${biasInfo.arrow} ${biasInfo.label} Bias</span>
                <span class="confidence-pill conf-high" style="font-size:0.6rem">⏱ ${horizon}</span>
            </div>
            <p class="card-analysis-summary">${escapeHtml(article.executive_summary || article.impact_summary || coreView.summary || '')}</p>
        </div>
    `;
}

// ---- Toast Notifications ----
function showToast(message, type = 'info') {
    const icons = { success: '✓', error: '✕', info: 'ℹ' };
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${icons[type] || icons.info}</span>
        <span>${escapeHtml(message)}</span>
        <div class="toast-progress"></div>
    `;
    toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ---- Connection Banner ----
function showConnectionBanner() {
    connectionBanner.classList.add('visible');
}

function hideConnectionBanner() {
    connectionBanner.classList.remove('visible');
    consecutiveFailures = 0;
}

// ---- Analyze Article ----
async function analyzeArticle(newsId, btnEl) {
    if (analyzingArticles.has(newsId)) return;
    analyzingArticles.add(newsId);

    // Show loading state on ALL buttons for this article
    document.querySelectorAll(`.analyze-btn[data-id="${newsId}"]`).forEach(btn => {
        btn.disabled = true;
        btn.innerHTML = '<div class="analyzing-spinner"></div> Analyzing…';
        btn.classList.add('analyzing');
    });

    try {
        const res = await apiFetch(`${API_BASE}/api/indian_analyze/${newsId}`, { method: 'POST' });
        const json = await res.json();

        if (json.status === 'success') {
            showToast('Analysis complete — impact score assigned', 'success');

            // Instantly patch the local DOM and state so the user sees results immediately
            const updatedArticle = newsData.find(a => a.id === newsId);
            if (updatedArticle && json.data) {
                const analysisResult = json.data;

                // If server returned the complete updated DB row, use it (best source of truth)
                if (json.article) {
                    // Merge the full DB row into our local article (preserves all flat fields)
                    Object.assign(updatedArticle, json.article);
                    // Ensure analysis_data is the full object (not stringified)
                    if (typeof updatedArticle.analysis_data === 'string') {
                        try { updatedArticle.analysis_data = JSON.parse(updatedArticle.analysis_data); } catch (e) { }
                    }
                } else {
                    // Fallback: manually map nested analysis to flat fields
                    updatedArticle.analyzed = true;
                    updatedArticle.analysis_data = analysisResult;

                    const coreView = analysisResult.core_view || {};
                    updatedArticle.impact_score = coreView.impact_score ?? analysisResult.impact_score ?? null;
                    updatedArticle.market_bias = (coreView.market_bias || 'neutral').toLowerCase();
                    updatedArticle.signal_bucket = (analysisResult.signal_bucket || 'NOISE').toUpperCase();
                    updatedArticle.executive_summary = analysisResult.executive_summary || '';
                    updatedArticle.decision_trace = analysisResult.decision_trace || {};

                    const event = analysisResult.event || {};
                    updatedArticle.news_category = event.event_type || updatedArticle.news_category || 'general';

                    const score = updatedArticle.impact_score || 0;
                    if (!updatedArticle.news_relevance || updatedArticle.news_relevance === 'None') {
                        updatedArticle.news_relevance = score >= 6 ? 'High' : score >= 3 ? 'Medium' : 'Low';
                    }

                    const stockImpacts = analysisResult.stock_impacts || [];
                    if (stockImpacts.length > 0 && (!updatedArticle.symbols || updatedArticle.symbols.length === 0)) {
                        updatedArticle.symbols = stockImpacts.map(s => s.symbol).filter(Boolean);
                    }
                    if (stockImpacts.length > 0) {
                        updatedArticle.primary_symbol = stockImpacts[0].symbol || null;
                    }
                }

                // Update the card in the DOM immediately
                const existingCard = document.getElementById(`article-card-${newsId}`);
                if (existingCard) {
                    const isFeatured = existingCard.classList.contains('featured-card');
                    const newCard = createNewsCard(updatedArticle, 0, isFeatured);
                    existingCard.innerHTML = newCard.innerHTML;
                    existingCard.className = newCard.className;
                    existingCard.onclick = () => openModal(updatedArticle);
                }

                // Re-open modal with updated article if modal was open
                if (modalOverlay.classList.contains('active')) {
                    openModal(updatedArticle);
                }
            }

            // No need for fetchNews here — WebSocket handles cross-user sync instantly
        } else {
            const isBusy = json.message && json.message.includes('busy');
            if (isBusy) {
                showToast('Server is busy — will auto-retry in 5s', 'info');
                document.querySelectorAll(`.analyze-btn[data-id="${newsId}"]`).forEach(btn => {
                    btn.innerHTML = '⏳ Queued — retrying...';
                });
                // Auto-retry after 5 seconds
                setTimeout(() => {
                    analyzingArticles.delete(newsId);
                    analyzeArticle(newsId, btnEl);
                }, 5000);
                return; // Don't delete from analyzingArticles yet
            }
            showToast('Analysis failed — click to retry', 'error');
            document.querySelectorAll(`.analyze-btn[data-id="${newsId}"]`).forEach(btn => {
                btn.disabled = false;
                btn.innerHTML = '❌ Failed — Retry';
                btn.classList.remove('analyzing');
            });
            console.error('Analysis failed:', json.message);
        }
    } catch (err) {
        showToast('Network error — could not analyze', 'error');
        document.querySelectorAll(`.analyze-btn[data-id="${newsId}"]`).forEach(btn => {
            btn.disabled = false;
            btn.innerHTML = '❌ Error — Retry';
            btn.classList.remove('analyzing');
        });
        console.error('Analysis request error:', err);
    } finally {
        analyzingArticles.delete(newsId);
    }
}

// ---- Modal Logic ----
const modalOverlay = document.getElementById('modalOverlay');
const modalBody = document.getElementById('modalBody');
const modalClose = document.getElementById('modalClose');

// ---- Indian Compact Rendering ----
function renderIndianCompactModal(article, analysis) {
    try {
        if (!analysis) throw new Error("No analysis data available");

        const coreView = analysis.core_view || {};
        const evidenceQuality = analysis.evidence_quality || {};
        const stocks = analysis.stock_impacts || [];
        const sectors = analysis.sector_impacts || [];
        const tradeability = analysis.tradeability || {};
        const impactTriggers = analysis.impact_triggers || {};

        const impactScore = coreView.impact_score || article.impact_score || 0;
        const scoreClass = getScoreClass(impactScore);
        const scoreLabel = getScoreLabel(impactScore);

        const bucket = (analysis.signal_bucket || 'NOISE').toUpperCase();
        const bucketCls = `bucket-${bucket.toLowerCase().replace('_', '-')}`;

        const bias = (coreView.market_bias || 'Neutral').toLowerCase();
        const biasCls = bias === 'bullish' ? 'impact-positive' : bias === 'bearish' ? 'impact-negative' : 'impact-neutral';
        const biasArrow = bias === 'bullish' ? '↑' : bias === 'bearish' ? '↓' : '→';

        // Tradeability Badge
        let tradeCls = 'trade-no-edge';
        let tradeIcon = '⚖️';
        const tradeClass = tradeability.classification || 'no_edge';
        if (tradeClass === 'actionable_now') { tradeCls = 'trade-actionable'; tradeIcon = '⚡'; }
        else if (tradeClass === 'wait_for_confirmation') { tradeCls = 'trade-potential'; tradeIcon = '🔍'; }

        // Render Entities
        const entityChips = [
            ...stocks.map(s => `<span class="entity-tag stock clickable-entity" onclick="event.stopPropagation(); closeModal(); selectChartPair('${escapeHtml(s.symbol)}')">${escapeHtml(s.symbol)}</span>`),
            ...sectors.map(s => `<span class="entity-tag sector">${escapeHtml(s.sector)}</span>`)
        ].join('');

        const modalBodyHtml = `
            <div class="analysis-panel">
                <div class="analysis-panel-header">
                    <div class="analysis-panel-title">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                        Indian Market Intelligence IQ
                    </div>
                    <div class="analysis-panel-badges">
                        ${wrapTooltip(`<span class="signal-bucket-badge ${bucketCls}">${bucket}</span>`, 'signal_bucket', bucket)}
                        ${wrapTooltip(`<span class="tradeability-badge ${tradeCls}">${tradeIcon} ${escapeHtml((tradeClass).replace(/_/g, ' ').toUpperCase())}</span>`, 'tradeability', tradeClass)}
                    </div>
                </div>

                <div class="analysis-score-row">
                    <div class="analysis-score-main">
                        ${wrapTooltip(`<span class="analysis-score-number ${scoreClass}">${impactScore}</span>`, 'impact_score', impactScore <= 1 ? '0-1' : impactScore <= 3 ? '2-3' : impactScore <= 5 ? '4-5' : impactScore <= 7 ? '6-7' : '8-10')}
                        <div class="analysis-score-meta">
                            <span class="analysis-score-label">${scoreLabel}</span>
                            <span class="analysis-score-sub" style="font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">impact score</span>
                        </div>
                    </div>
                    <div class="parent-bias">
                        ${wrapTooltip(`<span class="bias-pill ${biasCls}">${biasArrow} ${coreView.market_bias || 'Neutral'} Bias</span>`, 'market_bias', (coreView.market_bias || 'neutral'))}
                        ${wrapTooltip(`<span style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; font-weight:700; cursor:help;">HORIZON: ${escapeHtml(coreView.horizon || 'short_term').replace(/_/g, ' ').toUpperCase()}</span>`, 'horizon', (coreView.horizon || 'short_term'))}
                    </div>
                </div>

                <div class="analysis-tabs">
                    <button class="analysis-tab active" onclick="switchTab(this, 'tab-ia-overview')">OVERVIEW</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-impacts')">MARKET IMPACTS</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-setup')">TRADE SETUP</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-invalidations')">INVALIDATIONS</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-reasoning')">REASONING</button>
                </div>

                <!-- TAB 1: Overview -->
                <div id="tab-ia-overview" class="analysis-tab-panel active">
                    <div class="analysis-summary-box">
                        <p style="line-height:1.5; color:var(--text-primary); font-weight:500;">${escapeHtml(analysis.executive_summary || coreView.summary || '')}</p>
                    </div>

                    <div class="summary-split-container">
                        <div class="summary-split-box confirmed">
                            <div class="summary-split-title confirmed">✓ Confirmed</div>
                            <ul class="summary-list">
                                ${(evidenceQuality.confirmed || []).map(item => `<li class="summary-item">${escapeHtml(item)}</li>`).join('') || '<li class="summary-item">No specific confirmations.</li>'}
                            </ul>
                        </div>
                        <div class="summary-split-box unknown">
                            <div class="summary-split-title unknown">? Unknown / Risk</div>
                            <ul class="summary-list">
                                ${(evidenceQuality.unknowns_risks || []).map(item => `<li class="summary-item">${escapeHtml(item)}</li>`).join('') || '<li class="summary-item">No major unknowns identified.</li>'}
                            </ul>
                        </div>
                    </div>

                    <div class="analysis-sub-section" style="margin-top:20px;">
                        <div class="analysis-bars-title">Affected Entities</div>
                        <div class="entity-tags-container">${entityChips || '<span style="color:var(--text-muted); font-size:0.75rem;">None identified</span>'}</div>
                    </div>
                </div>

        <!-- TAB 2: Impacts -->
        <div id="tab-ia-impacts" class="analysis-tab-panel">
            <div class="analysis-sub-section">
                <div class="analysis-bars-title">Stock Specific Potential</div>
                <div class="forex-pairs-grid">
                    ${stocks.map(s => `
                        <div class="forex-pair-card" style="border-left: 3px solid ${(s.bias || '').toLowerCase() === 'bullish' ? 'var(--accent-2)' : (s.bias || '').toLowerCase() === 'bearish' ? '#ff4757' : 'var(--text-muted)'}; position: relative;">
                            <button class="chart-link-btn" onclick="event.stopPropagation(); closeModal(); selectChartPair('${escapeHtml(s.symbol)}')" title="View ${escapeHtml(s.symbol)} on Chart"
                                    style="position: absolute; top: 12px; right: 12px; background: rgba(108, 99, 255, 0.15); border: none; border-radius: 6px; width: 28px; height: 28px; color: var(--accent-1); cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s;">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
                            </button>
                            <div class="forex-pair-header">
                                <span class="forex-pair-name">${escapeHtml(s.symbol)}</span>
                                <span class="forex-pair-dir ${(s.bias || '').toLowerCase() === 'bullish' ? 'dir-bullish' : (s.bias || '').toLowerCase() === 'bearish' ? 'dir-bearish' : 'dir-neutral'}">${escapeHtml((s.bias || '').toUpperCase())}</span>
                                <span style="margin-left:auto; font-size:0.7rem; font-weight:700; color:var(--text-muted); margin-right: 32px;">CONF: ${s.confidence}%</span>
                            </div>
                            <p style="font-size:0.85rem; color:var(--text-primary); font-weight:600; margin:8px 0;">${escapeHtml(s.company_name)}</p>
                            <div style="display:flex; gap:12px; font-size:0.75rem; color:var(--text-secondary); margin-bottom:8px;">
                                <span><strong>Reaction:</strong> ${escapeHtml(s.reaction || 'uncertain')}</span>
                                <span><strong>Timing:</strong> ${escapeHtml(s.timing || 'short_term')}</span>
                            </div>
                            <p style="font-size:0.8rem; color:var(--text-secondary); line-height:1.4;">${escapeHtml(s.why || '')}</p>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No direct stock impacts.</p>'}
                        </div>
                    </div>
                    <div class="analysis-sub-section" style="margin-top:24px;">
                        <div class="analysis-bars-title">Sector Wide Impacts</div>
                        <div class="forex-pairs-grid">
                            ${sectors.map(sec => `
                                <div class="forex-pair-card" style="border-left: 3px solid ${(sec.bias || '').toLowerCase() === 'bullish' ? 'var(--accent-2)' : (sec.bias || '').toLowerCase() === 'bearish' ? '#ff4757' : 'var(--text-muted)'}">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name">${escapeHtml(sec.sector)}</span>
                                        <span class="forex-pair-dir ${(sec.bias || '').toLowerCase() === 'bullish' ? 'dir-bullish' : (sec.bias || '').toLowerCase() === 'bearish' ? 'dir-bearish' : 'dir-neutral'}">${escapeHtml((sec.bias || '').toUpperCase())}</span>
                                    </div>
                                    <p style="font-size:0.8rem; color:var(--text-secondary); margin-top:8px; line-height:1.4;">${escapeHtml(sec.why)}</p>

                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No sector impacts.</p>'}
                        </div>
                    </div>
                </div>

                <!-- TAB 3: Setup -->
                <div id="tab-ia-setup" class="analysis-tab-panel">
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title">Tradeability</div>

                        <div style="background:var(--bg-main); border:1px solid var(--border-color); border-radius:10px; padding:16px; margin-top:8px;">
                            
                            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                                ${wrapTooltip(`<span class="tradeability-badge ${tradeCls}" style="font-size:0.8rem;">
                                    ${tradeIcon} ${tradeClass.replace(/_/g, ' ').toUpperCase()}
                                </span>`, 'tradeability', tradeClass)}

                                ${tradeability.remaining_impact_state ? wrapTooltip(`
                                    <span class="impact-state-badge state-${tradeability.remaining_impact_state.replace(/_/g, '-')}" 
                                        style="font-size:0.65rem; padding:4px 10px; border-radius:99px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; background:rgba(255,255,255,0.05); border:1px solid var(--border-color); cursor:help;">
                                        🎯 ${tradeability.remaining_impact_state.replace(/_/g, ' ')}
                                    </span>
                                `, 'remaining_impact', tradeability.remaining_impact_state) : ''}
                            </div>

                            ${tradeability.priced_in_assessment ? `
                            <div style="background:linear-gradient(135deg, rgba(99,102,241,0.08), rgba(139,92,246,0.05)); border:1px solid rgba(99,102,241,0.25); border-radius:10px; padding:14px 16px; margin-bottom:14px;">
                                <div style="font-size:0.7rem; color:#818cf8; font-weight:700; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">
                                    ⏱️ Remaining Impact Assessment
                                </div>
                                <div style="font-size:0.88rem; color:var(--text-primary); line-height:1.55;">
                                    ${escapeHtml(tradeability.priced_in_assessment)}
                                </div>
                            </div>` : ''}

                            <p style="font-size:0.9rem; color:var(--text-primary); line-height:1.5; margin-bottom:12px;">
                                <strong>What to do:</strong> ${escapeHtml(tradeability.what_to_do || 'No trade.')}
                            </p>

                            <p style="font-size:0.85rem; color:var(--text-secondary); line-height:1.5;">
                                <strong>Reason:</strong> ${escapeHtml(tradeability.reason || 'Awaiting further data.')}
                            </p>

                        </div>
                    </div>
                </div>

                <!-- TAB 4: Invalidations -->
                <div id="tab-ia-invalidations" class="analysis-tab-panel">
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title" style="color:#ff6b7a;">Impact Killers (Negate Thesis)</div>
                        <div class="forex-pairs-grid" style="margin-top:12px;">
                            ${(impactTriggers.impact_killers || []).map(k => `
                                <div class="forex-pair-card" style="border-left: 3px solid #ff4757; background: rgba(255, 71, 87, 0.03);">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name" style="color:#ff6b7a;">${escapeHtml(k.trigger)}</span>
                                    </div>
                                    <div style="font-size:0.85rem; color:var(--text-primary); margin:8px 0; line-height:1.4;">
                                        ${escapeHtml(k.why || '')}
                                    </div>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No impact killers identified.</p>'}
                        </div>
                    </div>
                    <div class="analysis-sub-section" style="margin-top:24px;">
                        <div class="analysis-bars-title" style="color:var(--accent-2);">Impact Amplifiers (Strengthen Thesis)</div>
                        <div class="forex-pairs-grid" style="margin-top:12px;">
                            ${(impactTriggers.impact_amplifiers || []).map(a => `
                                <div class="forex-pair-card" style="border-left: 3px solid var(--accent-2); background: rgba(0, 212, 170, 0.03);">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name" style="color:var(--accent-2);">${escapeHtml(a.trigger)}</span>
                                    </div>
                                    <div style="font-size:0.85rem; color:var(--text-primary); margin:8px 0; line-height:1.4;">
                                        ${escapeHtml(a.why || '')}
                                    </div>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No amplifiers identified.</p>'}
                        </div>
                    </div>
                </div>

                <!-- TAB 5: Reasoning -->
                <div id="tab-ia-reasoning" class="analysis-tab-panel">
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title" style="color:var(--accent-1);">Agent Decision Trace</div>
                        <div class="reasoning-trace-container" style="margin-top:12px; display:flex; flex-direction:column; gap:12px;">
                            ${(() => {
                const dt = parseJsonField(article.decision_trace) || analysis.decision_trace || {};
                const steps = [
                    { key: 'event_identification', label: '1. Event Identification', icon: '🔍' },
                    { key: 'entity_mapping', label: '2. Entity Mapping', icon: '🎯' },
                    { key: 'impact_scoring', label: '3. Impact Scoring', icon: '⚡' },
                    { key: 'remaining_impact', label: '4. Remaining Impact', icon: '⏱️' },
                    { key: 'tradeability_reasoning', label: '5. Tradeability Reasoning', icon: '⚖️' }
                ];
                return steps.map(step => `
                                    <div class="reasoning-step-card" style="background:rgba(255,255,255,0.03); border:1px solid var(--border-color); border-radius:8px; padding:12px;">
                                        <div style="font-size:0.75rem; color:var(--accent-1); font-weight:700; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
                                            <span>${step.icon}</span> ${step.label.toUpperCase()}
                                        </div>
                                        <div style="font-size:0.88rem; color:var(--text-secondary); line-height:1.5;">${escapeHtml(dt[step.key] || 'No log for this step.')}</div>
                                    </div>
                                `).join('');
            })()}
                        </div>
                    </div>
                </div>
            </div>
        `;

        modalBody.innerHTML = `
            ${article.image_url ? `<img class="modal-image" src="${escapeHtml(article.image_url)}" alt="" onerror="this.style.display='none'">` : ''}
            <div class="card-header-row" style="margin-bottom: 12px; margin-top: 8px;">
                <div class="card-header-left">
                    ${renderRelevanceBadge(article.news_relevance, true)}
                    ${renderCategoryBadge(article.news_category, true)}
                </div>
                <div class="card-header-right">
                    <span class="card-time">${formatTime(article.published)}</span>
                </div>
            </div>
            
            <h2 class="modal-title">${escapeHtml(article.title)}</h2>
            
            <div class="modal-timestamps-premium" style="margin-bottom: 24px; margin-top: 20px;">
                <div class="ts-row"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</div>
                <div class="ts-row"><strong>We Posted:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</div>
            </div>

            <div class="modal-description" style="margin-bottom: 24px;">${escapeHtml(article.description || '')}</div>
            
            ${modalBodyHtml}

            <div class="modal-action-footer" style="margin-top: 32px; display: flex; flex-direction: column; gap: 12px;">
                ${(() => {
                    const isAnalyzing = analyzingArticles.has(article.id);
                    const btnState = isAnalyzing ? 'disabled' : '';
                    const btnClass = isAnalyzing ? 'analyzing' : '';
                    const btnText = isAnalyzing ? '<div class="analyzing-spinner-sm"></div> Analyzing…' : 'Analyze';
                    
                    return `
                        <button class="analyze-btn analyze-btn-sm ${btnClass}" 
                                style="display:inline-flex; justify-content:center; align-items:center; padding:10px 22px; border-radius:24px; font-weight:700; background:rgba(108, 99, 255, 0.12); border:1px solid rgba(108, 99, 255, 0.3); color:var(--accent-1); cursor:pointer; max-width:180px; width:auto; margin:0 auto; transition:all 0.2s;"
                                data-id="${article.id}" ${btnState} 
                                onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">
                            ${btnText}
                        </button>
                    `;
                })()}

                <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn card-read-btn" style="text-align:center; padding:16px; border-radius:12px; font-weight:700; background:linear-gradient(90deg, #6366f1, #00d4aa); color:#fff; text-decoration:none; display:block; border:none; width:100%; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.25);">
                    Read Full Article →
                </a>
            </div>
        `;

        const modalEl = modalOverlay.querySelector('.modal');
        modalEl.classList.add('modal-expanded');
        modalOverlay.classList.add('active');
        document.body.style.overflow = 'hidden';

    } catch (err) {
        console.error("Error rendering Indian compact modal:", err);
        modalBody.innerHTML = `<div style="padding:20px; color:var(--bearish); background:rgba(255,71,87,0.05); border:1px solid var(--bearish); border-radius:8px;">
            <h3 style="margin-top:0">⚠️ Rendering Error</h3>
            <p>Failed to display analysis details. The data might be malformed or missing required fields.</p>
            <pre style="font-size:0.75rem; white-space:pre-wrap; margin-top:10px; opacity:0.8;">${err.message}</pre>
        </div>`;
    }
}


/**
 * Switch between tabs in the analysis panel.
 * @param {HTMLElement} btn - The button element that was clicked.
 * @param {string} tabId - The ID of the tab panel to display.
 */
function switchTab(btn, tabId) {
    // 1. Get the container (analysis-panel)
    const container = btn.closest('.analysis-panel');
    if (!container) return;

    // 2. Update button states
    const tabs = container.querySelectorAll('.analysis-tab');
    tabs.forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    // 3. Update panel visibility
    const panels = container.querySelectorAll('.analysis-tab-panel');
    panels.forEach(p => p.classList.remove('active'));

    const activePanel = container.querySelector(`#${tabId}`);
    if (activePanel) {
        activePanel.classList.add('active');
    }
}



function getAnalysisData(article) {
    if (!article || !article.analysis_data) return null;
    let data = article.analysis_data;
    if (typeof data === 'string') {
        try {
            data = JSON.parse(data);
        } catch (e) {
            console.error("Failed to parse analysis_data:", e);
            return null;
        }
    }
    return data;
}

function openModal(article) {
    // Try full JSONB analysis_data first
    let analysis = getAnalysisData(article);

    // Detect if this is the new Indian Compact Schema
    const isIndianCompact = analysis && (analysis.core_view !== undefined || analysis.stock_impacts !== undefined);

    if (isIndianCompact) {
        renderIndianCompactModal(article, analysis);
        return;
    }

    const isAnalyzing = analyzingArticles.has(article.id);
    const btnState = isAnalyzing ? 'disabled' : '';
    const btnClass = isAnalyzing ? 'analyzing' : '';
    
    let btnText = 'Analyze';
    if (isAnalyzing) {
        btnText = '<div class="analyzing-spinner-sm"></div> Analyzing...';
    }

    // Match "IA Flat Display" / "Intelligence Section" from Image 6
    let classificationHtml = '';
    if (article.news_relevance && article.news_relevance !== 'None') {
        const imp = (article.news_impact_level || 'Neutral').toLowerCase();
        let css = 'impact-neutral';
        if (imp === 'positive') css = 'impact-positive';
        if (imp === 'negative') css = 'impact-negative';

        classificationHtml = `
            <div class="ia-flat-display" style="margin-top:20px; border-top:1px solid var(--border); padding-top:20px;">
                <span class="impact-tag ${css}" style="margin-bottom:12px; display:inline-block;">📊 ${article.news_impact_level || 'Neutral'} Impact</span>
                <div class="ia-reason-box" style="border-left:4px solid #6c63ff; background:rgba(108, 99, 255, 0.05); padding:16px; border-radius:0 8px 8px 0;">
                    <div class="reason-label" style="color:#6c63ff; font-weight:800; text-transform:uppercase; font-size:0.7rem; margin-bottom:8px; opacity:0.8;">Market Intelligence Reason</div>
                    <div class="ia-reason-text" style="font-size:0.9rem; line-height:1.5; color:var(--text-secondary);">${escapeHtml(article.news_reason || 'Analysis details not available.')}</div>
                </div>
            </div>
        `;
    }

    let descriptionHtml = article.description ? `<p class="modal-description" style="font-size:1.05rem; line-height:1.6; color:var(--text-primary); margin-bottom:24px;">${escapeHtml(article.description)}</p>` : '';

    modalBody.innerHTML = `
        ${article.image_url ? `<img class="modal-image" src="${escapeHtml(article.image_url)}" alt="" onerror="this.style.display='none'">` : ''}
        
        <div class="modal-header-top">
            <div class="modal-badges-row" style="display:flex; gap:8px;">
                ${renderRelevanceBadge(article.news_relevance)}
                ${renderCategoryBadge(article.news_category)}
            </div>
            <span class="card-source" style="color:#00d4aa; font-weight:700; margin-left:auto; text-transform:uppercase; font-size:0.8rem;">• ${escapeHtml(article.source || 'Unknown')}</span>
        </div>
        <h2 class="modal-title">${escapeHtml(article.title)}</h2>
        <div class="modal-timestamps">
            <span class="modal-timestamp-line"><strong>Source Posted:</strong> ${timeAgo(article.published > article.created_at ? article.created_at : article.published)} · ${formatTime(article.published > article.created_at ? article.created_at : article.published)}</span>
            <span class="modal-timestamp-line"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</span>
        </div>

        ${descriptionHtml}

        ${(Array.isArray(article.symbols) && article.symbols.length > 0) ? `
                    <div class="modal-affected-stocks" style="display:flex; align-items:center; gap:10px; margin-bottom: 8px;">
                        <span style="font-size:0.6rem; color:var(--text-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.8px; white-space:nowrap;">Affected Stocks:</span>
                        <div style="display:flex; gap:6px; flex-wrap:wrap; justify-content:center;">
                            ${renderAllSymbolsBadge(article.symbols)}
                        </div>
                    </div>
                    ` : ''}
        
        ${classificationHtml}
        
        

        <div class="modal-action-footer" style="margin-top:32px; display:flex; flex-direction:column; gap:16px;">
            <div class="modal-analyze-center" style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:12px; padding:12px 0;">
                <button class="analyze-btn analyze-btn-sm ${btnClass}" style="padding:8px 22px; font-size:0.92rem; border-radius:99px; font-weight:700; width:auto; min-width:120px;" data-id="${article.id}" ${btnState} onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">
                    ${btnText}
                </button>
            </div>
            <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn" style="text-align:center; padding:16px; border-radius:12px; font-weight:700; background:linear-gradient(90deg, #6366f1, #00d4aa); color:#fff; text-decoration:none;">
                Read Full Article →
            </a>
        </div>
    `;

    const modalEl = modalOverlay.querySelector('.modal');
    modalEl.classList.remove('modal-expanded');

    modalOverlay.classList.add('active');
    document.body.style.overflow = 'hidden';
}

async function fetchPredictionsForModal(newsId) {
    try {
        const predTabBtn = document.getElementById('predTabBtn');
        const loader = document.getElementById('predictionsLoader');
        const content = document.getElementById('predictionsContent');
        if (!predTabBtn || !loader || !content) return;

        const res = await apiFetch(`${API_BASE}/api/predictions?news_id=${newsId}`);
        const json = await res.json();

        loader.style.display = 'none';

        // Formatting helpers
        const _symMap = {
            "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
            "XRP-USD": "Ripple", "ADA-USD": "Cardano", "DOGE-USD": "Dogecoin",
            "AVAX-USD": "Avalanche", "LINK-USD": "Chainlink", "DOT-USD": "Polkadot",
            "LTC-USD": "Litecoin", "UNI-USD": "Uniswap", "SHIB-USD": "Shiba Inu",
            "MATIC-USD": "Polygon",
            "EURUSD=X": "EUR/USD", "USDJPY=X": "USD/JPY", "GBPUSD=X": "GBP/USD",
            "USDCHF=X": "USD/CHF", "AUDUSD=X": "AUD/USD", "USDCAD=X": "USD/CAD",
            "NZDUSD=X": "NZD/USD", "DX-Y.NYB": "US Dollar Index (DXY)",
            "GC=F": "Gold", "CL=F": "Crude Oil", "SI=F": "Silver",
            "^GSPC": "S&P 500", "NQ=F": "NASDAQ", "^DJI": "Dow Jones",
            "^N225": "Nikkei 225", "^GDAXI": "DAX", "^FTSE": "FTSE 100"
        };
        const formatSymbol = sym => _symMap[sym] || sym;
        const formatPrice = p => {
            if (!p) return '0.00';
            const v = parseFloat(p);
            if (v < 0.01) return v.toFixed(6);
            if (v < 1) return v.toFixed(4);
            return v.toFixed(2);
        };

        if (json.status === 'success' && json.data.length > 0) {
            predTabBtn.style.display = 'inline-block';

            let html = '<div class="predictions-list">';
            json.data.forEach(p => {
                const status = (p.status || 'pending').toLowerCase();
                const dir = (p.direction || 'Neutral').toLowerCase();
                const isBull = dir === 'bullish' || dir === 'positive' || dir === 'up';
                const isBear = dir === 'bearish' || dir === 'negative' || dir === 'down';
                const dirEmoji = isBull ? '📈' : (isBear ? '📉' : '➖');
                const dirColor = isBull ? 'var(--bullish)' : (isBear ? 'var(--bearish)' : 'var(--text-muted)');

                // Map status to our new classes
                const statusCls = status === 'expired' ? 'missed'
                    : status === 'wrong' ? 'missed'
                        : status === 'overperformed' ? 'underrated'
                            : status === 'underperformed' ? 'overstated'
                                : status;

                const finalMove = p.final_move_pct != null ? parseFloat(p.final_move_pct).toFixed(2) : (p.last_move_pct != null ? parseFloat(p.last_move_pct).toFixed(2) : '0.00');
                const mfeRaw = p.mfe_pct != null ? parseFloat(p.mfe_pct) : 0;

                // MFE & Target Signs
                const mfeSign = isBear ? -1 : 1;
                const mfeDisplay = (mfeSign * mfeRaw).toFixed(2);
                const mfePrefix = mfeSign * mfeRaw > 0 ? '+' : '';

                const targetPctRaw = p.predicted_move_pct ? parseFloat(p.predicted_move_pct) : 0;
                let targetNum = isBear ? -targetPctRaw : targetPctRaw; // Real % move
                const targetDisplay = (isBear ? '-' : '+') + targetPctRaw;

                // Absolute colors
                const curPct = parseFloat(finalMove);
                const moveColor = curPct > 0 ? 'var(--bullish)' : (curPct < 0 ? 'var(--bearish)' : 'var(--text-muted)');
                const mfeColor = mfeSign * mfeRaw > 0 ? 'var(--bullish)' : (mfeSign * mfeRaw < 0 ? 'var(--bearish)' : 'inherit');
                const barColor = isBull ? 'var(--bullish)' : isBear ? 'var(--bearish)' : 'var(--text-muted)';
                const biasBorderColor = isBull ? 'rgba(0, 212, 170, 0.4)' : isBear ? 'rgba(255, 71, 87, 0.4)' : 'rgba(255, 193, 7, 0.4)';
                const dirCls = isBull ? 'dir-bullish' : isBear ? 'dir-bearish' : 'dir-neutral';

                // Target Price Logic
                const startPriceFloat = parseFloat(p.start_price || 0);
                const currentPriceFloat = parseFloat(p.final_price || p.last_price || p.start_price);
                const targetPriceFloat = startPriceFloat * (1 + (targetNum / 100));

                // Progress to Target (0 to 100%) - based on max favorable
                let mfeProgressPct = 0;
                if (targetPctRaw > 0) {
                    mfeProgressPct = Math.max(0, Math.min(100, (mfeRaw / targetPctRaw) * 100));
                }

                html += `
                    <div class="forex-pair-card" style="border-left-color:${biasBorderColor}; margin-bottom: 24px; padding: 18px; border-radius: 8px; box-shadow: 0 4px 14px rgba(0,0,0,0.15); background: var(--surface-light);">
                        
                        <!-- Header -->
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div>
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span class="forex-pair-name" style="font-size:1.15rem; font-weight:700;">${escapeHtml(p.asset_display_name || formatSymbol(p.asset))}</span>
                                    ${p.direction ? `<span class="forex-pair-dir ${dirCls}" style="padding:2px 8px; font-size:0.65rem; border-radius:12px;">${escapeHtml(p.direction.toUpperCase())}</span>` : ''}
                                </div>
                                <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 6px;">
                                    Duration: <strong style="color: var(--text-secondary);">${escapeHtml(p.expected_duration_label)}</strong>
                                </div>
                            </div>
                            <span class="pred-status-full pred-status-${statusCls}" style="padding: 4px 12px; font-size:0.75rem;">${(statusCls).toUpperCase()}</span>
                        </div>

                        <!-- 3-Column Stats Grid -->
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 24px; padding: 16px; background: rgba(0,0,0,0.15); border-radius: 8px; border: 1px solid rgba(255,255,255,0.03);">
                            <div>
                                <div style="font-size:0.65rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom: 4px;">Start Price</div>
                                <div style="font-family:'JetBrains Mono',monospace; font-size:1rem; font-weight:700; color:var(--text-primary);">$${formatPrice(startPriceFloat)}</div>
                                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">Entry</div>
                            </div>
                            <div>
                                <div style="font-size:0.65rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom: 4px;">${status === 'pending' ? 'Current Price' : 'Final Price'}</div>
                                <div style="font-family:'JetBrains Mono',monospace; font-size:1rem; font-weight:700; color:var(--text-primary);">$${formatPrice(currentPriceFloat)}</div>
                                <div style="font-size: 0.75rem; color: ${moveColor}; margin-top: 4px; font-weight: 600;">${curPct > 0 ? '+' : ''}${finalMove}%</div>
                            </div>
                            <div>
                                <div style="font-size:0.65rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom: 4px;">Target Price</div>
                                <div style="font-family:'JetBrains Mono',monospace; font-size:1rem; font-weight:700; color:${barColor};">$${formatPrice(targetPriceFloat)}</div>
                                <div style="font-size: 0.75rem; color: ${barColor}; margin-top: 4px; font-weight: 600;">${targetDisplay}%</div>
                            </div>
                        </div>

                        <!-- Max Favorable Progress Bar -->
                        <div style="margin-top: 24px;">
                            <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 8px;">
                                <div style="font-size: 0.8rem; color: var(--text-secondary);">
                                    Max Favorable Excursion <span style="color: var(--text-muted); font-size: 0.7rem; margin-left:4px;">(Best Outcome)</span>
                                </div>
                                <div style="font-family:'JetBrains Mono',monospace; font-size: 1.05rem; font-weight: 700; color: ${mfeColor};">
                                    ${mfePrefix}${mfeDisplay}%
                                </div>
                            </div>
                            
                            <div style="position: relative; height: 8px; background: rgba(255,255,255,0.06); border-radius: 4px; overflow: hidden; box-shadow: inset 0 1px 3px rgba(0,0,0,0.3);">
                                <div style="position: absolute; left: 0; top: 0; height: 100%; width: ${mfeProgressPct}%; background: ${barColor}; opacity: ${mfeProgressPct >= 100 ? '1' : '0.7'}; border-radius: 4px;"></div>
                            </div>
                            <div style="display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.7rem; color: var(--text-muted);">
                                <span>0%</span>
                                <span>Progress strictly towards Target (${targetDisplay}%)</span>
                                <span>100%</span>
                            </div>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            content.innerHTML = html;

            // ALSO: Populate inline trackers in the Directional Bias tab
            json.data.forEach(p => {
                const encodedAsset = encodeURIComponent(p.asset);
                const inlineBox = document.getElementById(`inline-pred-${newsId}-${encodedAsset}`);
                // Try fallback logic if names don't match perfectly
                let targetBox = inlineBox;
                if (!targetBox) {
                    // Try to find a matching prefix (e.g., 'bitcoin' vs 'BTC-USD')
                    const allBoxes = document.querySelectorAll(`[id^='inline-pred-${newsId}-']`);
                    for (const box of allBoxes) {
                        const boxId = decodeURIComponent(box.id);
                        if (boxId.toLowerCase().includes(p.asset.toLowerCase()) || p.asset.toLowerCase().includes(boxId.replace(`inline-pred-${newsId}-`, '').toLowerCase())) {
                            targetBox = box;
                            break;
                        }
                    }
                }

                if (targetBox) {
                    const status = (p.status || 'pending').toLowerCase();
                    const statusCap = status.toUpperCase();
                    const finalMove = p.final_move_pct != null ? p.final_move_pct.toFixed(2) : (p.last_move_pct != null ? p.last_move_pct.toFixed(2) : '0.00');
                    const mfeRaw = p.mfe_pct != null ? parseFloat(p.mfe_pct) : 0;
                    const _dir = (p.direction || '').toLowerCase();
                    const _isBear = _dir === 'bearish' || _dir === 'negative' || _dir === 'down';
                    const _isBull = _dir === 'bullish' || _dir === 'positive' || _dir === 'up';
                    const mfeSign = _isBear ? -1 : 1;
                    const mfeDisplay = (mfeSign * mfeRaw).toFixed(2);
                    const mfeColor = _isBull ? 'var(--bullish)' : _isBear ? 'var(--bearish)' : 'inherit';
                    const mfePrefix = mfeSign > 0 ? '+' : '';
                    const curPrice = p.final_price || p.last_price || p.start_price;

                    targetBox.innerHTML = `
                        <div style="background:var(--bg-main); border:1px solid var(--border-color); border-radius:6px; padding:8px 10px;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span style="font-size:0.75rem; color:var(--text-muted); font-weight:600;">LIVE TRACKER <span style="font-size:0.65rem; color:var(--text-secondary);">(${p.asset_display_name || formatSymbol(p.asset)})</span></span>
                                <span class="pred-status-full pred-status-${statusCls}" style="font-size:0.6rem; padding:2px 6px;">${statusCap}</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; margin-top:8px; font-family:'JetBrains Mono', monospace; font-size:0.85rem;">
                                <div>
                                    <div style="color:var(--text-muted); font-size:0.65rem;">START</div>
                                    <div>$${formatPrice(p.start_price)}</div>
                                </div>
                                <div style="text-align:right;">
                                    <div style="color:var(--text-muted); font-size:0.65rem;">CURRENT</div>
                                    <div>$${formatPrice(curPrice)}</div>
                                </div>
                            </div>
                            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px; padding-top:8px; border-top:1px dashed var(--border-color);">
                                <div style="display:flex; flex-direction:column; gap:2px;">
                                    <span style="font-size:0.65rem; color:var(--text-muted);">ACTUAL MOVE</span>
                                    <span style="font-family:'JetBrains Mono', monospace; font-size:0.85rem; color:${parseFloat(finalMove) > 0 ? 'var(--bullish)' : parseFloat(finalMove) < 0 ? 'var(--bearish)' : 'inherit'}; font-weight:700;">${finalMove}%</span>
                                </div>
                                <div style="display:flex; flex-direction:column; gap:2px; text-align:right;">
                                    <span style="font-size:0.65rem; color:var(--text-muted);">MAX FAVORABLE</span>
                                    <span style="font-family:'JetBrains Mono', monospace; font-size:0.85rem; color:${mfeColor}; font-weight:700;">${mfePrefix}${mfeDisplay}%</span>
                                </div>
                            </div>
                        </div>
                    `;
                }
            });

        } else {
            content.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem; padding:12px 0;">No predictions tracked for this article.</p>';
        }
    } catch (err) {
        console.error('Failed to load predictions:', err);
    }
}



function closeModal() {
    modalOverlay.classList.remove('active');
    const modalEl = modalOverlay.querySelector('.modal');
    if (modalEl) modalEl.classList.remove('modal-expanded');
    document.body.style.overflow = '';
}

modalClose.addEventListener('click', closeModal);
modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
});

// ---- Card Timestamp Rendering ----
function renderCardTimestamps(article) {
    // Ensure logical display: Source Posted cannot be after Scraped
    const pubTime = article.published > article.created_at ? article.created_at : article.published;

    return `
        <div class="card-timestamps">
            <span class="card-timestamp-line"><strong>Source Posted:</strong> ${timeAgo(pubTime)} · ${formatTime(pubTime)}</span>
            <span class="card-timestamp-line"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</span>
        </div>
    `;
}


function getCategoryGradient(category) {
    const gradients = {
        corporate_event: 'linear-gradient(135deg, #0f172a 0%, #1e3a8a 45%, #3b82f6 100%)',
        government_policy: 'linear-gradient(135deg, #312e81 0%, #c2410c 45%, #f59e0b 100%)',
        macro_data: 'linear-gradient(135deg, #1e3a8a 0%, #7c3aed 45%, #d946ef 100%)',
        default: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.85) 100%)'
    };
    const key = (category || '').toString().trim().toLowerCase();
    return gradients[key] || gradients.default;
}

function renderNewsMedia(article) {
    const hasImage = article.image_url && article.image_url.toString().trim().length > 0;
    const safeSource = escapeHtml(article.source || 'Unknown');
    const safeTitle = escapeHtml(article.title || 'Untitled headline');
    const fallbackGradient = getCategoryGradient(article.news_category);

    return `
        <div class="card-image ${hasImage ? '' : 'missing-image'}">
            ${hasImage ? `<img class="card-media-img" src="${escapeHtml(article.image_url)}" alt="" onload="this.parentElement.classList.remove('missing-image');" onerror="this.style.display='none'; this.parentElement.classList.add('missing-image');">` : ''}
            <div class="card-media-fallback" style="background: ${fallbackGradient};">
                <div class="fallback-overlay"></div>
                <span class="fallback-source-badge">${safeSource}</span>
                <div class="fallback-title">${safeTitle}</div>
            </div>
        </div>`;
}

// ---- Render News Card ----
function createNewsCard(article, index, isFeatured = false) {
    const card = document.createElement('div');
    card.id = `article-card-${article.id}`;
    card.className = isFeatured ? 'news-card featured-card' : 'news-card';
    card.style.animationDelay = `${index * 0.05}s`;

    const imageHtml = renderNewsMedia(article);

    const featuredBadge = isFeatured ? `<span class="featured-type-badge">${article.featuredType}</span>` : '';

    card.innerHTML = `
        ${imageHtml}
        <div class="card-header-row">
            <div class="card-header-left">
                ${renderImpactBadge(article)}
                ${renderRelevanceBadge(article.news_relevance)}
                ${renderCategoryBadge(article.news_category)}
                ${featuredBadge}
            </div>
            <span class="card-source">${escapeHtml(article.source || 'Unknown')}</span>
        </div>
        
        <h2 class="card-title">${escapeHtml(article.title)}</h2>
        ${article.description ? `<p class="card-description">${escapeHtml(article.description)}</p>` : ''}
        
        ${(Array.isArray(article.symbols) && article.symbols.length > 0) ? `
        <div class="card-affected-stocks" style="display:block; padding:10px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); align-items:center; gap:10px; margin: 16px 0 12px 0;">
            <span style="font-size:0.6rem; color:var(--text-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.8px; white-space:nowrap;">Affected Stocks:</span><br>
            <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px;">
                ${renderAllSymbolsBadge(article.symbols)}
            </div>
        </div>
        ` : ''}

        <hr style="border:0; border-top:1px solid var(--border-color); margin:12px 0; opacity:0.15;">
        
        <div class="card-footer" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
            <div class="card-timestamps-premium" style="margin:0;">
                <div class="ts-row" style="font-size:0.65rem;"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</div>
                <div class="ts-row" style="font-size:0.65rem;"><strong>We Posted:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</div>
                ${article.analyzed_at ? `<div class="ts-row" style="font-size:0.65rem;"><strong>Analyzed:</strong> ${timeAgo(article.analyzed_at)} · ${formatTime(article.analyzed_at)}</div>` : ''}
            </div>
            <div class="card-footer-right">
                ${renderAnalyzeButton(article)}
            </div>
        </div>
        
        <div class="card-action-row" style="margin-top:auto;">
            <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-now-btn-premium" onclick="event.stopPropagation()" 
               style="display:flex; align-items:center; justify-content:center; width:100%; padding:12px; border-radius:12px; background:linear-gradient(135deg, #6c63ff, #00d4aa); color:white; font-weight:800; text-decoration:none; transition:all 0.3s; box-shadow: 0 4px 15px rgba(108, 99, 255, 0.2);">
                Read Now →
            </a>
        </div>
    `;

    card.onclick = () => openModal(article);
    return card;
}

// ---- Render News (supports Prepend, Append, and Full Refresh) ----
function renderNews(articles, prepend = false, append = false) {
    if (!prepend && !append) {
        newsGrid.innerHTML = '';
        featuredGrid.innerHTML = '';
        featuredSection.style.display = 'none';
        allNewsHeader.style.display = 'none';

        if (!articles || articles.length === 0) {
            emptyState.style.display = 'flex';
            newsGrid.style.display = 'none';
            if (searchQuery) {
                emptyStateTitle.textContent = `No results for "${searchQuery}"`;
                emptyStateMsg.textContent = 'Try a different search term or clear the filter.';
            } else {
                emptyStateTitle.textContent = 'No articles yet';
                emptyStateMsg.textContent = 'The monitor is fetching news. Articles will appear here automatically.';
            }
            return;
        }
    }

    if (!articles || articles.length === 0) return;

    emptyState.style.display = 'none';
    newsGrid.style.display = 'grid';

    // Update article count
    if (!prepend && !append) {
        updateDisplayCounts();
    }

    // Always render into newsGrid - relevance banner above communicates the active filter visually
    const targetGrid = newsGrid;

    // Handle Featured Articles (ONLY on the first page/initial load/refresh when not searching)
    if (!prepend && !append && !searchQuery && currentDashboardView === 'feed') {
        let regularArticles = [...articles];
        let featured = [];

        // 1. Find Latest (Scraped within last 20 minutes)
        const TWENTY_MINS_MS = 20 * 60 * 1000;
        const now = Date.now();

        for (let i = regularArticles.length - 1; i >= 0; i--) {
            const article = regularArticles[i];
            if (article.created_at) {
                const scrapedTime = new Date(article.created_at).getTime();
                if (now - scrapedTime <= TWENTY_MINS_MS) {
                    const latest = regularArticles.splice(i, 1)[0];
                    latest.featuredType = '🆕 Latest News';
                    featured.unshift(latest);
                }
            }
        }

        // 2. Find Most Impacted from remaining
        let mostImpactedIdx = -1;
        let highestScore = 3;

        regularArticles.forEach((a, idx) => {
            if (a.impact_score && a.impact_score > highestScore) {
                highestScore = a.impact_score;
                mostImpactedIdx = idx;
            }
        });

        if (mostImpactedIdx !== -1) {
            const mostImpacted = regularArticles.splice(mostImpactedIdx, 1)[0];
            mostImpacted.featuredType = '🔥 Most Impacted';
            featured.unshift(mostImpacted);
        }

        if (featured.length > 0) {
            featuredSection.style.display = 'block';
            allNewsHeader.style.display = 'block';
            featured.forEach((art, idx) => {
                featuredGrid.appendChild(createNewsCard(art, idx, true));
            });
        }

        // Render the remaining regular articles
        regularArticles.forEach((article, index) => {
            targetGrid.appendChild(createNewsCard(article, index));
        });
    } else {
        // Standard Prepended or Appended rendering (for load more and search results)
        if (prepend) {
            for (let i = articles.length - 1; i >= 0; i--) {
                const card = createNewsCard(articles[i], i);
                card.style.animation = 'none'; // skip animation for silent auto-updates
                targetGrid.insertBefore(card, targetGrid.firstChild);
            }
        } else {
            articles.forEach((article, index) => {
                const card = createNewsCard(article, index);
                targetGrid.appendChild(card);
            });
        }
    }
}

// ---- Fetch Sources ----
async function fetchSources() {
    try {
        const res = await apiFetch(`${API_BASE}/api/indian_sources`);
        const json = await res.json();

        if (json.status === 'success') {
            // Keep the "All Sources" button
            const allBtn = filtersContainer.querySelector('[data-source="all"]');

            // Remove all other pills
            const existingPills = filtersContainer.querySelectorAll('.filter-pill:not([data-source="all"])');
            existingPills.forEach(p => p.remove());

            // Add new pills from API
            if (Array.isArray(json.data)) {
                json.data.forEach(source => {
                    const pill = document.createElement('button');
                    pill.className = 'filter-pill';
                    pill.dataset.source = source;
                    pill.textContent = source;
                    pill.setAttribute('role', 'tab');
                    pill.setAttribute('aria-selected', source === currentSource ? 'true' : 'false');
                    if (source === currentSource) pill.classList.add('active');
                    filtersContainer.appendChild(pill);
                });
            }

            // Ensure "All Sources" is active if currentSource is 'all'
            if (currentSource === 'all' && allBtn) {
                allBtn.classList.add('active');
                allBtn.setAttribute('aria-selected', 'true');
            }
        }
    } catch (err) {
        console.error('Failed to fetch sources:', err);
    }
}

function formatSymbol(sym) {
    if (!sym) return '';
    sym = sym.toUpperCase();

    // Hardcoded common indices/commodities
    const friendlyNames = {
        'GC=F': 'Gold',
        'SI=F': 'Silver',
        'CL=F': 'Crude Oil',
        'NG=F': 'Natural Gas',
        '^GSPC': 'S&P 500',
        '^DJI': 'Dow Jones',
        '^IXIC': 'Nasdaq',
        '^NDX': 'Nasdaq 100',
        '^RUT': 'Russell 2000',
        '^FTSE': 'FTSE 100',
        '^N225': 'Nikkei 225',
        'BTC-USD': 'Bitcoin',
        'ETH-USD': 'Ethereum',
        'SOL-USD': 'Solana',
        'DX-Y.NYB': 'US Dollar Index',
        'ZN=F': '10-Year T-Note'
    };

    if (friendlyNames[sym]) {
        return friendlyNames[sym];
    }

    // Try stripping suffixes for Forex / Crypto if no exact match
    sym = sym.trim();
    if (sym.endsWith('=X')) {
        let pair = sym.replace('=X', '').trim();
        // e.g. USDCNY -> USD/CNY
        if (pair.length === 6) {
            return `${pair.substring(0, 3)}/${pair.substring(3, 6)}`;
        }
        return pair;
    }

    if (sym.endsWith('-USD')) {
        return sym.replace('-USD', '');
    }

    if (sym.endsWith('=F')) {
        return sym.replace('=F', ' Futures');
    }

    return sym;
}

// ---- Fetch News (with Pagination & Smart Refresh) ----
async function fetchNews(isLoadMore = false, isBackgroundRefresh = false) {
    // --- Guard logic via state machine ---
    if (isLoadMore && !hasMoreArticles) return;

    if (requestState !== 'idle' && (isLoadMore || isBackgroundRefresh)) {
        dbg('fetchNews skipped (busy):', { requestState, isLoadMore, isBackgroundRefresh });
        return;
    }

    // --- Generational ID: prevents an aborted call's finally from stomping a newer call ---
    // Increment and capture THIS call's generation number.
    const myGeneration = ++_fetchGeneration;

    // Determine the new state
    const newState = isLoadMore ? 'loadmore' : isBackgroundRefresh ? 'background' : 'loading';
    dbg('fetchNews start:', { myGeneration, newState, currentSource, currentRelevance, searchQuery });

    // --- UI feedback for filter/initial loads ---
    if (newState === 'loading') {
        showSkeletonLoader();
        showRefreshIndicator();
    }

    if (newState === 'loadmore') {
        const indicator = document.getElementById('loadMoreIndicator');
        if (indicator) indicator.style.display = 'flex';
    }

    // Abort any previous in-flight fetch cleanly
    if (_fetchAbortController) {
        dbg('fetchNews aborting previous request');
        _fetchAbortController.abort();
        _fetchAbortController = null;
    }

    requestState = newState;
    _fetchAbortController = new AbortController();
    const signal = _fetchAbortController.signal;

    try {
        const offset = isLoadMore ? (currentPage + 1) * articlesPerPage : 0;
        const fetchOffset = isBackgroundRefresh ? 0 : offset;
        const fetchLimit = isBackgroundRefresh ? 20 : articlesPerPage;

        let url = `${API_BASE}/api/indian_news?today_only=false&limit=${fetchLimit}&offset=${fetchOffset}`;

        if (currentSource && currentSource !== 'all') {
            url += `&source=${encodeURIComponent(currentSource)}`;
        }

        // FIX 1: Use the canonical (DB-casing) value, never the raw pill attribute
        const canonicalRelevance = RELEVANCE_CANONICAL[currentRelevance];
        if (canonicalRelevance) {
            url += `&relevance=${encodeURIComponent(canonicalRelevance)}`;
        }

        if (showOnlyAnalyzed) {
            url += `&analyzed_only=true`;
        }
        if (currentEventId) {
            url += `&event_id=${encodeURIComponent(currentEventId)}`;
        }
        if (searchQuery) {
            url += `&search=${encodeURIComponent(searchQuery)}`;
        }

        // FIX 2: Only exclude noisy articles when no specific relevance is selected.
        // If the user has selected the "Noisy" tab (or any other tab), do NOT add this.
        // Previously this was always added for 'feed' view, which broke the Noisy tab
        // and silently excluded articles from other relevance tabs too.
        if (currentDashboardView === 'feed' && !canonicalRelevance) {
            url += `&exclude_noisy=true`;
        }

        console.log('DEBUG → currentRelevance:', currentRelevance);
        console.log('DEBUG → canonicalRelevance:', canonicalRelevance);
        console.log('DEBUG → final URL:', url);

        dbg('fetchNews fetching:', url);
        const res = await apiFetch(url, { signal });
        const json = await res.json();

        if (json.status === 'success') {
            const newArticles = json.data || [];
            const filteredArticles = newArticles;
            console.log('DEBUG → articles received:', newArticles.length, 'filtered:', filteredArticles.length);
            if (filteredArticles.length !== newArticles.length) {
                console.log(`DEBUG → dropped ${newArticles.length - filteredArticles.length} articles that did not match current relevance '${currentRelevance}'`);
            }
            dbg('fetchNews success:', { count: filteredArticles.length, state: newState });

            if (isBackgroundRefresh) {
                // Smart Refresh: Add new articles and update existing ones seamlessly
                const trulyNew = [];
                filteredArticles.forEach(a => {
                    if (!seenArticleIds.has(a.id)) {
                        trulyNew.push(a);
                    } else {
                        // Check for updates to existing articles (e.g. analyzed status changed)
                        const existIdx = newsData.findIndex(x => x.id === a.id);
                        if (existIdx !== -1) {
                            const oldA = newsData[existIdx];
                            if (oldA.analyzed !== a.analyzed || oldA.impact_score !== a.impact_score || oldA.prediction_count !== a.prediction_count || oldA.event_id !== a.event_id || oldA.symbols !== a.symbols) {
                                a.featuredType = oldA.featuredType; // preserve featured label
                                newsData[existIdx] = a;
                                const existingCard = document.getElementById(`article-card-${a.id}`);
                                if (existingCard) {
                                    const isFeatured = existingCard.classList.contains('featured-card');
                                    const newCard = createNewsCard(a, existIdx, isFeatured);
                                    existingCard.innerHTML = newCard.innerHTML;
                                    existingCard.className = newCard.className;
                                    existingCard.onclick = () => openModal(a);
                                }
                            }
                        }
                    }
                });

                if (trulyNew.length > 0) {
                    console.log(`[Smart Refresh] Found ${trulyNew.length} new articles`);
                    trulyNew.forEach(a => seenArticleIds.add(a.id));
                    // Directly prepend new articles and render them
                    newsData = [...trulyNew, ...newsData];
                    renderNews(newsData);
                }
            } else if (isLoadMore) {
                // Infinite Scroll: Append new articles
                if (filteredArticles.length < articlesPerPage) {
                    hasMoreArticles = false;
                }
                currentPage++;

                // Filter out duplicates just in case
                const uniqueNew = filteredArticles.filter(a => !seenArticleIds.has(a.id));
                newsData = [...newsData, ...uniqueNew];
                uniqueNew.forEach(a => seenArticleIds.add(a.id));

                renderNews(uniqueNew, false, true); // append = true
            } else {
                // Initial load or Filter change
                if (filteredArticles.length === 0 && currentRelevance !== 'all') {
                    console.warn(`No articles found for relevance '${currentRelevance}'`);
                }
                newsData = filteredArticles;
                seenArticleIds.clear();
                filteredArticles.forEach(a => seenArticleIds.add(a.id));
                currentPage = 0;
                hasMoreArticles = filteredArticles.length >= articlesPerPage;
                renderNews(newsData);
            }

            // Update counts
            updateDisplayCounts();

            // Recover from any previous network failures
            if (consecutiveFailures > 0) {
                hideConnectionBanner();
                showToast('Connection restored', 'success');
            }
            consecutiveFailures = 0;
            consecutiveBackendFailures = 0;

        } else {
            // ── BACKEND FAILURE ──────────────────────────────────────────────────────
            // The server responded (network is fine) but the query itself failed.
            // e.g. DB timeout, query error. Show a soft toast, NOT the connection banner.
            consecutiveBackendFailures++;
            console.warn(`[fetchNews] Backend error (attempt ${consecutiveBackendFailures}):`, json.message || 'Unknown error');

            if (consecutiveBackendFailures >= CONNECTION_FAIL_THRESHOLD) {
                // DB is consistently struggling — show a less alarmist backend notice
                showToast('Server is under load — retrying shortly', 'warning');
                // Reset so the next success clears the toast without multiple fires
                consecutiveBackendFailures = 0;
            }
            // Do NOT touch consecutiveFailures — that is reserved for real network loss.
        }

    } catch (err) {
        if (err.name === 'AbortError') {
            dbg('fetchNews aborted cleanly (gen', myGeneration, ')');
            // Aborted calls must NOT reset requestState — a newer call owns it now.
            return;
        }

        // Only handle network failures if this call is still the active one
        if (myGeneration !== _fetchGeneration) return;

        // ── NETWORK FAILURE ──────────────────────────────────────────────────────
        consecutiveFailures++;
        console.error('[fetchNews] Error:', err);

        if (consecutiveFailures >= CONNECTION_FAIL_THRESHOLD) {
            showConnectionBanner();
        }

    } finally {
        // FIX 3: Only the OWNING call (latest generation) may reset shared state.
        // An aborted call's finally block must not stomp the newer call's requestState.
        if (myGeneration === _fetchGeneration) {
            requestState = 'idle';
            _fetchAbortController = null;
            hideRefreshIndicator();
            hideSkeletonLoader();
            const indicator = document.getElementById('loadMoreIndicator');
            if (indicator) indicator.style.display = 'none';

            const sentinel = document.getElementById('infiniteScrollSentinel');
            if (sentinel) sentinel.style.display = hasMoreArticles ? 'block' : 'none';
        } else {
            dbg('fetchNews finally ignored (stale gen', myGeneration, ', current', _fetchGeneration, ')');
        }
    }
}


// ---- Display Counts Helper ----
function updateDisplayCounts() {
    const displayCount = searchQuery ? newsData.length : totalDbArticles;
    const suffix = searchQuery ? ' results' : ' articles';

    const formattedCount = displayCount.toLocaleString();
    if (articleCount) articleCount.textContent = `${formattedCount}${suffix}`;
    if (drawerCount) drawerCount.textContent = `${formattedCount}${suffix}`;
    
    updateRelevanceHeroStats();
}

// ---- Update Relevance Hero Stats ----
function updateRelevanceHeroStats() {
    if (currentRelevance === 'all') return;
    
    // Get current filter info
    const relevanceConfig = {
        'high useful': { title: 'High Useful News', color: '#00d4aa', label: 'HIGH USEFUL' },
        'useful': { title: 'Useful News', color: '#00d4aa', label: 'USEFUL' },
        'medium': { title: 'Medium Relevance', color: '#f0c040', label: 'MEDIUM' },
        'neutral': { title: 'Neutral News', color: '#a0aabc', label: 'NEUTRAL' },
        'noisy': { title: 'Noisy News', color: '#ff4757', label: 'NOISY' }
    };
    
    const config = relevanceConfig[currentRelevance];
    if (!config) return;
    
    // Count total and analyzed articles in current view
    let totalCount = 0;
    let analyzedCount = 0;
    let latestTimestamp = null;
    
    if (newsData && newsData.length > 0) {
        newsData.forEach(article => {
            const rel = (article.news_relevance || '').toLowerCase();
            // Check if article matches current relevance filter
            let matches = false;
            if (currentRelevance === 'high useful' && rel === 'high useful') matches = true;
            else if (currentRelevance === 'useful' && rel === 'useful') matches = true;
            else if (currentRelevance === 'medium' && rel === 'medium') matches = true;
            else if (currentRelevance === 'neutral' && rel === 'neutral') matches = true;
            else if (currentRelevance === 'noisy' && (rel.includes('noisy') || rel.includes('noise'))) matches = true;
            
            if (matches) {
                totalCount++;
                if (article.analyzed) analyzedCount++;
                
                // Track latest timestamp
                if (article.published) {
                    const ts = new Date(article.published).getTime();
                    if (!latestTimestamp || ts > latestTimestamp) {
                        latestTimestamp = ts;
                    }
                }
            }
        });
    }
    
    // Update DOM
    const articlesLabel = document.getElementById('relevanceArticlesLabel');
    const articlesCount = document.getElementById('relevanceArticlesCount');
    const analyzedLabel = document.getElementById('relevanceAnalyzedLabel');
    const analyzedCountElement = document.getElementById('relevanceAnalyzedCount');
    const latestUpdate = document.getElementById('relevanceLatestUpdate');
    
    if (articlesLabel) articlesLabel.textContent = `${config.label} ARTICLES`;
    if (articlesCount) articlesCount.textContent = totalCount.toLocaleString();
    if (analyzedLabel) analyzedLabel.textContent = `ANALYZED ARTICLES`;
    if (analyzedCountElement) analyzedCountElement.textContent = analyzedCount.toLocaleString();
    if (latestUpdate) {
        if (latestTimestamp) {
            const latestDate = new Date(latestTimestamp);
            latestUpdate.textContent = timeAgo(latestDate.toISOString());
        } else {
            latestUpdate.textContent = '--';
        }
    }
}

// ---- Fetch Footer Stats ----
async function fetchStats() {
    try {
        const res = await apiFetch(`${API_BASE}/api/indian_stats`);
        const json = await res.json();
        if (json.status === 'success') {
            const d = json.data;
            totalDbArticles = d.total_articles;
            updateDisplayCounts();

            document.getElementById('footerTotal').textContent = d.total_articles.toLocaleString();
            document.getElementById('footerAnalyzed').textContent = d.analyzed_articles.toLocaleString();
            document.getElementById('footerSources').textContent = d.source_count.toLocaleString();

            // Format uptime
            const secs = d.uptime_seconds;
            const h = Math.floor(secs / 3600);
            const m = Math.floor((secs % 3600) / 60);
            document.getElementById('footerUptime').textContent = h > 0 ? `${h}h ${m}m` : `${m}m`;
        }
    } catch (err) {
        // Silently fail — not critical
    }
}

// ---- Refresh Indicator ----
function showRefreshIndicator() {
    refreshIndicator.classList.add('visible');
}

function hideRefreshIndicator() {
    setTimeout(() => {
        refreshIndicator.classList.remove('visible');
    }, 500);
}

// ---- Skeleton Loader ----
function showSkeletonLoader() {
    if (!newsGrid) return;
    // We only show skeletons if we are loading from scratch/filter (not load-more)
    const skeletonHtml = Array.from({ length: 6 }).map(() => `
        <div class="skeleton-card">
            <div class="skeleton-img skeleton-shimmer"></div>
            <div class="skeleton-title skeleton-shimmer"></div>
            <div class="skeleton-text skeleton-shimmer"></div>
            <div class="skeleton-text skeleton-shimmer" style="width: 80%"></div>
            <div class="skeleton-meta skeleton-shimmer"></div>
        </div>
    `).join('');
    
    newsGrid.innerHTML = skeletonHtml;
    // Hide empty state if visible
    if (emptyState) emptyState.style.display = 'none';
    if (featuredSection) featuredSection.style.display = 'none';
}

function hideSkeletonLoader() {
    // Safety net: if renderNews didn't run (e.g., request was aborted), manually
    // clear any skeleton cards left in the grid.
    if (!newsGrid) return;
    const skeletons = newsGrid.querySelectorAll('.skeleton-card');
    if (skeletons.length === newsGrid.children.length && skeletons.length > 0) {
        // Grid contains ONLY skeleton cards — clear it and show empty state
        newsGrid.innerHTML = '';
        if (emptyState) {
            emptyState.style.display = 'flex';
            if (emptyStateMsg) emptyStateMsg.textContent = 'The monitor is fetching news. Articles will appear here automatically.';
        }
    }
}

// ---- Search Functionality ----
searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        searchQuery = searchInput.value.trim();
        searchClear.style.display = searchQuery ? 'flex' : 'none';
        currentPage = 0; // Reset to first page for new search
        hasMoreArticles = true;
        seenArticleIds.clear();
        fetchNews();
    }, SEARCH_DEBOUNCE);
});

searchClear.addEventListener('click', () => {
    searchInput.value = '';
    searchQuery = '';
    searchClear.style.display = 'none';
    currentPage = 0; // Reset to first page
    hasMoreArticles = true;
    seenArticleIds.clear();
    fetchNews();
    searchInput.focus();
});

// ---- Scroll-to-top and Sticky header logic ----
let lastScrollY = window.scrollY;
const headerEl = document.querySelector('.header');

// Dynamically set header height for sticky positioning
const updateHeaderHeight = () => {
    if (headerEl && filtersSection) {
        filtersSection.style.top = `${headerEl.offsetHeight}px`;
    }
};
// Initial and on-resize update
updateHeaderHeight();
window.addEventListener('resize', updateHeaderHeight);

window.addEventListener('scroll', () => {
    const currentScrollY = window.scrollY;
    
    // Scroll-to-top button logic
    if (currentScrollY > SCROLL_TOP_THRESHOLD) {
        scrollTopBtn.classList.add('visible');
    } else {
        scrollTopBtn.classList.remove('visible');
    }

    // Hide/Show filters on scroll down/up
    if (filtersSection) {
        if (currentScrollY > lastScrollY && currentScrollY > 100) {
            // Scrolling down
            filtersSection.classList.add('filters-hidden');
        } else {
            // Scrolling up
            filtersSection.classList.remove('filters-hidden');
        }
    }
    
    lastScrollY = currentScrollY;
}, { passive: true });

scrollTopBtn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
});

// ---- Filter Click Handler ----
if (filtersContainer) {
    filtersContainer.addEventListener('click', (e) => {
        const pill = e.target.closest('.filter-pill');
        if (!pill) return;

        filtersContainer.querySelectorAll('.filter-pill').forEach(p => {
            p.classList.remove('active');
            p.setAttribute('aria-selected', 'false');
        });
        pill.classList.add('active');
        pill.setAttribute('aria-selected', 'true');

        currentSource = pill.dataset.source;
        
        // Reset state before fetching to ensure UI consistency
        currentPage = 0;
        hasMoreArticles = true;
        seenArticleIds.clear();
        
        fetchNews();
    });
}

const relevanceContainers = [
    document.getElementById('relevanceContainer'),
    document.getElementById('relevanceContainerMobile')
];

relevanceContainers.forEach(container => {
    if (container) {
        container.addEventListener('click', (e) => {
            const pill = e.target.closest('.filter-pill');
            if (!pill) return;

            // Update UI for both desktop and mobile containers to keep them perfectly synced
            relevanceContainers.forEach(c => {
                if (c) {
                    c.querySelectorAll('.filter-pill').forEach(p => {
                        p.classList.remove('active');
                        p.setAttribute('aria-selected', 'false');
                    });
                    
                    const activePill = c.querySelector(`.filter-pill[data-relevance="${pill.dataset.relevance}"]`);
                    if (activePill) {
                        activePill.classList.add('active');
                        activePill.setAttribute('aria-selected', 'true');
                    }
                }
            });

            currentDashboardView = 'feed'; // Switch back to feed if clicked from events view
            currentRelevance = pill.dataset.relevance;
            
            // Reset state before fetching to ensure UI consistency
            currentPage = 0;
            hasMoreArticles = true;
            seenArticleIds.clear();

            // Update Hero section text dynamically based on selected relevance
            if (relevanceView && relevanceTitle && relevanceSubtitle && relevanceKicker) {
                if (currentRelevance !== 'all') {
                    const titles = {
                        'high useful': { title: 'High Useful News', color: '#00d4aa', subtitle: 'Crucial market movers with direct, confirmed impact.' },
                        'useful': { title: 'Useful News', color: '#00d4aa', subtitle: 'Actionable updates and material market information.' },
                        'medium': { title: 'Medium Relevance', color: '#f0c040', subtitle: 'Sector updates and broader market signals.' },
                        'neutral': { title: 'Neutral News', color: '#a0aabc', subtitle: 'General updates without immediate directional bias.' },
                        'noisy': { title: 'Noisy News', color: '#ff4757', subtitle: 'Articles flagged as noise, daily recaps, or low-impact commentary. Separated to keep your main feed clean.' }
                    };
                    const config = titles[currentRelevance];
                    if (config) {
                        relevanceTitle.textContent = config.title;
                        relevanceSubtitle.textContent = config.subtitle;
                        relevanceKicker.textContent = `Filter: ${currentRelevance.replace(' ', ' / ').toUpperCase()}`;
                        relevanceKicker.style.color = config.color;
                    }
                }
            }
            
            applyDashboardViewState();
            fetchNews();
            
            if (isDrawerOpen) toggleMobileMenu();
        });
    }
});


// ---- Mobile Drawer Toggle ----
function toggleMobileMenu() {
    isDrawerOpen = !isDrawerOpen;
    if (isDrawerOpen) {
        mobileMenuTrigger.classList.add('active');
        mobileDrawer.classList.add('active');
        drawerOverlay.classList.add('active');
        document.body.style.overflow = 'hidden'; // Stop background scroll
    } else {
        mobileMenuTrigger.classList.remove('active');
        mobileDrawer.classList.remove('active');
        drawerOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }
}

if (mobileMenuTrigger) mobileMenuTrigger.addEventListener('click', toggleMobileMenu);
if (drawerOverlay) drawerOverlay.addEventListener('click', toggleMobileMenu);

// Close drawer on link click
document.querySelectorAll('.drawer-link').forEach(link => {
    link.addEventListener('click', () => {
        if (isDrawerOpen) toggleMobileMenu();
    });
});

function setDashboardNavState() {
    const navItems = document.querySelectorAll('[data-view-target]');
    navItems.forEach((item) => {
        const target = item.getAttribute('data-view-target');
        const isActive = target === currentDashboardView;

        if (item.classList.contains('nav-view-btn')) {
            item.classList.toggle('is-active', isActive);
            item.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        }

        if (item.classList.contains('drawer-link')) {
            item.classList.toggle('active', isActive);
            item.setAttribute('aria-current', isActive ? 'page' : 'false');
        }
    });
}

function applyDashboardViewState() {
    const isEventsView = currentDashboardView === 'events';
    const isFeedView = currentDashboardView === 'feed';
    
    // Only show relevance hero if we are on the Feed view and a specific relevance is selected
    const isRelevanceView = isFeedView && currentRelevance && currentRelevance !== 'all';
    
    if (filtersSection) {
        filtersSection.style.display = isEventsView ? 'none' : '';
    }

    const relevanceFiltersSection = document.getElementById('relevanceFiltersSection');
    if (relevanceFiltersSection) {
        relevanceFiltersSection.style.display = isEventsView ? 'none' : '';
    }

    if (feedView) feedView.style.display = isEventsView ? 'none' : 'block';
    if (relevanceView) relevanceView.style.display = isRelevanceView ? 'block' : 'none';
    if (eventsBoardView) eventsBoardView.style.display = isEventsView ? 'block' : 'none';
    
    setDashboardNavState();
}

window.switchDashboardView = function (targetView, options = {}) {
    if (targetView === 'events') {
        currentDashboardView = 'events';
    } else {
        currentDashboardView = 'feed';
        currentSource = 'all';
        currentRelevance = 'all'; 
        searchQuery = '';
        if (searchInput) {
            searchInput.value = '';
            searchClear.style.display = 'none';
        }

        // Visually reset source and relevance pills when switching back to main feed directly via top nav
        if (filtersContainer) {
            filtersContainer.querySelectorAll('.filter-pill').forEach(p => {
                const isAll = p.dataset.source === 'all';
                p.classList.toggle('active', isAll);
                p.setAttribute('aria-selected', isAll ? 'true' : 'false');
            });
        }
        const relContainers = [
            document.getElementById('relevanceContainer'),
            document.getElementById('relevanceContainerMobile')
        ];
        relContainers.forEach(container => {
            if (container) {
                 container.querySelectorAll('.filter-pill').forEach(p => {
                    const isAll = p.dataset.relevance === 'all';
                    p.classList.toggle('active', isAll);
                    p.setAttribute('aria-selected', isAll ? 'true' : 'false');
                });
            }
        });
    }
    
    applyDashboardViewState();

    if (currentDashboardView === 'events') {
        renderEventsBoard(eventsData);
        if (!eventsData.length) {
            fetchEvents();
        }
    } else {
        // Refresh news for the selected view
        currentPage = 0;
        hasMoreArticles = true; // FIX 4: Always reset before initial fetch on view switch
        seenArticleIds.clear();
        fetchNews();
    }

    if (options.scroll !== false) {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
};

function bindDashboardViewNavigation() {
    document.querySelectorAll('[data-view-target]').forEach((item) => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const targetView = item.getAttribute('data-view-target') || 'feed';
            window.switchDashboardView(targetView);
        });
    });
}

function setupEventsBoardControls() {
    if (eventsBoardSearchInput) {
        eventsBoardSearchInput.addEventListener('input', () => {
            eventsBoardSearch = eventsBoardSearchInput.value.trim().toLowerCase();
            renderEventsBoard(eventsData);
        });
    }

    if (eventsBoardSort) {
        eventsBoardSort.addEventListener('change', () => {
            eventsBoardSortBy = eventsBoardSort.value;
            renderEventsBoard(eventsData);
        });
    }
}

// ---- Initial Load ----
async function init() {
    bindDashboardViewNavigation();
    setupEventsBoardControls();
    
    // NOTE: WebSocket is auto-started by the IIFE below (initWebSocket IIFE at the bottom of the file).
    // Do NOT call initWebSocket() here — the IIFE runs immediately when this script loads.

    window.switchDashboardView('feed', { scroll: false }); // This calls fetchNews() internally

    // Setup Infinite Scroll (IntersectionObserver)
    const sentinel = document.getElementById('infiniteScrollSentinel');
    if (sentinel) {
        const observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && hasMoreArticles && requestState === 'idle') {
                dbg('[Infinite Scroll] Sentinel hit, loading more...');
                fetchNews(true); // isLoadMore = true
            }
        }, { threshold: 0.1 });
        observer.observe(sentinel);
    }

    // Setup Filter Bar Scroll Navigation
    const filtersContainer = document.getElementById('filtersContainer');
    const sourceScrollLeftBtn = document.getElementById('sourceScrollLeftBtn');
    const sourceScrollRightBtn = document.getElementById('sourceScrollRightBtn');
    const relevanceContainer = document.getElementById('relevanceContainer');
    const relevanceScrollLeftBtn = document.getElementById('relevanceScrollLeftBtn');
    const relevanceScrollRightBtn = document.getElementById('relevanceScrollRightBtn');

    function setupScroll(container, leftBtn, rightBtn) {
        if (!container || !leftBtn || !rightBtn) return;
        const scrollAmount = 200;

        leftBtn.addEventListener('click', () => {
            container.scrollBy({ left: -scrollAmount, behavior: 'smooth' });
        });

        rightBtn.addEventListener('click', () => {
            container.scrollBy({ left: scrollAmount, behavior: 'smooth' });
        });

        container.addEventListener('scroll', () => {
             const { scrollLeft, scrollWidth, clientWidth } = container;
             if (scrollLeft > 10) leftBtn.classList.remove('disabled');
             else leftBtn.classList.add('disabled');
             
             if ((scrollLeft + clientWidth) < (scrollWidth - 10)) rightBtn.classList.remove('disabled');
             else rightBtn.classList.add('disabled');
        });

        setTimeout(() => container.dispatchEvent(new Event('scroll')), 500);
    }

    setupScroll(filtersContainer, sourceScrollLeftBtn, sourceScrollRightBtn);
    setupScroll(relevanceContainer, relevanceScrollLeftBtn, relevanceScrollRightBtn);

    // NOTE: fetchNews() is already triggered by switchDashboardView('feed') above.
    // We only need to start the other initial data fetches here.
    await Promise.all([fetchSources(), fetchStats(), fetchHolidays(), fetchEvents()]);
}

init();

// ---- Auto-refresh (Smart Refresh) ----
setInterval(() => {
    // We no longer poll fetchNews or fetchSources every 30s. 
    // WebSocket (initWebSocket) now handles discovery of new articles in real-time.
    dbg('[Poll] Background maintenance check...');
    fetchStats();
    fetchEvents();
}, REFRESH_INTERVAL);

// ===== WebSocket Real-Time Sync =====
// Connects to backend WS for instant cross-user updates (analysis results, new articles)
(function initWebSocket() {
    let ws = null;
    let wsRetryCount = 0;
    const WS_MAX_RETRY_DELAY = 30000; // 30s max backoff
    let wsPingTimer = null;
    let wsReconnectTimer = null;

    function getWsUrl() {
        // Derive ws:// or wss:// from API_BASE
        let base = API_BASE || window.location.origin;
        if (base.startsWith('https://')) {
            return base.replace('https://', 'wss://') + '/ws';
        } else if (base.startsWith('http://')) {
            return base.replace('http://', 'ws://') + '/ws';
        }
        // Fallback: use current page protocol
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${window.location.host}/ws`;
    }

    function connectWebSocket() {
        try {
            const url = getWsUrl();
            console.log(`[WS] Connecting to ${url}...`);
            ws = new WebSocket(url);

            ws.onopen = () => {
                console.log('[WS] Connected ✓');
                wsRetryCount = 0;
                // Start periodic ping to keep connection alive
                clearInterval(wsPingTimer);
                wsPingTimer = setInterval(() => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send('ping');
                    }
                }, 25000); // ping every 25s
            };

            ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    handleWsMessage(msg);
                } catch (e) {
                    // Ignore non-JSON messages (like pong)
                }
            };

            ws.onclose = () => {
                console.log('[WS] Disconnected. Will retry...');
                clearInterval(wsPingTimer);
                scheduleReconnect();
            };

            ws.onerror = (err) => {
                console.warn('[WS] Error:', err);
                // onclose will fire after this, triggering reconnect
            };
        } catch (e) {
            console.warn('[WS] Failed to create WebSocket:', e);
            scheduleReconnect();
        }
    }

    function scheduleReconnect() {
        clearTimeout(wsReconnectTimer);
        const delay = Math.min(1000 * Math.pow(2, wsRetryCount), WS_MAX_RETRY_DELAY);
        wsRetryCount++;
        console.log(`[WS] Reconnecting in ${delay}ms (attempt ${wsRetryCount})...`);
        wsReconnectTimer = setTimeout(connectWebSocket, delay);
    }

    function handleWsMessage(msg) {
        if (msg.type === 'article_updated' && msg.scope === 'indian') {
            // Another user analyzed an Indian article — update our local state + DOM instantly
            const updatedArticle = msg.article;
            if (!updatedArticle || !updatedArticle.id) return;

            console.log(`[WS] Article updated: #${updatedArticle.id}`);

            // Parse analysis_data if stringified
            if (typeof updatedArticle.analysis_data === 'string') {
                try { updatedArticle.analysis_data = JSON.parse(updatedArticle.analysis_data); } catch (e) { }
            }

            // Update local newsData array
            const existIdx = newsData.findIndex(a => a.id === updatedArticle.id);
            if (existIdx !== -1) {
                // Preserve featured label if it had one
                updatedArticle.featuredType = newsData[existIdx].featuredType;
                newsData[existIdx] = updatedArticle;
            }

            // Patch the DOM card if it exists
            const existingCard = document.getElementById(`article-card-${updatedArticle.id}`);
            if (existingCard) {
                const isFeatured = existingCard.classList.contains('featured-card');
                const newCard = createNewsCard(updatedArticle, 0, isFeatured);
                existingCard.innerHTML = newCard.innerHTML;
                existingCard.className = newCard.className;
                existingCard.onclick = () => openModal(updatedArticle);
            }

            // If this article's modal is currently open, refresh it
            if (modalOverlay && modalOverlay.classList.contains('active')) {
                const modalTitleEl = modalBody && modalBody.querySelector('.modal-title');
                if (modalTitleEl && modalTitleEl.textContent === updatedArticle.title) {
                    openModal(updatedArticle);
                }
            }

        } else if (msg.type === 'new_articles') {
            // New articles arrived — trigger a smart refresh
            console.log(`[WS] New articles notification received`);
            fetchNews(false, true);
        } else if (msg.type === 'pong') {
            // Heartbeat response — no action needed
        }
    }

    // Start WebSocket connection
    connectWebSocket();

    // Handle Tab Switching (Background Throttling Mitigation)
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            // When tab becomes active, check if connection was lost due to browser suspending background timers
            if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
                console.log('[WS] Tab active again, fast-reconnecting...');
                clearInterval(wsPingTimer);
                clearTimeout(wsReconnectTimer);
                wsRetryCount = 0; // Reset the backoff delay
                connectWebSocket();
                
                // Smart refresh to fetch any data missed while the tab was asleep
                fetchNews(false, true); 
                fetchEvents();
            } else if (ws && ws.readyState === WebSocket.OPEN) {
                // Send an immediate ping to confirm connection is still alive
                ws.send('ping');
            }
        }
    });
})();

// ---- Fetch Events ----
async function fetchEvents() {
    try {
        const res = await apiFetch(`${API_BASE}/api/events/india`);
        const json = await res.json();
        if (json.status === 'success') {
            eventsData = Array.isArray(json.data) ? json.data : [];
            renderEvents(eventsData);
            renderEventsBoard(eventsData);
        }
    } catch (e) {
        console.error("Failed to fetch events", e);
    }
}

function sortEventsForBoard(events) {
    const sorted = [...events];
    if (eventsBoardSortBy === 'articles') {
        sorted.sort((a, b) => (Number(b.article_count) || 0) - (Number(a.article_count) || 0));
        return sorted;
    }
    if (eventsBoardSortBy === 'title') {
        sorted.sort((a, b) => (a.event_title || '').localeCompare(b.event_title || ''));
        return sorted;
    }
    sorted.sort((a, b) => new Date(b.latest_update).getTime() - new Date(a.latest_update).getTime());
    return sorted;
}

function updateEventsBoardSummary(events) {
    if (eventsBoardTotal) eventsBoardTotal.textContent = (events.length || 0).toLocaleString();

    const totalArticles = events.reduce((sum, ev) => sum + (Number(ev.article_count) || 0), 0);
    if (eventsBoardArticles) eventsBoardArticles.textContent = totalArticles.toLocaleString();

    let latestTs = 0;
    events.forEach((ev) => {
        const ts = new Date(ev.latest_update).getTime();
        if (!Number.isNaN(ts) && ts > latestTs) latestTs = ts;
    });

    if (eventsBoardLastUpdate) {
        if (!latestTs) {
            eventsBoardLastUpdate.textContent = '--';
            eventsBoardLastUpdate.removeAttribute('title');
        } else {
            const iso = new Date(latestTs).toISOString();
            eventsBoardLastUpdate.textContent = timeAgo(iso);
            eventsBoardLastUpdate.setAttribute('title', formatDateTimeIST(iso));
        }
    }
}

function renderEventsBoard(events) {
    if (!eventsBoardGrid || !eventsBoardEmpty) return;

    const safeEvents = Array.isArray(events) ? events : [];
    updateEventsBoardSummary(safeEvents);

    let boardEvents = safeEvents;
    if (eventsBoardSearch) {
        boardEvents = boardEvents.filter((ev) => {
            const title = (ev.event_title || '').toLowerCase();
            const eventId = (ev.event_id || '').toLowerCase();
            return title.includes(eventsBoardSearch) || eventId.includes(eventsBoardSearch);
        });
    }

    boardEvents = sortEventsForBoard(boardEvents);

    if (!boardEvents.length) {
        eventsBoardGrid.innerHTML = '';
        eventsBoardEmpty.style.display = 'flex';
        return;
    }

    eventsBoardEmpty.style.display = 'none';
    const fourHoursAgo = Date.now() - (4 * 60 * 60 * 1000);

    eventsBoardGrid.innerHTML = boardEvents.map((ev, idx) => {
        const title = ev.event_title || 'Untitled Event';
        const articleCount = Number(ev.article_count) || 0;
        const lastUpdateTs = new Date(ev.latest_update).getTime();
        const isLive = !Number.isNaN(lastUpdateTs) && lastUpdateTs > fourHoursAgo;
        const activeClass = currentEventId === ev.event_id ? ' active' : '';
        const attention = articleCount >= 10 ? 'High Attention' : articleCount >= 4 ? 'Medium Attention' : 'Emerging';
        const statusKey = isLive ? 'live' : 'tracking';
        const statusLabel = isLive ? 'Live' : 'Tracking';
        const attentionKey = articleCount >= 10 ? 'high_attention' : articleCount >= 4 ? 'medium_attention' : 'emerging';
        const timeLabel = formatDateTimeIST(ev.latest_update);

        return `
            <article class="events-board-card${activeClass}" data-event-idx="${idx}" style="--event-index:${idx};" title="Open event details">
                <div class="events-board-card-top">
                    ${wrapTooltip(`<span class="events-board-chip ${isLive ? 'chip-live' : 'chip-tracking'}" aria-label="${statusLabel}">${statusLabel}</span>`, 'event_activity_status', statusKey)}
                    <span class="events-board-time">${timeAgo(ev.latest_update)}</span>
                </div>
                <h3 class="events-board-card-title">${escapeHtml(title)}</h3>
                <p class="events-board-card-meta">${escapeHtml(timeLabel)}</p>
                <div class="events-board-card-bottom">
                    <span class="events-board-article-count">${articleCount.toLocaleString()} articles</span>
                    ${wrapTooltip(`<span class="events-board-open" aria-label="${attention}">${attention}</span>`, 'event_attention_level', attentionKey)}
                </div>
            </article>
        `;
    }).join('');

    eventsBoardGrid.querySelectorAll('.events-board-card').forEach((card) => {
        card.addEventListener('click', () => {
            const idx = Number(card.getAttribute('data-event-idx'));
            const selectedEvent = boardEvents[idx];
            if (!selectedEvent) return;

            showEventDetail(
                selectedEvent.event_id,
                selectedEvent.event_title || 'Untitled Event',
                Number(selectedEvent.article_count) || 0,
                selectedEvent.latest_update
            );
        });
    });
}

function renderEvents(events) {
    const section = document.getElementById('eventsSection');
    const container = document.getElementById('eventsContainer');

    if (!section || !container) return;

    if (!events || events.length === 0) {
        section.style.display = 'none';
        container.innerHTML = '';
        return;
    }

    section.style.display = 'block';

    let html = '';
    events.forEach((ev, idx) => {
        const eventTitle = ev.event_title || 'Untitled Event';
        const articleCount = Number(ev.article_count) || 0;
        const timeAgoStr = timeAgo(ev.latest_update);
        const isActive = currentEventId === ev.event_id;
        // Dynamic "Live" indicator if updated in last 4 hours (Indian market is more volatile)
        const lastUpdate = new Date(ev.latest_update).getTime();
        const fourHoursAgo = Date.now() - (4 * 60 * 60 * 1000);
        const isLive = lastUpdate > fourHoursAgo;

        html += `
            <div class="event-card ${isActive ? 'active' : ''}" 
                 data-event-id="${ev.event_id}"
                 data-event-title="${escapeHtml(ev.event_title)}"
                 data-article-count="${ev.article_count}"
                 data-latest-update="${ev.latest_update}"
                 onclick="showEventDetail('${escapeForInlineJsAttr(ev.event_id)}', '${escapeForInlineJsAttr(ev.event_title)}', ${ev.article_count}, '${escapeForInlineJsAttr(ev.latest_update)}'); event.stopPropagation();"
                 style="cursor: pointer; transition: all 0.2s ease;"
                 title="Click to see all articles for this event">
                <div class="event-card-header">
                    <span class="event-label">EVENT TRACKER</span>
                    <span class="event-time">${timeAgoStr}</span>
                </div>
                <h3 class="event-card-title">${escapeHtml(eventTitle)}</h3>
                <div class="event-footer">
                    <div class="event-updates">
                        <span class="event-pulse" style="display: ${isLive ? 'flex' : 'none'}"></span>
                        <span>${articleCount} articles</span>
                    </div>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;

    container.querySelectorAll('.event-card').forEach((card) => {
        card.addEventListener('click', () => {
            const idx = Number(card.getAttribute('data-event-idx'));
            const selectedEvent = events[idx];
            if (!selectedEvent) return;

            showEventDetail(
                selectedEvent.event_id,
                selectedEvent.event_title || 'Untitled Event',
                Number(selectedEvent.article_count) || 0,
                selectedEvent.latest_update
            );
        });
    });

    // Attach once to avoid stacking listeners on every refresh.
    if (!container.dataset.scrollBound) {
        container.addEventListener('scroll', checkScrollButtons, { passive: true });
        container.dataset.scrollBound = '1';
    }
    // Initial check
    setTimeout(checkScrollButtons, 100);
}

function checkScrollButtons() {
    const container = document.getElementById('eventsContainer');
    // More specific selectors to avoid hijacking other buttons
    const parent = document.querySelector('.events-board-view') || document;
    const leftBtn = parent.querySelector('.scroll-btn.left');
    const rightBtn = parent.querySelector('.scroll-btn.right');
    if (!container || !leftBtn || !rightBtn) return;

    leftBtn.style.display = container.scrollLeft > 20 ? 'flex' : 'none';

    const atEnd = container.scrollLeft + container.clientWidth >= container.scrollWidth - 20;
    rightBtn.style.display = atEnd ? 'none' : 'flex';
}

window.filterByEvent = function (eventId, eventName) {
    if (currentEventId === eventId) {
        clearEventFilter(); // Toggle off if clicked again
        return;
    }
    currentEventId = eventId;
    document.getElementById('activeEventFilterPill').style.display = 'flex';
    document.getElementById('activeEventName').textContent = eventName;

    const header = document.getElementById('allNewsHeader');
    if (header) header.scrollIntoView({ behavior: 'smooth', block: 'start' });

    fetchNews();
    fetchEvents();
};

window.scrollEvents = function (direction) {
    const container = document.getElementById('eventsContainer');
    if (!container) return;

    // Calculate scroll amount based on card width
    const firstCard = container.querySelector('.event-card');
    const scrollAmount = firstCard ? firstCard.offsetWidth + 20 : 340;

    container.scrollBy({
        left: direction * scrollAmount,
        behavior: 'smooth'
    });
}

window.clearEventFilter = function () {
    currentEventId = null;
    document.getElementById('activeEventFilterPill').style.display = 'none';
    fetchNews();
    fetchEvents();
};


// ============================================
// LIVE TRADE VIEW — CHART PANEL (Fixed)
// ============================================

let lwChart = null;
let lwCandleSeries = null;
let currentChartSymbol = null;
let chartRefreshTimer = null;
let chartSearchDebounce = null;
let isChartPanelOpen = false;
const istDateFormatter = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'short',
    hour12: false
});
const istTickFormatter = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
});


// ---- Panel Open/Close ----
function openChartPanel() {
    isChartPanelOpen = true;
    const overlay = document.getElementById('chartOverlay');
    overlay.style.display = 'block';
    document.body.style.overflow = 'hidden';

    // Load first available pair if nothing selected yet
    if (!currentChartSymbol) {
        loadFirstAvailablePair();
    } else {
        loadChart(currentChartSymbol);
    }
}

function closeChartPanel() {
    isChartPanelOpen = false;
    document.getElementById('chartOverlay').style.display = 'none';
    document.body.style.overflow = '';
    stopChartRefresh();
}

// Close on backdrop click
document.getElementById('chartOverlay').addEventListener('click', function (e) {
    if (e.target === this) closeChartPanel();
});

// ESC closes panel
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isChartPanelOpen) closeChartPanel();
});

// Close search dropdown when clicking outside
document.addEventListener('click', function (e) {
    const drop = document.getElementById('chartSearchDrop');
    const input = document.getElementById('chartSearchInput');
    if (drop && input && !drop.contains(e.target) && e.target !== input) {
        drop.style.display = 'none';
    }
});

// ---- Auto-refresh control ----
function stopChartRefresh() {
    if (chartRefreshTimer) {
        clearInterval(chartRefreshTimer);
        chartRefreshTimer = null;
    }
}

function startChartRefresh(symbol) {
    stopChartRefresh();
    chartRefreshTimer = setInterval(async () => {
        if (isChartPanelOpen && currentChartSymbol === symbol) {
            await refreshChartData(symbol);
        }
    }, 3 * 60 * 1000); // every 3 minutes
}

// ---- Load first pair that has candle data ----
async function loadFirstAvailablePair() {
    try {
        const res = await apiFetch(`${API_BASE}/api/nse/pairs?q=TCS`);
        const json = await res.json();
        if (json.status === 'success' && json.data.length > 0) {
            await loadChart(json.data[0]);
            return;
        }
        // fallback: get any pair
        const res2 = await apiFetch(`${API_BASE}/api/nse/pairs`);
        const json2 = await res2.json();
        if (json2.status === 'success' && json2.data.length > 0) {
            await loadChart(json2.data[0]);
        }
    } catch (e) { console.warn('loadFirstAvailablePair error', e); }
}

// ---- Search ----
function onChartSearch(value) {
    clearTimeout(chartSearchDebounce);
    chartSearchDebounce = setTimeout(() => doChartSearch(value.trim()), 220);
}

async function doChartSearch(query) {
    const drop = document.getElementById('chartSearchDrop');
    if (!query || query.length < 1) {
        drop.style.display = 'none';
        return;
    }
    try {
        const url = `${API_BASE}/api/nse/pairs?q=${encodeURIComponent(query)}`;
        const res = await apiFetch(url);
        const json = await res.json();
        if (json.status !== 'success' || !json.data.length) {
            drop.innerHTML = `<div style="padding:10px 14px;color:#888;font-size:0.82rem;">No matching pairs found.</div>`;
            drop.style.display = 'block';
            return;
        }
        drop.innerHTML = json.data.map(sym => {
            const parts = sym.split(':');
            const base = parts[1] || sym;
            const exchange = parts[0] || '';
            return `<div
                onclick="selectChartPair('${sym}')"
                style="padding:9px 16px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,0.04);transition:background 0.12s;"
                onmouseover="this.style.background='rgba(108,99,255,0.15)'"
                onmouseout="this.style.background='transparent'">
                <span style="font-weight:700;font-size:0.87rem;color:var(--text-main,#f0f0f0);">${base}</span>
                <span style="font-size:0.72rem;color:#666;text-transform:uppercase;letter-spacing:0.5px;">${exchange}</span>
            </div>`;
        }).join('');
        drop.style.display = 'block';
    } catch (e) {
        drop.style.display = 'none';
    }
}

function selectChartPair(symbol) {
    if (!symbol) return;

    // Sanitize symbol: NSE stocks usually don't need "/" etc.
    let cleanSymbol = symbol.split(':').pop().toUpperCase().replace(/[^A-Z0-9]/g, '');

    console.log('[CHART] Selecting pair:', symbol, '-> Clean:', cleanSymbol);
    currentChartSymbol = cleanSymbol;

    // Open the chart panel
    openChartPanel();

    const drop = document.getElementById('chartSearchDrop');
    const input = document.getElementById('chartSearchInput');
    if (drop) drop.style.display = 'none';
    if (input) input.value = cleanSymbol;

    // Trigger loadChart directly to ensure immediate visual feedback
    loadChart(cleanSymbol);

    // Scroll to chart container
    const chartContainer = document.getElementById('lwChartContainer');
    if (chartContainer) {
        chartContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

// ---- Parse timestamp robustly ----
function parseToUnixSec(timeStr) {
    // Server sends "2026-03-27T09:48:00" — treat as UTC by appending Z
    const s = timeStr.endsWith('Z') || timeStr.includes('+') ? timeStr : timeStr + 'Z';
    return Math.floor(new Date(s).getTime() / 1000);
}

// ---- Fetch & render ----
async function loadChart(symbol) {
    if (!symbol) return;

    console.log('[LOAD CHART] Starting loadChart with symbol:', symbol);

    // Track selected pair
    currentChartSymbol = symbol;
    stopChartRefresh();

    setChartLoading(symbol);

    const data = await fetchCandleData(symbol);
    if (!data) {
        console.log('[LOAD CHART] No candle data received');
        return;
    }

    console.log('[LOAD CHART] Rendering chart with', data.length, 'candles');
    window.chartCandleData = data; // Store globally for overlay tracking
    renderLWChart(data);
    updateChartStats(symbol, data);

    // Overlay news markers on chart
    console.log('[LOAD CHART] Calling overlayNewsMarkers...');
    await overlayNewsMarkers(symbol);

    console.log('[LOAD CHART] Starting chart refresh');
    startChartRefresh(symbol);
}


// Refresh only adds new candles to existing chart
async function refreshChartData(symbol) {
    if (!symbol || !lwCandleSeries) return;
    const data = await fetchCandleData(symbol);
    if (!data) return;
    // Update all data (handles new candles at end)
    window.chartCandleData = data;
    lwCandleSeries.setData(data);
    lwChart.timeScale().scrollToRealTime();
    updateChartStats(symbol, data);

    // Resync overlay if exists
    if (typeof syncNewsOverlay === 'function') syncNewsOverlay();
}

function updateChartStats(symbol, data) {
    const label = document.getElementById('chartSymbolLabel');
    const price = document.getElementById('chartLastPrice');
    const change = document.getElementById('chartPriceChange');
    if (!label || !price || !change) return;

    if (!data || data.length < 1) {
        label.textContent = symbol;
        price.textContent = '—';
        change.textContent = '—';
        return;
    }

    const last = data[data.length - 1];
    const prev = data.length > 1 ? data[data.length - 2] : last;
    const diff = last.close - prev.close;
    const pct = ((diff / prev.close) * 100).toFixed(2);

    label.textContent = symbol.toUpperCase();
    price.textContent = last.close.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    change.textContent = (diff >= 0 ? '+' : '') + pct + '%';
    change.style.color = diff >= 0 ? '#00d4aa' : '#ff4757';
}

// ---- Fetch news markers for overlay ----
async function fetchNewsMarkers(symbol) {
    try {
        // Extract just the pair name without exchange prefix (e.g., TCS from OANDA:TCS)
        let pairOnly = symbol;
        if (symbol && symbol.includes(':')) {
            pairOnly = symbol.split(':')[1];
        }

        const url = `${API_BASE}/api/nse/news-markers?symbol=${encodeURIComponent(pairOnly)}`;
        console.log('[NEWS MARKERS] Fetching from URL:', url);
        const res = await apiFetch(url);
        const json = await res.json();
        console.log('[NEWS MARKERS] Response:', json);
        if (json.status === 'success' && Array.isArray(json.data)) {
            console.log('[NEWS MARKERS] Success! Found', json.data.length, 'news items');
            console.log('[NEWS MARKERS] Data:', json.data);
            return json.data;
        }
        console.log('[NEWS MARKERS] No data in response or invalid status');
        return [];
    } catch (err) {
        console.error('fetchNewsMarkers error:', err);
        return [];
    }
}

// ---- Overlay news markers on chart — Dot-only + hover tooltip ----
async function overlayNewsMarkers(symbol) {
    if (!lwChart || !lwCandleSeries) return;

    const container = document.getElementById('lwChartContainer');
    if (!container) return;

    // Wipe any old overlay
    const oldContainer = document.getElementById('newsOverlayContainer');
    if (oldContainer) oldContainer.remove();
    const oldTooltip = document.getElementById('newsHoverTooltip');
    if (oldTooltip) oldTooltip.remove();

    // Fetch news for this symbol
    const newsMarkers = await fetchNewsMarkers(symbol);
    if (!newsMarkers || newsMarkers.length === 0) {
        displayNewsPanel([]);
        return;
    }

    // Group by 3-min candle bucket
    chartNewsByTime = {};
    const newsByTime = {};
    for (const news of newsMarkers) {
        const pubTime = parseToUnixSec(news.published);
        const snapped = Math.floor(pubTime / 180) * 180;
        if (!newsByTime[snapped]) newsByTime[snapped] = [];
        newsByTime[snapped].push(news);
        if (!chartNewsByTime[snapped]) chartNewsByTime[snapped] = [];
        chartNewsByTime[snapped].push(news);
    }

    window.chartNewsData = newsMarkers;
    window.chartNewsByTime = chartNewsByTime;
    window._newsByTime = newsByTime;

    // ---- Build SVG overlay (dots only) ----
    const overlayContainer = document.createElement('div');
    overlayContainer.id = 'newsOverlayContainer';
    overlayContainer.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;overflow:hidden;';
    container.appendChild(overlayContainer);

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.id = 'newsOverlaySVG';
    svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;overflow:visible;';

    // Defs for gradient
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.innerHTML = `
        <radialGradient id="dotGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="#a29bfe"/>
            <stop offset="100%" stop-color="#6c63ff"/>
        </radialGradient>
    `;
    svg.appendChild(defs);
    overlayContainer.appendChild(svg);

    // One group per time slot: pulse ring + dot + dashed line + label
    for (const [timeStr, newsAtTime] of Object.entries(newsByTime)) {
        const time = parseInt(timeStr);
        const count = newsAtTime.length;
        const firstNews = newsAtTime[0];

        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.id = 'news-g-' + time;
        g.classList.add('news-marker-group');
        g.style.display = 'none';

        // Pulse ring (positioned at origin; group is moved via transform)
        const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        ring.classList.add('news-dot-pulse');
        ring.setAttribute('r', '8');
        ring.setAttribute('fill', 'none');
        ring.setAttribute('stroke', '#6c63ff');
        ring.setAttribute('stroke-width', '1.5');
        ring.setAttribute('opacity', '0.55');

        // Core dot
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('r', '4');
        dot.setAttribute('fill', 'url(#dotGrad)');
        dot.setAttribute('stroke', '#fff');
        dot.setAttribute('stroke-width', '1.2');

        // Dashed connector line (x1/y1 = dot at origin; x2/y2 set in sync)
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.id = 'news-line-' + time;
        line.setAttribute('x1', '0');
        line.setAttribute('y1', '0');
        line.setAttribute('stroke', 'rgba(162,155,254,0.6)');
        line.setAttribute('stroke-width', '1');
        line.setAttribute('stroke-dasharray', '3 3');

        // HTML label via foreignObject (absolute pos set in sync)
        const fo = document.createElementNS('http://www.w3.org/2000/svg', 'foreignObject');
        fo.id = 'news-fo-' + time;
        fo.setAttribute('width', '280');
        fo.setAttribute('height', '100');
        fo.setAttribute('overflow', 'visible');

        const labelDiv = document.createElement('div');
        labelDiv.className = 'nml-label';
        labelDiv.innerHTML = `
            <div class="nml-meta">${new Date(time * 1000).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · ${escapeHtml(firstNews.source || 'News')}</div>
            <div class="nml-title">${escapeHtml(count > 1 ? '(' + count + ') ' + firstNews.title : firstNews.title)}</div>
        `;
        labelDiv.onclick = () => openModal(firstNews);
        labelDiv.style.cursor = 'pointer';

        fo.appendChild(labelDiv);

        g.appendChild(ring);
        g.appendChild(dot);
        g.appendChild(line);
        g.appendChild(fo);
        svg.appendChild(g);
    }

    // ---- Floating hover tooltip ----
    const tooltip = document.createElement('div');
    tooltip.id = 'newsHoverTooltip';
    tooltip.className = 'news-hover-tooltip';
    tooltip.style.display = 'none';
    container.appendChild(tooltip);

    // Subscribe to chart movements to reposition dots
    lwChart.timeScale().subscribeVisibleTimeRangeChange(syncNewsOverlay);
    lwChart.timeScale().subscribeSizeChange(syncNewsOverlay);

    // Subscribe to crosshair movement to show/hide detailed marker
    lwChart.subscribeCrosshairMove(function (param) {
        // Clear previous active state
        if (window._activeNewsGId) {
            const prev = document.getElementById(window._activeNewsGId);
            if (prev) prev.classList.remove('is-active');
            window._activeNewsGId = null;
        }

        if (!param || !param.time || !window._newsByTime) return;

        const newsAtTime = window._newsByTime[param.time];
        if (!newsAtTime || !newsAtTime.length) return;

        // Show this one
        const gId = 'news-g-' + param.time;
        const g = document.getElementById(gId);
        if (g) {
            g.classList.add('is-active');
            window._activeNewsGId = gId;
        }
    });

    // Clear native markers
    lwCandleSeries.setMarkers([]);
    // Reset top margin
    lwChart.applyOptions({ layout: { topMarginRatio: 0 } });

    syncNewsOverlay();
    displayNewsPanel(newsMarkers);
}

// ---- Sync dot + dashed line + label with chart viewport ----
function syncNewsOverlay() {
    if (!lwChart || !lwCandleSeries || !window._newsByTime) return;

    const container = document.getElementById('newsOverlayContainer');
    if (!container) return;

    const containerW = container.clientWidth;
    const containerH = container.clientHeight;

    // Sort times so we can detect closeness for staggering
    const times = Object.keys(window._newsByTime).map(Number).sort((a, b) => a - b);

    // Track last X per row to handle horizontal collision (simple left-to-right sweep)
    // We use 2 vertical zones: above (even index) and below (odd index)
    const LABEL_W = 280;   // news label width
    const LABEL_H = 80;    // news label height
    const LINE_LEN = 110;   // base length of dashed line
    const STEP = LABEL_H + 12; // extra offset per stagger level

    // rightEdge[zone] = last X + labelW placed in that zone
    const rightEdge = {}; // { 'above_0': xRight, 'below_0': xRight, 'above_1': xRight, ... }

    times.forEach((time, idx) => {
        const g = document.getElementById('news-g-' + time);
        const line = document.getElementById('news-line-' + time);
        const fo = document.getElementById('news-fo-' + time);
        if (!g || !line || !fo) return;

        const xCoord = lwChart.timeScale().timeToCoordinate(time);
        if (xCoord === null || xCoord < 0 || xCoord > containerW) {
            g.style.display = 'none';
            return;
        }

        // Find candle HIGH for anchor Y
        let dotY = 0; // relative to group origin; group is placed at candle high
        let anchorY = containerH * 0.45;
        if (window.chartCandleData) {
            for (let i = 0; i < window.chartCandleData.length; i++) {
                const c = window.chartCandleData[i];
                if (c.time >= time) {
                    const yp = lwCandleSeries.priceToCoordinate(c.high);
                    if (yp !== null && !isNaN(yp)) anchorY = yp;
                    break;
                }
            }
        }

        // Place group at the candle's high coordinate
        g.setAttribute('transform', `translate(${xCoord}, ${anchorY})`);
        g.style.display = '';

        // Decide above or below (alternate by index)
        const isAbove = (idx % 2 === 0);
        const direction = isAbove ? -1 : 1;

        // Find stagger level: scan levels until we find one without collision
        let level = 0;
        const side = isAbove ? 'above' : 'below';
        while (true) {
            const key = `${side}_${level}`;
            const lastRight = rightEdge[key] || -Infinity;
            if (xCoord > lastRight - 10) {
                // Fits in this level
                rightEdge[key] = xCoord + LABEL_W;
                break;
            }
            level++;
            if (level > 8) { level = 0; break; } // safety cap
        }

        // Line end Y (relative to group origin which is at candle high)
        let totalLen = LINE_LEN + level * STEP;

        // Smart Boundary Handling: Shorten line if it would go out of chart
        if (isAbove) {
            const proposedTop = anchorY - totalLen - LABEL_H;
            if (proposedTop < 10) {
                totalLen = Math.max(25, anchorY - LABEL_H - 10);
            }
        } else {
            const proposedBottom = anchorY + totalLen + LABEL_H;
            if (proposedBottom > containerH - 15) {
                totalLen = Math.max(25, containerH - anchorY - LABEL_H - 15);
            }
        }

        const lineEndY = direction * totalLen; // negative = up, positive = down

        // Update line
        line.setAttribute('x1', '0');
        line.setAttribute('y1', '0');
        line.setAttribute('x2', '0');
        line.setAttribute('y2', lineEndY);

        // Position foreignObject
        //   X: center label at 0 (candle x), flip if near right edge
        let foX = -LABEL_W / 2;
        if (xCoord + LABEL_W / 2 > containerW - 4) foX = -LABEL_W;
        if (xCoord - LABEL_W / 2 < 4) foX = 0;

        //   Y: label top = lineEndY - LABEL_H (if above) or lineEndY (if below)
        const foY = isAbove ? lineEndY - LABEL_H : lineEndY;

        fo.setAttribute('x', foX);
        fo.setAttribute('y', foY);
        fo.setAttribute('width', LABEL_W);
        fo.setAttribute('height', LABEL_H);
    });
}


// ---- Display news timeline panel ----
function displayNewsPanel(newsMarkers) {
    console.log('[NEWS PANEL] Creating news panel with', newsMarkers.length, 'items');

    // Find or create panel container
    let panel = document.getElementById('chartNewsPanel');
    if (!panel) {
        const container = document.getElementById('lwChartContainer').parentElement;
        panel = document.createElement('div');
        panel.id = 'chartNewsPanel';
        panel.style.cssText = `
            margin-top: 30px;
            background: var(--bg-card, rgba(18, 18, 28, 0.75));
            border: 1px solid rgba(108, 99, 255, 0.2);
            border-radius:12px;
            padding: 16px;
            max-height: 280px;
            overflow-y: auto;
        `;
        container.appendChild(panel);
    }

    // Sort by published time descending
    const sorted = [...newsMarkers].sort((a, b) => new Date(b.published) - new Date(a.published));

    let html = `
        <div style="font-size: 0.85rem; font-weight: 700; color: #6c63ff; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px;">
            📰 News Feed (${sorted.length} items)
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px;">
    `;

    for (const news of sorted) {
        const timeStr = new Date(news.published).toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true
        });
        const pairsStr = Array.isArray(news.affected_stocks)
            ? news.affected_stocks.join(', ')
            : 'N/A';

        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        const titleCol = isLight ? '#0f172a' : '#e0e0e0';
        const metaCol = isLight ? '#555570' : '#888';
        const bgNormal = isLight ? 'rgba(108, 99, 255, 0.05)' : 'rgba(108, 99, 255, 0.08)';
        const bgHover = isLight ? 'rgba(108, 99, 255, 0.12)' : 'rgba(108, 99, 255, 0.15)';
        const borderNormal = isLight ? 'rgba(108, 99, 255, 0.1)' : 'rgba(108, 99, 255, 0.2)';
        const borderHover = isLight ? 'rgba(108, 99, 255, 0.3)' : 'rgba(108, 99, 255, 0.4)';

        html += `
            <div style="
                background: ${bgNormal};
                border: 1px solid ${borderNormal};
                border-radius: 8px;
                padding: 10px 12px;
                cursor: pointer;
                transition: all 0.2s ease;
            " onmouseover="this.style.background='${bgHover}'; this.style.borderColor='${borderHover}'"
               onmouseout="this.style.background='${bgNormal}'; this.style.borderColor='${borderNormal}'">
                <div style="font-size: 0.78rem; font-weight: 700; color: ${titleCol}; margin-bottom: 4px; line-height: 1.3;">
                    ${escapeHtml(news.title)}
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.7rem; color: ${metaCol};">${timeStr}</span>
                    <span style="
                        font-size: 0.65rem;
                        background: #6c63ff;
                        color: #fff;
                        padding: 2px 8px;
                        border-radius: 4px;
                        font-weight: 600;
                    ">${escapeHtml(pairsStr)}</span>
                </div>
            </div>
        `;
    }

    html += '</div>';
    panel.innerHTML = html;
}


async function fetchCandleData(symbol) {
    const container = document.getElementById('lwChartContainer');
    const loadingMsg = document.getElementById('chartLoadingMsg');

    try {
        const url = `${API_BASE}/api/nse/candles?symbol=${encodeURIComponent(symbol)}&limit=500`;
        const res = await apiFetch(url);
        const json = await res.json();

        if (json.status !== 'success' || !json.data || json.data.length === 0) {
            if (loadingMsg) {
                loadingMsg.style.display = 'block';
                loadingMsg.innerHTML = `
                    <div style="display:flex;flex-direction:column;align-items:center;gap:12px;padding:40px;">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="rgba(108,99,255,0.4)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
                        <div style="color:#f59e0b;font-weight:600;font-size:0.92rem;">No data for <strong style="color:#f0f0f0;">${symbol.split(':')[1] || symbol}</strong></div>
                        <div style="color:#888;font-size:0.8rem;text-align:center;max-width:280px;">The pipeline is still collecting candles for this pair. Try again in a few minutes or select a major pair like TCS.</div>
                    </div>`;
            }
            if (container) container.style.display = 'none';
            return null;
        }

        // Data comes newest-first from API — reverse to oldest-first
        const raw = json.data.slice().reverse();

        // Parse & deduplicate
        const seenTimes = new Set();
        const candles = [];
        for (const c of raw) {
            const t = parseToUnixSec(c.time);
            if (seenTimes.has(t)) continue;
            seenTimes.add(t);
            candles.push({ time: t, open: c.open, high: c.high, low: c.low, close: c.close });
        }
        candles.sort((a, b) => a.time - b.time);
        return candles;

    } catch (err) {
        if (loadingMsg) {
            loadingMsg.style.display = 'block';
            loadingMsg.innerHTML = `<div style="color:#ff4757;">❌ Network error: ${escapeHtml(err.message)}</div>`;
        }
        if (container) container.style.display = 'none';
        return null;
    }
}

function setChartLoading(symbol) {
    const loadingMsg = document.getElementById('chartLoadingMsg');
    const container = document.getElementById('lwChartContainer');
    if (loadingMsg) {
        loadingMsg.style.display = 'block';
        loadingMsg.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:center;gap:10px;padding:40px;color:#9ca3af;">
                <div style="width:20px;height:20px;border:2px solid rgba(108,99,255,0.3);border-top-color:#6c63ff;border-radius:50%;animation:spin 0.7s linear infinite;flex-shrink:0;"></div>
                Loading <strong style="color:#f0f0f0;">${symbol.split(':')[1] || symbol}</strong>…
            </div>`;
    }
    if (container) container.style.display = 'none';
}

// ---- Render chart with LightweightCharts ----
function renderLWChart(candleData) {
    const container = document.getElementById('lwChartContainer');
    const loadingMsg = document.getElementById('chartLoadingMsg');
    if (!container) return;

    // Destroy previous chart instance
    if (lwChart) {
        try { lwChart.remove(); } catch (_) { }
        lwChart = null;
        lwCandleSeries = null;
    }

    container.style.display = 'block';
    if (loadingMsg) loadingMsg.style.display = 'none';

    // Ensure container has measurable size
    const w = container.clientWidth || 900;
    const h = 460;

    lwChart = LightweightCharts.createChart(container, {
        width: w,
        height: h,
        layout: {
            background: { 
                type: LightweightCharts.ColorType.Solid, 
                color: document.documentElement.getAttribute('data-theme') === 'light' ? '#ffffff' : '#0b0f19' 
            },
            textColor: document.documentElement.getAttribute('data-theme') === 'light' ? '#131722' : '#7d8490',
            fontFamily: "'Inter', -apple-system, sans-serif",
            fontSize: 11,
        },
        grid: {
            vertLines: { 
                color: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.035)', 
                style: LightweightCharts.LineStyle.Dashed 
            },
            horzLines: { 
                color: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.035)', 
                style: LightweightCharts.LineStyle.Dashed 
            },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: 'rgba(108,99,255,0.6)',
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: '#6c63ff',
            },
            horzLine: {
                color: 'rgba(108,99,255,0.6)',
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: '#6c63ff',
            },
        },
        rightPriceScale: {
            borderColor: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)',
            textColor: document.documentElement.getAttribute('data-theme') === 'light' ? '#131722' : '#7d8490',
            scaleMargins: { top: 0.08, bottom: 0.08 },
        },
        timeScale: {
            borderColor: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)',
            textColor: document.documentElement.getAttribute('data-theme') === 'light' ? '#131722' : '#7d8490',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 8,
            barSpacing: 10,
            tickMarkFormatter: (time, tickMarkType, locale) => {
                return istTickFormatter.format(new Date(time * 1000));
            },
        },
        localization: {
            timeFormatter: (time) => istDateFormatter.format(new Date(time * 1000)),
        },
        handleScroll: { mouseWheel: true, pressedMouseMove: true },
        handleScale: { mouseWheel: true, pinch: true },
    });

    // Handle clicks on news markers
    lwChart.subscribeClick(param => {
        if (!param.time || !chartNewsByTime[param.time]) return;

        const newsItems = chartNewsByTime[param.time];
        if (newsItems.length === 1) {
            openModal(newsItems[0]);
        } else {
            // Simplified: open the first one if multiple exist at the same bucket
            openModal(newsItems[0]);
        }
    });

    // Candlestick series (forex style: green up, red down)
    lwCandleSeries = lwChart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
        priceFormat: {
            type: 'price',
            precision: 5,
            minMove: 0.00001,
        },
    });

    lwCandleSeries.setData(candleData);
    lwChart.timeScale().fitContent();

    // Live crosshair tooltip
    attachCrosshairTooltip();

    // Create news marker tooltip element
    let newsTooltip = document.getElementById('newsMarkerTooltip');
    if (!newsTooltip) {
        newsTooltip = document.createElement('div');
        newsTooltip.id = 'newsMarkerTooltip';
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        newsTooltip.style.cssText = `
            position: absolute;
            top: 60px;
            right: 12px;
            z-index: 11;
            background: ${isLight ? 'rgba(255, 255, 255, 0.98)' : 'rgba(11, 15, 25, 0.95)'};
            border: 1px solid ${isLight ? 'rgba(108, 99, 255, 0.2)' : 'rgba(108, 99, 255, 0.4)'};
            color: ${isLight ? '#1a1a2e' : '#f0f0f5'};
            border-radius: 8px;
            padding: 12px;
            font-family: 'Inter', sans-serif;
            display: none;
            max-width: 340px;
            box-shadow: 0 8px 32px ${isLight ? 'rgba(0, 0, 0, 0.1)' : 'rgba(0, 0, 0, 0.6)'};
            backdrop-filter: blur(8px);
            overflow-y: auto;
            max-height: 180px;
        `;
        container.style.position = 'relative';
        container.appendChild(newsTooltip);
    }

    // Responsive resize observer
    if (window._chartObs) window._chartObs.disconnect();
    window._chartObs = new ResizeObserver(() => {
        if (lwChart && container.clientWidth > 0) {
            lwChart.applyOptions({ width: container.clientWidth });
        }
    });
    window._chartObs.observe(container);
}

// ---- Crosshair OHLC Tooltip ----
function attachCrosshairTooltip() {
    // Remove old tooltip if present
    const old = document.getElementById('chartCrosshairTooltip');
    if (old) old.remove();

    const tooltip = document.createElement('div');
    tooltip.id = 'chartCrosshairTooltip';
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    tooltip.style.cssText = `
        position:absolute; top:12px; left:12px; z-index:10;
        background:${isLight ? 'rgba(255, 255, 255, 0.95)' : 'rgba(11, 15, 25, 0.92)'}; 
        border:1px solid ${isLight ? 'rgba(108, 99, 255, 0.2)' : 'rgba(108, 99, 255, 0.35)'};
        color: ${isLight ? '#1a1a2e' : '#f0f0f5'};
        border-radius:8px; padding:8px 12px; font-size:0.78rem; font-family:'Inter',sans-serif;
        pointer-events:none; display:none; backdrop-filter:blur(4px);
        box-shadow:0 4px 20px ${isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(0, 0, 0, 0.5)'};
    `;
    const container = document.getElementById('lwChartContainer');
    container.style.position = 'relative';
    container.appendChild(tooltip);

    lwChart.subscribeCrosshairMove(param => {
        if (!param.time || !param.seriesData || !param.seriesData.size) {
            tooltip.style.display = 'none';
            return;
        }
        const bar = param.seriesData.get(lwCandleSeries);
        if (!bar) { tooltip.style.display = 'none'; return; }

        const timeStr = istDateFormatter.format(new Date(param.time * 1000));

        const isUp = bar.close >= bar.open;
        const col = isUp ? '#26a69a' : '#ef5350';
        const chg = ((bar.close - bar.open) / bar.open * 100).toFixed(3);
        const sign = isUp ? '+' : '';

        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        const labelCol = isLight ? '#555570' : '#9ca3af';
        const valCol = isLight ? '#1a1a2e' : '#e0e0e0';

        tooltip.style.display = 'block';
        tooltip.innerHTML = `
            <div style="color:${labelCol};font-size:0.7rem;margin-bottom:4px;">${timeStr} · 3m</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 16px;font-size:0.78rem;">
                <span style="color:${labelCol};">O</span><span style="color:${valCol};font-weight:600;">${bar.open.toFixed(5)}</span>
                <span style="color:${labelCol};">H</span><span style="color:#26a69a;font-weight:600;">${bar.high.toFixed(5)}</span>
                <span style="color:${labelCol};">L</span><span style="color:#ef5350;font-weight:600;">${bar.low.toFixed(5)}</span>
                <span style="color:${labelCol};">C</span><span style="color:${col};font-weight:700;">${bar.close.toFixed(5)}</span>
                <span style="color:${labelCol};">Chg</span><span style="color:${col};font-weight:600;">${sign}${chg}%</span>
            </div>
        `;
    });
}

// ---- Update symbol info row ----
function updateChartStats(symbol, candles) {
    if (!candles || !candles.length) return;
    const last = candles[candles.length - 1];
    const first = candles[0];

    const symLabel = document.getElementById('chartSymbolLabel');
    const priceEl = document.getElementById('chartLastPrice');
    const chgEl = document.getElementById('chartPriceChange');

    if (symLabel) symLabel.textContent = symbol.split(':')[1] || symbol;

    const price = last.close;
    const priceStr = price < 10 ? price.toFixed(5) : price >= 1000 ? price.toFixed(2) : price.toFixed(4);
    if (priceEl) priceEl.textContent = priceStr;

    if (chgEl && first) {
        const chg = ((last.close - first.open) / first.open * 100).toFixed(3);
        const pos = parseFloat(chg) >= 0;
        chgEl.textContent = `${pos ? '+' : ''}${chg}%`;
        chgEl.style.cssText = `font-size:0.78rem;font-weight:700;padding:3px 8px;border-radius:5px;
            color:${pos ? '#26a69a' : '#ef5350'};
            background:${pos ? 'rgba(38,166,154,0.12)' : 'rgba(239,83,80,0.12)'};`;
    }
}

// Inject animation keyframes once
(function () {
    if (document.getElementById('chartStyleTag')) return;
    const style = document.createElement('style');
    style.id = 'chartStyleTag';
    style.textContent = `
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes livePulse {
            0%, 100% { opacity:1; transform:scale(1); }
            50% { opacity:0.4; transform:scale(0.8); }
        }
    `;
    document.head.appendChild(style);
})();





// ---- Market Status Check ----
let dynamicHolidays = null;

async function fetchHolidays() {
    try {
        const res = await apiFetch(`${API_BASE}/api/nse/holidays`);
        const json = await res.json();
        if (json.status === 'success') {
            dynamicHolidays = json.data;
        }
    } catch (e) {
        console.error('Failed to fetch holidays:', e);
    }
}

function updateMarketStatus() {
    const badge = document.getElementById('chartLiveBadge');
    if (!badge) return;

    // Use dynamically fetched holidays if available, else a minimal fallback list
    const holidays = dynamicHolidays || {
        "2026-03-31": "Shri Mahavir Jayanti"
    };

    // Get current time in IST
    const now = new Date();
    const istTimeString = now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' });
    const istTime = new Date(istTimeString);

    const year = istTime.getFullYear();
    const month = String(istTime.getMonth() + 1).padStart(2, '0');
    const date = String(istTime.getDate()).padStart(2, '0');
    const dateStr = `${year}-${month}-${date}`;

    const day = istTime.getDay(); // 0 is Sunday, 6 is Saturday
    const hours = istTime.getHours();
    const minutes = istTime.getMinutes();

    // Convert to minutes since midnight for easy comparison
    const timeInMins = hours * 60 + minutes;
    const openInMins = 9 * 60 + 15; // 9:15 AM
    const closeInMins = 15 * 60 + 32; // 3:32 PM

    const isWeekend = (day === 0 || day === 6);
    const isMarketHours = (timeInMins >= openInMins && timeInMins <= closeInMins);
    const holidayName = holidays[dateStr];

    if (holidayName) {
        badge.innerHTML = `<span style="width:7px;height:7px;border-radius:50%;background:#ef5350;"></span>CLOSED (${holidayName})`;
        badge.style.color = '#ef5350';
        badge.style.background = 'rgba(239,83,80,0.12)';
        badge.style.borderColor = 'rgba(239,83,80,0.3)';
    } else if (isWeekend || !isMarketHours) {
        badge.innerHTML = `<span style="width:7px;height:7px;border-radius:50%;background:#ef5350;"></span>CLOSED`;
        badge.style.color = '#ef5350';
        badge.style.background = 'rgba(239,83,80,0.12)';
        badge.style.borderColor = 'rgba(239,83,80,0.3)';
    } else {
        badge.innerHTML = `<span style="width:7px;height:7px;border-radius:50%;background:#00d464;animation:livePulse 1.4s infinite;"></span>LIVE`;
        badge.style.color = '#00d464';
        badge.style.background = 'rgba(0,212,100,0.12)';
        badge.style.borderColor = 'rgba(0,212,100,0.3)';
    }
}

// Call on load and check every minute
setInterval(updateMarketStatus, 60000);
setTimeout(updateMarketStatus, 500);

// ====================================
// EVENT DETAIL PANEL FUNCTIONS (NEW)
// ====================================

let eventDetailData = {
    eventId: null,
    eventTitle: null,
    articles: [],
    filteredArticles: []
};

function showEventDetail(eventId, eventTitle, articleCount, latestUpdate) {
    const overlay = document.getElementById('eventDetailOverlay');
    const titleEl = document.getElementById('eventDetailTitle');
    const metaEl = document.getElementById('eventDetailMeta');
    const countEl = document.getElementById('eventDetailArticleCount');
    const updateEl = document.getElementById('eventDetailLatestUpdate');

    eventDetailData.eventId = eventId;
    eventDetailData.eventTitle = eventTitle;

    titleEl.textContent = eventTitle;
    countEl.textContent = articleCount;

    const updateDate = new Date(latestUpdate);
    const timeStr = updateDate.toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'Asia/Kolkata'
    });
    updateEl.textContent = timeStr;

    const updateTime = timeAgo(latestUpdate);
    metaEl.innerHTML = `<span>${articleCount} articles</span><span>Updated ${updateTime}</span>`;

    // Fetch event-specific news
    fetchEventNews(eventId);

    overlay.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeEventDetail() {
    const overlay = document.getElementById('eventDetailOverlay');
    overlay.classList.remove('active');
    document.body.style.overflow = '';
    eventDetailData.eventId = null;
}

async function fetchEventNews(eventId) {
    try {
        const response = await apiFetch(`${API_BASE}/api/indian_news?event_id=${encodeURIComponent(eventId)}&limit=100`);
        const json = await response.json();

        if (json.status === 'success' && json.data) {
            eventDetailData.articles = json.data;
            eventDetailData.filteredArticles = json.data;
            renderEventNews(eventDetailData.filteredArticles);
        }
    } catch (e) {
        console.error("Failed to fetch event news", e);
    }
}

function renderEventNews(articles) {
    const grid = document.getElementById('eventDetailNewsGrid');
    const emptyState = document.getElementById('eventDetailEmptyState');

    if (!articles || articles.length === 0) {
        grid.innerHTML = '';
        emptyState.style.display = 'flex';
        return;
    }

    emptyState.style.display = 'none';

    let html = '';
    articles.forEach((article, idx) => {
        if (!article || !article.id) return; // Skip invalid articles

        const timeAgo_str = timeAgo(article.published);
        const relevance = (article.news_relevance || 'neutral').toLowerCase();
        const relevanceEmoji = getRelevanceEmoji(relevance);
        const sourceDisplay = escapeHtml(article.source || 'News Source');
        const titleDisplay = escapeHtml(article.title || 'Untitled Article');
        // Show full description without truncation
        const fullDesc = article.description || article.summary || 'No description available';
        const descDisplay = escapeHtml(fullDesc);
        const hasImage = article.image_url && article.image_url.trim().length > 0;
        const imageUrl = hasImage ? `url('${escapeHtml(article.image_url)}')` : '';
        const impactLevel = article.news_impact_level ? escapeHtml(article.news_impact_level).toLowerCase() : 'neutral';
        let impactClass = 'impact-neutral';
        if (impactLevel === 'positive' || impactLevel === 'high') impactClass = 'impact-positive';
        if (impactLevel === 'negative' || impactLevel === 'low') impactClass = 'impact-negative';

        // Get category
        const category = article.news_category || article.category || 'General';
        const categoryDisplay = escapeHtml(category);
        let categoryClass = 'category-general';
        const catLower = category.toLowerCase();
        if (catLower.includes('market') || catLower.includes('stock')) categoryClass = 'category-market';
        if (catLower.includes('tech') || catLower.includes('technology')) categoryClass = 'category-tech';
        if (catLower.includes('economy') || catLower.includes('macro')) categoryClass = 'category-economy';
        if (catLower.includes('crypto')) categoryClass = 'category-crypto';
        if (catLower.includes('forex') || catLower.includes('currency')) categoryClass = 'category-forex';

        // Map relevance to CSS class
        let relevanceClass = 'neutral';
        if (relevance.includes('noisy') || relevance.includes('noise')) relevanceClass = 'noisy';
        else if (relevance.includes('useful')) relevanceClass = 'useful';
        else if (relevance.includes('medium')) relevanceClass = 'medium';
        else if (relevance.includes('very high')) relevanceClass = 'useful';

        // Mark noisy articles
        const isNoisy = relevance === 'noisy' || relevance === 'noise';
        const noiseClass = isNoisy ? ' noisy-article' : '';

        html += `
            <div class="event-news-card${noiseClass}" data-article-id="${article.id}" data-relevance="${relevance}" onclick="openArticleDetail(event, ${idx})">
                ${hasImage ? `<div class="event-news-card-image" style="background-image: ${imageUrl}; background-size: cover; background-position: center;"></div>` : ''}
                <div class="event-news-card-content">
                    <div class="event-news-card-meta">
                        <span class="event-news-source">${sourceDisplay}</span>
                        <span class="event-news-time">${timeAgo_str}</span>
                    </div>
                    <div class="event-news-card-badges">
                        <span class="badge-category ${categoryClass}">${categoryDisplay}</span>
                        <span class="badge-relevance ${relevanceClass}">${relevanceEmoji} ${relevance}</span>
                    </div>
                    <h3 class="event-news-card-title">${titleDisplay}</h3>
                    <p class="event-news-card-desc">${descDisplay}</p>
                    
                </div>
                <div class="event-news-card-action">
                    <button class="read-more-btn" onclick="event.stopPropagation();">Read Full →</button>
                </div>
            </div>
        `;
    });

    grid.innerHTML = html;
}

function openArticleDetail(event, articleIdx) {
    event.stopPropagation();
    if (articleIdx < 0 || articleIdx >= eventDetailData.filteredArticles.length) return;
    const article = eventDetailData.filteredArticles[articleIdx];
    openModal(article);
}

function getRelevanceEmoji(relevance) {
    const map = {
        'high useful': '🔥',
        'useful': '🟢',
        'medium': '🟡',
        'neutral': '⚖️',
        'noisy': '🔴'
    };
    return map[relevance] || '📊';
}

// Event detail panel search
document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('eventDetailSearchInput');
    const relevanceSelect = document.getElementById('eventDetailRelevanceFilter');

    if (searchInput) {
        searchInput.addEventListener('input', debounce(filterEventNews, SEARCH_DEBOUNCE));
    }

    if (relevanceSelect) {
        relevanceSelect.addEventListener('change', filterEventNews);
    }

    // Close on overlay click
    const overlay = document.getElementById('eventDetailOverlay');
    if (overlay) {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                closeEventDetail();
            }
        });
    }
});

function filterEventNews() {
    const searchInput = document.getElementById('eventDetailSearchInput');
    const relevanceSelect = document.getElementById('eventDetailRelevanceFilter');

    const searchQuery = (searchInput?.value || '').toLowerCase();
    const relevanceFilter = relevanceSelect?.value || 'all';

    eventDetailData.filteredArticles = eventDetailData.articles.filter(article => {
        const matchSearch = !searchQuery ||
            article.title.toLowerCase().includes(searchQuery) ||
            (article.description || '').toLowerCase().includes(searchQuery) ||
            (article.source || '').toLowerCase().includes(searchQuery);

        const matchRelevance = relevanceFilter === 'all' ||
            (article.news_relevance || '').toLowerCase() === relevanceFilter.toLowerCase();

        return matchSearch && matchRelevance;
    });

    renderEventNews(eventDetailData.filteredArticles);
}

// Update renderEvents to call the new event detail function
function updateEventRenderFunction() {
    // This modifies the event click handler to show detail panel instead
    const events = document.querySelectorAll('.event-card');
    events.forEach(card => {
        card.style.cursor = 'pointer';
        card.addEventListener('click', function () {
            const eventId = this.getAttribute('data-event-id');
            const eventTitle = this.getAttribute('data-event-title');
            const articleCount = this.getAttribute('data-article-count');
            const latestUpdate = this.getAttribute('data-latest-update');

            showEventDetail(eventId, eventTitle, articleCount, latestUpdate);
        });
    });
}

function debounce(func, wait) {
    let timeout;
    return function (...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}
