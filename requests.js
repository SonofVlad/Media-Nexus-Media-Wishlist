const REQUESTS_API = "api/requests";
const LOOKUP_API = "api/lookup";
const BATCH_REQUESTER_API = "api/requests/batch-requester";
const BATCH_DELETE_API = "api/requests/batch-delete";

let records = [];
let currentLookup = null;
let lookupTimer = null;
let currentSelectionScope = null;

const sortState = {
  active: {key: "submittedAt", direction: "desc"},
  completed: {key: "submittedAt", direction: "desc"}
};

const activeList = document.querySelector("#active-list");
const completedList = document.querySelector("#completed-list");
const activeEmpty = document.querySelector("#active-empty");
const completedEmpty = document.querySelector("#completed-empty");
const dialog = document.querySelector("#request-dialog");
const form = document.querySelector("#request-form");
const message = document.querySelector("#message");
const imdbInput = document.querySelector("#imdb-url");
const lookupButton = document.querySelector("#lookup-button");
const lookupStatus = document.querySelector("#lookup-status");
const metadataPreview = document.querySelector("#metadata-preview");
const previewType = document.querySelector("#preview-type");
const previewName = document.querySelector("#preview-name");
const previewYear = document.querySelector("#preview-year");
const submitButton = document.querySelector("#submit-request");

const editDialog = document.querySelector("#edit-requester-dialog");
const editForm = document.querySelector("#edit-requester-form");
const editSummary = document.querySelector("#edit-requester-summary");
const newRequesterInput = document.querySelector("#new-requester");

const deleteDialog = document.querySelector("#delete-dialog");
const deleteForm = document.querySelector("#delete-form");
const deleteConfirmations = document.querySelector("#delete-confirmations");

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function row(item, scope) {
  return `<tr>
    <td class="select-column utility-column">
      <input
        type="checkbox"
        class="row-select"
        data-id="${esc(item.id)}"
        data-scope="${scope}"
        aria-label="Select ${esc(item.contentName)}"
      >
    </td>
    <td class="requester-column">${esc(item.requester)}</td>
    <td class="type-column">${esc(item.mediaType)}</td>
    <td class="name-column">${esc(item.contentName)}</td>
    <td class="year-column">${Number(item.year)}</td>
    <td class="imdb-column"><a href="${esc(item.imdbUrl)}" target="_blank" rel="noopener">IMDb</a></td>
    <td class="acquired-column"><input type="checkbox" data-id="${esc(item.id)}" data-field="acquired"
      data-name="${esc(item.contentName)}" ${item.acquired ? "checked" : ""}></td>
    <td class="processed-column"><input type="checkbox" data-id="${esc(item.id)}" data-field="processed"
      data-name="${esc(item.contentName)}" ${item.processed ? "checked" : ""}></td>
  </tr>`;
}

function compareValues(left, right, key) {
  const a = left?.[key];
  const b = right?.[key];

  if (typeof a === "boolean" || typeof b === "boolean") {
    return Number(Boolean(a)) - Number(Boolean(b));
  }

  if (key === "year") {
    return Number(a || 0) - Number(b || 0);
  }

  return String(a ?? "").localeCompare(
    String(b ?? ""),
    undefined,
    {numeric: true, sensitivity: "base"}
  );
}

function sortedRecords(items, scope) {
  const {key, direction} = sortState[scope];
  const multiplier = direction === "asc" ? 1 : -1;

  return [...items].sort((left, right) => {
    const primary = compareValues(left, right, key) * multiplier;
    if (primary !== 0) return primary;

    return String(left.contentName || "").localeCompare(
      String(right.contentName || ""),
      undefined,
      {numeric: true, sensitivity: "base"}
    );
  });
}

function updateSortIndicators() {
  document.querySelectorAll(".sort-button").forEach(button => {
    const state = sortState[button.dataset.scope];
    const active = state.key === button.dataset.sort;

    button.classList.toggle("active", active);
    button.dataset.direction = active ? state.direction : "";
    button.setAttribute(
      "aria-sort",
      active
        ? (state.direction === "asc" ? "ascending" : "descending")
        : "none"
    );
  });
}

function render() {
  const active = sortedRecords(
    records.filter(item => !item.processed),
    "active"
  );
  const completed = sortedRecords(
    records.filter(item => item.processed),
    "completed"
  );

  activeList.innerHTML = active.map(item => row(item, "active")).join("");
  completedList.innerHTML = completed.map(item => row(item, "completed")).join("");

  activeEmpty.hidden = active.length > 0;
  completedEmpty.hidden = completed.length > 0;

  document.querySelectorAll(".select-all").forEach(box => {
    box.checked = false;
    box.indeterminate = false;
  });

  updateActionButtons("active");
  updateActionButtons("completed");
  updateSortIndicators();
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})}
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const details = Array.isArray(data.details) ? ` ${data.details.join(" ")}` : "";
    throw new Error((data.error || "Request failed.") + details);
  }

  return data;
}

