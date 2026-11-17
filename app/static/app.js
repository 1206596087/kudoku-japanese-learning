"use strict";

const API = "/api";
const learnerId = 1;
const defaultSettings = {
  voiceURI: "",
  rate: 1,
  furigana: "tap",
  level: "N5",
  dailyTarget: 10,
  desiredRetention: 0.9,
  animations: true,
};

const state = {
  route: "home",
  settings: loadSettings(),
  dashboard: null,
  currentSentence: null,
  learnRevealed: false,
  revealFurigana: false,
  listeningScore: 0,
  mastery: 0,
  childTab: "variant",
  reviewSentence: null,
  reviewMode: "listening",
  reviewRevealed: false,
  grammarLevel: "",
  grammarQuery: "",
  libraryPage: 1,
  libraryTotal: 0,
  wordPage: 1,
  readingPage: 1,
  recordLevel: "",
  account: null,
  voices: [],
  currentSpeech: null,
  resetArmed: false,
};

const els = {
  loading: document.querySelector("#loading-layer"),
  toast: document.querySelector("#toast-region"),
  dialog: document.querySelector("#detail-dialog"),
  dialogContent: document.querySelector("#dialog-content"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogBadge: document.querySelector("#dialog-badge"),
};

function loadSettings() {
  try {
    return { ...defaultSettings, ...JSON.parse(localStorage.getItem("kudoku-settings") || "{}") };
  } catch {
    return { ...defaultSettings };
  }
}

function saveSettings() {
  localStorage.setItem("kudoku-settings", JSON.stringify(state.settings));
  document.body.classList.toggle("no-motion", !state.settings.animations);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function rubyHTML(segments = []) {
  return segments.map((part) => {
    if (part.reading) return `<ruby>${esc(part.surface)}<rt>${esc(part.reading)}</rt></ruby>`;
    return esc(part.surface);
  }).join("");
}

function furiganaClass() {
  if (state.settings.furigana === "never") return "furigana-never";
  if (state.settings.furigana === "tap" && !state.revealFurigana) return "furigana-hidden";
  return "";
}

function kindLabel(kind) {
  return { core: "核心句", variant: "固定变体", real: "场景扩展", personal: "个性模板" }[kind] || kind;
}

function tokenStrip(tokens = []) {
  return `<div class="token-strip">${tokens.map((token) => {
    const gloss = token.particle_note_zh || token.gloss_zh || token.gloss_en || "查看词条";
    if (!token.lexeme_id) return `<span class="token-chip ${token.particle_note_zh ? "is-particle" : "is-unlinked"}" title="${esc(gloss)}"><b lang="ja">${esc(token.surface)}</b><small>${esc(token.reading || "")} ${token.particle_note_zh ? `· ${esc(token.particle_note_zh)}` : ""}</small></span>`;
    return `<button class="token-chip" data-word-id="${token.lexeme_id}" title="${esc(gloss)}"><b lang="ja">${esc(token.surface)}</b><small>${esc(token.reading || "")} · ${esc(gloss)}</small></button>`;
  }).join("")}</div>`;
}

function setLoading(show) {
  els.loading.hidden = !show;
}

function toast(message, type = "info") {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`;
  node.textContent = message;
  els.toast.append(node);
  window.setTimeout(() => node.remove(), 3300);
}

async function api(path, options = {}, showLoading = false) {
  if (showLoading) setLoading(true);
  try {
    const response = await fetch(`${API}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let body = null;
    try { body = await response.json(); } catch { body = {}; }
    if (response.status === 401) showAuthGate({ configured: true, authenticated: false });
    if (!response.ok) throw new Error(body.detail || "请求失败，请稍后再试");
    return body;
  } catch (error) {
    toast(error.message || "连接本地服务失败", "error");
    throw error;
  } finally {
    if (showLoading) setLoading(false);
  }
}

function navigate(route) {
  if (location.hash !== `#${route}`) location.hash = route;
  else activateRoute(route);
}

async function activateRoute(route) {
  const valid = ["home", "learn", "review", "grammar", "library", "words", "readings", "records", "settings"];
  state.route = valid.includes(route) ? route : "home";
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === state.route));
  document.querySelectorAll("[data-route]").forEach((button) => button.classList.toggle("is-active", button.dataset.route === state.route));
  stopSpeech();
  document.querySelector("#main-content").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: state.settings.animations ? "smooth" : "auto" });
  try {
    if (state.route === "home") await loadDashboard();
    if (state.route === "learn") await loadLearnSentence(false);
    if (state.route === "review") await loadReview();
    if (state.route === "grammar") await loadGrammars();
    if (state.route === "library") await loadLibrary();
    if (state.route === "words") await loadWords();
    if (state.route === "readings") await loadReadings();
    if (state.route === "records") await loadRecords();
    if (state.route === "settings") renderSettings();
  } catch { /* API helper already surfaced the error. */ }
}

async function loadDashboard() {
  state.dashboard = await api(`/dashboard?learner_id=${learnerId}`, {}, true);
  const d = state.dashboard;
  state.settings.level = d.profile.current_level_code;
  saveSettings();
  document.querySelector("#current-level").textContent = `${d.profile.current_level_code} · 当前阶段`;
  document.querySelector("#level-orbit").textContent = d.profile.current_level_code;
  document.querySelector("#today-count").textContent = d.today_count;
  document.querySelector("#mastered-count").textContent = d.mastered_count;
  document.querySelector("#due-count").textContent = d.due_count;
  document.querySelector("#review-due-label").textContent = `到期 ${d.due_count}`;
  const date = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
  document.querySelector("#today-label").textContent = `${date} · 今天也从一句话开始`;
  const skillData = [
    ["听力", d.averages.listening], ["理解", d.averages.comprehension], ["表达", d.averages.production],
  ];
  document.querySelector("#skill-bars").innerHTML = skillData.map(([label, value]) => `
    <div class="skill-row"><span>${label}</span><div class="bar"><i style="--value:${Math.min(value / 5 * 100, 100)}%"></i></div><b>${Number(value).toFixed(1)}</b></div>
  `).join("");
  document.querySelector("#level-grid").innerHTML = d.levels.map((level) => `
    <button class="level-card ${level.code === d.profile.current_level_code ? "is-current" : ""} ${level.sentence_count === 0 ? "is-empty" : ""}" data-level-card="${esc(level.code)}" ${level.sentence_count === 0 ? "aria-describedby=empty-n1" : ""}>
      <strong>${esc(level.code)}</strong><span>${esc(level.goal_zh)}</span><small>${level.sentence_count ? `${level.sentence_count} 句` : "内容准备中"}</small>
    </button>
  `).join("");
  const recent = document.querySelector("#recent-grammars");
  recent.innerHTML = d.recent_grammars.length ? d.recent_grammars.map((item) => `
    <button class="recent-item" data-grammar-id="${esc(item.id)}"><strong>${esc(item.title_zh)}</strong><span>${esc(item.pattern)} · ${esc(item.level_code)}</span></button>
  `).join("") : `<div class="empty-inline">开始第一句后，这里会留下你的语法轨迹。</div>`;
}

