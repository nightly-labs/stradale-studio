'use strict';
const $ = id => document.getElementById(id);
const native = !!window.webkit?.messageHandlers?.native;
const callbacks = new Map();
let requestID = 0, page = 0, queueBusy = false, lastQueue = null;
function notify(text, error = false) { $('notice').hidden = false; $('notice').textContent = text; $('notice').classList.toggle('error', error); }
function bridge(action, extra = {}) {
  return new Promise((resolve, reject) => {
    if (!native) return reject(Error('Open the Mac app to use this control. You can enter folder paths here during development.'));
    const id = String(++requestID);
    callbacks.set(id, { resolve, reject });
    window.webkit.messageHandlers.native.postMessage({ action, id, ...extra });
  });
}
window.nativeResponse = (id, value, error) => {
  const pending = callbacks.get(id); if (!pending) return;
  callbacks.delete(id); error ? pending.reject(Error(error)) : pending.resolve(value);
};
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || 'Local request failed.');
  return body;
}
function view(editor) {
  $('queue-view').hidden = editor; $('editor-view').hidden = !editor;
  $('queue-tab').classList.toggle('active', !editor); $('editor-tab').classList.toggle('active', editor);
  window.scrollTo({ top: 0 });
  if (editor) { $('notice').hidden = true; requestAnimationFrame(fit); }
}
$('queue-tab').onclick = $('back-queue').onclick = () => view(false);
$('editor-tab').onclick = () => view(true);
for (const kind of ['source', 'output']) {
  $(kind + '-path').readOnly = native;
  $('choose-' + kind).onclick = async () => {
    try { const path = await bridge('chooseFolder', { kind }); if (path) $(kind + '-path').value = path; } catch (e) { notify(e.message, true); }
  };
}
$('add-folder').onclick = async () => {
  if (queueBusy) return; queueBusy = true; $('add-folder').disabled = true;
  notify('Reading the folder. The photos are not loaded yet…');
  try {
    const batch = await api('/api/folders', { source: $('source-path').value, destination: $('output-path').value, target: $('batch-target').value, replacement: $('batch-text').value, recursive: $('recursive').checked });
    page = 0; $('filter').value = '';
    notify(`${batch.count.toLocaleString()} ${batch.count === 1 ? "image" : "images"} added. ${lastQueue?.paused === false ? 'The queue is running.' : 'Select Start queue to begin.'}`);
    await refresh();
  } catch (e) { notify(e.message, true); }
  finally { queueBusy = false; $('add-folder').disabled = false; }
};
for (const action of ['start', 'pause', 'retry']) $(action).onclick = async () => {
  try { await api('/api/queue/' + action, {}); await refresh(); if (action === 'pause') notify('Paused. Images already in progress will finish.'); } catch (e) { notify(e.message, true); }
};
$('filter').onchange = () => { page = 0; refresh(); };
$('previous').onclick = () => { page = Math.max(0, page - 1); refresh(); };
$('next').onclick = () => { page++; refresh(); };
function download(url, filename) { const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); }
$('report').onclick = async () => {
  try {
    if (native) { const csv = await (await fetch('/api/report')).text(); const saved = await bridge('saveText', { text: csv, filename: 'stradale-report.csv' }); if (saved) notify('Report saved.'); }
    else download('/api/report', 'stradale-report.csv');
  } catch (e) { notify(e.message, true); }
};
let savingWorkers = false;
for (let count = 1; count <= 50; count++) $('workers').add(new Option(String(count), String(count), false, count === 2));
$('workers').onchange = async () => {
  savingWorkers = true; $('workers').disabled = true;
  try {
    await api('/api/queue/workers', { workers: Number($('workers').value) });
    notify(`Set to ${$('workers').value} images at once. Images already in progress will finish.`);
  } catch (e) { $('workers').value = String(lastQueue?.workers ?? 2); notify(e.message, true); }
  finally { savingWorkers = false; $('workers').disabled = false; await refresh(); }
};
const stateNames = { pending: 'Waiting', running: 'Processing', done: 'Saved', review: 'Need review', failed: 'Failed' };
let refreshing = false, rowSignature = '', batchSignature = '';
async function refresh() {
  if (refreshing) return; refreshing = true;
  try {
    const data = await api(`/api/queue?state=${encodeURIComponent($('filter').value)}&page=${page}`); lastQueue = data;
    const c = data.counts, total = Object.values(c).reduce((a, b) => a + b, 0);
    $('total-count').textContent = total.toLocaleString();
    for (const state of ['done', 'review', 'failed', 'pending']) $(state + '-count').textContent = c[state].toLocaleString();
    const complete = c.done + c.review + c.failed;
    $('progress').max = total || 1; $('progress').value = complete;
    $('queue-state').textContent = data.paused ? (c.running ? 'Pausing…' : 'Paused') : c.pending || c.running ? `Processing · ${c.running} active` : 'Queue finished';
    $('start').disabled = !data.paused || !c.pending; $('pause').disabled = data.paused; $('retry').disabled = !c.failed; $('report').disabled = !total;
    if (!savingWorkers && document.activeElement !== $('workers') && $('workers').value !== String(data.workers)) $('workers').value = String(data.workers);
    $('page-label').textContent = data.total ? `${page * 50 + 1}–${Math.min((page + 1) * 50, data.total)} of ${data.total.toLocaleString()}` : '0 images';
    $('previous').disabled = page === 0; $('next').disabled = (page + 1) * 50 >= data.total;
    const rows = document.createDocumentFragment();
    for (const job of data.rows) {
      const tr = document.createElement('tr'), file = document.createElement('td'); file.textContent = job.relative;
      if (job.error) { const small = document.createElement('small'); small.textContent = job.error; file.append(small); }
      const state = document.createElement('td'), tag = document.createElement('span'); tag.className = 'state ' + job.state; tag.textContent = stateNames[job.state]; state.append(tag);
      const duration = document.createElement('td'); duration.textContent = job.milliseconds == null ? '—' : (job.milliseconds / 1000).toFixed(2) + ' s';
      const action = document.createElement('td');
      if (['done', 'review', 'failed'].includes(job.state)) {
        const button = document.createElement('button'); button.textContent = job.state === 'done' ? 'Inspect' : 'Review'; button.onclick = () => openJob(job.id); action.append(button);
      }
      tr.append(file, state, duration, action); rows.append(tr);
    }
    const signature = JSON.stringify(data.rows);
    if (signature !== rowSignature) { $('jobs').replaceChildren(rows); rowSignature = signature; }
    $('queue-empty').hidden = data.total > 0;
    $('queue-empty').querySelector('h2').textContent = total ? 'No images in this view' : 'Ready for the first folder';
    const batches = document.createDocumentFragment();
    for (const batch of data.batches) {
      const row = document.createElement('div'); row.className = 'batch'; const description = document.createElement('div');
      const title = document.createElement('p'); title.textContent = batch.target + ' → ' + batch.replacement;
      const path = document.createElement('p'); path.className = 'micro'; path.textContent = batch.output; description.append(title, path);
      const open = document.createElement('button'); open.textContent = 'Open output folder'; open.onclick = async () => { try { await bridge('openFolder', { path: batch.output }); } catch (e) { notify(e.message, true); } };
      row.append(description, open); batches.append(row);
    }
    const signatureBatches = JSON.stringify(data.batches);
    if (signatureBatches !== batchSignature) { $('batches').replaceChildren(batches); batchSignature = signatureBatches; }
  } catch (e) { notify(e.message, true); } finally { refreshing = false; }
}
async function poll() { await refresh(); setTimeout(poll, 1200); } poll();

