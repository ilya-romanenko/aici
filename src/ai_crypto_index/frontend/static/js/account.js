(() => {
    const body = document.body;
    const BILLING_TOKEN_KEY = 'aiciAccountToken';
    const PLAYGROUND_STORAGE_KEY = 'aici:playground:key';
    const PLAYGROUND_PERSISTED_KEY = 'aici:playground:key:persisted';
    const NOTIFICATION_STORAGE_KEY = 'aici:notifications:read';
    const SIDEBAR_STORAGE_KEY = 'aici:sidebar:open';
    const DESKTOP_BREAKPOINT = 1024;
    const FOCUSABLE_SIDEBAR_SELECTOR = 'a[href], area[href], button, input, select, textarea, [tabindex]';
    const CTA_SESSION_STORAGE_KEY = 'aici_cta_session_id';
    const CTA_UTM_STORAGE_KEY = 'aici_cta_utm_snapshot';
    const CTA_UTM_FIELDS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];
    let modalClosingTimeout = null;
    let modalScrollbarWidth = null;

    const measureScrollbarWidth = () => Math.max(0, window.innerWidth - document.documentElement.clientWidth);

    const syncScrollbarCompensation = () => {
        if (!body) {
            return;
        }
        const shouldCompensate =
            body.classList.contains('is-modal-open') || body.classList.contains('is-sidebar-scroll-locked');
        if (!shouldCompensate) {
            body.style.removeProperty('--account-scrollbar-width');
            return;
        }
        const isModalOpen = body.classList.contains('is-modal-open');
        const scrollbarWidth = isModalOpen && modalScrollbarWidth !== null
            ? modalScrollbarWidth
            : measureScrollbarWidth();
        if (scrollbarWidth > 0) {
            body.style.setProperty('--account-scrollbar-width', `${scrollbarWidth}px`);
            return;
        }
        body.style.removeProperty('--account-scrollbar-width');
    };

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
            const raw = window.sessionStorage ? window.sessionStorage.getItem(CTA_UTM_STORAGE_KEY) : null;
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
            // ignore storage access errors
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
                if (window.sessionStorage) {
                    window.sessionStorage.setItem(CTA_UTM_STORAGE_KEY, JSON.stringify(snapshot));
                }
            } catch (error) {
                // ignore storage access errors
            }
        }

        return snapshot;
    };

    const readCtaUtm = (snapshot, field) => {
        const value = snapshot && typeof snapshot[field] === 'string' ? snapshot[field].trim() : '';
        return value || null;
    };

    const getCtaSessionId = () => {
        try {
            if (!window.sessionStorage) {
                return null;
            }
            const existing = window.sessionStorage.getItem(CTA_SESSION_STORAGE_KEY);
            if (existing) {
                return existing;
            }
            const generated =
                typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
                    ? crypto.randomUUID()
                    : `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
            window.sessionStorage.setItem(CTA_SESSION_STORAGE_KEY, generated);
            return generated;
        } catch (error) {
            return null;
        }
    };

    const isFocusableNode = (node) => {
        if (!(node instanceof HTMLElement)) {
            return false;
        }
        if (node.hasAttribute('disabled')) {
            return false;
        }
        if (node.getAttribute('aria-hidden') === 'true') {
            return false;
        }
        return node.getAttribute('tabindex') !== '-1';
    };

    const setModalScrollLock = (isLocked) => {
        if (!body) {
            return;
        }
        if (isLocked) {
            modalScrollbarWidth = measureScrollbarWidth();
        }
        body.classList.toggle('is-modal-open', isLocked);
        if (isLocked) {
            body.classList.remove('is-modal-closing');
            if (modalClosingTimeout) {
                window.clearTimeout(modalClosingTimeout);
                modalClosingTimeout = null;
            }
        } else {
            body.classList.add('is-modal-closing');
            if (modalClosingTimeout) {
                window.clearTimeout(modalClosingTimeout);
            }
            modalClosingTimeout = window.setTimeout(() => {
                body.classList.remove('is-modal-closing');
                modalClosingTimeout = null;
            }, 200);
            modalScrollbarWidth = null;
        }
        syncScrollbarCompensation();
    };

    const initAccountSelects = (root) => {
        if (!root) {
            return null;
        }
        const selects = Array.from(root.querySelectorAll('select'));
        if (!selects.length) {
            return null;
        }

        let openItem = null;
        let selectCounter = 0;

        const buildItem = (select) => {
            if (select.dataset.accountSelectReady === 'true') {
                return null;
            }
            select.dataset.accountSelectReady = 'true';

            const wrapper = document.createElement('div');
            wrapper.className = 'account-select';
            wrapper.dataset.accountSelect = 'true';

            const trigger = document.createElement('button');
            trigger.type = 'button';
            trigger.className = 'account-select__trigger';
            trigger.setAttribute('aria-haspopup', 'listbox');
            trigger.setAttribute('aria-expanded', 'false');

            const valueNode = document.createElement('span');
            valueNode.className = 'account-select__value';
            trigger.appendChild(valueNode);

            const list = document.createElement('ul');
            list.className = 'account-select__list';
            list.setAttribute('role', 'listbox');
            list.tabIndex = -1;
            list.hidden = true;
            const listId = `account-select-${selectCounter++}`;
            list.id = listId;
            trigger.setAttribute('aria-controls', listId);

            const labelText = select.closest('label')?.querySelector('span')?.textContent?.trim();
            if (labelText) {
                trigger.setAttribute('aria-label', labelText);
                list.setAttribute('aria-label', labelText);
            }

            select.classList.add('account-select__native');
            select.setAttribute('aria-hidden', 'true');
            select.tabIndex = -1;

            const parent = select.parentNode;
            if (!parent) {
                return null;
            }
            parent.insertBefore(wrapper, select);
            wrapper.appendChild(select);
            wrapper.appendChild(trigger);
            wrapper.appendChild(list);

            const getOptionNodes = () => Array.from(list.querySelectorAll('.account-select__option'));

            const setActiveIndex = (index) => {
                const optionNodes = getOptionNodes();
                const safeIndex = Math.max(0, Math.min(index, optionNodes.length - 1));
                optionNodes.forEach((node, nodeIndex) => {
                    const isActive = nodeIndex === safeIndex;
                    node.classList.toggle('is-active', isActive);
                    if (isActive) {
                        list.setAttribute('aria-activedescendant', node.id);
                    }
                });
                list.dataset.activeIndex = String(safeIndex);
            };

            const getNextEnabledIndex = (startIndex, step) => {
                const optionNodes = getOptionNodes();
                if (!optionNodes.length) {
                    return -1;
                }
                let nextIndex = startIndex;
                for (let i = 0; i < optionNodes.length; i += 1) {
                    nextIndex = (nextIndex + step + optionNodes.length) % optionNodes.length;
                    if (!optionNodes[nextIndex].classList.contains('is-disabled')) {
                        return nextIndex;
                    }
                }
                return startIndex;
            };

            const buildOptions = () => {
                list.innerHTML = '';
                const options = Array.from(select.options);
                options.forEach((option, index) => {
                    const item = document.createElement('li');
                    item.className = 'account-select__option';
                    item.setAttribute('role', 'option');
                    item.dataset.index = String(index);
                    item.dataset.value = option.value;
                    item.id = `${listId}-option-${index}`;
                    item.textContent = option.textContent;
                    if (option.disabled) {
                        item.classList.add('is-disabled');
                        item.setAttribute('aria-disabled', 'true');
                    }
                    list.appendChild(item);
                });
            };

            const syncFromSelect = () => {
                const options = Array.from(select.options);
                const selectedIndex = select.selectedIndex >= 0 ? select.selectedIndex : 0;
                const selectedOption = options[selectedIndex];
                valueNode.textContent = selectedOption ? selectedOption.textContent : '';

                const optionNodes = getOptionNodes();
                optionNodes.forEach((node) => {
                    const nodeIndex = Number(node.dataset.index);
                    const isSelected = nodeIndex === selectedIndex;
                    node.classList.toggle('is-selected', isSelected);
                    node.setAttribute('aria-selected', isSelected ? 'true' : 'false');
                });

                trigger.disabled = select.disabled;
                wrapper.classList.toggle('is-disabled', select.disabled);
                trigger.setAttribute('aria-disabled', select.disabled ? 'true' : 'false');
            };

            const setOpen = (shouldOpen) => {
                if (select.disabled) {
                    return;
                }
                if (shouldOpen) {
                    if (openItem && openItem !== item) {
                        openItem.setOpen(false);
                    }
                    wrapper.classList.add('is-open');
                    list.hidden = false;
                    trigger.setAttribute('aria-expanded', 'true');
                    openItem = item;
                    const currentIndex = select.selectedIndex >= 0 ? select.selectedIndex : 0;
                    setActiveIndex(currentIndex);
                    list.focus({ preventScroll: true });
                } else {
                    wrapper.classList.remove('is-open');
                    list.hidden = true;
                    trigger.setAttribute('aria-expanded', 'false');
                    if (openItem === item) {
                        openItem = null;
                    }
                }
            };

            const selectByIndex = (index) => {
                const options = Array.from(select.options);
                if (!options[index] || options[index].disabled) {
                    return;
                }
                select.selectedIndex = index;
                select.dispatchEvent(new Event('change', { bubbles: true }));
            };

            const handleTriggerClick = () => {
                setOpen(!wrapper.classList.contains('is-open'));
            };

            const handleTriggerKeyDown = (event) => {
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    setOpen(true);
                    const nextIndex = getNextEnabledIndex(select.selectedIndex, 1);
                    if (nextIndex >= 0) {
                        setActiveIndex(nextIndex);
                    }
                    return;
                }
                if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    setOpen(true);
                    const nextIndex = getNextEnabledIndex(select.selectedIndex, -1);
                    if (nextIndex >= 0) {
                        setActiveIndex(nextIndex);
                    }
                    return;
                }
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setOpen(true);
                }
            };

            const handleListKeyDown = (event) => {
                const activeIndex = Number(list.dataset.activeIndex || '0');
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    const nextIndex = getNextEnabledIndex(activeIndex, 1);
                    if (nextIndex >= 0) {
                        setActiveIndex(nextIndex);
                    }
                    return;
                }
                if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    const nextIndex = getNextEnabledIndex(activeIndex, -1);
                    if (nextIndex >= 0) {
                        setActiveIndex(nextIndex);
                    }
                    return;
                }
                if (event.key === 'Home') {
                    event.preventDefault();
                    setActiveIndex(0);
                    return;
                }
                if (event.key === 'End') {
                    event.preventDefault();
                    const optionNodes = getOptionNodes();
                    setActiveIndex(optionNodes.length - 1);
                    return;
                }
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectByIndex(activeIndex);
                    setOpen(false);
                    trigger.focus({ preventScroll: true });
                    return;
                }
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    setOpen(false);
                    trigger.focus({ preventScroll: true });
                    return;
                }
                if (event.key === 'Tab') {
                    setOpen(false);
                }
            };

            const handleListClick = (event) => {
                const optionNode = event.target.closest('.account-select__option');
                if (!optionNode || optionNode.classList.contains('is-disabled')) {
                    return;
                }
                const index = Number(optionNode.dataset.index || '-1');
                if (Number.isNaN(index) || index < 0) {
                    return;
                }
                selectByIndex(index);
                setOpen(false);
                trigger.focus({ preventScroll: true });
            };

            trigger.addEventListener('click', handleTriggerClick);
            trigger.addEventListener('keydown', handleTriggerKeyDown);
            list.addEventListener('keydown', handleListKeyDown);
            list.addEventListener('click', handleListClick);
            select.addEventListener('change', syncFromSelect);

            buildOptions();
            syncFromSelect();

            const item = { select, wrapper, trigger, list, setOpen, buildOptions, syncFromSelect };
            return item;
        };

        const items = selects.map(buildItem).filter(Boolean);

        const closeAll = () => {
            items.forEach((item) => item.setOpen(false));
        };

        const syncAll = () => {
            items.forEach((item) => {
                item.buildOptions();
                item.syncFromSelect();
            });
        };

        const handleDocumentClick = (event) => {
            if (!openItem) {
                return;
            }
            if (openItem.wrapper.contains(event.target)) {
                return;
            }
            openItem.setOpen(false);
        };

        const handleDocumentKeyDown = (event) => {
            if (event.key !== 'Escape' || !openItem) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            openItem.setOpen(false);
            openItem.trigger.focus({ preventScroll: true });
        };

        document.addEventListener('click', handleDocumentClick);
        document.addEventListener('keydown', handleDocumentKeyDown);

        return { closeAll, syncAll };
    };

    const storeAccessToken = (token) => {
        try {
            if (!token) {
                window.sessionStorage.removeItem(BILLING_TOKEN_KEY);
                return;
            }
            window.sessionStorage.setItem(BILLING_TOKEN_KEY, token);
        } catch (_) {
            /* storage blocked */
        }
    };

    const readAccessToken = () => {
        try {
            return window.sessionStorage.getItem(BILLING_TOKEN_KEY);
        } catch (_) {
            return null;
        }
    };

    const fetchAccessTokenViaRefresh = async () => {
        const refreshUrl = body?.dataset.authRefreshUrl || '';
        if (!refreshUrl) {
            return null;
        }
        try {
            const response = await fetch(refreshUrl, {
                method: 'POST',
                headers: { Accept: 'application/json' },
                credentials: 'include',
            });
            if (!response.ok) {
                return null;
            }
            const payload = await response.json().catch(() => ({}));
            if (typeof payload?.access_token === 'string') {
                storeAccessToken(payload.access_token);
                return payload.access_token;
            }
        } catch (_) {
            return null;
        }
        return null;
    };

    const acquireAccessToken = async () => {
        const existing = readAccessToken();
        if (existing) {
            return existing;
        }
        return fetchAccessTokenViaRefresh();
    };

    const readNotificationIds = (() => {
        try {
            const raw = window.localStorage?.getItem(NOTIFICATION_STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            if (Array.isArray(parsed)) {
                return new Set(parsed.filter((item) => typeof item === 'string' && item));
            }
        } catch (_) {
            /* storage blocked */
        }
        return new Set();
    })();

    const persistReadNotifications = () => {
        try {
            window.localStorage?.setItem(NOTIFICATION_STORAGE_KEY, JSON.stringify([...readNotificationIds]));
        } catch (_) {
            /* storage blocked */
        }
    };

    const isNotificationRead = (id) => Boolean(id) && readNotificationIds.has(id);

    const markNotificationRead = (id) => {
        if (!id || readNotificationIds.has(id)) {
            return;
        }
        readNotificationIds.add(id);
        persistReadNotifications();
    };

    const markNotificationsRead = (ids = []) => {
        let changed = false;
        ids.forEach((id) => {
            if (!id || readNotificationIds.has(id)) {
                return;
            }
            readNotificationIds.add(id);
            changed = true;
        });
        if (changed) {
            persistReadNotifications();
        }
    };

    const authorizedFetch = async (url, options = {}, allowRetry = true) => {
        const token = await acquireAccessToken();
        if (!token) {
            throw new Error('Re-authentication required.');
        }
        const headers = {
            Accept: 'application/json',
            ...(options.headers || {}),
            Authorization: `Bearer ${token}`,
        };
        const response = await fetch(url, {
            ...options,
            headers,
        });
        if (response.status === 401 && allowRetry) {
            storeAccessToken(null);
            const refreshed = await fetchAccessTokenViaRefresh();
            if (refreshed) {
                return authorizedFetch(url, options, false);
            }
            throw new Error('Session expired, please sign in again.');
        }
        return response;
    };

    const persistPlaygroundSecret = (secret) => {
        const hasPersistedOptIn = (() => {
            try {
                return Boolean(window.localStorage?.getItem(PLAYGROUND_PERSISTED_KEY));
            } catch (_) {
                return false;
            }
        })();
        try {
            if (secret) {
                window.localStorage?.setItem(PLAYGROUND_STORAGE_KEY, secret);
                if (hasPersistedOptIn) {
                    window.localStorage?.setItem(PLAYGROUND_PERSISTED_KEY, secret);
                }
            } else {
                window.localStorage?.removeItem(PLAYGROUND_STORAGE_KEY);
            }
        } catch (_) {
            /* storage blocked */
        }
        try {
            window.dispatchEvent(
                new CustomEvent('aici:playground-secret', {
                    detail: { secret },
                }),
            );
        } catch (_) {
            /* ignore */
        }
    };

    const sidebar = document.querySelector('[data-sidebar]');
    const sidebarTrigger = document.querySelector('[data-sidebar-trigger]');
    const sidebarClose = document.querySelector('[data-sidebar-close]');
    const sidebarHeader = document.querySelector('[data-sidebar-header-home]');
    const sidebarHomeUrl = sidebarHeader?.dataset.homeUrl || '';
    const toastRegion = document.querySelector('[data-toast-region]');
    const notificationsRoot = document.querySelector('[data-notifications-root]');
    const notificationsButton = document.querySelector('[data-notifications-button]');
    const notificationsPanel = document.querySelector('[data-notifications-panel]');
    const sidebarLayout = document.querySelector('[data-sidebar-layout]');
    const sidebarScrim = document.querySelector('[data-sidebar-scrim]');
    const sidebarNavLinks = Array.from(document.querySelectorAll('.account-shell__nav-link'));
    const sidebarInertTargets = Array.from(document.querySelectorAll('[data-sidebar-inert]'));
    const shellHeader = document.querySelector('[data-shell-header]');
    const hasSidebar = Boolean(sidebar && sidebarLayout);

    let isSidebarOpen = true;
    let isOverlayMode = false;
    let lastViewportDesktop = true;
    let scrollTopBeforeLock = 0;
    let headerHeightRaf = 0;
    let headerResizeObserver = null;

    const syncHeaderHeight = () => {
        if (!body || !shellHeader) {
            return;
        }
        const nextHeight = Math.round(shellHeader.getBoundingClientRect().height);
        if (!nextHeight) {
            return;
        }
        body.style.setProperty('--account-header-height', `${nextHeight}px`);
    };

    const requestHeaderHeightSync = () => {
        if (headerHeightRaf) {
            window.cancelAnimationFrame(headerHeightRaf);
        }
        headerHeightRaf = window.requestAnimationFrame(() => {
            headerHeightRaf = 0;
            syncHeaderHeight();
        });
    };

    const initHeaderHeightSync = () => {
        requestHeaderHeightSync();
        if (!shellHeader || typeof ResizeObserver !== 'function' || headerResizeObserver) {
            return;
        }
        headerResizeObserver = new ResizeObserver(() => {
            requestHeaderHeightSync();
        });
        headerResizeObserver.observe(shellHeader);
    };

    const readStoredSidebarState = () => {
        try {
            const stored = window.localStorage?.getItem(SIDEBAR_STORAGE_KEY);
            if (stored === 'true') {
                return true;
            }
            if (stored === 'false') {
                return false;
            }
        } catch (_) {
            /* storage blocked */
        }
        return null;
    };

    const persistSidebarState = (state) => {
        try {
            window.localStorage?.setItem(SIDEBAR_STORAGE_KEY, state ? 'true' : 'false');
        } catch (_) {
            /* storage blocked */
        }
    };

    const getIsDesktop = () => window.matchMedia(`(min-width: ${DESKTOP_BREAKPOINT}px)`).matches;

    const applySidebarClasses = () => {
        body.classList.toggle('is-sidebar-open', isSidebarOpen);
        body.classList.toggle('is-sidebar-docked', !isOverlayMode);
        body.classList.toggle('is-sidebar-overlay', isOverlayMode);
    };

    const updateSidebarA11y = () => {
        if (sidebarTrigger) {
            sidebarTrigger.setAttribute('aria-expanded', isSidebarOpen ? 'true' : 'false');
        }
        if (sidebar) {
            const isDialog = isOverlayMode && isSidebarOpen;
            sidebar.setAttribute('aria-hidden', isSidebarOpen ? 'false' : 'true');
            sidebar.setAttribute('role', isDialog ? 'dialog' : 'complementary');
            if (isDialog) {
                sidebar.setAttribute('aria-modal', 'true');
            } else {
                sidebar.removeAttribute('aria-modal');
            }
            if (isSidebarOpen) {
                sidebar.removeAttribute('inert');
            } else {
                sidebar.setAttribute('inert', '');
            }
        }
        if (sidebarScrim) {
            sidebarScrim.hidden = !(isOverlayMode && isSidebarOpen);
        }
    };

    const lockBodyScroll = () => {
        if (body.classList.contains('is-sidebar-scroll-locked')) {
            syncScrollbarCompensation();
            return;
        }
        scrollTopBeforeLock = window.scrollY || 0;
        body.dataset.sidebarScroll = `${scrollTopBeforeLock}`;
        body.classList.add('is-sidebar-scroll-locked');
        body.dataset.sidebarPrevOverflow = body.style.overflow || '';
        body.dataset.sidebarPrevPosition = body.style.position || '';
        body.dataset.sidebarPrevTop = body.style.top || '';
        body.dataset.sidebarPrevLeft = body.style.left || '';
        body.dataset.sidebarPrevRight = body.style.right || '';
        body.dataset.sidebarPrevWidth = body.style.width || '';
        body.style.overflow = 'hidden';
        body.style.position = 'fixed';
        body.style.top = `-${scrollTopBeforeLock}px`;
        body.style.left = '0';
        body.style.right = '0';
        body.style.width = '100%';
        syncScrollbarCompensation();
    };

    const unlockBodyScroll = () => {
        if (!body.classList.contains('is-sidebar-scroll-locked')) {
            syncScrollbarCompensation();
            return;
        }
        const restore = Number(body.dataset.sidebarScroll || 0);
        body.classList.remove('is-sidebar-scroll-locked');
        body.style.overflow = body.dataset.sidebarPrevOverflow || '';
        body.style.position = body.dataset.sidebarPrevPosition || '';
        body.style.top = body.dataset.sidebarPrevTop || '';
        body.style.left = body.dataset.sidebarPrevLeft || '';
        body.style.right = body.dataset.sidebarPrevRight || '';
        body.style.width = body.dataset.sidebarPrevWidth || '';
        delete body.dataset.sidebarScroll;
        delete body.dataset.sidebarPrevOverflow;
        delete body.dataset.sidebarPrevPosition;
        delete body.dataset.sidebarPrevTop;
        delete body.dataset.sidebarPrevLeft;
        delete body.dataset.sidebarPrevRight;
        delete body.dataset.sidebarPrevWidth;
        syncScrollbarCompensation();
        window.scrollTo(0, restore);
    };

    const setInertTargets = (shouldDisable) => {
        sidebarInertTargets.forEach((element) => {
            const focusable = Array.from(element.querySelectorAll(FOCUSABLE_SIDEBAR_SELECTOR)).filter(isFocusableNode);
            if (shouldDisable) {
                if (!Object.prototype.hasOwnProperty.call(element.dataset, 'sidebarAriaHidden')) {
                    element.dataset.sidebarAriaHidden = element.getAttribute('aria-hidden') || '';
                }
                element.setAttribute('aria-hidden', 'true');
                element.setAttribute('inert', '');
                focusable.forEach((node) => {
                    if (!Object.prototype.hasOwnProperty.call(node.dataset, 'sidebarTabIndex')) {
                        node.dataset.sidebarTabIndex = node.getAttribute('tabindex') || '';
                    }
                    node.setAttribute('tabindex', '-1');
                });
                return;
            }
            if (Object.prototype.hasOwnProperty.call(element.dataset, 'sidebarAriaHidden')) {
                const previous = element.dataset.sidebarAriaHidden;
                if (previous) {
                    element.setAttribute('aria-hidden', previous);
                } else {
                    element.removeAttribute('aria-hidden');
                }
                delete element.dataset.sidebarAriaHidden;
            } else {
                element.removeAttribute('aria-hidden');
            }
            element.removeAttribute('inert');
            focusable.forEach((node) => {
                if (Object.prototype.hasOwnProperty.call(node.dataset, 'sidebarTabIndex')) {
                    const previous = node.dataset.sidebarTabIndex;
                    if (previous) {
                        node.setAttribute('tabindex', previous);
                    } else {
                        node.removeAttribute('tabindex');
                    }
                    delete node.dataset.sidebarTabIndex;
                } else if (node.getAttribute('tabindex') === '-1') {
                    node.removeAttribute('tabindex');
                }
            });
        });
    };

    const getSidebarFocusables = () => {
        if (!sidebar) {
            return [];
        }
        return Array.from(sidebar.querySelectorAll(FOCUSABLE_SIDEBAR_SELECTOR)).filter(isFocusableNode);
    };

    const focusFirstSidebarItem = () => {
        const focusable = getSidebarFocusables();
        if (focusable.length) {
            focusable[0].focus();
            return;
        }
        sidebar?.focus();
    };

    const restoreSidebarTriggerFocus = () => {
        if (sidebarTrigger && typeof sidebarTrigger.focus === 'function') {
            sidebarTrigger.focus({ preventScroll: true });
        }
    };

    const enforceSidebarFocus = (event) => {
        if (!isOverlayMode || !isSidebarOpen || event.key !== 'Tab') {
            return;
        }
        const focusable = getSidebarFocusables();
        if (!focusable.length) {
            event.preventDefault();
            sidebar?.focus();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (event.shiftKey) {
            if (active === first || !sidebar?.contains(active)) {
                event.preventDefault();
                last.focus();
            }
            return;
        }
        if (active === last) {
            event.preventDefault();
            first.focus();
        }
    };

    const renderSidebarState = ({ skipFocus = false } = {}) => {
        applySidebarClasses();
        updateSidebarA11y();
        if (isOverlayMode) {
            setInertTargets(isSidebarOpen);
            if (isSidebarOpen) {
                lockBodyScroll();
                if (!skipFocus) {
                    focusFirstSidebarItem();
                }
            } else {
                unlockBodyScroll();
                if (!skipFocus) {
                    restoreSidebarTriggerFocus();
                }
            }
            requestHeaderHeightSync();
            return;
        }
        setInertTargets(false);
        unlockBodyScroll();
        requestHeaderHeightSync();
    };

    const setSidebarState = (state, options = {}) => {
        const { persist = true, skipFocus = false } = options;
        isSidebarOpen = Boolean(state);
        if (persist) {
            persistSidebarState(isSidebarOpen);
        }
        renderSidebarState({ skipFocus });
    };

    const toggleSidebarState = () => setSidebarState(!isSidebarOpen);

    const handleSidebarNavLinkClick = () => {
        if (!hasSidebar || !isOverlayMode || !isSidebarOpen) {
            return;
        }
        setSidebarState(false, { skipFocus: true });
    };

    const navigateHomeFromSidebarHeader = () => {
        if (!sidebarHomeUrl) {
            return;
        }
        window.location.assign(sidebarHomeUrl);
    };

    const handleSidebarResize = () => {
        syncScrollbarCompensation();
        requestHeaderHeightSync();
        if (!hasSidebar) {
            return;
        }
        const isDesktop = getIsDesktop();
        const modeChanged = isDesktop !== lastViewportDesktop;
        lastViewportDesktop = isDesktop;
        isOverlayMode = !isDesktop;
        const storedState = readStoredSidebarState();
        const defaultState = isDesktop;
        if (modeChanged) {
            isSidebarOpen = typeof storedState === 'boolean' ? storedState : defaultState;
        }
        renderSidebarState({ skipFocus: true });
    };

    const initSidebarState = () => {
        if (!hasSidebar) {
            return;
        }
        const isDesktop = getIsDesktop();
        lastViewportDesktop = isDesktop;
        isOverlayMode = !isDesktop;
        const storedState = readStoredSidebarState();
        const defaultState = isDesktop;
        isSidebarOpen = typeof storedState === 'boolean' ? storedState : defaultState;
        renderSidebarState({ skipFocus: true });
    };

    const syncNotificationBadges = () => {
        if (!notificationsRoot) {
            return;
        }
        const bellDot = notificationsRoot.querySelector('.account-shell__bell-dot');
        const counter = notificationsRoot.querySelector('.account-shell__notifications-counter');
        const items = notificationsRoot.querySelectorAll('[data-notification-id]');
        let unreadCount = 0;
        items.forEach((item) => {
            const id = item.dataset.notificationId;
            const unread = id ? !isNotificationRead(id) : false;
            item.classList.toggle('is-unread', unread);
            if (unread) {
                unreadCount += 1;
            }
        });
        if (bellDot) {
            bellDot.classList.toggle('is-hidden', unreadCount === 0);
        }
        if (counter) {
            counter.textContent = unreadCount ? unreadCount + ' new' : 'No new notifications';
        }
    };

    const markAllNotificationsAsRead = () => {
        if (!notificationsRoot) {
            return;
        }
        const ids = Array.from(notificationsRoot.querySelectorAll('[data-notification-id]'))
            .map((item) => item.dataset.notificationId)
            .filter(Boolean);
        markNotificationsRead(ids);
        syncNotificationBadges();
    };

    initHeaderHeightSync();
    initSidebarState();

    if (sidebarTrigger) {
        sidebarTrigger.addEventListener('click', toggleSidebarState);
    }

    if (sidebarClose) {
        sidebarClose.addEventListener('click', () => setSidebarState(false));
    }

    if (sidebarScrim) {
        sidebarScrim.addEventListener('click', () => setSidebarState(false));
    }

    if (sidebarNavLinks.length) {
        sidebarNavLinks.forEach((link) => {
            link.addEventListener('click', handleSidebarNavLinkClick);
        });
    }

    if (sidebarHeader && sidebarHomeUrl) {
        sidebarHeader.addEventListener('click', (event) => {
            const target = event.target;
            if (target instanceof Element && target.closest('[data-sidebar-close]')) {
                return;
            }
            navigateHomeFromSidebarHeader();
        });
        sidebarHeader.addEventListener('keydown', (event) => {
            const target = event.target;
            if (target instanceof Element && target.closest('[data-sidebar-close]')) {
                return;
            }
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                navigateHomeFromSidebarHeader();
            }
        });
    }

    window.addEventListener('resize', handleSidebarResize);
    window.visualViewport?.addEventListener('resize', requestHeaderHeightSync);

    const highlightToastRegion = () => {
        if (!toastRegion) {
            return;
        }
        toastRegion.classList.add('account-shell__toast-region--pulse');
        try {
            toastRegion.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (_) {
            toastRegion.scrollIntoView();
        }
        window.setTimeout(() => {
            toastRegion.classList.remove('account-shell__toast-region--pulse');
        }, 1400);
    };

    let isNotificationsOpen = false;
    syncNotificationBadges();

    const closeNotificationsPanel = () => {
        if (!notificationsButton || !notificationsPanel) {
            return;
        }
        notificationsPanel.hidden = true;
        notificationsButton.setAttribute('aria-expanded', 'false');
        notificationsRoot?.classList.remove('is-open');
        isNotificationsOpen = false;
    };

    const openNotificationsPanel = () => {
        if (!notificationsButton || !notificationsPanel) {
            highlightToastRegion();
            return;
        }
        notificationsPanel.hidden = false;
        notificationsButton.setAttribute('aria-expanded', 'true');
        notificationsRoot?.classList.add('is-open');
        isNotificationsOpen = true;
        markAllNotificationsAsRead();
    };

    const toggleNotificationsPanel = () => {
        if (!notificationsPanel) {
            highlightToastRegion();
            return;
        }
        if (isNotificationsOpen) {
            closeNotificationsPanel();
        } else {
            openNotificationsPanel();
        }
    };

    const handleNotificationsKeydown = (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') {
            return;
        }
        event.preventDefault();
        toggleNotificationsPanel();
    };

    if (notificationsButton) {
        notificationsButton.addEventListener('click', toggleNotificationsPanel);
        notificationsButton.addEventListener('keydown', handleNotificationsKeydown);
    }

    document.addEventListener('click', (event) => {
        if (!notificationsRoot || !isNotificationsOpen) {
            return;
        }
        if (!notificationsRoot.contains(event.target)) {
            closeNotificationsPanel();
        }
    });

    body.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            if (hasSidebar && isSidebarOpen) {
                setSidebarState(false);
            }
            closeNotificationsPanel();
        }
        if (hasSidebar) {
            enforceSidebarFocus(event);
        }
    });

    const initToasts = () => {
        if (!toastRegion) {
            return;
        }

        const dismissToast = (toast) => {
            if (!toast) {
                return;
            }
            const toastId = toast.dataset.toastId;
            if (toastId) {
                markNotificationRead(toastId);
            }
            toast.remove();
            syncNotificationBadges();
        };

        const toasts = Array.from(toastRegion.querySelectorAll('[data-toast-message]'));
        toasts.forEach((toast) => {
            const toastId = toast.dataset.toastId;
            if (isNotificationRead(toastId)) {
                toast.remove();
                return;
            }
            if (toastId) {
                markNotificationRead(toastId);
            }
        });
        syncNotificationBadges();

        toastRegion.addEventListener('click', (event) => {
            const dismissControl = event.target.closest('[data-toast-dismiss]');
            if (!dismissControl) {
                return;
            }
            const toast = dismissControl.closest('[data-toast-message]');
            dismissToast(toast);
        });

        const autoToasts = toastRegion.querySelectorAll('[data-toast-message][data-auto-close]');
        autoToasts.forEach((toast) => {
            const delay = Number(toast.dataset.autoClose) || 0;
            if (!delay) {
                return;
            }
            setTimeout(() => {
                toast.classList.add('is-hiding');
                toast.addEventListener(
                    'transitionend',
                    () => dismissToast(toast),
                    { once: true }
                );
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(-6px)';
            }, delay);
        });
    };

    const initKeysSecurity = () => {
        const root = document.querySelector('[data-keys-root]');
        if (!root) {
            return;
        }
        const dataset = body?.dataset || {};
        const endpoints = {
            list: dataset.apiKeysUrl || '',
            create: dataset.apiKeysCreateUrl || '',
            updateTemplate: dataset.apiKeysUpdateUrl || '',
            rotateTemplate: dataset.apiKeysRotateUrl || '',
            revokeTemplate: dataset.apiKeysRevokeUrl || '',
            activityTemplate: dataset.apiKeysActivityUrl || '',
        };
        if (!endpoints.list || !endpoints.create || !endpoints.updateTemplate) {
            return;
        }

        const elements = {
            summaryPlan: root.querySelector('[data-keys-summary-plan]'),
            summarySlots: root.querySelector('[data-keys-summary-slots]'),
            summaryUsage: root.querySelector('[data-keys-summary-usage]'),
            summaryDaily: root.querySelector('[data-keys-summary-daily]'),
            summaryBurst: root.querySelector('[data-keys-summary-burst]'),
            createForm: document.querySelector('[data-keys-create-form]'),
            createRole: document.querySelector('[data-keys-create-role]'),
            createError: document.querySelector('[data-keys-create-error]'),
            refresh: root.querySelector('[data-keys-refresh]'),
            secretWrap: root.querySelector('[data-keys-secret]'),
            secretValue: root.querySelector('[data-keys-secret-value]'),
            secretCopy: root.querySelector('[data-keys-secret-copy]'),
            list: root.querySelector('[data-keys-list]'),
            empty: root.querySelector('[data-keys-empty]'),
            detailForm: root.querySelector('[data-keys-detail-form]'),
            detailRole: root.querySelector('[data-keys-detail-role]'),
            detailTitle: root.querySelector('[data-keys-detail-title]'),
            detailError: root.querySelector('[data-keys-detail-error]'),
            rotate: root.querySelector('[data-keys-rotate]'),
            revoke: root.querySelector('[data-keys-revoke]'),
            activityList: root.querySelector('[data-keys-activity-list]'),
            activityEmpty: root.querySelector('[data-keys-activity-empty]'),
        };
        elements.createModal = document.querySelector('[data-keys-create-modal]');
        elements.createDialog = elements.createModal?.querySelector('.keys-modal__dialog') || null;
        elements.createOpeners = Array.from(document.querySelectorAll('[data-keys-create-open]'));
        elements.createClosers = Array.from(document.querySelectorAll('[data-keys-create-close]'));
        const defaultSecretCopyLabel = elements.secretCopy?.textContent?.trim() || 'Copy secret';

        const state = {
            keys: [],
            roles: [],
            rolesLoaded: false,
            selectedKeyId: null,
            planLimits: null,
            maxKeys: 0,
            hiddenKeyIds: new Set(),
        };

        const buildUrl = (template, keyId) => template.replace('__KEY__', keyId);

        const formatQuota = (value, suffix) => {
            if (value === null || typeof value === 'undefined') {
                return 'n/a';
            }
            const numeric = Number(value);
            if (!Number.isFinite(numeric)) {
                return 'n/a';
            }
            return `${numeric.toLocaleString()} ${suffix}`;
        };

        const formatUsage = (usage) => {
            if (!usage) {
                return 'n/a';
            }
            const calls = Number(usage.monthly_calls) || 0;
            const limit = typeof usage.monthly_quota === 'number' && usage.monthly_quota > 0 ? usage.monthly_quota : null;
            if (!limit) {
                return `${calls.toLocaleString()} tokens`;
            }
            return `${calls.toLocaleString()} / ${limit.toLocaleString()} tokens`;
        };

        const getReadableMessage = (message, fallback) => {
            if (typeof message !== 'string') {
                return fallback;
            }
            const trimmed = message.trim();
            const hasNonAscii = /[^\x20-\x7E]/.test(trimmed);
            return hasNonAscii || !trimmed ? fallback : trimmed;
        };

        const setSecret = (value) => {
            if (!elements.secretWrap || !elements.secretValue) {
                return;
            }
            if (elements.secretCopy) {
                elements.secretCopy.textContent = defaultSecretCopyLabel;
                elements.secretCopy.disabled = false;
            }
            if (!value) {
                elements.secretWrap.hidden = true;
                persistPlaygroundSecret('');
                return;
            }
            elements.secretValue.textContent = value;
            elements.secretWrap.hidden = false;
            persistPlaygroundSecret(value);
        };

        const parseListField = (value) => {
            if (typeof value !== 'string') {
                return [];
            }
            return value
                .split(/[\n,]+/)
                .map((item) => item.trim())
                .filter(Boolean);
        };

        const joinListField = (values) => (Array.isArray(values) ? values.join('\n') : '');

        const clearErrors = () => {
            if (elements.createError) {
                elements.createError.textContent = '';
            }
            if (elements.detailError) {
                elements.detailError.textContent = '';
            }
        };
        const isCreateModalOpen = () => Boolean(elements.createModal && elements.createModal.hidden === false);
        const openCreateModal = () => {
            clearErrors();
            if (elements.createModal) {
                setModalScrollLock(true);
                elements.createModal.hidden = false;
            }
            if (elements.createDialog) {
                elements.createDialog.scrollTop = 0;
            }
            const firstInput = elements.createForm?.querySelector('input[name="label"]');
            if (firstInput) {
                window.requestAnimationFrame(() => {
                    try {
                        firstInput.focus({ preventScroll: true });
                    } catch (_) {
                        firstInput.focus();
                    }
                });
            }
        };
        const closeCreateModal = () => {
            if (elements.createModal) {
                elements.createModal.hidden = true;
                setModalScrollLock(false);
            }
            if (elements.createError) {
                elements.createError.textContent = '';
            }
        };

        const getSelectedKey = () => state.keys.find((item) => item.id === state.selectedKeyId);

        const updateDetailActions = (key) => {
            const saveButton = elements.detailForm?.querySelector('[type="submit"]');
            const rotateButton = elements.rotate;
            const revokeButton = elements.revoke;
            const hasKey = Boolean(key);
            const isRevoked = key?.status === 'revoked';
            if (saveButton) {
                saveButton.hidden = !hasKey || isRevoked;
                saveButton.disabled = !hasKey || isRevoked;
            }
            if (rotateButton) {
                rotateButton.hidden = !hasKey || isRevoked;
                rotateButton.disabled = !hasKey || isRevoked;
            }
            if (revokeButton) {
                revokeButton.hidden = !hasKey;
                revokeButton.textContent = isRevoked ? 'Delete' : 'Revoke';
                revokeButton.dataset.action = isRevoked ? 'delete' : 'revoke';
            }
        };

        const resetDetailView = () => {
            if (elements.detailForm) {
                elements.detailForm.reset();
                elements.detailForm.dataset.keyId = '';
            }
            if (elements.detailTitle) {
                elements.detailTitle.textContent = 'Select a key';
            }
            updateDetailActions(null);
            if (elements.activityList) {
                elements.activityList.innerHTML = '<div class="keys-activity__row is-empty">Select a key to view history.</div>';
            }
            if (elements.activityEmpty) {
                elements.activityEmpty.hidden = true;
            }
            setSecret('');
        };

        const setSummary = (payload) => {
            state.planLimits = payload?.plan_limits || null;
            state.maxKeys = Number(payload?.max_keys) || 0;
            const keys = Array.isArray(payload?.keys) ? payload.keys : [];
            const activeKeys = keys.filter((key) => key.status === 'active');
            const activeCount = activeKeys.length;
            if (elements.summaryPlan) {
                elements.summaryPlan.textContent = `${(payload?.plan_code || 'unknown').toUpperCase()} plan`;
            }
            if (elements.summarySlots) {
                elements.summarySlots.textContent = `${activeCount} of ${state.maxKeys} active keys used`;
            }
            if (elements.summaryUsage) {
                const usageTotal = Number(payload?.monthly_usage_total);
                const usageLabel = Number.isFinite(usageTotal)
                    ? usageTotal
                    : keys.reduce((total, key) => total + (Number(key?.usage?.monthly_calls) || 0), 0);
                const limit = state.planLimits?.monthly_quota;
                elements.summaryUsage.textContent = limit
                    ? `${usageLabel.toLocaleString()} / ${limit.toLocaleString()} tokens`
                    : `${usageLabel.toLocaleString()} tokens`;
            }
            if (elements.summaryDaily) {
                elements.summaryDaily.textContent = formatQuota(state.planLimits?.daily_quota, 'tokens / day');
            }
            if (elements.summaryBurst) {
                const burst = formatQuota(state.planLimits?.burst_per_second, 'tokens/s');
                const latency = state.planLimits?.data_latency_seconds || 0;
                elements.summaryBurst.textContent = `${burst}, latency ${latency ? `${latency}s` : 'RT'}`;
            }
            const reachedLimit = Boolean(state.maxKeys && activeCount >= state.maxKeys);
            if (elements.createForm) {
                const submit = elements.createForm.querySelector('[type="submit"]');
                if (submit) {
                    submit.disabled = reachedLimit;
                }
            }
            if (elements.createOpeners?.length) {
                elements.createOpeners.forEach((button) => {
                    button.disabled = reachedLimit;
                    if (reachedLimit) {
                        button.setAttribute('title', 'Active key limit reached');
                        button.setAttribute('aria-disabled', 'true');
                    } else {
                        button.setAttribute('title', 'New key');
                        button.removeAttribute('aria-disabled');
                    }
                });
            }
        };
        const renderRoles = (selectNode) => {
            if (!selectNode) {
                return;
            }
            const prevValue = selectNode.value;
            selectNode.innerHTML = '';
            const roles = Array.isArray(state.roles) ? state.roles : [];
            if (!state.rolesLoaded) {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = 'Loading roles...';
                selectNode.appendChild(option);
                selectNode.disabled = true;
                return;
            }
            if (!roles.length) {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = 'No roles available';
                selectNode.appendChild(option);
                selectNode.disabled = true;
                return;
            }
            selectNode.disabled = false;
            roles.forEach((role) => {
                const option = document.createElement('option');
                option.value = role;
                option.textContent = role.replace(/_/g, ' ');
                selectNode.appendChild(option);
            });
            if (prevValue && roles.includes(prevValue)) {
                selectNode.value = prevValue;
            }
        };

        const renderList = () => {
            if (!elements.list) {
                return;
            }
            if (!state.keys.length) {
                elements.list.innerHTML = '<li class="keys-list__item is-empty">No keys available.</li>';
                if (elements.empty) {
                    elements.empty.hidden = false;
                }
                return;
            }
            if (elements.empty) {
                elements.empty.hidden = true;
            }
            const items = state.keys
                .map((key) => {
                    const activeClass = key.id === state.selectedKeyId ? ' is-active' : '';
                    const usage = formatUsage(key?.usage);
                    const status = key.status || '-';
                    return `
                        <li class="keys-list__item${activeClass}" data-key-id="${key.id}">
                            <div class="keys-list__info">
                                <strong>${key.label || 'Untitled'}</strong>
                                <span>${key.role || '-'}</span>
                            </div>
                            <div class="keys-list__meta">
                                <span class="keys-status-badge keys-status-badge--${status}">${status}</span>
                                <small>${usage}</small>
                            </div>
                        </li>
                    `;
                })
                .join('');
            elements.list.innerHTML = items;
        };

        const renderDetail = (key) => {
            if (!elements.detailForm || !elements.detailTitle) {
                return;
            }
            elements.detailForm.dataset.keyId = key.id;
            const controls = elements.detailForm.elements;
            if (controls.namedItem('label')) {
                controls.namedItem('label').value = key.label || '';
            }
            if (controls.namedItem('application_name')) {
                controls.namedItem('application_name').value = key.application_name || '';
            }
            if (controls.namedItem('tags')) {
                controls.namedItem('tags').value = (key.tags || []).join(',');
            }
            if (controls.namedItem('ip_allowlist')) {
                controls.namedItem('ip_allowlist').value = joinListField(key.ip_allowlist);
            }
            if (controls.namedItem('label_constraints')) {
                controls.namedItem('label_constraints').value = joinListField(key.label_constraints);
            }
            renderRoles(elements.detailRole);
            if (elements.detailRole && key.role) {
                elements.detailRole.value = key.role;
            }
            elements.detailTitle.textContent = key.label || 'Selected key';
            updateDetailActions(key);
            if (elements.activityList) {
                elements.activityList.innerHTML = '<div class="keys-activity__row is-empty">Loading events...</div>';
            }
        };

        const fetchActivity = async (keyId) => {
            if (!elements.activityList || !endpoints.activityTemplate) {
                return;
            }
            if (elements.activityEmpty) {
                elements.activityEmpty.hidden = true;
            }
            const url = buildUrl(endpoints.activityTemplate, keyId);
            try {
                const response = await authorizedFetch(url);
                if (!response.ok) {
                    throw new Error('Failed to load keys.');
                }
                const payload = await response.json();
                const events = Array.isArray(payload?.events) ? payload.events : [];
                if (!events.length) {
                    elements.activityList.innerHTML = '';
                    if (elements.activityEmpty) {
                        elements.activityEmpty.hidden = false;
                    }
                    return;
                }
                if (elements.activityEmpty) {
                    elements.activityEmpty.hidden = true;
                }
                const markup = events
                    .map((event) => {
                        const dateValue = event.created_at ? new Date(event.created_at) : null;
                        const timestamp = dateValue && !Number.isNaN(dateValue.getTime()) ? dateValue.toLocaleString() : '--';
                        const description = event.description ? `<p>${event.description}</p>` : '<p>--</p>';
                        return `
                            <div class="keys-activity__row">
                                <div class="keys-activity__cell" data-label="Event">
                                    <strong>${event.event_type}</strong>
                                </div>
                                <div class="keys-activity__cell" data-label="Details">
                                    ${description}
                                </div>
                                <div class="keys-activity__cell keys-activity__cell--meta" data-label="Actor">
                                    <span>${event.actor || 'system'}</span>
                                </div>
                                <div class="keys-activity__cell keys-activity__cell--meta" data-label="When">
                                    <span>${timestamp}</span>
                                </div>
                            </div>
                        `;
                    })
                    .join('');
                elements.activityList.innerHTML = markup;
            } catch (error) {
                if (elements.activityList) {
                    elements.activityList.innerHTML = `<div class="keys-activity__row is-empty">${error instanceof Error ? error.message : 'Failed to load activity.'}</div>`;
                }
            }
        };

        const selectKey = (keyId) => {
            state.selectedKeyId = keyId;
            clearErrors();
            renderList();
            const key = state.keys.find((item) => item.id === keyId);
            if (key) {
                renderDetail(key);
                void fetchActivity(keyId);
            }
        };

        const fetchKeys = async (options = {}) => {
            if (!elements.list) {
                return;
            }
            const preserveSecret = Boolean(options.preserveSecret);
            elements.list.innerHTML = '<li class="keys-list__item is-empty">Loading keys...</li>';
            if (!preserveSecret) {
                setSecret('');
            }
            clearErrors();
            try {
                const response = await authorizedFetch(endpoints.list);
                if (!response.ok) {
                    throw new Error('Failed to load keys.');
                }
                const payload = await response.json();
                const fetchedKeys = payload?.keys || [];
                state.keys = fetchedKeys.filter((key) => !state.hiddenKeyIds.has(key.id));
                state.roles = payload?.allowed_roles || [];
                state.rolesLoaded = true;
                renderRoles(elements.createRole);
                renderRoles(elements.detailRole);
                setSummary(payload);
                renderList();
                if (!state.keys.length) {
                    resetDetailView();
                } else if (!state.selectedKeyId) {
                    selectKey(state.keys[0].id);
                } else if (state.selectedKeyId) {
                    const current = state.keys.find((key) => key.id === state.selectedKeyId);
                    if (current) {
                        renderDetail(current);
                        void fetchActivity(current.id);
                    } else if (state.keys.length) {
                        selectKey(state.keys[0].id);
                    }
                }
            } catch (error) {
                if (elements.list) {
                    elements.list.innerHTML = `<li class="keys-list__item is-empty">${error instanceof Error ? error.message : 'Failed to load keys.'}</li>`;
                }
            }
        };
        const handleCreate = async (event) => {
            event.preventDefault();
            if (!elements.createForm) {
                return;
            }
            elements.createError && (elements.createError.textContent = '');
            const formData = new FormData(elements.createForm);
            const tags = parseListField(formData.get('tags'));
            const ipAllow = parseListField(formData.get('ip_allowlist'));
            const labelConstraints = parseListField(formData.get('label_constraints'));
            const payload = {
                label: formData.get('label'),
                application_name: formData.get('application_name') || null,
                role: formData.get('role') || null,
                tags: tags.length ? tags : null,
                ip_allowlist: ipAllow.length ? ipAllow : null,
                label_constraints: labelConstraints.length ? labelConstraints : null,
            };
            const submitButton = elements.createForm.querySelector('[type="submit"]');
            if (submitButton) {
                submitButton.disabled = true;
            }
            try {
                const response = await authorizedFetch(endpoints.create, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data?.detail || 'Failed to create key.');
                }
                const secret = data?.secret || '';
                setSecret(secret);
                elements.createForm.reset();
                closeCreateModal();
                await fetchKeys({ preserveSecret: Boolean(secret) });
            } catch (error) {
                if (elements.createError) {
                    elements.createError.textContent = error instanceof Error ? error.message : 'Failed to create key.';
                }
            } finally {
                if (submitButton) {
                    submitButton.disabled = false;
                }
            }
        };

        const handleUpdate = async (event) => {
            event.preventDefault();
            if (!elements.detailForm) {
                return;
            }
            const keyId = elements.detailForm.dataset.keyId;
            if (!keyId) {
                return;
            }
            const currentKey = getSelectedKey();
            if (currentKey?.status === 'revoked') {
                return;
            }
            elements.detailError && (elements.detailError.textContent = '');
            const formData = new FormData(elements.detailForm);
            const tags = parseListField(formData.get('tags'));
            const ipAllow = parseListField(formData.get('ip_allowlist'));
            const labelConstraints = parseListField(formData.get('label_constraints'));
            const payload = {
                label: formData.get('label'),
                application_name: formData.get('application_name') || null,
                role: formData.get('role') || null,
                tags: tags.length ? tags : null,
                ip_allowlist: ipAllow.length ? ipAllow : null,
                label_constraints: labelConstraints.length ? labelConstraints : null,
            };
            const submitButton = elements.detailForm.querySelector('[type="submit"]');
            if (submitButton) {
                submitButton.disabled = true;
            }
            try {
                const response = await authorizedFetch(buildUrl(endpoints.updateTemplate, keyId), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!response.ok) {
                    const errorPayload = await response.json().catch(() => ({}));
                    throw new Error(errorPayload?.detail || 'Failed to update key.');
                }
                await response.json().catch(() => ({}));
                await fetchKeys();
            } catch (error) {
                if (elements.detailError) {
                    elements.detailError.textContent = error instanceof Error ? error.message : 'Failed to update key.';
                }
            } finally {
                if (submitButton) {
                    submitButton.disabled = false;
                }
            }
        };

        const handleRotate = async () => {
            if (!state.selectedKeyId || !endpoints.rotateTemplate) {
                return;
            }
            const currentKey = getSelectedKey();
            if (currentKey?.status === 'revoked') {
                return;
            }
            elements.detailError && (elements.detailError.textContent = '');
            try {
                const response = await authorizedFetch(buildUrl(endpoints.rotateTemplate, state.selectedKeyId), {
                    method: 'POST',
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(getReadableMessage(payload?.detail, 'Unable to rotate this key.'));
                }
                const secret = payload?.secret || '';
                setSecret(secret);
                await fetchKeys({ preserveSecret: Boolean(secret) });
            } catch (error) {
                if (elements.detailError) {
                    elements.detailError.textContent = error instanceof Error ? error.message : 'Unable to rotate this key.';
                }
            }
        };
        const handleRevoke = async () => {
            if (!state.selectedKeyId || !endpoints.revokeTemplate) {
                return;
            }
            const currentKey = getSelectedKey();
            const isRevoked = currentKey?.status === 'revoked';
            const confirmMessage = isRevoked
                ? 'Delete this key? This action cannot be undone.'
                : 'Revoke this key? This action cannot be undone.';
            if (!window.confirm(confirmMessage)) {
                return;
            }
            clearErrors();
            try {
                const response = await authorizedFetch(buildUrl(endpoints.revokeTemplate, state.selectedKeyId), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: isRevoked ? 'delete_by_user' : 'user_action' }),
                });
                if (!response.ok) {
                    const errorPayload = await response.json().catch(() => ({}));
                    throw new Error(
                        errorPayload?.detail || (isRevoked ? 'Failed to delete this key.' : 'Unable to revoke this key.'),
                    );
                }
                await response.json().catch(() => ({}));
                if (isRevoked) {
                    state.hiddenKeyIds.add(state.selectedKeyId);
                    state.keys = state.keys.filter((item) => item.id !== state.selectedKeyId);
                    state.selectedKeyId = null;
                    if (state.keys.length) {
                        selectKey(state.keys[0].id);
                    } else {
                        renderList();
                        resetDetailView();
                    }
                    return;
                }
                state.selectedKeyId = null;
                await fetchKeys();
            } catch (error) {
                if (elements.detailError) {
                    const fallback = isRevoked ? 'Failed to delete this key.' : 'Unable to revoke this key.';
                    elements.detailError.textContent = error instanceof Error ? error.message : fallback;
                }
            }
        };


        if (elements.list) {
            elements.list.addEventListener('click', (event) => {
                const item = event.target.closest('[data-key-id]');
                if (item) {
                    const keyId = item.getAttribute('data-key-id');
                    if (!keyId || keyId === state.selectedKeyId) {
                        return;
                    }
                    setSecret('');
                    selectKey(keyId);
                }
            });
        }

        if (elements.createForm) {
            elements.createForm.addEventListener('submit', handleCreate);
        }

        if (elements.secretCopy && elements.secretValue) {
            elements.secretCopy.addEventListener('click', () => {
                const value = elements.secretValue.textContent.trim();
                if (!value) {
                    return;
                }
                const showFeedback = () => {
                    elements.secretCopy.textContent = 'Copied';
                    elements.secretCopy.disabled = true;
                    setTimeout(() => {
                        elements.secretCopy.textContent = defaultSecretCopyLabel;
                        elements.secretCopy.disabled = false;
                    }, 1400);
                };
                const copyPromise = navigator.clipboard?.writeText
                    ? navigator.clipboard.writeText(value)
                    : Promise.reject();
                copyPromise.then(showFeedback).catch(() => showFeedback());
            });
        }

        if (elements.refresh) {
            elements.refresh.addEventListener('click', () => {
                clearErrors();
                fetchKeys();
            });
        }

        if (elements.detailForm) {
            elements.detailForm.addEventListener('submit', handleUpdate);
        }

        elements.rotate?.addEventListener('click', handleRotate);
        elements.revoke?.addEventListener('click', handleRevoke);

        if (elements.createOpeners?.length) {
            elements.createOpeners.forEach((button) => {
                button.addEventListener('click', openCreateModal);
            });
        }

        if (elements.createClosers?.length) {
            elements.createClosers.forEach((button) => {
                button.addEventListener('click', closeCreateModal);
            });
        }

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && isCreateModalOpen()) {
                closeCreateModal();
            }
        });

        void fetchKeys();
    };

    const initBilling = () => {
        const root = document.querySelector('[data-billing-root]');
        const statusUrl = body?.dataset.apiBillingStatusUrl;
        const checkoutUrl = body?.dataset.apiBillingCheckoutCryptoUrl;
        const cancelUrl = body?.dataset.apiBillingCancelUrl;
        if (!root || !statusUrl || !checkoutUrl) {
            return;
        }
        const planLabel = root.querySelector('[data-billing-plan]');
        const statusLabel = root.querySelector('[data-billing-status]');
        const errorLabel = root.querySelector('[data-billing-error]');
        const upgradeButton = root.querySelector('[data-billing-upgrade]');
        const modal = document.querySelector('[data-plan-modal]');
        const modalTitle = modal?.querySelector('[data-modal-title]');
        const modalPrice = modal?.querySelector('[data-modal-price]');
        const modalFeatures = modal?.querySelector('[data-modal-features]');
        const modalTrial = modal?.querySelector('[data-modal-trial]');
        const modalConfirm = modal?.querySelector('[data-plan-confirm]');
        const modalError = modal?.querySelector('[data-plan-error]');
        const plansScope = document.querySelector('.billing-plans') || document;
        const carousel = root.querySelector('[data-plans-carousel]');
        const carouselViewport = root.querySelector('[data-carousel-viewport]');
        const carouselTrack = root.querySelector('[data-carousel-track]');
        const prevButton = root.querySelector('[data-carousel-prev]');
        const nextButton = root.querySelector('[data-carousel-next]');
        const parsedCarouselBreakpoint = Number.parseInt(carousel?.dataset.carouselMobileBreakpoint || '', 10);
        const carouselMobileBreakpoint =
            Number.isFinite(parsedCarouselBreakpoint) && parsedCarouselBreakpoint > 0 ? parsedCarouselBreakpoint : 900;
        let selectedPlan = null;
        let currentSubscription = null;
        let carouselEnabled = false;
        const getPlanCards = () => Array.from(plansScope.querySelectorAll('[data-plan-card]'));
        const getVisiblePlanCards = () => getPlanCards().filter((card) => card && card.offsetParent !== null);
        const isCompactCarouselViewport = () => window.matchMedia(`(max-width: ${carouselMobileBreakpoint}px)`).matches;

        plansScope.querySelectorAll('[data-plan-trigger]').forEach((button) => {
            if (!button.dataset.disabledOriginal) {
                button.dataset.disabledOriginal = button.disabled ? 'true' : 'false';
            }
        });

        const readPlanFromCard = (planNode) => ({
            code: planNode.dataset.planCard,
            name: planNode.dataset.planName,
            price: planNode.dataset.planPrice,
            trial: planNode.dataset.planTrial,
            features: (planNode.dataset.planFeatures || '').split('||').filter(Boolean),
        });

        const setCheckoutError = (message) => {
            if (modalError) {
                modalError.textContent = message;
            }
            if (errorLabel) {
                errorLabel.textContent = message;
            }
        };

        const setButtonLoading = (button, isLoading) => {
            if (!button) {
                return;
            }
            button.classList.toggle('account-cta--loading', isLoading);
            if (isLoading) {
                button.setAttribute('aria-busy', 'true');
            } else {
                button.removeAttribute('aria-busy');
            }
        };

        const updateCarouselNav = () => {
            if (!carouselEnabled || !carouselViewport) {
                return;
            }
            const maxScroll = Math.max(0, carouselViewport.scrollWidth - carouselViewport.clientWidth);
            const atStart = carouselViewport.scrollLeft <= 8;
            const atEnd = carouselViewport.scrollLeft >= maxScroll - 8;
            if (prevButton) {
                prevButton.disabled = atStart;
            }
            if (nextButton) {
                nextButton.disabled = atEnd;
            }
        };

        const setCarouselState = (isEnabled) => {
            carouselEnabled = Boolean(isEnabled);
            if (carousel) {
                carousel.dataset.carouselEnabled = carouselEnabled ? 'true' : 'false';
            }
            if (carouselTrack) {
                carouselTrack.dataset.carouselActive = carouselEnabled ? 'true' : 'false';
            }
            if (!carouselViewport) {
                return;
            }
            if (!carouselEnabled) {
                carouselViewport.scrollTo({ left: 0, behavior: 'auto' });
                if (prevButton) {
                    prevButton.disabled = true;
                }
                if (nextButton) {
                    nextButton.disabled = true;
                }
                return;
            }
            updateCarouselNav();
        };

        const getCarouselStep = () => {
            const cards = getVisiblePlanCards();
            if (!cards.length || !carouselTrack) {
                return 0;
            }
            const gap = Number.parseFloat(window.getComputedStyle(carouselTrack).gap || '0') || 0;
            const firstWidth = cards[0].getBoundingClientRect().width;
            return firstWidth + gap;
        };

        const slideCarousel = (direction) => {
            if (!carouselEnabled || !carouselViewport) {
                return;
            }
            const step = getCarouselStep();
            if (!step) {
                return;
            }
            const delta = direction === 'next' ? step : -step;
            carouselViewport.scrollBy({ left: delta, behavior: 'smooth' });
        };

        const refreshCarousel = () => {
            const visibleCards = getVisiblePlanCards().length;
            const shouldEnable = visibleCards > 1 && (isCompactCarouselViewport() || visibleCards > 3);
            setCarouselState(shouldEnable);
            if (carouselEnabled) {
                window.requestAnimationFrame(updateCarouselNav);
            }
        };

        const scrollActivePlanIntoView = ({ behavior = 'auto' } = {}) => {
            if (!carouselViewport || !carouselEnabled || !isCompactCarouselViewport()) {
                return;
            }
            const activeCard = plansScope.querySelector('.billing-plan--current');
            if (!activeCard || activeCard.hidden) {
                return;
            }
            const viewportRect = carouselViewport.getBoundingClientRect();
            const cardRect = activeCard.getBoundingClientRect();
            const maxScroll = Math.max(0, carouselViewport.scrollWidth - carouselViewport.clientWidth);
            const targetLeft = carouselViewport.scrollLeft + (cardRect.left - viewportRect.left);
            const boundedTargetLeft = Math.max(0, Math.min(maxScroll, Math.round(targetLeft)));
            carouselViewport.scrollTo({ left: boundedTargetLeft, behavior });
            window.requestAnimationFrame(updateCarouselNav);
        };

        const extractDetailText = (detail) => {
            if (!detail) {
                return null;
            }
            if (typeof detail === 'string') {
                return detail;
            }
            if (Array.isArray(detail)) {
                const messages = detail
                    .map((item) => {
                        if (typeof item === 'string') {
                            return item;
                        }
                        if (item && typeof item === 'object') {
                            return item.msg || item.message || item.detail;
                        }
                        return null;
                    })
                    .filter(Boolean);
                return messages.length ? messages.join('; ') : null;
            }
            if (typeof detail === 'object') {
                return extractDetailText(detail.detail) || detail.msg || detail.message || null;
            }
            return null;
        };

        const closeModal = () => {
            if (!modal) {
                return;
            }
            modal.hidden = true;
            document.body.classList.remove('is-plan-modal-open');
            selectedPlan = null;
            setCheckoutError('');
        };

        const openModal = (planNode) => {
            if (!modal || !planNode) {
                return;
            }
            selectedPlan = readPlanFromCard(planNode);
            modal.hidden = false;
            document.body.classList.add('is-plan-modal-open');
            modalTitle && (modalTitle.textContent = selectedPlan.name);
            modalPrice && (modalPrice.textContent = selectedPlan.price);
            if (modalTrial) {
                const trialDays = Number(selectedPlan.trial) || 0;
                if (trialDays > 0) {
                    modalTrial.textContent = `Trial: ${trialDays} days`;
                    modalTrial.hidden = false;
                } else {
                    modalTrial.hidden = true;
                }
            }
            if (modalFeatures) {
                modalFeatures.innerHTML = '';
                selectedPlan.features.forEach((feature) => {
                    const li = document.createElement('li');
                    li.textContent = feature;
                    modalFeatures.appendChild(li);
                });
            }
            setCheckoutError('');
        };

        const startCheckout = async (direct = false) => {
            if (!selectedPlan) {
                return;
            }
            if (!direct && modalConfirm) {
                modalConfirm.disabled = true;
                setButtonLoading(modalConfirm, true);
            }
            setCheckoutError('');
            try {
                const response = await authorizedFetch(checkoutUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plan_code: selectedPlan.code }),
                });
                const payload = await response.json().catch(() => ({}));
                const detailMessage = extractDetailText(payload?.detail) || extractDetailText(payload);
                if (!response.ok) {
                    throw new Error(detailMessage || 'Unable to start crypto checkout.');
                }
                if (payload?.hosted_url) {
                    window.location.href = payload.hosted_url;
                    return;
                }
                throw new Error(detailMessage || 'Unable to start crypto checkout.');
            } catch (error) {
                setCheckoutError(error instanceof Error ? error.message : 'Checkout failed to start.');
            } finally {
                if (!direct && modalConfirm) {
                    modalConfirm.disabled = false;
                    setButtonLoading(modalConfirm, false);
                }
            }
        };

        const setCurrentPlanCard = (planCode, options = {}) => {
            const cancelAtPeriodEnd = Boolean(options.cancelAtPeriodEnd);
            getPlanCards().forEach((card) => {
                const isCurrent = card.dataset.planCard === planCode;
                card.classList.toggle('billing-plan--current', isCurrent);
                const badge = card.querySelector('[data-plan-active-badge]');
                if (badge) {
                    badge.hidden = !isCurrent;
                }
                const cta = card.querySelector('[data-plan-cta]');
                const cancelButton = card.querySelector('[data-plan-cancel]');
                const resumeButton = card.querySelector('[data-plan-resume]');
                const cancelNote = card.querySelector('[data-plan-cancel-note]');
                const canCancel =
                    Boolean(cancelUrl) && ['pro', 'ultra'].includes((card.dataset.planCard || '').toLowerCase());
                const shouldShowCancel = isCurrent && canCancel && !cancelAtPeriodEnd;
                const shouldShowResume = isCurrent && canCancel && cancelAtPeriodEnd;
                if (cancelButton) {
                    cancelButton.hidden = !shouldShowCancel;
                    cancelButton.disabled = !shouldShowCancel;
                    if (cancelButton.disabled) {
                        cancelButton.setAttribute('aria-disabled', 'true');
                    } else {
                        cancelButton.removeAttribute('aria-disabled');
                    }
                }
                if (resumeButton) {
                    resumeButton.hidden = !shouldShowResume;
                    resumeButton.disabled = !shouldShowResume;
                    if (resumeButton.disabled) {
                        resumeButton.setAttribute('aria-disabled', 'true');
                    } else {
                        resumeButton.removeAttribute('aria-disabled');
                    }
                }
                if (cancelNote) {
                    cancelNote.hidden = !(isCurrent && cancelAtPeriodEnd);
                }
                if (cta) {
                    cta.hidden = false;
                }
                const trialPill = card.querySelector('[data-plan-trial-pill]');
                if (trialPill) {
                    trialPill.hidden = isCurrent;
                }
                const trigger = card.querySelector('[data-plan-trigger]');
                if (trigger) {
                    const originallyDisabled = trigger.dataset.disabledOriginal === 'true';
                    trigger.disabled = originallyDisabled || isCurrent;
                    trigger.hidden = isCurrent;
                    if (trigger.disabled) {
                        trigger.setAttribute('aria-disabled', 'true');
                    } else {
                        trigger.removeAttribute('aria-disabled');
                        trigger.removeAttribute('title');
                    }
                }
            });
        };

        const setFreePlanVisibility = ({ activePlanCode, cancelAtPeriodEnd }) => {
            const freeCard = plansScope.querySelector('[data-plan-card="free"]');
            if (!freeCard) {
                return;
            }
            const shouldHide = Boolean(activePlanCode && activePlanCode !== 'free' && !cancelAtPeriodEnd);
            freeCard.hidden = shouldHide;
            if (shouldHide) {
                freeCard.setAttribute('aria-hidden', 'true');
            } else {
                freeCard.removeAttribute('aria-hidden');
            }
        };

        const fetchStatus = async () => {
            try {
                const response = await authorizedFetch(statusUrl);
                const payload = await response.json().catch(() => ({}));
                const subscription = payload?.subscription;
                currentSubscription = subscription || null;
                const planCode = (subscription?.plan_code || 'free').toLowerCase();
                planLabel && (planLabel.textContent = planCode ? planCode.toUpperCase() : 'FREE');
                const cancelAtPeriodEnd = Boolean(subscription?.cancel_at_period_end);
                setFreePlanVisibility({ activePlanCode: planCode, cancelAtPeriodEnd });
                setCurrentPlanCard(planCode, { cancelAtPeriodEnd });
                const statusText = subscription?.status || payload?.account_status || 'unknown';
                statusLabel && (statusLabel.textContent = cancelAtPeriodEnd ? `${statusText} (canceling)` : statusText);
                refreshCarousel();
                scrollActivePlanIntoView();
            } catch (error) {
                errorLabel && (errorLabel.textContent = error instanceof Error ? error.message : 'Failed to load billing status.');
            }
        };

        const updatePlanRenewal = async (planCode, triggerButton, options = {}) => {
            const resume = Boolean(options.resume);
            if (!cancelUrl) {
                return;
            }
            if (triggerButton) {
                setButtonLoading(triggerButton, true);
            }
            setCheckoutError('');
            try {
                const response = await authorizedFetch(cancelUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ...(planCode ? { plan_code: planCode } : {}),
                        ...(resume ? { resume: true } : {}),
                    }),
                });
                const payload = await response.json().catch(() => ({}));
                const detailMessage = extractDetailText(payload?.detail) || extractDetailText(payload);
                if (!response.ok) {
                    throw new Error(detailMessage || (resume ? 'Unable to resume plan.' : 'Unable to cancel renewal.'));
                }
                const subscription = payload?.subscription || currentSubscription;
                currentSubscription = subscription || null;
                const cancelAtPeriodEnd = Boolean(subscription?.cancel_at_period_end);
                const planCodeResolved = (subscription?.plan_code || planCode || 'free').toLowerCase();
                setCurrentPlanCard(planCodeResolved, { cancelAtPeriodEnd });
                const statusText = subscription?.status || (statusLabel?.textContent || 'active');
                statusLabel &&
                    (statusLabel.textContent = cancelAtPeriodEnd ? `${statusText} (canceling)` : statusText);
                setFreePlanVisibility({ activePlanCode: planCodeResolved, cancelAtPeriodEnd });
                refreshCarousel();
                scrollActivePlanIntoView();
                if (errorLabel) {
                    errorLabel.textContent = '';
                }
            } catch (error) {
                setCheckoutError(
                    error instanceof Error
                        ? error.message
                        : resume
                          ? 'Failed to resume plan.'
                          : 'Failed to cancel plan.',
                );
            } finally {
                if (triggerButton) {
                    setButtonLoading(triggerButton, false);
                }
            }
        };

        const handlePlanSelection = async (planCode, triggerButton) => {
            const planCard = plansScope.querySelector(`[data-plan-card="${planCode}"]`);
            if (!planCard) {
                return;
            }
            selectedPlan = readPlanFromCard(planCard);
            const ctaButton = triggerButton || planCard.querySelector('[data-plan-trigger]');
            if (ctaButton) {
                ctaButton.disabled = true;
                ctaButton.setAttribute('aria-disabled', 'true');
                setButtonLoading(ctaButton, true);
            }
            try {
                await startCheckout(true);
            } finally {
                setButtonLoading(ctaButton, false);
                if (ctaButton) {
                    const originallyDisabled = ctaButton.dataset.disabledOriginal === 'true';
                    const isCurrent = planCard.classList.contains('billing-plan--current');
                    ctaButton.disabled = originallyDisabled || isCurrent;
                    if (!ctaButton.disabled) {
                        ctaButton.removeAttribute('aria-disabled');
                    }
                }
            }
        };

        plansScope.querySelectorAll('[data-plan-trigger]').forEach((button) => {
            button.addEventListener('click', (event) => {
                const planCode = event.currentTarget.getAttribute('data-plan-trigger');
                void handlePlanSelection(planCode, event.currentTarget);
            });
        });
        plansScope.querySelectorAll('[data-plan-cancel]').forEach((button) => {
            button.addEventListener('click', (event) => {
                const planCode = event.currentTarget.getAttribute('data-plan-cancel');
                void updatePlanRenewal(planCode, event.currentTarget);
            });
        });
        plansScope.querySelectorAll('[data-plan-resume]').forEach((button) => {
            button.addEventListener('click', (event) => {
                const planCode = event.currentTarget.getAttribute('data-plan-resume');
                void updatePlanRenewal(planCode, event.currentTarget, { resume: true });
            });
        });
        upgradeButton?.addEventListener('click', () => {
            const firstPlan = root.querySelector('[data-plan-card]');
            if (firstPlan) {
                const planCode = firstPlan.getAttribute('data-plan-card');
                void handlePlanSelection(planCode);
            }
        });
        modal?.querySelectorAll('[data-plan-close]').forEach((button) => button.addEventListener('click', closeModal));
        modalConfirm?.addEventListener('click', startCheckout);
        carouselViewport?.addEventListener('scroll', () => {
            if (!carouselEnabled) {
                return;
            }
            updateCarouselNav();
        });
        prevButton?.addEventListener('click', () => slideCarousel('prev'));
        nextButton?.addEventListener('click', () => slideCarousel('next'));
        if (carouselViewport) {
            window.addEventListener('resize', () => {
                refreshCarousel();
                scrollActivePlanIntoView();
            });
        }
        refreshCarousel();
        fetchStatus();
    };

    const usageSparklineTouchPointerTypes = new Set(['touch', 'pen']);

    const parseUsageSparklinePoints = (rawPoints) => {
        if (Array.isArray(rawPoints)) {
            return rawPoints;
        }
        if (typeof rawPoints !== 'string' || !rawPoints.trim()) {
            return [];
        }
        try {
            const parsed = JSON.parse(rawPoints);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    };

    const parseUsageSparklineViewBox = (chart, svg) => {
        const rawViewBox = (svg?.getAttribute('viewBox') || chart?.dataset?.viewbox || '').trim();
        const parts = rawViewBox.split(/\s+/).map((part) => Number(part));
        const [minXRaw, minYRaw, widthRaw, heightRaw] = parts.length >= 4 ? parts : [0, 0, Number.NaN, Number.NaN];
        const minX = Number.isFinite(minXRaw) ? minXRaw : 0;
        const minY = Number.isFinite(minYRaw) ? minYRaw : 0;
        const widthFallback = Math.max(svg?.clientWidth || 0, chart?.clientWidth || 0, 1);
        const heightFallback = Math.max(svg?.clientHeight || 0, chart?.clientHeight || 0, 1);
        const width = Number.isFinite(widthRaw) && widthRaw > 0 ? widthRaw : widthFallback;
        const height = Number.isFinite(heightRaw) && heightRaw > 0 ? heightRaw : heightFallback;
        return { minX, minY, width, height };
    };

    const initUsageSparkline = (chart) => {
        if (!(chart instanceof HTMLElement)) {
            return null;
        }
        if (chart.__usageSparklineApi) {
            return chart.__usageSparklineApi;
        }
        const svg = chart.querySelector('svg');
        const line = chart.querySelector('[data-usage-sparkline-line]');
        const marker = chart.querySelector('[data-usage-sparkline-marker]');
        const tooltip = chart.querySelector('[data-usage-sparkline-tooltip]');
        if (!svg || !line || !tooltip) {
            return null;
        }
        const tooltipDateNode = tooltip.querySelector('[data-usage-sparkline-date]');
        const tooltipRequestsNode = tooltip.querySelector('[data-usage-sparkline-requests]');
        const tooltipErrorsNode = tooltip.querySelector('[data-usage-sparkline-errors]');
        const tooltipErrorRateNode = tooltip.querySelector('[data-usage-sparkline-error-rate]');
        const markerMode =
            typeof chart.dataset.usageSparklineMarkerMode === 'string'
                ? chart.dataset.usageSparklineMarkerMode.trim().toLowerCase()
                : '';
        const isHoverMarkerMode = markerMode === 'hover';
        const state = {
            points: [],
            activeTouchPointerId: null,
            touchTooltipPinned: false,
        };

        const toNumberOrNull = (value) => {
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
        };

        const clamp = (value, min, max) => {
            if (max < min) {
                return min;
            }
            return Math.min(Math.max(value, min), max);
        };

        const toDisplayCount = (value) => {
            if (!Number.isFinite(value)) {
                return '-';
            }
            return Math.round(value).toLocaleString();
        };

        const toDisplayRate = (value) => {
            if (!Number.isFinite(value)) {
                return '-';
            }
            return `${(value * 100).toFixed(2)}%`;
        };

        const getViewBox = () => parseUsageSparklineViewBox(chart, svg);

        const applyLinePoints = (points) => {
            if (!line) {
                return;
            }
            const tagName = line.tagName.toLowerCase();
            if (!points.length) {
                if (tagName === 'polyline') {
                    line.setAttribute('points', '');
                    return;
                }
                if (tagName === 'path') {
                    line.setAttribute('d', '');
                }
                return;
            }
            if (tagName === 'polyline') {
                line.setAttribute(
                    'points',
                    points
                        .map((point) => `${point.x},${point.y}`)
                        .join(' '),
                );
                return;
            }
            if (tagName === 'path') {
                line.setAttribute(
                    'd',
                    points
                        .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.y}`)
                        .join(' '),
                );
            }
        };

        const setMarkerPoint = (point) => {
            if (!marker) {
                return;
            }
            if (!point) {
                marker.setAttribute('hidden', '');
                return;
            }
            marker.removeAttribute('hidden');
            marker.setAttribute('cx', String(point.x));
            marker.setAttribute('cy', String(point.y));
        };

        const resetMarker = () => {
            if (!state.points.length || isHoverMarkerMode) {
                setMarkerPoint(null);
                return;
            }
            setMarkerPoint(state.points[state.points.length - 1]);
        };

        const hideTooltip = () => {
            tooltip.classList.remove('is-visible');
            tooltip.hidden = true;
            resetMarker();
        };

        const renderTooltip = (point, rect, viewBox) => {
            if (!point || !rect || rect.width <= 0 || rect.height <= 0 || viewBox.width <= 0 || viewBox.height <= 0) {
                return;
            }
            if (tooltipDateNode) {
                tooltipDateNode.textContent = point.dateLabel || point.isoDate || '';
            }
            if (tooltipRequestsNode) {
                tooltipRequestsNode.textContent = toDisplayCount(point.requests);
            }
            if (tooltipErrorsNode) {
                tooltipErrorsNode.textContent = toDisplayCount(point.errors);
            }
            if (tooltipErrorRateNode) {
                tooltipErrorRateNode.textContent = toDisplayRate(point.errorRate);
            }
            tooltip.hidden = false;
            const normalizedX = (point.x - viewBox.minX) / viewBox.width;
            const normalizedY = (point.y - viewBox.minY) / viewBox.height;
            const pixelX = normalizedX * rect.width;
            const pixelY = normalizedY * rect.height;
            const tooltipWidth = tooltip.offsetWidth || 0;
            const tooltipHeight = tooltip.offsetHeight || 0;
            const safeRight = Math.max(8, rect.width - tooltipWidth - 8);
            const safeBottom = Math.max(8, rect.height - tooltipHeight - 8);
            const preferredTop = pixelY - tooltipHeight - 12;
            const fallbackTop = pixelY + 12;
            let left = clamp(pixelX - tooltipWidth / 2, 8, safeRight);
            let top = clamp(preferredTop >= 8 ? preferredTop : fallbackTop, 8, safeBottom);

            const markerRadius = marker ? Number(marker.getAttribute('r')) || 4 : 4;
            const markerPadding = markerRadius + 6;
            const isPointInsideTooltip =
                pixelX >= left - markerPadding &&
                pixelX <= left + tooltipWidth + markerPadding &&
                pixelY >= top - markerPadding &&
                pixelY <= top + tooltipHeight + markerPadding;
            if (isPointInsideTooltip) {
                const sideOffset = markerRadius + 14;
                const canPlaceRight = pixelX + sideOffset + tooltipWidth <= rect.width - 8;
                const canPlaceLeft = pixelX - sideOffset - tooltipWidth >= 8;
                if (canPlaceRight || canPlaceLeft) {
                    left = canPlaceRight
                        ? clamp(pixelX + sideOffset, 8, safeRight)
                        : clamp(pixelX - tooltipWidth - sideOffset, 8, safeRight);
                    top = clamp(pixelY - tooltipHeight / 2, 8, safeBottom);
                }
            }
            tooltip.style.left = `${left}px`;
            tooltip.style.top = `${top}px`;
            tooltip.classList.add('is-visible');
            setMarkerPoint(point);
        };

        const normalizePoint = (point, index, sourcePoints, viewBox, maxRequests) => {
            const pointsCount = sourcePoints.length;
            const step = viewBox.width / Math.max(pointsCount - 1, 1);
            const requestsRaw = toNumberOrNull(point?.requests);
            const callCountRaw = toNumberOrNull(point?.call_count);
            const requests = requestsRaw ?? callCountRaw ?? 0;
            const errorsRaw = toNumberOrNull(point?.errors);
            const errorCountRaw = toNumberOrNull(point?.error_count);
            const errors = errorsRaw ?? errorCountRaw ?? 0;
            const providedRate = toNumberOrNull(point?.error_rate);
            const errorRate = providedRate ?? (requests > 0 ? errors / requests : 0);
            const xRaw = toNumberOrNull(point?.x);
            const yRaw = toNumberOrNull(point?.y);
            const normalizedRequests = maxRequests > 0 ? clamp(requests / maxRequests, 0, 1) : 0;
            const computedY = viewBox.minY + viewBox.height - normalizedRequests * (viewBox.height - 8) - 4;
            const dateValue = typeof point?.date === 'string' ? point.date.trim() : '';
            const isoDateValue = typeof point?.iso_date === 'string' ? point.iso_date.trim() : '';
            return {
                x: xRaw ?? viewBox.minX + index * step,
                y: yRaw ?? computedY,
                requests,
                errors,
                errorRate,
                dateLabel: dateValue,
                isoDate: isoDateValue,
            };
        };

        const updateTooltipByClientX = (clientX) => {
            if (!state.points.length || !Number.isFinite(clientX)) {
                return false;
            }
            const rect = chart.getBoundingClientRect();
            const viewBox = getViewBox();
            if (!rect.width || !rect.height || !viewBox.width || !viewBox.height) {
                return false;
            }
            const relativeX = clamp(clientX - rect.left, 0, rect.width);
            const svgX = viewBox.minX + (relativeX / rect.width) * viewBox.width;
            let nearestPoint = state.points[0];
            let minDistance = Math.abs(state.points[0].x - svgX);
            for (let index = 1; index < state.points.length; index += 1) {
                const candidate = state.points[index];
                const distance = Math.abs(candidate.x - svgX);
                if (distance < minDistance) {
                    minDistance = distance;
                    nearestPoint = candidate;
                }
            }
            renderTooltip(nearestPoint, rect, viewBox);
            return true;
        };

        const isTouchLikePointer = (event) => {
            const pointerType = typeof event?.pointerType === 'string' ? event.pointerType.toLowerCase() : '';
            return usageSparklineTouchPointerTypes.has(pointerType);
        };

        const clearTouchState = () => {
            state.activeTouchPointerId = null;
            state.touchTooltipPinned = false;
            hideTooltip();
        };

        const handleMousePointerMove = (event) => {
            if (isTouchLikePointer(event)) {
                return;
            }
            state.touchTooltipPinned = false;
            updateTooltipByClientX(event.clientX);
        };

        const handleMousePointerLeave = (event) => {
            if (isTouchLikePointer(event) || state.touchTooltipPinned) {
                return;
            }
            hideTooltip();
        };

        const handleTouchPointerDown = (event) => {
            if (!isTouchLikePointer(event) || !state.points.length) {
                return;
            }
            state.activeTouchPointerId = event.pointerId;
            state.touchTooltipPinned = true;
            if (typeof chart.setPointerCapture === 'function') {
                try {
                    chart.setPointerCapture(event.pointerId);
                } catch (_) {
                    /* ignore capture errors */
                }
            }
            updateTooltipByClientX(event.clientX);
        };

        const handleTouchPointerMove = (event) => {
            if (!isTouchLikePointer(event) || state.activeTouchPointerId !== event.pointerId) {
                return;
            }
            state.touchTooltipPinned = true;
            updateTooltipByClientX(event.clientX);
        };

        const handleTouchPointerUp = (event) => {
            if (!isTouchLikePointer(event) || state.activeTouchPointerId !== event.pointerId) {
                return;
            }
            if (
                typeof chart.hasPointerCapture === 'function' &&
                typeof chart.releasePointerCapture === 'function'
            ) {
                try {
                    if (chart.hasPointerCapture(event.pointerId)) {
                        chart.releasePointerCapture(event.pointerId);
                    }
                } catch (_) {
                    /* ignore invalid capture state */
                }
            }
            state.activeTouchPointerId = null;
        };

        const handleTouchPointerCancel = (event) => {
            if (!isTouchLikePointer(event)) {
                return;
            }
            if (state.activeTouchPointerId !== null && state.activeTouchPointerId !== event.pointerId) {
                return;
            }
            clearTouchState();
        };

        const handleGlobalPointerDown = (event) => {
            if (!state.touchTooltipPinned) {
                return;
            }
            const target = event.target;
            if (!(target instanceof Node)) {
                return;
            }
            if (chart.contains(target)) {
                return;
            }
            clearTouchState();
        };

        const handleGlobalKeydown = (event) => {
            if (event.key !== 'Escape' || !state.touchTooltipPinned) {
                return;
            }
            clearTouchState();
        };

        const handleGlobalViewportChange = () => {
            if (!state.touchTooltipPinned) {
                return;
            }
            clearTouchState();
        };

        const setPoints = (nextPoints) => {
            const sourcePoints = parseUsageSparklinePoints(nextPoints);
            const viewBox = getViewBox();
            const maxRequests = sourcePoints.reduce((maxValue, point) => {
                const requestsRaw = toNumberOrNull(point?.requests);
                const callCountRaw = toNumberOrNull(point?.call_count);
                const value = requestsRaw ?? callCountRaw ?? 0;
                return Math.max(maxValue, value);
            }, 0);
            state.points = sourcePoints.map((point, index) =>
                normalizePoint(point, index, sourcePoints, viewBox, Math.max(maxRequests, 1)),
            );
            applyLinePoints(state.points);
            state.activeTouchPointerId = null;
            state.touchTooltipPinned = false;
            hideTooltip();
        };

        chart.style.touchAction = 'pan-y';
        chart.addEventListener('pointerenter', handleMousePointerMove);
        chart.addEventListener('pointermove', handleMousePointerMove);
        chart.addEventListener('pointerdown', handleMousePointerMove);
        chart.addEventListener('pointerdown', handleTouchPointerDown);
        chart.addEventListener('pointermove', handleTouchPointerMove);
        chart.addEventListener('pointerup', handleTouchPointerUp);
        chart.addEventListener('pointercancel', handleTouchPointerCancel);
        chart.addEventListener('pointerleave', handleMousePointerLeave);
        document.addEventListener('pointerdown', handleGlobalPointerDown, true);
        document.addEventListener('keydown', handleGlobalKeydown);
        window.addEventListener('scroll', handleGlobalViewportChange, { passive: true });
        window.addEventListener('resize', handleGlobalViewportChange);
        window.addEventListener('orientationchange', handleGlobalViewportChange);

        const api = {
            setPoints,
        };
        chart.__usageSparklineApi = api;
        setPoints(chart.dataset.points || '[]');
        return api;
    };

    const initUsageSparklines = (scope = document) => {
        if (!scope || typeof scope.querySelectorAll !== 'function') {
            return;
        }
        scope.querySelectorAll('[data-usage-sparkline]').forEach((chart) => {
            initUsageSparkline(chart);
        });
    };

    const initUsage = () => {
        const root = document.querySelector('[data-usage-root]');
        const summaryUrl = body?.dataset.apiUsageSummaryUrl;
        const errorsUrl = body?.dataset.apiUsageErrorsUrl;
        const exportUrl = body?.dataset.apiUsageExportUrl;
        const alertsUrl = body?.dataset.apiUsageAlertsUrl;
        const alertsUpdateUrl = body?.dataset.apiUsageAlertsUpdateUrl;
        if (!root || !summaryUrl || !errorsUrl || !exportUrl || !alertsUrl || !alertsUpdateUrl) {
            return;
        }
        const totalLabel = root.querySelector('[data-usage-total]');
        const maxLabel = root.querySelector('[data-usage-max]');
        const errorsLabel = root.querySelector('[data-usage-errors]');
        const monthlyLabel = root.querySelector('[data-usage-monthly]');
        const avgLabel = root.querySelector('[data-usage-avg]');
        const errorsTotalLabel = root.querySelector('[data-usage-errors-total]');
        const planLabel = root.querySelector('[data-usage-plan]');
        const usageSparkline = initUsageSparkline(
            root.querySelector('[data-usage-sparkline="usage-trend"]') || root.querySelector('[data-usage-sparkline]'),
        );
        const errorsTable = root.querySelector('[data-usage-errors-table]');
        const rangeButtons = root.querySelectorAll('[data-usage-range]');
        const exportButton = root.querySelector('[data-usage-export]');
        const alertsForm = root.querySelector('[data-usage-alerts-form]');
        const alertsList = alertsForm?.querySelector('[data-alerts-list]');
        const alertsStatus = alertsForm?.querySelector('[data-alert-status]');
        const addAlertButton = alertsForm?.querySelector('[data-alert-add]');
        const alertTemplate = document.getElementById('usage-alert-row-template');
        let currentRange = Number(rangeButtons[0]?.dataset.usageRange || 30);
        const destinationCopy = {
            email: {
                placeholder: 'alerts@example.com',
            },
            slack: {
                placeholder: 'https://hooks.slack.com/services/...',
            },
        };
        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        const getDestinationCopy = (channelValue) => destinationCopy[channelValue] || destinationCopy.email;
        const isValidEmail = (value) => emailPattern.test(value);
        const isValidUrl = (value) => {
            try {
                const url = new URL(value);
                return url.protocol === 'http:' || url.protocol === 'https:';
            } catch (_) {
                return false;
            }
        };
        const isValidThreshold = (value) =>
            Number.isFinite(value) && Number.isInteger(value) && value >= 10 && value <= 1000;

        const clearAlertRowErrors = (row) => {
            if (!row) {
                return;
            }
            row.querySelectorAll('[data-alert-error]').forEach((node) => {
                node.textContent = '';
            });
            row.querySelectorAll('.usage-alert__field.is-invalid').forEach((field) => {
                field.classList.remove('is-invalid');
            });
        };

        const setAlertFieldError = (row, fieldKey, message) => {
            if (!row || !fieldKey || !message) {
                return;
            }
            const fieldError = row.querySelector(`[data-alert-error="${fieldKey}"]`);
            if (!fieldError) {
                return;
            }
            fieldError.textContent = message;
            const field = fieldError.closest('.usage-alert__field');
            field?.classList.add('is-invalid');
        };

        const parseAlertFieldErrors = (detail) => {
            const errors = new Map();
            if (!Array.isArray(detail)) {
                return errors;
            }
            detail.forEach((item) => {
                if (!item || typeof item !== 'object') {
                    return;
                }
                const loc = item.loc;
                if (!Array.isArray(loc)) {
                    return;
                }
                const alertsIndex = loc.indexOf('alerts');
                if (alertsIndex === -1) {
                    return;
                }
                const rowIndex = loc[alertsIndex + 1];
                const field = loc[alertsIndex + 2];
                if (typeof rowIndex !== 'number' || typeof field !== 'string') {
                    return;
                }
                const messageSource = item.msg || item.message || item.detail;
                const message = typeof messageSource === 'string' ? messageSource.trim() : '';
                if (!message) {
                    return;
                }
                const normalizedField =
                    field === 'threshold_percent' ? 'threshold' : field === 'channel_type' ? 'channel' : field;
                if (!errors.has(rowIndex)) {
                    errors.set(rowIndex, {});
                }
                const rowErrors = errors.get(rowIndex);
                if (!rowErrors[normalizedField]) {
                    rowErrors[normalizedField] = message;
                }
            });
            return errors;
        };

        const applyAlertFieldErrors = (detail, entries) => {
            const errors = parseAlertFieldErrors(detail);
            if (!errors.size) {
                return false;
            }
            errors.forEach((rowErrors, index) => {
                const entry = entries[index];
                if (!entry) {
                    return;
                }
                Object.entries(rowErrors).forEach(([fieldKey, message]) => {
                    setAlertFieldError(entry.row, fieldKey, message);
                });
            });
            return true;
        };

        const normalizeAlertStatus = (detail, fallbackMessage) => {
            if (typeof detail === 'string') {
                const trimmed = detail.trim();
                return trimmed || fallbackMessage;
            }
            if (Array.isArray(detail)) {
                const parts = detail
                    .map((item) => {
                        if (typeof item === 'string') {
                            return item.trim();
                        }
                        if (item && typeof item === 'object') {
                            const candidate = item.message || item.detail || item.error;
                            if (typeof candidate === 'string') {
                                return candidate.trim();
                            }
                        }
                        return '';
                    })
                    .filter(Boolean);
                return parts.length ? parts.join(', ') : fallbackMessage;
            }
            if (detail && typeof detail === 'object') {
                const candidate = detail.message || detail.detail || detail.error;
                if (typeof candidate === 'string') {
                    const trimmed = candidate.trim();
                    if (trimmed) {
                        return trimmed;
                    }
                }
                return fallbackMessage;
            }
            return fallbackMessage;
        };

        const setAlertsStatus = (message) => {
            if (!alertsStatus) {
                return;
            }
            const text = typeof message === 'string' ? message.trim() : '';
            alertsStatus.textContent = text;
            alertsStatus.hidden = !text;
        };

        setAlertsStatus('');

        const renderChart = (points) => {
            usageSparkline?.setPoints(points);
        };

        const renderErrors = (items) => {
            if (!errorsTable) {
                return;
            }
            errorsTable.innerHTML = '';
            const toDisplayText = (value, fallback = '-') => {
                if (value === null || value === undefined) {
                    return fallback;
                }
                const text = String(value).trim();
                return text || fallback;
            };
            const toDisplayDateTime = (value) => {
                const text = toDisplayText(value, '');
                if (!text) {
                    return '-';
                }
                const parsed = new Date(text);
                if (Number.isNaN(parsed.getTime())) {
                    return text;
                }
                return parsed.toLocaleString();
            };
            const createCell = (label, content, className = '') => {
                const cell = document.createElement('td');
                cell.dataset.label = label;
                if (className) {
                    cell.className = className;
                }
                if (typeof content === 'string') {
                    cell.textContent = content;
                } else if (content instanceof Node) {
                    cell.appendChild(content);
                }
                return cell;
            };
            if (!items.length) {
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 5;
                cell.textContent = 'No errors recorded.';
                row.appendChild(cell);
                errorsTable.appendChild(row);
                return;
            }
            items.forEach((item) => {
                const row = document.createElement('tr');
                const endpointWrap = document.createElement('div');
                endpointWrap.className = 'usage-table__endpoint';
                const endpointMain = document.createElement('span');
                endpointMain.className = 'usage-table__endpoint-main';
                endpointMain.textContent = toDisplayText(item.route_path);
                endpointWrap.appendChild(endpointMain);
                const routeName = toDisplayText(item.route_name, '');
                if (routeName && routeName !== endpointMain.textContent) {
                    const endpointMeta = document.createElement('span');
                    endpointMeta.className = 'usage-table__endpoint-meta';
                    endpointMeta.textContent = routeName;
                    endpointWrap.appendChild(endpointMeta);
                }
                row.appendChild(createCell('Endpoint', endpointWrap));
                row.appendChild(createCell('Status', toDisplayText(item.status_code)));
                row.appendChild(createCell('Error code', toDisplayText(item.error_code)));
                row.appendChild(createCell('Occurrences', toDisplayText(item.occurrences)));
                row.appendChild(createCell('Last seen', toDisplayDateTime(item.last_seen)));
                errorsTable.appendChild(row);
            });
        };

        const fetchUsage = async () => {
            try {
                const response = await authorizedFetch(`${summaryUrl}?window_days=${currentRange}`);
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload?.detail || 'Failed to load usage.');
                }
                const points = payload?.points || [];
                const totals = payload?.totals || {};
                totalLabel && (totalLabel.textContent = totals.total_calls ?? 0);
                maxLabel && (maxLabel.textContent = totals.max_calls ?? 0);
                errorsLabel && (errorsLabel.textContent = totals.total_errors ?? 0);
                avgLabel && (avgLabel.textContent = totals.average_per_day ?? 0);
                errorsTotalLabel && (errorsTotalLabel.textContent = totals.total_errors ?? 0);
                planLabel && (planLabel.textContent = `${totals.plan_code?.toUpperCase() || 'PLAN'} plan`);
                if (monthlyLabel) {
                    const quota = totals.monthly_quota;
                    monthlyLabel.textContent = quota ? `${totals.monthly_usage || 0} / ${quota}` : `${totals.monthly_usage || 0}`;
                }
                renderChart(points);
            } catch (error) {
                console.error(error);
            }
        };

        const fetchUsageErrors = async () => {
            try {
                const response = await authorizedFetch(`${errorsUrl}?window_days=${currentRange}`);
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload?.detail || 'Failed to load errors.');
                }
                renderErrors(payload?.errors || []);
            } catch (_) {
                renderErrors([]);
            }
        };

        const addAlertRow = (preset) => {
            if (!alertTemplate || !alertsList) {
                return;
            }
            const fragment = alertTemplate.content.cloneNode(true);
            const row = fragment.querySelector('[data-alert="row"]');
            if (!row) {
                return;
            }
            const detailsRow = row.querySelector('.usage-alert');
            const channelField = row.querySelector('[data-alert-channel]');
            const destinationField = row.querySelector('[data-alert-destination]');
            const thresholdField = row.querySelector('[data-alert-threshold]');
            const enabledField = row.querySelector('[data-alert-enabled]');
            const removeButton = row.querySelector('[data-alert-remove]');
            const summaryChannel = row.querySelector('[data-alert-summary-channel]');
            const summaryDestination = row.querySelector('[data-alert-summary-destination]');
            const summaryThreshold = row.querySelector('[data-alert-summary-threshold]');
            const summaryStatus = row.querySelector('[data-alert-summary-status]');

            const updateDestinationCopy = () => {
                const channelValue = channelField?.value || 'email';
                const copy = getDestinationCopy(channelValue);
                if (destinationField) {
                    destinationField.placeholder = copy.placeholder;
                }
            };

            const updateSummary = () => {
                const channelLabel = channelField?.selectedOptions?.[0]?.textContent?.trim() || channelField?.value || 'Channel';
                const destinationValue = destinationField?.value?.trim() || 'Add destination';
                const thresholdValue = Number(thresholdField?.value) || 80;
                const isEnabled = Boolean(enabledField?.checked);
                if (summaryChannel) {
                    summaryChannel.textContent = channelLabel;
                }
                if (summaryDestination) {
                    summaryDestination.textContent = destinationValue;
                }
                if (summaryThreshold) {
                    summaryThreshold.textContent = `${thresholdValue}%`;
                }
                if (summaryStatus) {
                    summaryStatus.textContent = isEnabled ? 'Enabled' : 'Disabled';
                    summaryStatus.classList.toggle('is-disabled', !isEnabled);
                }
            };
            if (preset) {
                channelField.value = preset.channel_type || 'email';
                destinationField.value = preset.destination || '';
                thresholdField.value = preset.threshold_percent || 80;
                enabledField.checked = Boolean(preset.enabled);
            }
            updateDestinationCopy();
            updateSummary();
            if (detailsRow instanceof HTMLDetailsElement) {
                detailsRow.open = !(destinationField?.value?.trim() || '');
            }
            channelField?.addEventListener('change', () => {
                clearAlertRowErrors(row);
                updateDestinationCopy();
                updateSummary();
            });
            destinationField?.addEventListener('input', () => {
                clearAlertRowErrors(row);
                updateSummary();
            });
            thresholdField?.addEventListener('input', () => {
                clearAlertRowErrors(row);
                updateSummary();
            });
            enabledField?.addEventListener('change', () => {
                clearAlertRowErrors(row);
                updateSummary();
            });
            removeButton?.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                const confirmMessage = removeButton?.dataset?.confirm || 'Remove this alert?';
                const shouldRemove = typeof window.confirm === 'function' ? window.confirm(confirmMessage) : true;
                if (!shouldRemove) {
                    return;
                }
                row.remove();
                setAlertsStatus('Alert removed. Save alerts to persist.');
            });
            alertsList.appendChild(row);
        };

        const fetchAlerts = async () => {
            try {
                const response = await authorizedFetch(alertsUrl);
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload?.detail || 'Failed to load alerts.');
                }
                alertsList.innerHTML = '';
                const alerts = payload?.alerts || [];
                if (!alerts.length) {
                    addAlertRow();
                    return;
                }
                alerts.forEach((alert) => addAlertRow(alert));
            } catch (_) {
                alertsList.innerHTML = '';
                addAlertRow();
            }
        };

        const handleAlertsSubmit = async (event) => {
            event.preventDefault();
            if (!alertsList) {
                return;
            }
            const rows = alertsList.querySelectorAll('[data-alert="row"]');
            const rowEntries = Array.from(rows).map((row) => {
                const channel = row.querySelector('[data-alert-channel]');
                const destination = row.querySelector('[data-alert-destination]');
                const threshold = row.querySelector('[data-alert-threshold]');
                const enabled = row.querySelector('[data-alert-enabled]');
                const thresholdRaw = threshold?.value ?? '';
                const thresholdValue = thresholdRaw === '' ? Number.NaN : Number(thresholdRaw);
                return {
                    row,
                    channelValue: channel?.value || 'email',
                    destinationValue: destination?.value?.trim() || '',
                    thresholdValue,
                    enabledValue: Boolean(enabled?.checked),
                };
            });
            rowEntries.forEach((entry) => clearAlertRowErrors(entry.row));
            let hasClientErrors = false;
            rowEntries.forEach((entry) => {
                if (!entry.destinationValue) {
                    return;
                }
                if (entry.channelValue === 'email') {
                    if (!isValidEmail(entry.destinationValue)) {
                        setAlertFieldError(entry.row, 'destination', 'Enter a valid email address.');
                        hasClientErrors = true;
                    }
                } else if (!isValidUrl(entry.destinationValue)) {
                    setAlertFieldError(entry.row, 'destination', 'Enter a valid URL.');
                    hasClientErrors = true;
                }
                if (!isValidThreshold(entry.thresholdValue)) {
                    setAlertFieldError(entry.row, 'threshold', 'Enter a whole number between 10 and 1000.');
                    hasClientErrors = true;
                }
            });
            if (hasClientErrors) {
                setAlertsStatus('Fix the highlighted fields.');
                return;
            }
            const payloadEntries = rowEntries
                .filter((entry) => entry.destinationValue)
                .map((entry) => ({
                    row: entry.row,
                    payload: {
                        channel_type: entry.channelValue,
                        destination: entry.destinationValue,
                        threshold_percent: entry.thresholdValue,
                        enabled: entry.enabledValue,
                    },
                }));
            const alertsPayload = payloadEntries.map((entry) => entry.payload);
            setAlertsStatus('Saving alerts...');
            try {
                const response = await authorizedFetch(alertsUpdateUrl, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ alerts: alertsPayload }),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    if (applyAlertFieldErrors(payload?.detail, payloadEntries)) {
                        setAlertsStatus('Fix the highlighted fields.');
                        return;
                    }
                    throw new Error(normalizeAlertStatus(payload?.detail, 'Unable to save alerts.'));
                }
                setAlertsStatus('Alerts saved.');
            } catch (error) {
                const fallbackMessage = 'Unable to save alerts.';
                const message =
                    error instanceof Error
                        ? normalizeAlertStatus(error.message, fallbackMessage)
                        : normalizeAlertStatus(error, fallbackMessage);
                setAlertsStatus(message);
            }
        };

        const handleExport = async () => {
            if (!exportButton) {
                return;
            }
            exportButton.disabled = true;
            try {
                const response = await authorizedFetch(`${exportUrl}?window_days=${currentRange}`, {
                    headers: { Accept: 'text/csv' },
                });
                if (!response.ok) {
                    const payload = await response.json().catch(() => ({}));
                    throw new Error(payload?.detail || 'Unable to export usage.');
                }
                const blob = await response.blob();
                const disposition = response.headers?.get?.('content-disposition') || '';
                const filenameMatch = disposition.match(/filename=\"?([^\";]+)\"?/i);
                const filename = filenameMatch?.[1] || `usage_${currentRange}d.csv`;
                const downloadUrl = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = downloadUrl;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(downloadUrl);
            } catch (error) {
                console.error(error);
            } finally {
                exportButton.disabled = false;
            }
        };

        rangeButtons.forEach((button) => {
            button.addEventListener('click', () => {
                rangeButtons.forEach((btn) => btn.classList.remove('is-active'));
                button.classList.add('is-active');
                currentRange = Number(button.dataset.usageRange || 30);
                fetchUsage();
                fetchUsageErrors();
            });
        });

        alertsForm?.addEventListener('submit', handleAlertsSubmit);
        addAlertButton?.addEventListener('click', () => addAlertRow());
        exportButton?.addEventListener('click', () => {
            handleExport();
        });

        fetchUsage();
        fetchUsageErrors();
        fetchAlerts();
    };

    const initProfileEditor = () => {
        const modal = document.querySelector('[data-profile-modal]');
        const openButton = document.querySelector('[data-profile-open]');
        const form = modal?.querySelector('[data-profile-form]');
        if (!modal || !openButton || !form) {
            return;
        }

        const updateUrl = body?.dataset.profileUpdateUrl || '';
        const profileUrl = body?.dataset.profileFetchUrl || '';
        const resendUrl = body?.dataset.authResendConfirmationUrl || '';
        const logoutUrl = body?.dataset.authLogoutUrl || '';
        const accountSelectApi = initAccountSelects(form);
        const closeButtons = Array.from(modal.querySelectorAll('[data-profile-close]'));
        const statusNode = modal.querySelector('[data-profile-status]');
        const submitButton = form.querySelector('[data-profile-submit]') || form.querySelector('button[type="submit"]');
        const noCompanyInput = form.querySelector('input[name="no_company"]');
        const orgFields = Array.from(form.querySelectorAll('[data-profile-org-field]'));
        const nameNodes = Array.from(document.querySelectorAll('.account-shell__profile-name'));
        const emailInput = form.querySelector('[data-profile-email-input]');
        const emailEditButton = form.querySelector('[data-profile-email-edit]');
        const emailResendButton = form.querySelector('[data-profile-email-resend]');
        const emailStatus = form.querySelector('[data-profile-email-status]');
        const emailStatusIcon = form.querySelector('[data-profile-email-status-icon]');
        const emailStatusText = form.querySelector('[data-profile-email-status-text]');
        const logoutOpenButton = modal.querySelector('[data-profile-logout-open]');
        const logoutModal = document.querySelector('[data-logout-modal]');
        const logoutConfirmButton = logoutModal?.querySelector('[data-logout-confirm]');
        const logoutCloseButtons = logoutModal
            ? Array.from(logoutModal.querySelectorAll('[data-logout-close], [data-logout-cancel]'))
            : [];
        const logoutStatusNode = logoutModal?.querySelector('[data-logout-status]');
        let storedEmail = emailInput?.value?.trim() || '';
        let profileSnapshot = null;
        let isEmailEditing = false;
        let restoreProfileAfterLogout = false;

        const setStatus = (variant, message) => {
            if (!statusNode) {
                return;
            }
            statusNode.textContent = message || '';
            statusNode.classList.toggle('is-success', variant === 'success');
            statusNode.classList.toggle('is-error', variant === 'error');
        };

        const getFieldWrapper = (control) => {
            if (!control) {
                return null;
            }
            return control.closest('.account-profile__field') || control.closest('.account-profile__checkbox');
        };

        const clearFieldErrors = () => {
            const wrappers = Array.from(
                form.querySelectorAll('.account-profile__field.is-invalid, .account-profile__checkbox.is-invalid'),
            );
            wrappers.forEach((wrapper) => wrapper.classList.remove('is-invalid'));
            const controls = Array.from(form.elements);
            controls.forEach((control) => {
                if (
                    control instanceof HTMLInputElement ||
                    control instanceof HTMLSelectElement ||
                    control instanceof HTMLTextAreaElement
                ) {
                    control.setCustomValidity('');
                }
            });
        };

        const setFieldError = (fieldName, message) => {
            if (!fieldName || !message) {
                return;
            }
            const control = form.querySelector(`[name="${fieldName}"]`);
            if (!control) {
                return;
            }
            if (
                control instanceof HTMLInputElement ||
                control instanceof HTMLSelectElement ||
                control instanceof HTMLTextAreaElement
            ) {
                control.setCustomValidity(message);
            }
            const wrapper = getFieldWrapper(control);
            if (wrapper) {
                wrapper.classList.add('is-invalid');
            }
        };

        const parseFieldErrors = (payload) => {
            if (!payload || typeof payload !== 'object' || !Array.isArray(payload.detail)) {
                return null;
            }
            const errors = {};
            payload.detail.forEach((item) => {
                if (!item || typeof item !== 'object') {
                    return;
                }
                const loc = Array.isArray(item.loc) ? item.loc : [];
                const field = loc[loc.length - 1];
                if (typeof field !== 'string') {
                    return;
                }
                const message =
                    typeof item.msg === 'string'
                        ? item.msg
                        : typeof item.message === 'string'
                            ? item.message
                            : '';
                if (!message) {
                    return;
                }
                errors[field] = message;
            });
            return Object.keys(errors).length ? errors : null;
        };

        const setEmailEditingState = (shouldEdit) => {
            if (!emailInput || !emailEditButton) {
                return;
            }
            isEmailEditing = shouldEdit;
            emailInput.readOnly = !shouldEdit;
            emailInput.classList.toggle('is-editable', shouldEdit);
            emailEditButton.textContent = shouldEdit ? 'Cancel change' : 'Change email';
            if (shouldEdit) {
                emailInput.focus();
                emailInput.select();
                return;
            }
            emailInput.value = storedEmail;
        };

        const setEmailStatus = (isVerified, emailValue) => {
            if (!emailStatus) {
                return;
            }
            const verifiedIcon = emailStatus.dataset.verifiedIcon || '';
            const pendingIcon = emailStatus.dataset.pendingIcon || '';
            emailStatus.classList.toggle('is-verified', isVerified);
            emailStatus.classList.toggle('is-pending', !isVerified);
            emailStatus.dataset.profileEmailVerified = isVerified ? 'true' : 'false';
            if (emailStatusIcon) {
                emailStatusIcon.src = isVerified ? verifiedIcon : pendingIcon;
            }
            if (emailStatusText) {
                emailStatusText.textContent = isVerified ? 'Email verified' : 'Email not verified';
            }
            if (emailResendButton) {
                emailResendButton.hidden = isVerified;
                emailResendButton.disabled = isVerified;
            }
            if (!isVerified && emailValue) {
                emailStatus.setAttribute('aria-label', `Email pending verification: ${emailValue}`);
            } else {
                emailStatus.removeAttribute('aria-label');
            }
        };

        const setButtonBusy = (isBusy) => {
            if (!submitButton) {
                return;
            }
            submitButton.disabled = isBusy;
            submitButton.classList.toggle('account-cta--loading', isBusy);
            if (isBusy) {
                submitButton.setAttribute('aria-busy', 'true');
            } else {
                submitButton.removeAttribute('aria-busy');
            }
        };

        const setResendBusy = (isBusy) => {
            if (!emailResendButton) {
                return;
            }
            emailResendButton.disabled = isBusy;
            emailResendButton.classList.toggle('account-cta--loading', isBusy);
            if (isBusy) {
                emailResendButton.setAttribute('aria-busy', 'true');
            } else {
                emailResendButton.removeAttribute('aria-busy');
            }
        };

        const setLogoutStatus = (variant, message) => {
            if (!logoutStatusNode) {
                return;
            }
            logoutStatusNode.textContent = message || '';
            logoutStatusNode.classList.toggle('is-success', variant === 'success');
            logoutStatusNode.classList.toggle('is-error', variant === 'error');
        };

        const setLogoutBusy = (isBusy) => {
            if (!logoutConfirmButton) {
                return;
            }
            logoutConfirmButton.disabled = isBusy;
            logoutConfirmButton.classList.toggle('account-cta--loading', isBusy);
            if (isBusy) {
                logoutConfirmButton.setAttribute('aria-busy', 'true');
            } else {
                logoutConfirmButton.removeAttribute('aria-busy');
            }
        };

        const setModalState = (isOpen) => {
            modal.hidden = !isOpen;
            setModalScrollLock(isOpen);
            openButton.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            if (isOpen) {
                const firstField = form.querySelector('input:not([type="checkbox"]), select, textarea');
                if (firstField) {
                    window.requestAnimationFrame(() => firstField.focus());
                }
            } else if (typeof openButton.focus === 'function') {
                openButton.focus({ preventScroll: true });
            }
        };

        const setLogoutModalState = (isOpen) => {
            if (!logoutModal) {
                return;
            }
            logoutModal.hidden = !isOpen;
            setModalScrollLock(isOpen);
            if (isOpen && logoutConfirmButton) {
                window.requestAnimationFrame(() => logoutConfirmButton.focus());
            }
        };

        const applyOrgDisabledState = () => {
            if (!noCompanyInput) {
                return;
            }
            const shouldDisable = noCompanyInput.checked;
            orgFields.forEach((field) => {
                field.disabled = shouldDisable;
                const wrapper = field.closest('.account-profile__field');
                if (wrapper) {
                    wrapper.classList.toggle('is-disabled', shouldDisable);
                }
                if (shouldDisable) {
                    field.setAttribute('aria-disabled', 'true');
                } else {
                    field.removeAttribute('aria-disabled');
                }
            });
            accountSelectApi?.syncAll();
        };

        const buildPayload = () => {
            const formData = new FormData(form);
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

        const setControlValue = (name, value) => {
            const control = form.querySelector(`[name="${name}"]`);
            if (!control) {
                return;
            }
            control.value = typeof value === 'string' ? value : '';
            if (control instanceof HTMLSelectElement) {
                control.dispatchEvent(new Event('change', { bubbles: true }));
            }
        };

        const applyProfilePayload = (payload) => {
            if (!payload || typeof payload !== 'object') {
                return;
            }
            const fullName = typeof payload.full_name === 'string' ? payload.full_name : '';
            const jobTitle = typeof payload.job_title === 'string' ? payload.job_title : '';
            const useCase = typeof payload.use_case === 'string' ? payload.use_case : '';
            const email = typeof payload.email === 'string' ? payload.email : '';
            const emailVerifiedAt = typeof payload.email_verified_at === 'string' ? payload.email_verified_at : '';
            const organization = typeof payload.organization === 'object' && payload.organization ? payload.organization : null;
            const organizationName = typeof organization?.name === 'string' ? organization.name : '';
            const organizationSize = typeof organization?.size_label === 'string' ? organization.size_label : '';

            setControlValue('full_name', fullName);
            setControlValue('job_title', jobTitle);
            setControlValue('organization_name', organizationName);
            setControlValue('organization_size', organizationSize);
            setControlValue('use_case', useCase);
            setControlValue('email', email);
            storedEmail = email;
            setEmailStatus(Boolean(emailVerifiedAt), email);
            setEmailEditingState(false);

            if (noCompanyInput) {
                noCompanyInput.checked = !organization;
            }
            applyOrgDisabledState();

            const displayName = fullName || email;
            if (displayName) {
                nameNodes.forEach((node) => {
                    node.textContent = displayName;
                });
            }
        };

        const buildProfileSnapshot = () => {
            const fullName = form.querySelector('input[name="full_name"]')?.value?.trim() || '';
            const jobTitle = form.querySelector('select[name="job_title"]')?.value?.trim() || '';
            const organizationName = form.querySelector('input[name="organization_name"]')?.value?.trim() || '';
            const organizationSize = form.querySelector('select[name="organization_size"]')?.value?.trim() || '';
            const useCase = form.querySelector('select[name="use_case"]')?.value?.trim() || '';
            const emailValue = emailInput?.value?.trim() || '';
            const isVerified = emailStatus?.dataset.profileEmailVerified === 'true';
            const organization = noCompanyInput?.checked
                ? null
                : {
                      name: organizationName,
                      size_label: organizationSize,
                  };

            return {
                full_name: fullName,
                job_title: jobTitle,
                use_case: useCase,
                email: emailValue,
                email_verified_at: isVerified ? 'true' : '',
                organization,
            };
        };

        const resetProfileForm = () => {
            clearFieldErrors();
            if (profileSnapshot) {
                applyProfilePayload(profileSnapshot);
                accountSelectApi?.syncAll();
                return;
            }
            form.reset();
            accountSelectApi?.syncAll();
            storedEmail = emailInput?.value?.trim() || '';
            if (emailStatus) {
                const isVerified = emailStatus.dataset.profileEmailVerified === 'true';
                setEmailStatus(isVerified, storedEmail);
            }
            applyOrgDisabledState();
            setEmailEditingState(false);
        };

        const fetchProfile = async () => {
            if (!profileUrl) {
                profileSnapshot = buildProfileSnapshot();
                return;
            }
            setStatus(null, 'Loading profile...');
            clearFieldErrors();
            try {
                const response = await authorizedFetch(profileUrl, {
                    headers: { Accept: 'application/json' },
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const rawDetail =
                        typeof payload?.detail === 'string'
                            ? payload.detail
                            : typeof payload?.message === 'string'
                                ? payload.message
                                : '';
                    throw new Error(rawDetail || 'Unable to load profile.');
                }
                profileSnapshot = payload;
                applyProfilePayload(payload);
                accountSelectApi?.syncAll();
                setStatus(null, '');
            } catch (error) {
                const fallback =
                    error instanceof Error && typeof error.message === 'string'
                        ? error.message
                        : 'Unable to load profile.';
                setStatus('error', fallback);
            }
        };

        const handleEmailEditClick = () => {
            if (!emailInput || !emailEditButton) {
                return;
            }
            setEmailEditingState(!isEmailEditing);
        };

        const handleEmailResend = async () => {
            if (!resendUrl) {
                setStatus('error', 'Resend endpoint unavailable.');
                return;
            }
            if (!emailInput) {
                setStatus('error', 'Email input unavailable.');
                return;
            }
            const emailValue = emailInput.value.trim();
            if (isEmailEditing && storedEmail !== emailValue) {
                setStatus('error', 'Save the new email before resending.');
                return;
            }
            if (!emailValue) {
                setStatus('error', 'Email is required.');
                return;
            }
            setStatus(null, 'Sending confirmation email...');
            setResendBusy(true);
            try {
                const response = await fetch(resendUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ email: emailValue }),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const message =
                        typeof payload?.detail === 'string'
                            ? payload.detail
                            : typeof payload?.message === 'string'
                                ? payload.message
                                : 'Unable to resend confirmation.';
                    throw new Error(message);
                }
                setStatus('success', 'Confirmation email sent.');
            } catch (error) {
                const fallback =
                    error instanceof Error && typeof error.message === 'string'
                        ? error.message
                        : 'Unable to resend confirmation.';
                setStatus('error', fallback);
            } finally {
                setResendBusy(false);
            }
        };

        const handleLogoutOpen = () => {
            if (!logoutModal) {
                return;
            }
            restoreProfileAfterLogout = !modal.hidden;
            if (!modal.hidden) {
                setModalState(false);
            }
            setLogoutStatus(null, '');
            setLogoutModalState(true);
        };

        const handleLogoutClose = () => {
            setLogoutModalState(false);
            setLogoutStatus(null, '');
            if (restoreProfileAfterLogout) {
                setModalState(true);
            }
            restoreProfileAfterLogout = false;
        };

        const handleLogoutConfirm = async () => {
            if (!logoutUrl) {
                setLogoutStatus('error', 'Logout endpoint unavailable.');
                return;
            }
            setLogoutStatus(null, 'Signing out...');
            setLogoutBusy(true);
            try {
                const response = await fetch(logoutUrl, {
                    method: 'POST',
                    credentials: 'include',
                    headers: { Accept: 'application/json' },
                });
                if (!response.ok) {
                    const payload = await response.json().catch(() => ({}));
                    const message =
                        typeof payload?.detail === 'string'
                            ? payload.detail
                            : typeof payload?.message === 'string'
                                ? payload.message
                                : 'Unable to log out.';
                    throw new Error(message);
                }
                storeAccessToken(null);
                window.location.reload();
            } catch (error) {
                const fallback =
                    error instanceof Error && typeof error.message === 'string'
                        ? error.message
                        : 'Unable to log out.';
                setLogoutStatus('error', fallback);
            } finally {
                setLogoutBusy(false);
            }
        };

        const handleFormKeyDown = (event) => {
            if (event.key !== 'Enter') {
                return;
            }
            const target = event.target;
            if (!(target instanceof HTMLInputElement)) {
                return;
            }
            event.preventDefault();
        };

        const handleFieldInput = (event) => {
            const target = event.target;
            if (
                !(target instanceof HTMLInputElement) &&
                !(target instanceof HTMLSelectElement) &&
                !(target instanceof HTMLTextAreaElement)
            ) {
                return;
            }
            if (target.validity.customError) {
                target.setCustomValidity('');
            }
            const wrapper = getFieldWrapper(target);
            if (wrapper) {
                wrapper.classList.remove('is-invalid');
            }
        };

        const handleSubmit = async (event) => {
            event.preventDefault();
            if (!updateUrl) {
                setStatus('error', 'Profile endpoint unavailable.');
                return;
            }
            clearFieldErrors();
            if (typeof form.reportValidity === 'function' && !form.reportValidity()) {
                setStatus('error', 'Please review the highlighted fields.');
                return;
            }
            setStatus(null, 'Saving...');
            setButtonBusy(true);
            const previousEmail = storedEmail;
            try {
                const response = await authorizedFetch(updateUrl, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(buildPayload()),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const fieldErrors = parseFieldErrors(payload);
                    if (fieldErrors) {
                        Object.entries(fieldErrors).forEach(([field, message]) => {
                            setFieldError(field, message);
                        });
                        const firstMessage = Object.values(fieldErrors)[0] || 'Please review the highlighted fields.';
                        setStatus('error', firstMessage);
                        if (typeof form.reportValidity === 'function') {
                            form.reportValidity();
                        }
                        return;
                    }
                    const rawDetail =
                        typeof payload?.detail === 'string'
                            ? payload.detail
                            : typeof payload?.message === 'string'
                                ? payload.message
                                : '';
                    if (rawDetail === 'account_exists') {
                        setFieldError('email', 'Email already in use.');
                        if (typeof form.reportValidity === 'function') {
                            form.reportValidity();
                        }
                        setStatus('error', 'Email already in use.');
                        return;
                    }
                    const message = rawDetail || 'Unable to update profile.';
                    throw new Error(message);
                }
                applyProfilePayload(payload);
                profileSnapshot = payload;
                const nextEmail = typeof payload?.email === 'string' ? payload.email : '';
                if (previousEmail && nextEmail && previousEmail !== nextEmail) {
                    setStatus('success', 'Email updated. Please confirm via email.');
                } else {
                    setStatus('success', 'Profile updated.');
                }
            } catch (error) {
                const fallback =
                    error instanceof Error && typeof error.message === 'string'
                        ? error.message
                        : 'Unable to update profile.';
                setStatus('error', fallback);
            } finally {
                setButtonBusy(false);
            }
        };

        const handleOpen = async () => {
            setStatus(null, '');
            setModalState(true);
            setEmailEditingState(false);
            applyOrgDisabledState();
            accountSelectApi?.syncAll();
            await fetchProfile();
        };

        const handleClose = () => {
            setModalState(false);
            setStatus(null, '');
            resetProfileForm();
            accountSelectApi?.closeAll();
        };

        const handleModalEscape = (event) => {
            if (event.key !== 'Escape') {
                return;
            }
            if (logoutModal && !logoutModal.hidden) {
                handleLogoutClose();
                return;
            }
            if (!modal.hidden) {
                handleClose();
            }
        };

        openButton.addEventListener('click', handleOpen);
        closeButtons.forEach((button) => button.addEventListener('click', handleClose));
        form.addEventListener('submit', handleSubmit);
        form.addEventListener('keydown', handleFormKeyDown);
        form.addEventListener('input', handleFieldInput);
        noCompanyInput?.addEventListener('change', applyOrgDisabledState);
        emailEditButton?.addEventListener('click', handleEmailEditClick);
        emailResendButton?.addEventListener('click', handleEmailResend);
        logoutOpenButton?.addEventListener('click', handleLogoutOpen);
        logoutConfirmButton?.addEventListener('click', handleLogoutConfirm);
        logoutCloseButtons.forEach((button) => button.addEventListener('click', handleLogoutClose));
        document.addEventListener('keydown', handleModalEscape);

        applyOrgDisabledState();
        setEmailStatus(emailStatus?.dataset.profileEmailVerified === 'true', storedEmail);
        profileSnapshot = buildProfileSnapshot();
    };

    const initSupportForms = () => {
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
            if (typeof body.message === 'string') {
                return body.message;
            }
            return null;
        };

        const getStatusNode = (form) => {
            return form.querySelector('[data-intake-status]') || form.querySelector('.landing-intake__status');
        };

        const parsePositiveInt = (value, fallback) => {
            const parsed = Number.parseInt(value, 10);
            if (Number.isFinite(parsed) && parsed > 0) {
                return parsed;
            }
            return fallback;
        };

        const fileToBase64 = (file) =>
            new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => {
                    const buffer = reader.result;
                    if (!(buffer instanceof ArrayBuffer)) {
                        reject(new Error('Failed to read the attachment.'));
                        return;
                    }
                    const bytes = new Uint8Array(buffer);
                    let binary = '';
                    const chunkSize = 0x8000;
                    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                        const chunk = bytes.subarray(offset, offset + chunkSize);
                        binary += String.fromCharCode(...chunk);
                    }
                    resolve(btoa(binary));
                };
                reader.onerror = () => reject(new Error('Failed to read the attachment.'));
                reader.readAsArrayBuffer(file);
            });

        const collectAttachments = async (form) => {
            const attachmentInputs = Array.from(form.querySelectorAll('[data-attachments-input]'));
            if (!attachmentInputs.length) {
                return [];
            }
            const maxCount = parsePositiveInt(form.dataset.attachmentMaxCount, 3);
            const maxBytes = parsePositiveInt(form.dataset.attachmentMaxSize, 5 * 1024 * 1024);
            const files = attachmentInputs.flatMap((input) => Array.from(input.files || []));
            if (files.length > maxCount) {
                throw new Error(`Attach up to ${maxCount} files.`);
            }
            const attachments = [];
            for (const file of files) {
                if (file.size > maxBytes) {
                    const limitMb = Math.round(maxBytes / (1024 * 1024));
                    throw new Error(`Each attachment must be under ${limitMb} MB.`);
                }
                const contentBase64 = await fileToBase64(file);
                attachments.push({
                    filename: file.name || 'screenshot',
                    content_type: file.type || 'application/octet-stream',
                    content_base64: contentBase64,
                });
            }
            return attachments;
        };

        const formatAttachmentSize = (bytes) => {
            if (!Number.isFinite(bytes) || bytes <= 0) {
                return '0 KB';
            }
            const megabytes = bytes / (1024 * 1024);
            if (megabytes >= 1) {
                return `${megabytes.toFixed(1)} MB`;
            }
            return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        };

        forms.forEach((form) => {
            const statusNode = getStatusNode(form);
            const submitButton = form.querySelector('button[type="submit"]');
            const canUpdateLabel = Boolean(submitButton && submitButton.childElementCount === 0);
            const originalLabel = canUpdateLabel ? submitButton.textContent.trim() : '';
            const successMessage =
                form.dataset.successMessage || 'Thanks for your submission - we will follow up shortly.';
            const submittingMessage = form.dataset.submittingMessage || 'Submitting...';
            const attachmentInputs = Array.from(form.querySelectorAll('[data-attachments-input]'));
            const attachmentList = form.querySelector('[data-attachments-list]');
            const attachmentSummary = form.querySelector('[data-attachments-summary]');
            const attachmentsZone = form.querySelector('[data-attachments]');
            const maxAttachments = parsePositiveInt(form.dataset.attachmentMaxCount, 3);
            const maxAttachmentBytes = parsePositiveInt(form.dataset.attachmentMaxSize, 5 * 1024 * 1024);
            let attachmentStore = attachmentInputs.flatMap((input) => Array.from(input.files || []));

            const setStatus = (variant, message) => {
                if (!statusNode) {
                    return;
                }
                const nextMessage = message || '';
                statusNode.textContent = nextMessage;
                statusNode.classList.toggle('is-success', variant === 'success');
                statusNode.classList.toggle('is-error', variant === 'error');
                statusNode.classList.toggle('is-visible', nextMessage !== '');
                statusNode.setAttribute('aria-hidden', nextMessage === '' ? 'true' : 'false');
            };

            const setButtonBusy = (isBusy) => {
                if (!submitButton) {
                    return;
                }
                submitButton.disabled = isBusy;
                submitButton.classList.toggle('is-loading', isBusy);
                submitButton.classList.toggle('account-cta--loading', isBusy);
                submitButton.setAttribute('aria-busy', isBusy ? 'true' : 'false');
                if (canUpdateLabel && originalLabel) {
                    submitButton.textContent = isBusy ? submittingMessage : originalLabel;
                }
            };

            const updateAttachmentSummary = () => {
                if (!attachmentSummary) {
                    return;
                }
                const count = attachmentStore.length;
                const label = count === 1 ? 'file' : 'files';
                if (!count) {
                    attachmentSummary.textContent = `No files selected yet (0/${maxAttachments}).`;
                    attachmentSummary.classList.remove('is-ready');
                    return;
                }
                attachmentSummary.textContent = `${count}/${maxAttachments} ${label} ready to send.`;
                attachmentSummary.classList.add('is-ready');
            };

            const setAttachmentInputs = () => {
                const dt = new DataTransfer();
                attachmentStore.forEach((file) => dt.items.add(file));
                attachmentInputs.forEach((input) => {
                    input.files = dt.files;
                });
                renderAttachmentList();
            };

            const addAttachments = (newFiles) => {
                if (!newFiles?.length) {
                    return;
                }
                const next = [...attachmentStore];
                let hasCountOverflow = false;
                for (const file of newFiles) {
                    if (file.size > maxAttachmentBytes) {
                        const limitMb = Math.round(maxAttachmentBytes / (1024 * 1024));
                        setStatus('error', `Each attachment must be under ${limitMb} MB.`);
                        return;
                    }
                    if (next.length >= maxAttachments) {
                        hasCountOverflow = true;
                        continue;
                    }
                    next.push(file);
                }
                attachmentStore = next;
                setAttachmentInputs();
                if (hasCountOverflow) {
                    setStatus('error', `Attach up to ${maxAttachments} files.`);
                    return;
                }
                setStatus(null, '');
            };

            const removeAttachmentAt = (index) => {
                attachmentStore = attachmentStore.filter((_, idx) => idx !== index);
                setAttachmentInputs();
            };

            const renderAttachmentList = () => {
                if (!attachmentList) {
                    return;
                }
                attachmentList.innerHTML = '';
                updateAttachmentSummary();
                if (!attachmentStore.length) {
                    return;
                }
                attachmentStore.forEach((file, index) => {
                    const item = document.createElement('span');
                    item.className = 'support-form__upload-item';
                    const icon = document.createElement('span');
                    icon.className = 'support-form__upload-item-icon';
                    const extension = file?.name?.split?.('.')?.pop?.()?.slice?.(0, 4);
                    icon.textContent = extension && extension.length ? extension.toUpperCase() : `${index + 1}`;
                    const label = document.createElement('span');
                    label.className = 'support-form__upload-item-name';
                    label.textContent = `${file?.name || 'attachment'} (${formatAttachmentSize(file?.size || 0)})`;
                    const remove = document.createElement('button');
                    remove.type = 'button';
                    remove.className = 'support-form__upload-remove';
                    remove.setAttribute('aria-label', `Remove ${file?.name || 'attachment'}`);
                    remove.textContent = 'x';
                    remove.addEventListener('click', (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        removeAttachmentAt(index);
                    });
                    item.appendChild(icon);
                    item.appendChild(label);
                    item.appendChild(remove);
                    attachmentList.appendChild(item);
                });
            };

            attachmentInputs.forEach((input) => {
                input.addEventListener('change', (event) => {
                    const files = Array.from(event.target?.files || []);
                    if (event.target) {
                        event.target.value = '';
                    }
                    addAttachments(files);
                });
            });

            if (attachmentsZone) {
                const primaryInput = attachmentInputs[0];
                const openPicker = () => {
                    if (!primaryInput) {
                        return;
                    }
                    primaryInput.click();
                };
                attachmentsZone.addEventListener('click', (event) => {
                    const isRemoveButton = Boolean(event.target?.closest('.support-form__upload-remove'));
                    const isList = Boolean(event.target?.closest('[data-attachments-list]'));
                    const isLabel = Boolean(event.target?.closest('.support-form__upload-label'));
                    const isFileInput = Boolean(event.target?.closest('[data-attachments-input]'));
                    if (isRemoveButton || isList || !primaryInput) {
                        return;
                    }
                    if (isLabel || isFileInput) {
                        // Let the native label/input click open the picker to avoid double dialogs.
                        return;
                    }
                    openPicker();
                });
                attachmentsZone.addEventListener('keydown', (event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') {
                        return;
                    }
                    const isRemoveButton = Boolean(event.target?.closest('.support-form__upload-remove'));
                    const isList = Boolean(event.target?.closest('[data-attachments-list]'));
                    if (isRemoveButton || isList || !primaryInput) {
                        return;
                    }
                    event.preventDefault();
                    openPicker();
                });
                attachmentsZone.addEventListener('dragover', (event) => {
                    event.preventDefault();
                    attachmentsZone.classList.add('is-dragover');
                });
                attachmentsZone.addEventListener('dragleave', () => {
                    attachmentsZone.classList.remove('is-dragover');
                });
                attachmentsZone.addEventListener('drop', (event) => {
                    event.preventDefault();
                    attachmentsZone.classList.remove('is-dragover');
                    const files = Array.from(event.dataTransfer?.files || []);
                    addAttachments(files);
                });
            }

            setAttachmentInputs();

            form.addEventListener('submit', async (event) => {
                event.preventDefault();

                if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
                    if (typeof form.reportValidity === 'function') {
                        form.reportValidity();
                    }
                    setStatus('error', 'Please review the highlighted fields.');
                    return;
                }

                setStatus(null, 'Sending...');
                setButtonBusy(true);

                try {
                    const formData = new FormData(form);
                    const payload = {};

                    for (const [key, value] of formData.entries()) {
                        if (value instanceof File) {
                            continue;
                        }
                        if (typeof value === 'string') {
                            const trimmed = value.trim();
                            if (trimmed !== '') {
                                payload[key] = trimmed;
                            }
                        }
                    }

                    const attachments = await collectAttachments(form);
                    if (attachments.length) {
                        payload.attachments = attachments;
                    }

                    const response = await fetch(form.getAttribute('action') || window.location.href, {
                        method: (form.getAttribute('method') || 'POST').toUpperCase(),
                        headers: {
                            Accept: 'application/json',
                            'Content-Type': 'application/json',
                        },
                        credentials: 'same-origin',
                        body: JSON.stringify(payload),
                    });

                    const responseBody = await response.json().catch(() => null);
                    if (!response.ok) {
                        const detailMessage =
                            parseErrorDetail(responseBody) || 'Submission failed. Please try again.';
                        throw new Error(detailMessage);
                    }

                    form.reset();
                    attachmentStore = [];
                    setAttachmentInputs();
                    setStatus('success', successMessage);
                } catch (error) {
                    const fallback = 'Submission failed. Please try again.';
                    const message = error instanceof Error && error.message ? error.message : fallback;
                    setStatus('error', message);
                } finally {
                    setButtonBusy(false);
                }
            });

            setStatus(null, '');
            updateAttachmentSummary();
        });
    };

    const initCookieConsent = () => {
        if (window.AICICookieConsent?.init) {
            window.AICICookieConsent.init();
        }
    };

    const initCtaTracking = () => {
        const ctaNodes = document.querySelectorAll('[data-cta-id]');
        if (!ctaNodes.length) {
            return;
        }

        const bodyDataset = body && body.dataset ? body.dataset : {};
        const defaultEndpointRaw = bodyDataset.ctaEndpoint || '/api/v1/events/cta';
        const defaultEndpoint =
            typeof defaultEndpointRaw === 'string' && defaultEndpointRaw.trim()
                ? defaultEndpointRaw.trim()
                : '/api/v1/events/cta';
        const pagePath = window.location.pathname || '/';
        const utmSnapshot = resolveCtaUtmSnapshot();
        const cooldownMap = new WeakMap();

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

        const resolveAuthState = (node) => {
            const explicit = (node.dataset.ctaAuthState || '').trim().toLowerCase();
            if (explicit === 'authenticated' || explicit === 'anonymous') {
                return explicit;
            }
            const globalState = (bodyDataset.isAuthenticated || '').trim().toLowerCase();
            if (globalState === 'false') {
                return 'anonymous';
            }
            return 'authenticated';
        };

        const buildMetadata = (node, location) => {
            const baseMetadata = {
                page_path: pagePath,
                section: node.dataset.ctaSection || location,
                placement: node.dataset.ctaPlacement || location,
                scenario: node.dataset.ctaScenario || 'navigate',
                event_type: 'cta_click',
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
                await fetch(node.dataset.ctaEndpoint || defaultEndpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    keepalive: true,
                    body: JSON.stringify(payload),
                });
            } catch (error) {
                // ignore network failures for non-blocking analytics
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

    document.addEventListener('DOMContentLoaded', () => {
        initCtaTracking();
        initToasts();
        initKeysSecurity();
        initBilling();
        initUsageSparklines();
        initUsage();
        initProfileEditor();
        initSupportForms();
        initCookieConsent();
    });
})();




