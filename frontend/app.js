// =========================================
// CryptoWire — Frontend Logic (Production)
// =========================================

const API_BASE = '';  // Same origin
const REFRESH_INTERVAL = 30_000; // 30 seconds
const SEARCH_DEBOUNCE = 300;
const SCROLL_TOP_THRESHOLD = 400;
const CONNECTION_FAIL_THRESHOLD = 2;

let currentSource = 'all';
let searchQuery = '';
let newsData = [];
let sourceFilters = [];
const analyzingArticles = new Set();

let showOnlyAnalyzed = false;
let currentRelevance = 'all';
let isFetching = false;
let consecutiveFailures = 0;
let searchDebounceTimer = null;

// ---- DOM Elements ----
const newsGrid = document.getElementById('newsGrid');
const emptyState = document.getElementById('emptyState');
const emptyStateTitle = document.getElementById('emptyStateTitle');
const emptyStateMsg = document.getElementById('emptyStateMsg');
const filtersContainer = document.getElementById('filtersContainer');
const relevanceFilter = document.getElementById('relevanceFilter');
const analyzedToggle = document.getElementById('analyzedToggle');
const articleCount = document.getElementById('articleCount');
const clockEl = document.getElementById('clock');
const refreshIndicator = document.getElementById('refreshIndicator');
const searchInput = document.getElementById('searchInput');
const searchClear = document.getElementById('searchClear');
const scrollTopBtn = document.getElementById('scrollTopBtn');
const connectionBanner = document.getElementById('connectionBanner');
const toastContainer = document.getElementById('toastContainer');
const themeToggle = document.getElementById('themeToggle');

// ---- Theme Toggle ----
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cw-theme', theme);
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
    clockEl.textContent = now.toLocaleTimeString('en-US', options) + ' IST';
}

setInterval(updateClock, 1000);
updateClock();

// ---- Time Ago ----
function timeAgo(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
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
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: true,
        timeZone: 'Asia/Kolkata'
    });
}

// ---- HTML Escaping ----
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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
    let markets = article.affected_markets || {};
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

function renderRelevanceBadge(relevance) {
    if (!relevance) return '';
    const rel = relevance.toLowerCase();
    let cssClass = 'rel-neutral';
    if (rel.includes('noisy')) cssClass = 'rel-noisy';
    else if (rel.includes('very high')) cssClass = 'rel-very-high';
    else if (rel.includes('crypto') || rel.includes('forex') || rel.includes('useful')) cssClass = 'rel-useful';

    const label = relevance.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    return `<span class="relevance-badge ${cssClass}"><span class="relevance-dot"></span>${escapeHtml(label)}</span>`;
}

function renderCategoryBadge(category) {
    if (!category || category.toLowerCase() === 'none') return '';
    const label = category.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    return `<span class="category-badge">${escapeHtml(label)}</span>`;
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
    if (article.is_new_information == null) return '';
    if (article.is_new_information) {
        return `<span class="new-info-badge new-info-new">🆕 NEW INFO</span>`;
    }
    return `<span class="new-info-badge new-info-priced">📊 PRICED IN</span>`;
}


