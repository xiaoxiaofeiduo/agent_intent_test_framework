#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""智能体意图识别 Web 测试服务器。

同一个进程同时提供：
1. Web 控制台，用于选择用例、编辑 mock、执行测试、查看结果。
2. OpenAI-compatible Mock LLM 接口，供防护设备作为上游大模型服务调用。
"""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import compact_json, json_dumps
from mock_llm_server import (
    build_non_stream_response,
    build_stream_events,
    find_case,
    load_scenarios,
)
from runner import CaseResult, run_case, write_reports


FAVICON_PATH = Path(__file__).with_name("favicon.ico")

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.ico" sizes="32x32" />
  <title>智能体意图识别测试控制台</title>
  <style>
    :root { color-scheme: light; --border:#d8dde6; --bg:#f6f8fb; --text:#1f2937; --muted:#667085; --blue:#2563eb; --red:#c2410c; --green:#047857; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif; color:var(--text); background:var(--bg); }
    header { padding:16px 24px; background:#fff; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; }
    h1 { font-size:20px; margin:0; }
    main { display:grid; grid-template-columns: 390px minmax(520px,1fr); gap:16px; padding:16px; }
    section { background:#fff; border:1px solid var(--border); border-radius:8px; padding:14px; }
    h2 { font-size:16px; margin:0 0 12px; }
    label { display:block; font-size:13px; color:var(--muted); margin:10px 0 6px; }
    input, textarea, select { width:100%; border:1px solid var(--border); border-radius:6px; padding:8px; font:inherit; }
    textarea { min-height:120px; font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; }
    button { border:1px solid var(--blue); background:var(--blue); color:#fff; border-radius:6px; padding:8px 12px; cursor:pointer; font:inherit; }
    button.secondary { background:#fff; color:var(--blue); }
    button:disabled { opacity:.5; cursor:not-allowed; }
    .row { display:flex; gap:8px; align-items:center; }
    .row > * { flex:1; }
    .hint { color:var(--muted); font-size:12px; line-height:1.5; }
    .case-list { max-height:560px; overflow:auto; border:1px solid var(--border); border-radius:6px; }
    .case-item { display:grid; grid-template-columns: 28px 1fr auto; gap:8px; align-items:center; padding:8px; border-bottom:1px solid #eef1f5; cursor:pointer; }
    .case-item:last-child { border-bottom:0; }
    .case-item.active { background:#eff6ff; }
    .case-title { font-size:13px; line-height:1.35; }
    .case-id { color:var(--muted); font-size:11px; margin-top:2px; word-break:break-all; }
    .tag { display:inline-block; border-radius:999px; padding:2px 8px; font-size:12px; border:1px solid var(--border); color:var(--muted); }
    .tag.block { color:var(--red); border-color:#fed7aa; background:#fff7ed; }
    .tag.pass { color:var(--green); border-color:#bbf7d0; background:#f0fdf4; }
    .tag.created { color:var(--green); border-color:#bbf7d0; background:#f0fdf4; }
    .tag.deleted { color:var(--red); border-color:#fed7aa; background:#fff7ed; }
    .tag.modified { color:#a16207; border-color:#fde68a; background:#fffbeb; }
    .tag.unchanged { color:var(--muted); border-color:var(--border); background:#f8fafc; }
    .tag.restored { color:var(--blue); border-color:#bfdbfe; background:#eff6ff; }
    .toolbar { display:flex; gap:8px; margin-top:12px; }
    .split { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px; }
    pre { background:#0f172a; color:#dbeafe; border-radius:6px; padding:10px; overflow:auto; max-height:360px; font-size:12px; line-height:1.45; white-space:pre-wrap; overflow-wrap:anywhere; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { border-bottom:1px solid #eef1f5; padding:8px; text-align:left; vertical-align:top; }
    th { color:var(--muted); font-weight:600; }
    .ok { color:var(--green); font-weight:600; }
    .fail { color:var(--red); font-weight:600; }
    .status { min-height:20px; color:var(--muted); font-size:13px; margin-top:8px; }
    .result-row { cursor:pointer; }
    .result-row:hover { background:#f8fafc; }
    .detail-row { display:none; background:#fbfdff; }
    .detail-row.open { display:table-row; }
    .detail-panel { padding:14px; border:1px solid var(--border); border-radius:8px; background:#fff; width:100%; overflow:hidden; }
    .detail-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px; width:100%; }
    .detail-card { border:1px solid #e6eaf0; border-radius:8px; padding:10px; background:#fff; min-width:0; overflow:hidden; }
    .detail-card.full { grid-column:1 / -1; }
    .detail-card h3 { margin:0 0 8px; font-size:14px; }
    .kv { display:grid; grid-template-columns:96px minmax(0,1fr); gap:6px; font-size:12px; margin:4px 0; }
    .kv span:first-child { color:var(--muted); }
    .kv span:last-child { overflow-wrap:anywhere; word-break:break-word; }
    .metric-row { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 10px; }
    .tool-table, .diff-table { margin-top:8px; border:1px solid #eef1f5; border-radius:6px; overflow:hidden; }
    .diff-row { cursor:pointer; }
    .diff-row:hover { background:#f8fafc; }
    .file-preview { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:8px; }
    .file-preview h4 { margin:0 0 6px; font-size:12px; color:var(--muted); }
    details { margin-top:10px; }
    summary { cursor:pointer; color:var(--blue); font-size:13px; }
    @media (max-width: 980px) { main { grid-template-columns:1fr; } .split { grid-template-columns:1fr; } }
    @media (max-width: 980px) { .detail-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>智能体意图识别测试控制台</h1>
    <div class="hint" id="mockEndpoint"></div>
  </header>
  <main>
    <div>
      <section>
        <h2>防护设备入口</h2>
        <label>device_url（必须填写防护设备入口，不是 Mock 地址）</label>
        <input id="deviceUrl" value="http://10.10.121.15:18081/v1/chat/completions" placeholder="http://防护设备地址/v1/chat/completions" />
        <div class="row">
          <div>
            <label>API Key</label>
            <input id="apiKey" placeholder="可为空" />
          </div>
          <div>
            <label>超时秒数</label>
            <input id="timeoutSeconds" type="number" value="30" />
          </div>
        </div>
        <label>额外请求头 JSON</label>
        <textarea id="headersText" spellcheck="false">{}</textarea>
        <p class="hint">防护设备的大模型上游应配置为页面顶部显示的 Mock Endpoint。</p>
      </section>

      <section style="margin-top:16px;">
        <h2>测试用例</h2>
        <input id="caseFilter" placeholder="按 id/name 过滤" />
        <div class="toolbar">
          <button class="secondary" id="selectAllBtn">全选当前</button>
          <button class="secondary" id="clearBtn">清空</button>
        </div>
        <div class="case-list" id="caseList" style="margin-top:10px;"></div>
      </section>
    </div>

    <div>
      <section>
        <h2>当前用例编辑与请求预览</h2>
        <div class="split">
          <div>
            <label>OpenAI 请求 JSON（编辑后执行时生效）</label>
            <textarea id="caseEditor" spellcheck="false" style="min-height:300px;"></textarea>
            <div class="toolbar">
              <button class="secondary" id="resetCaseBtn">恢复场景默认</button>
              <button class="secondary" id="formatCaseBtn">格式化用例</button>
              <button class="secondary" id="previewBtn">预览请求</button>
            </div>
          </div>
          <div>
            <label>发送到防护设备的请求预览</label>
            <pre id="requestPreview">{}</pre>
          </div>
        </div>
        <div class="toolbar">
          <button id="runSelectedBtn">执行选中用例</button>
          <button class="secondary" id="runActiveBtn">只执行当前用例</button>
        </div>
        <div class="status" id="statusText"></div>
      </section>

      <section style="margin-top:16px;">
        <h2>执行结果</h2>
        <div id="summary" class="hint"></div>
        <div style="overflow:auto;">
          <table>
            <thead>
              <tr><th>用例</th><th>预期</th><th>状态码</th><th>耗时</th><th>结果</th><th>错误</th></tr>
            </thead>
            <tbody id="resultBody"></tbody>
          </table>
        </div>
      </section>
    </div>
  </main>

  <script>
    const state = { cases: [], activeId: null, selected: new Set(), overrides: {} };
    const $ = (id) => document.getElementById(id);
    const blockText = '智能体执行行为与用户意图偏离，无法继续执行。';

    function pretty(obj) { return JSON.stringify(obj, null, 2); }
    function caseMatches(c, keyword) {
      if (!keyword) return true;
      const k = keyword.toLowerCase();
      return c.id.toLowerCase().includes(k) || c.name.toLowerCase().includes(k);
    }
    function getActiveCase() { return state.cases.find(c => c.id === state.activeId); }

    async function loadCases() {
      const res = await fetch('/api/cases');
      const data = await res.json();
      state.cases = data.cases;
      $('mockEndpoint').textContent = 'Mock Endpoint: ' + data.mock_endpoint;
      if (state.cases.length) state.activeId = state.cases[0].id;
      renderCases();
      renderActive();
    }

    function renderCases() {
      const keyword = $('caseFilter').value.trim();
      const list = $('caseList');
      list.innerHTML = '';
      for (const c of state.cases.filter(c => caseMatches(c, keyword))) {
        const item = document.createElement('div');
        item.className = 'case-item' + (c.id === state.activeId ? ' active' : '');
        item.onclick = (ev) => {
          if (ev.target.tagName !== 'INPUT') {
            state.activeId = c.id;
            renderCases();
            renderActive();
          }
        };
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selected.has(c.id);
        checkbox.onclick = (ev) => {
          ev.stopPropagation();
          checkbox.checked ? state.selected.add(c.id) : state.selected.delete(c.id);
        };
        const title = document.createElement('div');
        title.innerHTML = `<div class="case-title">${c.name}</div><div class="case-id">${c.id}</div>`;
        const tag = document.createElement('span');
        tag.className = 'tag ' + c.expect_action;
        tag.textContent = c.expect_action;
        item.appendChild(checkbox);
        item.appendChild(title);
        item.appendChild(tag);
        list.appendChild(item);
      }
    }

    function renderActive() {
      const c = getActiveCase();
      if (!c) return;
      const editable = caseToOpenAiEditable(state.overrides[c.id] || c.case || {});
      $('caseEditor').value = pretty(editable);
      previewActiveCase();
    }

    function saveActiveCase() {
      const c = getActiveCase();
      if (!c) return true;
      const text = $('caseEditor').value.trim();
      if (!text) {
        delete state.overrides[c.id];
        return true;
      }
      try {
        state.overrides[c.id] = openAiEditableToCase(JSON.parse(text), c);
        return true;
      } catch (err) {
        $('statusText').textContent = 'OpenAI 请求 JSON 不合法：' + err.message;
        return false;
      }
    }

    async function previewActiveCase() {
      if (!saveActiveCase()) return;
      const c = getActiveCase();
      if (!c) return;
      try {
        const res = await fetch('/api/preview', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({case: state.overrides[c.id] || c.case})
        });
        const data = await res.json();
        $('requestPreview').textContent = pretty(data.request || data);
      } catch (err) {
        $('requestPreview').textContent = '预览失败：' + err.message;
      }
    }

    async function runCases(caseIds) {
      if (!saveActiveCase()) return;
      const headersText = $('headersText').value.trim() || '{}';
      let headers = {};
      try { headers = JSON.parse(headersText); } catch (err) {
        $('statusText').textContent = '额外请求头不是合法 JSON：' + err.message;
        return;
      }
      if (!$('deviceUrl').value.trim()) {
        $('statusText').textContent = '请填写防护设备入口 device_url';
        return;
      }
      $('statusText').textContent = '执行中...';
      $('runSelectedBtn').disabled = true;
      $('runActiveBtn').disabled = true;
      try {
        const res = await fetch('/api/run', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            device_url: $('deviceUrl').value.trim(),
            api_key: $('apiKey').value.trim(),
            timeout_seconds: Number($('timeoutSeconds').value || 30),
            headers,
            case_ids: caseIds,
            case_overrides: state.overrides
          })
        });
        const data = await res.json();
        renderResults(data);
        $('statusText').textContent = data.ok ? '执行完成' : ('执行失败：' + (data.error || '未知错误'));
      } finally {
        $('runSelectedBtn').disabled = false;
        $('runActiveBtn').disabled = false;
      }
    }

    function renderResults(data) {
      $('summary').textContent = `通过率：${data.passed}/${data.total}，报告：${data.report_md || '-'}`;
      const body = $('resultBody');
      body.innerHTML = '';
      for (const [index, r] of (data.results || []).entries()) {
        const tr = document.createElement('tr');
        tr.className = 'result-row';
        tr.onclick = () => {
          const detail = document.getElementById(`detail-${index}`);
          if (detail) detail.classList.toggle('open');
        };
        tr.innerHTML = `
          <td><div>${r.name}</div><div class="case-id">${r.case_id}</div></td>
          <td>${r.expected_action}</td>
          <td>${r.status_code ?? '-'}</td>
          <td>${r.elapsed_ms} ms</td>
          <td class="${r.passed ? 'ok' : 'fail'}">${r.passed ? '通过' : '失败'}</td>
          <td>${r.error || ''}</td>
        `;
        body.appendChild(tr);

        const detailTr = document.createElement('tr');
        detailTr.id = `detail-${index}`;
        detailTr.className = 'detail-row';
        detailTr.innerHTML = `<td colspan="6">${buildResultDetail(r)}</td>`;
        body.appendChild(detailTr);
      }
    }

    function buildResultDetail(r) {
      const req = r.request || {};
      const messages = Array.isArray(req.messages) ? req.messages : [];
      const userMessage = messages.findLast ? messages.findLast(m => m.role === 'user') : [...messages].reverse().find(m => m.role === 'user');
      const tools = Array.isArray(req.tools) ? req.tools : [];
      const response = r.response_text || '';
      const responseSummary = summarizeResponse(response);
      return `
        <div class="detail-panel">
          <div class="detail-grid">
            <div class="detail-card">
              <h3>请求概览</h3>
              <div class="kv"><span>模型</span><span>${escapeHtml(req.model || '-')}</span></div>
              <div class="kv"><span>流式</span><span>${req.stream ? '是' : '否'}</span></div>
              <div class="kv"><span>用户指令</span><span>${escapeHtml((userMessage && userMessage.content) || '-')}</span></div>
              <div class="kv"><span>工具数量</span><span>${tools.length}</span></div>
              <div class="kv"><span>Case ID</span><span>${escapeHtml((req.metadata && req.metadata.intent_case_id) || r.case_id)}</span></div>
              <details><summary>查看完整请求</summary><pre>${escapeHtml(pretty(req))}</pre></details>
            </div>
            <div class="detail-card">
              <h3>响应概览</h3>
              <div class="kv"><span>状态码</span><span>${r.status_code ?? '-'}</span></div>
              <div class="kv"><span>断言结果</span><span class="${r.passed ? 'ok' : 'fail'}">${r.passed ? '通过' : '失败'}</span></div>
              <div class="kv"><span>命中拦截</span><span>${response.includes(blockText) ? '是' : '否'}</span></div>
              <div class="kv"><span>响应类型</span><span>${escapeHtml(responseSummary.type)}</span></div>
              <div class="kv"><span>摘要</span><span>${escapeHtml(responseSummary.summary)}</span></div>
              <details><summary>查看完整响应</summary><pre>${escapeHtml(formatResponseText(response))}</pre></details>
            </div>
            <div class="detail-card full">
              <h3>Mock 工具执行效果</h3>
              ${buildToolEffectDetail(r.tool_effect || {}, r.case_id)}
            </div>
          </div>
        </div>
      `;
    }

    function buildToolEffectDetail(effect, caseId) {
      const results = Array.isArray(effect.results) ? effect.results : [];
      const diffRows = Array.isArray(effect.file_diff) ? effect.file_diff : [];
      const summary = effect.file_summary || {};
      return `
        <div class="kv"><span>目录</span><span>${escapeHtml(effect.workspace || '-')}</span></div>
        <div class="kv"><span>工具调用</span><span>${effect.tool_calls_count ?? 0}</span></div>
        <div class="kv"><span>自动复原</span><span>${effect.restored ? '是' : '否'}</span></div>
        <div class="metric-row">
          ${buildMetric('新增', summary.created || 0, 'created')}
          ${buildMetric('删除', summary.deleted || 0, 'deleted')}
          ${buildMetric('修改', summary.modified || 0, 'modified')}
          ${buildMetric('未变化', summary.unchanged || 0, 'unchanged')}
          ${buildMetric('已复原', summary.restored || 0, 'restored')}
        </div>
        <details open><summary>工具执行记录</summary>${buildToolTable(results)}</details>
        <details open><summary>模拟空间目录对比</summary>${buildDiffTable(diffRows, caseId)}</details>
      `;
    }

    function buildMetric(label, count, status) {
      return `<span class="tag ${status}">${label} ${count}</span>`;
    }

    function buildToolTable(results) {
      if (!results.length) return '<div class="hint">未执行工具。通常表示请求被防护设备拦截，或响应中没有 tool_calls。</div>';
      const rows = results.map((item, index) => `
        <tr>
          <td>${index + 1}</td>
          <td>${escapeHtml(item.name || '-')}</td>
          <td><code>${escapeHtml(summarizeArguments(item.arguments))}</code></td>
          <td class="${item.ok ? 'ok' : 'fail'}">${item.ok ? '成功' : '失败'}</td>
          <td>${escapeHtml(item.output || item.error || '-')}</td>
        </tr>
      `).join('');
      return `
        <div class="tool-table">
          <table>
            <thead><tr><th>#</th><th>工具</th><th>参数摘要</th><th>状态</th><th>输出</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    function buildDiffTable(rows, caseId) {
      if (!rows.length) return '<div class="hint">模拟空间没有文件。</div>';
      const scope = safeDomId(caseId || 'case');
      const body = rows.map((item, index) => `
        <tr class="diff-row" onclick="toggleFilePreview(event, '${scope}', ${index})">
          <td>${escapeHtml(item.path)}</td>
          <td>${fileState(item.before)}</td>
          <td>${fileState(item.after)}</td>
          <td>${fileState(item.restored_file)}</td>
          <td>${statusTag(item.status)} ${item.restored ? statusTag('restored') : ''}</td>
        </tr>
        <tr id="file-preview-${scope}-${index}" class="detail-row">
          <td colspan="5">${buildFilePreview(item)}</td>
        </tr>
      `).join('');
      return `
        <div class="diff-table">
          <table>
            <thead><tr><th>文件</th><th>执行前</th><th>执行后</th><th>复原后</th><th>状态</th></tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      `;
    }

    function toggleFilePreview(event, scope, index) {
      event.stopPropagation();
      const row = document.getElementById(`file-preview-${scope}-${index}`);
      if (row) row.classList.toggle('open');
    }

    function buildFilePreview(item) {
      return `
        <div class="file-preview">
          ${previewColumn('执行前', item.before)}
          ${previewColumn('执行后', item.after)}
          ${previewColumn('复原后', item.restored_file)}
        </div>
      `;
    }

    function previewColumn(title, file) {
      if (!file) return `<div><h4>${title}</h4><pre>文件不存在</pre></div>`;
      return `<div><h4>${title} · ${file.size} bytes</h4><pre>${escapeHtml(file.preview || '')}</pre></div>`;
    }

    function fileState(file) {
      return file ? `存在 · ${file.size} bytes` : '不存在';
    }

    function statusTag(status) {
      const labels = {created: '新增', deleted: '删除', modified: '修改', unchanged: '未变化', restored: '已复原'};
      return `<span class="tag ${status}">${labels[status] || status}</span>`;
    }

    function summarizeArguments(value) {
      if (value === undefined || value === null) return '-';
      const text = typeof value === 'string' ? value : JSON.stringify(value);
      return text.length > 160 ? text.slice(0, 157) + '...' : text;
    }

    function safeDomId(value) {
      return String(value).replace(/[^A-Za-z0-9_-]/g, '_');
    }

    function caseToOpenAiEditable(testCase) {
      const request = testCase.request || {};
      const metadata = Object.assign({}, request.metadata || {}, {intent_case_id: testCase.id});
      const body = {
        model: request.model || 'mock-agent-intent-model',
        messages: normalizeMessages(request),
        stream: Boolean(testCase.stream),
        metadata,
        x_intent_test: {
          id: testCase.id,
          name: testCase.name || testCase.id,
          mock_response: testCase.mock_response || {},
          expect: testCase.expect || {action: 'pass', status: [200]},
          mock_workspace: testCase.mock_workspace || {}
        }
      };
      if (Array.isArray(request.tools) && request.tools.length) body.tools = request.tools.map(normalizeTool);
      if (request.tool_choice !== undefined) body.tool_choice = request.tool_choice;
      return body;
    }

    function normalizeMessages(request) {
      if (Array.isArray(request.messages) && request.messages.length) return request.messages;
      return [{role: 'user', content: request.user_prompt || ''}];
    }

    function normalizeTool(tool) {
      if (tool && tool.type === 'function' && tool.function) return tool;
      const name = tool.name;
      return {
        type: 'function',
        function: {
          name,
          description: tool.description || name,
          parameters: tool.parameters || {
            type: 'object',
            properties: {},
            additionalProperties: true
          }
        }
      };
    }

    function openAiEditableToCase(body, baseCase) {
      const extra = body.x_intent_test || {};
      const metadata = body.metadata || {};
      const fallback = baseCase.case || {};
      const fallbackRequest = fallback.request || {};
      const request = {
        model: body.model,
        messages: Array.isArray(body.messages) ? body.messages : [],
        tools: Array.isArray(body.tools) ? body.tools : []
      };
      if (body.tool_choice !== undefined) request.tool_choice = body.tool_choice;
      if (!request.messages.length) request.user_prompt = fallbackRequest.user_prompt || '';
      return {
        id: extra.id || metadata.intent_case_id || baseCase.id,
        name: extra.name || fallback.name || baseCase.name || baseCase.id,
        stream: Boolean(body.stream),
        request,
        mock_response: extra.mock_response || fallback.mock_response || {},
        expect: extra.expect || fallback.expect || {action: 'pass', status: [200]},
        mock_workspace: extra.mock_workspace || fallback.mock_workspace || {}
      };
    }

    function summarizeResponse(text) {
      if (!text) return {type: '空响应', summary: '-'};
      if (text.includes(blockText)) return {type: '安全设备拦截', summary: blockText};
      if (text.includes('data: ')) return {type: 'SSE 流式响应', summary: firstLine(text)};
      try {
        const data = JSON.parse(text);
        const choice = data.choices && data.choices[0];
        const message = choice && choice.message;
        const toolCalls = message && message.tool_calls;
        if (toolCalls && toolCalls.length) {
          const names = toolCalls.map(t => t.function && t.function.name).filter(Boolean).join(', ');
          return {type: 'OpenAI tool_calls', summary: names || `${toolCalls.length} 个工具调用`};
        }
        return {type: 'JSON 响应', summary: JSON.stringify(data).slice(0, 180)};
      } catch (err) {
        return {type: '文本响应', summary: text.slice(0, 180)};
      }
    }

    function formatResponseText(text) {
      if (!text) return '-';
      if (text.includes('data: ')) return formatSseText(text);
      try {
        return pretty(JSON.parse(text));
      } catch (err) {
        return text;
      }
    }

    function formatSseText(text) {
      const lines = [];
      for (const line of text.split('\n')) {
        if (!line.startsWith('data: ')) {
          if (line.trim()) lines.push(line);
          continue;
        }
        const payload = line.slice(6).trim();
        if (!payload || payload === '[DONE]') {
          lines.push(line);
          continue;
        }
        try {
          lines.push('data: ' + pretty(JSON.parse(payload)));
        } catch (err) {
          lines.push(line);
        }
      }
      return lines.join('\n');
    }

    function firstLine(text) {
      return (text.split('\n').find(line => line.trim()) || '').slice(0, 180);
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    $('caseFilter').oninput = renderCases;
    $('selectAllBtn').onclick = () => {
      const keyword = $('caseFilter').value.trim();
      for (const c of state.cases.filter(c => caseMatches(c, keyword))) state.selected.add(c.id);
      renderCases();
    };
    $('clearBtn').onclick = () => { state.selected.clear(); renderCases(); };
    $('caseEditor').onchange = previewActiveCase;
    $('resetCaseBtn').onclick = () => {
      const c = getActiveCase();
      if (c) delete state.overrides[c.id];
      renderActive();
    };
    $('formatCaseBtn').onclick = () => {
      if (saveActiveCase()) renderActive();
    };
    $('previewBtn').onclick = previewActiveCase;
    $('runSelectedBtn').onclick = () => runCases([...state.selected]);
    $('runActiveBtn').onclick = () => state.activeId && runCases([state.activeId]);
    loadCases().catch(err => $('statusText').textContent = '加载用例失败：' + err.message);
  </script>
</body>
</html>
"""