async function chooseLevel(level) {
  const levelInfo = state.dashboard?.levels.find((item) => item.code === level);
  if (levelInfo && levelInfo.sentence_count === 0) {
    toast(`${level} 内容尚未导入，先从已有等级学习。`);
    return;
  }
  await api("/profile/level", { method: "POST", body: JSON.stringify({ learner_id: learnerId, level }) }, true);
  state.settings.level = level;
  state.currentSentence = null;
  saveSettings();
  navigate("learn");
}

async function loadLearnSentence(forceNext = false, random = false) {
  if (state.currentSentence && !forceNext) {
    renderLearn();
    return;
  }
  const exclude = forceNext && state.currentSentence ? `&exclude=${encodeURIComponent(state.currentSentence.id)}` : "";
  try {
    const selection = random ? "random" : "adaptive";
    state.currentSentence = await api(`/sentences/next?level=${state.settings.level}&learner_id=${learnerId}&selection=${selection}${exclude}`, {}, true);
    state.learnRevealed = false;
    state.revealFurigana = state.settings.furigana === "always";
    state.listeningScore = 0;
    state.mastery = 0;
    state.childTab = "variant";
    renderLearn();
  } catch {
    document.querySelector("#learn-stage").innerHTML = emptyState(`${state.settings.level} 暂无课程`, "可以在设置中切换到其他等级。", "返回首页", "home");
  }
}

function emptyState(title, copy, action = "", route = "") {
  return `<div class="empty-state"><strong>${esc(title)}</strong><p>${esc(copy)}</p>${action ? `<button class="secondary-button" data-route="${esc(route)}">${esc(action)}</button>` : ""}</div>`;
}

function renderLearn() {
  const s = state.currentSentence;
  if (!s) return;
  document.querySelector("#learn-level-badge").textContent = s.level_code;
  document.querySelector("#learn-step-count").textContent = `见过 ${s.exposure.shown_count} 次 · 单元 ${Number(s.unit_progress.mastery_percent).toFixed(0)}%`;
  const stage = document.querySelector("#learn-stage");
  stage.innerHTML = state.learnRevealed ? learnRevealedHTML(s) : learnBlindHTML(s);
}

function learnBlindHTML(s) {
  return `<article class="lesson-card">
    <div class="blind-panel">
      <span class="blind-label">场景提示 · 暂不显示日文</span>
      <div class="scene-text">${esc(s.scene_zh)}</div>
      <button class="sound-button" data-speak="current" aria-label="播放日语句子"></button>
      <span class="audio-caption">点击播放 · ${Number(state.settings.rate).toFixed(2).replace(".00", "")}×</span>
      <div class="listen-rating" role="group" aria-label="听懂程度">
        <button class="rating-button" data-listen-score="5">基本听懂</button>
        <button class="rating-button" data-listen-score="3">听懂一部分</button>
        <button class="rating-button" data-listen-score="1">没有听懂</button>
      </div>
    </div>
  </article>`;
}

function learnRevealedHTML(s) {
  const groups = s.children || { variant: [], real: [], personal: [] };
  const activeChildren = groups[state.childTab] || [];
  const tabLabels = { variant: `固定变体 ${groups.variant.length}`, real: `场景扩展 ${groups.real.length}`, personal: `个性模板 ${groups.personal.length}` };
  return `<div class="revealed-content">
    <article class="lesson-card">
      <div class="sentence-hero">
        <span class="soft-badge">${kindLabel(s.kind)} · ${esc(s.status)}</span>
        <div class="japanese-sentence ${furiganaClass()}" lang="ja">${rubyHTML(s.ruby_segments)}</div>
        <div class="sentence-tools">
          <button class="tool-button" data-speak="current">▶ 重新播放</button>
          ${state.settings.furigana === "tap" ? `<button class="tool-button" data-toggle-furigana>${state.revealFurigana ? "隐藏假名" : "显示假名"}</button>` : ""}
        </div>
        <details><summary class="text-button">查看中文意思</summary><p class="translation">${esc(s.translation_zh)}</p></details>
      </div>
      <div class="lesson-body">
        <section class="lesson-section">
          <div class="lesson-section-head"><div><span class="subtle">逐词索引</span><h2>点击词语查看词典</h2></div></div>
          ${tokenStrip(s.tokens)}
        </section>
        <section class="lesson-section">
          <div class="lesson-section-head"><div><span class="subtle">教学切分</span><h2>一句话怎样组合起来</h2></div></div>
          <div class="chunk-grid">${s.chunks.map((chunk) => `
            <article class="chunk-card"><div class="chunk-reading">${esc(chunk.reading)}</div><div class="chunk-surface" lang="ja">${esc(chunk.surface)}</div><div class="chunk-meaning">${esc(chunk.meaning_zh)}</div><span class="chunk-function">${esc(chunk.function_zh)}</span></article>
          `).join("")}</div>
        </section>
        <section class="lesson-section">
          <div class="lesson-section-head"><div><span class="subtle">本句新语法</span><h2>${esc(s.grammar_title)}</h2></div></div>
          <div class="grammar-card">
            <div><span class="subtle">表达结构</span><div class="grammar-pattern" lang="ja">${esc(s.grammar_pattern)}</div><p>${esc(s.grammar_meaning)}</p></div>
            <div class="grammar-copy"><p><b>接续：</b>${esc(s.grammar_formation)}</p><p><b>为什么这样说：</b>${esc(s.grammar_rationale)}</p><p><b>在当前句中：</b>${esc(s.grammar_note_zh)}</p><p><b>注意：</b>${esc(s.grammar_mistakes)}</p></div>
          </div>
        </section>
        <section class="lesson-section">
          <div class="lesson-section-head"><div><span class="subtle">结构不变，词语变化</span><h2>迁移到新的句子</h2></div></div>
          <div class="variant-tabs">${Object.entries(tabLabels).map(([key,label]) => `<button class="chip ${state.childTab === key ? "is-active" : ""}" data-child-tab="${key}">${label}</button>`).join("")}</div>
          <div class="variant-list">${activeChildren.map((child) => `
            <button class="variant-card" data-sentence-detail="${esc(child.id)}"><div class="variant-jp ${furiganaClass()}" lang="ja">${rubyHTML(child.ruby_segments)}</div><span class="variant-zh">${esc(child.translation_zh)}</span></button>
          `).join("")}</div>
        </section>
      </div>
    </article>
    <div class="lesson-footer">
      <div class="mastery-actions" role="group" aria-label="掌握程度">
        <button class="mastery-button ${state.mastery === 1 ? "is-selected" : ""}" data-mastery="1">不熟悉</button>
        <button class="mastery-button ${state.mastery === 3 ? "is-selected" : ""}" data-mastery="3">基本理解</button>
        <button class="mastery-button ${state.mastery === 5 ? "is-selected" : ""}" data-mastery="5">已经掌握</button>
      </div>
      <button class="primary-button" id="save-next" ${state.mastery ? "" : "disabled"}>保存并学习下一句 →</button>
    </div>
  </div>`;
}

