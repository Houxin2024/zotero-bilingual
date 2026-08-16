/* global Zotero */

var BilingualSync = {
    pluginID: "bilingual-linked-reader@houxin2024.github.io",
    mappingStatusPath: null,
    mapCache: new Map(),
    viewerState: new WeakMap(),
    readyReaders: new WeakSet(),
    readyReaderWindows: new WeakMap(),
    ignoredReaders: new WeakSet(),
    mapRetry: new WeakMap(),
    residentCacheWindows: new Set(),
    progressWindows: new Set(),
    handler: null,
    toolbarHandler: null,
    active: false,

    async start() {
        this.active = true;
        this.configurePDF2zhForLinkedReading();
        this.handler = (event) => {
            this.handleSelection(event).catch(error => {
                Zotero.logError(error);
                Zotero.debug("[BilingualSync] selection failed: " + error);
            });
        };
        this.toolbarHandler = (event) => {
            this.prepareSingleClick(event.reader).catch(error => {
                Zotero.logError(error);
                Zotero.debug("[BilingualSync] single-click setup failed: " + error);
            });
        };
        Zotero.Reader.registerEventListener("renderTextSelectionPopup", this.handler, this.pluginID);
        Zotero.Reader.registerEventListener("renderToolbar", this.toolbarHandler, this.pluginID);
        Zotero.debug("[BilingualSync] started");
        this.bootstrapOpenReaders().catch(error => {
            Zotero.logError(error);
            Zotero.debug("[BilingualSync] reader bootstrap failed: " + error);
        });
    },

    configurePDF2zhForLinkedReading() {
        const preferences = {
            "extensions.zotero.pdf2zh.noMono": true,
            "extensions.zotero.pdf2zh.mono": false,
            "extensions.zotero.pdf2zh.noDual": false,
            "extensions.zotero.pdf2zh.dual": true,
            "extensions.zotero.pdf2zh.dualMode": "LR",
            "extensions.zotero.pdf2zh.transFirst": true,
            "extensions.zotero.pdf2zh.dual-open": true,
            "extensions.zotero.pdf2zh.disableRichTextTranslate": true,
            "extensions.bilingualLinkedReader.residentPageCacheEnabled": false,
        };
        for (const [key, value] of Object.entries(preferences)) {
            Zotero.Prefs.set(key, value, true);
        }
    },

    stop() {
        this.active = false;
        for (const win of [...this.progressWindows]) {
            this.hideMappingProgress(win);
        }
        for (const win of [...this.residentCacheWindows]) {
            this.releaseResidentPageCache(win);
        }
        if (this.handler) {
            Zotero.Reader.unregisterEventListener("renderTextSelectionPopup", this.handler);
        }
        if (this.toolbarHandler) {
            Zotero.Reader.unregisterEventListener("renderToolbar", this.toolbarHandler);
        }
        this.handler = null;
        this.toolbarHandler = null;
        this.mapCache.clear();
        this.readyReaders = new WeakSet();
        this.readyReaderWindows = new WeakMap();
        this.ignoredReaders = new WeakSet();
        this.mapRetry = new WeakMap();
    },

    getViewerWindow(reader) {
        const roots = [
            reader?._lastView?._iframeWindow,
            reader?._internalReader?._primaryView?._iframeWindow,
            reader?._iframeWindow,
        ].filter(Boolean);
        const seen = new Set();
        const queue = roots.map(win => ({ win, depth: 0 }));
        while (queue.length) {
            const { win, depth } = queue.shift();
            if (!win || seen.has(win)) continue;
            seen.add(win);
            try {
                if (win.PDFViewerApplication?.pdfViewer) return win;
                if (depth >= 3) continue;
                for (let index = 0; index < win.frames.length; index++) {
                    queue.push({ win: win.frames[index], depth: depth + 1 });
                }
            }
            catch (error) {
                // Ignore inaccessible frames and continue with known reader windows.
            }
        }
        return roots[0] || null;
    },

    residentPageLimit() {
        const configured = Number(
            Zotero.Prefs.get("extensions.bilingualLinkedReader.residentPageLimit", true),
        );
        return Number.isFinite(configured) && configured > 0
            ? Math.max(10, Math.min(128, Math.round(configured)))
            : 64;
    },

    residentPageCacheEnabled() {
        // Fail safe: the experimental canvas-retention hook interfered with
        // PDF.js retries on some translated documents. Keep all renderer
        // lifecycle methods untouched until a non-invasive cache exists.
        return false;
    },

    listenToViewer(eventBus, eventName, handler) {
        if (typeof eventBus?._on === "function") {
            eventBus._on(eventName, handler);
            return () => eventBus._off?.(eventName, handler);
        }
        if (typeof eventBus?.on === "function") {
            eventBus.on(eventName, handler);
            return () => eventBus.off?.(eventName, handler);
        }
        return () => {};
    },

    reportResidentPageCache(cache, force = false) {
        if (typeof this.writeStatus !== "function" || !cache?.enabled) return;
        const now = Date.now();
        if (!force && now - cache.lastStatusAt < 2000) return;
        cache.lastStatusAt = now;
        this.writeStatus({
            state: "resident-page-cache-ready",
            cacheUpdatedAt: new Date().toISOString(),
            attachment: cache.attachment,
            cachedPages: [...cache.retained.keys()],
            cachedPageCount: cache.retained.size,
            cacheLimit: cache.limit,
            documentPages: cache.viewer.pagesCount,
            preventedEvictions: cache.preventedEvictions,
            preventedResets: cache.preventedResets,
            preventedDetailEvictions: cache.preventedDetailEvictions,
            explicitEvictions: cache.explicitEvictions,
            cacheStrategy: "retain-finished-page-and-detail-states",
            retainedOnlyWhenFinished: true,
            warmupState: null,
            warmedPageCount: null,
            warmupPageTotal: null,
            warmupFailures: null,
            warmupStartedAt: null,
            warmupCompletedAt: null,
            residentCacheSmokePassed: cache.smokePassed,
            residentDetailCacheSmokePassed: cache.smokeDetailPassed,
            residentCacheSmokePage: cache.smokePage,
            residentCacheSmokeAt: cache.smokeAt,
        }).catch(error => Zotero.debug("[BilingualSync] cache status failed: " + error));
    },

    ensureResidentPageCache(win, pdfPath) {
        const state = this.viewerState.get(win) || {};
        if (!this.residentPageCacheEnabled()) {
            if (state.residentCache) this.releaseResidentPageCache(win);
            if (!state.residentCacheDisabledReported) {
                state.residentCacheDisabledReported = true;
                this.viewerState.set(win, state);
                if (typeof this.writeStatus === "function") {
                    this.writeStatus({
                        state: "pdfjs-native-rendering-ready",
                        cacheUpdatedAt: new Date().toISOString(),
                        attachment: pdfPath.replace(/^.*[\\/]/, ""),
                        cachedPages: [],
                        cachedPageCount: 0,
                        cacheStrategy: "disabled-use-pdfjs-native-cache",
                        retainedOnlyWhenFinished: null,
                        residentCacheSmokePassed: null,
                        residentDetailCacheSmokePassed: null,
                        preventedEvictions: 0,
                        preventedResets: 0,
                        preventedDetailEvictions: 0,
                    }).catch(error => Zotero.debug("[BilingualSync] native cache status failed: " + error));
                }
            }
            return true;
        }
        const viewer = win?.PDFViewerApplication?.pdfViewer;
        if (!viewer?.pdfDocument || !viewer.pagesCount || viewer._pages?.length !== viewer.pagesCount) {
            return false;
        }
        if (state.residentCache?.enabled && state.residentCache.viewer === viewer) return true;
        if (state.residentCache) this.releaseResidentPageCache(win);

        const controller = this;
        const cache = {
            enabled: true,
            viewer,
            pdfDocument: viewer.pdfDocument,
            attachment: pdfPath.replace(/^.*[\\/]/, ""),
            limit: Math.min(this.residentPageLimit(), viewer.pagesCount),
            retained: new Map(),
            protected: new Map(),
            wrapped: [],
            removeViewerListeners: [],
            preventedEvictions: 0,
            preventedResets: 0,
            preventedDetailEvictions: 0,
            explicitEvictions: 0,
            lastStatusAt: 0,
            win,
        };

        for (const pageView of viewer._pages) {
            if (
                !pageView
                || typeof pageView.destroy !== "function"
                || typeof pageView.reset !== "function"
            ) continue;
            const originalDestroy = pageView.destroy;
            const originalReset = pageView.reset;
            const originalDraw = pageView.draw;
            const originalUpdateVisibleArea = pageView.updateVisibleArea;
            const wrappedDestroy = function (...args) {
                if (
                    cache.enabled
                    && viewer.pdfDocument === cache.pdfDocument
                    && cache.retained.has(this.id)
                    && this.renderingState === 3
                ) {
                    if (cache.smokeMode) {
                        cache.smokeDestroyIntercepted = true;
                    }
                    else {
                        cache.preventedEvictions += 1;
                        controller.reportResidentPageCache(cache);
                    }
                    return;
                }
                return originalDestroy.apply(this, args);
            };
            const wrappedReset = function (...args) {
                const emptyReset = args.length === 0
                    || (
                        args.length === 1
                        && args[0]
                        && typeof args[0] === "object"
                        && Object.keys(args[0]).length === 0
                    );
                if (
                    cache.enabled
                    && !cache.bypassReset
                    && emptyReset
                    && viewer.pdfDocument === cache.pdfDocument
                    && cache.retained.has(this.id)
                    && this.renderingState === 3
                ) {
                    if (cache.smokeMode) cache.smokeResetIntercepted = true;
                    else {
                        cache.preventedResets += 1;
                        controller.reportResidentPageCache(cache);
                    }
                    return;
                }
                cache.retained.delete(this.id);
                cache.protected.delete(this.id);
                return originalReset.apply(this, args);
            };
            const wrappedDraw = typeof originalDraw === "function"
                ? function (...args) {
                    // A new draw must be free to cancel/reset until it reaches
                    // FINISHED. Re-add it only after the draw promise resolves.
                    cache.retained.delete(this.id);
                    cache.protected.delete(this.id);
                    const result = originalDraw.apply(this, args);
                    Promise.resolve(result).then(
                        () => cache.touch?.(this.id),
                        () => {},
                    );
                    return result;
                }
                : null;
            const wrappedUpdateVisibleArea = typeof originalUpdateVisibleArea === "function"
                ? function (...args) {
                    // PDF.js discards the high-resolution detail canvas when a
                    // page leaves the viewport, even when its normal canvas is
                    // still resident.  On wide bilingual pages this looks like
                    // a full reload when scrolling back.  Keep the visited
                    // page's detail canvas until our own LRU evicts the page.
                    if (
                        cache.enabled
                        && args[0] === null
                        && viewer.pdfDocument === cache.pdfDocument
                        && cache.retained.has(this.id)
                        && this.renderingState === 3
                        && this.detailView
                    ) {
                        if (cache.smokeMode) cache.smokeDetailIntercepted = true;
                        else {
                            cache.preventedDetailEvictions += 1;
                            controller.reportResidentPageCache(cache);
                        }
                        return;
                    }
                    return originalUpdateVisibleArea.apply(this, args);
                }
                : null;
            pageView.destroy = wrappedDestroy;
            pageView.reset = wrappedReset;
            if (wrappedDraw) pageView.draw = wrappedDraw;
            if (wrappedUpdateVisibleArea) pageView.updateVisibleArea = wrappedUpdateVisibleArea;
            cache.wrapped.push({
                pageView,
                originalDestroy,
                wrappedDestroy,
                originalReset,
                wrappedReset,
                originalDraw,
                wrappedDraw,
                originalUpdateVisibleArea,
                wrappedUpdateVisibleArea,
            });
        }

        cache.markRetained = (pageNumber) => {
            if (!cache.enabled) return;
            const pageView = viewer.getPageView(Number(pageNumber) - 1);
            if (!pageView) return;
            const hasRenderedSurface = pageView.renderingState === 3
                && (
                    pageView.div?.hasAttribute?.("data-loaded")
                    || pageView.div?.querySelector?.("canvas")
                );
            if (!hasRenderedSurface) return;
            cache.retained.delete(pageView.id);
            cache.retained.set(pageView.id, pageView);
            while (cache.retained.size > cache.limit) {
                const oldest = cache.retained.entries().next().value;
                if (!oldest) break;
                const [oldestID, oldestView] = oldest;
                cache.retained.delete(oldestID);
                cache.protected.delete(oldestID);
                const wrapped = cache.wrapped.find(entry => entry.pageView === oldestView);
                cache.bypassReset = true;
                try {
                    wrapped?.originalDestroy.call(oldestView);
                }
                finally {
                    cache.bypassReset = false;
                }
                cache.explicitEvictions += 1;
            }
        };

        cache.touch = (pageNumber) => {
            if (!cache.enabled) return;
            const pageView = viewer.getPageView(Number(pageNumber) - 1);
            if (!pageView) return;
            if (pageView.renderingState !== 3) return;
            cache.markRetained(pageNumber);
            cache.protected.delete(pageView.id);
            cache.protected.set(pageView.id, pageView);
        };

        cache.captureFinishedPages = () => {
            if (!cache.enabled) return;
            let changed = false;
            for (const pageView of viewer._pages) {
                if (pageView?.renderingState === 3 && !cache.retained.has(pageView.id)) {
                    cache.markRetained(pageView.id);
                    changed = true;
                }
            }
            if (changed) controller.reportResidentPageCache(cache, true);
        };

        for (const pageView of viewer._pages) {
            cache.markRetained(pageView.id);
            cache.touch(pageView.id);
        }
        const runSmokeWhenReady = () => {
            if (!cache.smokeTested && controller.runResidentPageCacheSmokeTest(cache)) {
                controller.reportResidentPageCache(cache, true);
            }
        };
        const onPageRender = event => {
            cache.markRetained(event.pageNumber);
            runSmokeWhenReady();
        };
        const onPageRendered = event => {
            cache.touch(event.pageNumber);
            runSmokeWhenReady();
        };
        const onPageChanging = event => cache.markRetained(event.pageNumber);
        const onUpdateViewArea = () => cache.captureFinishedPages();
        const onPagesDestroy = () => controller.releaseResidentPageCache(win);
        cache.removeViewerListeners.push(
            this.listenToViewer(viewer.eventBus, "pagerender", onPageRender),
            this.listenToViewer(viewer.eventBus, "pagerendered", onPageRendered),
            this.listenToViewer(viewer.eventBus, "pagechanging", onPageChanging),
            this.listenToViewer(viewer.eventBus, "updateviewarea", onUpdateViewArea),
            this.listenToViewer(viewer.eventBus, "pagesdestroy", onPagesDestroy),
        );
        cache.onViewerScroll = () => {
            if (cache.scrollFrame) return;
            cache.scrollFrame = win.requestAnimationFrame(() => {
                cache.scrollFrame = null;
                cache.captureFinishedPages();
            });
        };
        viewer.container?.addEventListener?.("scroll", cache.onViewerScroll, { passive: true });
        cache.onUnload = () => controller.releaseResidentPageCache(win);
        win.addEventListener("unload", cache.onUnload, { once: true });
        win.__bilingualResidentPageCache = () => ({
            attachment: cache.attachment,
            limit: cache.limit,
            documentPages: viewer.pagesCount,
            cachedPages: [...cache.retained.keys()],
            preventedEvictions: cache.preventedEvictions,
            preventedResets: cache.preventedResets,
            preventedDetailEvictions: cache.preventedDetailEvictions,
            explicitEvictions: cache.explicitEvictions,
            detailCacheSmokePassed: cache.smokeDetailPassed,
            retainedOnlyWhenFinished: true,
        });
        state.residentCache = cache;
        this.viewerState.set(win, state);
        this.residentCacheWindows.add(win);
        this.runResidentPageCacheSmokeTest(cache);
        this.reportResidentPageCache(cache, true);
        Zotero.debug(`[BilingualSync] resident page cache ready: ${cache.limit}/${viewer.pagesCount}`);
        return true;
    },

    runResidentPageCacheSmokeTest(cache) {
        if (cache.smokeTested) return cache.smokePassed;
        const pageView = cache.protected.values().next().value;
        if (!pageView) return false;
        cache.smokeTested = true;
        const beforeState = pageView.renderingState;
        const beforeChildren = pageView.div?.childElementCount;
        const beforeCanvas = pageView.div?.querySelector?.("canvas") || null;
        cache.smokeMode = true;
        cache.smokeResetIntercepted = false;
        cache.smokeDestroyIntercepted = false;
        cache.smokeDetailIntercepted = false;
        const originalDetailView = pageView.detailView;
        let sentinelDetailReset = false;
        const sentinelDetailView = {
            reset() {
                sentinelDetailReset = true;
            },
        };
        pageView.detailView = sentinelDetailView;
        pageView.updateVisibleArea?.(null);
        cache.smokeDetailPassed = Boolean(
            cache.smokeDetailIntercepted
            && !sentinelDetailReset
            && pageView.detailView === sentinelDetailView
        );
        pageView.detailView = originalDetailView;
        pageView.reset();
        pageView.destroy();
        cache.smokeMode = false;
        const sameCanvas = !beforeCanvas || pageView.div?.querySelector?.("canvas") === beforeCanvas;
        cache.smokePassed = Boolean(
            cache.smokeResetIntercepted
            && cache.smokeDestroyIntercepted
            && cache.smokeDetailPassed
            && pageView.renderingState === beforeState
            && pageView.div?.childElementCount === beforeChildren
            && sameCanvas,
        );
        cache.smokePage = pageView.id;
        cache.smokeAt = new Date().toISOString();
        return cache.smokePassed;
    },

    releaseResidentPageCache(win) {
        const state = this.viewerState.get(win);
        const cache = state?.residentCache;
        if (!cache) return;
        cache.enabled = false;
        for (const removeListener of cache.removeViewerListeners || []) {
            try {
                removeListener();
            }
            catch (error) {
                Zotero.debug("[BilingualSync] cache listener cleanup failed: " + error);
            }
        }
        if (cache.onUnload) win.removeEventListener?.("unload", cache.onUnload);
        if (cache.onViewerScroll) {
            cache.viewer.container?.removeEventListener?.("scroll", cache.onViewerScroll);
        }
        if (cache.scrollFrame) win.cancelAnimationFrame?.(cache.scrollFrame);
        for (const {
            pageView,
            originalDestroy,
            wrappedDestroy,
            originalReset,
            wrappedReset,
            originalDraw,
            wrappedDraw,
            originalUpdateVisibleArea,
            wrappedUpdateVisibleArea,
        } of cache.wrapped || []) {
            if (pageView.destroy === wrappedDestroy) pageView.destroy = originalDestroy;
            if (pageView.reset === wrappedReset) pageView.reset = originalReset;
            if (wrappedDraw && pageView.draw === wrappedDraw) pageView.draw = originalDraw;
            if (
                wrappedUpdateVisibleArea
                && pageView.updateVisibleArea === wrappedUpdateVisibleArea
            ) {
                pageView.updateVisibleArea = originalUpdateVisibleArea;
            }
        }
        try {
            delete win.__bilingualResidentPageCache;
        }
        catch (error) {
            // The reader window is already being destroyed.
        }
        state.residentCache = null;
        this.viewerState.set(win, state);
        this.residentCacheWindows.delete(win);
    },

    async getAttachmentPath(reader) {
        const item = reader?._item || Zotero.Items.get(reader?.itemID);
        if (!item) return null;
        if (typeof item.getFilePathAsync === "function") return item.getFilePathAsync();
        return item.getFilePath?.() || null;
    },

    async readJSON(path) {
        if (typeof Zotero.File?.getContentsAsync === "function") {
            return JSON.parse(await Zotero.File.getContentsAsync(path));
        }
        throw new Error("Zotero.File.getContentsAsync is unavailable");
    },

    indexMap(map) {
        if (map.__bilingualIndex) return map;
        const pages = new Map((map.pages || []).map(page => [Number(page.pageIndex), page]));
        const segments = new Map();
        for (const segment of map.segments || []) {
            const pageIndex = Number(segment.pageIndex);
            if (!segments.has(pageIndex)) segments.set(pageIndex, []);
            segments.get(pageIndex).push(segment);
        }
        Object.defineProperty(map, "__bilingualIndex", {
            value: { pages, segments },
            enumerable: false,
        });
        return map;
    },

    async readRemoteJSON(url) {
        const response = await Zotero.HTTP.request("GET", url, {
            responseType: "json",
            timeout: 1800,
        });
        return typeof response.response === "string"
            ? JSON.parse(response.response)
            : response.response;
    },

    async readMappingStatus(pdfPath) {
        const basename = pdfPath.replace(/^.*[\\/]/, "");
        const configuredPath = String(
            this.mappingStatusPath
            || Zotero.Prefs.get("extensions.bilingualLinkedReader.mappingStatusPath", true)
            || "",
        );
        let status = null;
        if (configuredPath) {
            try {
                status = await this.readJSON(configuredPath);
            }
            catch (error) {
                // Fall back to the local translation server below.
            }
        }
        if (!status) {
            try {
                const server = String(
                    Zotero.Prefs.get("extensions.bilingualLinkedReader.serverURL", true)
                    || Zotero.Prefs.get("extensions.zotero.pdf2zh.new_serverip", true)
                    || "http://127.0.0.1:8890",
                ).replace(/\/$/, "");
                status = await this.readRemoteJSON(
                    server + "/api/mapping-status?filename=" + encodeURIComponent(basename),
                );
            }
            catch (error) {
                return null;
            }
        }
        const statusFile = status.currentFile || status.completedFile || "";
        return statusFile === basename ? status : null;
    },

    showMappingProgress(win, status) {
        if (!win?.document?.body) return;
        const document = win.document;
        let node = document.getElementById("codex-bilingual-map-progress");
        if (!node) {
            node = document.createElement("div");
            node.id = "codex-bilingual-map-progress";
            node.innerHTML = "<div class='codex-map-title'></div><div class='codex-map-track'><div class='codex-map-fill'></div></div><div class='codex-map-detail'></div>";
            Object.assign(node.style, {
                position: "fixed",
                right: "18px",
                bottom: "18px",
                width: "270px",
                padding: "12px 14px",
                background: "rgba(255,255,255,0.96)",
                color: "#273142",
                border: "1px solid rgba(102,126,234,0.38)",
                borderRadius: "10px",
                boxShadow: "0 8px 26px rgba(31,41,55,0.18)",
                font: "13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
                zIndex: "2147483646",
                pointerEvents: "none",
            });
            const track = node.querySelector(".codex-map-track");
            Object.assign(track.style, {
                height: "6px",
                marginTop: "8px",
                overflow: "hidden",
                background: "#e7eaf3",
                borderRadius: "999px",
            });
            Object.assign(node.querySelector(".codex-map-fill").style, {
                height: "100%",
                width: "0%",
                background: "linear-gradient(90deg,#667eea,#7c3aed)",
                borderRadius: "999px",
                transition: "width 0.35s ease",
            });
            Object.assign(node.querySelector(".codex-map-detail").style, {
                minHeight: "16px",
                marginTop: "6px",
                color: "#6b7280",
                fontSize: "11px",
            });
            document.body.appendChild(node);
            this.progressWindows.add(win);
        }
        const progress = Math.max(0, Math.min(100, Number(status?.mappingProgress) || 0));
        const ready = progress >= 100;
        node.querySelector(".codex-map-title").textContent = ready
            ? "✓ 双语句子映射已就绪"
            : `双语句子映射 ${Math.round(progress)}% · ${status?.mappingStage || "等待后台启动"}`;
        node.querySelector(".codex-map-fill").style.width = progress + "%";
        node.querySelector(".codex-map-fill").style.background = ready
            ? "#16a34a"
            : "linear-gradient(90deg,#667eea,#7c3aed)";
        node.querySelector(".codex-map-detail").textContent = status?.mappingDetail || (ready ? "现在可以单击任一侧句子联动。" : "PDF 已生成，正在建立中英文坐标关系。 ");
        if (ready) {
            win.setTimeout(() => this.hideMappingProgress(win), 3000);
        }
    },

    hideMappingProgress(win) {
        try {
            win?.document?.getElementById("codex-bilingual-map-progress")?.remove();
        }
        catch (error) {
            // The reader window may already be closing.
        }
        this.progressWindows.delete(win);
    },

    async loadMap(pdfPath, forceRefresh = false) {
        const cached = this.mapCache.get(pdfPath) || null;
        if (cached && !forceRefresh) return cached;
        const basename = pdfPath.replace(/^.*[\\/]/, "");
        const candidates = [pdfPath + ".bilingual.json"];
        const mapDirectory = String(
            Zotero.Prefs.get("extensions.bilingualLinkedReader.mapDirectory", true) || "",
        ).replace(/[\\/]$/, "");
        if (mapDirectory) candidates.push(mapDirectory + "\\" + basename + ".bilingual.json");
        for (const path of candidates) {
            try {
                const map = this.indexMap(await this.readJSON(path));
                this.mapCache.set(pdfPath, map);
                Zotero.debug("[BilingualSync] loaded map: " + path);
                return map;
            }
            catch (error) {
                // Try the next deterministic location.
            }
        }
        try {
            const server = String(
                Zotero.Prefs.get("extensions.bilingualLinkedReader.serverURL", true)
                || Zotero.Prefs.get("extensions.zotero.pdf2zh.new_serverip", true)
                || "http://127.0.0.1:8890",
            ).replace(/\/$/, "");
            const url = server + "/translatedFile/" + encodeURIComponent(basename + ".bilingual.json");
            const map = this.indexMap(await this.readRemoteJSON(url));
            this.mapCache.set(pdfPath, map);
            Zotero.debug("[BilingualSync] loaded map: " + url);
            return map;
        }
        catch (error) {
            // A map may appear after translation finishes, so do not cache misses.
        }
        return cached;
    },

    clearOverlay(win) {
        if (!win?.document) return;
        for (const node of win.document.querySelectorAll(".codex-bilingual-linked-highlight")) {
            node.remove();
        }
    },

    ensureSelectionWatcher(win) {
        const state = this.viewerState.get(win) || {};
        if (state.onSelectionChange) return;
        const onSelectionChange = () => {
            win.setTimeout(() => {
                const selected = win.getSelection?.()?.toString()?.trim();
                if (!selected && Date.now() - (state.lastLinkedClickAt || 0) > 180) {
                    this.clearOverlay(win);
                }
            }, 80);
        };
        win.document.addEventListener("selectionchange", onSelectionChange);
        state.onSelectionChange = onSelectionChange;
        this.viewerState.set(win, state);
    },

    async prepareSingleClick(reader) {
        if (!reader || this.ignoredReaders.has(reader)) return;
        const pdfPath = await this.getAttachmentPath(reader);
        if (!pdfPath) return;
        const label = pdfPath + " " + (reader?._item?.getField?.("title") || "");
        if (!/(compare|dual)/i.test(label)) {
            this.ignoredReaders.add(reader);
            return;
        }
        let win = this.getViewerWindow(reader);
        if (win?.document?.body && win.PDFViewerApplication?.pdfViewer) {
            this.ensureResidentPageCache(win, pdfPath);
        }
        const retry = this.mapRetry.get(reader);
        if (retry?.at > Date.now()) return;
        const map = await this.loadMap(pdfPath);
        if (!map) {
            const attempts = (retry?.attempts || 0) + 1;
            const delay = Math.min(5000, 1000 * (2 ** Math.min(attempts - 1, 3)));
            this.mapRetry.set(reader, { attempts, at: Date.now() + delay });
            await this.writeStatus?.({
                state: "waiting-for-sentence-map",
                attachment: pdfPath.replace(/^.*[\\/]/, ""),
                retryInMilliseconds: delay,
            });
            this.showMappingProgress(
                win,
                await this.readMappingStatus(pdfPath) || {
                    mappingProgress: 0,
                    mappingStage: "等待后台启动",
                    mappingDetail: "翻译已完成，句子映射任务即将开始。",
                },
            );
            return;
        }
        this.mapRetry.delete(reader);
        for (let attempt = 0; attempt < 30; attempt++) {
            win = this.getViewerWindow(reader);
            if (win?.document?.body && win.PDFViewerApplication?.pdfViewer) {
                if (!this.ensureResidentPageCache(win, pdfPath)) {
                    await Zotero.Promise.delay(250);
                    continue;
                }
                this.ensureSelectionWatcher(win);
                this.ensureClickWatcher(win, pdfPath, map);
                this.showMappingProgress(win, {
                    mappingProgress: 100,
                    mappingStage: "句子映射已就绪",
                    mappingDetail: "现在可以单击任一侧句子联动。",
                });
                this.readyReaders.add(reader);
                this.readyReaderWindows.set(reader, win);
                await this.runSingleClickSmokeTest(win, map);
                return;
            }
            await Zotero.Promise.delay(250);
        }
    },

    async bootstrapOpenReaders() {
        // Reader tabs can be opened long after Zotero starts. Keep a cheap
        // watcher alive so every newly opened/reopened bilingual PDF receives
        // the click handler even when renderToolbar is not emitted again.
        while (this.active) {
            for (const reader of Zotero.Reader._readers || []) {
                if (this.ignoredReaders.has(reader)) continue;
                const win = this.getViewerWindow(reader);
                const cache = win && this.viewerState.get(win)?.residentCache;
                const viewer = win?.PDFViewerApplication?.pdfViewer;
                const cacheHealthy = !this.residentPageCacheEnabled()
                    || (
                        cache?.enabled
                        && cache.viewer === viewer
                        && cache.pdfDocument === viewer?.pdfDocument
                    );
                const healthy = this.readyReaders.has(reader)
                    && this.readyReaderWindows.get(reader) === win
                    && cacheHealthy;
                if (!healthy) {
                    this.readyReaders.delete(reader);
                    await this.prepareSingleClick(reader);
                }
            }
            await Zotero.Promise.delay(750);
        }
    },

    ensureClickWatcher(win, pdfPath, map) {
        const state = this.viewerState.get(win) || {};
        if (state.onClick) return;
        const ignoredTarget = target => target?.closest?.(
            "a,button,input,textarea,select,[role='button'],.toolbar,.annotationEditorLayer",
        );
        const dispatchClick = event => {
            if (event.button !== 0 || event.detail !== 1 || ignoredTarget(event.target)) return;
            const click = {
                clientX: event.clientX,
                clientY: event.clientY,
                target: event.target,
            };
            state.lastLinkedClickAt = Date.now();
            if (state.clickFrame) win.cancelAnimationFrame(state.clickFrame);
            state.clickFrame = win.requestAnimationFrame(() => {
                state.clickFrame = null;
                this.handlePageClick(win, pdfPath, map, click).catch(error => {
                    Zotero.logError(error);
                    Zotero.debug("[BilingualSync] single click failed: " + error);
                });
            });
        };
        const onPointerDown = event => {
            if (event.button !== 0 || ignoredTarget(event.target)) return;
            state.pointerDown = { clientX: event.clientX, clientY: event.clientY, target: event.target, at: Date.now() };
        };
        const onPointerUp = event => {
            const start = state.pointerDown;
            state.pointerDown = null;
            if (!start || event.button !== 0 || ignoredTarget(event.target)) return;
            const moved = Math.hypot(event.clientX - start.clientX, event.clientY - start.clientY);
            if (moved > 7 || Date.now() - start.at > 850) return;
            state.pointerUpCount = (state.pointerUpCount || 0) + 1;
            state.lastPointerLinkedAt = Date.now();
            dispatchClick({ button: 0, detail: 1, clientX: event.clientX, clientY: event.clientY, target: event.target || start.target });
        };
        const onClick = event => {
            if (Date.now() - (state.lastPointerLinkedAt || 0) < 250) return;
            dispatchClick(event);
        };
        const onDoubleClick = () => {
            if (state.clickFrame) {
                win.cancelAnimationFrame(state.clickFrame);
                state.clickFrame = null;
            }
            this.clearOverlay(win);
        };
        win.document.addEventListener("pointerdown", onPointerDown, true);
        win.document.addEventListener("pointerup", onPointerUp, true);
        win.document.addEventListener("click", onClick, true);
        win.document.addEventListener("dblclick", onDoubleClick, true);
        state.onPointerDown = onPointerDown;
        state.onPointerUp = onPointerUp;
        state.onClick = onClick;
        state.onDoubleClick = onDoubleClick;
        this.viewerState.set(win, state);
    },

    async runSingleClickSmokeTest(win, map) {
        const state = this.viewerState.get(win) || {};
        if (state.smokeTested) return;
        state.smokeTested = true;
        this.viewerState.set(win, state);
        const page = map.pages?.[0];
        const firstPair = (map.segments || [])
            .filter(segment => Number(segment.pageIndex) === 0)
            .flatMap(segment => segment.sentencePairs || [])
            .find(pair => this.isProsePair(pair));
        if (!page || !firstPair) return;
        const rect = (page.leftLanguage === "en" ? firstPair.enRects : firstPair.zhRects)?.[0];
        if (!rect) return;
        for (let attempt = 0; attempt < 20; attempt++) {
            const pageView = win.PDFViewerApplication?.pdfViewer?.getPageView(0);
            const bounds = pageView?.div?.getBoundingClientRect?.();
            if (pageView?.viewport && bounds?.width && bounds?.height) {
                const [viewportX, viewportY] = pageView.viewport.convertToViewportPoint((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2);
                const clientX = bounds.left + viewportX * bounds.width / pageView.viewport.width;
                const clientY = bounds.top + viewportY * bounds.height / pageView.viewport.height;
                const target = win.document.elementFromPoint(clientX, clientY) || pageView.div;
                const EventType = win.PointerEvent || win.MouseEvent;
                target.dispatchEvent(new EventType("pointerdown", { bubbles: true, button: 0, clientX, clientY }));
                target.dispatchEvent(new EventType("pointerup", { bubbles: true, button: 0, clientX, clientY }));
                await Zotero.Promise.delay(250);
                await this.handlePageClick(win, "", map, { clientX, clientY, target });
                win.setTimeout(() => this.clearOverlay(win), 1200);
                return;
            }
            await Zotero.Promise.delay(100);
        }
    },

    clickPosition(win, click) {
        const viewer = win.PDFViewerApplication?.pdfViewer;
        if (!viewer) return null;
        for (let pageIndex = 0; pageIndex < viewer.pagesCount; pageIndex++) {
            const pageView = viewer.getPageView(pageIndex);
            if (!pageView?.div || !pageView.viewport) continue;
            const bounds = pageView.div.getBoundingClientRect();
            const targetInside = pageView.div.contains(click.target);
            const pointInside = bounds.left <= click.clientX && click.clientX <= bounds.right
                && bounds.top <= click.clientY && click.clientY <= bounds.bottom;
            if (!targetInside && !pointInside) continue;
            if (!bounds.width || !bounds.height) return null;
            const viewportX = (click.clientX - bounds.left) * pageView.viewport.width / bounds.width;
            const viewportY = (click.clientY - bounds.top) * pageView.viewport.height / bounds.height;
            const [pdfX, pdfY] = pageView.viewport.convertToPdfPoint(viewportX, viewportY);
            return { pageIndex, pdfX, pdfY };
        }
        return null;
    },

    async handlePageClick(win, pdfPath, map, click) {
        const position = this.clickPosition(win, click);
        if (!position) {
            await this.writeStatus?.({ state: "click-no-page", lastClickAt: new Date().toISOString() });
            return;
        }
        const pointRect = [
            position.pdfX - 0.8,
            position.pdfY - 0.8,
            position.pdfX + 0.8,
            position.pdfY + 0.8,
        ];
        let activeMap = this.mapCache.get(pdfPath) || map;
        let target = this.findTarget(activeMap, position.pageIndex, [pointRect]);
        const pageSegments = activeMap.__bilingualIndex?.segments?.get(position.pageIndex) || [];
        const state = this.viewerState.get(win) || {};
        if (!target && pdfPath && pageSegments.length === 0 && Date.now() - (state.lastMapRefreshAt || 0) > 3000) {
            state.lastMapRefreshAt = Date.now();
            this.viewerState.set(win, state);
            const refreshed = await this.loadMap(pdfPath, true);
            if (refreshed) {
                activeMap = refreshed;
                target = this.findTarget(activeMap, position.pageIndex, [pointRect]);
            }
        }
        this.clearOverlay(win);
        if (!target) {
            await this.writeStatus?.({ state: "click-no-sentence", lastClickAt: new Date().toISOString(), page: position.pageIndex + 1, mapVersion: activeMap.version });
            return;
        }
        this.drawOverlay(win, position.pageIndex, target.sourceRects || [], "source");
        if (!this.drawOverlay(win, position.pageIndex, target.rects || [], "target")) return;
        await this.writeStatus?.({ state: "precise-linked-click", lastClickAt: new Date().toISOString(), page: position.pageIndex + 1, direction: target.direction, mapVersion: target.mapVersion });
        Zotero.debug("[BilingualSync] linked " + target.direction + " on page " + (position.pageIndex + 1));
    },

    unionRects(rects) {
        return [
            Math.min(...rects.map(rect => rect[0])),
            Math.min(...rects.map(rect => rect[1])),
            Math.max(...rects.map(rect => rect[2])),
            Math.max(...rects.map(rect => rect[3])),
        ];
    },

    intersectionArea(a, b) {
        const width = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
        const height = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
        return width * height;
    },

    candidateScore(selection, box) {
        if (!box) return -Infinity;
        const overlap = this.intersectionArea(selection, box);
        const selectionArea = Math.max(1, (selection[2] - selection[0]) * (selection[3] - selection[1]));
        const cx = (selection[0] + selection[2]) / 2;
        const cy = (selection[1] + selection[3]) / 2;
        const containsCenter = box[0] <= cx && cx <= box[2] && box[1] <= cy && cy <= box[3];
        const dx = Math.max(box[0] - cx, 0, cx - box[2]);
        const dy = Math.max(box[1] - cy, 0, cy - box[3]);
        return overlap / selectionArea + (containsCenter ? 4 : 0) - 0.004 * dy - 0.001 * dx;
    },

    rectSetScore(selectionRects, candidateRects) {
        if (!selectionRects?.length || !candidateRects?.length) return -Infinity;
        const selectionArea = selectionRects.reduce(
            (sum, rect) => sum + Math.max(1, (rect[2] - rect[0]) * (rect[3] - rect[1])),
            0,
        );
        let overlap = 0;
        let centerHits = 0;
        for (const selection of selectionRects) {
            const cx = (selection[0] + selection[2]) / 2;
            const cy = (selection[1] + selection[3]) / 2;
            for (const candidate of candidateRects) {
                overlap += this.intersectionArea(selection, candidate);
                if (candidate[0] <= cx && cx <= candidate[2] && candidate[1] <= cy && cy <= candidate[3]) {
                    centerHits += 1;
                    break;
                }
            }
        }
        const selectionUnion = this.unionRects(selectionRects);
        const candidateUnion = this.unionRects(candidateRects);
        const fallback = this.candidateScore(selectionUnion, candidateUnion);
        return 5 * overlap / selectionArea + 1.5 * centerHits / selectionRects.length + 0.12 * fallback;
    },

    lexicalCount(text) {
        return (String(text || "").match(/[A-Za-z\u3400-\u9fff]/g) || []).length;
    },

    isProsePair(pair) {
        const english = pair.enText || "";
        const likelyReference = /^\s*\d{1,3}\.\s+[A-Z]/.test(english)
            || (/\b(?:Nature|Science|Nat\.|Cell|Proc\.|Bioinform\.|Biotechnol\.|Immunol\.)\b/.test(english)
                && /\(20\d{2}\)/.test(english));
        const lowSemanticConfidence = Number.isFinite(pair.semanticScore) && pair.semanticScore < 0.42;
        return !likelyReference
            && !lowSemanticConfidence
            && this.lexicalCount(english) >= 8
            && this.lexicalCount(pair.zhText) >= 5
            && /[A-Za-z]{4,}/.test(english)
            && /[\u3400-\u9fff]{3,}/.test(pair.zhText || "");
    },

    bestUnitIndex(selection, units) {
        if (!units?.length) return -1;
        let bestIndex = 0;
        let bestScore = -Infinity;
        units.forEach((unit, index) => {
            const score = this.candidateScore(selection, unit.box);
            if (score > bestScore) {
                bestIndex = index;
                bestScore = score;
            }
        });
        return bestIndex;
    },

    correspondingUnit(sourceIndex, sourceUnits, targetUnits) {
        if (sourceIndex < 0 || !targetUnits?.length) return null;
        if (sourceUnits.length === 1 || targetUnits.length === 1) return targetUnits[0];
        const fraction = sourceIndex / (sourceUnits.length - 1);
        return targetUnits[Math.round(fraction * (targetUnits.length - 1))];
    },

    findTarget(map, pageIndex, selectionRects) {
        const index = map.__bilingualIndex || this.indexMap(map).__bilingualIndex;
        const page = index.pages.get(pageIndex);
        if (!page) return null;
        const selection = this.unionRects(selectionRects);
        const isLeft = ((selection[0] + selection[2]) / 2) < page.rightOffset;
        const leftLanguage = page.leftLanguage || "zh";
        const sourceLanguage = isLeft ? leftLanguage : (leftLanguage === "en" ? "zh" : "en");
        const sourceIsZh = sourceLanguage === "zh";
        const segments = index.segments.get(pageIndex) || [];
        let best = null;
        let bestScore = -Infinity;
        for (const segment of segments) {
            const sourceBox = sourceIsZh ? segment.zhBox : segment.enBox;
            const score = this.candidateScore(selection, sourceBox);
            if (score > bestScore) {
                best = segment;
                bestScore = score;
            }
        }
        if (!best || bestScore < -1.5) return null;
        if (map.version >= 2 && best.sentencePairs?.length) {
            let bestPair = null;
            let bestPairScore = -Infinity;
            for (const pair of best.sentencePairs) {
                if (!this.isProsePair(pair)) continue;
                const sourceRects = sourceIsZh ? pair.zhRects : pair.enRects;
                const score = this.rectSetScore(selectionRects, sourceRects);
                if (score > bestPairScore) {
                    bestPair = pair;
                    bestPairScore = score;
                }
            }
            if (bestPair && bestPairScore > 0.25) {
                return {
                    sourceRects: sourceIsZh ? bestPair.zhRects : bestPair.enRects,
                    rects: sourceIsZh ? bestPair.enRects : bestPair.zhRects,
                    sourceText: sourceIsZh ? bestPair.zhText : bestPair.enText,
                    targetText: sourceIsZh ? bestPair.enText : bestPair.zhText,
                    direction: sourceIsZh ? "中→英" : "英→中",
                    matchScore: Math.round(bestPairScore * 1000) / 1000,
                    mapVersion: map.version,
                };
            }
            if (map.version >= 3) return null;
        }
        const sourceUnits = sourceIsZh ? best.zhSentences : best.enSentences;
        const targetUnits = sourceIsZh ? best.enSentences : best.zhSentences;
        const sourceIndex = this.bestUnitIndex(selection, sourceUnits);
        const targetUnit = this.correspondingUnit(sourceIndex, sourceUnits, targetUnits);
        return {
            sourceRects: sourceUnits[sourceIndex]?.rects || (sourceUnits[sourceIndex]?.box ? [sourceUnits[sourceIndex].box] : []),
            rects: targetUnit?.box ? [targetUnit.box] : [sourceIsZh ? best.enBox : best.zhBox],
            direction: sourceIsZh ? "中→英" : "英→中",
            mapVersion: map.version || 1,
        };
    },

    drawOverlay(win, pageIndex, rects, role = "target") {
        const viewer = win.PDFViewerApplication?.pdfViewer;
        const pageView = viewer?.getPageView(pageIndex);
        if (!pageView?.div || !pageView?.viewport) return false;
        const sourceRole = role === "source";
        for (const rect of rects) {
            const p1 = pageView.viewport.convertToViewportPoint(rect[0], rect[1]);
            const p2 = pageView.viewport.convertToViewportPoint(rect[2], rect[3]);
            const left = Math.min(p1[0], p2[0]);
            const top = Math.min(p1[1], p2[1]);
            const right = Math.max(p1[0], p2[0]);
            const bottom = Math.max(p1[1], p2[1]);
            const node = win.document.createElement("div");
            node.className = "codex-bilingual-linked-highlight codex-bilingual-" + role;
            Object.assign(node.style, {
                position: "absolute",
                left: left + "px",
                top: top + "px",
                width: Math.max(2, right - left) + "px",
                height: Math.max(2, bottom - top) + "px",
                background: sourceRole ? "rgba(14, 165, 233, 0.18)" : "rgba(255, 193, 7, 0.34)",
                border: sourceRole ? "1px solid rgba(2, 132, 199, 0.74)" : "1px solid rgba(245, 139, 0, 0.82)",
                borderRadius: "2px",
                boxSizing: "border-box",
                pointerEvents: "none",
                zIndex: "8",
                mixBlendMode: "multiply",
            });
            pageView.div.appendChild(node);
        }
        return true;
    },

    async handleSelection(event) {
        const { reader, params, doc, append } = event;
        const position = params?.annotation?.position;
        if (!position?.rects?.length || !Number.isInteger(position.pageIndex)) return;
        const pdfPath = await this.getAttachmentPath(reader);
        if (!pdfPath) return;
        const label = pdfPath + " " + (reader?._item?.getField?.("title") || "");
        const compareMode = /(compare|dual)/i.test(label);
        const map = await this.loadMap(pdfPath);
        if (!map) return;
        const win = this.getViewerWindow(reader);
        if (!win) return;
        this.ensureSelectionWatcher(win);
        if (compareMode) this.ensureClickWatcher(win, pdfPath, map);
        this.clearOverlay(win);
        if (compareMode) {
            const target = this.findTarget(map, position.pageIndex, position.rects);
            if (!target) return;
            this.drawOverlay(win, position.pageIndex, target.sourceRects || [], "source");
            if (!this.drawOverlay(win, position.pageIndex, target.rects, "target")) return;
            const badge = doc.createElement("span");
            badge.textContent = "↔";
            badge.title = "精确双语联动（" + target.direction + "）";
            badge.style.cssText = "font-weight:700;color:#d97706;padding:0 4px;";
            append(badge);
            return;
        }
    },
};
