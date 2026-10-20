/* ==========================================================================
   SuperStar Web Panel — 前端逻辑
   结构：常量 → 全局状态 → 工具函数 → 请求封装 → 视图切换
         → 配置 → 登录 → 课程 → 学习 → 日志 → 题库 → 初始化
   ========================================================================== */
'use strict';

/* ---------- 常量 ---------- */
// AI 相关配置项在界面上隐藏，保存时按原值回填，避免被覆盖丢失
const AI_CONFIG_KEYS = new Set([
    'check_llm_connection', 'siliconflow_key', 'siliconflow_model',
    'siliconflow_endpoint', 'endpoint', 'key', 'model', 'http_proxy',
]);
const CONFIG_SECTIONS = [
    { id: 'common', label: '通用设置' },
    { id: 'tiku', label: '题库设置' },
    { id: 'notification', label: '通知设置' },
];
const MULTILINE_CONFIG_KEYS = new Set(['course_list']);
// 敏感配置项：以密码框形式渲染，点击眼睛图标可临时查看，避免明文直接暴露
const SENSITIVE_CONFIG_KEYS = new Set(['password', 'tokens', 'key', 'api_key', 'secret', 'siliconflow_key']);
const CONFIG_MASK = '********';
const MAX_LOG_ENTRIES = 500;          // 日志 DOM 上限，防止长时间运行拖慢页面
const STUDY_POLL_MS = 2000;           // 学习状态轮询间隔
const SAVE_MSG_RESET_MS = 3000;       // 保存结果提示停留时长
const STOP_FORCE_MS = 45000;          // 停止等待超过该时长则自动升级为强制收尾
const STUDY_STATUS_MAP = {
    idle: { text: '空闲', cls: 'idle' },
    running: { text: '运行中', cls: 'running' },
    completed: { text: '已完成', cls: 'completed' },
    error: { text: '错误', cls: 'error' },
    stopped: { text: '已停止', cls: 'idle' },
    stopping: { text: '正在停止...', cls: 'stopping' },
};

/* ---------- 全局状态 ---------- */
const state = {
    view: null,
    config: null,
    courses: [],
    selectedCourseIds: new Set(),
    cacheItems: [],
    logCount: 0,
    studyPollTimer: null,
    elapsedTimer: null,
    lastStartTime: 0,
    stopRequestedAt: 0,   // 点下"停止"的时间戳，用于兜底重试
    forcingStop: false,
};
let logEventSource = null;

/* ---------- 工具函数 ---------- */
const $ = (id) => document.getElementById(id);

// 同时转义引号，保证结果可安全用于 HTML 属性与文本
function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function debounce(fn, delay) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

function showToast(message, type = 'info') {
    const container = $('toastContainer');
    if (!container) return;
    const colors = { info: 'bg-info', success: 'bg-success', warning: 'bg-warning', error: 'bg-danger' };
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    container.insertAdjacentHTML('beforeend', `
        <div id="${id}" class="toast ${colors[type] || colors.info}" role="alert">
            <div class="toast-body d-flex justify-content-between align-items-center">
                <span>${escapeHtml(message)}</span>
                <button type="button" class="btn-close btn-close-white ms-3" data-bs-dismiss="toast" aria-label="关闭"></button>
            </div>
        </div>`);
    const el = $(id);
    const toast = new bootstrap.Toast(el, { delay: 4000 });
    toast.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
}

function setButtonLoading(button, isLoading, loadingText) {
    if (!button) return;
    if (isLoading) {
        button.dataset.originalText = button.innerHTML;
        button.disabled = true;
        button.textContent = loadingText || '处理中...';
    } else {
        button.disabled = false;
        if (button.dataset.originalText) {
            button.innerHTML = button.dataset.originalText;
            delete button.dataset.originalText;
        }
    }
}

/* ---------- API 请求封装（带网络异常兜底） ---------- */
async function apiFetch(url, options = {}) {
    const config = {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
    };
    if (config.body && typeof config.body === 'object' && !(config.body instanceof FormData)) {
        config.body = JSON.stringify(config.body);
    }
    try {
        const res = await fetch(url, config);
        const contentType = res.headers.get('content-type') || '';
        const data = contentType.includes('application/json')
            ? await res.json()
            : { status: 'error', msg: await res.text() };
        if (!res.ok && data.status !== 'error') {
            data.status = 'error';
            data.msg = data.msg || `请求失败 (${res.status})`;
        }
        return data;
    } catch (err) {
        return { status: 'error', msg: `网络请求失败: ${err.message || err}` };
    }
}

/* ---------- 视图切换 ---------- */
const VIEW_LOADERS = {
    auth: () => checkLoginStatus(),
    courses: () => loadCourses(),
    study: () => refreshStudyStatus(),
    tiku: () => loadCache(),
    config: () => loadConfig(),
};

