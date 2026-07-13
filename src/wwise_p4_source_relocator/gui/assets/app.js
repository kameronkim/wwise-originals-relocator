const state = {
  bridgeReady: false,
  doctorPassed: false,
  busy: false,
};

const checkLabels = {
  'project-root': '프로젝트 폴더',
  'wwise-project': 'Wwise 프로젝트',
  'originals-wav': 'Originals WAV',
  'wwu-sources': 'Work Unit source',
  'p4-cli': 'Perforce CLI',
  'p4-workspace': 'Perforce workspace',
  'waapi-client': 'WAAPI client',
  'waapi-server': 'Wwise 연결',
};

const element = (id) => document.getElementById(id);

function settingsFromForm() {
  return {
    projectRoot: element('project-root').value.trim(),
    objectRoot: element('object-root').value.trim(),
    chapter: element('chapter').value.trim(),
    waapiUrl: element('waapi-url').value.trim(),
    p4Executable: element('p4-executable').value.trim(),
  };
}

function applySettings(settings) {
  element('project-root').value = settings.projectRoot || '';
  element('object-root').value = settings.objectRoot || '\\Containers\\Default Work Unit\\VO';
  element('chapter').value = settings.chapter || 'CH04';
  element('waapi-url').value = settings.waapiUrl || 'ws://127.0.0.1:8080/waapi';
  element('p4-executable').value = settings.p4Executable || '';
  updateProjectState();
}

function updateProjectState() {
  const selected = Boolean(element('project-root').value.trim());
  const badge = element('project-state');
  badge.textContent = selected ? '선택됨' : '선택 필요';
  badge.className = `panel-state ${selected ? 'ready' : 'neutral'}`;
}

function renderSystem(system) {
  element('platform-value').textContent = system.platform || '—';
  element('p4-status').textContent = system.p4Detected ? '감지됨' : '찾지 못함';
  element('p4-detail').textContent = system.p4Executable || '직접 선택할 수 있습니다';
  element('wwise-status').textContent = system.wwiseDetected ? '감지됨' : '연결 확인 필요';
  element('wwise-detail').textContent = system.wwiseConsole || 'Wwise 실행 후 환경 확인';
  element('data-detail').textContent = system.dataRoot || '앱 폴더의 data';
}

function renderReadiness(result) {
  const list = element('readiness-list');
  list.replaceChildren();
  for (const check of result.checks || []) {
    const item = document.createElement('li');
    item.className = check.status;
    const symbol = document.createElement('span');
    symbol.className = 'check-symbol';
    symbol.textContent = check.status === 'pass' ? '✓' : '!';
    const copy = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = checkLabels[check.name] || check.name;
    const message = document.createElement('p');
    message.textContent = check.message;
    copy.append(title, message);
    item.append(symbol, copy);
    list.append(item);
  }
  element('readiness-empty').hidden = true;
  list.hidden = false;
  state.doctorPassed = Boolean(result.ready);
  element('run-plan').disabled = !state.doctorPassed || !state.bridgeReady;
  setStep('doctor', state.doctorPassed ? 'done' : 'active');
  if (result.reports?.markdown) {
    const report = element('doctor-report');
    report.textContent = `환경 보고서: ${result.reports.markdown}`;
    report.hidden = false;
  }
}

function renderPlan(result) {
  const counts = result.counts || {};
  element('move-count').textContent = counts['move-and-patch'] || 0;
  element('skip-count').textContent = counts.skip || 0;
  element('review-count').textContent = counts['manual-review'] || 0;
  element('validation-state').textContent = result.validation?.valid ? '통과' : '확인 필요';
  element('plan-summary').hidden = false;
  element('plan-empty').hidden = true;

  const body = element('plan-table-body');
  body.replaceChildren();
  for (const item of result.items || []) {
    const row = document.createElement('tr');
    row.append(
      tableCell(item.sourceFileName || item.objectPath, 'file-name'),
      tableCell(item.from || '—', 'path-text'),
      tableCell(item.to || '—', 'path-text'),
      actionCell(item.action),
    );
    body.append(row);
  }
  element('plan-table-wrap').hidden = false;
  setStep('plan', 'done');
  if (result.reports?.planMarkdown) {
    const report = element('plan-report');
    report.textContent = `계획 보고서: ${result.reports.planMarkdown}`;
    report.hidden = false;
  }
}

function tableCell(value, className) {
  const cell = document.createElement('td');
  const span = document.createElement('span');
  span.className = className;
  span.textContent = value;
  cell.append(span);
  return cell;
}

function actionCell(action) {
  const labels = {
    'move-and-patch': '이동 가능',
    skip: '이미 정리됨',
    'manual-review': '담당자 확인',
  };
  const cell = document.createElement('td');
  const badge = document.createElement('span');
  badge.className = `action-badge ${action}`;
  badge.textContent = labels[action] || action;
  cell.append(badge);
  return cell;
}

function setStep(step, status) {
  const item = document.querySelector(`[data-step="${step}"]`);
  if (!item) return;
  item.classList.remove('active', 'done');
  item.classList.add(status);
}

function setBusy(busy, message = '준비됨') {
  state.busy = busy;
  element('activity-dot').className = `activity-dot ${busy ? 'busy' : ''}`;
  element('activity-text').textContent = message;
  element('run-doctor').disabled = busy || !state.bridgeReady;
  element('run-plan').disabled = busy || !state.bridgeReady || !state.doctorPassed;
  element('choose-project').disabled = busy || !state.bridgeReady;
  element('choose-p4').disabled = busy || !state.bridgeReady;
}

