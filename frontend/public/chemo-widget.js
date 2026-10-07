/* Host integration v1: configure trusted origins and a server-issued token before use. */
(function () {
  'use strict';
  window.createChemoWidget = function (config) {
    const serviceOrigin = new URL(config.serviceOrigin).origin;
    if (typeof config.getAccessToken !== 'function') throw new Error('getAccessToken is required');
    const session = crypto.randomUUID();
    let generation = 0, current = null, controller = null, pending = null, token = null, pollController = null, pollTimer = null, pollEpoch = 0;
    const root = document.createElement('div');
    root.style.cssText = 'position:fixed;right:24px;bottom:24px;z-index:2147483000;font-family:system-ui;';
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = '化疗智能体'; button.setAttribute('aria-expanded', 'false');
    button.style.cssText = 'padding:13px 18px;border:1px solid #5b9b92;border-radius:24px;background:#17685f;color:white;font:600 14px system-ui;box-shadow:0 6px 24px #183f3a22;cursor:pointer;';
    const panel = document.createElement('section');
    panel.hidden = true; panel.setAttribute('aria-label', '化疗智能体');
    const panelSurface = 'position:fixed;border:1px solid #dce5e1;border-radius:14px;background:white;box-shadow:0 12px 60px #183f3a33;overflow:hidden;';
    const panelNormal = 'right:24px;bottom:82px;width:min(1280px,calc(100vw - 48px));height:min(840px,calc(100vh - 120px));';
    let maximized = false;
    panel.style.cssText = panelSurface + panelNormal;
    const close = document.createElement('button');
    close.textContent = '收起'; close.type = 'button'; close.setAttribute('aria-label', '收起智能体');
    close.style.cssText = 'position:absolute;right:12px;top:3px;z-index:2;border:1px solid #dae5e0;border-radius:6px;background:white;padding:4px 10px;cursor:pointer;';
    const expand = document.createElement('button');
    expand.type = 'button'; expand.textContent = '展开'; expand.setAttribute('aria-label', '展开工作台');
    expand.style.cssText = 'position:absolute;right:70px;top:3px;z-index:2;border:1px solid #dae5e0;border-radius:6px;background:white;padding:4px 10px;cursor:pointer;';
    expand.onclick = () => { maximized = !maximized; panel.style.cssText = panelSurface + (maximized ? 'left:12px;top:12px;width:calc(100vw - 24px);height:calc(100vh - 24px);' : panelNormal); button.hidden = maximized; expand.textContent = maximized ? '恢复' : '展开'; expand.setAttribute('aria-label', maximized ? '恢复窗口大小' : '展开工作台'); };
    const frame = document.createElement('iframe');
    frame.title = '化疗智能体患者工作台'; frame.style.cssText = 'position:absolute;left:0;top:32px;border:0;width:100%;height:calc(100% - 32px);';
    frame.setAttribute('referrerpolicy', 'no-referrer');
    panel.append(frame, expand, close); root.append(panel, button); document.body.append(root);
    const send = () => { if (current && token && frame.src) frame.contentWindow.postMessage({ type: 'CHEMO_CONTEXT', version: '1', context_id: current.context_id, access_token: token }, serviceOrigin); };
    const receiver = event => { if (event.origin === serviceOrigin && event.source === frame.contentWindow && event.data?.type === 'CHEMO_READY') send(); };
    window.addEventListener('message', receiver);
    close.onclick = () => { panel.hidden = true; button.hidden = false; button.setAttribute('aria-expanded', 'false'); button.focus(); };
    button.onclick = () => {
      if (!current) {
        if (pending) receive(pending.payload, pending.key, pending.runGeneration).catch(error => { if (error.name !== 'AbortError') { button.textContent = '准备失败 · 点击重试'; config.onError?.(error); } });
        else button.textContent = '等待当前患者';
        return;
      }
      panel.hidden = !panel.hidden; button.setAttribute('aria-expanded', String(!panel.hidden));
      button.hidden = maximized && !panel.hidden;
      if (!panel.hidden) { if (!frame.src) frame.src = serviceOrigin + '/?context_id=' + encodeURIComponent(current.context_id); else send(); stopPoll(); void readPreparation(generation); }
    };
    function stopPoll() { ++pollEpoch; pollController?.abort(); if (pollTimer !== null) clearTimeout(pollTimer); pollTimer = null; }
    async function readPreparation(runGeneration) {
      if (!current || runGeneration !== generation) return;
      const requestEpoch = ++pollEpoch;
      pollController = new AbortController();
      try {
        const response = await fetch(serviceOrigin + '/api/v1/contexts/' + encodeURIComponent(current.context_id) + '/preparation-status', { signal: pollController.signal, headers: { 'Authorization': 'Bearer ' + token } });
        const value = await response.json();
        if (runGeneration !== generation || requestEpoch !== pollEpoch) return;
        if (!response.ok) throw new Error(value.message || '准备状态读取失败');
        if (value.context_state !== 'ACTIVE' || Date.parse(value.expires_at) <= Date.now()) { button.textContent = '化疗智能体 · 上下文已失效'; return; }
        if (value.prepare_status === 'FAILED' || value.prepare_status === 'CANCELLED' || value.prepare_status === 'SUPERSEDED') { button.textContent = '化疗智能体 · 准备未完成'; return; }
        const ready = value.prepare_status === 'SUCCEEDED' && value.decision_status === 'SUCCEEDED';
        button.textContent = ready ? value.candidate_count > 0 ? '化疗智能体 · 候选已就绪' : '化疗智能体 · 当前暂无候选' : '化疗智能体 · 后台准备中';
        pollTimer = setTimeout(() => readPreparation(runGeneration), ready ? 6000 : 1800);
      } catch (error) {
        if (runGeneration === generation && requestEpoch === pollEpoch && error.name !== 'AbortError') { button.textContent = '化疗智能体 · 状态读取失败'; config.onError?.(error); }
      }
    }
    async function receive(payload, requestKey, runGeneration) {
      controller?.abort(); const launchController = new AbortController(); controller = launchController;
      const obtained = await config.getAccessToken(payload);
      if (runGeneration !== generation || launchController !== controller || launchController.signal.aborted) return;
      token = obtained;
      const response = await fetch(serviceOrigin + '/api/v1/launch-context', { method: 'POST', signal: launchController.signal,
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token, 'Idempotency-Key': requestKey }, body: JSON.stringify(payload) });
      const value = await response.json();
      if (runGeneration !== generation || launchController !== controller || launchController.signal.aborted) return;
      if (!response.ok) throw new Error(value.message || '后台准备未完成');
      current = value; pending = null; button.textContent = '化疗智能体 · 已开始准备'; send(); stopPoll(); void readPreparation(runGeneration);
    }
    return {
      async updatePatient(context) {
        const old = current; const runGeneration = ++generation;
        controller?.abort(); stopPoll(); current = null; token = null; frame.removeAttribute('src'); panel.hidden = true; button.hidden = false; button.setAttribute('aria-expanded', 'false');
        const payload = { ...context, request_scene: 'AUTO_PREPARE', session_scope_ref: session, client_generation: runGeneration, previous_context_id: old?.context_id || null };
        const key = crypto.randomUUID(); pending = { payload, key, runGeneration }; button.textContent = '化疗智能体 · 后台准备';
        try { await receive(payload, key, runGeneration); }
        catch (error) { if (runGeneration === generation && error.name !== 'AbortError') { button.textContent = '准备失败 · 可重试'; config.onError?.(error); } }
      },
      async retry() { if (pending) await receive(pending.payload, pending.key, pending.runGeneration); else if (current) { stopPoll(); await readPreparation(generation); } },
      destroy() { ++generation; controller?.abort(); stopPoll(); pending = null; current = null; token = null; window.removeEventListener('message', receiver); root.remove(); }
    };
  };
})();