function switchView(viewName) {
    if (!$(`view-${viewName}`)) viewName = 'auth';
    document.querySelectorAll('.view-section').forEach((el) => el.classList.add('d-none'));
    $(`view-${viewName}`).classList.remove('d-none');
    state.view = viewName;
    localStorage.setItem('superstar.activeView', viewName);

    document.querySelectorAll('[data-view]').forEach((el) => {
        el.classList.toggle('active', el.dataset.view === viewName);
    });

    VIEW_LOADERS[viewName]?.();
}

/* ---------- 配置 ---------- */
async function loadConfig() {
    const result = await apiFetch('/api/config');
    if (result.status === 'ok') {
        state.config = result.data;
        renderConfigForm();
    } else {
        $('configForm').innerHTML =
            `<div class="alert alert-danger mb-0">加载配置失败: ${escapeHtml(result.msg)}</div>`;
    }
}

function buildConfigFieldHtml(section, key, value) {
    const id = `cfg-${section}-${key}`;
    const label = escapeHtml(key);
    const attrs = `id="${id}" data-section="${section}" data-key="${key}"`;
    if (value === 'true' || value === 'false') {
        return `<div class="config-field form-check form-switch">
            <input class="form-check-input" type="checkbox" ${attrs} ${value === 'true' ? 'checked' : ''}>
            <label class="form-check-label" for="${id}">${label}</label>
        </div>`;
    }
    if (MULTILINE_CONFIG_KEYS.has(key)) {
        return `<div class="config-field config-field-wide">
            <label for="${id}" class="form-label small">${label}</label>
            <textarea class="form-control form-control-sm" ${attrs} rows="2">${escapeHtml(value)}</textarea>
        </div>`;
    }
    const isSensitive = SENSITIVE_CONFIG_KEYS.has(key);
    if (isSensitive) {
        // 敏感项：密码框渲染（后端已脱敏），点击眼睛图标可临时切换显示
        const shown = value && value !== CONFIG_MASK;
        return `<div class="config-field config-field-secret">
            <label for="${id}" class="form-label small">${label} <span class="text-warning small">🔒 已隐藏</span></label>
            <div class="secret-wrap">
                <input type="${shown ? 'text' : 'password'}" class="form-control form-control-sm" ${attrs} value="${escapeHtml(value)}" placeholder="已隐藏，重新输入以修改" autocomplete="off">
                <button type="button" class="secret-eye" data-eye-for="${id}" title="显示 / 隐藏">👁</button>
            </div>
        </div>`;
    }
    return `<div class="config-field">
        <label for="${id}" class="form-label small">${label}</label>
        <input type="text" class="form-control form-control-sm" ${attrs} value="${escapeHtml(value)}">
    </div>`;
}

function renderConfigForm() {
    const container = $('configForm');
    if (!container || !state.config) return;

    const html = [];
    for (const { id: section, label } of CONFIG_SECTIONS) {
        const data = state.config[section];
        if (!data) continue;
        html.push(`<div class="config-section" data-section="${section}">`);
        html.push(`<h6 class="config-section-title">${label}</h6>`);
        for (const [key, value] of Object.entries(data)) {
            if (!AI_CONFIG_KEYS.has(key)) html.push(buildConfigFieldHtml(section, key, value));
        }
        html.push('</div>');
    }
    container.innerHTML = html.join('');

    // 敏感字段眼睛图标：切换明文/密文显示
    container.querySelectorAll('[data-eye-for]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const input = $(btn.dataset.eyeFor);
            if (!input) return;
            input.type = input.type === 'password' ? 'text' : 'password';
            btn.classList.toggle('active', input.type === 'text');
        });
    });

    // 重新渲染后保持用户当前所在的标签页
    const activeTab = document.querySelector('[data-config-tab].active');
    activateConfigTab(activeTab ? activeTab.dataset.configTab : 'common');
}

function activateConfigTab(section) {
    document.querySelectorAll('[data-config-tab]').forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.configTab === section);
    });
    document.querySelectorAll('.config-section').forEach((el) => {
        el.style.display = el.dataset.section === section ? 'grid' : 'none';
    });
}

async function saveConfig() {
    const btn = $('btnSaveConfig');
    setButtonLoading(btn, true, '保存中...');

    const config = {};
    // 先回填界面隐藏的 AI 相关配置
    if (state.config) {
        for (const [section, data] of Object.entries(state.config)) {
            config[section] = {};
            for (const [key, value] of Object.entries(data)) {
                if (AI_CONFIG_KEYS.has(key)) config[section][key] = value;
            }
        }
    }
    document.querySelectorAll('#configForm [data-section][data-key]').forEach((el) => {
        const { section, key } = el.dataset;
        if (!config[section]) config[section] = {};
        config[section][key] = el.type === 'checkbox' ? String(el.checked) : el.value;
    });

    try {
        const result = await apiFetch('/api/config', { method: 'POST', body: config });
        const msg = $('configSaveMsg');
        const ok = result.status === 'ok';
        msg.textContent = ok ? '已保存' : (result.msg || '保存失败');
        msg.className = `ms-2 save-msg ${ok ? 'text-success' : 'text-danger'}`;
        if (ok) showToast('配置已保存', 'success');
        setTimeout(() => { msg.textContent = ''; }, SAVE_MSG_RESET_MS);
    } finally {
        setButtonLoading(btn, false);
    }
}

