const state = {
  bridgeReady: false,
  doctorPassed: false,
  busy: false,
  system: {},
  plan: null,
  selectedItems: [],
  activeOperation: null,
  operationHistory: null,
};

const operationStatusLabels = {
  prepared: '적용 준비 중',
  'awaiting-wwise-reload': 'Wwise Reload 대기',
  applied: 'P4V 인계 가능',
  'handed-off': 'P4V 마감 대기',
  completed: '완료',
  'rolled-back': 'Rollback 완료',
  failed: '복구 필요',
};

const checkLabels = {
  'project-root': '프로젝트 폴더',
  'wwise-project': 'Wwise 프로젝트',
  'originals-wav': 'Originals WAV',
  'wwu-sources': 'Work Unit source',
  'p4-cli': 'Perforce CLI',
  'p4-connection': 'P4V 연결',
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
  'p4-connection': {
    pass: 'P4V와 같은 Perforce 서버, 사용자와 workspace를 사용할 수 있습니다.',
    fail: 'P4V에서 올바른 연결로 로그인한 뒤 연결 불러오기를 실행하세요.',
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
  'source-still-exists': '이동 전 위치에 WAV가 남아 있습니다.',
  'target-missing': '이동할 위치에서 WAV를 찾을 수 없습니다.',
  'unexpected-wwu-diff': 'Work Unit에 계획하지 않은 변경이 포함되어 있습니다.',
  'work-unit-invalid': 'Work Unit XML을 읽을 수 없습니다.',
  'old-source-present': 'Work Unit에 이동 전 source 경로가 남아 있습니다.',
  'new-source-mismatch': 'Work Unit의 새 source 경로가 정확히 하나가 아닙니다.',
  'p4-opened-failed': 'Perforce opened 상태를 읽지 못했습니다.',
  'p4-move-missing': 'Perforce에 WAV move/add와 move/delete가 모두 보이지 않습니다.',
  'p4-edit-missing': 'Perforce에 Work Unit edit가 보이지 않습니다.',
  'p4-diff-failed': 'Perforce Work Unit diff를 읽지 못했습니다.',
  'unsafe-p4-diff': 'Work Unit diff가 source 경로 변경만으로 제한되지 않았습니다.',
  'work-unit-local-changes': 'Work Unit에 이 작업 이전의 로컬 변경이 있습니다. P4V에서 변경을 먼저 정리하세요.',
  'rollback-exception': 'Rollback 실행이 예기치 않게 중단되었습니다. 보고서와 로그를 확인하세요.',
  'wwise-response-invalid': 'Wwise가 올바른 응답을 반환하지 않았습니다.',
  'wwise-object-missing': 'Wwise에서 적용 대상 객체 하나를 찾지 못했습니다.',
  'wwise-object-invalid': 'Wwise가 올바르지 않은 객체 정보를 반환했습니다.',
  'wwise-guid-changed': 'Wwise 객체 GUID가 적용 manifest와 다릅니다.',
  'wwise-path-changed': 'Wwise 객체 경로가 적용 manifest와 다릅니다.',
  'wwise-source-mismatch': 'Wwise가 이동된 source 경로를 아직 불러오지 않았습니다.',
  'wwise-source-missing': 'Wwise가 가리키는 원본 WAV 파일을 찾을 수 없습니다.',
};

const element = (id) => document.getElementById(id);

function settingsFromForm() {
  return {
    projectRoot: element('project-root').value.trim(),
    objectRoot: element('object-root').value.trim(),
    chapter: element('chapter').value.trim(),
    waapiUrl: element('waapi-url').value.trim(),
    p4Executable: element('p4-executable').value.trim(),
    p4Port: element('p4-port').value.trim(),
    p4User: element('p4-user').value.trim(),
    p4Client: element('p4-client').value.trim(),
    p4Charset: element('p4-charset').value.trim(),
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
  element('p4-port').value = settings.p4Port || '';
  element('p4-user').value = settings.p4User || '';
  element('p4-client').value = settings.p4Client || '';
  element('p4-charset').value = settings.p4Charset || '';
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

function p4ConnectionSummary() {
  return [
    element('p4-port')?.value.trim(),
    element('p4-user')?.value.trim(),
    element('p4-client')?.value.trim(),
  ].filter(Boolean).join(' · ');
}

function renderSystem(system) {
  state.system = {...system};
  element('app-version').textContent = system.appVersion ? `v${system.appVersion}` : 'v—';
  element('platform-value').textContent = system.platform || '—';
  element('wwise-status').textContent = system.wwiseDetected ? '감지됨' : '연결 확인 필요';
  element('wwise-detail').textContent = system.wwiseConsole || 'Wwise 실행 후 환경 확인';
  element('data-detail').textContent = system.dataRoot || '앱 폴더의 data';
  const connection = system.p4Connection || {};
  if (connection.port || connection.user || connection.client) {
    element('p4-detail').textContent = [connection.port, connection.user, connection.client]
      .filter(Boolean)
      .join(' · ');
  }
  updateOfflineModePresentation();
}

function updateOfflineModePresentation() {
  const enabled = element('offline-test-mode').checked;
  const mutationLocked = Boolean(state.activeOperation);
  element('p4-executable').disabled = enabled || mutationLocked;
  element('p4-port').disabled = enabled || mutationLocked;
  element('p4-user').disabled = enabled || mutationLocked;
  element('p4-client').disabled = enabled || mutationLocked;
  element('p4-charset').disabled = enabled || mutationLocked;
  element('changelist').disabled = enabled || mutationLocked;
  element('offline-test-mode').disabled = mutationLocked;
  element('project-root').disabled = mutationLocked;
  element('choose-p4').disabled = enabled || mutationLocked || state.busy || !state.bridgeReady;
  element('detect-p4-connection').disabled = enabled || mutationLocked || state.busy || !state.bridgeReady;
  element('choose-project').disabled = mutationLocked || state.busy || !state.bridgeReady;
  element('p4-status').textContent = enabled
    ? '테스트에서 제외'
    : (state.system.p4Detected
      ? (state.system.p4ConnectionSource === 'p4v-environment' ? 'P4V 환경 감지됨' : '감지됨')
      : '찾지 못함');
  element('p4-detail').textContent = enabled
    ? '로컬 읽기 전용 점검만 실행합니다'
    : (p4ConnectionSummary() || state.system.p4Executable || '직접 선택할 수 있습니다');
  element('doctor-step-detail').textContent = enabled
    ? 'Wwise · 로컬 파일 · WAAPI'
    : 'Wwise · P4 · WAAPI';
  element('doctor-description').textContent = enabled
    ? 'Wwise 프로젝트, Originals WAV, Work Unit과 WAAPI 연결을 확인합니다. Perforce 점검은 제외됩니다.'
    : 'Wwise 프로젝트, Originals WAV, Work Unit, Perforce workspace와 WAAPI 연결을 확인합니다.';
  updateApplyButtons();
}

function renderReadiness(result) {
  if (result.p4Connection) {
    element('p4-port').value = result.p4Connection.port || element('p4-port').value;
    element('p4-user').value = result.p4Connection.user || element('p4-user').value;
    element('p4-client').value = result.p4Connection.client || element('p4-client').value;
    element('p4-charset').value = result.p4Connection.charset || element('p4-charset').value;
    element('p4-status').textContent = result.p4Connection.client
      ? '연결됨'
      : 'Workspace 선택 필요';
    element('p4-detail').textContent = p4ConnectionSummary() || 'Perforce 연결 확인됨';
  }
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
      && ['p4-cli', 'p4-connection', 'p4-workspace'].includes(check.name);
    const waapiMessage = check.name === 'waapi-server'
      ? waapiReadinessMessage(result, check)
      : null;
    const p4WorkspaceMessage = check.name === 'p4-workspace'
      ? p4WorkspaceReadinessMessage(result, check)
      : null;
    message.textContent = skippedPerforce
      ? '로컬 테스트 모드에서는 이 Perforce 점검을 건너뜁니다.'
      : (p4WorkspaceMessage || waapiMessage || readinessMessages[check.name]?.[check.status] || check.message);
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

function p4WorkspaceReadinessMessage(result, check) {
  if (check.status === 'pass') {
    return readinessMessages['p4-workspace'].pass;
  }
  if (result.p4WorkspaceIssue === 'connection-unavailable') {
    return 'Perforce 서버 연결을 먼저 확인한 뒤 다시 실행하세요.';
  }
  if (result.p4WorkspaceIssue === 'not-configured') {
    return '프로젝트에 맞는 workspace를 자동으로 찾지 못했습니다. P4V에서 workspace를 선택하거나 고급 연결 설정에 입력하세요.';
  }
  return '선택한 workspace에 이 Wwise 프로젝트가 매핑되어 있지 않습니다.';
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
  state.selectedItems = [];
  if (result.objectRoot && element('object-root').value !== result.objectRoot) {
    element('object-root').value = result.objectRoot;
  }
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
    row.dataset.planItemKey = planItemKey(item);
    row.append(
      selectionCell(item, result),
      tableCell(item.sourceFileName || item.objectPath, 'file-name'),
      planPathCell(item.from || '—', item.sourceFileName),
      planPathCell(item.to || '—', item.sourceFileName),
      actionCell(item.action),
      tableCell(item.reason || '—', 'reason-text'),
    );
    body.append(row);
  }
  element('plan-bulk-actions').hidden = false;
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
  const selectable = isSelectablePlanItem(item, result);
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'row-selector';
  checkbox.dataset.planItemKey = planItemKey(item);
  checkbox.disabled = !selectable;
  checkbox.setAttribute('aria-label', `${item.sourceFileName || item.objectPath} 선택`);
  checkbox.addEventListener('change', () => {
    if (checkbox.checked) {
      const selectedKeys = new Set(state.selectedItems.map(planItemKey));
      if (!selectedKeys.has(planItemKey(item))) state.selectedItems.push(item);
    } else {
      state.selectedItems = state.selectedItems.filter(
        (selected) => planItemKey(selected) !== planItemKey(item),
      );
    }
    renderApplySelection();
  });
  cell.append(checkbox);
  return cell;
}

function planItemKey(item) {
  return [item.objectPath || '', item.sourceFileName || '', item.from || '', item.to || ''].join('\u0000');
}

function isSelectablePlanItem(item, result = state.plan) {
  return Boolean(item
    && item.action === 'move-and-patch'
    && result?.validation?.valid
    && !result.offlineTestMode
    && !state.activeOperation);
}

function movablePlanItems() {
  return (state.plan?.items || []).filter((item) => item.action === 'move-and-patch');
}

function selectablePlanItems() {
  return movablePlanItems().filter((item) => isSelectablePlanItem(item));
}

function setAllPlanItemsSelected(selected) {
  state.selectedItems = selected ? selectablePlanItems() : [];
  renderApplySelection();
}

function syncPlanSelectionControls() {
  const selectable = selectablePlanItems();
  const selectableKeys = new Set(selectable.map(planItemKey));
  state.selectedItems = state.selectedItems.filter((item) => selectableKeys.has(planItemKey(item)));
  const selectedKeys = new Set(state.selectedItems.map(planItemKey));

  document.querySelectorAll('.row-selector').forEach((checkbox) => {
    const checked = selectedKeys.has(checkbox.dataset.planItemKey);
    checkbox.disabled = !selectableKeys.has(checkbox.dataset.planItemKey);
    checkbox.checked = checked;
    checkbox.closest('tr')?.classList.toggle('selected', checked);
  });

  const master = element('select-all-plan');
  master.disabled = selectable.length === 0;
  master.checked = selectable.length > 0 && selectedKeys.size === selectable.length;
  master.indeterminate = selectedKeys.size > 0 && selectedKeys.size < selectable.length;
  element('clear-plan-selection').disabled = selectedKeys.size === 0;
  element('plan-selected-count').textContent = `${selectedKeys.size}개 선택`;
  element('plan-selectable-count').textContent = `${movablePlanItems().length}개 이동 가능`;
}

function renderApplySelection() {
  syncPlanSelectionControls();
  if (state.activeOperation) {
    renderActiveOperation(state.activeOperation);
    return;
  }
  const selected = state.selectedItems;
  const hasSelection = selected.length > 0;
  element('active-operation').hidden = true;
  element('apply-controls').hidden = !hasSelection;
  element('apply-empty').hidden = hasSelection;
  element('apply-state').textContent = hasSelection ? `${selected.length}개 선택됨` : '계획 필요';
  element('apply-state').className = `panel-state ${hasSelection ? 'ready' : 'neutral'}`;
  if (hasSelection) {
    const selectedNames = selected.map((item) => item.sourceFileName);
    element('selected-file').textContent = summarizeFileNames(selectedNames);
    element('selected-file').title = selectedNames.length <= 10
      ? selectedNames.join(', ')
      : `${selectedNames.length}개 파일이 선택되었습니다.`;
    element('selected-move').textContent = selected.length === 1
      ? `${selected[0].from} → ${selected[0].to}`
      : `${selected.length}개 WAV를 같은 changelist에서 이동`;
    setStep('apply', 'active');
  }
  updateApplyButtons();
}

function renderActiveOperation(operation) {
  state.activeOperation = operation || null;
  const active = Boolean(operation);
  element('apply-validation-result').hidden = true;
  element('active-operation').hidden = !active;
  element('apply-controls').hidden = true;
  element('apply-empty').hidden = active;
  const validated = operation?.validated === true;
  const handedOff = operation?.status === 'handed-off';
  const awaitingReload = operation?.status === 'awaiting-wwise-reload';
  element('apply-state').textContent = active
    ? (operation.status === 'failed'
      ? 'Rollback 필요'
      : (handedOff ? 'P4V 마감 대기' : (awaitingReload ? 'Wwise Reload 대기' : '인계 가능')))
    : '계획 필요';
  element('apply-state').className = `panel-state ${active ? (validated && !handedOff ? 'ready' : 'warning') : 'neutral'}`;
  if (active) {
    const operationNames = operation.sourceFileNames || [operation.sourceFileName];
    element('active-file').textContent = summarizeFileNames(operationNames);
    element('active-file').title = operationNames.length <= 10
      ? operationNames.join(', ')
      : `${operationNames.length}개 파일 작업입니다.`;
    element('active-move').textContent = operationNames.length === 1
      ? `${operation.from} → ${operation.to}`
      : `${operationNames.length}개 WAV를 같은 changelist에서 이동`;
    element('apply-report').textContent = `Rollback manifest: ${operation.manifest}`;
    element('apply-report').hidden = false;
    element('run-validate-apply').hidden = !['awaiting-wwise-reload', 'applied'].includes(operation.status);
    element('run-handoff-apply').hidden = operation.status !== 'applied' || !validated;
    element('run-check-handoff').hidden = !handedOff;
    if (handedOff) {
      element('active-guide-1').textContent = 'P4V에서 WAV move와 Work Unit diff를 최종 검토합니다.';
      element('active-guide-2').textContent = '팀 절차에 따라 submit하거나 변경을 revert합니다.';
      element('active-guide-3').textContent = '마감 뒤 P4V 마감 상태 확인을 눌러 작업 잠금을 해제합니다.';
    } else if (awaitingReload) {
      element('active-guide-1').textContent = 'Wwise의 External Project Changes 창에서 affected Work Unit의 Reload를 누릅니다.';
      element('active-guide-2').textContent = 'Reload가 끝난 뒤 Wwise 반영 확인을 눌러 source 경로를 검증합니다.';
      element('active-guide-3').textContent = '반영 확인 전에는 P4V 인계를 실행할 수 없습니다.';
    } else {
      element('active-guide-1').textContent = 'Wwise와 Perforce 적용 상태를 확인했습니다.';
      element('active-guide-2').textContent = 'P4V로 인계하기 전에 파일과 Work Unit diff를 최종 검토합니다.';
      element('active-guide-3').textContent = 'P4V로 인계하거나 Rollback으로 원래 상태를 복구합니다.';
    }
    setStep('apply', 'active');
  } else {
    element('apply-report').hidden = true;
    element('apply-validation-result').hidden = true;
  }
  syncPlanSelectionControls();
  updateOfflineModePresentation();
}

function renderOperationHistory(history = {}) {
  state.operationHistory = history;
  const entries = history.entries || [];
  const list = element('history-list');
  const empty = element('history-empty');
  list.replaceChildren();

  for (const operation of entries) {
    const item = document.createElement('article');
    item.className = 'history-item';

    const primary = document.createElement('div');
    primary.className = 'history-primary';
    const createdAt = document.createElement('small');
    createdAt.textContent = formatOperationDate(operation.createdAt);
    const fileName = document.createElement('strong');
    fileName.textContent = operation.sourceFileName || '알 수 없는 파일';
    const move = document.createElement('p');
    move.textContent = `${operation.from || '—'} → ${operation.to || '—'}`;
    primary.append(createdAt, fileName, move);

    const status = document.createElement('span');
    status.className = `history-status ${operation.status || ''}`;
    status.textContent = operationStatusLabels[operation.status] || operation.status || '상태 확인 필요';

    const details = document.createElement('div');
    details.className = 'history-details';
    const changelist = document.createElement('span');
    changelist.textContent = operation.changelist
      ? `Changelist ${operation.changelist}`
      : '기본 changelist';
    const validation = document.createElement('span');
    validation.textContent = operation.validationRecorded
      ? '검증 보고서 있음'
      : '검증 보고서 없음';
    details.append(changelist, validation);

    const reportDetails = document.createElement('details');
    const reportSummary = document.createElement('summary');
    reportSummary.textContent = '보고서 위치';
    const reportPath = document.createElement('p');
    reportPath.textContent = operation.validationReport
      ? `작업 폴더: ${operation.reportDirectory}\n검증 보고서: ${operation.validationReport}`
      : `작업 폴더: ${operation.reportDirectory}`;
    reportDetails.append(reportSummary, reportPath);
    details.append(reportDetails);

    item.append(primary, status, details);
    list.append(item);
  }

  const hasProject = Boolean(element('project-root').value.trim());
  empty.querySelector('p').textContent = hasProject
    ? '이 프로젝트에서 만든 단일 파일 작업 기록이 없습니다.'
    : '프로젝트를 선택하면 이 앱에서 만든 작업 기록을 확인할 수 있습니다.';
  empty.hidden = entries.length > 0;
  list.hidden = entries.length === 0;

  const warning = element('history-warning');
  const unreadableCount = history.unreadableCount || 0;
  warning.textContent = unreadableCount
    ? `reports 폴더에서 읽지 못한 작업 기록 ${unreadableCount}개가 있습니다. 운영 담당자에게 전달하세요.`
    : '';
  warning.hidden = unreadableCount === 0;

  const root = element('history-root');
  root.textContent = history.reportRoot
    ? `전체 작업 기록 (${history.totalCount || 0}개): ${history.reportRoot}`
    : '';
  root.hidden = !history.reportRoot;
}

function formatOperationDate(value) {
  if (!value) return '시간 정보 없음';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function renderApplyValidation(result) {
  const validation = result.validation || {valid: false, issues: []};
  const panel = element('apply-validation-result');
  const issues = validation.issues || [];
  const valid = validation.valid === true;
  panel.className = `apply-validation-result ${valid ? 'valid' : 'invalid'}`;
  element('apply-validation-title').textContent = valid
    ? 'Wwise 반영 확인 완료'
    : '확인이 필요한 항목';
  element('apply-validation-summary').textContent = valid
    ? '로컬 파일, Perforce opened/diff, Wwise 객체와 source 경로가 모두 일치합니다.'
    : '아래 항목을 해결하거나 Rollback한 뒤 다시 계획해 주세요.';
  const list = element('apply-validation-list');
  list.replaceChildren();
  for (const issue of issues) {
    const item = document.createElement('li');
    const localized = validationMessages[issue.code] || issue.message;
    item.textContent = issue.objectPath ? `${localized} (${issue.objectPath})` : localized;
    list.append(item);
  }
  list.hidden = valid;
  panel.hidden = false;
  if (result.reports?.validation) {
    element('apply-report').textContent = `적용 검증 보고서: ${result.reports.validation}`;
    element('apply-report').hidden = false;
  }
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
  span.title = value;
  cell.append(span);
  return cell;
}

function planPathCell(value, sourceFileName) {
  const cell = document.createElement('td');
  const span = document.createElement('span');
  span.className = 'path-text';
  span.textContent = compactPlanLocation(value, sourceFileName);
  span.title = value;
  span.setAttribute('aria-label', value);
  cell.append(span);
  return cell;
}

function compactPlanLocation(value, sourceFileName) {
  if (!value || value === '—') return '—';
  const parts = value.replaceAll('\\', '/').split('/').filter(Boolean);
  if (parts.at(-1) === sourceFileName) parts.pop();
  const voicesIndex = parts.findIndex((part) => part.toLocaleLowerCase() === 'voices');
  const visibleParts = voicesIndex >= 0 ? parts.slice(voicesIndex + 1) : parts;
  return visibleParts.join(' / ') || '—';
}

function summarizeFileNames(names, limit = 3, separator = ', ') {
  const visibleNames = names.filter(Boolean);
  if (visibleNames.length <= limit) return visibleNames.join(separator);
  return `${visibleNames.slice(0, limit).join(separator)} 외 ${visibleNames.length - limit}개`;
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
  element('detect-p4-connection').disabled = busy || !state.bridgeReady || element('offline-test-mode').checked || Boolean(state.activeOperation);
  element('refresh-history').disabled = busy || !state.bridgeReady;
  updateApplyButtons();
}

function updateApplyButtons() {
  const offline = element('offline-test-mode')?.checked === true;
  const applyButton = element('run-apply');
  const rollbackButton = element('run-rollback');
  const validateButton = element('run-validate-apply');
  const handoffButton = element('run-handoff-apply');
  const checkHandoffButton = element('run-check-handoff');
  if (applyButton) {
    applyButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || state.selectedItems.length === 0
      || Boolean(state.activeOperation);
  }
  if (rollbackButton) {
    rollbackButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || !state.activeOperation;
  }
  if (validateButton) {
    validateButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || !state.activeOperation
      || !['awaiting-wwise-reload', 'applied'].includes(state.activeOperation.status);
  }
  if (handoffButton) {
    handoffButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || !state.activeOperation?.validated
      || state.activeOperation.status !== 'applied';
  }
  if (checkHandoffButton) {
    checkHandoffButton.disabled = state.busy
      || !state.bridgeReady
      || offline
      || state.activeOperation?.status !== 'handed-off';
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
    renderOperationHistory(initial.operationHistory || {});
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
      await refreshOperationHistory({reportErrors: false});
      if (!element('offline-test-mode').checked && state.system.p4Detected) {
        await detectP4Connection({quiet: true});
      }
    }
  } catch (error) {
    showError(error.message);
  }
}

async function detectP4Connection({quiet = false} = {}) {
  if (element('offline-test-mode').checked) return;
  if (!quiet) clearError();
  try {
    const result = await invoke('detect_p4_connection', settingsFromForm());
    applySettings(result.settings || {});
    state.system.p4Detected = true;
    state.system.p4Executable = result.settings?.p4Executable || state.system.p4Executable;
    element('p4-status').textContent = result.workspaceConfigured
      ? (result.source === 'p4v-environment' ? 'P4V 연결 감지됨' : 'Perforce 연결 감지됨')
      : 'Workspace 선택 필요';
    element('p4-detail').textContent = p4ConnectionSummary() || '연결 정보 확인됨';
    if (!quiet) {
      setBusy(
        false,
        result.workspaceConfigured
          ? 'P4V/Perforce 연결 정보를 불러왔습니다'
          : '서버 연결은 확인했지만 workspace 선택이 필요합니다',
      );
    }
  } catch (error) {
    if (!quiet) showError(error.message);
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
  const items = state.selectedItems;
  if (!items.length) return;
  const names = items.map((item) => item.sourceFileName);
  const confirmationToken = names.join('\n');
  const accepted = window.confirm(
    `${summarizeFileNames(names, 5, '\n')}\n\n선택한 WAV ${items.length}개를 같은 changelist에서 Perforce move하고 Work Unit 경로를 변경합니다.\n하나라도 실패하면 이미 적용한 항목을 자동으로 복구합니다. 이 프로그램은 submit하지 않습니다. 계속할까요?`,
  );
  if (!accepted) return;
  clearError();
  setBusy(true, `선택한 파일 ${items.length}개를 적용하고 있습니다…`);
  try {
    const result = await invoke(
      'run_apply',
      settingsFromForm(),
      names,
      confirmationToken,
    );
    if (!result.applied) {
      if (result.activeOperation) renderActiveOperation(result.activeOperation);
      await refreshOperationHistory({reportErrors: false});
      setBusy(false);
      showError(result.errorMessage || '파일 적용을 완료하지 못했습니다.');
      return;
    }
    renderActiveOperation(result.activeOperation);
    await refreshOperationHistory({reportErrors: false});
    setBusy(false, `${items.length}개 파일을 적용했습니다. Wwise에서 외부 변경을 다시 불러오세요`);
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function runRollback() {
  const operation = state.activeOperation;
  if (!operation) return;
  const operationNames = operation.sourceFileNames || [operation.sourceFileName];
  const accepted = window.confirm(
    `${summarizeFileNames(operationNames, 5, '\n')}\n\nmanifest에 기록된 WAV ${operationNames.length}개와 Work Unit만 원래 상태로 복구합니다. 계속할까요?`,
  );
  if (!accepted) return;
  clearError();
  setBusy(true, `${operation.sourceFileName}을 복구하고 있습니다…`);
  try {
    const result = await invoke(
      'run_rollback',
      settingsFromForm(),
      operation.confirmationToken || operation.sourceFileName,
    );
    if (!result.rolledBack) {
      renderActiveOperation(result.activeOperation || operation);
      await refreshOperationHistory({reportErrors: false});
      setBusy(false);
      const issues = result.validation?.issues || [];
      const details = issues.slice(0, 3).map(
        (issue) => validationMessages[issue.code] || issue.message,
      ).join(' ');
      showError(details || 'Rollback을 완료하지 못했습니다. 보고서와 로그를 확인하세요.');
      return;
    }
    renderActiveOperation(null);
    resetResults();
    await refreshOperationHistory({reportErrors: false});
    setBusy(false, 'Rollback을 완료했습니다. Wwise에서 외부 변경을 다시 불러오세요');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function runValidateApply() {
  const operation = state.activeOperation;
  if (!operation || !['awaiting-wwise-reload', 'applied'].includes(operation.status)) return;
  clearError();
  setBusy(true, `${operation.sourceFileName}의 Wwise 반영 상태를 확인하고 있습니다…`);
  try {
    const result = await invoke('run_validate_apply', settingsFromForm());
    renderActiveOperation(result.activeOperation || operation);
    renderApplyValidation(result);
    await refreshOperationHistory({reportErrors: false});
    setBusy(false, result.valid
      ? 'Wwise와 Perforce 적용 상태를 확인했습니다'
      : '적용 결과에 확인할 항목이 있습니다');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function runHandoffApply() {
  const operation = state.activeOperation;
  if (!operation?.validated || operation.status !== 'applied') return;
  const accepted = window.confirm(
    `${operation.sourceFileName}\n\n검증을 다시 실행한 뒤 이 작업을 P4V 검토 단계로 인계합니다.\n앱은 submit하지 않으며, P4V 마감 전까지 Rollback을 사용할 수 있습니다. 계속할까요?`,
  );
  if (!accepted) return;
  clearError();
  setBusy(true, `${operation.sourceFileName}을 다시 검증하고 P4V로 인계하고 있습니다…`);
  try {
    const result = await invoke(
      'run_handoff_apply',
      settingsFromForm(),
      operation.confirmationToken || operation.sourceFileName,
    );
    if (!result.handedOff) {
      renderActiveOperation(result.activeOperation || operation);
      renderApplyValidation(result);
      await refreshOperationHistory({reportErrors: false});
      setBusy(false, '인계 전 검증에서 확인할 항목이 있습니다');
      return;
    }
    renderActiveOperation(result.activeOperation);
    await refreshOperationHistory({reportErrors: false});
    setBusy(false, 'P4V 검토 단계로 인계했습니다. 앱은 submit하지 않습니다');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function runCheckHandoff() {
  const operation = state.activeOperation;
  if (operation?.status !== 'handed-off') return;
  clearError();
  setBusy(true, 'P4V의 opened 상태와 Wwise 반영 상태를 확인하고 있습니다…');
  try {
    const result = await invoke('run_check_handoff', settingsFromForm());
    if (!result.completed) {
      renderActiveOperation(result.activeOperation || operation);
      if (result.validation) renderApplyValidation(result);
      await refreshOperationHistory({reportErrors: false});
      setBusy(false, result.pendingPathCount
        ? `P4V에 관련 파일 ${result.pendingPathCount}개가 아직 열려 있습니다`
        : 'Wwise 반영 상태에 확인할 항목이 있습니다');
      return;
    }
    renderActiveOperation(null);
    resetResults();
    await refreshOperationHistory({reportErrors: false});
    element('apply-state').textContent = result.finalState === 'rolled-back'
      ? '외부 복구 확인'
      : '작업 완료';
    element('apply-state').className = 'panel-state ready';
    setStep('apply', 'done');
    setBusy(false, result.finalState === 'rolled-back'
      ? 'P4V에서 복구된 상태를 확인했습니다. Wwise를 다시 불러오세요'
      : 'P4V와 Wwise 마감 상태를 확인했습니다');
  } catch (error) {
    setBusy(false);
    showError(error.message);
  }
}

async function refreshOperationHistory({reportErrors = true} = {}) {
  if (!state.bridgeReady) return false;
  try {
    const history = await invoke('get_operation_history', settingsFromForm());
    renderOperationHistory(history);
    return true;
  } catch (error) {
    if (reportErrors) showError(error.message);
    return false;
  }
}

async function runRefreshHistory() {
  clearError();
  setBusy(true, '최근 작업 기록을 확인하고 있습니다…');
  const refreshed = await refreshOperationHistory();
  setBusy(false, refreshed ? '최근 작업 기록을 새로고침했습니다' : '작업 기록을 읽지 못했습니다');
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
  element('plan-bulk-actions').hidden = true;
  element('plan-table-body').replaceChildren();
  element('plan-table-wrap').hidden = true;
  element('validation-issues').hidden = true;
  element('apply-validation-result').hidden = true;
  element('plan-report').hidden = true;
  element('offline-result-note').hidden = true;
  state.plan = null;
  state.selectedItems = [];
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

function loadPreview(previewMode = '1') {
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
    p4Port: 'ssl:perforce.example.com:1666',
    p4User: 'audio.user',
    p4Client: 'audio-workspace',
    p4Charset: 'utf8',
    changelist: '123456',
    offlineTestMode: false,
  });
  renderSystem({
    platform: 'Windows',
    appVersion: '0.1.0rc3',
    p4Detected: true,
    p4Executable: 'C:\\Program Files\\Perforce\\p4.exe',
    p4Connection: {
      port: 'ssl:perforce.example.com:1666',
      user: 'audio.user',
      client: 'audio-workspace',
      charset: 'utf8',
    },
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
  const previewItems = previewMode === 'plan-100'
    ? buildLargePlanPreview(100)
    : [
      {sourceFileName: 'CH04_S102_WT_001.wav', from: 'Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav', to: 'Originals/Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav', action: 'move-and-patch'},
      {sourceFileName: 'CH04_S103_DI_004.wav', from: 'Originals/Voices/English(US)/Scenario/CH04/CH04_S103_DI_004.wav', to: 'Originals/Voices/English(US)/Dialog/CH04/CH04_S103_DI_004.wav', action: 'move-and-patch'},
      {sourceFileName: 'CH04_S104_SQ_002.wav', from: 'Originals/Voices/English(US)/Scenario/CH04/CH04_S104_SQ_002.wav', to: 'Originals/Voices/English(US)/Cutscene/CH04/CH04_S104_SQ_002.wav', action: 'move-and-patch'},
      {sourceFileName: 'CH04_CUT_010.wav', from: 'Originals/Voices/English(US)/Cutscene/CH04/CH04_CUT_010.wav', to: 'Originals/Voices/English(US)/Cutscene/CH04/CH04_CUT_010.wav', action: 'skip'},
    ];
  const previewCounts = previewItems.reduce((counts, item) => {
    counts[item.action] = (counts[item.action] || 0) + 1;
    return counts;
  }, {'move-and-patch': 0, skip: 0, 'manual-review': 0});
  renderPlan({
    counts: previewCounts,
    validation: {
      valid: true,
      issues: [],
    },
    items: previewItems,
    reports: {planMarkdown: 'data/reports/plan.md'},
  });
  if (!previewMode.startsWith('plan')) {
    renderActiveOperation({
      sourceFileName: 'CH04_S102_WT_001.wav',
      from: 'Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav',
      to: 'Originals/Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav',
      objectPath: '\\Containers\\Default Work Unit\\VO\\Script\\CH04\\CH04_S102_WT_001',
      changelist: '123456',
      status: 'handed-off',
      validated: false,
      manifest: 'data/reports/apply/rollback-manifest.json',
    });
    renderApplyValidation({
      valid: true,
      validation: {valid: true, issues: []},
      reports: {validation: 'data/reports/validate-apply/apply-validation.md'},
    });
  }
  renderOperationHistory({
    entries: [
      {
        createdAt: '2026-07-14T10:30:00+09:00',
        sourceFileName: 'CH04_S102_WT_001.wav',
        from: 'Originals/Voices/English(US)/Scenario/CH04/CH04_S102_WT_001.wav',
        to: 'Originals/Voices/English(US)/Script/CH04/CH04_S102_WT_001.wav',
        changelist: '123456',
        status: 'handed-off',
        validationRecorded: true,
        validationReport: 'data/reports/validate-apply/apply-validation.md',
        reportDirectory: 'data/reports/20260714T013000.000000Z-apply',
      },
      {
        createdAt: '2026-07-13T16:20:00+09:00',
        sourceFileName: 'CH04_CUT_010.wav',
        from: 'Originals/Voices/English(US)/Cutscene/CH04/CH04_CUT_010.wav',
        to: 'Originals/Voices/English(US)/Script/CH04/CH04_CUT_010.wav',
        changelist: '123455',
        status: 'rolled-back',
        validationRecorded: false,
        reportDirectory: 'data/reports/20260713T072000.000000Z-apply',
      },
    ],
    totalCount: 2,
    unreadableCount: 0,
    reportRoot: 'data/reports',
  });
  setBusy(false, '브라우저 미리보기');
}

function buildLargePlanPreview(count) {
  const targets = [
    {folder: 'Script', code: 'WT'},
    {folder: 'Dialog', code: 'DI'},
    {folder: 'Cutscene', code: 'SQ'},
    {folder: 'Dynamic', code: 'DY'},
  ];
  return Array.from({length: count}, (_, index) => {
    const target = targets[index % targets.length];
    const sequence = String(index + 1).padStart(3, '0');
    const sourceFileName = `CH04_S${sequence}_${target.code}_001.wav`;
    return {
      sourceFileName,
      from: `Originals/Voices/English(US)/Scenario/CH04/${sourceFileName}`,
      to: `Originals/Voices/English(US)/${target.folder}/CH04/${sourceFileName}`,
      action: 'move-and-patch',
    };
  });
}

element('choose-project').addEventListener('click', chooseProject);
element('choose-p4').addEventListener('click', chooseP4);
element('detect-p4-connection').addEventListener('click', detectP4Connection);
element('run-doctor').addEventListener('click', runDoctor);
element('run-plan').addEventListener('click', runPlan);
element('select-all-plan').addEventListener('change', (event) => setAllPlanItemsSelected(event.target.checked));
element('clear-plan-selection').addEventListener('click', () => setAllPlanItemsSelected(false));
element('run-apply').addEventListener('click', runApply);
element('run-validate-apply').addEventListener('click', runValidateApply);
element('run-handoff-apply').addEventListener('click', runHandoffApply);
element('run-check-handoff').addEventListener('click', runCheckHandoff);
element('run-rollback').addEventListener('click', runRollback);
element('refresh-history').addEventListener('click', runRefreshHistory);
element('project-root').addEventListener('input', updateProjectState);
element('offline-test-mode').addEventListener('change', changeOfflineMode);
window.addEventListener('pywebviewready', initialize, {once: true});

const previewMode = new URLSearchParams(window.location.search).get('preview');
if (previewMode) {
  loadPreview(previewMode);
} else {
  window.setTimeout(() => {
    if (!state.bridgeReady) {
      element('bridge-status').textContent = '앱 연결 없음';
      element('bridge-status').className = 'connection-badge preview';
      setBusy(false, 'Portable 앱에서 열어 주세요');
    }
  }, 1200);
}
