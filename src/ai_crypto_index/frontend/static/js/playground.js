(() => {
    'use strict';

    const body = document.body;
    if (!body || !body.classList.contains('account-shell')) {
        return;
    }

    const KEY_STORAGE = 'aici:playground:key';
    const PERSISTED_KEY_STORAGE = 'aici:playground:key:persisted';
    const shareBase = body.dataset.playgroundShareUrl || window.location.pathname;
    const snippetsStorageKey = body.dataset.playgroundSnippetsKey || 'aici:playground:snippets';
    const LEGACY_SNIPPETS_STORAGE_KEYS = ['aici:playground:snippets', 'aici:account:snippets'];
    const developerDocsBase = body.dataset.developerPortalUrl || '';
    const sharedParams = new URLSearchParams(window.location.search);
    const instances = new Set();
    let hasResponseContent = false;
    let responseContextId = 0;

    const readStoredKey = () => {
        try {
            const persisted = window.localStorage?.getItem(PERSISTED_KEY_STORAGE);
            if (persisted) {
                return persisted;
            }
            const legacy = window.localStorage?.getItem(KEY_STORAGE) || '';
            if (legacy) {
                window.localStorage?.setItem(PERSISTED_KEY_STORAGE, legacy);
            }
            return legacy;
        } catch (_) {
            return '';
        }
    };

    const writeStoredKey = (value) => {
        try {
            if (value) {
                window.localStorage?.setItem(KEY_STORAGE, value);
                window.localStorage?.setItem(PERSISTED_KEY_STORAGE, value);
            } else {
                window.localStorage?.removeItem(KEY_STORAGE);
                window.localStorage?.removeItem(PERSISTED_KEY_STORAGE);
            }
        } catch (_) {
            /* ignore storage errors */
        }
    };

    const copyToClipboard = async (text) => {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', 'true');
        textarea.style.position = 'absolute';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
    };

    const parseScriptPayload = (root, selector) => {
        const node = root.querySelector(selector);
        if (!node) {
            return null;
        }
        try {
            return JSON.parse(node.textContent || '{}');
        } catch (_) {
            return null;
        }
    };

    const hasValue = (value) => {
        if (value === null || value === undefined) {
            return false;
        }
        if (typeof value === 'string') {
            return value.trim() !== '';
        }
        return true;
    };

    const toBoolean = (value) => {
        if (typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'number') {
            return value > 0;
        }
        if (typeof value === 'string') {
            const normalized = value.trim().toLowerCase();
            if (!normalized) {
                return false;
            }
            return ['true', '1', 'yes', 'y', 'on'].includes(normalized);
        }
        return false;
    };

    const wait = (ms) =>
        new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });

    const normalizePerfMetrics = (metrics) => {
        const normalized = { ...(metrics || {}) };
        const has = (key) => Object.prototype.hasOwnProperty.call(normalized, key);
        if (has('AnnualReturn(%)') && !has('cagr')) {
            const value = Number(normalized['AnnualReturn(%)']);
            normalized.cagr = Number.isFinite(value) ? value / 100 : normalized.cagr;
        }
        if (has('AnnualVolatility(%)') && !has('annual_volatility')) {
            const value = Number(normalized['AnnualVolatility(%)']);
            normalized.annual_volatility = Number.isFinite(value) ? value / 100 : normalized.annual_volatility;
        }
        if (has('MaxDrawdown(%)') && !has('max_drawdown')) {
            const value = Number(normalized['MaxDrawdown(%)']);
            normalized.max_drawdown = Number.isFinite(value) ? value / 100 : normalized.max_drawdown;
        }
        if (has('SharpeRatio') && !has('sharpe')) {
            normalized.sharpe = normalized['SharpeRatio'];
        }
        if (has('Sortino') && !has('sortino')) {
            normalized.sortino = normalized['Sortino'];
        }
        if (has('vol') && !has('annual_volatility')) {
            const value = Number(normalized.vol);
            normalized.annual_volatility = Number.isFinite(value) ? value : normalized.annual_volatility;
        }
        if (has('cumulative_return') && !has('cagr')) {
            const value = Number(normalized.cumulative_return);
            normalized.cagr = Number.isFinite(value) ? value : normalized.cagr;
        }
        return normalized;
    };

    const renderWeightsChart = (container, endpoint, payload) => {
        const items = Array.isArray(payload?.items) ? payload.items.slice() : [];
        if (!items.length) {
            const placeholder = document.createElement('p');
            placeholder.className = 'developer-playground__placeholder';
            placeholder.textContent = 'No allocation data yet.';
            container.appendChild(placeholder);
            return;
        }
        items.sort((a, b) => (Number(b.weight) || 0) - (Number(a.weight) || 0));
        const limit = Number(endpoint?.visualization?.max_items) || items.length;
        const table = document.createElement('div');
        table.className = 'playground-allocation';

        const header = document.createElement('div');
        header.className = 'playground-allocation__row playground-allocation__row--head';
        const assetHead = document.createElement('span');
        assetHead.className = 'playground-allocation__cell playground-allocation__cell--label';
        assetHead.textContent = 'Asset';
        const weightHead = document.createElement('span');
        weightHead.className = 'playground-allocation__cell playground-allocation__cell--label';
        weightHead.textContent = 'Weight';
        header.append(assetHead, weightHead);
        table.appendChild(header);

        items.slice(0, limit).forEach((entry) => {
            const row = document.createElement('div');
            row.className = 'playground-allocation__row';

            const assetCell = document.createElement('span');
            assetCell.className = 'playground-allocation__cell playground-allocation__asset';
            assetCell.textContent = entry.asset || entry.symbol || 'N/A';

            const weightCell = document.createElement('div');
            weightCell.className = 'playground-allocation__cell playground-allocation__weight';
            const pct = Math.max(0, Math.min(100, (Number(entry.weight) || 0) * 100));
            const value = document.createElement('span');
            value.className = 'playground-allocation__value';
            value.textContent = `${pct.toFixed(1)}%`;

            const bar = document.createElement('span');
            bar.className = 'playground-allocation__bar';
            const fill = document.createElement('span');
            fill.className = 'playground-allocation__bar-fill';
            fill.style.width = `${pct}%`;
            bar.appendChild(fill);

            weightCell.append(value, bar);
            row.append(assetCell, weightCell);
            table.appendChild(row);
        });

        container.appendChild(table);
    };

    const renderPerfCards = (container, endpoint, payload) => {
        const metrics = Array.isArray(endpoint?.visualization?.metrics) ? endpoint.visualization.metrics : [];
        const normalizedMetrics = normalizePerfMetrics(payload?.metrics);
        if (!metrics.length || !normalizedMetrics || !Object.keys(normalizedMetrics).length) {
            const placeholder = document.createElement('p');
            placeholder.className = 'developer-playground__placeholder';
            placeholder.textContent = 'No metrics available yet.';
            container.appendChild(placeholder);
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'playground-metrics';
        metrics.forEach((metric) => {
            const card = document.createElement('article');
            card.className = 'playground-metrics__card';
            const label = document.createElement('p');
            label.className = 'playground-metrics__label';
            label.textContent = metric.label;
            const value = document.createElement('p');
            value.className = 'playground-metrics__value';
            const raw = normalizedMetrics[metric.key];
            if (metric.format === 'percent') {
                const pct = Number(raw) * 100;
                value.textContent = Number.isFinite(pct) ? `${pct.toFixed(2)}%` : 'N/A';
            } else {
                value.textContent = Number.isFinite(Number(raw)) ? Number(raw).toFixed(2) : 'N/A';
            }
            card.appendChild(label);
            card.appendChild(value);
            wrapper.appendChild(card);
        });
        container.appendChild(wrapper);
    };

    const normalizeSnippets = (value) => {
        if (!Array.isArray(value)) {
            return [];
        }
        return value
            .filter((item) => item && typeof item === 'object')
            .map((item, index) => ({
                id: String(item.id || `legacy-${index}`),
                name: String(item.name || '').trim(),
                language: String(item.language || 'python'),
                code: String(item.code || ''),
                method: String(item.method || ''),
                path: String(item.path || ''),
            }))
            .filter((item) => item.name && item.code);
    };

    const readSnippets = () => {
        try {
            const keysToTry = [snippetsStorageKey, ...LEGACY_SNIPPETS_STORAGE_KEYS.filter((key) => key !== snippetsStorageKey)];
            for (const key of keysToTry) {
                const raw = window.localStorage?.getItem(key);
                if (!raw) {
                    continue;
                }
                const parsed = normalizeSnippets(JSON.parse(raw));
                if (!parsed.length) {
                    continue;
                }
                if (key !== snippetsStorageKey) {
                    window.localStorage?.setItem(snippetsStorageKey, JSON.stringify(parsed));
                }
                return parsed;
            }
            return [];
        } catch (_) {
            return [];
        }
    };

    const writeSnippets = (items) => {
        try {
            window.localStorage?.setItem(snippetsStorageKey, JSON.stringify(items));
        } catch (_) {
            /* storage may be blocked */
        }
    };

    const initPlayground = (root) => {
        if (root.dataset.playgroundReady) {
            return;
        }
        root.dataset.playgroundReady = 'true';

        const config = parseScriptPayload(root, '[data-playground-config]');
        if (!config || !Array.isArray(config.endpoints)) {
            return;
        }
        const docs = parseScriptPayload(root, '[data-playground-docs]') || [];
        const docsIndex = new Map(docs.map((entry) => [entry.id, entry]));

        const endpointMap = new Map(config.endpoints.map((endpoint) => [endpoint.id, endpoint]));
        const fieldState = new Map();
        const fieldErrors = new Map();
        const fieldTouched = new Map();
        const RUN_ID_SYNC_ENDPOINTS = new Set(['run-weights', 'run-perf']);
        let latestRunIdLookup = { token: '', runId: '', fetchedAt: 0 };
        let runIdSyncRequestId = 0;
        const elements = {
            form: root.querySelector('[data-playground-form]'),
            tokenInput: root.querySelector('[data-playground-token]'),
            tokenToggle: root.querySelector('[data-playground-token-toggle]'),
            remember: root.querySelector('[data-playground-remember]'),
            endpointSelect: root.querySelector('[data-playground-endpoint]'),
            description: root.querySelector('[data-playground-description]'),
            fields: root.querySelector('[data-playground-fields]'),
            error: root.querySelector('[data-playground-error]'),
            statusNodes: root.querySelectorAll('[data-playground-status]'),
            method: root.querySelector('[data-playground-method]'),
            url: root.querySelector('[data-playground-url]'),
            chart: root.querySelector('[data-playground-chart]'),
            json: root.querySelector('[data-playground-json]'),
            response: root.querySelector('[data-playground-response]'),
            reset: root.querySelector('[data-playground-reset]'),
            share: root.querySelector('[data-playground-share]'),
            snippetForm: document.querySelector('[data-playground-snippets]'),
            docsLink: document.querySelector('[data-docs-link]'),
        };
        if (!elements.form || !elements.endpointSelect || !elements.chart || !elements.json) {
            return;
        }

        const instance = {
            setToken(secret) {
                if (!secret || !elements.tokenInput) {
                    return;
                }
                elements.tokenInput.value = secret;
                if (elements.remember) {
                    elements.remember.checked = true;
                }
                latestRunIdLookup = { token: '', runId: '', fetchedAt: 0 };
                void syncRunIdFromLatestAllocation();
            },
        };
        instances.add(instance);

        let isTokenVisible = false;

        const updateTokenVisibility = (visible) => {
            if (!elements.tokenInput) {
                return;
            }
            isTokenVisible = visible;
            elements.tokenInput.type = visible ? 'text' : 'password';
            if (elements.tokenToggle) {
                const label = visible ? 'Hide API token' : 'Show API token';
                elements.tokenToggle.dataset.visibility = visible ? 'visible' : 'hidden';
                elements.tokenToggle.setAttribute('aria-label', label);
                elements.tokenToggle.title = label;
            }
        };

        const setStatus = (message, variant = 'idle') => {
            if (!elements.statusNodes || elements.statusNodes.length === 0) {
                return;
            }
            elements.statusNodes.forEach((node) => {
                node.textContent = message;
                node.dataset.status = variant;
            });
        };

        const iconPaths = {
            copy: elements.snippetForm?.dataset.iconCopy || '/static/icons/copy.svg',
            delete: elements.snippetForm?.dataset.iconDelete || '/static/icons/delete.svg',
        };

        const setError = (message) => {
            if (elements.error) {
                elements.error.textContent = message || '';
            }
        };

        const formatLatency = (seconds) => {
            const value = Number(seconds);
            if (!Number.isFinite(value) || value <= 0) {
                return null;
            }
            if (value >= 86400) {
                const days = Math.ceil(value / 86400);
                return `${days} day${days === 1 ? '' : 's'}`;
            }
            if (value >= 3600) {
                const hours = Math.ceil(value / 3600);
                return `${hours} hour${hours === 1 ? '' : 's'}`;
            }
            if (value >= 60) {
                const minutes = Math.ceil(value / 60);
                return `${minutes} minute${minutes === 1 ? '' : 's'}`;
            }
            return `${Math.ceil(value)}s`;
        };

        const formatElapsedLabel = (seconds) => {
            const totalSeconds = Number(seconds);
            if (!Number.isFinite(totalSeconds) || totalSeconds < 0) {
                return null;
            }
            const minutes = Math.floor(totalSeconds / 60);
            const secs = Math.floor(totalSeconds % 60);
            const minutePart = minutes ? `${minutes}m ` : '';
            return `${minutePart}${secs}s`;
        };

        const parseRequestTokens = (response) => {
            const raw = response?.headers?.get?.('X-API-Request-Tokens');
            if (!raw) {
                return null;
            }
            const value = Number(raw);
            if (!Number.isFinite(value) || value < 0) {
                return null;
            }
            return Math.round(value);
        };

        const formatTokenCostLabel = (tokens) => {
            const value = Number(tokens);
            if (!Number.isFinite(value) || value < 0) {
                return null;
            }
            const normalized = Math.round(value);
            const suffix = normalized === 1 ? 'token' : 'tokens';
            return `Run cost: ${normalized.toLocaleString()} ${suffix}`;
        };

        const formatErrorDetail = (detail, response) => {
            const code = String(detail || '').trim().toLowerCase();
            if (!code) {
                return null;
            }
            if (code === 'data_locked_for_plan') {
                const latencySeconds = Number(response?.headers?.get?.('X-API-Latency-Seconds'));
                const planCode = response?.headers?.get?.('X-API-Plan');
                const waitHint = Number.isFinite(latencySeconds) && latencySeconds > 0
                    ? `Wait about ${formatLatency(latencySeconds)} for your ${planCode || 'plan'} window to unlock the latest run. `
                    : '';
                const planMatrix =
                    'Free: up to 24h. Pro: about 15 minutes. Ultra: immediate access to fresh runs.';
                return `${waitHint}Fresh run data is temporarily locked for your plan. ${planMatrix}`;
            }
            return null;
        };

        const ensureFieldState = (endpointId) => {
            if (!fieldState.has(endpointId)) {
                fieldState.set(endpointId, {});
            }
            return fieldState.get(endpointId);
        };

        const ensureFieldErrors = (endpointId) => {
            if (!fieldErrors.has(endpointId)) {
                fieldErrors.set(endpointId, new Map());
            }
            return fieldErrors.get(endpointId);
        };

        const ensureFieldTouched = (endpointId) => {
            if (!fieldTouched.has(endpointId)) {
                fieldTouched.set(endpointId, new Set());
            }
            return fieldTouched.get(endpointId);
        };

        const setFieldError = (endpointId, fieldName, message) => {
            const errors = ensureFieldErrors(endpointId);
            const node = errors.get(fieldName);
            if (node) {
                node.textContent = message || '';
            }
        };

        const clearFieldErrors = (endpointId) => {
            const errors = ensureFieldErrors(endpointId);
            errors.forEach((node) => {
                node.textContent = '';
            });
        };

        const getSelectedEndpoint = () => {
            const key = elements.endpointSelect.value;
            return endpointMap.get(key) || null;
        };

        const updateDocsLink = () => {
            if (!elements.docsLink || !developerDocsBase) {
                return;
            }
            const endpoint = getSelectedEndpoint();
            const hash = endpoint ? `#endpoint-${endpoint.id}` : '#docs';
            elements.docsLink.href = `${developerDocsBase}${hash}`;
        };

        const getFieldValue = (endpointId, field) => {
            const store = fieldState.get(endpointId);
            if (store && Object.prototype.hasOwnProperty.call(store, field.name)) {
                return store[field.name];
            }
            if (sharedParams.get('endpoint') === endpointId && sharedParams.has(`field_${field.name}`)) {
                return sharedParams.get(`field_${field.name}`) || '';
            }
            return field.default ?? '';
        };

        const updateDescription = (endpoint) => {
            if (!elements.description) {
                return;
            }
            elements.description.textContent = endpoint?.description || 'Select an endpoint to see details.';
        };

        const applyFieldAttributes = (node, field) => {
            if (!node || !field) {
                return;
            }
            const tag = node.tagName?.toLowerCase();
            if (tag === 'input' || tag === 'textarea') {
                if (field.min !== undefined) {
                    node.min = field.min;
                }
                if (field.max !== undefined) {
                    node.max = field.max;
                }
                if (field.step !== undefined) {
                    node.step = field.step;
                }
                if (field.pattern) {
                    node.pattern = field.pattern;
                }
                if (field.inputmode) {
                    node.setAttribute('inputmode', field.inputmode);
                }
                if (field.min_length) {
                    node.minLength = field.min_length;
                }
                if (field.max_length) {
                    node.maxLength = field.max_length;
                }
            }
        };

        const renderFields = (endpoint) => {
            if (!elements.fields) {
                return;
            }
            elements.fields.innerHTML = '';
            if (endpoint) {
                fieldErrors.set(endpoint.id, new Map());
            }
            if (!endpoint) {
                const placeholder = document.createElement('p');
                placeholder.className = 'developer-playground__placeholder';
                placeholder.textContent = 'Parameters will appear after you pick an endpoint.';
                elements.fields.appendChild(placeholder);
                return;
            }

            const isRunPipeline = endpoint.id === 'run-pipeline';
            let modeHintNode = null;
            const fields = Array.isArray(endpoint.fields) ? endpoint.fields : [];
            const endpointMetaItems = Array.isArray(endpoint.meta_items)
                ? endpoint.meta_items.filter((item) => typeof item === 'string' && item.trim())
                : [];

            const updateModeHint = (isAdvanced) => {
                if (!modeHintNode) {
                    return;
                }
                modeHintNode.textContent = isAdvanced
                    ? 'Advanced forecast trains LSTM models alongside EWMA, so runs typically take longer.'
                    : 'Basic forecast uses EWMA only for faster turnaround.';
            };

            const renderEndpointMeta = () => {
                const meta = document.createElement('div');
                meta.className = 'developer-playground__dynamic-control developer-playground__meta';
                const title = document.createElement('p');
                title.className = 'developer-playground__label';
                title.textContent = isRunPipeline ? 'Runtime & token cost' : 'Endpoint notes';
                const list = document.createElement('ul');
                list.className = 'developer-playground__meta-list';
                const items = endpointMetaItems.length
                    ? endpointMetaItems
                    : ['Token usage and runtime vary by endpoint, payload size, and selected parameters.'];
                items.forEach((text) => {
                    const item = document.createElement('li');
                    item.textContent = text;
                    list.appendChild(item);
                });
                const hasAdvancedForecastField = fields.some((field) => field.name === 'advanced_forecast');
                if (isRunPipeline && hasAdvancedForecastField) {
                    modeHintNode = document.createElement('li');
                    modeHintNode.dataset.playgroundModeHint = 'true';
                    list.appendChild(modeHintNode);
                    updateModeHint(false);
                }
                meta.append(title, list);
                elements.fields.appendChild(meta);
            };

            renderEndpointMeta();

            if (!fields.length) {
                const placeholder = document.createElement('p');
                placeholder.className = 'developer-playground__placeholder';
                placeholder.textContent = 'This endpoint does not require parameters.';
                elements.fields.appendChild(placeholder);
                return;
            }

            const buildFieldControl = (field) => {
                const wrapper = document.createElement('div');
                wrapper.className = 'developer-playground__dynamic-control';
                const label = document.createElement('label');
                label.className = 'developer-playground__label';
                label.setAttribute('for', `playground-field-${endpoint.id}-${field.name}`);
                label.textContent = field.label || field.name;
                const controlId = `playground-field-${endpoint.id}-${field.name}`;
                const initialValue = getFieldValue(endpoint.id, field);
                let control;
                const isBoolean = field.type === 'boolean';
                if (field.type === 'select' && Array.isArray(field.options)) {
                    control = document.createElement('select');
                    control.className = 'developer-playground__select';
                    control.id = controlId;
                    const defaultValue = hasValue(initialValue)
                        ? String(initialValue)
                        : String(field.options[0]?.value ?? '');
                    field.options.forEach((option) => {
                        const optionNode = document.createElement('option');
                        optionNode.value = String(option.value);
                        optionNode.textContent = option.label || option.value;
                        if (String(optionNode.value) === defaultValue) {
                            optionNode.selected = true;
                        }
                        control.appendChild(optionNode);
                    });
                } else if (isBoolean) {
                    control = document.createElement('input');
                    control.className = 'playground-switch__input';
                    control.type = 'checkbox';
                    control.id = controlId;
                    control.checked = toBoolean(initialValue);
                    control.setAttribute('role', 'switch');
                    control.setAttribute('aria-label', field.label || field.name);
                    control.setAttribute('aria-checked', String(control.checked));

                    const track = document.createElement('span');
                    track.className = 'playground-switch__track';
                    const thumb = document.createElement('span');
                    thumb.className = 'playground-switch__thumb';

                    const switchWrapper = document.createElement('label');
                    switchWrapper.className = 'playground-switch';
                    switchWrapper.setAttribute('for', controlId);
                    switchWrapper.append(control, track, thumb);

                    const row = document.createElement('div');
                    row.className = 'developer-playground__toggle-row';
                    row.append(label, switchWrapper);
                    wrapper.appendChild(row);
                } else {
                    const isTextarea = field.type === 'textarea';
                    control = document.createElement(isTextarea ? 'textarea' : 'input');
                    const controlClasses = ['developer-playground__input'];
                    if (isTextarea) {
                        controlClasses.push('developer-playground__input--area');
                    }
                    if (field.type === 'number') {
                        controlClasses.push('developer-playground__input--numeric');
                    }
                    control.className = controlClasses.join(' ');
                    if (!isTextarea) {
                        control.type = field.type && ['number', 'text'].includes(field.type) ? field.type : 'text';
                    }
                    control.id = controlId;
                    control.placeholder = field.placeholder || '';
                    control.value = hasValue(initialValue) ? initialValue : '';
                }
                applyFieldAttributes(control, field);
                const store = ensureFieldState(endpoint.id);
                const resolvedValue =
                    field.type === 'select'
                        ? control.value
                        : field.type === 'boolean'
                            ? control.checked
                                ? 'true'
                                : 'false'
                            : control.value ?? initialValue ?? '';
                store[field.name] = resolvedValue?.trim?.() ?? resolvedValue ?? '';
                if (field.required) {
                    control.required = true;
                }
                const handleChange = () => {
                    const store = ensureFieldState(endpoint.id);
                    const value =
                        field.type === 'select'
                            ? control.value
                            : field.type === 'boolean'
                                ? control.checked
                                    ? 'true'
                                    : 'false'
                                : control.value.trim();
                    store[field.name] = value;
                    ensureFieldTouched(endpoint.id).add(field.name);
                    if (field.type === 'boolean') {
                        control.setAttribute('aria-checked', String(control.checked));
                    }
                    setError('');
                    validateEndpointValues(endpoint);
                    if (field.name === 'advanced_forecast' && isRunPipeline) {
                        updateModeHint(control.checked);
                    }
                };
                const eventName = field.type === 'select' || field.type === 'boolean' ? 'change' : 'input';
                control.addEventListener(eventName, handleChange);
                if (!isBoolean) {
                    wrapper.appendChild(label);
                    wrapper.appendChild(control);
                }
                if (field.help) {
                    const help = document.createElement('p');
                    help.className = 'developer-playground__help';
                    help.textContent = field.help;
                    wrapper.appendChild(help);
                }
                if (field.name === 'advanced_forecast' && isRunPipeline) {
                    const hint = document.createElement('p');
                    hint.className = 'developer-playground__help';
                    hint.textContent =
                        'Use this when you need the LSTM + EWMA mix. It improves signal quality but adds runtime.';
                    wrapper.appendChild(hint);
                    updateModeHint(control.checked);
                }
                const errors = ensureFieldErrors(endpoint.id);
                const error = document.createElement('p');
                error.className = 'developer-playground__field-error';
                error.dataset.playgroundFieldError = field.name;
                errors.set(field.name, error);
                wrapper.appendChild(error);
                return wrapper;
            };

            const renderList = (list, target) => {
                list.forEach((field) => {
                    target.appendChild(buildFieldControl(field));
                });
            };

            const applyPresetValues = (preset) => {
                if (!preset || !preset.values) {
                    return;
                }
                const store = ensureFieldState(endpoint.id);
                Object.entries(preset.values).forEach(([fieldName, rawValue]) => {
                    const targetField = fields.find((entry) => entry.name === fieldName);
                    if (!targetField) {
                        return;
                    }
                    const controlId = `playground-field-${endpoint.id}-${fieldName}`;
                    const control = elements.fields?.querySelector(`#${controlId}`);
                    const stringValue =
                        rawValue === null || rawValue === undefined ? '' : String(rawValue);
                    store[fieldName] = stringValue;
                    if (control) {
                        control.value = stringValue;
                    }
                    setFieldError(endpoint.id, fieldName, '');
                });
                setError('');
            };

            const renderPresetPicker = (target) => {
                if (
                    !endpoint ||
                    endpoint.id !== 'run-pipeline' ||
                    !Array.isArray(endpoint.strategy_presets) ||
                    !endpoint.strategy_presets.length
                ) {
                    return null;
                }
                const control = document.createElement('div');
                control.className = 'developer-playground__dynamic-control developer-playground__presets';
                const label = document.createElement('label');
                label.className = 'developer-playground__label';
                label.setAttribute('for', `playground-preset-${endpoint.id}`);
                label.textContent = 'Strategy preset';
                const select = document.createElement('select');
                select.className = 'developer-playground__select';
                select.id = `playground-preset-${endpoint.id}`;
                const defaultPreset =
                    endpoint.strategy_presets.find((entry) => entry.id === 'balanced') ||
                    endpoint.strategy_presets[0];
                endpoint.strategy_presets.forEach((preset) => {
                    const option = document.createElement('option');
                    option.value = preset.id;
                    option.textContent = preset.label || preset.id;
                    if (defaultPreset && defaultPreset.id === preset.id) {
                        option.selected = true;
                    }
                    select.appendChild(option);
                });
                select.addEventListener('change', () => {
                    const preset = endpoint.strategy_presets.find(
                        (entry) => String(entry.id) === select.value,
                    );
                    if (!preset) {
                        return;
                    }
                    applyPresetValues(preset);
                    validateEndpointValues(endpoint);
                });
                const help = document.createElement('p');
                help.className = 'developer-playground__help';
                help.textContent = 'Prefill risk and allocation knobs without manual tuning.';
                control.append(label, select, help);
                target.appendChild(control);
                return { select, defaultPreset };
            };

            const requiredFields = fields.filter((field) => field.required);
            const optionalFields = fields.filter((field) => !field.required);
            let presetDefaults = null;

            if (requiredFields.length) {
                const group = document.createElement('div');
                group.className = 'developer-playground__group';
                const label = document.createElement('p');
                label.className = 'developer-playground__group-label';
                label.textContent = 'Required parameters';
                const list = document.createElement('div');
                list.className = 'developer-playground__dynamic-list';
                const presetResult = renderPresetPicker(list);
                if (presetResult) {
                    presetDefaults = presetResult;
                }
                renderList(requiredFields, list);
                group.appendChild(label);
                group.appendChild(list);
                elements.fields.appendChild(group);
            }

            if (optionalFields.length) {
                const details = document.createElement('details');
                details.className = 'developer-playground__advanced';
                const summary = document.createElement('summary');
                summary.className = 'developer-playground__advanced-summary';
                summary.textContent = 'Advanced parameters (optional)';
                const list = document.createElement('div');
                list.className = 'developer-playground__dynamic-list';
                renderList(optionalFields, list);
                details.appendChild(summary);
                details.appendChild(list);
                if (!requiredFields.length) {
                    details.open = true;
                }
                elements.fields.appendChild(details);
            }

            if (presetDefaults?.defaultPreset) {
                applyPresetValues(presetDefaults.defaultPreset);
                validateEndpointValues(endpoint);
            }
        };

        const hydrateFromShare = () => {
            const sharedEndpoint = sharedParams.get('endpoint');
            if (sharedEndpoint && endpointMap.has(sharedEndpoint)) {
                elements.endpointSelect.value = sharedEndpoint;
            }
        };

        const updateMeta = (endpoint, compiledPath) => {
            if (elements.method) {
                elements.method.textContent = endpoint?.method || 'GET';
            }
            if (elements.url) {
                elements.url.textContent = compiledPath || config.base_url || '';
            }
        };

        const compileUrl = (endpoint, options = {}) => {
            if (!endpoint) {
                return null;
            }
            const values = ensureFieldState(endpoint.id);
            let path = endpoint.path || '/';
            const displayPath = endpoint.path || path;
            const missing = [];
            (endpoint.fields || []).forEach((field) => {
                const value = values[field.name];
                if (field.location === 'path') {
                    if (hasValue(value)) {
                        const stringValue = typeof value === 'string' ? value : String(value);
                        path = path.replace(`{${field.name}}`, encodeURIComponent(stringValue));
                    } else if (field.required) {
                        missing.push(field.label || field.name);
                    }
                }
            });
            if (missing.length) {
                return { error: `Provide: ${missing.join(', ')}` };
            }
            const url = new URL(path, config.base_url || window.location.origin);
            (endpoint.fields || []).forEach((field) => {
                const value = values[field.name];
                if (field.location === 'query' && hasValue(value)) {
                    url.searchParams.set(field.name, String(value));
                }
            });
            if (endpoint.id === 'run-pipeline' && options.runId && !url.searchParams.has('run_id')) {
                url.searchParams.set('run_id', options.runId);
                if (options.runIdSource) {
                    url.searchParams.set('run_id_source', options.runIdSource);
                }
            }
            const displayQuery =
                endpoint.id === 'run-pipeline'
                    ? ''
                    : (endpoint.fields || [])
                          .filter((field) => field.location === 'query' && hasValue(values[field.name]))
                          .map((field) => field.name)
                          .join('&');
            const display = displayQuery ? `${displayPath}?${displayQuery}` : displayPath;
            return { href: url.href, display };
        };

        const shouldSyncRunIdForEndpoint = (endpointId) => RUN_ID_SYNC_ENDPOINTS.has(endpointId);

        const getRunIdDefault = (endpoint) => {
            const runIdField = (endpoint?.fields || []).find((field) => field.name === 'run_id');
            return hasValue(runIdField?.default) ? String(runIdField.default).trim() : '';
        };

        const canAutofillRunId = (endpoint, store, force = false) => {
            if (!endpoint || !shouldSyncRunIdForEndpoint(endpoint.id)) {
                return false;
            }
            if (force) {
                return true;
            }
            if (ensureFieldTouched(endpoint.id).has('run_id')) {
                return false;
            }
            const currentValue = hasValue(store?.run_id) ? String(store.run_id).trim() : '';
            if (!currentValue) {
                return true;
            }
            return currentValue === getRunIdDefault(endpoint);
        };

        const applyRunIdForEndpoint = (endpointId, runId, force = false) => {
            const endpoint = endpointMap.get(endpointId);
            if (!endpoint || !shouldSyncRunIdForEndpoint(endpoint.id)) {
                return false;
            }
            const store = ensureFieldState(endpoint.id);
            if (!canAutofillRunId(endpoint, store, force)) {
                return false;
            }
            const normalizedRunId = hasValue(runId) ? String(runId).trim() : '';
            if (!normalizedRunId) {
                return false;
            }
            store.run_id = normalizedRunId;
            if (force) {
                ensureFieldTouched(endpoint.id).delete('run_id');
            }
            const controlId = `playground-field-${endpoint.id}-run_id`;
            const control = elements.fields?.querySelector(`#${controlId}`);
            if (control) {
                control.value = normalizedRunId;
            }
            setFieldError(endpoint.id, 'run_id', '');
            return true;
        };

        const fetchLatestRunId = async (token) => {
            const normalizedToken = String(token || '').trim();
            if (!normalizedToken) {
                return null;
            }
            const now = Date.now();
            if (
                latestRunIdLookup.token === normalizedToken &&
                latestRunIdLookup.runId &&
                now - latestRunIdLookup.fetchedAt < 30000
            ) {
                return latestRunIdLookup.runId;
            }
            const latestEndpoint = endpointMap.get('weights-latest');
            if (!latestEndpoint) {
                return null;
            }
            const compiled = compileUrl(latestEndpoint);
            if (!compiled || compiled.error || !compiled.href) {
                return null;
            }
            try {
                const response = await fetch(compiled.href, {
                    method: latestEndpoint.method || 'GET',
                    headers: {
                        Accept: 'application/json',
                        'X-API-Key': normalizedToken,
                    },
                });
                if (!response.ok) {
                    return null;
                }
                const payload = await response.json();
                const runId = typeof payload?.run_id === 'string' ? payload.run_id.trim() : '';
                if (!runId) {
                    return null;
                }
                latestRunIdLookup = {
                    token: normalizedToken,
                    runId,
                    fetchedAt: now,
                };
                return runId;
            } catch (_) {
                return null;
            }
        };

        const syncRunIdFromLatestAllocation = async ({ force = false } = {}) => {
            const selectedEndpoint = getSelectedEndpoint();
            if (!selectedEndpoint || !shouldSyncRunIdForEndpoint(selectedEndpoint.id)) {
                return;
            }
            const token = elements.tokenInput?.value?.trim();
            if (!token) {
                return;
            }
            const requestId = ++runIdSyncRequestId;
            const latestRunId = await fetchLatestRunId(token);
            if (requestId !== runIdSyncRequestId || !latestRunId) {
                return;
            }
            let changed = false;
            RUN_ID_SYNC_ENDPOINTS.forEach((endpointId) => {
                changed = applyRunIdForEndpoint(endpointId, latestRunId, force) || changed;
            });
            if (!changed) {
                return;
            }
            const endpointAfterSync = getSelectedEndpoint();
            if (!endpointAfterSync) {
                return;
            }
            validateEndpointValues(endpointAfterSync);
            const compilation = compileUrl(endpointAfterSync);
            if (compilation && !compilation.error) {
                updateMeta(endpointAfterSync, compilation.display);
            }
        };

        const toSnippetFieldValue = (field, rawValue) => {
            if (!hasValue(rawValue)) {
                return null;
            }
            if (field?.type === 'number') {
                const numeric = toNumber(rawValue);
                if (Number.isFinite(numeric)) {
                    return numeric;
                }
                return String(rawValue);
            }
            if (field?.type === 'boolean') {
                return toBoolean(rawValue);
            }
            return String(rawValue);
        };

        const toPythonLiteral = (value) => {
            if (typeof value === 'number') {
                return Number.isFinite(value) ? String(value) : 'None';
            }
            if (typeof value === 'boolean') {
                return value ? 'True' : 'False';
            }
            if (value === null || value === undefined) {
                return 'None';
            }
            return JSON.stringify(String(value));
        };

        const buildRunPipelineSnippet = (endpoint, language) => {
            if (!endpoint || endpoint.id !== 'run-pipeline') {
                return '';
            }
            const runParamComments = {
                n_top_coins: 'required; 30..300; total_assets <= n_top_coins',
                start_date: 'optional; YYYY-MM-DD; >= 2021-01-01; not in future',
                lookback_days: 'required; 90..720; >= window_size',
                window_size: 'required; 14..120; <= lookback_days',
                forecast_horizon: 'required; 7..60; <= window_size and <= lookback_days',
                advanced_forecast: 'optional; boolean',
                total_assets: 'required; 5..30; <= n_top_coins',
                clustering_metric: 'required; string 1..64',
                weight_cap: 'required; 0.08..0.30; >= max(0.08, 1 / total_assets)',
                risk_min_weight: 'required; 0.005..0.08; <= min(0.08, 1 / total_assets)',
                risk_max_weight: 'required; 0.12..0.45; >= risk_min_weight',
                vol_floor_ratio: 'required; 0.25..0.70',
                gating_tolerance: 'required; 0.00..0.10',
                run_id: 'optional; 3..64, pattern ^[A-Za-z0-9_.-]+$',
            };
            const fields = Array.isArray(endpoint.fields) ? endpoint.fields : [];
            const values = ensureFieldState(endpoint.id);
            const queryFields = fields.filter((field) => field.location === 'query');
            const queryEntries = [];
            queryFields.forEach((field) => {
                const rawValue = values[field.name];
                if (!hasValue(rawValue)) {
                    return;
                }
                const parsedValue = toSnippetFieldValue(field, rawValue);
                if (parsedValue === null) {
                    return;
                }
                queryEntries.push([field.name, parsedValue]);
            });
            const endpointUrl = new URL(endpoint.path || '/run/async', config.base_url || window.location.origin);
            const basePath = endpointUrl.pathname.replace(/\/run\/async$/, '') || '/';
            const rawRequestPath = endpointUrl.pathname.slice(basePath.length) || '/run/async';
            const requestPath = rawRequestPath.startsWith('/') ? rawRequestPath : `/${rawRequestPath}`;
            const baseUrl = (config.base_url || '').replace(/\/$/, '');
            if (language === 'curl') {
                const requestUrl = new URL(endpointUrl.href);
                requestUrl.search = '';
                queryEntries.forEach(([name, value]) => {
                    requestUrl.searchParams.set(name, String(value));
                });
                return [
                    `curl -X POST "${requestUrl.href}" \\`,
                    '  -H "X-API-Key: aici_live_xxxx" \\',
                    '  -H "Accept: application/json"',
                ].join('\n');
            }
            const paramsBody = queryEntries.length
                ? `{\n${queryEntries
                      .map(([name, value]) => {
                          const comment = runParamComments[name];
                          const suffix = comment ? `  # ${comment}` : '';
                          return `    ${JSON.stringify(name)}: ${toPythonLiteral(value)},${suffix}`;
                      })
                      .join('\n')}\n}`
                : '{}';
            return [
                'import json',
                'import os',
                'import time',
                '',
                'import requests',
                '',
                'API_KEY = os.getenv("AICI_API_KEY", "aici_live_xxxx")',
                `BASE_URL = ${JSON.stringify(baseUrl)}`,
                'POLL_INTERVAL_SECONDS = 8',
                'MAX_POLLS = 60',
                'TERMINAL_STATES = {"done", "error", "cancelled"}',
                '',
                `params = ${paramsBody}`,
                '',
                'def _headers():',
                '    return {"X-API-Key": API_KEY, "Accept": "application/json"}',
                '',
                'def _request_json(method, url, *, params=None, timeout=15):',
                '    try:',
                '        response = requests.request(method, url, params=params, headers=_headers(), timeout=timeout)',
                '    except requests.RequestException as exc:',
                '        print(f"[REQUEST ERROR] {exc}")',
                '        raise SystemExit(1)',
                '',
                '    if response.ok:',
                '        return response.json()',
                '',
                '    print(f"[HTTP {response.status_code}] {response.request.method} {response.url}")',
                '    try:',
                '        detail = response.json().get("detail")',
                '    except ValueError:',
                '        detail = response.text or "<empty>"',
                '',
                '    if isinstance(detail, list):',
                '        for err in detail:',
                '            if isinstance(err, dict):',
                '                loc = ".".join(str(part) for part in err.get("loc", []))',
                '                msg = err.get("msg") or err.get("message") or "Validation error"',
                '                print(f" - {loc or \'<unknown>\'}: {msg}")',
                '            else:',
                '                print(f" - {err}")',
                '    else:',
                '        print(f" - {detail}")',
                '    raise SystemExit(1)',
                '',
                'if API_KEY == "aici_live_xxxx":',
                '    raise RuntimeError("Set AICI_API_KEY env var before running this snippet.")',
                '',
                'print("1/4 Triggering async run...")',
                'payload = _request_json(',
                '    "POST",',
                `    f"{BASE_URL}${requestPath}",`,
                '    params=params,',
                '    timeout=15,',
                ')',
                'run_id = payload["run_id"]',
                'print("   run_id:", run_id)',
                '',
                'print("2/4 Waiting for completion...")',
                'last_line = None',
                'final_state = "pending"',
                'progress = {}',
                'for attempt in range(1, MAX_POLLS + 1):',
                '    progress = _request_json(',
                '        "GET",',
                `        f"{BASE_URL}/runs/{run_id}/progress",`,
                '        timeout=15,',
                '    )',
                '    status_line = str(progress.get("status_line") or progress.get("state", "unknown"))',
                '    if status_line != last_line:',
                '        print(f"   [{attempt:02d}/{MAX_POLLS}] {status_line}")',
                '        last_line = status_line',
                '',
                '    final_state = str(progress.get("state", "unknown"))',
                '    if final_state in TERMINAL_STATES:',
                '        break',
                '    time.sleep(POLL_INTERVAL_SECONDS)',
                'else:',
                '    raise TimeoutError(f"Run did not finish after {MAX_POLLS * POLL_INTERVAL_SECONDS} seconds.")',
                '',
                'if final_state != "done":',
                '    print(f"3/4 Final state: {final_state}.")',
                '    last_message = str(progress.get("last_message") or "").strip()',
                '    if last_message:',
                '        print(f"   server message: {last_message}")',
                '    print("4/4 Snapshot is unavailable for this state.")',
                '    raise SystemExit(1)',
                '',
                'print("3/4 Fetching run snapshot...")',
                'result = _request_json(',
                '    "GET",',
                `    f"{BASE_URL}/runs/{run_id}/result",`,
                '    timeout=30,',
                ')',
                '',
                'output = {',
                '    "run_id": run_id,',
                '    "weights": result.get("weights") or {},',
                '    "perf": result.get("perf") or {},',
                '}',
                '',
                'print("4/4 Final snapshot:")',
                'print(json.dumps(output, ensure_ascii=False, indent=2))',
            ].join('\n');
        };

        const buildEndpointRequestSnippet = (endpoint, language) => {
            if (!endpoint) {
                return '';
            }
            const compiled = compileUrl(endpoint);
            if (!compiled || compiled.error || !compiled.href) {
                return '';
            }
            const method = String(endpoint.method || 'GET').toUpperCase();
            if (language === 'curl') {
                return [
                    `curl -X ${method} "${compiled.href}" \\`,
                    '  -H "X-API-Key: aici_live_xxxx" \\',
                    '  -H "Accept: application/json"',
                ].join('\n');
            }
            return [
                'import json',
                'import requests',
                '',
                'API_KEY = "aici_live_xxxx"',
                '',
                'response = requests.request(',
                `    ${JSON.stringify(method)},`,
                `    ${JSON.stringify(compiled.href)},`,
                '    headers={"X-API-Key": API_KEY, "Accept": "application/json"},',
                '    timeout=30,',
                ')',
                'response.raise_for_status()',
                'output = response.json()',
                'print(json.dumps(output, ensure_ascii=False, indent=2))',
            ].join('\n');
        };

        const buildDynamicSnippet = (endpoint, language) => {
            if (!endpoint) {
                return '';
            }
            if (endpoint.id === 'run-pipeline') {
                return buildRunPipelineSnippet(endpoint, language);
            }
            if (endpoint.id === 'weights-latest' || endpoint.id === 'run-weights' || endpoint.id === 'run-perf') {
                return buildEndpointRequestSnippet(endpoint, language);
            }
            return '';
        };

        const buildRunScopedUrl = (runId, suffix) => {
            if (!runId) {
                return null;
            }
            const endpoint = endpointMap.get('run-pipeline');
            const path = endpoint?.path || '';
            const prefix = path.includes('/run') ? path.slice(0, path.lastIndexOf('/run')) : path || '/api/v1';
            const scopedPath = `${prefix}/runs/${encodeURIComponent(runId)}/${suffix}`;
            return new URL(scopedPath, config.base_url || window.location.origin);
        };

        const buildProgressUrl = (runId) => buildRunScopedUrl(runId, 'progress');

        const buildCancelUrl = (runId) => buildRunScopedUrl(runId, 'cancel');

        const buildResultUrl = (runId) => buildRunScopedUrl(runId, 'result');

        const buildWeightsUrl = (runId) => buildRunScopedUrl(runId, 'weights');

        const buildPerfUrl = (runId) => buildRunScopedUrl(runId, 'perf');

        const progressPollDelaysMs = [2000, 5000, 8000, 12000];
        const getProgressPollDelay = (attempt) =>
            progressPollDelaysMs[Math.min(attempt, progressPollDelaysMs.length - 1)];
        const terminalProgressStates = new Set(['done', 'error', 'cancelled']);

        const createProgressHub = () => {
            const trackers = new Map();
            const clientId = `progress-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;
            const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('aici-run-progress') : null;
            let storage = null;
            try {
                storage = window.localStorage;
            } catch (_) {
                /* ignore storage errors */
            }

            const lockTtlMs = 15000;
            const makeLockKey = (runId) => `aici:progress-lock:${runId}`;
            const isTerminalState = (payload) => terminalProgressStates.has(payload?.state);

            const readLock = (runId) => {
                if (!storage) {
                    return null;
                }
                try {
                    const raw = storage.getItem(makeLockKey(runId));
                    return raw ? JSON.parse(raw) : null;
                } catch (_) {
                    return null;
                }
            };

            const writeLock = (runId) => {
                if (!storage) {
                    return;
                }
                try {
                    storage.setItem(
                        makeLockKey(runId),
                        JSON.stringify({ owner: clientId, expires_at: Date.now() + lockTtlMs }),
                    );
                } catch (_) {
                    /* ignore storage writes */
                }
            };

            const clearLock = (runId) => {
                if (!storage) {
                    return;
                }
                try {
                    const current = readLock(runId);
                    if (!current || current.owner === clientId) {
                        storage.removeItem(makeLockKey(runId));
                    }
                } catch (_) {
                    /* ignore storage cleanup */
                }
            };

            const ensureTracker = (runId) => {
                if (!trackers.has(runId)) {
                    trackers.set(runId, {
                        runId,
                        token: null,
                        payload: null,
                        listeners: new Set(),
                        pollAttempt: 0,
                        pollTimer: null,
                        polling: false,
                        leaderId: null,
                    });
                    if (trackers.size > 50) {
                        const finished = Array.from(trackers.entries()).filter(([, entry]) =>
                            isTerminalState(entry.payload),
                        );
                        const excess = Math.max(0, trackers.size - 50);
                        finished.slice(0, excess).forEach(([key]) => trackers.delete(key));
                    }
                }
                return trackers.get(runId);
            };

            const stopTracker = (tracker) => {
                if (tracker.pollTimer) {
                    window.clearTimeout(tracker.pollTimer);
                    tracker.pollTimer = null;
                }
                tracker.polling = false;
                tracker.pollAttempt = 0;
                if (tracker.leaderId === clientId) {
                    clearLock(tracker.runId);
                }
                tracker.leaderId = null;
            };

            const notify = (tracker) => {
                if (tracker.payload && channel) {
                    channel.postMessage({
                        type: 'progress',
                        runId: tracker.runId,
                        payload: tracker.payload,
                        ownerId: tracker.leaderId || clientId,
                    });
                }
                tracker.listeners.forEach((listener) => {
                    try {
                        listener(tracker.payload);
                    } catch (_) {
                        /* ignore listener errors */
                    }
                });
            };

            const acquireLock = (tracker) => {
                const lock = readLock(tracker.runId);
                const now = Date.now();
                if (lock?.expires_at && lock.expires_at > now && lock.owner && lock.owner !== clientId) {
                    tracker.leaderId = lock.owner;
                    return false;
                }
                tracker.leaderId = clientId;
                writeLock(tracker.runId);
                return true;
            };

            const scheduleRetry = (tracker) => {
                if (tracker.pollTimer || isTerminalState(tracker.payload)) {
                    return;
                }
                const delay = getProgressPollDelay(tracker.pollAttempt);
                tracker.pollAttempt = Math.min(tracker.pollAttempt + 1, progressPollDelaysMs.length - 1);
                tracker.pollTimer = window.setTimeout(() => {
                    tracker.pollTimer = null;
                    ensurePolling(tracker);
                }, delay);
            };

            const pollOnce = async (tracker) => {
                if (tracker.polling || isTerminalState(tracker.payload)) {
                    return;
                }
                if (tracker.leaderId !== clientId) {
                    scheduleRetry(tracker);
                    return;
                }
                const progressUrl = buildProgressUrl(tracker.runId);
                if (!progressUrl || !tracker.token) {
                    stopTracker(tracker);
                    return;
                }
                tracker.polling = true;
                writeLock(tracker.runId);
                try {
                    const response = await fetch(progressUrl.href, {
                        method: 'GET',
                        headers: {
                            Accept: 'application/json',
                            ...(tracker.token ? { 'X-API-Key': tracker.token } : {}),
                        },
                        cache: 'no-store',
                    });
                    if (response.ok) {
                        const payload = await response.json().catch(() => null);
                        if (payload) {
                            tracker.payload = payload;
                            tracker.pollAttempt = 0;
                            notify(tracker);
                            if (isTerminalState(payload)) {
                                stopTracker(tracker);
                                return;
                            }
                        }
                    } else {
                        tracker.pollAttempt = Math.min(tracker.pollAttempt + 1, progressPollDelaysMs.length - 1);
                    }
                } catch (_) {
                    tracker.pollAttempt = Math.min(tracker.pollAttempt + 1, progressPollDelaysMs.length - 1);
                } finally {
                    tracker.polling = false;
                }
                scheduleRetry(tracker);
            };

            const ensurePolling = (tracker) => {
                if (!tracker.listeners.size || isTerminalState(tracker.payload)) {
                    stopTracker(tracker);
                    return;
                }
                if (!tracker.token) {
                    return;
                }
                if (tracker.polling || tracker.pollTimer) {
                    return;
                }
                const lockAcquired = acquireLock(tracker);
                if (lockAcquired) {
                    void pollOnce(tracker);
                    return;
                }
                if (channel && !tracker.payload) {
                    channel.postMessage({ type: 'request_progress', runId: tracker.runId, from: clientId });
                }
                scheduleRetry(tracker);
            };

            if (channel) {
                channel.addEventListener('message', (event) => {
                    const data = event?.data;
                    if (!data || typeof data !== 'object') {
                        return;
                    }
                    if (data.type === 'progress' && data.runId) {
                        const tracker = ensureTracker(data.runId);
                        tracker.payload = data.payload || tracker.payload;
                        tracker.pollAttempt = 0;
                        tracker.leaderId = data.ownerId || tracker.leaderId;
                        notify(tracker);
                        if (isTerminalState(data.payload) && !tracker.listeners.size) {
                            stopTracker(tracker);
                        }
                    } else if (data.type === 'request_progress' && data.runId) {
                        const tracker = trackers.get(data.runId);
                        if (tracker?.payload) {
                            channel.postMessage({
                                type: 'progress',
                                runId: data.runId,
                                payload: tracker.payload,
                                ownerId: tracker.leaderId || clientId,
                            });
                        }
                    }
                });
            }

            const subscribe = (runId, token, listener) => {
                if (!runId || typeof listener !== 'function') {
                    return () => {};
                }
                const tracker = ensureTracker(runId);
                if (token) {
                    tracker.token = token;
                }
                tracker.listeners.add(listener);
                if (tracker.payload) {
                    listener(tracker.payload);
                }
                ensurePolling(tracker);
                if (channel && !tracker.payload) {
                    channel.postMessage({ type: 'request_progress', runId, from: clientId });
                }
                return () => {
                    tracker.listeners.delete(listener);
                    if (!tracker.listeners.size) {
                        stopTracker(tracker);
                    }
                };
            };

            const waitForCompletion = (runId, token, signal) => {
                if (!runId) {
                    return Promise.resolve(null);
                }
                return new Promise((resolve, reject) => {
                    let aborted = false;
                    let settled = false;
                    let handleAbort = null;
                    let unsubscribe = () => {};
                    unsubscribe = subscribe(runId, token, (payload) => {
                        if (!payload || aborted || settled) {
                            return;
                        }
                        if (isTerminalState(payload)) {
                            settled = true;
                            unsubscribe();
                            if (signal && handleAbort) {
                                signal.removeEventListener('abort', handleAbort);
                            }
                            resolve(payload);
                        }
                    });
                    handleAbort = () => {
                        if (settled) {
                            return;
                        }
                        aborted = true;
                        settled = true;
                        unsubscribe();
                        reject(new DOMException('Aborted', 'AbortError'));
                    };
                    if (signal) {
                        if (signal.aborted) {
                            handleAbort();
                            return;
                        }
                        if (!settled) {
                            signal.addEventListener('abort', handleAbort, { once: true });
                        }
                    }
                });
            };

            const stopTracking = (runId) => {
                const tracker = trackers.get(runId);
                if (!tracker) {
                    return;
                }
                stopTracker(tracker);
                tracker.listeners.clear();
                trackers.delete(runId);
            };

            return { subscribe, waitForCompletion, stopTracking };
        };

        const progressHub = createProgressHub();

        const waitForRunCompletion = (runId, token, signal) =>
            progressHub.waitForCompletion(runId, token, signal);

        const fetchRunResults = async (runId, token, signal) => {
            const resultUrl = buildResultUrl(runId);
            const headers = {
                Accept: 'application/json',
                ...(token ? { 'X-API-Key': token } : {}),
            };
            const parseError = async (response, fallback) => {
                let detail = null;
                try {
                    const json = await response.clone().json();
                    detail = json?.detail || json?.message || null;
                } catch (_) {
                    /* ignore parse errors */
                }
                const friendly = formatErrorDetail(detail, response);
                return friendly || detail || fallback;
            };
            const payload = { run_id: runId };
            let requestTokens = 0;
            if (resultUrl) {
                const resultResponse = await fetch(resultUrl.href, {
                    method: 'GET',
                    headers,
                    cache: 'no-store',
                    signal,
                });
                if (!resultResponse.ok) {
                    const message = await parseError(resultResponse, 'Failed to load results for this run.');
                    throw new Error(message);
                }
                const resultRequestTokens = parseRequestTokens(resultResponse);
                if (Number.isFinite(resultRequestTokens) && resultRequestTokens >= 0) {
                    requestTokens = Math.round(resultRequestTokens);
                }
                const resultJson = await resultResponse.json().catch(() => null);
                if (Array.isArray(resultJson?.items)) {
                    payload.items = resultJson.items;
                } else if (resultJson?.weights && typeof resultJson.weights === 'object') {
                    payload.weights = resultJson.weights;
                }
                if (resultJson?.metrics && typeof resultJson.metrics === 'object') {
                    payload.perf = resultJson.metrics;
                } else if (resultJson?.perf && typeof resultJson.perf === 'object') {
                    payload.perf = resultJson.perf;
                }
            }
            return { payload, requestTokens };
        };

        const toNumber = (value) => {
            if (typeof value === 'number') {
                return value;
            }
            if (typeof value === 'string' && value.trim() !== '') {
                const numeric = Number(value);
                return Number.isFinite(numeric) ? numeric : Number.NaN;
            }
            return Number.NaN;
        };

        const isValidDateString = (value) => {
            if (typeof value !== 'string' || !value.trim()) {
                return false;
            }
            const parts = value.split('-');
            if (parts.length !== 3) {
                return false;
            }
            const [year, month, day] = parts.map((part) => Number(part));
            if (![year, month, day].every((num) => Number.isInteger(num))) {
                return false;
            }
            const date = new Date(Date.UTC(year, month - 1, day));
            return (
                !Number.isNaN(date.getTime()) &&
                date.getUTCFullYear() === year &&
                date.getUTCMonth() === month - 1 &&
                date.getUTCDate() === day
            );
        };

        const validateFieldValue = (field, rawValue) => {
            const label = field.label || field.name;
            const value = typeof rawValue === 'string' ? rawValue.trim() : rawValue;
            if (field.required && !hasValue(value)) {
                return `${label} is required.`;
            }
            if (!hasValue(value)) {
                return null;
            }
            if (field.type === 'number') {
                const numeric = toNumber(value);
                if (!Number.isFinite(numeric)) {
                    return `${label} must be a number.`;
                }
                if (field.integer && !Number.isInteger(numeric)) {
                    return `${label} must be an integer.`;
                }
                if (field.min !== undefined && numeric < Number(field.min)) {
                    return `${label} must be at least ${field.min}.`;
                }
                if (field.max !== undefined && numeric > Number(field.max)) {
                    return `${label} must be at most ${field.max}.`;
                }
            }
            if (field.min_length && String(value).length < field.min_length) {
                return `${label} must be at least ${field.min_length} characters.`;
            }
            if (field.max_length && String(value).length > field.max_length) {
                return `${label} must be at most ${field.max_length} characters.`;
            }
            if (field.pattern) {
                const pattern = new RegExp(field.pattern);
                if (!pattern.test(String(value))) {
                    return label.toLowerCase().includes('date')
                        ? 'Start date must match YYYY-MM-DD.'
                        : `${label} format is invalid.`;
                }
            }
            if (Array.isArray(field.options) && field.options.length) {
                const allowed = field.options.some((option) => String(option.value) === String(value));
                if (!allowed) {
                    return `${label} must match one of the available options.`;
                }
            }
            if (field.name === 'start_date' && typeof value === 'string' && value.trim()) {
                if (!isValidDateString(value.trim())) {
                    return 'Start date must be a valid calendar date (YYYY-MM-DD).';
                }
            }
            return null;
        };

        const validateEndpointValues = (endpoint) => {
            if (!endpoint || !Array.isArray(endpoint.fields) || !endpoint.fields.length) {
                return null;
            }
            const values = ensureFieldState(endpoint.id);
            let firstError = null;
            endpoint.fields.forEach((field) => {
                const message = validateFieldValue(field, values[field.name]);
                setFieldError(endpoint.id, field.name, message);
                if (!firstError && message) {
                    firstError = message;
                }
            });
            if (endpoint.id === 'run-pipeline') {
                const nTopCoins = toNumber(values.n_top_coins);
                const totalAssets = toNumber(values.total_assets);
                const minWeight = toNumber(values.risk_min_weight);
                const maxWeight = toNumber(values.risk_max_weight);
                const weightCap = toNumber(values.weight_cap);
                const lookbackDays = toNumber(values.lookback_days);
                const windowSize = toNumber(values.window_size);
                const forecastHorizon = toNumber(values.forecast_horizon);
                const startDateValue = typeof values.start_date === 'string' ? values.start_date.trim() : '';
                if (Number.isFinite(nTopCoins) && Number.isFinite(totalAssets) && totalAssets > nTopCoins) {
                    const message = 'Total assets must be less or equal to n_top_coins.';
                    setFieldError(endpoint.id, 'total_assets', message);
                    if (!firstError) {
                        firstError = message;
                    }
                }
                if (Number.isFinite(minWeight) && Number.isFinite(maxWeight) && maxWeight < minWeight) {
                    const message = 'Risk parity max weight must be greater or equal to min weight.';
                    setFieldError(endpoint.id, 'risk_max_weight', message);
                    if (!firstError) {
                        firstError = message;
                    }
                }
                if (Number.isFinite(minWeight) && Number.isFinite(totalAssets)) {
                    if (minWeight * totalAssets > 1 + 1e-9) {
                        const message = 'risk_min_weight is too high for the asset count (sum exceeds 1).';
                        setFieldError(endpoint.id, 'risk_min_weight', message);
                        if (!firstError) {
                            firstError = message;
                        }
                    } else {
                        const maxRiskMin = Math.min(0.08, 1 / totalAssets);
                        if (minWeight > maxRiskMin + 1e-9) {
                            const message = 'risk_min_weight exceeds min(0.08, 1 / total_assets).';
                            setFieldError(endpoint.id, 'risk_min_weight', message);
                            if (!firstError) {
                                firstError = message;
                            }
                        }
                    }
                }
                if (Number.isFinite(weightCap) && Number.isFinite(totalAssets)) {
                    const minCap = Math.max(0.08, 1 / totalAssets);
                    if (weightCap < minCap - 1e-9) {
                        const message = 'weight_cap must be at least max(0.08, 1 / total_assets).';
                        setFieldError(endpoint.id, 'weight_cap', message);
                        if (!firstError) {
                            firstError = message;
                        }
                    }
                }
                if (Number.isFinite(lookbackDays) && Number.isFinite(windowSize) && lookbackDays < windowSize) {
                    const message = 'lookback_days must be greater or equal to window_size.';
                    setFieldError(endpoint.id, 'lookback_days', message);
                    if (!firstError) {
                        firstError = message;
                    }
                }
                if (Number.isFinite(forecastHorizon) && Number.isFinite(windowSize) && forecastHorizon > windowSize) {
                    const message = 'forecast_horizon must be less or equal to window_size.';
                    setFieldError(endpoint.id, 'forecast_horizon', message);
                    if (!firstError) {
                        firstError = message;
                    }
                }
                if (Number.isFinite(forecastHorizon) && Number.isFinite(lookbackDays) && forecastHorizon > lookbackDays) {
                    const message = 'forecast_horizon must be less or equal to lookback_days.';
                    setFieldError(endpoint.id, 'forecast_horizon', message);
                    if (!firstError) {
                        firstError = message;
                    }
                }
                if (startDateValue && isValidDateString(startDateValue)) {
                    const [year, month, day] = startDateValue.split('-').map((part) => Number(part));
                    const startDate = new Date(Date.UTC(year, month - 1, day));
                    const today = new Date();
                    const todayUtc = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
                    const minDate = new Date(Date.UTC(2021, 0, 1));
                    if (startDate > todayUtc) {
                        const message = 'Start date cannot be in the future.';
                        setFieldError(endpoint.id, 'start_date', message);
                        if (!firstError) {
                            firstError = message;
                        }
                    } else if (startDate < minDate) {
                        const message = 'start_date cannot be earlier than 2021-01-01';
                        setFieldError(endpoint.id, 'start_date', message);
                        if (!firstError) {
                            firstError = message;
                        }
                    } else {
                        const lookback = Number.isFinite(lookbackDays) ? lookbackDays : 0;
                        const maxHistoryDays = Math.max(lookback, 120);
                        const latestAllowed = new Date(todayUtc);
                        latestAllowed.setUTCDate(latestAllowed.getUTCDate() - maxHistoryDays);
                        if (startDate > latestAllowed) {
                            const message = 'Start date is too recent for the requested history window.';
                            setFieldError(endpoint.id, 'start_date', message);
                            if (!firstError) {
                                firstError = message;
                            }
                        } else if (Number.isFinite(lookbackDays)) {
                            const earliest = new Date(Date.UTC(2015, 0, 1));
                            const windowStart = new Date(startDate);
                            windowStart.setUTCDate(windowStart.getUTCDate() - lookbackDays);
                            if (windowStart < earliest) {
                                const message = 'lookback_days window exceeds available history from 2015-01-01.';
                                setFieldError(endpoint.id, 'start_date', message);
                                if (!firstError) {
                                    firstError = message;
                                }
                            }
                        }
                    }
                }
            }
            return firstError;
        };

        const toWeightItems = (payload) => {
            if (Array.isArray(payload?.items)) {
                return payload.items;
            }
            if (payload?.weights && typeof payload.weights === 'object') {
                return Object.entries(payload.weights).map(([asset, weight]) => ({
                    asset,
                    weight: Number(weight),
                }));
            }
            return [];
        };

        const renderPipelineRun = (container, endpoint, payload) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'playground-run';

            const metaRow = document.createElement('div');
            metaRow.className = 'playground-run__meta-row';
            const runId = document.createElement('p');
            runId.className = 'playground-run__meta';
            runId.textContent = payload?.run_id
                ? `Run ID: ${payload.run_id}`
                : 'Run ID appears after the pipeline finishes.';
            metaRow.appendChild(runId);

            const elapsedLabel = formatElapsedLabel(lastPipelineElapsedSeconds);
            if (elapsedLabel) {
                const elapsed = document.createElement('p');
                elapsed.className = 'playground-run__meta playground-run__meta--time';
                elapsed.textContent = `Elapsed: ${elapsedLabel}`;
                metaRow.appendChild(elapsed);
            }

            const tokenCostLabel = formatTokenCostLabel(lastPipelineTokenCost);
            if (tokenCostLabel) {
                const tokenCost = document.createElement('p');
                tokenCost.className = 'playground-run__meta playground-run__meta--cost';
                tokenCost.textContent = tokenCostLabel;
                metaRow.appendChild(tokenCost);
            }

            wrapper.appendChild(metaRow);

            const items = toWeightItems(payload);
            const hasMetrics = payload?.perf && Object.keys(payload.perf || {}).length > 0;

            if (items.length) {
                renderWeightsChart(wrapper, endpoint, { items });
            }

            if (hasMetrics) {
                renderPerfCards(wrapper, endpoint, { metrics: payload.perf });
            }

            if (!items.length && !hasMetrics) {
                const placeholder = document.createElement('p');
                placeholder.className = 'developer-playground__placeholder';
                placeholder.textContent = 'Run the pipeline to see weights and performance metrics.';
                wrapper.appendChild(placeholder);
            }

            container.appendChild(wrapper);
        };

        const renderResponse = (endpoint, payload) => {
            elements.chart.innerHTML = '';
            if (endpoint?.visualization?.type === 'weights') {
                renderWeightsChart(elements.chart, endpoint, payload);
            } else if (endpoint?.visualization?.type === 'perf-metrics') {
                renderPerfCards(elements.chart, endpoint, payload);
            } else if (endpoint?.visualization?.type === 'pipeline-run') {
                renderPipelineRun(elements.chart, endpoint, payload);
            } else {
                const placeholder = document.createElement('p');
                placeholder.className = 'developer-playground__placeholder';
                placeholder.textContent = 'Visualization unavailable for this endpoint.';
                elements.chart.appendChild(placeholder);
            }
            if (elements.json) {
                elements.json.textContent = JSON.stringify(payload ?? {}, null, 2);
            }
            hasResponseContent = true;
        };

        const scrollToResponse = () => {
            if (!elements.response) {
                return;
            }
            elements.response.scrollIntoView({ behavior: 'smooth', block: 'start' });
        };

        const pipelineStages = [
            { key: 'prep', label: 'Preparing run and resolving configuration...' },
            { key: 'download', label: 'Downloading and merging market data...' },
            { key: 'cluster', label: 'Clustering assets and filtering history...' },
            { key: 'train', label: 'Training forecasts for shortlisted assets...' },
            { key: 'optimize', label: 'Optimizing weights and computing metrics...' },
        ];
        let activeProgressCleanup = null;
        let lastPipelineElapsedSeconds = null;
        let lastPipelineTokenCost = null;
        let activeRunStartTs = null;
        let activeAbortController = null;

        const stopActiveProgress = (state = 'idle') => {
            if (typeof activeProgressCleanup === 'function') {
                activeProgressCleanup(state);
                activeProgressCleanup = null;
            }
        };

        const resetResponseContent = () => {
            stopActiveProgress();
            lastPipelineTokenCost = null;
            if (elements.chart) {
                elements.chart.innerHTML = '';
            }
            if (elements.json) {
                elements.json.textContent = '{}';
            }
            hasResponseContent = false;
            responseContextId += 1;
        };

        const createCancelButton = (onCancel) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'developer-icon-btn';
            btn.dataset.playgroundCancel = 'true';
            btn.title = 'Stop request';
            btn.setAttribute('aria-label', 'Stop request');
            const icon = document.createElement('img');
            icon.src = '/static/icons/stop_icon.svg';
            icon.alt = '';
            icon.width = 18;
            icon.height = 18;
            icon.setAttribute('aria-hidden', 'true');
            btn.appendChild(icon);
            btn.addEventListener('click', (event) => {
                event.preventDefault();
                onCancel?.();
            });
            return btn;
        };

        const startRequestProgress = (endpoint, runId, token, onCancel, options = {}) => {
            stopActiveProgress();
            if (!elements.chart) {
                return { stop: () => {}, startTracking: () => {} };
            }
            elements.chart.innerHTML = '';
            hasResponseContent = true;
            const { autoSubscribe = true } = options;
            if (!endpoint || endpoint.id !== 'run-pipeline') {
                const placeholder = document.createElement('p');
                placeholder.className = 'developer-playground__placeholder';
                placeholder.textContent = 'Request in progress...';
                if (typeof onCancel === 'function') {
                    const actions = document.createElement('div');
                    actions.className = 'developer-playground__actions developer-playground__actions--inline';
                    actions.appendChild(createCancelButton(onCancel));
                    placeholder.appendChild(actions);
                }
                elements.chart.appendChild(placeholder);
                const stop = (finalState = 'done') => {
                    if (finalState === 'error') {
                        placeholder.textContent = 'Request failed. Check the error below.';
                    } else if (finalState === 'cancelled') {
                        placeholder.textContent = 'Request cancelled.';
                    } else {
                        placeholder.textContent = 'Request completed.';
                    }
                    activeProgressCleanup = null;
                };
                activeProgressCleanup = stop;
                return { stop, startTracking: () => {} };
            }
            activeRunStartTs = Date.now();
            lastPipelineElapsedSeconds = null;
            lastPipelineTokenCost = null;

            const wrapper = document.createElement('div');
            wrapper.className = 'developer-playground__placeholder';
            const heading = document.createElement('p');
            heading.textContent = 'Pipeline is running. Waiting for API response...';
            wrapper.appendChild(heading);
            const helper = document.createElement('p');
            helper.className = 'developer-playground__help';
            helper.textContent = 'Live timer updates below while the pipeline runs.';
            wrapper.appendChild(helper);
            const list = document.createElement('ul');
            list.className = 'developer-playground__progress';
            const timers = [];
            let progressUnsubscribe = null;
            const clearTimerHandle = (id) => {
                window.clearTimeout(id);
                window.clearInterval(id);
            };
            const stagePrefix = (state) =>
                state === 'done'
                    ? '[ok]'
                    : state === 'error'
                        ? '[fail]'
                        : state === 'cancelled'
                            ? '[stop]'
                        : state === 'running'
                            ? '[run]'
                            : '[ ]';
            const items = pipelineStages.map((stage, index) => {
                const item = document.createElement('li');
                item.className = 'developer-playground__help';
                item.dataset.stageIndex = String(index);
                const labelNode = document.createElement('span');
                labelNode.className = 'developer-playground__stage-row';
                labelNode.dataset.stageLabel = stage.label;
                const spinner = document.createElement('span');
                spinner.className = 'developer-playground__spinner';
                spinner.setAttribute('aria-hidden', 'true');
                spinner.style.visibility = 'hidden';
                const textNode = document.createElement('span');
                textNode.dataset.stageText = 'true';
                textNode.className = 'developer-playground__stage-text';
                textNode.textContent = `${stagePrefix('pending')} ${stage.label}`;
                labelNode.append(spinner, textNode);
                item.appendChild(labelNode);
                list.appendChild(item);
                return { item, labelNode, textNode, spinner, stage };
            });
            const stageMessages = new Map();
            wrapper.appendChild(list);
            const elapsedNode = document.createElement('p');
            elapsedNode.className = 'developer-playground__help';
            const renderElapsed = () => {
                const elapsedSeconds = Math.floor((Date.now() - activeRunStartTs) / 1000);
                elapsedNode.textContent = `Elapsed: ${formatElapsedLabel(elapsedSeconds)}`;
            };
            renderElapsed();
            const elapsedTimerId = window.setInterval(renderElapsed, 1000);
            timers.push(elapsedTimerId);
            wrapper.appendChild(elapsedNode);
            if (typeof onCancel === 'function') {
                const actions = document.createElement('div');
                actions.className = 'developer-playground__actions developer-playground__actions--inline';
                actions.appendChild(createCancelButton(onCancel));
                wrapper.appendChild(actions);
            }
            elements.chart.appendChild(wrapper);

            const setStageState = (state) => {
                const activeKey = state === 'running' ? items[0]?.stage.key : null;
                items.forEach((entry) => {
                    const prefix = stagePrefix(state);
                    const detail = stageMessages.get(entry.stage.key);
                    const suffix = detail ? ` - ${detail}` : '';
                    const showSpinner = entry.stage.key === activeKey;
                    entry.textNode.textContent = `${prefix} ${entry.stage.label}${suffix}`;
                    entry.spinner.style.visibility = showSpinner ? 'visible' : 'hidden';
                    entry.spinner.style.opacity = showSpinner ? '1' : '0';
                    entry.item.dataset.status = state;
                });
            };
            const applyProgress = (payload) => {
                if (!payload || !Array.isArray(payload.stages)) {
                    return;
                }
                const byKey = new Map(payload.stages.map((stage) => [stage.key, stage]));
                let activeKey = null;
                items.forEach((entry) => {
                    const stagePayload = byKey.get(entry.stage.key);
                    const status = stagePayload?.status || 'running';
                    const label = stagePayload?.label || entry.stage.label;
                    if (stagePayload?.message) {
                        stageMessages.set(entry.stage.key, stagePayload.message);
                    }
                    if (status === 'running' && !activeKey) {
                        activeKey = entry.stage.key;
                    } else if (!activeKey && status === 'pending') {
                        activeKey = entry.stage.key;
                    }
                    const detail = stageMessages.get(entry.stage.key);
                    const suffix = detail ? ` - ${detail}` : '';
                    entry.textNode.textContent = `${stagePrefix(status)} ${label}${suffix}`;
                    entry.spinner.style.visibility = 'hidden';
                    entry.spinner.style.opacity = '0';
                    entry.item.dataset.status = status;
                });
                items.forEach((entry) => {
                    const showSpinner = entry.stage.key === activeKey && Boolean(activeKey);
                    entry.spinner.style.visibility = showSpinner ? 'visible' : 'hidden';
                    entry.spinner.style.opacity = showSpinner ? '1' : '0';
                });
                const latestLog =
                    Array.isArray(payload.logs) && payload.logs.length
                        ? payload.logs[payload.logs.length - 1]
                        : null;
                if (latestLog?.message) {
                    helper.textContent = latestLog.message;
                }
            };
            const startTracking = (id, authToken) => {
                if (!id) {
                    return;
                }
                if (progressUnsubscribe) {
                    progressUnsubscribe();
                    progressUnsubscribe = null;
                }
                const normalizedToken = typeof authToken === 'string' ? authToken.trim() : '';
                progressUnsubscribe = progressHub.subscribe(id, normalizedToken, (payload) => {
                    applyProgress(payload);
                    if (payload?.state && terminalProgressStates.has(payload.state)) {
                        setStageState(payload.state);
                    }
                });
            };
            if (runId && autoSubscribe) {
                startTracking(runId, token);
            }
            setStageState('running');
            let stopped = false;
            const stop = (finalState = 'done') => {
                if (stopped) {
                    return;
                }
                stopped = true;
                if (progressUnsubscribe) {
                    progressUnsubscribe();
                    progressUnsubscribe = null;
                }
                timers.forEach((id) => clearTimerHandle(id));
                timers.length = 0;
                if (activeRunStartTs !== null) {
                    lastPipelineElapsedSeconds = Math.max(
                        0,
                        Math.floor((Date.now() - activeRunStartTs) / 1000),
                    );
                }
                activeRunStartTs = null;
                setStageState(finalState);
                heading.textContent =
                    finalState === 'error'
                        ? 'Pipeline stopped. Review the error below.'
                        : finalState === 'cancelled'
                            ? 'Pipeline cancelled by user.'
                            : 'Pipeline finished. Rendering results...';
                activeProgressCleanup = null;
            };
            activeProgressCleanup = stop;
            return { stop, startTracking };
        };

        const resetResponseIfPresent = () => {
            if (!hasResponseContent) {
                return;
            }
            handleReset();
        };

        const runRequest = async () => {
            resetResponseIfPresent();
            setError('');
            const endpoint = getSelectedEndpoint();
            if (!endpoint) {
                setError('Select an endpoint.');
                return;
            }
            const validationMessage = validateEndpointValues(endpoint);
            if (validationMessage) {
                const message =
                    endpoint.id === 'run-pipeline'
                        ? 'Solve all validation errors before sending the request.'
                        : validationMessage;
                setError(message);
                return;
            }
            let runId = null;
            let runIdSource = null;
            if (endpoint.id === 'run-pipeline') {
                const values = ensureFieldState(endpoint.id);
                const manualRunId = values?.run_id;
                if (hasValue(manualRunId)) {
                    runId = String(manualRunId).trim();
                } else {
                    runId = `playground-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 6)}`;
                    runIdSource = 'auto';
                }
            }
            const compilation = compileUrl(endpoint, { runId, runIdSource });
            if (!compilation) {
                setError('Invalid request.');
                return;
            }
            if (compilation.error) {
                setError(compilation.error);
                return;
            }
            updateMeta(endpoint, compilation.display);
            if (!elements.tokenInput || !elements.tokenInput.value.trim()) {
                setError('Enter your API key.');
                return;
            }
            const token = elements.tokenInput.value.trim();
            const runContextId = responseContextId;
            if (activeAbortController) {
                activeAbortController.abort();
            }
            activeAbortController = new AbortController();
            const abortSignal = activeAbortController.signal;
            const statusMessage =
                endpoint.id === 'run-pipeline' ? 'Running pipeline...' : 'Sending request...';
            setStatus(statusMessage, 'loading');
            const cancelUrl = runId ? buildCancelUrl(runId) : null;
            const { stop: stopProgress, startTracking } = startRequestProgress(
                endpoint,
                runId,
                token,
                async () => {
                    activeAbortController?.abort();
                    if (cancelUrl) {
                        try {
                            await fetch(cancelUrl.href, {
                                method: 'POST',
                                headers: {
                                    Accept: 'application/json',
                                    ...(token ? { 'X-API-Key': token } : {}),
                                },
                            });
                        } catch (_) {
                            /* ignore cancel errors */
                        }
                    }
                    setStatus('Request cancelled', 'idle');
                    setError('Request cancelled by user.');
                },
                { autoSubscribe: endpoint.id !== 'run-pipeline' },
            );
            let hasSentRequest = false;
            try {
                hasSentRequest = true;
                const response = await fetch(compilation.href, {
                    method: endpoint.method || 'GET',
                    headers: {
                        'X-API-Key': token,
                        Accept: 'application/json',
                    },
                    signal: abortSignal,
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const detail = typeof payload?.detail === 'string' ? payload.detail : response.statusText;
                    const friendly = formatErrorDetail(detail, response);
                    throw new Error(friendly || detail || 'API error.');
                }
                if (runContextId !== responseContextId) {
                    return;
                }
                if (endpoint.id === 'run-pipeline') {
                    const triggerRequestTokens = parseRequestTokens(response);
                    lastPipelineTokenCost = Number.isFinite(triggerRequestTokens)
                        ? Math.max(0, Math.round(triggerRequestTokens))
                        : null;
                    const pipelineRunId = payload?.run_id || runId;
                    if (!pipelineRunId) {
                        throw new Error('Run ID is missing from the response.');
                    }
                    startTracking(pipelineRunId, token);
                    const completion = await waitForRunCompletion(pipelineRunId, token, abortSignal);
                    if (runContextId !== responseContextId) {
                        return;
                    }
                    if (!completion) {
                        setStatus('Request failed', 'error');
                        stopProgress('error');
                        setError('Pipeline is still running. Check progress shortly.');
                        scrollToResponse();
                        return;
                    }
                    if (completion.state === 'error') {
                        const latestMessage =
                            Array.isArray(completion.logs) && completion.logs.length
                                ? completion.logs[completion.logs.length - 1].message
                                : null;
                        setStatus('Request failed', 'error');
                        stopProgress('error');
                        setError(latestMessage || 'Pipeline failed. Review progress for details.');
                        scrollToResponse();
                        return;
                    }
                    if (completion.state === 'cancelled') {
                        stopProgress('cancelled');
                        setStatus('Request cancelled', 'idle');
                        setError('Pipeline cancelled.');
                        return;
                    }
                    const resultBundle = await fetchRunResults(pipelineRunId, token, abortSignal);
                    if (runContextId !== responseContextId) {
                        return;
                    }
                    const resultRequestTokens = Number.isFinite(resultBundle?.requestTokens)
                        ? Math.max(0, Math.round(resultBundle.requestTokens))
                        : 0;
                    if (resultRequestTokens > 0) {
                        const triggerTokens = Number.isFinite(lastPipelineTokenCost)
                            ? Math.max(0, Math.round(lastPipelineTokenCost))
                            : 0;
                        lastPipelineTokenCost = triggerTokens + resultRequestTokens;
                    }
                    setStatus('Request succeeded', 'success');
                    stopProgress('done');
                    renderResponse(endpoint, resultBundle?.payload ?? {});
                    scrollToResponse();
                    return;
                }
                setStatus('Request succeeded', 'success');
                stopProgress('done');
                renderResponse(endpoint, payload);
                scrollToResponse();
            } catch (error) {
                if (runContextId !== responseContextId) {
                    return;
                }
                if (error?.name === 'AbortError') {
                    stopProgress('cancelled');
                    setStatus('Request cancelled', 'idle');
                    setError('Request cancelled by user.');
                } else {
                    setStatus('Request failed', 'error');
                    stopProgress('error');
                    setError(error instanceof Error ? error.message : 'Unable to process the request.');
                    if (hasSentRequest) {
                        scrollToResponse();
                    }
                }
            } finally {
                activeAbortController = null;
            }
        };

        const handleSubmit = (event) => {
            event.preventDefault();
            if (elements.remember && elements.remember.checked && elements.tokenInput) {
                writeStoredKey(elements.tokenInput.value.trim());
            } else if (elements.remember && !elements.remember.checked) {
                writeStoredKey('');
            }
            void runRequest();
        };

        const handleReset = () => {
            resetResponseContent();
            setStatus('Ready to send', 'idle');
            setError('');
            const endpoint = getSelectedEndpoint();
            if (endpoint) {
                clearFieldErrors(endpoint.id);
                validateEndpointValues(endpoint);
            }
        };

        const buildShareLink = () => {
            const endpoint = getSelectedEndpoint();
            if (!endpoint) {
                return null;
            }
            const values = ensureFieldState(endpoint.id);
            const params = new URLSearchParams();
            params.set('endpoint', endpoint.id);
            (endpoint.fields || []).forEach((field) => {
                const value = values[field.name];
                if (hasValue(value)) {
                    params.set(`field_${field.name}`, String(value));
                }
            });
            return `${shareBase}?${params.toString()}`;
        };

        const handleShare = async () => {
            if (!elements.share) {
                return;
            }
            const link = buildShareLink();
            if (!link) {
                setError('Add a valid request before sharing.');
                return;
            }
            try {
                await copyToClipboard(link);
                elements.share.textContent = 'Link copied';
                setTimeout(() => {
                    elements.share.textContent = 'Share link';
                }, 2000);
            } catch (_) {
                setError('Could not copy link.');
            }
        };

        const renderSnippets = () => {
            if (!elements.snippetForm) {
                return;
            }
            const list = elements.snippetForm.querySelector('[data-snippet-list]');
            const emptyNode = elements.snippetForm.querySelector('[data-snippet-empty]');
            if (!list) {
                return;
            }

            const createIconButton = (datasetKey, label, iconPath) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'playground-snippet__icon-btn';
                btn.dataset[datasetKey] = 'true';
                btn.setAttribute('aria-label', label);
                btn.title = label;
                const icon = document.createElement('img');
                icon.src = iconPath;
                icon.alt = '';
                icon.width = 18;
                icon.height = 18;
                icon.setAttribute('aria-hidden', 'true');
                btn.appendChild(icon);
                return btn;
            };

            const buildSnippet = (snippet) => {
                const item = document.createElement('li');
                item.className = 'playground-snippet';
                item.dataset.snippetId = snippet.id;

                const header = document.createElement('div');
                header.className = 'playground-snippet__header';
                const title = document.createElement('strong');
                title.textContent = snippet.name;
                const lang = document.createElement('span');
                lang.textContent = (snippet.language || '').toUpperCase();
                header.append(title, lang);

                const meta = document.createElement('p');
                meta.className = 'playground-snippet__meta';
                const method = snippet.method || '';
                const path = snippet.path || '';
                meta.textContent = `${method} ${path}`.trim();

                const code = document.createElement('pre');
                code.className = 'playground-snippet__code';
                code.dataset.snippetCode = 'true';
                const lineCount = (snippet.code || '').split('\n').length;
                const collapsible = lineCount >= 4;
                code.dataset.collapsed = collapsible ? 'true' : 'false';
                code.textContent = snippet.code || '';

                const footer = document.createElement('div');
                footer.className = 'playground-snippet__footer';

                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'playground-snippet__toggle';
                toggle.dataset.snippetToggle = 'true';
                toggle.textContent = 'Show full code';
                toggle.setAttribute('aria-expanded', 'false');

                const actions = document.createElement('div');
                actions.className = 'playground-snippet__actions';
                actions.append(
                    createIconButton('snippetCopy', 'Copy snippet', iconPaths.copy),
                    createIconButton('snippetDelete', 'Delete snippet', iconPaths.delete),
                );

                if (collapsible) {
                    footer.append(toggle, actions);
                } else {
                    footer.append(actions);
                }
                item.append(header, meta, code, footer);
                return item;
            };

            list.innerHTML = '';
            const snippets = readSnippets();
            if (!snippets.length) {
                if (emptyNode) {
                    emptyNode.hidden = false;
                    list.appendChild(emptyNode);
                }
                return;
            }
            if (emptyNode) {
                emptyNode.hidden = true;
            }
            snippets.forEach((snippet) => {
                list.appendChild(buildSnippet(snippet));
            });
        };

        const saveSnippet = () => {
            if (!elements.snippetForm) {
                return;
            }
            const nameInput = elements.snippetForm.querySelector('[data-snippet-name]');
            const langSelect = elements.snippetForm.querySelector('[data-snippet-language]');
            const statusNode = elements.snippetForm.querySelector('[data-snippet-status]');
            const endpoint = getSelectedEndpoint();
            if (!endpoint || !nameInput || !langSelect) {
                return;
            }
            const doc = docsIndex.get(endpoint.id);
            const lang = langSelect.value || 'python';
            const dynamicSnippetCode = buildDynamicSnippet(endpoint, lang);
            const snippetCode =
                dynamicSnippetCode ||
                (lang === 'curl' ? doc?.curl_snippet : doc?.python_snippet || doc?.curl_snippet || '');
            const isPipelineSnippet = endpoint.id === 'run-pipeline';
            const compiled = compileUrl(endpoint);
            let snippetPath = doc?.path || endpoint.path || '/';
            if (!isPipelineSnippet && compiled?.href) {
                try {
                    const snippetUrl = new URL(compiled.href);
                    snippetPath = `${snippetUrl.pathname}${snippetUrl.search}`;
                } catch (_) {
                    /* ignore invalid url */
                }
            }
            if (isPipelineSnippet) {
                snippetPath = '/api/v1/run/async';
            }
            if (!snippetCode) {
                if (statusNode) {
                    statusNode.textContent = 'No sample available for this endpoint.';
                }
                return;
            }
            const name = nameInput.value.trim();
            if (!name) {
                if (statusNode) {
                    statusNode.textContent = 'Name your snippet.';
                }
                return;
            }
            const snippets = readSnippets();
            snippets.unshift({
                id: `${Date.now()}`,
                name,
                language: lang,
                code: snippetCode,
                method: isPipelineSnippet ? 'POST' : doc?.method || endpoint.method || 'GET',
                path: snippetPath,
            });
            writeSnippets(snippets.slice(0, 10));
            nameInput.value = '';
            if (statusNode) {
                statusNode.textContent = 'Saved.';
                setTimeout(() => {
                    statusNode.textContent = '';
                }, 2000);
            }
            renderSnippets();
        };

        const attachSnippetListeners = () => {
            if (!elements.snippetForm) {
                return;
            }
            const saveButton = elements.snippetForm.querySelector('[data-snippet-save]');
            const clearButton = elements.snippetForm.querySelector('[data-snippet-clear]');
            if (saveButton) {
                saveButton.addEventListener('click', saveSnippet);
            }
            if (clearButton) {
                clearButton.addEventListener('click', () => {
                    const confirmed = window.confirm('Clear all saved snippets? This cannot be undone.');
                    if (!confirmed) {
                        return;
                    }
                    writeSnippets([]);
                    renderSnippets();
                });
            }
            const list = elements.snippetForm.querySelector('[data-snippet-list]');
            list?.addEventListener('click', async (event) => {
                const toggleBtn = event.target.closest('[data-snippet-toggle]');
                if (toggleBtn) {
                    const item = toggleBtn.closest('[data-snippet-id]');
                    const codeBlock = item?.querySelector('[data-snippet-code]');
                    if (codeBlock) {
                        const isCollapsed = codeBlock.dataset.collapsed !== 'false';
                        codeBlock.dataset.collapsed = isCollapsed ? 'false' : 'true';
                        toggleBtn.setAttribute('aria-expanded', String(isCollapsed));
                        toggleBtn.textContent = isCollapsed ? 'Hide code' : 'Show full code';
                    }
                    return;
                }
                const copyBtn = event.target.closest('[data-snippet-copy]');
                if (copyBtn) {
                    const item = copyBtn.closest('[data-snippet-id]');
                    const snippetId = item?.dataset.snippetId;
                    if (!snippetId) {
                        return;
                    }
                    const snippet = readSnippets().find((entry) => entry.id === snippetId);
                    if (snippet) {
                        await copyToClipboard(snippet.code);
                    }
                    return;
                }
                const deleteBtn = event.target.closest('[data-snippet-delete]');
                if (deleteBtn) {
                    const target = deleteBtn.closest('[data-snippet-id]');
                    const snippetId = target?.dataset.snippetId;
                    if (!snippetId) {
                        return;
                    }
                    const snippets = readSnippets().filter((entry) => entry.id !== snippetId);
                    writeSnippets(snippets);
                    renderSnippets();
                }
            });
            renderSnippets();
        };

        const copyButtons = root.querySelectorAll('[data-copy-target]');
        const setCopyButtonState = (button, state) => {
            const defaultTitle = button.dataset.copyTitle || 'Copy';
            const copiedTitle = button.dataset.copiedTitle || 'Copied';
            const errorTitle = button.dataset.copyErrorTitle || 'Copy failed';
            const label = state === 'success' ? copiedTitle : state === 'error' ? errorTitle : defaultTitle;
            button.dataset.state = state;
            button.title = label;
            button.setAttribute('aria-label', label);
        };
        copyButtons.forEach((button) => {
            setCopyButtonState(button, 'idle');
            button.addEventListener('click', async () => {
                const targetId = button.dataset.copyTarget;
                if (!targetId) {
                    return;
                }
                const targetNode = document.getElementById(targetId);
                if (!targetNode) {
                    return;
                }
                try {
                    await copyToClipboard(targetNode.textContent || '');
                    setCopyButtonState(button, 'success');
                    setTimeout(() => {
                        setCopyButtonState(button, 'idle');
                    }, 2000);
                } catch (_) {
                    setCopyButtonState(button, 'error');
                    setTimeout(() => {
                        setCopyButtonState(button, 'idle');
                    }, 2000);
                }
            });
        });
        elements.form.addEventListener('submit', handleSubmit);
        elements.reset?.addEventListener('click', handleReset);
        elements.endpointSelect.addEventListener('change', () => {
            resetResponseIfPresent();
            const endpoint = getSelectedEndpoint();
            updateDescription(endpoint);
            renderFields(endpoint);
            setError('');
            const compilation = compileUrl(endpoint);
            if (compilation && !compilation.error) {
                updateMeta(endpoint, compilation.display);
            }
            if (endpoint) {
                validateEndpointValues(endpoint);
            }
            updateDocsLink();
            void syncRunIdFromLatestAllocation();
        });
        elements.share?.addEventListener('click', handleShare);

        const storedKey = readStoredKey();
        if (storedKey && elements.tokenInput) {
            elements.tokenInput.value = storedKey;
            if (elements.remember) {
                elements.remember.checked = true;
            }
        }
        updateTokenVisibility(false);
        elements.tokenToggle?.addEventListener('click', () => {
            updateTokenVisibility(!isTokenVisible);
        });
        elements.tokenInput?.addEventListener('change', () => {
            latestRunIdLookup = { token: '', runId: '', fetchedAt: 0 };
            void syncRunIdFromLatestAllocation();
        });

        hydrateFromShare();
        const endpoint = getSelectedEndpoint();
        updateDescription(endpoint);
        renderFields(endpoint);
        const initialCompilation = compileUrl(endpoint);
        if (initialCompilation && !initialCompilation.error) {
            updateMeta(endpoint, initialCompilation.display);
        }
        if (endpoint) {
            validateEndpointValues(endpoint);
        }
        void syncRunIdFromLatestAllocation();
        attachSnippetListeners();
        updateDocsLink();
    };

    const initAll = () => {
        const playgrounds = document.querySelectorAll('[data-api-playground]');
        playgrounds.forEach((node) => initPlayground(node));
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }

    window.addEventListener('aici:playground-secret', (event) => {
        const secret = event?.detail?.secret || '';
        if (!secret) {
            return;
        }
        instances.forEach((instance) => instance.setToken(secret));
        const rememberChecked = Array.from(document.querySelectorAll('[data-playground-remember]')).some(
            (node) => node.checked,
        );
        if (rememberChecked) {
            writeStoredKey(secret);
        }
    });
})();