function renderImpactBadge(article) {
    if (!article.impact_score) {
        const isAnalyzing = analyzingArticles.has(article.id);
        const btnState = isAnalyzing ? 'disabled' : '';
        const btnClass = isAnalyzing ? 'analyzing' : '';
        const btnText = isAnalyzing ? '<div class="analyzing-spinner-sm"></div> Analyzing…' : '✨ Analyze';

        return `
            <button class="analyze-btn analyze-btn-sm ${btnClass}" data-id="${article.id}" ${btnState} onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">
                 ${btnText}
            </button>
        `;
    }

    const scoreClass = getScoreClass(article.impact_score);
    const scoreLabel = getScoreLabel(article.impact_score);
    return `<span class="impact-score-badge ${scoreClass}">⚡ ${article.impact_score}/10 · ${scoreLabel}</span>`;
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
        } catch (e) {}
    }

    // Try fallback structure from flat DB
    if (!suggestions && article.suggestions_data) {
        if (typeof article.suggestions_data === 'object') {
            suggestions = article.suggestions_data;
        } else if (typeof article.suggestions_data === 'string') {
            try { suggestions = JSON.parse(article.suggestions_data); } catch (e) {}
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

    const scoreClass = getScoreClass(article.impact_score);
    const markets = parseAffectedMarkets(article);
    const modeInfo = getMarketModeInfo(article.market_mode);
    const usdInfo = getBiasInfo(article.usd_bias);
    const cryptoInfo = getBiasInfo(article.crypto_bias);
    const confInfo = getConfidenceInfo(article.confidence);

    return `
        <div class="card-analysis">
            <div class="card-analysis-header">
                <span class="impact-score-badge ${scoreClass}">⚡ ${article.impact_score}/10</span>
                <span class="market-mode-badge ${modeInfo.cssClass}">${modeInfo.icon} ${modeInfo.label}</span>
            </div>
            <div class="card-analysis-meta">
                <span class="bias-pill ${usdInfo.cssClass}">${usdInfo.arrow} USD ${usdInfo.label}</span>
                <span class="bias-pill ${cryptoInfo.cssClass}">${cryptoInfo.arrow} Crypto ${cryptoInfo.label}</span>
                <span class="confidence-pill ${confInfo.cssClass}">${confInfo.icon} ${confInfo.label}</span>
            </div>
            <p class="card-analysis-summary">${escapeHtml(article.impact_summary || '')}</p>
            <div class="card-analysis-footer-row">
                <span class="execution-window-badge">⏱ ${escapeHtml(article.execution_window || article.impact_duration || 'N/A')}</span>
                <div class="market-bars">
                    ${renderMarketBar('Global', markets.global || 0, 'bar-global')}
                    ${renderMarketBar('Forex', markets.forex || 0, 'bar-forex')}
                    ${renderMarketBar('Crypto', markets.crypto || 0, 'bar-crypto')}
                </div>
            </div>
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
        const res = await fetch(`${API_BASE}/api/analyze/${newsId}`, { method: 'POST' });
        const json = await res.json();

        if (json.status === 'success') {
            showToast('Analysis complete — impact score assigned', 'success');
            await fetchNews();
            // Re-open modal with updated article if modal was open
            const updatedArticle = newsData.find(a => a.id === newsId);
            if (updatedArticle && modalOverlay.classList.contains('active')) {
                openModal(updatedArticle);
            }
        } else {
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

function getAnalysisData(article) {
    let data = article.analysis_data;
    if (!data) return null;
    if (typeof data === 'string') {
        try { data = JSON.parse(data); } catch { return null; }
    }
    if (typeof data === 'object' && data !== null) return data;
    return null;
}

function openModal(article) {
    // Try full JSONB analysis_data first, then build from flat DB fields
    let analysis = getAnalysisData(article);

    // If no analysis_data but article IS analyzed, build from flat DB fields
    if (!analysis && (article.analyzed || article.impact_score)) {
        const flatMarkets = parseAffectedMarkets(article);
        analysis = {
            event_metadata: { title: article.title, source: article.source },
            event_classification: {},
            text_signal_analysis: {},
            core_impact_assessment: {
                primary_impact_score: article.impact_score || 0,
                market_category_scores: {
                    forex: flatMarkets.forex || 0,
                    crypto: flatMarkets.crypto || 0,
                    global_equities: flatMarkets.global || flatMarkets.global_equities || 0
                }
            },
            market_regime_context: {
                dominant_market_regime: article.market_mode || '',
                liquidity_condition_assumption: article.dollar_liquidity_state || '',
                volatility_expectation: article.volatility_regime || ''
            },
            directional_bias: { forex: [], crypto: [], global_equities: [] },
            time_modeling: { reaction_speed: article.execution_window || '', impact_duration: article.impact_duration || '' },
            probability_and_confidence: {
                direction_probability_pct: article.conviction_score || 0,
                overall_confidence_score: article.confidence || 0,
                confidence_breakdown: {}
            },
            risk_guidance: { suggested_exposure_range_pct: article.position_size_percent || '' },
            event_fatigue_analysis: {},
            scenario_analysis: {},
            self_critique: {},
            macro_linkage_reasoning: {},
            executive_summary: article.impact_summary || '',
            reasoning_summary: article.research_text || ''
        };
        if (article.usd_bias) {
            analysis.directional_bias.forex.push({ pair: 'USD (DXY)', direction: article.usd_bias, impact_strength: article.impact_score || 0, confidence: 0, reason: '' });
        }
        if (article.crypto_bias) {
            analysis.directional_bias.crypto.push({ asset: 'Crypto Market', direction: article.crypto_bias, impact_strength: article.impact_score || 0, confidence: 0, reason: '' });
        }
    }

    let analysisHtml = '';
    if (analysis) {
        const core = analysis.core_impact_assessment || {};
        const score = core.primary_impact_score || 0;
        const scoreClass = getScoreClass(score);
        const scoreLabel = getScoreLabel(score);
        const regime = analysis.market_regime_context || {};
        const modeInfo = getMarketModeInfo(regime.dominant_market_regime);
        const textSig = analysis.text_signal_analysis || {};
        const prob = analysis.probability_and_confidence || {};
        const timeMod = analysis.time_modeling || {};
        const risk = analysis.risk_guidance || {};
        const fatigue = analysis.event_fatigue_analysis || {};
        const scenario = analysis.scenario_analysis || {};
        const critique = analysis.self_critique || {};
        const classification = analysis.event_classification || {};
        const directionalBias = analysis.directional_bias || {};
        const confBreakdown = prob.confidence_breakdown || {};
        const markets = core.market_category_scores || {};

        // Build directional bias cards
        let biasGroupsHtml = '';
        for (const [marketType, items] of Object.entries(directionalBias)) {
            if (Array.isArray(items) && items.length > 0) {
                const validItems = items.filter(item => {
                    const name = (item.pair || item.asset || item.index || '').toLowerCase();
                    return name && name !== 'none' && name !== 'n/a' && name !== 'null';
                });
                if (validItems.length === 0) continue;

                const rows = validItems.map(item => {
                    const assetName = escapeHtml(item.pair || item.asset || item.index || '');
                    const dir = item.direction || '';
                    const dirLower = dir.toLowerCase();
                    const dirCls = dirLower === 'bullish' ? 'dir-bullish' : dirLower === 'bearish' ? 'dir-bearish' : 'dir-neutral';
                    const borderColor = dirLower === 'bullish' ? 'rgba(0, 212, 170, 0.4)' : dirLower === 'bearish' ? 'rgba(255, 71, 87, 0.4)' : 'rgba(255, 193, 7, 0.4)';
                    const movePct = item.expected_move_pct || '';
                    const cardTop = `<div class="forex-pair-card" style="border-left-color:${borderColor}">
                        <div class="forex-pair-header">
                            <span class="forex-pair-name">${assetName}</span>
                            ${dir ? `<span class="forex-pair-dir ${dirCls}">${escapeHtml(dir)}</span>` : ''}
                            ${movePct ? `<span style="margin-left:auto; font-size:0.72rem; font-weight:700; color:${dirLower === 'bullish' ? '#00d4aa' : dirLower === 'bearish' ? '#ff4757' : '#ffc107'}; background:${dirLower === 'bullish' ? 'rgba(0,212,170,0.1)' : dirLower === 'bearish' ? 'rgba(255,71,87,0.1)' : 'rgba(255,193,7,0.1)'}; padding:2px 8px; border-radius:4px;">${dirLower === 'bearish' ? '↓' : dirLower === 'bullish' ? '↑' : '→'} ${escapeHtml(String(movePct))}</span>` : ''}
                        </div>`;

                    const parseLevel = (val) => {
                        if (typeof val === 'string') {
                            const v = val.toLowerCase();
                            if (v.includes('high')) return { pct: 90, txt: 'High' };
                            if (v.includes('medium')) return { pct: 60, txt: 'Medium' };
                            if (v.includes('low')) return { pct: 30, txt: 'Low' };
                            return { pct: 50, txt: String(val) };
                        }
                        const num = Number(val) || 0;
                        return null; // Signals numeric fallback
                    };

                    const rawStr = item.impact_strength;
                    const strObj = parseLevel(rawStr) || { pct: (Number(rawStr) || 0) * 10, txt: `${Number(rawStr) || 0}/10` };

                    const rawConf = item.confidence;
                    const confObj = parseLevel(rawConf) || { pct: Number(rawConf) || 0, txt: `${Number(rawConf) || 0}%` };

                    return cardTop + `
                        <div class="trade-card-strength">
                            <span class="trade-card-strength-label">Strength</span>
                            <div class="trade-card-strength-track"><div class="trade-card-strength-fill" style="width:${strObj.pct}%"></div></div>
                            <span class="trade-card-strength-val" style="text-transform: capitalize;">${strObj.txt}</span>
                        </div>
                        <div class="trade-card-strength">
                            <span class="trade-card-strength-label">Confidence</span>
                            <div class="trade-card-strength-track"><div class="trade-card-strength-fill" style="width:${confObj.pct}%"></div></div>
                            <span class="trade-card-strength-val" style="text-transform: capitalize;">${confObj.txt}</span>
                        </div>
                        ${item.expected_duration ? `<div style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem">⏱ ${escapeHtml(item.expected_duration)}</div>` : ''}
                        <p style="font-size:0.85rem; margin-top:0.5rem; color:var(--text-secondary)">${escapeHtml(item.reason || '')}</p>
                        
                        <!-- Inline Prediction Tracker Box -->
                        <div id="inline-pred-${article.id}-${encodeURIComponent(assetName)}" style="margin-top:12px"></div>
                    </div>`;
                }).join('');
                biasGroupsHtml += `
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title">${marketType.replace(/_/g, ' ').toUpperCase()} DIRECTIONAL BIAS</div>
                        <div class="forex-pairs-grid">${rows}</div>
                    </div>`;
            }
        }

        analysisHtml = `
            <div class="modal-divider"></div>
            <div class="analysis-panel">
                <div class="analysis-panel-header">
                    <div class="analysis-panel-title">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                        AI Market Analysis
                    </div>
                    <div class="analysis-panel-badges">
                        ${classification.shock_type ? `<span class="new-info-badge new-info-new">${escapeHtml(classification.shock_type)}</span>` : ''}
                        ${classification.event_type ? `<span class="new-info-badge new-info-priced">${escapeHtml(classification.event_type)}</span>` : ''}
                        <span class="confidence-pill conf-high">Conf: ${prob.overall_confidence_score || 0}/10</span>
                    </div>
                </div>

                <div class="analysis-score-row">
                    <div class="analysis-score-main">
                        <span class="analysis-score-number ${scoreClass}">${score}</span>
                        <div class="analysis-score-meta">
                            <span class="analysis-score-label">${scoreLabel}</span>
                            <span class="analysis-score-sub">out of 10</span>
                        </div>
                    </div>
                    <span class="market-mode-badge ${modeInfo.cssClass}">${modeInfo.icon} ${modeInfo.label}</span>
                </div>

                <div class="analysis-tabs">
                    <button class="analysis-tab active" onclick="document.querySelectorAll('.analysis-tab').forEach(t=>t.classList.remove('active'));this.classList.add('active');document.querySelectorAll('.analysis-tab-panel').forEach(p=>p.classList.remove('active'));document.getElementById('tab-overview').classList.add('active')">Overview</button>
                    <button class="analysis-tab" onclick="document.querySelectorAll('.analysis-tab').forEach(t=>t.classList.remove('active'));this.classList.add('active');document.querySelectorAll('.analysis-tab-panel').forEach(p=>p.classList.remove('active'));document.getElementById('tab-bias').classList.add('active')">Directional Bias</button>
                    <button class="analysis-tab" onclick="document.querySelectorAll('.analysis-tab').forEach(t=>t.classList.remove('active'));this.classList.add('active');document.querySelectorAll('.analysis-tab-panel').forEach(p=>p.classList.remove('active'));document.getElementById('tab-insights').classList.add('active')">Insights</button>
                    <button class="analysis-tab" onclick="document.querySelectorAll('.analysis-tab').forEach(t=>t.classList.remove('active'));this.classList.add('active');document.querySelectorAll('.analysis-tab-panel').forEach(p=>p.classList.remove('active'));document.getElementById('tab-suggestions').classList.add('active')">Suggestions</button>
                    <button class="analysis-tab" id="predTabBtn" style="display:none" onclick="document.querySelectorAll('.analysis-tab').forEach(t=>t.classList.remove('active'));this.classList.add('active');document.querySelectorAll('.analysis-tab-panel').forEach(p=>p.classList.remove('active'));document.getElementById('tab-predictions').classList.add('active')">Predictions</button>
                </div>

                <!-- TAB 1: Overview -->
                <div id="tab-overview" class="analysis-tab-panel active">
                    <div class="analysis-metrics-grid">
                        <div class="analysis-metric"><span class="analysis-metric-label">Hawkish/Dovish</span><span class="analysis-metric-value">${textSig.hawkish_dovish_score || 0}/10</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Risk On/Off</span><span class="analysis-metric-value">${textSig.risk_on_off_score || 0}/10</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Uncertainty</span><span class="analysis-metric-value">${textSig.uncertainty_intensity_score || 0}/10</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Surprise</span><span class="analysis-metric-value">${core.perceived_surprise_score || 0}/10</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Duration</span><span class="analysis-metric-value">${escapeHtml(timeMod.impact_duration || 'N/A')}</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Reaction</span><span class="analysis-metric-value">${escapeHtml(timeMod.reaction_speed || 'N/A')}</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Fatigue</span><span class="analysis-metric-value">${fatigue.fatigue_score || 0}/10</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Exposure</span><span class="analysis-metric-value">${escapeHtml(String(risk.suggested_exposure_range_pct || 'N/A'))}</span></div>
                        <div class="analysis-metric"><span class="analysis-metric-label">Probability</span><span class="analysis-metric-value">${prob.direction_probability_pct || 0}%</span></div>
                    </div>
                    <div class="analysis-summary-box mt-5">
                        <p><strong>Executive Summary:</strong> ${escapeHtml(analysis.executive_summary || '')}</p>
                        ${analysis.reasoning_summary ? `<p style="margin-top:0.5rem"><strong>Reasoning:</strong> ${escapeHtml(analysis.reasoning_summary)}</p>` : ''}
                    </div>
                    <div class="analysis-bars-section">
                        <div class="analysis-bars-title">Category Impacts</div>
                        <div class="market-bars">
                            ${renderMarketBar('Forex', markets.forex || 0, 'bar-forex')}
                            ${renderMarketBar('Crypto', markets.crypto || 0, 'bar-crypto')}
                            ${renderMarketBar('Equities', markets.global_equities || 0, 'bar-equities')}
                        </div>
                    </div>
                    ${(confBreakdown.text_clarity || confBreakdown.shock_magnitude || confBreakdown.cross_asset_logic_strength) ? `
                    <div class="analysis-bars-section" style="margin-top:8px">
                        <div class="analysis-bars-title">Confidence Breakdown</div>
                        <div class="market-bars">
                            ${renderMarketBar('Text Clarity', confBreakdown.text_clarity || 0, 'bar-forex')}
                            ${renderMarketBar('Shock Magnitude', confBreakdown.shock_magnitude || 0, 'bar-crypto')}
                            ${renderMarketBar('Cross-Asset Logic', confBreakdown.cross_asset_logic_strength || 0, 'bar-equities')}
                        </div>
                    </div>` : ''}
                </div>

                <!-- TAB 2: Directional Bias -->
                <div id="tab-bias" class="analysis-tab-panel">
                    ${biasGroupsHtml || '<p style="color:var(--text-muted); font-size:0.85rem; padding:12px 0">No directional bias data available.</p>'}
                </div>

                <!-- TAB 3: Insights -->
                <div id="tab-insights" class="analysis-tab-panel">
                    ${(scenario.if_event_strengthens || scenario.if_event_fades || scenario.invalidation_trigger) ? `
                    <div class="analysis-sub-section" style="margin-bottom:12px">
                        <div class="analysis-bars-title">Scenario & Risk</div>
                        <div style="font-size:0.9rem; line-height:1.6; color:var(--text-secondary)">
                            ${scenario.if_event_strengthens ? `<p><strong>If Strengthens:</strong> ${escapeHtml(scenario.if_event_strengthens)}</p>` : ''}
                            ${scenario.if_event_fades ? `<p><strong>If Fades:</strong> ${escapeHtml(scenario.if_event_fades)}</p>` : ''}
                            ${scenario.invalidation_trigger ? `<p><strong>Invalidation:</strong> ${escapeHtml(scenario.invalidation_trigger)}</p>` : ''}
                        </div>
                    </div>` : ''}
                    ${(critique.primary_thesis_weakness || critique.strongest_counter_argument) ? `
                    <div class="analysis-sub-section" style="margin-bottom:12px">
                        <div class="analysis-bars-title">Self Critique</div>
                        <div style="font-size:0.9rem; line-height:1.6; color:var(--text-secondary)">
                            ${critique.primary_thesis_weakness ? `<p><strong>Weakness:</strong> ${escapeHtml(critique.primary_thesis_weakness)}</p>` : ''}
                            ${critique.strongest_counter_argument ? `<p><strong>Counter:</strong> ${escapeHtml(critique.strongest_counter_argument)}</p>` : ''}
                        </div>
                    </div>` : ''}
                    ${analysis.macro_linkage_reasoning?.causal_chain_explanation ? `
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title">Macro Linkage</div>
                        <p style="font-size:0.9rem; line-height:1.6; color:var(--text-secondary)">${escapeHtml(analysis.macro_linkage_reasoning.causal_chain_explanation)}</p>
                    </div>` : ''}
                </div>

                <!-- TAB 4: Suggestions -->
                <div id="tab-suggestions" class="analysis-tab-panel">
                    ${renderSuggestionsTab(article)}
                </div>

                <!-- TAB 5: Predictions (Populated async) -->
                <div id="tab-predictions" class="analysis-tab-panel">
                    <div id="predictionsLoader" class="analyzing-spinner-sm" style="margin:20px auto; border-color:var(--text-secondary); border-top-color:var(--accent);"></div>
                    <div id="predictionsContent"></div>
                </div>
            </div>
        `;
    } else {
        // Not analyzed — show analyze button
        const isAnalyzing = analyzingArticles.has(article.id);
        const btnState = isAnalyzing ? 'disabled' : '';
        const btnClass = isAnalyzing ? 'analyzing' : '';
        const btnText = isAnalyzing ? '<div class="analyzing-spinner"></div> Analyzing…' : '✨ Analyze This Article';
        analysisHtml = `
            <div class="modal-divider"></div>
            <div class="modal-analyze-wrapper">
                <button class="analyze-btn analyze-btn-lg ${btnClass}" data-id="${article.id}" ${btnState} onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">${btnText}</button>
            </div>
        `;
    }

    let descriptionHtml = '';
    if (article.description) {
        descriptionHtml = `<p class="modal-description">${escapeHtml(article.description)}</p>`;
    }

    modalBody.innerHTML = `
        ${article.image_url ? `<img class="modal-image" src="${escapeHtml(article.image_url)}" alt="" onerror="this.style.display='none'">` : ''}
        <div class="card-header-row" style="margin-bottom: 12px; margin-top: 8px;">
            <div class="card-header-left">
                ${renderRelevanceBadge(article.news_relevance)}
                ${renderCategoryBadge(article.news_category)}
            </div>
            <span class="card-source">${escapeHtml(article.source || 'Unknown')}</span>
        </div>
        <h2 class="modal-title">${escapeHtml(article.title)}</h2>
        <div class="modal-timestamps">
            <span class="modal-timestamp-line"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</span>
            <span class="modal-timestamp-line"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</span>
        </div>
        ${descriptionHtml}
        
        <div class="initial-classification-box">
            <div class="ic-header">
                <span class="ic-title">INITIAL CLASSIFICATION</span>
            </div>
            <div class="ic-reason">
                <strong>Reason:</strong> ${escapeHtml(article.news_reason || 'No initial reason provided.')}
            </div>
        </div>

        ${analysisHtml}
        <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn">
            Read Full Article →
        </a>
    `;

    const modalEl = modalOverlay.querySelector('.modal');
    if (analysis) {
        modalEl.classList.add('modal-expanded');
        // Fetch predictions if analysis exists
        if (article.prediction_count > 0 || article.impact_score) {
            fetchPredictionsForModal(article.id);
        }
    } else {
        modalEl.classList.remove('modal-expanded');
    }
    modalOverlay.classList.add('active');
    document.body.style.overflow = 'hidden';
}

async function fetchPredictionsForModal(newsId) {
    try {
        const predTabBtn = document.getElementById('predTabBtn');
        const loader = document.getElementById('predictionsLoader');
        const content = document.getElementById('predictionsContent');
        if (!predTabBtn || !loader || !content) return;

        const res = await fetch(`${API_BASE}/api/predictions?news_id=${newsId}`);
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
    return `
        <div class="card-timestamps">
            <span class="card-timestamp-line"><strong>Published:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</span>
            <span class="card-timestamp-line"><strong>Posted:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</span>
        </div>
    `;
}

// ---- Render News Cards ----
function renderNews(articles) {
    newsGrid.innerHTML = '';
    const featuredSection = document.getElementById('featuredSection');
    const featuredGrid = document.getElementById('featuredGrid');
    const allNewsHeader = document.getElementById('allNewsHeader');
    featuredGrid.innerHTML = '';

    // Filter by Analyzed Status
    if (showOnlyAnalyzed) {
        articles = articles.filter(a => a.impact_score != null);
    }

    // Filter by Search Query
    if (searchQuery) {
        const q = searchQuery.toLowerCase();
        articles = articles.filter(a =>
            (a.title && a.title.toLowerCase().includes(q)) ||
            (a.source && a.source.toLowerCase().includes(q)) ||
            (a.description && a.description.toLowerCase().includes(q))
        );
    }

    if (articles.length === 0) {
        featuredSection.style.display = 'none';
        allNewsHeader.style.display = 'none';
        newsGrid.style.display = 'none';
        emptyState.style.display = 'block';

        if (searchQuery) {
            emptyStateTitle.textContent = `No results for "${searchQuery}"`;
            emptyStateMsg.textContent = 'Try a different search term or clear the filter.';
        } else {
            emptyStateTitle.textContent = 'No articles yet';
            emptyStateMsg.textContent = 'The monitor is fetching news. Articles will appear here automatically.';
        }

        articleCount.textContent = '0 articles';
        return;
    }

    emptyState.style.display = 'none';
    newsGrid.style.display = 'grid';
    articleCount.textContent = `${articles.length} article${articles.length !== 1 ? 's' : ''}`;

    // Separate featured articles (only when NOT searching)
    let regularArticles = [...articles];
    let featured = [];

    if (!searchQuery) {
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
        let highestScore = 3; // Must be at least 4 to be featured

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
    }

    // Render Featured
    if (featured.length > 0) {
        featuredSection.style.display = 'block';
        allNewsHeader.style.display = 'block';

        featured.forEach((article, index) => {
            const card = document.createElement('div');
            card.className = 'news-card featured-card';
            card.style.animationDelay = `${index * 0.1}s`;

            const imageHtml = article.image_url ?
                `<div class="card-image"><img src="${escapeHtml(article.image_url)}" alt="" onerror="this.parentElement.style.display='none'; this.closest('.news-card').classList.add('no-image');"></div>` : '';
            if (!article.image_url) card.classList.add('no-image');

            card.innerHTML = `
                ${imageHtml}
                <div class="card-header-row">
                    <div class="card-header-left">
                        ${renderRelevanceBadge(article.news_relevance)}
                        ${renderCategoryBadge(article.news_category)}
                        <span class="featured-type-badge">${article.featuredType}</span>
                    </div>
                    <span class="card-source">${escapeHtml(article.source || 'Unknown')}</span>
                </div>
                <h2 class="card-title">${escapeHtml(article.title)}</h2>
                ${article.description ? `<p class="card-description">${escapeHtml(article.description)}</p>` : ''}
                <div class="card-footer">
                    ${renderCardTimestamps(article)}
                    <div class="card-footer-right">
                        ${renderImpactBadge(article)}
                    </div>
                </div>
                <div class="card-action-row" style="margin-top: 12px;">
                    <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn card-read-btn" onclick="event.stopPropagation()">
                        Read Now →
                    </a>
                </div>
            `;

            card.addEventListener('click', () => openModal(article));
            featuredGrid.appendChild(card);
        });
    } else {
        featuredSection.style.display = 'none';
        allNewsHeader.style.display = 'none';
    }

    // Render Regular
    regularArticles.forEach((article, index) => {
        const card = document.createElement('div');
        card.className = 'news-card';
        card.style.animationDelay = `${index * 0.05}s`;

        const imageHtml = article.image_url ?
            `<div class="card-image"><img src="${escapeHtml(article.image_url)}" alt="" onerror="this.parentElement.style.display='none'; this.closest('.news-card').classList.add('no-image');"></div>` : '';
        if (!article.image_url) card.classList.add('no-image');

        card.innerHTML = `
            ${imageHtml}
            <div class="card-header-row">
                <div class="card-header-left">
                    ${renderRelevanceBadge(article.news_relevance)}
                    ${renderCategoryBadge(article.news_category)}
                </div>
                <span class="card-source">${escapeHtml(article.source || 'Unknown')}</span>
            </div>
            <h2 class="card-title">${escapeHtml(article.title)}</h2>
            ${article.description ? `<p class="card-description">${escapeHtml(article.description)}</p>` : ''}
            <div class="card-footer">
                ${renderCardTimestamps(article)}
                <div class="card-footer-right">
                    ${renderImpactBadge(article)}
                </div>
            </div>
            <div class="card-action-row" style="margin-top: 12px;">
                <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn card-read-btn" onclick="event.stopPropagation()">
                    Read Now →
                </a>
            </div>
        `;

        card.addEventListener('click', () => openModal(article));
        newsGrid.appendChild(card);
    });
}

// ---- Fetch Sources ----
async function fetchSources() {
    try {
        const res = await fetch(`${API_BASE}/api/sources`);
        const json = await res.json();
        if (json.status === 'success' && json.data.length > 0) {
            const allBtn = filtersContainer.querySelector('[data-source="all"]');
            filtersContainer.innerHTML = '';
            filtersContainer.appendChild(allBtn);

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

            if (currentSource === 'all') {
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
            return `${pair.substring(0,3)}/${pair.substring(3,6)}`; 
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

// ---- Fetch News ----
async function fetchNews() {
    if (isFetching) return;
    isFetching = true;
    showRefreshIndicator();

    try {
        let url = `${API_BASE}/api/news?today_only=false`;
        if (currentSource && currentSource !== 'all') {
            url += `&source=${encodeURIComponent(currentSource)}`;
        }
        if (currentRelevance && currentRelevance !== 'all') {
            url += `&relevance=${encodeURIComponent(currentRelevance)}`;
        }
        if (showOnlyAnalyzed) {
            url += `&analyzed_only=true`;
        }

        const res = await fetch(url);
        const json = await res.json();

        if (json.status === 'success') {
            newsData = json.data;
            // The backend handles the `analyzed_only` filter, but we filter client-side just in case
            let displayData = [...newsData];
            if (showOnlyAnalyzed) displayData = displayData.filter(a => a.impact_score != null);
            renderNews(displayData);
            // Reset connection failures on success
            if (consecutiveFailures > 0) {
                hideConnectionBanner();
                showToast('Connection restored', 'success');
            }
            consecutiveFailures = 0;
        } else {
            console.error('API error:', json.message);
        }
    } catch (err) {
        consecutiveFailures++;
        console.error('Failed to fetch news:', err);
        if (consecutiveFailures >= CONNECTION_FAIL_THRESHOLD) {
            showConnectionBanner();
        }
    } finally {
        isFetching = false;
        hideRefreshIndicator();
    }
}

// ---- Fetch Footer Stats ----
async function fetchStats() {
    try {
        const res = await fetch(`${API_BASE}/api/stats`);
        const json = await res.json();
        if (json.status === 'success') {
            const d = json.data;
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

// ---- Search Functionality ----
searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        searchQuery = searchInput.value.trim();
        searchClear.style.display = searchQuery ? 'flex' : 'none';
        renderNews(newsData);
    }, SEARCH_DEBOUNCE);
});

searchClear.addEventListener('click', () => {
    searchInput.value = '';
    searchQuery = '';
    searchClear.style.display = 'none';
    renderNews(newsData);
    searchInput.focus();
});

// ---- Scroll-to-top ----
window.addEventListener('scroll', () => {
    if (window.scrollY > SCROLL_TOP_THRESHOLD) {
        scrollTopBtn.classList.add('visible');
    } else {
        scrollTopBtn.classList.remove('visible');
    }
}, { passive: true });

scrollTopBtn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
});

// ---- Filter Click Handler ----
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
    fetchNews();
});

if (relevanceFilter) {
    relevanceFilter.addEventListener('change', (e) => {
        currentRelevance = e.target.value;
        fetchNews();
    });
}

if (analyzedToggle) {
    analyzedToggle.addEventListener('click', () => {
        showOnlyAnalyzed = !showOnlyAnalyzed;
        if (showOnlyAnalyzed) {
            analyzedToggle.classList.add('active');
        } else {
            analyzedToggle.classList.remove('active');
        }
        fetchNews();
    });
}

// ---- Initial Load ----
async function init() {
    if (relevanceFilter) {
        relevanceFilter.value = 'all';
    }
    await Promise.all([fetchSources(), fetchNews(), fetchStats()]);
}

init();

// ---- Auto-refresh ----
setInterval(() => {
    fetchSources();
    fetchNews();
    fetchStats();
}, REFRESH_INTERVAL);
