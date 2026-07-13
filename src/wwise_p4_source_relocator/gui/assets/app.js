const state = {
  bridgeReady: false,
  doctorPassed: false,
  busy: false,
  system: {},
  plan: null,
  selectedItem: null,
  activeOperation: null,
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

const readinessMessages = {
  'project-root': {
    pass: '선택한 프로젝트 폴더를 확인했습니다.',
    fail: '선택한 프로젝트 폴더를 찾을 수 없습니다.',
  },
  'wwise-project': {
    pass: 'Wwise 프로젝트 파일을 확인했습니다.',
    fail: '폴더에는 .wproj 파일이 정확히 하나 있어야 합니다.',
  },
  'originals-wav': {
    pass: 'Originals 폴더의 WAV source를 확인했습니다.',
    fail: 'Originals 폴더에서 WAV source를 찾지 못했습니다.',
  },
  'wwu-sources': {
    pass: 'Work Unit의 source reference를 읽을 수 있습니다.',
    fail: '읽을 수 있는 Work Unit source reference가 없습니다.',
  },
  'p4-cli': {
    pass: '기존 Perforce CLI를 사용할 수 있습니다.',
    fail: 'p4.exe를 찾지 못했습니다. 고급 설정에서 직접 선택하세요.',
  },
  'p4-workspace': {
    pass: '프로젝트가 현재 Perforce workspace에 포함되어 있습니다.',
    fail: '현재 Perforce workspace에 프로젝트가 매핑되어 있지 않습니다.',
  },
  'waapi-client': {
    pass: 'Portable 앱의 Wwise 연결 모듈이 준비되었습니다.',
    fail: 'Portable 앱에 Wwise 연결 모듈이 포함되어 있지 않습니다.',
  },
  'waapi-server': {
    pass: '실행 중인 Wwise의 WAAPI에 연결할 수 있습니다.',
    fail: 'Wwise에서 WAAPI를 켜고 같은 포트를 사용하는 다른 프로그램이 없는지 확인하세요.',
  },
};

const validationMessages = {
  'manual-review': '자동 판단할 수 없는 source가 있습니다.',
  'p4-unavailable': 'Perforce CLI를 사용할 수 없습니다.',
  'project-root-missing': '프로젝트 폴더를 찾을 수 없습니다.',
  'originals-missing': '프로젝트에 Originals 폴더가 없습니다.',
  'incomplete-move': '현재 위치 또는 이동할 위치 정보가 부족합니다.',
  'outside-project': '프로젝트 폴더 밖의 경로가 포함되어 있습니다.',
  'same-path': '현재 위치와 이동할 위치가 같습니다.',
  'source-missing': '현재 WAV source 파일을 찾을 수 없습니다.',
  'target-exists': '이동할 위치에 같은 파일이 이미 있습니다.',
  'work-unit-missing': '관련 Work Unit 파일을 찾을 수 없습니다.',
  'outside-workspace': 'Perforce workspace 밖의 경로가 있습니다.',
  'already-opened': '다른 작업을 위해 이미 열린 파일이 있습니다.',
};

const element = (id) => document.getElementById(id);

function settingsFromForm() {
  return {
    projectRoot: element('project-root').value.trim(),
    objectRoot: element('object-root').value.trim(),
    chapter: element('chapter').value.trim(),
    waapiUrl: element('waapi-url').value.trim(),
    p4Executable: element('p4-executable').value.trim(),
    changelist: element('changelist').value.trim(),
    offlineTestMode: element('offline-test-mode').checked,
  };
}

function applySettings(settings) {
  element('project-root').value = settings.projectRoot || '';
  element('object-root').value = settings.objectRoot || '\\Containers\\Default Work Unit\\VO';
  element('chapter').value = settings.chapter || 'CH04';
  element('waapi-url').value = settings.waapiUrl || 'ws://127.0.0.1:8080/waapi';
  element('p4-executable').value = settings.p4Executable || '';
  element('changelist').value = settings.changelist || '';
  element('offline-test-mode').checked = settings.offlineTestMode === true;
  updateProjectState();
  updateOfflineModePresentation();
}

function updateProjectState() {
  const selected = Boolean(element('project-root').value.trim());
  const badge = element('project-state');
  badge.textContent = selected ? '선택됨' : '선택 필요';
  badge.className = `panel-state ${selected ? 'ready' : 'neutral'}`;
}

function renderSystem(system) {
  state.system = {...system};
  element('app-version').textContent = system.appVersion ? `v${system.appVersion}` : 'v—';
  element('platform-value').textContent = system.platform || '—';
  element('wwise-status').textContent = system.wwiseDetected ? '감지됨' : '연결 확인 필요';
  element('wwise-detail').textContent = system.wwiseConsole || 'Wwise 실행 후 환경 확인';
  element('data-detail').textContent = system.dataRoot || '앱 폴더의 data';
  updateOfflineModePresentation();
}

function updateOfflineModePresentation() {
  const enabled = element('offline-test-mode').checked;
  const mutationLocked = Boolean(state.activeOperation);
  element('p4-executable').disabled = enabled || mutationLocked;
  element('changelist').disabled = enabled || mutationLocked;
  element('offline-test-mode').disabled = mutationLocked;
  element('project-root').disabled = mutationLocked;
  element('choose-p4').disabled = enabled || mutationLocked || state.busy || !state.bridgeReady;
  element('choose-project').disabled = mutationLocked || state.busy || !state.bridgeReady;
  element('p4-status').textContent = enabled
    ? '테스트에서 제외'
    : (state.system.p4Detected ? '감지됨' : '찾지 못함');
  element('p4-detail').textContent = enabled
    ? '로컬 읽기 전용 점검만 실행합니다'
    : (state.system.p4Executable || '직접 선택할 수 있습니다');
  element('doctor-step-detail').textContent = enabled
    ? 'Wwise · 로컬 파일 · WAAPI'
    : 'Wwise · P4 · WAAPI';
  element('doctor-description').textContent = enabled
    ? 'Wwise 프로젝트, Originals WAV, Work Unit과 WAAPI 연결을 확인합니다. Perforce 점검은 제외됩니다.'
    : 'Wwise 프로젝트, Originals WAV, Work Unit, Perforce workspace와 WAAPI 연결을 확인합니다.';
  updateApplyButtons();
}

function renderReadiness(result) {
  const connection = result.waapiConnection;
  if (connection?.url) {
    element('waapi-url').value = connection.url;
    element('wwise-status').textContent = '자동 연결됨';
    element('wwise-detail').textContent = `${connection.transport.toUpperCase()} · ${connection.url}`;
  } else if (result.waapiIssue === 'modal-dialog') {
    element('wwise-status').textContent = 'Wwise 창 확인 필요';
    element('wwise-detail').textContent = '열린 설정창을 닫은 뒤 다시 확인하세요';
  } else if (result.waapiIssue === 'project-mismatch') {
    element('wwise-status').textContent = '프로젝트 불일치';
    element('wwise-detail').textContent = '선택한 프로젝트를 Wwise에서 열어 주세요';
  }
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
    const skippedPerforce = result.offlineTestMode
      && ['p4-cli', 'p4-workspace'].includes(check.name);
    const waapiMessage = check.name === 'waapi-server'
      ? waapiReadinessMessage(result, check)
      : null;
    message.textContent = skippedPerforce
      ? '로컬 테스트 모드에서는 이 Perforce 점검을 건너뜁니다.'
      : (waapiMessage || readinessMessages[check.name]?.[check.status] || check.message);
    copy.append(title, message);
    item.append(symbol, copy);
    list.append(item);
  }
  element('readiness-empty').hidden = true;
  list.hidden = false;
  state.doctorPassed = Boolean(result.ready);
  element('run-plan').disabled = !state.doctorPassed
    || !state.bridgeReady
    || Boolean(state.activeOperation);
  setStep('doctor', state.doctorPassed ? 'done' : 'active');
  if (result.reports?.markdown) {
    const report = element('doctor-report');
    report.textContent = `환경 보고서: ${result.reports.markdown}`;
    report.hidden = false;
  }
}

