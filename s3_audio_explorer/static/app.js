const $ = selector => document.querySelector(selector);
const selected = new Set();
let visibleFiles = [], browseSequence = 0;
const states = {queued: 'En attente', downloading: 'Téléchargement', cancelling: 'Annulation…', cancelled: 'Annulé', completed: 'Terminé', failed: 'Échec'};
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function bytes(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} Mo` : `${(n / 1024).toFixed(1)} Ko`;
}
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
  return data;
}
function updateSelection() {
  $('#download').disabled = selected.size === 0;
  $('#download').textContent = `Télécharger la sélection${selected.size ? ` (${selected.size})` : ''}`;
  const count = visibleFiles.filter(file => selected.has(file.key)).length;
  $('#select-all').checked = visibleFiles.length > 0 && count === visibleFiles.length;
  $('#select-all').indeterminate = count > 0 && count < visibleFiles.length;
  $('#select-all').disabled = visibleFiles.length === 0;
}
function breadcrumbs(prefix) {
  const nav = $('#breadcrumbs'); nav.replaceChildren();
  const root = element('button', 'Racine'); root.onclick = () => browse(''); nav.append(root);
  let path = '';
  for (const part of prefix.split('/').filter(Boolean)) {
    path += `${part}/`; const target = path;
    nav.append(element('span', '/'));
    const button = element('button', part); button.onclick = () => browse(target); nav.append(button);
  }
}
async function browse(prefix = '') {
  const sequence = ++browseSequence;
  $('#browse-error').textContent = '';
  try {
    const data = await api(`/api/browse?prefix=${encodeURIComponent(prefix)}`);
    if (sequence !== browseSequence) return;
    visibleFiles = data.files; breadcrumbs(prefix);
    const listing = $('#files'); listing.replaceChildren();
    for (const folder of data.folders) {
      const button = element('button', `📁  ${folder.slice(prefix.length)}`, 'folder');
      button.onclick = () => browse(folder); listing.append(button);
    }
    for (const file of data.files) {
      const row = element('label', undefined, 'file');
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
      checkbox.checked = selected.has(file.key);
      checkbox.onchange = () => { checkbox.checked ? selected.add(file.key) : selected.delete(file.key); updateSelection(); };
      row.append(checkbox, element('span', file.key.slice(prefix.length)), element('small', bytes(file.size)));
      listing.append(row);
    }
    if (!data.folders.length && !data.files.length) listing.append(element('p', 'Aucun fichier dans ce préfixe.', 'empty'));
    updateSelection();
  } catch (error) { if (sequence === browseSequence) $('#browse-error').textContent = error.message; }
}
$('#select-all').onchange = event => {
  for (const file of visibleFiles) event.target.checked ? selected.add(file.key) : selected.delete(file.key);
  // Les cases affichées peuvent être actualisées sans relire S3.
  document.querySelectorAll('#files .file input').forEach((input, index) => { input.checked = selected.has(visibleFiles[index].key); });
  updateSelection();
};
$('#download').onclick = async () => {
  const keys = [...selected]; $('#download').disabled = true;
  try {
    await api('/api/transfers', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({keys})});
    keys.forEach(key => selected.delete(key));
    document.querySelectorAll('#files .file input').forEach((input, index) => { input.checked = selected.has(visibleFiles[index].key); });
    $('#browse-error').textContent = ''; refreshTransfers();
  } catch (error) { $('#browse-error').textContent = error.message; }
  finally { updateSelection(); }
};
async function refreshTransfers() {
  try {
    const data = await api('/api/transfers'); $('#transfer-error').textContent = '';
    const container = $('#transfers');
    if (!data.transfers.length) return;
    const keep = new Set(data.transfers.map(job => `job-${job.id}`));
    for (const child of [...container.children]) if (!keep.has(child.id)) child.remove();
    for (const job of [...data.transfers].reverse()) {
      let card = document.getElementById(`job-${job.id}`);
      if (!card) {
        card = element('article', undefined, 'transfer'); card.id = `job-${job.id}`;
        const name = element('strong', job.key);
        const progress = document.createElement('progress'); progress.max = 100; progress.setAttribute('aria-label', job.key);
        const status = element('p'); const cancel = element('button', 'Annuler', 'cancel');
        cancel.onclick = async () => {
          cancel.disabled = true;
          try { await api(`/api/transfers/${job.id}/cancel`, {method: 'POST'}); await refreshTransfers(); }
          catch (error) { $('#transfer-error').textContent = error.message; cancel.disabled = false; }
        };
        card.append(name, progress, status, cancel); container.append(card);
      }
      card.querySelector('progress').value = job.status === 'completed' ? 100 : (job.total ? job.downloaded / job.total * 100 : 0);
      card.querySelector('p').textContent = `${states[job.status]} · ${bytes(job.downloaded)} / ${bytes(job.total)}${job.error ? ` · ${job.error}` : ''}`;
      card.querySelector('button').hidden = !['queued', 'downloading', 'cancelling'].includes(job.status);
      card.querySelector('button').disabled = job.status === 'cancelling';
      card.dataset.status = job.status;
    }
  } catch (error) { $('#transfer-error').textContent = error.message; }
}
async function poll() { await refreshTransfers(); setTimeout(poll, 700); }
browse(); poll();