function show(text, isError = false) {
  message.textContent = text;
  message.hidden = false;
  message.className = isError ? "error" : "success";
}

function resetLookup() {
  currentLookup = null;
  metadataPreview.hidden = true;
  lookupStatus.hidden = true;
  submitButton.disabled = true;
}

function selectedIds(scope) {
  return [...document.querySelectorAll(`.row-select[data-scope="${scope}"]:checked`)]
    .map(box => box.dataset.id);
}

function selectedRecords(scope) {
  const ids = new Set(selectedIds(scope));
  return records.filter(item => ids.has(String(item.id)));
}

function updateActionButtons(scope) {
  const count = selectedIds(scope).length;

  document.querySelector(`.edit-requester[data-scope="${scope}"]`).disabled = count === 0;
  document.querySelector(`.delete-selected[data-scope="${scope}"]`).disabled = count === 0;

  const allBox = document.querySelector(`.select-all[data-scope="${scope}"]`);
  const rowBoxes = [...document.querySelectorAll(`.row-select[data-scope="${scope}"]`)];

  allBox.checked = rowBoxes.length > 0 && count === rowBoxes.length;
  allBox.indeterminate = count > 0 && count < rowBoxes.length;
}

async function lookupTitle() {
  const imdbUrl = imdbInput.value.trim();
  resetLookup();

  if (!imdbUrl || !imdbInput.checkValidity()) {
    imdbInput.reportValidity();
    return;
  }

  lookupButton.disabled = true;
  lookupButton.textContent = "Looking…";
  lookupStatus.hidden = false;
  lookupStatus.className = "lookup-status";
  lookupStatus.textContent = "Looking up title…";

  try {
    currentLookup = await api(LOOKUP_API, {
      method: "POST",
      body: JSON.stringify({imdbUrl})
    });

    previewType.textContent = currentLookup.mediaType;
    previewName.textContent = currentLookup.contentName;
    previewYear.textContent = currentLookup.year;
    metadataPreview.hidden = false;

    lookupStatus.className = "lookup-status success";
    lookupStatus.textContent = "Title found. Confirm the details below.";
    submitButton.disabled = false;
  } catch (error) {
    lookupStatus.className = "lookup-status error";
    lookupStatus.textContent = error.message;
  } finally {
    lookupButton.disabled = false;
    lookupButton.textContent = "Look Up";
  }
}

async function load() {
  try {
    records = await api(REQUESTS_API);
    render();
  } catch (error) {
    show(error.message, true);
  }
}

imdbInput.addEventListener("input", () => {
  resetLookup();
  clearTimeout(lookupTimer);

  if (imdbInput.checkValidity() && imdbInput.value.trim()) {
    lookupTimer = setTimeout(lookupTitle, 500);
  }
});

imdbInput.addEventListener("paste", () => {
  clearTimeout(lookupTimer);
  lookupTimer = setTimeout(lookupTitle, 150);
});

lookupButton.addEventListener("click", lookupTitle);

form.addEventListener("submit", async event => {
  event.preventDefault();

  if (!form.reportValidity()) return;

  if (!currentLookup) {
    await lookupTitle();
    if (!currentLookup) return;
  }

  const values = new FormData(form);

  submitButton.disabled = true;
  submitButton.textContent = "Submitting…";

  try {
    const created = await api(REQUESTS_API, {
      method: "POST",
      body: JSON.stringify({
        requester: values.get("requester"),
        imdbUrl: values.get("imdbUrl")
      })
    });

    records.push(created);
    render();
    form.reset();
    resetLookup();
    dialog.close();
    show(`“${created.contentName}” was added.`);
  } catch (error) {
    show(error.message, true);
    submitButton.disabled = false;
  } finally {
    submitButton.textContent = "Submit Request";
  }
});

async function statusChange(event) {
  const box = event.target.closest("input[data-field]");
  if (!box) return;

  const field = box.dataset.field;
  const checked = box.checked;
  const oldValue = !checked;
  const name = box.dataset.name;

  const text = field === "processed"
    ? checked
      ? `Are you sure you want to mark “${name}” as processed? It will move to Completed.`
      : `Are you sure you want to return “${name}” to Active Requests?`
    : checked
      ? `Are you sure you want to mark “${name}” as acquired?`
      : `Are you sure you want to mark “${name}” as not acquired?`;

  if (!confirm(text)) {
    box.checked = oldValue;
    return;
  }

  try {
    const updated = await api(
      `${REQUESTS_API}/${encodeURIComponent(box.dataset.id)}`,
      {
        method: "PATCH",
        body: JSON.stringify({[field]: checked})
      }
    );

    const index = records.findIndex(item => item.id === updated.id);
    records[index] = updated;
    render();
  } catch (error) {
    box.checked = oldValue;
    show(error.message, true);
  }
}