/* ---------- 平台（多平台支持） ---------- */
// 平台列表由 /api/platforms 动态下发，前端不写死任何平台名。
// 新增一个平台只需要在后端注册 adapter，这里无需改动。
let platformCache = [];

function currentPlatformKey() {
    return $('platformSelect')?.value || 'chaoxing';
}

function currentPlatformMeta() {
    return platformCache.find((p) => p.key === currentPlatformKey()) || null;
}

async function loadPlatforms() {
    const result = await apiFetch('/api/platforms');
    if (result.status !== 'ok' || !Array.isArray(result.platforms)) return;
    platformCache = result.platforms;
    const select = $('platformSelect');
    if (!select) return;
    select.innerHTML = platformCache
        .map((p) => `<option value="${escapeHtml(p.key)}">${escapeHtml(p.name)}</option>`)
        .join('');
    if (result.selected) select.value = result.selected;
    applyPlatformMeta();
}

// 按当前平台刷新登录表单的文案、占位符、附加字段与 Cookie 开关
function applyPlatformMeta() {
    const meta = currentPlatformMeta();
    if (!meta) return;
    const setText = (id, text) => { const el = $(id); if (el) el.textContent = text; };
    const setAttr = (id, attr, text) => { const el = $(id); if (el) el.setAttribute(attr, text || ''); };

    setText('loginUsernameLabel', meta.account_hint || '账号');
    setText('loginPasswordLabel', meta.password_hint || '密码');
    setAttr('loginUsername', 'placeholder', `请输入${meta.account_hint || '账号'}`);
    setAttr('loginPassword', 'placeholder', `请输入${meta.password_hint || '密码'}`);
    setAttr('loginCookies', 'placeholder', meta.cookie_hint || 'key=value; key2=value2');

    const hint = $('authPlatformHint');
    if (hint) hint.textContent = `使用账号密码或 Cookie 登录${meta.name}${meta.notes ? ' · ' + meta.notes : ''}`;

    // 能力徽标：让用户一眼看出该平台支持哪些任务类型
    const capNames = {
        video: '视频', audio: '音频', document: '文档', read: '阅读',
        work: '作业', live: '直播', sign: '签到',
    };
    const caps = (meta.capabilities || []).filter((c) => capNames[c]).map((c) => capNames[c]);
    const capsEl = $('platformCaps');
    if (capsEl) capsEl.textContent = caps.length ? '支持: ' + caps.join('/') : '';

    const homeEl = $('platformHome');
    if (homeEl) {
        if (meta.home) { homeEl.href = meta.home; homeEl.classList.remove('d-none'); }
        else { homeEl.classList.add('d-none'); }
    }

    // 不支持 Cookie / 密码登录的平台直接隐藏对应卡片，避免用户做无效尝试
    const cookieBtn = $('btnCookieLogin');
    if (cookieBtn) cookieBtn.closest('.card').classList.toggle('d-none', !meta.supports_cookie_login);
    const loginForm = $('loginForm');
    if (loginForm) loginForm.closest('.card').classList.toggle('d-none', !meta.supports_password_login);

    renderPlatformExtraFields(meta);
}

// 渲染平台私有登录字段（由 adapter 的 PlatformMeta.config_fields 声明）
function renderPlatformExtraFields(meta) {
    const host = $('platformExtraFields');
    if (!host) return;
    const fields = meta.config_fields || [];
    host.innerHTML = fields.map((f) => {
        const id = `platformField_${f.key}`;
        const label = escapeHtml(f.label || f.key);
        if (f.type === 'select') {
            const opts = (f.options || []).map((o) => {
                const value = typeof o === 'string' ? o : o.value;
                const text = typeof o === 'string' ? o : (o.label || o.value);
                return `<option value="${escapeHtml(value)}">${escapeHtml(text)}</option>`;
            }).join('');
            return `<div class="field"><label class="field-lbl" for="${id}">${label}</label>`
                + `<select class="inp" id="${id}" data-field-key="${escapeHtml(f.key)}">${opts}</select></div>`;
        }
        return `<div class="field"><label class="field-lbl" for="${id}">${label}</label>`
            + `<input type="text" class="inp" id="${id}" data-field-key="${escapeHtml(f.key)}"`
            + ` placeholder="${escapeHtml(f.placeholder || '')}"></div>`;
    }).join('');
}