async function saveAndNext(context = "learn") {
  const sentence = context === "review" ? state.reviewSentence : state.currentSentence;
  const mastery = state.mastery;
  if (!sentence || !mastery) return;
  const button = document.querySelector(context === "review" ? "#review-next" : "#save-next");
  if (button) button.disabled = true;
  const listening = context === "learn" ? (state.listeningScore || mastery) : mastery;
  const saved = await api("/progress", {
    method: "POST",
    body: JSON.stringify({
      learner_id: learnerId,
      sentence_id: context === "review" ? (sentence.review_card_id || sentence.id) : sentence.id,
      listening_score: listening,
      comprehension_score: mastery,
      production_score: mastery === 5 ? 4 : Math.max(1, mastery - 1),
      event_type: context === "review" ? "review" : "learn",
      review_mode: context === "review" ? state.reviewMode : "lesson",
    }),
  });
  toast("学习记录已保存 · FSRS " + saved.rating_label);
  state.mastery = 0;
  if (context === "review") await loadReview();
  else await loadLearnSentence(true);
}

async function loadReview() {
  try {
    state.reviewSentence = await api(`/review/next?learner_id=${learnerId}&mode=${state.reviewMode}&level=${state.settings.level}`, {}, true);
    state.reviewRevealed = false;
    state.revealFurigana = state.settings.furigana === "always";
    state.mastery = 0;
    renderReview();
  } catch {
    document.querySelector("#review-stage").innerHTML = emptyState("暂时没有复习内容", "先学习几个新句子，系统会根据表现安排复习。", "开始学习", "learn");
  }
}

function renderReview() {
  const s = state.reviewSentence;
  if (!s) return;
  const stage = document.querySelector("#review-stage");
  let prompt = "";
  if (state.reviewMode === "listening") prompt = `<span class="blind-label">只听声音，判断大意</span><div class="scene-text">先不看文字，注意句子的结构。</div><button class="sound-button" data-speak="review" aria-label="播放复习句子"></button>`;
  if (state.reviewMode === "recall") prompt = `<span class="blind-label">看中文，回想日文</span><div class="scene-text">${esc(s.translation_zh)}</div><p class="audio-caption">可以先在心里说一遍，再揭晓。</p>`;
  if (state.reviewMode === "grammar") prompt = `<span class="blind-label">识别句式</span><div class="scene-text"><span lang="ja">${esc(s.grammar_pattern)}</span><br><small>${esc(s.grammar_meaning)}</small></div><p class="audio-caption">想一想这个结构在完整句子中如何出现。</p>`;
  stage.innerHTML = `<article class="review-card"><div class="review-prompt">${prompt}
    ${state.reviewRevealed ? reviewAnswerHTML(s) : `<button class="primary-button review-answer" id="reveal-review">揭晓答案</button>`}
  </div></article>`;
}

function reviewAnswerHTML(s) {
  return `<div class="review-answer revealed-content">
    <div class="japanese-sentence ${furiganaClass()}" lang="ja">${rubyHTML(s.ruby_segments)}</div>
    <p class="translation">${esc(s.translation_zh)}</p>
    <div class="sentence-tools"><button class="tool-button" data-speak="review">▶ 播放</button>${state.settings.furigana === "tap" ? `<button class="tool-button" data-toggle-furigana>${state.revealFurigana ? "隐藏假名" : "显示假名"}</button>` : ""}</div>
    <p class="audio-caption">${esc(s.grammar_title)} · ${esc(s.grammar_pattern)}</p>
    <p class="subtle">${esc(s.review_reason_zh || "")}</p>
    <div class="mastery-actions" role="group" aria-label="复习掌握程度">
      <button class="mastery-button ${state.mastery === 1 ? "is-selected" : ""}" data-mastery="1">还不熟</button>
      <button class="mastery-button ${state.mastery === 3 ? "is-selected" : ""}" data-mastery="3">想起来了</button>
      <button class="mastery-button ${state.mastery === 5 ? "is-selected" : ""}" data-mastery="5">很熟练</button>
    </div>
    <button class="primary-button" id="review-next" ${state.mastery ? "" : "disabled"}>保存并换一句</button>
  </div>`;
}

async function loadGrammars() {
  const params = new URLSearchParams({ learner_id: learnerId, limit: 100 });
  if (state.grammarLevel) params.set("level", state.grammarLevel);
  if (state.grammarQuery) params.set("q", state.grammarQuery);
  const data = await api(`/grammars?${params}`, {}, true);
  const grid = document.querySelector("#grammar-grid");
  grid.innerHTML = data.items.length ? data.items.map((item) => {
    const progress = Math.max(item.recognition_score, item.production_score);
    return `<button class="grammar-item" data-grammar-id="${esc(item.id)}">
      <div class="grammar-item-head"><span class="soft-badge">${esc(item.level_code)}</span><span class="subtle">${item.sentence_count}句</span></div>
      <div class="pattern" lang="ja">${esc(item.pattern)}</div><h3>${esc(item.title_zh)}</h3><p>${esc(item.meaning_zh)}</p>
      <div class="mini-progress" aria-label="掌握度 ${progress}/5">${[1,2,3,4,5].map((n) => `<i class="${n <= progress ? "filled" : ""}"></i>`).join("")}</div>
    </button>`;
  }).join("") : emptyState("没有匹配的语法", "换一个关键词或等级试试。");
}

async function openGrammar(grammarId) {
  const g = await api(`/grammars/${encodeURIComponent(grammarId)}?learner_id=${learnerId}`, {}, true);
  els.dialogTitle.textContent = g.title_zh;
  els.dialogBadge.textContent = `${g.level_code} · ${g.status}`;
  els.dialogContent.innerHTML = `<section class="dialog-section"><div class="grammar-pattern" lang="ja">${esc(g.pattern)}</div><p class="dialog-zh">${esc(g.meaning_zh)}</p></section>
    <section class="dialog-section"><h3>怎样连接</h3><p class="dialog-zh">${esc(g.formation_zh)}</p><h3>为什么这样表达</h3><p class="dialog-zh">${esc(g.rationale_zh)}</p><h3>使用提示</h3><p class="dialog-zh">${esc(g.nuance_zh)} ${esc(g.common_mistakes_zh)}</p></section>
    <section class="dialog-section"><h3>相关句子</h3><div class="variant-list">${g.sentences.slice(0, 9).map((s) => `<button class="variant-card" data-sentence-detail="${esc(s.id)}"><div class="variant-jp ${furiganaClass()}" lang="ja">${rubyHTML(s.ruby_segments)}</div><span class="variant-zh">${esc(s.translation_zh)}</span></button>`).join("")}</div></section>`;
  els.dialog.showModal();
}