function waapiReadinessMessage(result, check) {
  if (check.status === 'pass' && result.waapiConnection) {
    const transport = result.waapiConnection.transport.toUpperCase();
    return `${transport} 연결을 자동으로 확인했습니다.`;
  }
  if (result.waapiIssue === 'modal-dialog') {
    return 'Wwise에 열린 설정창이 있습니다. 창을 닫고 다시 확인하세요.';
  }
  if (result.waapiIssue === 'project-mismatch') {
    return 'Wwise에 열린 프로젝트가 선택한 프로젝트 폴더와 다릅니다.';
  }
  return null;
}

function renderPlan(result) {
  state.plan = result;
  state.selectedItem = null;
  const counts = result.counts || {};
  element('move-count').textContent = counts['move-and-patch'] || 0;
  element('skip-count').textContent = counts.skip || 0;
  element('review-count').textContent = counts['manual-review'] || 0;
  element('validation-state').textContent = result.validation?.valid
    ? (result.offlineTestMode ? '로컬 통과' : '통과')
    : '확인 필요';
  element('offline-result-note').hidden = !result.offlineTestMode;
  element('plan-summary').hidden = false;
  element('plan-empty').hidden = true;

  const body = element('plan-table-body');
  body.replaceChildren();
  for (const item of result.items || []) {
    const row = document.createElement('tr');
    row.append(
      selectionCell(item, result),
      tableCell(item.sourceFileName || item.objectPath, 'file-name'),
      tableCell(item.from || '—', 'path-text'),
      tableCell(item.to || '—', 'path-text'),
      actionCell(item.action),
      tableCell(item.reason || '—', 'reason-text'),
    );
    body.append(row);
  }
  element('plan-table-wrap').hidden = false;
  renderValidationIssues(result.validation?.issues || []);
  renderApplySelection();
  setStep('plan', 'done');
  if (result.reports?.planMarkdown) {
    const report = element('plan-report');
    report.textContent = `계획 보고서: ${result.reports.planMarkdown}`;
    report.hidden = false;
  }
}