function collectPlatformExtras() {
    const extras = {};
    document.querySelectorAll('#platformExtraFields [data-field-key]').forEach((el) => {
        const v = (el.value || '').trim();
        if (v) extras[el.dataset.fieldKey] = v;
    });
    return extras;
}

async function handlePlatformChange() {
    applyPlatformMeta();
    await apiFetch('/api/platform/select', { method: 'POST', body: { platform: currentPlatformKey() } });
    await checkLoginStatus();
}

/* ---------- 登录 ---------- */
async function checkLoginStatus() {
    const result = await apiFetch('/api/login/status');
    const badge = $('loginStatusBadge');
    const authStatus = $('authStatus');
    const authUid = $('authUid');
    const authPlatform = $('authPlatform');
    if (!badge || !authStatus || !authUid) return;
    if (result.logged_in) {
        const label = result.username || `UID: ${result.uid}`;
        badge.innerHTML = `<span class="badge bg-success">已登录 ${escapeHtml(label)}</span>`;
        authStatus.textContent = '已登录';
        authStatus.className = 'badge bg-success';
        authUid.textContent = result.uid ? `UID: ${result.uid}` : '';
        if (authPlatform) {
            const pname = result.platform_name || result.platform || '';
            authPlatform.textContent = pname;
            authPlatform.classList.toggle('d-none', !pname);
        }
    } else {
        badge.innerHTML = '<span class="badge bg-danger">未登录</span>';
        authStatus.textContent = '未登录';
        authStatus.className = 'badge bg-danger';
        authUid.textContent = '';
        if (authPlatform) authPlatform.classList.add('d-none');
    }
}

async function handleLoginSubmit(e) {
    e.preventDefault();
    const username = $('loginUsername').value.trim();
    const password = $('loginPassword').value; // 密码不做 trim，避免误改合法的前后空格
    if (!username || !password) {
        showToast('请输入用户名和密码', 'warning');
        return;
    }
    const btn = $('btnLogin');
    setButtonLoading(btn, true, '登录中...');
    try {
        const result = await apiFetch('/api/login', {
            method: 'POST',
            body: {
                username,
                password,
                platform: currentPlatformKey(),
                extras: collectPlatformExtras(),
            },
        });
        if (result.status) {
            showToast('登录成功', 'success');
            await checkLoginStatus();
            switchView('courses');
        } else {
            showToast('登录失败: ' + (result.msg || '未知错误'), 'error');
        }
    } finally {
        setButtonLoading(btn, false);
    }
}

async function handleCookieLogin() {
    const cookies = $('loginCookies').value.trim();
    if (!cookies) {
        showToast('请输入 Cookie', 'warning');
        return;
    }
    const btn = $('btnCookieLogin');
    setButtonLoading(btn, true, '登录中...');
    try {
        const result = await apiFetch('/api/login/cookies', {
            method: 'POST',
            body: { cookies, platform: currentPlatformKey() },
        });
        if (result.status) {
            showToast('Cookie 登录成功', 'success');
            await checkLoginStatus();
            switchView('courses');
        } else {
            showToast('Cookie 登录失败: ' + (result.msg || '未知错误'), 'error');
        }
    } finally {
        setButtonLoading(btn, false);
    }
}

async function handleLogout() {
    await apiFetch('/api/logout', { method: 'POST' });
    showToast('已退出', 'info');
    checkLoginStatus();
}

/* ---------- 课程 ---------- */
function emptyStateHtml(icon, text) {
    return `<div class="empty-state"><svg class="icon empty-icon"><use href="#${icon}"/></svg><p>${escapeHtml(text)}</p></div>`;
}

async function loadCourses() {
    const container = $('courseListContainer');
    container.innerHTML = '<div class="alert alert-info mb-0">正在加载课程列表...</div>';
    const result = await apiFetch('/api/courses');
    if (result.status === 'error') {
        container.innerHTML = (result.msg || '').includes('登录')
            ? emptyStateHtml('icon-key', '请先在「登录」页面登录后再加载课程列表。')
            : `<div class="alert alert-danger mb-0">${escapeHtml(result.msg)}</div>`;
        return;
    }
    state.courses = result.courses || [];
    state.selectedCourseIds = new Set((result.selected_ids || []).map(String));
    renderCourseList();
}