async function loadLibrary() {
  const params = new URLSearchParams({ page: state.libraryPage, page_size: 24 });
  const level = document.querySelector("#library-level")?.value || "";
  const kind = document.querySelector("#library-kind")?.value || "";
  const q = document.querySelector("#library-search")?.value.trim() || "";
  if (level) params.set("level", level);
  if (kind) params.set("kind", kind);
  if (q) params.set("q", q);
  const data = await api(`/library?${params}`, {}, true);
  state.libraryTotal = data.total;
  document.querySelector("#library-total").textContent = `${data.total.toLocaleString()} 个句子`;
  const list = document.querySelector("#sentence-list");
  list.innerHTML = data.items.length ? data.items.map((s) => `<button class="sentence-row" data-sentence-detail="${esc(s.id)}">
    <span class="type-label">${esc(s.level_code)}<br>${kindLabel(s.kind)}</span>
    <span class="row-jp ${furiganaClass()}" lang="ja">${rubyHTML(s.ruby_segments)}</span>
    <span class="row-zh">${esc(s.translation_zh)}</span><span class="row-grammar">${esc(s.grammar_title || "")}</span>
  </button>`).join("") : emptyState("没有匹配的句子", "调整搜索词或筛选条件后再试。",
  );
  renderPagination(data.total, data.page, data.page_size);
}

function renderPagination(total, page, size) {
  const pages = Math.max(1, Math.ceil(total / size));
  const node = document.querySelector("#library-pagination");
  if (pages <= 1) { node.innerHTML = ""; return; }
  node.innerHTML = `<button data-library-page="${Math.max(1, page - 1)}" ${page === 1 ? "disabled" : ""} aria-label="上一页">←</button><button disabled>${page} / ${pages}</button><button data-library-page="${Math.min(pages, page + 1)}" ${page === pages ? "disabled" : ""} aria-label="下一页">→</button>`;
}

async function loadWords() {
  const params = new URLSearchParams({ page: state.wordPage, page_size: 30 });
  const q = document.querySelector("#word-search")?.value.trim() || "";
  const common = document.querySelector("#word-common")?.checked || false;
  if (q) params.set("q", q);
  if (common) params.set("common_only", "true");
  const data = await api(`/words?${params}`, {}, true);
  document.querySelector("#word-total").textContent = `${data.total.toLocaleString()} 个词条`;
  const list = document.querySelector("#word-list");
  list.innerHTML = data.items.length ? data.items.map((word) => `<button class="word-row" data-word-id="${word.id}">
    <span><strong lang="ja">${esc(word.written_form || word.reading)}</strong><small lang="ja">${esc(word.reading)}</small></span>
    <span class="word-gloss">${esc(word.gloss_zh || word.gloss_en || "释义整理中")}</span>
    <span class="soft-badge">${word.common_rank ? `常用 ${word.common_rank}` : "扩展"}</span>
  </button>`).join("") : emptyState("没有匹配的词条", "尝试输入词典形、假名或释义开头。 ");
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  document.querySelector("#word-pagination").innerHTML = pages <= 1 ? "" : `<button data-word-page="${Math.max(1, data.page - 1)}" ${data.page === 1 ? "disabled" : ""}>←</button><button disabled>${data.page} / ${pages}</button><button data-word-page="${Math.min(pages, data.page + 1)}" ${data.page === pages ? "disabled" : ""}>→</button>`;
}

async function openWord(wordId) {
  const word = await api(`/words/${wordId}?learner_id=${learnerId}`, {}, true);
  const first = word.forms[0] || {};
  els.dialogTitle.textContent = first.written_form || first.reading || "词条";
  els.dialogBadge.textContent = word.common_rank ? `常用度 ${word.common_rank}` : "JMdict 扩展词条";
  const zh = word.glosses.filter((g) => g.language_code === "zho");
  const en = word.glosses.filter((g) => g.language_code === "eng");
  els.dialogContent.innerHTML = `<section class="dialog-section word-hero">
      <div class="dialog-jp ${furiganaClass()}" lang="ja">${word.furigana_segments.length ? rubyHTML(word.furigana_segments) : esc(first.written_form || first.reading)}</div>
      <p class="word-reading" lang="ja">${esc(first.reading || "")}</p>
      <p class="dialog-zh">${esc(zh.map((g) => g.gloss).join("；") || "中文释义尚待补充，下方保留JMdict英文原义。")}</p>
      <p class="word-english">${esc(en.slice(0, 12).map((g) => g.gloss).join("；"))}</p>
      <div class="dialog-actions"><button class="secondary-button" data-word-speak="${esc(first.written_form || first.reading)}">▶ 播放读音</button><button class="secondary-button" data-save-word="${word.id}">${word.saved ? "✓ 已收藏生词" : "＋ 收藏生词"}</button></div>
    </section>
    <section class="dialog-section"><h3>词形与来源</h3><p class="dialog-zh">词性：${esc(word.part_of_speech || "未标注")}</p><div class="form-list">${word.forms.slice(0, 16).map((form) => `<span><b lang="ja">${esc(form.written_form)}</b><small lang="ja">${esc(form.reading)}</small></span>`).join("")}</div>${word.memory_note_zh ? `<p class="memory-note">记忆线索：${esc(word.memory_note_zh)}<small>${word.memory_note_status === "reviewed" ? "已审校" : "自动生成 · 待审校"}</small></p>` : `<p class="subtle">这个词暂时没有可靠的记忆线索。</p>`}</section>
    <section class="dialog-section"><h3>课程例句</h3><div class="variant-list">${word.course_examples.length ? word.course_examples.map((s) => `<button class="variant-card" data-sentence-detail="${esc(s.id)}"><div class="variant-jp" lang="ja">${esc(s.text)}</div><span class="variant-zh">${esc(s.translation_zh)}</span></button>`).join("") : `<p class="subtle">课程句中暂未出现。</p>`}</div></section>
    <section class="dialog-section"><h3>原文中的使用</h3><div class="variant-list">${word.reading_examples.length ? word.reading_examples.map((s) => `<button class="variant-card" data-reading-id="${esc(s.id)}"><div class="variant-jp" lang="ja">${esc(s.text)}</div><span class="variant-zh">《${esc(s.title)}》· ${esc(s.author)}</span></button>`).join("") : `<p class="subtle">阅读语料中暂未出现。</p>`}</div></section>`;
  els.dialog.showModal();
}