function selectionCell(item, result) {
  const cell = document.createElement('td');
  const selectable = item.action === 'move-and-patch'
    && result.validation?.valid
    && !result.offlineTestMode
    && !state.activeOperation;
  const radio = document.createElement('input');
  radio.type = 'radio';
  radio.name = 'apply-source';
  radio.className = 'row-selector';
  radio.disabled = !selectable;
  radio.setAttribute('aria-label', `${item.sourceFileName || item.objectPath} 선택`);
  radio.addEventListener('change', () => {
    state.selectedItem = item;
    renderApplySelection();
  });
  cell.append(radio);
  return cell;
}

function renderApplySelection() {
  if (state.activeOperation) {
    renderActiveOperation(state.activeOperation);
    return;
  }
  const selected = state.selectedItem;
  element('active-operation').hidden = true;
  element('apply-controls').hidden = !selected;
  element('apply-empty').hidden = Boolean(selected);
  element('apply-state').textContent = selected ? '1개 선택됨' : '계획 필요';
  element('apply-state').className = `panel-state ${selected ? 'ready' : 'neutral'}`;
  if (selected) {
    element('selected-file').textContent = selected.sourceFileName;
    element('selected-move').textContent = `${selected.from} → ${selected.to}`;
    setStep('apply', 'active');
  }
  updateApplyButtons();
}

function renderActiveOperation(operation) {
  state.activeOperation = operation || null;
  const active = Boolean(operation);
  element('active-operation').hidden = !active;
  element('apply-controls').hidden = true;
  element('apply-empty').hidden = active;
  element('apply-state').textContent = active ? 'Rollback 대기' : '계획 필요';
  element('apply-state').className = `panel-state ${active ? 'warning' : 'neutral'}`;
  if (active) {
    element('active-file').textContent = operation.sourceFileName;
    element('active-move').textContent = `${operation.from} → ${operation.to}`;
    element('apply-report').textContent = `Rollback manifest: ${operation.manifest}`;
    element('apply-report').hidden = false;
    setStep('apply', 'active');
  } else {
    element('apply-report').hidden = true;
  }
  updateOfflineModePresentation();
}

function renderValidationIssues(issues) {
  const panel = element('validation-issues');
  const list = element('validation-issue-list');
  list.replaceChildren();
  for (const issue of issues) {
    const item = document.createElement('li');
    const localized = validationMessages[issue.code] || issue.message;
    item.textContent = issue.objectPath ? `${localized} (${issue.objectPath})` : localized;
    list.append(item);
  }
  panel.hidden = issues.length === 0;
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
  element('run-plan').disabled = busy
    || !state.bridgeReady
    || !state.doctorPassed
    || Boolean(state.activeOperation);
  element('choose-project').disabled = busy || !state.bridgeReady || Boolean(state.activeOperation);
  element('choose-p4').disabled = busy || !state.bridgeReady || element('offline-test-mode').checked || Boolean(state.activeOperation);
  updateApplyButtons();
}