function renderCourseList() {
    const container = $('courseListContainer');
    const query = ($('courseSearch')?.value || '').trim().toLowerCase();
    const courses = state.courses.filter((course) => {
        if (!query) return true;
        return `${course.title || ''} ${course.courseId || ''}`.toLowerCase().includes(query);
    });
    if (courses.length === 0) {
        container.innerHTML = emptyStateHtml('icon-search', query ? '没有匹配的课程' : '暂无课程数据');
        updateSelectedCourseCount();
        return;
    }
    const html = courses.map((course) => {
        const id = String(course.courseId);
        const checked = state.selectedCourseIds.has(id) ? 'checked' : '';
        const rawPct = Number(course.progress ?? course.progressPct ?? course.schedule);
        const hasPct = Number.isFinite(rawPct);
        const pct = hasPct ? Math.max(0, Math.min(100, rawPct)) : 0;
        let status = 'todo';
        if (hasPct && pct >= 100) status = 'done';
        else if ((hasPct && pct > 0) || state.selectedCourseIds.has(id)) status = 'doing';
        const statusText = { done: '已完成', doing: '进行中', todo: '未开始' }[status];
        const pctLabel = hasPct ? `${pct}%` : '--';
        return `
            <div class="col-lg-6 course-col">
                <div class="course-card" data-status="${status}">
                    <div class="course-body">
                        <label class="course-checkbox">
                            <input type="checkbox" class="form-check-input course-cb" value="${escapeHtml(id)}" ${checked}>
                            <span class="course-title">${escapeHtml(course.title)}</span>
                        </label>
                        <span class="course-status ${status}">${statusText}</span>
                        <span class="course-pct">${pctLabel}</span>
                        <span class="course-id-badge">${escapeHtml(id)}</span>
                    </div>
                    <div class="course-bar" style="margin:0 15px 13px"><i class="${status === 'done' ? 'done' : ''}" style="width:${pct}%"></i></div>
                </div>
            </div>`;
    });
    container.innerHTML = `<div class="row g-2">${html.join('')}</div>`;
    updateSelectedCourseCount();
}

function updateSelectedCourseCount() {
    const count = document.querySelectorAll('.course-cb:checked').length;
    const counter = $('selectedCourseCount');
    if (counter) counter.textContent = `已选 ${count} 个`;
    const all = document.querySelectorAll('.course-cb');
    const selectAll = $('selectAllCourses');
    if (selectAll) {
        selectAll.checked = all.length > 0 && count === all.length;
        selectAll.indeterminate = count > 0 && count < all.length;
    }
}

function handleSelectAllCourses() {
    document.querySelectorAll('.course-cb').forEach((cb) => {
        cb.checked = this.checked;
        if (this.checked) state.selectedCourseIds.add(String(cb.value));
        else state.selectedCourseIds.delete(String(cb.value));
    });
    updateSelectedCourseCount();
}

function handleCourseToggle(e) {
    if (!e.target.classList.contains('course-cb')) return;
    if (e.target.checked) state.selectedCourseIds.add(String(e.target.value));
    else state.selectedCourseIds.delete(String(e.target.value));
    updateSelectedCourseCount();
}

async function handleRefreshCourses() {
    const btn = $('btnRefreshCourses');
    setButtonLoading(btn, true, '刷新中...');
    try {
        await loadCourses();
    } finally {
        setButtonLoading(btn, false);
    }
}

async function handleSaveCourses() {
    const ids = Array.from(state.selectedCourseIds);
    if (ids.length === 0) {
        showToast('请先选择至少一个课程', 'warning');
        return;
    }
    const btn = $('btnSaveCourses');
    const msg = $('courseSaveMsg');
    setButtonLoading(btn, true, '保存中...');
    try {
        const result = await apiFetch('/api/courses/select', { method: 'POST', body: { course_ids: ids } });
        const ok = result.status === 'ok';
        msg.textContent = ok ? `已保存 ${ids.length} 个课程` : (result.msg || '保存失败');
        msg.className = `save-msg ${ok ? 'text-success' : 'text-danger'}`;
        if (ok) showToast(`已选择 ${ids.length} 个课程`, 'success');
    } finally {
        setButtonLoading(btn, false);
    }
    setTimeout(() => { msg.textContent = ''; }, SAVE_MSG_RESET_MS);
}