async function loadReadings() {
  const params = new URLSearchParams({ page: state.readingPage, page_size: 24, learner_id: learnerId });
  const q = document.querySelector("#reading-search")?.value.trim() || "";
  const workId = document.querySelector("#reading-work")?.value || "";
  if (q) params.set("q", q);
  if (workId) params.set("work_id", workId);
  const data = await api(`/readings?${params}`, {}, true);
  document.querySelector("#reading-work-grid").innerHTML = data.works.map((work) => `<article class="reading-work-card">
    <div><span class="soft-badge">${work.section_count}个阅读单元</span><h3>《${esc(work.title)}》</h3><p>${esc(work.author)}</p></div>
    <p class="reading-guide">${esc(work.guide_zh || "导读整理中")}</p>
    <div class="reading-progress"><i style="--value:${work.progress_percent}%"></i><span>${Number(work.progress_percent).toFixed(1)}%</span></div>
    <div class="dialog-actions"><button class="secondary-button" data-reading-sections="${esc(work.id)}">阅读单元</button><button class="primary-button" data-reading-id="${esc(work.last_sentence_id || work.first_sentence_id)}">${work.last_sentence_id ? "继续阅读" : "开始阅读"}</button></div>
  </article>`).join("");
  const select = document.querySelector("#reading-work");
  const current = select.value;
  select.innerHTML = `<option value="">所有作品</option>${data.works.map((work) => `<option value="${esc(work.id)}">${esc(work.title)} · ${work.sentence_count}句</option>`).join("")}`;
  select.value = current;
  document.querySelector("#reading-total").textContent = `${data.total.toLocaleString()} 个阅读句`;
  document.querySelector("#reading-list").innerHTML = data.items.length ? data.items.map((s) => `<button class="sentence-row reading-row" data-reading-id="${esc(s.id)}"><span class="type-label">${s.bookmarked ? "★" : s.difficulty_score.toFixed(1)}<br>${s.bookmarked ? "书签" : "难度"}</span><span class="row-jp" lang="ja">${esc(s.text)}</span><span class="row-zh">《${esc(s.title)}》· ${esc(s.author)}</span><span class="row-grammar">${s.translation_zh ? "有译文" : "词典拆解"}</span></button>`).join("") : emptyState("没有匹配的原文", "换一个关键词或作品试试。 ");
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  document.querySelector("#reading-pagination").innerHTML = pages <= 1 ? "" : `<button data-reading-page="${Math.max(1, data.page - 1)}" ${data.page === 1 ? "disabled" : ""}>←</button><button disabled>${data.page} / ${pages}</button><button data-reading-page="${Math.min(pages, data.page + 1)}" ${data.page === pages ? "disabled" : ""}>→</button>`;
}

async function openReading(sentenceId) {
  const s = await api(`/readings/${encodeURIComponent(sentenceId)}?learner_id=${learnerId}`, {}, true);
  await api("/readings/progress", { method: "POST", body: JSON.stringify({ learner_id: learnerId, sentence_id: s.id }) });
  els.dialogTitle.textContent = `《${s.title}》`;
  els.dialogBadge.textContent = `${s.author} · ${s.status}`;
  els.dialogContent.innerHTML = `<section class="dialog-section"><div class="dialog-jp ${furiganaClass()}" lang="ja">${rubyHTML(s.ruby_segments)}</div><p class="dialog-zh">${esc(s.translation_zh || "本句尚无经审校的中文译文，可通过下方逐词结构理解。")}</p><div class="dialog-actions"><button class="secondary-button" data-word-speak="${esc(s.text)}">▶ 播放原文</button><button class="secondary-button" data-reading-bookmark="${esc(s.id)}">${s.bookmarked ? "★ 已加入书签" : "☆ 加入书签"}</button></div></section>
    <section class="dialog-section"><h3>逐词结构</h3>${tokenStrip(s.tokens)}</section>
    <section class="dialog-section"><h3>作品导读 <small>自动整理 · 待审校</small></h3><p class="dialog-zh">${esc(s.guide_zh || "导读整理中")}</p><p class="subtle">${esc(s.era_note_zh || "")}</p></section>
    ${s.literary_notes.length ? `<section class="dialog-section"><h3>文学表达</h3>${s.literary_notes.map((note) => `<article class="literary-note"><b>${esc(note.title_zh)}</b><span lang="ja">${esc(note.pattern)}</span><p>${esc(note.explanation_zh)} ${esc(note.nuance_zh)}</p></article>`).join("")}</section>` : ""}
    <section class="dialog-section reading-navigation"><button class="secondary-button" data-reading-id="${esc(s.previous_id || "")}" ${s.previous_id ? "" : "disabled"}>← 上一句</button><button class="secondary-button" data-reading-sections="${esc(s.work_id)}">${esc(s.section?.title_zh || "阅读单元")}</button><button class="primary-button" data-reading-id="${esc(s.next_id || "")}" ${s.next_id ? "" : "disabled"}>下一句 →</button></section>
    <section class="dialog-section source-note"><h3>原文来源</h3><p>${esc(s.source_name)} · ${esc(s.orthography)} · ${esc(s.copyright_status)}</p><a href="${esc(s.source_url)}" target="_blank" rel="noopener">查看青空文库作品卡片</a></section>`;
  els.dialog.showModal();
}

async function openReadingSections(workId) {
  const data = await api(`/reading-sections?work_id=${encodeURIComponent(workId)}&learner_id=${learnerId}`, {}, true);
  els.dialogTitle.textContent = `《${data.work.title}》阅读单元`;
  els.dialogBadge.textContent = `${Number(data.work.progress_percent).toFixed(1)}% 已读`;
  els.dialogContent.innerHTML = `<section class="dialog-section"><p class="dialog-zh">${esc(data.work.guide_zh || "")}</p><p class="subtle">每个单元连续收录25句，便于在手机上逐段阅读；单元标题不是原作章节名。</p></section><section class="dialog-section section-list">${data.items.map((section) => `<button class="variant-card" data-reading-section="${esc(section.id)}"><div><b>${esc(section.title_zh)}</b><small>第${section.start_position}—${section.end_position}句</small></div><span class="variant-zh" lang="ja">${esc(section.opening_text || "")}</span></button>`).join("")}</section>`;
  els.dialog.showModal();
}

async function openReadingSection(sectionId) {
  const data = await api(`/reading-sections/${encodeURIComponent(sectionId)}?learner_id=${learnerId}`, {}, true);
  els.dialogTitle.textContent = `${data.section.title} · ${data.section.title_zh}`;
  els.dialogBadge.textContent = `${data.items.length}句`;
  els.dialogContent.innerHTML = `<section class="dialog-section"><p class="dialog-zh">${esc(data.section.summary_zh)}</p></section><section class="dialog-section reading-section-sentences">${data.items.map((s) => `<button class="variant-card" data-reading-id="${esc(s.id)}"><span class="soft-badge">${s.bookmarked ? "★" : s.position}</span><div class="variant-jp" lang="ja">${esc(s.text)}</div></button>`).join("")}</section>`;
  els.dialog.showModal();
}

