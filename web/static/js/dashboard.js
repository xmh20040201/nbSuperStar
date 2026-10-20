/* ==========================================================================
   dashboard.js —— 仪表盘 / 学习控制联动增强层（与 app.js 协作，不修改其逻辑）
   职责：
   1. 将 app.js 的 study 状态（updateStudyUI）镜像到仪表盘的进度环、波形元数据、
      统计卡增量；并同步到「学习控制」页的 st* 控件。
   2. 学习控制页的参数控件（stSpeed/stJobs/开始/停止）与仪表盘控件双向联动。
   3. 侧栏折叠、面包屑、导航高亮、课程筛选 chips、顶部登录胶囊与头像。
   ========================================================================== */
(function () {
    const $ = (id) => document.getElementById(id);
    const RING_C = 2 * Math.PI * 38; // r=38

    const VIEW_META = {
        dashboard: '仪表盘', study: '学习控制', courses: '课程列表',
        logs: '实时日志', config: '系统配置', auth: '账号登录', tiku: '题库缓存',
    };

    function fmtTime(sec) {
        sec = Number(sec) || 0;
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = Math.floor(sec % 60);
        return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function setActive(view) {
        document.querySelectorAll('.nav-link-ui').forEach((a) => {
            a.classList.toggle('active', a.dataset.view === view);
        });
        const c = $('crumbCurrent');
        if (c) c.textContent = VIEW_META[view] || view;
    }

    /* ---- 真实数据迷你折线图：spark 缓存历史采样点 ---- */
    const sparkHistory = { status: [], courses: [], chapters: [], elapsed: [] };
    const SPARK_MAX = 30;

    function drawSpark(key, value) {
        const arr = sparkHistory[key];
        if (!arr) return;
        arr.push(Number(value) || 0);
        if (arr.length > SPARK_MAX) arr.shift();
        const svg = document.querySelector(`[data-spark="${key}"]`);
        if (!svg) return;
        const line = svg.querySelector('polyline');
        if (!line) return;
        // 全为 0（未运行过）时不绘制装饰线
        if (arr.every((v) => v === 0)) { line.setAttribute('points', ''); return; }
        const max = Math.max(...arr, 1);
        const pts = arr.map((v, i) => {
            const x = arr.length > 1 ? (i / (arr.length - 1)) * 86 : 86;
            const y = 28 - (v / max) * 26;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        line.setAttribute('points', pts);
    }

    /* ---- 把 study 状态对象 s 映射到仪表盘富组件 ---- */
    function updateExtras(s) {
        s = s || {};
        const pct = Math.min(100, Math.max(0, Number(s.progress_pct) || 0));
        const running = Boolean(s.is_running);
        const done = Number(s.completed_courses || 0);
        const total = Number(s.total_courses || 0);
        const chDone = Number(s.completed_chapters || 0);
        const chTotal = Number(s.total_chapters || 0);

        // 真实数据折线：整体进度、课程/章节完成率、运行时长（分钟）
        drawSpark('status', pct);
        drawSpark('courses', total ? (done / total) * 100 : 0);
        drawSpark('chapters', chTotal ? (chDone / chTotal) * 100 : 0);
        drawSpark('elapsed', (Number(s.start_time) > 0 && running)
            ? (Date.now() / 1000 - Number(s.start_time)) / 60 : 0);

        // 本周学习时长柱状图：仅记录本次会话真实数据（按天累计运行分钟数）
        const week = $('weekBars');
        if (week) {
            try {
                const key = 'superstar.weekMinutes';
                const today = new Date().getDay();
                const idx = (today + 6) % 7; // 一=0 ... 日=6
                const wk = JSON.parse(localStorage.getItem(key) || '[0,0,0,0,0,0,0]');
                const mins = Math.max(0, Math.round((Date.now() / 1000 - Number(s.start_time || 0)) / 60));
                if (running && mins > (wk[idx] || 0)) wk[idx] = mins;
                localStorage.setItem(key, JSON.stringify(wk));
                const maxM = Math.max(...wk, 1);
                week.querySelectorAll('.b i').forEach((bar, i) => {
                    bar.style.height = wk[i] > 0 ? `${Math.max(6, Math.round(wk[i] / maxM * 100))}%` : '2%';
                    bar.parentElement.classList.toggle('today', i === idx && wk[i] > 0);
                    bar.parentElement.title = wk[i] > 0 ? `${wk[i]} 分钟` : '无记录';
                });
            } catch (e) { /* noop */ }
        }

        const ring = $('dashRing');
        if (ring) ring.setAttribute('stroke-dasharray', `${(pct / 100 * RING_C).toFixed(1)} ${RING_C.toFixed(1)}`);
        const rv = $('dashRingVal');
        if (rv) rv.textContent = `${Math.round(pct)}%`;

        const dp = $('dashDonePct');
        if (dp) dp.textContent = `${total ? Math.round(done / total * 100) : 0}%`;
        const cp = $('dashChPct');
        if (cp) cp.textContent = `${chTotal ? Math.round(chDone / chTotal * 100) : 0}%`;

        const rs = $('dashRunningSub');
        if (rs) rs.textContent = running ? '任务处理中' : (s.status === 'completed' ? '已完成' : '暂无运行中的任务');

        const meta = $('dashCurMeta');
        if (meta) meta.textContent = `当前课程 · ${running ? '进行中' : '待开始'}`;

        const tt = $('dashTaskType');
        if (tt) tt.textContent = s.current_type === 'video' ? '视频任务' : (s.current_type === 'audio' ? '音频任务' : '学习任务');

        const wm = $('dashWaveMeta');
        const played = s.current_played || s.played || 0;
        const dur = s.current_duration || s.duration || 0;
        if (wm) wm.textContent = dur ? `${fmtTime(played)} / ${fmtTime(dur)}` : '--:-- / --:--';

        const wave = $('dashWave');
        if (wave) wave.querySelectorAll('i').forEach((el) => el.classList.toggle('p', running));

        const sv = $('studyStatusVal');
        if (sv) sv.style.color = running ? '#6ee7b7' : (s.status === 'error' ? '#fca5a5' : '#8b9bb4');

        const fd = $('footDot');
        if (fd) fd.style.background = running ? 'var(--green)' : (s.status === 'error' ? 'var(--red)' : 'var(--muted)');

        const dot = $('navRunningDot');
        if (dot) dot.hidden = !running;
    }

    /* ---- 把仪表盘（canonical）状态镜像到学习控制页 st* ---- */
    function syncSt() {
        const pairs = [
            ['studyElapsed', 'stElapsed'], ['studyCurrentCourse', 'stCourse'],
            ['studyCurrentChapter', 'stChapter'], ['studyCompleted', 'stCompleted'],
            ['studyTotal', 'stTotal'], ['studyChaptersDone', 'stChDone'],
            ['studyChaptersTotal', 'stChTotal'], ['studyProgressText', 'stPct'],
        ];
        pairs.forEach(([a, b]) => {
            const A = $(a), B = $(b);
            if (A && B) B.textContent = A.textContent;
        });
        const sb = $('studyProgressBar'), tb = $('stBar');
        if (sb && tb) tb.style.width = sb.style.width;
        const sd = $('studyStatusDot'), td = $('stStatusDot');
        if (sd && td) td.className = sd.className;
        const st = $('studyStatusText'), tt = $('stStatusText');
        if (st && tt) tt.textContent = st.textContent;
        const se = $('studyElapsed'), f = $('sidebarElapsed');
        if (se && f) f.textContent = se.textContent;
    }

    function applyExtras(s) {
        try { updateExtras(s); syncSt(); } catch (e) { /* noop */ }
    }

    /* ---- 登录胶囊同步 ---- */
    function syncLoginPill() {
        const b = $('loginStatusBadge');
        if (!b) return;
        const txt = (b.textContent || '').trim() || '未登录';
        const off = /未登录|登录失败/.test(txt);
        b.classList.toggle('is-off', off);
        b.classList.remove('badge', 'bg-success', 'bg-danger');
        b.innerHTML = '<i></i>' + txt;
        const name = (txt.match(/已登录\s*(.*)/) || [])[1] || '';
        const av = $('topAvatarInitial');
        if (av) av.textContent = name ? name.slice(0, 1) : '未';
        const wn = $('topWhoName');
        if (wn) wn.textContent = name || '未登录';
    }

    /* ---- 课程筛选 chips ---- */
    function initChips() {
        const chips = $('courseFilterChips');
        const list = $('courseListContainer');
        if (!chips || !list) return;
        const labels = { all: '全部', doing: '进行中', done: '已完成', todo: '未开始' };

        function applyFilter(f) {
            list.querySelectorAll('.course-col').forEach((col) => {
                const card = col.querySelector('.course-card');
                const st = card ? card.dataset.status : '';
                col.style.display = (f === 'all' || f === st) ? '' : 'none';
            });
        }
        function refresh() {
            const cards = list.querySelectorAll('.course-card');
            const c = { all: cards.length, doing: 0, done: 0, todo: 0 };
            cards.forEach((cd) => { const s = cd.dataset.status; if (c[s] !== undefined) c[s]++; });
            chips.querySelectorAll('.fchip').forEach((ch) => {
                const f = ch.dataset.filter;
                ch.textContent = `${labels[f]} ${c[f] !== undefined ? c[f] : 0}`;
            });
            const active = chips.querySelector('.fchip.active');
            applyFilter(active ? active.dataset.filter : 'all');
        }
        chips.querySelectorAll('.fchip').forEach((ch) => ch.addEventListener('click', () => {
            chips.querySelectorAll('.fchip').forEach((x) => x.classList.remove('active'));
            ch.classList.add('active');
            applyFilter(ch.dataset.filter);
        }));
        new MutationObserver(refresh).observe(list, { childList: true, subtree: true });
        refresh();
    }

    /* ---- 学习控制页控件联动 ---- */
    function initStudyControls() {
        const stSpeed = $('stSpeed'), dashSpeed = $('studySpeed');
        if (stSpeed && dashSpeed) {
            stSpeed.value = dashSpeed.value || stSpeed.value;
            const sv = $('stSpeedValue');
            if (sv) sv.textContent = parseFloat(stSpeed.value).toFixed(2);
            stSpeed.addEventListener('input', function () {
                dashSpeed.value = this.value;
                dashSpeed.dispatchEvent(new Event('input', { bubbles: true }));
                if (sv) sv.textContent = parseFloat(this.value).toFixed(2);
            });
            dashSpeed.addEventListener('input', function () {
                stSpeed.value = this.value;
                if (sv) sv.textContent = parseFloat(this.value).toFixed(2);
            });
        }
        const stJobs = $('stJobs'), dashJobs = $('studyJobs');
        if (stJobs && dashJobs) {
            stJobs.value = dashJobs.value || stJobs.value;
            stJobs.addEventListener('input', function () { dashJobs.value = this.value; });
            dashJobs.addEventListener('input', function () { stJobs.value = this.value; });
        }
        // +/- 步进器
        document.querySelectorAll('.num .stp b').forEach((b) => b.addEventListener('click', function () {
            const inp = this.closest('.num').querySelector('input');
            if (!inp) return;
            const step = parseInt(this.dataset.step, 10) || 0;
            const min = parseInt(inp.min, 10) || 1;
            const max = parseInt(inp.max, 10) || 10;
            let v = (parseInt(inp.value, 10) || 0) + step;
            v = Math.max(min, Math.min(max, v));
            inp.value = v;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
        }));
        // 学习控制页开始/停止 → 先同步参数再调用 app.js 逻辑
        const stStart = $('stStart'), stStop = $('stStop');
        if (stStart) stStart.addEventListener('click', function () {
            if (stSpeed && dashSpeed) dashSpeed.value = stSpeed.value;
            if (stJobs && dashJobs) dashJobs.value = stJobs.value;
            if (typeof window.startStudy === 'function') window.startStudy();
        });
        if (stStop) stStop.addEventListener('click', function () {
            if (typeof window.stopStudy === 'function') window.stopStudy();
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        // 1) 劫持 updateStudyUI，把状态分发到富组件
        const orig = window.updateStudyUI;
        if (typeof orig === 'function') {
            window.updateStudyUI = function (s) {
                const r = orig.apply(this, arguments);
                applyExtras(s);
                return r;
            };
        }
        // 2) 登录胶囊
        const origLogin = window.checkLoginStatus;
        if (typeof origLogin === 'function') {
            window.checkLoginStatus = async function () {
                const r = await origLogin.apply(this, arguments);
                syncLoginPill();
                return r;
            };
        }
        // 3) 导航 / 面包屑 / 侧栏
        document.querySelectorAll('[data-view]').forEach((a) => a.addEventListener('click', () => setActive(a.dataset.view)));
        const tg = $('sidebarToggle');
        if (tg) tg.addEventListener('click', () => { const sb = $('appSidebar'); if (sb) sb.classList.toggle('open'); });

        initStudyControls();
        initChips();

        const active = document.querySelector('.nav-link-ui.active');
        setActive(active ? active.dataset.view : 'dashboard');

        // 4) 首屏拉一次学习状态，填充仪表盘
        if (typeof window.refreshStudyStatus === 'function') {
            try { window.refreshStudyStatus(); } catch (e) { /* noop */ }
        }
    });
})();
