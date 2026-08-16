#!/usr/bin/env python3
"""Build a deterministic PDF2zh XPI with an in-window progress card.

The input is intentionally pinned to the exact upstream 4.0.3 XPI shipped in
``windows/payload``.  This is a fail-closed source transformation: an unknown,
modified, or already-patched input is rejected instead of being partially
rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "windows" / "payload" / "zotero-pdf2zh-4.0.3.xpi"
SCRIPT_NAME = "content/scripts/pdf2zh.js"
EXPECTED_INPUT_SHA256 = (
    "31a7d73f67096dcfd1640012cad391a8898da78aef62782b91bb9b9f153cd8fc"
)
EXPECTED_ADDON_ID = "pdf2zh@guaguastandup.com"
EXPECTED_VERSION = "4.0.3"
PATCHED_VERSION = "4.0.3.3"
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
PATCH_MARKER = "zotero-bilingual-main-window-progress-card-v1"


class PatchError(RuntimeError):
    """Raised when a safe, complete transformation cannot be guaranteed."""


FILE_PROCESSOR_PATCH = r'''  // src/modules/pdf2zhFileProcessor.ts
  var FileProcessor = class _FileProcessor {
    constructor() {
      this.eventListeners = [];
    }
    static getInstance() {
      if (!_FileProcessor.instance) {
        _FileProcessor.instance = new _FileProcessor();
      }
      return _FileProcessor.instance;
    }
    addEventListener(listener) {
      this.eventListeners.push(listener);
      return () => {
        this.eventListeners = this.eventListeners.filter((item) => item !== listener);
      };
    }
    emit(event, data) {
      this.eventListeners.forEach((listener) => {
        try {
          listener(event, data);
        } catch (error) {
          ztoolkit.log(`事件监听器错误:`, error);
        }
      });
    }
    // 批量任务保持顺序执行，并区分成功、重复提交和失败。
    async processBatch(tasks) {
      this.emit("batchStarted", { totalTasks: tasks.length });
      let succeeded = 0;
      let failed = 0;
      let duplicates = 0;
      for (const task of tasks) {
        try {
          const outcome = await PDF2zhHelperFactory.processSingleFile(task);
          if (outcome === "duplicate") duplicates++;
          else succeeded++;
        } catch (error) {
          failed++;
        }
      }
      const result = {
        total: tasks.length,
        succeeded,
        failed,
        duplicates
      };
      this.emit("batchCompleted", result);
      return result;
    }
  };

'''


HELPER_PATCH = r'''  // src/modules/pdf2zhHelper.ts
  // zotero-bilingual patch: zotero-bilingual-main-window-progress-card-v1
  var PDF2ZH_PROGRESS_STYLE_ID = "pdf2zh-main-window-progress-style";
  var PDF2ZH_PROGRESS_STACK_ID = "pdf2zh-main-window-progress-stack";
  var PDF2ZH_PROGRESS_HTML_NS = "http://www.w3.org/1999/xhtml";
  var pdf2zhTaskCards = /* @__PURE__ */ new Set();
  var pdf2zhTaskMonitors = /* @__PURE__ */ new Set();
  function pdf2zhProgressElement(document2, tag, className = "") {
    const element = document2.createElementNS(PDF2ZH_PROGRESS_HTML_NS, tag);
    if (className) element.className = className;
    return element;
  }
  function pdf2zhFormatElapsed(milliseconds) {
    const totalSeconds = Math.max(0, Math.floor(milliseconds / 1e3));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }
  function pdf2zhStage(message) {
    const text = String(message || "").trim();
    if (!text || /^translate\s+\d+\/\d+$/i.test(text)) return null;
    if (/附加|attach|download/i.test(text)) return { rank: 5, label: "正在附加到 Zotero" };
    if (/Typesetting|Add Fonts|drawing instructions|排版|渲染|整理|生成双语/i.test(text)) {
      return { rank: 4, label: "正在生成双栏 PDF" };
    }
    if (/Translate Paragraphs|翻译正文|正在翻译/i.test(text)) {
      return { rank: 3, label: "正在翻译正文" };
    }
    if (/Automatic Term Extraction|术语/i.test(text)) {
      return { rank: 2, label: "正在整理术语" };
    }
    if (/Parse|DetectScanned|Page Layout|Paragraphs|Formulas|解析|扫描|版面|段落|公式/i.test(text)) {
      return { rank: 1, label: "正在解析 PDF" };
    }
    if (/接收|上传|初始化|prepare|start/i.test(text)) {
      return { rank: 0, label: "正在准备 PDF" };
    }
    return null;
  }
  var PDF2zhTaskCard = class _PDF2zhTaskCard {
    constructor(fileName, endpoint = "translate", win = Zotero.getMainWindow()) {
      this.fileName = String(fileName || "PDF");
      this.endpoint = endpoint;
      this.startedAt = Date.now();
      this.stageRank = -1;
      this.stageLabel = "正在准备 PDF";
      this.dismissed = false;
      this.elapsedTimer = void 0;
      this.closeTimer = void 0;
      this.monitorStops = /* @__PURE__ */ new Set();
      this.win = win || Zotero.getMainWindow();
      this.node = null;
      if (!this.win?.document?.documentElement) return;
      const document2 = this.win.document;
      _PDF2zhTaskCard.ensureStyle(document2);
      const stack = _PDF2zhTaskCard.ensureStack(document2);
      this.node = pdf2zhProgressElement(document2, "section", "pdf2zh-task-card");
      this.node.dataset.state = "running";
      this.node.setAttribute("role", "status");
      this.node.setAttribute("aria-live", "polite");
      const header = pdf2zhProgressElement(document2, "div", "pdf2zh-task-header");
      this.titleNode = pdf2zhProgressElement(document2, "div", "pdf2zh-task-title");
      const closeButton = pdf2zhProgressElement(document2, "button", "pdf2zh-task-close");
      closeButton.type = "button";
      closeButton.textContent = "×";
      closeButton.title = "关闭提示（任务会继续运行）";
      closeButton.setAttribute("aria-label", "关闭进度提示，翻译任务继续运行");
      closeButton.addEventListener("click", () => this.close());
      header.append(this.titleNode, closeButton);
      this.fileNode = pdf2zhProgressElement(document2, "div", "pdf2zh-task-file");
      this.fileNode.textContent = this.fileName;
      this.fileNode.title = this.fileName;
      const statusRow = pdf2zhProgressElement(document2, "div", "pdf2zh-task-status-row");
      this.stageNode = pdf2zhProgressElement(document2, "div", "pdf2zh-task-stage");
      this.elapsedNode = pdf2zhProgressElement(document2, "div", "pdf2zh-task-elapsed");
      statusRow.append(this.stageNode, this.elapsedNode);
      const track = pdf2zhProgressElement(document2, "div", "pdf2zh-task-track");
      track.setAttribute("aria-hidden", "true");
      this.barNode = pdf2zhProgressElement(document2, "div", "pdf2zh-task-bar");
      track.appendChild(this.barNode);
      this.node.append(header, this.fileNode, statusRow, track);
      stack.appendChild(this.node);
      pdf2zhTaskCards.add(this);
      this.render("running", endpoint === "translate" ? "正在翻译 PDF" : "正在处理 PDF", this.stageLabel);
      this.elapsedTimer = this.win.setInterval(() => this.updateElapsed(), 1e3);
    }
    static ensureStyle(document2) {
      if (document2.getElementById(PDF2ZH_PROGRESS_STYLE_ID)) return;
      const style = pdf2zhProgressElement(document2, "style");
      style.id = PDF2ZH_PROGRESS_STYLE_ID;
      style.textContent = `
        #${PDF2ZH_PROGRESS_STACK_ID} {
          position: fixed;
          inset-inline-end: 24px;
          inset-block-end: 24px;
          z-index: 100;
          display: flex;
          width: 336px;
          flex-direction: column;
          gap: 10px;
          color-scheme: light dark;
          pointer-events: none;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card {
          box-sizing: border-box;
          width: 336px;
          padding: 14px 16px 15px;
          overflow: hidden;
          color: var(--fill-primary, rgba(0, 0, 0, .85));
          background: var(--material-background, #fff);
          border: var(--material-border-quarternary, 1px solid rgba(0, 0, 0, .1));
          border-radius: 8px;
          box-shadow: 0 4px 18px rgba(0, 0, 0, .16);
          font-family: system-ui, -apple-system, sans-serif;
          font-size: 13px;
          line-height: 1.3333;
          pointer-events: auto;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-header,
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-status-row {
          display: flex;
          align-items: center;
          min-width: 0;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-header {
          min-height: 24px;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-title {
          min-width: 0;
          flex: 1;
          font-weight: 600;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-close {
          width: 24px;
          height: 24px;
          padding: 0;
          margin: -4px -6px -4px 8px;
          color: var(--fill-secondary, rgba(0, 0, 0, .55));
          background: transparent;
          border: 0;
          border-radius: 5px;
          font: inherit;
          font-size: 18px;
          line-height: 22px;
          cursor: pointer;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-close:hover {
          color: var(--fill-primary, rgba(0, 0, 0, .85));
          background: var(--fill-quinary, rgba(0, 0, 0, .05));
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-close:focus-visible {
          outline: 2px solid var(--accent-blue, #4072e5);
          outline-offset: 1px;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-file {
          margin-top: 3px;
          overflow: hidden;
          color: var(--fill-secondary, rgba(0, 0, 0, .55));
          font-size: 12px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-status-row {
          gap: 12px;
          margin-top: 11px;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-stage {
          min-width: 0;
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-elapsed {
          flex: none;
          color: var(--fill-secondary, rgba(0, 0, 0, .55));
          font-size: 12px;
          font-variant-numeric: tabular-nums;
          white-space: nowrap;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-track {
          position: relative;
          height: 5px;
          margin-top: 9px;
          overflow: hidden;
          background: var(--fill-quinary, rgba(0, 0, 0, .05));
          border-radius: 999px;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-bar {
          position: absolute;
          inset-block: 0;
          left: 0;
          width: 42%;
          background: var(--accent-blue, #4072e5);
          border-radius: inherit;
          animation: pdf2zh-task-indeterminate 1.35s ease-in-out infinite;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="success"] .pdf2zh-task-bar,
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="error"] .pdf2zh-task-bar,
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="info"] .pdf2zh-task-bar {
          width: 100%;
          transform: none;
          animation: none;
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="success"] .pdf2zh-task-bar {
          background: var(--accent-green, #39bf68);
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="error"] .pdf2zh-task-bar {
          background: var(--accent-red, #db2c3a);
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="info"] .pdf2zh-task-bar {
          background: var(--fill-tertiary, rgba(0, 0, 0, .25));
        }
        #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="error"] .pdf2zh-task-stage {
          color: var(--accent-red, #db2c3a);
          white-space: normal;
        }
        @keyframes pdf2zh-task-indeterminate {
          from { transform: translateX(-115%); }
          to { transform: translateX(275%); }
        }
        @media (prefers-reduced-motion: reduce) {
          #${PDF2ZH_PROGRESS_STACK_ID} .pdf2zh-task-card[data-state="running"] .pdf2zh-task-bar {
            left: 29%;
            animation: none;
          }
        }
      `;
      document2.documentElement.appendChild(style);
    }
    static ensureStack(document2) {
      let stack = document2.getElementById(PDF2ZH_PROGRESS_STACK_ID);
      if (stack) return stack;
      stack = pdf2zhProgressElement(document2, "div");
      stack.id = PDF2ZH_PROGRESS_STACK_ID;
      const host = document2.getElementById("zotero-pane-stack") || document2.documentElement;
      const overlay = document2.getElementById("zotero-pane-overlay");
      if (overlay?.parentNode === host) host.insertBefore(stack, overlay);
      else host.appendChild(stack);
      return stack;
    }
    static cleanupWindow(win) {
      for (const card of [...pdf2zhTaskCards]) {
        if (card.win === win) card.close();
      }
      win?.document?.getElementById(PDF2ZH_PROGRESS_STACK_ID)?.remove();
      win?.document?.getElementById(PDF2ZH_PROGRESS_STYLE_ID)?.remove();
    }
    static cleanupAll() {
      for (const win of Zotero.getMainWindows()) this.cleanupWindow(win);
      for (const card of [...pdf2zhTaskCards]) card.close();
      for (const stop of [...pdf2zhTaskMonitors]) stop();
    }
    updateElapsed() {
      if (!this.elapsedNode || this.dismissed) return;
      if (this.win?.closed || !this.node?.isConnected) {
        this.close();
        return;
      }
      this.elapsedNode.textContent = `已用 ${pdf2zhFormatElapsed(Date.now() - this.startedAt)}`;
    }
    render(state, title, stage) {
      if (!this.node || this.dismissed) return;
      if (this.win?.closed || !this.node.isConnected) {
        this.close();
        return;
      }
      this.node.dataset.state = state;
      this.titleNode.textContent = title;
      this.stageNode.textContent = stage;
      this.stageNode.title = stage;
      this.updateElapsed();
    }
    updateStage(message) {
      const stage = pdf2zhStage(message);
      if (!stage || stage.rank < this.stageRank || this.dismissed) return;
      this.stageRank = stage.rank;
      this.stageLabel = stage.label;
      this.render("running", this.endpoint === "translate" ? "正在翻译 PDF" : "正在处理 PDF", stage.label);
    }
    setAttaching() {
      if (this.stageRank > 5 || this.dismissed) return;
      this.stageRank = 5;
      this.stageLabel = "正在附加到 Zotero";
      this.render("running", "正在翻译 PDF", this.stageLabel);
    }
    succeed() {
      if (this.dismissed) return;
      this.stopElapsedTimer();
      this.render("success", "翻译完成", "双栏 PDF 已附加到 Zotero");
      this.closeTimer = this.win?.setTimeout(() => this.close(), 4e3);
    }
    fail(error) {
      if (this.dismissed) return;
      this.stopElapsedTimer();
      const message = String(error?.message || error || "请查看 PDF2zh 服务日志").replace(/\s+/g, " ").trim();
      this.render("error", "翻译失败", message || "请查看 PDF2zh 服务日志");
    }
    duplicate() {
      if (this.dismissed) return;
      this.stopElapsedTimer();
      this.render("info", "PDF 已在处理", "无需重复提交，原任务会继续运行");
      this.closeTimer = this.win?.setTimeout(() => this.close(), 5e3);
    }
    batchSummary(data) {
      if (this.dismissed) return;
      this.stopElapsedTimer();
      const failed = Number(data?.failed || 0);
      const duplicates = Number(data?.duplicates || 0);
      const succeeded = Number(data?.succeeded || 0);
      const stage = `成功 ${succeeded} · 已在处理 ${duplicates} · 失败 ${failed}`;
      this.render(failed ? "error" : "success", "批量处理完成", stage);
      if (!failed) this.closeTimer = this.win?.setTimeout(() => this.close(), 5e3);
    }
    stopElapsedTimer() {
      if (this.elapsedTimer !== void 0) {
        this.win?.clearInterval(this.elapsedTimer);
        this.elapsedTimer = void 0;
      }
    }
    addMonitorStop(stop) {
      if (this.dismissed) stop();
      else this.monitorStops.add(stop);
    }
    removeMonitorStop(stop) {
      this.monitorStops.delete(stop);
    }
    stopMonitors() {
      for (const stop of [...this.monitorStops]) stop();
      this.monitorStops.clear();
    }
    close() {
      if (this.dismissed) return;
      this.dismissed = true;
      this.stopElapsedTimer();
      this.stopMonitors();
      if (this.closeTimer !== void 0) this.win?.clearTimeout(this.closeTimer);
      this.node?.remove();
      pdf2zhTaskCards.delete(this);
    }
  };
  var PDF2zhHelperFactory = class {
    static {
      // 添加重试配置(其实不需要重试)
      this.MAX_RETRIES = 1;
    }
    static {
      this.RETRY_DELAY = 2e3;
    }
    static {
      this.activeTaskKeys = /* @__PURE__ */ new Set();
    }
    static getTaskKey(filepath, endpoint) {
      const normalizedPath = String(filepath || "").replace(/\\/g, "/").trim().toLocaleLowerCase();
      return `${endpoint}:${normalizedPath}`;
    }
    static startProgressMonitor(fileName, config2, card) {
      if (card.endpoint !== "translate") return () => {};
      const serverUrl = String(config2.serverUrl || "").replace(/\/$/, "");
      const EventSourceCtor = card.win?.EventSource || ztoolkit.getGlobal("EventSource");
      if (!serverUrl || typeof EventSourceCtor !== "function") return () => {};
      let source;
      let stopped = false;
      let taskId = "";
      const localStartedAt = card.startedAt;
      const normalizedFileName = String(fileName).trim().toLocaleLowerCase();
      const stop = () => {
        if (stopped) return;
        stopped = true;
        try {
          if (source) {
            source.onmessage = null;
            source.onerror = null;
          }
          source?.close();
        } catch (error) {
        }
        card.removeMonitorStop(stop);
        pdf2zhTaskMonitors.delete(stop);
      };
      try {
        source = new EventSourceCtor(`${serverUrl}/events`);
        source.onmessage = (event) => {
          if (stopped) return;
          if (card.dismissed) {
            stop();
            return;
          }
          try {
            const payload = JSON.parse(event.data);
            const tasks = payload?.type === "tasks" && Array.isArray(payload.data) ? payload.data : [];
            let task = null;
            if (taskId) {
              task = tasks.find((candidate) => candidate.taskId === taskId);
              if (!task) return;
            } else {
              const candidates = tasks.filter((candidate) => {
                if (String(candidate.fileName || "").trim().toLocaleLowerCase() !== normalizedFileName) return false;
                if (candidate.active === false) return false;
                const startedAt = Date.parse(candidate.startTime || "");
                return Number.isFinite(startedAt) && startedAt >= localStartedAt - 5e3;
              }).sort((left, right) => String(right.startTime || "").localeCompare(String(left.startTime || "")));
              task = candidates[0];
              if (task?.taskId) taskId = task.taskId;
            }
            if (!task) return;
            if (task.active === false) {
              if (task.status === "失败") card.fail(task.message);
              else card.setAttaching();
              return;
            }
            card.updateStage(task.message || task.status);
          } catch (error) {
            ztoolkit.log("无法读取 PDF2zh 任务进度:", error);
          }
        };
        source.onerror = () => {
          if (!stopped && card.stageRank < 0) card.updateStage("正在初始化");
        };
        pdf2zhTaskMonitors.add(stop);
        card.addMonitorStop(stop);
      } catch (error) {
        ztoolkit.log("无法连接 PDF2zh 任务进度:", error);
      }
      return stop;
    }
    // **** 由hooks.ts调用, main entries *****
    static async processWorker(endpoint) {
      const pane = ztoolkit.getGlobal("ZoteroPane");
      const sourceWin = pane?.document?.defaultView || Zotero.getMainWindow();
      const selectedItems = pane.getSelectedItems();
      if (selectedItems.length == 0) {
        ztoolkit.getGlobal("alert")("请先选择一个条目或附件。");
        return;
      }
      const tasks = [];
      const duplicateNames = [];
      for (const item of selectedItems) {
        try {
          const filepath = await this.validatePDFAttachment(item);
          const fileName = PathUtils.filename(filepath);
          const config2 = this.getServerConfig();
          const taskKey = this.getTaskKey(filepath, endpoint);
          if (this.activeTaskKeys.has(taskKey)) {
            duplicateNames.push(fileName);
            continue;
          }
          this.activeTaskKeys.add(taskKey);
          tasks.push({
            fileName,
            item,
            config: config2,
            endpoint,
            taskKey,
            win: sourceWin
          });
        } catch (error) {
          const card = new PDF2zhTaskCard("所选 PDF", endpoint, sourceWin);
          card.fail(error);
        }
      }
      if (tasks.length === 0 && duplicateNames.length > 0) {
        if (duplicateNames.length === 1) {
          const card = new PDF2zhTaskCard(duplicateNames[0], endpoint, sourceWin);
          card.duplicate();
        } else {
          const summary = new PDF2zhTaskCard(`${duplicateNames.length} 个 PDF`, endpoint, sourceWin);
          summary.batchSummary({ succeeded: 0, failed: 0, duplicates: duplicateNames.length });
        }
        return;
      }
      const fileProcessor = FileProcessor.getInstance();
      const result = await fileProcessor.processBatch(tasks);
      const batchResult = {
        ...result,
        total: result.total + duplicateNames.length,
        duplicates: result.duplicates + duplicateNames.length
      };
      if (batchResult.total > 1) {
        const summary = new PDF2zhTaskCard(`${batchResult.total} 个 PDF`, endpoint, sourceWin);
        summary.batchSummary(batchResult);
      }
    }
    // 处理单个文件
    static async processSingleFile(params) {
      const { fileName, item, config: config2, endpoint, taskKey, win } = params;
      ztoolkit.log(
        `Processing Single File: ${fileName}, ServerConfig: ${config2}`
      );
      const card = new PDF2zhTaskCard(fileName, endpoint, win);
      const stopProgressMonitor = this.startProgressMonitor(fileName, config2, card);
      try {
        const fileData = await this.prepareFileData(item);
        const response = await this.retryOperation(
          () => this.sendRequest(fileData, config2, endpoint)
        );
        if (response.status === "duplicate") {
          card.duplicate();
          return "duplicate";
        }
        if (response.status !== "success") {
          throw new Error(response.message || "PDF2zh 服务返回了未知状态");
        }
        if (!Array.isArray(response.fileList) || response.fileList.length === 0) {
          throw new Error("PDF2zh 服务未返回可附加的 PDF");
        }
        card.setAttaching();
        await this.handleResponse(response, item, config2);
        card.succeed();
        return "success";
      } catch (error) {
        ztoolkit.log(`处理单个文件失败: ${fileName}, 错误: ${error}`);
        card.fail(error);
        throw error;
      } finally {
        stopProgressMonitor();
        this.activeTaskKeys.delete(taskKey);
      }
    }
'''


SEND_REQUEST_PATCH = r'''    static async sendRequest(fileData, config2, endpoint) {
      return this.retryOperation(async () => {
        let llmApiConfig;
        if (config2.engine == "pdf2zh") {
          llmApiConfig = this.getActiveLLMApiConfig(config2.service);
        } else {
          llmApiConfig = this.getActiveLLMApiConfig(config2.next_service);
        }
        const requestBody = {
          fileName: fileData.fileName,
          fileContent: fileData.base64,
          ...config2
          // 发送config数据
        };
        ztoolkit.log("server config: ", config2);
        if (llmApiConfig) {
          requestBody.llm_api = llmApiConfig;
          ztoolkit.log("llmApiConfig", llmApiConfig);
        }
        const response = await fetch(`${config2.serverUrl}/${endpoint}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody)
        });
        let result;
        try {
          result = await response.json();
        } catch (error) {
          throw new Error(`PDF2zh 服务返回 HTTP ${response.status}`);
        }
        if (result?.errorType === "duplicate_task") {
          return {
            status: "duplicate",
            activeTask: result.activeTask || {},
            message: result.message || "该 PDF 已在后台处理"
          };
        }
        if (!response.ok || result?.status === "error") {
          throw new Error(result?.message || `PDF2zh 服务返回 HTTP ${response.status}`);
        }
        return result;
      });
    }
'''


ADD_ATTACHMENT_PATCH = r'''    static async addAttachment(params) {
      const { item, filePath, options, type, service } = params;
      const parentItemID = this.getParentItemID(item);
      let targetItem = item;
      if (item.isAttachment() && parentItemID) {
        targetItem = Zotero.Items.get(parentItemID);
      }
      let newTitle = service + "-" + type;
      const shortTitle = targetItem.getField("shortTitle");
      if (shortTitle && shortTitle.length > 0) {
        newTitle = shortTitle + "-" + service + "-" + type;
      }
      if (targetItem.isRegularItem?.()) {
        const incoming = await IOUtils.stat(filePath);
        for (const attachmentID of targetItem.getAttachments()) {
          const existing = Zotero.Items.get(attachmentID);
          if (!existing || existing.attachmentContentType !== "application/pdf") continue;
          if (existing.getField("title") !== (options.rename ? newTitle : PathUtils.filename(filePath))) continue;
          const existingPath = await existing.getFilePathAsync?.();
          if (!existingPath || !await this.safeExists(existingPath)) continue;
          const current = await IOUtils.stat(existingPath);
          if (current.size !== incoming.size) continue;
          ztoolkit.log(`\u5DF2\u6709\u76F8\u540C PDF \u9644\u4EF6\uFF0C\u8DF3\u8FC7\u91CD\u590D\u5BFC\u5165: ${newTitle}`);
          if (options.openAfterProcess && existing.id) {
            Zotero.Reader.open(existing.id);
          }
          return existing;
        }
      }
      const attachment = await Zotero.Attachments.importFromFile({
        file: filePath,
        parentItemID: parentItemID == void 0 ? void 0 : parentItemID,
        libraryID: item.libraryID,
        collections: parentItemID == void 0 ? this.getCollections(item) : void 0,
        title: options.rename ? newTitle : PathUtils.filename(filePath)
      });
      if (options.openAfterProcess && attachment?.id) {
        Zotero.Reader.open(attachment.id);
      }
      return attachment;
    }
'''


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _replace_block(
    source: str,
    *,
    start_marker: str,
    end_marker: str,
    expected_block_sha256: str,
    replacement: str,
    label: str,
) -> str:
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        raise PatchError(f"{label}: expected exactly one start and end marker")
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    original = source[start:end]
    actual = sha256_bytes(original.encode("utf-8"))
    if actual != expected_block_sha256:
        raise PatchError(
            f"{label}: source block changed (expected {expected_block_sha256}, got {actual})"
        )
    return source[:start] + replacement + source[end:]


def _replace_exact_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one exact anchor, found {count}")
    return source.replace(old, new, 1)


def patch_javascript(source: str) -> str:
    """Return the fully patched upstream JavaScript or fail without output."""

    if PATCH_MARKER in source:
        raise PatchError("input JavaScript is already patched")

    source = _replace_block(
        source,
        start_marker="  // src/modules/pdf2zhFileProcessor.ts\n",
        end_marker="  // src/modules/llmApiManager.ts\n",
        expected_block_sha256=(
            "50a309a5f7329dcc8d81da086507d5e84ebcf2074f89fea68d7fcc2ce8780ea7"
        ),
        replacement=FILE_PROCESSOR_PATCH,
        label="FileProcessor",
    )
    source = _replace_block(
        source,
        start_marker="  // src/modules/pdf2zhHelper.ts\n",
        end_marker="    // 准备文件数据\n",
        expected_block_sha256=(
            "3ac6767b9fff49269280e09f6572c51baadde84732f50fbed6d358150bef30b0"
        ),
        replacement=HELPER_PATCH,
        label="PDF2zhHelperFactory",
    )
    source = _replace_block(
        source,
        start_marker="    static async sendRequest(fileData, config2, endpoint) {\n",
        end_marker="    static async handleResponse(response, item, config2) {\n",
        expected_block_sha256=(
            "40e948d72148a8d129ab65d7131f55e4448194af96a0a8fd81a54d5bc09b8547"
        ),
        replacement=SEND_REQUEST_PATCH,
        label="sendRequest",
    )
    source = _replace_block(
        source,
        start_marker="    static async addAttachment(params) {\n",
        end_marker="    // ************* Config *************\n",
        expected_block_sha256=(
            "3034943f0c5c8ee3bd46313e045f324f886b4afb76c8a565df9eaa270e2d899e"
        ),
        replacement=ADD_ATTACHMENT_PATCH,
        label="addAttachment",
    )
    source = _replace_exact_once(
        source,
        "  async function onMainWindowUnload(win) {\n    ztoolkit.unregisterAll();",
        "  async function onMainWindowUnload(win) {\n    PDF2zhTaskCard.cleanupWindow(win);\n    ztoolkit.unregisterAll();",
        "main-window unload cleanup",
    )
    source = _replace_exact_once(
        source,
        "  function onShutdown() {\n    ztoolkit.unregisterAll();",
        "  function onShutdown() {\n    PDF2zhTaskCard.cleanupAll();\n    ztoolkit.unregisterAll();",
        "add-on shutdown cleanup",
    )

    checks = {
        PATCH_MARKER: 1,
        "PDF2ZH_PROGRESS_STACK_ID": 27,
        "new EventSourceCtor": 1,
        "关闭提示（任务会继续运行）": 1,
        "正在处理PDF文件": 0,
        "targetItem.getAttachments()": 1,
    }
    for needle, expected in checks.items():
        count = source.count(needle)
        if count != expected:
            raise PatchError(
                f"post-patch check failed for {needle!r}: expected {expected}, got {count}"
            )
    return source


def _validate_archive_names(infos: list[zipfile.ZipInfo]) -> None:
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise PatchError("input XPI contains duplicate archive member names")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise PatchError(f"unsafe archive member name: {name!r}")
    for required in ("manifest.json", SCRIPT_NAME):
        if names.count(required) != 1:
            raise PatchError(f"input XPI must contain exactly one {required}")


def _zip_info(name: str, is_directory: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED if is_directory else zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o40755 if is_directory else 0o100644
    info.external_attr = (mode & 0xFFFF) << 16
    if is_directory:
        info.external_attr |= 0x10
    return info


def build_patched_xpi(input_path: Path, output_path: Path) -> str:
    """Patch ``input_path`` into ``output_path`` and return its SHA-256."""

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise PatchError("input and output XPI paths must differ")
    if not input_path.is_file():
        raise PatchError(f"input XPI does not exist: {input_path}")

    input_bytes = input_path.read_bytes()
    input_hash = sha256_bytes(input_bytes)
    if input_hash != EXPECTED_INPUT_SHA256:
        raise PatchError(
            "unsupported input XPI: expected upstream 4.0.3 SHA-256 "
            f"{EXPECTED_INPUT_SHA256}, got {input_hash}"
        )

    try:
        with zipfile.ZipFile(io.BytesIO(input_bytes), "r") as source_archive:
            infos = source_archive.infolist()
            _validate_archive_names(infos)
            contents = {info.filename: source_archive.read(info) for info in infos}
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise PatchError(f"cannot read input XPI: {error}") from error

    try:
        manifest = json.loads(contents["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PatchError(f"invalid manifest.json: {error}") from error
    addon_id = manifest.get("applications", {}).get("zotero", {}).get("id")
    if addon_id != EXPECTED_ADDON_ID or manifest.get("version") != EXPECTED_VERSION:
        raise PatchError(
            f"unexpected add-on identity/version: {addon_id!r} {manifest.get('version')!r}"
        )
    manifest_text = contents["manifest.json"].decode("utf-8")
    version_anchor = f'"version": "{EXPECTED_VERSION}"'
    if manifest_text.count(version_anchor) != 1:
        raise PatchError("manifest.json does not contain one exact upstream version anchor")
    contents["manifest.json"] = manifest_text.replace(
        version_anchor,
        f'"version": "{PATCHED_VERSION}"',
        1,
    ).encode("utf-8")

    try:
        javascript = contents[SCRIPT_NAME].decode("utf-8")
    except UnicodeDecodeError as error:
        raise PatchError(f"{SCRIPT_NAME} is not UTF-8: {error}") from error
    contents[SCRIPT_NAME] = patch_javascript(javascript).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as output_archive:
            for name in sorted(contents):
                is_directory = name.endswith("/")
                data = b"" if is_directory else contents[name]
                output_archive.writestr(_zip_info(name, is_directory), data)
        with zipfile.ZipFile(temporary, "r") as check_archive:
            bad_member = check_archive.testzip()
            if bad_member:
                raise PatchError(f"generated XPI failed CRC validation at {bad_member}")
            generated_manifest = check_archive.read("manifest.json")
            if generated_manifest != contents["manifest.json"]:
                raise PatchError("generated XPI unexpectedly changed manifest.json")
            generated_manifest_data = json.loads(generated_manifest.decode("utf-8"))
            if generated_manifest_data.get("version") != PATCHED_VERSION:
                raise PatchError("generated XPI has the wrong local revision version")
            generated_script = check_archive.read(SCRIPT_NAME).decode("utf-8")
            if generated_script.count(PATCH_MARKER) != 1:
                raise PatchError("generated XPI is missing the unique UI patch marker")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    return sha256_bytes(output_path.read_bytes())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deterministic PDF2zh 4.0.3.3 local-revision XPI whose translation progress "
            "appears as a closeable card inside the Zotero main window."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"pinned upstream XPI (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="patched XPI path (must differ from --input)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        digest = build_patched_xpi(args.input, args.output)
    except PatchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"output: {args.output.resolve()}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