function updateApplyButtons() {
  const offline = element('offline-test-mode')?.checked === true;
  const applyButton = element('run-apply');
  const rollbackButton = element('run-rollback');
  if (applyButton) {
    applyButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || !state.selectedItem
      || Boolean(state.activeOperation);
  }
  if (rollbackButton) {
    rollbackButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || !state.activeOperation;
  }
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
    if (initial.activeOperation) renderActiveOperation(initial.activeOperation);
    if ((initial.activeOperationCount || 0) > 1) {
      showError('복구가 필요한 manifest가 여러 개 있습니다. 로그와 reports 폴더를 운영 담당자에게 전달하세요.');
    }
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
      resetResults();
      setStep('project', 'done');
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
  const offline = element('offline-test-mode').checked;
  setBusy(true, offline
    ? 'Wwise와 로컬 프로젝트를 확인하고 있습니다…'
    : 'Wwise와 Perforce 환경을 확인하고 있습니다…');
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

async function runApply() {
  const item = state.selectedItem;
  if (!item) return;
  const accepted = window.confirm(
    `${item.sourceFileName}\n\n이 WAV 한 개를 Perforce move하고 Work Unit 경로를 변경합니다.\n이 프로그램은 submit하지 않습니다. 계속할까요?`,
  );
  if (!accepted) return;
  clearError();
  setBusy(true, `${item.sourceFileName}을 적용하고 있습니다…`);
  try {
    const result = await invoke(
      'run_apply',
      settingsFromForm(),
      item.sourceFileName,
      item.sourceFileName,
    );
    if (!result.applied) {
      if (result.activeOperation) renderActiveOperation(result.activeOperation);
      setBusy(false);
      showError(result.errorMessage || '파일 적용을 완료하지 못했습니다.');
      return;
    }
    renderActiveOperation(result.activeOperation);
    setBusy(false, '한 파일을 적용했습니다. Wwise에서 외부 변경을 다시 불러오세요');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function runRollback() {
  const operation = state.activeOperation;
  if (!operation) return;
  const accepted = window.confirm(
    `${operation.sourceFileName}\n\nmanifest에 기록된 WAV와 Work Unit만 원래 상태로 복구합니다. 계속할까요?`,
  );
  if (!accepted) return;
  clearError();
  setBusy(true, `${operation.sourceFileName}을 복구하고 있습니다…`);
  try {
    const result = await invoke(
      'run_rollback',
      settingsFromForm(),
      operation.sourceFileName,
    );
    if (!result.rolledBack) {
      renderActiveOperation(result.activeOperation || operation);
      setBusy(false);
      showError('Rollback을 완료하지 못했습니다. 보고서와 로그를 확인하세요.');
      return;
    }
    renderActiveOperation(null);
    resetResults();
    setBusy(false, 'Rollback을 완료했습니다. Wwise에서 외부 변경을 다시 불러오세요');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

function changeOfflineMode() {
  resetResults();
  updateOfflineModePresentation();
  clearError();
  element('activity-text').textContent = element('offline-test-mode').checked
    ? 'Perforce 없는 로컬 테스트 모드'
    : '일반 환경 점검 모드';
}

function resetResults() {
  state.doctorPassed = false;
  element('run-plan').disabled = true;
  element('readiness-list').replaceChildren();
  element('readiness-list').hidden = true;
  element('readiness-empty').hidden = false;
  element('doctor-report').hidden = true;
  element('plan-summary').hidden = true;
  element('plan-empty').hidden = false;
  element('plan-table-body').replaceChildren();
  element('plan-table-wrap').hidden = true;
  element('validation-issues').hidden = true;
  element('plan-report').hidden = true;
  element('offline-result-note').hidden = true;
  state.plan = null;
  state.selectedItem = null;
  if (!state.activeOperation) {
    element('apply-controls').hidden = true;
    element('apply-empty').hidden = false;
    element('apply-state').textContent = '계획 필요';
    element('apply-state').className = 'panel-state neutral';
  }
  setStep('doctor', 'active');
  const planStep = document.querySelector('[data-step="plan"]');
  planStep?.classList.remove('active', 'done');
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
    changelist: '123456',
    offlineTestMode: false,
  });
  renderSystem({
    platform: 'Windows',
    appVersion: '0.1.0',
    p4Detected: true,
    p4Executable: 'C:\\Program Files\\Perforce\\p4.exe',
    wwiseDetected: true,
    wwiseConsole: 'WwiseConsole.exe',
    dataRoot: 'WwiseRelocator\\data',
  });
  renderReadiness({
    ready: true,
    waapiConnection: {transport: 'http', url: 'http://127.0.0.1:8090/waapi'},
    checks: Object.keys(checkLabels).map((name) => ({name, status: 'pass', message: `${checkLabels[name]} 준비가 완료되었습니다.`})),
    reports: {markdown: 'data/reports/readiness.md'},
  });
  renderPlan({
    counts: {'move-and-patch': 1, skip: 1, 'manual-review': 0},
    validation: {
      valid: true,
      issues: [],
    },
    items: [
      {sourceFileName: 'CH04_S102_WT_001.wav', from: 'Scenario/CH04/CH04_S102_WT_001.wav', to: 'Script/CH04/CH04_S102_WT_001.wav', action: 'move-and-patch'},
      {sourceFileName: 'CH04_CUT_010.wav', from: 'Cutscene/CH04/CH04_CUT_010.wav', to: 'Cutscene/CH04/CH04_CUT_010.wav', action: 'skip'},
    ],
    reports: {planMarkdown: 'data/reports/plan.md'},
  });
  setBusy(false, '브라우저 미리보기');
}

element('choose-project').addEventListener('click', chooseProject);
element('choose-p4').addEventListener('click', chooseP4);
element('run-doctor').addEventListener('click', runDoctor);
element('run-plan').addEventListener('click', runPlan);
element('run-apply').addEventListener('click', runApply);
element('run-rollback').addEventListener('click', runRollback);
element('project-root').addEventListener('input', updateProjectState);
element('offline-test-mode').addEventListener('change', changeOfflineMode);
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