async function showReadingBookmarks() {
  const data = await api(`/reading-bookmarks?learner_id=${learnerId}`, {}, true);
  els.dialogTitle.textContent = "我的阅读书签";
  els.dialogBadge.textContent = `${data.total}条`;
  els.dialogContent.innerHTML = data.items.length ? `<section class="dialog-section variant-list">${data.items.map((s) => `<button class="variant-card" data-reading-id="${esc(s.id)}"><div class="variant-jp" lang="ja">${esc(s.text)}</div><span class="variant-zh">《${esc(s.title)}》· 第${s.position}句</span></button>`).join("")}</section>` : `<p class="subtle">阅读时点击“加入书签”，重要句子会出现在这里。</p>`;
  els.dialog.showModal();
}

async function analyzeSentence() {
  const input = document.querySelector("#analyzer-input");
  const text = input.value.trim();
  if (!text) { toast("请先输入一个日文句子。", "error"); return; }
  const data = await api("/analyze", { method: "POST", body: JSON.stringify({ text }) }, true);
  const node = document.querySelector("#analysis-result");
  node.innerHTML = `<h3>结构拆解</h3><p class="dialog-zh">${esc(data.structure_summary_zh)}</p>${tokenStrip(data.tokens.map((token) => ({ ...token, gloss_zh: token.glosses.filter((g) => g.language_code === "zho").map((g) => g.gloss).join("；"), gloss_en: token.glosses.filter((g) => g.language_code === "eng").map((g) => g.gloss).join("; ") })))}<div class="clause-list">${data.clauses.map((clause) => `<article><b lang="ja">${esc(clause.surface)}</b><span>${esc(clause.role_zh)}${clause.connector_zh ? ` · ${esc(clause.connector_zh)}` : ""}</span></article>`).join("")}</div><p class="literal-order"><b>日语顺序直译线索：</b>${esc(data.literal_order_zh)}</p>${data.ambiguity_notes.map((note) => `<p class="subtle">${esc(note)}</p>`).join("")}<p class="subtle">这是词典级结构线索，不等同于完整意译；复杂从句仍需结合上下文确认。</p>`;
}

async function openSentence(sentenceId) {
  const s = await api(`/sentences/${encodeURIComponent(sentenceId)}?learner_id=${learnerId}`, {}, true);
  els.dialogTitle.textContent = s.grammar_title || "句子详情";
  els.dialogBadge.textContent = `${s.level_code} · ${kindLabel(s.kind)} · ${s.status}`;
  els.dialogContent.innerHTML = `<section class="dialog-section"><div class="dialog-jp ${furiganaClass()}" lang="ja">${rubyHTML(s.ruby_segments)}</div><p class="dialog-zh">${esc(s.translation_zh)}</p><button class="secondary-button" data-dialog-speak="${esc(s.id)}">▶ 播放日语</button></section>
    <section class="dialog-section"><h3>逐词索引</h3>${tokenStrip(s.tokens)}</section>
    <section class="dialog-section"><h3>教学切分</h3><div class="chunk-grid">${s.chunks.map((c) => `<article class="chunk-card"><div class="chunk-reading">${esc(c.reading)}</div><div class="chunk-surface">${esc(c.surface)}</div><div class="chunk-meaning">${esc(c.meaning_zh)}</div><span class="chunk-function">${esc(c.function_zh)}</span></article>`).join("")}</div></section>
    <section class="dialog-section"><h3>${esc(s.grammar_title)}</h3><p class="dialog-zh"><b lang="ja">${esc(s.grammar_pattern)}</b><br>${esc(s.grammar_note_zh)}</p></section>`;
  els.dialog.dataset.audioText = s.audio_text;
  els.dialog.showModal();
}

async function loadRecords() {
  const params = new URLSearchParams({ learner_id: learnerId });
  if (state.recordLevel) params.set("level", state.recordLevel);
  const data = await api(`/records?${params}`, {}, true);
  const summary = data.summary;
  document.querySelector("#record-summary").innerHTML = [
    [summary.viewed_sentences, "见过的句子"],
    [summary.total_showings, "累计出现"],
    [summary.total_completions, "完成学习"],
    [summary.started_units, "开始的单元"],
    [`${Number(summary.average_mastery).toFixed(0)}%`, "平均掌握度"],
  ].map(([value, label]) => `<article class="record-metric"><strong>${esc(value)}</strong><span>${label}</span></article>`).join("");
  document.querySelector("#unit-record-list").innerHTML = data.units.length ? data.units.map((unit) => `<article class="unit-record">
    <div class="unit-record-head"><span class="soft-badge">${unit.level_code}</span><b>${Number(unit.mastery_percent).toFixed(0)}%</b></div>
    <h3>${esc(unit.title_zh || unit.pattern || "核心表达")}</h3><p lang="ja">${esc(unit.text)}</p>
    <div class="unit-mastery"><i style="--value:${unit.mastery_percent}%"></i></div>
    <div class="unit-record-meta"><span>出现 ${unit.shown_count} 次</span><span>完成 ${unit.completed_sentences}/${unit.sentence_count} 句</span></div>
  </article>`).join("") : emptyState("这个等级还没有开始的单元", "抽取一条句子后，单元出现次数和掌握度会显示在这里。", "随机学习", "learn");
  document.querySelector("#recent-sentence-records").innerHTML = data.recent_sentences.length ? data.recent_sentences.map((s) => `<button class="sentence-row" data-sentence-detail="${esc(s.id)}"><span class="type-label">${s.level_code}<br>${s.shown_count}次</span><span class="row-jp" lang="ja">${esc(s.text)}</span><span class="row-zh">${esc(s.translation_zh)}</span><span class="row-grammar">完成 ${s.completed_count}</span></button>`).join("") : emptyState("还没有学习记录", "开始随机学习一条句子后，这里会记录它出现过多少次。", "开始学习", "learn");
}

function renderSettings() {
  document.querySelector("#speed-select").value = String(state.settings.rate);
  document.querySelector("#furigana-select").value = state.settings.furigana;
  document.querySelector("#level-select").value = state.settings.level;
  document.querySelector("#daily-target").value = String(state.settings.dailyTarget);
  document.querySelector("#retention-select").value = String(state.settings.desiredRetention);
  document.querySelector("#animation-toggle").checked = state.settings.animations;
  populateVoices();
}

function populateVoices() {
  if (!("speechSynthesis" in window)) {
    document.querySelector("#voice-select").innerHTML = `<option value="">此浏览器不支持语音</option>`;
    return;
  }
  state.voices = speechSynthesis.getVoices().filter((voice) => voice.lang.toLowerCase().startsWith("ja"));
  const select = document.querySelector("#voice-select");
  if (!select) return;
  if (!state.voices.length) {
    select.innerHTML = `<option value="">未发现日语语音</option>`;
    return;
  }
  select.innerHTML = state.voices.map((voice) => `<option value="${esc(voice.voiceURI)}">${esc(voice.name)} · ${esc(voice.lang)}</option>`).join("");
  if (!state.settings.voiceURI || !state.voices.some((voice) => voice.voiceURI === state.settings.voiceURI)) {
    state.settings.voiceURI = state.voices[0].voiceURI;
    saveSettings();
  }
  select.value = state.settings.voiceURI;
}

