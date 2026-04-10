// =========================================
// Zentrade — Frontend Configuration
// =========================================
// This file is loaded before the main application logic.
// Set BACKEND_URL to your API server address (e.g., http://localhost:8000).
// Leave it as an empty string '' if serving frontend and backend on the same port.

window.APP_CONFIG = {
    // Optional override: set localStorage key `backend_url_override` to force a backend URL.
    BACKEND_URL: (function () {
        try {
            const forced = localStorage.getItem('backend_url_override');
            if (forced && forced.trim()) return forced.trim();
        } catch (_) {
            // Ignore localStorage access errors.
        }

        const host = window.location.hostname;
        if (host === 'localhost' || host === '127.0.0.1') {
            return 'http://localhost:8000';
        }

        // Default to same-origin when frontend/backend are served together.
        return '';
    })()
};