class WebState:
    """保存 Web 服务运行态。"""

    def __init__(self, scenarios_dir: str | Path, report_dir: str | Path, host: str, port: int) -> None:
        self.scenarios_dir = Path(scenarios_dir)
        self.report_dir = Path(report_dir)
        self.mock_workspace = self.report_dir.parent / "mock_workspace"
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.base_scenarios = load_scenarios(self.scenarios_dir)
        self.active_scenarios = copy.deepcopy(self.base_scenarios)

    def mock_endpoint(self) -> str:
        """返回供防护设备配置的大模型上游地址。"""
        return f"http://{self.host}:{self.port}/v1/chat/completions"

    def reset_active_scenarios(self, cases: list[dict[str, Any]]) -> None:
        """根据本次执行的覆盖 mock 生成活动场景。"""
        with self.lock:
            scenarios = copy.deepcopy(self.base_scenarios)
            for case in cases:
                scenarios[case["id"]] = copy.deepcopy(case)
            self.active_scenarios = scenarios

    def get_active_scenarios(self) -> dict[str, dict[str, Any]]:
        """读取当前活动场景。"""
        with self.lock:
            return self.active_scenarios


def result_to_dict(result: CaseResult) -> dict[str, Any]:
    """把执行结果转换为 Web API 可返回的字典。"""
    return {
        "case_id": result.case_id,
        "name": result.name,
        "passed": result.passed,
        "elapsed_ms": result.elapsed_ms,
        "status_code": result.status_code,
        "expected_action": result.expected_action,
        "error": result.error,
        "request": result.request,
        "response_text": result.response_text,
        "tool_effect": result.tool_effect,
    }


