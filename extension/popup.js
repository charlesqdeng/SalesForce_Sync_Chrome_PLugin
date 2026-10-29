const API = "http://127.0.0.1:8765";
const reportInput = document.querySelector("#report-url");
const sheetInput = document.querySelector("#sheet-url");
const status = document.querySelector("#status");
const details = document.querySelector("#details");
const buttons = [...document.querySelectorAll("button")];

chrome.storage.local.get(["reportUrl", "sheetUrl"], (values) => {
  reportInput.value = values.reportUrl || "";
  sheetInput.value = values.sheetUrl || "";
});

function show(message, error = false, log = "") {
  status.textContent = message;
  status.classList.toggle("error", error);
  details.textContent = log;
  details.hidden = !log;
}

const fields = {
  reportUrl: {
    input: reportInput,
    name: "Salesforce report URL",
    pattern: /00O[A-Za-z0-9]{12,15}/,
    invalid: "The Salesforce report URL must contain a report ID.",
  },
  sheetUrl: {
    input: sheetInput,
    name: "Google Sheet URL",
    pattern: /docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_-]+/,
    invalid: "Enter a Google Sheets URL.",
  },
};

function fieldValue(key) {
  const field = fields[key];
  const value = field.input.value.trim();
  if (!field.pattern.test(value)) {
    throw new Error(field.invalid);
  }
  return value;
}

function config() {
  return { reportUrl: fieldValue("reportUrl"), sheetUrl: fieldValue("sheetUrl") };
}

async function request(path, data) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch {
    throw new Error("Cannot reach the local helper. Start the app with ./start.sh and keep it running.");
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || `Local helper returned HTTP ${response.status}.`);
  }
  return result;
}

function withBusy(action) {
  return async () => {
    buttons.forEach((button) => { button.disabled = true; });
    show("Working…");
    try {
      await action();
    } catch (error) {
      show(error.message, true);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  };
}

document.querySelectorAll(".url-field").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const key = form.dataset.key;
    try {
      const value = fieldValue(key);
      chrome.storage.local.set({ [key]: value }, () => show(`${fields[key].name} saved.`));
    } catch (error) {
      show(error.message, true);
    }
  });
});

document.querySelector("#export").addEventListener("click", withBusy(async () => {
  const accepted = window.confirm(
    "Exporting replaces the contents of the active worksheet. Continue?"
  );
  if (!accepted) {
    show("Export cancelled.");
    return;
  }
  const result = await request("/api/export", config());
  show(result.message, false, result.log);
}));

document.querySelector("#reexport").addEventListener("click", withBusy(async () => {
  const values = config();
  const accepted = window.confirm(
    "Clear the whole worksheet and export the report again?\n\n" +
    "Any sheet edits that haven't been synced to Salesforce will be lost."
  );
  if (!accepted) {
    show("Re-export cancelled.");
    return;
  }
  const result = await request("/api/export", { ...values, discardEdits: true });
  show(result.message, false, result.log);
}));

document.querySelector("#sync").addEventListener("click", withBusy(async () => {
  const values = config();
  const preview = await request("/api/sync", { ...values, dryRun: true });
  const updates = preview.counts?.push || 0;
  if (!updates) {
    show(preview.message, preview.counts?.conflict > 0 || preview.counts?.error > 0, preview.log);
    return;
  }
  const accepted = window.confirm(
    `${preview.message}\n\nApply ${updates} update(s) to Salesforce?`
  );
  if (!accepted) {
    show("Sync cancelled. No Salesforce changes were made.", false, preview.log);
    return;
  }
  const result = await request("/api/sync", { ...values, dryRun: false });
  show(result.message, result.counts?.conflict > 0 || result.counts?.error > 0, result.log);
}));
