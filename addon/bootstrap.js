/* global Zotero, Services, Components */

var syncContext;

function install() {}

async function startup({ rootURI, resourceURI }) {
    await Zotero.initializationPromise;
    rootURI = rootURI || resourceURI.spec;
    syncContext = { Zotero, Services, Components, rootURI };
    Services.scriptloader.loadSubScript(rootURI + "sync.js?v=0.7.3", syncContext);
    await syncContext.BilingualSync.start();
}

function shutdown(data, reason) {
    if (reason === APP_SHUTDOWN) return;
    try {
        syncContext?.BilingualSync?.stop();
    }
    catch (error) {
        Zotero.logError(error);
    }
    syncContext = null;
}

function uninstall() {}