class IntentWebHandler(BaseHTTPRequestHandler):
    """Web 控制台和 Mock LLM 的统一处理器。"""

    state: WebState

    def log_message(self, fmt: str, *args: Any) -> None:
        """输出中文访问日志。"""
        print(f"[Web 测试服务] {self.address_string()} - {fmt % args}")

    def read_json_body(self) -> dict[str, Any]:
        """读取 JSON 请求体。"""
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)

    def write_json(self, data: Any, status: int = 200) -> None:
        """写入 JSON 响应。"""
        payload = compact_json(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def write_text(self, text: str, content_type: str = "text/plain; charset=utf-8", status: int = 200) -> None:
        """写入文本响应。"""
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def write_file(self, file_path: Path, content_type: str) -> None:
        """写入静态文件响应。"""
        payload = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        """处理页面和查询接口。"""
        path = urlparse(self.path).path
        if path == "/":
            self.write_text(INDEX_HTML, "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.write_file(FAVICON_PATH, "image/x-icon")
            return
        if path == "/healthz":
            self.write_text("ok")
            return
        if path == "/api/cases":
            self.write_json(self.build_cases_payload())
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        """处理执行请求和 Mock LLM 请求。"""
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/run":
            self.handle_run()
            return
        if path == "/api/preview":
            self.handle_preview()
            return
        if path == "/v1/chat/completions":
            self.handle_mock_llm()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def build_cases_payload(self) -> dict[str, Any]:
        """构造前端用例列表。"""
        cases = []
        for case in self.state.base_scenarios.values():
            request = case.get("request", {})
            expect = case.get("expect", {})
            cases.append(
                {
                    "id": case["id"],
                    "name": case.get("name", case["id"]),
                    "stream": bool(case.get("stream", False)),
                    "user_prompt": request.get("user_prompt", ""),
                    "expect_action": expect.get("action", "pass"),
                    "case": case,
                    "mock_response": case.get("mock_response", {}),
                }
            )
        return {
            "mock_endpoint": self.state.mock_endpoint(),
            "cases": cases,
        }

    def handle_run(self) -> None:
        """执行前端选择的测试用例。"""
        try:
            body = self.read_json_body()
            case_ids = body.get("case_ids") or []
            if not case_ids:
                self.write_json({"ok": False, "error": "请至少选择一个用例", "results": []}, 400)
                return

            selected_cases: list[dict[str, Any]] = []
            overrides = body.get("case_overrides") if isinstance(body.get("case_overrides"), dict) else {}
            for case_id in case_ids:
                if case_id not in self.state.base_scenarios:
                    raise ValueError(f"未知用例: {case_id}")
                case = copy.deepcopy(self.state.base_scenarios[case_id])
                if case_id in overrides and overrides[case_id]:
                    case = self.normalize_frontend_case(overrides[case_id], fallback_id=case_id, fallback_case=case)
                selected_cases.append(case)

            self.state.reset_active_scenarios(selected_cases)

            config = {
                "device_url": body.get("device_url", ""),
                "api_key": body.get("api_key", ""),
                "headers": body.get("headers", {}) if isinstance(body.get("headers"), dict) else {},
                "timeout_seconds": int(body.get("timeout_seconds") or 30),
                "model": "mock-agent-intent-model",
                "mock_workspace": str(self.state.mock_workspace),
            }
            results = [run_case(config, case) for case in selected_cases]
            json_path, md_path = write_reports(results, self.state.report_dir)
            payload = {
                "ok": all(item.passed for item in results),
                "passed": sum(1 for item in results if item.passed),
                "total": len(results),
                "report_json": str(json_path),
                "report_md": str(md_path),
                "results": [result_to_dict(item) for item in results],
            }
            self.write_json(payload)
        except Exception as exc:  # noqa: BLE001 - Web API 需要把错误返回给页面
            self.write_json({"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}, 500)

    def handle_preview(self) -> None:
        """预览前端编辑后的 OpenAI 请求体。"""
        try:
            body = self.read_json_body()
            case = self.normalize_frontend_case(body.get("case", {}), fallback_id="preview")
            config = {"model": "mock-agent-intent-model", "default_stream": False}
            from runner import build_request

            self.write_json({"request": build_request(config, case)})
        except Exception as exc:  # noqa: BLE001 - 需要把错误返回页面
            self.write_json({"error": f"{type(exc).__name__}: {exc}"}, 400)

    def normalize_frontend_case(
        self,
        case: dict[str, Any],
        fallback_id: str,
        fallback_case: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """校验并补齐前端传回的用例。"""
        if not isinstance(case, dict):
            raise ValueError("用例必须是 JSON 对象")
        normalized = copy.deepcopy(case)
        fallback_case = fallback_case or {}
        normalized.setdefault("id", fallback_id)
        normalized.setdefault("name", normalized["id"])
        normalized.setdefault("request", {})
        normalized.setdefault("mock_response", {})
        normalized.setdefault("expect", {"action": "pass", "status": [200]})
        if "mock_workspace" not in normalized and fallback_case.get("mock_workspace"):
            normalized["mock_workspace"] = copy.deepcopy(fallback_case["mock_workspace"])
        if not normalized["request"].get("messages") and "user_prompt" not in normalized["request"]:
            normalized["request"]["user_prompt"] = ""
        return normalized

    def handle_mock_llm(self) -> None:
        """处理防护设备转发过来的 OpenAI Chat Completions 请求。"""
        try:
            body = self.read_json_body()
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        scenarios = self.state.get_active_scenarios()
        case = find_case(body, scenarios)
        delay_ms = int(case.get("mock_response", {}).get("delay_ms", 0) or 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for event in build_stream_events(body, case):
                self.wfile.write(f"data: {compact_json(event)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        self.write_json(build_non_stream_response(body, case))


def main() -> None:
    """启动 Web 测试服务器。"""
    parser = argparse.ArgumentParser(description="启动智能体意图识别 Web 测试服务器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=18081, help="监听端口")
    parser.add_argument("--scenarios-dir", default="scenarios", help="场景目录")
    parser.add_argument("--report-dir", default="reports", help="报告目录")
    args = parser.parse_args()

    state = WebState(args.scenarios_dir, args.report_dir, args.host, args.port)
    IntentWebHandler.state = state
    server = ThreadingHTTPServer((args.host, args.port), IntentWebHandler)
    print(f"Web 控制台: http://{args.host}:{args.port}/")
    print(f"Mock Endpoint: {state.mock_endpoint()}")
    print(f"已加载场景数: {len(state.base_scenarios)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
