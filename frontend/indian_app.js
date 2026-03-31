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

    if (rel.includes('very high')) cssClass = 'rel-very-high';
    else if (rel.includes('high')) cssClass = 'rel-high';
    else if (rel.includes('useful')) cssClass = 'rel-useful';
    else if (rel.includes('medium')) cssClass = 'rel-medium';
    else if (rel.includes('noisy')) cssClass = 'rel-noisy';

    const label = relevance.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    return `<span class="relevance-badge ${cssClass}"><span class="relevance-dot"></span>${escapeHtml(label)}</span>`;
}

function renderCategoryBadge(category) {
    if (!category || category.toLowerCase() === 'none') return '';
    const label = category.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    return `<span class="category-badge">${escapeHtml(label)}</span>`;
}

function renderAllSymbolsBadge(symbols) {
    if (!symbols) return '';
    let symbolArr = symbols;
    if (typeof symbols === 'string') {
        try { symbolArr = JSON.parse(symbols); } catch { return ''; }
    }
    if (!Array.isArray(symbolArr) || symbolArr.length === 0) return '';
    return symbolArr.map(sym => `<span class="category-badge" style="background: rgba(255, 193, 7, 0.2); color: #ffca28; border-color: rgba(255, 193, 7, 0.4); font-weight: bold; letter-spacing: 0.5px;">${escapeHtml(sym)}</span>`).join('');
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
    let badges = '';
    const isAnalyzed = article.impact_score != null;
    const analysis = parseJsonField(article.analysis_data);

    if (isAnalyzed) {
        // Show Signal Bucket (High priority visual)
        if (analysis && analysis.signal_bucket) {
            const bucket = analysis.signal_bucket.toUpperCase();
            const bucketCls = `bucket-${analysis.signal_bucket.toLowerCase().replace('_', '-')}`;
            badges += `<span class="signal-bucket-badge ${bucketCls}" style="margin-right: 8px;">${bucket}</span>`;
        }

        // Show Impact Score
        if (article.impact_score != null) {
            const scoreClass = getScoreClass(article.impact_score);
            const scoreLabel = getScoreLabel(article.impact_score);
            badges += `<span class="impact-score-badge ${scoreClass}">⚡ ${article.impact_score}/10 · ${scoreLabel}</span>`;
        }
    } else {
        // Fallback or legacy impact displays
        if (article.news_impact_level && article.news_impact_level !== 'None' && article.news_impact_level !== 'Neutral') {
            const imp = article.news_impact_level.toLowerCase();
            let css = 'impact-neutral';
            if (imp === 'positive') css = 'impact-positive';
            if (imp === 'negative') css = 'impact-negative';
            badges += `<span class="impact-tag ${css}" style="margin-right:6px">📊 ${article.news_impact_level} Impact</span>`;
        }
    }

    // Always show Analyze Button if NOT analyzed
    if (!isAnalyzed) {
        const isAnalyzing = analyzingArticles.has(article.id);
        const btnState = isAnalyzing ? 'disabled' : '';
        const btnClass = isAnalyzing ? 'analyzing' : '';
        const btnText = isAnalyzing ? '<div class="analyzing-spinner-sm"></div> Analyzing…' : '✨ Analyze';
        const symbolsHtml = renderAllSymbolsBadge(article.symbols);

        const analyzeBtnHtml = `
            <div style="display:flex; align-items:center; gap:8px; flex-wrap: wrap;">
                <button class="analyze-btn analyze-btn-sm ${btnClass}" data-id="${article.id}" ${btnState} onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">
                     ${btnText}
                </button>
                ${symbolsHtml ? `<div style="display:flex; gap:4px; flex-wrap:wrap;">${symbolsHtml}</div>` : ''}
            </div>
        `;

        if (badges) {
            return `<div class="card-impact-stack"><div class="card-badges-row">${badges}</div><div class="card-analyze-row">${analyzeBtnHtml}</div></div>`;
        }
        return analyzeBtnHtml;
    }

    const analyzedSymbolsHtml = renderAllSymbolsBadge(article.symbols);
    return badges ? `<div class="card-impact-stack"><div class="card-badges-row">${badges}</div>${analyzedSymbolsHtml ? `<div style="display:flex; gap:4px; margin-top:8px; flex-wrap:wrap;">${analyzedSymbolsHtml}</div>` : ''}</div>` : '';
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

    const bias = (coreView.market_bias || article.usd_bias || 'Neutral').toLowerCase();
    const biasInfo = getBiasInfo(bias);
    const horizon = coreView.primary_horizon || article.execution_window || article.impact_duration || 'N/A';

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
        const res = await fetch(`${API_BASE}/api/indian_analyze/${newsId}`, { method: 'POST' });
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

// ---- Indian Compact Rendering ----
function renderIndianCompactModal(article, analysis) {
    try {
        if (!analysis) throw new Error("No analysis data available");

        const newsSummary = analysis.news_summary || {};
        const coreView = analysis.core_view || {};
        const affected = analysis.affected_entities || {};
        const stocks = analysis.stock_impacts || [];
        const sectors = analysis.sector_impacts || [];
        const evidence = analysis.evidence || [];
        const tradeability = analysis.tradeability || {};

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
        if (tradeability.classification === 'actionable_now') { tradeCls = 'trade-actionable'; tradeIcon = '⚡'; }
        else if (tradeability.classification === 'potential_opportunity') { tradeCls = 'trade-potential'; tradeIcon = '🔍'; }

        // Render Entities
        const entityChips = [
            ...(affected.stocks || []).map(s => `<span class="entity-tag stock">${escapeHtml(s)}</span>`),
            ...(affected.sectors || []).map(s => `<span class="entity-tag sector">${escapeHtml(s)}</span>`),
            ...(affected.indices || []).map(s => `<span class="entity-tag index">${escapeHtml(s)}</span>`)
        ].join('');

        const modalBodyHtml = `
            <div class="analysis-panel">
                <div class="analysis-panel-header">
                    <div class="analysis-panel-title">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                        Indian Market Intelligence IQ
                    </div>
                    <div class="analysis-panel-badges">
                        <span class="signal-bucket-badge ${bucketCls}">${bucket}</span>
                        <span class="tradeability-badge ${tradeCls}" style="margin-left:8px;">${tradeIcon} ${escapeHtml((tradeability.classification || 'Researching').replace('_', ' ').toUpperCase())}</span>
                    </div>
                </div>

                <div class="analysis-score-row">
                    <div class="analysis-score-main">
                        <span class="analysis-score-number ${scoreClass}">${impactScore}</span>
                        <div class="analysis-score-meta">
                            <span class="analysis-score-label">${scoreLabel}</span>
                            <span class="analysis-score-sub" style="font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">impact score</span>
                        </div>
                    </div>
                    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px;">
                        <span class="bias-pill ${biasCls}">${biasArrow} ${coreView.market_bias || 'Neutral'} Bias</span>
                        <span style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; font-weight:700;">HORIZON: ${escapeHtml(coreView.primary_horizon || 'Short Term').toUpperCase()}</span>
                    </div>
                </div>

                <div class="analysis-tabs">
                    <button class="analysis-tab active" onclick="switchTab(this, 'tab-ia-overview')">OVERVIEW</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-impacts')">MARKET IMPACTS</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-setup')">TRADE SETUP</button>
                    <button class="analysis-tab" onclick="switchTab(this, 'tab-ia-invalidations')">INVALIDATIONS</button>
                </div>

                <!-- TAB 1: Overview -->
                <div id="tab-ia-overview" class="analysis-tab-panel active">
                    <div class="analysis-summary-box">
                        <p style="font-size:1.05rem; line-height:1.5; color:var(--text-primary); font-weight:500;">${escapeHtml(analysis.executive_summary || coreView.summary || '')}</p>
                    </div>

                    <div class="summary-split-container">
                        <div class="summary-split-box confirmed">
                            <div class="summary-split-title confirmed">✓ Confirmed</div>
                            <ul class="summary-list">
                                ${(newsSummary.what_is_confirmed || []).map(item => `<li class="summary-item">${escapeHtml(item)}</li>`).join('') || '<li class="summary-item">No specific confirmations.</li>'}
                            </ul>
                        </div>
                        <div class="summary-split-box unknown">
                            <div class="summary-split-title unknown">? Unknown / Risk</div>
                            <ul class="summary-list">
                                ${(newsSummary.what_is_unknown || []).map(item => `<li class="summary-item">${escapeHtml(item)}</li>`).join('') || '<li class="summary-item">No major unknowns identified.</li>'}
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
                                <div class="forex-pair-card" style="border-left: 3px solid ${(s.bias||'').toLowerCase() === 'bullish' ? 'var(--accent-2)' : (s.bias||'').toLowerCase() === 'bearish' ? '#ff4757' : 'var(--text-muted)'}">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name">${escapeHtml(s.symbol)}</span>
                                        <span class="forex-pair-dir ${(s.bias||'').toLowerCase() === 'bullish' ? 'dir-bullish' : (s.bias||'').toLowerCase() === 'bearish' ? 'dir-bearish' : 'dir-neutral'}">${escapeHtml((s.bias||'').toUpperCase())}</span>
                                        <span style="margin-left:auto; font-size:0.7rem; font-weight:700; color:var(--text-muted)">CONF: ${s.confidence}%</span>
                                    </div>
                                    <p style="font-size:0.85rem; color:var(--text-primary); font-weight:600; margin:8px 0;">${escapeHtml(s.company_name)}</p>
                                    <div style="font-size:0.75rem; color:var(--text-secondary); margin-bottom:8px;"><strong>Role:</strong> ${escapeHtml(s.role)}</div>
                                    <p style="font-size:0.8rem; color:var(--text-secondary); line-height:1.4;">${escapeHtml(s.why)}</p>
                                    ${s.invalidation ? `<div style="margin-top:8px; font-size:0.7rem; color:#ff4757; font-style:italic;">🛑 ${escapeHtml(s.invalidation)}</div>` : ''}
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No direct stock impacts.</p>'}
                        </div>
                    </div>
                    <div class="analysis-sub-section" style="margin-top:24px;">
                        <div class="analysis-bars-title">Sector Wide Impacts</div>
                        <div class="forex-pairs-grid">
                            ${sectors.map(sec => `
                                <div class="forex-pair-card" style="border-left: 3px solid ${(sec.bias||'').toLowerCase() === 'bullish' ? 'var(--accent-2)' : (sec.bias||'').toLowerCase() === 'bearish' ? '#ff4757' : 'var(--text-muted)'}">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name">${escapeHtml(sec.sector)}</span>
                                        <span class="forex-pair-dir ${(sec.bias||'').toLowerCase() === 'bullish' ? 'dir-bullish' : (sec.bias||'').toLowerCase() === 'bearish' ? 'dir-bearish' : 'dir-neutral'}">${escapeHtml((sec.bias||'').toUpperCase())}</span>
                                    </div>
                                    <p style="font-size:0.8rem; color:var(--text-secondary); margin-top:8px; line-height:1.4;">${escapeHtml(sec.why)}</p>
                                    <div style="margin-top:8px; display:flex; justify-content:space-between; font-size:0.65rem; color:var(--text-muted);">
                                        <span>Horizon: ${escapeHtml(sec.time_horizon)}</span>
                                        <span>Conf: ${sec.confidence}%</span>
                                    </div>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No sector impacts.</p>'}
                        </div>
                    </div>
                </div>

                <!-- TAB 3: Setup -->
                <div id="tab-ia-setup" class="analysis-tab-panel">
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title">Tradeability & Triggers</div>
                        <div class="trade-trigger-container">
                            ${(tradeability.action_triggers || []).map(t => `<div class="trade-trigger-item">⚡ ${escapeHtml(t)}</div>`).join('') || '<div class="trade-trigger-item">No immediate triggers identified.</div>'}
                        </div>
                        <p style="font-size:0.85rem; color:var(--text-secondary); margin-top:12px; line-height:1.5;">
                            <strong>Reasoning:</strong> ${escapeHtml(tradeability.reasoning || 'Awaiting further data for trade entry.')}
                        </p>
                    </div>
                    <div class="analysis-sub-section" style="margin-top:24px;">
                        <div class="analysis-bars-title">Insights & Evidence</div>
                        <div style="display:flex; flex-direction:column; gap:8px;">
                            ${evidence.map(e => `
                                <div style="padding:10px; background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:8px; display:flex; justify-content:space-between; align-items:center;">
                                    <div style="font-size:0.8rem; color:var(--text-secondary); flex:1;">${escapeHtml(e.detail)}</div>
                                    <div style="display:flex; gap:8px; align-items:center; margin-left:12px;">
                                        <span class="impact-pill ${(e.strength||'').toLowerCase() === 'high' ? 'impact-pos' : (e.strength||'').toLowerCase() === 'low' ? 'impact-neg' : 'impact-neu'}">${escapeHtml((e.strength||'').toUpperCase())}</span>
                                        <span style="font-size:0.65rem; color:var(--text-muted); font-weight:700;">${e.confidence}%</span>
                                    </div>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No recorded evidence points.</p>'}
                        </div>
                    </div>
                </div>

                <!-- TAB 4: Invalidations -->
                <div id="tab-ia-invalidations" class="analysis-tab-panel">
                    <div class="analysis-sub-section">
                        <div class="analysis-bars-title" style="color:#ff6b7a;">Impact Killers (Negate Thesis)</div>
                        <div class="forex-pairs-grid" style="margin-top:12px;">
                            ${(analysis.impact_triggers?.impact_killers || []).map(k => `
                                <div class="forex-pair-card" style="border-left: 3px solid #ff4757; background: rgba(255, 71, 87, 0.03);">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name" style="color:#ff6b7a;">${escapeHtml(k.trigger)}</span>
                                        <span style="margin-left:auto; font-size:0.7rem; font-weight:700; color:var(--text-muted)">CONF: ${k.confidence}%</span>
                                    </div>
                                    <div style="font-size:0.85rem; color:var(--text-primary); margin:8px 0; line-height:1.4;">
                                        <strong>Why it kills:</strong> ${escapeHtml(k.why_it_kills_the_impact)}
                                    </div>
                                    <div style="font-size:0.8rem; color:var(--text-secondary); line-height:1.4;">
                                        <strong>Market Effect:</strong> ${escapeHtml(k.resulting_market_effect)}
                                    </div>
                                    <div style="margin-top:8px; font-size:0.7rem; color:var(--text-muted); font-style:italic;">
                                        ⏱ Sensitivity: ${escapeHtml(k.time_sensitivity)}
                                    </div>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No immediate impact killers identified.</p>'}
                        </div>
                    </div>
                    <div class="analysis-sub-section" style="margin-top:24px;">
                        <div class="analysis-bars-title" style="color:var(--accent-2);">Impact Amplifiers (Strengthen Thesis)</div>
                        <div class="forex-pairs-grid" style="margin-top:12px;">
                            ${(analysis.impact_triggers?.impact_amplifiers || []).map(a => `
                                <div class="forex-pair-card" style="border-left: 3px solid var(--accent-2); background: rgba(0, 212, 170, 0.03);">
                                    <div class="forex-pair-header">
                                        <span class="forex-pair-name" style="color:var(--accent-2);">${escapeHtml(a.trigger)}</span>
                                        <span style="margin-left:auto; font-size:0.7rem; font-weight:700; color:var(--text-muted)">CONF: ${a.confidence}%</span>
                                    </div>
                                    <div style="font-size:0.85rem; color:var(--text-primary); margin:8px 0; line-height:1.4;">
                                        <strong>Why it amplifies:</strong> ${escapeHtml(a.why_it_amplifies_the_impact)}
                                    </div>
                                    <div style="font-size:0.8rem; color:var(--text-secondary); line-height:1.4;">
                                        <strong>Market Effect:</strong> ${escapeHtml(a.resulting_market_effect)}
                                    </div>
                                    <div style="margin-top:8px; font-size:0.7rem; color:var(--text-muted); font-style:italic;">
                                        ⏱ Sensitivity: ${escapeHtml(a.time_sensitivity)}
                                    </div>
                                </div>
                            `).join('') || '<p style="color:var(--text-muted); font-size:0.85rem;">No major amplifiers identified.</p>'}
                        </div>
                    </div>
                </div>
            </div>
        `;

        modalBody.innerHTML = `
            ${article.image_url ? `<img class="modal-image" src="${escapeHtml(article.image_url)}" alt="" onerror="this.style.display='none'">` : ''}
            <div class="card-header-row" style="margin-bottom: 12px; margin-top: 8px;">
                <div class="card-header-left">
                    ${renderRelevanceBadge(article.news_relevance)}
                    ${renderCategoryBadge(article.news_category)}
                </div>
                <div class="card-header-right">
                    <span class="card-time">${formatTime(article.published)}</span>
                </div>
            </div>
            
            <h2 class="modal-title">${escapeHtml(article.title)}</h2>
            
            <div class="modal-timestamps-premium" style="margin-bottom: 24px; margin-top: 20px;">
                <div class="ts-row"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</div>
                <div class="ts-row"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</div>
            </div>

            <div class="modal-description" style="margin-bottom: 24px;">${escapeHtml(article.description || '')}</div>
            
            ${modalBodyHtml}

            <div class="modal-action-footer" style="margin-top: 32px;">
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
    const btnText = isAnalyzing ? '<div class="analyzing-spinner-sm"></div> Analyzing...' : '✨ Analyze Now';

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
        
        <div class="modal-header-top" style="display:flex; align-items:center; margin-bottom:16px;">
            <div class="modal-badges-row" style="display:flex; gap:8px;">
                ${renderRelevanceBadge(article.news_relevance)}
                ${renderCategoryBadge(article.news_category)}
            </div>
            <span class="card-source" style="color:#00d4aa; font-weight:700; margin-left:auto; text-transform:uppercase; font-size:0.8rem;">• ${escapeHtml(article.source || 'Unknown')}</span>
        </div>

        <h2 class="modal-title" style="margin-bottom:16px; font-weight:800; line-height:1.3; font-size:1.8rem;">${escapeHtml(article.title)}</h2>
        
        <div class="modal-timestamps-stacked" style="margin-bottom:24px; display:flex; flex-direction:column; gap:4px;">
            <div class="modal-ts-line" style="font-size:0.9rem; color:var(--text-muted);"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</div>
            <div class="modal-ts-line" style="font-size:0.9rem; color:var(--text-muted);"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</div>
        </div>

        ${descriptionHtml}
        
        ${classificationHtml}
        
        <div class="modal-action-footer" style="margin-top:32px; display:flex; flex-direction:column; gap:16px;">
            <div class="modal-analyze-center" style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:12px; padding:12px 0;">
                ${article.symbols ? `<div style="display:flex; justify-content:center; gap:6px; flex-wrap:wrap;">${renderAllSymbolsBadge(article.symbols)}</div>` : ''}
                <button class="analyze-btn analyze-btn-sm ${btnClass}" style="padding:12px 40px; font-size:1rem; border-radius:99px; font-weight:700;" data-id="${article.id}" ${btnState} onclick="event.stopPropagation(); analyzeArticle(${article.id}, this)">
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
                <div class="card-timestamps-premium">
                    <div class="ts-row"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</div>
                    <div class="ts-row"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</div>
                </div>
                ${article.description ? `<p class="card-description">${escapeHtml(article.description)}</p>` : ''}
                <div class="card-footer">
                    <div class="card-footer-right">
                        ${renderImpactBadge(article)}
                    </div>
                </div>
                <div class="card-action-row">
                    <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn card-read-btn" onclick="event.stopPropagation()">
                        Read Full Article →
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
            <div class="card-timestamps-premium">
                <div class="ts-row"><strong>Source Posted:</strong> ${timeAgo(article.published)} · ${formatTime(article.published)}</div>
                <div class="ts-row"><strong>Scraped:</strong> ${timeAgo(article.created_at)} · ${formatTime(article.created_at)}</div>
            </div>
            ${article.description ? `<p class="card-description">${escapeHtml(article.description)}</p>` : ''}
            <div class="card-footer">
                <div class="card-footer-right">
                    ${renderImpactBadge(article)}
                </div>
            </div>
            <div class="card-action-row">
                <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="read-article-btn card-read-btn" onclick="event.stopPropagation()">
                    Read Full Article →
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
        const res = await fetch(`${API_BASE}/api/indian_sources`);
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

// ---- Fetch News ----
async function fetchNews() {
    if (isFetching) return;
    isFetching = true;
    showRefreshIndicator();

    try {
        let url = `${API_BASE}/api/indian_news?today_only=false`;
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
        const res = await fetch(`${API_BASE}/api/indian_stats`);
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

// ---- Tab Switching Helper ----
window.switchTab = function(btn, tabId) {
    const panel = btn.closest('.analysis-panel');
    if (!panel) return;
    
    panel.querySelectorAll('.analysis-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    
    panel.querySelectorAll('.analysis-tab-panel').forEach(p => p.classList.remove('active'));
    const target = document.getElementById(tabId);
    if (target) target.classList.add('active');
};
