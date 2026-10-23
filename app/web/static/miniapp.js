const tg = window.Telegram?.WebApp;
const state = { page: "home", scope: "mine", filter: "all", pageIndex: 0, templatePage: 0, week: 0, historyPage: 0 };
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const fmt = value => Number(value || 0).toLocaleString("ru-RU");
const requestKey = () => crypto.randomUUID();
const formatDate = (value, options = { day:"2-digit", month:"2-digit", year:"numeric" }) => value
  ? new Intl.DateTimeFormat("ru-RU", { ...options, timeZone:window.tfTimezone || "Europe/Moscow" }).format(new Date(value))
  : "";
const statusInfo = {
  BACKLOG: ["Ждёт согласования", "orange"], TODO: ["К выполнению", "blue"],
  IN_PROGRESS: ["В работе", "blue"], DONE: ["Выполнено", "green"], CANCELLED: ["В архиве", "red"]
};

function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2200);
}

async function api(path, { method = "GET", body } = {}) {
  if (!tg?.initData) throw new Error("Открой TaskFlow из Telegram, чтобы загрузить свои данные.");
  const response = await fetch(path, {
    method,
    headers: { Authorization: `tma ${tg.initData}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store"
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) throw new Error("Сеанс Telegram устарел. Закрой и снова открой Mini App.");
    throw new Error(payload?.detail || "Не удалось выполнить действие. Попробуй обновить экран.");
  }
  return payload;
}

function openModal(title, content) {
  document.getElementById("modal").innerHTML = `<div class="handle"></div><div class="modal-head"><h2>${esc(title)}</h2><button class="close" onclick="closeModal()">×</button></div>${content}`;
  document.getElementById("modalWrap").classList.add("open");
  tg?.BackButton?.show();
}
function closeModal() {
  document.getElementById("modalWrap").classList.remove("open");
  if (state.page === "home") tg?.BackButton?.hide();
}
function showError(error) {
  document.getElementById("view").innerHTML = `<div class="empty"><b>☁️</b>${esc(error.message || error)}<p><button class="secondary" onclick="render()">Повторить</button></p></div>`;
}
function nav() {
  const items = [["home","⌂","Обзор"],["tasks","☷","Задачи"],["new","＋","Создать"],["inbox","♧","Входящие"],["stars","✦","Звёзды"]];
  document.getElementById("nav").innerHTML = items.map(([id, icon, label]) =>
    `<button class="nav ${state.page === id ? "active" : ""} ${id === "new" ? "add" : ""}" onclick="${id === "new" ? "openTaskForm()" : `go('${id}')`}"><b>${icon}</b>${label}</button>`).join("");
}
function go(page) { state.page = page; state.pageIndex = 0; render(); }
function taskRow(task) {
  const [label, color] = statusInfo[task.status] || statusInfo.BACKLOG;
  const epic = task.subtask_summary?.count ? `<div class="task-meta"><span>🧩 ${task.subtask_summary.done_count}/${task.subtask_summary.count} подзадач</span><span>Детали · ✦ ${fmt(task.subtask_summary.agreed_points)}</span></div>` : "";
  return `<article class="task-card" onclick="taskDetail(${task.id})"><div class="check" aria-hidden="true"></div><div class="task-body"><div class="task-title">${task.kind === "SHARED" ? "🤝 " : ""}${esc(task.title)}</div><div class="task-meta"><span class="badge ${color}">${label}</span><span>◷ ${task.deadline ? esc(formatDate(task.deadline)) : "Без срока"}</span>${task.owner ? `<span>· ${esc(task.owner)}</span>` : ""}</div>${epic}</div>${task.points ? `<div class="task-points">✦ ${fmt(task.points)}</div>` : ""}</article>`;
}
function personCard(score, ownId) {
  const percent = Math.max(0, Math.min(100, Math.round(score.net * 100 / score.target)));
  return `<div class="person-card"><div class="person-head"><span class="avatar">${score.user_id === ownId ? "🧑🏻" : "👩🏻"}</span>${score.user_id === ownId ? "ТВОЙ ПРОГРЕСС" : esc(score.name.toUpperCase())}</div><div class="person-points">${fmt(score.net)} <small>/ ${fmt(score.target)}</small></div><div class="mini-bar"><i style="width:${percent}%;background:${score.user_id === ownId ? "#3988f7" : "#27b889"}"></i></div></div>`;
}
async function homePage() {
  const data = await api("/api/miniapp/bootstrap");
  window.tfTimezone = data.user.timezone;
  document.getElementById("profileName").textContent = data.user.name;
  const own = data.week.scores.find(s => s.user_id === data.user.id);
  const percent = Math.max(0, Math.min(100, Math.round(own.net * 100 / own.target)));
  const active = data.active_preview || [];
  document.getElementById("view").innerHTML = `<div class="greeting"><div><div class="eyebrow">${esc(data.week.label)}</div><h1>Привет, ${esc(data.user.name)} 👋</h1></div><div class="week-chip">Эта неделя</div></div><section class="hero"><div class="hero-head"><div><div class="hero-label">ТВОЙ РЕЗУЛЬТАТ ЗА НЕДЕЛЮ</div><div class="hero-total">${fmt(own.net)} <small>/ ${fmt(own.target)} ✦</small></div></div><div class="goal-ring"><span>${percent}%</span></div></div><div class="progress"><i style="width:${percent}%"></i></div><div class="hero-foot"><span>✅ Задачи +${fmt(own.earned)}</span><span>⚠️ Штрафы −${fmt(own.lost)}</span></div></section><div class="duo">${data.week.scores.map(s => personCard(s, data.user.id)).join("")}</div><div class="section-head"><h2>Мой фокус</h2><button class="text-btn" onclick="go('tasks')">Все задачи →</button></div><div class="metrics"><div class="stat"><div class="stat-icon">📌</div><div class="stat-num">${data.active_count}</div><div class="stat-label">Активные</div></div><div class="stat"><div class="stat-icon">📥</div><div class="stat-num">${data.pending_count}</div><div class="stat-label">Согласование</div></div><div class="stat"><div class="stat-icon">✦</div><div class="stat-num">${fmt(own.net)}</div><div class="stat-label">Баллы недели</div></div></div><div class="section-head"><h2>Текущие задачи</h2><button class="text-btn" onclick="go('tasks')">Смотреть все →</button></div>${active.length ? active.map(taskRow).join("") : `<div class="empty"><b>☀️</b>План свободен. Создай задачу или открой входящие.</div>`}<div class="section-head"><h2>Быстрый доступ</h2></div><div class="shortcuts"><button class="shortcut" onclick="go('inbox')"><span class="tile">📥</span><span>Входящие<small>${data.pending_count} ждут ответа</small></span></button><button class="shortcut" onclick="go('templates')"><span class="tile">📚</span><span>Шаблоны<small>Создать быстрее</small></span></button><button class="shortcut" onclick="go('penalties')"><span class="tile">⚠️</span><span>Штрафы<small>Список и типы</small></span></button><button class="shortcut" onclick="go('settings')"><span class="tile">⚙️</span><span>Настройки<small>Цель и часовой пояс</small></span></button></div>`;
}
async function tasksPage() {
  const params = new URLSearchParams({ scope: state.scope, filter: state.filter, page: state.pageIndex });
  const data = await api(`/api/miniapp/tasks?${params}`);
  const summary = data.summary;
  document.getElementById("view").innerHTML = `<h1 class="page-title">Задачи</h1><p class="sub">Общий план для вас двоих</p>${summary ? `<div class="summary-row"><div class="summary-pill"><strong>${fmt(summary.todo_points)}</strong> к выполнению</div><div class="summary-pill"><strong>${fmt(summary.in_progress_points)}</strong> в работе</div><div class="summary-pill"><strong>${fmt(summary.planned_points)}</strong> план ✦</div></div>` : ""}<div class="tabscope">${[["mine","Мои"],["peer","Партнёра"],["shared","Общие"],["assigned","Создал"]].map(([key,label]) => `<button class="${state.scope === key ? "active" : ""}" onclick="setScope('${key}')">${label}</button>`).join("")}</div><div class="filters">${[["all","Все"],["active","Активные"],["work","В работе"],["backlog","Согласование"],["done","Готово"],["archive","Архив"],["overdue","Просроченные"]].map(([key,label]) => `<button class="${state.filter === key ? "active" : ""}" onclick="setFilter('${key}')">${label}</button>`).join("")}</div>${data.items.length ? data.items.map(taskRow).join("") : `<div class="empty"><b>☀️</b>Здесь пока нет задач</div>`}<div class="modal-actions"><button class="secondary" ${data.page <= 0 ? "disabled" : ""} onclick="turnPage(-1)">← Назад</button><span class="summary-pill">${data.page + 1} · ${Math.max(1, Math.ceil(data.count / 5))}</span><button class="secondary" ${((data.page + 1) * 5 >= data.count) ? "disabled" : ""} onclick="turnPage(1)">Далее →</button></div><button class="primary" style="margin-top:14px" onclick="openTaskForm()">＋ Новая задача</button>`;
}
function setScope(value) { state.scope = value; state.pageIndex = 0; render(); }
function setFilter(value) { state.filter = value; state.pageIndex = 0; render(); }
function turnPage(delta) { state.pageIndex = Math.max(0, state.pageIndex + delta); render(); }

async function taskDetail(id) {
  try {
    const t = await api(`/api/miniapp/tasks/${id}`);
    const [label,color] = statusInfo[t.status];
    const actions = [];
    if (t.is_evaluator) actions.push(`<button class="primary" onclick="openRate(${t.id},${t.version},${t.proposed_points || "null"})">⭐ Оценить / принять</button>`);
    if (t.status === "TODO" && t.can_manage) actions.push(`<button class="primary" onclick="closeModal();taskAction(${t.id},${t.version},'IN_PROGRESS')">▶ Начать</button>`);
    if (["TODO","IN_PROGRESS"].includes(t.status) && t.can_manage && !t.blockers.length && (!t.children.length || t.subtask_summary.done_count === t.children.length)) actions.push(`<button class="primary" onclick="closeModal();confirmTaskAction(${t.id},${t.version},'DONE')">✓ Выполнено</button>`);
    if (["TODO","IN_PROGRESS"].includes(t.status) && t.can_manage && (t.blockers.length || (t.children.length && t.subtask_summary.done_count !== t.children.length))) actions.push(`<div class="summary-pill">${t.blockers.length ? "Сначала выполните задачи, от которых зависит эта" : "Завершите все подзадачи, чтобы закрыть эпик"}</div>`);
    if (t.status === "IN_PROGRESS" && t.can_manage) actions.push(`<button class="secondary" onclick="closeModal();taskAction(${t.id},${t.version},'TODO')">📌 К выполнению</button>`);
    if (t.status === "DONE" && t.can_manage && !t.blocked_by_parent) {
      actions.push(`<button class="secondary" onclick="closeModal();confirmTaskAction(${t.id},${t.version},'TODO')">↩ К выполнению</button>`);
      actions.push(`<button class="secondary" onclick="closeModal();confirmTaskAction(${t.id},${t.version},'IN_PROGRESS')">▶ Вернуть в работу</button>`);
    }
    if (t.blocked_by_parent) actions.push(`<div class="summary-pill">Сначала откройте эпик «${esc(t.parent.title)}», чтобы переоткрыть подзадачу</div>`);
    if (t.can_rerate) actions.push(`<button class="secondary" onclick="openRate(${t.id},${t.version},${t.points},true)">Изменить оценку</button>`);
    const editable = t.can_manage && ["BACKLOG","TODO","IN_PROGRESS"].includes(t.status);
    const subtaskSection = t.parent_task_id
      ? `<div class="section-head"><h2>Подзадача</h2></div><div class="panel"><div class="setting-row"><span>Родительская задача</span><button class="text-btn" onclick="taskDetail(${t.parent.id})">${esc(t.parent.title)} →</button></div></div>`
      : `<div class="section-head"><h2>Подзадачи · ${t.subtask_summary.done_count}/${t.subtask_summary.count}</h2></div><div class="panel"><div class="setting-row"><span>Прогресс</span><b>${t.subtask_summary.progress}%</b></div><div class="setting-row"><span>Сумма согласованных оценок</span><b>✦ ${fmt(t.subtask_summary.agreed_points)}</b></div><div class="setting-row"><span>Бонус эпика</span><b>${t.bonus_points ? `✦ ${fmt(t.bonus_points)}` : "Согласуется отдельно"}</b></div></div>${t.children.map(c => {const [cl,cc]=statusInfo[c.status]||statusInfo.BACKLOG;return `<div class="task-card" onclick="taskDetail(${c.id})"><div class="task-body"><div class="task-title">${esc(c.title)}</div><div class="task-meta"><span class="badge ${cc}">${cl}</span><span>${c.points ? `✦ ${fmt(c.points)}` : "Оценка на согласовании"}</span></div></div>${t.can_manage && !["DONE","CANCELLED"].includes(t.status)?`<button class="text-btn" onclick="event.stopPropagation();detachSubtask(${t.id},${t.version},${c.id},${c.version})">Убрать</button>`:""}</div>`}).join("")}${t.can_manage && !["DONE","CANCELLED"].includes(t.status) ? `<button class="primary" style="margin-top:8px" onclick="createSubtask(${t.id},${t.version})">＋ Создать подзадачу</button><button class="secondary" style="margin-top:8px" onclick="attachSubtaskForm(${t.id},${t.version})">↳ Добавить существующую задачу</button>` : ""}`;
    const blockerSection = t.blockers.length ? `<div class="section-head"><h2>Заблокировано</h2></div><div class="panel">${t.blockers.map(b=>`<div class="setting-row"><span>${esc(b.title)}</span><b>${statusInfo[b.status]?.[0]||b.status}</b></div>`).join("")}</div>` : "";
    const linkSection = `<div class="section-head"><h2>Связанные задачи</h2></div><div class="panel">${t.links.map(link=>`<div class="setting-row"><button class="text-btn" onclick="taskDetail(${link.task_id})">${link.kind === "DEPENDS_ON" ? "Зависит от" : "Связана с"}: ${esc(link.title)}</button>${t.can_manage?`<button class="text-btn" onclick="removeTaskLink(${link.id},${link.version},${t.id})">Убрать</button>`:""}</div>`).join("")||`<div class="setting-row"><span>Пока нет связей</span></div>`}</div>${t.can_manage ? `<button class="secondary" style="margin-top:8px" onclick="createTaskLinkForm(${t.id},${t.version})">＋ Связать задачу</button>` : ""}`;
    openModal("Задача", `<div class="badge ${color}">${label}</div><h2 style="font-size:20px;margin:12px 0">${esc(t.title)}</h2><div class="panel"><div class="setting-row"><span>Исполнители</span><b>${esc(t.participants.join(", "))}</b></div><div class="setting-row"><span>Оценка</span><b>${t.points ? `✦ ${fmt(t.points)}${t.kind === "SHARED" ? " каждому" : ""}` : "На оценку"}</b></div><div class="setting-row"><span>Дедлайн</span><b>${t.deadline ? esc(formatDate(t.deadline,{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"})) : "Без срока"}</b></div><div class="setting-row"><span>План</span><b>${t.estimated_minutes || "—"} мин.</b></div><div class="setting-row"><span>Предложил</span><b>${esc(t.proposal_by)}</b></div>${t.awarded_points ? `<div class="setting-row"><span>Начислено</span><b>✦ ${fmt(t.awarded_points)}</b></div>` : ""}</div>${t.comment ? `<p class="sub" style="margin-top:12px">${esc(t.comment)}</p>` : ""}${blockerSection}${subtaskSection}${linkSection}<div style="display:grid;gap:8px;margin-top:14px">${actions.join("")}${editable ? `<button class="secondary" onclick="openTaskForm(${t.id})">✎ Изменить</button>` : ""}${editable ? `<button class="danger" onclick="closeModal();confirmTaskAction(${t.id},${t.version},'CANCELLED')">В архив</button>` : ""}</div>`);
  } catch (error) { toast(error.message); }
}
async function openTaskForm(id = 0, task = null, templateId = 0, parent = null) {
  let template = null;
  if (id && !task) task = await api(`/api/miniapp/tasks/${id}`).catch(error => { toast(error.message); return null; });
  if (id && !task) return;
  if (templateId && typeof templateId === "number") {
    template = await api(`/api/miniapp/templates/${templateId}`).catch(error => { toast(error.message); return null; });
    if (!template) return;
  }
  const editing = Boolean(id);
  const fields = editing
    ? `<input type="hidden" name="version" value="${task.version}"><div class="field"><label>Название · изменение потребует повторного согласования</label><input name="title" maxlength="200" required value="${esc(task.title)}"></div><div class="field"><label>Дедлайн</label><input name="deadline" type="date" value="${task.deadline ? task.deadline.slice(0,10) : ""}"></div><div class="field"><label>План, минут</label><input name="estimated_minutes" type="number" min="1" max="10080" value="${task.estimated_minutes || ""}"></div><div class="field"><label>Комментарий</label><textarea name="comment" maxlength="1500">${esc(task.comment || "")}</textarea></div>`
    : `<input type="hidden" name="operation_key" value="${requestKey()}">${parent ? `<input type="hidden" name="parent_task_id" value="${parent.id}"><input type="hidden" name="parent_version" value="${parent.version}"><div class="setting-row"><span>Подзадача для эпика</span><b>${esc(parent.title || `#${parent.id}`)}</b></div>` : ""}<div class="field"><label>Название задачи</label><input name="title" maxlength="200" required value="${esc(template?.title || "")}" placeholder="Например, подготовить презентацию"></div><div class="field"><label>${parent ? "Исполнитель · как в эпике" : "Для кого"}</label><div class="choice-row">${[["SELF","Себе"],["PEER","Партнёру"],["SHARED","Вместе"]].map(([value,label]) => `<button type="button" class="choice ${value === (parent?.target || "SELF") ? "selected" : ""}" data-target="${value}" ${parent ? "disabled" : ""} onclick="selectTarget(this)">${label}</button>`).join("")}</div><input type="hidden" name="target" value="${parent?.target || "SELF"}"></div><div class="field"><label>Предложить звёзды · 1–10 000</label><input name="proposed_points" type="number" min="1" max="10000" value="${template?.points || ""}" placeholder="Оценит партнёр"></div><div class="field"><label>Дедлайн</label><input name="deadline" type="date"></div><div class="field"><label>План, минут</label><input name="estimated_minutes" type="number" min="1" max="10080" value="${template?.estimated_minutes || ""}"></div><div class="field"><label>Комментарий</label><textarea name="comment" maxlength="1500"></textarea></div>${template ? `<input type="hidden" name="template_id" value="${template.id}"><input type="hidden" name="template_version" value="${template.version}">` : ""}`;
  openModal(editing ? "Изменить задачу" : "Новая задача", `<form onsubmit="saveTask(event,${id})">${fields}<div class="modal-actions"><button type="button" class="secondary" onclick="closeModal()">Отмена</button><button class="primary">${editing ? "Сохранить" : "Создать задачу"}</button></div></form>`);
}
function selectTarget(button) {
  button.parentNode.querySelectorAll(".choice").forEach(item => item.classList.remove("selected"));
  button.classList.add("selected");
  button.parentNode.nextElementSibling.value = button.dataset.target;
}
async function saveTask(event, id) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  const date = data.deadline || null;
  try {
    if (id) {
      const body = { version: Number(data.version), title: data.title, deadline: date,
        estimated_minutes: data.estimated_minutes ? Number(data.estimated_minutes) : null,
        comment: data.comment || null };
      await api(`/api/miniapp/tasks/${id}`, { method: "PATCH", body });
    } else {
      const body = { title: data.title, target: data.target,
        proposed_points: data.proposed_points ? Number(data.proposed_points) : null,
        deadline: date, estimated_minutes: data.estimated_minutes ? Number(data.estimated_minutes) : null,
        comment: data.comment || null, operation_key: data.operation_key,
        template_id: data.template_id ? Number(data.template_id) : null,
        template_version: data.template_version ? Number(data.template_version) : null };
      if (data.parent_task_id) { body.parent_task_id = Number(data.parent_task_id); body.parent_version = Number(data.parent_version); }
      await api("/api/miniapp/tasks", { method: "POST", body });
    }
    closeModal(); toast(id ? "Изменения сохранены" : "Задача создана"); render();
  } catch (error) { toast(error.message); }
}
async function createSubtask(parentId, parentVersion) {
  try { const parent=await api(`/api/miniapp/tasks/${parentId}`); closeModal(); openTaskForm(0,null,0,{id:parent.id,version:parent.version,target:parent.child_target,title:parent.title}); }
  catch(e){toast(e.message)}
}
async function attachSubtaskForm(parentId,parentVersion) {
  try {
    const [parent,data]=await Promise.all([api(`/api/miniapp/tasks/${parentId}`),api(`/api/miniapp/tasks/${parentId}/attachable`)]);
    const items=data.items.filter(t=>!t.parent_task_id && t.status!=="CANCELLED");
    if(!items.length){toast("Нет доступных задач для добавления");return}
    openModal("Добавить подзадачу",`<p class="sub">Оценка, исполнитель и история существующей задачи сохранятся.</p><div class="field"><label>Задача</label><select id="attachTask">${items.map(t=>`<option value="${t.id}" data-version="${t.version}">${esc(t.title)} · ${statusInfo[t.status]?.[0]||t.status}</option>`).join("")}</select></div><div class="modal-actions"><button class="secondary" onclick="closeModal()">Отмена</button><button class="primary" onclick="attachSubtask(${parent.id},${parent.version})">Добавить</button></div>`);
  } catch(e){toast(e.message)}
}
async function attachSubtask(parentId,parentVersion){const select=document.getElementById("attachTask"),childId=Number(select.value),childVersion=Number(select.selectedOptions[0].dataset.version);try{await api(`/api/miniapp/tasks/${parentId}/subtasks/${childId}`,{method:"POST",body:{version:parentVersion,child_version:childVersion,operation_key:requestKey()}});closeModal();toast("Подзадача добавлена");taskDetail(parentId)}catch(e){toast(e.message)}}
function detachSubtask(parentId,parentVersion,childId,childVersion){confirmDialog("Убрать подзадачу?","Задача останется в общем списке. Её оценка и история сохранятся.","Убрать",async()=>{try{await api(`/api/miniapp/tasks/${parentId}/subtasks/${childId}`,{method:"DELETE",body:{version:parentVersion,child_version:childVersion,operation_key:requestKey()}});closeModal();toast("Подзадача убрана");taskDetail(parentId)}catch(e){toast(e.message)}})}
async function createTaskLinkForm(taskId,version){try{const [task,data]=await Promise.all([api(`/api/miniapp/tasks/${taskId}`),api(`/api/miniapp/tasks/${taskId}/attachable`)]);const items=data.items.filter(t=>t.id!==task.id && t.status!=="CANCELLED");if(!items.length){toast("Нет задач для связи");return}openModal("Связать задачу",`<div class="field"><label>Задача</label><select id="linkTarget">${items.map(t=>`<option value="${t.id}" data-version="${t.version}">${esc(t.title)}</option>`).join("")}</select></div><div class="field"><label>Тип связи</label><select id="linkKind"><option value="RELATED">Связана с</option><option value="DEPENDS_ON">Сначала выполнить выбранную задачу</option></select></div><div class="modal-actions"><button class="secondary" onclick="closeModal()">Отмена</button><button class="primary" onclick="saveTaskLink(${task.id},${task.version})">Создать связь</button></div>`)}catch(e){toast(e.message)}}
async function saveTaskLink(taskId,version){const select=document.getElementById("linkTarget"),targetId=Number(select.value);try{await api(`/api/miniapp/tasks/${taskId}/links`,{method:"POST",body:{target_task_id:targetId,target_version:Number(select.selectedOptions[0].dataset.version),version,kind:document.getElementById("linkKind").value,operation_key:requestKey()}});closeModal();toast("Связь добавлена");taskDetail(taskId)}catch(e){toast(e.message)}}
function removeTaskLink(linkId,version,taskId){confirmDialog("Удалить связь?","Связь между задачами будет удалена.","Удалить",async()=>{try{await api(`/api/miniapp/task-links/${linkId}`,{method:"DELETE",body:{version,operation_key:requestKey()}});closeModal();toast("Связь удалена");taskDetail(taskId)}catch(e){toast(e.message)}},true)}
function confirmDialog(title, description, confirmText, callback, danger = false) {
  openModal("Подтверждение", `<div class="confirm-card"><div class="big">${danger ? "⚠️" : "✨"}</div><h3>${esc(title)}</h3><p>${esc(description)}</p></div><div class="modal-actions"><button class="secondary" onclick="closeModal()">Назад</button><button class="${danger ? "danger" : "primary"}" id="confirmAction">${esc(confirmText)}</button></div>`);
  document.getElementById("confirmAction").onclick = async () => { closeModal(); await callback(); };
}
function confirmTaskAction(id, version, action) {
  const t = action === "CANCELLED" ? "Отменить задачу?" : action === "DONE" ? "Завершить задачу?" : "Открыть задачу снова?";
  const d = action === "CANCELLED" ? "Задача останется в архиве и не начислит баллы."
    : action === "DONE" ? "Баллы будут начислены каждому участнику общей задачи."
    : "Начисление будет отменено в исходной неделе.";
  confirmDialog(t,d,"Подтвердить",() => taskAction(id,version,action,true),action === "CANCELLED");
}
async function taskAction(id, version, action, confirmed = false, points = null) {
  try { await api(`/api/miniapp/tasks/${id}/actions`, { method:"POST", body:{version,status:action,confirmed,points} });
    toast("Изменения сохранены"); render(); }
  catch (error) { toast(error.message); }
}
function openRate(id, version, proposed, rerate = false) {
  const amount = proposed || 500;
  openModal(rerate ? "Изменить оценку" : "Оценить задачу", `<div class="field"><label>Баллы · 1–10 000</label><input id="rateValue" type="number" min="1" max="10000" value="${amount}"></div><div class="choice-row">${[100,250,500,750,1000].map(n => `<button class="choice" onclick="document.getElementById('rateValue').value=${n}">${n}</button>`).join("")}</div><div class="modal-actions"><button class="secondary" onclick="closeModal()">Отмена</button><button class="primary" onclick="submitRate(${id},${version},${rerate})">${rerate ? "Сохранить" : "Принять"}</button></div>${rerate ? "<p class='sub'>Изменение оценки нужно подтвердить.</p>" : ""}`);
}
function submitRate(id, version, rerate) {
  const points = Number(document.getElementById("rateValue").value);
  if (!Number.isInteger(points) || points < 1 || points > 10000) return toast("Укажи от 1 до 10 000 баллов");
  if (rerate) return confirmDialog("Подтвердить новую оценку?","Текущая цена будет заменена новой.","Подтвердить",() => taskAction(id,version,"RERATE",true,points));
  closeModal(); taskAction(id,version,"RATE",false,points);
}

async function inboxPage() {
  const data = await api(`/api/miniapp/inbox?page=${state.pageIndex}`);
  document.getElementById("view").innerHTML = `<h1 class="page-title">Входящие</h1><p class="sub">Предложения, которые ждут твоего решения · ${data.count}</p>${data.items.length ? data.items.map(t => `<div class="approval"><div style="display:flex;justify-content:space-between"><span class="badge orange">${t.owner === document.getElementById("profileName").textContent ? "Нужно оценить" : "Предложение"}</span><span class="task-points">${t.proposed_points ? `✦ ${fmt(t.proposed_points)}` : "Оценка нужна"}${t.kind === "SHARED" ? " каждому" : ""}</span></div><h3 style="font-size:15px;margin:12px 0 5px">${esc(t.title)}</h3><div class="task-meta">От ${esc(t.proposal_by)} · ${t.deadline ? esc(new Date(t.deadline).toLocaleDateString("ru-RU")) : "Без срока"} · ${t.estimated_minutes || "—"} мин.</div><div class="approval-actions"><button class="primary" style="padding:10px" onclick="openRate(${t.id},${t.version},${t.proposed_points || "null"})">⭐ Оценить / принять</button><button class="secondary" onclick="rejectTask(${t.id},${t.version})">Отклонить</button></div></div>`).join("") : `<div class="empty"><b>✨</b>Нет предложений для оценки</div>`}<div class="modal-actions"><button class="secondary" ${state.pageIndex<=0?"disabled":""} onclick="state.pageIndex--;render()">← Назад</button><span class="summary-pill">${state.pageIndex+1}</span><button class="secondary" ${(state.pageIndex+1)*5>=data.count?"disabled":""} onclick="state.pageIndex++;render()">Далее →</button></div>`;
}
function rejectTask(id, version) { confirmDialog("Отклонить предложение?","Задача будет отменена и не попадёт в план.","Отклонить",() => taskAction(id,version,"REJECT",true),true); }

async function starsPage() {
  const [data, history] = await Promise.all([api(`/api/miniapp/results?offset=${state.week}`),api(`/api/miniapp/results/history?page=${state.historyPage}`)]);
  const bootstrap = await api("/api/miniapp/bootstrap");
  document.getElementById("view").innerHTML = `<h1 class="page-title">Звёзды</h1><p class="sub">Результаты и история баллов</p><div class="week-switch"><button onclick="state.week=Math.max(-5200,state.week-1);render()">‹</button><strong>${esc(data.label)}</strong><button onclick="state.week=Math.min(0,state.week+1);render()">›</button></div>${data.scores.map(s => { const pct=Math.max(0,Math.min(100,Math.round(s.net*100/s.target))); return `<section class="score-card"><div class="score-top"><div><div class="person-head"><span class="avatar">${s.user_id===bootstrap.user.id?"🧑🏻":"👩🏻"}</span><span class="score-name">${esc(s.name)}</span></div><div class="score-total" style="margin-top:9px">${fmt(s.net)} <span style="font-size:12px;color:#8793a5">/ ${fmt(s.target)} ✦</span></div></div><div class="goal-ring" style="background:conic-gradient(${s.user_id===bootstrap.user.id?"#3988f7":"#27b889"} ${pct}%,#edf2f8 ${pct}%)"><span style="color:#25344b">${pct}%</span></div></div><div class="mini-bar"><i style="width:${pct}%;background:${s.user_id===bootstrap.user.id?"#3988f7":"#27b889"}"></i></div><div class="score-detail"><span>Задачи <b>+${fmt(s.earned)}</b></span><span>Штрафы <b>−${fmt(s.lost)}</b></span><span>Осталось <b>${fmt(Math.max(0,s.target-s.net))}</b></span></div></section>`;}).join("")}<div class="section-head"><h2>История баллов</h2><span class="badge">Страница ${history.page+1}</span></div><div class="panel">${history.items.length?history.items.map(w=>`<div class="setting-row"><span>📅 ${esc(w.label)}<small>${w.scores.map(s=>`${esc(s.name)} ${fmt(s.net)} ✦`).join(" · ")}</small></span></div>`).join(""):`<div class="empty">История появится после первых начислений</div>`}</div><div class="modal-actions"><button class="secondary" ${history.page<=0?"disabled":""} onclick="state.historyPage--;render()">← Новее</button><button class="secondary" ${!history.has_next?"disabled":""} onclick="state.historyPage++;render()">Старше →</button></div><button class="secondary" style="width:100%;margin-top:14px" onclick="go('penalties')">⚠️ Открыть список штрафов</button>`;
}

async function templatePage() {
  const data = await api(`/api/miniapp/templates?include_inactive=true&page=${state.templatePage}`);
  const lastPage = Math.max(0, Math.ceil(data.count / 5) - 1);
  if (state.templatePage > lastPage) { state.templatePage = lastPage; return templatePage(); }
  document.getElementById("view").innerHTML = `<h1 class="page-title">Шаблоны</h1><p class="sub">Общие заготовки с готовой оценкой</p>${data.items.map(t=>`<div class="task-card" onclick="templateActions(${t.id})"><div class="tile" style="width:38px;height:38px;border-radius:13px;background:var(--blue2);display:grid;place-items:center">📚</div><div class="task-body"><div class="task-title">${esc(t.title)}</div><div class="task-meta">${t.is_active?"✅ Включён":"⏸ Выключен"} · ${t.estimated_minutes||"—"} мин.</div></div><div class="task-points">✦ ${fmt(t.points)}</div></div>`).join("")}${data.items.length?"":"<div class='empty'><b>📚</b>Создай первый шаблон</div>"}<div class="modal-actions"><button class="secondary" ${state.templatePage===0?"disabled":""} onclick="state.templatePage--;render()">← Назад</button><span class="summary-pill">${state.templatePage+1} · ${lastPage+1}</span><button class="secondary" ${state.templatePage>=lastPage?"disabled":""} onclick="state.templatePage++;render()">Далее →</button></div><button class="primary" onclick="openTemplateForm()">＋ Создать шаблон</button>`;
}
async function templateActions(id) {
  try { const item=await api(`/api/miniapp/templates/${id}`); const version=item.version;
    openModal("Шаблон", `<h2 style="margin:8px 0">${esc(item.title)}</h2><div class="panel"><div class="setting-row"><span>Оценка</span><b>✦ ${fmt(item.points)}</b></div><div class="setting-row"><span>План</span><b>${item.estimated_minutes||"—"} мин.</b></div><div class="setting-row"><span>Статус</span><b>${item.is_active?"Включён":"Выключен"}</b></div></div><div style="display:grid;gap:8px;margin-top:14px">${item.is_active?`<button class="primary" onclick="closeModal();openTaskForm(0,null,${id})">＋ Создать задачу из шаблона</button>`:""}<button class="secondary" onclick="closeModal();openTemplateForm(${id})">✎ Изменить</button><button class="secondary" onclick="templateToggle(${id},${version},${!item.is_active})">${item.is_active?"Выключить":"Включить"}</button><button class="danger" onclick="templateDelete(${id},${version})">Удалить шаблон</button></div>`);
  } catch(error) { toast(error.message); }
}
async function openTemplateForm(templateId=0) {
  let item=null;
  if(templateId){item=await api(`/api/miniapp/templates/${templateId}`).catch(e=>{toast(e.message);return null});if(!item)return;}
  openModal(item?"Изменить шаблон":"Новый шаблон", `<form onsubmit="saveTemplate(event,${item?.id||0})">${item?`<input type="hidden" name="version" value="${item.version}">`:`<input type="hidden" name="operation_key" value="${requestKey()}">`}<div class="field"><label>Название</label><input name="title" maxlength="200" required value="${esc(item?.title||"")}"></div><div class="field"><label>Баллы · 1–10 000</label><input name="points" type="number" min="1" max="10000" required value="${item?.points||500}"></div><div class="field"><label>Время в минутах</label><input name="estimated_minutes" type="number" min="1" max="10080" value="${item?.estimated_minutes||60}"></div><button class="primary">Сохранить шаблон</button></form>`);
}
async function saveTemplate(event,id){event.preventDefault();const d=Object.fromEntries(new FormData(event.target));try{if(id)await api(`/api/miniapp/templates/${id}`,{method:"PATCH",body:{version:+d.version,title:d.title,points:+d.points,estimated_minutes:d.estimated_minutes?+d.estimated_minutes:null}});else await api("/api/miniapp/templates",{method:"POST",body:{title:d.title,points:+d.points,estimated_minutes:d.estimated_minutes?+d.estimated_minutes:null,operation_key:d.operation_key}});closeModal();toast("Шаблон сохранён");render()}catch(e){toast(e.message)}}
function templateToggle(id,version,isActive){api(`/api/miniapp/templates/${id}`,{method:"PATCH",body:{version,is_active:isActive}}).then(()=>{closeModal();render();toast("Статус обновлён")}).catch(e=>toast(e.message))}
function templateDelete(id,version){confirmDialog("Удалить шаблон?","Созданные по нему задачи сохранят свои условия.","Удалить",async()=>{try{await api(`/api/miniapp/templates/${id}?version=${version}`,{method:"DELETE"});render();toast("Шаблон удалён")}catch(e){toast(e.message)}},true)}

async function penaltyPage() {
  const [data, types] = await Promise.all([api(`/api/miniapp/penalties?page=${state.pageIndex}`),allPenaltyTypes(true)]);
  document.getElementById("view").innerHTML = `<h1 class="page-title">Штрафы</h1><p class="sub">Суммы сохраняются в истории недели</p><button class="primary" onclick="penaltyForm()">＋ Назначить штраф</button><div class="section-head"><h2>Типы штрафов</h2><button class="text-btn" onclick="penaltyTypeForm()">＋ Добавить</button></div><div class="panel">${types.map(t=>`<div class="setting-row"><span>${esc(t.emoji)} ${esc(t.name)}<small>${esc(t.calculation_type)}${t.calculation_type!=="MANUAL"?` · ${fmt(t.rate)} ✦`:""}</small></span><button onclick="penaltyTypeForm(${t.id})">${t.is_active?"Настроить":"Выкл."}</button></div>`).join("")}</div><div class="section-head"><h2>Недавние штрафы</h2></div><div class="panel">${data.items.map(p=>`<div class="setting-row"><span>⚠️ ${esc(p.name)}<small>${esc(p.target)} · ${new Date(p.created_at).toLocaleDateString("ru-RU")}${p.comment?` · ${esc(p.comment)}`:""}</small></span><b style="color:#db6571">−${fmt(p.points)} ✦ <button class="text-btn" onclick="deletePenalty(${p.id},${p.version})">Убрать</button></b></div>`).join("")||`<div class="empty">Штрафов пока нет</div>`}</div>`;
}
async function allPenaltyTypes(includeInactive=false){let page=0,items=[],hasNext=true;while(hasNext){const r=await api(`/api/miniapp/penalty-types?include_inactive=${includeInactive}&page=${page++}`);items.push(...r.items);hasNext=r.has_next}return items}
async function penaltyForm(){try{const [types,data]=await Promise.all([allPenaltyTypes(),api("/api/miniapp/penalties")]);const active=types.filter(t=>t.is_active);openModal("Новый штраф", `<div class="field"><label>Кому назначить</label><select id="penTarget">${data.participants.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></div><div class="field"><label>Тип штрафа</label><select id="penType" onchange="updatePenaltyAmount()">${active.map((p,i)=>`<option value="${i}">${esc(p.emoji)} ${esc(p.name)}${p.calculation_type==="PER_MINUTE"?` · ${fmt(p.rate)} ✦/мин`:""}</option>`).join("")}</select></div><div class="field"><label id="amountLabel">Минуты / баллы</label><input id="penAmount" type="number" min="1" value="5"></div><div class="field"><label>Комментарий</label><textarea id="penComment" maxlength="1500" placeholder="Пояснение"></textarea></div><div class="modal-actions"><button class="secondary" onclick="closeModal()">Отмена</button><button class="primary" onclick="previewPenalty()">Проверить сумму</button></div>`);window.penaltyTypes=active;}catch(e){toast(e.message)}}
function updatePenaltyAmount(){const p=window.penaltyTypes[+document.getElementById("penType").value];document.getElementById("amountLabel").textContent=p.calculation_type==="PER_MINUTE"?"Минуты":"Баллы";document.getElementById("penAmount").value=p.calculation_type==="FIXED"?"":5;document.getElementById("penAmount").disabled=p.calculation_type==="FIXED"}
async function previewPenalty(){const p=window.penaltyTypes[+document.getElementById("penType").value],amount=Number(document.getElementById("penAmount").value);const body={type_id:p.id,quantity:p.calculation_type==="PER_MINUTE"?amount:null,manual:p.calculation_type==="MANUAL"?amount:null};try{const preview=await api("/api/miniapp/penalties/preview",{method:"POST",body});window.pendingPenalty={target:Number(document.getElementById("penTarget").value),preview,comment:document.getElementById("penComment").value,operationKey:requestKey()};openModal("Проверь штраф",`<div class="confirm-card"><div class="big">⚠️</div><h3>${esc(preview.name)}</h3><p>${preview.calculation_type==="PER_MINUTE"?`${preview.quantity} мин. × ${fmt(preview.rate)}`:preview.calculation_type==="FIXED"?"Фиксированная сумма":"Вручную"}${window.pendingPenalty.comment?`<br>${esc(window.pendingPenalty.comment)}`:""}<br><br>Будет вычтено <b>${fmt(preview.points)} ✦</b>.</p></div><div class="modal-actions"><button class="secondary" onclick="penaltyForm()">Назад</button><button class="primary" onclick="createPenalty()">Подтвердить</button></div>`)}catch(e){toast(e.message)}}
async function createPenalty(){const {target,preview,comment,operationKey}=window.pendingPenalty;try{await api("/api/miniapp/penalties",{method:"POST",body:{target_id:target,preview,operation_key:operationKey,comment:comment||null,confirmed:true}});closeModal();toast("Штраф назначен");render()}catch(e){toast(e.message)}}
function deletePenalty(id,version){confirmDialog("Убрать штраф?","Баллы вернутся в исходную неделю. Запись останется в истории.","Подтвердить",async()=>{try{await api(`/api/miniapp/penalties/${id}?version=${version}&confirmed=true`,{method:"DELETE"});render();toast("Штраф компенсирован")}catch(e){toast(e.message)}},true)}
async function penaltyTypeForm(typeId=0){let item=null;if(typeId){const types=await allPenaltyTypes(true);item=types.find(t=>t.id===typeId)||null}openModal(item?"Настроить тип":"Новый тип штрафа",`<form onsubmit="savePenaltyType(event,${item?.id||0})">${item?`<input type="hidden" name="version" value="${item.version}">`:""}<div class="field"><label>Название</label><input name="name" maxlength="80" required value="${esc(item?.name||"")}"></div><div class="field"><label>Emoji</label><input name="emoji" maxlength="16" required value="${esc(item?.emoji||"⚠️")}"></div><div class="field"><label>Описание</label><input name="description" maxlength="500" value="${esc(item?.description||"")}"></div><div class="field"><label>Расчёт</label><select name="calculation_type"><option value="PER_MINUTE" ${item?.calculation_type==="PER_MINUTE"?"selected":""}>За минуту</option><option value="FIXED" ${item?.calculation_type==="FIXED"?"selected":""}>Фиксированная сумма</option><option value="MANUAL" ${item?.calculation_type==="MANUAL"?"selected":""}>Вручную</option></select></div><div class="field"><label>Ставка</label><input name="rate" type="number" min="0" max="1000000" value="${item?.rate??0}"></div>${item?`<label><input name="is_active" type="checkbox" ${item.is_active?"checked":""}> Тип включён</label>`:""}<button class="primary" style="margin-top:16px">Сохранить</button></form>`)}
async function savePenaltyType(event,id){event.preventDefault();const d=Object.fromEntries(new FormData(event.target));const body={name:d.name,emoji:d.emoji,description:d.description||"",calculation_type:d.calculation_type,rate:+d.rate};try{if(id){body.version=+d.version;body.is_active=event.target.is_active.checked;await api(`/api/miniapp/penalty-types/${id}`,{method:"PATCH",body})}else await api("/api/miniapp/penalty-types",{method:"POST",body});closeModal();render();toast("Тип штрафа сохранён")}catch(e){toast(e.message)}}

async function settingsPage(){const data=await api("/api/miniapp/settings");document.getElementById("view").innerHTML=`<h1 class="page-title">Настройки</h1><p class="sub">Личная цель и часовой пояс</p><div class="panel"><div class="setting-row"><span>🎯 Недельная цель<small>Твоя цель на неделю</small></span><button onclick="editGoal(${data.weekly_target})">${fmt(data.weekly_target)} ✦</button></div><div class="setting-row"><span>🌍 Часовой пояс<small>Отображение дедлайнов</small></span><button onclick="editTimezone('${esc(data.timezone)}')">${esc(data.timezone)}</button></div><div class="setting-row"><span>📅 Общая неделя<small>Границы подсчёта для обоих</small></span><b>Пн — Вс</b></div></div><div class="section-head"><h2>Разделы</h2></div><div class="shortcuts"><button class="shortcut" onclick="go('templates')"><span class="tile">📚</span><span>Шаблоны<small>Общие задачи</small></span></button><button class="shortcut" onclick="go('penalties')"><span class="tile">⚠️</span><span>Штрафы<small>Типы и история</small></span></button></div>`}
function editGoal(current){openModal("Недельная цель",`<div class="field"><label>От 1 до 1 000 000 баллов</label><input id="goalValue" type="number" min="1" max="1000000" value="${current}"></div><div class="choice-row">${[5000,10000,15000,20000].map(n=>`<button class="choice" onclick="document.getElementById('goalValue').value=${n}">${fmt(n)}</button>`).join("")}</div><button class="primary" style="margin-top:16px" onclick="saveSettings({weekly_target:+document.getElementById('goalValue').value})">Сохранить</button>`)}
function editTimezone(current){openModal("Часовой пояс",`<div class="field"><label>Для показа дат и дедлайнов</label><select id="zone"><option value="Europe/Moscow" ${current==="Europe/Moscow"?"selected":""}>Москва · Europe/Moscow</option><option value="Europe/Helsinki" ${current==="Europe/Helsinki"?"selected":""}>Хельсинки · Europe/Helsinki</option><option value="UTC" ${current==="UTC"?"selected":""}>UTC</option></select></div><p class="sub">Общая неделя результатов остаётся прежней.</p><button class="primary" onclick="saveSettings({timezone:document.getElementById('zone').value})">Сохранить</button>`)}
async function saveSettings(body){try{await api("/api/miniapp/settings",{method:"PATCH",body});closeModal();toast("Настройки сохранены");render()}catch(e){toast(e.message)}}

function applyTheme(theme, persist = false){document.documentElement.dataset.theme=theme;document.getElementById("themeToggle").textContent=theme==="dark"?"☀":"☾";document.querySelector('meta[name="theme-color"]').content=theme==="dark"?"#111820":"#f4f7fb";if(persist)localStorage.setItem("taskflow-theme",theme)}
function toggleTheme(){const current=document.documentElement.dataset.theme||"light";applyTheme(current==="dark"?"light":"dark",true)}
async function render(){nav();const view=document.getElementById("view");view.innerHTML="<div class='empty'><b>◌</b>Загружаю TaskFlow…</div>";try{if(!tg?.initData){showError("Открой TaskFlow из Telegram, чтобы войти в свой аккаунт.");return}const pages={home:homePage,tasks:tasksPage,inbox:inboxPage,stars:starsPage,templates:templatePage,penalties:penaltyPage,settings:settingsPage};await (pages[state.page]||homePage)()}catch(error){showError(error)}}

const savedTheme=localStorage.getItem("taskflow-theme");
applyTheme(savedTheme||(tg?.colorScheme==="dark"?"dark":"light"));
tg?.ready(); tg?.expand();
tg?.BackButton?.hide();
tg?.onEvent?.("themeChanged",()=>{if(!localStorage.getItem("taskflow-theme"))applyTheme(tg.colorScheme==="dark"?"dark":"light")});
tg?.BackButton?.onClick(()=>{if(document.getElementById("modalWrap").classList.contains("open")){closeModal();return}if(state.page!=="home")go("home");else tg.BackButton.hide()});
render();