// The editor uses source-pixel coordinates at every zoom level.
const canvas = $('canvas'), ctx = canvas.getContext('2d');
let source = null, result = null, encoded = '', points = [], scale = 1, selecting = false, dragging = -1, editing = false, original = false, jobID = null, jobState = null;
function editorStatus(text) { $('editor-status').textContent = text; }
function editorControls() {
  for (const id of ['find-plate', 'corners']) $(id).disabled = !source || editing;
  $('apply').disabled = !source || points.length !== 4 || editing;
  $('compare').disabled = !result || editing; $('save-image').disabled = !result || editing;
  $('save-job').hidden = !jobID || !['review', 'failed'].includes(jobState); $('save-job').disabled = !result || editing;
  for (const id of ['photo', 'find-text', 'new-text']) $(id).disabled = editing;
}
function invalidate() { result = null; original = false; $('compare').textContent = 'Show original'; editorControls(); }
function draw() {
  if (!source) return;
  canvas.width = Math.round(source.width * scale); canvas.height = Math.round(source.height * scale);
  ctx.drawImage(result && !original ? result : source, 0, 0, canvas.width, canvas.height);
  if (!result || original) {
    ctx.strokeStyle = '#b8ee8f'; ctx.lineWidth = 1.5; ctx.beginPath();
    points.forEach((p, i) => i ? ctx.lineTo(p[0] * scale, p[1] * scale) : ctx.moveTo(p[0] * scale, p[1] * scale));
    if (points.length === 4) ctx.closePath(); ctx.stroke();
    points.forEach((p, i) => { ctx.fillStyle = '#c0e3a1'; ctx.beginPath(); ctx.arc(p[0] * scale, p[1] * scale, 6, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#15200e'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(i + 1, p[0] * scale, p[1] * scale + 3); });
  }
}
function fit() { if (source && !$('editor-view').hidden) { scale = Math.min(1, ($('stage').clientWidth - 2) / source.width, 1600 / source.width); draw(); } }
async function image(url) { const img = new Image(); img.src = url; await img.decode(); return img; }
async function load(blob, filename) {
  const url = URL.createObjectURL(blob); let img;
  try { img = await image(url); } finally { URL.revokeObjectURL(url); }
  if (img.width * img.height > 24000000) throw Error('Use an image with no more than 24 million pixels.');
  const temp = document.createElement('canvas'); temp.width = img.width; temp.height = img.height; temp.getContext('2d').drawImage(img, 0, 0);
  encoded = temp.toDataURL('image/png').split(',')[1]; source = img; points = []; selecting = false; invalidate();
  $('image-empty').hidden = true; canvas.hidden = false; $('editor-name').textContent = `${filename} · ${img.width} × ${img.height}`;
  fit(); editorStatus('Find the plate, or select four corners.');
}
$('photo').onchange = async () => {
  if (!$('photo').files[0] || editing) return; editing = true; editorControls();
  try { await load($('photo').files[0], $('photo').files[0].name); jobID = null; jobState = null; $('editor-context').textContent = 'SINGLE IMAGE'; }
  catch (e) { editorStatus(e.message); } finally { editing = false; editorControls(); }
};
async function openJob(id) {
  if (editing) return; editing = true; source = result = null; encoded = ''; points = []; jobID = jobState = null; canvas.hidden = true; $('image-empty').hidden = false; $('image-empty').textContent = 'Loading image…'; editorControls(); view(true);
  try {
    const job = await api('/api/jobs/' + id); const response = await fetch(`/api/jobs/${id}/image`);
    if (!response.ok) throw Error((await response.json()).error);
    await load(await response.blob(), job.relative); jobID = id; jobState = job.state;
    $('find-text').value = job.target; $('new-text').value = job.replacement; $('editor-context').textContent = 'QUEUE IMAGE / ' + stateNames[job.state].toUpperCase();
    if (job.corners) points = JSON.parse(job.corners);
    if (job.state === 'done') { const output = await fetch(`/api/jobs/${id}/image?result=1`); if (!output.ok) throw Error('The saved output is not available.'); const url = URL.createObjectURL(await output.blob()); try { result = await image(url); } finally { URL.revokeObjectURL(url); } editorStatus('Saved result. Use Show original to compare.'); }
    else editorStatus(job.error || 'Select the corners to repair this image.');
    draw();
  } catch (e) { editorStatus(e.message); $('image-empty').textContent = 'This image could not be opened.'; } finally { editing = false; editorControls(); }
}
$('find-plate').onclick = async () => {
  editing = true; editorControls(); editorStatus('Finding the plate on your Mac…');
  try { const data = await api('/api/detect', { image: encoded, target: $('find-text').value }); invalidate(); points = data.match?.corners || []; selecting = false; draw(); editorStatus(data.match ? `Found in ${data.milliseconds} ms. Check the corners.` : 'No clear match. Select four corners manually.'); }
  catch (e) { editorStatus(e.message); } finally { editing = false; editorControls(); }
};
$('apply').onclick = async () => {
  editing = true; editorControls(); editorStatus('Preparing the replacement…');
  try { const data = await api('/api/render', { image: encoded, corners: points, text: $('new-text').value }); result = await image('data:image/png;base64,' + data.image); original = false; $('compare').textContent = 'Show original'; draw(); editorStatus(`Ready in ${data.milliseconds} ms. Inspect the result, then save it.`); }
  catch (e) { editorStatus(e.message); } finally { editing = false; editorControls(); }
};
$('save-job').onclick = async () => {
  editing = true; editorControls();
  try { await api(`/api/jobs/${jobID}/manual`, { corners: points, text: $('new-text').value }); jobState = 'done'; $('editor-context').textContent = 'QUEUE IMAGE / SAVED'; editorStatus('Saved to the batch output folder.'); refresh(); }
  catch (e) { editorStatus(e.message); } finally { editing = false; editorControls(); }
};
$('save-image').onclick = async () => {
  try {
    const out = document.createElement('canvas'); out.width = result.width; out.height = result.height; out.getContext('2d').drawImage(result, 0, 0); const dataURL = out.toDataURL('image/png');
    if (native) { const saved = await bridge('saveImage', { image: dataURL.split(',')[1], filename: 'stradale.png' }); if (saved) editorStatus('PNG saved.'); }
    else download(dataURL, 'stradale.png');
  } catch (e) { editorStatus(e.message); }
};
$('new-text').oninput = () => { invalidate(); draw(); };
$('corners').onclick = () => { invalidate(); points = []; selecting = true; draw(); editorControls(); editorStatus('Select top left, top right, bottom right, then bottom left.'); };
$('compare').onclick = () => { original = !original; $('compare').textContent = original ? 'Show result' : 'Show original'; draw(); };
$('fit').onclick = fit; $('zoom').onclick = () => { if (source) { scale = Math.min(8, 8000 / Math.max(source.width, source.height), scale * 1.5); draw(); } };
function point(e) { const b = canvas.getBoundingClientRect(); return [Math.max(0, Math.min(source.width - 1, (e.clientX - b.left) / scale)), Math.max(0, Math.min(source.height - 1, (e.clientY - b.top) / scale))]; }
canvas.onpointerdown = e => {
  if (editing || !source) return; const p = point(e);
  if (selecting && points.length < 4) { points.push(p); if (points.length === 4) { selecting = false; editorStatus('Corners set. Preview the replacement.'); } draw(); editorControls(); return; }
  dragging = points.findIndex(c => Math.hypot(c[0] - p[0], c[1] - p[1]) * scale < 18);
  if (dragging >= 0) { invalidate(); canvas.setPointerCapture(e.pointerId); draw(); }
};
canvas.onpointermove = e => { if (dragging >= 0) { points[dragging] = point(e); draw(); } };
canvas.onpointerup = canvas.onpointercancel = () => dragging = -1;
window.addEventListener('resize', fit);