async function handleLoadReadingLinks() {
    const container = $('readingLinksContainer');
    const btn = $('btnLoadReadingLinks');
    setButtonLoading(btn, true, '扫描中...');
    container.innerHTML = '<div class="alert alert-info mb-0">正在扫描已选课程的阅读任务，这一步需要逐章检查，请稍等。</div>';
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
        const result = await apiFetch('/api/reading/links');
        if (result.status !== 'ok') {
            container.innerHTML = `<div class="alert alert-danger mb-0">${escapeHtml(result.msg || '生成阅读链接失败')}</div>`;
            return;
        }
        const items = result.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="alert alert-warning mb-0">
                    已扫描 ${escapeHtml(result.scanned || 0)} 个章节，没有找到学习通返回的阅读任务卡片。
                    请确认已勾选包含阅读任务的课程并点击「保存选择」。
                </div>`;
            return;
        }
        const rows = items.map((item) => {
            const finished = item.finished
                ? '<span class="badge bg-success ms-2">章节已完成</span>'
                : '<span class="badge bg-warning ms-2">章节未完成</span>';
            return `
                <div class="list-group-item reading-link-item">
                    <div class="d-flex justify-content-between gap-3 align-items-start">
                        <div>
                            <div class="fw-semibold">${escapeHtml(item.chapter_title)}${finished}</div>
                            <div class="small text-muted">${escapeHtml(item.course_title)} · ${escapeHtml(item.chapter_id)}</div>
                        </div>
                        <a class="btn btn-outline-info btn-sm" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">打开</a>
                    </div>
                </div>`;
        });
        container.innerHTML = `
            <div class="card reading-links-card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>阅读链接</span>
                    <span class="badge count-badge">找到 ${items.length} 个，扫描 ${escapeHtml(result.scanned || 0)} 个章节</span>
                </div>
                <div class="list-group list-group-flush">${rows.join('')}</div>
            </div>`;
    } finally {
        setButtonLoading(btn, false);
        container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/* ---------- 学习控制 ---------- */
async function startStudy() {
    const btn = $('btnStartStudy');
    const speed = parseFloat($('studySpeed').value);
    const jobs = parseInt($('studyJobs').value, 10) || 4;
    setButtonLoading(btn, true, '启动中...');
    try {
        const result = await apiFetch('/api/study/start', { method: 'POST', body: { speed, jobs } });
        if (result.status === 'ok') {
            showToast('学习已启动', 'success');
            state.stopRequestedAt = 0;
        } else if ((result.msg || '').includes('登录')) {
            // 未登录：跳转到登录页并提示
            showToast('请先登录账号再启动学习', 'warning');
            switchView('auth');
            checkLoginStatus();
        } else {
            showToast('启动失败: ' + (result.msg || '未知错误'), 'error');
        }
    } finally {
        setButtonLoading(btn, false);
    }
    // 以服务端状态为准回填按钮：启动成功则开始轮询，失败/未登录则恢复可点击
    await refreshStudyStatusSafely();
}

async function stopStudy() {
    const btn = $('btnStopStudy');
    setButtonLoading(btn, true, '停止中...');
    try {
        const result = await apiFetch('/api/study/stop', { method: 'POST' });
        const ok = result.status === 'ok';
        showToast(ok ? '正在停止...' : (result.msg || '停止失败'), ok ? 'warning' : 'error');
        if (ok) {
            // 先按"正在停止"渲染，避免 setButtonLoading(false) 把按钮重新点亮
            state.stopRequestedAt = Date.now();
            if ($('studyStatusText')) $('studyStatusText').textContent = STUDY_STATUS_MAP.stopping.text;
            ensureStudyPolling();
        }
    } finally {
        setButtonLoading(btn, false);
    }
    // 停止期间按钮必须保持禁用，否则用户会重复点出「没有正在运行的任务」
    await refreshStudyStatusSafely();
}

// 状态接口失败时不要把异常抛到调用方，否则后续按钮/UI 更新会被整体跳过
async function refreshStudyStatusSafely() {
    try {
        await refreshStudyStatus();
    } catch (err) {
        console.warn('刷新学习状态失败', err);
    }
}

async function refreshStudyStatus() {
    const result = await apiFetch('/api/study/status');
    if (result.status === 'ok') {
        updateStudyUI(result.data);
        await maybeForceStop(result.data);
    }
}

// 停止请求发出太久仍是 running/stopping：说明当前任务点没能在检查点退出，
// 自动兜底收敛状态，避免界面永远停在"正在停止..."且开始按钮不可用。
async function maybeForceStop(s) {
    if (!state.stopRequestedAt || state.forcingStop) return;
    if (s.status !== 'stopping' && s.status !== 'running') return;
    if (Date.now() - state.stopRequestedAt < STOP_FORCE_MS) return;

    state.forcingStop = true;
    try {
        const result = await apiFetch('/api/study/stop', { method: 'POST', body: { force: true } });
        if (result.status === 'ok') {
            showToast('已强制结束本次刷课', 'warning');
            state.stopRequestedAt = 0;
            await refreshStudyStatus();
        }
    } finally {
        state.forcingStop = false;
    }
}

function ensureStudyPolling() {
    if (!state.studyPollTimer) {
        state.studyPollTimer = setInterval(refreshStudyStatus, STUDY_POLL_MS);
    }
}

function stopStudyPolling() {
    if (state.studyPollTimer) {
        clearInterval(state.studyPollTimer);
        state.studyPollTimer = null;
    }
}

function startElapsedTimer() {
    stopElapsedTimer();
    renderElapsed();
    state.elapsedTimer = setInterval(renderElapsed, 1000);
}

function stopElapsedTimer() {
    if (state.elapsedTimer) {
        clearInterval(state.elapsedTimer);
        state.elapsedTimer = null;
    }
}

function renderElapsed() {
    if (!state.lastStartTime) return;
    const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - state.lastStartTime));
    const h = String(Math.floor(elapsed / 3600)).padStart(2, '0');
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    $('studyElapsed').textContent = `${h}:${m}:${s}`;
}

function updateStudyUI(s) {
    const isRunning = Boolean(s.is_running);
    const isStopping = s.status === 'stopping';
    // 停止中：开始按钮保持禁用，停止按钮保持禁用，避免重复启动/重复停止
    $('btnStartStudy').disabled = isRunning || isStopping;
    $('btnStopStudy').disabled = !isRunning || isStopping;

    const st = STUDY_STATUS_MAP[s.status] || { text: s.status || '未知', cls: 'idle' };
    $('studyStatusText').textContent = st.text;
    $('studyStatusDot').className = `status-dot ${st.cls}`;
    $('studyCurrentCourse').textContent = s.current_course || '-';
    $('studyCurrentChapter').textContent = s.current_chapter || '-';
    $('studyCompleted').textContent = s.completed_courses ?? 0;
    $('studyTotal').textContent = s.total_courses ?? 0;
    $('studyChaptersDone').textContent = s.completed_chapters ?? 0;
    $('studyChaptersTotal').textContent = s.total_chapters ?? 0;

    const pct = Math.min(100, Math.max(0, Number(s.progress_pct) || 0));
    const bar = $('studyProgressBar');
    bar.style.width = `${pct}%`;
    bar.setAttribute('aria-valuenow', String(pct));
    $('studyProgressText').textContent = `${pct}%`;

    // 已用时间：运行中每秒刷新，结束后定格在最后值
    if (s.start_time > 0) {
        state.lastStartTime = s.start_time;
        if (isRunning) startElapsedTimer();
        else {
            stopElapsedTimer();
            renderElapsed();
        }
    }

    // 侧栏摘要
    const indicator = $('statusIndicator');
    if (indicator) {
        indicator.textContent = st.text;
        indicator.className = 'text-' + (
            s.status === 'running' ? 'success'
            : s.status === 'error' ? 'danger'
            : s.status === 'completed' ? 'info'
            : 'secondary'
        );
    }
    const chDone = s.completed_chapters || 0;
    const chTotal = s.total_chapters || 0;
    const chInfo = chTotal > 0 ? ` | ${chDone}/${chTotal}节` : '';
    $('sidebarProgress').textContent =
        `${s.completed_courses}/${s.total_courses} (${pct}%)${chInfo}`;

    // 运行中保持轮询（页面刷新后重新打开也能恢复），停止后清理
    if (isRunning) {
        ensureStudyPolling();
    } else {
        stopStudyPolling();
        state.stopRequestedAt = 0;
    }
}

/* ---------- 日志 ---------- */
function connectLogStream() {
    if (logEventSource) logEventSource.close();
    logEventSource = new EventSource('/api/logs/stream');
    logEventSource.addEventListener('log', (event) => appendLogEntry(JSON.parse(event.data)));
    logEventSource.addEventListener('progress', (event) => {
        try {
            const s = JSON.parse(event.data);
            if (s && s.is_running !== undefined) updateStudyUI(s);
        } catch (e) { /* 忽略格式异常的进度事件 */ }
    });
    logEventSource.addEventListener('status', () => {
        if (state.view === 'study') refreshStudyStatus();
    });
    logEventSource.onerror = () => console.warn('SSE connection error, will reconnect...');
}

function appendLogEntry(data) {
    const container = $('logContainer');
    if (!container) return;
    const autoScroll = $('autoScrollLogs')?.checked;
    const isAtBottom = autoScroll
        || (container.scrollTop + container.clientHeight >= container.scrollHeight - 50);
    const level = data.level || 'INFO';

    const div = document.createElement('div');
    div.className = 'log-entry';
    div.innerHTML = `<span class="log-time">${escapeHtml(data.timestamp || '')}</span>`
        + `<span class="log-level log-level-badge-${level}">${level}</span>`
        + `<span class="log-level-${level}">${escapeHtml(data.message || '')}</span>`;
    container.appendChild(div);

    // 限制 DOM 条数，避免长时间运行导致页面卡顿
    while (container.childElementCount > MAX_LOG_ENTRIES) {
        container.removeChild(container.firstElementChild);
    }
    state.logCount = container.childElementCount;
    const badge = $('logCountBadge');
    if (badge) badge.textContent = `${state.logCount} 条`;

    if (isAtBottom) container.scrollTop = container.scrollHeight;
}

function clearLogs() {
    const container = $('logContainer');
    if (container) container.innerHTML = '';
    state.logCount = 0;
    const badge = $('logCountBadge');
    if (badge) badge.textContent = '0 条';
    showToast('日志显示已清空', 'info');
}

async function copyLogs() {
    const text = $('logContainer')?.innerText || '';
    if (!text.trim()) {
        showToast('暂无日志可复制', 'warning');
        return;
    }
    try {
        await navigator.clipboard.writeText(text);
        showToast('日志已复制', 'success');
    } catch (err) {
        showToast('复制失败: ' + err.message, 'error');
    }
}

function handleAutoScrollChange() {
    if (this.checked) {
        const container = $('logContainer');
        if (container) container.scrollTop = container.scrollHeight;
    }
}

/* ---------- 题库缓存 ---------- */
async function loadCache(search = '') {
    const url = search ? `/api/tiku/cache?search=${encodeURIComponent(search)}` : '/api/tiku/cache';
    const result = await apiFetch(url);
    const tbody = $('cacheTableBody');
    if (result.status === 'ok') {
        state.cacheItems = result.items || [];
        renderCacheTable();
    } else if (tbody) {
        tbody.innerHTML = `<tr><td colspan="2" class="text-danger">${escapeHtml(result.msg || '加载失败')}</td></tr>`;
    }
}

function renderCacheTable() {
    const tbody = $('cacheTableBody');
    if (!tbody) return;
    $('cacheCount').textContent = state.cacheItems.length;
    if (state.cacheItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" class="text-muted text-center py-4">暂无缓存数据</td></tr>';
        return;
    }
    tbody.innerHTML = state.cacheItems.map((item) => `
        <tr>
            <td class="text-truncate" style="max-width:400px" title="${escapeHtml(item.question)}">${escapeHtml(item.question)}</td>
            <td class="text-truncate" style="max-width:400px" title="${escapeHtml(item.answer)}">${escapeHtml(item.answer)}</td>
        </tr>`).join('');
}

const debouncedCacheSearch = debounce((value) => loadCache(value), 300);

async function clearCache() {
    if (!confirm('确定清空所有答案缓存？')) return;
    const btn = $('btnClearCache');
    setButtonLoading(btn, true, '清空中...');
    try {
        const result = await apiFetch('/api/tiku/cache', { method: 'DELETE' });
        if (result.status === 'ok') {
            showToast('缓存已清空', 'success');
            loadCache();
        } else {
            showToast('清空失败: ' + (result.msg || '未知错误'), 'error');
        }
    } finally {
        setButtonLoading(btn, false);
    }
}

/* ---------- 初始化 ---------- */
function init() {
    // 导航（顶栏 + 侧栏共用 data-view）
    document.querySelectorAll('[data-view]').forEach((el) => {
        el.addEventListener('click', (e) => {
            e.preventDefault();
            switchView(el.dataset.view);
        });
    });

    // 配置标签页是静态元素，只绑定一次（避免重复渲染后监听器叠加）
    document.querySelectorAll('[data-config-tab]').forEach((tab) => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            activateConfigTab(tab.dataset.configTab);
        });
    });

    $('btnSaveConfig')?.addEventListener('click', saveConfig);
    $('platformSelect')?.addEventListener('change', handlePlatformChange);
    $('loginForm')?.addEventListener('submit', handleLoginSubmit);
    $('btnCookieLogin')?.addEventListener('click', handleCookieLogin);
    $('btnLogout')?.addEventListener('click', handleLogout);

    $('selectAllCourses')?.addEventListener('change', handleSelectAllCourses);
    $('courseSearch')?.addEventListener('input', renderCourseList);
    $('courseListContainer')?.addEventListener('change', handleCourseToggle);
    $('btnRefreshCourses')?.addEventListener('click', handleRefreshCourses);
    $('btnSaveCourses')?.addEventListener('click', handleSaveCourses);
    $('btnLoadReadingLinks')?.addEventListener('click', handleLoadReadingLinks);

    $('studySpeed')?.addEventListener('input', function () {
        $('speedValue').textContent = parseFloat(this.value).toFixed(2);
    });
    $('btnStartStudy')?.addEventListener('click', startStudy);
    $('btnStopStudy')?.addEventListener('click', stopStudy);

    $('btnClearLogs')?.addEventListener('click', clearLogs);
    $('btnCopyLogs')?.addEventListener('click', copyLogs);
    $('autoScrollLogs')?.addEventListener('change', handleAutoScrollChange);

    $('tikuSearch')?.addEventListener('input', function () {
        debouncedCacheSearch(this.value.trim());
    });
    $('btnClearCache')?.addEventListener('click', clearCache);

    // 页面卸载时释放连接与定时器
    window.addEventListener('beforeunload', () => {
        if (logEventSource) logEventSource.close();
        stopStudyPolling();
        stopElapsedTimer();
    });

    connectLogStream();
    // 先拉取平台列表再渲染登录态：applyPlatformMeta 依赖 platformCache
    loadPlatforms().then(checkLoginStatus);
    switchView(localStorage.getItem('superstar.activeView') || 'dashboard');
}

document.addEventListener('DOMContentLoaded', init);
