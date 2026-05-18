(() => {
    const COOKIE_CONSENT_STORAGE_KEY = 'aici:cookie-consent';
    const COOKIE_CONSENT_VERSION = 2;
    const DEFAULT_ANALYTICS_OPT_IN = true;
    let cookieConsentMemory = null;
    let isInitialized = false;

    const canShowConsentOnPage = () => {
        if (!document.body) {
            return false;
        }
        const isLandingPage = document.body.classList.contains('landing-page');
        const hasAccount = document.body.dataset?.hasAccount === 'true';
        const isAuthenticated = document.body.dataset?.isAuthenticated === 'true';
        return isLandingPage && !(hasAccount || isAuthenticated);
    };

    const readStoredConsent = () => {
        try {
            const raw = window.localStorage?.getItem(COOKIE_CONSENT_STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === 'object') {
                    return parsed;
                }
            }
        } catch (_) {
            /* localStorage blocked */
        }
        return cookieConsentMemory;
    };

    const persistConsent = (state) => {
        const payload = {
            version: COOKIE_CONSENT_VERSION,
            analytics: Boolean(state?.analytics),
            acknowledgedEssential: true,
            savedAt: new Date().toISOString(),
        };
        try {
            window.localStorage?.setItem(COOKIE_CONSENT_STORAGE_KEY, JSON.stringify(payload));
        } catch (_) {
            cookieConsentMemory = payload;
        }
        return payload;
    };

    const ensureUi = (policyHref, iconHref) => {
        let container = document.querySelector('[data-cookie-consent]');
        let trigger = document.querySelector('[data-cookie-open]');

        if (!container) {
            container = document.createElement('section');
            container.className = 'cookie-consent';
            container.setAttribute('data-cookie-consent', 'true');
            container.setAttribute('role', 'region');
            container.setAttribute('aria-label', 'Cookie preferences');
            container.setAttribute('hidden', '');
            container.innerHTML = `
                <div class="cookie-consent__header">
                    <button class="cookie-consent__icon" type="button" data-cookie-toggle aria-expanded="true" aria-label="Collapse cookie banner">
                        <span class="cookie-consent__icon-glow" aria-hidden="true"></span>
                        <img src="${iconHref}" alt="" data-cookie-icon>
                    </button>
                    <div class="cookie-consent__intro">
                        <p class="cookie-consent__title">Cookie preferences</p>
                        <p class="cookie-consent__subtitle">
                            An essential auth cookie keeps you signed in. Analytics loads only with your consent.
                        </p>
                    </div>
                </div>
                <div class="cookie-consent__content">
                    <div class="cookie-consent__group">
                        <div class="cookie-consent__group-text">
                            <span class="cookie-consent__group-title">Auth refresh (essential)</span>
                            <p class="cookie-consent__group-desc">
                                Used to extend your session; no marketing identifiers involved.
                            </p>
                        </div>
                        <span class="cookie-consent__badge" aria-label="Essential">Required</span>
                    </div>
                    <div class="cookie-consent__group">
                        <div class="cookie-consent__group-text">
                            <label class="cookie-consent__group-title" for="cookie-analytics-toggle">Analytics (optional)</label>
                            <p class="cookie-consent__group-desc">
                                Helps us see which sections are useful. You can turn it off anytime.
                            </p>
                        </div>
                        <label class="cookie-switch" aria-label="Enable analytics cookies">
                            <input class="cookie-switch__input" type="checkbox" id="cookie-analytics-toggle" data-cookie-analytics>
                            <span class="cookie-switch__track" aria-hidden="true"></span>
                            <span class="cookie-switch__thumb" aria-hidden="true"></span>
                        </label>
                    </div>
                    <p class="cookie-consent__note">
                        Note: the auth cookie is set only for sign-in and session refresh. Third-party trackers do not load by default.
                    </p>
                    <div class="cookie-consent__actions" role="group" aria-label="Manage cookies">
                        <button type="button" class="cookie-consent__btn cookie-consent__btn--primary" data-cookie-save>Save preferences</button>
                        <button type="button" class="cookie-consent__btn cookie-consent__btn--ghost" data-cookie-reject>Essential only</button>
                        <a class="cookie-consent__policy" data-cookie-policy href="${policyHref}">Cookie policy</a>
                    </div>
                </div>
            `;
            document.body.appendChild(container);
        } else {
            const policyLink = container.querySelector('[data-cookie-policy]');
            if (policyLink) {
                policyLink.setAttribute('href', policyHref);
            }
            const icon = container.querySelector('[data-cookie-icon]');
            if (icon) {
                icon.setAttribute('src', iconHref);
            }
        }

        if (!trigger) {
            trigger = document.createElement('button');
            trigger.type = 'button';
            trigger.className = 'cookie-consent__trigger';
            trigger.setAttribute('data-cookie-open', 'true');
            trigger.setAttribute('aria-label', 'Open cookie preferences');
            trigger.innerHTML = `
                <img class="cookie-consent__trigger-icon" src="${iconHref}" alt="" aria-hidden="true">
            `;
            trigger.setAttribute('hidden', '');
            document.body.appendChild(trigger);
        } else {
            const triggerIcon = trigger.querySelector('img.cookie-consent__trigger-icon');
            if (triggerIcon) {
                triggerIcon.setAttribute('src', iconHref);
            }
        }

        const analyticsToggle = container.querySelector('[data-cookie-analytics]');
        const saveButton = container.querySelector('[data-cookie-save]');
        const rejectButton = container.querySelector('[data-cookie-reject]');
        const collapseToggle = container.querySelector('[data-cookie-toggle]');

        return {
            container,
            trigger,
            analyticsToggle,
            saveButton,
            rejectButton,
            collapseToggle,
        };
    };

    const initCookieConsent = () => {
        if (!document.body || isInitialized) {
            return;
        }

        if (!canShowConsentOnPage()) {
            return;
        }

        const policyHref = document.body.dataset.cookiePolicyUrl || '/cookie-policy';
        const iconHref = document.body.dataset.cookieIconUrl || '/static/icons/cookies.svg';
        const { container, trigger, analyticsToggle, saveButton, rejectButton, collapseToggle } = ensureUi(policyHref, iconHref);

        if (!container || !trigger || !analyticsToggle || !saveButton || !rejectButton) {
            return;
        }

        isInitialized = true;

        const applyState = (analyticsEnabled) => {
            analyticsToggle.checked = Boolean(analyticsEnabled);
            container.dataset.analytics = analyticsToggle.checked ? 'on' : 'off';
        };

        let isCollapsed = false;
        let removeOutsideClickListener = null;
        const isMobileCookieViewport = () => window.matchMedia('(max-width: 640px)').matches;

        const setCollapsed = (next, { focusAnalytics = false } = {}) => {
            isCollapsed = Boolean(next);
            container.classList.toggle('is-collapsed', isCollapsed);
            const expanded = !isCollapsed;
            container.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            if (collapseToggle) {
                collapseToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                collapseToggle.setAttribute('aria-label', expanded ? 'Collapse cookie banner' : 'Expand cookie banner');
            }
            if (expanded && focusAnalytics) {
                analyticsToggle.focus({ preventScroll: true });
            }
        };

        const stopOutsideClickListener = () => {
            if (typeof removeOutsideClickListener === 'function') {
                removeOutsideClickListener();
                removeOutsideClickListener = null;
            }
        };

        const hideBanner = () => {
            stopOutsideClickListener();
            container.classList.remove('is-visible');
            container.setAttribute('hidden', '');
            trigger.removeAttribute('hidden');
            setCollapsed(false);
        };

        const startOutsideClickListener = () => {
            stopOutsideClickListener();

            const handler = (event) => {
                const target = event.target;
                if (!target || container.contains(target)) {
                    return;
                }
                hideBanner();
            };

            document.addEventListener('pointerdown', handler);
            removeOutsideClickListener = () => {
                document.removeEventListener('pointerdown', handler);
            };
        };

        const showBanner = () => {
            setCollapsed(false);
            container.classList.add('is-visible');
            container.removeAttribute('hidden');
            trigger.setAttribute('hidden', '');
            startOutsideClickListener();
        };

        const stored = readStoredConsent();
        if (stored && typeof stored.analytics === 'boolean') {
            applyState(stored.analytics);
        } else {
            applyState(DEFAULT_ANALYTICS_OPT_IN);
        }

        const shouldShow = !stored || stored.version !== COOKIE_CONSENT_VERSION;
        if (shouldShow) {
            showBanner();
        } else {
            hideBanner();
        }

        const commit = (analyticsEnabled) => {
            const payload = persistConsent({ analytics: analyticsEnabled });
            applyState(payload.analytics);
            hideBanner();
            window.dispatchEvent(
                new CustomEvent('cookie-consent:updated', {
                    detail: payload,
                })
            );
        };

        saveButton.addEventListener('click', () => {
            commit(analyticsToggle.checked);
        });

        rejectButton.addEventListener('click', () => {
            commit(false);
        });

        analyticsToggle.addEventListener('change', () => {
            applyState(analyticsToggle.checked);
        });

        if (collapseToggle) {
            collapseToggle.addEventListener('click', () => {
                if (!isCollapsed && isMobileCookieViewport()) {
                    hideBanner();
                    return;
                }
                setCollapsed(!isCollapsed, { focusAnalytics: isCollapsed });
            });
        }

        trigger.addEventListener('click', () => {
            showBanner();
            analyticsToggle.focus({ preventScroll: true });
        });
    };

    window.AICICookieConsent = window.AICICookieConsent || {};
    window.AICICookieConsent.init = initCookieConsent;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCookieConsent, { once: true });
    } else {
        initCookieConsent();
    }
})();
