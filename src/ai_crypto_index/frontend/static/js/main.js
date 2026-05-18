// Main client-side interactions for the AI Crypto Index landing page.
(function initLandingScripts() {
    'use strict';

    const headerHeightObserverKey = Symbol('landingHeaderHeightObserver');
    const CTA_SESSION_STORAGE_KEY = 'aici_cta_session_id';
    const CTA_UTM_STORAGE_KEY = 'aici_cta_utm_snapshot';
    const CTA_UTM_FIELDS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];

    const parseSearchParams = () => {
        try {
            return new URLSearchParams(window.location.search || '');
        } catch (error) {
            return null;
        }
    };

    const resolveCtaUtmSnapshot = () => {
        const snapshot = {};
        try {
            const storage = window.localStorage || window.sessionStorage;
            const raw = storage ? storage.getItem(CTA_UTM_STORAGE_KEY) : null;
            if (raw) {
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    CTA_UTM_FIELDS.forEach((field) => {
                        const value = typeof parsed[field] === 'string' ? parsed[field].trim() : '';
                        if (value) {
                            snapshot[field] = value;
                        }
                    });
                }
            }
        } catch (error) {
            // ignore storage access issues
        }

        const params = parseSearchParams();
        let didUpdate = false;
        if (params) {
            CTA_UTM_FIELDS.forEach((field) => {
                const value = params.get(field);
                const normalized = typeof value === 'string' ? value.trim() : '';
                if (!normalized) {
                    return;
                }
                if (snapshot[field] !== normalized) {
                    snapshot[field] = normalized;
                    didUpdate = true;
                }
            });
        }

        if (didUpdate) {
            try {
                const storage = window.localStorage || window.sessionStorage;
                if (storage) {
                    storage.setItem(CTA_UTM_STORAGE_KEY, JSON.stringify(snapshot));
                }
            } catch (error) {
                // ignore storage access issues
            }
        }

        return snapshot;
    };

    const readCtaUtm = (snapshot, field) => {
        const value = snapshot && typeof snapshot[field] === 'string' ? snapshot[field].trim() : '';
        return value || null;
    };

    const CTA_SESSION_ID_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days, matches attribution lookback

    const getCtaSessionId = () => {
        try {
            const storage = window.localStorage || window.sessionStorage;
            if (!storage) {
                return null;
            }
            const raw = storage.getItem(CTA_SESSION_STORAGE_KEY);
            if (raw) {
                try {
                    const parsed = JSON.parse(raw);
                    if (parsed && parsed.id && typeof parsed.expires === 'number' && parsed.expires > Date.now()) {
                        return parsed.id;
                    }
                } catch (_) {
                    // legacy plain string value written by sessionStorage
                    if (typeof raw === 'string' && raw.length > 0) {
                        return raw;
                    }
                }
            }
            const generated =
                typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
                    ? crypto.randomUUID()
                    : `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
            storage.setItem(
                CTA_SESSION_STORAGE_KEY,
                JSON.stringify({ id: generated, expires: Date.now() + CTA_SESSION_ID_TTL_MS }),
            );
            return generated;
        } catch (error) {
            return null;
        }
    };

    const initFaqAccordion = () => {
        const faqItems = document.querySelectorAll('[data-faq-item]');
        if (!faqItems.length) {
            return;
        }

        const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
        const prefersReducedMotion = Boolean(reduceMotionQuery.matches);

        const getTrigger = (item) => item.querySelector('.landing-faq__question');
        const getPanel = (item) => item.querySelector('[data-faq-panel]');
        const panelTransitionHandlers = new WeakMap();

        const shouldAnimatePanel = (panel) => {
            if (prefersReducedMotion || !panel) {
                return false;
            }

            const styles = window.getComputedStyle(panel);
            const durations = styles.transitionDuration.split(',').map((value) => parseFloat(value) || 0);
            const delays = styles.transitionDelay.split(',').map((value) => parseFloat(value) || 0);

            return durations.some((duration, index) => {
                const delay = delays[index] ?? delays[0] ?? 0;
                return duration + delay > 0;
            });
        };

        const registerTransitionEnd = (panel, callback) => {
            if (!panel) {
                return;
            }

            const existingHandler = panelTransitionHandlers.get(panel);
            if (existingHandler) {
                panel.removeEventListener('transitionend', existingHandler);
                panel.removeEventListener('transitioncancel', existingHandler);
            }

            const handler = (event) => {
                if (event.target !== panel || event.propertyName !== 'max-height') {
                    return;
                }
                panel.removeEventListener('transitionend', handler);
                panel.removeEventListener('transitioncancel', handler);
                panelTransitionHandlers.delete(panel);
                callback();
            };

            panelTransitionHandlers.set(panel, handler);
            panel.addEventListener('transitionend', handler);
            panel.addEventListener('transitioncancel', handler);
        };

        const closeItem = (item) => {
            const trigger = getTrigger(item);
            const panel = getPanel(item);
            if (!trigger || !panel) {
                return;
            }
            if (trigger.getAttribute('aria-expanded') === 'false') {
                return;
            }

            trigger.setAttribute('aria-expanded', 'false');

            const animatePanel = shouldAnimatePanel(panel);

            if (!animatePanel) {
                item.classList.remove('is-open');
                panel.setAttribute('hidden', '');
                panel.style.maxHeight = '';
                return;
            }

            registerTransitionEnd(panel, () => {
                panel.setAttribute('hidden', '');
                panel.style.maxHeight = '';
            });

            const currentHeight = panel.scrollHeight;
            panel.style.maxHeight = `${currentHeight}px`;
            window.requestAnimationFrame(() => {
                item.classList.remove('is-open');
                panel.style.maxHeight = '0px';
            });
        };

        const openItem = (item) => {
            const trigger = getTrigger(item);
            const panel = getPanel(item);
            if (!trigger || !panel) {
                return;
            }
            if (trigger.getAttribute('aria-expanded') === 'true') {
                return;
            }

            trigger.setAttribute('aria-expanded', 'true');
            panel.removeAttribute('hidden');

            const animatePanel = shouldAnimatePanel(panel);

            if (!animatePanel) {
                panel.style.maxHeight = 'none';
                item.classList.add('is-open');
                return;
            }

            panel.style.maxHeight = '0px';

            registerTransitionEnd(panel, () => {
                if (item.classList.contains('is-open')) {
                    panel.style.maxHeight = 'none';
                }
            });

            window.requestAnimationFrame(() => {
                item.classList.add('is-open');
                panel.style.maxHeight = `${panel.scrollHeight}px`;
            });
        };

        faqItems.forEach((item) => {
            const trigger = getTrigger(item);
            const panel = getPanel(item);
            if (!trigger || !panel) {
                return;
            }

            trigger.setAttribute('aria-expanded', 'false');
            panel.setAttribute('hidden', '');
            panel.style.maxHeight = '0px';

            const handleToggle = () => {
                const isExpanded = trigger.getAttribute('aria-expanded') === 'true';
                faqItems.forEach((otherItem) => {
                    if (otherItem !== item) {
                        closeItem(otherItem);
                    }
                });
                if (isExpanded) {
                    closeItem(item);
                } else {
                    openItem(item);
                }
            };

            trigger.addEventListener('click', handleToggle);
            trigger.addEventListener('keydown', (event) => {
                const key = event.key;
                if (key === ' ' || key === 'Enter' || key === 'Spacebar') {
                    event.preventDefault();
                    handleToggle();
                }
            });
        });

        if (!prefersReducedMotion) {
            window.addEventListener('resize', () => {
                faqItems.forEach((item) => {
                    if (!item.classList.contains('is-open')) {
                        return;
                    }
                    const panel = getPanel(item);
                    if (!panel) {
                        return;
                    }
                    if (panel.style.maxHeight === 'none') {
                        return;
                    }
                    panel.style.maxHeight = `${panel.scrollHeight}px`;
                });
            });
        }
    };

    const initMobileHeaders = () => {
        const headers = document.querySelectorAll('[data-mobile-header]');
        if (!headers.length) {
            return;
        }

        const desktopQuery = window.matchMedia('(min-width: 960px)');
        const focusableSelectors = [
            'a[href]',
            'button:not([disabled])',
            'input:not([disabled])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            '[tabindex]:not([tabindex="-1"])',
        ].join(', ');
        const contexts = [];

        const updateBodyLock = () => {
            const shouldLock = contexts.some((ctx) => ctx.drawer && ctx.drawer.classList.contains('is-open'));
            document.body.classList.toggle('has-mobile-menu-open', shouldLock);
            document.documentElement.classList.toggle('has-mobile-menu-open', shouldLock);
        };

        const setDrawerAriaHidden = (ctx, hidden) => {
            if (!ctx.drawer) {
                return;
            }
            if (hidden && !desktopQuery.matches) {
                ctx.drawer.setAttribute('aria-hidden', 'true');
            } else {
                ctx.drawer.removeAttribute('aria-hidden');
            }
        };

        const closeMenu = (ctx, options = {}) => {
            const { shouldRestoreFocus = true } = options;
            if (!ctx.drawer || !ctx.trigger) {
                return;
            }
            ctx.drawer.classList.remove('is-open');
            ctx.header.classList.remove('is-menu-open');
            ctx.trigger.classList.remove('is-active');
            ctx.trigger.setAttribute('aria-expanded', 'false');
            ctx.trigger.setAttribute('aria-label', ctx.openLabel);
            ctx.overlay?.classList.remove('is-visible');
            ctx.drawer.removeAttribute('tabindex');
            setDrawerAriaHidden(ctx, true);
            if (shouldRestoreFocus) {
                ctx.trigger.focus();
            }
            updateBodyLock();
        };

        const getFocusableNodes = (container) => {
            if (!container) {
                return [];
            }
            return Array.from(container.querySelectorAll(focusableSelectors)).filter((node) => {
                if (node.hasAttribute('disabled')) {
                    return false;
                }
                if (node.getAttribute('aria-hidden') === 'true') {
                    return false;
                }
                return true;
            });
        };

        const openMenu = (ctx) => {
            if (!ctx.drawer || !ctx.trigger) {
                return;
            }
            ctx.drawer.classList.add('is-open');
            ctx.header.classList.add('is-menu-open');
            ctx.trigger.classList.add('is-active');
            ctx.trigger.setAttribute('aria-expanded', 'true');
            ctx.trigger.setAttribute('aria-label', ctx.closeLabel);
            ctx.overlay?.classList.add('is-visible');
            setDrawerAriaHidden(ctx, false);
            updateBodyLock();

            const focusable = getFocusableNodes(ctx.drawer);
            if (focusable.length) {
                focusable[0].focus();
            } else {
                ctx.drawer.setAttribute('tabindex', '-1');
                ctx.drawer.focus();
            }
        };

        const toggleMenu = (ctx) => {
            const isOpen = ctx.drawer && ctx.drawer.classList.contains('is-open');
            if (isOpen) {
                closeMenu(ctx);
            } else {
                openMenu(ctx);
            }
        };

        headers.forEach((header) => {
            const trigger = header.querySelector('[data-menu-trigger]');
            const drawerId = trigger ? trigger.getAttribute('aria-controls') : null;
            const drawer = drawerId ? document.getElementById(drawerId) : header.querySelector('[data-menu-drawer]');
            const overlay =
                drawerId && drawerId.length
                    ? document.querySelector(`[data-menu-overlay-for="${drawerId}"]`)
                    : header.nextElementSibling && header.nextElementSibling.matches('[data-menu-overlay]')
                      ? header.nextElementSibling
                      : null;

            if (!trigger || !drawer) {
                return;
            }

            const ctx = {
                header,
                trigger,
                drawer,
                overlay,
                openLabel: trigger.getAttribute('aria-label') || 'Open menu',
                closeLabel: trigger.dataset.menuCloseLabel || 'Close menu',
            };

            setDrawerAriaHidden(ctx, true);

            const handleDrawerKeydown = (event) => {
                if (!ctx.drawer.classList.contains('is-open')) {
                    return;
                }
                if (event.key === 'Escape') {
                    event.preventDefault();
                    closeMenu(ctx);
                    return;
                }
                if (event.key !== 'Tab') {
                    return;
                }
                const focusable = getFocusableNodes(ctx.drawer);
                if (!focusable.length) {
                    event.preventDefault();
                    ctx.trigger.focus();
                    return;
                }
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                    return;
                }
                if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            };

            drawer.addEventListener('keydown', handleDrawerKeydown);

            trigger.addEventListener('click', () => {
                toggleMenu(ctx);
            });
            trigger.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
                    event.preventDefault();
                    toggleMenu(ctx);
                }
            });

            overlay?.addEventListener('click', () => {
                closeMenu(ctx);
            });

            // Add event listeners to all navigation links to close menu when clicked
            const navLinks = ctx.drawer.querySelectorAll('.landing-header__nav-link');
            navLinks.forEach(link => {
                link.addEventListener('click', () => {
                    closeMenu(ctx);
                });
            });

            contexts.push(ctx);
        });

        // Allow other modules (e.g., modal system) to force-close all mobile menus.
        window.addEventListener('mobile-menu:close-all', () => {
            contexts.forEach((ctx) => {
                closeMenu(ctx, { shouldRestoreFocus: false });
            });
            updateBodyLock();
        });

        const handleDesktopChange = () => {
            contexts.forEach((ctx) => {
                if (desktopQuery.matches) {
                    ctx.drawer.classList.remove('is-open');
                    ctx.overlay?.classList.remove('is-visible');
                    ctx.trigger?.classList.remove('is-active');
                    ctx.trigger?.setAttribute('aria-expanded', 'false');
                    ctx.trigger?.setAttribute('aria-label', ctx.openLabel);
                    ctx.header.classList.remove('is-menu-open');
                    ctx.drawer.removeAttribute('tabindex');
                    setDrawerAriaHidden(ctx, false);
                } else if (!ctx.drawer.classList.contains('is-open')) {
                    setDrawerAriaHidden(ctx, true);
                }
            });
            updateBodyLock();
        };

        if (desktopQuery.addEventListener) {
            desktopQuery.addEventListener('change', handleDesktopChange);
        } else {
            desktopQuery.addListener(handleDesktopChange);
        }

        handleDesktopChange();
    };

    const initSmoothScrollAnchors = () => {
        const allLinks = Array.from(document.querySelectorAll('a[href^="#"]'));
        if (!allLinks.length) {
            return;
        }

        const header = document.querySelector('.landing-header');

        const shouldHandleLink = (link) => {
            const href = link.getAttribute('href');
            if (!href || href === '#' || href === '#0') {
                return false;
            }
            if (link.dataset.modalTrigger) {
                return false;
            }
            if (href === '#main-content') {
                return false;
            }
            if (link.classList.contains('visually-hidden')) {
                return false;
            }
            return true;
        };

        const smoothLinks = allLinks.filter(shouldHandleLink);
        if (!smoothLinks.length) {
            return;
        }

        const getHeaderOffset = () => (header ? header.offsetHeight : 0);

        const resolveTarget = (hash) => {
            if (!hash || hash.length <= 1) {
                return null;
            }
            const targetId = hash.slice(1);
            const section = document.getElementById(targetId);
            if (!section) {
                return null;
            }
            if (targetId === 'faq') {
                const faqInner = section.querySelector('.landing-faq__inner');
                return faqInner || section;
            }
            return section;
        };

        const ensureFocusable = (element) => {
            if (!element) {
                return element;
            }
            const isNaturallyFocusable = element.matches(
                'a, button, input, textarea, select, details, [tabindex]:not([tabindex="-1"])'
            );
            if (!isNaturallyFocusable && !element.hasAttribute('tabindex')) {
                element.setAttribute('tabindex', '-1');
                element.dataset.scrollFocusable = 'true';
            }
            return element;
        };

        const focusTarget = (element) => {
            if (!element) {
                return;
            }
            window.requestAnimationFrame(() => {
                element.focus({ preventScroll: true });
            });
        };

        const getMarginTop = (element) => {
            if (!element) {
                return 0;
            }
            const computedStyles = window.getComputedStyle(element);
            const marginValue = parseFloat(computedStyles.marginTop);
            return Number.isFinite(marginValue) ? marginValue : 0;
        };

        const updateHash = (hash, mode = 'push') => {
            if (!hash) {
                return;
            }
            const supportsHistory = Boolean(window.history);
            const isReplace = mode === 'replace';
            const historyMethod = isReplace ? 'replaceState' : 'pushState';
            if (supportsHistory && typeof window.history[historyMethod] === 'function') {
                window.history[historyMethod](null, '', hash);
                return;
            }
            if (isReplace) {
                const baseUrl = window.location.href.replace(/#.*$/, '');
                window.location.replace(`${baseUrl}${hash}`);
                return;
            }
            window.location.hash = hash;
        };

        const scrollToTarget = (target, hash, behavior = 'smooth', hashUpdateMode = 'push') => {
            if (!target) {
                return;
            }

            const focusableTarget = ensureFocusable(target);
            const headerOffset = getHeaderOffset();
            const rect = focusableTarget.getBoundingClientRect();
            const currentScroll = window.pageYOffset || window.scrollY || 0;
            const marginTop = getMarginTop(focusableTarget);
            const targetTop = rect.top + currentScroll - headerOffset - marginTop;
            const finalTop = targetTop < 0 ? 0 : targetTop;

            window.scrollTo({
                top: finalTop,
                behavior,
            });

            focusTarget(focusableTarget);

            if (hashUpdateMode === 'push' || hashUpdateMode === 'replace') {
                updateHash(hash, hashUpdateMode);
            }
        };

        const handleClick = (event) => {
            const link = event.currentTarget;
            const hash = link.getAttribute('href');
            const target = resolveTarget(hash);
            if (!target) {
                return;
            }
            event.preventDefault();
            scrollToTarget(target, hash, 'smooth', 'push');
        };

        smoothLinks.forEach((link) => {
            link.addEventListener('click', handleClick);
        });

        if (window.location.hash) {
            const target = resolveTarget(window.location.hash);
            if (target) {
                window.requestAnimationFrame(() => {
                    scrollToTarget(target, window.location.hash, 'auto', 'replace');
                });
            }
        }
    };

    const initModalSystem = () => {
        const modals = Array.from(document.querySelectorAll('[data-modal]'));
        if (!modals.length) {
            return;
        }

        const triggers = Array.from(document.querySelectorAll('[data-modal-trigger]'));
        if (!triggers.length) {
            return;
        }

        const focusableSelector =
            'a[href]:not([tabindex="-1"]), button:not([disabled]):not([tabindex="-1"]), textarea:not([disabled]):not([tabindex="-1"]), input:not([disabled]):not([tabindex="-1"]), select:not([disabled]):not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])';
        const state = {
            activeModal: null,
            activeTrigger: null,
            lastFocused: null,
        };

        const getModalByName = (name) => modals.find((modal) => modal.dataset.modal === name);

        const getFocusableElements = (modal) =>
            Array.from(modal.querySelectorAll(focusableSelector)).filter((element) => {
                return element.offsetParent !== null || window.getComputedStyle(element).position === 'fixed';
            });

        const focusFirstElement = (modal) => {
            const focusableElements = getFocusableElements(modal);
            if (!focusableElements.length) {
                return;
            }
            const preferred = modal.querySelector('[data-modal-initial-focus]');
            const targetElement =
                preferred && focusableElements.includes(preferred) ? preferred : focusableElements[0];
            window.requestAnimationFrame(() => {
                targetElement.focus();
            });
        };

        const handleKeydown = (event) => {
            if (!state.activeModal) {
                return;
            }

            if (event.key === 'Escape') {
                event.preventDefault();
                closeModal(state.activeModal);
                return;
            }

            if (event.key !== 'Tab') {
                return;
            }

            const focusableElements = getFocusableElements(state.activeModal);
            if (!focusableElements.length) {
                return;
            }

            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];
            const isShiftPressed = event.shiftKey;
            const activeElement = document.activeElement;

            if (isShiftPressed && activeElement === firstElement) {
                event.preventDefault();
                lastElement.focus();
                return;
            }

            if (!isShiftPressed && activeElement === lastElement) {
                event.preventDefault();
                firstElement.focus();
            }
        };

        const openModal = (modal, trigger) => {
            if (!modal || modal === state.activeModal) {
                return;
            }

            if (state.activeModal) {
                closeModal(state.activeModal);
            }

            // Close any open mobile navigation drawers to avoid blurred overlays on small screens.
            window.dispatchEvent(new CustomEvent('mobile-menu:close-all'));
            document.body.classList.remove('has-mobile-menu-open');
            document.documentElement.classList.remove('has-mobile-menu-open');

            state.activeModal = modal;
            state.activeTrigger = trigger || null;
            state.lastFocused = document.activeElement;
            const sourceCtaId = trigger?.dataset?.ctaId ? String(trigger.dataset.ctaId).trim() : '';
            const sourceScenario = trigger?.dataset?.ctaScenario ? String(trigger.dataset.ctaScenario).trim() : '';
            if (sourceCtaId) {
                modal.dataset.modalTriggerCtaId = sourceCtaId;
            } else {
                delete modal.dataset.modalTriggerCtaId;
            }
            if (sourceScenario) {
                modal.dataset.modalTriggerScenario = sourceScenario;
            } else {
                delete modal.dataset.modalTriggerScenario;
            }

            modal.classList.add('is-active');
            modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('landing-page--modal-open');
            document.documentElement.classList.add('landing-page--modal-open');
            focusFirstElement(modal);
            document.addEventListener('keydown', handleKeydown, true);
        };

        const closeModal = (modal) => {
            const targetModal = modal || state.activeModal;
            if (!targetModal) {
                return;
            }

            const wasActive = state.activeModal === targetModal;

            targetModal.classList.remove('is-active');
            targetModal.setAttribute('aria-hidden', 'true');

            if (wasActive) {
                document.body.classList.remove('landing-page--modal-open');
                document.documentElement.classList.remove('landing-page--modal-open');
                document.removeEventListener('keydown', handleKeydown, true);
                const focusTarget = state.activeTrigger || state.lastFocused;
                state.activeModal = null;
                state.activeTrigger = null;
                state.lastFocused = null;
                if (focusTarget) {
                    window.requestAnimationFrame(() => {
                        focusTarget.focus();
                    });
                }
            }

            targetModal.dispatchEvent(
                new CustomEvent('modal:closed', {
                    bubbles: true,
                    detail: {
                        modal: targetModal,
                    },
                })
            );
        };

        triggers.forEach((trigger) => {
            const targetName = trigger.dataset.modalTrigger;
            if (!targetName) {
                return;
            }
            const targetModal = getModalByName(targetName);
            if (!targetModal) {
                return;
            }

            const handleOpen = (event) => {
                event.preventDefault();
                openModal(targetModal, trigger);
            };

            trigger.addEventListener('click', handleOpen);
            trigger.addEventListener('keydown', (event) => {
                const key = event.key;
                if (key === ' ' || key === 'Enter' || key === 'Spacebar') {
                    event.preventDefault();
                    openModal(targetModal, trigger);
                }
            });
        });

        document.addEventListener('modal:request-close', (event) => {
            if (!event) {
                return;
            }
            const detail = event.detail || {};
            let requestedModal = detail.modal;
            if (!(requestedModal instanceof HTMLElement) || !modals.includes(requestedModal)) {
                requestedModal = state.activeModal;
            }
            if (!requestedModal) {
                return;
            }
            closeModal(requestedModal);
        });

        modals.forEach((modal) => {
            const closeTargets = modal.querySelectorAll('[data-modal-close]');
            closeTargets.forEach((target) => {
                target.addEventListener('click', (event) => {
                    event.preventDefault();
                    closeModal(modal);
                });
            });
        });
    };

    const initPerformanceSwitcher = () => {
        const root = document.querySelector('[data-performance-root]');
        const payloadNode = document.getElementById('performance-data');
        if (!root || !payloadNode) {
            return;
        }

        let payload;
        try {
            payload = JSON.parse(payloadNode.textContent || '{}');
        } catch (error) {
            return;
        }

        if (!payload || typeof payload !== 'object' || !payload.strategies) {
            return;
        }

        const strategies = payload.strategies;
        let activePeriodYears = 5;
        const buttons = document.querySelectorAll('[data-performance-strategy]');
        const periodButtons = document.querySelectorAll('[data-performance-period-btn]');
        const periodNode = root.querySelector('[data-performance-period]');
        const summaryModeNode = root.querySelector('[data-performance-summary-mode]');
        const captionNode = root.querySelector('[data-performance-caption]');
        const liveSinceNode = root.querySelector('[data-performance-live-since]');
        const backtestWindowNode = root.querySelector('[data-performance-backtest-window]');
        const costAssumptionsNode = root.querySelector('[data-performance-cost-assumptions]');
        const calculationBasisNode = root.querySelector('[data-performance-calculation-basis]');
        const chartNode = root.querySelector('[data-performance-chart]');
        const chartCanvas = root.querySelector('[data-performance-chart-canvas]');
        const overlayNode = root.querySelector('[data-performance-overlay]');
        const hoverLine = overlayNode ? overlayNode.querySelector('[data-performance-hover-line]') : null;
        const tooltipNode = overlayNode ? overlayNode.querySelector('[data-performance-tooltip]') : null;
        const tooltipDateNode = overlayNode ? overlayNode.querySelector('[data-performance-tooltip-date]') : null;
        const tooltipValueNodes = {
            index: overlayNode ? overlayNode.querySelector('[data-performance-tooltip-value="index"]') : null,
            benchmark: overlayNode ? overlayNode.querySelector('[data-performance-tooltip-value="benchmark"]') : null,
        };
        const tooltipNameNodes = {
            index: overlayNode ? overlayNode.querySelector('[data-performance-tooltip-name="index"]') : null,
            benchmark: overlayNode ? overlayNode.querySelector('[data-performance-tooltip-name="benchmark"]') : null,
        };
        const tooltipRowNodes = {
            index: overlayNode ? overlayNode.querySelector('[data-performance-tooltip-row="index"]') : null,
            benchmark: overlayNode ? overlayNode.querySelector('[data-performance-tooltip-row="benchmark"]') : null,
        };
        const axisXGroup = chartNode ? chartNode.querySelector('.landing-performance__axis--x') : null;
        const axisYGroup = chartNode ? chartNode.querySelector('.landing-performance__axis--y') : null;
        const legendNodes = root.querySelectorAll('[data-performance-legend]');
        const metricNodes = root.querySelectorAll('[data-performance-metric]');
        const markers = {
            index: chartNode ? chartNode.querySelector('[data-series-marker="index"]') : null,
            benchmark: chartNode ? chartNode.querySelector('[data-series-marker="benchmark"]') : null,
        };
        const peakLayer = chartNode ? chartNode.querySelector('[data-peak-layer]') : null;
        const peakLine = chartNode ? chartNode.querySelector('[data-peak-line]') : null;
        const peakMarker = chartNode ? chartNode.querySelector('[data-peak-marker]') : null;
        const peakLabel = chartNode ? chartNode.querySelector('[data-peak-label]') : null;
        const peakLabelTitle = peakLabel ? peakLabel.querySelector('[data-peak-label-title]') : null;
        const peakLabelValue = peakLabel ? peakLabel.querySelector('[data-peak-label-value]') : null;
        const peakLabelDelta = peakLabel ? peakLabel.querySelector('[data-peak-label-delta]') : null;
        const SVG_NS = 'http://www.w3.org/2000/svg';
        const PEAK_AXIS_X = 20;
        const PEAK_LABEL_BOTTOM_GAP = 30;
        const PEAK_LABEL_LINE_GAP = 12;
        const PEAK_LABEL_MIN_Y = 28;
        const liveBacktest =
            payload.liveBacktest && typeof payload.liveBacktest === 'object' ? payload.liveBacktest : null;
        const liveBacktestByStrategyRaw =
            payload.liveBacktestByStrategy && typeof payload.liveBacktestByStrategy === 'object'
                ? payload.liveBacktestByStrategy
                : payload.live_backtest_by_strategy &&
                    typeof payload.live_backtest_by_strategy === 'object'
                  ? payload.live_backtest_by_strategy
                  : {};
        const resolveLiveBacktestForStrategy = (strategyKey) => {
            const key = typeof strategyKey === 'string' ? strategyKey.trim().toLowerCase() : '';
            if (key) {
                const strategyPayload = liveBacktestByStrategyRaw[key];
                if (strategyPayload && typeof strategyPayload === 'object') {
                    return strategyPayload;
                }
                if (key === 'risky') {
                    const aggressivePayload = liveBacktestByStrategyRaw.aggressive;
                    if (aggressivePayload && typeof aggressivePayload === 'object') {
                        return aggressivePayload;
                    }
                }
            }
            return liveBacktest;
        };

        const chartState = {
            points: {
                index: [],
                benchmark: [],
            },
            lastPoints: {
                index: null,
                benchmark: null,
            },
            labels: {
                index: 'AI Crypto Index',
                benchmark: 'BTC+ETH 50/50',
            },
            peak: null,
        };
        const touchPointerTypes = new Set(['touch', 'pen']);
        const interactionState = {
            activeTouchPointerId: null,
            touchTooltipPinned: false,
        };

        const resolveSvgBox = (svg) => {
            if (!svg) {
                return null;
            }
            const baseViewBox = svg.viewBox && svg.viewBox.baseVal;
            if (
                baseViewBox &&
                Number.isFinite(baseViewBox.width) &&
                Number.isFinite(baseViewBox.height) &&
                baseViewBox.width > 0 &&
                baseViewBox.height > 0
            ) {
                return { width: baseViewBox.width, height: baseViewBox.height };
            }
            const viewBoxAttr = typeof svg.getAttribute === 'function' ? svg.getAttribute('viewBox') : null;
            if (viewBoxAttr) {
                const parts = viewBoxAttr
                    .replace(/,/g, ' ')
                    .trim()
                    .split(/\s+/)
                    .map((value) => Number(value));
                if (parts.length === 4 && parts.every((num) => Number.isFinite(num))) {
                    const [, , width, height] = parts;
                    if (width > 0 && height > 0) {
                        return { width, height };
                    }
                }
            }
            const rect = typeof svg.getBoundingClientRect === 'function' ? svg.getBoundingClientRect() : null;
            if (
                rect &&
                Number.isFinite(rect.width) &&
                Number.isFinite(rect.height) &&
                rect.width > 0 &&
                rect.height > 0
            ) {
                return { width: rect.width, height: rect.height };
            }
            return null;
        };

        const normalizePoints = (points) => {
            if (!Array.isArray(points)) {
                return [];
            }
            return points
                .map((point) => ({
                    x: Number(point.x),
                    y: Number(point.y),
                    value: Number(point.value),
                    value_text: typeof point.value_text === 'string' ? point.value_text : '',
                    date: typeof point.date === 'string' ? point.date : '',
                    date_label: typeof point.date_label === 'string' ? point.date_label : '',
                }))
                .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
        };

        const createPointFromMarker = (series) => {
            if (!series) {
                return null;
            }
            const x = Number(series.marker_x);
            const y = Number(series.marker_y);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return null;
            }
            const fallback =
                Array.isArray(series.points) && series.points.length
                    ? series.points[series.points.length - 1]
                    : null;
            return {
                x,
                y,
                value:
                    fallback && Number.isFinite(Number(fallback.value))
                        ? Number(fallback.value)
                        : Number.NaN,
                value_text:
                    fallback && typeof fallback.value_text === 'string' ? fallback.value_text : '',
                date: fallback && typeof fallback.date === 'string' ? fallback.date : '',
                date_label:
                    fallback && typeof fallback.date_label === 'string' ? fallback.date_label : '',
            };
        };

        const normalizeIsoDate = (value) => {
            if (typeof value !== 'string') {
                return null;
            }
            const trimmed = value.trim();
            return /^\d{4}-\d{2}-\d{2}$/.test(trimmed) ? trimmed : null;
        };

        const normalizeLiveBacktestSeries = (series) => {
            if (!Array.isArray(series)) {
                return [];
            }
            return series
                .map((point) => {
                    const date = normalizeIsoDate(point?.date);
                    const value = Number(point?.value);
                    if (!date || !Number.isFinite(value)) {
                        return null;
                    }
                    return { date, value };
                })
                .filter((point) => point !== null);
        };

        const normalizeBasisPart = (value, fallback) => {
            if (typeof value !== 'string') {
                return fallback;
            }
            const trimmed = value.trim();
            return trimmed || fallback;
        };
        const formatIncluded = (flag) => (flag ? 'Included' : 'Excluded');
        const formatHumanDate = (isoDate) => {
            const parts = parseIsoDateParts(isoDate);
            if (!parts) {
                return typeof isoDate === 'string' ? isoDate : '';
            }
            return `${parts.day} ${MONTH_SHORT_NAMES[parts.month - 1]} ${parts.year}`;
        };
        const formatRange = (start, end) => (
            start && end ? `${formatHumanDate(start)} to ${formatHumanDate(end)}` : 'Unavailable'
        );
        const formatCostAssumptions = (feesIncluded, slippageIncluded) => {
            const normalizedFeesIncluded = Boolean(feesIncluded);
            const normalizedSlippageIncluded = Boolean(slippageIncluded);
            if (normalizedFeesIncluded === normalizedSlippageIncluded) {
                return formatIncluded(normalizedFeesIncluded);
            }
            return `Fees: ${formatIncluded(normalizedFeesIncluded)} | Slippage: ${formatIncluded(normalizedSlippageIncluded)}`;
        };
        const applyLiveBacktestTransparency = (strategyKey) => {
            const liveBacktestForStrategy = resolveLiveBacktestForStrategy(strategyKey);
            const liveStartDateForStrategy = normalizeIsoDate(liveBacktestForStrategy?.live_start_date);
            const backtestWindowStartForStrategy = normalizeIsoDate(
                liveBacktestForStrategy?.backtest_window_start
            );
            const backtestWindowEndForStrategy = normalizeIsoDate(
                liveBacktestForStrategy?.backtest_window_end
            );
            const strategyCalculationBasis =
                liveBacktestForStrategy?.calculation_basis &&
                typeof liveBacktestForStrategy.calculation_basis === 'object'
                    ? liveBacktestForStrategy.calculation_basis
                    : null;
            const calculationBasisTextForStrategy = [
                normalizeBasisPart(strategyCalculationBasis?.frequency, '1d'),
                normalizeBasisPart(strategyCalculationBasis?.currency, 'USD'),
                normalizeBasisPart(strategyCalculationBasis?.timestamp_policy, 'UTC daily close'),
            ].join(' | ');

            if (liveSinceNode) {
                liveSinceNode.textContent = liveStartDateForStrategy
                    ? formatHumanDate(liveStartDateForStrategy)
                    : 'Not available yet';
            }
            if (backtestWindowNode) {
                backtestWindowNode.textContent = formatRange(
                    backtestWindowStartForStrategy,
                    backtestWindowEndForStrategy
                );
            }
            if (costAssumptionsNode) {
                costAssumptionsNode.textContent = formatCostAssumptions(
                    liveBacktestForStrategy?.fees_included,
                    liveBacktestForStrategy?.slippage_included
                );
            }
            if (calculationBasisNode) {
                calculationBasisNode.textContent = calculationBasisTextForStrategy;
            }
        };
        const buildContinuousSeries = (baseSeries, liveContinuationSeries) => {
            const merged = [];
            const usedDates = new Set();

            const appendPoint = (point) => {
                if (!point || typeof point.date !== 'string' || !Number.isFinite(Number(point.value))) {
                    return;
                }
                if (usedDates.has(point.date)) {
                    return;
                }
                merged.push({
                    date: point.date,
                    value: Number(point.value),
                });
                usedDates.add(point.date);
            };

            baseSeries.forEach(appendPoint);
            if (!liveContinuationSeries.length) {
                return merged;
            }

            const liveStart = liveContinuationSeries[0]?.date || null;
            if (liveStart) {
                for (let index = merged.length - 1; index >= 0; index -= 1) {
                    if (merged[index].date >= liveStart) {
                        usedDates.delete(merged[index].date);
                        merged.splice(index, 1);
                    }
                }
            }

            liveContinuationSeries.forEach(appendPoint);
            merged.sort((left, right) => left.date.localeCompare(right.date));
            return merged;
        };

        const CHART_WIDTH = 640;
        const CHART_HEIGHT = 360;
        const CHART_PADDING_LEFT = 20;
        const CHART_PADDING_RIGHT = 20;
        const CHART_PADDING_TOP = 20;
        const CHART_PADDING_BOTTOM = 40;
        const MAX_PATH_POINTS = 420;
        const MODE_TAIL_POINTS = 48;
        const MONTH_SHORT_NAMES = [
            'Jan',
            'Feb',
            'Mar',
            'Apr',
            'May',
            'Jun',
            'Jul',
            'Aug',
            'Sep',
            'Oct',
            'Nov',
            'Dec',
        ];

        const parseIsoDateParts = (isoDate) => {
            if (typeof isoDate !== 'string') {
                return null;
            }
            const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate.trim());
            if (!match) {
                return null;
            }
            const year = Number.parseInt(match[1], 10);
            const month = Number.parseInt(match[2], 10);
            const day = Number.parseInt(match[3], 10);
            if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
                return null;
            }
            if (month < 1 || month > 12 || day < 1 || day > 31) {
                return null;
            }
            return { year, month, day };
        };
        const formatModeDateLabel = (isoDate) => {
            const parts = parseIsoDateParts(isoDate);
            if (!parts) {
                return typeof isoDate === 'string' ? isoDate : '';
            }
            const day = String(parts.day).padStart(2, '0');
            return `${day} ${MONTH_SHORT_NAMES[parts.month - 1]} ${parts.year}`;
        };
        const formatModeMonthLabel = (isoDate) => {
            const parts = parseIsoDateParts(isoDate);
            if (!parts) {
                return typeof isoDate === 'string' ? isoDate : '';
            }
            return `${MONTH_SHORT_NAMES[parts.month - 1]} ${parts.year}`;
        };
        const formatEquityValue = (value) => {
            if (!Number.isFinite(value)) {
                return 'N/A';
            }
            if (value >= 10) {
                return `${value.toFixed(0)}x`;
            }
            if (value >= 2) {
                return `${value.toFixed(1)}x`;
            }
            return `${value.toFixed(2)}x`;
        };
        const downsampleModeSeries = (
            series,
            maxPoints = MAX_PATH_POINTS,
            tailPointsToKeep = MODE_TAIL_POINTS
        ) => {
            const totalPoints = Array.isArray(series) ? series.length : 0;
            if (!totalPoints) {
                return { sampled: [], indices: [] };
            }
            if (totalPoints <= maxPoints) {
                return {
                    sampled: series.slice(),
                    indices: Array.from({ length: totalPoints }, (_, index) => index),
                };
            }

            const safeTailPoints = Math.min(
                Math.max(2, Number(tailPointsToKeep) || 0),
                Math.max(2, maxPoints - 1),
                totalPoints
            );
            const tailStartIndex = Math.max(totalPoints - safeTailPoints, 1);
            const headCapacity = Math.max(1, maxPoints - safeTailPoints);
            const headLastIndex = tailStartIndex - 1;

            const headIndices = [];
            if (headCapacity <= 1 || headLastIndex <= 0) {
                headIndices.push(0);
            } else {
                const headStep = headLastIndex / (headCapacity - 1);
                for (let index = 0; index < headCapacity; index += 1) {
                    headIndices.push(Math.round(headStep * index));
                }
            }

            const tailIndices = Array.from(
                { length: totalPoints - tailStartIndex },
                (_, offset) => tailStartIndex + offset
            );
            const indices = Array.from(new Set([...headIndices, ...tailIndices])).sort(
                (left, right) => left - right
            );
            if (indices[indices.length - 1] !== totalPoints - 1) {
                indices.push(totalPoints - 1);
            }
            return {
                sampled: indices.map((index) => series[index]),
                indices,
            };
        };
        const ensureYRange = (rawMin, rawMax) => {
            let minValue = Number(rawMin);
            let maxValue = Number(rawMax);
            if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
                minValue = 0;
                maxValue = 1;
            }
            if (Math.abs(maxValue - minValue) < Number.EPSILON) {
                const spread = minValue !== 0 ? Math.abs(minValue) : 1;
                minValue -= 0.1 * spread;
                maxValue += 0.1 * spread;
            }
            return { minValue, maxValue };
        };
        const buildChartAxesFromSeries = (series, yMin, yMax, maxXTicks = 5, maxYTicks = 5) => {
            const xTicks = [];
            const yTicks = [];
            const totalPoints = Array.isArray(series) ? series.length : 0;
            const usableWidth = CHART_WIDTH - CHART_PADDING_LEFT - CHART_PADDING_RIGHT;
            const usableHeight = CHART_HEIGHT - CHART_PADDING_TOP - CHART_PADDING_BOTTOM;

            if (totalPoints > 0) {
                const tickCount = Math.min(maxXTicks, totalPoints);
                let indices;
                if (tickCount <= 1) {
                    indices = [0];
                } else {
                    const step = (totalPoints - 1) / (tickCount - 1);
                    indices = Array.from({ length: tickCount }, (_, index) => Math.round(step * index));
                    indices = Array.from(new Set(indices)).sort((left, right) => left - right);
                    indices[0] = 0;
                    indices[indices.length - 1] = totalPoints - 1;
                }
                indices.forEach((index) => {
                    const ratio = totalPoints > 1 ? index / (totalPoints - 1) : 0;
                    const coordinate = CHART_PADDING_LEFT + usableWidth * ratio;
                    const point = series[index];
                    xTicks.push({
                        coordinate: Number(coordinate.toFixed(2)),
                        label: formatModeMonthLabel(point?.date),
                        value: point?.date || '',
                    });
                });
            }

            const yTickCount = Math.max(2, maxYTicks);
            for (let index = 0; index < yTickCount; index += 1) {
                const ratio = yTickCount > 1 ? index / (yTickCount - 1) : 0;
                const value = yMin + ratio * (yMax - yMin);
                const coordinate = CHART_HEIGHT - CHART_PADDING_BOTTOM - usableHeight * ratio;
                yTicks.push({
                    coordinate: Number(coordinate.toFixed(2)),
                    label: formatEquityValue(value),
                    value,
                });
            }

            return {
                x_ticks: xTicks,
                y_ticks: yTicks,
            };
        };
        const projectModeSeries = (series, yMin, yMax) => {
            if (!Array.isArray(series) || !series.length) {
                return [];
            }
            const usableWidth = CHART_WIDTH - CHART_PADDING_LEFT - CHART_PADDING_RIGHT;
            const usableHeight = CHART_HEIGHT - CHART_PADDING_TOP - CHART_PADDING_BOTTOM;
            return series.map((point, index) => {
                const xRatio = series.length > 1 ? index / (series.length - 1) : 0;
                const yRatio = (point.value - yMin) / (yMax - yMin);
                const x = CHART_PADDING_LEFT + usableWidth * xRatio;
                const y = CHART_HEIGHT - CHART_PADDING_BOTTOM - usableHeight * yRatio;
                return {
                    x: Number(x.toFixed(2)),
                    y: Number(y.toFixed(2)),
                };
            });
        };
        const coordsToLinePath = (coords) => {
            if (!Array.isArray(coords) || !coords.length) {
                return '';
            }
            return coords
                .map((coord, index) => `${index === 0 ? 'M' : 'L'} ${coord.x} ${coord.y}`)
                .join(' ');
        };
        const coordsToFillPath = (coords) => {
            if (!Array.isArray(coords) || !coords.length) {
                return '';
            }
            const bottom = CHART_HEIGHT - CHART_PADDING_BOTTOM;
            const first = coords[0];
            const last = coords[coords.length - 1];
            const linePath = coordsToLinePath(coords);
            return `${linePath} L ${last.x} ${bottom} L ${first.x} ${bottom} Z`;
        };
        const buildSeriesPoints = (originalSeries, sampleIndices, coords) => {
            if (!Array.isArray(sampleIndices) || !Array.isArray(coords) || sampleIndices.length !== coords.length) {
                return [];
            }
            return sampleIndices.map((seriesIndex, pointIndex) => {
                const sourcePoint = originalSeries[seriesIndex];
                const coord = coords[pointIndex];
                const value = Number(sourcePoint?.value);
                const date = sourcePoint?.date || '';
                return {
                    x: coord.x,
                    y: coord.y,
                    value,
                    value_text: formatEquityValue(value),
                    date,
                    date_label: formatModeDateLabel(date),
                };
            });
        };
        const buildAlignedSeriesByDate = (baseSeries, referenceSeries) => {
            if (!Array.isArray(baseSeries) || !baseSeries.length) {
                return [];
            }
            if (!Array.isArray(referenceSeries) || !referenceSeries.length) {
                return [];
            }
            const normalizedReference = referenceSeries
                .map((point) => {
                    const date = typeof point?.date === 'string' ? point.date : '';
                    const value = Number(point?.value);
                    if (!date || !Number.isFinite(value)) {
                        return null;
                    }
                    return { date, value };
                })
                .filter((point) => point !== null)
                .sort((left, right) => left.date.localeCompare(right.date));
            if (!normalizedReference.length) {
                return [];
            }
            const valueByDate = new Map();
            normalizedReference.forEach((point) => {
                const date = point.date;
                const value = point.value;
                valueByDate.set(date, value);
            });
            if (!valueByDate.size) {
                return [];
            }

            const firstReferenceValue = Number(normalizedReference[0]?.value);
            const lastReferenceDate = normalizedReference[normalizedReference.length - 1]?.date || '';
            let carryValue = Number.isFinite(firstReferenceValue) ? firstReferenceValue : Number.NaN;
            if (!Number.isFinite(carryValue)) {
                const firstValidReference = normalizedReference.find((point) =>
                    Number.isFinite(Number(point?.value))
                );
                carryValue = firstValidReference ? Number(firstValidReference.value) : Number.NaN;
            }
            if (!Number.isFinite(carryValue)) {
                return [];
            }

            const aligned = [];
            baseSeries.forEach((point) => {
                const date = typeof point?.date === 'string' ? point.date : '';
                if (!date) {
                    return;
                }
                if (lastReferenceDate && date > lastReferenceDate) {
                    return;
                }
                if (valueByDate.has(date)) {
                    carryValue = Number(valueByDate.get(date));
                }
                if (!Number.isFinite(carryValue)) {
                    return;
                }
                aligned.push({ date, value: carryValue });
            });
            return aligned.length === baseSeries.length ? aligned : [];
        };
        const buildModeChartDataset = (series, mode, benchmarkSeries = []) => {
            if (!Array.isArray(series) || !series.length) {
                return null;
            }

            const values = series.map((point) => Number(point.value)).filter((value) => Number.isFinite(value));
            if (!values.length) {
                return null;
            }
            const normalizedBenchmarkSeries = Array.isArray(benchmarkSeries)
                ? benchmarkSeries
                      .map((point) => {
                          const date = typeof point?.date === 'string' ? point.date : '';
                          const value = Number(point?.value);
                          if (!date || !Number.isFinite(value)) {
                              return null;
                          }
                          return { date, value };
                      })
                      .filter((point) => point !== null)
                : [];
            const benchmarkAligned =
                normalizedBenchmarkSeries.length === series.length ? normalizedBenchmarkSeries : [];
            const benchmarkValues = benchmarkAligned
                .map((point) => Number(point.value))
                .filter((value) => Number.isFinite(value));
            const combinedValues = benchmarkValues.length ? [...values, ...benchmarkValues] : values;

            const rawMin = Math.min(...combinedValues);
            const rawMax = Math.max(...combinedValues);
            const { minValue: yMin, maxValue: yMax } = ensureYRange(rawMin, rawMax);
            const { sampled, indices } = downsampleModeSeries(series);
            const coords = projectModeSeries(sampled, yMin, yMax);
            const linePath = coordsToLinePath(coords);
            const fillPath = coordsToFillPath(coords);
            const points = buildSeriesPoints(series, indices, coords);
            const usableHeight = CHART_HEIGHT - CHART_PADDING_TOP - CHART_PADDING_BOTTOM;
            const lastValue = Number(series[series.length - 1].value);
            const markerRatio = (lastValue - yMin) / (yMax - yMin);
            const markerY = CHART_HEIGHT - CHART_PADDING_BOTTOM - usableHeight * markerRatio;
            const firstValue = Number(series[0].value);
            const totalReturn = Number.isFinite(firstValue) && firstValue !== 0
                ? (lastValue / firstValue) - 1
                : Number.NaN;
            let benchmarkChartPath = null;
            let benchmarkLegendDeltaText = 'N/A in mode chart';
            if (benchmarkAligned.length) {
                const benchmarkSampled = indices.map((index) => benchmarkAligned[index]);
                const benchmarkCoords = projectModeSeries(benchmarkSampled, yMin, yMax);
                const benchmarkLinePath = coordsToLinePath(benchmarkCoords);
                const benchmarkPoints = buildSeriesPoints(
                    benchmarkAligned,
                    indices,
                    benchmarkCoords
                );
                const benchmarkLastValue = Number(benchmarkAligned[benchmarkAligned.length - 1].value);
                const benchmarkMarkerRatio = (benchmarkLastValue - yMin) / (yMax - yMin);
                const benchmarkMarkerY =
                    CHART_HEIGHT - CHART_PADDING_BOTTOM - usableHeight * benchmarkMarkerRatio;
                benchmarkChartPath = {
                    line_path: benchmarkLinePath,
                    fill_path: null,
                    marker_x: CHART_WIDTH - CHART_PADDING_RIGHT,
                    marker_y: Number(benchmarkMarkerY.toFixed(2)),
                    points: benchmarkPoints,
                };

                const benchmarkFirstValue = Number(benchmarkAligned[0].value);
                const benchmarkTotalReturn =
                    Number.isFinite(benchmarkFirstValue) && benchmarkFirstValue !== 0
                        ? benchmarkLastValue / benchmarkFirstValue - 1
                        : Number.NaN;
                benchmarkLegendDeltaText = Number.isFinite(benchmarkTotalReturn)
                    ? `${(benchmarkTotalReturn * 100).toFixed(1)}% total return`
                    : 'N/A in mode chart';
            }
            const legendLabel = 'AI Crypto Index';
            const modeName =
                mode === 'continuous'
                    ? 'Backtest + Live'
                    : mode === 'live'
                    ? 'Live'
                    : 'Backtest';

            return {
                chart_period_label: `${series[0].date} to ${series[series.length - 1].date}`,
                chart_caption:
                    mode === 'continuous'
                        ? 'Backtest runs until the first monthly auto-run, then real monthly run results continue the curve.'
                        : mode === 'live'
                        ? 'Live mode shows real index history after launch (UTC daily closes).'
                        : 'Backtest mode shows historical simulation before Live since (UTC daily closes).',
                chart_paths: {
                    index: {
                        line_path: linePath,
                        fill_path: fillPath,
                        marker_x: CHART_WIDTH - CHART_PADDING_RIGHT,
                        marker_y: Number(markerY.toFixed(2)),
                        points,
                    },
                    benchmark: benchmarkChartPath,
                },
                chart_axes: buildChartAxesFromSeries(series, yMin, yMax),
                legend: [
                    {
                        css_modifier: 'index',
                        label: legendLabel,
                        delta_text: Number.isFinite(totalReturn)
                            ? `${(totalReturn * 100).toFixed(1)}% total return`
                            : `${modeName} series`,
                    },
                    {
                        css_modifier: 'benchmark',
                        label: 'BTC+ETH 50/50',
                        delta_text: benchmarkLegendDeltaText,
                    },
                ],
            };
        };
        const normalizeStrategyKey = (strategyKey) => {
            const key = typeof strategyKey === 'string' ? strategyKey.trim().toLowerCase() : '';
            if (!key) {
                return '';
            }
            return key === 'risky' ? 'aggressive' : key;
        };
        const subtractYears = (isoDate, years) => {
            const parts = parseIsoDateParts(isoDate);
            if (!parts) return isoDate;
            const y = String(parts.year - years).padStart(4, '0');
            const m = String(parts.month).padStart(2, '0');
            const d = String(parts.day).padStart(2, '0');
            return `${y}-${m}-${d}`;
        };
        const sliceSeriesByYears = (series, years) => {
            if (!years || !Array.isArray(series) || !series.length) return series;
            const cutoff = subtractYears(series[series.length - 1].date, years);
            const sliced = series.filter((p) => p.date >= cutoff);
            if (sliced.length < 2) return series;
            const base = Number(sliced[0].value);
            if (!Number.isFinite(base) || base === 0) return sliced;
            return sliced.map((p) => ({ ...p, value: p.value / base }));
        };

        const ANNUALIZATION_FACTOR = 365;
        const RISK_FREE_RATE = 0.05;

        const computeAnnualizedMetrics = (series) => {
            if (!Array.isArray(series) || series.length < 2) return null;
            const first = series[0];
            const last = series[series.length - 1];
            const firstDate = new Date(first.date);
            const lastDate = new Date(last.date);
            const totalDays = Math.max((lastDate - firstDate) / 86400000, 1);
            const years = totalDays / ANNUALIZATION_FACTOR;
            const totalReturn = last.value / first.value;
            const cagr = Math.pow(totalReturn, 1 / years) - 1;

            const logReturns = [];
            for (let i = 1; i < series.length; i++) {
                const prev = series[i - 1].value;
                const curr = series[i].value;
                if (prev > 0 && curr > 0) {
                    logReturns.push(Math.log(curr / prev));
                }
            }
            if (!logReturns.length) return null;
            const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
            const variance =
                logReturns.length > 1
                    ? logReturns.reduce((a, b) => a + (b - mean) ** 2, 0) / (logReturns.length - 1)
                    : 0;
            const std = Math.sqrt(variance);
            const vol = std * Math.sqrt(ANNUALIZATION_FACTOR);
            const excess = mean - RISK_FREE_RATE / ANNUALIZATION_FACTOR;
            const sharpe = std > 0 ? (excess / std) * Math.sqrt(ANNUALIZATION_FACTOR) : NaN;

            let runningMax = series[0].value;
            let maxDrawdown = 0;
            for (const point of series) {
                if (point.value > runningMax) runningMax = point.value;
                const dd = point.value / runningMax - 1;
                if (dd < maxDrawdown) maxDrawdown = dd;
            }

            return { cagr, vol, sharpe, maxDrawdown, totalReturn: totalReturn - 1, years };
        };

        const buildDynamicMetricCards = (indexMetrics, benchmarkMetrics) => {
            if (!indexMetrics) return null;
            const bm = benchmarkMetrics || {
                cagr: 0, vol: 0, sharpe: 0, maxDrawdown: 0, totalReturn: 0, years: 0,
            };

            const fmtDelta = (v, prec, suffix) =>
                Number.isFinite(v) ? `${v >= 0 ? '+' : ''}${v.toFixed(prec)}${suffix}` : 'N/A';
            const mod = (v, posGood) => {
                if (!Number.isFinite(v) || Math.abs(v) < 1e-6) return 'neutral';
                return (v >= 0) === posGood ? 'positive' : 'negative';
            };

            const cagrDelta = (indexMetrics.cagr - bm.cagr) * 100;
            const volGap = (bm.vol - indexMetrics.vol) * 100;
            const sharpeDelta = indexMetrics.sharpe - bm.sharpe;
            const cushion = (Math.abs(bm.maxDrawdown) - Math.abs(indexMetrics.maxDrawdown)) * 100;

            return [
                {
                    label: 'CAGR',
                    badge_text: `${(bm.cagr * 100).toFixed(1)}% baseline`,
                    badge_modifier: mod(cagrDelta, true),
                    value_text: `${(indexMetrics.cagr * 100).toFixed(1)}%`,
                    delta_text: fmtDelta(cagrDelta, 1, ' pts vs 50/50'),
                    delta_modifier: mod(cagrDelta, true),
                    note: `Compound CAGR across ${indexMetrics.years.toFixed(1)} years versus an equal-weight BTC+ETH mix.`,
                },
                {
                    label: 'Volatility',
                    badge_text: `${(bm.vol * 100).toFixed(1)}% baseline`,
                    badge_modifier: mod(volGap, true),
                    value_text: `${(indexMetrics.vol * 100).toFixed(1)}%`,
                    delta_text: fmtDelta(volGap, 1, ' pts tighter vs 50/50'),
                    delta_modifier: mod(volGap, true),
                    note: 'Dynamic volatility controls aim to dampen swings relative to the equal-weight benchmark.',
                },
                {
                    label: 'Sharpe',
                    badge_text: `${bm.sharpe.toFixed(2)} baseline`,
                    badge_modifier: mod(sharpeDelta, true),
                    value_text: `${indexMetrics.sharpe.toFixed(2)}`,
                    delta_text: fmtDelta(sharpeDelta, 2, ' vs 50/50'),
                    delta_modifier: mod(sharpeDelta, true),
                    note: `Sharpe calculated using a ${(RISK_FREE_RATE * 100).toFixed(1)}% risk-free reference rate.`,
                },
                {
                    label: 'Max drawdown',
                    badge_text: `${(Math.abs(bm.maxDrawdown) * 100).toFixed(1)}% baseline`,
                    badge_modifier: mod(cushion, true),
                    value_text: `${(Math.abs(indexMetrics.maxDrawdown) * 100).toFixed(1)}%`,
                    delta_text: fmtDelta(cushion, 1, ' pts cushion vs 50/50'),
                    delta_modifier: mod(cushion, true),
                    note: 'Adaptive hedges reduce drawdowns relative to the equal-weight BTC+ETH benchmark.',
                },
            ];
        };

        const resolveContinuousChartDatasetForStrategy = (strategyKey, snapshot) => {
            const normalizedStrategyKey = normalizeStrategyKey(strategyKey);
            if (!normalizedStrategyKey) {
                return null;
            }
            const liveBacktestForStrategy = resolveLiveBacktestForStrategy(normalizedStrategyKey);
            const liveSeries = normalizeLiveBacktestSeries(liveBacktestForStrategy?.live_series);
            const backtestSeries = normalizeLiveBacktestSeries(liveBacktestForStrategy?.backtest_series);
            const continuousSeries = buildContinuousSeries(backtestSeries, liveSeries);
            if (!continuousSeries.length) {
                return null;
            }
            const liveBacktestBenchmarkSeries = normalizeLiveBacktestSeries(
                liveBacktestForStrategy?.benchmark_series
            );
            const snapshotBenchmarkSeries = normalizeLiveBacktestSeries(
                snapshot?.chart_paths?.benchmark?.points
            );
            const benchmarkSeries =
                liveBacktestBenchmarkSeries.length > 0
                    ? liveBacktestBenchmarkSeries
                    : snapshotBenchmarkSeries;
            const alignedBenchmarkSeries = buildAlignedSeriesByDate(
                continuousSeries,
                benchmarkSeries
            );
            const slicedSeries = sliceSeriesByYears(continuousSeries, activePeriodYears);
            const slicedBenchmark = sliceSeriesByYears(
                alignedBenchmarkSeries.length ? alignedBenchmarkSeries : [],
                activePeriodYears
            );
            const chartDataset = buildModeChartDataset(
                slicedSeries,
                'continuous',
                slicedBenchmark
            );
            if (chartDataset) {
                chartDataset._slicedSeries = slicedSeries;
                chartDataset._slicedBenchmark = slicedBenchmark;
            }
            return chartDataset;
        };

        if (summaryModeNode) {
            summaryModeNode.textContent = 'Backtest + Live continuity';
        }
        applyLiveBacktestTransparency(root.dataset.activeStrategy || payload.defaultKey || '');

        const applyStrategyChart = (snapshot) => {
            if (!snapshot) {
                return null;
            }
            const strategyKey =
                snapshot.strategy_key || root.dataset.activeStrategy || payload.defaultKey || '';
            const continuousChartDataset = resolveContinuousChartDatasetForStrategy(
                strategyKey,
                snapshot
            );
            const chartPeriodLabel = continuousChartDataset?.chart_period_label || snapshot.chart_period_label;
            const chartCaption = continuousChartDataset?.chart_caption || snapshot.chart_caption;
            const snapshotChartPaths =
                snapshot?.chart_paths && typeof snapshot.chart_paths === 'object'
                    ? snapshot.chart_paths
                    : null;
            const continuousChartPaths =
                continuousChartDataset?.chart_paths &&
                typeof continuousChartDataset.chart_paths === 'object'
                    ? continuousChartDataset.chart_paths
                    : null;
            const chartPaths = continuousChartPaths || snapshotChartPaths;
            const chartAxes = continuousChartDataset?.chart_axes || snapshot.chart_axes;
            const legend = continuousChartDataset?.legend || snapshot.legend;
            if (periodNode) {
                periodNode.textContent = chartPeriodLabel || '';
            }
            if (captionNode) {
                captionNode.textContent = chartCaption || '';
            }
            updateChart(chartPaths, chartAxes);
            updateLegend(legend);
            return continuousChartDataset;
        };

        const updateTooltipNames = () => {
            Object.entries(tooltipNameNodes).forEach(([key, node]) => {
                if (!node) {
                    return;
                }
                node.textContent = chartState.labels[key] || '';
            });
        };

        const setPeakState = (peak) => {
            chartState.peak = peak;
        };

        const clearPeakLabel = () => {
            if (peakLabelTitle) {
                peakLabelTitle.textContent = '';
                peakLabelTitle.setAttribute('dy', '0');
                peakLabelTitle.removeAttribute('y');
            }
            if (peakLabelValue) {
                peakLabelValue.textContent = '';
                peakLabelValue.setAttribute('dy', '0');
                peakLabelValue.removeAttribute('y');
            }
            if (peakLabelDelta) {
                peakLabelDelta.textContent = '';
                peakLabelDelta.setAttribute('dy', '0');
                peakLabelDelta.removeAttribute('y');
            }
            if (peakLabel) {
                peakLabel.removeAttribute('y');
            }
        };

        const setPeakVisibility = (active, seriesKey) => {
            if (!peakLayer) {
                return;
            }
            peakLayer.dataset.active = active ? 'true' : 'false';
            if (seriesKey) {
                peakLayer.dataset.series = seriesKey;
            } else {
                delete peakLayer.dataset.series;
            }
            if (!active) {
                clearPeakLabel();
            }
        };

        const setTooltipRowVisibility = (key, visible) => {
            const row = tooltipRowNodes[key];
            if (!row) {
                return;
            }
            row.style.display = visible ? '' : 'none';
        };

        const syncTooltipRows = () => {
            setTooltipRowVisibility('index', true);
            setTooltipRowVisibility('benchmark', chartState.points.benchmark.length > 0);
        };

        const setMarker = (key, point) => {
            const marker = markers[key];
            if (!marker) {
                return;
            }
            if (!point) {
                marker.style.opacity = key === 'index' ? '1' : '0';
                return;
            }
            marker.setAttribute('cx', String(point.x));
            marker.setAttribute('cy', String(point.y));
            marker.style.opacity = '1';
        };

        const setOverlayActive = (active) => {
            if (!overlayNode) {
                return;
            }
            overlayNode.dataset.active = active ? 'true' : 'false';
        };

        const resetMarkers = () => {
            interactionState.activeTouchPointerId = null;
            interactionState.touchTooltipPinned = false;
            setOverlayActive(false);
            setMarker('index', chartState.lastPoints.index);
            if (chartState.points.benchmark.length > 0) {
                setMarker('benchmark', chartState.lastPoints.benchmark);
            } else {
                setMarker('benchmark', null);
            }
            if (hoverLine) {
                hoverLine.style.left = '-9999px';
            }
            if (tooltipNode) {
                tooltipNode.style.left = '-9999px';
                tooltipNode.style.top = '-9999px';
                tooltipNode.style.removeProperty('--landing-performance-tooltip-shift');
                tooltipNode.style.removeProperty('--landing-performance-tooltip-arrow');
            }
            if (tooltipDateNode) {
                tooltipDateNode.textContent = '';
            }
            syncTooltipRows();
        };

        const renderAxisGroup = (group, ticks, axisKey) => {
            if (!group) {
                return;
            }
            while (group.firstChild) {
                group.removeChild(group.firstChild);
            }
            ticks.forEach((tick, index) => {
                if (!tick) {
                    return;
                }
                const textNode = document.createElementNS(SVG_NS, 'text');
                textNode.classList.add(
                    'landing-performance__axis-label',
                    `landing-performance__axis-label--${axisKey}`
                );
                if (axisKey === 'x') {
                    textNode.dataset.performanceAxisX = String(index);
                    textNode.setAttribute('x', String(tick.coordinate));
                    textNode.setAttribute('y', '348');
                } else {
                    textNode.dataset.performanceAxisY = String(index);
                    textNode.setAttribute('x', '12');
                    textNode.setAttribute('y', String(tick.coordinate));
                }
                textNode.setAttribute(
                    'data-axis-value',
                    typeof tick.value === 'undefined' ? '' : String(tick.value)
                );
                textNode.textContent = tick.label || '';
                group.appendChild(textNode);
            });
        };

        const comparePeakCandidates = (current, next) => {
            if (!next || !next.point) {
                return current;
            }
            if (!current || !current.point) {
                return next;
            }
            const nextHasValue = Number.isFinite(next.point.value);
            const currentHasValue = Number.isFinite(current.point.value);
            if (nextHasValue && currentHasValue) {
                if (next.point.value > current.point.value) {
                    return next;
                }
                if (next.point.value < current.point.value) {
                    return current;
                }
            } else if (nextHasValue && !currentHasValue) {
                return next;
            } else if (!nextHasValue && currentHasValue) {
                return current;
            }
            if (next.point.y < current.point.y) {
                return next;
            }
            if (next.point.y > current.point.y) {
                return current;
            }
            if (next.point.x > current.point.x) {
                return next;
            }
            return current;
        };

        const findPeakPoint = () => {
            let peak = null;
            const inspect = (points, seriesKey) => {
                if (!Array.isArray(points) || !points.length) {
                    return;
                }
                points.forEach((point) => {
                    peak = comparePeakCandidates(peak, { point, series: seriesKey });
                });
            };
            inspect(chartState.points.index, 'index');
            inspect(chartState.points.benchmark, 'benchmark');
            if (!peak || !peak.point) {
                return null;
            }
            return peak;
        };

        const updateAxes = (axes) => {
            if (!axes) {
                return;
            }
            renderAxisGroup(axisXGroup, Array.isArray(axes.x_ticks) ? axes.x_ticks : [], 'x');
            renderAxisGroup(axisYGroup, Array.isArray(axes.y_ticks) ? axes.y_ticks : [], 'y');
        };

        const formatTooltipValue = (point) => {
            if (!point) {
                return '—';
            }
            const baseText =
                point.value_text && point.value_text !== 'N/A'
                    ? point.value_text
                    : Number.isFinite(point.value)
                    ? `${point.value.toFixed(2)}x`
                    : '—';
            if (!Number.isFinite(point.value)) {
                return baseText;
            }
            const pct = (point.value - 1) * 100;
            if (!Number.isFinite(pct)) {
                return baseText;
            }
            const pctText = `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
            return `${baseText} · ${pctText}`;
        };

        const formatPeakLabel = (point) => {
            if (!point) {
                return { title: 'Peak', value: '', delta: '' };
            }
            const baseText =
                point.value_text && point.value_text !== 'N/A'
                    ? point.value_text
                    : Number.isFinite(point.value)
                    ? `${point.value.toFixed(2)}x`
                    : '';
            const pct = Number.isFinite(point.value) ? (point.value - 1) * 100 : Number.NaN;
            const deltaText = Number.isFinite(pct) ? `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%` : '';
            return {
                title: 'Peak',
                value: baseText,
                delta: deltaText,
            };
        };

        const updatePeakHighlight = () => {
            if (!peakLayer || !peakLine || !peakMarker || !peakLabel) {
                setPeakState(null);
                return;
            }
            const peak = findPeakPoint();
            if (!peak) {
                setPeakVisibility(false);
                setPeakState(null);
                return;
            }
            const { point, series } = peak;
            const axisX = PEAK_AXIS_X;
            const labelX = (axisX + point.x) / 2;

            peakLine.setAttribute('x1', String(axisX));
            peakLine.setAttribute('y1', String(point.y));
            peakLine.setAttribute('x2', String(point.x));
            peakLine.setAttribute('y2', String(point.y));
            peakMarker.setAttribute('cx', String(point.x));
            peakMarker.setAttribute('cy', String(point.y));

            const labelData = formatPeakLabel(point);
            const labelXString = String(labelX);
            const lineEntries = [
                { node: peakLabelTitle, text: labelData.title || '' },
                { node: peakLabelValue, text: labelData.value || '' },
                { node: peakLabelDelta, text: labelData.delta || '' },
            ];

            const activeEntries = lineEntries.filter((entry) => entry.node && entry.text);
            const lineCount = activeEntries.length || (peakLabelTitle ? 1 : 0);
            const desiredBottomY = point.y - PEAK_LABEL_BOTTOM_GAP;
            const startY = Math.max(
                desiredBottomY - (lineCount > 0 ? (lineCount - 1) * PEAK_LABEL_LINE_GAP : 0),
                PEAK_LABEL_MIN_Y
            );

            const positionedLines = [];
            let currentLineIndex = 0;
            lineEntries.forEach((entry) => {
                if (!entry.node) {
                    return;
                }
                const text = entry.text;
                entry.node.setAttribute('x', labelXString);
                entry.node.setAttribute('dy', '0');
                if (text) {
                    const yPos = startY + currentLineIndex * PEAK_LABEL_LINE_GAP;
                    entry.node.setAttribute('y', String(yPos));
                    entry.node.textContent = text;
                    positionedLines.push({ node: entry.node, y: yPos });
                    currentLineIndex += 1;
                } else {
                    entry.node.textContent = '';
                    entry.node.removeAttribute('y');
                }
            });

            if (peakLabel) {
                peakLabel.setAttribute('x', labelXString);
                peakLabel.setAttribute('y', String(startY));
            }

            if (peakLabel && positionedLines.length) {
                try {
                    if (typeof peakLabel.getBBox === 'function') {
                        const box = peakLabel.getBBox();
                        if (box && Number.isFinite(box.y) && Number.isFinite(box.height)) {
                            const targetBottom = point.y - PEAK_LABEL_BOTTOM_GAP;
                            let shift = targetBottom - (box.y + box.height);
                            let newTop = box.y + shift;
                            if (newTop < PEAK_LABEL_MIN_Y) {
                                shift += PEAK_LABEL_MIN_Y - newTop;
                                newTop = PEAK_LABEL_MIN_Y;
                            }
                            if (Math.abs(shift) > 0.5) {
                                const currentLabelY = Number(peakLabel.getAttribute('y'));
                                if (Number.isFinite(currentLabelY)) {
                                    peakLabel.setAttribute('y', String(currentLabelY + shift));
                                }
                                positionedLines.forEach((line) => {
                                    line.node.setAttribute('y', String(line.y + shift));
                                    line.y += shift;
                                });
                            }
                        }
                    }
                } catch (error) {
                    // getBBox may throw when the element is hidden; safe to ignore
                }
            }

            setPeakState(peak);
            setPeakVisibility(true, series);
        };

        const updateTooltipPosition = (pixelX, pixelY, rect) => {
            if (!tooltipNode) {
                return;
            }
            tooltipNode.style.left = `${pixelX}px`;
            tooltipNode.style.top = `${pixelY}px`;

            const tooltipWidth = tooltipNode.offsetWidth || 0;
            const containerWidth =
                rect && Number.isFinite(rect.width) && rect.width > 0
                    ? rect.width
                    : overlayNode && overlayNode.clientWidth
                    ? overlayNode.clientWidth
                    : 0;
            const halfWidth = tooltipWidth / 2;
            const edgePadding = 16;
            let shift = '-50%';
            let arrow = '50%';

            if (containerWidth > 0 && halfWidth > 0) {
                if (pixelX + halfWidth > containerWidth - edgePadding) {
                    shift = '-100%';
                    arrow = 'calc(100% - 12px)';
                } else if (pixelX - halfWidth < edgePadding) {
                    shift = '0%';
                    arrow = '12px';
                }
            }

            tooltipNode.style.setProperty('--landing-performance-tooltip-shift', shift);
            tooltipNode.style.setProperty('--landing-performance-tooltip-arrow', arrow);
        };

        const isTouchLikePointer = (event) => {
            const pointerType = typeof event?.pointerType === 'string' ? event.pointerType.toLowerCase() : '';
            return touchPointerTypes.has(pointerType);
        };

        const updateTooltipByClientX = (clientX) => {
            if (!chartNode || !chartCanvas || !chartState.points.index.length) {
                return false;
            }
            const rect = chartCanvas.getBoundingClientRect();
            const svgBox = resolveSvgBox(chartNode);
            const rectWidth =
                rect && Number.isFinite(rect.width) && rect.width > 0
                    ? rect.width
                    : chartCanvas.clientWidth || chartCanvas.offsetWidth || 0;
            const rectHeight =
                rect && Number.isFinite(rect.height) && rect.height > 0
                    ? rect.height
                    : chartCanvas.clientHeight || chartCanvas.offsetHeight || 0;
            const rectLeft = rect && Number.isFinite(rect.left) ? rect.left : 0;
            if (!Number.isFinite(clientX) || !svgBox || svgBox.width <= 0 || svgBox.height <= 0) {
                return false;
            }
            if (rectWidth <= 0 || rectHeight <= 0) {
                return false;
            }
            const relativeX = Math.min(Math.max(clientX - rectLeft, 0), rectWidth);
            const svgX = (relativeX / rectWidth) * svgBox.width;
            let nearestIndex = 0;
            let minDistance = Number.POSITIVE_INFINITY;
            chartState.points.index.forEach((point, index) => {
                const distance = Math.abs(point.x - svgX);
                if (distance < minDistance) {
                    minDistance = distance;
                    nearestIndex = index;
                }
            });
            const indexPoint = chartState.points.index[nearestIndex];
            if (!indexPoint) {
                return false;
            }
            const benchmarkPoint =
                chartState.points.benchmark.length > nearestIndex
                    ? chartState.points.benchmark[nearestIndex]
                    : null;
            const pixelX = (indexPoint.x / svgBox.width) * rectWidth;
            const pixelY = (indexPoint.y / svgBox.height) * rectHeight;
            setOverlayActive(true);
            setMarker('index', indexPoint);
            setMarker('benchmark', benchmarkPoint);
            if (hoverLine) {
                hoverLine.style.left = `${pixelX}px`;
            }
            updateTooltipPosition(pixelX, pixelY, { width: rectWidth });
            if (tooltipDateNode) {
                tooltipDateNode.textContent = indexPoint.date_label || indexPoint.date || '';
            }
            if (tooltipValueNodes.index) {
                tooltipValueNodes.index.textContent = formatTooltipValue(indexPoint);
            }
            if (tooltipValueNodes.benchmark) {
                if (benchmarkPoint) {
                    tooltipValueNodes.benchmark.textContent = formatTooltipValue(benchmarkPoint);
                    setTooltipRowVisibility('benchmark', true);
                } else {
                    tooltipValueNodes.benchmark.textContent = '—';
                    setTooltipRowVisibility('benchmark', false);
                }
            }
            return true;
        };

        const hideTouchTooltip = () => {
            if (interactionState.activeTouchPointerId !== null && chartCanvas) {
                if (
                    typeof chartCanvas.hasPointerCapture === 'function' &&
                    typeof chartCanvas.releasePointerCapture === 'function'
                ) {
                    try {
                        if (chartCanvas.hasPointerCapture(interactionState.activeTouchPointerId)) {
                            chartCanvas.releasePointerCapture(interactionState.activeTouchPointerId);
                        }
                    } catch (error) {
                        // Ignore invalid pointer capture state and proceed with reset.
                    }
                }
            }
            resetMarkers();
        };

        const handleMousePointerMove = (event) => {
            if (isTouchLikePointer(event)) {
                return;
            }
            interactionState.touchTooltipPinned = false;
            updateTooltipByClientX(event.clientX);
        };

        const handleMousePointerLeave = (event) => {
            if (isTouchLikePointer(event) || interactionState.touchTooltipPinned) {
                return;
            }
            resetMarkers();
        };

        const handleTouchPointerDown = (event) => {
            if (!isTouchLikePointer(event) || !chartCanvas) {
                return;
            }
            interactionState.activeTouchPointerId = event.pointerId;
            interactionState.touchTooltipPinned = true;
            if (typeof chartCanvas.setPointerCapture === 'function') {
                try {
                    chartCanvas.setPointerCapture(event.pointerId);
                } catch (error) {
                    // Ignore unsupported capture errors and continue.
                }
            }
            updateTooltipByClientX(event.clientX);
        };

        const handleTouchPointerMove = (event) => {
            if (!isTouchLikePointer(event)) {
                return;
            }
            if (interactionState.activeTouchPointerId !== event.pointerId) {
                return;
            }
            interactionState.touchTooltipPinned = true;
            updateTooltipByClientX(event.clientX);
        };

        const handleTouchPointerUp = (event) => {
            if (!isTouchLikePointer(event) || !chartCanvas) {
                return;
            }
            if (interactionState.activeTouchPointerId !== event.pointerId) {
                return;
            }
            if (
                typeof chartCanvas.hasPointerCapture === 'function' &&
                typeof chartCanvas.releasePointerCapture === 'function'
            ) {
                try {
                    if (chartCanvas.hasPointerCapture(event.pointerId)) {
                        chartCanvas.releasePointerCapture(event.pointerId);
                    }
                } catch (error) {
                    // Ignore invalid pointer capture state.
                }
            }
            interactionState.activeTouchPointerId = null;
        };

        const handleTouchPointerCancel = (event) => {
            if (!isTouchLikePointer(event)) {
                return;
            }
            if (
                interactionState.activeTouchPointerId !== null &&
                interactionState.activeTouchPointerId !== event.pointerId
            ) {
                return;
            }
            hideTouchTooltip();
        };

        const handleGlobalPointerDown = (event) => {
            if (!interactionState.touchTooltipPinned || !chartCanvas) {
                return;
            }
            const target = event.target;
            if (!(target instanceof Node)) {
                return;
            }
            if (chartCanvas.contains(target)) {
                return;
            }
            hideTouchTooltip();
        };

        const handleTouchTooltipDismiss = () => {
            if (!interactionState.touchTooltipPinned) {
                return;
            }
            hideTouchTooltip();
        };

        const handleGlobalKeydown = (event) => {
            if (event.key !== 'Escape') {
                return;
            }
            handleTouchTooltipDismiss();
        };

        const initHoverTracking = () => {
            if (!chartCanvas) {
                return;
            }
            chartCanvas.style.touchAction = 'pan-y';
            chartCanvas.addEventListener('pointerenter', handleMousePointerMove);
            chartCanvas.addEventListener('pointermove', handleMousePointerMove);
            chartCanvas.addEventListener('pointerdown', handleMousePointerMove);
            chartCanvas.addEventListener('pointerdown', handleTouchPointerDown);
            chartCanvas.addEventListener('pointermove', handleTouchPointerMove);
            chartCanvas.addEventListener('pointerup', handleTouchPointerUp);
            chartCanvas.addEventListener('pointercancel', handleTouchPointerCancel);
            chartCanvas.addEventListener('pointerleave', handleMousePointerLeave);
            document.addEventListener('pointerdown', handleGlobalPointerDown, true);
            window.addEventListener('scroll', handleTouchTooltipDismiss, { passive: true });
            window.addEventListener('resize', handleTouchTooltipDismiss);
            window.addEventListener('orientationchange', handleTouchTooltipDismiss);
            document.addEventListener('keydown', handleGlobalKeydown);
        };

        if (overlayNode) {
            overlayNode.dataset.active = 'false';
        }
        if (peakLayer) {
            peakLayer.dataset.active = 'false';
            clearPeakLabel();
        }
        syncTooltipRows();
        updateTooltipNames();
        initHoverTracking();

        const updateChart = (chartPaths, axes) => {
            if (!chartNode) {
                return;
            }
            const indexLine = chartNode.querySelector('[data-series-line="index"]');
            const benchmarkLine = chartNode.querySelector('[data-series-line="benchmark"]');
            const fillNode = chartNode.querySelector('[data-series-fill="index"]');
            const indexSeries = chartPaths?.index || null;
            const benchmarkSeries = chartPaths?.benchmark || null;

            if (indexLine && indexSeries) {
                indexLine.setAttribute('d', indexSeries.line_path || '');
            }
            if (benchmarkLine) {
                benchmarkLine.setAttribute('d', benchmarkSeries ? benchmarkSeries.line_path || '' : '');
            }
            if (fillNode) {
                if (indexSeries && indexSeries.fill_path) {
                    fillNode.setAttribute('d', indexSeries.fill_path);
                    fillNode.style.opacity = '1';
                } else {
                    fillNode.setAttribute('d', '');
                    fillNode.style.opacity = '0';
                }
            }

            chartState.points.index = normalizePoints(indexSeries?.points);
            chartState.points.benchmark = normalizePoints(benchmarkSeries?.points);
            chartState.lastPoints.index =
                chartState.points.index.length > 0
                    ? chartState.points.index[chartState.points.index.length - 1]
                    : createPointFromMarker(indexSeries);
            chartState.lastPoints.benchmark =
                chartState.points.benchmark.length > 0
                    ? chartState.points.benchmark[chartState.points.benchmark.length - 1]
                    : createPointFromMarker(benchmarkSeries);

            if (!chartState.lastPoints.benchmark) {
                chartState.points.benchmark = [];
            }

            syncTooltipRows();
            resetMarkers();
            updateAxes(axes);
            updatePeakHighlight();
        };

        const updateLegend = (legend) => {
            legendNodes.forEach((node) => {
                const index = Number.parseInt(node.dataset.performanceLegend || '', 10);
                if (Number.isNaN(index)) {
                    return;
                }
                const entry = legend?.[index];
                if (!entry) {
                    node.textContent = '';
                    return;
                }
                node.textContent = `${entry.label} - ${entry.delta_text}`;
            });

            if (Array.isArray(legend)) {
                const labels = { ...chartState.labels };
                legend.forEach((entry) => {
                    if (!entry) {
                        return;
                    }
                    if (entry.css_modifier === 'index') {
                        labels.index = entry.label;
                    }
                    if (entry.css_modifier === 'benchmark') {
                        labels.benchmark = entry.label;
                    }
                });
                chartState.labels = labels;
                updateTooltipNames();
            }
        };

        const updateMetrics = (metrics) => {
            metricNodes.forEach((article) => {
                const index = Number.parseInt(article.dataset.performanceMetric || '', 10);
                const card = !Number.isNaN(index) ? metrics?.[index] : null;
                const labelNode = article.querySelector('.landing-kpi__label');
                const badgeNode = article.querySelector('.landing-kpi__badge');
                const valueNode = article.querySelector('.landing-kpi__value');
                const deltaNode = article.querySelector('.landing-kpi__delta');
                const noteNode = article.querySelector('.landing-kpi__note');

                if (!card) {
                    return;
                }

                if (labelNode) {
                    labelNode.textContent = card.label;
                }
                if (badgeNode) {
                    badgeNode.textContent = card.badge_text;
                    badgeNode.dataset.variant = card.badge_modifier || 'neutral';
                }
                if (valueNode) {
                    valueNode.textContent = card.value_text;
                }
                if (deltaNode) {
                    deltaNode.textContent = card.delta_text;
                    deltaNode.dataset.trend = card.delta_modifier || 'neutral';
                }
                if (noteNode) {
                    noteNode.textContent = card.note;
                }
            });
        };

        const emitStrategyChange = (strategyKey) => {
            if (!strategyKey) {
                return;
            }
            window.dispatchEvent(
                new CustomEvent('performance:strategy-change', {
                    detail: { strategyKey },
                })
            );
        };

        const applySnapshot = (snapshot) => {
            if (!snapshot) {
                return;
            }
            const continuousDataset = applyStrategyChart(snapshot);
            const slicedIndex = continuousDataset?._slicedSeries;
            const slicedBenchmark = continuousDataset?._slicedBenchmark;
            if (slicedIndex && slicedIndex.length >= 2) {
                const indexMetrics = computeAnnualizedMetrics(slicedIndex);
                const benchmarkMetrics = computeAnnualizedMetrics(slicedBenchmark);
                const dynamicCards = buildDynamicMetricCards(indexMetrics, benchmarkMetrics);
                if (dynamicCards) {
                    updateMetrics(dynamicCards);
                } else {
                    updateMetrics(snapshot.metric_cards);
                }
            } else {
                updateMetrics(snapshot.metric_cards);
            }
            const strategyKey = snapshot.strategy_key || '';
            const normalizedStrategyKey = normalizeStrategyKey(strategyKey) || strategyKey;
            applyLiveBacktestTransparency(normalizedStrategyKey);
            root.dataset.activeStrategy = normalizedStrategyKey;
            emitStrategyChange(normalizedStrategyKey);
        };

        const setActiveButton = (activeKey) => {
            buttons.forEach((button) => {
                const key = button.dataset.performanceStrategy;
                const isActive = key === activeKey;
                button.classList.toggle('is-active', isActive);
                button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
        };

        const handleSwitch = (targetKey) => {
            if (!targetKey || !Object.prototype.hasOwnProperty.call(strategies, targetKey)) {
                return;
            }
            if (root.dataset.activeStrategy === targetKey) {
                return;
            }
            applySnapshot(strategies[targetKey]);
            setActiveButton(targetKey);
        };

        buttons.forEach((button) => {
            const key = button.dataset.performanceStrategy;
            if (!key || !Object.prototype.hasOwnProperty.call(strategies, key)) {
                return;
            }
            button.addEventListener('click', () => {
                handleSwitch(key);
            });
            button.addEventListener('keydown', (event) => {
                const keyName = event.key;
                if (keyName === 'Enter' || keyName === ' ') {
                    event.preventDefault();
                    handleSwitch(key);
                }
            });
        });

        const setActivePeriodButton = (years) => {
            periodButtons.forEach((btn) => {
                const isActive = Number(btn.dataset.performancePeriodBtn) === years;
                btn.classList.toggle('is-active', isActive);
                btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
        };

        periodButtons.forEach((btn) => {
            const years = Number(btn.dataset.performancePeriodBtn);
            if (!years) {
                return;
            }
            btn.addEventListener('click', () => {
                if (activePeriodYears === years) {
                    return;
                }
                activePeriodYears = years;
                setActivePeriodButton(years);
                const activeStrategyBtn = [...buttons].find(
                    (b) => b.getAttribute('aria-pressed') === 'true'
                );
                const currentKey =
                    (activeStrategyBtn && activeStrategyBtn.dataset.performanceStrategy) ||
                    payload.defaultKey ||
                    '';
                const snapshot = strategies[currentKey];
                if (snapshot) {
                    applySnapshot(snapshot);
                }
            });
        });

        const initialKey =
            (root.dataset.activeStrategy && strategies[root.dataset.activeStrategy]
                ? root.dataset.activeStrategy
                : null) ||
            (payload.defaultKey && strategies[payload.defaultKey] ? payload.defaultKey : null) ||
            Object.keys(strategies)[0];

        if (initialKey) {
            applySnapshot(strategies[initialKey]);
            setActiveButton(initialKey);
        }
    };

    const initPerformanceComposition = () => {
        const root = document.querySelector('[data-performance-composition-root]');
        const payloadNode = document.getElementById('composition-data');
        if (!root || !payloadNode) {
            return;
        }

        let payload;
        try {
            payload = JSON.parse(payloadNode.textContent || '{}');
        } catch (error) {
            return;
        }
        if (!payload || typeof payload !== 'object') {
            return;
        }

        const monthSelectNode = root.querySelector('[data-performance-composition-month]');
        const strategySelectNode = root.querySelector('[data-performance-composition-strategy]');
        const modeLabelNode = root.querySelector('[data-performance-composition-mode-label]');
        const sourceNode = root.querySelector('[data-performance-composition-source]');
        const bodyNode = root.querySelector('[data-performance-composition-body]');
        const panelNode = root.querySelector('[data-performance-composition-panel]');
        const toggleNode = root.querySelector('[data-performance-composition-toggle]');
        const toggleTextNode = toggleNode
            ? toggleNode.querySelector('.landing-performance__composition-toggle-text')
            : null;
        if (!monthSelectNode || !bodyNode) {
            return;
        }

        const MONTH_PATTERN = /^\d{4}-\d{2}$/;
        const COMPOSITION_PRESET_KEYS = ['classic', 'conservative', 'aggressive'];
        const COMPOSITION_PRESET_LABELS = {
            classic: 'Classic',
            conservative: 'Conservative',
            aggressive: 'Aggressive',
        };
        const normalizeStrategyKey = (value) => {
            if (typeof value !== 'string') {
                return '';
            }
            const trimmed = value.trim().toLowerCase();
            if (!trimmed) {
                return '';
            }
            return trimmed === 'risky' ? 'aggressive' : trimmed;
        };
        const normalizePresetStrategyKey = (value) => {
            const strategyKey = normalizeStrategyKey(value);
            return COMPOSITION_PRESET_KEYS.includes(strategyKey) ? strategyKey : '';
        };
        const normalizeMonth = (value) => {
            if (typeof value !== 'string') {
                return null;
            }
            const trimmed = value.trim();
            return MONTH_PATTERN.test(trimmed) ? trimmed : null;
        };
        const normalizeText = (value, fallback = '-') => {
            if (typeof value !== 'string') {
                return fallback;
            }
            const trimmed = value.trim();
            return trimmed || fallback;
        };
        const normalizeSnapshots = (snapshots) => {
            if (!Array.isArray(snapshots)) {
                return [];
            }
            return snapshots
                .map((item) => {
                    const month = normalizeMonth(item?.month);
                    const asset = normalizeText(item?.asset, '');
                    const weight = Number(item?.weight);
                    if (!month || !asset || !Number.isFinite(weight)) {
                        return null;
                    }
                    return {
                        month,
                        asset,
                        weight,
                        source: normalizeText(item?.source, 'unknown'),
                        runId: normalizeText(item?.run_id, '-'),
                    };
                })
                .filter((item) => item !== null);
        };

        const normalizeLiveBacktestByStrategy = () => {
            const raw =
                payload.liveBacktestByStrategy && typeof payload.liveBacktestByStrategy === 'object'
                    ? payload.liveBacktestByStrategy
                    : payload.live_backtest_by_strategy &&
                        typeof payload.live_backtest_by_strategy === 'object'
                      ? payload.live_backtest_by_strategy
                      : {};
            const normalized = {};
            Object.entries(raw).forEach(([key, value]) => {
                const strategyKey = normalizeStrategyKey(key);
                if (!strategyKey || !value || typeof value !== 'object') {
                    return;
                }
                normalized[strategyKey] = value;
                if (strategyKey === 'aggressive') {
                    normalized.risky = value;
                }
            });
            return normalized;
        };

        const normalizeSnapshotsByStrategy = (raw) => {
            const snapshotsByStrategy = new Map();
            if (!raw || typeof raw !== 'object') {
                return snapshotsByStrategy;
            }
            Object.entries(raw).forEach(([strategyKeyRaw, rowsRaw]) => {
                const strategyKey = normalizeStrategyKey(strategyKeyRaw);
                if (!strategyKey) {
                    return;
                }
                const rows = normalizeSnapshots(rowsRaw);
                snapshotsByStrategy.set(strategyKey, rows);
                if (strategyKey === 'aggressive') {
                    snapshotsByStrategy.set('risky', rows);
                }
            });
            return snapshotsByStrategy;
        };

        const normalizeCurrentMonthByStrategy = (raw) => {
            const currentMonthByStrategy = new Map();
            if (!raw || typeof raw !== 'object') {
                return currentMonthByStrategy;
            }
            Object.entries(raw).forEach(([strategyKeyRaw, monthRaw]) => {
                const strategyKey = normalizeStrategyKey(strategyKeyRaw);
                const month = normalizeMonth(monthRaw);
                if (!strategyKey || !month) {
                    return;
                }
                currentMonthByStrategy.set(strategyKey, month);
                if (strategyKey === 'aggressive') {
                    currentMonthByStrategy.set('risky', month);
                }
            });
            return currentMonthByStrategy;
        };

        const groupByMonth = (rows) => {
            const grouped = new Map();
            rows.forEach((row) => {
                if (!grouped.has(row.month)) {
                    grouped.set(row.month, []);
                }
                grouped.get(row.month).push(row);
            });
            grouped.forEach((rowsByMonth) => {
                rowsByMonth.sort((left, right) => right.weight - left.weight);
            });
            return grouped;
        };

        const monthlySnapshots = normalizeSnapshots(
            payload.monthlySnapshots ||
                payload.monthly_snapshots ||
                [
                    ...(payload.monthlyBacktestSnapshots || payload.monthly_backtest_snapshots || []),
                    ...(payload.monthlyLiveSnapshots || payload.monthly_live_snapshots || []),
                ]
        );
        const strategyMonthlySnapshots = normalizeSnapshotsByStrategy(
            payload.monthlySnapshotsByStrategy || payload.monthly_snapshots_by_strategy
        );
        const strategyCurrentMonth = normalizeCurrentMonthByStrategy(
            payload.monthlySnapshotsCurrentMonthByStrategy ||
                payload.monthly_snapshots_current_month_by_strategy
        );
        const defaultStrategyKey = normalizeStrategyKey(
            payload.defaultStrategyKey ||
                payload.default_strategy_key ||
                payload.monthlySnapshotsDefaultStrategy ||
                payload.monthly_snapshots_default_strategy
        );

        if (strategyMonthlySnapshots.size === 0) {
            const fallbackKey =
                defaultStrategyKey ||
                normalizeStrategyKey(root.dataset.activeStrategy || '') ||
                'classic';
            strategyMonthlySnapshots.set(fallbackKey, monthlySnapshots);
            if (fallbackKey === 'aggressive') {
                strategyMonthlySnapshots.set('risky', monthlySnapshots);
            }
            const fallbackMonth = normalizeMonth(
                payload.monthlySnapshotsCurrentMonth || payload.monthly_snapshots_current_month
            );
            if (fallbackMonth) {
                strategyCurrentMonth.set(fallbackKey, fallbackMonth);
                if (fallbackKey === 'aggressive') {
                    strategyCurrentMonth.set('risky', fallbackMonth);
                }
            }
        }

        const liveBacktestByStrategy = normalizeLiveBacktestByStrategy();
        const liveBacktestFallback =
            payload.liveBacktest && typeof payload.liveBacktest === 'object'
                ? payload.liveBacktest
                : payload.live_backtest && typeof payload.live_backtest === 'object'
                  ? payload.live_backtest
                  : null;
        const resolveLiveBacktestForStrategy = (strategyKey) => {
            const normalizedKey = normalizeStrategyKey(strategyKey);
            if (!normalizedKey) {
                return liveBacktestFallback;
            }
            if (liveBacktestByStrategy[normalizedKey]) {
                return liveBacktestByStrategy[normalizedKey];
            }
            if (normalizedKey === 'aggressive' && liveBacktestByStrategy.risky) {
                return liveBacktestByStrategy.risky;
            }
            return liveBacktestFallback;
        };

        const renderMonthOptions = (months, selectedMonth) => {
            monthSelectNode.innerHTML = '';
            if (!months.length) {
                const optionNode = document.createElement('option');
                optionNode.value = '';
                optionNode.textContent = 'No snapshots';
                monthSelectNode.appendChild(optionNode);
                monthSelectNode.disabled = true;
                monthSelectNode.setAttribute('aria-disabled', 'true');
                return;
            }
            months.forEach((month) => {
                const optionNode = document.createElement('option');
                optionNode.value = month;
                optionNode.textContent = month;
                optionNode.selected = month === selectedMonth;
                monthSelectNode.appendChild(optionNode);
            });
            monthSelectNode.disabled = false;
            monthSelectNode.removeAttribute('aria-disabled');
        };
        const renderStrategyOptions = (selectedStrategy) => {
            if (!strategySelectNode) {
                return;
            }
            strategySelectNode.innerHTML = '';
            COMPOSITION_PRESET_KEYS.forEach((strategyKey) => {
                const optionNode = document.createElement('option');
                optionNode.value = strategyKey;
                optionNode.textContent = COMPOSITION_PRESET_LABELS[strategyKey];
                optionNode.selected = strategyKey === selectedStrategy;
                strategySelectNode.appendChild(optionNode);
            });
        };
        const createTableValue = (text, className, title = '') => {
            const valueNode = document.createElement('span');
            valueNode.className = className;
            valueNode.textContent = text;
            valueNode.setAttribute('translate', 'no');
            valueNode.setAttribute('data-translate', 'no');
            valueNode.setAttribute('lang', 'en');
            if (title) {
                valueNode.title = title;
            }
            return valueNode;
        };
        const createTableCell = ({ text, className, label, valueClassName, title = '' }) => {
            const cellNode = document.createElement('td');
            cellNode.className = className;
            cellNode.dataset.label = label;
            cellNode.setAttribute('translate', 'no');
            cellNode.setAttribute('data-translate', 'no');
            cellNode.appendChild(createTableValue(text, valueClassName, title));
            return cellNode;
        };
        const createEmptyRow = (message) => {
            const rowNode = document.createElement('tr');
            rowNode.className = 'composition-table__row composition-table__row--empty';
            const cellNode = document.createElement('td');
            cellNode.className = 'composition-table__cell composition-table__cell--empty';
            cellNode.colSpan = 3;
            cellNode.textContent = message;
            rowNode.appendChild(cellNode);
            return rowNode;
        };
        const renderRows = (rows) => {
            bodyNode.innerHTML = '';
            if (!Array.isArray(rows) || !rows.length) {
                bodyNode.appendChild(createEmptyRow('No monthly snapshots are available.'));
                return;
            }
            rows.forEach((item, index) => {
                const rowNode = document.createElement('tr');
                rowNode.className = 'composition-table__row';
                rowNode.appendChild(
                    createTableCell(
                        {
                            text: String(index + 1),
                            className: 'composition-table__cell composition-table__cell--rank',
                            label: '#',
                            valueClassName: 'composition-table__value',
                        }
                    )
                );
                rowNode.appendChild(
                    createTableCell(
                        {
                            text: item.asset,
                            className: 'composition-table__cell composition-table__cell--asset',
                            label: 'Asset',
                            valueClassName: 'composition-table__text',
                            title: item.asset,
                        }
                    )
                );
                rowNode.appendChild(
                    createTableCell(
                        {
                            text: `${(item.weight * 100).toFixed(2)}%`,
                            className: 'composition-table__cell composition-table__cell--numeric',
                            label: 'Weight %',
                            valueClassName: 'composition-table__value',
                        }
                    )
                );
                bodyNode.appendChild(rowNode);
            });
        };
        const renderSource = (rows) => {
            if (!sourceNode) {
                return;
            }
            if (!rows.length) {
                sourceNode.textContent = 'No monthly snapshots are available.';
                return;
            }
            const first = rows[0];
            const sourceValue = typeof first.source === 'string' ? first.source : 'unknown';
            const sourceLabel = sourceValue.toLowerCase() === 'auto' ? 'live' : sourceValue;
            sourceNode.textContent = `Source: ${sourceLabel} | Run: ${first.runId}`;
        };
        const selectedMonthByStrategy = new Map();
        let activeStrategyKey = '';

        const resolveStrategyContext = (strategyKey) => {
            const resolvedStrategyKey =
                normalizePresetStrategyKey(strategyKey) ||
                normalizePresetStrategyKey(defaultStrategyKey) ||
                'classic';
            const rows = strategyMonthlySnapshots.get(resolvedStrategyKey) || [];
            const groupedByMonth = groupByMonth(rows);
            const months = Array.from(groupedByMonth.keys()).sort((left, right) =>
                right.localeCompare(left)
            );
            const currentMonth = strategyCurrentMonth.get(resolvedStrategyKey) || null;
            const liveBacktest = resolveLiveBacktestForStrategy(resolvedStrategyKey);
            const liveStartDate =
                liveBacktest && typeof liveBacktest.live_start_date === 'string'
                    ? liveBacktest.live_start_date
                    : '';
            const liveStartMonth = normalizeMonth(
                liveStartDate && liveStartDate.length >= 7 ? liveStartDate.slice(0, 7) : ''
            );
            return {
                strategyKey: resolvedStrategyKey,
                groupedByMonth,
                months,
                currentMonth,
                liveStartMonth,
            };
        };

        const resolveSelectedMonth = (context, requestedMonth) => {
            const normalizedRequested = normalizeMonth(requestedMonth);
            if (normalizedRequested && context.months.includes(normalizedRequested)) {
                return normalizedRequested;
            }
            const remembered = selectedMonthByStrategy.get(context.strategyKey);
            if (remembered && context.months.includes(remembered)) {
                return remembered;
            }
            if (context.currentMonth && context.months.includes(context.currentMonth)) {
                return context.currentMonth;
            }
            return context.months[0] || null;
        };

        const renderForStrategy = (strategyKey, requestedMonth = null) => {
            const context = resolveStrategyContext(strategyKey);
            activeStrategyKey = context.strategyKey;
            if (activeStrategyKey) {
                root.dataset.activeStrategy = activeStrategyKey;
            }
            const selectedMonth = resolveSelectedMonth(context, requestedMonth);
            if (selectedMonth) {
                selectedMonthByStrategy.set(activeStrategyKey, selectedMonth);
            } else {
                selectedMonthByStrategy.delete(activeStrategyKey);
            }

            renderStrategyOptions(activeStrategyKey);
            renderMonthOptions(context.months, selectedMonth);
            const rows = selectedMonth ? context.groupedByMonth.get(selectedMonth) || [] : [];
            if (modeLabelNode) {
                modeLabelNode.textContent =
                    Boolean(context.liveStartMonth) &&
                    typeof selectedMonth === 'string' &&
                    selectedMonth >= context.liveStartMonth
                        ? 'Live (real run)'
                        : 'Backtest (simulation)';
            }
            renderRows(rows);
            renderSource(rows);
        };

        const setCollapsedState = (collapsed) => {
            const isCollapsed = Boolean(collapsed);
            root.classList.toggle('is-collapsed', isCollapsed);
            if (panelNode) {
                panelNode.hidden = isCollapsed;
            }
            if (toggleNode) {
                toggleNode.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
            }
            if (toggleTextNode) {
                toggleTextNode.textContent = isCollapsed ? 'Expand' : 'Collapse';
            }
        };

        monthSelectNode.addEventListener('change', () => {
            if (!activeStrategyKey) {
                return;
            }
            renderForStrategy(activeStrategyKey, monthSelectNode.value);
        });
        if (strategySelectNode) {
            strategySelectNode.addEventListener('change', () => {
                renderForStrategy(strategySelectNode.value);
            });
        }
        if (toggleNode) {
            toggleNode.addEventListener('click', () => {
                const isCurrentlyExpanded = toggleNode.getAttribute('aria-expanded') === 'true';
                setCollapsedState(isCurrentlyExpanded);
            });
        }

        window.addEventListener('performance:strategy-change', (event) => {
            const incomingKey = normalizePresetStrategyKey(event.detail?.strategyKey || '');
            if (incomingKey && incomingKey !== activeStrategyKey) {
                renderForStrategy(incomingKey);
                if (strategySelectNode) {
                    strategySelectNode.value = incomingKey;
                }
            }
        });

        const initialStrategy =
            normalizePresetStrategyKey(root.dataset.activeStrategy || '') ||
            normalizePresetStrategyKey(defaultStrategyKey) ||
            'classic';
        renderForStrategy(initialStrategy);
        setCollapsedState(true);
    };

    const initIntakeForms = () => {
        const forms = document.querySelectorAll('[data-intake-form]');
        if (!forms.length) {
            return;
        }

        const parseErrorDetail = (body) => {
            if (!body || typeof body !== 'object') {
                return null;
            }
            if (typeof body.detail === 'string') {
                return body.detail;
            }
            if (Array.isArray(body.detail)) {
                return body.detail
                    .map((item) => {
                        if (!item) {
                            return null;
                        }
                        if (typeof item === 'string') {
                            return item;
                        }
                        if (typeof item.msg === 'string') {
                            return item.msg;
                        }
                        if (typeof item.message === 'string') {
                            return item.message;
                        }
                        return null;
                    })
                    .filter(Boolean)
                    .join(' ');
            }
            if (Array.isArray(body.errors)) {
                return body.errors
                    .map((item) => {
                        if (!item) {
                            return null;
                        }
                        if (typeof item === 'string') {
                            return item;
                        }
                        if (typeof item.msg === 'string') {
                            return item.msg;
                        }
                        if (typeof item.message === 'string') {
                            return item.message;
                        }
                        return null;
                    })
                    .filter(Boolean)
                    .join(' ');
            }
            if (typeof body.message === 'string') {
                return body.message;
            }
            return null;
        };

        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const requiredMessages = {
            terms_accepted: 'You must accept the terms of service and privacy policy.',
        };

        const getFieldContainer = (control) => {
            return control.closest('.landing-intake__field, .landing-intake__checkbox');
        };

        const resolveLabel = (container, control) => {
            const fallback =
                control.getAttribute('aria-label') ||
                control.placeholder ||
                control.name ||
                'This field';
            if (!container) {
                return fallback;
            }
            const textNode =
                container.querySelector('.landing-intake__label-text') ||
                container.querySelector('.landing-intake__label') ||
                container.querySelector('.landing-intake__checkbox-text');
            if (textNode && typeof textNode.textContent === 'string') {
                return textNode.textContent.replace(/\(\s*Optional\s*\)/gi, '').trim() || fallback;
            }
            if (typeof container.textContent === 'string' && container.textContent.trim()) {
                return container.textContent.replace(/\(\s*Optional\s*\)/gi, '').trim();
            }
            return fallback;
        };

        const ensureErrorNode = (container) => {
            if (!container) {
                return null;
            }
            let node = container.querySelector('.landing-intake__error');
            if (node) {
                return node;
            }
            node = document.createElement('p');
            node.className = 'landing-intake__error';
            node.setAttribute('role', 'status');
            node.setAttribute('aria-live', 'polite');
            container.appendChild(node);
            return node;
        };

        const setFieldError = (control, message) => {
            const container = getFieldContainer(control);
            if (!container) {
                return;
            }
            let errorNode = container.querySelector('.landing-intake__error');
            if (!message) {
                container.classList.remove('is-invalid');
                if (errorNode) {
                    errorNode.textContent = '';
                }
                if (typeof control.removeAttribute === 'function') {
                    control.removeAttribute('aria-invalid');
                }
                return;
            }
            if (!errorNode) {
                errorNode = ensureErrorNode(container);
            }
            if (!errorNode) {
                return;
            }
            container.classList.add('is-invalid');
            errorNode.textContent = message;
            if (typeof control.setAttribute === 'function') {
                control.setAttribute('aria-invalid', 'true');
            }
        };

        forms.forEach((form) => {
            const statusNode = form.querySelector('.landing-intake__status');
            const submitButton = form.querySelector('button[type="submit"]');
            const submitLabelNode = submitButton ? submitButton.querySelector('.landing-cta__text') : null;
            const boolFields = (form.dataset.boolFields || '')
                .split(',')
                .map((name) => name.trim())
                .filter(Boolean);
            const boolFieldSet = new Set(boolFields);
            const successMessage =
                form.dataset.successMessage || 'Thanks for your submission - we will follow up shortly.';
            const submittingMessage = form.dataset.submittingMessage || 'Submitting...';
            const originalLabel = submitLabelNode ? submitLabelNode.textContent || '' : '';
            const endpoint = form.getAttribute('action') || window.location.href;
            const intakeKey = form.dataset.intakeForm || '';
            let toastMessage = form.dataset.successToast || '';
            if (!toastMessage && intakeKey) {
                const selector =
                    typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
                        ? `[data-modal-trigger="${CSS.escape(intakeKey)}"]`
                        : `[data-modal-trigger="${intakeKey.replace(/"/g, '\\"')}"]`;
                const relatedTrigger = document.querySelector(selector);
                if (relatedTrigger && relatedTrigger.dataset && relatedTrigger.dataset.ctaConfirm) {
                        toastMessage = relatedTrigger.dataset.ctaConfirm;
                }
            }

            const modal = form.closest('[data-modal]');
            let touchedControls = new WeakSet();
            const controls = Array.from(form.querySelectorAll('input, textarea, select')).filter((control) => {
                return control && control.name && control.type !== 'hidden';
            });

            const setStatus = (variant, message) => {
                if (!statusNode) {
                    return;
                }
                statusNode.textContent = message || '';
                statusNode.classList.toggle('is-success', variant === 'success');
                statusNode.classList.toggle('is-error', variant === 'error');
            };

            const setButtonBusy = (isBusy) => {
                if (!submitButton) {
                    return;
                }
                submitButton.disabled = isBusy;
                submitButton.classList.toggle('is-loading', isBusy);
                if (submitLabelNode) {
                    submitLabelNode.textContent = isBusy ? submittingMessage : originalLabel;
                }
            };

            const validateControl = (control) => {
                if (!control || control.disabled || !control.name || control.type === 'hidden') {
                    return true;
                }

                const container = getFieldContainer(control);
                const labelText = resolveLabel(container, control);
                const fieldKey = control.name;
                let message = '';

                if (control.type === 'checkbox') {
                    if (control.required && !control.checked) {
                        message =
                            requiredMessages[fieldKey] ||
                            `Please confirm ${labelText.toLowerCase() || 'this option'}.`;
                    }
                } else if (control.tagName === 'SELECT') {
                    const value = control.value;
                    if (control.required && (!value || value.trim() === '')) {
                        message =
                            requiredMessages[fieldKey] ||
                            `Please choose ${labelText.toLowerCase() || 'an option'}.`;
                    }
                } else {
                    const rawValue = typeof control.value === 'string' ? control.value : '';
                    const value = rawValue.trim();
                    if (!value) {
                        if (control.required) {
                            message =
                                requiredMessages[fieldKey] || `${labelText || 'This field'} is required.`;
                        }
                    } else {
                        let minLength = Number.parseInt(control.getAttribute('minlength') || '', 10);
                        if (!Number.isFinite(minLength) || minLength < 0) {
                            minLength = NaN;
                        }
                        if (Number.isFinite(minLength) && minLength > 0 && value.length < minLength) {
                            message = `${labelText || 'This field'} must be at least ${minLength} characters.`;
                        }

                        const maxLengthAttr = control.getAttribute('maxlength');
                        const maxLength = maxLengthAttr ? Number.parseInt(maxLengthAttr, 10) : NaN;
                        if (!message && Number.isFinite(maxLength) && maxLength > 0 && value.length > maxLength) {
                            message = `${labelText || 'This field'} must be at most ${maxLength} characters.`;
                        }

                        if (!message && control.type === 'email' && !emailPattern.test(value)) {
                            message = 'Enter a valid work email.';
                        }
                    }
                }

                setFieldError(control, message);
                return !message;
            };

            const clearErrors = () => {
                controls.forEach((control) => {
                    setFieldError(control, '');
                });
            };

            const resetValidationState = () => {
                clearErrors();
                touchedControls = new WeakSet();
                setStatus(null, '');
            };

            if (modal) {
                modal.addEventListener('modal:closed', (event) => {
                    if (event && event.target && event.target !== modal) {
                        return;
                    }
                    form.reset();
                    resetValidationState();
                });
            }

            const validateForm = () => {
                const invalidControls = [];
                controls.forEach((control) => {
                    touchedControls.add(control);
                    if (!validateControl(control)) {
                        invalidControls.push(control);
                    }
                });
                return invalidControls;
            };

            const scheduleLiveValidation = (control) => {
                const container = getFieldContainer(control);
                if (!container) {
                    return;
                }
                if (touchedControls.has(control) || container.classList.contains('is-invalid')) {
                    validateControl(control);
                }
            };

            controls.forEach((control) => {
                const isCheckbox = control.type === 'checkbox';
                const isSelect = control.tagName === 'SELECT';
                const liveEvent = isCheckbox || isSelect ? 'change' : 'input';
                control.addEventListener(liveEvent, () => {
                    scheduleLiveValidation(control);
                });
                control.addEventListener('blur', () => {
                    touchedControls.add(control);
                    validateControl(control);
                });
            });

            form.addEventListener('submit', async (event) => {
                event.preventDefault();

                const invalidControls = validateForm();
                if (invalidControls.length) {
                    const focusTarget = invalidControls[0];
                    if (focusTarget && typeof focusTarget.focus === 'function') {
                        focusTarget.focus({ preventScroll: false });
                    }
                    setStatus('error', 'Please review the highlighted fields.');
                    return;
                }

                setStatus(null, 'Sending...');
                setButtonBusy(true);

                const formData = new FormData(form);
                const payload = {};

                for (const [key, value] of formData.entries()) {
                    if (boolFieldSet.has(key)) {
                        continue;
                    }
                    if (typeof value === 'string') {
                        const trimmed = value.trim();
                        if (trimmed !== '') {
                            payload[key] = trimmed;
                        }
                    } else if (value != null) {
                        payload[key] = value;
                    }
                }

                boolFields.forEach((name) => {
                    payload[name] = formData.has(name);
                });

                try {
                    const response = await fetch(endpoint, {
                        method: (form.getAttribute('method') || 'POST').toUpperCase(),
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify(payload),
                    });

                    if (!response.ok) {
                        const errorBody = await response.json().catch(() => null);
                        const detailMessage = parseErrorDetail(errorBody) || 'Submission failed. Please try again.';
                        const requestError = new Error(detailMessage);
                        requestError.status = response.status;
                        throw requestError;
                    }

                    form.reset();
                    resetValidationState();

                    if (modal) {
                        modal.dispatchEvent(
                            new CustomEvent('modal:request-close', {
                                bubbles: true,
                                detail: {
                                    modal,
                                },
                            })
                        );
                    } else {
                        setStatus('success', successMessage);
                    }

                    if (toastMessage) {
                        window.dispatchEvent(
                            new CustomEvent('cta:show-toast', {
                                detail: {
                                    message: toastMessage,
                                    variant: 'success',
                                },
                            })
                        );
                    }
                } catch (error) {
                    const fallback = 'Submission failed. Please try again.';
                    const message =
                        error instanceof Error && typeof error.message === 'string' ? error.message : fallback;
                    setStatus('error', message);
                } finally {
                    setButtonBusy(false);
                }
            });
        });
    };

    const initRegistrationFlow = () => {
        const modal = document.querySelector('[data-registration-modal]');
        const form = modal?.querySelector('[data-registration-form]');
        if (!modal || !form) {
            return;
        }

        const dataset = modal.dataset || {};
        const pagePath = window.location.pathname || '/';
        const utmSnapshot = resolveCtaUtmSnapshot();
        const ctaEndpointRaw = document.body?.dataset?.ctaEndpoint || '/api/v1/events/cta';
        const ctaEndpoint = ctaEndpointRaw && ctaEndpointRaw.trim() ? ctaEndpointRaw.trim() : '/api/v1/events/cta';
        const emailInput = form.querySelector('[data-registration-email-input]');
        const passwordInput = form.querySelector('[data-registration-password-input]');
        const passwordToggle = form.querySelector('[data-password-toggle]');
        const passwordToggleIcon = passwordToggle?.querySelector('.registration-password__toggle-icon');
        const passwordMeter = form.querySelector('[data-password-meter]');
        const strengthLabel = form.querySelector('[data-password-strength-label]');
        const progressSteps = Array.from(modal.querySelectorAll('[data-registration-progress-step]'));
        const steps = Array.from(modal.querySelectorAll('[data-registration-step]'));
        const statusNode = modal.querySelector('[data-registration-status]');
        const verifyEmailNode = modal.querySelector('[data-registration-email]');
        const resendButton = modal.querySelector('[data-registration-resend]');
        const countdownNode = modal.querySelector('[data-registration-countdown]');
        const openMailButton = modal.querySelector('[data-registration-open-mail]');
        const editEmailButton = modal.querySelector('[data-registration-edit-email]');
        const submitButton = form.querySelector('[data-registration-submit]');
        const submitLabelNode = submitButton ? submitButton.querySelector('.landing-cta__text') : null;
        const submitDefaultLabel = submitLabelNode ? submitLabelNode.textContent || '' : '';
        const resendLabelNode = resendButton ? resendButton.querySelector('.landing-cta__text') : null;
        const resendDefaultLabel = resendLabelNode ? resendLabelNode.textContent || '' : '';
        const countdownDefaultText = countdownNode ? countdownNode.textContent || '' : '';
        const termsCheckbox = form.querySelector('input[name="terms"]');
        const newsletterCheckbox = form.querySelector('input[name="newsletter_opt_in"]');
        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const defaultCountdownSeconds = (() => {
            const parsed = Number.parseInt(countdownDefaultText, 10);
            return Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
        })();
        const controls = [emailInput, passwordInput, termsCheckbox].filter(Boolean);
        let lastEmail = '';
        let touchedControls = new WeakSet();
        let resendTimer = null;

        const setStatus = (tone, message) => {
            if (!statusNode) {
                return;
            }
            statusNode.textContent = message || '';
            if (tone) {
                statusNode.dataset.tone = tone;
            } else {
                statusNode.removeAttribute('data-tone');
            }
        };

        const setSubmitBusy = (busy) => {
            if (!submitButton) {
                return;
            }
            submitButton.disabled = Boolean(busy);
            if (busy) {
                submitButton.setAttribute('aria-busy', 'true');
            } else {
                submitButton.removeAttribute('aria-busy');
            }
            if (submitLabelNode) {
                submitLabelNode.textContent = busy ? 'Creating account...' : submitDefaultLabel;
            }
        };

        const setResendBusy = (busy) => {
            if (!resendButton) {
                return;
            }
            if (busy) {
                resendButton.disabled = true;
                resendButton.setAttribute('aria-busy', 'true');
            } else {
                resendButton.removeAttribute('aria-busy');
            }
            if (resendLabelNode) {
                resendLabelNode.textContent = busy ? 'Resending...' : resendDefaultLabel;
            }
        };

        const getFieldContainer = (control) => control?.closest('.registration-field, .registration-checkbox');

        const ensureErrorNode = (container) => {
            if (!container) {
                return null;
            }
            const errorClass = container.classList.contains('registration-checkbox')
                ? 'registration-checkbox__error'
                : 'registration-field__error';
            let node = container.querySelector(`.${errorClass}`);
            if (node) {
                return node;
            }
            node = document.createElement('p');
            node.className = errorClass;
            node.setAttribute('role', 'status');
            node.setAttribute('aria-live', 'polite');
            container.appendChild(node);
            return node;
        };

        const setFieldError = (control, message) => {
            const container = getFieldContainer(control);
            if (!container) {
                return;
            }
            let errorNode = container.querySelector('.registration-field__error, .registration-checkbox__error');
            if (!message) {
                container.classList.remove('is-invalid');
                if (errorNode) {
                    errorNode.textContent = '';
                }
                if (control && typeof control.removeAttribute === 'function') {
                    control.removeAttribute('aria-invalid');
                }
                return;
            }
            if (!errorNode) {
                errorNode = ensureErrorNode(container);
            }
            if (!errorNode) {
                return;
            }
            container.classList.add('is-invalid');
            errorNode.textContent = message;
            if (control && typeof control.setAttribute === 'function') {
                control.setAttribute('aria-invalid', 'true');
            }
        };

        const evaluatePasswordStrength = (value) => {
            const text = typeof value === 'string' ? value : '';
            const hasLower = /[a-z]/.test(text);
            const hasUpper = /[A-Z]/.test(text);
            const hasNumber = /\d/.test(text);
            const hasSpecial = /[^A-Za-z0-9]/.test(text);
            const hasLetter = hasLower || hasUpper;
            let score = 0;
            if (text.length >= 10 && text.length < 14) {
                score += 1;
            } else if (text.length >= 14) {
                score += 2;
            }
            if (hasLetter && hasNumber) {
                score += 1;
            }
            if (hasSpecial) {
                score += 1;
            }
            if (!text.trim()) {
                score = 0;
            }
            score = Math.min(score, 3);
            let label = 'Weak';
            if (score === 2) {
                label = 'Fair';
            } else if (score >= 3) {
                label = 'Strong';
            }
            return {
                score,
                label,
                hasLetter,
                hasNumber,
                hasSpecial,
            };
        };

        const updatePasswordStrength = () => {
            const value = passwordInput ? passwordInput.value : '';
            const { score, label } = evaluatePasswordStrength(value);
            if (strengthLabel) {
                strengthLabel.dataset.score = String(score);
                strengthLabel.textContent = label;
            }
            if (passwordMeter) {
                passwordMeter.dataset.score = String(score);
            }
        };

        const setPasswordVisibility = (visible) => {
            if (!passwordInput || !passwordToggle) {
                return;
            }
            passwordInput.setAttribute('type', visible ? 'text' : 'password');
            passwordToggle.setAttribute('aria-pressed', visible ? 'true' : 'false');
            passwordToggle.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
            const iconAttr = visible ? 'hideIcon' : 'showIcon';
            if (passwordToggleIcon) {
                const nextSrc = passwordToggle.dataset[iconAttr] || passwordToggleIcon.getAttribute('src');
                if (nextSrc) {
                    passwordToggleIcon.setAttribute('src', nextSrc);
                }
            }
        };

        const initPasswordToggle = () => {
            if (!passwordInput || !passwordToggle) {
                return;
            }
            setPasswordVisibility(false);
            const toggleVisibility = () => {
                const shouldShow = passwordInput.getAttribute('type') === 'password';
                setPasswordVisibility(shouldShow);
                passwordInput.focus();
                if (typeof passwordInput.setSelectionRange === 'function') {
                    const cursor = passwordInput.value.length;
                    passwordInput.setSelectionRange(cursor, cursor);
                }
            };
            passwordToggle.addEventListener('click', (event) => {
                event.preventDefault();
                toggleVisibility();
            });
            passwordToggle.addEventListener('keydown', (event) => {
                const key = event.key;
                if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
                    event.preventDefault();
                    toggleVisibility();
                }
            });
        };

        const validateControl = (control) => {
            if (!control || control.disabled) {
                return true;
            }
            const type = control.type;
            const value = typeof control.value === 'string' ? control.value.trim() : '';
            let message = '';

            if (type === 'checkbox') {
                if (control.required && !control.checked) {
                    message = 'Please confirm you agree to the terms and privacy policy.';
                }
            } else if (type === 'email') {
                if (control.required && !value) {
                    message = 'Email is required.';
                } else if (value && !emailPattern.test(value)) {
                    message = 'Enter a valid work email.';
                }
            } else if (control === passwordInput) {
                if (control.required && !value) {
                    message = 'Password is required.';
                } else if (value && value.length < 10) {
                    message = 'Password must be at least 10 characters.';
                } else if (value && value.length > 160) {
                    message = 'Password must be at most 160 characters.';
                } else {
                    const { hasLetter, hasNumber, hasSpecial } = evaluatePasswordStrength(value);
                    if (!(hasLetter && hasNumber && hasSpecial)) {
                        message = 'Use letters, numbers, and special characters.';
                    }
                }
            }

            setFieldError(control, message);
            return !message;
        };

        const validateForm = () => {
            const invalidControls = [];
            controls.forEach((control) => {
                touchedControls.add(control);
                if (!validateControl(control)) {
                    invalidControls.push(control);
                }
            });
            return invalidControls;
        };

        const resetValidation = () => {
            controls.forEach((control) => {
                setFieldError(control, '');
            });
            touchedControls = new WeakSet();
            setStatus('', '');
        };

        const resetCountdown = () => {
            window.clearInterval(resendTimer);
            resendTimer = null;
            if (countdownNode) {
                countdownNode.textContent = countdownDefaultText;
            }
            if (resendButton) {
                resendButton.disabled = true;
                resendButton.removeAttribute('aria-busy');
            }
            if (resendLabelNode) {
                resendLabelNode.textContent = resendDefaultLabel;
            }
        };

        const switchStep = (nextStep) => {
            steps.forEach((step) => {
                if (step.dataset.registrationStep === nextStep) {
                    step.removeAttribute('hidden');
                } else {
                    step.setAttribute('hidden', '');
                }
            });
            progressSteps.forEach((step) => {
                const isActive = step.dataset.registrationProgressStep === nextStep;
                step.classList.toggle('is-active', isActive);
            });
        };

        const startResendCountdown = (seconds = defaultCountdownSeconds) => {
            if (!countdownNode || !resendButton) {
                return;
            }
            window.clearInterval(resendTimer);
            let remaining = Number.isFinite(seconds) && seconds > 0 ? seconds : defaultCountdownSeconds;
            resendButton.disabled = true;
            countdownNode.textContent = `${remaining}s`;
            resendTimer = window.setInterval(() => {
                remaining -= 1;
                if (remaining <= 0) {
                    window.clearInterval(resendTimer);
                    resendTimer = null;
                    countdownNode.textContent = '0s';
                    resendButton.disabled = false;
                    return;
                }
                countdownNode.textContent = `${remaining}s`;
            }, 1000);
        };

        const parseErrorMessage = (payload) => {
            if (!payload) {
                return null;
            }
            if (typeof payload.detail === 'string') {
                if (payload.detail === 'account_exists') {
                    return 'Account already exists. Try signing in instead.';
                }
                if (payload.detail === 'resend_rate_limited') {
                    return 'Too many requests. Try again in a minute.';
                }
                return payload.detail;
            }
            if (typeof payload.message === 'string') {
                return payload.message;
            }
            if (Array.isArray(payload.detail)) {
                return payload.detail
                    .map((item) => {
                        if (!item) {
                            return null;
                        }
                        if (typeof item.msg === 'string') {
                            return item.msg;
                        }
                        if (typeof item.message === 'string') {
                            return item.message;
                        }
                        if (typeof item === 'string') {
                            return item;
                        }
                        return null;
                    })
                    .filter(Boolean)
                    .join(' ');
            }
            return null;
        };

        const trackSignupStarted = () => {
            const sourceCtaId = typeof modal.dataset.modalTriggerCtaId === 'string' ? modal.dataset.modalTriggerCtaId : '';
            const sourceScenario =
                typeof modal.dataset.modalTriggerScenario === 'string' ? modal.dataset.modalTriggerScenario : '';
            const payload = {
                cta_id: 'signup_started',
                location: 'signup_modal',
                metadata: {
                    page_path: pagePath,
                    section: 'signup_modal',
                    placement: 'signup_modal',
                    scenario: sourceScenario || 'signup_started',
                    auth_state: 'anonymous',
                    event_type: 'signup_started',
                    source_cta_id: sourceCtaId || null,
                    utm_source: readCtaUtm(utmSnapshot, 'utm_source'),
                    utm_medium: readCtaUtm(utmSnapshot, 'utm_medium'),
                    utm_campaign: readCtaUtm(utmSnapshot, 'utm_campaign'),
                    utm_content: readCtaUtm(utmSnapshot, 'utm_content'),
                    utm_term: readCtaUtm(utmSnapshot, 'utm_term'),
                    session_id: getCtaSessionId(),
                },
            };
            return fetch(ctaEndpoint, {
                method: 'POST',
                headers: {
                    Accept: 'application/json',
                    'Content-Type': 'application/json',
                },
                keepalive: true,
                body: JSON.stringify(payload),
            }).catch(() => null);
        };

        const handleSubmit = async (event) => {
            event.preventDefault();
            const invalidControls = validateForm();
            if (invalidControls.length) {
                const focusTarget = invalidControls[0];
                if (focusTarget && typeof focusTarget.focus === 'function') {
                    focusTarget.focus();
                }
                setStatus('error', 'Please fix the highlighted fields.');
                return;
            }
            const email = String(emailInput?.value || '').trim().toLowerCase();
            const password = String(passwordInput?.value || '');
            const payload = {
                email,
                password,
                newsletter_opt_in: Boolean(newsletterCheckbox?.checked),
                terms_version: dataset.termsVersion || '2024-01',
                cta_session_id: getCtaSessionId(),
                source_cta_id: typeof modal.dataset.modalTriggerCtaId === 'string' ? modal.dataset.modalTriggerCtaId : null,
                source_page_path: pagePath,
                source_scenario:
                    typeof modal.dataset.modalTriggerScenario === 'string' ? modal.dataset.modalTriggerScenario : null,
            };
            if (!dataset.signupEndpoint) {
                setStatus('error', 'Signup endpoint unavailable.');
                return;
            }
            void trackSignupStarted();
            setStatus('info', 'Creating your account...');
            setSubmitBusy(true);
            try {
                const response = await fetch(dataset.signupEndpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(payload),
                });
                const body = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const message = parseErrorMessage(body) || 'Unable to create account. Try again.';
                    throw new Error(message);
                }
                lastEmail = typeof body?.email === 'string' && body.email ? body.email : email;
                if (verifyEmailNode && lastEmail) {
                    verifyEmailNode.textContent = lastEmail;
                }
                setStatus('success', 'Confirmation link sent. Check your inbox.');
                switchStep('verify');
                startResendCountdown(defaultCountdownSeconds);
                if (openMailButton) {
                    openMailButton.focus();
                }
            } catch (error) {
                const fallback = 'Unable to create account. Try again.';
                const message =
                    error instanceof Error && typeof error.message === 'string' ? error.message : fallback;
                setStatus('error', message);
            } finally {
                setSubmitBusy(false);
            }
        };

        const handleResend = async (event) => {
            event.preventDefault();
            const email = (lastEmail || String(emailInput?.value || '')).trim().toLowerCase();
            if (!email) {
                setStatus('error', 'Enter your email to resend the confirmation.');
                return;
            }
            if (!dataset.resendEndpoint) {
                setStatus('error', 'Resend endpoint unavailable.');
                return;
            }
            setStatus('info', 'Resending confirmation email...');
            setResendBusy(true);
            try {
                const response = await fetch(dataset.resendEndpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ email }),
                });
                const body = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const message = parseErrorMessage(body) || 'Unable to resend confirmation right now.';
                    throw new Error(message);
                }
                setStatus('success', 'If the email is registered, a new confirmation link is on the way.');
                startResendCountdown(defaultCountdownSeconds);
            } catch (error) {
                const fallback = 'Unable to resend confirmation right now.';
                const message =
                    error instanceof Error && typeof error.message === 'string' ? error.message : fallback;
                setStatus('error', message);
            } finally {
                setResendBusy(false);
            }
        };

        controls.forEach((control) => {
            const isCheckbox = control.type === 'checkbox';
            const eventName = isCheckbox ? 'change' : 'input';
            control.addEventListener(eventName, () => {
                if (control === passwordInput) {
                    updatePasswordStrength();
                }
                if (touchedControls.has(control) || control.closest('.is-invalid')) {
                    validateControl(control);
                }
            });
            control.addEventListener('blur', () => {
                touchedControls.add(control);
                validateControl(control);
            });
        });

        if (openMailButton) {
            openMailButton.addEventListener('click', (event) => {
                event.preventDefault();
                const email = lastEmail || String(emailInput?.value || '').trim();
                if (!email) {
                    setStatus('error', 'Enter your email to open your mail app.');
                    return;
                }
                window.location.href = `mailto:${email}`;
            });
        }

        if (editEmailButton) {
            editEmailButton.addEventListener('click', (event) => {
                event.preventDefault();
                switchStep('signup');
                resetCountdown();
                setStatus('', '');
                if (emailInput) {
                    emailInput.focus();
                }
            });
        }

        form.addEventListener('submit', handleSubmit);
        if (resendButton) {
            resendButton.addEventListener('click', handleResend);
        }

        modal.addEventListener('modal:closed', (event) => {
            if (event && event.target && event.target !== modal) {
                return;
            }
            form.reset();
            resetValidation();
            resetCountdown();
            if (verifyEmailNode) {
                verifyEmailNode.textContent = '';
            }
            switchStep('signup');
            setPasswordVisibility(false);
            updatePasswordStrength();
            lastEmail = '';
        });

        initPasswordToggle();
        updatePasswordStrength();
        switchStep('signup');
    };

    const initCtaTracking = () => {
        const ctaNodes = document.querySelectorAll('[data-cta-id]');
        if (!ctaNodes.length) {
            return;
        }

        const bodyDataset = document.body && document.body.dataset ? document.body.dataset : {};
        const defaultEndpoint = bodyDataset.ctaEndpoint || '/api/v1/events/cta';
        const toastNode = document.querySelector('[data-cta-toast]');
        const cooldownMap = new WeakMap();
        const isAdminPanel = window.location.pathname.startsWith('/admin');
        const pagePath = window.location.pathname || '/';
        const utmSnapshot = resolveCtaUtmSnapshot();
        let toastTimer = null;

        const showToast = (message, variant) => {
            if (!isAdminPanel || !toastNode || !message) {
                return;
            }
            window.clearTimeout(toastTimer);
            toastNode.textContent = message;
            toastNode.dataset.variant = variant || 'info';
            toastNode.classList.add('is-visible');
            toastTimer = window.setTimeout(() => {
                toastNode.classList.remove('is-visible');
            }, 4000);
        };

        window.addEventListener('cta:show-toast', (event) => {
            if (!event || !event.detail || !event.detail.message) {
                return;
            }
            const detail = event.detail;
            showToast(detail.message, detail.variant || 'info');
        });

        const normalizeLocation = (rawLocation) => {
            const value = typeof rawLocation === 'string' ? rawLocation.trim() : '';
            if (!value) {
                return pagePath;
            }
            if (value === '/') {
                return value;
            }
            if (value.startsWith('/')) {
                return value.replace(/^\/+/, '') || '/';
            }
            return value;
        };

        const resolveAuthState = (node) => {
            const explicit = (node.dataset.ctaAuthState || '').trim().toLowerCase();
            if (explicit === 'authenticated' || explicit === 'anonymous') {
                return explicit;
            }
            const globalState = (bodyDataset.isAuthenticated || '').trim().toLowerCase();
            if (globalState === 'true') {
                return 'authenticated';
            }
            if (globalState === 'false') {
                return 'anonymous';
            }
            return 'anonymous';
        };

        const parseMetadata = (node) => {
            if (!node.dataset.ctaMeta) {
                return {};
            }
            try {
                const parsed = JSON.parse(node.dataset.ctaMeta);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    return parsed;
                }
            } catch (error) {
                // ignore malformed metadata payloads
            }
            return {};
        };

        const buildMetadata = (node, location) => {
            const baseMetadata = {
                page_path: pagePath,
                section: node.dataset.ctaSection || location,
                placement: node.dataset.ctaPlacement || location,
                scenario: node.dataset.ctaScenario || 'navigate',
                event_type: 'cta_click',
                cta_format: node.dataset.ctaFormat || 'unknown',
                auth_state: resolveAuthState(node),
                utm_source: readCtaUtm(utmSnapshot, 'utm_source'),
                utm_medium: readCtaUtm(utmSnapshot, 'utm_medium'),
                utm_campaign: readCtaUtm(utmSnapshot, 'utm_campaign'),
                utm_content: readCtaUtm(utmSnapshot, 'utm_content'),
                utm_term: readCtaUtm(utmSnapshot, 'utm_term'),
                session_id: getCtaSessionId(),
            };
            return Object.assign(baseMetadata, parseMetadata(node));
        };

        const isNativeInteractiveControl = (node) => {
            const tagName = (node.tagName || '').toUpperCase();
            if (tagName === 'A' || tagName === 'BUTTON') {
                return true;
            }
            if (tagName !== 'INPUT') {
                return false;
            }
            const inputType = (node.getAttribute('type') || '').toLowerCase();
            return inputType === 'button' || inputType === 'submit' || inputType === 'reset';
        };

        const sendEvent = async (node) => {
            const ctaId = node.dataset.ctaId;
            if (!ctaId) {
                return;
            }

            const now = Date.now();
            const rawCooldown = Number.parseInt(node.dataset.ctaCooldown || '', 10);
            const cooldown = Number.isFinite(rawCooldown) ? rawCooldown : 8000;
            const lastHit = cooldownMap.get(node) || 0;
            if (now - lastHit < cooldown) {
                return;
            }
            cooldownMap.set(node, now);

            const location = normalizeLocation(node.dataset.ctaLocation);
            const payload = {
                cta_id: ctaId,
                location,
                metadata: buildMetadata(node, location),
            };

            const href = node.getAttribute('href');
            if (href) {
                payload.href = href;
            }

            try {
                const response = await fetch(node.dataset.ctaEndpoint || defaultEndpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    keepalive: true,
                    body: JSON.stringify(payload),
                });

                if (!response.ok) {
                    throw new Error('Request failed');
                }

                const skipToast = node.hasAttribute('data-cta-skip-toast');

                if (!node.dataset.modalTrigger && !skipToast) {
                    const confirmMessage = node.dataset.ctaConfirm || 'Action registered.';
                    showToast(confirmMessage, 'success');
                }
            } catch (error) {
                const fallback = node.dataset.ctaError || 'Unable to record the action. Please try again later.';
                showToast(fallback, 'error');
            }
        };

        ctaNodes.forEach((node) => {
            node.addEventListener('click', (event) => {
                if (event && typeof event.button === 'number' && event.button !== 0) {
                    return;
                }
                void sendEvent(node);
            });
            node.addEventListener('keydown', (event) => {
                if (!event || event.repeat) {
                    return;
                }
                const key = event.key;
                if (key === 'Enter') {
                    if (isNativeInteractiveControl(node)) {
                        return;
                    }
                    void sendEvent(node);
                    return;
                }
                if ((key === ' ' || key === 'Spacebar') && !isNativeInteractiveControl(node)) {
                    event.preventDefault();
                    void sendEvent(node);
                }
            });
        });
    };

    const initLoginFlow = () => {
        const root = document.querySelector('[data-login-root]');
        if (!root) {
            return;
        }

        const dataset = root.dataset || {};
        const states = Array.from(root.querySelectorAll('[data-login-state]'));
        const loginForm = root.querySelector('[data-login-form]');
        const loginStatus = root.querySelector('[data-login-status]');
        const loginSubmit = root.querySelector('[data-login-submit]');
        const forgotForm = root.querySelector('[data-login-forgot-form]');
        const forgotStatus = root.querySelector('[data-login-forgot-status]');
        const forgotSubmit = root.querySelector('[data-login-forgot-submit]');
        const forgotTrigger = root.querySelector('[data-login-forgot-trigger]');
        const backTrigger = root.querySelector('[data-login-back]');
        const successEmail = root.querySelector('[data-login-success-email]');
        const successCta = root.querySelector('[data-login-success-cta]');
        const nextUrl = (dataset.nextUrl || '').trim();
        const successRedirectUrl = (nextUrl && nextUrl.startsWith('/')) ? nextUrl : (successCta?.getAttribute('href') || '/app/overview');
        const passwordInput = root.querySelector('[data-login-password]');
        const passwordToggle = root.querySelector('[data-login-password-toggle]');
        const passwordToggleIcon = passwordToggle?.querySelector('.auth-confirm__password-toggle-icon');
        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const loginControls = loginForm
            ? Array.from(loginForm.querySelectorAll('input')).filter((control) => control && control.name)
            : [];
        let touchedControls = new WeakSet();

        const setStatus = (node, variant, message) => {
            if (!node) {
                return;
            }
            if (variant) {
                node.dataset.variant = variant;
            } else {
                delete node.dataset.variant;
            }
            node.textContent = message || '';
        };

        const setButtonBusy = (button, busy) => {
            if (!button) {
                return;
            }
            button.disabled = Boolean(busy);
            if (busy) {
                button.setAttribute('aria-busy', 'true');
            } else {
                button.removeAttribute('aria-busy');
            }
        };

        const getFieldContainer = (control) => control?.closest('.auth-confirm__field');

        const ensureErrorNode = (container) => {
            if (!container) {
                return null;
            }
            let node = container.querySelector('.auth-confirm__error');
            if (node) {
                return node;
            }
            node = document.createElement('p');
            node.className = 'auth-confirm__error';
            node.setAttribute('role', 'status');
            node.setAttribute('aria-live', 'polite');
            container.appendChild(node);
            return node;
        };

        const setFieldError = (control, message) => {
            const container = getFieldContainer(control);
            if (!container) {
                return;
            }
            let errorNode = container.querySelector('.auth-confirm__error');
            if (!message) {
                container.classList.remove('is-invalid');
                if (errorNode) {
                    errorNode.textContent = '';
                }
                if (control && typeof control.removeAttribute === 'function') {
                    control.removeAttribute('aria-invalid');
                }
                return;
            }
            if (!errorNode) {
                errorNode = ensureErrorNode(container);
            }
            if (!errorNode) {
                return;
            }
            container.classList.add('is-invalid');
            errorNode.textContent = message;
            if (control && typeof control.setAttribute === 'function') {
                control.setAttribute('aria-invalid', 'true');
            }
        };

        const validateLoginControl = (control) => {
            if (!control || control.disabled) {
                return true;
            }
            const type = control.type;
            const value = typeof control.value === 'string' ? control.value.trim() : '';
            let message = '';
            if (type === 'email') {
                if (!value) {
                    message = 'Email is required.';
                } else if (!emailPattern.test(value)) {
                    message = 'Enter a valid work email.';
                }
            } else if (type === 'password') {
                if (!value) {
                    message = 'Password is required.';
                } else if (value.length < 8) {
                    message = 'Password must be at least 8 characters.';
                } else {
                    const hasLetter = /[A-Za-z]/.test(value);
                    const hasNumber = /\d/.test(value);
                    if (!(hasLetter && hasNumber)) {
                        message = 'Use letters and numbers in your password.';
                    }
                }
            }
            setFieldError(control, message);
            return !message;
        };

        const validateLoginForm = () => {
            const invalid = [];
            loginControls.forEach((control) => {
                touchedControls.add(control);
                if (!validateLoginControl(control)) {
                    invalid.push(control);
                }
            });
            return invalid;
        };

        const resetLoginValidation = () => {
            loginControls.forEach((control) => setFieldError(control, ''));
            touchedControls = new WeakSet();
        };

        const switchState = (nextState) => {
            if (!states.length) {
                return;
            }
            states.forEach((node) => {
                if (node.dataset.loginState === nextState) {
                    node.removeAttribute('hidden');
                } else {
                    node.setAttribute('hidden', '');
                }
            });
        };

        const validateForm = (form) => {
            if (!form) {
                return false;
            }
            if (typeof form.reportValidity === 'function') {
                return form.reportValidity();
            }
            return true;
        };

        const readJson = async (response) => {
            try {
                return await response.json();
            } catch (_) {
                return {};
            }
        };

        const setPasswordVisibility = (visible) => {
            if (!passwordInput || !passwordToggle) {
                return;
            }
            passwordInput.setAttribute('type', visible ? 'text' : 'password');
            passwordToggle.setAttribute('aria-pressed', visible ? 'true' : 'false');
            passwordToggle.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
            if (passwordToggleIcon) {
                const iconAttr = visible ? 'hideIcon' : 'showIcon';
                const nextSrc = passwordToggle.dataset[iconAttr] || passwordToggleIcon.getAttribute('src');
                if (nextSrc) {
                    passwordToggleIcon.setAttribute('src', nextSrc);
                }
            }
        };

        const initPasswordToggle = () => {
            if (!passwordInput || !passwordToggle) {
                return;
            }
            setPasswordVisibility(false);
            const toggleVisibility = () => {
                const shouldShow = passwordInput.getAttribute('type') === 'password';
                setPasswordVisibility(shouldShow);
                passwordInput.focus();
                if (typeof passwordInput.setSelectionRange === 'function') {
                    const cursor = passwordInput.value.length;
                    passwordInput.setSelectionRange(cursor, cursor);
                }
            };
            passwordToggle.addEventListener('click', (event) => {
                event.preventDefault();
                toggleVisibility();
            });
            passwordToggle.addEventListener('keydown', (event) => {
                const key = event.key;
                if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
                    event.preventDefault();
                    toggleVisibility();
                }
            });
        };

        initPasswordToggle();

        if (forgotTrigger) {
            forgotTrigger.addEventListener('click', (event) => {
                event.preventDefault();
                switchState('forgot');
                resetLoginValidation();
                setStatus(loginStatus, '', '');
                setStatus(forgotStatus, '', '');
            });
        }

        if (backTrigger) {
            backTrigger.addEventListener('click', (event) => {
                event.preventDefault();
                switchState('form');
                resetLoginValidation();
                setStatus(loginStatus, '', '');
                setStatus(forgotStatus, '', '');
            });
        }

        if (loginForm) {
            loginControls.forEach((control) => {
                const eventName = control.type === 'checkbox' ? 'change' : 'input';
                control.addEventListener(eventName, () => {
                    if (touchedControls.has(control) || control.closest('.is-invalid')) {
                        validateLoginControl(control);
                    }
                });
                control.addEventListener('blur', () => {
                    touchedControls.add(control);
                    validateLoginControl(control);
                });
            });

            loginForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                const invalidControls = validateLoginForm();
                if (invalidControls.length) {
                    const focusTarget = invalidControls[0];
                    if (focusTarget && typeof focusTarget.focus === 'function') {
                        focusTarget.focus();
                    }
                    setStatus(loginStatus, 'error', 'Please fix the highlighted fields.');
                    return;
                }
                if (!dataset.loginEndpoint) {
                    setStatus(loginStatus, 'error', 'Login endpoint unavailable.');
                    return;
                }
                const formData = new FormData(loginForm);
                const email = String(formData.get('email') || '').trim();
                const password = String(formData.get('password') || '');
                if (!email || !password) {
                    setStatus(loginStatus, 'error', 'Enter your email and password.');
                    return;
                }
                setStatus(loginStatus, 'info', 'Signing you in…');
                setButtonBusy(loginSubmit, true);
                try {
                    const response = await fetch(dataset.loginEndpoint, {
                        method: 'POST',
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                        },
                        credentials: 'include',
                        body: JSON.stringify({ email, password }),
                    });
                    const payload = await readJson(response);
                    if (!response.ok) {
                        const detail = typeof payload?.detail === 'string' ? payload.detail : null;
                        if (detail === 'invalid_credentials') {
                            throw new Error('Invalid email or password. Double-check your credentials.');
                        }
                        if (detail === 'account_pending_activation') {
                            throw new Error('Account pending activation. Check your inbox for the confirmation link.');
                        }
                        const message =
                            typeof payload?.message === 'string'
                                ? payload.message
                                : 'Unable to complete sign in. Try again later.';
                        throw new Error(message);
                    }
                    if (successEmail) {
                        const profileEmail =
                            typeof payload?.profile === 'object' && payload.profile && typeof payload.profile.email === 'string'
                                ? payload.profile.email
                                : '';
                        successEmail.textContent = profileEmail || email;
                    }
                    switchState('success');
                    window.setTimeout(() => {
                        window.location.assign(successRedirectUrl);
                    }, 800);
                } catch (error) {
                    const fallback =
                        error instanceof Error && typeof error.message === 'string'
                            ? error.message
                            : 'Sign in failed. Try again.';
                    setStatus(loginStatus, 'error', fallback);
                } finally {
                    setButtonBusy(loginSubmit, false);
                }
            });
        }

        if (forgotForm) {
            forgotForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                if (!validateForm(forgotForm)) {
                    return;
                }
                if (!dataset.forgotEndpoint) {
                    setStatus(forgotStatus, 'error', 'Reset endpoint unavailable.');
                    return;
                }
                const formData = new FormData(forgotForm);
                const email = String(formData.get('email') || '').trim();
                if (!email) {
                    setStatus(forgotStatus, 'error', 'Enter your work email.');
                    return;
                }
                setStatus(forgotStatus, 'info', 'Sending reset link…');
                setButtonBusy(forgotSubmit, true);
                try {
                    const response = await fetch(dataset.forgotEndpoint, {
                        method: 'POST',
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ email }),
                    });
                    const payload = await readJson(response);
                    if (!response.ok) {
                        const message =
                            typeof payload?.message === 'string'
                                ? payload.message
                                : 'Unable to send reset link. Try again later.';
                        throw new Error(message);
                    }
                    const message =
                        typeof payload?.message === 'string'
                            ? payload.message
                            : 'If the email is registered, the reset link is on the way.';
                    setStatus(forgotStatus, 'success', message);
                } catch (error) {
                    const fallback =
                        error instanceof Error && typeof error.message === 'string'
                            ? error.message
                            : 'Unable to send reset link. Try again.';
                    setStatus(forgotStatus, 'error', fallback);
                } finally {
                    setButtonBusy(forgotSubmit, false);
                }
            });
        }
    };

    const initConfirmFlow = () => {
        const root = document.querySelector('[data-confirm-root]');
        if (!root) {
            return;
        }

        const dataset = root.dataset || {};
        const states = Array.from(root.querySelectorAll('[data-confirm-state]'));
        const profileForm = root.querySelector('[data-confirm-profile-form]');
        const profileSubmit = root.querySelector('[data-confirm-profile-submit]');
        const profileStatus = root.querySelector('[data-confirm-profile-status]');
        const skipButton = root.querySelector('[data-confirm-skip]');
        const resendForm = root.querySelector('[data-confirm-resend-form]');
        const resendStatus = root.querySelector('[data-confirm-resend-status]');
        const confirmEmailNode = root.querySelector('[data-confirm-email]');
        const errorTextNode = root.querySelector('[data-confirm-error-text]');
        const successCta = root.querySelector('.auth-confirm__cta');
        const noCompanyInput = profileForm?.querySelector('input[name="no_company"]');
        const orgFields = profileForm
            ? Array.from(profileForm.querySelectorAll('[name="organization_name"], [name="organization_size"]'))
            : [];
        const resendEmailInput = resendForm?.querySelector('input[name="email"]');
        let accessToken = '';

        const setState = (nextState) => {
            states.forEach((node) => {
                if (node.dataset.confirmState === nextState) {
                    node.removeAttribute('hidden');
                } else {
                    node.setAttribute('hidden', '');
                }
            });
        };

        const setStatus = (node, variant, message) => {
            if (!node) {
                return;
            }
            if (variant) {
                node.dataset.variant = variant;
            } else {
                delete node.dataset.variant;
            }
            node.textContent = message || '';
        };

        const setButtonBusy = (button, busy) => {
            if (!button) {
                return;
            }
            button.disabled = Boolean(busy);
            if (busy) {
                button.setAttribute('aria-busy', 'true');
            } else {
                button.removeAttribute('aria-busy');
            }
        };

        const readJson = async (response) => {
            try {
                return await response.json();
            } catch (_) {
                return {};
            }
        };

        const setErrorState = (message) => {
            if (errorTextNode && message) {
                errorTextNode.textContent = message;
            }
            setState('error');
        };

        const getTextValue = (value) => (typeof value === 'string' ? value.trim() : '');

        const getEmailLocalPart = (emailValue) => {
            const normalized = getTextValue(emailValue).toLowerCase();
            if (!normalized) {
                return '';
            }
            const atIndex = normalized.indexOf('@');
            return atIndex === -1 ? normalized : normalized.slice(0, atIndex);
        };

        const resolveRedirectUrl = () => {
            const href = successCta?.getAttribute('href');
            const trimmed = getTextValue(href);
            return trimmed || '/app/overview';
        };

        const isProfileComplete = (profile) => {
            if (!profile || typeof profile !== 'object') {
                return false;
            }
            const emailLocal = getEmailLocalPart(profile.email);
            const fullName = getTextValue(profile.full_name);
            const hasMeaningfulName = Boolean(fullName && fullName.toLowerCase() !== emailLocal);
            const hasJobTitle = Boolean(getTextValue(profile.job_title));
            const hasUseCase = Boolean(getTextValue(profile.use_case));
            const organization = typeof profile.organization === 'object' && profile.organization ? profile.organization : null;
            const hasOrgName = Boolean(getTextValue(organization?.name));
            const hasOrgSize = Boolean(getTextValue(organization?.size_label));
            const hasOrgUseCase = Boolean(getTextValue(organization?.primary_use_case));
            return hasMeaningfulName || hasJobTitle || hasUseCase || hasOrgName || hasOrgSize || hasOrgUseCase;
        };

        const applyOrgDisabledState = () => {
            if (!noCompanyInput) {
                return;
            }
            const shouldDisable = noCompanyInput.checked;
            orgFields.forEach((field) => {
                field.disabled = shouldDisable;
                const wrapper = field.closest('.auth-confirm__field');
                if (wrapper) {
                    wrapper.classList.toggle('is-disabled', shouldDisable);
                }
                if (shouldDisable) {
                    field.setAttribute('aria-disabled', 'true');
                } else {
                    field.removeAttribute('aria-disabled');
                }
            });
        };

        const buildProfilePayload = () => {
            if (!profileForm) {
                return {};
            }
            const formData = new FormData(profileForm);
            const payload = {};
            for (const [key, value] of formData.entries()) {
                if (key === 'no_company') {
                    payload.no_company = true;
                    continue;
                }
                if (typeof value === 'string') {
                    payload[key] = value.trim();
                }
            }
            if (!Object.prototype.hasOwnProperty.call(payload, 'no_company')) {
                payload.no_company = false;
            }
            return payload;
        };

        const handleConfirm = async () => {
            if (!dataset.confirmEndpoint) {
                setErrorState('Confirm endpoint unavailable.');
                return;
            }
            const token = dataset.confirmToken || '';
            if (!token) {
                setErrorState('Confirmation token missing.');
                return;
            }
            setState('loading');
            try {
                const response = await fetch(dataset.confirmEndpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    credentials: 'include',
                    body: JSON.stringify({ token }),
                });
                const payload = await readJson(response);
                if (!response.ok) {
                    const detail = typeof payload?.detail === 'string' ? payload.detail : '';
                    if (detail === 'token_expired') {
                        setErrorState('This link has expired. Request a new confirmation email.');
                        return;
                    }
                    setErrorState('This link is invalid. Request a new confirmation email.');
                    return;
                }
                accessToken = typeof payload?.access_token === 'string' ? payload.access_token : '';
                const profile = typeof payload?.profile === 'object' && payload.profile ? payload.profile : null;
                const email = profile && typeof profile.email === 'string' ? profile.email : '';
                if (confirmEmailNode && email) {
                    confirmEmailNode.textContent = email;
                }
                if (profileForm) {
                    if (isProfileComplete(profile)) {
                        window.location.assign(resolveRedirectUrl());
                        return;
                    }
                    setState('profile');
                } else {
                    setState('success');
                }
            } catch (_) {
                setErrorState('Unable to verify the link right now.');
            }
        };

        const handleProfileSubmit = async (event) => {
            event.preventDefault();
            if (!dataset.profileEndpoint) {
                setStatus(profileStatus, 'error', 'Profile endpoint unavailable.');
                return;
            }
            if (!accessToken) {
                setStatus(profileStatus, 'error', 'Session expired. Reopen the confirmation link.');
                return;
            }
            setStatus(profileStatus, 'info', 'Saving...');
            setButtonBusy(profileSubmit, true);
            try {
                const response = await fetch(dataset.profileEndpoint, {
                    method: 'PATCH',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                        Authorization: `Bearer ${accessToken}`,
                    },
                    body: JSON.stringify(buildProfilePayload()),
                });
                const payload = await readJson(response);
                if (!response.ok) {
                    const message =
                        typeof payload?.message === 'string' ? payload.message : 'Unable to update profile.';
                    throw new Error(message);
                }
                const email = typeof payload?.email === 'string' ? payload.email : '';
                if (confirmEmailNode && email) {
                    confirmEmailNode.textContent = email;
                }
                setStatus(profileStatus, 'success', 'Profile saved.');
                setState('success');
            } catch (error) {
                const fallback =
                    error instanceof Error && typeof error.message === 'string'
                        ? error.message
                        : 'Unable to update profile.';
                setStatus(profileStatus, 'error', fallback);
            } finally {
                setButtonBusy(profileSubmit, false);
            }
        };

        const handleSkip = () => {
            setState('success');
        };

        const handleResend = async (event) => {
            event.preventDefault();
            if (!dataset.resendEndpoint) {
                setStatus(resendStatus, 'error', 'Resend endpoint unavailable.');
                return;
            }
            if (!resendEmailInput) {
                setStatus(resendStatus, 'error', 'Email input unavailable.');
                return;
            }
            if (typeof resendForm?.reportValidity === 'function' && !resendForm.reportValidity()) {
                return;
            }
            const email = String(resendEmailInput.value || '').trim().toLowerCase();
            if (!email) {
                setStatus(resendStatus, 'error', 'Enter your email to resend the link.');
                return;
            }
            setStatus(resendStatus, 'info', 'Resending confirmation email...');
            const resendButton = resendForm?.querySelector('button[type="submit"]');
            setButtonBusy(resendButton, true);
            try {
                const response = await fetch(dataset.resendEndpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ email }),
                });
                const payload = await readJson(response);
                if (!response.ok) {
                    const message =
                        typeof payload?.message === 'string'
                            ? payload.message
                            : 'Unable to resend confirmation right now.';
                    throw new Error(message);
                }
                setStatus(resendStatus, 'success', 'If the email is registered, a new link is on the way.');
            } catch (error) {
                const fallback =
                    error instanceof Error && typeof error.message === 'string'
                        ? error.message
                        : 'Unable to resend confirmation right now.';
                setStatus(resendStatus, 'error', fallback);
            } finally {
                setButtonBusy(resendForm?.querySelector('button[type="submit"]'), false);
            }
        };

        applyOrgDisabledState();
        noCompanyInput?.addEventListener('change', applyOrgDisabledState);
        profileForm?.addEventListener('submit', handleProfileSubmit);
        skipButton?.addEventListener('click', handleSkip);
        resendForm?.addEventListener('submit', handleResend);

        handleConfirm();
    };

    const copyTextToClipboard = async (value) => {
        if (!value) {
            return false;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(value);
            return true;
        }
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'absolute';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        const succeeded = document.execCommand('copy');
        document.body.removeChild(textarea);
        return succeeded;
    };

    const initDocsCopyButtons = (root) => {
        const copyButtons = root.querySelectorAll('[data-copy-value], [data-copy-code], [data-copy-anchor]');
        if (!copyButtons.length) {
            return;
        }
        const nativeInteractiveTags = new Set(['button', 'a', 'input', 'textarea', 'select', 'summary']);

        const feedback = root.querySelector('[data-copy-feedback]');
        let resetTimer = null;

        const setFeedback = (message) => {
            if (!feedback) {
                return;
            }
            feedback.textContent = message;
            window.clearTimeout(resetTimer);
            resetTimer = window.setTimeout(() => {
                feedback.textContent = '';
            }, 1800);
        };

        const readCodeValue = (button) => {
            const codeRoot = button.closest('[data-docs-code]');
            const codeElement = codeRoot ? codeRoot.querySelector('code') : null;
            if (!codeElement) {
                return '';
            }
            return codeElement.innerText.trim();
        };

        const buildAnchorValue = (anchorId) => {
            if (!anchorId) {
                return '';
            }
            const normalized = anchorId.startsWith('#') ? anchorId.slice(1) : anchorId;
            const { origin, pathname, hostname } = window.location;
            const productionOrigin = 'https://aici.pro';
            const isLocalHost = hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '0.0.0.0';
            const resolvedOrigin = isLocalHost ? productionOrigin : origin;
            return `${resolvedOrigin}${pathname}#${normalized}`;
        };

        const handleCopy = async (event) => {
            event.preventDefault();
            const target = event.currentTarget;
            if (!target) {
                return;
            }
            let value = target.getAttribute('data-copy-value') || '';

            if (!value && target.hasAttribute('data-copy-anchor')) {
                value = buildAnchorValue(target.getAttribute('data-copy-anchor'));
            }

            if (!value && target.hasAttribute('data-copy-code')) {
                value = readCodeValue(target);
            }

            if (!value) {
                return;
            }

            const ok = await copyTextToClipboard(value);
            if (ok) {
                target.classList.add('is-copied');
                window.setTimeout(() => target.classList.remove('is-copied'), 1200);
                setFeedback('Скопировано');
            }
        };

        copyButtons.forEach((button) => {
            const tagName = button.tagName ? button.tagName.toLowerCase() : '';
            const isNativeInteractive = nativeInteractiveTags.has(tagName);
            if (!isNativeInteractive && !button.hasAttribute('tabindex')) {
                button.setAttribute('tabindex', '0');
            }
            if (!isNativeInteractive && !button.hasAttribute('role')) {
                button.setAttribute('role', 'button');
            }
            button.addEventListener('click', handleCopy);
            button.addEventListener('keydown', (event) => {
                const key = event.key;
                if (key === 'Enter' || key === ' ') {
                    event.preventDefault();
                    handleCopy(event);
                }
            });
        });
    };

    const initDocsNavigation = (sections) => {
        if (!sections.length) {
            return;
        }

        const sidebarLinks = new Map();
        document.querySelectorAll('[data-docs-link]').forEach((link) => {
            const target = link.getAttribute('data-target') || link.getAttribute('href') || '';
            const normalized = target.replace('#', '');
            sidebarLinks.set(normalized, link);
        });
        const sidebarSubnav = new Map();
        document.querySelectorAll('[data-docs-subnav]').forEach((node) => {
            const sectionId = node.getAttribute('data-docs-subnav') || '';
            if (!sectionId) {
                return;
            }
            sidebarSubnav.set(sectionId, node);
        });
        const sectionSubLinks = new Map();
        document.querySelectorAll('[data-docs-sub-link]').forEach((link) => {
            const parent = link.getAttribute('data-parent') || '';
            if (!parent) {
                return;
            }
            const href = link.getAttribute('href') || '';
            const anchorId = href.replace('#', '');
            if (!anchorId) {
                return;
            }
            link.dataset.docsAnchorId = anchorId;
            if (!sectionSubLinks.has(parent)) {
                sectionSubLinks.set(parent, []);
            }
            sectionSubLinks.get(parent).push(link);
        });

        const prevLink = document.querySelector('[data-docs-prev]');
        const nextLink = document.querySelector('[data-docs-next]');
        const topbar = document.querySelector('.docs-topbar');
        const sidebar = document.querySelector('.docs-sidebar');
        const sidebarNav = document.querySelector('[data-docs-sidebar-nav]');
        const sidebarToggle = document.querySelector('[data-docs-nav-toggle]');
        const sidebarCurrentLabel = document.querySelector('[data-docs-nav-current]');
        const mobileSidebarQuery = window.matchMedia('(max-width: 1080px)');
        let isMobileSidebarOpen = false;

        const getOffset = () => {
            if (!topbar) {
                return 88;
            }
            const rect = topbar.getBoundingClientRect();
            const height = Number.isFinite(rect.height) ? rect.height : 0;
            return Math.max(72, height + 16);
        };

        const scrollToElement = (element, behavior = 'smooth') => {
            if (!element) {
                return;
            }
            const rect = element.getBoundingClientRect();
            const current = window.pageYOffset || window.scrollY || 0;
            const targetTop = rect.top + current - getOffset();
            window.scrollTo({
                top: targetTop < 0 ? 0 : targetTop,
                behavior,
            });
        };

        const resolveSectionId = (hash) => {
            if (!hash) {
                return '';
            }
            if (sections.some((section) => section.id === hash)) {
                return hash;
            }
            const matched = sections.find((section) => hash.startsWith(`${section.id}-`));
            return matched ? matched.id : '';
        };
        const resolveAnchorId = (hash, sectionId) => {
            if (!hash || !sectionId || hash === sectionId) {
                return '';
            }
            if (!hash.startsWith(`${sectionId}-`)) {
                return '';
            }
            return hash;
        };

        const setPaginationLink = (link, section) => {
            if (!link) {
                return;
            }
            if (!section) {
                link.classList.add('is-disabled');
                link.setAttribute('aria-disabled', 'true');
                link.setAttribute('tabindex', '-1');
                link.textContent = '';
                return;
            }
            link.classList.remove('is-disabled');
            link.removeAttribute('aria-disabled');
            link.removeAttribute('tabindex');
            link.setAttribute('href', `#${section.id}`);
            link.textContent = section.label;
        };

        const updatePagination = (activeId) => {
            const currentIndex = sections.findIndex((section) => section.id === activeId);
            if (currentIndex === -1) {
                return;
            }
            setPaginationLink(prevLink, sections[currentIndex - 1]);
            setPaginationLink(nextLink, sections[currentIndex + 1]);
        };

        const getSectionLabel = (sectionId) => {
            const section = sections.find((candidate) => candidate.id === sectionId);
            return section ? section.label : sectionId;
        };

        const updateSidebarCurrentLabel = (sectionId) => {
            if (!sidebarCurrentLabel || !sectionId) {
                return;
            }
            sidebarCurrentLabel.textContent = getSectionLabel(sectionId);
        };

        const setMobileSidebarState = (nextOpenState) => {
            if (!sidebar || !sidebarNav || !sidebarToggle) {
                return;
            }
            if (!mobileSidebarQuery.matches) {
                sidebar.classList.remove('is-mobile-open');
                sidebarNav.hidden = false;
                sidebarToggle.setAttribute('aria-expanded', 'true');
                isMobileSidebarOpen = false;
                return;
            }
            isMobileSidebarOpen = Boolean(nextOpenState);
            sidebar.classList.toggle('is-mobile-open', isMobileSidebarOpen);
            sidebarNav.hidden = !isMobileSidebarOpen;
            sidebarToggle.setAttribute('aria-expanded', isMobileSidebarOpen ? 'true' : 'false');
        };

        const closeMobileSidebar = () => {
            if (!mobileSidebarQuery.matches) {
                return;
            }
            setMobileSidebarState(false);
        };

        const syncSidebarViewportState = () => {
            if (mobileSidebarQuery.matches) {
                setMobileSidebarState(false);
                return;
            }
            setMobileSidebarState(true);
        };

        const updateSubnavVisibility = (activeSectionId) => {
            sidebarSubnav.forEach((subnav, sectionId) => {
                const isOpen = sectionId === activeSectionId;
                subnav.classList.toggle('is-open', isOpen);
                subnav.hidden = !isOpen;
            });
        };

        const updateSubLinkState = (activeSectionId, preferredAnchorId = '') => {
            sectionSubLinks.forEach((links, sectionId) => {
                const fallbackAnchorId = links.length ? links[0].dataset.docsAnchorId || '' : '';
                const hasPreferredAnchor = links.some((link) => link.dataset.docsAnchorId === preferredAnchorId);
                const resolvedAnchorId =
                    sectionId === activeSectionId
                        ? hasPreferredAnchor
                            ? preferredAnchorId
                            : fallbackAnchorId
                        : '';
                links.forEach((link) => {
                    const isCurrent = sectionId === activeSectionId && link.dataset.docsAnchorId === resolvedAnchorId;
                    link.classList.toggle('is-active', isCurrent);
                    if (isCurrent) {
                        link.setAttribute('aria-current', 'true');
                    } else {
                        link.removeAttribute('aria-current');
                    }
                });
            });
        };

        const resolveScrollAnchorId = (activeSectionId) => {
            if (!activeSectionId) {
                return '';
            }
            const links = sectionSubLinks.get(activeSectionId) || [];
            if (!links.length) {
                return '';
            }
            const scrollTop = window.pageYOffset || window.scrollY || 0;
            const probeTop = scrollTop + getOffset() + 24;
            let resolvedAnchorId = links[0].dataset.docsAnchorId || '';
            links.forEach((link) => {
                const anchorId = link.dataset.docsAnchorId || '';
                if (!anchorId) {
                    return;
                }
                const anchor = document.getElementById(anchorId);
                if (!anchor) {
                    return;
                }
                const anchorTop = anchor.getBoundingClientRect().top + scrollTop;
                if (anchorTop <= probeTop) {
                    resolvedAnchorId = anchorId;
                }
            });
            return resolvedAnchorId;
        };

        let activeId = null;
        const setActive = (id, options = {}) => {
            const {
                shouldUpdateHash = true,
                hash = '',
                anchorId = '',
                shouldScroll = false,
                scrollBehavior = 'smooth',
            } = options;
            const targetSection = sections.find((section) => section.id === id) || sections[0];
            if (!targetSection) {
                return;
            }
            const hasSectionChanged = activeId !== targetSection.id;
            activeId = targetSection.id;
            if (hasSectionChanged) {
                sections.forEach((section) => {
                    const isCurrent = section.id === activeId;
                    section.element.hidden = !isCurrent;
                    section.element.classList.toggle('is-active', isCurrent);
                });
                updatePagination(activeId);
            }
            sidebarLinks.forEach((link, target) => {
                link.classList.toggle('is-active', target === activeId);
            });
            updateSubnavVisibility(activeId);
            updateSubLinkState(activeId, anchorId);
            updateSidebarCurrentLabel(activeId);
            if (shouldUpdateHash && window.history && typeof window.history.replaceState === 'function') {
                const nextHash = hash || `#${anchorId || activeId}`;
                window.history.replaceState(null, '', nextHash);
            }
            if (shouldScroll) {
                const scrollTargetId = anchorId || activeId;
                const scrollTarget =
                    document.getElementById(scrollTargetId) ||
                    (scrollTargetId === activeId ? targetSection.element : null) ||
                    targetSection.element;
                scrollToElement(scrollTarget, scrollBehavior);
            }
            requestScrollSubLinkSync();
        };

        let scrollSubLinkFrameId = null;
        const syncScrollSubLinkState = () => {
            scrollSubLinkFrameId = null;
            if (!activeId) {
                return;
            }
            const visibleAnchorId = resolveScrollAnchorId(activeId);
            if (!visibleAnchorId) {
                return;
            }
            updateSubLinkState(activeId, visibleAnchorId);
        };
        const requestScrollSubLinkSync = () => {
            if (scrollSubLinkFrameId !== null) {
                return;
            }
            scrollSubLinkFrameId = window.requestAnimationFrame(syncScrollSubLinkState);
        };

        sections.forEach((section) => {
            section.element.hidden = true;
        });

        const initialHash = window.location.hash.replace('#', '');
        const initialSectionId = resolveSectionId(initialHash) || sections[0].id;
        const initialAnchorId = resolveAnchorId(initialHash, initialSectionId);
        setActive(initialSectionId, {
            shouldUpdateHash: false,
            anchorId: initialAnchorId,
            shouldScroll: Boolean(initialHash),
            scrollBehavior: 'auto',
        });

        if (sidebarToggle && sidebarNav) {
            sidebarToggle.addEventListener('click', () => {
                if (!mobileSidebarQuery.matches) {
                    return;
                }
                setMobileSidebarState(!isMobileSidebarOpen);
            });
            sidebarToggle.addEventListener('keydown', (event) => {
                if (!mobileSidebarQuery.matches) {
                    return;
                }
                if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') {
                    return;
                }
                event.preventDefault();
                setMobileSidebarState(!isMobileSidebarOpen);
            });
        }

        if (sidebar) {
            sidebar.addEventListener('keydown', (event) => {
                if (!mobileSidebarQuery.matches || !isMobileSidebarOpen || event.key !== 'Escape') {
                    return;
                }
                event.preventDefault();
                closeMobileSidebar();
                if (sidebarToggle) {
                    sidebarToggle.focus();
                }
            });
        }

        if (mobileSidebarQuery.addEventListener) {
            mobileSidebarQuery.addEventListener('change', syncSidebarViewportState);
        } else if (mobileSidebarQuery.addListener) {
            mobileSidebarQuery.addListener(syncSidebarViewportState);
        }
        syncSidebarViewportState();

        sidebarLinks.forEach((link, target) => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                setActive(target, {
                    hash: `#${target}`,
                    anchorId: '',
                    shouldScroll: true,
                    scrollBehavior: 'smooth',
                });
                closeMobileSidebar();
            });
        });
        sectionSubLinks.forEach((links, sectionId) => {
            links.forEach((link) => {
                link.addEventListener('click', (event) => {
                    event.preventDefault();
                    const anchorId = link.dataset.docsAnchorId || '';
                    if (!anchorId) {
                        return;
                    }
                    setActive(sectionId, {
                        hash: `#${anchorId}`,
                        anchorId,
                        shouldScroll: true,
                        scrollBehavior: 'smooth',
                    });
                    closeMobileSidebar();
                });
            });
        });

        window.addEventListener('hashchange', () => {
            const newHash = window.location.hash.replace('#', '');
            if (!newHash) {
                return;
            }
            const nextSectionId = resolveSectionId(newHash);
            if (!nextSectionId) {
                return;
            }
            setActive(nextSectionId, {
                shouldUpdateHash: false,
                anchorId: resolveAnchorId(newHash, nextSectionId),
                shouldScroll: true,
                scrollBehavior: 'auto',
            });
        });
        window.addEventListener('scroll', requestScrollSubLinkSync, { passive: true });
        window.addEventListener('resize', requestScrollSubLinkSync);
    };

    const initDocsEndpoints = (root) => {
        const endpointsRoot = root.querySelector('[data-docs-endpoints]');
        const preview = root.querySelector('[data-endpoint-preview]');
        if (!endpointsRoot || !preview) {
            return;
        }

        const endpoints = Array.from(endpointsRoot.querySelectorAll('[data-method]'));
        if (!endpoints.length) {
            return;
        }

        const baseUrl = preview.getAttribute('data-base-url') || '';
        const pathElement = preview.querySelector('[data-endpoint-path]');
        const sampleElement = preview.querySelector('[data-endpoint-sample]');
        const methodElement = preview.querySelector('[data-endpoint-method]');

        const updatePreview = (endpoint) => {
            endpoints.forEach((node) => node.classList.toggle('is-active', node === endpoint));
            const method = endpoint.getAttribute('data-method') || 'GET';
            const path = endpoint.getAttribute('data-path') || '';
            const fullPath = `${baseUrl}${path}`;
            if (methodElement) {
                methodElement.textContent = method;
            }
            if (pathElement) {
                pathElement.textContent = fullPath;
                pathElement.setAttribute('data-copy-value', fullPath);
            }
            if (sampleElement) {
                sampleElement.textContent = `curl -X ${method} "${fullPath}" \\\n  -H "X-API-Key: YOUR_API_KEY" \\\n  -H "Accept: application/json"`;
            }
        };

        endpoints.forEach((endpoint) => {
            endpoint.addEventListener('click', () => updatePreview(endpoint));
        });

        const defaultEndpoint = endpoints.find((node) => node.classList.contains('is-active')) || endpoints[0];
        updatePreview(defaultEndpoint);
    };

    const initDocsToc = () => {
        const tocBlocks = Array.from(document.querySelectorAll('[data-docs-toc]'));
        if (!tocBlocks.length) {
            return;
        }

        const topbar = document.querySelector('.docs-topbar');
        const getOffset = () => {
            if (!topbar) {
                return 88;
            }
            const rect = topbar.getBoundingClientRect();
            const height = Number.isFinite(rect.height) ? rect.height : 0;
            return Math.max(72, height + 16);
        };

        const scrollToAnchor = (anchor, behavior = 'smooth') => {
            if (!anchor) {
                return;
            }
            const rect = anchor.getBoundingClientRect();
            const current = window.pageYOffset || window.scrollY || 0;
            const targetTop = rect.top + current - getOffset();
            window.scrollTo({
                top: targetTop < 0 ? 0 : targetTop,
                behavior,
            });
        };

        const updateTocState = (toc, targetId) => {
            const buttons = toc.querySelectorAll('[data-toc-target]');
            buttons.forEach((button) => {
                const isCurrent = button.getAttribute('data-toc-target') === targetId;
                button.classList.toggle('is-active', isCurrent);
                if (isCurrent) {
                    button.setAttribute('aria-current', 'true');
                } else {
                    button.removeAttribute('aria-current');
                }
            });
        };

        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    const anchorId = entry.target.id;
                    if (!anchorId) {
                        return;
                    }
                    const toc = entry.target.closest('[data-docs-section]')?.querySelector('[data-docs-toc]');
                    if (!toc) {
                        return;
                    }
                    if (entry.isIntersecting) {
                        updateTocState(toc, anchorId);
                    }
                });
            },
            {
                rootMargin: '-45% 0px -40% 0px',
            }
        );

        tocBlocks.forEach((toc) => {
            const section = toc.closest('[data-docs-section]');
            const buttons = Array.from(toc.querySelectorAll('[data-toc-target]'));
            buttons.forEach((button) => {
                const targetId = button.getAttribute('data-toc-target');
                if (!targetId || !section) {
                    return;
                }
                const anchor = section.querySelector(`#${targetId}`);
                if (!anchor) {
                    return;
                }
                observer.observe(anchor);

                const handleClick = (event) => {
                    event.preventDefault();
                    updateTocState(toc, targetId);
                    scrollToAnchor(anchor, 'smooth');
                    if (window.history && typeof window.history.replaceState === 'function') {
                        window.history.replaceState(null, '', `#${anchor.id}`);
                    }
                };

                button.addEventListener('click', handleClick);
                button.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        handleClick(event);
                    }
                });
            });
        });

        const handleInitialHash = () => {
            const hash = window.location.hash.replace('#', '');
            if (!hash) {
                return;
            }
            const target = document.getElementById(hash);
            if (!target) {
                return;
            }
            scrollToAnchor(target, 'auto');
            const toc = target.closest('[data-docs-section]')?.querySelector('[data-docs-toc]');
            if (toc) {
                updateTocState(toc, hash);
            }
        };

        window.requestAnimationFrame(handleInitialHash);
        window.addEventListener('hashchange', handleInitialHash);
    };

    const initDocsPage = () => {
        const docsRoot = document.querySelector('.docs-page');
        if (!docsRoot) {
            return;
        }
        const sectionNodes = Array.from(docsRoot.querySelectorAll('[data-docs-section]'));
        if (!sectionNodes.length) {
            return;
        }
        const sections = sectionNodes.map((section) => ({
            id: section.id,
            label:
                section.getAttribute('data-docs-label') ||
                (section.querySelector('h2') ? section.querySelector('h2').textContent.trim() : section.id),
            element: section,
        }));

        initDocsCopyButtons(docsRoot);
        initDocsNavigation(sections);
        initDocsToc();
        initDocsEndpoints(docsRoot);
    };

    const initPlansCarousels = () => {
        const carousels = Array.from(document.querySelectorAll('[data-plans-carousel]'));
        if (!carousels.length) {
            return;
        }

        carousels.forEach((carousel) => {
            const viewport = carousel.querySelector('[data-carousel-viewport]');
            const track = carousel.querySelector('[data-carousel-track]');
            const prevButton = carousel.querySelector('[data-carousel-prev]');
            const nextButton = carousel.querySelector('[data-carousel-next]');
            if (!viewport || !track) {
                return;
            }

            const parsedBreakpoint = Number.parseInt(carousel.dataset.carouselMobileBreakpoint || '', 10);
            const mobileBreakpoint = Number.isFinite(parsedBreakpoint) && parsedBreakpoint > 0 ? parsedBreakpoint : 900;
            let carouselEnabled = false;

            const isCompactViewport = () => window.matchMedia(`(max-width: ${mobileBreakpoint}px)`).matches;

            const getCards = () => Array.from(track.querySelectorAll('.billing-plan'));
            const getVisibleCards = () => getCards().filter((card) => card && card.offsetParent !== null);

            const updateNav = () => {
                if (!carouselEnabled) {
                    return;
                }
                const maxScroll = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
                const atStart = viewport.scrollLeft <= 8;
                const atEnd = viewport.scrollLeft >= maxScroll - 8;
                if (prevButton) {
                    prevButton.disabled = atStart;
                }
                if (nextButton) {
                    nextButton.disabled = atEnd;
                }
            };

            const setCarouselState = (isEnabled) => {
                carouselEnabled = Boolean(isEnabled);
                carousel.dataset.carouselEnabled = carouselEnabled ? 'true' : 'false';
                track.dataset.carouselActive = carouselEnabled ? 'true' : 'false';
                if (!carouselEnabled) {
                    viewport.scrollTo({ left: 0, behavior: 'auto' });
                    if (prevButton) {
                        prevButton.disabled = true;
                    }
                    if (nextButton) {
                        nextButton.disabled = true;
                    }
                    return;
                }
                updateNav();
            };

            const getSlideStep = () => {
                const cards = getVisibleCards();
                if (!cards.length) {
                    return 0;
                }
                const gap = Number.parseFloat(window.getComputedStyle(track).gap || '0') || 0;
                return cards[0].getBoundingClientRect().width + gap;
            };

            const slideCarousel = (direction) => {
                if (!carouselEnabled) {
                    return;
                }
                const step = getSlideStep();
                if (!step) {
                    return;
                }
                viewport.scrollBy({ left: direction === 'next' ? step : -step, behavior: 'smooth' });
            };

            const refreshCarousel = () => {
                const visibleCards = getVisibleCards().length;
                const shouldEnable = visibleCards > 1 && (isCompactViewport() || visibleCards > 3);
                setCarouselState(shouldEnable);
                if (carouselEnabled) {
                    window.requestAnimationFrame(updateNav);
                }
            };

            viewport.addEventListener('scroll', () => {
                if (!carouselEnabled) {
                    return;
                }
                updateNav();
            }, { passive: true });
            prevButton?.addEventListener('click', () => slideCarousel('prev'));
            nextButton?.addEventListener('click', () => slideCarousel('next'));
            window.addEventListener('resize', refreshCarousel);
            refreshCarousel();
        });
    };

    const initCookieConsent = () => {
        if (window.AICICookieConsent?.init) {
            window.AICICookieConsent.init();
        }
    };

    const initHeaderHeightVariable = () => {
        const header = document.querySelector('.landing-header, .pricing-topbar, .docs-topbar');
        if (!header) {
            return;
        }

        const root = document.documentElement;
        let pendingFrame = null;

        const applyHeight = () => {
            pendingFrame = null;
            const headerBox = header.getBoundingClientRect();
            const nextValue = `${Math.round(headerBox.height)}px`;
            if (!headerBox.height) {
                return;
            }
            if (root.style.getPropertyValue('--landing-header-height') === nextValue) {
                return;
            }
            root.style.setProperty('--landing-header-height', nextValue);
        };

        const requestHeightUpdate = () => {
            if (pendingFrame !== null) {
                return;
            }
            pendingFrame = window.requestAnimationFrame(applyHeight);
        };

        requestHeightUpdate();

        if (typeof window.ResizeObserver !== 'undefined') {
            const observer = new window.ResizeObserver(requestHeightUpdate);
            observer.observe(header);
            header[headerHeightObserverKey] = observer;
        } else {
            window.addEventListener('resize', requestHeightUpdate);
        }

        window.addEventListener('orientationchange', requestHeightUpdate);
        window.addEventListener('load', requestHeightUpdate);
    };

    const initLandingInteractions = () => {
        initHeaderHeightVariable();
        initFaqAccordion();
        initMobileHeaders();
        initPlansCarousels();
        initSmoothScrollAnchors();
        initModalSystem();
        initRegistrationFlow();
        initPerformanceSwitcher();
        initPerformanceComposition();
        initCtaTracking();
        initIntakeForms();
        initLoginFlow();
        initConfirmFlow();
        initDocsPage();
        initCookieConsent();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initLandingInteractions);
    } else {
        initLandingInteractions();
    }
})();