function selectionChange(event) {
  const rowBox = event.target.closest(".row-select");
  if (!rowBox) return;
  updateActionButtons(rowBox.dataset.scope);
}

function selectAllChange(event) {
  const allBox = event.target.closest(".select-all");
  if (!allBox) return;

  const scope = allBox.dataset.scope;
  document.querySelectorAll(`.row-select[data-scope="${scope}"]`)
    .forEach(box => { box.checked = allBox.checked; });

  updateActionButtons(scope);
}

function openEditRequester(scope) {
  const selected = selectedRecords(scope);
  if (!selected.length) return;

  currentSelectionScope = scope;
  editSummary.textContent = selected.length === 1
    ? `Change the requester for “${selected[0].contentName}”.`
    : `Change the requester for ${selected.length} selected requests.`;

  const uniqueNames = [...new Set(selected.map(item => item.requester))];
  newRequesterInput.value = uniqueNames.length === 1 ? uniqueNames[0] : "";
  editDialog.showModal();
  newRequesterInput.focus();
  newRequesterInput.select();
}

editForm.addEventListener("submit", async event => {
  event.preventDefault();

  const ids = selectedIds(currentSelectionScope);
  const requester = newRequesterInput.value.trim();

  if (!requester || !ids.length) return;

  try {
    const result = await api(BATCH_REQUESTER_API, {
      method: "POST",
      body: JSON.stringify({
        requestIds: ids,
        requester
      })
    });

    const updatedById = new Map(result.updated.map(item => [item.id, item]));
    records = records.map(item => updatedById.get(item.id) || item);

    editDialog.close();
    render();
    show(`${result.updated.length} request${result.updated.length === 1 ? "" : "s"} updated.`);
  } catch (error) {
    show(error.message, true);
  }
});

function openDelete(scope) {
  const selected = selectedRecords(scope);
  if (!selected.length) return;

  currentSelectionScope = scope;
  deleteConfirmations.innerHTML = selected.map(item => `
    <label class="delete-confirmation">
      <span>
        <strong>${esc(item.contentName)}</strong>
        <small>Requester: ${esc(item.requester)}</small>
      </span>
      <input
        type="text"
        data-delete-id="${esc(item.id)}"
        placeholder="Type ${esc(item.requester)}"
        autocomplete="off"
        required
      >
    </label>
  `).join("");

  deleteDialog.showModal();
  deleteConfirmations.querySelector("input")?.focus();
}

deleteForm.addEventListener("submit", async event => {
  event.preventDefault();

  const inputs = [...deleteConfirmations.querySelectorAll("[data-delete-id]")];
  if (!inputs.length || !deleteForm.reportValidity()) return;

  const confirmations = inputs.map(input => ({
    id: input.dataset.deleteId,
    requesterConfirmation: input.value
  }));

  if (!confirm(`Permanently delete ${confirmations.length} selected request${confirmations.length === 1 ? "" : "s"}?`)) {
    return;
  }

  try {
    const result = await api(BATCH_DELETE_API, {
      method: "POST",
      body: JSON.stringify({confirmations})
    });

    const deletedIds = new Set(result.deletedIds);
    records = records.filter(item => !deletedIds.has(String(item.id)));

    deleteDialog.close();
    render();
    show(`${result.deletedCount} request${result.deletedCount === 1 ? "" : "s"} deleted.`);
  } catch (error) {
    show(error.message, true);
  }
});

function handleSort(button) {
  const scope = button.dataset.scope;
  const key = button.dataset.sort;
  const current = sortState[scope];

  if (current.key === key) {
    current.direction = current.direction === "asc" ? "desc" : "asc";
  } else {
    current.key = key;
    current.direction = key === "year" || key === "acquired" || key === "processed"
      ? "desc"
      : "asc";
  }

  render();
}

document.querySelectorAll(".sort-button").forEach(button => {
  button.addEventListener("click", () => handleSort(button));
});

activeList.addEventListener("change", event => {
  statusChange(event);
  selectionChange(event);
});
completedList.addEventListener("change", event => {
  statusChange(event);
  selectionChange(event);
});

document.querySelectorAll(".select-all").forEach(box => {
  box.addEventListener("change", selectAllChange);
});

document.querySelectorAll(".edit-requester").forEach(button => {
  button.addEventListener("click", () => openEditRequester(button.dataset.scope));
});

document.querySelectorAll(".delete-selected").forEach(button => {
  button.addEventListener("click", () => openDelete(button.dataset.scope));
});

document.querySelector("#add-request").addEventListener("click", () => {
  form.reset();
  resetLookup();
  dialog.showModal();
});

document.querySelector("#cancel").addEventListener("click", () => dialog.close());
document.querySelector("#cancel-edit-requester").addEventListener("click", () => editDialog.close());
document.querySelector("#cancel-delete").addEventListener("click", () => deleteDialog.close());

load();