function speak(text, button = null) {
  if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
    toast("当前浏览器不支持语音朗读，请换用最新版Chrome或Edge。", "error");
    return;
  }
  if (!state.voices.length) populateVoices();
  if (!state.voices.length) {
    toast("设备中没有可用的日语语音，请先在系统中安装日语语音包。", "error");
    return;
  }
  if (state.currentSpeech?.text === text && speechSynthesis.speaking) {
    if (speechSynthesis.paused) {
      speechSynthesis.resume();
      button?.classList.add("is-speaking");
      button?.setAttribute("aria-label", "暂停日语句子");
    } else {
      speechSynthesis.pause();
      button?.classList.remove("is-speaking");
      button?.setAttribute("aria-label", "继续播放日语句子");
    }
    return;
  }
  stopSpeech();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ja-JP";
  utterance.rate = Number(state.settings.rate);
  utterance.voice = state.voices.find((voice) => voice.voiceURI === state.settings.voiceURI) || state.voices[0];
  utterance.onstart = () => {
    button?.classList.add("is-speaking");
    button?.setAttribute("aria-label", "暂停日语句子");
  };
  utterance.onend = () => {
    button?.classList.remove("is-speaking");
    button?.setAttribute("aria-label", "播放日语句子");
    state.currentSpeech = null;
  };
  utterance.onerror = () => { button?.classList.remove("is-speaking"); state.currentSpeech = null; toast("语音播放失败，请检查系统日语语音。", "error"); };
  state.currentSpeech = { text, button, utterance };
  speechSynthesis.speak(utterance);
}

function stopSpeech() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  state.currentSpeech?.button?.classList.remove("is-speaking");
  state.currentSpeech?.button?.setAttribute("aria-label", "播放日语句子");
  state.currentSpeech = null;
}

async function resetProgress(button) {
  if (!state.resetArmed) {
    state.resetArmed = true;
    button.textContent = "再次点击并确认重置";
    window.setTimeout(() => { state.resetArmed = false; button.textContent = "重置学习进度"; }, 6000);
    return;
  }
  if (!window.confirm("确定清除全部学习进度吗？课程、阅读与词典内容不会被删除。")) {
    state.resetArmed = false;
    button.textContent = "重置学习进度";
    return;
  }
  await api("/reset", { method: "POST", body: JSON.stringify({ learner_id: learnerId, confirmation: "RESET" }) }, true);
  state.resetArmed = false;
  state.currentSentence = null;
  state.reviewSentence = null;
  button.textContent = "重置学习进度";
  toast("学习进度已重置");
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, [data-route]");
  if (!target) return;
  try {
    if (target.dataset.route) { navigate(target.dataset.route); return; }
    if (target.id === "continue-learning") { navigate("learn"); return; }
    if (target.id === "random-learning") { await loadLearnSentence(true, true); return; }
    if (target.dataset.levelCard) { await chooseLevel(target.dataset.levelCard); return; }
    if (target.dataset.listenScore) {
      state.listeningScore = Number(target.dataset.listenScore);
      state.learnRevealed = true;
      renderLearn();
      return;
    }
    if (target.dataset.speak) {
      const sentence = target.dataset.speak === "review" ? state.reviewSentence : state.currentSentence;
      if (sentence) speak(sentence.audio_text, target);
      return;
    }
    if (target.dataset.dialogSpeak) { speak(els.dialog.dataset.audioText, target); return; }
    if (target.hasAttribute("data-toggle-furigana")) {
      state.revealFurigana = !state.revealFurigana;
      state.route === "review" ? renderReview() : renderLearn();
      return;
    }
    if (target.dataset.childTab) { state.childTab = target.dataset.childTab; renderLearn(); return; }
    if (target.dataset.mastery) {
      state.mastery = Number(target.dataset.mastery);
      state.route === "review" ? renderReview() : renderLearn();
      return;
    }
    if (target.id === "save-next") { await saveAndNext("learn"); return; }
    if (target.id === "reveal-review") { state.reviewRevealed = true; renderReview(); return; }
    if (target.id === "review-next") { await saveAndNext("review"); return; }
    if (target.dataset.reviewMode) {
      state.reviewMode = target.dataset.reviewMode;
      document.querySelectorAll("[data-review-mode]").forEach((node) => node.classList.toggle("is-active", node === target));
      await loadReview(); return;
    }
    if (target.dataset.level !== undefined && target.closest("#grammar-level-tabs")) {
      state.grammarLevel = target.dataset.level;
      document.querySelectorAll("#grammar-level-tabs .chip").forEach((node) => node.classList.toggle("is-active", node === target));
      await loadGrammars(); return;
    }
    if (target.dataset.recordLevel !== undefined) {
      state.recordLevel = target.dataset.recordLevel;
      document.querySelectorAll("#record-level-tabs .chip").forEach((node) => node.classList.toggle("is-active", node === target));
      await loadRecords(); return;
    }
    if (target.dataset.grammarId) { await openGrammar(target.dataset.grammarId); return; }
    if (target.dataset.sentenceDetail) { await openSentence(target.dataset.sentenceDetail); return; }
    if (target.dataset.saveWord) {
      const lexemeId = Number(target.dataset.saveWord);
      const result = await api("/saved-words/toggle", { method: "POST", body: JSON.stringify({ learner_id: learnerId, lexeme_id: lexemeId, source_type: "dictionary" }) });
      toast(result.saved ? "已加入生词收藏" : "已移出生词收藏");
      await openWord(lexemeId); return;
    }
    if (target.dataset.wordId) { await openWord(Number(target.dataset.wordId)); return; }
    if (target.dataset.readingBookmark) {
      const result = await api("/reading-bookmarks/toggle", { method: "POST", body: JSON.stringify({ learner_id: learnerId, sentence_id: target.dataset.readingBookmark }) });
      toast(result.bookmarked ? "已加入阅读书签" : "已移除阅读书签");
      await openReading(target.dataset.readingBookmark); return;
    }
    if (target.dataset.readingSections) { await openReadingSections(target.dataset.readingSections); return; }
    if (target.dataset.readingSection) { await openReadingSection(target.dataset.readingSection); return; }
    if (target.dataset.readingId) { await openReading(target.dataset.readingId); return; }
    if (target.dataset.wordSpeak) { speak(target.dataset.wordSpeak, target); return; }
    if (target.dataset.libraryPage) { state.libraryPage = Number(target.dataset.libraryPage); await loadLibrary(); return; }
    if (target.dataset.wordPage) { state.wordPage = Number(target.dataset.wordPage); await loadWords(); return; }
    if (target.dataset.readingPage) { state.readingPage = Number(target.dataset.readingPage); await loadReadings(); return; }
    if (target.id === "analyze-button") { await analyzeSentence(); return; }
    if (target.id === "show-reading-bookmarks") { await showReadingBookmarks(); return; }
    if (target.id === "logout-account") {
      await api("/account/logout", { method: "POST" }, true);
      showAuthGate({ configured: true, authenticated: false });
      return;
    }
    if (target.id === "dialog-close") { els.dialog.close(); return; }
    if (target.id === "reset-progress") { await resetProgress(target); }
  } catch { /* API errors are shown as toasts. */ }
});