function showError(message) {
  const banner = element('error-banner');
  banner.textContent = message;
  banner.hidden = false;
  element('activity-dot').className = 'activity-dot error';
  element('activity-text').textContent = '확인이 필요합니다';
}

function clearError() {
  element('error-banner').hidden = true;
}

async function invoke(method, ...args) {
  if (!window.pywebview?.api?.[method]) {
    throw new Error('Portable 앱 연결을 사용할 수 없습니다.');
  }
  const result = await window.pywebview.api[method](...args);
  if (!result?.ok) throw new Error(result?.error || '작업을 완료하지 못했습니다.');
  return result;
}

async function initialize() {
  if (state.bridgeReady) return;
  state.bridgeReady = true;
  element('bridge-status').textContent = 'Portable 앱 연결됨';
  element('bridge-status').className = 'connection-badge ready';
  clearError();
  try {
    const initial = await invoke('get_initial_state');
    applySettings(initial.settings || {});
    renderSystem(initial.system || {});
    setBusy(false);
  } catch (error) {
    showError(error.message);
  }
}

async function chooseProject() {
  clearError();
  try {
    const result = await invoke('choose_project');
    if (!result.cancelled) {
      element('project-root').value = result.projectRoot;
      updateProjectState();
      state.doctorPassed = false;
      element('run-plan').disabled = true;
      setStep('project', 'done');
      setStep('doctor', 'active');
    }
  } catch (error) {
    showError(error.message);
  }
}

async function chooseP4() {
  clearError();
  try {
    const result = await invoke('choose_p4');
    if (!result.cancelled) {
      element('p4-executable').value = result.p4Executable;
      element('p4-status').textContent = '직접 선택됨';
      element('p4-detail').textContent = result.p4Executable;
    }
  } catch (error) {
    showError(error.message);
  }
}

async function runDoctor() {
  clearError();
  if (!element('project-root').value.trim()) {
    showError('먼저 Wwise 프로젝트 폴더를 선택해 주세요.');
    return;
  }
  setBusy(true, 'Wwise와 Perforce 환경을 확인하고 있습니다…');
  try {
    const result = await invoke('run_doctor', settingsFromForm());
    renderReadiness(result);
    setBusy(false, result.ready ? '환경 확인을 통과했습니다' : '해결할 항목이 있습니다');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function runPlan() {
  clearError();
  setBusy(true, 'Wwise source와 이동 계획을 확인하고 있습니다…');
  try {
    const result = await invoke('run_plan', settingsFromForm());
    renderPlan(result);
    setBusy(false, '읽기 전용 이동 계획이 준비되었습니다');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

function loadPreview() {
  state.bridgeReady = false;
  element('bridge-status').textContent = '브라우저 미리보기';
  element('bridge-status').className = 'connection-badge preview';
  element('preview-banner').hidden = false;
  applySettings({
    projectRoot: 'C:\\Work\\Audio\\WwiseProject',
    objectRoot: '\\Containers\\Default Work Unit\\VO',
    chapter: 'CH04',
    waapiUrl: 'ws://127.0.0.1:8080/waapi',
    p4Executable: 'C:\\Program Files\\Perforce\\p4.exe',
  });
  renderSystem({
    platform: 'Windows',
    p4Detected: true,
    p4Executable: 'C:\\Program Files\\Perforce\\p4.exe',
    wwiseDetected: true,
    wwiseConsole: 'WwiseConsole.exe',
    dataRoot: 'WwiseRelocator\\data',
  });
  renderReadiness({
    ready: true,
    checks: Object.keys(checkLabels).map((name) => ({name, status: 'pass', message: `${checkLabels[name]} 준비가 완료되었습니다.`})),
    reports: {markdown: 'data/reports/readiness.md'},
  });
  renderPlan({
    counts: {'move-and-patch': 1, skip: 1, 'manual-review': 1},
    validation: {valid: true},
    items: [
      {sourceFileName: 'CH04_S102_WT_001.wav', from: 'Scenario/CH04/CH04_S102_WT_001.wav', to: 'Script/CH04/CH04_S102_WT_001.wav', action: 'move-and-patch'},
      {sourceFileName: 'CH04_CUT_010.wav', from: 'Cutscene/CH04/CH04_CUT_010.wav', to: 'Cutscene/CH04/CH04_CUT_010.wav', action: 'skip'},
      {sourceFileName: 'Shared_Line.wav', from: 'Scenario/CH04/Shared_Line.wav', to: null, action: 'manual-review'},
    ],
    reports: {planMarkdown: 'data/reports/plan.md'},
  });
  setBusy(false, '브라우저 미리보기');
}

element('choose-project').addEventListener('click', chooseProject);
element('choose-p4').addEventListener('click', chooseP4);
element('run-doctor').addEventListener('click', runDoctor);
element('run-plan').addEventListener('click', runPlan);
element('project-root').addEventListener('input', updateProjectState);
window.addEventListener('pywebviewready', initialize, {once: true});

if (new URLSearchParams(window.location.search).get('preview') === '1') {
  loadPreview();
} else {
  window.setTimeout(() => {
    if (!state.bridgeReady) {
      element('bridge-status').textContent = '앱 연결 없음';
      element('bridge-status').className = 'connection-badge preview';
      setBusy(false, 'Portable 앱에서 열어 주세요');
    }
  }, 1200);
}