document.querySelector("#library-filters").addEventListener("submit", async (event) => {
  event.preventDefault(); state.libraryPage = 1; await loadLibrary();
});

document.querySelector("#word-filters").addEventListener("submit", async (event) => {
  event.preventDefault(); state.wordPage = 1; await loadWords();
});

document.querySelector("#reading-filters").addEventListener("submit", async (event) => {
  event.preventDefault(); state.readingPage = 1; await loadReadings();
});

let grammarSearchTimer = 0;
document.querySelector("#grammar-search").addEventListener("input", (event) => {
  clearTimeout(grammarSearchTimer);
  grammarSearchTimer = window.setTimeout(async () => { state.grammarQuery = event.target.value.trim(); await loadGrammars(); }, 260);
});

document.querySelector("#voice-select").addEventListener("change", (event) => { state.settings.voiceURI = event.target.value; saveSettings(); });
document.querySelector("#speed-select").addEventListener("change", (event) => { state.settings.rate = Number(event.target.value); stopSpeech(); saveSettings(); });
document.querySelector("#furigana-select").addEventListener("change", (event) => { state.settings.furigana = event.target.value; state.revealFurigana = event.target.value === "always"; saveSettings(); });
document.querySelector("#daily-target").addEventListener("change", (event) => { state.settings.dailyTarget = Number(event.target.value); saveSettings(); });
document.querySelector("#retention-select").addEventListener("change", async (event) => {
  state.settings.desiredRetention = Number(event.target.value); saveSettings();
  await api("/profile/retention", { method: "POST", body: JSON.stringify({ learner_id: learnerId, desired_retention: state.settings.desiredRetention }) });
  toast("复习记忆目标已更新");
});
document.querySelector("#animation-toggle").addEventListener("change", (event) => { state.settings.animations = event.target.checked; saveSettings(); });
document.querySelector("#level-select").addEventListener("change", async (event) => {
  await chooseLevel(event.target.value);
  if (state.route !== "learn") renderSettings();
});

els.dialog.addEventListener("click", (event) => {
  if (event.target === els.dialog) els.dialog.close();
});

document.addEventListener("keydown", (event) => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  if (event.key === " " && (state.route === "learn" || state.route === "review")) {
    event.preventDefault();
    const sentence = state.route === "review" ? state.reviewSentence : state.currentSentence;
    if (sentence) speak(sentence.audio_text);
  }
  if (event.key.toLowerCase() === "r" && state.route === "learn" && !state.learnRevealed) {
    state.listeningScore = 3; state.learnRevealed = true; renderLearn();
  }
  if (event.key.toLowerCase() === "n" && state.route === "learn" && state.mastery) saveAndNext("learn");
});

window.addEventListener("hashchange", () => activateRoute(location.hash.slice(1)));
window.addEventListener("beforeunload", stopSpeech);
if ("speechSynthesis" in window) speechSynthesis.addEventListener?.("voiceschanged", populateVoices);

function showAuthGate(status) {
  const gate = document.querySelector("#auth-gate");
  state.account = status;
  gate.hidden = Boolean(status.authenticated);
  const setup = !status.configured;
  document.querySelector("#auth-title").textContent = setup ? "创建你的私人账号" : "回到你的学习空间";
  document.querySelector("#auth-copy").textContent = setup ? "首次设置后，所有句子次数、单元掌握度和复习记录都会归入这个账号。" : "登录后继续上次的句子和阅读位置。";
  document.querySelector("#auth-submit").textContent = setup ? "创建并进入" : "登录";
  document.querySelector("#auth-password").autocomplete = setup ? "new-password" : "current-password";
  document.querySelector("#auth-hint").textContent = setup ? "密码至少8位；目前是单人私人账号。" : "账号会保持登录30天。";
  showAuthError();
  if (!gate.hidden) window.setTimeout(() => document.querySelector("#auth-username").focus(), 0);
}

function showAuthError(message = "", field = null) {
  const error = document.querySelector("#auth-error");
  error.textContent = message;
  error.hidden = !message;
  document.querySelectorAll("#auth-form input").forEach((input) => input.removeAttribute("aria-invalid"));
  if (field) {
    field.setAttribute("aria-invalid", "true");
    field.focus();
  }
}

document.querySelector("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const usernameInput = document.querySelector("#auth-username");
  const passwordInput = document.querySelector("#auth-password");
  const submit = document.querySelector("#auth-submit");
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  const endpoint = state.account?.configured ? "/account/login" : "/account/setup";
  showAuthError();
  if (username.length < 3) {
    showAuthError("用户名至少需要3个字符。", usernameInput);
    return;
  }
  if (username.length > 40 || !/^[A-Za-z0-9_.\-\u4e00-\u9fff]+$/.test(username)) {
    showAuthError("用户名只能使用中文、字母、数字、点、短横线或下划线。", usernameInput);
    return;
  }
  if (password.length < 8) {
    showAuthError("密码至少需要8位。", passwordInput);
    return;
  }
  if (password.length > 128) {
    showAuthError("密码不能超过128位。", passwordInput);
    return;
  }
  submit.disabled = true;
  const originalLabel = submit.textContent;
  submit.textContent = state.account?.configured ? "正在登录…" : "正在创建…";
  form.setAttribute("aria-busy", "true");
  try {
    await api(endpoint, { method: "POST", body: JSON.stringify({ username, password }) }, true);
    const status = await api("/account/status");
    showAuthGate(status);
    document.querySelector("#auth-password").value = "";
    await activateRoute(location.hash.slice(1) || "home");
  } catch (error) {
    showAuthError(error.message || "账号请求失败，请稍后再试。");
  } finally {
    submit.disabled = false;
    submit.textContent = originalLabel;
    form.removeAttribute("aria-busy");
  }
});

document.querySelector("#auth-form").addEventListener("input", () => showAuthError());

async function init() {
  saveSettings();
  populateVoices();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  const account = await api("/account/status");
  showAuthGate(account);
  if (!account.authenticated) return;
  await activateRoute(location.hash.slice(1) || "home");
}

init().catch((error) => toast(error.message || "应用初始化失败", "error"));

